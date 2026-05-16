"""record_mercury_demo.py — Automated end-to-end Mercury demo for Gemma 4 Good Hackathon.

Records ~2.5 min showing:
  1. TUI  — mercury -z prompt + Gemma 4 E4B response in a terminal window
  2. WebUI — http://localhost:5173 chat interface, sends a message, gets reply
  3. Discord — bot-test-3 channel, posts a message, Snowy replies

Usage:
    python scripts/record_mercury_demo.py [--out D:/cortex/assets]
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import subprocess
import sys
import time
from pathlib import Path

# Force UTF-8 stdout/stderr so non-ASCII characters don't blow up on cp1252 terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

MERCURY = r"C:\Users\soumi\AppData\Local\Programs\Python\Python312\Scripts\mercury.exe"
CORTEX_PYTHON = r"C:\Users\soumi\cortex\.venv\Scripts\python.exe"
WEBUI_URL = "http://localhost:5173"
DISCORD_CHANNEL_ID = "1497827070510895234"  # bot-test-3
OUT_DEFAULT = Path(r"D:\cortex\assets")

# Demo prompts
TUI_PROMPT = (
    "I'm running entirely on Gemma 4 E4B via native MLX on an M4 Max. "
    "In one sentence, tell me why local-first AI matters for digital equity."
)
WEB_PROMPT = (
    "You're Snowy, a local-first AI running on personal hardware. "
    "Give me one concrete reason why running AI locally closes the digital divide."
)
DISCORD_PROMPT = (
    "Mercury, tell me something cool about how you run entirely on Gemma 4 natively."
)


def start_ffmpeg(out_path: Path) -> subprocess.Popen:
    cmd = [
        "ffmpeg", "-y",
        "-f", "gdigrab", "-framerate", "30",
        "-offset_x", "0", "-offset_y", "0",
        "-video_size", "3840x1600",
        "-draw_mouse", "1",
        "-i", "desktop",
        "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def stop_ffmpeg(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.write(b"q\n")
        proc.stdin.flush()
        proc.wait(timeout=10)
    except Exception:
        proc.terminate()


def run_tui_in_window(prompt: str) -> None:
    """Open a visible cmd window that runs mercury -z and shows the response."""
    cmd_str = f'"{MERCURY}" -z "{prompt}"'
    # Title the window so it's recognizable
    subprocess.Popen(
        f'start "Mercury TUI — Gemma 4 E4B via MLX" cmd /k "{cmd_str}"',
        shell=True,
    )


def post_discord_message(token: str, channel_id: str, content: str) -> None:
    """Post a message to a Discord channel via REST API (triggers bot reply)."""
    import urllib.request, urllib.error, json
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    payload = json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[discord] posted: {r.status}")
    except urllib.error.HTTPError as e:
        print(f"[discord] error {e.code}: {e.read()[:200]}")


async def drive_webui(url: str, prompt: str) -> None:
    """Open Chrome at the Mercury WebUI and send a chat message."""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--window-size=1440,900", "--window-position=1200,100"],
        )
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        print(f"[webui] opening {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)

        # Try common Mercury WebUI chat input selectors
        selectors = [
            "textarea[placeholder*='Message']",
            "textarea[placeholder*='message']",
            "textarea[placeholder*='Ask']",
            "input[placeholder*='Message']",
            "textarea",
            "[data-testid='chat-input']",
            ".chat-input",
            "#message-input",
        ]
        chat_input = None
        for sel in selectors:
            try:
                chat_input = await page.wait_for_selector(sel, timeout=3000, state="visible")
                if chat_input:
                    print(f"[webui] found input: {sel}")
                    break
            except Exception:
                continue

        if chat_input:
            await chat_input.click()
            await asyncio.sleep(0.5)
            await chat_input.fill(prompt)
            await asyncio.sleep(1)
            # Submit — try Enter key first, then look for send button
            await page.keyboard.press("Enter")
            print("[webui] message sent")
            await asyncio.sleep(25)  # wait for Gemma 4 response
        else:
            print("[webui] WARNING: could not find chat input — recording page as-is")
            await asyncio.sleep(10)

        # Keep browser open for 20s so recording captures the response
        await asyncio.sleep(20)
        await browser.close()


async def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / f"mercury_demo_{int(time.time())}.mp4"

    # Load Discord bot token from env file
    env_path = Path.home() / ".mercury" / ".env"
    discord_token = ""
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DISCORD_BOT_TOKEN="):
                discord_token = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    print(f"[demo] output -> {raw}")
    print("[demo] starting ffmpeg...")
    rec = start_ffmpeg(raw)
    await asyncio.sleep(2)  # let ffmpeg initialize

    # ── Phase 1: TUI (0:00 – 0:35) ──────────────────────────────
    print("\n[demo] ── Phase 1: TUI ──")
    run_tui_in_window(TUI_PROMPT)
    print("[demo] waiting 35s for TUI response...")
    await asyncio.sleep(35)

    # ── Phase 2: WebUI (0:35 – 1:40) ────────────────────────────
    print("\n[demo] ── Phase 2: WebUI ──")
    await drive_webui(WEBUI_URL, WEB_PROMPT)   # ~45s including wait

    # ── Phase 3: Discord (1:40 – 2:30) ─────────────────────────
    print("\n[demo] ── Phase 3: Discord ──")
    if discord_token:
        print(f"[demo] posting to #{DISCORD_CHANNEL_ID}...")
        post_discord_message(discord_token, DISCORD_CHANNEL_ID, DISCORD_PROMPT)
        print("[demo] waiting 50s for Discord bot reply...")
        await asyncio.sleep(50)
    else:
        print("[demo] WARNING: no DISCORD_BOT_TOKEN — skipping Discord phase")
        await asyncio.sleep(10)

    # ── Wrap up ──────────────────────────────────────────────────
    print("\n[demo] stopping recording...")
    stop_ffmpeg(rec)
    size_mb = raw.stat().st_size / 1e6 if raw.exists() else 0
    print(f"[demo] done — {raw} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()
    asyncio.run(main(Path(args.out)))
