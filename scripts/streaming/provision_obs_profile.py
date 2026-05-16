"""Provision a clean native 'RTK' OBS profile + scene-collection scaffold.

Must run while OBS is CLOSED (OBS rewrites these files on exit).
- Backs up global.ini.
- Writes profile RTK: Advanced output, NVENC H.264, 1080p60, CBR 6500 (Twitch),
  simultaneous local recording (CQP18, high quality for clean training data),
  4 recorded audio tracks (T1 stream mix / T2 mic / T3 desktop+game / T4 Discord).
- service.json wired to the secured Twitch key (read from ~/.streaming/twitch.key).
- Clones the existing valid scene JSON as RTK.json (guaranteed-valid schema for
  this OBS build); real scenes/sources are built post-launch via obs-websocket.
- Points global.ini [Basic] at RTK / RTK.json.
"""
from __future__ import annotations
import json
import os
import shutil
from pathlib import Path

APPDATA = Path(os.environ["APPDATA"]) / "obs-studio"
PROFILES = APPDATA / "basic" / "profiles"
SCENES = APPDATA / "basic" / "scenes"
HOME = Path(os.path.expanduser("~"))
REC_DIR = r"C:\Users\soumi\Videos\OBS"

basic_ini = """[General]
Name=RTK

[Output]
Mode=Advanced

[Video]
BaseCX=1920
BaseCY=1080
OutputCX=1920
OutputCY=1080
FPSType=0
FPSCommon=60
ScaleType=lanczos
ColorFormat=NV12
ColorSpace=709
ColorRange=Partial

[Audio]
SampleRate=48000
ChannelSetup=Stereo

[AdvOut]
ApplyServiceSettings=true
UseRescale=false
TrackIndex=1
VodTrackIndex=2
Encoder=obs_nvenc_h264_tex
RecType=Standard
RecFilePath=%s
RecFormat2=hybrid_mp4
RecUseRescale=false
RecTracks=15
RecEncoder=obs_nvenc_h264_tex
FLVTrack=1
RecSplitFile=true
RecSplitFileType=Time
RecSplitFileTime=60
""" % REC_DIR

stream_enc = {
    "bitrate": 6500, "keyint_sec": 2, "preset2": "p5", "tune": "hq",
    "multipass": "qres", "profile": "high", "rate_control": "CBR",
    "bf": 2, "psycho_aq": True, "gpu": 0, "max_bitrate": 6500,
}
record_enc = {
    "rate_control": "CQP", "cqp": 18, "keyint_sec": 0, "preset2": "p6",
    "tune": "hq", "profile": "high", "bf": 2, "psycho_aq": True, "gpu": 0,
}


def main() -> None:
    # 0. safety: refuse if OBS running
    import subprocess
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    if "obs64.exe" in out:
        raise SystemExit("ABORT: OBS is running. Close it first (it overwrites config on exit).")

    # 1. backup
    g = APPDATA / "global.ini"
    bak = APPDATA / "global.ini.rtkbak"
    if not bak.exists():
        shutil.copy2(g, bak)
        print(f"backup -> {bak}")

    # 2. profile dir
    pdir = PROFILES / "RTK"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "basic.ini").write_text(basic_ini, encoding="utf-8")
    (pdir / "streamEncoder.json").write_text(json.dumps(stream_enc, indent=2))
    (pdir / "recordEncoder.json").write_text(json.dumps(record_enc, indent=2))

    key = (HOME / ".streaming" / "twitch.key").read_text().strip()
    service = {
        "type": "rtmp_common",
        "settings": {
            "service": "Twitch",
            "protocol": "RTMP",
            "key": key,
            "bwtest": False,
        },
    }
    (pdir / "service.json").write_text(json.dumps(service))
    print(f"profile RTK written ({len(key)=}, key not echoed)")

    # 3. scene collection: clone a known-valid schema, rename, blank scenes
    src = SCENES / "Untitled.json"
    sc = json.loads(src.read_text())
    sc["name"] = "RTK"
    rtk_scene = SCENES / "RTK.json"
    rtk_scene.write_text(json.dumps(sc))
    print(f"scene collection scaffold -> {rtk_scene} (scenes/sources built via websocket post-launch)")

    # 4. point global.ini at RTK
    lines = g.read_text(encoding="utf-8", errors="ignore").splitlines()
    repl = {
        "Profile=": "Profile=RTK",
        "ProfileDir=": "ProfileDir=RTK",
        "SceneCollection=": "SceneCollection=RTK",
        "SceneCollectionFile=": "SceneCollectionFile=RTK",
    }
    for i, ln in enumerate(lines):
        for pfx, new in repl.items():
            if ln.strip().startswith(pfx):
                lines[i] = new
    g.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("global.ini [Basic] -> RTK / RTK.json")
    print("DONE. Safe to launch OBS.")


if __name__ == "__main__":
    main()
