"""End-to-end Cortex scan: file in, narrations + brain screenshot out.

Defaults to the public Cortex endpoint so the result lands in the same
registry that backs the public gallery. Set CORTEX_API_BASE for local
development or Cloudflare-hosted deployments.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_CORTEX_BASE = os.environ.get(
    "CORTEX_API_BASE",
    "https://redteamkitchen.com",
)
SCREENSHOT_BASE = os.environ.get(
    "CORTEX_SCREENSHOT_BASE",
    DEFAULT_CORTEX_BASE,
)
DEFAULT_TIMEOUT_SEC = 600  # 10 min upper bound on a single scan
POLL_INTERVAL_SEC = 4


@dataclass
class ScanResult:
    scan_id: str
    status: str  # "complete" | "failed" | "timeout"
    filename: str
    top_rois: list[str]
    peak_t: int | None
    seconds_elapsed: float | None
    narrations: dict[str, str]              # {persona_id: text}
    gallery_url: str
    scan_url: str
    brain_screenshot_png_b64: str | None = None
    error: dict[str, Any] | None = None
    elapsed_sec: float = 0.0


class ScanError(RuntimeError):
    pass


async def submit_and_wait(
    file_path: str | Path,
    *,
    cortex_base: str = DEFAULT_CORTEX_BASE,
    tier: int = 2,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    capture_screenshot: bool = True,
    conversation_id: str = "default",
) -> ScanResult:
    """Submit a file to Cortex, poll until done, optionally screenshot the brain.

    Raises ScanError on connection failure or HTTP rejection at submission.
    Returns a ScanResult with status="failed" or "timeout" if the scan itself
    didn't complete cleanly.
    """
    file_path = Path(file_path)
    if not file_path.is_file():
        raise ScanError(f"file not found: {file_path}")

    t0 = time.time()
    cortex_base = cortex_base.rstrip("/")

    # 1. Submit
    log.info("[cortex_scan] submitting %s → %s", file_path.name, cortex_base)
    async with httpx.AsyncClient(timeout=30.0) as client:
        with file_path.open("rb") as fh:
            files = {"file": (file_path.name, fh, _guess_mime(file_path))}
            data = {"tier": str(tier), "source": "mercury"}
            r = await client.post(f"{cortex_base}/api/scan", files=files, data=data)
            if r.status_code >= 400:
                raise ScanError(f"submit rejected: HTTP {r.status_code} {r.text[:200]}")
            body = r.json()
    scan_id = body.get("scan_id")
    if not scan_id:
        raise ScanError(f"no scan_id in response: {body}")
    log.info("[cortex_scan] scan_id=%s", scan_id)

    # 2. Poll
    record: dict[str, Any] = {}
    deadline = t0 + timeout_sec
    async with httpx.AsyncClient(timeout=10.0) as client:
        while time.time() < deadline:
            try:
                r = await client.get(f"{cortex_base}/api/scan/{scan_id}")
                if r.status_code == 200:
                    record = r.json()
                    status = record.get("status", "?")
                    if status in ("complete", "failed"):
                        break
            except Exception as exc:
                log.warning("[cortex_scan] poll error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SEC)
        else:
            return ScanResult(
                scan_id=scan_id,
                status="timeout",
                filename=file_path.name,
                top_rois=[],
                peak_t=None,
                seconds_elapsed=None,
                narrations={},
                gallery_url=f"{cortex_base}/gallery.html",
                scan_url=f"{cortex_base}/?scan={scan_id}",
                error={"message": f"timeout after {timeout_sec}s"},
                elapsed_sec=time.time() - t0,
            )

    # 3. Optional screenshot via stealth browser
    screenshot_b64: str | None = None
    if capture_screenshot and record.get("status") == "complete":
        try:
            # Lazy import so the cortex_scan tool works even if the browser
            # subpackage / Patchright isn't installed.
            from mercury_skills.browser.tools import screenshot_url
            scan_url = f"{SCREENSHOT_BASE.rstrip('/')}/?scan={scan_id}"
            shot = await screenshot_url(
                scan_url,
                conversation_id=conversation_id,
                full_page=False,
                wait_for_selector="canvas",  # Three.js mounts a <canvas>
            )
            screenshot_b64 = shot.get("png_b64")
            log.info("[cortex_scan] screenshot captured (%d bytes)", shot.get("byte_size", 0))
        except Exception as exc:
            log.warning("[cortex_scan] screenshot failed: %s", exc)

    return ScanResult(
        scan_id=scan_id,
        status=record.get("status", "?"),
        filename=record.get("filename", file_path.name),
        top_rois=list(record.get("top_rois", [])),
        peak_t=record.get("peak_t"),
        seconds_elapsed=record.get("seconds_elapsed"),
        narrations=dict(record.get("narrations", {})),
        gallery_url=f"{cortex_base}/gallery.html",
        scan_url=f"{cortex_base}/?scan={scan_id}",
        brain_screenshot_png_b64=screenshot_b64,
        error=record.get("error"),
        elapsed_sec=time.time() - t0,
    )


def _guess_mime(p: Path) -> str:
    ext = p.suffix.lower()
    return {
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".txt": "text/plain", ".md": "text/markdown",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


PERSONA_META = {
    "student":      {"label": "🎓 Student",     "tagline": "ISU undergraduate, Normal IL",                "color": 0xCC0000},
    "patient":      {"label": "🏥 Patient",     "tagline": "Carle / BroMenn waiting room, Bloomington-Normal", "color": 0x1A73E8},
    "clinician":   {"label": "👨‍⚕️ Clinician",   "tagline": "Northwestern Memorial / RUSH neurologist, Chicago", "color": 0x4E2A84},
    "ml_scientist": {"label": "🧪 ML Scientist","tagline": "Senior research scientist, Chicago tech corridor", "color": 0x4285F4},
}


def format_for_discord(r: ScanResult) -> dict[str, Any]:
    """Render the result for Discord using rich embeds (no narration truncation).

    Discord embed limits we respect:
      - description: 4096 chars (we cap at 4000 to be safe)
      - one message can carry up to 10 embeds
      - total embed payload: 6000 chars

    A typical 4-narration result is ~1500-2500 chars total → fits in one message.
    """
    if r.status != "complete":
        if r.status == "failed":
            msg = (r.error or {}).get("message", "?") if r.error else "?"
            return {"text": f"❌ Scan `{r.scan_id[:8]}` failed: {msg}",
                    "image_b64": None, "embeds": []}
        return {"text": f"⏰ Scan `{r.scan_id[:8]}` timed out after {r.elapsed_sec:.0f}s",
                "image_b64": None, "embeds": []}

    rois = ", ".join(t.replace("7Networks_", "") for t in r.top_rois[:3]) or "(none)"
    header_text = (
        f"**Scan `{r.scan_id[:8]}` complete** — {r.filename}\n"
        f"Top regions: `{rois}` · peak t={r.peak_t} · {r.elapsed_sec:.0f}s end-to-end\n"
        f"<{r.gallery_url}> · <{r.scan_url}>"
    )

    embeds = []
    # Order: student → patient → clinician → ml_scientist for the same UX as the WebUI
    for key in ("student", "patient", "clinician", "ml_scientist"):
        text = r.narrations.get(key)
        if not text:
            continue
        meta = PERSONA_META.get(key, {"label": key.title(), "tagline": "", "color": 0x808080})
        # Cap at 4000 to stay under embed.description's 4096 limit
        snippet = text[:4000] + ("…" if len(text) > 4000 else "")
        embeds.append({
            "title":       meta["label"],
            "description": snippet,
            "color":       meta["color"],
            "footer":      {"text": meta["tagline"]},
        })
    return {
        "text":      header_text,
        "embeds":    embeds,
        "image_b64": r.brain_screenshot_png_b64,
    }
