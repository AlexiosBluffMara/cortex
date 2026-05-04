"""watchdog.py - Seratonin fleet watchdog.

Pings every service in the Seratonin stack once every N seconds. If a service
is dead, restarts it using a known launch recipe. Tracks restart attempts and
backs off so we don't thrash on persistent failures.

Also pings Big Apple (best-effort) so the local watchdog log shows the full
fleet state - we can't restart Big Apple's processes from Windows, but a
human reading the log will see the peer go red and act.

Run from PowerShell:
  python D:/cortex/fleet/watchdog.py

Or as a scheduled task at user logon (see fleet/install_watchdog_task.ps1).

Optional env:
  WATCHDOG_INTERVAL_SEC  poll interval, default 20
  WATCHDOG_MAX_RESTARTS  per-service restart cap inside the back-off window, default 3
  WATCHDOG_BACKOFF_SEC   reset window for the restart counter, default 600
  WATCHDOG_LOG_DIR       defaults to C:/Temp/logs
  WATCHDOG_HTTP_PORT     exposes /status JSON on this port, default 8780 (set to 0 to disable)
  BIGAPPLE_HOST          tailnet IP, default 100.93.240.52
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
VENV_PY = Path(r"C:\Users\soumi\cortex\.venv\Scripts\python.exe")
NPM_CMD = Path(r"C:\Program Files\nodejs\npm.cmd")
MERCURY_EXE = Path(r"D:\mercury\.venv\Scripts\mercury.exe")
LOG_DIR = Path(os.environ.get("WATCHDOG_LOG_DIR", r"C:\Temp\logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

INTERVAL_SEC = int(os.environ.get("WATCHDOG_INTERVAL_SEC", "20"))
MAX_RESTARTS = int(os.environ.get("WATCHDOG_MAX_RESTARTS", "3"))
BACKOFF_SEC = int(os.environ.get("WATCHDOG_BACKOFF_SEC", "600"))
HTTP_PORT = int(os.environ.get("WATCHDOG_HTTP_PORT", "8780"))
BIGAPPLE_HOST = os.environ.get("BIGAPPLE_HOST", "100.93.240.52")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "fleet_watchdog.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("watchdog")


def _load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


HERMES_ENV = _load_dotenv(Path(os.environ["USERPROFILE"]) / ".hermes" / ".env")


def _check_http(url: str, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


def _check_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _start_detached(name: str, exe: str | Path, args: list[str], cwd: str | Path,
                    env_extra: dict[str, str] | None = None,
                    log_file: str | Path | None = None) -> subprocess.Popen | None:
    """Spawn a long-running process detached; redirect stdout/err to a log file."""
    log_path = Path(log_file or LOG_DIR / f"{name}.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(HERMES_ENV)
    if env_extra:
        env.update(env_extra)
    try:
        fh = open(log_path, "ab", buffering=0)
        creationflags = 0
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            creationflags = 0x00000008 | 0x00000200
        proc = subprocess.Popen(
            [str(exe), *args],
            cwd=str(cwd),
            env=env,
            stdout=fh,
            stderr=fh,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        log.info("[restart] launched %s (pid=%s) -> %s", name, proc.pid, log_path)
        return proc
    except Exception as exc:
        log.error("[restart] failed to launch %s: %s", name, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Service definitions
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Service:
    name: str
    health: Callable[[], bool]                          # returns True if alive
    restart: Callable[[], subprocess.Popen | None] | None  # None = monitor-only
    role: str = "seratonin"                              # seratonin | bigapple
    restarts: deque = field(default_factory=lambda: deque(maxlen=10))
    last_status: bool | None = None
    last_change: float = field(default_factory=time.time)
    consecutive_fails: int = 0


def _restart_router() -> subprocess.Popen | None:
    return _start_detached(
        "router",
        VENV_PY,
        ["-m", "uvicorn", "inference_router.server:app",
         "--host", "0.0.0.0", "--port", "8766", "--log-level", "info"],
        REPO,
        env_extra={
            "OLLAMA_BACKENDS": "http://localhost:11434,http://100.93.240.52:11434",
            "ROUTER_PORT": "8766",
        },
        log_file=LOG_DIR / "cortex_router.log",
    )


def _restart_backend() -> subprocess.Popen | None:
    return _start_detached(
        "backend",
        VENV_PY,
        ["-m", "uvicorn", "webapp.server:app",
         "--host", "0.0.0.0", "--port", "8773", "--log-level", "info"],
        REPO,
        env_extra={
            "OLLAMA_URL": "http://localhost:8766",
            "MODEL_FAST": "gemma4:e4b",
            "MODEL_DEEP": "gemma4:26b",
            "MODEL_EXPERT": "gemma4:31b",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        log_file=LOG_DIR / "cortex_8773.log",
    )


def _restart_vite() -> subprocess.Popen | None:
    return _start_detached(
        "vite",
        NPM_CMD,
        ["run", "dev"],
        REPO / "webapp",
        log_file=LOG_DIR / "cortex_frontend.log",
    )


def _restart_ollama() -> subprocess.Popen | None:
    """Best-effort: launch `ollama serve`. If it's installed as a Windows service
    we can't restart it from here, but `ollama serve` will fail-fast in that case
    and the service will recover on its own."""
    ollama = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    if not ollama.is_file():
        ollama = Path(r"C:\Program Files\Ollama\ollama.exe")
    if not ollama.is_file():
        log.warning("[restart] ollama.exe not found; cannot restart")
        return None
    return _start_detached("ollama", ollama, ["serve"], REPO, log_file=LOG_DIR / "ollama.log")


SERVICES: list[Service] = [
    Service("router",        lambda: _check_http("http://localhost:8766/healthz"),       _restart_router),
    Service("backend",       lambda: _check_http("http://localhost:8773/api/health"),    _restart_backend),
    Service("vite",          lambda: _check_http("http://localhost:5173/"),              _restart_vite),
    Service("ollama_local",  lambda: _check_http("http://localhost:11434/api/tags"),     _restart_ollama),
    # Peer (read-only — we can't restart big-apple processes from here)
    Service("backend_peer",   lambda: _check_http(f"http://{BIGAPPLE_HOST}:8773/api/health"), None, role="bigapple"),
    Service("ollama_peer",    lambda: _check_http(f"http://{BIGAPPLE_HOST}:11434/api/tags"),  None, role="bigapple"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Watchdog loop
# ─────────────────────────────────────────────────────────────────────────────

def _try_restart(svc: Service) -> bool:
    """Attempt a restart, honoring the back-off window."""
    if svc.restart is None:
        return False
    now = time.time()
    # Drop restart attempts older than the back-off window
    while svc.restarts and now - svc.restarts[0] > BACKOFF_SEC:
        svc.restarts.popleft()
    if len(svc.restarts) >= MAX_RESTARTS:
        log.warning("[%s] restart suppressed: %d attempts in last %ds (cap=%d)",
                    svc.name, len(svc.restarts), BACKOFF_SEC, MAX_RESTARTS)
        return False
    log.info("[%s] DEAD - attempting restart (%d/%d in window)",
             svc.name, len(svc.restarts) + 1, MAX_RESTARTS)
    proc = svc.restart()
    if proc is not None:
        svc.restarts.append(now)
        return True
    return False


def _tick() -> dict[str, Any]:
    """One pass: check every service, restart dead local ones."""
    snapshot: dict[str, Any] = {"ts": int(time.time()), "services": {}}
    for svc in SERVICES:
        alive = svc.health()
        if alive != svc.last_status:
            log.info("[%s] %s -> %s", svc.name,
                     "?" if svc.last_status is None else ("UP" if svc.last_status else "DOWN"),
                     "UP" if alive else "DOWN")
            svc.last_status = alive
            svc.last_change = time.time()
        if alive:
            svc.consecutive_fails = 0
        else:
            svc.consecutive_fails += 1
            # Wait one extra cycle before restarting to avoid acting on transient blips
            if svc.consecutive_fails >= 2 and svc.restart is not None:
                _try_restart(svc)
        snapshot["services"][svc.name] = {
            "alive": alive,
            "role": svc.role,
            "consecutive_fails": svc.consecutive_fails,
            "restarts_window": list(svc.restarts),
            "last_change": svc.last_change,
            "monitor_only": svc.restart is None,
        }
    return snapshot


_LATEST: dict[str, Any] = {"ts": 0, "services": {}}


def _loop() -> None:
    log.info("[watchdog] starting; interval=%ds, restart cap=%d/%ds, peer=%s",
             INTERVAL_SEC, MAX_RESTARTS, BACKOFF_SEC, BIGAPPLE_HOST)
    while True:
        try:
            snap = _tick()
            _LATEST.clear()
            _LATEST.update(snap)
        except Exception as exc:
            log.exception("[watchdog] tick failed: %s", exc)
        time.sleep(INTERVAL_SEC)


# ─────────────────────────────────────────────────────────────────────────────
# Tiny HTTP /status endpoint so the WebUI / status page can pull live state
# ─────────────────────────────────────────────────────────────────────────────

def _serve_http(port: int) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/status"):
                body = json.dumps(_LATEST).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_):  # silence access log
            return

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("[watchdog] /status JSON on http://0.0.0.0:%d/status", port)
    srv.serve_forever()


def main() -> None:
    if HTTP_PORT > 0:
        threading.Thread(target=_serve_http, args=(HTTP_PORT,), daemon=True).start()
    _loop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("[watchdog] interrupted - exiting")
