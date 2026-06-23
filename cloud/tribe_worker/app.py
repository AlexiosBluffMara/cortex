"""FastAPI worker for cloud-hosted TRIBE v2 scans.

This app intentionally mirrors the subset of the main Cortex API that
`webapp.server` expects when `CORTEX_CLOUD_TRIBE_ENDPOINT` is configured:

  POST /api/scan
  GET  /api/scan/{scan_id}
  GET  /api/scan/{scan_id}/bold-vertex
  GET  /api/scan/{scan_id}/bold-simulate
  GET  /healthz
  GET  /api/tribe/status

Set `CORTEX_WORKER_MODE=real` in a GPU container to delegate to
`cortex.pipeline.run_inference`. The default `fake` mode is deterministic and
exists for contract tests, smoke tests, and provider bring-up before weights are
mounted.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import mimetypes
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

TR_SECONDS = 0.5
N_VERTICES = 20484
WORKER_MODE = os.environ.get("CORTEX_WORKER_MODE", "fake").strip().lower()
WORKER_PROVIDER = os.environ.get("CORTEX_WORKER_PROVIDER", "cloud-tribe-worker")
WORKER_TOKEN = os.environ.get("CORTEX_WORKER_TOKEN", "")
ROOT_DIR = Path(os.environ.get("CORTEX_WORKER_ROOT", tempfile.gettempdir())) / "cortex-tribe-worker"
UPLOAD_DIR = ROOT_DIR / "uploads"
SCAN_DIR = ROOT_DIR / "scans"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SCAN_DIR.mkdir(parents=True, exist_ok=True)

YEO_ROIS = [
    "7Networks_LH_Vis_1",
    "7Networks_RH_Vis_2",
    "7Networks_LH_SomMot_3",
    "7Networks_RH_Default_4",
    "7Networks_LH_Cont_5",
    "7Networks_RH_DorsAttn_6",
    "7Networks_LH_SalVentAttn_7",
    "7Networks_RH_Limbic_8",
]


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _dir_has_files(path: Path | None) -> bool:
    if path is None or not path.exists() or not path.is_dir():
        return False
    try:
        return any(path.iterdir())
    except OSError:
        return False


def _tribe_real_readiness() -> dict[str, Any]:
    """Describe whether this container can run real TRIBE inference.

    This intentionally avoids importing `cortex.pipeline` because that can be
    expensive and may initialize accelerator libraries. It performs deploy-time
    checks that explain the usual cloud failures: missing Python deps, missing
    weights, and no visible accelerator.
    """
    modules = {
        "torch": _module_available("torch"),
        "tribev2": _module_available("tribev2"),
        "cortex.pipeline": _module_available("cortex.pipeline"),
    }
    weights_dir: Path | None = None
    cache_dir: Path | None = None
    try:
        from cortex import config as cortex_config  # noqa: PLC0415

        weights_dir = Path(cortex_config.WEIGHTS_DIR)
        cache_dir = Path(cortex_config.CACHE_DIR)
    except Exception:
        weights_dir = None
        cache_dir = None

    torch_info: dict[str, Any] = {
        "importable": modules["torch"],
        "cuda_available": False,
        "cuda_device_count": 0,
        "device_name": None,
    }
    if modules["torch"]:
        try:
            import torch  # noqa: PLC0415

            torch_info["cuda_available"] = bool(torch.cuda.is_available())
            torch_info["cuda_device_count"] = int(torch.cuda.device_count())
            if torch.cuda.is_available():
                torch_info["device_name"] = torch.cuda.get_device_name(0)
        except Exception as exc:  # noqa: BLE001
            torch_info["error"] = f"{exc.__class__.__name__}: {exc}"

    checks = {
        "modules": modules,
        "weights_dir": str(weights_dir) if weights_dir else None,
        "weights_present": _dir_has_files(weights_dir),
        "cache_dir": str(cache_dir) if cache_dir else None,
        "torch": torch_info,
    }
    missing: list[str] = []
    for name, available in modules.items():
        if not available:
            missing.append(f"missing Python module: {name}")
    if not checks["weights_present"]:
        missing.append("TRIBE weights directory is missing or empty")
    if not torch_info["cuda_available"]:
        missing.append("no CUDA GPU visible to the worker")

    return {
        "real_mode_ready": not missing,
        "missing": missing,
        "checks": checks,
    }


def tribe_readiness() -> dict[str, Any]:
    real = _tribe_real_readiness()
    return {
        "ok": WORKER_MODE != "real" or real["real_mode_ready"],
        "mode": WORKER_MODE,
        "provider": WORKER_PROVIDER,
        "contract_ready": True,
        "real_mode_required": WORKER_MODE == "real",
        **real,
    }


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    if not WORKER_TOKEN:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if authorization.removeprefix("Bearer ").strip() != WORKER_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


@dataclass
class ScanRecord:
    id: str
    filename: str
    status: str = "queued"
    tier: int = 1
    source: str = "cloud"
    narration_model: str = ""
    analysis_mode: str = "tribe_video"
    compute_target: str = "cloud"
    top_rois: list[str] = field(default_factory=list)
    peak_t: int | None = None
    seconds_elapsed: float | None = None
    tribe_seconds: float | None = None
    n_t: int | None = None
    tr_seconds: float = TR_SECONDS
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "tier": self.tier,
            "source": self.source,
            "narration_model": self.narration_model,
            "analysis_mode": self.analysis_mode,
            "compute_target": self.compute_target,
            "top_rois": self.top_rois,
            "peak_t": self.peak_t,
            "seconds_elapsed": self.seconds_elapsed,
            "tribe_seconds": self.tribe_seconds,
            "n_t": self.n_t,
            "tr_seconds": self.tr_seconds,
            "has_bold_vertex": (SCAN_DIR / f"{self.id}.npy").exists(),
            "bold_vertex_url": f"/api/scan/{self.id}/bold-vertex",
            "source_media_url": f"/api/scan/{self.id}/source-media",
            "error": self.error,
        }


class Registry:
    def __init__(self) -> None:
        self.records: dict[str, ScanRecord] = {}
        self.lock = asyncio.Lock()

    async def put(self, record: ScanRecord) -> None:
        async with self.lock:
            self.records[record.id] = record

    async def get(self, scan_id: str) -> ScanRecord | None:
        async with self.lock:
            return self.records.get(scan_id)

    async def update(self, scan_id: str, **fields: Any) -> ScanRecord | None:
        async with self.lock:
            record = self.records.get(scan_id)
            if record is None:
                return None
            for key, value in fields.items():
                setattr(record, key, value)
            return record

    async def list_recent(self, limit: int = 50) -> list[ScanRecord]:
        async with self.lock:
            return list(self.records.values())[-limit:]


def _analysis_mode(filename: str, content_type: str | None = None) -> str:
    suffix = Path(filename).suffix.lower()
    ctype = (content_type or "").lower()
    if suffix in {".mp4", ".mov", ".webm", ".mkv", ".avi"} or ctype.startswith("video/"):
        return "tribe_video"
    if suffix in {".wav", ".mp3", ".m4a", ".ogg", ".webm"} or ctype.startswith("audio/"):
        return "tribe_audio"
    if suffix in {".txt", ".md", ".srt", ".vtt"} or ctype.startswith("text/"):
        return "tribe_text"
    return "tribe_text_bridge_document"


def _fake_bold(scan_id: str, n_t: int = 64, n_vertices: int = N_VERTICES) -> np.ndarray:
    seed = int(hashlib.sha256(scan_id.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 2 * np.pi, n_t, dtype=np.float32)
    base = rng.normal(0, 0.015, size=(n_t, n_vertices)).astype(np.float32)
    for band, phase in enumerate((0.0, 0.8, 1.6, 2.4)):
        start = band * (n_vertices // 8)
        end = min(n_vertices, start + (n_vertices // 10))
        base[:, start:end] += (0.18 * np.sin(t[:, None] + phase)).astype(np.float32)
    return base


def _top_rois(scan_id: str) -> list[str]:
    offset = int(hashlib.md5(scan_id.encode("utf-8")).hexdigest()[:2], 16) % len(YEO_ROIS)
    return [YEO_ROIS[(offset + i) % len(YEO_ROIS)] for i in range(6)]


def _slice_bold(arr: np.ndarray, n_t: int) -> np.ndarray:
    n_t = max(2, min(int(n_t), int(arr.shape[0])))
    if n_t == arr.shape[0]:
        sliced = arr
    else:
        idx = np.linspace(0, arr.shape[0] - 1, n_t).astype(np.int64)
        sliced = arr[idx]
    return np.ascontiguousarray(sliced, dtype="<f4")


async def _run_real(scan_id: str, media_path: Path) -> tuple[np.ndarray, list[str], int, float]:
    loop = asyncio.get_event_loop()

    def _do() -> tuple[np.ndarray, list[str], int, float]:
        from cortex.pipeline import run_inference  # noqa: PLC0415

        started = time.time()
        result = run_inference(str(media_path))
        preds = np.asarray(getattr(result, "preds", None), dtype=np.float32)
        if preds.ndim != 2 or preds.shape[1] != N_VERTICES:
            raise RuntimeError(f"unexpected TRIBE preds shape: {preds.shape}")
        return (
            preds,
            list(getattr(result, "top_rois", []) or _top_rois(scan_id)),
            int(getattr(result, "peak_t", 0) or 0),
            float(getattr(result, "seconds_elapsed", time.time() - started) or time.time() - started),
        )

    return await loop.run_in_executor(None, _do)


async def _process_scan(app: FastAPI, scan_id: str, media_path: Path) -> None:
    started = time.time()
    await app.state.registry.update(scan_id, status="running")
    try:
        if WORKER_MODE == "real":
            readiness = tribe_readiness()
            if not readiness["real_mode_ready"]:
                raise RuntimeError(
                    "TRIBE real mode is not ready: "
                    + "; ".join(readiness.get("missing") or ["unknown readiness failure"])
                )
            bold, rois, peak_t, seconds_elapsed = await _run_real(scan_id, media_path)
        else:
            await asyncio.sleep(float(os.environ.get("CORTEX_WORKER_FAKE_DELAY_S", "0.05")))
            bold = _fake_bold(scan_id)
            rois = _top_rois(scan_id)
            peak_t = int(np.argmax(np.abs(bold).mean(axis=1)))
            seconds_elapsed = round(time.time() - started, 2)

        np.save(SCAN_DIR / f"{scan_id}.npy", bold.astype(np.float32))
        await app.state.registry.update(
            scan_id,
            status="complete",
            top_rois=rois,
            peak_t=peak_t,
            seconds_elapsed=seconds_elapsed,
            tribe_seconds=round(time.time() - started, 2),
            n_t=int(bold.shape[0]),
        )
    except Exception as exc:  # noqa: BLE001
        await app.state.registry.update(
            scan_id,
            status="failed",
            error={"message": str(exc), "type": exc.__class__.__name__},
        )


def create_app(registry: Registry | None = None) -> FastAPI:
    app = FastAPI(title="Cortex Cloud TRIBE Worker", version="0.1.0")
    app.state.registry = registry or Registry()

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        records = await app.state.registry.list_recent(limit=1000)
        readiness = tribe_readiness()
        return {
            "ok": readiness["ok"],
            "mode": WORKER_MODE,
            "provider": WORKER_PROVIDER,
            "n_vertices": N_VERTICES,
            "tr_seconds": TR_SECONDS,
            "queue_depth": sum(1 for item in records if item.status == "queued"),
            "active": sum(1 for item in records if item.status == "running"),
            "readiness": readiness,
        }

    @app.get("/api/tribe/readiness")
    async def tribe_readiness_endpoint() -> dict[str, Any]:
        return tribe_readiness()

    @app.get("/api/tribe/status")
    async def tribe_status() -> dict[str, Any]:
        health = await healthz()
        readiness = health["readiness"]
        return {
            "ok": readiness["ok"],
            "pc_online": False,
            "cloud_worker": True,
            "tribe_loaded": WORKER_MODE == "real" and readiness["real_mode_ready"],
            "tribe_ready": readiness["ok"],
            "can_warm_tribe": True,
            "message": (
                "Cloud TRIBE worker is reachable."
                if readiness["ok"]
                else "Cloud worker is reachable, but real TRIBE mode is not ready."
            ),
            **health,
        }

    @app.post("/api/scan", dependencies=[Depends(require_bearer)])
    async def submit_scan(
        file: UploadFile = File(...),
        tier: int = Form(default=1),
        source: str = Form(default="cloud"),
        narration_model: str = Form(default=""),
        compute_target: str = Form(default="cloud"),
    ) -> JSONResponse:
        scan_id = uuid.uuid4().hex[:12]
        suffix = Path(file.filename or "upload.bin").suffix or ".bin"
        target = UPLOAD_DIR / f"{scan_id}{suffix}"
        with target.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        await file.close()

        record = ScanRecord(
            id=scan_id,
            filename=file.filename or target.name,
            tier=tier,
            source=source,
            narration_model=narration_model,
            compute_target=compute_target,
            analysis_mode=_analysis_mode(file.filename or target.name, file.content_type),
        )
        await app.state.registry.put(record)
        asyncio.create_task(_process_scan(app, scan_id, target))
        return JSONResponse(
            {
                "ok": True,
                "scan_id": scan_id,
                "status": "queued",
                "analysis_mode": record.analysis_mode,
                "compute_target": compute_target,
            },
            status_code=202,
        )

    @app.get("/api/scan/{scan_id}", dependencies=[Depends(require_bearer)])
    async def get_scan(scan_id: str) -> dict[str, Any]:
        record = await app.state.registry.get(scan_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
        return record.to_dict()

    @app.get("/api/scans", dependencies=[Depends(require_bearer)])
    async def list_scans(limit: int = 50, status: str = "all") -> dict[str, Any]:
        records = await app.state.registry.list_recent(limit=limit)
        rows = [item.to_dict() for item in records if status == "all" or item.status == status]
        return {"ok": True, "scans": rows[-limit:][::-1]}

    @app.get("/api/scan/{scan_id}/bold-vertex", dependencies=[Depends(require_bearer)])
    async def bold_vertex(scan_id: str, n_t: int = 100) -> Response:
        record = await app.state.registry.get(scan_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
        path = SCAN_DIR / f"{scan_id}.npy"
        if not path.exists():
            raise HTTPException(status_code=404, detail="per-vertex preds not ready")
        arr = np.load(path, mmap_mode="r")
        sliced = _slice_bold(arr, n_t)
        return Response(
            content=sliced.tobytes(),
            media_type="application/octet-stream",
            headers={
                "X-N-T": str(sliced.shape[0]),
                "X-N-Vert": str(sliced.shape[1]),
                "X-Scan-Id": scan_id,
                "Cache-Control": "public, max-age=300",
            },
        )

    @app.get("/api/scan/{scan_id}/bold-simulate", dependencies=[Depends(require_bearer)])
    async def bold_simulate(scan_id: str, n_t: int = 100) -> dict[str, Any]:
        path = SCAN_DIR / f"{scan_id}.npy"
        if path.exists():
            arr = _slice_bold(np.load(path, mmap_mode="r"), n_t)
            rows = arr[:, :8].astype(float).tolist()
        else:
            arr = _fake_bold(scan_id, n_t=max(2, min(n_t, 100)), n_vertices=8)
            rows = arr.astype(float).tolist()
        return {
            "scan_id": scan_id,
            "n_t": len(rows),
            "n_regions": 8,
            "region_ids": YEO_ROIS,
            "bold": rows,
            "tr_seconds": TR_SECONDS,
            "simulated": not path.exists(),
        }

    @app.get("/api/scan/{scan_id}/source-media", dependencies=[Depends(require_bearer)])
    async def source_media(scan_id: str) -> FileResponse:
        for path in UPLOAD_DIR.glob(f"{scan_id}.*"):
            return FileResponse(
                str(path),
                media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
        raise HTTPException(status_code=404, detail="source media not available")

    return app


app = create_app()
