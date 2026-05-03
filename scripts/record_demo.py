"""record_demo.py — automated end-to-end + gallery showcase recording via Patchright.

Two modes:
  --mode end_to_end   Long video: open demo, upload a clip, watch through queued/
                      narrating/complete, scrub timeline, play the brain tour,
                      open each persona narration. Saves <out_dir>/end_to_end.webm
  --mode gallery      Short video: open /gallery.html, scroll through the cards,
                      hover one to expand. Saves <out_dir>/gallery.webm

Both videos are unedited captures from a real headed Chromium driven by Patchright.
After recording, run scripts/caption_video.sh (ffmpeg drawtext) to overlay captions.

Usage:
  python scripts/record_demo.py --mode end_to_end --clip D:/cortex/assets/nasa_artemis_15s_silent.mp4
  python scripts/record_demo.py --mode gallery
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Optional

from patchright.async_api import async_playwright

DEMO_URL = os.environ.get("CORTEX_DEMO_URL", "https://big-apple.scylla-betta.ts.net")
OUT_DIR = Path(os.environ.get("RECORD_OUT", str(Path(__file__).resolve().parent.parent / "scans" / "recordings")))
OUT_DIR.mkdir(parents=True, exist_ok=True)


async def record_end_to_end(clip_path: str, headless: bool = False, max_wait_sec: int = 360) -> str:
    """Drive the live demo through a full scan and capture the whole session."""
    clip = Path(clip_path)
    assert clip.is_file(), f"clip not found: {clip}"

    out_video_dir = OUT_DIR / f"end_to_end_{int(time.time())}"
    out_video_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=["--window-size=1440,900"])
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(out_video_dir),
            record_video_size={"width": 1440, "height": 900},
        )
        page = await ctx.new_page()

        print(f"[record] opening {DEMO_URL}")
        await page.goto(DEMO_URL, wait_until="networkidle", timeout=60_000)
        await page.wait_for_selector("canvas", timeout=20_000)
        await asyncio.sleep(2)

        print(f"[record] uploading {clip.name}")
        # Drop-zone uses <input type=file id=scan-file>
        file_input = await page.wait_for_selector("input#scan-file", timeout=10_000)
        await file_input.set_input_files(str(clip))
        await asyncio.sleep(1)
        # Click Analyze
        analyze = await page.wait_for_selector("button#scan-submit", timeout=5_000)
        await analyze.click()
        print("[record] scan submitted")

        # Wait for the scan to flow through queued → running → narrating → complete
        # We watch the GPU badge or the overlay text to decide when to stop.
        deadline = time.time() + max_wait_sec
        last_phase = ""
        while time.time() < deadline:
            try:
                gpu_badge = await page.text_content("#gpu-badge")
                phase = (gpu_badge or "").strip()
            except Exception:
                phase = "?"
            if phase != last_phase:
                print(f"[record] t={int(time.time())}: GPU={phase}")
                last_phase = phase
            # Stop when the narration tabs have content
            try:
                first_narr = await page.text_content("#narration-student")
                if first_narr and len(first_narr.strip()) > 80:
                    print("[record] first narration visible")
                    break
            except Exception:
                pass
            await asyncio.sleep(2)

        print("[record] scrubbing timeline")
        # Walk the timeline a few times for visual interest
        timeline = await page.query_selector("#timeline")
        if timeline:
            box = await timeline.bounding_box()
            if box:
                for frac in (0.1, 0.4, 0.7, 0.5, 0.9, 0.0):
                    await page.mouse.move(box["x"] + box["width"] * frac, box["y"] + box["height"] / 2)
                    await page.mouse.down()
                    await asyncio.sleep(0.4)
                    await page.mouse.up()
                    await asyncio.sleep(0.6)

        print("[record] clicking through persona tabs")
        for persona in ("patient", "clinician", "ml_scientist", "student"):
            tab = await page.query_selector(f'button.narr-tab[data-narr="{persona}"]')
            if tab:
                await tab.click()
                await asyncio.sleep(2.5)

        print("[record] starting tour mode")
        tour_btn = await page.query_selector("#tour-btn")
        if tour_btn:
            await tour_btn.click()
            await asyncio.sleep(10)
            await tour_btn.click()  # stop

        print("[record] done — closing context to flush video")
        await ctx.close()
        await browser.close()

        # Patchright writes one .webm per page in the record_video_dir
        webms = sorted(out_video_dir.glob("*.webm"))
        if not webms:
            raise RuntimeError(f"no video produced in {out_video_dir}")
        print(f"[record] saved: {webms[-1]}")
        return str(webms[-1])


async def record_gallery(headless: bool = False) -> str:
    out_video_dir = OUT_DIR / f"gallery_{int(time.time())}"
    out_video_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=["--window-size=1440,900"])
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(out_video_dir),
            record_video_size={"width": 1440, "height": 900},
        )
        page = await ctx.new_page()

        print(f"[record] opening {DEMO_URL}/gallery.html")
        await page.goto(f"{DEMO_URL}/gallery.html", wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(2)

        # Scroll the gallery
        for _ in range(6):
            await page.mouse.wheel(0, 350)
            await asyncio.sleep(1.4)

        # Scroll back to top, hover/expand the first card
        await page.evaluate("window.scrollTo({top:0, behavior:'smooth'})")
        await asyncio.sleep(1.5)
        first_expand = await page.query_selector(".scan-card .expand")
        if first_expand:
            await first_expand.click()
            await asyncio.sleep(3)

        # Pop over to /personas to give context
        await page.goto(f"{DEMO_URL}/personas.html", wait_until="networkidle", timeout=20_000)
        await asyncio.sleep(2)
        for _ in range(4):
            await page.mouse.wheel(0, 350)
            await asyncio.sleep(1.4)

        await ctx.close()
        await browser.close()
        webms = sorted(out_video_dir.glob("*.webm"))
        if not webms:
            raise RuntimeError(f"no video produced in {out_video_dir}")
        print(f"[record] saved: {webms[-1]}")
        return str(webms[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("end_to_end", "gallery"), default="end_to_end")
    ap.add_argument("--clip", default="D:/cortex/assets/nasa_artemis_15s_silent.mp4",
                    help="(end_to_end only) path to the clip to upload")
    ap.add_argument("--headless", action="store_true", help="record without showing the window")
    ap.add_argument("--max-wait", type=int, default=420, help="max seconds to wait for the scan")
    args = ap.parse_args()

    if args.mode == "end_to_end":
        out = asyncio.run(record_end_to_end(args.clip, headless=args.headless, max_wait_sec=args.max_wait))
    else:
        out = asyncio.run(record_gallery(headless=args.headless))
    print(f"\nVIDEO: {out}")


if __name__ == "__main__":
    main()
