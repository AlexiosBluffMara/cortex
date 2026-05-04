"""scan_bot.py — minimal Discord bot that exposes /scan via mercury_skills.cortex_scan.

Behavior:
  - Slash command:  /scan       — reply with usage + the live demo URL
  - Slash command:  /scan_url <url>   — fetch + scan
  - On any message with an attachment + the word "scan" (or @mention):
      1. download the attachment
      2. call mercury_skills.cortex_scan.submit_and_wait
      3. reply with the four narrations + Patchright screenshot of the rendered brain

Runs alongside (or instead of) Mercury's gateway, using the same DISCORD_BOT_TOKEN.
Two Mercury processes can't share a token; this script REPLACES Mercury for the
duration of a demo. Stop Mercury before starting this.

Env required:
  DISCORD_BOT_TOKEN          — same token Mercury uses
  CORTEX_API_BASE            — defaults to https://big-apple.scylla-betta.ts.net

Optional:
  DISCORD_SCAN_CHANNEL_IDS   — comma-separated channel IDs to restrict to
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import tempfile
import base64
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

# Make the cortex repo importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mercury_skills.cortex_scan import submit_and_wait, ScanError
from mercury_skills.cortex_scan.scan import format_for_discord

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("scan_bot")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_BOT_TOKEN env var required")

CORTEX_API_BASE = os.environ.get("CORTEX_API_BASE", "https://big-apple.scylla-betta.ts.net")
ALLOWED_CHANNEL_IDS = {
    int(c.strip()) for c in os.environ.get("DISCORD_SCAN_CHANNEL_IDS", "").split(",")
    if c.strip().isdigit()
}

intents = discord.Intents.default()
intents.message_content = True   # need this to read message text
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


@bot.event
async def on_ready():
    log.info("logged in as %s (id=%s)", bot.user, bot.user.id if bot.user else "?")
    log.info("connected to %d guilds", len(bot.guilds))
    # Sync slash commands per-guild (instant) AND globally (up to 1 hr propagation)
    for g in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=g)
            synced = await bot.tree.sync(guild=g)
            log.info("synced %d slash commands to guild %s (%s)", len(synced), g.name, g.id)
        except Exception as exc:
            log.warning("guild sync failed for %s: %s", g.id, exc)
    try:
        synced = await bot.tree.sync()
        log.info("synced %d global slash commands (1hr propagation)", len(synced))
    except Exception as exc:
        log.warning("global sync failed: %s", exc)
    activity = discord.Game(name=f"/scan · {CORTEX_API_BASE}")
    await bot.change_presence(activity=activity, status=discord.Status.online)


def _allowed_here(channel_id: int) -> bool:
    return not ALLOWED_CHANNEL_IDS or channel_id in ALLOWED_CHANNEL_IDS


def _looks_like_scan_request(content: str) -> bool:
    if not content:
        return False
    s = content.lower()
    return any(t in s for t in ("scan", "brain", "cortex"))


async def _do_scan(channel: discord.abc.Messageable, file_bytes: bytes, filename: str,
                   tier: int = 2, who: str | None = None):
    """Save the attachment, run the scan, reply in-channel with text + screenshot."""
    try:
        await channel.typing().__aenter__()
    except Exception:
        pass

    notice = await channel.send(
        f"🧠 Submitting **{filename}** to Cortex (Big Apple → Seratonin TRIBE → 4 personas) …\n"
        f"Public gallery: {CORTEX_API_BASE}/gallery.html"
    )

    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(file_bytes)
        tmp_path = tf.name

    try:
        result = await submit_and_wait(
            tmp_path,
            cortex_base=CORTEX_API_BASE,
            tier=tier,
            timeout_sec=600,
            capture_screenshot=True,
            conversation_id=f"discord-{channel.id}",
        )
    except ScanError as exc:
        await notice.edit(content=f"❌ Could not submit scan: `{exc}`")
        return
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    payload = format_for_discord(result)
    text = payload["text"]
    img_b64 = payload.get("image_b64")

    # Discord caps message body at 2000 chars; split if needed.
    chunks: list[str] = []
    while text:
        chunks.append(text[:1900])
        text = text[1900:]

    files: list[discord.File] = []
    if img_b64:
        try:
            img = io.BytesIO(base64.b64decode(img_b64))
            files.append(discord.File(img, filename=f"brain_{result.scan_id[:8]}.png"))
        except Exception as exc:
            log.warning("could not decode screenshot: %s", exc)

    # First chunk goes on the original notice (with image attachment)
    await notice.edit(content=chunks[0])
    if files:
        await channel.send(files=files)
    for chunk in chunks[1:]:
        await channel.send(chunk)


# ─────────────────────────────────────────────────────────────────────────────
# Slash commands
# ─────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="scan", description="Run a Cortex brain-response scan on an attached file")
@app_commands.describe(
    file="Video / audio / image / text file to scan",
    tier="Narration depth (0-6, default 2)",
)
async def slash_scan(
    interaction: discord.Interaction,
    file: discord.Attachment,
    tier: int = 2,
):
    if not _allowed_here(interaction.channel_id or 0):
        await interaction.response.send_message("This channel is not enabled for /scan.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"Got it — pulling **{file.filename}** ({file.size//1024} KB) into Cortex.",
        ephemeral=False,
    )
    file_bytes = await file.read()
    await _do_scan(interaction.channel, file_bytes, file.filename, tier=tier,
                   who=str(interaction.user))


@bot.tree.command(name="gallery", description="Show the live Cortex scan gallery")
async def slash_gallery(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🧠 Live Cortex gallery: {CORTEX_API_BASE}/gallery.html\n"
        f"Personas: {CORTEX_API_BASE}/personas.html\n"
        f"Specs: {CORTEX_API_BASE}/specs.html",
        ephemeral=False,
    )


@bot.tree.command(name="cortex_help", description="How to use the Cortex scan bot")
async def slash_help(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"**Cortex (`@abmsnowy`)** — multimodal brain-response analysis.\n\n"
        f"`/scan <file>` — submit an attachment for analysis. Returns 4 persona narrations + "
        f"a screenshot of the rendered 3D brain.\n"
        f"`/gallery` — link to the public scan gallery.\n\n"
        f"You can also just **drop a file** in chat with the word \"scan\" or `@abmsnowy`.\n\n"
        f"Live demo: {CORTEX_API_BASE}\n"
        f"Source: https://github.com/AlexiosBluffMara/cortex",
        ephemeral=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Plain-message trigger (drop a file + say "scan this")
# ─────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.author.bot:
        return
    if not _allowed_here(message.channel.id):
        return
    # Trigger only when (a) we're @mentioned OR (b) message contains the word "scan"
    mentioned = bot.user in message.mentions if bot.user else False
    if not (mentioned or _looks_like_scan_request(message.content)):
        return
    if not message.attachments:
        if mentioned:
            await message.reply(
                "Mention me with a file attached, or use `/scan <file>` — I'll run a Cortex scan and reply.",
            )
        return
    att = message.attachments[0]
    file_bytes = await att.read()
    await _do_scan(message.channel, file_bytes, att.filename, tier=2)


if __name__ == "__main__":
    log.info("starting scan_bot · CORTEX_API_BASE=%s · allowed_channels=%s",
             CORTEX_API_BASE, ALLOWED_CHANNEL_IDS or "(any)")
    bot.run(TOKEN, log_handler=None)
