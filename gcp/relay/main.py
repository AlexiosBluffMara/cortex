"""Cortex Cloud Run relay — routes public scan requests to the local 5090."""
from __future__ import annotations
import asyncio, os, uuid, json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.cloud import firestore, storage

TUNNEL_URL  = os.environ["TUNNEL_URL"]       # e.g. https://abc.trycloudflare.com
GCS_BUCKET  = os.environ["GCS_BUCKET"]       # cortex-public-scans
GCP_PROJECT = os.environ["GCP_PROJECT"]      # abm-isu
MAX_MB      = 50

app = FastAPI(title="Cortex Relay")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

db  = firestore.AsyncClient(project=GCP_PROJECT)
gcs = storage.Client(project=GCP_PROJECT)
bucket = gcs.bucket(GCS_BUCKET)

@app.get("/api/health")
async def health():
    return {"ok": True, "tunnel": TUNNEL_URL}

@app.get("/api/scans")
async def list_scans(limit: int = 50):
    docs = db.collection("scans").order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)
    results = []
    async for doc in docs.stream():
        d = doc.to_dict()
        d["id"] = doc.id
        results.append({k: d[k] for k in ["id","status","filename","peak_t","tr_seconds","thumbnail_url","created_at","narrations"] if k in d})
    return {"scans": results}

@app.post("/api/scan")
async def submit_scan(file: UploadFile = File(...), tier: int = Form(default=4)):
    scan_id = uuid.uuid4().hex[:12]
    data = await file.read()
    if len(data) > MAX_MB * 1024 * 1024:
        return JSONResponse({"error": "file too large"}, status_code=413)

    ext = Path(file.filename).suffix.lower()
    blob = bucket.blob(f"uploads/{scan_id}{ext}")
    blob.upload_from_string(data, content_type=file.content_type or "application/octet-stream")
    upload_url = f"https://storage.googleapis.com/{GCS_BUCKET}/uploads/{scan_id}{ext}"

    await db.collection("scans").document(scan_id).set({
        "id": scan_id, "status": "queued", "filename": file.filename,
        "tier": tier, "created_at": firestore.SERVER_TIMESTAMP, "upload_url": upload_url,
    })

    asyncio.create_task(_forward_to_5090(scan_id, data, file.filename, ext, tier))
    return JSONResponse({"ok": True, "scan_id": scan_id}, status_code=202)

async def _forward_to_5090(scan_id: str, data: bytes, filename: str, ext: str, tier: int):
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            files = {"file": (filename, data, "application/octet-stream")}
            form  = {"tier": str(tier), "source": "public-relay", "external_scan_id": scan_id}
            r = await client.post(f"{TUNNEL_URL}/api/scan", files=files, data=form)
            if r.status_code == 202:
                body = r.json()
                await db.collection("scans").document(scan_id).update({"local_scan_id": body["scan_id"]})
    except Exception as e:
        await db.collection("scans").document(scan_id).update({"status": "failed", "error": str(e)})

@app.get("/api/scan/{scan_id}")
async def get_scan(scan_id: str):
    doc = await db.collection("scans").document(scan_id).get()
    if not doc.exists:
        return JSONResponse({"error": "not found"}, status_code=404)
    d = doc.to_dict(); d["id"] = scan_id
    return d

@app.websocket("/api/ws/{scan_id}")
async def ws_scan(ws: WebSocket, scan_id: str):
    await ws.accept()
    try:
        for _ in range(120):  # max 10 min poll
            doc = await db.collection("scans").document(scan_id).get()
            if doc.exists:
                d = doc.to_dict(); d["id"] = scan_id
                await ws.send_json(d)
                if d.get("status") in ("complete", "failed"):
                    break
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
