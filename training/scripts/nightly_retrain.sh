#!/usr/bin/env bash
# nightly_retrain.sh — full training cycle, designed to be invoked by
# Mercury's cron scheduler (cron/jobs.py).
#
# Steps:
#   1. extract trajectories from Mercury session dirs
#   2. abort if dataset has fewer than MIN_NEW examples (avoid retraining on stale data)
#   3. train a LoRA on top of base Gemma 4 e4b
#   4. export GGUF + register in Ollama (Seratonin)
#   5. export MLX, scp to Big Apple, reload mlx_lm.server
#   6. run smoke probes against both backends and log results

set -euo pipefail
BASE="/mnt/d/cortex/training"
cd "$BASE"

source ~/unsloth-env/.venv/bin/activate

MIN_NEW=${MIN_NEW:-25}
DATE=$(date +%Y%m%d)
LOG="$BASE/logs/retrain-${DATE}.log"
mkdir -p "$BASE/logs"
exec > >(tee -a "$LOG") 2>&1
echo "=== nightly_retrain $(date) ==="

# 1. extract
python scripts/extract_trajectories.py \
    --src ~/.mercury/sessions /mnt/d/mercury/runs ~/gemma4-pipeline/runs \
    --out "datasets/mercury-${DATE}.jsonl"

N=$(wc -l < "datasets/mercury-${DATE}.jsonl" || echo 0)
if [[ $N -lt $MIN_NEW ]]; then
    echo "skip: only $N examples (< $MIN_NEW); not retraining"
    exit 0
fi
echo "examples=$N"

# 2. train
python scripts/train_lora.py \
    --config configs/mercury-gemma4-e4b-lora.yaml \
    --output-suffix "$DATE"

# 3. export
python scripts/export_gguf.py \
    --ckpt "checkpoints/mercury-gemma4-e4b-LATEST" \
    --register

python scripts/export_mlx.py \
    --ckpt "checkpoints/mercury-gemma4-e4b-LATEST"

# 4. deploy
bash scripts/deploy.sh "$DATE"

echo "=== nightly_retrain done $(date) ==="
