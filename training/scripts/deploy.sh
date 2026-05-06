#!/usr/bin/env bash
# deploy.sh — push exported artifacts to Seratonin Ollama + Big Apple MLX.
# Run from /mnt/d/cortex/training inside WSL2.

set -euo pipefail
cd "$(dirname "$0")/.."

DATE=${1:-$(date +%Y%m%d)}
GGUF="exports/mercury-gemma4-e4b-${DATE}.gguf"
MLX_DIR="exports/mercury-gemma4-e4b-${DATE}-mlx"
MODELFILE="exports/Modelfile.mercury-gemma4-e4b-${DATE}"
MAC_HOST="big-apple"
MAC_USER="soumitlahiri"
MAC_DST="~/.cache/huggingface/hub/models--mercury--gemma-4-e4b-${DATE}"

echo "=== deploying date=${DATE} ==="

# 1. Local Ollama (Seratonin)
if [[ -f "$GGUF" && -f "$MODELFILE" ]]; then
    echo "--- ollama create mercury:e4b ---"
    ollama create "mercury:e4b" -f "$MODELFILE"
    echo "--- ollama create mercury:e4b-${DATE} (date-tagged copy) ---"
    ollama create "mercury:e4b-${DATE}" -f "$MODELFILE"
else
    echo "skip Ollama deploy: ${GGUF} or ${MODELFILE} missing"
fi

# 2. Big Apple MLX
if [[ -d "$MLX_DIR" ]]; then
    echo "--- scp ${MLX_DIR} -> ${MAC_HOST}:${MAC_DST} ---"
    ssh "${MAC_USER}@${MAC_HOST}" "mkdir -p ${MAC_DST}"
    scp -r "$MLX_DIR"/* "${MAC_USER}@${MAC_HOST}:${MAC_DST}/"
    echo "--- big-apple: relaunching ai.mlx.server with new model ---"
    ssh "${MAC_USER}@${MAC_HOST}" "
        sed -i.bak 's|--model</string><string>[^<]*|--model</string><string>${MAC_DST/#~/$HOME}|' \
            ~/Library/LaunchAgents/ai.mlx.server.plist || true
        launchctl unload ~/Library/LaunchAgents/ai.mlx.server.plist 2>/dev/null || true
        launchctl load -w ~/Library/LaunchAgents/ai.mlx.server.plist
    "
else
    echo "skip MLX deploy: ${MLX_DIR} missing"
fi

# 3. Smoke
echo "--- smoke: Seratonin Ollama mercury:e4b ---"
curl -s -m 30 -X POST http://localhost:11434/api/generate \
    -d "{\"model\":\"mercury:e4b\",\"prompt\":\"In one sentence, who are you?\",\"stream\":false,\"options\":{\"num_predict\":40}}" \
    | head -c 400
echo
echo "--- smoke: Big Apple MLX :8090 ---"
curl -s -m 30 -X POST http://big-apple:8090/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"mercury-gemma4-e4b-${DATE}\",\"messages\":[{\"role\":\"user\",\"content\":\"In one sentence, who are you?\"}],\"max_tokens\":40}" \
    | head -c 400
echo

echo "=== deploy done ==="
