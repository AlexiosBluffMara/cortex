"""GCP Cloud Run worker — TRIBE v2 inference endpoint.

Mirror image of `cortex.gcp_inference.GCPInferenceFallback` (the client). The
client posts a job, polls until done, and reads the result. This module is
what the client talks to. Wire format is the contract:

  POST /infer             body: { "media_path": str, "scan_id": str (optional) }
                          → 202 { "job_id": str, "status": "queued" }
  GET  /infer/{job_id}    → 200 { "status": "queued" | "running" | "succeeded" | "failed",
                                  "result": {...} | None,
                                  "error":  {...} | None,
                                  "started_at": iso-str | None,
                                  "finished_at": iso-str | None }
  GET  /healthz           → 200 { "ok": True, "gpu": str, "queue_depth": int,
                                  "active_jobs": int }

Job lifecycle:

  queued ──> running ──> succeeded / failed

State storage: in-memory dict keyed by job_id. For production we'd back this
with Firestore + a Cloud Tasks queue (one task per job) so a Cloud Run revision
swap doesn't drop running work. The swap-in point is `JobRegistry`.

Deployment shape (see `gcp/Dockerfile.tribe-worker` and `docs/gcp.md`):

  - Cloud Run with `--gpu=1 --gpu-type=a100` (`a2-highgpu-1g` on Compute Engine
    is the canonical alternative for >15-minute jobs).
  - `--no-cpu-throttling` so the worker keeps the GPU warm between requests.
  - `--max-instances=2` (avoid quota surprises during demo).
  - Identity-token auth via `--no-allow-unauthenticated`. The cortex
    client (`GCPInferenceFallback`) is configured with the same identity
    token.

The actual TRIBE inference is delegated to `cortex.pipeline.run_inference` so
the worker uses the exact same code path the local 5090 does.
"""
from __future__ import annotations

import asyncio
import os
import socket
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

EXPECTED_TOKEN = os.environ.get("GCP_INFERENCE_TOKEN", "")


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    """Reject requests without a valid Bearer token.

    In production GCP Cloud Run handles identity-token auth at the platform
    level; this is the in-app shim for tests + dev. Disable by leaving
    `GCP_INFERENCE_TOKEN` unset, in which case any request passes.
    """
    if not EXPECTED_TOKEN:
        return  # auth disabled
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != EXPECTED_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


# ---------------------------------------------------------------------------
# Job state machine
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    media_path: str
    status: JobStatus = JobStatus.QUEUED
    scan_id: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    queued_at: str = field(default_factory=_utc_iso)
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status.value,
            "scan_id": self.scan_id,
            "result": self.result,
            "error": self.error,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobRegistry:
    """In-memory job store.

    Production swap-in: replace `_jobs` with a Firestore-backed dict-of-dicts
    plus a Cloud Tasks consumer. The interface stays the same — `put`, `get`,
    `update`, `count_active`, `count_queue_depth`.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def put(self, job: Job) -> None:
        async with self._lock:
            self._jobs[job.id] = job

    async def get(self, job_id: str) -> Job | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update(self, job_id: str, **fields: Any) -> Job | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            return job

    async def count_active(self) -> int:
        async with self._lock:
            return sum(1 for j in self._jobs.values() if j.status is JobStatus.RUNNING)

    async def count_queue_depth(self) -> int:
        async with self._lock:
            return sum(1 for j in self._jobs.values() if j.status is JobStatus.QUEUED)


# ---------------------------------------------------------------------------
# Inference runner — delegates to cortex.pipeline so worker == local code
# ---------------------------------------------------------------------------

async def _run_inference(media_path: str) -> dict[str, Any]:
    """Run TRIBE v2 against the given media file and return a wire-shaped dict.

    Lazy-imports `cortex.pipeline` so unit tests can substitute a fake module
    via `sys.modules["cortex.pipeline"]` without dragging in torch.
    """
    loop = asyncio.get_event_loop()
    # The pipeline module is heavy (torch + TRIBE deps); import only when needed.
    from cortex import pipeline as _pipeline

    def _do() -> dict[str, Any]:
        result = _pipeline.run_inference(media_path)
        # `result` is the in-process InferenceResult shape; serialize the
        # wire-relevant fields. preds_url is None until we upload to GCS,
        # which is the next layer up — for now leave that to the caller.
        return {
            "preds_url": getattr(result, "preds_url", ""),
            "top_rois": list(getattr(result, "top_rois", [])),
            "peak_t": int(getattr(result, "peak_t", 0)),
            "seconds_elapsed": float(getattr(result, "seconds_elapsed", 0.0)),
        }

    return await loop.run_in_executor(None, _do)


async def _process_job(registry: JobRegistry, job_id: str) -> None:
    """Background task: move a queued job through running → succeeded/failed."""
    job = await registry.get(job_id)
    if job is None:
        return
    await registry.update(job_id, status=JobStatus.RUNNING, started_at=_utc_iso())

    try:
        result = await _run_inference(job.media_path)
        await registry.update(
            job_id,
            status=JobStatus.SUCCEEDED,
            result=result,
            finished_at=_utc_iso(),
        )
    except Exception as exc:
        await registry.update(
            job_id,
            status=JobStatus.FAILED,
            error={"message": str(exc), "type": exc.__class__.__name__},
            finished_at=_utc_iso(),
        )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app(registry: JobRegistry | None = None) -> FastAPI:
    app = FastAPI(
        title="Cortex Inference Worker",
        description="GCP Cloud Run worker for TRIBE v2 brain analysis",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.registry = registry or JobRegistry()

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        reg: JobRegistry = app.state.registry
        return {
            "ok": True,
            "version": "0.1.0",
            "gpu": os.environ.get("CORTEX_WORKER_GPU", "a100-40gb"),
            "host": socket.gethostname(),
            "active_jobs": await reg.count_active(),
            "queue_depth": await reg.count_queue_depth(),
        }

    @app.post("/infer", dependencies=[Depends(require_bearer)])
    async def submit(payload: dict[str, Any]) -> JSONResponse:
        media_path = payload.get("media_path")
        if not media_path or not isinstance(media_path, str):
            raise HTTPException(status_code=400, detail="media_path is required")

        scan_id = payload.get("scan_id")
        if scan_id is not None and not isinstance(scan_id, str):
            raise HTTPException(status_code=400, detail="scan_id must be a string")

        job = Job(id=uuid.uuid4().hex[:16], media_path=media_path, scan_id=scan_id)
        await app.state.registry.put(job)
        # Fire-and-forget background task — the inference path does its own
        # error handling via _process_job.
        asyncio.create_task(_process_job(app.state.registry, job.id))

        return JSONResponse(
            {"job_id": job.id, "status": JobStatus.QUEUED.value},
            status_code=status.HTTP_202_ACCEPTED,
        )

    @app.get("/infer/{job_id}", dependencies=[Depends(require_bearer)])
    async def lookup(job_id: str) -> dict[str, Any]:
        job = await app.state.registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        return job.to_dict()

    return app


# Default app instance for `gunicorn gcp.cloud_run.inference_api:app` /
# `uvicorn gcp.cloud_run.inference_api:app`.
app = create_app()
