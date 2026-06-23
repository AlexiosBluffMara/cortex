"""record_desktop_demo.py — full-desktop recording showing both WebUI + Discord paths.

Captures the entire Windows desktop using ffmpeg gdigrab while:
  1. Driving the Cortex WebUI in a HEADED Chromium (Patchright) — uploads
     NASA Artemis, watches the full TRIBE → narration → 3D ribbon flow.
  2. Posting a "/scan demonstration" message via the @abmsnowy Discord bot
     (using the bot's own API) into a target channel, with the brain screenshot
     attached. This is the only way to demonstrate the Discord side without a
     human typing — bots cannot fire slash commands as if they were a user.

The desktop recorder runs the whole time so window switches, Discord
notifications, and OS chrome are all captured naturally.

Outputs:
  scans/recordings/_mp4/cortex_desktop_full.mp4    long full-desktop recording
  (the WebUI-only short clip is also produced as a side effect)

Env:
  DISCORD_BOT_TOKEN      — bot token (loaded from ~/.hermes/.env)
  DISCORD_DEMO_CHANNEL   — channel ID to post the demo in (default: bot-test-1)
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.async_api import async_playwright
# We DON'T use Playwright's bundled Chromium because Windows Defender keeps
# quarantining the binary. Instead we launch the system's REAL Chrome (which
# Defender trusts) with --remote-debugging-port and connect via CDP.
import shutil as _shutil
import subprocess as _sp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mercury_skills.cortex_scan import submit_and_wait
from mercury_skills.cortex_scan.scan import format_for_discord

# ── Config ──────────────────────────────────────────────────────────────────
DEMO_URL          = os.environ.get("CORTEX_DEMO_URL", "https://redteamkitchen.com")
WEBUI_CLIP        = os.environ.get("WEBUI_CLIP",   r"D:\cortex\assets\nasa_artemis_15s_silent.mp4")
DISCORD_CLIP      = os.environ.get("DISCORD_CLIP", r"D:\cortex\assets\demo_clip_20s_silent.mp4")
DISCORD_CHANNEL   = os.environ.get("DISCORD_DEMO_CHANNEL", "1489805907641503774")  # #bot-test-1
OUT_DIR           = ROOT / "scans" / "recordings" / "_mp4"
OUT_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG = r"C:\Users\soumi\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe"

# Load Discord token from ~/.hermes/.env
def _load_token() -> str | None:
    env_file = Path.home() / ".hermes" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("DISCORD_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("DISCORD_BOT_TOKEN")

DISCORD_TOKEN = _load_token()


# ── Desktop recorder via ffmpeg gdigrab ─────────────────────────────────────
class DesktopRecorder:
    def __init__(self, out_path: str, framerate: int = 24):
        self.out_path = out_path
        self.framerate = framerate
        self.proc: subprocess.Popen | None = None

    def start(self):
        cmd = [
            FFMPEG, "-y",
            "-f", "gdigrab",
            "-framerate", str(self.framerate),
            "-i", "desktop",
            "-c:v", "libx264",
            "-preset", "ultrafast",  # priority is reliable capture, recompress later if needed
            "-pix_fmt", "yuv420p",
            "-crf", "26",
            "-movflags", "+faststart",
            self.out_path,
        ]
        # Suppress ffmpeg's noisy stderr to a log file
        log = open(self.out_path + ".log", "w")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=log, stderr=log,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        print(f"[record] desktop capture started -> {self.out_path}")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.communicate(b"q\n", timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
            print("[record] desktop capture stopped")


# ── Discord bot API helpers ─────────────────────────────────────────────────
async def discord_post(channel_id: str, content: str, image_bytes: bytes | None = None,
                       image_filename: str = "brain.png", embeds: list | None = None):
    """Post a message (with optional image + rich embeds) as the bot."""
    if not DISCORD_TOKEN:
        print("[discord] no token — skipping post")
        return None
    headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    payload: dict = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    async with httpx.AsyncClient(timeout=20.0) as client:
        if image_bytes is None:
            r = await client.post(url, headers=headers, json=payload)
        else:
            files = {
                "files[0]": (image_filename, image_bytes, "image/png"),
                "payload_json": (None, json.dumps(payload)),
            }
            r = await client.post(url, headers=headers, files=files)
    if r.status_code >= 400:
        print(f"[discord] post failed HTTP {r.status_code}: {r.text[:200]}")
        return None
    return r.json()


# ── WebUI demo via Patchright (HEADED, so it appears on the desktop) ────────
def _find_system_browser() -> str:
    """Locate the user's installed Chrome (or Edge as fallback). Defender trusts these."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise RuntimeError("No system Chrome or Edge found")


async def run_webui_demo(clip_path: str, max_wait_sec: int = 360):
    """Launch the user's REAL Chrome with remote debugging, connect via CDP, drive the demo.

    We avoid Playwright's bundled chromium because Windows Defender keeps
    quarantining it. The system browser is trusted and stable.
    """
    browser_exe = _find_system_browser()
    debug_port = 9222
    profile_dir = Path(os.environ.get("TEMP", "/tmp")) / f"cortex_demo_profile_{int(time.time())}"
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"[webui] launching system browser: {browser_exe}")
    chrome_proc = _sp.Popen([
        browser_exe,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        f"--window-size=1600,950",
        DEMO_URL,
    ])
    # Give Chrome a moment to start its CDP listener
    await asyncio.sleep(4)

    print(f"[webui] connecting Playwright via CDP at localhost:{debug_port}")
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{debug_port}")
        except Exception as exc:
            chrome_proc.terminate()
            raise RuntimeError(f"CDP connect failed (Chrome may not have started): {exc}")
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        # Make sure we're on the demo URL even if a tab opened on something else
        if DEMO_URL not in page.url:
            await page.goto(DEMO_URL, wait_until="domcontentloaded", timeout=60_000)

        await page.goto(DEMO_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_selector("#three-root canvas", state="attached", timeout=30_000)
        await asyncio.sleep(4)  # let WebGL warm up — recording captures the brain idle

        print(f"[webui] uploading {clip_path}")
        file_input = await page.wait_for_selector("input#scan-file", timeout=10_000)
        await file_input.set_input_files(clip_path)
        await asyncio.sleep(2)

        print("[webui] clicking Analyze")
        analyze = await page.wait_for_selector("button#scan-submit", timeout=5_000)
        await analyze.click()

        # Dwell on the system monitor while the scan runs
        print("[webui] dwelling on system monitor")
        try:
            mon = await page.query_selector(".telemetry")
            if mon:
                await mon.scroll_into_view_if_needed()
                box = await mon.bounding_box()
                if box:
                    await page.mouse.move(box["x"]+box["width"]/2, box["y"]+box["height"]/2)
        except Exception:
            pass

        # Wait for completion
        deadline = time.time() + max_wait_sec
        while time.time() < deadline:
            try:
                first = await page.text_content("#narration-student")
                if first and len(first.strip()) > 80:
                    break
            except Exception:
                pass
            await asyncio.sleep(3)
        print("[webui] first narration visible")
        await asyncio.sleep(2)

        # Walk through the persona tabs
        for persona in ("patient", "clinician", "ml_scientist", "student"):
            tab = await page.query_selector(f'button.narr-tab[data-narr="{persona}"]')
            if tab:
                await tab.click()
                await asyncio.sleep(2.5)

        # Cycle the chart panels
        for tab in ("rois", "ribbon", "polar", "rois"):
            t_btn = await page.query_selector(f'button.data-tab[data-tab="{tab}"]')
            if t_btn:
                await t_btn.click()
                await asyncio.sleep(3)

        # Brain tour mode
        tour_btn = await page.query_selector("#tour-btn")
        if tour_btn:
            await tour_btn.click()
            await asyncio.sleep(8)
            await tour_btn.click()

        # Disconnect Playwright (don't close ctx — we'd kill the user's chrome)
        try:
            await browser.close()
        except Exception:
            pass
    # Politely terminate the Chrome we spawned
    try:
        chrome_proc.terminate()
        chrome_proc.wait(timeout=8)
    except Exception:
        try:
            chrome_proc.kill()
        except Exception:
            pass


# ── Discord demo: bot posts a real scan to the channel ──────────────────────
async def run_discord_demo(clip_path: str):
    print(f"[discord] running cortex_scan on {clip_path} (will post result to channel {DISCORD_CHANNEL})")
    # 1. Tell channel "starting demo scan"
    await discord_post(
        DISCORD_CHANNEL,
        f"🧠 **Live demo** — `{Path(clip_path).name}` going through the full Cortex pipeline.\n"
        f"Public URL: {DEMO_URL} · Gallery: {DEMO_URL}/gallery.html",
    )

    # 2. Run the scan via Mercury cortex_scan tool (proves the integration works)
    result = await submit_and_wait(
        clip_path,
        cortex_base=DEMO_URL,
        tier=2,
        timeout_sec=600,
        capture_screenshot=True,
        conversation_id="demo-recording",
    )

    # 3. Post the formatted result + brain screenshot + 4 narration embeds
    payload = format_for_discord(result)
    text = payload["text"]
    embeds = payload.get("embeds") or []
    img_b64 = payload.get("image_b64")
    img_bytes = base64.b64decode(img_b64) if img_b64 else None

    # All 4 narration embeds + screenshot fit in one message — no truncation.
    await discord_post(
        DISCORD_CHANNEL,
        text[:1900],
        image_bytes=img_bytes,
        image_filename=f"brain_{result.scan_id[:8]}.png",
        embeds=embeds,
    )
    print("[discord] demo posted (full narrations as embeds)")
    return result


# ── Main orchestrator ───────────────────────────────────────────────────────
async def main():
    print("[main] starting full desktop demo (sequential WebUI then Discord)")
    desktop_out = str(OUT_DIR / "cortex_desktop_full.mp4")
    rec = DesktopRecorder(desktop_out, framerate=20)

    rec.start()
    # Give ffmpeg 2s to settle
    await asyncio.sleep(2)

    try:
        # Sequential, not parallel — avoids resource contention and gives a clean
        # narrative arc in the recording: WebUI demo first, then Discord.
        await run_webui_demo(WEBUI_CLIP, max_wait_sec=360)
        await asyncio.sleep(3)
        await run_discord_demo(DISCORD_CLIP)
    finally:
        # Give a couple seconds of "result is visible" before stopping recorder
        await asyncio.sleep(8)
        rec.stop()

    print()
    print(f"✓ Desktop recording: {desktop_out}")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not in ~/.hermes/.env")
        sys.exit(1)
    asyncio.run(main())
