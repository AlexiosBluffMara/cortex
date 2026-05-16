"""lights_state — tiny client for the Ascended Base Hue lights state machine.

Lets Mercury / Cortex / any Python script signal a state without shelling out
to bash. Drop-in replacement for `state-update.sh <event>`.

Usage:
    from lights_state import cortex_session, mercury_session, mark_waiting, set_usage

    # Context manager: lights green for the duration, off when done.
    with cortex_session():
        run_multi_persona_pipeline()

    # Manual:
    mercury_start()
    try:
        do_stuff()
    finally:
        mercury_end()

    # Pre-warn / signal explicit wait:
    mark_waiting(True)
    answer = wait_for_user_input()
    mark_waiting(False)

    # Push a usage percentage (used by the poller, but useful from anywhere):
    set_usage(82)

The state file is the same `~/.cortex/lights-state.json` the bash script
writes; this just performs the equivalent atomic mutation in Python.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STATE_PATH = Path(os.environ.get(
    "LIGHTS_STATE",
    str(Path.home() / ".cortex" / "lights-state.json"),
))
_LOCK = threading.Lock()

_DEFAULT_STATE: dict[str, Any] = {
    "claude_active": False,
    "claude_idle": False,
    "mercury_active": False,
    "cortex_active": False,
    "waiting": False,
    "usage_percent": 0,
    "last_update": "",
}


def _read() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return dict(_DEFAULT_STATE)
    try:
        s = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return dict(_DEFAULT_STATE)
    # Backfill missing keys so old state files don't break new code paths
    for k, v in _DEFAULT_STATE.items():
        s.setdefault(k, v)
    return s


def _write(state: dict[str, Any]) -> None:
    state["last_update"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".lights-state-", dir=str(STATE_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _mutate(**kw: Any) -> dict[str, Any]:
    with _LOCK:
        s = _read()
        s.update(kw)
        _write(s)
        return s


# --- public API ---

def mercury_start() -> None: _mutate(mercury_active=True)
def mercury_end()   -> None: _mutate(mercury_active=False)
def cortex_start()  -> None: _mutate(cortex_active=True)
def cortex_end()    -> None: _mutate(cortex_active=False)
def mark_waiting(b: bool) -> None: _mutate(waiting=bool(b))
def set_usage(pct: int) -> None: _mutate(usage_percent=max(0, min(100, int(pct))))


@contextmanager
def cortex_session() -> Iterator[None]:
    cortex_start()
    try:
        yield
    finally:
        cortex_end()


@contextmanager
def mercury_session() -> Iterator[None]:
    mercury_start()
    try:
        yield
    finally:
        mercury_end()


@contextmanager
def waiting_for(_what: str = "") -> Iterator[None]:
    """Mark `waiting=True` for the duration. The `_what` arg is for
    self-documentation; the daemon doesn't read it. Lights go amber."""
    mark_waiting(True)
    try:
        yield
    finally:
        mark_waiting(False)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "show":
        print(json.dumps(_read(), indent=2))
    elif cmd in ("mercury-start", "mercury-end", "cortex-start", "cortex-end"):
        globals()[cmd.replace("-", "_")]()
        print(f"set: {cmd}")
    elif cmd == "waiting":
        mark_waiting(True); print("waiting=True")
    elif cmd == "not-waiting":
        mark_waiting(False); print("waiting=False")
    elif cmd == "usage":
        set_usage(int(sys.argv[2])); print(f"usage={sys.argv[2]}%")
    else:
        print("commands: show | mercury-start | mercury-end | cortex-start | cortex-end | waiting | not-waiting | usage <n>")
        sys.exit(2)
