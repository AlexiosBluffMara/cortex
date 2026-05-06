#!/usr/bin/env bash
# prep-flipper-sd.sh -- run inside WSL2 Ubuntu after F: is mounted at /mnt/f.
# Populates the Flipper Zero SD card with a curated kit:
#   - directory structure Momentum expects
#   - Lucaslhm IRDB subset (TVs, ACs, projectors, audio receivers, cameras)
#   - UberGuidoZ sub-GHz curated subset
#   - Hak5 BadUSB payloads + Soumit's custom Cortex demo payloads
#   - Empty directories for NFC / RFID captures
#
# Idempotent. Safe to re-run.

set -e
SD=/mnt/f
WORK=/tmp/flipper-prep
LOG=$SD/.prep.log

if [[ ! -d "$SD" ]]; then
    echo "ERROR: $SD is not mounted." >&2
    exit 1
fi

mkdir -p "$WORK"
exec > >(tee -a "$LOG") 2>&1
echo "=== prep-flipper-sd.sh started $(date -u +%FT%TZ) ==="

# 1. Directory structure ------------------------------------------------------
echo "--- creating directory tree ---"
for d in apps apps_data badusb infrared/assets nfc rfid subghz/assets update wallpapers; do
    mkdir -p "$SD/$d"
done

# 2. Lucaslhm Flipper-IRDB (curated subset) ----------------------------------
if [[ ! -d "$WORK/IRDB" ]]; then
    echo "--- cloning Lucaslhm Flipper-IRDB (depth 1) ---"
    git clone --depth=1 https://github.com/Lucaslhm/Flipper-IRDB.git "$WORK/IRDB"
fi
echo "--- copying IRDB subset to SD ---"
# Only the categories we actually use; saves ~80% of the size
for cat in TVs ACs Projectors Audio_Receivers Cameras Soundbars Streaming_Devices Computers; do
    if [[ -d "$WORK/IRDB/$cat" ]]; then
        rsync -a --delete "$WORK/IRDB/$cat/" "$SD/infrared/assets/$cat/"
        echo "  + $cat ($(find "$SD/infrared/assets/$cat" -name '*.ir' | wc -l) remotes)"
    fi
done

# Universal codes (the always-on remotes)
if [[ -d "$WORK/IRDB/_Generic" ]]; then
    rsync -a "$WORK/IRDB/_Generic/" "$SD/infrared/assets/universal/"
fi

# 3. UberGuidoZ sub-GHz subset (Tools + AssetPacks only; avoid the 2 GB main repo) ---
if [[ ! -d "$WORK/UberGuidoZ-subghz" ]]; then
    echo "--- cloning UberGuidoZ Sub-GHz region-unlock + tools ---"
    git clone --depth=1 --filter=blob:none --no-checkout \
        https://github.com/UberGuidoZ/Flipper.git "$WORK/UberGuidoZ-subghz"
    cd "$WORK/UberGuidoZ-subghz"
    git sparse-checkout init --cone
    git sparse-checkout set Sub-GHz Wifi-Devboard Notifications BadUSB
    git checkout HEAD
    cd -
fi
if [[ -d "$WORK/UberGuidoZ-subghz/Sub-GHz" ]]; then
    rsync -a "$WORK/UberGuidoZ-subghz/Sub-GHz/" "$SD/subghz/" \
        --exclude='*.md' --exclude='LICENSE'
fi
if [[ -d "$WORK/UberGuidoZ-subghz/BadUSB" ]]; then
    rsync -a "$WORK/UberGuidoZ-subghz/BadUSB/" "$SD/badusb/uberguidoz/" \
        --exclude='*.md' --exclude='LICENSE'
fi

# 4. Hak5 BadUSB payloads -----------------------------------------------------
if [[ ! -d "$WORK/hak5-payloads" ]]; then
    echo "--- cloning Hak5 USB Rubber Ducky payloads ---"
    git clone --depth=1 https://github.com/hak5/usbrubberducky-payloads.git "$WORK/hak5-payloads"
fi
mkdir -p "$SD/badusb/hak5"
# DuckyScript files only; skip the docs
find "$WORK/hak5-payloads" -name '*.txt' -path '*/payload*' -exec cp {} "$SD/badusb/hak5/" \;
echo "  + $(ls "$SD/badusb/hak5/" 2>/dev/null | wc -l) Hak5 payloads"

# 5. Custom Cortex demo payloads ---------------------------------------------
echo "--- writing custom Cortex / Ascended Base payloads ---"
mkdir -p "$SD/badusb/ascended-base"

cat > "$SD/badusb/ascended-base/start-cortex-demo.txt" <<'PAYLOAD'
REM Ascended Base -- "start the Cortex demo" macro
REM Plug Flipper into Seratonin in BadUSB mode, hit Start, walk away
DEFAULTDELAY 200

REM Open Run dialog
GUI r
DELAY 400

REM Launch the demo orchestrator PowerShell script
STRING powershell -NoProfile -ExecutionPolicy Bypass -Command "& 'D:\cortex\scripts\start-demo.ps1'"
ENTER
DELAY 1500

REM Then open Cortex landing page in Chrome kiosk
GUI r
DELAY 400
STRING chrome --kiosk https://cortex.redteamkitchen.com/
ENTER
PAYLOAD

cat > "$SD/badusb/ascended-base/health-check.txt" <<'PAYLOAD'
REM Ascended Base -- public health probe via Windows Terminal
DEFAULTDELAY 150
GUI r
DELAY 300
STRING wt powershell -NoProfile -Command "foreach ($h in @('redteamkitchen.com','cortex.redteamkitchen.com','mercury.redteamkitchen.com','ollama.redteamkitchen.com','inference.redteamkitchen.com')) { try { $c = (Invoke-WebRequest -Uri https://$h/ -SkipHttpErrorCheck -TimeoutSec 6 -UseBasicParsing).StatusCode; Write-Host \"$c $h\" } catch { Write-Host \"ERR $h\" } } ; Read-Host 'press enter to close'"
ENTER
PAYLOAD

cat > "$SD/badusb/ascended-base/lock-pc.txt" <<'PAYLOAD'
REM Ascended Base -- panic lock the PC (when handing the Flipper to a stranger)
GUI l
PAYLOAD

cat > "$SD/badusb/ascended-base/README.txt" <<'PAYLOAD'
Ascended Base custom Flipper BadUSB payloads.

start-cortex-demo.txt   - one-tap "deploy demo" macro for Seratonin
health-check.txt        - opens Windows Terminal, runs the public-health probe
lock-pc.txt             - emergency Win+L lock screen
PAYLOAD

# 6. Notification / wallpaper packs (small, optional) ------------------------
# Skipping by default to keep prep fast. Uncomment if you want them.
# if [[ -d "$WORK/UberGuidoZ-subghz/Notifications" ]]; then
#     mkdir -p "$SD/dolphin"
#     rsync -a "$WORK/UberGuidoZ-subghz/Notifications/" "$SD/dolphin/notifications/"
# fi

# 7. Verify -------------------------------------------------------------------
echo ""
echo "=== Final SD card layout ==="
if command -v tree >/dev/null 2>&1; then
    tree -L 2 "$SD" 2>/dev/null | head -40
else
    find "$SD" -maxdepth 2 -type d | sort
fi

echo ""
echo "=== Sizes ==="
du -sh "$SD"/* 2>/dev/null | sort -h

echo ""
echo "=== Free space remaining ==="
df -h "$SD" | tail -1

echo ""
echo "=== prep-flipper-sd.sh complete $(date -u +%FT%TZ) ==="
