#!/usr/bin/env bash
# Run inside WSL2 Ubuntu on Seratonin to stand up the dashboard.
# Listens on 0.0.0.0:9090 — Pi kiosk + any browser on the LAN can hit it.

set -euo pipefail

ROOT="$HOME/.cortex/dashboard"
SRC="/mnt/d/cortex/dashboard"
mkdir -p "$ROOT"

if [[ ! -d "$ROOT/.venv" ]]; then
    python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/.venv/bin/pip" install --quiet fastapi uvicorn httpx

# systemd user unit
SD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SD_DIR"
cat > "$SD_DIR/cortex-dashboard.service" <<EOF
[Unit]
Description=Cortex live dashboard (port 9090)
After=network-online.target

[Service]
Type=simple
ExecStart=$ROOT/.venv/bin/python $SRC/server.py
Restart=on-failure
RestartSec=4
Environment=PORT=9090
Environment=MERCURY_HOME=/mnt/d/mercury_home
Environment=PI_HOST=baby-pi
Environment=ADGUARD_USER=soumit
Environment=ADGUARD_PASS=ChangeMeNow!

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now cortex-dashboard
sleep 2
systemctl --user --no-pager status cortex-dashboard | head -10
echo ""
echo "Dashboard live: http://seratonin:9090/  (combined view)"
echo "                http://seratonin:9090/mercury  (left monitor)"
echo "                http://seratonin:9090/cortex   (right monitor)"
