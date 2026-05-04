"""edit_demo_video.py - fast ffmpeg-only post-process for the demo recording.

Reads a raw recording + timeline JSON and outputs a heavily-edited final MP4:
  - Long "wait" stretches (>5s gap between timeline events) get sped up 10x
  - Each event gets a translucent yellow highlight box + caption text
  - Title events get a big banner caption
  - Uses NVENC (RTX 5090) for fast encoding

Usage:
  python scripts/edit_demo_video.py demo_videos/raw_XXXX.mp4 demo_videos/timeline_XXXX.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WAIT_THRESHOLD = 5.0  # seconds without an event = speedup
SPEEDUP_FACTOR = 10.0  # how much to compress wait segments
MON_W, MON_H = 3840, 1600
FPS = 30


def _quote(s: str) -> str:
    """Escape special characters for ffmpeg drawtext."""
    return (s.replace("\\", "\\\\")
              .replace(":", "\\:")
              .replace("'", "’")  # curly apostrophe so we don't escape
              .replace(",", "\\,"))


def main() -> None:
    raw = Path(sys.argv[1])
    timeline_path = Path(sys.argv[2])
    out = raw.with_name(f"final_{raw.stem.split('_')[-1]}.mp4")

    timeline = json.loads(timeline_path.read_text())
    timeline.sort(key=lambda e: e["t"])

    # Probe duration
    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(raw),
    ]).decode().strip()
    raw_duration = float(probe)
    print(f"[edit] raw {raw} = {raw_duration:.1f}s, {len(timeline)} events")

    # Build segment plan: list of (t0, t1, speed_factor)
    # Anything not covered by an event AND longer than WAIT_THRESHOLD is sped up.
    event_times = [(e["t"], e["end"]) for e in timeline]
    event_times.sort()

    # Find covered intervals (union of event ranges)
    covered: list[tuple[float, float]] = []
    for t0, t1 in event_times:
        if covered and t0 <= covered[-1][1] + 0.01:
            covered[-1] = (covered[-1][0], max(covered[-1][1], t1))
        else:
            covered.append((t0, t1))

    # Build segments by walking through gaps and covered intervals
    segments: list[tuple[float, float, float]] = []
    cursor = 0.0
    for t0, t1 in covered:
        if t0 > cursor:
            gap = t0 - cursor
            if gap >= WAIT_THRESHOLD:
                segments.append((cursor, t0, SPEEDUP_FACTOR))
            else:
                segments.append((cursor, t0, 1.0))
        # Cover the event interval at 1x (highlight visible)
        segments.append((t0, t1, 1.0))
        cursor = t1
    if cursor < raw_duration:
        gap = raw_duration - cursor
        if gap >= WAIT_THRESHOLD:
            segments.append((cursor, raw_duration, SPEEDUP_FACTOR))
        else:
            segments.append((cursor, raw_duration, 1.0))

    # Merge adjacent same-speed segments
    merged: list[tuple[float, float, float]] = []
    for seg in segments:
        if merged and merged[-1][2] == seg[2] and abs(merged[-1][1] - seg[0]) < 0.05:
            merged[-1] = (merged[-1][0], seg[1], seg[2])
        else:
            merged.append(seg)
    segments = merged
    print(f"[edit] {len(segments)} segments after merge")
    total_orig = sum(t1 - t0 for t0, t1, _ in segments)
    total_new  = sum((t1 - t0) / sp for t0, t1, sp in segments)
    print(f"[edit] orig {total_orig:.1f}s -> new {total_new:.1f}s")

    def remap(orig_t: float) -> float:
        new_t = 0.0
        for t0, t1, sp in segments:
            if orig_t <= t0:
                return new_t
            if orig_t < t1:
                return new_t + (orig_t - t0) / sp
            new_t += (t1 - t0) / sp
        return new_t

    # Build ffmpeg filter_complex.
    # Step 1: Split [0:v] into N copies, one per segment.
    # Step 2: For each segment: trim, setpts (speedup), label as [s0],[s1],...
    # Step 3: Concat all segment outputs into [base].
    # Step 4: Apply drawbox + drawtext per event onto [base], chained.
    # Step 5: Output [final].

    parts: list[str] = []
    n = len(segments)
    splits = "".join([f"[v{i}]" for i in range(n)])
    parts.append(f"[0:v]split={n}{splits}")
    seg_outs = []
    for i, (t0, t1, sp) in enumerate(segments):
        # trim: start..end of original; setpts: speed
        parts.append(
            f"[v{i}]trim=start={t0:.3f}:end={t1:.3f},setpts=PTS-STARTPTS,setpts=PTS/{sp:.3f}[s{i}]"
        )
        seg_outs.append(f"[s{i}]")
    parts.append(f"{''.join(seg_outs)}concat=n={n}:v=1:a=0[base]")

    # Captions only — no highlight boxes (the screen-coordinate guesses didn't
    # land on the right region most of the time, so they were noise). Captions
    # use Google Sans Code (falls back to Segoe UI) and brand cardinal red,
    # always pinned to the bottom band so they don't fight with the UI.
    font_candidates = [
        r"C:/Windows/Fonts/GoogleSans-Bold.ttf",
        r"C:/Windows/Fonts/GoogleSansCode-Bold.ttf",
        r"C:/Windows/Fonts/seguibl.ttf",     # Segoe UI Black
        r"C:/Windows/Fonts/segoeuib.ttf",    # Segoe UI Bold
        r"C:/Windows/Fonts/segoeui.ttf",
    ]
    font_path = None
    for fp in font_candidates:
        if Path(fp).is_file():
            font_path = fp.replace(":", "\\:")  # ffmpeg escape
            break
    assert font_path, "no usable font found in C:/Windows/Fonts"

    # Brand colors
    CARDINAL = "0xCC0000"  # Red Team Kitchen / ISU cardinal
    INK      = "white"
    SHADOW   = "black@0.85"
    TITLE_COLOR  = INK
    TITLE_BG     = "0x141821@0.92"   # near-black panel
    CAPTION_COLOR = INK
    CAPTION_BG    = "0x141821@0.85"

    # Caption band sits in the bottom 200 px so it never overlaps the UI.
    CAPTION_Y = MON_H - 220
    TITLE_Y   = 110

    drawops: list[str] = []
    for i, ev in enumerate(timeline):
        s = remap(ev["t"])
        e = remap(ev["end"])
        if e - s < 0.3:
            continue
        text = _quote(ev["text"][:140])
        if ev["kind"] == "title":
            font_size = 84
            color     = TITLE_COLOR
            bg        = TITLE_BG
            y         = TITLE_Y
        else:
            font_size = 56
            color     = CAPTION_COLOR
            bg        = CAPTION_BG
            y         = CAPTION_Y
        drawops.append(
            f"drawtext=enable='between(t,{s:.2f},{e:.2f})':"
            f"fontfile='{font_path}':text='{text}':"
            f"fontsize={font_size}:fontcolor={color}:"
            f"bordercolor={SHADOW}:borderw=4:"
            f"box=1:boxcolor={bg}:boxborderw=24:"
            f"x=(w-text_w)/2:y={y}"
        )
        # Underline accent in cardinal red beneath the caption band
        if ev["kind"] != "title":
            # Small cardinal-red bar under the caption to draw the eye
            drawops.append(
                f"drawbox=enable='between(t,{s:.2f},{e:.2f})':"
                f"x=(w-{int(MON_W*0.25)})/2:y={y + font_size + 36}:"
                f"w={int(MON_W*0.25)}:h=8:color={CARDINAL}@1.0:t=fill"
            )

    if drawops:
        parts.append(f"[base]{','.join(drawops)}[final]")
    else:
        parts.append("[base]null[final]")

    filter_complex = ";".join(parts)
    filter_path = raw.with_suffix(".filter.txt")
    filter_path.write_text(filter_complex, encoding="utf-8")
    print(f"[edit] wrote filter to {filter_path} ({len(filter_complex)} chars)")

    # Try NVENC first (huge speedup on RTX 5090), fall back to libx264
    cmd_nvenc = [
        "ffmpeg", "-y",
        "-i", str(raw),
        "-/filter_complex", str(filter_path),
        "-map", "[final]",
        "-c:v", "h264_nvenc",
        "-preset", "p4",
        "-rc", "vbr",
        "-cq", "23",
        "-b:v", "0",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        str(out),
    ]
    cmd_x264 = [
        "ffmpeg", "-y",
        "-i", str(raw),
        "-/filter_complex", str(filter_path),
        "-map", "[final]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        str(out),
    ]
    print("[edit] trying NVENC...")
    r = subprocess.run(cmd_nvenc, capture_output=True, text=True)
    if r.returncode != 0:
        print("[edit] NVENC failed, falling back to libx264:")
        print(r.stderr[-400:])
        r = subprocess.run(cmd_x264, capture_output=True, text=True)
        if r.returncode != 0:
            print("[edit] libx264 also failed:")
            print(r.stderr[-1200:])
            sys.exit(1)
    print(f"[edit] wrote {out}")
    print("[edit] last ffmpeg lines:")
    print((r.stderr or "")[-300:])


if __name__ == "__main__":
    main()
