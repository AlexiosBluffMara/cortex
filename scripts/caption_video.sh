#!/usr/bin/env bash
# caption_video.sh — overlay timestamped captions onto a recorded demo MP4.
#
# Usage:
#   bash scripts/caption_video.sh end_to_end   # captions the long video
#   bash scripts/caption_video.sh gallery      # captions the gallery video
#
# Output:
#   <input>_captioned.mp4 in the same dir.
#
# Captions are hard-coded for the two recordings produced by record_demo.py.
# Tune the timestamps if the recording length changes.

set -euo pipefail

MODE="${1:-end_to_end}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MP4_DIR="$ROOT/scans/recordings/_mp4"

# Find an ffmpeg binary
FFMPEG="$(command -v ffmpeg || true)"
if [[ -z "$FFMPEG" ]]; then
  for c in \
    "/c/Users/soumi/AppData/Local/ms-playwright/ffmpeg-1011/ffmpeg-win64.exe" \
    "/c/Users/soumi/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1-full_build/bin/ffmpeg.exe"; do
    if [[ -x "$c" ]]; then FFMPEG="$c"; break; fi
  done
fi
[[ -z "$FFMPEG" ]] && { echo "no ffmpeg found"; exit 1; }
echo "using ffmpeg: $FFMPEG"

# We need a font file. Look in common places.
FONT=""
for f in \
  "C:/Windows/Fonts/segoeuib.ttf" \
  "C:/Windows/Fonts/arialbd.ttf" \
  "C:/Windows/Fonts/calibrib.ttf"; do
  if [[ -f "$f" ]]; then FONT="$f"; break; fi
done
# ffmpeg drawtext on Windows wants forward slashes + a colon escaped
FONT_FOR_FFMPEG="${FONT//\\//}"
FONT_FOR_FFMPEG="${FONT_FOR_FFMPEG/C:/C\\:}"
[[ -n "$FONT" ]] && echo "using font: $FONT"

# Caption timeline: each line is "<start_sec> <duration_sec> <text>"
# end_to_end recording is roughly:
#   0-4    page loads, brain spins up
#   4-10   user picks the file
#   10-15  Analyze clicked, scan submitted
#   15-180 TRIBE running on Seratonin (proxy hop)
#   180-215 narrating phase
#   215-360 timeline scrubbing + persona tabs + tour
captions_end_to_end=(
  "0   4  Cortex live demo — redteamkitchen.com"
  "4   6  Drag a video onto the dropzone (or pick from disk)"
  "10  4  Submit the scan"
  "15 25  TRIBE v2 inference running on the RTX 5090 (proxied via Tailscale)"
  "40 30  20,484 cortical vertices being predicted at 2 Hz"
  "70 30  Gemma 4 narrates locally, with OpenRouter free tier as fallback"
  "180 15 Scan complete — four parallel narrations from four readers"
  "200 12 Each narration is one TRIBE prediction interpreted four ways"
  "230 12 Material 3 timeline — scrub to any moment in the BOLD trace"
  "265 14 Switch personas: Student / Patient / Clinician / ML Scientist"
  "300 18 Brain tour mode — auto-cycles top regions of activation"
  "330 25 redteamkitchen.com · github.com/AlexiosBluffMara/cortex"
)
captions_gallery=(
  "0   5  Cortex public scan gallery — every completed scan, all four narrations"
  "6   8  Each card: top regions detected, four persona readings, replay link"
  "16  9  Scans land here whether they came from Discord, the WebUI, or the API"
  "27  8  Personas page — Student · Patient · Clinician · ML Scientist"
  "38  8  Tied to real Illinois institutions: ISU · Carle/BroMenn · NW Memorial · RUSH"
  "48  8  redteamkitchen.com · redteamkitchen.com"
)

if [[ "$MODE" == "end_to_end" ]]; then
  IN="$MP4_DIR/cortex_end_to_end.mp4"
  OUT="$MP4_DIR/cortex_end_to_end_captioned.mp4"
  CAPS=("${captions_end_to_end[@]}")
elif [[ "$MODE" == "gallery" ]]; then
  IN="$MP4_DIR/cortex_gallery.mp4"
  OUT="$MP4_DIR/cortex_gallery_captioned.mp4"
  CAPS=("${captions_gallery[@]}")
else
  echo "unknown mode: $MODE"; exit 1
fi

[[ -f "$IN" ]] || { echo "input video not found: $IN"; exit 1; }

# Build the drawtext filter chain
filter=""
for cap in "${CAPS[@]}"; do
  start="${cap%% *}"; rest="${cap#* }"
  dur="${rest%% *}";  text="${rest#* }"
  end=$(awk -v s="$start" -v d="$dur" 'BEGIN{printf "%.2f", s+d}')
  esc_text=$(printf %s "$text" | sed -e 's/:/\\:/g' -e "s/'/\\\\'/g" -e 's/,/\\,/g')
  fontspec=""
  if [[ -n "$FONT" ]]; then fontspec="fontfile='$FONT_FOR_FFMPEG':"; fi
  one="drawtext=${fontspec}text='${esc_text}':x=(w-text_w)/2:y=h-text_h-50:fontsize=22:fontcolor=white:bordercolor=0x000000B0:borderw=4:box=1:boxcolor=0x000000A0:boxborderw=12:enable='between(t,${start},${end})'"
  filter+="${filter:+,}${one}"
done

echo "writing: $OUT"
"$FFMPEG" -y -i "$IN" -vf "$filter" -c:v libx264 -preset fast -crf 22 -movflags +faststart "$OUT" 2>&1 | tail -5
echo
echo "done: $OUT"
ls -lh "$OUT"
