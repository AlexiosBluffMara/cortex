"""Live ops for the alexiosbluffmara stream: native OBS overlays + analytics.

  setup    - add native overlay sources to Demo (brand bug, live-stats, chat),
             position them, switch to Demo
  monitor  - loop: Twitch API + OBS health -> update on-stream stats overlay
             + print analytics line   (run in background)
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from obsws_python import ReqClient

CHANNEL_ID = "1499028196"          # alexiosbluffmara
CHANNEL = "alexiosbluffmara"
GQL = "https://gql.twitch.tv/gql"
GQL_CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # public web client-id (read-only)

cfg = json.loads((Path(os.environ["APPDATA"]) /
                  "obs-studio/plugin_config/obs-websocket/config.json").read_text())


def cl() -> ReqClient:
    return ReqClient(host="localhost", port=cfg["server_port"],
                     password=cfg["server_password"], timeout=12)


def twitch_stream() -> dict:
    q = ('{"query":"query{user(id:\\"%s\\"){stream{type viewersCount createdAt}}}"}'
         % CHANNEL_ID)
    out = subprocess.run(
        ["curl", "-s", "-X", "POST", GQL, "-H", f"Client-ID: {GQL_CID}",
         "-H", "Content-Type: application/json", "--data", q],
        capture_output=True, text=True).stdout
    try:
        return ((json.loads(out).get("data") or {}).get("user") or {}).get("stream") or {}
    except Exception:
        return {}


def _scene_item(c, scene, name):
    for it in c.get_scene_item_list(scene).scene_items:
        if it["sourceName"] == name:
            return it["sceneItemId"]
    return None


def setup() -> None:
    c = cl()
    scene = "Demo"
    existing = {i["inputName"] for i in c.get_input_list().inputs}

    def ensure(name, kind, settings):
        if name in existing:
            c.set_input_settings(name, settings, True)
            print(f"  ~ updated {name}")
        else:
            c.create_input(scene, name, kind, settings, True)
            print(f"  + {name}")

    ensure("Brand Bug", "text_gdiplus_v3", {
        "text": f"● LIVE   twitch.tv/{CHANNEL}",
        "font": {"face": "Segoe UI", "size": 36, "style": "Bold"},
        "color1": 0xFFFFFFFF, "color2": 0xFFFFFFFF, "outline": True,
        "outline_size": 2, "outline_color": 0xFF000000})
    ensure("Live Stats", "text_gdiplus_v3", {
        "text": "warming up…",
        "font": {"face": "Consolas", "size": 24},
        "color1": 0xFF00E5C8, "color2": 0xFF00E5C8, "outline": True,
        "outline_size": 2, "outline_color": 0xFF000000})
    ensure("Twitch Chat", "browser_source", {
        "url": f"https://www.twitch.tv/popout/{CHANNEL}/chat?darkpopout",
        "width": 360, "height": 760, "reroute_audio": False})

    # positions on a 3840x2160 canvas
    place = {"Brand Bug": (60, 60), "Live Stats": (60, 2160 - 140),
             "Twitch Chat": (3840 - 380, 60)}
    for nm, (x, y) in place.items():
        sid = _scene_item(c, scene, nm)
        if sid is not None:
            c.set_scene_item_transform(scene, sid, {
                "positionX": float(x), "positionY": float(y)})
            print(f"  = positioned {nm} @ ({x},{y})")
    c.set_current_program_scene(scene)
    print("scene -> Demo (overlays live)")


def monitor() -> None:
    c = cl()
    while True:
        s = c.get_stream_status()
        rc = c.get_record_status()
        tw = twitch_stream()
        live = tw.get("type") == "live"
        viewers = tw.get("viewersCount", 0)
        up = "?"
        if tw.get("createdAt"):
            started = datetime.fromisoformat(tw["createdAt"].replace("Z", "+00:00"))
            secs = int((datetime.now(timezone.utc) - started).total_seconds())
            up = f"{secs//3600}h{secs%3600//60:02d}m"
        kbps = 0.0
        if s.output_duration:
            kbps = s.output_bytes * 8 / (s.output_duration / 1000) / 1000
        skip = (s.output_skipped_frames / s.output_total_frames * 100
                if s.output_total_frames else 0)
        overlay = (f"{'● LIVE' if live else '○ OFFLINE'}  "
                   f"{viewers} viewers  up {up}\n"
                   f"{kbps:,.0f} kbps  skip {skip:.2f}%  "
                   f"{'REC' if rc.output_active else 'no-rec'}")
        try:
            c.set_input_settings("Live Stats", {"text": overlay}, True)
        except Exception as e:
            print("overlay update failed:", e)
        print(f"[{datetime.now():%H:%M:%S}] twitch_live={live} viewers={viewers} "
              f"up={up} obs_active={s.output_active} {kbps:,.0f}kbps "
              f"skip={skip:.2f}% rec={rc.output_active}", flush=True)
        time.sleep(25)


if __name__ == "__main__":
    {"setup": setup, "monitor": monitor}.get(
        sys.argv[1] if len(sys.argv) > 1 else "setup", setup)()
