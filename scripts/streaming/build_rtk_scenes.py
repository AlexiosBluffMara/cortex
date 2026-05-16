"""Build the RTK scene collection natively over obs-websocket (validated by OBS).

Scenes: Standby, Demo, Talking head, Co-stream, BRB.
Audio (multitrack stems for the clean data split):
  T1 = full stream mix
  T2 = own mic only           (Audeze Maxwell)
  T3 = desktop/game only      (default render)
  T4 = Discord only           (VAC 'Line 1'; Discord app must output to Line 1)
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path

from obsws_python import ReqClient

cfg = json.loads((Path(os.environ["APPDATA"]) /
                  "obs-studio/plugin_config/obs-websocket/config.json").read_text())
cl = ReqClient(host="localhost", port=cfg["server_port"],
               password=cfg["server_password"], timeout=10)

v = cl.get_version()
print(f"OBS {v.obs_version} / ws {v.obs_web_socket_version}")
prof = cl.get_profile_list()
print(f"active profile : {prof.current_profile_name}")
scol = cl.get_scene_collection_list()
print(f"scene collection: {scol.current_scene_collection_name}")

SCENES = ["Standby", "Demo", "Talking head", "Co-stream", "BRB"]
existing = {s["sceneName"] for s in cl.get_scene_list().scenes}
for name in SCENES:
    if name not in existing:
        cl.create_scene(name)
        print(f"  + scene {name}")
# remove leftover default scene(s) not in our set
for s in list(cl.get_scene_list().scenes):
    if s["sceneName"] not in SCENES:
        try:
            cl.remove_scene(s["sceneName"])
            print(f"  - removed leftover scene {s['sceneName']}")
        except Exception as e:
            print(f"  (keep {s['sceneName']}: {e})")


def find_device(inp_name, kind, settings, prop, needle):
    """Create input, resolve a device_id whose label contains `needle`, set it."""
    try:
        cl.create_input("Demo", inp_name, kind, settings, True)
    except Exception as e:
        if "already" not in str(e).lower():
            print(f"  ! {inp_name}: {e}")
            return
    try:
        items = cl.get_input_properties_list_property_items(inp_name, prop).property_items
        match = next((it for it in items
                      if needle.lower() in it["itemName"].lower()), None)
        if match:
            cl.set_input_settings(inp_name, {prop: match["itemValue"]}, True)
            print(f"  = {inp_name} -> {match['itemName']}")
        else:
            avail = ", ".join(it["itemName"] for it in items[:6])
            print(f"  ? {inp_name}: '{needle}' not found. Available: {avail}")
    except Exception as e:
        print(f"  ! {inp_name} device query: {e}")


# --- audio stems ---
find_device("Mic - Own Voice", "wasapi_input_capture", {}, "device_id", "Audeze Maxwell")
find_device("Desktop / Game Audio", "wasapi_output_capture", {}, "device_id", "")
find_device("Discord (VAC Line 1)", "wasapi_input_capture", {}, "device_id", "Line 1")

# track routing -> clean stems
for inp, tracks in [
    ("Mic - Own Voice",        {"1": True, "2": True}),
    ("Desktop / Game Audio",   {"1": True, "3": True}),
    ("Discord (VAC Line 1)",   {"1": True, "4": True}),
]:
    try:
        cl.set_input_audio_tracks(inp, tracks)
        print(f"  ~ {inp} tracks {sorted(k for k,x in tracks.items() if x)}")
    except Exception as e:
        print(f"  ! tracks {inp}: {e}")

# --- video / capture sources ---
try:
    cl.create_input("Demo", "Display Capture", "monitor_capture",
                     {"method": 2}, True)  # 2 = WGC (Windows 10 2004+)
    print("  + Display Capture -> Demo")
except Exception as e:
    print(f"  (Display Capture: {e})")

try:
    cl.create_input("Talking head", "Webcam", "dshow_input", {}, True)
    print("  + Webcam input created (set device in UI if a physical cam exists)")
except Exception as e:
    print(f"  (Webcam: {e})")

for scn, txt in [("Standby", "Red Team Kitchen — starting soon"),
                 ("BRB", "Be right back")]:
    try:
        cl.create_input(scn, f"{scn} Text", "text_gdiplus_v3",
                         {"text": txt, "font": {"face": "Segoe UI", "size": 96}}, True)
        print(f"  + text on {scn}")
    except Exception as e:
        print(f"  (text {scn}: {e})")

cl.set_current_program_scene("Standby")
print("\nscenes:", [s["sceneName"] for s in cl.get_scene_list().scenes])
print("inputs:", [i["inputName"] for i in cl.get_input_list().inputs])
print("DONE")
