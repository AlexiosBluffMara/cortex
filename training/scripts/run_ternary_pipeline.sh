#!/usr/bin/env bash
# run_ternary_pipeline.sh — end-to-end: bitnetize Gemma → QAT → snap →
# GGUF I2_S → ready to scp to Pi.
#
# Run inside ~/unsloth-env on the 5090 box.

set -euo pipefail
cd "$(dirname "$0")/.."

LOG="logs/ternary-$(date +%Y%m%d-%H%M).log"
mkdir -p logs exports
exec > >(tee -a "$LOG") 2>&1
echo "=== run_ternary_pipeline $(date) ==="

source ~/unsloth-env/.venv/bin/activate

# 1. Sanity self-test: BitLinear swap on Gemma 4 e2b
python scripts/bitnetize.py --model unsloth/gemma-4-E2B-it

# 2. QAT training (long-running)
python scripts/train_ternary_gemma.py \
    --config configs/ternary-gemma-e2b.yaml

# 3. Clone microsoft/BitNet for the converter helper if missing
if [[ ! -d ~/BitNet ]]; then
    git clone --recursive https://github.com/microsoft/BitNet.git ~/BitNet
    pushd ~/BitNet
    pip install -r requirements.txt --quiet
    popd
fi

# 4. Snap + export to bitnet.cpp I2_S GGUF
python scripts/export_ternary_gguf.py \
    --ckpt checkpoints/ternary-gemma-e2b/final \
    --output exports/ternary-gemma-e2b-i2s.gguf \
    --bitnet-repo ~/BitNet

ls -lh exports/ternary-gemma-e2b-i2s.gguf
echo "=== ready to deploy: scp to baby-pi via deploy-pi-models.sh ==="
