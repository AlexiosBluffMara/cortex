"""cortex-lights — state-driven Hue daemon for Soumit's living room.

Lights reflect *what's happening* on the user's machine, not cluster health:

  OFF    nothing active (default during the day)
  GREEN  Claude Code OR Mercury session actively running
  AMBER  Claude Code session is idle (waiting on user prompt)
  RED    Claude usage >= 90% OR rate-limited

State is read from a small JSON file (default ~/.cortex/lights-state.json)
that hooks + the usage poller update. Daemon watches the file's mtime and
pushes Hue commands only on state changes — no spam.

State file shape:
    {
      "claude_active":   bool,    # Claude Code session in progress
      "claude_idle":     bool,    # session active but waiting on prompt
      "mercury_active":  bool,    # Mercury agent is mid-turn
      "usage_percent":   int,     # 0-100, latest known
      "last_update":     "ISO-8601"
    }

Wire-up:
  - Claude Code hooks (~/.claude/settings.json) update the file on
    UserPromptSubmit / Stop / SessionStart / SessionEnd events.
  - Mercury writes the file from agent_start / agent_stop hooks.
  - cortex-lights-usage.py polls Claude usage every 5 min.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger("lights")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

STATE_PATH = Path(os.environ.get("LIGHTS_STATE",
                                 str(Path.home() / ".cortex" / "lights-state.json")))
HUE_BRIDGE_IP = os.environ.get("HUE_BRIDGE_IP", "192.168.0.134")
HUE_GROUP = int(os.environ.get("HUE_GROUP", "0"))
POLL_SEC = float(os.environ.get("POLL_SEC", "1.0"))
DAYTIME_OFF = os.environ.get("DAYTIME_OFF", "1") == "1"  # honor user's "off by default" rule

COLORS = {
    "green": (0.214, 0.709),
    "amber": (0.529, 0.413),
    "red":   (0.675, 0.322),
}


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        LOG.warning("bad state file: %s", exc)
        return {}


def decide(state: dict[str, Any]) -> str:
    """Map state -> color name or 'off'."""
    usage = int(state.get("usage_percent", 0) or 0)
    if usage >= 90:
        return "red"
    if state.get("claude_active") or state.get("mercury_active"):
        if state.get("claude_idle") and not state.get("mercury_active"):
            return "amber"
        return "green"
    return "off"


def hue_apply(bridge, color: str) -> None:
    if color == "off":
        bridge.set_group(HUE_GROUP, {"on": False, "transitiontime": 6})
        return
    xy = COLORS[color]
    bridge.set_group(HUE_GROUP, {
        "on": True, "bri": 220, "xy": list(xy), "transitiontime": 4
    })


def main() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        STATE_PATH.write_text(json.dumps({
            "claude_active": False, "claude_idle": False,
            "mercury_active": False, "usage_percent": 0,
            "last_update": ""
        }), encoding="utf-8")
        LOG.info("initialized %s", STATE_PATH)

    from phue import Bridge  # type: ignore
    bridge = Bridge(HUE_BRIDGE_IP)
    bridge.connect()
    LOG.info("Hue bridge: %s group: %s", HUE_BRIDGE_IP, HUE_GROUP)

    last_color = None
    last_mtime = 0.0
    # Apply current state immediately
    while True:
        try:
            mtime = STATE_PATH.stat().st_mtime
        except FileNotFoundError:
            time.sleep(POLL_SEC)
            continue
        if mtime != last_mtime:
            last_mtime = mtime
            state = read_state()
            color = decide(state)
            if color != last_color:
                LOG.info("state -> %s (claude_active=%s claude_idle=%s mercury_active=%s usage=%s%%)",
                         color, state.get("claude_active"), state.get("claude_idle"),
                         state.get("mercury_active"), state.get("usage_percent"))
                try:
                    hue_apply(bridge, color)
                    last_color = color
                except Exception as exc:
                    LOG.warning("hue apply failed: %s", exc)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
