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
import base64
import mimetypes
import time
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
# TRIBE proxy (split-stack: Big Apple as public face, Seratonin runs TRIBE)
# ---------------------------------------------------------------------------
# When CORTEX_TRIBE_PROXY is set, video/audio/text scans (the ones that need
# TRIBE inference, which currently requires CUDA) are forwarded to that backend
# transparently. Image scans stay local because they bypass TRIBE entirely.
#
# Result: Big Apple keeps the public Funnel + image-scan capability + serves
# the gallery; Seratonin handles the heavy CUDA work; the user sees one URL.
import os as _os
TRIBE_PROXY_URL = (_os.environ.get("CORTEX_TRIBE_PROXY", "") or "").rstrip("/")
TRIBE_NEEDED_EXTS = ALLOWED_VIDEO | ALLOWED_AUDIO | ALLOWED_TEXT | ALLOWED_DOCUMENT
# (image scans use Gemma vision directly — no TRIBE needed)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"
OPENROUTER_MEDIA_MAX_MB = float(_os.environ.get("OPENROUTER_MEDIA_MAX_MB", "8"))
OPENROUTER_MODEL_CACHE_TTL_S = int(_os.environ.get("OPENROUTER_MODEL_CACHE_TTL_S", "1800"))

OPENROUTER_FREE_LIMITS = {
    "requests_per_minute": 20,
    "daily_without_10_credits": 50,
    "daily_with_10_credits": 1000,
    "credits_required_for_1000_per_day": 10,
    "account_note": "Free models require a positive credit balance; at least $10 purchased raises the daily free-model limit.",
    "source": "https://openrouter.ai/docs/api/reference/limits",
}

NARRATION_MODEL_CATALOG = [
    {
        "id": "openrouter:google/gemma-4-26b-a4b-it:free",
        "label": "Gemma 4 26B A4B",
        "provider": "OpenRouter",
        "group": "Free",
        "default": True,
        "modalities": ["text", "image", "video"],
        "context_length": 262144,
        "prompt_price": 0.0,
        "completion_price": 0.0,
        "notes": "Best default for Cortex narration: Gemma through OpenRouter, no local Gemma VRAM.",
    },
    {
        "id": "openrouter:google/gemma-4-31b-it:free",
        "label": "Gemma 4 31B",
        "provider": "OpenRouter",
        "group": "Free",
        "modalities": ["text", "image", "video"],
        "context_length": 262144,
        "prompt_price": 0.0,
        "completion_price": 0.0,
        "notes": "Stronger free Gemma option when available.",
    },
    {
        "id": "openrouter:openrouter/free",
        "label": "Free Models Router",
        "provider": "OpenRouter",
        "group": "Free",
        "modalities": ["text", "image"],
        "context_length": 200000,
        "prompt_price": 0.0,
        "completion_price": 0.0,
        "notes": "Lets OpenRouter pick an available free model that matches the request.",
    },
    {
        "id": "openrouter:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "label": "Nemotron 3 Nano Omni",
        "provider": "OpenRouter",
        "group": "Free",
        "modalities": ["text", "image", "audio", "video"],
        "context_length": 256000,
        "prompt_price": 0.0,
        "completion_price": 0.0,
        "notes": "Free multimodal fallback for audio/video source descriptions.",
    },
    {
        "id": "openrouter:nousresearch/hermes-3-llama-3.1-405b:free",
        "label": "Hermes 3 405B",
        "provider": "OpenRouter",
        "group": "Free",
        "modalities": ["text"],
        "context_length": 131072,
        "prompt_price": 0.0,
        "completion_price": 0.0,
        "notes": "Text-only free Nous/Hermes option.",
    },
    {
        "id": "openrouter:openai/gpt-oss-120b:free",
        "label": "gpt-oss 120B",
        "provider": "OpenRouter",
        "group": "Free",
        "modalities": ["text"],
        "context_length": 131072,
        "prompt_price": 0.0,
        "completion_price": 0.0,
        "notes": "Free text model for comparison.",
    },
    {
        "id": "openrouter:meta-llama/llama-3.3-70b-instruct:free",
        "label": "Llama 3.3 70B",
        "provider": "OpenRouter",
        "group": "Free",
        "modalities": ["text"],
        "context_length": 131072,
        "prompt_price": 0.0,
        "completion_price": 0.0,
        "notes": "Free general-purpose text model.",
    },
    {
        "id": "openrouter:google/gemma-4-26b-a4b-it",
        "label": "Gemma 4 26B A4B",
        "provider": "OpenRouter",
        "group": "Paid",
        "modalities": ["text", "image", "video"],
        "context_length": 262144,
        "prompt_price": 0.00000006,
        "completion_price": 0.00000033,
        "notes": "Paid fallback if the free Gemma endpoint is rate-limited.",
    },
    {
        "id": "openrouter:moonshotai/kimi-k2.5",
        "label": "Kimi K2.5",
        "provider": "OpenRouter",
        "group": "Paid",
        "modalities": ["text", "image"],
        "context_length": 262144,
        "prompt_price": 0.000000375,
        "completion_price": 0.000002025,
        "notes": "Kimi comparison model for the Cortex hackathon lineage.",
    },
    {
        "id": "openrouter:deepseek/deepseek-chat-v3-0324",
        "label": "DeepSeek V3",
        "provider": "OpenRouter",
        "group": "Paid",
        "modalities": ["text"],
        "context_length": 163840,
        "prompt_price": 0.00000020,
        "completion_price": 0.00000077,
        "notes": "Low-cost text fallback.",
    },
    {
        "id": "openrouter:qwen/qwen3-235b-a22b",
        "label": "Qwen3 235B A22B",
        "provider": "OpenRouter",
        "group": "Paid",
        "modalities": ["text"],
        "context_length": 262144,
        "prompt_price": 0.000000455,
        "completion_price": 0.00000182,
        "notes": "Large MoE text fallback.",
    },
    {
        "id": "local:gemma4:e4b",
        "label": "Gemma 4 E4B local",
        "provider": "Ollama",
        "group": "Local fallback",
        "modalities": ["text", "image"],
        "context_length": 8192,
        "prompt_price": 0.0,
        "completion_price": 0.0,
        "notes": "Only use when OpenRouter is unavailable; consumes local VRAM.",
    },
]

OPENROUTER_FREE_MODEL_PRIORITY = [
    OPENROUTER_DEFAULT_MODEL,
    "google/gemma-4-31b-it:free",
    "openrouter/free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-120b:free",
]
_OPENROUTER_MODEL_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "models": [],
    "error": None,
    "refreshed_at": None,
}


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

class ScanRegistry:
    """SQLite-backed scan registry — durable across backend restarts.

    Schema:  scans(id TEXT PRIMARY KEY, payload JSON, updated REAL)
    Reads are in-memory cache + write-through to SQLite. Writes are async-safe
    (one big lock) but fast because SQLite handles a few hundred writes/sec
    on local disk without breaking a sweat.

    To migrate from the prior in-memory implementation: existing scans on disk
    (in tribev2_cache/, scans/, etc.) aren't auto-imported — only NEW scans
    starting from this revision get persisted. The merge in /api/scans against
    upstream's gallery still surfaces the old scans, so users see them anyway.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        # Open a single connection for the lifetime of the process.
        # check_same_thread=False because asyncio may dispatch to threadpool.
        import sqlite3 as _sqlite3
        db_path = Path(__file__).resolve().parent.parent / "scans" / "registry.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = _sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                id      TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated REAL NOT NULL
            )
        """)
        self._conn.commit()
        # Eager-load on startup
        import json as _json
        for row in self._conn.execute("SELECT id, payload FROM scans"):
            try:
                self._store[row[0]] = _json.loads(row[1])
            except Exception:
                continue
        log.info("[registry] loaded %d scans from %s", len(self._store), db_path)

    async def put(self, scan_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._store[scan_id] = payload
            self._persist(scan_id, payload)

    async def update(self, scan_id: str, **fields: Any) -> None:
        async with self._lock:
            if scan_id in self._store:
                self._store[scan_id].update(fields)
                self._persist(scan_id, self._store[scan_id])

    async def get(self, scan_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return self._store.get(scan_id)

    def all_ids(self) -> list[str]:
        return list(self._store.keys())

    def _persist(self, scan_id: str, payload: dict[str, Any]) -> None:
        """Write-through to SQLite. Errors are logged but don't break the request."""
        try:
            import json as _json
            blob = _json.dumps(payload, default=str)
            self._conn.execute(
                "INSERT INTO scans(id, payload, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated=excluded.updated",
                (scan_id, blob, time.time()),
            )
        except Exception as exc:
            log.warning("[registry] persist failed for %s: %s", scan_id, exc)


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


def _read_openrouter_key_from_file(env_path: Path) -> str:
    try:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or not stripped.startswith("OPENROUTER_API_KEY="):
                continue
            value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            return value
    except Exception:
        return ""
    return ""


def _load_openrouter_api_key_info() -> dict[str, str]:
    """Load OpenRouter key metadata without exposing the key to clients or logs."""
    api_key = _os.environ.get("OPENROUTER_API_KEY", "").strip()
    if api_key:
        return {
            "api_key": api_key,
            "source": "process_env",
            "source_label": "OPENROUTER_API_KEY environment variable",
        }

    env_candidates: list[tuple[Path, str]] = []
    custom_env_path = (
        _os.environ.get("CORTEX_OPENROUTER_ENV_PATH", "").strip()
        or _os.environ.get("OPENROUTER_ENV_PATH", "").strip()
    )
    if custom_env_path:
        env_candidates.append((Path(custom_env_path).expanduser(), "configured operator env file"))
    env_candidates.extend([
        (Path(__file__).resolve().parent.parent / ".env", "repo .env"),
        (Path.home() / ".hermes" / ".env", "~/.hermes/.env"),
    ])

    for env_path, label in env_candidates:
        if not env_path.exists():
            continue
        api_key = _read_openrouter_key_from_file(env_path).strip()
        if api_key:
            return {
                "api_key": api_key,
                "source": "env_file",
                "source_label": label,
            }
    return {"api_key": "", "source": "missing", "source_label": "not configured"}


def _load_openrouter_api_key() -> str:
    return _load_openrouter_api_key_info()["api_key"]


def _safe_openrouter_message(data: Any) -> str:
    try:
        msg = (data or {}).get("error", {}).get("message", "")
        return str(msg)[:180]
    except Exception:
        return ""


async def _openrouter_key_status() -> dict[str, Any]:
    """Check the configured OpenRouter key without spending model credits."""
    key_info = _load_openrouter_api_key_info()
    api_key = key_info["api_key"]
    key_source = {
        "source": key_info["source"],
        "label": key_info["source_label"],
    }
    if not api_key:
        return {
            "configured": False,
            "ok": False,
            "status": "missing_key",
            "key_source": key_source,
            "message": "OPENROUTER_API_KEY is not configured.",
            "action_required": "Set OPENROUTER_API_KEY in the service environment, D:\\cortex\\.env, or ~/.hermes/.env, then restart the Cortex FastAPI process.",
        }
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{OPENROUTER_API_BASE}/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            safe = {
                "label": data.get("label"),
                "limit": data.get("limit"),
                "usage": data.get("usage"),
                "limit_remaining": data.get("limit_remaining"),
                "is_free_tier": data.get("is_free_tier"),
                "rate_limit": data.get("rate_limit"),
            }
            return {"configured": True, "ok": True, "status": "ready", "key_source": key_source, "key": safe}
        return {
            "configured": True,
            "ok": False,
            "status": "invalid_key" if resp.status_code in {401, 403} else "error",
            "key_source": key_source,
            "http_status": resp.status_code,
            "message": _safe_openrouter_message(resp.json() if resp.content else {}),
            "action_required": "Replace the configured OPENROUTER_API_KEY; OpenRouter rejected the current key.",
        }
    except Exception as exc:
        return {
            "configured": True,
            "ok": False,
            "status": "unreachable",
            "key_source": key_source,
            "message": str(exc)[:180],
            "action_required": "Check outbound network access to OpenRouter before the live demo.",
        }


def _price_to_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _openrouter_model_is_free(model: dict[str, Any]) -> bool:
    raw_id = str(model.get("id") or "")
    if raw_id.endswith(":free") or raw_id == "openrouter/free":
        return True
    pricing = model.get("pricing") or {}
    raw_prices = [pricing.get(key) for key in ("prompt", "completion", "request", "image", "audio") if key in pricing]
    return bool(raw_prices) and all(_price_to_float(price) == 0.0 for price in raw_prices)


def _clean_openrouter_label(model: dict[str, Any]) -> str:
    raw_id = str(model.get("id") or "")
    label = str(model.get("name") or raw_id)
    if ":" in label:
        label = label.split(":", 1)[1]
    label = label.replace("(free)", "").replace("(Free)", "")
    label = " ".join(label.split()).strip()
    return label or raw_id


def _catalog_item_from_openrouter_model(model: dict[str, Any]) -> dict[str, Any]:
    raw_id = str(model.get("id") or "")
    architecture = model.get("architecture") or {}
    pricing = model.get("pricing") or {}
    modalities = list(architecture.get("input_modalities") or ["text"])
    context_length = int(model.get("context_length") or (model.get("top_provider") or {}).get("context_length") or 0)
    modality_text = ", ".join(modalities) if modalities else "text"
    return {
        "id": f"openrouter:{raw_id}",
        "label": _clean_openrouter_label(model),
        "provider": "OpenRouter",
        "group": "Free",
        "default": raw_id == OPENROUTER_DEFAULT_MODEL,
        "modalities": modalities,
        "context_length": context_length,
        "prompt_price": _price_to_float(pricing.get("prompt")),
        "completion_price": _price_to_float(pricing.get("completion")),
        "notes": f"Live OpenRouter free model. Inputs: {modality_text}.",
    }


def _prioritize_openrouter_free_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {model_id: index for index, model_id in enumerate(OPENROUTER_FREE_MODEL_PRIORITY)}

    def _rank(item: dict[str, Any]) -> tuple[int, int, str]:
        raw_id = str(item.get("id", "")).removeprefix("openrouter:")
        modalities = set(item.get("modalities") or [])
        modality_rank = 0 if "video" in modalities else 1 if "image" in modalities else 2 if "audio" in modalities else 3
        return (priority.get(raw_id, 500), modality_rank, str(item.get("label") or raw_id).lower())

    return sorted(models, key=_rank)


def _dedupe_model_catalog(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in models:
        model_id = str(item.get("id") or "")
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        deduped.append(item)
    return deduped


def _openrouter_free_models_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_models = payload.get("data") or []
    if not isinstance(raw_models, list):
        return []
    models = [
        _catalog_item_from_openrouter_model(model)
        for model in raw_models
        if isinstance(model, dict) and model.get("id") and _openrouter_model_is_free(model)
    ]
    prioritized = _prioritize_openrouter_free_models(_dedupe_model_catalog(models))
    default_catalog_id = f"openrouter:{OPENROUTER_DEFAULT_MODEL}"
    if not any(item["id"] == default_catalog_id for item in prioritized):
        static_default = next((m for m in NARRATION_MODEL_CATALOG if m["id"] == default_catalog_id), None)
        if static_default:
            prioritized.insert(0, dict(static_default))
    return prioritized


async def _fetch_openrouter_free_models(force_refresh: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if (
        not force_refresh
        and _OPENROUTER_MODEL_CACHE["models"]
        and float(_OPENROUTER_MODEL_CACHE["expires_at"] or 0) > now
    ):
        return list(_OPENROUTER_MODEL_CACHE["models"])
    import httpx as _httpx

    async with _httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(f"{OPENROUTER_API_BASE}/models")
    resp.raise_for_status()
    models = _openrouter_free_models_from_payload(resp.json())
    _OPENROUTER_MODEL_CACHE.update({
        "expires_at": now + OPENROUTER_MODEL_CACHE_TTL_S,
        "models": models,
        "error": None,
        "refreshed_at": int(now),
    })
    return list(models)


def _static_paid_and_local_models() -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in NARRATION_MODEL_CATALOG
        if item.get("group") != "Free" or str(item.get("id", "")).startswith("local:")
    ]


async def _narration_model_catalog() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        live_free_models = await _fetch_openrouter_free_models()
    except Exception as exc:
        _OPENROUTER_MODEL_CACHE["error"] = str(exc)[:180]
        live_free_models = []

    if live_free_models:
        models = _dedupe_model_catalog(live_free_models + _static_paid_and_local_models())
        meta = {
            "catalog_source": "openrouter_live",
            "catalog_count": len(live_free_models),
            "catalog_refreshed_at": _OPENROUTER_MODEL_CACHE.get("refreshed_at"),
            "catalog_error": None,
        }
        return models, meta

    return list(NARRATION_MODEL_CATALOG), {
        "catalog_source": "static_fallback",
        "catalog_count": len(NARRATION_MODEL_CATALOG),
        "catalog_refreshed_at": None,
        "catalog_error": _OPENROUTER_MODEL_CACHE.get("error"),
    }


def _estimate_catalog_item_cost(
    item: dict[str, Any],
    prompt_tokens: int = 4800,
    completion_tokens: int = 4000,
) -> float:
    return (
        _price_to_float(item.get("prompt_price")) * prompt_tokens
        + _price_to_float(item.get("completion_price")) * completion_tokens
    )


def _estimate_narration_cost(model_id: str, prompt_tokens: int = 4800, completion_tokens: int = 4000) -> float:
    """Estimate four-persona narration cost for UI display."""
    item = next((m for m in NARRATION_MODEL_CATALOG if m["id"] == model_id), None)
    if not item:
        return 0.0
    return _estimate_catalog_item_cost(item, prompt_tokens, completion_tokens)



def _media_metadata_context(media_path: Path) -> str:
    """Cheap, local media summary used when multimodal cloud description is unavailable."""
    suffix = media_path.suffix.lower()
    if suffix in ALLOWED_TEXT:
        text = media_path.read_text(encoding="utf-8", errors="replace").strip()
        return (
            "Source media metadata:\n"
            f"- file: {media_path.name}\n"
            "- modality: text\n"
            f"- characters: {len(text)}\n"
            f"- content_excerpt: {text[:1400]!r}\n"
            "Use this text as the semantic stimulus that TRIBE v2 received through its text events path."
        )
    try:
        from cortex import media_processor as _mp
        info = _mp.probe(media_path)
        return (
            "Source media metadata:\n"
            f"- file: {media_path.name}\n"
            f"- modality: {'video' if info.width else 'audio' if info.has_audio else 'file'}\n"
            f"- duration_s: {info.duration_s:.2f}\n"
            f"- video: {info.width}x{info.height} at {info.fps:.2f} fps, codec={info.codec or 'none'}\n"
            f"- audio: {'present' if info.has_audio else 'absent'}, codec={info.audio_codec or 'none'}, sample_rate={info.audio_sample_rate or 0}\n"
            "Use this metadata to preserve modality awareness; do not claim a full semantic media understanding unless a description is provided."
        )
    except Exception:
        kind = (
            "image" if suffix in ALLOWED_IMAGE else
            "video" if suffix in ALLOWED_VIDEO else
            "audio" if suffix in ALLOWED_AUDIO else
            "text/document"
        )
        return f"Source media metadata: file={media_path.name}; modality={kind}; detailed probe unavailable."


def _openrouter_content_for_media(media_path: Path) -> tuple[str, list[dict[str, Any]] | None]:
    suffix = media_path.suffix.lower()
    mime = mimetypes.guess_type(str(media_path))[0] or "application/octet-stream"
    raw_b64 = base64.b64encode(media_path.read_bytes()).decode("ascii")
    prompt = (
        "Describe this Cortex stimulus for a neuroscience brain-response demo. "
        "Focus on what a viewer/hearer is experiencing, including motion, objects, text, speech, music, and emotional tone. "
        "Do not diagnose anyone. Keep it under 160 words."
    )
    if suffix in ALLOWED_IMAGE:
        return OPENROUTER_DEFAULT_MODEL, [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{raw_b64}"}},
        ]
    if suffix in ALLOWED_VIDEO:
        return OPENROUTER_DEFAULT_MODEL, [
            {"type": "text", "text": prompt + " Include the soundtrack or speech if the model can perceive it."},
            {"type": "video_url", "video_url": {"url": f"data:{mime};base64,{raw_b64}"}},
        ]
    if suffix in ALLOWED_AUDIO:
        audio_format = suffix.lstrip(".") or "wav"
        if audio_format == "m4a":
            audio_format = "mp4"
        return "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", [
            {"type": "text", "text": prompt},
            {"type": "input_audio", "input_audio": {"data": raw_b64, "format": audio_format}},
        ]
    return OPENROUTER_DEFAULT_MODEL, None


async def _describe_media_for_prompt(media_path: Path) -> str:
    """Generate a bounded multimodal source description without loading local Gemma."""
    metadata = _media_metadata_context(media_path)
    if _os.environ.get("CORTEX_DISABLE_OPENROUTER_MEDIA_CONTEXT", "").lower() in {"1", "true", "yes"}:
        return metadata
    api_key = _load_openrouter_api_key()
    if not api_key:
        return metadata + "\nOpenRouter multimodal source description: unavailable (no API key configured)."
    max_bytes = int(OPENROUTER_MEDIA_MAX_MB * 1024 * 1024)
    try:
        if media_path.stat().st_size > max_bytes:
            return (
                metadata
                + f"\nOpenRouter multimodal source description: skipped because media exceeds {OPENROUTER_MEDIA_MAX_MB:g} MB."
            )
        model, content = _openrouter_content_for_media(media_path)
        if content is None:
            return metadata
        import httpx as _httpx
        body = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 260,
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://cortex.redteamkitchen.com",
            "X-Title": "Cortex",
        }
        async with _httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{OPENROUTER_API_BASE}/chat/completions",
                json=body,
                headers=headers,
            )
        if resp.status_code >= 400:
            return metadata + f"\nOpenRouter multimodal source description: unavailable ({resp.status_code})."
        data = resp.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        text = str(text).strip()
        if not text:
            return metadata
        return metadata + f"\nOpenRouter multimodal source description ({model}):\n{text[:1400]}"
    except Exception as exc:
        return metadata + f"\nOpenRouter multimodal source description: unavailable ({str(exc)[:120]})."


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

async def _build_fleet_snapshot(app: FastAPI) -> dict[str, Any]:
    """Build the fleet-health snapshot via the closure registered by create_app().
    This indirection lets the lifespan-level telemetry loop reuse the same
    code path as the HTTP /api/fleet-health endpoint without duplicating logic
    or moving everything to module scope.
    """
    fn = getattr(app.state, "fleet_snapshot", None)
    if fn is None:
        return {"ok": False, "error": "fleet_snapshot not yet registered"}
    return await fn()


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

    # ── Live telemetry broadcaster ──────────────────────────────────────────
    # Pushes fleet-health snapshots over the WS hub at 2 Hz, but ONLY when the
    # snapshot has actually changed. Drops the per-client polling load by ~100×
    # and gives the UI sub-200 ms updates instead of the old 2 s interval.
    async def _telemetry_loop() -> None:
        import json as _json
        last_sig = None
        while True:
            try:
                snap = await _build_fleet_snapshot(app)
                # Sign on the volatile bits only — ignore `ts` and exact float
                # noise so we don't push every tick.
                sig_payload = {
                    "nodes": {
                        k: {kk: v.get(kk) for kk in
                            ("alive", "device_kind", "gpu_state",
                             "queue_depth", "completed", "failed", "active")}
                        for k, v in (snap.get("nodes") or {}).items()
                    },
                    "services": snap.get("services"),
                }
                sig = _json.dumps(sig_payload, sort_keys=True)
                if sig != last_sig:
                    await hub.broadcast({"type": "fleet:health", "data": snap})
                    last_sig = sig
            except Exception as exc:  # noqa: BLE001 — never let this crash startup
                log.debug("[telemetry] tick failed: %s", exc)
            await asyncio.sleep(0.5)

    telemetry_task = asyncio.create_task(_telemetry_loop())
    log.info("[webapp] startup complete (scheduler=%s, telemetry=on)", scheduler.state.value)
    try:
        yield
    finally:
        telemetry_task.cancel()
        try:
            await telemetry_task
        except (asyncio.CancelledError, Exception):
            pass
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

    @app.get("/api/router-health")
    async def router_health() -> dict[str, Any]:
        """Proxy to the inference router's /healthz, with sanitized response.
        Lets the browser show Big Apple + OpenRouter status without exposing
        internal hostnames/credentials.
        """
        import httpx as _httpx
        try:
            async with _httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get("http://localhost:8766/healthz")
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass
        return {"ok": False, "ollama_backends": {}, "openrouter": False}

    @app.get("/api/narration-models")
    async def narration_models() -> dict[str, Any]:
        """Return the UI-safe narration model catalog and current free-tier assumptions."""
        models, catalog_meta = await _narration_model_catalog()
        return {
            "default_model": f"openrouter:{OPENROUTER_DEFAULT_MODEL}",
            **catalog_meta,
            "estimated_tokens_per_scan": {
                "prompt": 4800,
                "completion": 4000,
                "total": 8800,
                "assumption": "Four personas, roughly 1200 prompt + 1000 output tokens each.",
            },
            "fixed_local_cost_estimate_usd": {
                "tribe_electricity": 0.00006,
                "hardware_depreciation": 0.00080,
                "total": 0.00086,
            },
            "openrouter_free_limits": OPENROUTER_FREE_LIMITS,
            "funding_guidance": {
                "minimum_to_unlock_1000_free_requests_per_day": 10,
                "current_user_reported_credit_usd": 15,
                "recommendation": "Keep at least $10 purchased and keep balance positive. With about $15 funded, use free models first and reserve paid models for rate-limit or quality fallback.",
            },
            "models": [
                {**m, "estimated_narration_cost_usd": round(_estimate_catalog_item_cost(m), 6)}
                for m in models
            ],
        }

    @app.get("/api/openrouter/status")
    async def openrouter_status() -> dict[str, Any]:
        """Check OpenRouter key health without exposing the key or spending completion credits."""
        status = await _openrouter_key_status()
        return {
            **status,
            "free_limits": OPENROUTER_FREE_LIMITS,
            "default_model": f"openrouter:{OPENROUTER_DEFAULT_MODEL}",
        }

    @app.get("/api/tribe/status")
    async def tribe_status() -> dict[str, Any]:
        gpu = _scheduler.vram_report()
        queue_status = _queue.status()
        queue_busy = bool(queue_status.get("active_request") or queue_status.get("processing") or queue_status.get("queue_depth"))
        state = gpu.get("state") or getattr(_scheduler.state, "value", str(_scheduler.state))
        return {
            "ok": True,
            "pc_online": True,
            "gpu": gpu,
            "queue": queue_status,
            "state": state,
            "queue_busy": queue_busy,
            "tribe_loaded": state == "tribe_active",
            "tribe_ready": state == "tribe_active" and not queue_busy,
            "can_warm_tribe": bool(gpu.get("tribe_fits") or state == "tribe_active") and not queue_busy,
            "message": (
                "TRIBE v2 is loaded and ready."
                if state == "tribe_active" and not queue_busy
                else "GPU is available for TRIBE v2."
                if bool(gpu.get("tribe_fits")) and not queue_busy
                else "GPU or queue is busy; wait before warming TRIBE v2."
            ),
        }

    @app.post("/api/tribe/warm")
    async def warm_tribe() -> JSONResponse:
        """Load TRIBE v2 into accelerator memory if the queue and VRAM allow it.

        This is intentionally the same capability a scan already uses, exposed
        as a demo-readiness button so the operator can warm the model before
        going live.
        """
        queue_status = _queue.status()
        queue_busy = bool(queue_status.get("active_request") or queue_status.get("processing") or queue_status.get("queue_depth"))
        if queue_busy:
            return JSONResponse(
                {"ok": False, "status": "busy", "message": "A scan is already running or queued.", "queue": queue_status},
                status_code=409,
            )
        gpu = _scheduler.vram_report()
        state = gpu.get("state") or getattr(_scheduler.state, "value", str(_scheduler.state))
        if not (gpu.get("tribe_fits") or state == "tribe_active"):
            return JSONResponse(
                {"ok": False, "status": "insufficient_vram", "message": "TRIBE v2 does not currently fit in free VRAM.", "gpu": gpu},
                status_code=409,
            )
        if not hasattr(_scheduler, "ensure_tribe"):
            return JSONResponse(
                {"ok": False, "status": "unsupported_scheduler", "message": "This scheduler cannot warm TRIBE v2."},
                status_code=501,
            )
        await app.state.hub.broadcast({"type": "tribe_warm_started"})
        try:
            await _scheduler.ensure_tribe()
            gpu_after = _scheduler.vram_report()
            await app.state.hub.broadcast({"type": "tribe_warm_complete", "gpu": gpu_after})
            return JSONResponse({"ok": True, "status": "tribe_ready", "gpu": gpu_after})
        except Exception as exc:
            await app.state.hub.broadcast({"type": "tribe_warm_failed", "message": str(exc)[:160]})
            return JSONResponse(
                {"ok": False, "status": "warm_failed", "message": str(exc)[:240], "gpu": _scheduler.vram_report()},
                status_code=500,
            )

    @app.get("/api/fleet-health")
    async def fleet_health() -> dict[str, Any]:
        """One-stop fleet status — same payload that the lifespan telemetry
        loop pushes over the WebSocket as `fleet:health` events. Kept as a
        REST endpoint for first-paint + clients that don't want to upgrade to
        WS (curl, monitoring scripts, etc.).
        """
        return await _build_fleet_snapshot(app)

    async def _do_fleet_snapshot() -> dict[str, Any]:
        import asyncio as _asyncio
        import httpx as _httpx
        import os as _os
        from cortex import device as _device

        peer_base = (TRIBE_PROXY_URL or "").rstrip("/")
        # Identify roles deterministically: cuda host = "seratonin", mps = "bigapple"
        my_kind = _device.DEVICE_KIND
        my_role = "seratonin" if my_kind == "cuda" else "bigapple" if my_kind == "mps" else my_kind
        peer_role = "bigapple" if my_role == "seratonin" else "seratonin"

        # Local snapshot (cheap, in-process)
        local_gpu = _scheduler.vram_report()
        local_queue = _queue.status()
        my_view = {
            "role": my_role,
            "alive": True,
            "device_kind": my_kind,
            "device_name": local_gpu.get("device_name", "?"),
            "gpu_state": local_gpu.get("state"),
            "free_gb": local_gpu.get("free_gb"),
            "used_gb": local_gpu.get("used_gb"),
            "total_gb": local_gpu.get("total_gb"),
            "tribe_fits": local_gpu.get("tribe_fits"),
            "queue_depth": local_queue.get("queue_depth"),
            "completed": local_queue.get("completed"),
            "failed": local_queue.get("failed"),
            "active": local_queue.get("active_request"),
        }

        async def _get_json(client, url, timeout=2.0):
            try:
                r = await client.get(url, timeout=timeout)
                return r.json() if r.status_code == 200 else None
            except Exception:
                return None

        async def _check_port(client, url, timeout=2.0):
            try:
                r = await client.get(url, timeout=timeout)
                return r.status_code == 200
            except Exception:
                return False

        async with _httpx.AsyncClient() as client:
            tasks = {
                "router_local":   _get_json(client, "http://localhost:8766/healthz"),
                "ollama_local":   _check_port(client, "http://localhost:11434/api/tags"),
            }
            if peer_base:
                tasks["peer_view"]    = _get_json(client, f"{peer_base}/api/health")
                tasks["router_peer"]  = _get_json(client, f"{peer_base}/api/router-health")
                # peer's ollama: don't probe port 11434 directly because some nodes
                # (Sera) bind Ollama to 127.0.0.1. Ask the peer's own /api/router-health
                # for its ollama_backends dict — that's authoritative.
            results = dict(zip(tasks.keys(), await _asyncio.gather(*tasks.values())))

        router_local = results.get("router_local") or {}
        peer_view = results.get("peer_view") or {}
        peer_gpu = peer_view.get("gpu", {}) if peer_view else {}
        peer_queue = peer_view.get("queue", {}) if peer_view else {}
        their_view = {
            "role": peer_role,
            "alive": bool(peer_view),
            "device_kind": peer_gpu.get("device_kind"),
            "device_name": peer_gpu.get("device_name"),
            "gpu_state": peer_gpu.get("state"),
            "free_gb": peer_gpu.get("free_gb"),
            "used_gb": peer_gpu.get("used_gb"),
            "total_gb": peer_gpu.get("total_gb"),
            "tribe_fits": peer_gpu.get("tribe_fits"),
            "queue_depth": peer_queue.get("queue_depth"),
            "completed": peer_queue.get("completed"),
            "failed": peer_queue.get("failed"),
            "active": peer_queue.get("active_request"),
        } if peer_base else None

        # Service status — distinguish "not applicable" (no router on this node, no
        # peer configured) from "DOWN" (configured but unreachable). null = n/a.
        is_proxy_node = bool(peer_base)        # this node proxies TRIBE → has no router
        router_peer_payload = results.get("router_peer") or {}
        # ollama_peer: derived from peer router's own backend self-check, not a
        # direct port probe (which fails when peer Ollama is bound localhost-only).
        peer_backends = router_peer_payload.get("ollama_backends", {}) if router_peer_payload else {}
        # any backend reporting True from the peer's perspective means peer's Ollama is up
        ollama_peer_status = (
            any(peer_backends.values()) if peer_backends else None
        )

        return {
            "ok": True,
            "ts": int(time.time()),
            "host": my_role,
            "nodes": {my_role: my_view, **({peer_role: their_view} if their_view else {})},
            "services": {
                # router_local: n/a on the proxy node (it never runs a router locally)
                "router_local": (None if is_proxy_node else bool(router_local)),
                "ollama_local": bool(results.get("ollama_local")),
                # router_peer: only meaningful when there's a peer
                "router_peer":  (bool(router_peer_payload) if peer_base else None),
                # ollama_peer: derived from peer's router self-check
                "ollama_peer":  ollama_peer_status,
                "openrouter":   (
                    bool(router_local.get("openrouter")) if router_local
                    else bool(router_peer_payload.get("openrouter")) if router_peer_payload
                    else False
                ),
            },
            "router": {
                "ollama_backends": router_local.get("ollama_backends", {}) if router_local
                                    else router_peer_payload.get("ollama_backends", {}),
                "openrouter": (
                    router_local.get("openrouter") if router_local
                    else router_peer_payload.get("openrouter") if router_peer_payload
                    else False
                ),
            },
            "tribe_proxy_url": peer_base or None,
        }

    # Expose the closure on the app so the lifespan loop can call it
    app.state.fleet_snapshot = _do_fleet_snapshot

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
        """Live accelerator telemetry for the Twitch / OBS overlay at /specs.html.

        On NVIDIA hosts: nvidia-smi gives temp/power/clock/fan/util.
        On Apple Silicon: powermetrics needs sudo, so we surface the (much smaller)
                          set we can read without privileges. Caller treats absent
                          fields as None and the overlay falls back to "—".
        """
        import shutil
        import subprocess
        from cortex import device as _device

        if _device.DEVICE_KIND == "cuda":
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
                        # CREATE_NO_WINDOW — without it this telemetry poll
                        # flashes a conhost console window. See device._NO_WINDOW.
                        creationflags=_device._NO_WINDOW,
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
                    "device_kind":   "cuda",
                    "device_name":   _device.device_name(),
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

        if _device.DEVICE_KIND == "mps":
            # Apple Silicon: no priv-free GPU temp/power reading. Return memory + load.
            try:
                used = _device.used_vram_gb()
                free = _device.free_vram_gb()
                total = _device.total_vram_gb()
                util_pct = (used / total * 100) if total > 0 else None
                return {
                    "device_kind":   "mps",
                    "device_name":   _device.device_name(),
                    "used_gb":       round(used, 2),
                    "free_gb":       round(free, 2),
                    "total_gb":      round(total, 2),
                    "util_gpu_pct":  round(util_pct, 1) if util_pct is not None else None,
                }
            except Exception as exc:
                log.debug("[webapp] mps telemetry failed: %s", exc)
                return {"device_kind": "mps", "device_name": _device.device_name()}

        return {"device_kind": _device.DEVICE_KIND, "device_name": _device.device_name()}

    # -----------------------------------------------------------------------
    # Scan submission
    # -----------------------------------------------------------------------

    @app.post("/api/scan")
    async def submit_scan(
        file: UploadFile = File(...),
        tier: int = Form(default=1, ge=0, le=6),
        source: str = Form(default="webui"),
        narration_model: str = Form(default=f"openrouter:{OPENROUTER_DEFAULT_MODEL}"),
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

        # ────────────────────────────────────────────────────────────────────
        # TRIBE proxy: if this file needs TRIBE (video/audio/text/document) and
        # we're configured to forward such scans elsewhere (because the local
        # device can't run TRIBE — Apple Silicon), POST the file to that
        # backend and store a reference. Image scans stay local because they
        # use Gemma vision and bypass TRIBE entirely.
        # ────────────────────────────────────────────────────────────────────
        if TRIBE_PROXY_URL and ext in TRIBE_NEEDED_EXTS:
            log.info("[webapp] proxying TRIBE scan %s (%s) → %s", scan_id, ext, TRIBE_PROXY_URL)
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=30.0) as client:
                    with target.open("rb") as fh:
                        files_payload = {"file": (file.filename, fh.read(), file.content_type or "application/octet-stream")}
                    data_payload = {
                        "tier": str(tier),
                        "source": f"proxy-from-bigapple:{source}",
                        "narration_model": narration_model,
                    }
                    r = await client.post(
                        f"{TRIBE_PROXY_URL}/api/scan",
                        files=files_payload,
                        data=data_payload,
                    )
                if r.status_code >= 400:
                    raise RuntimeError(f"upstream {r.status_code}: {r.text[:200]}")
                upstream = r.json()
                upstream_id = upstream.get("scan_id")
                if not upstream_id:
                    raise RuntimeError(f"upstream returned no scan_id: {upstream}")

                # Record the proxy reference under OUR scan_id so /api/scan/<id>
                # and /api/scans both find it. The scan-detail handler will
                # transparently fetch upstream state on each read.
                await app.state.registry.put(
                    scan_id,
                    {
                        "id": scan_id,
                        "upstream_id": upstream_id,
                        "upstream_base": TRIBE_PROXY_URL,
                        "status": "queued",
                        "filename": file.filename,
                        "tier": tier,
                        "source": source,
                        "narration_model": narration_model,
                        "size_mb": round(size / (1024 * 1024), 2),
                        "proxied": True,
                    },
                )
                target.unlink(missing_ok=True)  # local copy not needed; upstream owns the file

                await app.state.hub.broadcast(
                    {"type": "scan_queued", "scan_id": scan_id, "filename": file.filename, "proxied": True}
                )
                return JSONResponse(
                    {"ok": True, "scan_id": scan_id, "status": "queued", "proxied": True},
                    status_code=202,
                )
            except Exception as exc:
                log.error("[webapp] TRIBE proxy failed (%s) — falling back to local: %s", scan_id, exc)
                # If proxy is unreachable, fall through to local processing
                # (which will fail on MPS-only hosts but at least surface a real error).

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

    async def _hydrate_proxied(rec: dict[str, Any]) -> dict[str, Any]:
        """If this scan is proxied to another backend, fetch its current state
        upstream and merge it into the local record. Local fields (id,
        filename, source) win; upstream fills status / top_rois / narrations /
        peak_t / etc. Failure to reach upstream returns whatever we have locally.
        """
        if not rec.get("proxied"):
            return rec
        upstream_id = rec.get("upstream_id")
        upstream_base = (rec.get("upstream_base") or TRIBE_PROXY_URL or "").rstrip("/")
        if not upstream_id or not upstream_base:
            return rec
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"{upstream_base}/api/scan/{upstream_id}")
            if r.status_code == 200:
                u = r.json()
                # Merge: keep local id + filename + source, take upstream
                # status + results + narrations.
                merged = dict(rec)
                for k in ("status", "top_rois", "peak_t", "tr_seconds", "n_t",
                          "seconds_elapsed", "narrations", "narration", "error"):
                    if k in u and u[k] is not None:
                        merged[k] = u[k]
                return merged
        except Exception as exc:
            log.debug("[webapp] proxy hydrate failed for %s: %s", rec.get("id"), exc)
        return rec

    @app.get("/api/scan/{scan_id}")
    async def get_scan(scan_id: str) -> dict[str, Any]:
        record = await app.state.registry.get(scan_id)
        if record is None:
            # Fall through to upstream — gallery may surface upstream ids directly
            if TRIBE_PROXY_URL:
                try:
                    import httpx as _httpx
                    async with _httpx.AsyncClient(timeout=5.0) as client:
                        r = await client.get(f"{TRIBE_PROXY_URL.rstrip('/')}/api/scan/{scan_id}")
                    if r.status_code == 200:
                        return r.json()
                except Exception:
                    pass
            raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
        return await _hydrate_proxied(record)

    def _scan_media_summary(scan_id: str) -> dict[str, Any]:
        """Describe gallery media without forcing clients to probe 404s."""
        ascii_path = Path("D:/cortex/scans/ascii") / f"{scan_id}_ascii.mp4"
        bold_vertex_path = Path("D:/cortex/scans") / f"{scan_id}.npy"
        source_path: Path | None = None
        for ext in sorted(ALLOWED_EXTS):
            candidate = UPLOAD_DIR / f"{scan_id}{ext}"
            if candidate.exists():
                source_path = candidate
                break

        source_kind = None
        if source_path is not None:
            ext = source_path.suffix.lower()
            if ext in ALLOWED_IMAGE:
                source_kind = "image"
            elif ext in ALLOWED_VIDEO:
                source_kind = "video"
            elif ext in ALLOWED_AUDIO:
                source_kind = "audio"
            elif ext in ALLOWED_TEXT:
                source_kind = "text"
            elif ext in ALLOWED_DOCUMENT:
                source_kind = "document"

        has_ascii_video = ascii_path.exists()
        has_bold_vertex = bold_vertex_path.exists()
        return {
            "has_bold_vertex": has_bold_vertex,
            "bold_vertex_url": f"/api/scan/{scan_id}/bold-vertex" if has_bold_vertex else None,
            "has_ascii_video": has_ascii_video,
            "ascii_video_url": f"/api/scan/{scan_id}/ascii-video" if has_ascii_video else None,
            "source_media_url": f"/api/scan/{scan_id}/source-media" if source_path else None,
            "source_media_kind": source_kind,
        }

    @app.get("/api/scans")
    async def list_scans(limit: int = 50, status: str = "complete") -> dict[str, Any]:
        """List recent scans (for the public gallery).

        Returns a compact summary per scan: id, filename, status, top_rois,
        peak_t, narrations (all 4 personas), seconds_elapsed.

        Proxied scans are hydrated in-line. ALSO if TRIBE_PROXY_URL is set,
        the upstream's /api/scans is merged in so the gallery survives a local
        restart (which wipes our in-memory registry).
        """
        out: list[dict[str, Any]] = []
        seen_upstream_ids: set[str] = set()
        seen_local_ids: set[str] = set()

        # 1. Local scans (direct + proxy-referenced)
        ids = app.state.registry.all_ids()
        for sid in ids:
            rec = await app.state.registry.get(sid)
            if rec is None:
                continue
            rec = await _hydrate_proxied(rec)
            if status != "all" and rec.get("status") != status:
                continue
            seen_local_ids.add(sid)
            if rec.get("upstream_id"):
                seen_upstream_ids.add(rec["upstream_id"])
            media = _scan_media_summary(sid)
            # Defensive: dict.get(k, default) only returns `default` when the
            # key is ABSENT. Image scans persist `top_rois: null` (key present,
            # value None), so `rec.get("top_rois", [])[:5]` evaluated to
            # `None[:5]` → TypeError → EVERY /api/scans call 500'd → the public
            # gallery page rendered empty/broken even though scans completed
            # fine. `(x or default)` handles both absent AND null. (2026-05-15)
            out.append({
                "id": sid,
                "filename": rec.get("filename"),
                "status": rec.get("status"),
                "tier": rec.get("tier"),
                "top_rois": (rec.get("top_rois") or [])[:5],
                "peak_t": rec.get("peak_t"),
                "tr_seconds": rec.get("tr_seconds"),
                "n_t": rec.get("n_t"),
                "size_mb": rec.get("size_mb"),
                "seconds_elapsed": rec.get("seconds_elapsed"),
                "tribe_seconds": rec.get("tribe_seconds"),
                "narration_seconds": rec.get("narration_seconds"),
                "narration_timings": rec.get("narration_timings") or {},
                "narrations": rec.get("narrations") or {},
                "created_at": rec.get("created_at"),
                "proxied": rec.get("proxied", False),
                **media,
            })

        # 2. If a TRIBE proxy is configured, merge upstream's scans too — so the
        #    gallery shows everything even after we lose our in-memory registry.
        if TRIBE_PROXY_URL:
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(f"{TRIBE_PROXY_URL}/api/scans?limit=200&status={status}")
                if r.status_code == 200:
                    upstream_data = r.json()
                    for u in upstream_data.get("scans", []):
                        uid = u.get("id")
                        if not uid or uid in seen_upstream_ids:
                            continue
                        has_ascii_video = u.get("has_ascii_video")
                        if has_ascii_video is None:
                            has_ascii_video = True
                        # Synthesize a local-style record pointing to upstream;
                        # the gallery's <video src=/api/scan/{id}/...> will
                        # proxy to upstream via _proxy_media because this entry
                        # is also marked proxied.
                        out.append({
                            **u,
                            "id": uid,                 # surface upstream id
                            "proxied": True,
                            "_upstream_only": True,
                            "has_bold_vertex": bool(u.get("has_bold_vertex", has_ascii_video)),
                            "bold_vertex_url": u.get("bold_vertex_url") or f"/api/scan/{uid}/bold-vertex",
                            "has_ascii_video": bool(has_ascii_video),
                            "ascii_video_url": u.get("ascii_video_url") or f"/api/scan/{uid}/ascii-video",
                            "source_media_url": u.get("source_media_url"),
                            "source_media_kind": u.get("source_media_kind"),
                        })
            except Exception as exc:
                log.debug("[webapp] upstream /api/scans merge failed: %s", exc)

        # Real per-vertex brain previews first, then newest first. Scans without
        # persisted vertices still render through the regional BOLD fallback.
        out.sort(
            key=lambda r: (
                1 if r.get("has_bold_vertex") else 0,
                r.get("created_at") or 0,
            ),
            reverse=True,
        )
        return {"count": len(out), "scans": out[:limit]}

    # -----------------------------------------------------------------------
    # Narrations lookup
    # -----------------------------------------------------------------------

    @app.get("/api/scan/{scan_id}/narrations")
    async def get_narrations(scan_id: str) -> dict[str, Any]:
        record = await app.state.registry.get(scan_id)
        # Fall through to upstream if no local record (gallery surfaced upstream ids)
        if record is None and TRIBE_PROXY_URL:
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(f"{TRIBE_PROXY_URL.rstrip('/')}/api/scan/{scan_id}/narrations")
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass
        if record is None:
            raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
        # Hydrate from upstream if proxied
        record = await _hydrate_proxied(record)
        narrations = record.get("narrations") or {}
        # If local hydrate gave us nothing and we have a proxy, ask upstream directly
        if not narrations and TRIBE_PROXY_URL:
            try:
                import httpx as _httpx
                upstream_id = record.get("upstream_id") or scan_id
                upstream_base = (record.get("upstream_base") or TRIBE_PROXY_URL).rstrip("/")
                async with _httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(f"{upstream_base}/api/scan/{upstream_id}/narrations")
                if r.status_code == 200:
                    j = r.json()
                    narrations = j.get("narrations") or {}
            except Exception:
                pass
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

    async def _proxy_media(scan_id: str, suffix_path: str) -> Response | None:
        """If the scan is proxied to another backend, fetch the media from there
        using the upstream_id. Returns the raw response or None if not proxied /
        upstream unreachable / not found upstream.

        Three cases:
          1. We have a local record with upstream_id+upstream_base → proxy.
          2. We have NO local record but TRIBE_PROXY_URL is set → assume the
             scan_id IS the upstream id (gallery merge surfaces upstream ids
             directly), and proxy with that.
          3. No local record AND no proxy → caller falls through to local 404.
        """
        rec = await app.state.registry.get(scan_id)
        upstream_id: str | None = None
        upstream_base: str | None = None
        if rec and rec.get("proxied"):
            upstream_id = rec.get("upstream_id")
            upstream_base = (rec.get("upstream_base") or TRIBE_PROXY_URL or "").rstrip("/")
        elif rec is None and TRIBE_PROXY_URL:
            # Gallery merged upstream scan_id directly — try as-is
            upstream_id = scan_id
            upstream_base = TRIBE_PROXY_URL.rstrip("/")
        if not upstream_id or not upstream_base:
            return None
        url = f"{upstream_base}/api/scan/{upstream_id}{suffix_path}"
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(url)
            if r.status_code == 200:
                return Response(
                    content=r.content,
                    media_type=r.headers.get("content-type", "application/octet-stream"),
                    headers={
                        "Cache-Control": "public, max-age=300",
                        "X-Proxied-From": upstream_base,
                        "X-Upstream-Scan-Id": upstream_id,
                        # Mirror upstream's per-vertex shape headers if present
                        **({k: v for k, v in r.headers.items()
                            if k.lower() in ("x-n-t", "x-n-vert", "x-scan-id")}),
                    },
                )
        except Exception as exc:
            log.debug("[webapp] media proxy failed (%s%s): %s", scan_id, suffix_path, exc)
        return None

    @app.get("/api/scan/{scan_id}/manim-video")
    async def manim_video_endpoint(scan_id: str, scene: str = "BoldTimeseries") -> Response:
        """Serve the Manim brain activation explainer video for a completed scan.

        Scenes: BoldTimeseries | BrainNetworkDiagram
        Status 202 if still generating, 404 if not available.
        """
        # Proxy fallthrough for scans handled by another backend
        proxied = await _proxy_media(scan_id, f"/manim-video?scene={scene}")
        if proxied is not None:
            return proxied
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
        # Proxy fallthrough for scans handled by another backend
        proxied = await _proxy_media(scan_id, "/ascii-video")
        if proxied is not None:
            return proxied
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

    @app.get("/api/scan/{scan_id}/source-media")
    async def source_media_endpoint(scan_id: str) -> FileResponse:
        """Serve the original uploaded media for gallery fallbacks."""
        for ext in sorted(ALLOWED_EXTS):
            source_path = UPLOAD_DIR / f"{scan_id}{ext}"
            if source_path.exists():
                media_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
                return FileResponse(
                    str(source_path),
                    media_type=media_type,
                    headers={"Cache-Control": "public, max-age=86400", "X-Scan-Id": scan_id},
                )
        raise HTTPException(status_code=404, detail="source media not available for this scan")

    @app.get("/api/gallery/test-image")
    async def gallery_test_image_endpoint() -> FileResponse:
        """Serve a stable image for no-video gallery cards."""
        candidates = [
            Path("D:/cortex/data/artemis_inbox/artemis_02_moon_over_sls.jpg"),
            Path("D:/cortex/data/artemis_inbox/artemis_03_full_moon_pad39b.jpg"),
            UPLOAD_DIR / "f023653e664f.jpg",
            UPLOAD_DIR / "29485ea42ce9.jpg",
            Path("D:/cortex/website/assets/placeholder.png"),
        ]
        for image_path in candidates:
            if image_path.exists():
                media_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
                return FileResponse(
                    str(image_path),
                    media_type=media_type,
                    headers={"Cache-Control": "public, max-age=86400"},
                )
        raise HTTPException(status_code=404, detail="gallery test image not available")

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
        # Proxy fallthrough for scans handled by another backend
        proxied = await _proxy_media(scan_id, f"/bold-vertex?n_t={n_t}")
        if proxied is not None:
            return proxied
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
        # Proxy fallthrough for scans handled by another backend (returns JSON)
        rec = await app.state.registry.get(scan_id)
        upstream_id, upstream_base = None, None
        if rec and rec.get("proxied"):
            upstream_id = rec.get("upstream_id")
            upstream_base = (rec.get("upstream_base") or TRIBE_PROXY_URL or "").rstrip("/")
        elif rec is None and TRIBE_PROXY_URL:
            upstream_id = scan_id
            upstream_base = TRIBE_PROXY_URL.rstrip("/")
        if upstream_id and upstream_base:
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(f"{upstream_base}/api/scan/{upstream_id}/bold-simulate?n_t={n_t}")
                if r.status_code == 200:
                    return r.json()
            except Exception as exc:
                log.debug("[webapp] bold-simulate proxy failed: %s", exc)
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
        narration_model: str = Form(default=f"openrouter:{OPENROUTER_DEFAULT_MODEL}"),
    ) -> JSONResponse:
        clean_text = text.strip()
        if not clean_text:
            return JSONResponse({"error": "empty text"}, status_code=400)
        scan_id = uuid.uuid4().hex[:12]
        target = UPLOAD_DIR / f"{scan_id}.txt"
        target.write_text(clean_text[:4000], encoding="utf-8")
        await app.state.registry.put(
            scan_id,
            {
                "id": scan_id,
                "status": "queued",
                "filename": "<text stimulus>",
                "tier": tier,
                "source": source,
                "narration_model": narration_model,
                "text": clean_text[:1000],
                "analysis_mode": "tribe_text",
            },
        )
        asyncio.create_task(
            _run_scan_background(app, scan_id, str(target), tier, source, narration_model)
        )
        await app.state.hub.broadcast(
            {"type": "scan_queued", "scan_id": scan_id, "filename": "<text stimulus>"}
        )
        return JSONResponse(
            {"ok": True, "scan_id": scan_id, "status": "queued", "analysis_mode": "tribe_text"},
            status_code=202,
        )

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
        def _public_file(name: str) -> FileResponse:
            path = PUBLIC_DIR / name
            if not path.exists():
                raise HTTPException(status_code=404, detail=f"{name} not found")
            return FileResponse(str(path))

        # Explicit routes for the multi-page demo so we don't need Vite in production.
        # Order matters: FastAPI routes registered above (all /api/*) take precedence.
        @app.get("/")
        async def index() -> FileResponse:
            return _public_file("index.html")

        @app.get("/gallery.html")
        async def gallery_page() -> FileResponse:
            return _public_file("gallery.html")

        @app.get("/personas.html")
        async def personas_page() -> FileResponse:
            return _public_file("personas.html")

        @app.get("/specs.html")
        async def specs_page() -> FileResponse:
            return _public_file("specs.html")

        @app.get("/status.html")
        async def status_page() -> FileResponse:
            return _public_file("status.html")

        # Clean URLs (no .html). The bare paths are the canonical form;
        # the .html paths above stay for backward compat with old links.
        @app.get("/status")
        async def status_alias() -> FileResponse:
            return _public_file("status.html")

        @app.get("/gallery")
        async def gallery_alias() -> FileResponse:
            return _public_file("gallery.html")

        @app.get("/personas")
        async def personas_alias() -> FileResponse:
            return _public_file("personas.html")

        @app.get("/specs")
        async def specs_alias() -> FileResponse:
            return _public_file("specs.html")

        @app.get("/demo")
        async def demo_alias() -> FileResponse:
            return _public_file("index.html")

        # Mount the entire public dir at /static/* for asset references like
        # /static/main.js, /static/style.css, /static/atlas.json, etc.
        # Also mount at /assets/* (legacy) for the Vite-style asset path.
        app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")
        app.mount("/assets", StaticFiles(directory=PUBLIC_DIR), name="assets")

        # Direct top-level fallthrough for files that the public HTML references
        # by bare name (main.js, style.css, atlas.json, brain_fsaverage5.glb, etc.).
        # We expose these explicitly rather than mounting public/ at "/" because
        # mounting at "/" shadows every API route.
        # Each response gets a long Cache-Control so Cloudflare and browsers
        # cache hard. Bust by bumping the ?v=… query string in the HTML <script>
        # tags when assets change.
        STATIC_CACHE = {
            ".js":   "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800, immutable",
            ".css":  "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800, immutable",
            ".glb":  "public, max-age=86400, s-maxage=2592000, stale-while-revalidate=2592000, immutable",
            ".json": "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800",
            ".svg":  "public, max-age=86400, s-maxage=2592000, immutable",
        }
        for _name in ("main.js", "charts.js", "style.css", "atlas.json", "brain_fsaverage5.glb",
                      "vertex_labels.json", "favicon.svg",
                      "gridstack-all.js", "gridstack.min.css",
                      "cortex-nav.js"):
            _path = PUBLIC_DIR / _name
            if _path.exists():
                _ext = _path.suffix.lower()
                _ttl = STATIC_CACHE.get(_ext, "public, max-age=3600")
                # Capture loop vars via default args to avoid late-binding
                async def _serve(_p=_path, _ct=_ttl):
                    return FileResponse(str(_p), headers={"Cache-Control": _ct})
                app.add_api_route(f"/{_name}", _serve, methods=["GET"])

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
        import httpx
        or_model = model[len("openrouter:"):]
        api_key = _load_openrouter_api_key()
        if not api_key:
            return (
                "OpenRouter narration is selected, but OPENROUTER_API_KEY is not configured. "
                "TRIBE results were produced locally; add a valid OpenRouter key to enable cloud narration."
            )
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
            if resp.status_code >= 400:
                try:
                    err_msg = _safe_openrouter_message(resp.json())
                except Exception:
                    err_msg = ""
                return (
                    f"OpenRouter narration unavailable ({resp.status_code}). "
                    f"{err_msg or 'Check the API key, credits, and selected model.'}"
                )
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
    narration_model: str = f"openrouter:{OPENROUTER_DEFAULT_MODEL}",
) -> None:
    """Image scan: describe the image, then narrate expected neural correlates.

    When OpenRouter is selected, use OpenRouter multimodal description so local
    Gemma does not occupy VRAM that should remain available for TRIBE.
    """
    queue: RequestQueue = app.state.queue
    registry: ScanRegistry = app.state.registry
    hub: WebSocketHub = app.state.hub

    async def _emit(phase: str, **extra: Any) -> None:
        await hub.broadcast({"type": "scan_progress", "scan_id": scan_id, "phase": phase, **extra})
        await registry.update(scan_id, status=phase)

    try:
        await _emit("narrating")

        loop = asyncio.get_event_loop()
        if narration_model.startswith("openrouter:"):
            visual_context = await _describe_media_for_prompt(Path(media_path))
        else:
            desc = await loop.run_in_executor(
                None, lambda: _media_gate.classify_image(Path(media_path))
            )
            visual_context = f"Visual description: {desc.short_description()}"
        brain_ctx = (
            f"Input modality: image\n"
            f"{visual_context}\n\n"
            "No fMRI scan was performed. Based on cognitive neuroscience knowledge, "
            "describe the brain regions and networks expected to activate when a person "
            "views this image."
        )
        label = Path(media_path).name
        user_prompt = _prompts.TIER_USER_TEMPLATE.format(label=label, brain_context=brain_ctx)

        # Run all 4 persona narrations in parallel — Ollama NUM_PARALLEL=4 batches
        # them on the same model instance, OpenRouter handles them concurrently.
        async def _one(pid: str, tier_n: int, sys_prompt: str) -> tuple[str, str]:
            text = await _narrate_with_model(
                model=narration_model,
                prompt=user_prompt,
                system=sys_prompt,
                tier=tier_n,
                num_predict=_tiers._TIER_NUM_PREDICT[tier_n],
                temperature=_tiers._TIER_TEMPERATURE[tier_n],
                queue=queue,
                source=source,
            )
            return pid, text
        results = await asyncio.gather(*[
            _one(pid, t, sp) for pid, (t, sp) in _prompts.PERSONA_CONFIGS.items()
        ])
        narrations: dict[str, str] = dict(results)

        await registry.update(scan_id, status="complete", narration=narrations.get("student", ""), narrations=narrations, top_rois=None, peak_t=None)
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
    narration_model: str = f"openrouter:{OPENROUTER_DEFAULT_MODEL}",
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

        # Run all 4 persona narrations in parallel.
        async def _one(pid: str, tier_n: int, sys_prompt: str) -> tuple[str, str]:
            text = await _narrate_with_model(
                model=narration_model,
                prompt=user_prompt,
                system=sys_prompt,
                tier=tier_n,
                num_predict=_tiers._TIER_NUM_PREDICT[tier_n],
                temperature=_tiers._TIER_TEMPERATURE[tier_n],
                queue=queue,
                source=source,
            )
            return pid, text
        results = await asyncio.gather(*[
            _one(pid, t, sp) for pid, (t, sp) in _prompts.PERSONA_CONFIGS.items()
        ])
        narrations: dict[str, str] = dict(results)

        await registry.update(scan_id, status="complete", narration=narrations.get("student", ""), narrations=narrations, top_rois=None, peak_t=None)
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
    narration_model: str = f"openrouter:{OPENROUTER_DEFAULT_MODEL}",
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
        scan_t0 = time.time()
        await _emit("running")
        result = await queue.submit(
            request_type=RequestType.BRAIN_SCAN,
            payload={"media_path": media_path},
            priority=0 if source == "webui" else 5,
            source=source,
        )
        tribe_seconds = round(time.time() - scan_t0, 2)
        await _emit("narrating", tribe_seconds=tribe_seconds)

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

        # Build full brain context so the narrator gets both the real BOLD data
        # and modality/source context. For video/audio this preserves soundtrack
        # awareness through OpenRouter multimodal description or metadata fallback.
        loop = asyncio.get_event_loop()
        bold_ctx = await loop.run_in_executor(
            None,
            lambda: analyse(result, harvard_oxford=False, juelich=False).gemma_context(),
        )
        media_ctx = await _describe_media_for_prompt(Path(media_path))
        brain_ctx = f"{media_ctx}\n\nTRIBE v2 BOLD response summary:\n{bold_ctx}"
        label = Path(media_path).name
        user_prompt = _prompts.TIER_USER_TEMPLATE.format(label=label, brain_context=brain_ctx)

        # Fan out all 4 persona narrations concurrently — Ollama NUM_PARALLEL=4
        # batches them on one model instance, OpenRouter handles them in parallel.
        narrations: dict[str, str] = {}
        narration_timings: dict[str, float] = {}
        narr_t0 = time.time()

        async def _one_narr(pid: str, tier_n: int, sys_prompt: str) -> tuple[str, str, float]:
            t_pers = time.time()
            text = await _narrate_with_model(
                model=narration_model,
                prompt=user_prompt,
                system=sys_prompt,
                tier=tier_n,
                num_predict=_tiers._TIER_NUM_PREDICT[tier_n],
                temperature=_tiers._TIER_TEMPERATURE[tier_n],
                queue=queue,
                source=source,
            )
            return pid, text, round(time.time() - t_pers, 2)

        narr_results = await asyncio.gather(*[
            _one_narr(pid, t, sp) for pid, (t, sp) in _prompts.PERSONA_CONFIGS.items()
        ])
        for pid, text, dt in narr_results:
            narrations[pid] = text
            narration_timings[pid] = dt
        narration_seconds = round(time.time() - narr_t0, 2)

        preds = getattr(result, "preds", None)
        await registry.update(
            scan_id,
            status="complete",
            top_rois=getattr(result, "top_rois", None),
            peak_t=getattr(result, "peak_t", None),
            seconds_elapsed=getattr(result, "seconds_elapsed", None),
            tribe_seconds=tribe_seconds,
            narration_seconds=narration_seconds,
            narration_timings=narration_timings,
            narration=narrations.get("student", ""),
            narrations=narrations,
            media_context=media_ctx,
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
# Text-only scan (typed text is persisted as .txt and routed through TRIBE)
# ---------------------------------------------------------------------------

async def _run_text_scan_background(
    app: FastAPI,
    scan_id: str,
    text: str,
    tier: int,
    source: str,
    narration_model: str = f"openrouter:{OPENROUTER_DEFAULT_MODEL}",
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
        user_prompt = _prompts.TIER_USER_TEMPLATE.format(label="text stimulus", brain_context=brain_ctx)

        async def _one(pid: str, tier_n: int, sys_prompt: str) -> tuple[str, str]:
            text_out = await _narrate_with_model(
                model=narration_model,
                prompt=user_prompt,
                system=sys_prompt,
                tier=tier_n,
                num_predict=_tiers._TIER_NUM_PREDICT[tier_n],
                temperature=_tiers._TIER_TEMPERATURE[tier_n],
                queue=queue,
                source=source,
            )
            return pid, text_out

        results = await asyncio.gather(*[
            _one(pid, t, sp) for pid, (t, sp) in _prompts.PERSONA_CONFIGS.items()
        ])
        narrations: dict[str, str] = dict(results)

        await registry.update(
            scan_id,
            status="complete",
            narration=narrations.get("student", next(iter(narrations.values()), "")),
            narrations=narrations,
            top_rois=None,
            peak_t=None,
        )
        await hub.broadcast({"type": "scan_complete", "scan_id": scan_id})
        await hub.broadcast({"type": "scan_narrations_ready", "scan_id": scan_id, "narrations": narrations})
        log.info("[webapp] text scan %s complete", scan_id)

    except Exception as exc:
        err = CortexError(code=ErrorCode.INFERENCE_FAILED, message=str(exc), component="webapp.text_scan")
        await registry.update(scan_id, status="failed", error=err.to_dict())
        await hub.broadcast({"type": "scan_failed", "scan_id": scan_id, "error": err.to_dict()})
        log.error("[webapp] text scan %s failed: %s", scan_id, exc)


# Default app instance (used by `uvicorn webapp.server:app`)
app = create_app()
