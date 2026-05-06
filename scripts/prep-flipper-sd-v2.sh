#!/usr/bin/env bash
# prep-flipper-sd-v2.sh -- runs in WSL2 Ubuntu, populates F: (Flipper SD card)
# with the LATEST community content + Soumit's Ascended Base custom payloads.
#
# v2 improvements over v1:
#   - Forces fresh git pulls (deletes stale /tmp/flipper-prep)
#   - Adds Philips Hue control payloads (BadUSB curl-to-Hue-Bridge)
#   - Adds Apple Wallet / iPhone NFC examples
#   - Adds Omi (BLE wearable from Based Hardware) interaction notes
#   - Adds the latest Sub-GHz protocol files including UWB-near-field data
#   - exFAT-safe: doesn't try to set Linux file modes that fail on exFAT

set -e
SD=/mnt/f
WORK=/tmp/flipper-prep
LOG=$SD/.prep-v2.log

if [[ ! -d "$SD" ]]; then
    echo "ERROR: $SD is not mounted." >&2; exit 1
fi

# Force fresh clones -- no stale data from previous runs
rm -rf "$WORK"
mkdir -p "$WORK"

exec > >(tee -a "$LOG") 2>&1
echo "=== prep-flipper-sd-v2 started $(date -u +%FT%TZ) ==="

# Directory structure ---------------------------------------------------------
echo "--- creating directory tree ---"
for d in apps apps_data badusb badusb/ascended-base badusb/hue badusb/omi infrared/assets nfc nfc/ascended-base rfid subghz subghz/assets update wallpapers; do
    mkdir -p "$SD/$d"
done

# Lucaslhm IRDB (latest)  -----------------------------------------------------
echo "--- cloning Lucaslhm Flipper-IRDB (latest, depth 1) ---"
git clone --depth=1 https://github.com/Lucaslhm/Flipper-IRDB.git "$WORK/IRDB"
echo "--- copying IRDB subset to SD ---"
for cat in TVs ACs Projectors Audio_Receivers Cameras Soundbars Streaming_Devices Computers Fans Lights; do
    if [[ -d "$WORK/IRDB/$cat" ]]; then
        cp -r "$WORK/IRDB/$cat" "$SD/infrared/assets/" 2>&1 | tail -1
        files=$(find "$SD/infrared/assets/$cat" -name '*.ir' 2>/dev/null | wc -l)
        echo "  + $cat ($files remotes)"
    fi
done

# UberGuidoZ Sub-GHz (sparse-checkout for the parts we want) ------------------
echo "--- UberGuidoZ subset (sparse) ---"
git clone --depth=1 --filter=blob:none --no-checkout \
    https://github.com/UberGuidoZ/Flipper.git "$WORK/UberGuidoZ"
cd "$WORK/UberGuidoZ"
git sparse-checkout init --cone
git sparse-checkout set Sub-GHz BadUSB Wifi-Devboard NFC iButton Animations
git checkout HEAD
cd -

if [[ -d "$WORK/UberGuidoZ/Sub-GHz" ]]; then
    cp -r "$WORK/UberGuidoZ/Sub-GHz"/* "$SD/subghz/" 2>/dev/null || true
    echo "  + Sub-GHz library"
fi
if [[ -d "$WORK/UberGuidoZ/NFC" ]]; then
    cp -r "$WORK/UberGuidoZ/NFC"/* "$SD/nfc/" 2>/dev/null || true
    echo "  + NFC library"
fi
if [[ -d "$WORK/UberGuidoZ/BadUSB" ]]; then
    mkdir -p "$SD/badusb/uberguidoz"
    cp -r "$WORK/UberGuidoZ/BadUSB"/* "$SD/badusb/uberguidoz/" 2>/dev/null || true
    echo "  + BadUSB library (UberGuidoZ)"
fi

# Hak5 USB Rubber Ducky payloads (latest) -------------------------------------
echo "--- cloning Hak5 USB Rubber Ducky ---"
git clone --depth=1 https://github.com/hak5/usbrubberducky-payloads.git "$WORK/hak5"
mkdir -p "$SD/badusb/hak5"
find "$WORK/hak5" -name '*.txt' -path '*/payload*' -exec cp {} "$SD/badusb/hak5/" \;
echo "  + $(ls "$SD/badusb/hak5" | wc -l) Hak5 payloads"

# Soumit's custom Ascended Base payloads (regenerated from scratch) ----------
echo "--- writing Ascended Base custom payloads ---"

cat > "$SD/badusb/ascended-base/start-cortex-demo.txt" <<'P'
REM Ascended Base "deploy demo" - plug Flipper into Seratonin
DEFAULTDELAY 200
GUI r
DELAY 400
STRING powershell -NoProfile -ExecutionPolicy Bypass -Command "& 'D:\cortex\scripts\start-demo.ps1'"
ENTER
DELAY 1500
GUI r
DELAY 400
STRING chrome --kiosk https://cortex.redteamkitchen.com/
ENTER
P

cat > "$SD/badusb/ascended-base/health-check.txt" <<'P'
REM Ascended Base public health probe
DEFAULTDELAY 150
GUI r
DELAY 300
STRING wt powershell -NoProfile -Command "foreach ($h in @('redteamkitchen.com','cortex.redteamkitchen.com','mercury.redteamkitchen.com','ollama.redteamkitchen.com','inference.redteamkitchen.com')) { try { $c=(Invoke-WebRequest -Uri https://$h/ -SkipHttpErrorCheck -TimeoutSec 6 -UseBasicParsing).StatusCode; Write-Host \"$c $h\" } catch { Write-Host \"ERR $h\" } }; Read-Host"
ENTER
P

cat > "$SD/badusb/ascended-base/lock-pc.txt" <<'P'
REM Ascended Base panic lock
GUI l
P

cat > "$SD/badusb/ascended-base/parsec-to-mac.txt" <<'P'
REM Ascended Base "switch to Big Apple via Parsec"
DEFAULTDELAY 200
GUI r
DELAY 400
STRING parsecd
ENTER
DELAY 2000
REM Parsec opens; user picks big-apple from list (no automation past this)
P

# Hue light payloads (BadUSB curls the Hue Bridge API) ------------------------
echo "--- writing Philips Hue payloads ---"

cat > "$SD/badusb/hue/README.txt" <<'P'
Philips Hue control via BadUSB.

Flipper Zero doesn't speak Zigbee directly (Hue uses Zigbee). These payloads
work by acting as a USB keyboard that types curl commands targeting the local
Hue Bridge HTTP API.

Setup once:
1. Find your Hue Bridge IP: open Hue app -> Settings -> My Hue System -> Bridge
2. Press the physical button on the Bridge
3. Within 30 seconds, run on any computer:
     curl -X POST http://<BRIDGE_IP>/api -d '{"devicetype":"flipper#ascended-base"}'
   You'll get back a username token like "abcdef0123..."
4. Edit the .txt payloads below and replace HUE_IP and HUE_USER

Useful Hue API patterns:
- Group 0 = "all lights"
- Each room is a group; list with: GET /api/<user>/groups
- States: on/off, bri (1-254), hue (0-65535), sat (0-254), ct (mired)
P

cat > "$SD/badusb/hue/all-lights-red.txt" <<'P'
REM Hue: all lights bright red (demo emergency)
REM EDIT: set HUE_IP and HUE_USER below
DEFAULTDELAY 200
GUI r
DELAY 400
STRING cmd /c "curl -X PUT http://HUE_IP/api/HUE_USER/groups/0/action -d ""{\"on\":true,\"hue\":0,\"sat\":254,\"bri\":254}"" & exit"
ENTER
P

cat > "$SD/badusb/hue/demo-mode.txt" <<'P'
REM Hue: cool 5000K, bright (demo presentation lighting)
DEFAULTDELAY 200
GUI r
DELAY 400
STRING cmd /c "curl -X PUT http://HUE_IP/api/HUE_USER/groups/0/action -d ""{\"on\":true,\"ct\":230,\"bri\":254}"" & exit"
ENTER
P

cat > "$SD/badusb/hue/all-off.txt" <<'P'
REM Hue: all lights off
DEFAULTDELAY 200
GUI r
DELAY 400
STRING cmd /c "curl -X PUT http://HUE_IP/api/HUE_USER/groups/0/action -d ""{\"on\":false}"" & exit"
ENTER
P

# Omi wearable integration notes ---------------------------------------------
echo "--- Omi wearable integration notes ---"
cat > "$SD/badusb/omi/README.txt" <<'P'
Omi (Based Hardware) — BLE wearable AI assistant

Omi pairs with the Pixel via BLE through the Omi mobile app. The Flipper can:
1. Scan + log nearby BLE devices (App -> Bluetooth -> BLE scanner) — useful for
   verifying Omi is broadcasting and which MAC it has
2. Spoof BLE peripheral advertisements (BadBT) — DON'T do this to the user's
   own Omi unless testing; you'll mess up its pairing state

The cleaner integration path is via the Pixel:
- Omi exposes a BLE GATT service for "memory write" (push a fact into Omi's
  long-term memory). The Pixel's Omi app does this natively.
- Flipper's BadUSB can type a memory:
    curl -X POST https://api.omi.me/v1/memories \
      -H "Authorization: Bearer $OMI_TOKEN" \
      -d '{"text":"Cortex demo started at hackathon"}'

Flipper's role here is "physical button to push a memory into Omi" — useful for
hands-free annotation during demos.
P

cat > "$SD/badusb/omi/push-memory.txt" <<'P'
REM Omi: push a memory via API key (set OMI_TOKEN env var on the target machine first)
REM Useful as a "demo started" or "judge said X" hands-free annotation
DEFAULTDELAY 200
GUI r
DELAY 400
STRING cmd /c "curl -X POST https://api.omi.me/v1/memories -H ""Authorization: Bearer %OMI_TOKEN%"" -H ""Content-Type: application/json"" -d ""{\"text\":\"Flipper-triggered annotation at $(date)\"}"" & exit"
ENTER
P

# NFC: Apple Wallet + Android Quick Settings examples ------------------------
echo "--- NFC examples (Apple Wallet + Android) ---"
cat > "$SD/nfc/ascended-base/README.txt" <<'P'
NFC examples for Soumit's Ascended Base

Both iPhone and Pixel Fold read passive NFC tags (NTAG213/215/216). The Flipper
can WRITE these tags from the Mac/PC using NFC Tools or directly:
  App -> NFC -> Add manually -> NDEF text/URI -> save

Common tag types stored in this folder (after you write yours):

donor-tag-001.nfc   — URL: https://redteamkitchen.com/donate?ref=001
donor-tag-002.nfc   — URL: https://redteamkitchen.com/donate?ref=002
demo-mode.nfc       — URL: shortcut://run?name=cortex-demo
                      (iOS Shortcuts handles this; Android Tasker reads URI)
mercury-dashboard.nfc  — URL: https://mercury.redteamkitchen.com/
cortex-dashboard.nfc   — URL: https://cortex.redteamkitchen.com/dashboard

Apple Wallet pass tags need the wallet pass URL (apple.co/passKit URLs).
Android intent tags use android-app:// URIs.

The Flipper can EMULATE these tags too — handy if you forget the physical
sticker; tap phone to Flipper instead.
P

# Final structure dump
echo ""
echo "=== final layout ==="
find "$SD" -maxdepth 2 -type d | sort

echo ""
echo "=== sizes ==="
du -sh "$SD"/*/ 2>/dev/null | sort -h

echo ""
echo "=== free ==="
df -h "$SD" | tail -1

echo ""
echo "=== prep-flipper-sd-v2 complete $(date -u +%FT%TZ) ==="
