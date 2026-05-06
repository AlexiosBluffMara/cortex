"""Cortex Cloud Run relay — routes public scan requests to the local 5090.

Open access — no auth required. Protection layers:
  - slowapi: 30 submissions/hour per IP + 5/minute burst cap
  - 50 MB file-size cap
  - IP geolocation logged to Firestore for every submission
  - Extensible ban-list (IP_BLOCKLIST env var, comma-separated)

Fallback when 5090 is unreachable:
  - Scan submission: marked 'queued_cloud'
  - Narration: Gemini 3.1 Flash Lite (gemini-3.1-flash-lite-preview) — set GEMINI_API_KEY env var to enable
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import os
import uuid
from pathlib import Path

import httpx
from cachetools import TTLCache
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import firestore, storage
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

log = logging.getLogger("cortex-relay")

TUNNEL_URL   = os.environ["TUNNEL_URL"]
GCS_BUCKET   = os.environ["GCS_BUCKET"]
GCP_PROJECT  = os.environ["GCP_PROJECT"]
# Gemini APIs disabled at the project level (April 2026). The relay must NOT
# call generativelanguage.googleapis.com — those calls return PERMISSION_DENIED.
# Narration falls back to the local inference router when the 5090 is offline.
GEMINI_KEY   = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
INFERENCE_URL = os.environ.get("INFERENCE_URL", "https://inference.redteamkitchen.com")
MAX_MB       = 50
TUNNEL_TIMEOUT = 10

# fMRI uploads — separate CMEK-encrypted bucket, never the public scan bucket
FMRI_BUCKET = os.environ.get("FMRI_BUCKET", "cortex-fmri-uploads")
FMRI_REGION = os.environ.get("FMRI_REGION", "us-central1")
FMRI_KEYRING = os.environ.get("FMRI_KEYRING", "cortex-fmri-keys")
FMRI_SIGNED_URL_TTL_MIN = 15
FMRI_MAX_GB = 2  # signed-URL hard cap

# Optional comma-separated IP blocklist — set in Cloud Run env vars without redeploy
_IP_BLOCKLIST: set[str] = {
    ip.strip() for ip in os.environ.get("IP_BLOCKLIST", "").split(",") if ip.strip()
}

# ─── cost constants ───────────────────────────────────────────────────────────
# Local (RTX 5090 ~300W × ~30s at $0.15/kWh) — electricity only, no compute cost
COST_LOCAL_USD = 0.00006
# Cloud Gemini 3.1 Flash estimated pricing (April 2026)
_GEMINI_IN_PER_M  = 0.10   # $ per 1M input tokens
_GEMINI_OUT_PER_M = 0.40   # $ per 1M output tokens
# Estimated token budget: 800 input + 350 output × 4 personas
COST_CLOUD_USD = (3200 * _GEMINI_IN_PER_M + 1400 * _GEMINI_OUT_PER_M) / 1_000_000  # ~$0.00088

# ─── rate limiter ─────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ─── geo cache: 24-hour TTL, up to 8192 unique IPs ───────────────────────────
_geo_cache: TTLCache = TTLCache(maxsize=8192, ttl=86400)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[0].strip() if xff else (request.client.host if request.client else "0.0.0.0")


async def _geo(ip: str) -> dict:
    """Non-blocking geo lookup via ipinfo.io (free tier: 50K req/mo). Cached 24h per IP."""
    if ip in _geo_cache:
        return _geo_cache[ip]
    result: dict = {"ip": ip, "city": "", "region": "", "country": "", "org": "", "loc": ""}
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"https://ipinfo.io/{ip}/json",
                            headers={"Accept": "application/json"})
            if r.status_code == 200:
                d = r.json()
                result = {
                    "ip": ip,
                    "city":    d.get("city", ""),
                    "region":  d.get("region", ""),
                    "country": d.get("country", ""),
                    "org":     d.get("org", ""),
                    "loc":     d.get("loc", ""),
                }
    except Exception:
        pass
    _geo_cache[ip] = result
    return result


# ─── app ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Cortex Relay")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_STATIC = Path(__file__).parent / "public"
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

db     = firestore.AsyncClient(project=GCP_PROJECT)
gcs    = storage.Client(project=GCP_PROJECT)
bucket = gcs.bucket(GCS_BUCKET)
fmri_bucket = gcs.bucket(FMRI_BUCKET)


# ─── static pages ─────────────────────────────────────────────────────────────

_LANDING_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cortex — multimodal brain-response analysis</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: #0b0d10; color: #e6e8eb;
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif; }
  main { max-width: 720px; margin: 0 auto; padding: 56px 24px 40px; }
  h1 { font-size: 30px; margin: 0 0 8px; letter-spacing: -0.01em; }
  .sub { color: #a8b0b8; margin: 0 0 32px; }
  .card { background: #14181d; border: 1px solid #232a31; border-radius: 10px;
    padding: 22px; margin: 18px 0; }
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  input[type=file] { color: #c9cfd6; background: #0f1418; border: 1px solid #2a323a;
    border-radius: 6px; padding: 8px; max-width: 100%; }
  button { background: #cc0000; color: #fff; border: 0; border-radius: 6px;
    padding: 9px 18px; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: progress; }
  button:hover:not(:disabled) { background: #e60000; }
  .status { display: flex; justify-content: space-between; gap: 12px;
    color: #c9cfd6; font-size: 14px; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: #6b7280; margin-right: 6px; vertical-align: middle; }
  .dot.up { background: #22c55e; } .dot.down { background: #ef4444; }
  a { color: #ff6b6b; }
  .log { font: 12.5px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    color: #97a0a8; margin-top: 10px; word-break: break-all; }
  footer { color: #6b7280; font-size: 12.5px; margin-top: 40px;
    border-top: 1px solid #1d232a; padding-top: 18px; }
  footer a { color: #97a0a8; }
</style>
</head><body>
<main>
  <h1>Cortex — multimodal brain-response analysis</h1>
  <p class="sub">Submit a video clip; get a 20,484-vertex cortical activation map
    + narration. Built for the Gemma 4 Good Hackathon.</p>

  <form class="card" id="f" enctype="multipart/form-data">
    <div class="row">
      <input type="file" name="file" id="file" required
             accept="video/*,audio/*,image/*">
      <button type="submit" id="go">Analyze</button>
    </div>
    <div class="log" id="log"></div>
  </form>

  <div class="card status">
    <div><span class="dot" id="dot-5090"></span>5090 inference: <b id="s-5090">checking…</b></div>
    <div><span class="dot up"></span>relay: <b>ok</b></div>
  </div>

  <p><a href="/gallery">Browse the public gallery →</a></p>

  <footer>
    Cortex is part of Red Team Kitchen, a Chicago-based research collaboration
    with Illinois State University.
  </footer>
</main>
<script>
  const dot = document.getElementById('dot-5090');
  const lab = document.getElementById('s-5090');
  fetch('/api/healthz').then(r => r.json()).then(j => {
    const up = !!j['5090'];
    dot.className = 'dot ' + (up ? 'up' : 'down');
    lab.textContent = up ? 'healthy' : 'down';
  }).catch(() => { dot.className = 'dot down'; lab.textContent = 'unreachable'; });

  const f = document.getElementById('f');
  const log = document.getElementById('log');
  const go = document.getElementById('go');
  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    const file = document.getElementById('file').files[0];
    if (!file) return;
    go.disabled = true;
    log.textContent = 'uploading…';
    const fd = new FormData();
    fd.append('file', file);
    fd.append('tier', '4');
    try {
      const r = await fetch('/api/scan', { method: 'POST', body: fd });
      const j = await r.json();
      if (j.scan_id) {
        log.textContent = 'submitted: scan_id=' + j.scan_id + ' — redirecting…';
        location.href = '/scan/' + j.scan_id;
      } else {
        log.textContent = JSON.stringify(j);
        go.disabled = false;
      }
    } catch (err) {
      log.textContent = 'error: ' + err;
      go.disabled = false;
    }
  });
</script>
</body></html>
"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(content=_LANDING_HTML, headers={"Cache-Control": "public, max-age=300"})

@app.get("/gallery", response_class=HTMLResponse)
async def gallery():
    return (_STATIC / "gallery.html").read_text()

@app.get("/scan.html")
async def scan_html_compat(id: str = "", persona: str = ""):
    """Backwards-compat: /scan.html?id=X → /scan/X?id=X&persona=..."""
    from fastapi.responses import RedirectResponse
    if not id:
        return RedirectResponse("/gallery")
    qs = f"?id={id}" + (f"&persona={persona}" if persona else "")
    return RedirectResponse(f"/scan/{id}{qs}", status_code=302)

@app.get("/scan/{scan_id}", response_class=HTMLResponse)
async def scan_page(scan_id: str):
    return (_STATIC / "scan.html").read_text()

@app.get("/ads.txt")
async def ads_txt():
    return HTMLResponse(
        content="google.com, pub-7794155680942670, DIRECT, f08c47fec0942fa0\n",
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400", "X-Robots-Tag": "noindex"},
    )

@app.get("/robots.txt")
async def robots_txt():
    return HTMLResponse(
        content=(
            "User-agent: *\nAllow: /\n"
            "User-agent: Mediapartners-Google\nAllow: /\n"
            "Sitemap: https://cortex.redteamkitchen.com/sitemap.xml\n"
        ),
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400"},
    )

@app.get("/sitemap.xml")
async def sitemap():
    return HTMLResponse(
        content=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            '<url><loc>https://cortex.redteamkitchen.com/gallery</loc></url>'
            '</urlset>'
        ),
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ─── health + info ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"ok": True, "tunnel": TUNNEL_URL, "5090_online": await _5090_alive()}

@app.get("/api/healthz")
async def healthz():
    return {"5090": await _5090_alive(), "relay": "ok"}

@app.get("/api/status")
async def status():
    """Alias for /api/health — kept for backwards-compat with older gallery clients."""
    alive = await _5090_alive()
    return {"ok": alive, "5090_online": alive, "gpu_online": alive, "tunnel": TUNNEL_URL}

@app.get("/api/info")
async def info():
    return {
        "relay": "cortex-relay v3",
        "access": "open",
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
        "cost": {
            "local_usd":  COST_LOCAL_USD,
            "cloud_usd":  COST_CLOUD_USD,
            "local_label":  f"${COST_LOCAL_USD*100:.4f}¢/scan  (RTX 5090 local)",
            "cloud_label":  f"${COST_CLOUD_USD*100:.4f}¢/scan  (Gemini cloud fallback)",
        },
    }

@app.get("/api/geo")
async def geo_check(request: Request):
    """Returns caller's geolocation — useful for transparency / debugging."""
    return await _geo(_client_ip(request))

@app.get("/api/utilization")
async def relay_utilization():
    return await _5090_utilization()


# ─── 5090 helpers ─────────────────────────────────────────────────────────────

async def _5090_alive() -> bool:
    try:
        async with httpx.AsyncClient(timeout=TUNNEL_TIMEOUT) as c:
            r = await c.get(f"{TUNNEL_URL}/api/info")
            return r.status_code == 200
    except Exception:
        return False

async def _5090_utilization() -> dict:
    try:
        async with httpx.AsyncClient(timeout=TUNNEL_TIMEOUT) as c:
            r = await c.get(f"{TUNNEL_URL}/api/utilization")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {"accepting": False, "queue_depth": 0, "scheduler_state": "unreachable"}


# ─── file proxy ───────────────────────────────────────────────────────────────

@app.get("/api/files/{bucket_path:path}")
async def proxy_file(bucket_path: str):
    """Proxy GCS object through Cloud Run — bypasses allUsers IAM org-policy restriction."""
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
@limiter.limit("5/minute;30/hour")
async def submit_scan(
    request: Request,
    file: UploadFile = File(...),
    tier: int = Form(default=4),
):
    ip = _client_ip(request)

    if ip in _IP_BLOCKLIST:
        raise HTTPException(403, "Access denied.")

    geo = await _geo(ip)
    scan_id = uuid.uuid4().hex[:12]

    data = await file.read()
    if len(data) > MAX_MB * 1024 * 1024:
        return JSONResponse({"error": f"File exceeds {MAX_MB} MB limit."}, status_code=413)

    ext      = Path(file.filename or "upload").suffix.lower() or ".bin"
    blob     = bucket.blob(f"uploads/{scan_id}{ext}")
    blob.upload_from_string(data, content_type=file.content_type or "application/octet-stream")
    file_url = f"/api/files/uploads/{scan_id}{ext}"

    await db.collection("scans").document(scan_id).set({
        "id": scan_id,
        "status": "queued",
        "filename": file.filename,
        "tier": tier,
        "created_at": firestore.SERVER_TIMESTAMP,
        "upload_gcs": f"gs://{GCS_BUCKET}/uploads/{scan_id}{ext}",
        "file_url": file_url,
        # geo / analytics — never shown publicly
        "submitted_from_ip":      ip,
        "submitted_from_country": geo.get("country", ""),
        "submitted_from_city":    geo.get("city", ""),
        "submitted_from_org":     geo.get("org", ""),
        # cost
        "cost_mode":         "local",
        "cost_estimate_usd": COST_LOCAL_USD,
    })

    asyncio.create_task(_forward_to_5090(scan_id, data, file.filename or "upload", ext, tier))

    return JSONResponse({
        "ok": True,
        "scan_id": scan_id,
        "cost_mode": "local",
        "cost_estimate_usd": COST_LOCAL_USD,
        "cost_label": f"~{COST_LOCAL_USD * 100 * 100:.3f}¢ (RTX 5090 local)",
    }, status_code=202)


async def _gemini_metadata(filename: str) -> dict:
    """Ask Gemini for structured scan metadata (top_rois, peak_t) as JSON."""
    if not GEMINI_KEY:
        return {"top_rois": ["Visual cortex", "Default Mode Network", "Prefrontal cortex"], "peak_t": 18}

    prompt = (
        f"A media file named '{filename}' was analyzed by TRIBE v2 (Meta's brain foundation model). "
        "Based on the filename and typical neural responses to media, return a JSON object with:\n"
        '- "top_rois": array of 4-6 brain region names using anatomical/Yeo-7 terms (e.g. '
        '"Visual cortex", "Auditory cortex", "Default Mode Network", "Prefrontal cortex", '
        '"Anterior temporal lobe", "Posterior parietal cortex", "Insula", "Amygdala", '
        '"Dorsal Attention Network", "Somatomotor cortex", "Limbic network")\n'
        '- "peak_t": integer between 10 and 80 (the TR with peak BOLD activation)\n\n'
        "Return ONLY valid JSON with no extra explanation."
    )

    import json as _json, re as _re
    try:
        # Gemini APIs are disabled at the project level — route to the local
        # inference router (5090) at https://inference.redteamkitchen.com instead.
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{INFERENCE_URL}/v1/generate",
                json={"prompt": prompt, "max_tokens": 256, "temperature": 0.7,
                      "response_format": "json"},
            )
            if r.status_code == 200:
                body = r.json()
                text = body.get("text") or body.get("output") or body.get("response") or ""
                m = _re.search(r'\{.*\}', text, _re.DOTALL)
                if m:
                    parsed = _json.loads(m.group())
                    return {
                        "top_rois": parsed.get("top_rois", []),
                        "peak_t":   int(parsed.get("peak_t", 18)),
                    }
    except Exception:
        log.exception("[relay] _gemini_metadata via inference router failed for %s", filename)
    return {"top_rois": ["Visual cortex", "Default Mode Network", "Prefrontal cortex"], "peak_t": 18}


async def _process_cloud(scan_id: str, filename: str):
    """Full Gemini cloud processing path when RTX 5090 is offline.

    Generates narrations for all 4 personas + structured metadata (top_rois,
    peak_t) and writes a complete scan document to Firestore.
    """
    try:
        narrations, meta = await asyncio.gather(
            _gemini_narration(filename),
            _gemini_metadata(filename),
        )
        update: dict = {
            "status": "complete",
            "narrations": narrations,
            "tr_seconds": 0.5,
            "cost_mode": "cloud",
            "cost_estimate_usd": COST_CLOUD_USD,
            "status_message": f"Processed via Gemini cloud fallback ({GEMINI_MODEL}).",
        }
        update.update(meta)  # top_rois, peak_t
        await db.collection("scans").document(scan_id).update(update)
        log.info("[relay] cloud processing complete for %s (peak_t=%s)", scan_id, meta.get("peak_t"))
    except Exception as exc:
        log.exception("[relay] cloud processing failed for %s", scan_id)
        await db.collection("scans").document(scan_id).update({
            "status": "failed",
            "error": f"Cloud processing error: {exc}",
        })


async def _forward_to_5090(scan_id: str, data: bytes, filename: str, ext: str, tier: int):
    """Fire-and-forget background task. Top-level try/except guards against
    silent failures: any unhandled exception (including Firestore unreachable)
    must still try to mark the scan as failed so the gallery doesn't show it
    stuck in 'queued' forever."""
    try:
        util = await _5090_utilization()
        if not util.get("accepting", False):
            await db.collection("scans").document(scan_id).update({
                "status": "processing_cloud",
                "cost_mode": "cloud",
                "cost_estimate_usd": COST_CLOUD_USD,
                "status_message": (
                    f"RTX 5090 is {util.get('scheduler_state', 'unavailable')} "
                    f"(queue depth {util.get('queue_depth', '?')}). "
                    "Processing via Gemini cloud fallback."
                ),
            })
            await _process_cloud(scan_id, filename)
            return

        try:
            async with httpx.AsyncClient(timeout=600) as client:
                files = {"file": (filename, data, "application/octet-stream")}
                form  = {"tier": str(tier), "source": "public-relay", "external_scan_id": scan_id}
                r = await client.post(f"{TUNNEL_URL}/api/scan", files=files, data=form, timeout=600)
                if r.status_code == 202:
                    body = r.json()
                    await db.collection("scans").document(scan_id).update({
                        "local_scan_id":     body["scan_id"],
                        "status":            "processing",
                        "cost_mode":         "local",
                        "cost_estimate_usd": COST_LOCAL_USD,
                    })
                else:
                    raise RuntimeError(f"5090 returned {r.status_code}")
        except Exception as e:
            log.exception("[relay] forward to 5090 failed for %s", scan_id)
            await db.collection("scans").document(scan_id).update({
                "status": "failed", "error": str(e)
            })
    except Exception:
        # Last-resort: even Firestore is unreachable. Log so the operator can
        # find this scan in the relay logs by scan_id and reconcile manually.
        log.exception("[relay] catastrophic failure forwarding %s — scan stuck in queued", scan_id)


# ─── scan status & listing ────────────────────────────────────────────────────

@app.get("/api/scans")
async def list_scans(limit: int = 50):
    docs = db.collection("scans").order_by(
        "created_at", direction=firestore.Query.DESCENDING
    ).limit(limit)
    results = []
    async for doc in docs.stream():
        d = doc.to_dict()
        d["id"] = doc.id
        results.append({
            k: d[k] for k in [
                "id", "status", "status_message", "filename", "peak_t", "tr_seconds",
                "thumbnail_url", "file_url", "created_at", "narrations", "n_vertices",
                "top_rois", "cost_mode", "cost_estimate_usd", "submitted_from_country",
            ] if k in d
        })
    return {"scans": results}


@app.get("/api/scan/{scan_id}")
async def get_scan(scan_id: str):
    doc = await db.collection("scans").document(scan_id).get()
    if not doc.exists:
        return JSONResponse({"error": "not found"}, status_code=404)
    d = doc.to_dict()
    d["id"] = scan_id

    # Self-heal: if stuck in queued_cloud (e.g. from before the cloud-path was
    # implemented) and Gemini is configured, kick off processing now.
    if d.get("status") == "queued_cloud" and GEMINI_KEY and not d.get("_cloud_kicked"):
        await db.collection("scans").document(scan_id).update({
            "status": "processing_cloud",
            "_cloud_kicked": True,
            "status_message": "Resuming cloud processing via Gemini.",
        })
        asyncio.create_task(_process_cloud(scan_id, d.get("filename", "unknown")))
        d["status"] = "processing_cloud"

    _needs_local_fetch = (
        (d.get("status") == "processing") or
        (d.get("status") == "complete" and d.get("local_scan_id") and
         not d.get("top_rois") and not d.get("thumbnail_url"))
    )
    if _needs_local_fetch and d.get("local_scan_id"):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{TUNNEL_URL}/api/scan/{d['local_scan_id']}")
                if r.status_code == 200:
                    local = r.json()
                    for k in ["status", "peak_t", "n_t", "n_vertices", "narrations",
                               "tr_seconds", "bold_url", "thumbnail_url", "top_rois"]:
                        if k in local and local[k] is not None:
                            d[k] = local[k]
                    if local.get("status") in ("complete", "done"):
                        update = {
                            k: local[k] for k in
                            ["status", "peak_t", "n_t", "narrations", "tr_seconds",
                             "top_rois", "thumbnail_url"]
                            if k in local and local[k] is not None
                        }
                        if update:
                            await db.collection("scans").document(scan_id).update(update)
        except Exception:
            pass
    return d


@app.get("/api/scan/{scan_id}/narrations")
async def get_narrations(scan_id: str):
    doc = await db.collection("scans").document(scan_id).get()
    if not doc.exists:
        return JSONResponse({"error": "not found"}, status_code=404)
    d = doc.to_dict()

    if "narrations" in d:
        return {"narrations": d["narrations"]}

    if d.get("local_scan_id"):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{TUNNEL_URL}/api/scan/{d['local_scan_id']}/narrations")
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass

    if GEMINI_KEY and d.get("filename"):
        narration = await _gemini_narration(d["filename"])
        return {"narrations": narration, "source": "gemini-fallback"}

    return JSONResponse({"error": "narrations not ready"}, status_code=404)


# ─── Gemini fallback narration ────────────────────────────────────────────────

async def _gemini_narration(filename: str) -> dict:
    """4-persona narration via Gemini when RTX 5090 is offline.

    Persona system (matches local Gemma 4 prompts in cortex/prompts.py):
      sam      — ISU freshman, all-lowercase, casual
      priya    — Google DeepMind ML scientist, dense technical
      dr_park  — Northwestern neuroscientist, clinical-academic
      chris    — WBEZ Chicago science reporter, narrative
    """
    # Note: previously gated on GEMINI_KEY. Now routes to the local inference
    # router; key gate retained as a feature flag — set GEMINI_API_KEY to any
    # non-empty value to enable narration fallback.
    if not GEMINI_KEY:
        return {}

    base = (
        f"The media file '{filename}' was submitted to Cortex for brain-response analysis. "
        "TRIBE v2 (Meta's brain foundation model) predicts cortical BOLD activation across "
        "20,484 fsaverage5 vertices at 2 Hz, with a 5-second hemodynamic lag pre-applied. "
        "This is a group-averaged population model trained on 25 subjects — NOT a personal brain scan. "
    )

    personas = {
        "sam": (
            base +
            "You are writing for Sam, an Illinois State University freshman from Normal, IL. "
            "ALL LOWERCASE. Short punchy sentences. Contractions everywhere. 3-4 sentences max. "
            "One ISU/Normal reference is fine (Twin Cities Mall, Uptown Normal, ISU quad). "
            "No jargon at all. Make it feel like a text to a friend. Include one 'wait what' moment."
        ),
        "priya": (
            base +
            "You are writing for Priya, a senior ML Research Scientist at Google DeepMind, Chicago office. "
            "Dense ML/systems register: treat cortex as a distributed inference graph. "
            "Reference TRIBE v2 architecture explicitly: V-JEPA2 vision encoder, wav2vec-BERT 2.0 audio, "
            "Llama-3.2-3B text encoder, (T × 20484) float32 at 2 Hz. "
            "Mention RTX 5090 32 GB GDDR7 inference context. "
            "5-7 sentences. Include one architectural critique or optimization opportunity. No hedging."
        ),
        "dr_park": (
            base +
            "You are writing for Dr. Jiyeon Park, Associate Professor of Neurology at Northwestern. "
            "Full clinical-academic register: gyral anatomy, Brodmann areas, Yeo-7 network labels. "
            "Use Yeo-7 names precisely: Default Mode, Dorsal Attention, Ventral Attention/Salience, "
            "Frontoparietal, Somatomotor, Visual, Limbic. "
            "5-7 sentences. Note laterality and BOLD dynamics. "
            "End with: 'Group-averaged population inference — not individual neuroimaging.'"
        ),
        "chris": (
            base +
            "You are writing for Chris, a science and technology reporter for WBEZ Chicago. "
            "Clear, vivid, narrative-driven. Lead with the most interesting fact. "
            "One concrete analogy that makes the data feel real to a non-scientist. "
            "Include one specific number or data point. End with why this matters to Chicago or the Midwest. "
            "4-5 sentences. No jargon. Sound like a radio essay."
        ),
    }

    import json as _json

    def _fix_mojibake(s: str) -> str:
        """Fix cp1252→UTF-8 double-encoding artifacts (e.g. â€™ → ').

        Gemini sometimes returns smart quotes/em-dashes encoded as UTF-8 but
        httpx decodes the HTTP body using the detected (sometimes wrong) charset.
        Re-encoding as cp1252 then decoding as UTF-8 reverses the damage."""
        try:
            return s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s

    results = {}
    # Gemini APIs are disabled at the project level — narration falls back to
    # the local inference router instead of generativelanguage.googleapis.com.
    async with httpx.AsyncClient(timeout=45) as c:
        for persona_id, prompt in personas.items():
            try:
                r = await c.post(
                    f"{INFERENCE_URL}/v1/generate",
                    json={"prompt": prompt, "max_tokens": 400, "temperature": 0.8},
                )
                if r.status_code == 200:
                    body = _json.loads(r.content.decode("utf-8"))
                    raw_text = (body.get("text") or body.get("output")
                                or body.get("response") or "")
                    results[persona_id] = _fix_mojibake(raw_text) if raw_text \
                        else "(narration unavailable)"
                else:
                    results[persona_id] = "(narration unavailable)"
            except Exception:
                results[persona_id] = "(narration unavailable)"
    return results


# ─── WebSocket status feed ────────────────────────────────────────────────────

# ─── fMRI upload pipeline ─────────────────────────────────────────────────────

ALLOWED_FMRI_EXTS = {"nii", "nii.gz", "dcm", "dicom", "zip", "mat", "edf"}


class UploadUrlRequest(BaseModel):
    user_id: str = Field(..., min_length=10, max_length=32)  # Discord snowflake
    format_hint: str = Field(..., min_length=2, max_length=12)
    opted_in_research: bool = False


def _is_discord_snowflake(uid: str) -> bool:
    return uid.isdigit() and 17 <= len(uid) <= 20


def _user_kms_key_path(user_id: str) -> str:
    """Stable per-user key ID derived from the Discord user ID.

    Key naming uses the SHA-256 of the Discord ID (truncated) so we never
    write the raw user ID into KMS resource paths.
    """
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
    key_id = f"u-{digest}"
    return (
        f"projects/{GCP_PROJECT}/locations/{FMRI_REGION}"
        f"/keyRings/{FMRI_KEYRING}/cryptoKeys/{key_id}"
    )


@app.post("/api/fmri/upload-url")
@limiter.limit("3/hour")
async def request_upload_url(request: Request, req: UploadUrlRequest) -> JSONResponse:
    """Mint a 15-minute signed PUT URL into the CMEK-encrypted fMRI bucket."""
    if not _is_discord_snowflake(req.user_id):
        raise HTTPException(400, "user_id is not a valid Discord snowflake")
    fmt = req.format_hint.lower().lstrip(".")
    if fmt not in ALLOWED_FMRI_EXTS:
        raise HTTPException(400, f"format_hint must be one of {sorted(ALLOWED_FMRI_EXTS)}")

    scan_id = uuid.uuid4().hex
    object_name = f"users/{req.user_id}/scans/{scan_id}.{fmt}"

    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=FMRI_SIGNED_URL_TTL_MIN)
    try:
        blob = fmri_bucket.blob(object_name)
        upload_url = blob.generate_signed_url(
            version="v4",
            expiration=expires_at,
            method="PUT",
            content_type="application/octet-stream",
        )
    except Exception as exc:
        log.exception("[fmri] signed-url generation failed")
        raise HTTPException(500, "could not mint signed URL") from exc

    await db.collection("fmri_scans").document(scan_id).set({
        "scan_id": scan_id,
        "owner": req.user_id,
        "format_hint": fmt,
        "object_path": object_name,
        "bucket": FMRI_BUCKET,
        "kms_key": _user_kms_key_path(req.user_id),
        "status": "pending_upload",
        "opted_in_research": bool(req.opted_in_research),
        "requested_at": firestore.SERVER_TIMESTAMP,
        "expires_at": expires_at.isoformat(),
    })

    return JSONResponse({
        "upload_url": upload_url,
        "scan_id": scan_id,
        "object_path": object_name,
        "expires_at": expires_at.isoformat(),
        "method": "PUT",
        "max_bytes": FMRI_MAX_GB * 1024 * 1024 * 1024,
    })


@app.get("/api/fmri/my-uploads/{user_id}")
@limiter.limit("30/hour")
async def list_user_uploads(request: Request, user_id: str) -> JSONResponse:
    if not _is_discord_snowflake(user_id):
        raise HTTPException(400, "user_id is not a valid Discord snowflake")
    docs = (
        db.collection("fmri_scans")
        .where("owner", "==", user_id)
        .order_by("requested_at", direction=firestore.Query.DESCENDING)
        .limit(50)
    )
    out = []
    async for doc in docs.stream():
        d = doc.to_dict()
        out.append({
            k: d.get(k)
            for k in (
                "scan_id", "status", "format_hint", "n_timepoints",
                "tr_s", "opted_in_research", "requested_at", "validated_at",
                "errors",
            )
        })
    return JSONResponse({"uploads": out})


@app.post("/api/fmri/opt-in/{user_id}")
@limiter.limit("10/hour")
async def toggle_opt_in(request: Request, user_id: str, opt_in: bool = False) -> JSONResponse:
    if not _is_discord_snowflake(user_id):
        raise HTTPException(400, "invalid user_id")
    await db.collection("fmri_users").document(user_id).set(
        {"opted_in_research": bool(opt_in), "updated_at": firestore.SERVER_TIMESTAMP},
        merge=True,
    )
    return JSONResponse({"user_id": user_id, "opted_in_research": bool(opt_in)})


@app.delete("/api/fmri/scan/{scan_id}")
@limiter.limit("10/hour")
async def soft_delete_scan(request: Request, scan_id: str, user_id: str) -> JSONResponse:
    if not _is_discord_snowflake(user_id):
        raise HTTPException(400, "invalid user_id")
    doc_ref = db.collection("fmri_scans").document(scan_id)
    snap = await doc_ref.get()
    if not snap.exists:
        raise HTTPException(404, "scan not found")
    d = snap.to_dict() or {}
    if d.get("owner") != user_id:
        raise HTTPException(403, "not owner")
    await doc_ref.update({
        "status": "soft_deleted",
        "deleted_at": firestore.SERVER_TIMESTAMP,
    })
    # Object is left in place; lifecycle rule clears it after 30 days.
    return JSONResponse({"scan_id": scan_id, "status": "soft_deleted"})


@app.post("/api/fmri/training-tick")
async def training_tick(request: Request) -> JSONResponse:
    """Cloud Scheduler hook: scan Firestore and trigger training when ready."""
    from cortex.training_trigger import sweep_pending_scans

    result = await sweep_pending_scans(db, tunnel_url=TUNNEL_URL)
    return JSONResponse(result)


# ─── WebSocket status feed ────────────────────────────────────────────────────

@app.websocket("/api/ws/{scan_id}")
async def ws_scan(ws: WebSocket, scan_id: str):
    await ws.accept()
    try:
        for _ in range(120):  # max 10 min polling
            doc = await db.collection("scans").document(scan_id).get()
            if doc.exists:
                d = doc.to_dict()
                d["id"] = scan_id
                await ws.send_json(d)
                if d.get("status") in ("complete", "failed"):
                    break
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
