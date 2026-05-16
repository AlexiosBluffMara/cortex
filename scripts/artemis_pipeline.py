"""artemis_pipeline.py — feed the Artemis-themed media set through Cortex
and build a topic database.

For each file in D:/cortex/data/artemis_inbox/:
  1. POST to {CORTEX}/api/scan (multipart)
  2. Poll {CORTEX}/api/scan/{id} until status == complete (or failed/timeout)
  3. Collect the 4-tier narrations + timings + modality

Outputs:
  D:/cortex/data/artemis_db.sqlite   (table `scans`)
  D:/cortex/data/artemis_db.json     (same data, human-readable)

The DB is keyed on ONE topic: NASA Artemis / lunar exploration.
"""
from __future__ import annotations

import json
import mimetypes
import sqlite3
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path

CORTEX = "http://localhost:8765"
INBOX = Path(r"D:/cortex/data/artemis_inbox")
DB_SQLITE = Path(r"D:/cortex/data/artemis_db.sqlite")
DB_JSON = Path(r"D:/cortex/data/artemis_db.json")
TOPIC = "NASA Artemis / lunar exploration"

MODALITY = {
    ".mp4": "video", ".mov": "video", ".webm": "video",
    ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".wav": "audio", ".mp3": "audio", ".m4a": "audio",
    ".txt": "text", ".md": "text",
}


def post_scan(path: Path) -> str | None:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    boundary = f"----rtk{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{CORTEX}/api/scan", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": "artemis-pipeline/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read())
        return d.get("scan_id") or d.get("id")
    except Exception as e:
        print(f"  POST failed for {path.name}: {e}")
        return None


def poll(scan_id: str, timeout_s: int = 900) -> dict:
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"{CORTEX}/api/scan/{scan_id}", timeout=10) as r:
                last = json.loads(r.read())
        except Exception:
            time.sleep(3)
            continue
        st = last.get("status")
        if st in ("complete", "failed", "error"):
            return last
        time.sleep(4)
    last["status"] = last.get("status") or "timeout"
    return last


def main() -> int:
    files = sorted(p for p in INBOX.iterdir()
                   if p.is_file() and not p.name.startswith("_"))
    print(f"Artemis pipeline — {len(files)} files, topic={TOPIC!r}")

    DB_SQLITE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_SQLITE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            scan_id TEXT PRIMARY KEY,
            topic TEXT, filename TEXT, modality TEXT, status TEXT,
            tier INTEGER, narration_model TEXT, size_mb REAL,
            seconds_elapsed REAL,
            narr_student TEXT, narr_patient TEXT,
            narr_clinician TEXT, narr_ml_scientist TEXT,
            top_rois TEXT, created_at TEXT, ingested_at TEXT
        )""")
    # Idempotent rebuild: the topic DB is fully derived from the fixed inbox,
    # so a re-run must yield exactly one row per file. PRIMARY KEY is scan_id
    # (unique per run), so without this clear, re-runs accumulate stale rows
    # (e.g. a pre-fix `failed` row lingering next to the new `complete` one).
    con.execute("DELETE FROM scans WHERE topic = ?", (TOPIC,))
    con.commit()

    rows = []
    for f in files:
        modality = MODALITY.get(f.suffix.lower(), "unknown")
        print(f"\n[{modality:5s}] {f.name}  ({f.stat().st_size//1024} KB)")
        sid = post_scan(f)
        if not sid:
            continue
        print(f"  scan_id={sid} — polling...")
        res = poll(sid)
        narr = res.get("narrations") or {}
        print(f"  status={res.get('status')} tiers={list(narr.keys())} "
              f"elapsed={res.get('seconds_elapsed')}")
        row = {
            "scan_id": sid, "topic": TOPIC, "filename": f.name,
            "modality": modality, "status": res.get("status"),
            "tier": res.get("tier"),
            "narration_model": res.get("narration_model"),
            "size_mb": res.get("size_mb"),
            "seconds_elapsed": res.get("seconds_elapsed"),
            "narr_student": narr.get("student", ""),
            "narr_patient": narr.get("patient", ""),
            "narr_clinician": narr.get("clinician", ""),
            "narr_ml_scientist": narr.get("ml_scientist", ""),
            "top_rois": json.dumps(res.get("top_rois") or []),
            "created_at": res.get("created_at"),
            "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        con.execute(
            "INSERT OR REPLACE INTO scans VALUES "
            "(:scan_id,:topic,:filename,:modality,:status,:tier,:narration_model,"
            ":size_mb,:seconds_elapsed,:narr_student,:narr_patient,:narr_clinician,"
            ":narr_ml_scientist,:top_rois,:created_at,:ingested_at)", row)
        con.commit()
        rows.append(row)

    con.close()
    DB_JSON.write_text(json.dumps({
        "topic": TOPIC,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(rows),
        "by_modality": {m: sum(1 for r in rows if r["modality"] == m)
                        for m in {r["modality"] for r in rows}},
        "scans": rows,
    }, indent=2), encoding="utf-8")

    print(f"\n=== DONE — {len(rows)} scans in the Artemis DB ===")
    print(f"  sqlite: {DB_SQLITE}")
    print(f"  json:   {DB_JSON}")
    for r in rows:
        ok = "OK" if r["status"] == "complete" else r["status"]
        print(f"  [{ok:8s}] {r['modality']:5s} {r['filename']:35s} "
              f"{len([k for k in ('narr_student','narr_patient','narr_clinician','narr_ml_scientist') if r[k]])}/4 tiers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
