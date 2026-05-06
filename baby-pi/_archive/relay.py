"""relay.py — Cortex Pi smart-home brain.

A small FastAPI service that the orchestra (and Pixel Fold / Mercury / etc.)
can hit to issue natural-language smart-home commands. Pipeline:

    POST /act   {"text": "dim the bedroom to 30 percent"}
        ↓
    Local Gemma 4 e2b on Ollama parses intent into JSON
        ↓
    Dispatch to Hue (set_group) / Cast (TTS) / orchestra (status)
        ↓
    Return {"ok": true, "result": "...", "intent": {...}}

Config via env (see setup-baby-pi-llm.sh systemd unit):
  OLLAMA_URL       http://127.0.0.1:11434
  MODEL            gemma4:e2b
  HUE_BRIDGE_IP    192.168.0.134
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("relay")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("MODEL", "gemma4:e2b")
HUE_BRIDGE_IP = os.environ.get("HUE_BRIDGE_IP", "")

INTENT_PROMPT = """You convert a natural-language smart-home command into a single line of JSON.

Schema (one of):
  {"action":"hue_group","group":"<room name or 0 for all>","on":true|false,"brightness":0..255,"color":"red|green|blue|amber|warm_white|cold_white"|null}
  {"action":"hue_off"}
  {"action":"hue_on","brightness":200}
  {"action":"speak","text":"<words to speak>"}
  {"action":"status"}
  {"action":"unknown"}

Output ONLY the JSON object on a single line, no prose, no markdown fences.

User said: {{REQUEST}}
JSON:"""

app = FastAPI(title="cortex-pi-relay", version="0.1.0")


# ---------------------------------------------------------------------------
# Hue
# ---------------------------------------------------------------------------
COLORS = {
    "green":       (0.214, 0.709),
    "red":         (0.675, 0.322),
    "blue":        (0.155, 0.060),
    "amber":       (0.529, 0.413),
    "warm_white":  (0.444, 0.408),
    "cold_white":  (0.313, 0.329),
}


def hue_bridge():
    if not HUE_BRIDGE_IP:
        return None
    try:
        from phue import Bridge  # type: ignore
        b = Bridge(HUE_BRIDGE_IP)
        b.connect()
        return b
    except Exception as exc:
        LOG.warning("Hue connect failed: %s", exc)
        return None


def hue_resolve_group(b, name: Any) -> int:
    if isinstance(name, int):
        return name
    if isinstance(name, str) and name.isdigit():
        return int(name)
    if not name or name in ("all", "everywhere"):
        return 0
    try:
        groups = b.get_group()
        for gid, g in groups.items():
            if (g.get("name") or "").lower() == str(name).lower():
                return int(gid)
    except Exception:
        pass
    return 0


def hue_apply(intent: dict) -> str:
    b = hue_bridge()
    if not b:
        return "no Hue bridge configured"
    cmd: dict[str, Any] = {}
    on = intent.get("on")
    if on is not None:
        cmd["on"] = bool(on)
    bri = intent.get("brightness")
    if bri is not None:
        try:
            v = int(bri)
            if 0 <= v <= 100:  # interpret as percent
                v = int(v / 100 * 254)
            cmd["bri"] = max(1, min(254, v))
            cmd.setdefault("on", True)
        except Exception:
            pass
    color = intent.get("color")
    if color and color in COLORS:
        cmd["xy"] = list(COLORS[color])
        cmd.setdefault("on", True)
    if not cmd:
        return "no actionable Hue field"
    grp = hue_resolve_group(b, intent.get("group", 0))
    cmd.setdefault("transitiontime", 4)
    b.set_group(grp, cmd)
    return f"hue group={grp} cmd={cmd}"


# ---------------------------------------------------------------------------
# Cast (TTS) — uses orchestra on Big Apple via HTTP, since launchd-mDNS pain
# is already solved there. Falls back to logging if not reachable.
# ---------------------------------------------------------------------------
async def cast_speak(text: str) -> str:
    # Best-effort: hit a tiny passthrough we'll add to the Mac orchestra later.
    # For now, just log; orchestra-cast can be wired in step 2.
    LOG.info("CAST_TTS: %s", text)
    return f"queued: {text}"


# ---------------------------------------------------------------------------
# LLM intent parsing
# ---------------------------------------------------------------------------
async def llm_intent(text: str) -> dict:
    prompt = INTENT_PROMPT.replace("{{REQUEST}}", text)
    body = {"model": MODEL, "prompt": prompt, "stream": False,
            "options": {"num_predict": 80, "temperature": 0.0}}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{OLLAMA_URL}/api/generate", json=body)
        r.raise_for_status()
        out = r.json().get("response", "").strip()
    # Extract first JSON object from output
    out = out.splitlines()[0].strip() if out else ""
    if "```" in out:
        out = out.split("```")[1].lstrip("json").strip()
    try:
        return json.loads(out)
    except Exception:
        LOG.warning("LLM gave non-JSON: %r", out)
        return {"action": "unknown", "raw": out}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class Act(BaseModel):
    text: str


@app.post("/act")
async def act(req: Act):
    intent = await llm_intent(req.text)
    action = intent.get("action", "unknown")
    if action in ("hue_group", "hue_on"):
        return {"ok": True, "intent": intent, "result": hue_apply(intent)}
    if action == "hue_off":
        return {"ok": True, "intent": intent,
                "result": hue_apply({"on": False, "group": 0})}
    if action == "speak":
        msg = await cast_speak(intent.get("text") or req.text)
        return {"ok": True, "intent": intent, "result": msg}
    if action == "status":
        return {"ok": True, "intent": intent, "result": "see /status"}
    return {"ok": False, "intent": intent, "result": "unrecognized action"}


@app.get("/status")
async def status():
    out: dict[str, Any] = {"model": MODEL, "ollama": OLLAMA_URL,
                           "hue_bridge": HUE_BRIDGE_IP}
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{OLLAMA_URL}/api/version")
            out["ollama_version"] = r.json().get("version")
    except Exception as exc:
        out["ollama_version"] = f"err:{exc}"
    return out


@app.get("/healthz")
async def healthz():
    return {"ok": True}


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
