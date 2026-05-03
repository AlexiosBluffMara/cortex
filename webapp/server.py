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
    from google.cloud import firestore as _firestore
    from google.cloud import storage as _gcs
    _GCP_AVAILABLE = True
except ImportError:
    _GCP_AVAILABLE = False

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cortex import media_gate as _media_gate
from cortex import prompts as _prompts
from cortex import tiers as _tiers
from cortex.analysis import analyse
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

    @app.get("/api/utilization")
    async def utilization() -> dict[str, Any]:
        """Public endpoint used by the Cloud Run relay to decide whether to route
        a job to the local 5090 or fall back to cloud.

        Returns:
            accepting      – True if the local GPU will accept new jobs now
            queue_depth    – number of requests currently queued
            max_queue      – maximum queue depth before rejection
            scheduler_state– "idle" | "running" | "overloaded"
            vram           – VRAM stats dict from the scheduler
        """
        q_status = _queue.status()
        vram = _scheduler.vram_report()
        depth = q_status.get("queued", 0)
        max_q = q_status.get("max_queue", 8)
        running = q_status.get("running", 0)
        total_load = depth + running
        if total_load == 0:
            state = "idle"
        elif total_load < max_q:
            state = "running"
        else:
            state = "overloaded"
        return {
            "accepting": state != "overloaded",
            "queue_depth": depth,
            "running": running,
            "max_queue": max_q,
            "scheduler_state": state,
            "vram": vram,
        }

    @app.get("/api/gpu/telemetry")
    async def gpu_telemetry() -> dict[str, Any]:
        """Live nvidia-smi telemetry for the Twitch / OBS overlay at /specs.html.

        Returns: temp_c, power_w, clock_mhz, mem_clock_mhz, fan_pct, util_gpu_pct.
        Falls back to {} when nvidia-smi is unavailable (e.g. CPU-only host).
        """
        import shutil
        import subprocess

        nvsmi = shutil.which("nvidia-smi")
        if not nvsmi:
            return {}

        try:
            out = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [
                        nvsmi,
                        "--query-gpu="
                        "temperature.gpu,power.draw,clocks.gr,clocks.mem,fan.speed,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2,
                ),
            )
            line = (out.stdout or "").strip().splitlines()[0]
            parts = [p.strip() for p in line.split(",")]

            def _f(s: str) -> float | None:
                try:
                    return float(s)
                except (ValueError, TypeError):
                    return None

            return {
                "temp_c":        _f(parts[0]),
                "power_w":       _f(parts[1]),
                "clock_mhz":     _f(parts[2]),
                "mem_clock_mhz": _f(parts[3]),
                "fan_pct":       _f(parts[4]),
                "util_gpu_pct":  _f(parts[5]),
            }
        except Exception as exc:
            log.debug("[webapp] nvidia-smi telemetry failed: %s", exc)
            return {}

    # -----------------------------------------------------------------------
    # Scan submission
    # -----------------------------------------------------------------------

    @app.post("/api/scan")
    async def submit_scan(
        file: UploadFile = File(...),
        tier: int = Form(default=1, ge=0, le=6),
        source: str = Form(default="webui"),
        narration_model: str = Form(default="local:gemma4:e4b"),
        external_scan_id: str = Form(default=""),
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
                "narration_model": narration_model,
                "size_mb": round(size / (1024 * 1024), 2),
            },
        )

        # Mirror the queued state to Firestore so local-direct scans appear in
        # the public gallery alongside relay-submitted scans. The relay creates
        # this doc itself; here we cover the localhost:8765 path. Best-effort —
        # if GCP is unavailable, the scan still runs locally.
        if _GCP_AVAILABLE:
            asyncio.create_task(
                _push_queued_to_firestore(scan_id, file.filename, tier, source)
            )

        # Fire-and-forget background task: run the brain scan, write result
        asyncio.create_task(_run_scan_background(
            app, scan_id, str(target), tier, source, narration_model,
            external_scan_id=external_scan_id or None,
        ))

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

    @app.get("/api/scans")
    async def list_scans(limit: int = 50, status: str = "complete") -> dict[str, Any]:
        """List recent scans (for the public gallery).

        Returns a compact summary per scan: id, filename, status, top_rois,
        peak_t, narrations (all 4 personas), seconds_elapsed.
        """
        ids = app.state.registry.all_ids()
        out = []
        for sid in ids:
            rec = await app.state.registry.get(sid)
            if rec is None:
                continue
            if status != "all" and rec.get("status") != status:
                continue
            out.append({
                "id": sid,
                "filename": rec.get("filename"),
                "status": rec.get("status"),
                "tier": rec.get("tier"),
                "top_rois": rec.get("top_rois", [])[:5],
                "peak_t": rec.get("peak_t"),
                "tr_seconds": rec.get("tr_seconds"),
                "n_t": rec.get("n_t"),
                "size_mb": rec.get("size_mb"),
                "seconds_elapsed": rec.get("seconds_elapsed"),
                "narrations": rec.get("narrations", {}),
                "created_at": rec.get("created_at"),
            })
        # Most recent first
        out.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
        return {"count": len(out), "scans": out[:limit]}

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

    @app.get("/api/scan/{scan_id}/manim-video")
    async def manim_video_endpoint(scan_id: str, scene: str = "BoldTimeseries") -> Response:
        """Serve the Manim brain activation explainer video for a completed scan.

        Scenes: BoldTimeseries | BrainNetworkDiagram
        Status 202 if still generating, 404 if not available.
        """
        manim_dir = Path("D:/cortex/scans/manim")
        for subdir in [
            manim_dir / "videos" / "manim_bold_scene" / "l480p15",
            manim_dir / "videos" / "manim_bold_scene" / "m720p30",
        ]:
            mp4 = subdir / f"{scene}.mp4"
            if mp4.exists():
                return Response(
                    content=mp4.read_bytes(),
                    media_type="video/mp4",
                    headers={"Cache-Control": "public, max-age=86400", "X-Scan-Id": scan_id},
                )
        npy = Path("D:/cortex/scans") / f"{scan_id}.npy"
        if npy.exists():
            return Response(
                content=b'{"status":"generating"}',
                media_type="application/json",
                status_code=202,
            )
        raise HTTPException(status_code=404, detail="manim-video not available for this scan")

    @app.get("/api/scan/{scan_id}/ascii-video")
    async def ascii_video_endpoint(scan_id: str) -> Response:
        """Serve the Hermes ascii-video BOLD visualization for a completed scan.

        Generated automatically after scan completes. Returns the .mp4 file.
        Status 202 if still generating, 404 if not available.
        """
        ascii_dir  = Path("D:/cortex/scans/ascii")
        mp4_path   = ascii_dir / f"{scan_id}_ascii.mp4"
        if mp4_path.exists():
            return Response(
                content=mp4_path.read_bytes(),
                media_type="video/mp4",
                headers={"Cache-Control": "public, max-age=86400", "X-Scan-Id": scan_id},
            )
        # Check if source .npy exists; if so, trigger generation
        npy = Path("D:/cortex/scans") / f"{scan_id}.npy"
        if npy.exists():
            return Response(
                content=b'{"status":"generating"}',
                media_type="application/json",
                status_code=202,
            )
        raise HTTPException(status_code=404, detail="ascii-video not available for this scan")

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
        except Exception as exc:
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
        narration_model: str = Form(default="local:gemma4:e4b"),
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
                "narration_model": narration_model,
                "text": text.strip()[:1000],
            },
        )
        asyncio.create_task(
            _run_text_scan_background(app, scan_id, text.strip()[:1000], tier, source, narration_model)
        )
        await app.state.hub.broadcast(
            {"type": "scan_queued", "scan_id": scan_id, "filename": "<text stimulus>"}
        )
        return JSONResponse({"ok": True, "scan_id": scan_id, "status": "queued"}, status_code=202)

    # -----------------------------------------------------------------------
    # TRIBE v2 fine-tune kickoff (called by training_trigger.py from the cloud)
    # -----------------------------------------------------------------------

    @app.post("/api/training/start")
    async def start_training(payload: dict[str, Any]) -> JSONResponse:
        scan_ids = list(payload.get("scan_ids", []))
        if not scan_ids:
            return JSONResponse({"error": "scan_ids required"}, status_code=400)
        # Schedule it through the request queue so we don't collide with
        # in-flight inference jobs on the GPU.
        job_id = f"train-{uuid.uuid4().hex[:10]}"
        log.info(
            "[webapp] training/start job=%s scans=%d mode=%s",
            job_id, len(scan_ids), payload.get("mode", "fmri-fine-tune"),
        )

        async def _kickoff() -> None:
            from cortex.train_tribe import _run as run_train  # noqa: PLC0415

            try:
                run_train(scan_ids)
            except Exception as exc:  # noqa: BLE001
                log.exception("[webapp] training failed: %s", exc)

        asyncio.create_task(_kickoff())
        return JSONResponse({"ok": True, "job_id": job_id, "n_scans": len(scan_ids)},
                            status_code=202)

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

    # AdSense / SEO files served regardless of PUBLIC_DIR
    from fastapi.responses import PlainTextResponse

    @app.get("/ads.txt")
    async def ads_txt() -> PlainTextResponse:
        return PlainTextResponse(
            "google.com, pub-7794155680942670, DIRECT, f08c47fec0942fa0\n",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/robots.txt")
    async def robots_txt() -> PlainTextResponse:
        return PlainTextResponse(
            "User-agent: *\nAllow: /\nUser-agent: Mediapartners-Google\nAllow: /\n"
            "Sitemap: https://cortex.redteamkitchen.com/sitemap.xml\n",
            headers={"Cache-Control": "public, max-age=86400"},
        )

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


async def _narrate_with_model(
    model: str,
    prompt: str,
    system: str,
    tier: int,
    num_predict: int,
    temperature: float,
    queue: "RequestQueue",
    source: str,
) -> str:
    """Route a narration request to local Ollama, Gemini API, or OpenRouter."""
    prefix = model.split(":")[0]

    if prefix == "local":
        return await queue.submit(
            request_type=RequestType.NARRATE,
            payload={
                "prompt":      prompt,
                "system":      system,
                "tier":        tier,
                "num_predict": num_predict,
                "temperature": temperature,
                "model":       model[len("local:"):],  # e.g. "gemma4:e4b"
            },
            priority=0 if source == "webui" else 5,
            source=source,
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": prompt},
    ]

    if prefix == "gemini":
        import os
        import httpx
        gemini_model = model[len("gemini:"):]
        api_key = os.environ.get("GEMINI_API_KEY", "")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{gemini_model}:generateContent?key={api_key}"
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"maxOutputTokens": num_predict, "temperature": temperature},
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    if prefix == "openrouter":
        import os
        import httpx
        or_model = model[len("openrouter:"):]
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            # fall back: try reading from ~/.hermes/.env
            env_path = Path.home() / ".hermes" / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("OPENROUTER_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        body = {
            "model": or_model,
            "messages": messages,
            "max_tokens": num_predict,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://cortex.redteamkitchen.com",
            "X-Title": "Cortex",
        }
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    # Unknown prefix — fall back to local queue
    return await queue.submit(
        request_type=RequestType.NARRATE,
        payload={
            "prompt":      prompt,
            "system":      system,
            "tier":        tier,
            "num_predict": num_predict,
            "temperature": temperature,
        },
        priority=0 if source == "webui" else 5,
        source=source,
    )


async def _run_image_scan_background(
    app: FastAPI,
    scan_id: str,
    media_path: str,
    tier: int,
    source: str,
    narration_model: str = "local:gemma4:e4b",
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
            narrations[persona_id] = await _narrate_with_model(
                model=narration_model,
                prompt=user_prompt,
                system=sys_prompt,
                tier=tier_n,
                num_predict=_tiers._TIER_NUM_PREDICT[tier_n],
                temperature=_tiers._TIER_TEMPERATURE[tier_n],
                queue=queue,
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
    narration_model: str = "local:gemma4:e4b",
) -> None:
    """Document scan: extract text, then narrates expected neural correlates."""
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
            narrations[persona_id] = await _narrate_with_model(
                model=narration_model,
                prompt=user_prompt,
                system=sys_prompt,
                tier=tier_n,
                num_predict=_tiers._TIER_NUM_PREDICT[tier_n],
                temperature=_tiers._TIER_TEMPERATURE[tier_n],
                queue=queue,
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


async def _generate_manim_video(
    scan_id: str,
    peak_t: int | None,
) -> None:
    """Fire-and-forget: generate Manim brain explainer videos in background."""
    try:
        from cortex.manim_brain import render_bold_explainer
        loop = asyncio.get_event_loop()
        npy_path = Path("D:/cortex/scans") / f"{scan_id}.npy"
        if not npy_path.exists():
            return
        for scene in ("BoldTimeseries", "BrainNetworkDiagram"):
            out = await loop.run_in_executor(
                None,
                lambda s=scene: render_bold_explainer(
                    bold_npy=npy_path,
                    output_dir="D:/cortex/scans/manim",
                    peak_t=peak_t,
                    scene=s,
                    quality="l",
                )
            )
            if out:
                log.info("[webapp] manim %s generated for %s → %s", scene, scan_id, out)
    except Exception as _e:
        log.debug("[webapp] manim generation failed for %s: %s", scan_id, _e)


async def _generate_ascii_video(
    scan_id: str,
    bold: Any,                  # numpy float32 (T, 20484)
    peak_t: int | None,
) -> None:
    """Fire-and-forget: generate Hermes ascii-video BOLD visualization in background."""
    try:
        from cortex.ascii_video import generate_for_scan
        npy_path = Path("D:/cortex/scans") / f"{scan_id}.npy"
        out = await generate_for_scan(
            scan_id=scan_id,
            bold_path=npy_path,
            output_dir="D:/cortex/scans/ascii",
            peak_t=peak_t,
            resolution="480p",
        )
        if out:
            log.info("[webapp] ascii-video generated for %s → %s", scan_id, out)
        else:
            log.debug("[webapp] ascii-video skipped for %s (no output)", scan_id)
    except Exception as _e:
        log.debug("[webapp] ascii-video generation failed for %s: %s", scan_id, _e)


async def _push_queued_to_firestore(
    scan_id: str,
    filename: str,
    tier: int,
    source: str,
) -> None:
    """Write a 'queued' marker for a local-direct scan to Firestore.

    The relay writes this doc itself for relay-submitted scans. This helper
    covers the localhost:8765 path so direct uploads also appear in the
    public gallery. Best-effort — failures are logged and swallowed.
    """
    if not _GCP_AVAILABLE:
        return
    import os

    project = os.environ.get("GCP_PROJECT", "abm-isu")
    loop    = asyncio.get_event_loop()

    def _sync_set():
        fs_client = _firestore.Client(project=project)
        fs_client.collection("scans").document(scan_id).set(
            {
                "id": scan_id,
                "status": "queued",
                "filename": filename,
                "tier": tier,
                "source": source,
                "cost_mode": "local",
                "submitted_via": "local-direct",
                "created_at": _firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

    try:
        await loop.run_in_executor(None, _sync_set)
    except Exception as exc:
        log.warning("[webapp] firestore queued-mirror failed for %s: %s", scan_id, exc)


async def _push_to_gcp(
    scan_id: str,
    result: Any,
    narrations: dict[str, str],
    external_scan_id: str | None = None,
) -> str | None:
    """Push scan results to GCS + Firestore. Returns thumbnail_url if generated."""
    if not _GCP_AVAILABLE:
        return None
    import io
    import os

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

                    # ISU-branded diverging colormap (matches main.js zToRGB)
                    # Negative (suppressed blood flow): neutral dark -> ISU Blue #56758f
                    # Positive (activated):             neutral dark -> ISU Gold #F6A917 -> ISU Red #CC0000
                    z_scale = max(float(_np.abs(frame).max()), 0.0001)

                    def _z2rgb(z_raw_val: float) -> tuple:
                        t = max(-1.0, min(1.0, z_raw_val / z_scale))
                        m = abs(t) ** 0.65
                        BR, BG, BB = 0.17, 0.17, 0.23
                        if t >= 0:
                            if m < 0.55:
                                p = m / 0.55
                                r = BR + p * (0.965 - BR)
                                g = BG + p * (0.663 - BG)
                                b = BB + p * (0.090 - BB)
                            else:
                                p = (m - 0.55) / 0.45
                                r = 0.965 - p * (0.965 - 0.80)
                                g = 0.663 - p * 0.663
                                b = 0.090 - p * 0.090
                        else:
                            r = BR - m * (BR - 0.337)
                            g = BG - m * (BG - 0.459)
                            b = BB + m * (0.561 - BB)
                        return (int(r * 255), int(g * 255), int(b * 255))

                    side = 143                                       # 143*143=20449; close to 20484
                    n_v  = min(len(frame), side * side)
                    rgb  = _np.zeros((side, side, 3), dtype=_np.uint8)
                    for i in range(n_v):
                        r, c = divmod(i, side)
                        rgb[r, c] = _z2rgb(float(frame[i]))

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

        # If this scan was submitted via the public relay, also write back to
        # the relay's Firestore doc so thumbnail_url + top_rois appear there.
        if external_scan_id and external_scan_id != scan_id:
            relay_update = {k: update[k] for k in
                            ["status", "narrations", "top_rois", "peak_t", "tr_seconds",
                             "thumbnail_url", "npy_url"]
                            if k in update}
            if relay_update:
                fs_client.collection("scans").document(external_scan_id).set(
                    relay_update, merge=True
                )

        return update.get("thumbnail_url")

    return await loop.run_in_executor(None, _sync_push)


async def _run_scan_background(
    app: FastAPI,
    scan_id: str,
    media_path: str,
    tier: int,
    source: str,
    narration_model: str = "local:gemma4:e4b",
    external_scan_id: str | None = None,
) -> None:
    """Run a brain scan in the background and stream progress to WebSocket clients."""
    suffix = Path(media_path).suffix.lower()
    if suffix in _IMAGE_EXTS:
        return await _run_image_scan_background(app, scan_id, media_path, tier, source, narration_model)
    if suffix in _DOCUMENT_EXTS:
        return await _run_document_scan_background(app, scan_id, media_path, tier, source, narration_model)

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
                bold_arr = _np.asarray(preds, dtype=_np.float32)
                _np.save(_scans_dir / f"{scan_id}.npy", bold_arr)
                log.info("[webapp] persisted preds for %s shape=%s", scan_id, bold_arr.shape)
                # Fire-and-forget: generate ASCII art video (Hermes ascii-video technique)
                asyncio.create_task(_generate_ascii_video(
                    scan_id, bold_arr, getattr(result, "peak_t", None)
                ))
                # Fire-and-forget: Manim brain explainer (Hermes Manim skill)
                asyncio.create_task(_generate_manim_video(
                    scan_id, getattr(result, "peak_t", None)
                ))
        except Exception as _exc:
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
            narrations[persona_id] = await _narrate_with_model(
                model=narration_model,
                prompt=user_prompt,
                system=sys_prompt,
                tier=tier_n,
                num_predict=_tiers._TIER_NUM_PREDICT[tier_n],
                temperature=_tiers._TIER_TEMPERATURE[tier_n],
                queue=queue,
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
            thumb_url = await _push_to_gcp(scan_id, result, narrations, external_scan_id)
            if thumb_url:
                await registry.update(scan_id, thumbnail_url=thumb_url)
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
    narration_model: str = "local:gemma4:e4b",
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

        narration = await _narrate_with_model(
            model=narration_model,
            prompt=user_prompt,
            system=system_prompt,
            tier=tier,
            num_predict=_tiers._TIER_NUM_PREDICT[tier],
            temperature=_tiers._TIER_TEMPERATURE[tier],
            queue=queue,
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
