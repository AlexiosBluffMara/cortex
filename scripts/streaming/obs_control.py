"""OBS Studio control + native record/stream orchestration over obs-websocket.

Seratonin (RTX 5090 NVENC). WebSocket: localhost:4455.
Password is read from the OBS plugin config so it stays in one place.

Usage:
    uv run python obs_control.py status
    uv run python obs_control.py scenes
    uv run python obs_control.py scene "Demo"
    uv run python obs_control.py rec start|stop
    uv run python obs_control.py stream start|stop
    uv run python obs_control.py health        # live skip/congestion loop
    uv run python obs_control.py build-scenes   # create the 5 presets
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

from obsws_python import ReqClient

WS_CFG = Path(os.environ["APPDATA"]) / "obs-studio/plugin_config/obs-websocket/config.json"


def _client() -> ReqClient:
    cfg = json.loads(WS_CFG.read_text())
    if not cfg.get("server_enabled"):
        print("WARN: obs-websocket server_enabled=false. "
              "Enable Tools->WebSocket Server in OBS (or restart OBS after the "
              "config patch) before this will connect.", file=sys.stderr)
    return ReqClient(host="localhost", port=cfg.get("server_port", 4455),
                     password=cfg["server_password"], timeout=5)


def status(cl: ReqClient) -> None:
    st = cl.get_stream_status()
    rc = cl.get_record_status()
    sc = cl.get_current_program_scene()
    print(f"scene      : {sc.current_program_scene_name}")
    print(f"streaming  : {st.output_active}  ({st.output_bytes/1e6:.1f} MB sent)")
    print(f"recording  : {rc.output_active}  ({rc.output_timecode})")


def scenes(cl: ReqClient) -> None:
    for s in cl.get_scene_list().scenes:
        print(s["sceneName"])


def health(cl: ReqClient) -> None:
    """Skip rate >2% over a rolling window = degrade bitrate/fps before viewers see it."""
    last_total = last_skip = 0
    while True:
        s = cl.get_stream_status()
        dt = s.output_total_frames - last_total
        dk = s.output_skipped_frames - last_skip
        rate = (dk / dt * 100) if dt else 0.0
        flag = "  <-- DEGRADE" if rate > 2.0 else ""
        print(f"frames+{dt:5d} skipped+{dk:4d} skip={rate:5.2f}% "
              f"congestion={s.output_congestion:6.2%}{flag}")
        last_total, last_skip = s.output_total_frames, s.output_skipped_frames
        time.sleep(5)


def build_scenes(cl: ReqClient) -> None:
    """Create the 5 presets from the livestreaming skill. Idempotent."""
    presets = ["Standby", "Demo", "Talking head", "Co-stream", "BRB"]
    existing = {s["sceneName"] for s in cl.get_scene_list().scenes}
    for name in presets:
        if name not in existing:
            cl.create_scene(name)
            print(f"created scene: {name}")
        else:
            print(f"exists: {name}")
    print("\nSources (display/audio capture) are added per-scene in OBS UI or via "
          "create_input — encoder is set globally: NVENC H.264, 1080p60, CBR "
          "6500k (Twitch) / 9000k (YT), keyint 2s, AAC 160k.")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    cl = _client()
    if cmd == "status":
        status(cl)
    elif cmd == "scenes":
        scenes(cl)
    elif cmd == "scene":
        cl.set_current_program_scene(sys.argv[2])
        print(f"switched -> {sys.argv[2]}")
    elif cmd == "rec":
        (cl.start_record if sys.argv[2] == "start" else cl.stop_record)()
        print(f"record {sys.argv[2]}")
    elif cmd == "stream":
        if sys.argv[2] == "start":
            # repair: a profile rewrite once dropped the Twitch ingest 'server',
            # making rtmp_output fail to start. Always re-assert before going live.
            ss = cl.get_stream_service_settings().stream_service_settings
            if ss.get("service") == "Twitch" and not ss.get("server"):
                cl.set_stream_service_settings("rtmp_common", {
                    "service": "Twitch", "protocol": "RTMP",
                    "server": "auto", "key": ss.get("key"), "bwtest": False})
                print("repaired Twitch service (server=auto)")
            cl.start_stream()
        else:
            cl.stop_stream()
        print(f"stream {sys.argv[2]}")
    elif cmd == "health":
        health(cl)
    elif cmd == "build-scenes":
        build_scenes(cl)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
