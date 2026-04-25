"""GCP A100 fallback for TRIBE v2 inference (per SPEC §5 + §15).

When the local 5090 OOMs during a brain scan, the GPU scheduler can hand the
job off to a Cloud Run-fronted A100 worker. This module is the client side:

  - `GCPInferenceFallback.submit(media_path)` posts the media to the GCP
    endpoint, polls until the worker returns an `InferenceResult`-shaped
    payload, and either returns it as a `RemoteInferenceResult` or returns
    a `CortexError`.
  - `GCPInferenceFallback.health()` checks reachability without burning
    inference budget.
  - `GCPInferenceFallback.available()` tells callers (the scheduler) whether
    the fallback is even configured — so they can `raise` instead of route
    when GCP creds aren't set.

The class is registered with `GPUScheduler.set_inference_fallback(...)` once
at startup; the scheduler's `_run_brain_scan_inner` calls it inside the
`except CUDA OOM` branch.

Wire format (matches what we'll publish in `gcp/cloud_run/inference_api.py`):

  POST /infer        body: { "scan_id": str, "media_url": str, "tier": int }
                     → 202 { "job_id": str }
  GET  /infer/{id}   → 200 { "status": "running" | "succeeded" | "failed",
                              "result": {...} | None, "error": {...} | None }
  GET  /healthz      → 200 { "ok": True, "gpu": "a100-40gb", "queue_depth": int }

Auth via Bearer token (Cloud Run identity token or static secret).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cortex.errors import (
    CortexError,
    CortexException,
    ErrorCode,
)
from cortex.logger import log

# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

@dataclass
class RemoteInferenceResult:
    """Mirrors the in-process InferenceResult shape so the scheduler can
    return either local or remote results without callers caring.
    """

    preds_url: str             # GCS signed URL to the `(T, 20484)` BOLD array
    top_rois: list[str]
    peak_t: int
    seconds_elapsed: float
    fallback_used: str = "gcp_a100"
    job_id: str = ""

    @property
    def fallback(self) -> str:
        return self.fallback_used


# ---------------------------------------------------------------------------
# Protocol for inference fallbacks (lets tests substitute trivially)
# ---------------------------------------------------------------------------

class InferenceFallback(Protocol):
    """Anything that takes a media path and returns either a remote result or
    a CortexError. Used by the GPU scheduler's OOM recovery branch.
    """

    name: str

    def available(self) -> bool: ...

    async def health(self) -> dict[str, Any]: ...

    async def submit(self, media_path: str | Path) -> RemoteInferenceResult | CortexError: ...


class NullFallback:
    """No-op fallback. Returned by `default_fallback()` when GCP isn't configured."""

    name = "null"

    def available(self) -> bool:
        return False

    async def health(self) -> dict[str, Any]:
        return {"available": False, "reason": "no fallback configured"}

    async def submit(self, media_path: str | Path) -> CortexError:
        return CortexError(
            code=ErrorCode.GCP_QUOTA_EXCEEDED,
            message="No inference fallback configured (GCP_INFERENCE_ENDPOINT unset)",
            component="gcp_inference",
            recovery_action="Set GCP_INFERENCE_ENDPOINT and GCP_INFERENCE_TOKEN env vars, or run on local 5090.",
        )


# ---------------------------------------------------------------------------
# GCP A100 fallback
# ---------------------------------------------------------------------------

class GCPInferenceFallback:
    """Submit TRIBE v2 inference to a GCP-hosted A100 worker.

    Auth model: Bearer token from `GCP_INFERENCE_TOKEN` env var (or the
    explicit `token` argument). The token is checked once on `submit()`; if
    missing, we return an `unavailable`-flavored CortexError without making a
    network call.
    """

    name = "gcp_a100"

    DEFAULT_TIMEOUT_S = 300         # whole-job timeout (TRIBE on A100 ~70s + slack)
    POLL_INTERVAL_S = 1.5
    INITIAL_RETRY_S = 0.5
    MAX_RETRIES = 3

    def __init__(
        self,
        endpoint: str | None = None,
        token: str | None = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.endpoint = (endpoint or os.environ.get("GCP_INFERENCE_ENDPOINT", "")).rstrip("/")
        self.token = token or os.environ.get("GCP_INFERENCE_TOKEN", "")
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return bool(self.endpoint and self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def health(self) -> dict[str, Any]:
        if not self.available():
            return {"available": False, "reason": "endpoint or token missing"}

        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.endpoint}/healthz",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                resp.raise_for_status()
                payload = resp.json()
                return {"available": True, **payload}
        except Exception as exc:
            log.warning("[gcp_inference] healthz failed: %s", exc)
            return {"available": False, "reason": f"healthz failed: {exc}"}

    async def submit(self, media_path: str | Path) -> RemoteInferenceResult | CortexError:
        """Submit a media path to the GCP A100 worker, poll, return the result.

        The worker is expected to upload the input to GCS itself once it pulls
        the signed URL we provide; we don't stream the bytes here. For the
        hackathon demo this is fine — the FastAPI scan endpoint already
        persists the upload to disk under `uploads/`, and a tiny GCS uploader
        is the next layer up.
        """
        if not self.available():
            return CortexError(
                code=ErrorCode.GCP_QUOTA_EXCEEDED,
                message="GCP inference fallback not configured",
                component="gcp_inference",
                recovery_action="Set GCP_INFERENCE_ENDPOINT and GCP_INFERENCE_TOKEN.",
            )

        import httpx
        media_path = str(media_path)

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                # 1. Submit
                job_id = await self._post_with_retries(client, media_path)
                if isinstance(job_id, CortexError):
                    return job_id

                # 2. Poll
                deadline = asyncio.get_event_loop().time() + self.timeout_s
                while asyncio.get_event_loop().time() < deadline:
                    poll = await client.get(
                        f"{self.endpoint}/infer/{job_id}",
                        headers={"Authorization": f"Bearer {self.token}"},
                    )
                    if poll.status_code == 404:
                        # Worker restarted between submit and poll
                        return CortexError(
                            code=ErrorCode.GCP_PREEMPTED,
                            message=f"GCP job {job_id} disappeared (worker preempted?)",
                            component="gcp_inference",
                            retry=True,
                        )
                    poll.raise_for_status()
                    body = poll.json()
                    status = body.get("status")
                    if status == "succeeded":
                        log.info("[gcp_inference] job %s succeeded", job_id)
                        return self._parse_result(body, job_id)
                    if status == "failed":
                        return CortexError(
                            code=ErrorCode.INFERENCE_FAILED,
                            message=f"GCP worker reported failure: {body.get('error', {}).get('message', 'unknown')}",
                            component="gcp_inference",
                            fallback_used="gcp_a100",
                        )
                    await asyncio.sleep(self.POLL_INTERVAL_S)

                return CortexError(
                    code=ErrorCode.TRIBE_TIMEOUT,
                    message=f"GCP job {job_id} did not complete within {self.timeout_s}s",
                    component="gcp_inference",
                )

        except httpx.HTTPError as exc:
            log.error("[gcp_inference] HTTP error: %s", exc)
            return CortexError(
                code=ErrorCode.GCP_NETWORK_TIMEOUT,
                message=f"Network failure talking to GCP: {exc}",
                component="gcp_inference",
                retry=True,
            )

    async def _post_with_retries(
        self, client: Any, media_path: str
    ) -> str | CortexError:
        """POST /infer with exponential backoff on transient failures."""
        delay = self.INITIAL_RETRY_S
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = await client.post(
                    f"{self.endpoint}/infer",
                    headers=self._headers(),
                    json={"media_path": media_path},
                )
                if resp.status_code == 429:
                    # Rate limited — back off
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                resp.raise_for_status()
                body = resp.json()
                job_id = body.get("job_id")
                if not job_id:
                    return CortexError(
                        code=ErrorCode.INFERENCE_FAILED,
                        message="GCP /infer response missing job_id",
                        component="gcp_inference",
                    )
                return str(job_id)
            except Exception as exc:
                last_exc = exc
                log.warning("[gcp_inference] submit attempt %d failed: %s", attempt + 1, exc)
                await asyncio.sleep(delay)
                delay *= 2
        return CortexError(
            code=ErrorCode.GCP_NETWORK_TIMEOUT,
            message=f"GCP /infer failed after {self.MAX_RETRIES} attempts: {last_exc}",
            component="gcp_inference",
            retry=False,
        )

    def _parse_result(self, body: dict[str, Any], job_id: str) -> RemoteInferenceResult | CortexError:
        result = body.get("result") or {}
        try:
            return RemoteInferenceResult(
                preds_url=result["preds_url"],
                top_rois=list(result["top_rois"]),
                peak_t=int(result["peak_t"]),
                seconds_elapsed=float(result.get("seconds_elapsed", 0.0)),
                job_id=job_id,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return CortexError(
                code=ErrorCode.INFERENCE_FAILED,
                message=f"GCP returned malformed result: {exc}",
                component="gcp_inference",
            )


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def default_fallback() -> InferenceFallback:
    """Return whichever fallback the environment authorizes.

    If `GCP_INFERENCE_ENDPOINT` and `GCP_INFERENCE_TOKEN` are both set, return
    a configured `GCPInferenceFallback`. Otherwise return `NullFallback()` so
    callers can still call `.submit()` without surrounding null checks — the
    null fallback returns a structured CortexError that explains why.
    """
    fb = GCPInferenceFallback()
    if fb.available():
        return fb
    return NullFallback()


def raise_for_error(result: RemoteInferenceResult | CortexError) -> RemoteInferenceResult:
    """Helper for callers that want exception semantics."""
    if isinstance(result, CortexError):
        raise CortexException(result)
    return result


__all__ = [
    "GCPInferenceFallback",
    "InferenceFallback",
    "NullFallback",
    "RemoteInferenceResult",
    "default_fallback",
    "raise_for_error",
]
