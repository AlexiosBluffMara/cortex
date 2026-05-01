"""TRIBE v2 retraining-trigger logic.

Decides whether to kick off fine-tuning based on opted-in scan count and
whether the local 5090 is reachable. Falls back to a Vertex AI custom job
when the 5090 is busy or offline.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger("cortex.training_trigger")

TRAINING_THRESHOLD = int(os.environ.get("CORTEX_TRAINING_THRESHOLD", "50"))
LOCAL_VRAM_FLOOR_GB = float(os.environ.get("CORTEX_LOCAL_VRAM_FLOOR_GB", "24"))
UTIL_URL = os.environ.get(
    "CORTEX_UTIL_URL", "https://ollama.redteamkitchen.com/api/utilization"
)
LOCAL_TRAIN_URL = os.environ.get(
    "CORTEX_LOCAL_TRAIN_URL", "https://ollama.redteamkitchen.com/api/training/start"
)
VERTEX_REGION = os.environ.get("CORTEX_VERTEX_REGION", "us-central1")
VERTEX_PROJECT = os.environ.get("GCP_PROJECT", "abm-isu")


async def _local_utilization() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(UTIL_URL)
            if r.status_code == 200:
                return r.json()
    except Exception as exc:
        log.warning("[training] util fetch failed: %s", exc)
    return {"accepting": False, "scheduler_state": "unreachable"}


async def _trigger_local(scan_ids: list[str]) -> dict[str, Any]:
    payload = {"scan_ids": scan_ids, "mode": "fmri-fine-tune"}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(LOCAL_TRAIN_URL, json=payload)
            if r.status_code in (200, 202):
                body = r.json()
                return {"action": "local", "job_id": body.get("job_id", "unknown"),
                        "scans": len(scan_ids)}
    except Exception as exc:
        log.exception("[training] local trigger failed")
        return {"action": "error", "error": str(exc)}
    return {"action": "error", "error": "local relay rejected"}


def _trigger_vertex(scan_ids: list[str]) -> dict[str, Any]:
    """Submit a Vertex AI custom job. Stubbed: prints the spec and returns a mock id.

    Real submission happens via the google-cloud-aiplatform SDK once weights and
    training config are wired through.
    """
    job_name = f"tribe-v2-finetune-{len(scan_ids)}-{scan_ids[0][:6] if scan_ids else 'empty'}"
    log.info("[training] (stub) Vertex job=%s scans=%d region=%s",
             job_name, len(scan_ids), VERTEX_REGION)
    return {"action": "cloud", "job_id": job_name, "scans": len(scan_ids)}


async def check_and_trigger_training(
    scan_ids: list[str],
    *,
    threshold: int = TRAINING_THRESHOLD,
) -> dict[str, Any]:
    """Decide whether to fire TRIBE v2 retraining for the given scan batch."""
    if len(scan_ids) < threshold:
        return {"action": "wait", "count": len(scan_ids), "threshold": threshold}

    util = await _local_utilization()
    free_gb = float((util.get("vram") or {}).get("free_gb", 0))
    tribe_active = bool((util.get("vram") or {}).get("tribe_active", False))

    if (
        util.get("accepting", False)
        and not tribe_active
        and free_gb >= LOCAL_VRAM_FLOOR_GB
    ):
        return await _trigger_local(scan_ids)

    return _trigger_vertex(scan_ids)


async def sweep_pending_scans(db: Any, *, tunnel_url: str = "") -> dict[str, Any]:
    """Cloud Scheduler entry-point. Walks Firestore for un-trained opted-in
    validated scans, then calls check_and_trigger_training.
    """
    docs = (
        db.collection("fmri_scans")
        .where("status", "==", "validated")
        .where("opted_in_research", "==", True)
        .where("trained", "==", False)
        .limit(500)
    )
    scan_ids: list[str] = []
    async for doc in docs.stream():
        scan_ids.append(doc.id)

    if not scan_ids:
        return {"action": "wait", "count": 0, "threshold": TRAINING_THRESHOLD}

    result = await check_and_trigger_training(scan_ids)

    # Mark scans as queued so we don't re-submit on the next tick.
    if result.get("action") in ("local", "cloud"):
        for sid in scan_ids:
            await db.collection("fmri_scans").document(sid).update(
                {"trained": True, "training_job": result.get("job_id", "")}
            )
    return result


__all__ = [
    "TRAINING_THRESHOLD",
    "check_and_trigger_training",
    "sweep_pending_scans",
]
