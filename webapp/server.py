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

try:
    from google.cloud import firestore as _firestore, storage as _gcs
    _GCP_AVAILABLE = True
except ImportError:
    _GCP_AVAILABLE = False

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cortex.errors import (
    CortexError,
    ErrorCode,
    file_too_large,
    invalid_file_type,
)
from cortex.analysis import analyse
from cortex.gpu_scheduler import GPUScheduler, GPUState, get_scheduler
from cortex.logger import log
from cortex import media_gate as _media_gate, prompts as _prompts, tiers as _tiers
from cortex.request_queue import RequestQueue, RequestType, get_queue

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ALLOWED_VIDEO    = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".gif",
                    ".ts", ".m4v", ".3gp", ".ogv", ".flv", ".wmv", ".divx"}
ALLOWED_AUDIO    = {".mp3", ".wav", ".flac", ".ogg", ".m4a",
                    ".aac", ".wma", ".opus", ".ac3", ".aiff", ".aif"}
ALLOWED_IMAGE    = {".jpg", ".jpeg", ".png", ".webp",
                    ".bmp", ".tiff", ".tif", ".heic", ".heif", ".avif"}
ALLOWED_TEXT     = {".txt", ".md", ".srt", ".vtt"}
ALLOWED_DOCUMENT = {".html", ".htm", ".pdf"}
ALLOWED_EXTS     = ALLOWED_VIDEO | ALLOWED_AUDIO | ALLOWED_IMAGE | ALLOWED_TEXT | ALLOWED_DOCUMENT
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
    # Narrations lookup
    # -----------------------------------------------------------------------

    @app.get("/api/scan/{scan_id}/narrations")
    async def get_narrations(scan_id: str) -> dict[str, Any]:
        record = await app.state.registry.get(scan_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
        narrations = record.get("narrations") or {}
        if not narrations and record.get("narration"):
            narrations = {"college": record["narration"]}
        return {"scan_id": scan_id, "narrations": narrations, "status": record.get("status")}

    # -----------------------------------------------------------------------
    # Model / server info
    # -----------------------------------------------------------------------

    @app.get("/api/info")
    async def model_info() -> dict[str, Any]:
        return {
            "tribe_v2": {
                "sample_rate_hz": 2.0,
                "tr_seconds": 0.5,
                "n_vertices": 20484,
                "surface": "fsaverage5",
                "hrf_lag_seconds": 5.0,
                "training_subjects": 25,
                "max_input_seconds_practical": 120,
                "description": (
                    "TRIBE v2 predicts fsaverage5 BOLD at 2 Hz. t=N in the timeseries "
                    "corresponds to N × 0.5 seconds of predicted cortical response. "
                    "A 5-second hemodynamic lag is pre-applied, so predictions are "
                    "temporally aligned to the stimulus. "
                    "E.g. peak_t=7 → peak activation at 3.5 s; peak_t=11 → 5.5 s."
                ),
            },
            "gemma": {
                "fast_model": "gemma4:e4b",
                "fast_speed_toks": 194,
                "tiers": {
                    "0-1": "E4B fast model",
                    "2-4": "26B MoE deep model",
                    "5-6": "31B dense expert model",
                },
                "context_limits": {
                    "tier_0_1": 4096,
                    "tier_2_4": 8192,
                    "tier_5": 16384,
                    "tier_6": 32768,
                },
            },
            "upload": {
                "max_mb": 50,
                "max_duration_seconds_practical": 120,
                "accepted_types": ["video", "audio", "image", "pdf", "text"],
            },
            "prod_readiness_notes": [
                "ScanRegistry is in-memory: restart loses all records. Add Redis/Firestore TTL for prod.",
                "GPU scheduler supports one active model at a time; parallel requests queue.",
                "TRIBE v2 is group-averaged (25 subjects): not a personal diagnostic tool.",
                "Max practical video: ~2 min at 2Hz = 240 timepoints × 20484 verts × 4B = ~20 MB/scan.",
            ],
        }

    # -----------------------------------------------------------------------
    # Re-narrate an existing scan at a different tier
    # -----------------------------------------------------------------------

    @app.post("/api/scan/{scan_id}/narrate")
    async def re_narrate(scan_id: str, tier: int = 1) -> dict[str, Any]:
        record = await app.state.registry.get(scan_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
        if record.get("status") != "complete":
            raise HTTPException(status_code=409, detail="Scan not complete yet")

        tier = max(0, min(6, tier))

        # Rebuild brain context from the stored TRIBE result (top_rois + peak_t)
        # We don't have the InferenceResult object anymore, so we craft a minimal
        # brain_context string from what was persisted.
        top_rois = record.get("top_rois") or []
        peak_t   = record.get("peak_t")

        brain_ctx_lines = []
        if top_rois:
            brain_ctx_lines.append(f"top_rois: {top_rois[:6]}")
        if peak_t is not None:
            brain_ctx_lines.append(f"peak_t: {peak_t}")
        brain_ctx = "\n".join(brain_ctx_lines) or "No detailed brain context available."

        label        = record.get("filename", scan_id)
        user_prompt  = _prompts.TIER_USER_TEMPLATE.format(label=label, brain_context=brain_ctx)
        system_prompt= _prompts.ALL_TIER_SYSTEMS[tier]

        narration = await _queue.submit(
            request_type=RequestType.NARRATE,
            payload={
                "prompt":      user_prompt,
                "system":      system_prompt,
                "tier":        tier,
                "num_predict": _tiers._TIER_NUM_PREDICT[tier],
                "temperature": _tiers._TIER_TEMPERATURE[tier],
            },
            priority=0,
            source="webui-renarrate",
        )

        await app.state.registry.update(scan_id, narration=narration, tier=tier)
        return {"ok": True, "narration": narration, "tier": tier}

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

    @app.get("/api/scan/{scan_id}/bold-vertex")
    async def bold_vertex(scan_id: str, n_t: int = 100) -> Response:
        """Return the persisted per-vertex BOLD trace for `scan_id`.

        Shape on disk:  (T, 20484) float32  (fsaverage5 surface).
        Response body:  binary Float32 little-endian, row-major.
        Headers:        X-N-T, X-N-Vert, Content-Type=application/octet-stream

        If the .npy file is missing (e.g. webapp was restarted before this scan
        completed, or persistence failed) we 404 — the client falls back to
        the per-region `/bold-simulate` endpoint automatically.
        """
        scans_dir = Path("D:/cortex/scans")
        npy = scans_dir / f"{scan_id}.npy"
        if not npy.exists():
            raise HTTPException(status_code=404, detail=f"per-vertex preds not on disk for {scan_id}")
        try:
            import numpy as _np
            arr = _np.load(npy, mmap_mode="r")
        except Exception as exc:                                    # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"npy load failed: {exc}") from exc

        T_full = arr.shape[0]
        n_t = max(2, min(int(n_t), T_full))
        if n_t == T_full:
            sliced = arr
        else:
            # uniform-spaced index; we don't interpolate — clean integer picks
            idx = _np.linspace(0, T_full - 1, n_t).astype(_np.int64)
            sliced = arr[idx]

        # Force C-contiguous Float32 LE for predictable client decoding
        sliced = _np.ascontiguousarray(sliced, dtype="<f4")
        body = sliced.tobytes()
        return Response(
            content=body,
            media_type="application/octet-stream",
            headers={
                "X-N-T": str(sliced.shape[0]),
                "X-N-Vert": str(sliced.shape[1]),
                "X-Scan-Id": scan_id,
                "Cache-Control": "public, max-age=300",
            },
        )

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
    # Text-only scan submission
    # -----------------------------------------------------------------------

    @app.post("/api/text-scan")
    async def submit_text_scan(
        text: str = Form(...),
        tier: int = Form(default=1, ge=0, le=6),
        source: str = Form(default="webui"),
    ) -> JSONResponse:
        if not text.strip():
            return JSONResponse({"error": "empty text"}, status_code=400)
        scan_id = uuid.uuid4().hex[:12]
        await app.state.registry.put(
            scan_id,
            {
                "id": scan_id,
                "status": "queued",
                "filename": "<text stimulus>",
                "tier": tier,
                "source": source,
                "text": text.strip()[:1000],
            },
        )
        asyncio.create_task(
            _run_text_scan_background(app, scan_id, text.strip()[:1000], tier, source)
        )
        await app.state.hub.broadcast(
            {"type": "scan_queued", "scan_id": scan_id, "filename": "<text stimulus>"}
        )
        return JSONResponse({"ok": True, "scan_id": scan_id, "status": "queued"}, status_code=202)

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

_IMAGE_EXTS    = {'.jpg', '.jpeg', '.png', '.webp', '.gif',
                  '.bmp', '.tiff', '.tif', '.heic', '.heif', '.avif'}
_DOCUMENT_EXTS = {'.html', '.htm', '.pdf'}
_TEXT_EXTS     = {'.txt', '.md', '.srt', '.vtt'}   # routed through TRIBE text path


def _extract_document_text(path: Path) -> str:
    """Extract plain text from HTML or PDF for Gemma context."""
    suffix = path.suffix.lower()
    if suffix in {'.html', '.htm'}:
        raw = path.read_text(encoding='utf-8', errors='replace')
        import html.parser
        class _S(html.parser.HTMLParser):
            def __init__(self):
                super().__init__()
                self._parts, self._skip = [], False
            def handle_starttag(self, tag, attrs):
                if tag in ('script', 'style', 'head', 'nav', 'footer'):
                    self._skip = True
            def handle_endtag(self, tag):
                if tag in ('script', 'style', 'head', 'nav', 'footer'):
                    self._skip = False
            def handle_data(self, data):
                if not self._skip and data.strip():
                    self._parts.append(data.strip())
        s = _S()
        s.feed(raw)
        return ' '.join(s._parts)[:4000]
    if suffix == '.pdf':
        try:
            import fitz
            doc = fitz.open(str(path))
            return ' '.join(page.get_text() for page in doc).strip()[:4000]
        except ImportError:
            pass
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            return ' '.join(p.extract_text() or '' for p in reader.pages)[:4000]
        except ImportError:
            pass
        return f"[PDF document: {path.name} — no PDF library available for text extraction]"
    return path.read_text(encoding='utf-8', errors='replace')[:4000]


async def _run_image_scan_background(
    app: FastAPI,
    scan_id: str,
    media_path: str,
    tier: int,
    source: str,
) -> None:
    """Image scan: Gemma vision describes the image, then Gemma narrates neural correlates."""
    queue: RequestQueue = app.state.queue
    registry: ScanRegistry = app.state.registry
    hub: WebSocketHub = app.state.hub

    async def _emit(phase: str, **extra: Any) -> None:
        await hub.broadcast({"type": "scan_progress", "scan_id": scan_id, "phase": phase, **extra})
        await registry.update(scan_id, status=phase)

    try:
        await _emit("narrating")

        loop = asyncio.get_event_loop()
        desc = await loop.run_in_executor(
            None, lambda: _media_gate.classify_image(Path(media_path))
        )
        brain_ctx = (
            f"Input modality: image\n"
            f"Visual description: {desc.short_description()}\n\n"
            "No fMRI scan was performed. Based on cognitive neuroscience knowledge, "
            "describe the brain regions and networks expected to activate when a person "
            "views this image."
        )
        label = Path(media_path).name
        user_prompt = _prompts.TIER_USER_TEMPLATE.format(label=label, brain_context=brain_ctx)

        narrations: dict[str, str] = {}
        for persona_id, (tier_n, sys_prompt) in _prompts.PERSONA_CONFIGS.items():
            narrations[persona_id] = await queue.submit(
                request_type=RequestType.NARRATE,
                payload={
                    "prompt":      user_prompt,
                    "system":      sys_prompt,
                    "tier":        tier_n,
                    "num_predict": _tiers._TIER_NUM_PREDICT[tier_n],
                    "temperature": _tiers._TIER_TEMPERATURE[tier_n],
                },
                priority=0 if source == "webui" else 5,
                source=source,
            )

        await registry.update(scan_id, status="complete", narration=narrations.get("american", ""), narrations=narrations, top_rois=None, peak_t=None)
        await hub.broadcast({"type": "scan_complete", "scan_id": scan_id})
        await hub.broadcast({"type": "scan_narrations_ready", "scan_id": scan_id, "narrations": narrations})
        log.info("[webapp] image scan %s complete", scan_id)

    except Exception as exc:
        err = CortexError(code=ErrorCode.INFERENCE_FAILED, message=str(exc), component="webapp.image_scan")
        await registry.update(scan_id, status="failed", error=err.to_dict())
        await hub.broadcast({"type": "scan_failed", "scan_id": scan_id, "error": err.to_dict()})
        log.error("[webapp] image scan %s failed: %s", scan_id, exc)


async def _run_document_scan_background(
    app: FastAPI,
    scan_id: str,
    media_path: str,
    tier: int,
    source: str,
) -> None:
    """Document scan: extract text, then Gemma narrates expected neural correlates."""
    queue: RequestQueue = app.state.queue
    registry: ScanRegistry = app.state.registry
    hub: WebSocketHub = app.state.hub

    async def _emit(phase: str, **extra: Any) -> None:
        await hub.broadcast({"type": "scan_progress", "scan_id": scan_id, "phase": phase, **extra})
        await registry.update(scan_id, status=phase)

    try:
        await _emit("narrating")
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, lambda: _extract_document_text(Path(media_path)))
        label = Path(media_path).name
        brain_ctx = (
            f"Input modality: document\nFilename: {label}\n"
            f"Extracted content: \"{text}\"\n\n"
            "No fMRI scan was performed. Based on cognitive neuroscience knowledge, "
            "describe the brain regions and networks expected to activate when a person "
            "reads or engages with this content."
        )
        user_prompt = _prompts.TIER_USER_TEMPLATE.format(label=label, brain_context=brain_ctx)

        narrations: dict[str, str] = {}
        for persona_id, (tier_n, sys_prompt) in _prompts.PERSONA_CONFIGS.items():
            narrations[persona_id] = await queue.submit(
                request_type=RequestType.NARRATE,
                payload={
                    "prompt":      user_prompt,
                    "system":      sys_prompt,
                    "tier":        tier_n,
                    "num_predict": _tiers._TIER_NUM_PREDICT[tier_n],
                    "temperature": _tiers._TIER_TEMPERATURE[tier_n],
                },
                priority=0 if source == "webui" else 5,
                source=source,
            )

        await registry.update(scan_id, status="complete", narration=narrations.get("american", ""), narrations=narrations, top_rois=None, peak_t=None)
        await hub.broadcast({"type": "scan_complete", "scan_id": scan_id})
        await hub.broadcast({"type": "scan_narrations_ready", "scan_id": scan_id, "narrations": narrations})
        log.info("[webapp] document scan %s complete", scan_id)
    except Exception as exc:
        err = CortexError(code=ErrorCode.INFERENCE_FAILED, message=str(exc), component="webapp.doc_scan")
        await registry.update(scan_id, status="failed", error=err.to_dict())
        await hub.broadcast({"type": "scan_failed", "scan_id": scan_id, "error": err.to_dict()})
        log.error("[webapp] document scan %s failed: %s", scan_id, exc)


async def _push_to_gcp(
    scan_id: str,
    result: Any,
    narrations: dict[str, str],
) -> None:
    if not _GCP_AVAILABLE:
        return
    import os, io
    import numpy as _np

    bucket_name = os.environ.get("GCS_BUCKET", "cortex-public-scans")
    project     = os.environ.get("GCP_PROJECT", "abm-isu")

    loop = asyncio.get_event_loop()

    def _sync_push():
        gcs_client  = _gcs.Client(project=project)
        fs_client   = _firestore.Client(project=project)
        bucket      = gcs_client.bucket(bucket_name)

        update: dict[str, Any] = {
            "status":      "complete",
            "narrations":  narrations,
            "tr_seconds":  0.5,
        }

        preds = getattr(result, "preds", None)
        if preds is not None:
            arr = _np.asarray(preds, dtype=_np.float32)

            # Upload .npy
            npy_buf = io.BytesIO()
            _np.save(npy_buf, arr)
            npy_buf.seek(0)
            npy_blob = bucket.blob(f"bolddata/{scan_id}.npy")
            npy_blob.upload_from_file(npy_buf, content_type="application/octet-stream")
            update["npy_url"] = f"https://storage.googleapis.com/{bucket_name}/bolddata/{scan_id}.npy"

            # Thumbnail: map 20484 vertices → 200x200 via a flat 143×143 grid,
            # then letterbox to 200×200.  Uses peak_t frame z-scores + zToRGB logic.
            peak_t = getattr(result, "peak_t", None)
            if peak_t is not None:
                try:
                    from PIL import Image as _Image
                    frame = arr[int(peak_t)]                        # (20484,)
                    z = frame.copy()
                    z_min, z_max = z.min(), z.max()
                    if z_max > z_min:
                        z = (z - z_min) / (z_max - z_min)          # 0..1
                    else:
                        z = _np.zeros_like(z)

                    # zToRGB: blue→cyan→green→yellow→red
                    def _z2rgb(v):
                        if v < 0.25:
                            t = v / 0.25
                            return (0, int(t*255), 255)
                        elif v < 0.5:
                            t = (v - 0.25) / 0.25
                            return (0, 255, int((1-t)*255))
                        elif v < 0.75:
                            t = (v - 0.5) / 0.25
                            return (int(t*255), 255, 0)
                        else:
                            t = (v - 0.75) / 0.25
                            return (255, int((1-t)*255), 0)

                    side = 143                                       # 143*143=20449; close to 20484
                    n_v  = min(len(z), side * side)
                    rgb  = _np.zeros((side, side, 3), dtype=_np.uint8)
                    for i in range(n_v):
                        r, c = divmod(i, side)
                        rgb[r, c] = _z2rgb(float(z[i]))

                    img = _Image.fromarray(rgb, 'RGB').resize((200, 200), _Image.NEAREST)
                    thumb_buf = io.BytesIO()
                    img.save(thumb_buf, format='JPEG', quality=82)
                    thumb_buf.seek(0)
                    thumb_blob = bucket.blob(f"thumbnails/{scan_id}.jpg")
                    thumb_blob.upload_from_file(thumb_buf, content_type="image/jpeg")
                    update["thumbnail_url"] = f"https://storage.googleapis.com/{bucket_name}/thumbnails/{scan_id}.jpg"
                except Exception:
                    pass

        top_rois = getattr(result, "top_rois", None)
        peak_t   = getattr(result, "peak_t", None)
        if top_rois is not None:
            update["top_rois"] = top_rois
        if peak_t is not None:
            update["peak_t"] = int(peak_t)

        fs_client.collection("scans").document(scan_id).set(update, merge=True)

    await loop.run_in_executor(None, _sync_push)


async def _run_scan_background(
    app: FastAPI,
    scan_id: str,
    media_path: str,
    tier: int,
    source: str,
) -> None:
    """Run a brain scan in the background and stream progress to WebSocket clients."""
    suffix = Path(media_path).suffix.lower()
    if suffix in _IMAGE_EXTS:
        return await _run_image_scan_background(app, scan_id, media_path, tier, source)
    if suffix in _DOCUMENT_EXTS:
        return await _run_document_scan_background(app, scan_id, media_path, tier, source)

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

        # Persist the per-vertex BOLD trace so the WebUI can render the full
        # 20,484-vertex animation (not just the 50-region downsample) and so
        # that ?scan=<id> link-shares survive a webapp restart.
        try:
            import numpy as _np
            _scans_dir = Path("D:/cortex/scans")
            _scans_dir.mkdir(parents=True, exist_ok=True)
            preds = getattr(result, "preds", None)
            if preds is not None:
                _np.save(_scans_dir / f"{scan_id}.npy", _np.asarray(preds, dtype=_np.float32))
                log.info("[webapp] persisted preds for %s shape=%s", scan_id, preds.shape)
        except Exception as _exc:                                  # noqa: BLE001
            log.warning("[webapp] preds persist failed for %s: %s", scan_id, _exc)

        # Build full brain context so Gemma gets real data, not a generic prompt.
        loop = asyncio.get_event_loop()
        brain_ctx = await loop.run_in_executor(
            None,
            lambda: analyse(result, harvard_oxford=False, juelich=False).gemma_context(),
        )
        label = Path(media_path).name
        user_prompt = _prompts.TIER_USER_TEMPLATE.format(label=label, brain_context=brain_ctx)

        narrations: dict[str, str] = {}
        for persona_id, (tier_n, sys_prompt) in _prompts.PERSONA_CONFIGS.items():
            narrations[persona_id] = await queue.submit(
                request_type=RequestType.NARRATE,
                payload={
                    "prompt":      user_prompt,
                    "system":      sys_prompt,
                    "tier":        tier_n,
                    "num_predict": _tiers._TIER_NUM_PREDICT[tier_n],
                    "temperature": _tiers._TIER_TEMPERATURE[tier_n],
                },
                priority=0 if source == "webui" else 5,
                source=source,
            )

        preds = getattr(result, "preds", None)
        await registry.update(
            scan_id,
            status="complete",
            top_rois=getattr(result, "top_rois", None),
            peak_t=getattr(result, "peak_t", None),
            seconds_elapsed=getattr(result, "seconds_elapsed", None),
            narration=narrations.get("american", ""),
            narrations=narrations,
            tr_seconds=0.5,
            n_t=int(preds.shape[0]) if preds is not None else None,
        )
        await hub.broadcast({"type": "scan_complete", "scan_id": scan_id})
        await hub.broadcast({"type": "scan_narrations_ready", "scan_id": scan_id, "narrations": narrations})
        log.info("[webapp] scan %s complete", scan_id)

        try:
            await _push_to_gcp(scan_id, result, narrations)
            log.info("[webapp] GCP push complete for %s", scan_id)
        except Exception as _gcp_exc:
            log.warning("[webapp] GCP push failed for %s: %s", scan_id, _gcp_exc)

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


# ---------------------------------------------------------------------------
# Text-only scan (no TRIBE inference — Gemma predicts neural correlates from text)
# ---------------------------------------------------------------------------

async def _run_text_scan_background(
    app: FastAPI,
    scan_id: str,
    text: str,
    tier: int,
    source: str,
) -> None:
    queue: RequestQueue = app.state.queue
    registry: ScanRegistry = app.state.registry
    hub: WebSocketHub = app.state.hub

    async def _emit(phase: str, **extra: Any) -> None:
        await hub.broadcast({"type": "scan_progress", "scan_id": scan_id, "phase": phase, **extra})
        await registry.update(scan_id, status=phase)

    try:
        await _emit("narrating")

        brain_ctx = (
            f'Input modality: text\nContent: "{text}"\n\n'
            "No fMRI scan was performed. Based on cognitive neuroscience knowledge, "
            "describe the brain regions and networks expected to activate when a person "
            "reads, thinks about, or experiences this stimulus."
        )
        user_prompt   = _prompts.TIER_USER_TEMPLATE.format(label="text stimulus", brain_context=brain_ctx)
        system_prompt = _prompts.ALL_TIER_SYSTEMS[max(0, min(6, tier))]

        narration = await queue.submit(
            request_type=RequestType.NARRATE,
            payload={
                "prompt":      user_prompt,
                "system":      system_prompt,
                "tier":        tier,
                "num_predict": _tiers._TIER_NUM_PREDICT[tier],
                "temperature": _tiers._TIER_TEMPERATURE[tier],
            },
            priority=0 if source == "webui" else 5,
            source=source,
        )

        await registry.update(scan_id, status="complete", narration=narration, top_rois=None, peak_t=None)
        await hub.broadcast({"type": "scan_complete", "scan_id": scan_id})
        log.info("[webapp] text scan %s complete", scan_id)

    except Exception as exc:
        err = CortexError(code=ErrorCode.INFERENCE_FAILED, message=str(exc), component="webapp.text_scan")
        await registry.update(scan_id, status="failed", error=err.to_dict())
        await hub.broadcast({"type": "scan_failed", "scan_id": scan_id, "error": err.to_dict()})
        log.error("[webapp] text scan %s failed: %s", scan_id, exc)


# Default app instance (used by `uvicorn webapp.server:app`)
app = create_app()
