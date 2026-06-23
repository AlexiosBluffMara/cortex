#!/usr/bin/env bash
# start-orchestra.sh — finalize the orchestra daemon on Seratonin.
# Assumes ~/cortex-orchestra/orchestra.py already exists.
# Picks Living Room speaker (Google Home Mini) as the primary Cast target.

set -uo pipefail
trap 'echo "FAIL line $LINENO: $BASH_COMMAND"' ERR

VENV="$HOME/cortex-mlx"
ORCH_DIR="$HOME/cortex-orchestra"
PLIST="$HOME/Library/LaunchAgents/ai.cortex.orchestra.plist"

if [[ ! -s "$ORCH_DIR/orchestra.py" ]]; then
    echo "ERROR: $ORCH_DIR/orchestra.py missing"; exit 1
fi
echo "orchestra.py present ($(wc -l < $ORCH_DIR/orchestra.py) lines)"

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
<key>SessionCreate</key><true/>
<key>ProcessType</key><string>Interactive</string>
<key>StandardOutPath</key><string>/tmp/orchestra.log</string>
<key>StandardErrorPath</key><string>/tmp/orchestra.err</string>
<key>EnvironmentVariables</key><dict>
  <key>PATH</key><string>$VENV/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  <key>PROBE_SEC</key><string>30</string>
  <key>HUE_BRIDGE_IP</key><string>192.168.0.134</string>
  <key>HUE_GROUP</key><string>0</string>
  <key>CAST_DEVICE_IP</key><string>192.168.0.232</string>
  <key>CAST_DEVICE</key><string>Living Room speaker</string>
  <key>ANNOUNCE_ON_STARTUP</key><string>1</string>
  <key>PULSE_EVERY_PROBE</key><string>1</string>
</dict>
</dict></plist>
EOF
echo "plist written: $PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
echo "orchestra launchd loaded"
sleep 2

# Show pid + first few log lines
echo "--- launchd state ---"
launchctl list | grep ai.cortex.orchestra || true
echo ""
echo "=== streaming /tmp/orchestra.log (Ctrl-C to detach, daemon keeps running) ==="
sleep 4
tail -F /tmp/orchestra.log
