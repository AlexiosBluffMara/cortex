"""Cortex webapp — FastAPI server.

Exposes the local TRIBE v2 + Gemma 4 pipeline over HTTP/WebSocket so the
Vite/Three.js viewer (and the public Cloudflare-fronted demo) can drive it.

Endpoints
---------
GET  /api/health           Health check + scheduler/queue status
POST /api/scan             Submit a media file for brain analysis
GET  /api/scan/{scan_id}   Look up the result of a previous scan
WS   /api/ws               Live updates: scheduler state, scan progress

Static
------
GET  /                     Three.js viewer (webapp/public/index.html)
GET  /assets/*             Vite bundle output

Run locally::

    uvicorn webapp.server:app --host 0.0.0.0 --port 8765 --reload
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cortex.errors import (
    CortexError,
    ErrorCode,
    file_too_large,
    invalid_file_type,
)
from cortex.gpu_scheduler import GPUScheduler, GPUState, get_scheduler
from cortex.logger import log
from cortex.request_queue import RequestQueue, RequestType, get_queue

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ALLOWED_VIDEO = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
ALLOWED_AUDIO = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_EXTS = ALLOWED_VIDEO | ALLOWED_AUDIO | ALLOWED_IMAGE
MAX_UPLOAD_MB = 50
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
PUBLIC_DIR = Path(__file__).resolve().parent / "public"
UPLOAD_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

class ScanRegistry:
    """In-memory store of scan results, keyed by scan_id.

    For the hackathon demo this is fine; production would back this with Redis
    or Firestore + a TTL on each entry.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def put(self, scan_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._store[scan_id] = payload

    async def update(self, scan_id: str, **fields: Any) -> None:
        async with self._lock:
            if scan_id in self._store:
                self._store[scan_id].update(fields)

    async def get(self, scan_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return self._store.get(scan_id)

    def all_ids(self) -> list[str]:
        return list(self._store.keys())


class WebSocketHub:
    """Fan-out hub for live updates. Every connected client receives every event."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        async with self._lock:
            dead: list[WebSocket] = []
            for client in self._clients:
                try:
                    await client.send_json(event)
                except Exception:
                    dead.append(client)
            for client in dead:
                self._clients.discard(client)

    @property
    def connection_count(self) -> int:
        return len(self._clients)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Wire scheduler state-change notifications to the WebSocket hub
    scheduler: GPUScheduler = get_scheduler()
    hub: WebSocketHub = app.state.hub  # type: ignore[attr-defined]

    def _on_state_change(state: GPUState) -> None:
        # Fire-and-forget broadcast — listeners are sync, hub is async
        try:
            asyncio.get_event_loop().create_task(
                hub.broadcast({"type": "scheduler_state", "state": state.value})
            )
        except RuntimeError:
            pass  # No running loop (happens during shutdown)

    scheduler.on_state_change(_on_state_change)
    log.info("[webapp] startup complete (scheduler=%s)", scheduler.state.value)
    yield
    log.info("[webapp] shutdown")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    *,
    queue: RequestQueue | None = None,
    scheduler: GPUScheduler | None = None,
) -> FastAPI:
    """Build the FastAPI app. Injectable for tests."""
    _queue = queue or get_queue()
    _scheduler = scheduler or get_scheduler()

    app = FastAPI(
        title="Cortex",
        description="Brain-response analysis with Gemma 4 + TRIBE v2",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.queue = _queue
    app.state.scheduler = _scheduler
    app.state.registry = ScanRegistry()
    app.state.hub = WebSocketHub()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # locked down per-deployment via reverse proxy
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -----------------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": "0.1.0",
            "gpu": _scheduler.vram_report(),
            "queue": _queue.status(),
            "websocket_clients": app.state.hub.connection_count,
        }

    # -----------------------------------------------------------------------
    # Scan submission
    # -----------------------------------------------------------------------

    @app.post("/api/scan")
    async def submit_scan(
        file: UploadFile = File(...),
        tier: int = Form(default=1, ge=0, le=6),
        source: str = Form(default="webui"),
    ) -> JSONResponse:
        if not file.filename:
            err = invalid_file_type("(no filename)", component="webapp")
            return JSONResponse(err.to_dict(), status_code=400)

        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTS:
            err = invalid_file_type(file.filename, component="webapp")
            return JSONResponse(err.to_dict(), status_code=400)

        # Stream upload to disk while enforcing the size cap. Track oversize
        # via a flag so we exit the `with` block before unlinking — on Windows,
        # a file with an open handle can't be deleted.
        scan_id = uuid.uuid4().hex[:12]
        target = UPLOAD_DIR / f"{scan_id}{ext}"
        size = 0
        oversized = False
        try:
            with target.open("wb") as fh:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        oversized = True
                        break
                    fh.write(chunk)
        finally:
            await file.close()

        if oversized:
            target.unlink(missing_ok=True)
            err = file_too_large(
                size_mb=size / (1024 * 1024),
                max_mb=MAX_UPLOAD_MB,
                component="webapp",
            )
            return JSONResponse(err.to_dict(), status_code=413)

        await app.state.registry.put(
            scan_id,
            {
                "id": scan_id,
                "status": "queued",
                "filename": file.filename,
                "tier": tier,
                "source": source,
                "size_mb": round(size / (1024 * 1024), 2),
            },
        )

        # Fire-and-forget background task: run the brain scan, write result
        asyncio.create_task(_run_scan_background(app, scan_id, str(target), tier, source))

        await app.state.hub.broadcast(
            {"type": "scan_queued", "scan_id": scan_id, "filename": file.filename}
        )

        return JSONResponse(
            {"ok": True, "scan_id": scan_id, "status": "queued"},
            status_code=202,
        )

    # -----------------------------------------------------------------------
    # Scan lookup
    # -----------------------------------------------------------------------

    @app.get("/api/scan/{scan_id}")
    async def get_scan(scan_id: str) -> dict[str, Any]:
        record = await app.state.registry.get(scan_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
        return record

    # -----------------------------------------------------------------------
    # Atlas + simulated BOLD (drives the Three.js viewer)
    # -----------------------------------------------------------------------

    @app.get("/api/atlas")
    async def get_atlas() -> Any:
        """Return the Schaefer-style stand-in atlas the viewer renders against.

        The viewer fetches this once on load. Replacing the file on disk is
        the canonical way to swap in the real Schaefer-400 + Yeo-7 lookup.
        """
        atlas_file = PUBLIC_DIR / "atlas.json"
        if not atlas_file.exists():
            raise HTTPException(status_code=404, detail="atlas.json missing")
        import json as _json
        return _json.loads(atlas_file.read_text(encoding="utf-8"))

    @app.get("/api/scan/{scan_id}/bold-simulate")
    async def simulate_bold(scan_id: str, n_t: int = 100) -> dict[str, Any]:
        """Return a deterministic, scan-id-keyed simulated BOLD trace for demos.

        Real scans get their actual TRIBE v2 predictions. This endpoint exists
        so the time scrubber can demo on the placeholder index page even when
        no real inference has run. Trace shape: (n_t, n_regions).
        """
        atlas_file = PUBLIC_DIR / "atlas.json"
        if not atlas_file.exists():
            raise HTTPException(status_code=404, detail="atlas.json missing")
        import json as _json
        import math
        atlas = _json.loads(atlas_file.read_text(encoding="utf-8"))
        regions = atlas["regions"]

        # Seed the trace deterministically from the scan_id so reloads animate
        # the same way.
        seed = sum(ord(c) for c in scan_id) or 1
        n_t = max(8, min(n_t, 512))
        bold = []
        for t in range(n_t):
            row = []
            for i, _r in enumerate(regions):
                # Each region gets a phase-shifted gaussian "burst" centered
                # at a different time, plus a low-frequency drift.
                centre = (seed * (i + 1)) % n_t
                width = 6.0 + ((seed + i) % 5)
                burst = math.exp(-((t - centre) ** 2) / (2 * width * width))
                drift = 0.15 * math.sin(0.06 * (t + seed % 17) + i)
                row.append(round(0.85 * burst + drift, 4))
            bold.append(row)

        return {
            "scan_id": scan_id,
            "n_t": n_t,
            "n_regions": len(regions),
            "region_ids": [r["id"] for r in regions],
            "bold": bold,                        # (n_t, n_regions)
            "tr_seconds": 0.5,                   # 2 Hz, matching TRIBE v2
            "simulated": True,
        }

    # -----------------------------------------------------------------------
    # WebSocket
    # -----------------------------------------------------------------------

    @app.websocket("/api/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        hub: WebSocketHub = app.state.hub
        await hub.connect(ws)
        # Send initial state on connection
        await ws.send_json(
            {
                "type": "hello",
                "scheduler_state": _scheduler.state.value,
                "queue": _queue.status(),
            }
        )
        try:
            while True:
                # Drain incoming pings; we don't actually use client messages yet.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(ws)

    # -----------------------------------------------------------------------
    # Static viewer
    # -----------------------------------------------------------------------

    if PUBLIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=PUBLIC_DIR), name="assets")

        @app.get("/")
        async def index() -> FileResponse:
            index_html = PUBLIC_DIR / "index.html"
            if not index_html.exists():
                raise HTTPException(status_code=404, detail="Viewer not built")
            return FileResponse(str(index_html))

    return app


# ---------------------------------------------------------------------------
# Background scan runner
# ---------------------------------------------------------------------------

async def _run_scan_background(
    app: FastAPI,
    scan_id: str,
    media_path: str,
    tier: int,
    source: str,
) -> None:
    """Run a brain scan in the background and stream progress to WebSocket clients."""
    queue: RequestQueue = app.state.queue
    registry: ScanRegistry = app.state.registry
    hub: WebSocketHub = app.state.hub

    async def _emit(phase: str, **extra: Any) -> None:
        await hub.broadcast({"type": "scan_progress", "scan_id": scan_id, "phase": phase, **extra})
        await registry.update(scan_id, status=phase)

    try:
        await _emit("running")
        result = await queue.submit(
            request_type=RequestType.BRAIN_SCAN,
            payload={"media_path": media_path},
            priority=0 if source == "webui" else 5,
            source=source,
        )
        await _emit("narrating")
        narration = await queue.submit(
            request_type=RequestType.NARRATE,
            payload={
                "prompt": "Narrate the brain response.",
                "system": "Be educational and accurate.",
                "tier": tier,
            },
            priority=0 if source == "webui" else 5,
            source=source,
        )

        await registry.update(
            scan_id,
            status="complete",
            top_rois=getattr(result, "top_rois", None),
            peak_t=getattr(result, "peak_t", None),
            seconds_elapsed=getattr(result, "seconds_elapsed", None),
            narration=narration,
        )
        await hub.broadcast({"type": "scan_complete", "scan_id": scan_id})
        log.info("[webapp] scan %s complete", scan_id)

    except Exception as exc:
        err = CortexError(
            code=ErrorCode.INFERENCE_FAILED,
            message=str(exc),
            component="webapp.background",
        )
        await registry.update(scan_id, status="failed", error=err.to_dict())
        await hub.broadcast(
            {"type": "scan_failed", "scan_id": scan_id, "error": err.to_dict()}
        )
        log.error("[webapp] scan %s failed: %s", scan_id, exc)


# Default app instance (used by `uvicorn webapp.server:app`)
app = create_app()
