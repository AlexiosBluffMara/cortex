"""GCS finalize -> Pub/Sub -> validate fMRI uploads.

Subscriber on `projects/abm-isu/topics/cortex-fmri-finalized`. For every
finalized object under `users/*/scans/*`:

  1. Stream the first ~16 MB header (or the full body if small).
  2. Run cortex.fmri_validator.validate.
  3. Update Firestore fmri_scans/{scan_id} with status + metadata.
  4. If valid + opted_in_research -> mark trained=False so the scheduler picks it up.
  5. If rejected, fire a Discord webhook to the user via Mercury.

Env:
  GCP_PROJECT, MERCURY_DM_WEBHOOK (optional), HEADER_BYTES (default 16 MB).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import PurePosixPath

import functions_framework
import httpx
from cloudevents.http import CloudEvent
from google.cloud import firestore, storage

from cortex.fmri_validator import MAX_BYTES, validate

log = logging.getLogger("fmri_validate")
logging.basicConfig(level=logging.INFO)

GCP_PROJECT = os.environ["GCP_PROJECT"]
MERCURY_DM_WEBHOOK = os.environ.get("MERCURY_DM_WEBHOOK", "")
HEADER_BYTES = int(os.environ.get("HEADER_BYTES", str(16 * 1024 * 1024)))

db = firestore.Client(project=GCP_PROJECT)
gcs = storage.Client(project=GCP_PROJECT)


def _scan_id_from_path(object_name: str) -> tuple[str, str, str]:
    """`users/<discord_id>/scans/<scan_id>.<ext>` -> (discord_id, scan_id, ext)."""
    p = PurePosixPath(object_name)
    parts = p.parts
    if len(parts) < 4 or parts[0] != "users" or parts[2] != "scans":
        return "", "", ""
    discord_id = parts[1]
    name = parts[3]
    if name.endswith(".nii.gz"):
        scan_id, ext = name[:-7], "nii.gz"
    else:
        stem, _, ext = name.rpartition(".")
        scan_id = stem
    return discord_id, scan_id, ext


def _notify_discord(user_id: str, scan_id: str, errors: list[str]) -> None:
    if not MERCURY_DM_WEBHOOK:
        log.info("[fmri] no MERCURY_DM_WEBHOOK; skipping rejection DM for %s", scan_id)
        return
    payload = {
        "user_id": user_id,
        "scan_id": scan_id,
        "kind": "fmri_rejected",
        # Errors are validator-generated strings that never include scan content.
        "errors": errors,
    }
    try:
        httpx.post(MERCURY_DM_WEBHOOK, json=payload, timeout=10)
    except Exception as exc:
        log.warning("[fmri] mercury notify failed: %s", exc)


@functions_framework.cloud_event
def on_finalize(event: CloudEvent) -> None:
    """Pub/Sub trigger. Body: a GCS OBJECT_FINALIZE notification."""
    raw = event.data
    if isinstance(raw, dict) and "message" in raw:
        # CloudEvents wraps Pub/Sub messages
        encoded = raw["message"].get("data", "")
        body = json.loads(base64.b64decode(encoded).decode("utf-8")) if encoded else {}
    else:
        body = raw if isinstance(raw, dict) else {}

    bucket_name = body.get("bucket")
    object_name = body.get("name", "")
    size = int(body.get("size", "0"))

    if not bucket_name or not object_name:
        log.warning("[fmri] missing bucket/name in event")
        return

    discord_id, scan_id, ext = _scan_id_from_path(object_name)
    if not scan_id:
        log.info("[fmri] ignoring non-scan object: %s", object_name)
        return

    log.info("[fmri] validating scan_id=%s size=%d ext=%s", scan_id, size, ext)

    if size > MAX_BYTES:
        _update(scan_id, status="rejected", errors=["file exceeds 2 GB cap"])
        _notify_discord(discord_id, scan_id, ["file exceeds 2 GB cap"])
        return

    blob = gcs.bucket(bucket_name).blob(object_name)
    # Stream just the header for huge files; full body for small ones.
    n_to_read = min(size, HEADER_BYTES)
    try:
        data = blob.download_as_bytes(start=0, end=n_to_read - 1) if n_to_read else b""
    except Exception as exc:
        log.exception("[fmri] download failed for %s", object_name)
        _update(scan_id, status="rejected", errors=[f"download failed: {type(exc).__name__}"])
        return

    result = validate(data, format_hint=_ext_to_format(ext), filename=object_name)

    update = {
        "status": "validated" if result.ok else "rejected",
        "format": result.format,
        "dimensions": result.dimensions,
        "tr_s": result.tr_s,
        "n_timepoints": result.n_timepoints,
        "anonymized": result.anonymized,
        "errors": result.errors,
        "object_size": size,
        "validated_at": firestore.SERVER_TIMESTAMP,
        "trained": False,
    }
    _update(scan_id, **update)

    if not result.ok:
        _notify_discord(discord_id, scan_id, result.errors)


def _ext_to_format(ext: str) -> str:
    e = ext.lower()
    if e in ("nii", "nii.gz"):
        return "nifti"
    if e in ("dcm", "dicom"):
        return "dicom"
    if e == "zip":
        return "bids"
    if e == "mat":
        return "matlab"
    if e == "edf":
        return "edf"
    return ""


def _update(scan_id: str, **fields: object) -> None:
    db.collection("fmri_scans").document(scan_id).set(fields, merge=True)
