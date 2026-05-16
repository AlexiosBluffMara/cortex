#!/usr/bin/env python3
"""chatvote — Twitch chat audience voting → OBS browser-source overlay.

Right-sized for live BG3 choice-steering: viewers type a vote command in chat
(e.g. `!1`, `!2`, `!a`), this tallies it, and an OBS browser source renders a
live bar overlay. No Twitch app, no EBS, no extension review, no credentials —
it connects to Twitch IRC ANONYMOUSLY (reading public chat needs no auth; we
only ever READ, never post). Stdlib only — nothing to pip-install mid-stream.

Run:
    python chatvote.py --channel alexiosbluffmara --port 8809

Then in OBS: add a Browser source → http://localhost:8809/overlay
Operator control panel:                http://localhost:8809/control

Endpoints:
    /overlay        OBS browser source (transparent, auto-updating bars)
    /control        operator page: set the question + options, reset
    /state.json     current poll + tallies (the overlay polls this)
    /api/set        POST {question, options:[...]}  (used by /control)
    /api/reset      POST  — clears votes, keeps options
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import random
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("chatvote")

TWITCH_IRC_HOST = "irc.chat.twitch.tv"
TWITCH_IRC_PORT = 6697  # TLS


class Poll:
    """Thread-safe vote state. One vote per user per poll; last vote wins."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.question = "Waiting for the next choice…"
        # option key (lowercase, no '!') -> label
        self.options: dict[str, str] = {"1": "Option 1", "2": "Option 2"}
        self._votes: dict[str, str] = {}  # username -> option key
        self.opened_at = time.time()

    def set_poll(self, question: str, options: list[str]) -> None:
        with self._lock:
            self.question = question.strip() or "Make your choice:"
            opts: dict[str, str] = {}
            for i, label in enumerate(options, start=1):
                label = (label or "").strip()
                if label:
                    opts[str(i)] = label
            self.options = opts or {"1": "Yes", "2": "No"}
            self._votes.clear()
            self.opened_at = time.time()

    def reset(self) -> None:
        with self._lock:
            self._votes.clear()
            self.opened_at = time.time()

    def record(self, user: str, key: str) -> bool:
        """Record a vote if key is a valid option. Returns True if counted."""
        with self._lock:
            if key not in self.options:
                return False
            self._votes[user.lower()] = key
            return True

    def snapshot(self) -> dict:
        with self._lock:
            counts = {k: 0 for k in self.options}
            for k in self._votes.values():
                if k in counts:
                    counts[k] += 1
            total = sum(counts.values())
            leader = max(counts, key=lambda k: counts[k]) if total else None
            return {
                "question": self.question,
                "options": [
                    {
                        "key": k,
                        "label": self.options[k],
                        "votes": counts[k],
                        "pct": round(100 * counts[k] / total) if total else 0,
                        "leading": (k == leader and total > 0),
                    }
                    for k in self.options
                ],
                "total": total,
                "elapsed": int(time.time() - self.opened_at),
            }


# ---------------------------------------------------------------------------
# Twitch IRC — anonymous read-only. Robust: keepalive + reconnect w/ backoff.
# ---------------------------------------------------------------------------

def _parse_privmsg(line: str) -> tuple[str, str] | None:
    """Parse a Twitch IRC PRIVMSG into (username, message). Tolerant of the
    optional IRCv3 tag prefix. Returns None for non-chat lines."""
    if line.startswith("@"):
        sp = line.find(" ")
        if sp == -1:
            return None
        line = line[sp + 1 :]
    if "PRIVMSG" not in line:
        return None
    try:
        prefix, rest = line.split(" ", 1)
        user = prefix[1:].split("!", 1)[0]
        _, msg = rest.split(" :", 1)
        return user, msg.rstrip("\r\n")
    except ValueError:
        return None


def irc_reader(channel: str, poll: Poll, stop: threading.Event) -> None:
    backoff = 1.0
    while not stop.is_set():
        sock = None
        try:
            raw = socket.create_connection((TWITCH_IRC_HOST, TWITCH_IRC_PORT), timeout=20)
            sock = ssl.create_default_context().wrap_socket(
                raw, server_hostname=TWITCH_IRC_HOST
            )
            nick = f"justinfan{random.randint(10000, 99999)}"  # anonymous read
            sock.sendall(f"NICK {nick}\r\n".encode())
            sock.sendall(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
            sock.sendall(f"JOIN #{channel.lower()}\r\n".encode())
            sock.settimeout(30)
            log.info("connected to Twitch chat #%s (anonymous read)", channel)
            backoff = 1.0
            buf = ""
            while not stop.is_set():
                try:
                    chunk = sock.recv(8192).decode("utf-8", "replace")
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("Twitch closed the connection")
                buf += chunk
                while "\r\n" in buf:
                    line, buf = buf.split("\r\n", 1)
                    if line.startswith("PING"):
                        sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                        continue
                    parsed = _parse_privmsg(line)
                    if not parsed:
                        continue
                    user, msg = parsed
                    msg = msg.strip()
                    if not msg.startswith("!"):
                        continue
                    key = msg[1:].strip().lower()
                    if key.startswith("vote "):
                        key = key[5:].strip()
                    if poll.record(user, key):
                        log.debug("vote: %s -> %s", user, key)
        except Exception as exc:  # noqa: BLE001 — keep the reader alive no matter what
            if not stop.is_set():
                log.warning("IRC error (%s); reconnecting in %.0fs", exc, backoff)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        if not stop.is_set():
            stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)


# ---------------------------------------------------------------------------
# HTTP: overlay (OBS), operator control, state json
# ---------------------------------------------------------------------------

OVERLAY_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>chatvote overlay</title><style>
*{margin:0;box-sizing:border-box;font-family:'Roboto','Segoe UI',sans-serif}
body{background:transparent;overflow:hidden;color:#fff}
#wrap{position:absolute;left:24px;bottom:24px;width:440px;
 background:rgba(15,15,18,.82);border-left:6px solid #CC0000;border-radius:10px;
 padding:16px 18px;backdrop-filter:blur(3px);text-shadow:0 1px 3px #000}
#q{font-size:19px;font-weight:700;margin-bottom:4px}
#meta{font-size:12px;opacity:.65;margin-bottom:10px}
.row{margin:8px 0}.lab{display:flex;justify-content:space-between;font-size:15px;
 font-weight:500;margin-bottom:3px}.lead{color:#36d399}
.bar{height:14px;background:rgba(255,255,255,.12);border-radius:7px;overflow:hidden}
.fill{height:100%;background:#CC0000;border-radius:7px;transition:width .4s ease}
.fill.lead{background:#36d399}
</style></head><body><div id="wrap">
<div id="q">…</div><div id="meta"></div><div id="rows"></div></div>
<script>
async function tick(){
 try{const s=await(await fetch('/state.json',{cache:'no-store'})).json();
 document.getElementById('q').textContent=s.question;
 document.getElementById('meta').textContent=s.total+' vote'+(s.total==1?'':'s')+' · type !KEY in chat';
 document.getElementById('rows').innerHTML=s.options.map(o=>
  `<div class="row"><div class="lab ${o.leading?'lead':''}"><span>!${o.key} ${o.label}</span>`+
  `<span>${o.votes} (${o.pct}%)</span></div>`+
  `<div class="bar"><div class="fill ${o.leading?'lead':''}" style="width:${o.pct}%"></div></div></div>`).join('');
 }catch(e){}
}
setInterval(tick,1000);tick();
</script></body></html>"""

CONTROL_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>chatvote control</title>
<style>body{font-family:system-ui;background:#0f0f12;color:#eee;max-width:560px;margin:40px auto;padding:0 16px}
input,textarea,button{font:inherit;width:100%;margin:6px 0;padding:10px;border-radius:6px;border:1px solid #333;
background:#1b1b20;color:#eee}button{background:#CC0000;border:0;cursor:pointer;font-weight:700}
h2{color:#CC0000}small{opacity:.6}</style></head><body>
<h2>chatvote · operator</h2>
<p><small>Set the BG3 choice + options viewers will vote on. Overlay updates instantly.</small></p>
<label>Question</label><input id="q" placeholder="Save the tieflings, or side with the goblins?">
<label>Options (one per line — viewers type !1, !2, …)</label>
<textarea id="o" rows="5">Save the tieflings\nSide with the goblins</textarea>
<button onclick="setPoll()">Open / replace poll</button>
<button onclick="fetch('/api/reset',{method:'POST'}).then(()=>msg('votes reset'))"
 style="background:#444">Reset votes (keep options)</button>
<p id="m"><small></small></p>
<script>
function msg(t){document.getElementById('m').innerHTML='<small>'+t+'</small>'}
async function setPoll(){
 const q=document.getElementById('q').value;
 const options=document.getElementById('o').value.split('\\n').map(s=>s.trim()).filter(Boolean);
 await fetch('/api/set',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({question:q,options})});msg('poll opened: '+options.length+' options');
}
</script></body></html>"""


def make_handler(poll: Poll):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet — don't spam the console per request
            pass

        def _send(self, code, body: bytes, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/overlay"):
                self._send(200, OVERLAY_HTML.encode(), "text/html; charset=utf-8")
            elif path == "/control":
                self._send(200, CONTROL_HTML.encode(), "text/html; charset=utf-8")
            elif path == "/state.json":
                self._send(200, json.dumps(poll.snapshot()).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/reset":
                poll.reset()
                self._send(200, b'{"ok":true}', "application/json")
                return
            if path == "/api/set":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(n) or b"{}")
                    poll.set_poll(
                        html.escape(str(data.get("question", ""))),
                        [html.escape(str(x)) for x in (data.get("options") or [])],
                    )
                    self._send(200, b'{"ok":true}', "application/json")
                except (ValueError, TypeError) as exc:
                    self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")
                return
            self._send(404, b"not found", "text/plain")

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description="Twitch chat vote -> OBS overlay")
    ap.add_argument("--channel", required=True, help="Twitch channel login (no #)")
    ap.add_argument("--port", type=int, default=8809)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    poll = Poll()
    stop = threading.Event()
    t = threading.Thread(target=irc_reader, args=(args.channel, poll, stop), daemon=True)
    t.start()

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(poll))
    log.info("overlay  http://%s:%d/overlay", args.host, args.port)
    log.info("control  http://%s:%d/control", args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.shutdown()


if __name__ == "__main__":
    main()
