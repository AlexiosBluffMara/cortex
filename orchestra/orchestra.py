"""cortex-orchestra
Always-on daemon that keeps Ascended Base nodes "conversing" and reflects
cluster health into the smart home (Hue + Google Home / Cast).

Runs on Big Apple (always-on Mac). Every PROBE_SEC seconds it:
  1. Pings each backend (MLX:8090, Big Apple Ollama:11434, Seratonin
     Ollama:11434, Baby Pi BitNet:8000) with a tiny prompt.
  2. Logs latency + tok/s to ~/.cortex/orchestra.db.
  3. On health-state change:
       - Pulses Hue lights to a state color (green/amber/red).
       - Casts a one-line TTS announcement to a Google Home / Nest device.
  4. Emits a heartbeat line to stdout so `tail -F /tmp/orchestra.log`
     shows a steady stream of inter-device chatter.

Config via env:
  HUE_BRIDGE_IP        e.g. 192.168.0.50  (auto-discovered if unset)
  HUE_GROUP            light group name to control     (default: "All Lights")
  CAST_DEVICE          friendly_name of the speaker    (default: first Mini found)
  PROBE_SEC            seconds between probes          (default: 30)
  ANNOUNCE_ON_STARTUP  "1" to TTS once on boot         (default: "1")
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

LOG = logging.getLogger("orchestra")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

PROBE_SEC = int(os.environ.get("PROBE_SEC", "30"))
# HUE_GROUP can be a numeric group id (string) or a room name like "Living Room".
# 0 is the magic Philips group meaning "all lights".
HUE_GROUP = os.environ.get("HUE_GROUP", "0")
HUE_BRIDGE_IP = os.environ.get("HUE_BRIDGE_IP", "")
CAST_DEVICE = os.environ.get("CAST_DEVICE", "")
CAST_DEVICE_IP = os.environ.get("CAST_DEVICE_IP", "")
ANNOUNCE_ON_STARTUP = os.environ.get("ANNOUNCE_ON_STARTUP", "1") == "1"
PULSE_EVERY_PROBE = os.environ.get("PULSE_EVERY_PROBE", "0") == "1"

DB_PATH = Path.home() / ".cortex" / "orchestra.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class Backend:
    name: str
    kind: str   # "mlx" | "ollama" | "bitnet"
    url: str
    model: str


BACKENDS: list[Backend] = [
    Backend("mlx-bigapple",     "mlx",    "http://127.0.0.1:8090",    "mlx-community/gemma-4-26b-a4b-it-4bit"),
    Backend("ollama-bigapple",  "ollama", "http://127.0.0.1:11434",   "gemma4:e4b"),
    Backend("ollama-seratonin", "ollama", "http://seratonin:11434",   "gemma4:e4b"),
    # Baby Pi is a Cortex Edge node (Tailscale + AdGuard Home) — NOT an
    # inference backend. It does not run any LLM. Don't add Pi entries here.
]


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """CREATE TABLE IF NOT EXISTS probe(
             ts REAL, backend TEXT, ok INTEGER, latency_ms REAL,
             toks INTEGER, tok_per_s REAL, err TEXT)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS state(
             ts REAL, level TEXT, summary TEXT)"""
    )
    return c


# ---------------------------------------------------------------------------
# Backend probes
# ---------------------------------------------------------------------------
PROBE_PROMPT = "Reply with just one word: OK"


async def probe_mlx(b: Backend, client: httpx.AsyncClient) -> dict:
    body = {
        "model": b.model,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "max_tokens": 8, "temperature": 0,
    }
    t0 = time.perf_counter()
    r = await client.post(f"{b.url}/v1/chat/completions", json=body, timeout=15)
    dt = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    j = r.json()
    toks = j.get("usage", {}).get("completion_tokens", 0) or 8
    return {"latency_ms": dt, "toks": toks, "tok_per_s": toks * 1000 / max(dt, 1)}


async def probe_ollama(b: Backend, client: httpx.AsyncClient) -> dict:
    body = {"model": b.model, "prompt": PROBE_PROMPT, "stream": False,
            "options": {"num_predict": 8, "temperature": 0}}
    t0 = time.perf_counter()
    r = await client.post(f"{b.url}/api/generate", json=body, timeout=20)
    dt = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    j = r.json()
    toks = j.get("eval_count", 0) or 8
    eval_ns = j.get("eval_duration", 0) or int(dt * 1e6)
    tps = toks * 1e9 / max(eval_ns, 1)
    return {"latency_ms": dt, "toks": toks, "tok_per_s": tps}


async def probe_bitnet(b: Backend, client: httpx.AsyncClient) -> dict:
    t0 = time.perf_counter()
    r = await client.get(f"{b.url}/healthz", timeout=5)
    dt = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    return {"latency_ms": dt, "toks": 0, "tok_per_s": 0.0}


PROBES = {"mlx": probe_mlx, "ollama": probe_ollama, "bitnet": probe_bitnet}


async def probe(b: Backend, client: httpx.AsyncClient) -> tuple[bool, dict, str]:
    try:
        r = await PROBES[b.kind](b, client)
        return True, r, ""
    except Exception as exc:
        return False, {"latency_ms": 0, "toks": 0, "tok_per_s": 0.0}, repr(exc)[:200]


# ---------------------------------------------------------------------------
# Hue
# ---------------------------------------------------------------------------
class HueController:
    """Best-effort Hue bridge controller. Silent no-op if bridge is missing."""
    COLORS = {
        # xy values in CIE 1931 (approx)
        "green": (0.214, 0.709),
        "amber": (0.529, 0.413),
        "red":   (0.675, 0.322),
        "blue":  (0.155, 0.060),
    }

    def __init__(self) -> None:
        self.bridge = None
        self.group: int | str = 0
        try:
            from phue import Bridge  # type: ignore
        except Exception as exc:
            LOG.warning("phue not installed: %s", exc)
            return
        ip = HUE_BRIDGE_IP or self._discover()
        if not ip:
            LOG.warning("no Hue bridge discovered")
            return
        try:
            self.bridge = Bridge(ip)
            self.bridge.connect()
            LOG.info("Hue bridge: %s", ip)
        except Exception as exc:
            LOG.warning("Hue connect failed (press the bridge button): %s", exc)
            self.bridge = None
            return
        # Resolve HUE_GROUP env: numeric id OR room name. Default 0 = all lights.
        try:
            self.group = int(HUE_GROUP)
        except (TypeError, ValueError):
            try:
                groups = self.bridge.get_group()
                match = [int(gid) for gid, g in groups.items()
                         if g.get("name") == HUE_GROUP]
                self.group = match[0] if match else 0
            except Exception:
                self.group = 0
        LOG.info("Hue group target: %s", self.group)

    @staticmethod
    def _discover() -> str | None:
        try:
            r = httpx.get("https://discovery.meethue.com/", timeout=5)
            data = r.json()
            return data[0]["internalipaddress"] if data else None
        except Exception:
            return None

    def pulse(self, color: str) -> None:
        if not self.bridge:
            return
        xy = self.COLORS.get(color)
        if not xy:
            return
        try:
            cmd = {"on": True, "bri": 250, "xy": list(xy), "transitiontime": 4}
            self.bridge.set_group(self.group, cmd)
            LOG.info("Hue pulse group=%s color=%s xy=%s", self.group, color, xy)
        except Exception as exc:
            LOG.warning("Hue pulse failed: %s", exc)


# ---------------------------------------------------------------------------
# Google Home / Cast
# ---------------------------------------------------------------------------
class CastSpeaker:
    """Speaks via Google Cast (Home / Nest / Mini). Best-effort no-op if absent."""
    def __init__(self) -> None:
        self.cast = None
        self.browser = None
        try:
            import pychromecast  # type: ignore
            from pychromecast.controllers.media import MediaController  # noqa: F401
        except Exception as exc:
            LOG.warning("pychromecast not installed: %s", exc)
            return

        # PRIMARY PATH: direct IP, no mDNS. Required because launchd-spawned
        # processes on macOS can't get multicast UDP for Bonjour/Zeroconf.
        if CAST_DEVICE_IP:
            try:
                import uuid as _uuid
                from pychromecast.models import CastInfo, HostServiceInfo
                services = {HostServiceInfo(CAST_DEVICE_IP, 8009)}
                ci = CastInfo(
                    services=services,
                    uuid=_uuid.uuid4(),
                    model_name="Google Home Mini",
                    friendly_name=CAST_DEVICE or "Living Room speaker",
                    host=CAST_DEVICE_IP,
                    port=8009,
                    cast_type="audio",
                    manufacturer="Google Inc.",
                )
                cast = pychromecast.Chromecast(cast_info=ci)
                cast.wait(timeout=10)
                self.cast = cast
                LOG.info("Cast target by static IP: %s @ %s",
                         ci.friendly_name, CAST_DEVICE_IP)
                return
            except Exception as exc:
                LOG.warning("Direct-IP Cast init failed: %s", exc)

        if CAST_DEVICE:
            try:
                casts, browser = pychromecast.get_listed_chromecasts(
                    friendly_names=[CAST_DEVICE], discovery_timeout=10
                )
                if casts:
                    target = casts[0]
                    target.wait(timeout=10)
                    self.cast = target
                    self.browser = browser
                    LOG.info("Cast target by name: %s (%s)",
                             target.cast_info.friendly_name,
                             target.cast_info.model_name)
                    return
                else:
                    LOG.warning("Cast device %r not found by name; trying mDNS sweep",
                                CAST_DEVICE)
            except Exception as exc:
                LOG.warning("get_listed_chromecasts failed (%s); falling back", exc)

        try:
            casts, browser = pychromecast.get_chromecasts(timeout=10)
        except Exception as exc:
            LOG.warning("Cast discovery failed: %s", exc)
            return
        self.browser = browser
        if not casts:
            LOG.warning("no Cast devices found on LAN")
            return
        target = None
        if CAST_DEVICE:
            for c in casts:
                if c.cast_info.friendly_name == CAST_DEVICE:
                    target = c
                    break
        if not target:
            for c in casts:
                m = (c.cast_info.model_name or "").lower()
                if any(k in m for k in ("home", "nest", "mini", "hub")):
                    target = c
                    break
        if not target:
            target = casts[0]
        target.wait(timeout=10)
        self.cast = target
        LOG.info("Cast target: %s (%s)", target.cast_info.friendly_name,
                 target.cast_info.model_name)

    def say(self, text: str) -> None:
        if not self.cast:
            return
        try:
            from urllib.parse import quote
            url = ("https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob"
                   f"&tl=en&q={quote(text)}")
            mc = self.cast.media_controller
            mc.play_media(url, "audio/mp3")
            mc.block_until_active(timeout=5)
        except Exception as exc:
            LOG.warning("Cast say failed: %s", exc)


# ---------------------------------------------------------------------------
# Aggregate health
# ---------------------------------------------------------------------------
def aggregate(results: list[tuple[Backend, bool, dict, str]]) -> tuple[str, str]:
    """Return (level, summary)."""
    healthy = [r for r in results if r[1]]
    failed  = [r for r in results if not r[1]]
    n_total = len(results)
    n_ok = len(healthy)
    if n_ok == n_total:
        level = "green"
    elif n_ok >= 1:
        level = "amber"
    else:
        level = "red"
    parts = []
    for b, ok, res, _err in results:
        if ok:
            parts.append(f"{b.name}={res['tok_per_s']:.0f}t/s")
        else:
            parts.append(f"{b.name}=DOWN")
    return level, " ".join(parts)


def announce_text(level: str, results: list[tuple[Backend, bool, dict, str]]) -> str:
    n_ok = sum(1 for r in results if r[1])
    n = len(results)
    if level == "green":
        # Find fastest backend
        best = max((r for r in results if r[1]),
                   key=lambda r: r[2]["tok_per_s"], default=None)
        if best:
            b, _, res, _ = best
            return (f"All {n} Ascended Base nodes online. "
                    f"Fastest is {b.name.replace('-', ' on ')} "
                    f"at {int(res['tok_per_s'])} tokens per second.")
        return f"All {n} nodes online."
    if level == "amber":
        down = [r[0].name for r in results if not r[1]]
        return f"Degraded. {n_ok} of {n} nodes online. Down: {', '.join(down)}."
    return "Cluster down. All Ascended Base inference backends offline."


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
async def main() -> None:
    hue = HueController()
    cast = CastSpeaker()
    last_level: str | None = None
    conn = db()

    if ANNOUNCE_ON_STARTUP:
        cast.say("Ascended Base orchestrator coming online.")

    async with httpx.AsyncClient() as client:
        while True:
            t = time.time()
            tasks = [probe(b, client) for b in BACKENDS]
            done = await asyncio.gather(*tasks)
            results = list(zip(BACKENDS, *zip(*[(ok, r, e) for ok, r, e in done])))
            # Persist
            for b, ok, res, err in results:
                conn.execute(
                    "INSERT INTO probe VALUES (?,?,?,?,?,?,?)",
                    (t, b.name, int(ok), res["latency_ms"],
                     res["toks"], res["tok_per_s"], err),
                )
            level, summary = aggregate(results)
            conn.execute("INSERT INTO state VALUES (?,?,?)", (t, level, summary))
            conn.commit()

            LOG.info("[%s] %s", level.upper(), summary)
            # Hue + Cast announcements moved out of the cluster-health daemon.
            # Lights are now driven by cortex-lights.py from session state, not
            # cluster health. Orchestra just logs and casts on big state changes.
            last_level = level

            await asyncio.sleep(PROBE_SEC)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
