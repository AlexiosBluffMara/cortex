"""Switch OBS to the RTK profile, dedupe default audio, and VERIFY NVENC.

Hard rule: never stream/record on x264. We do not assume the encoder — we
start a short test recording and read it back out of the OBS log.
"""
from __future__ import annotations
import glob
import json
import os
import time
from pathlib import Path

from obsws_python import ReqClient

OBS = Path(os.environ["APPDATA"]) / "obs-studio"
cfg = json.loads((OBS / "plugin_config/obs-websocket/config.json").read_text())
cl = ReqClient(host="localhost", port=cfg["server_port"],
               password=cfg["server_password"], timeout=10)

# 1. switch to RTK profile (brings NVENC/CBR/multitrack/CQP-record + Twitch key)
before = cl.get_profile_list().current_profile_name
if before != "RTK":
    cl.set_current_profile("RTK")
    time.sleep(3)
now = cl.get_profile_list().current_profile_name
print(f"profile: {before} -> {now}")
assert now == "RTK", "profile switch FAILED"

# 2. dedupe: mute + untrack the auto default Desktop Audio / Mic/Aux
names = {i["inputName"] for i in cl.get_input_list().inputs}
for dup in ("Desktop Audio", "Mic/Aux"):
    if dup in names:
        try:
            cl.set_input_mute(dup, True)
            cl.set_input_audio_tracks(dup, {str(t): False for t in range(1, 7)})
            print(f"  silenced default '{dup}' (using routed stems instead)")
        except Exception as e:
            print(f"  ! {dup}: {e}")

# 3. verify NVENC via a short test recording + log readback
log = max(glob.glob(str(OBS / "logs" / "*.txt")), key=os.path.getmtime)
mark = os.path.getsize(log)
st = cl.get_record_status()
if not st.output_active:
    cl.start_record()
    time.sleep(6)
    r = cl.stop_record()
    print(f"test recording -> {getattr(r, 'output_path', '?')}")
    time.sleep(2)

tail = Path(log).read_text(errors="ignore")[mark:]
enc_lines = [l for l in tail.splitlines()
             if any(k in l for k in ("NVENC", "nvenc", "x264", "encoder:",
                                     "Audio Encoder", "Video Encoder"))]
print("\n--- encoder log ---")
for l in enc_lines[:18]:
    print(l.strip())

is_nvenc = any(("nvenc" in l.lower()) for l in enc_lines)
is_x264 = any("x264" in l.lower() for l in enc_lines)
print("\nRESULT:",
      "NVENC ACTIVE (good)" if (is_nvenc and not is_x264)
      else "x264 / UNVERIFIED — FIX BEFORE GOING LIVE")

recs = sorted(glob.glob(r"C:\Users\soumi\Videos\OBS\*"), key=os.path.getmtime)
print("newest recording file:", recs[-1] if recs else "NONE")
