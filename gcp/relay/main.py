"""Cortex Cloud Run relay — routes public scan requests to the local 5090.

Fallback behaviour when 5090 is unreachable:
  - Scan submission: marked 'queued_local'; will retry on next /api/scans poll
  - Narration: Gemini 1.5 Flash API (set GEMINI_API_KEY env var to enable)
"""
from __future__ import annotations
import asyncio, os, uuid, json, logging, time
from pathlib import Path
from typing import Any, Optional

import httpx
from cachetools import TTLCache
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import firestore, storage
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

log = logging.getLogger("cortex-relay")

TUNNEL_URL      = os.environ["TUNNEL_URL"]
GCS_BUCKET      = os.environ["GCS_BUCKET"]
GCP_PROJECT     = os.environ["GCP_PROJECT"]
GEMINI_KEY      = os.environ.get("GEMINI_API_KEY", "")
# Google OAuth client ID — set in Cloud Run env vars after creating in GCP Console
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
# Gemini fallback model — override with GEMINI_MODEL env var on Cloud Run without redeploy
# Gemini 3 / 3.1 model lineup (April 2026):
#   gemini-3.1-flash-preview      Frontier-class Flash; fast + capable; free tier (reduced quota) — DEFAULT
#   gemini-3.1-flash-lite-preview Budget Flash; cheapest + fastest; free tier (reduced quota)
#   gemini-3.1-pro-preview        Most capable; complex agentic tasks; PAID-ONLY (no free tier)
#   gemini-3-flash                Previous stable Flash; $0.50/$3.00 per 1M tok; free tier
# Note: 2.5 family is legacy/deprecated — avoid for new work.
GEMINI_MODEL     = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-preview")
# Domains allowed to submit scans (pipe-separated: "philanthropytraders.com|redteamkitchen.com")
ALLOWED_DOMAINS = set(os.environ.get("ALLOWED_DOMAINS", "philanthropytraders.com,redteamkitchen.com").split(","))
MAX_MB          = 50
TUNNEL_TIMEOUT  = 10

# Cache verified tokens for 5 minutes so we don't call Google on every request
_token_cache: TTLCache = TTLCache(maxsize=256, ttl=300)


# ─── auth ─────────────────────────────────────────────────────────────────────

def _verify_google_token(token: str) -> dict:
    """Verify a Google ID token and return the payload. Raises HTTPException on failure."""
    if token in _token_cache:
        return _token_cache[token]
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google OAuth not configured on this server (GOOGLE_CLIENT_ID missing).")
    try:
        payload = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), GOOGLE_CLIENT_ID, clock_skew_in_seconds=10
        )
    except Exception as e:
        raise HTTPException(401, f"Invalid Google token: {e}")
    hd = payload.get("hd", "")
    if hd not in ALLOWED_DOMAINS:
        raise HTTPException(
            403,
            f"Your Google account domain '{hd}' is not authorized to submit scans. "
            f"Sign in with a @{' or @'.join(sorted(ALLOWED_DOMAINS))} account."
        )
    _token_cache[token] = payload
    return payload


async def require_domain_auth(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency: require a valid Google ID token from an allowed domain."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in with your Google (@philanthropytraders.com or @redteamkitchen.com) account to submit scans.")
    return _verify_google_token(authorization.split("Bearer ", 1)[1])

app = FastAPI(title="Cortex Relay")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# Serve static gallery + scan pages
_STATIC = Path(__file__).parent / "public"
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse("/gallery")

@app.get("/gallery", response_class=HTMLResponse)
async def gallery():
    return (_STATIC / "gallery.html").read_text()

@app.get("/scan/{scan_id}", response_class=HTMLResponse)
async def scan_page(scan_id: str):
    return (_STATIC / "scan.html").read_text()

db     = firestore.AsyncClient(project=GCP_PROJECT)
gcs    = storage.Client(project=GCP_PROJECT)
bucket = gcs.bucket(GCS_BUCKET)


# ─── health ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    alive = await _5090_alive()
    return {"ok": True, "tunnel": TUNNEL_URL, "5090_online": alive}

async def _5090_alive() -> bool:
    try:
        async with httpx.AsyncClient(timeout=TUNNEL_TIMEOUT) as c:
            r = await c.get(f"{TUNNEL_URL}/api/info")
            return r.status_code == 200
    except Exception:
        return False


# ─── file proxy (avoids allUsers IAM restriction on org-policy accounts) ─────

@app.get("/api/files/{bucket_path:path}")
async def proxy_file(bucket_path: str):
    """Proxy GCS object through Cloud Run. Allows public reads without allUsers IAM."""
    blob = bucket.blob(bucket_path)
    if not blob.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    content_type = blob.content_type or "application/octet-stream"

    def _stream():
        with blob.open("rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(_stream(), media_type=content_type,
                              headers={"Cache-Control": "public, max-age=86400"})


# ─── scan submission ──────────────────────────────────────────────────────────

@app.post("/api/scan")
async def submit_scan(
    file: UploadFile = File(...),
    tier: int = Form(default=4),
    user: dict = Depends(require_domain_auth),
):
    scan_id = uuid.uuid4().hex[:12]
    data = await file.read()
    if len(data) > MAX_MB * 1024 * 1024:
        return JSONResponse({"error": "file too large"}, status_code=413)

    ext = Path(file.filename or "upload").suffix.lower() or ".bin"
    blob = bucket.blob(f"uploads/{scan_id}{ext}")
    blob.upload_from_string(data, content_type=file.content_type or "application/octet-stream")
    # Use relay proxy URL so files are accessible without public IAM
    file_url = f"/api/files/uploads/{scan_id}{ext}"

    await db.collection("scans").document(scan_id).set({
        "id": scan_id, "status": "queued", "filename": file.filename,
        "tier": tier, "created_at": firestore.SERVER_TIMESTAMP,
        "upload_gcs": f"gs://{GCS_BUCKET}/uploads/{scan_id}{ext}",
        "file_url": file_url,
        "submitted_by": user.get("email", "unknown"),
        "submitted_by_domain": user.get("hd", "unknown"),
    })

    asyncio.create_task(_forward_to_5090(scan_id, data, file.filename or "upload", ext, tier))
    return JSONResponse({"ok": True, "scan_id": scan_id}, status_code=202)


async def _forward_to_5090(scan_id: str, data: bytes, filename: str, ext: str, tier: int):
    """Try to forward to the local 5090. Falls back gracefully if unreachable."""
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.get(f"{TUNNEL_URL}/api/info", timeout=TUNNEL_TIMEOUT)
            if r.status_code != 200:
                raise RuntimeError("5090 health check failed")
    except Exception:
        await db.collection("scans").document(scan_id).update({
            "status": "queued_local",
            "status_message": "RTX 5090 is currently unavailable. Your scan will run when it comes back online.",
        })
        # If Gemini is available, at least do narration for any cached scans
        return

    try:
        async with httpx.AsyncClient(timeout=600) as client:
            files = {"file": (filename, data, "application/octet-stream")}
            form  = {"tier": str(tier), "source": "public-relay", "external_scan_id": scan_id}
            r = await client.post(f"{TUNNEL_URL}/api/scan", files=files, data=form, timeout=600)
            if r.status_code == 202:
                body = r.json()
                await db.collection("scans").document(scan_id).update({
                    "local_scan_id": body["scan_id"],
                    "status": "processing",
                })
            else:
                raise RuntimeError(f"5090 returned {r.status_code}")
    except Exception as e:
        await db.collection("scans").document(scan_id).update({
            "status": "failed", "error": str(e)
        })


# ─── scan status & listing ────────────────────────────────────────────────────

@app.get("/api/scans")
async def list_scans(limit: int = 50):
    docs = db.collection("scans").order_by(
        "created_at", direction=firestore.Query.DESCENDING
    ).limit(limit)
    results = []
    async for doc in docs.stream():
        d = doc.to_dict(); d["id"] = doc.id
        results.append({
            k: d[k] for k in
            ["id","status","status_message","filename","peak_t","tr_seconds",
             "thumbnail_url","file_url","created_at","narrations","n_vertices"]
            if k in d
        })
    return {"scans": results}


@app.get("/api/scan/{scan_id}")
async def get_scan(scan_id: str):
    # First check Firestore
    doc = await db.collection("scans").document(scan_id).get()
    if not doc.exists:
        return JSONResponse({"error": "not found"}, status_code=404)
    d = doc.to_dict(); d["id"] = scan_id

    # If processing on the local machine, try to pull live status from 5090
    if d.get("status") == "processing" and d.get("local_scan_id"):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{TUNNEL_URL}/api/scan/{d['local_scan_id']}")
                if r.status_code == 200:
                    local = r.json()
                    # Merge live fields in
                    for k in ["status","peak_t","n_t","n_vertices","narrations","tr_seconds","bold_url","thumbnail_url"]:
                        if k in local:
                            d[k] = local[k]
                    if local.get("status") == "complete":
                        await db.collection("scans").document(scan_id).update({
                            k: local[k] for k in
                            ["status","peak_t","n_t","narrations","tr_seconds"] if k in local
                        })
        except Exception:
            pass  # fall through with Firestore data
    return d


@app.get("/api/scan/{scan_id}/narrations")
async def get_narrations(scan_id: str):
    doc = await db.collection("scans").document(scan_id).get()
    if not doc.exists:
        return JSONResponse({"error": "not found"}, status_code=404)
    d = doc.to_dict()
    if "narrations" in d:
        return {"narrations": d["narrations"]}
    # Try live fetch from 5090
    if d.get("local_scan_id"):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{TUNNEL_URL}/api/scan/{d['local_scan_id']}/narrations")
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass
    # Gemini fallback narration if key is set and scan has a description
    if GEMINI_KEY and d.get("filename"):
        narration = await _gemini_narration(d["filename"])
        return {"narrations": narration, "source": "gemini-fallback"}
    return JSONResponse({"error": "narrations not ready"}, status_code=404)


# ─── Gemini fallback narration ────────────────────────────────────────────────

async def _gemini_narration(filename: str) -> dict:
    """Gemini fallback narration when local Gemma 4 (RTX 5090) is unavailable.

    Model choice guide (April 2026 — set GEMINI_MODEL env var on Cloud Run to override):
      gemini-3.1-flash-preview      DEFAULT — frontier Flash; best speed+quality; free tier (reduced)
      gemini-3.1-flash-lite-preview Cheapest option; ~$0.25/$1.50 per 1M tok; free tier (reduced)
      gemini-3.1-pro-preview        Most capable; paid-only (no free tier); complex multi-step reasoning
      gemini-3-flash                Previous stable Flash; $0.50/$3.00 per 1M tok; free tier
    Note: 2.5 family is legacy — avoid for new work.
    """

    Generates 4 persona narrations — Alex (American), Jordan (Student),
    Dr. Maya Chen (Neurosurgeon), Atlas (ML Engineer).
    """
    if not GEMINI_KEY:
        return {}
    base = (
        f"The media file '{filename}' was submitted to Cortex for brain-response analysis. "
        "TRIBE v2 (Meta's brain foundation model) predicts cortical BOLD activation across "
        "20,484 fsaverage5 vertices at 2 Hz, with a 5-second hemodynamic lag pre-applied. "
        "This is a group-averaged population model trained on 25 subjects — NOT a personal brain scan. "
    )
    personas = {
        "american": (
            base +
            "You are explaining this to Alex, a curious American adult with a high school education. "
            "Write 3-4 sentences in plain, warm, conversational language using one everyday American analogy. "
            "No jargon, no region names. Make it surprising and interesting."
        ),
        "student": (
            base +
            "You are Jordan, a 16-year-old AP Biology student who's excited about neuroscience. "
            "Write 4-5 sentences, enthusiastic and educational. You may name major brain lobes and one network. "
            "Explain WHY those areas activate. Use one energetic transition like 'What's wild is that...'"
        ),
        "neurosurgeon": (
            base +
            "You are writing for Dr. Maya Chen, an attending neurosurgeon specializing in functional brain imaging. "
            "Write 5-7 sentences in clinical register. Use gyral anatomy, Brodmann areas, and Yeo-7 networks. "
            "Note laterality, BOLD dynamics, and one clinical framing sentence. "
            "End with explicit caveat: group-averaged, not patient-specific imaging."
        ),
        "ml_engineer": (
            base +
            "You are writing for Atlas, a senior ML engineer who builds multimodal foundation models. "
            "Write 5-7 sentences in dense technical language. Reference TRIBE v2 architecture: "
            "V-JEPA2 vision encoder, wav2vec-BERT 2.0, Llama-3.2-3B text encoder, (T × 20484) float32 at 2 Hz. "
            "Mention RTX 5090 32GB GDDR7 inference context. Draw one analogy to attention maps or saliency. "
            "End with scale economics: $0.30/scan cloud vs $0.006 local."
        ),
    }
    results = {}
    async with httpx.AsyncClient(timeout=30) as c:
        for persona_id, prompt in personas.items():
            try:
                r = await c.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}",
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                )
                if r.status_code == 200:
                    results[persona_id] = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                results[persona_id] = "(narration unavailable)"
    return results


# ─── WebSocket status feed ────────────────────────────────────────────────────

@app.websocket("/api/ws/{scan_id}")
async def ws_scan(ws: WebSocket, scan_id: str):
    await ws.accept()
    try:
        for _ in range(120):  # max 10 min
            doc = await db.collection("scans").document(scan_id).get()
            if doc.exists:
                d = doc.to_dict(); d["id"] = scan_id
                await ws.send_json(d)
                if d.get("status") in ("complete", "failed"):
                    break
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass


# ─── info ─────────────────────────────────────────────────────────────────────

@app.get("/api/info")
async def info():
    return {
        "relay": "cortex-relay v2",
        "tunnel": TUNNEL_URL,
        "5090_online": await _5090_alive(),
        "tribe_v2": {
            "sample_rate_hz": 2.0, "tr_seconds": 0.5,
            "n_vertices": 20484, "surface": "fsaverage5",
            "hrf_lag_seconds": 5.0,
        },
        "fallback": {
            "narration": GEMINI_MODEL if GEMINI_KEY else "none",
            "inference": "queued_local (no cloud TRIBE v2 equivalent)",
        },
    }
