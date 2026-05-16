"""cortex-lights — state-driven Hue daemon for Soumit's living room.

Lights reflect *what's happening* on the user's machine, not cluster health:

  OFF     nothing active (default during the day)
  GREEN   Claude / Mercury / Cortex actively running
  AMBER   Claude session is idle (waiting on user prompt) OR explicit `waiting` flag
  YELLOW  Claude usage in 75-89% range (pre-warn before red).
          Daemon also fires a 15s breathing alert when first entering this state.
  RED     Claude usage >= 90% OR rate-limited

State is read from a small JSON file (default ~/.cortex/lights-state.json)
that hooks + the usage poller update. Daemon watches the file's mtime and
pushes Hue commands only on state changes — no spam.

State file shape:
    {
      "claude_active":   bool,    # Claude Code session in progress
      "claude_idle":     bool,    # session active but waiting on prompt
      "mercury_active":  bool,    # Mercury agent is mid-turn
      "cortex_active":   bool,    # Cortex GPU pipeline is mid-run (multi-persona, vision, etc.)
      "waiting":         bool,    # explicit "waiting on external thing" override
      "usage_percent":   int,     # 0-100, latest known
      "last_update":     "ISO-8601"
    }

Wire-up:
  - Claude Code hooks (~/.claude/settings.json) update the file on
    UserPromptSubmit / Stop / SessionStart / SessionEnd events.
  - Mercury writes the file from agent_start / agent_stop hooks.
  - Cortex pipeline calls `lights_state.cortex_start()` / `.cortex_end()`
    (see D:/cortex/lights/lights_state.py — Python helper).
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
WARN_THRESHOLD = int(os.environ.get("WARN_THRESHOLD", "75"))   # yellow pre-warn
RED_THRESHOLD = int(os.environ.get("RED_THRESHOLD", "90"))     # red rate-limit warn

# CIE 1931 xy color points.
COLORS = {
    "green":  (0.214, 0.709),
    "amber":  (0.529, 0.413),  # idle / waiting on user
    "yellow": (0.460, 0.470),  # 75-89% usage warn — visibly different from amber
    "red":    (0.675, 0.322),
}

# Per-state brightness so they read distinctly even at a glance.
BRI = {"green": 220, "amber": 180, "yellow": 240, "red": 254}


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
    if usage >= RED_THRESHOLD:
        return "red"
    if usage >= WARN_THRESHOLD:
        return "yellow"
    active = (state.get("claude_active") or state.get("mercury_active")
              or state.get("cortex_active"))
    if active:
        # Explicit "waiting" overrides → amber even if active.
        # Or claude_idle without other active processes → amber.
        if state.get("waiting"):
            return "amber"
        if state.get("claude_idle") and not (state.get("mercury_active") or state.get("cortex_active")):
            return "amber"
        return "green"
    return "off"


def hue_apply(bridge, color: str, *, alert: bool = False) -> None:
    """Push a color to the Hue group. `alert=True` fires a 15s breathing
    pulse on entry — used to draw attention when crossing into a warn state."""
    if color == "off":
        bridge.set_group(HUE_GROUP, {"on": False, "transitiontime": 6})
        return
    xy = COLORS[color]
    payload = {
        "on": True,
        "bri": BRI.get(color, 220),
        "xy": list(xy),
        "transitiontime": 4,
    }
    if alert:
        # Apply color first, THEN start the alert (otherwise alert overrides color).
        bridge.set_group(HUE_GROUP, payload)
        try:
            bridge.set_group(HUE_GROUP, {"alert": "lselect"})
        except Exception as exc:
            LOG.debug("alert pulse failed (non-fatal): %s", exc)
    else:
        bridge.set_group(HUE_GROUP, payload)


def main() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        STATE_PATH.write_text(json.dumps({
            "claude_active": False, "claude_idle": False,
            "mercury_active": False, "cortex_active": False,
            "waiting": False, "usage_percent": 0,
            "last_update": ""
        }), encoding="utf-8")
        LOG.info("initialized %s", STATE_PATH)

    from phue import Bridge  # type: ignore
    bridge = Bridge(HUE_BRIDGE_IP)
    bridge.connect()
    LOG.info("Hue bridge: %s group: %s warn=%d red=%d",
             HUE_BRIDGE_IP, HUE_GROUP, WARN_THRESHOLD, RED_THRESHOLD)

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
                LOG.info("state -> %s (claude_active=%s claude_idle=%s mercury_active=%s cortex_active=%s waiting=%s usage=%s%%)",
                         color, state.get("claude_active"), state.get("claude_idle"),
                         state.get("mercury_active"), state.get("cortex_active"),
                         state.get("waiting"), state.get("usage_percent"))
                # Fire the breathing alert ONLY when crossing into a warn
                # state, not on subsequent ticks where we're still warn.
                alert = color in ("yellow", "red") and last_color not in ("yellow", "red")
                try:
                    hue_apply(bridge, color, alert=alert)
                    last_color = color
                except Exception as exc:
                    LOG.warning("hue apply failed: %s", exc)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
