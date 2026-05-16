r"""Demux a finalized OBS multitrack recording into SEPARATE files:

  D:\rtk-data\separated\
    video\<id>.mp4                     video only (no audio)
    audio_mic\<id>.m4a                 T2 mic stem      (own, unrestricted)
    audio_game\<id>.m4a                T3 game/desktop  (own, unrestricted)
    audio_discord_quarantine\<id>.m4a  T4 Discord       (consent-gated, LOCAL ONLY)

Stream-copy only (-c copy): fast, lossless, no re-encode.
Every output is ffprobe-verified: video file must have 0 audio streams;
each audio file must have 0 video streams. Prints PASS/FAIL per file.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

SEP = Path(r"D:\rtk-data\separated")
DIRS = {
    "video": SEP / "video",
    "mic": SEP / "audio_mic",
    "game": SEP / "audio_game",
    "discord": SEP / "audio_discord_quarantine",
}
for d in DIRS.values():
    d.mkdir(parents=True, exist_ok=True)

# stream index in the OBS master: 0:v video, 0:a:0 T1 mix, a:1 mic, a:2 game, a:3 discord
JOBS = [
    ("video",   ["-map", "0:v:0", "-an"],  ".mp4"),
    ("mic",     ["-map", "0:a:1", "-vn"],  ".m4a"),
    ("game",    ["-map", "0:a:2", "-vn"],  ".m4a"),
    ("discord", ["-map", "0:a:3", "-vn"],  ".m4a"),
]


def _probe(p: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "json", str(p)], capture_output=True, text=True).stdout
    types = [s["codec_type"] for s in json.loads(out or '{"streams":[]}')["streams"]]
    return {"video": types.count("video"), "audio": types.count("audio")}


def separate(src: Path) -> bool:
    sid = src.stem
    ok = True
    for tag, maps, ext in JOBS:
        dst = DIRS[tag] / f"{sid}{ext}"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(src), *maps, "-c", "copy", str(dst)], check=True)
        pr = _probe(dst)
        if tag == "video":
            good = pr["video"] == 1 and pr["audio"] == 0
        else:
            good = pr["audio"] == 1 and pr["video"] == 0
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {tag:8} -> {dst.name}  "
              f"(v={pr['video']} a={pr['audio']})")
    return ok


if __name__ == "__main__":
    src = Path(sys.argv[1])
    print(f"=== separating {src.name} ===")
    ok = separate(src)
    print("RESULT:", "all streams cleanly separated" if ok else "SEPARATION FAILED")
    sys.exit(0 if ok else 1)
