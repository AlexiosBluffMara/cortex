#!/usr/bin/env bash
# install.sh — set up cortex-lights on Seratonin WSL2.
#
# Creates a venv at ~/.cortex/lights-env, installs phue, drops a systemd
# user unit so the daemon stays alive across logouts/reboots.

set -euo pipefail

ROOT="$HOME/.cortex"
ENV="$ROOT/lights-env"
SRC="/mnt/d/cortex/lights"

mkdir -p "$ROOT"
if [[ ! -f "$ENV/bin/python" ]]; then
    rm -rf "$ENV"
    python3 -m venv "$ENV"
fi
"$ENV/bin/pip" install --upgrade pip --quiet
"$ENV/bin/pip" install phue --quiet

# Install the Hue username file from Seratonin if we haven't paired yet on this box.
if [[ ! -f "$HOME/.python_hue" ]]; then
    if scp -o StrictHostKeyChecking=accept-new \
        soumitlahiri@seratonin:~/.python_hue \
        "$HOME/.python_hue" 2>/dev/null; then
        echo "borrowed Hue creds from seratonin"
    else
        echo "WARN: no Hue creds. Press the bridge button, then:"
        echo "  $ENV/bin/python -c 'from phue import Bridge; Bridge(\"192.168.0.134\").connect()'"
    fi
fi

# Initialize state file
[[ -f "$ROOT/lights-state.json" ]] || cat > "$ROOT/lights-state.json" <<'EOF'
{"claude_active": false, "claude_idle": false, "mercury_active": false, "usage_percent": 0, "last_update": ""}
EOF

# systemd user unit
SD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SD_DIR"
cat > "$SD_DIR/cortex-lights.service" <<EOF
[Unit]
Description=Cortex lights daemon (Hue, state-driven)
After=network.target

[Service]
Type=simple
ExecStart=$ENV/bin/python $SRC/lights.py
Restart=on-failure
RestartSec=3
Environment=HUE_BRIDGE_IP=192.168.0.134
Environment=HUE_GROUP=0
Environment=LIGHTS_STATE=$ROOT/lights-state.json

[Install]
WantedBy=default.target
EOF

cat > "$SD_DIR/cortex-lights-usage.service" <<EOF
[Unit]
Description=Cortex lights usage poller (Claude rate-limit window)
After=cortex-lights.service

[Service]
Type=simple
ExecStart=$ENV/bin/python $SRC/usage-poller.py --interval 300
Restart=on-failure
RestartSec=10
Environment=LIGHTS_STATE=$ROOT/lights-state.json
Environment=STATE_UPDATE=$SRC/state-update.sh
Environment=TOKEN_BUDGET_5H=1500000
Environment=CLAUDE_HOME=/mnt/c/Users/soumi/.claude

[Install]
WantedBy=default.target
EOF

# Enable lingering so user services survive logout (requires sudo)
if ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
    sudo loginctl enable-linger "$USER" 2>/dev/null || true
fi

systemctl --user daemon-reload
systemctl --user enable --now cortex-lights.service
systemctl --user enable --now cortex-lights-usage.service
sleep 2
systemctl --user --no-pager status cortex-lights.service | head -10
echo "---"
systemctl --user --no-pager status cortex-lights-usage.service | head -10
echo ""
echo "state file: $ROOT/lights-state.json"
echo "to test: bash $SRC/state-update.sh claude-prompt-submit"
