#!/usr/bin/env bash
# setup-baby-pi-llm.sh — install Ollama + gemma4:e2b + smart-home relay on RPi 5.
# Run on the Pi via Tailscale SSH:
#   ssh soumitlahiri@baby-pi 'bash -s' < setup-baby-pi-llm.sh

set -euo pipefail
LOG=/tmp/setup-baby-pi-llm.log
exec > >(tee -a "$LOG") 2>&1
echo "=== setup-baby-pi-llm started $(date) ==="

uname -a
free -h | head -2
df -h / | head -2

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    curl wget jq git python3-venv python3-pip \
    build-essential libopenblas-dev pkg-config

# ---------------------------------------------------------------------------
# 2. Ollama (arm64 build)
# ---------------------------------------------------------------------------
if ! command -v ollama >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sh
fi

# Bind Ollama to all interfaces so the Mac/Seratonin can probe it
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<EOF
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
sleep 3
curl -s http://127.0.0.1:11434/api/version || echo "WARN: ollama not responding"

# ---------------------------------------------------------------------------
# 3. Pull gemma4:e2b q4_k_m (the heavy quant for Pi)
# ---------------------------------------------------------------------------
ollama pull gemma4:e2b
ollama list | head -5

# ---------------------------------------------------------------------------
# 4. Pre-warm so the orchestra probe gets a hot model immediately
# ---------------------------------------------------------------------------
curl -s -m 60 -X POST http://127.0.0.1:11434/api/generate \
    -d '{"model":"gemma4:e2b","prompt":"hi","stream":false,"options":{"num_predict":4}}' \
    | head -c 300
echo

# ---------------------------------------------------------------------------
# 5. Smart-home relay venv + service
# ---------------------------------------------------------------------------
mkdir -p ~/cortex-pi
python3 -m venv ~/cortex-pi/.venv
~/cortex-pi/.venv/bin/pip install --upgrade pip
~/cortex-pi/.venv/bin/pip install fastapi uvicorn httpx phue pychromecast pyyaml

# Drop the relay code (will scp from Seratonin separately if not present)
if [[ ! -f ~/cortex-pi/relay.py ]]; then
    echo "WARN: ~/cortex-pi/relay.py missing — scp it from Seratonin:"
    echo "    scp /mnt/d/cortex/baby-pi/relay.py soumitlahiri@baby-pi:~/cortex-pi/relay.py"
fi

# systemd unit
sudo tee /etc/systemd/system/cortex-pi-relay.service >/dev/null <<EOF
[Unit]
Description=Cortex Pi smart-home relay (Hue + Cast + Gemma)
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/cortex-pi
ExecStart=$HOME/cortex-pi/.venv/bin/python $HOME/cortex-pi/relay.py
Restart=on-failure
RestartSec=5
Environment="HUE_BRIDGE_IP=192.168.0.134"
Environment="OLLAMA_URL=http://127.0.0.1:11434"
Environment="MODEL=gemma4:e2b"

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable cortex-pi-relay 2>/dev/null || true
sudo systemctl start cortex-pi-relay 2>/dev/null || true

# ---------------------------------------------------------------------------
# 6. Open firewall ports for LAN access
# ---------------------------------------------------------------------------
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow 11434/tcp comment 'ollama' || true
    sudo ufw allow 8000/tcp  comment 'cortex-pi-relay' || true
fi

echo ""
echo "=== inventory ==="
ollama list
echo ""
systemctl is-active ollama && echo "ollama: active"
systemctl is-active cortex-pi-relay 2>/dev/null && echo "cortex-pi-relay: active" || echo "cortex-pi-relay: NOT active (relay.py missing?)"
echo ""
ip -4 addr show | grep inet | head -5
echo ""
echo "=== setup-baby-pi-llm done $(date) ==="
