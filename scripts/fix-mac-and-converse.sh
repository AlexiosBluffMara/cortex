#!/usr/bin/env bash
# fix-mac-and-converse.sh
# Run on Seratonin (Mac). Brings the cluster up, flips MLX to primary,
# and starts the cortex-orchestra daemon that ties the Mac to Hue + Google Home.
#
# Steps:
#   1. caffeinate -di so the Mac doesn't sleep mid-demo
#   2. Verify MLX gemma-4 model is fully on disk (15 GB)
#   3. Load MLX launchd daemon on :8090 + smoke test
#   4. Verify Ollama on :11434
#   5. Install orchestra deps into the cortex-mlx venv
#   6. Auto-discover Hue bridge + register if needed
#   7. Auto-discover Google Cast / Home devices
#   8. Drop orchestra.py + launchd plist + load it
#   9. Tail the live conversation stream

set -uo pipefail
exec > >(tee -a /tmp/fix-mac-and-converse.log) 2>&1
trap 'echo "FAIL at line $LINENO (last cmd: $BASH_COMMAND)"' ERR
echo "=== fix-mac-and-converse $(date) ==="

VENV="$HOME/cortex-mlx"
ORCH_DIR="$HOME/cortex-orchestra"
PLIST="$HOME/Library/LaunchAgents/ai.cortex.orchestra.plist"
SERA_HOST="seratonin"        # Tailscale name of the Windows desktop
MODEL_DIR="$HOME/.cache/huggingface/hub/models--mlx-community--gemma-4-26b-a4b-it-4bit"

# ---------------------------------------------------------------------------
# 1. Keep the Mac awake while the cluster is in service
# ---------------------------------------------------------------------------
pkill -f 'caffeinate -di' 2>/dev/null || true
nohup caffeinate -di > /tmp/caffeinate.log 2>&1 &
echo "caffeinate PID=$!"

# ---------------------------------------------------------------------------
# 2. Verify MLX model is fully on disk
# ---------------------------------------------------------------------------
SIZE_GB=$(du -sg "$MODEL_DIR" 2>/dev/null | awk '{print $1}')
SIZE_GB=${SIZE_GB:-0}
echo "MLX model dir: ${SIZE_GB} GB"
if [[ $SIZE_GB -lt 12 ]]; then
    echo "ERROR: model dir < 12 GB; pull incomplete. Aborting."; exit 1
fi

# ---------------------------------------------------------------------------
# 3. Load MLX launchd daemon (idempotent)
# ---------------------------------------------------------------------------
if ! curl -s -m 2 http://127.0.0.1:8090/v1/models >/dev/null 2>&1; then
    bash /tmp/flip-to-mlx-primary.sh || true
fi
echo -n "MLX :8090 -> "; curl -s -m 5 http://127.0.0.1:8090/v1/models | head -c 200; echo

# ---------------------------------------------------------------------------
# 4. Verify Ollama
# ---------------------------------------------------------------------------
echo -n "Ollama :11434 -> "; curl -s -m 5 http://127.0.0.1:11434/api/version || echo "DOWN"
echo

# ---------------------------------------------------------------------------
# 5. Install orchestra deps
# ---------------------------------------------------------------------------
"$VENV/bin/pip" install -q phue pychromecast aiohttp httpx zeroconf
echo "deps installed"

# ---------------------------------------------------------------------------
# 6. Discover Hue bridge (skips if already authenticated)
# ---------------------------------------------------------------------------
HUE_IP=$(curl -s -m 6 https://discovery.meethue.com/ 2>/dev/null \
    | "$VENV/bin/python" -c "import sys,json
try:
    d=json.load(sys.stdin)
    print(d[0]['internalipaddress']) if d else None
except Exception:
    pass" 2>/dev/null || true)
# Fallback: scan LAN via Philips Hue mDNS-ish hostname
if [[ -z "$HUE_IP" ]]; then
    for ip in $(arp -a 2>/dev/null | awk '{print $2}' | tr -d '()' | grep -E '^192\.168\.0\.'); do
        if curl -s -m 1 "http://$ip/api/0/config" 2>/dev/null | grep -q '"bridgeid"'; then
            HUE_IP=$ip; break
        fi
    done
fi
echo "Hue bridge IP: ${HUE_IP:-<not found>}"

if [[ -n "$HUE_IP" && ! -f "$HOME/.python_hue" ]]; then
    echo ">>> Press the round button on top of the Hue bridge NOW (within 30 s)..."
    for i in {1..30}; do
        if "$VENV/bin/python" -c "from phue import Bridge; Bridge('$HUE_IP').connect()" 2>/dev/null; then
            echo "Hue paired."
            break
        fi
        sleep 1
    done
fi

# ---------------------------------------------------------------------------
# 7. Discover Google Cast / Home devices
# ---------------------------------------------------------------------------
"$VENV/bin/python" - <<'PY'
import pychromecast
casts, browser = pychromecast.get_chromecasts(timeout=8)
for c in casts:
    print("CAST:", c.cast_info.friendly_name, "|", c.cast_info.model_name, "@", c.cast_info.host)
pychromecast.discovery.stop_discovery(browser)
PY

# ---------------------------------------------------------------------------
# 8. Drop orchestra.py + launchd plist
# ---------------------------------------------------------------------------
mkdir -p "$ORCH_DIR"
# Try Tailscale-SSH pull from Seratonin first; fall back to a stub message.
if scp -o StrictHostKeyChecking=accept-new \
       "$SERA_HOST":/mnt/d/cortex/orchestra/orchestra.py \
       "$ORCH_DIR/orchestra.py" 2>/dev/null; then
    echo "orchestra.py pulled from Seratonin"
elif scp -o StrictHostKeyChecking=accept-new \
       "soumitty@$SERA_HOST":/mnt/d/cortex/orchestra/orchestra.py \
       "$ORCH_DIR/orchestra.py" 2>/dev/null; then
    echo "orchestra.py pulled from Seratonin (soumitty)"
else
    echo "WARN: could not scp orchestra.py from $SERA_HOST"
    echo "     paste it manually to $ORCH_DIR/orchestra.py and re-run"
    exit 2
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>ai.cortex.orchestra</string>
<key>ProgramArguments</key><array>
  <string>$VENV/bin/python</string>
  <string>$ORCH_DIR/orchestra.py</string>
</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>/tmp/orchestra.log</string>
<key>StandardErrorPath</key><string>/tmp/orchestra.err</string>
<key>EnvironmentVariables</key><dict>
  <key>PATH</key><string>$VENV/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  <key>PROBE_SEC</key><string>30</string>
  <key>HUE_GROUP</key><string>All Lights</string>
  <key>ANNOUNCE_ON_STARTUP</key><string>1</string>
</dict>
</dict></plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
echo "orchestra launchd loaded"

# ---------------------------------------------------------------------------
# 9. Live stream
# ---------------------------------------------------------------------------
echo ""
echo "=== conversing. ctrl-C to detach (daemon keeps running) ==="
sleep 3
tail -F /tmp/orchestra.log
