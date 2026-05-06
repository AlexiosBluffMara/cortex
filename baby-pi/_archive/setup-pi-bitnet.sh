#!/usr/bin/env bash
# setup-pi-bitnet.sh — install bitnet.cpp + native BitNet b1.58 model on the
# Raspberry Pi 5. Run on the Pi after first boot via:
#
#   ssh soumitlahiri@baby-pi 'bash -s' < setup-pi-bitnet.sh
#
# Compiles with -march=armv8.2-a+dotprod -mtune=cortex-a76 for the Pi 5's
# Cortex-A76 cores. Pulls Microsoft's official ternary 2B-4T model.

set -euo pipefail
LOG=/tmp/setup-pi-bitnet.log
exec > >(tee -a "$LOG") 2>&1
echo "=== setup-pi-bitnet started $(date) ==="

# Sanity checks
arch=$(uname -m)
if [[ "$arch" != "aarch64" ]]; then
    echo "ERROR: expected aarch64, got $arch — this script is for Pi 5 only"
    exit 1
fi
free -h | head -2
nproc
cat /proc/cpuinfo | grep -E '(model name|Features)' | head -2

# ---------------------------------------------------------------------
# 1. Build deps
# ---------------------------------------------------------------------
sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential cmake git python3-venv python3-pip \
    libopenblas-dev clang lld

# ---------------------------------------------------------------------
# 2. Clone bitnet.cpp + submodules (it vendors llama.cpp internally)
# ---------------------------------------------------------------------
mkdir -p ~/cortex-pi
cd ~/cortex-pi
if [[ ! -d BitNet ]]; then
    git clone --recursive https://github.com/microsoft/BitNet.git
fi
cd BitNet

# ---------------------------------------------------------------------
# 3. Python deps for the conversion driver
# ---------------------------------------------------------------------
if [[ ! -d .venv ]]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

# ---------------------------------------------------------------------
# 4. Pull the official Microsoft BitNet b1.58 2B-4T model
#    (~1.4 GB I2_S tensors + tokenizer)
# ---------------------------------------------------------------------
HF_REPO="microsoft/BitNet-b1.58-2B-4T-gguf"
LOCAL=~/cortex-pi/models/$HF_REPO
mkdir -p "$LOCAL"
if [[ ! -f "$LOCAL/ggml-model-i2_s.gguf" ]]; then
    pip install huggingface_hub --quiet
    python -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='$HF_REPO', local_dir='$LOCAL',
                  allow_patterns=['*.gguf','*.json','tokenizer*'])
"
fi
ls -lh "$LOCAL"

# ---------------------------------------------------------------------
# 5. Build bitnet.cpp tuned for Cortex-A76
# ---------------------------------------------------------------------
cd ~/cortex-pi/BitNet
export CC=clang
export CXX=clang++
export CFLAGS="-O3 -march=armv8.2-a+dotprod -mtune=cortex-a76 -mcpu=cortex-a76 -funroll-loops"
export CXXFLAGS="$CFLAGS"

# Use the official setup script which picks the right TL kernel for I2_S
python setup_env.py --hf-repo "$HF_REPO" --quant-type i2_s

# Verify the binary exists
test -x build/bin/llama-cli || test -x build/bin/main || \
    (echo "ERROR: bitnet.cpp build did not produce expected binary"; ls -la build/bin/; exit 2)

# ---------------------------------------------------------------------
# 6. Quick smoke benchmark
# ---------------------------------------------------------------------
GGUF=$(find ~/cortex-pi/models -name 'ggml-model-i2_s.gguf' | head -1)
if [[ -z "$GGUF" ]]; then
    GGUF=$(find ~/cortex-pi/models -name '*.gguf' | head -1)
fi
echo ""
echo "--- BitNet b1.58 2B-4T smoke ---"
BIN=$(ls build/bin/llama-cli 2>/dev/null || ls build/bin/main 2>/dev/null)
"$BIN" -m "$GGUF" \
    -p "In one sentence, what is the visual cortex?" \
    -n 80 -t 4 \
    --no-warmup 2>&1 | tail -25

# ---------------------------------------------------------------------
# 7. systemd unit so the bitnet model is always loaded behind a small
#    OpenAI-compatible HTTP API (port 8081)
# ---------------------------------------------------------------------
SERVER_BIN=$(ls build/bin/llama-server 2>/dev/null || true)
if [[ -n "$SERVER_BIN" ]]; then
    sudo tee /etc/systemd/system/cortex-bitnet.service >/dev/null <<EOF
[Unit]
Description=Cortex BitNet b1.58 2B-4T (Microsoft, ternary) — port 8081
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/cortex-pi/BitNet
ExecStart=$SERVER_BIN -m $GGUF --host 0.0.0.0 --port 8081 -t 4 -c 4096
Restart=on-failure
RestartSec=5
Nice=-5
CPUSchedulingPolicy=fifo
CPUSchedulingPriority=10

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now cortex-bitnet
    sleep 2
    systemctl is-active cortex-bitnet && echo "cortex-bitnet active on :8081"
else
    echo "WARN: llama-server not built; skipping systemd unit."
fi

# ---------------------------------------------------------------------
# 8. Performance pinning for the Pi 5 (CPU governor only — keep HDMI audio
#    + GPU mem default since user has an active cooler and wants full I/O)
# ---------------------------------------------------------------------
echo "performance" | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null 2>&1 || true
sudo sysctl -w vm.swappiness=10 >/dev/null
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-cortex-pi.conf >/dev/null

# Persist performance governor across reboots
sudo tee /etc/systemd/system/cortex-cpu-governor.service >/dev/null <<'EOF'
[Unit]
Description=Pin Pi 5 CPUs to performance governor
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor'
RemainAfterExit=true

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable cortex-cpu-governor

# HDMI audio stays on. Active cooler handles thermals.

echo ""
echo "=== setup-pi-bitnet done $(date) ==="
echo ""
echo "API endpoints:"
echo "  http://baby-pi:8081/v1/chat/completions   (BitNet b1.58 2B-4T, ternary)"
echo "  http://baby-pi:11434/api/generate         (Ollama: gemma4-pi:* models)"
