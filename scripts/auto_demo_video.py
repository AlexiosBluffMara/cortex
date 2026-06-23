"""auto_demo_video.py - record + caption + edit a Cortex demo end-to-end.

Pipeline:
  1. Launches an ffmpeg gdigrab capture of the PRIMARY monitor only.
  2. Drives the WebUI via system Chrome (CDP) for both sample files in
     D:/cortex/assets/, going through:
        a. Open the public demo URL.
        b. Drag/drop the file into the upload box (programmatic, via DOM event).
        c. Wait for status -> running -> narrating -> complete.
        d. Show the gallery, hover the new scan card, open it.
  3. After each WebUI run, posts the result to a Discord channel via webhook
     (since the bot can't act on its own posts), then tabs over to the
     Discord PWA so the user sees the embed land.
  4. Stops ffmpeg, captures a JSON timeline of EVERY event with
     (t_start, t_end, kind, text, region) entries.
  5. Re-encodes the raw capture with overlays:
        - drawbox highlights around the active UI region per event
        - drawtext caption under the highlight
        - waiting periods get a 4x speedup
  6. Outputs a single coherent MP4 in D:/cortex/demo_videos/.

This script intentionally keeps everything in one file so it's auditable.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
ASSETS = REPO / "assets"
OUT_DIR = REPO / "demo_videos"
OUT_DIR.mkdir(exist_ok=True)

# Primary monitor (DISPLAY2 in our setup) — verified via Add-Type Forms.Screen
MON_X, MON_Y = 0, 0
MON_W, MON_H = 3840, 1600
FPS = 30

WEBUI_URL = os.environ.get("WEBUI_URL", "https://redteamkitchen.com")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_DEMO_CHANNEL_ID", "1498659332512940032")
DISCORD_CHANNEL_URL = os.environ.get(
    "DISCORD_CHANNEL_URL",
    f"https://discord.com/channels/1164231468378239017/{DISCORD_CHANNEL_ID}",
)

SAMPLE_FILES = [
    ASSETS / "demo_clip_20s.mp4",
    ASSETS / "nasa_artemis_15s_silent.mp4",
]
for p in SAMPLE_FILES:
    if not p.is_file():
        raise SystemExit(f"sample missing: {p}")

ts0 = time.time()
TIMELINE: list[dict] = []


def event(kind: str, text: str, region: tuple[int, int, int, int] | None = None,
          duration: float = 4.0) -> None:
    """Record a timeline event relative to recording start."""
    t = time.time() - ts0
    TIMELINE.append({
        "t":      round(t, 2),
        "end":    round(t + duration, 2),
        "kind":   kind,            # "highlight" | "speedup" | "title" | "result"
        "text":   text,
        "region": region,
    })
    print(f"[{t:7.1f}s] {kind:10s} {text}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. ffmpeg recording
# ─────────────────────────────────────────────────────────────────────────────

def start_recording(out_path: Path) -> subprocess.Popen:
    cmd = [
        "ffmpeg", "-y",
        "-f", "gdigrab",
        "-framerate", str(FPS),
        "-offset_x", str(MON_X),
        "-offset_y", str(MON_Y),
        "-video_size", f"{MON_W}x{MON_H}",
        "-i", "desktop",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-crf", "23",
        str(out_path),
    ]
    print("[ffmpeg]", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)  # let the encoder warm up
    return proc


def stop_recording(proc: subprocess.Popen) -> None:
    try:
        proc.communicate(input=b"q", timeout=10)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=5)


# ─────────────────────────────────────────────────────────────────────────────
# 2. WebUI drive via Patchright/system Chrome over CDP
# ─────────────────────────────────────────────────────────────────────────────

async def drive_webui_for(file_path: Path, label: str) -> dict[str, Any]:
    """Open WebUI, upload file, wait for completion. Returns scan record."""
    sys.path.insert(0, str(REPO))
    from mercury_skills.browser.session import get_or_create_session, close_session
    # Force a fresh browser per sample so stale CDP ports don't bite us
    session_id = f"demo-{label}-{int(time.time())}"
    await close_session(session_id)
    sess = await get_or_create_session(session_id)
    page = await sess.goto(WEBUI_URL, wait_until="domcontentloaded", timeout=45.0)
    event("title", f"Cortex WebUI - sample {label}", duration=3)
    await asyncio.sleep(2)

    # Snapshot existing scan_ids so we can identify the NEW one after upload
    import httpx
    pre_ids: set[str] = set()
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{WEBUI_URL}/api/scans?limit=50&status=all")
            if r.status_code == 200:
                pre_ids = {s["id"] for s in r.json().get("scans", [])}
        except Exception:
            pass
    print(f"[demo] pre-upload scan_id baseline: {len(pre_ids)} ids")

    # Wait for the file input to mount, then setInputFiles
    event("highlight", f"Drop {file_path.name} into the upload pane",
          region=(80, 800, 800, 1200), duration=4)
    file_input = await page.wait_for_selector("#scan-file", timeout=15_000)
    await file_input.set_input_files(str(file_path))
    await asyncio.sleep(1.5)
    # Trigger change event explicitly in case Patchright already did
    await page.evaluate(
        "document.getElementById('scan-file').dispatchEvent(new Event('change', {bubbles:true}))"
    )
    await asyncio.sleep(1.0)
    submit_btn = await page.wait_for_selector("#scan-submit:not([disabled])", timeout=10_000)
    await submit_btn.click()
    await asyncio.sleep(1.5)

    # Find scan_id from the rendered status
    event("highlight", "TRIBE backend submits scan -> Seratonin RTX 5090",
          region=(800, 200, 2400, 800), duration=4)

    # Poll scan_id — pick the FIRST id we haven't seen before with our filename
    scan_id = None
    deadline = time.time() + 60
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.time() < deadline:
            r = await client.get(f"{WEBUI_URL}/api/scans?limit=50&status=all")
            if r.status_code == 200:
                rows = r.json().get("scans", [])
                for row in rows:
                    if row["id"] in pre_ids:
                        continue
                    if row.get("filename") == file_path.name:
                        scan_id = row["id"]
                        break
            if scan_id:
                break
            await asyncio.sleep(1.5)
    if not scan_id:
        raise RuntimeError(f"never saw scan for {file_path.name}")
    print(f"[demo] scan_id={scan_id}")

    # Now wait for completion, narrating periodic events
    event("speedup", "TRIBE inference + 4 persona narrations (RTX 5090)",
          region=(800, 800, 3200, 1500), duration=8)
    last_status = None
    async with httpx.AsyncClient(timeout=5.0) as client:
        deadline = time.time() + 600
        while time.time() < deadline:
            r = await client.get(f"{WEBUI_URL}/api/scan/{scan_id}")
            if r.status_code == 200:
                d = r.json()
                if d.get("status") != last_status:
                    last_status = d.get("status")
                    event("highlight", f"status: {last_status}",
                          region=(2400, 80, 3700, 200), duration=2)
                if last_status in ("complete", "failed"):
                    break
            await asyncio.sleep(3)
        else:
            raise RuntimeError("scan timed out")

    event("highlight", "All four narrations rendered",
          region=(2200, 200, 3800, 1500), duration=5)
    await asyncio.sleep(4)

    # Hop to gallery (Playwright timeout is in milliseconds!)
    await page.goto(f"{WEBUI_URL}/gallery.html", wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(2)
    event("highlight", "Gallery shows newest scan with 3D brain + persona embeds",
          region=(80, 200, 3800, 1500), duration=6)
    await asyncio.sleep(4)

    # Pull final record
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{WEBUI_URL}/api/scan/{scan_id}")
        record = r.json()
    return record


# ─────────────────────────────────────────────────────────────────────────────
# 3. Discord post via webhook (so the channel shows the embed land live)
# ─────────────────────────────────────────────────────────────────────────────

async def post_to_discord(record: dict[str, Any], file_label: str) -> None:
    if not DISCORD_BOT_TOKEN:
        event("title", "Discord step skipped (no DISCORD_BOT_TOKEN env)", duration=3)
        return
    sys.path.insert(0, str(REPO))
    from mercury_skills.cortex_scan.scan import format_for_discord, ScanResult

    # Reconstruct a ScanResult-like object for format_for_discord
    sr = ScanResult(
        scan_id=record["id"],
        status=record.get("status", "?"),
        filename=record.get("filename", file_label),
        top_rois=record.get("top_rois", []),
        peak_t=record.get("peak_t"),
        seconds_elapsed=record.get("seconds_elapsed"),
        narrations=record.get("narrations", {}),
        gallery_url=f"{WEBUI_URL}/gallery.html",
        scan_url=f"{WEBUI_URL}/?scan={record['id']}",
        elapsed_sec=record.get("seconds_elapsed", 0),
    )
    payload = format_for_discord(sr)
    body = {
        "content": payload["text"][:2000],  # discord message cap
        "embeds": payload.get("embeds") or [],
    }

    import httpx
    event("title", f"Posting {file_label} narrations to Discord", duration=3)
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
            json=body,
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
        )
        print(f"[discord] bot post status={r.status_code} body={r.text[:200]}")

    # Open the Discord channel in browser so we see it land
    from mercury_skills.browser.session import get_or_create_session, close_session
    session_id = f"demo-discord-{file_label}-{int(time.time())}"
    await close_session(session_id)
    sess = await get_or_create_session(session_id)
    try:
        await sess.goto(DISCORD_CHANNEL_URL, wait_until="domcontentloaded", timeout=45.0)
    except Exception as exc:
        print(f"[discord] could not open channel page: {exc}")
    await asyncio.sleep(5)
    event("highlight", "Discord channel: 4 persona embeds posted by Snowy",
          region=(800, 200, 3200, 1500), duration=7)
    await asyncio.sleep(5)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Post-processing with moviepy
# ─────────────────────────────────────────────────────────────────────────────

def post_process(raw_path: Path, timeline: list[dict], out_path: Path) -> None:
    """Render the raw capture with overlays + speedups."""
    from moviepy import (VideoFileClip, ColorClip, CompositeVideoClip,
                          TextClip, vfx, concatenate_videoclips)

    print(f"[edit] reading {raw_path}")
    clip = VideoFileClip(str(raw_path))
    duration = clip.duration

    # Determine speedup segments — kind=="speedup" gets 4x, everything else stays 1x
    # Build a sorted list of (t0, t1, factor)
    segments: list[tuple[float, float, float]] = []
    cursor = 0.0
    speedups = sorted([e for e in timeline if e["kind"] == "speedup"], key=lambda e: e["t"])
    for ev in speedups:
        t0 = ev["t"]
        t1 = min(ev["end"], duration)
        if t0 > cursor:
            segments.append((cursor, t0, 1.0))
        if t1 > t0:
            segments.append((t0, t1, 4.0))
        cursor = t1
    if cursor < duration:
        segments.append((cursor, duration, 1.0))

    sub_clips = []
    for t0, t1, factor in segments:
        sub = clip.subclipped(t0, t1)
        if factor != 1.0:
            sub = sub.with_effects([vfx.MultiplySpeed(factor=factor)])
        sub_clips.append(sub)
    base = concatenate_videoclips(sub_clips)

    # Now we need a mapping from original-timeline t -> new-timeline t after speedups
    def remap(orig_t: float) -> float:
        """Map an original timestamp into the post-speedup timeline."""
        new_t = 0.0
        for t0, t1, factor in segments:
            if orig_t <= t0:
                return new_t
            if orig_t < t1:
                return new_t + (orig_t - t0) / factor
            new_t += (t1 - t0) / factor
        return new_t

    overlays = [base]
    font_path = r"C:\Windows\Fonts\segoeui.ttf"
    if not Path(font_path).is_file():
        font_path = None
    for ev in timeline:
        if ev["kind"] not in ("highlight", "title", "result"):
            continue
        new_start = remap(ev["t"])
        new_end   = remap(ev["end"])
        if new_end - new_start < 0.4:
            continue

        # Highlight box (translucent rectangle outline)
        if ev.get("region") and ev["kind"] == "highlight":
            x1, y1, x2, y2 = ev["region"]
            box = (ColorClip(size=(x2 - x1, y2 - y1), color=(255, 215, 0))
                   .with_opacity(0.10)
                   .with_position((x1, y1))
                   .with_start(new_start)
                   .with_duration(new_end - new_start))
            overlays.append(box)

        # Caption
        text = ev["text"][:120]
        if ev["kind"] == "title":
            tc = TextClip(text=text, font=font_path, font_size=72,
                          color="white", stroke_color="black", stroke_width=2,
                          bg_color=(0, 0, 0, 200), margin=(40, 20))
            tc = tc.with_position(("center", 80)).with_start(new_start).with_duration(new_end - new_start)
        else:
            tc = TextClip(text=text, font=font_path, font_size=44,
                          color="white", stroke_color="black", stroke_width=2,
                          bg_color=(0, 0, 0, 180), margin=(20, 10))
            # Place caption below highlight if region known, else bottom
            if ev.get("region"):
                _, _, _, y2 = ev["region"]
                tc = tc.with_position(("center", min(y2 + 20, MON_H - 120)))
            else:
                tc = tc.with_position(("center", MON_H - 120))
            tc = tc.with_start(new_start).with_duration(new_end - new_start)
        overlays.append(tc)

    final = CompositeVideoClip(overlays, size=clip.size)
    print(f"[edit] writing {out_path} ({final.duration:.1f}s, was {duration:.1f}s)")
    final.write_videofile(
        str(out_path),
        codec="libx264",
        audio=False,
        fps=FPS,
        preset="fast",
        threads=8,
    )
    clip.close()
    final.close()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    raw_path   = OUT_DIR / f"raw_{int(time.time())}.mp4"
    final_path = OUT_DIR / f"cortex_demo_{int(time.time())}.mp4"
    timeline_path = OUT_DIR / f"timeline_{int(time.time())}.json"

    proc = start_recording(raw_path)
    global ts0
    ts0 = time.time()  # reset clock once recording is rolling
    event("title", "Cortex multimodal brain-response demo", duration=4)
    try:
        for sample in SAMPLE_FILES:
            label = sample.stem
            print(f"\n=== Demo run: {sample.name} ===")
            try:
                record = await drive_webui_for(sample, label)
            except Exception as exc:
                print(f"[demo] WebUI run for {sample.name} failed: {exc}")
                event("title", f"WebUI run for {sample.name} hit an error", duration=4)
                continue
            try:
                await post_to_discord(record, label)
            except Exception as exc:
                print(f"[demo] Discord post for {sample.name} failed: {exc}")
                event("title", f"Discord post for {sample.name} hit an error", duration=3)
        event("title", "Demo complete - https://redteamkitchen.com", duration=4)
    finally:
        stop_recording(proc)
        print(f"[done] raw at {raw_path}")
        timeline_path.write_text(json.dumps(TIMELINE, indent=2))
        print(f"[done] timeline at {timeline_path}")

    print("\n=== Editing (fast path: ffmpeg + NVENC) ===")
    edit_script = REPO / "scripts" / "edit_demo_video.py"
    edit_cmd = [sys.executable, str(edit_script), str(raw_path), str(timeline_path)]
    print("[edit]", " ".join(edit_cmd))
    rc = subprocess.run(edit_cmd, cwd=str(REPO)).returncode
    if rc == 0:
        # edit_demo_video writes final_<ts>.mp4 next to raw
        produced = raw_path.with_name(f"final_{raw_path.stem.split('_')[-1]}.mp4")
        if produced.is_file():
            shutil.copy(str(produced), str(final_path))
            print(f"[done] final at {final_path}")
        else:
            print(f"[edit] expected {produced} not found; falling back to moviepy")
            try:
                post_process(raw_path, TIMELINE, final_path)
            except Exception as exc:
                print(f"[edit] moviepy fallback failed: {exc}")
                shutil.copy(str(raw_path), str(final_path))
    else:
        print(f"[edit] ffmpeg edit returned rc={rc}; falling back to moviepy")
        try:
            post_process(raw_path, TIMELINE, final_path)
        except Exception as exc:
            print(f"[edit] moviepy fallback failed: {exc}")
            shutil.copy(str(raw_path), str(final_path))
    print(f"\n[done] final at {final_path}")


if __name__ == "__main__":
    asyncio.run(main())
