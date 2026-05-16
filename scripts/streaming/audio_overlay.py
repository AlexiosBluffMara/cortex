"""Live on-stream AUDIO STATUS overlay + objective proof game audio is on
both BROADCAST (track 1 mix) and RECORDING (track 3 isolated stem).

Uses obs-websocket high-volume InputVolumeMeters events (real-time, zero
recording/stream disruption). Updates an OBS text source every ~1s with
per-stem dB + bar + ACTIVE/SILENT. Because Mic->[1,2], Game->[1,3],
Discord->[1,4] and track 1 is the streamed mix, any stem showing signal
is provably in BOTH the broadcast and its isolated record track.

  setup    create/position the 'Audio Status' overlay on Demo
  run      stream meter events -> overlay (run in background)
  probe N  print N seconds of measured dB to the console then exit (proof)
"""
from __future__ import annotations
import json
import math
import os
import sys
import time
from pathlib import Path

from obsws_python import ReqClient, EventClient

CFG = json.loads((Path(os.environ["APPDATA"]) /
                  "obs-studio/plugin_config/obs-websocket/config.json").read_text())
STEMS = {"Mic - Own Voice": "MIC", "Game (App Audio)": "GAME",
         "Discord (App Audio)": "DISC"}
_last = {v: -120.0 for v in STEMS.values()}


def _req() -> ReqClient:
    return ReqClient(host="localhost", port=CFG["server_port"],
                     password=CFG["server_password"], timeout=10)


def _db(levels) -> float:
    # inputLevelsMul: per-channel [mag, peak, inputpeak] multipliers 0..1
    m = 0.0
    for ch in levels or []:
        for x in ch:
            m = max(m, x)
    return 20 * math.log10(m) if m > 1e-7 else -120.0


def _bar(db: float) -> str:
    # map -60..0 dB to 0..10 blocks
    n = max(0, min(10, round((db + 60) / 6)))
    return "▮" * n + "▯" * (10 - n)


def _line() -> str:
    out = []
    for label in ("MIC", "GAME", "DISC"):
        db = _last[label]
        st = "ACTIVE" if db > -55 else "silent"
        out.append(f"{label} {db:5.0f}dB {_bar(db)} {st}")
    return "AUDIO  " + "   ".join(out)


def setup() -> None:
    c = _req()
    ins = {i["inputName"] for i in c.get_input_list().inputs}
    if "Audio Status" not in ins:
        c.create_input("Demo", "Audio Status", "text_gdiplus_v3",
                        {"text": "AUDIO  initialising…",
                         "font": {"face": "Consolas", "size": 22},
                         "color1": 0xFF66FF99, "color2": 0xFF66FF99,
                         "outline": True, "outline_size": 2,
                         "outline_color": 0xFF000000}, True)
        print("+ Audio Status overlay created")
    for it in c.get_scene_item_list("Demo").scene_items:
        if it["sourceName"] == "Audio Status":
            c.set_scene_item_transform("Demo", it["sceneItemId"],
                                       {"positionX": 60.0, "positionY": 1900.0})
            print("= positioned Audio Status @ (60,1900)")


def _client() -> EventClient:
    # 1<<16 = InputVolumeMeters (high-volume sub)
    return EventClient(host="localhost", port=CFG["server_port"],
                       password=CFG["server_password"], subs=(1 << 16))


def run(probe_secs: int = 0) -> None:
    setup()
    ev = _client()
    req = _req()

    def on_input_volume_meters(data):
        for inp in getattr(data, "inputs", []):
            nm = inp.get("inputName")
            if nm in STEMS:
                _last[STEMS[nm]] = _db(inp.get("inputLevelsMul"))

    ev.callback.register(on_input_volume_meters)
    t0 = time.time()
    last_push = 0.0
    while True:
        now = time.time()
        if now - last_push >= 1.0:
            txt = _line()
            try:
                req.set_input_settings("Audio Status", {"text": txt}, True)
            except Exception:
                req = _req()
            if probe_secs:
                print(f"[{now-t0:4.0f}s] {txt}", flush=True)
            last_push = now
        if probe_secs and now - t0 >= probe_secs:
            print("probe done")
            return
        time.sleep(0.2)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "setup":
        setup()
    elif cmd == "probe":
        run(int(sys.argv[2]) if len(sys.argv) > 2 else 10)
    else:
        run()
