#!/usr/bin/env bash
# deploy.sh — push exported artifacts to Seratonin Ollama.
# Run from /mnt/d/cortex/training inside WSL2.

set -euo pipefail
cd "$(dirname "$0")/.."

DATE=${1:-$(date +%Y%m%d)}
GGUF="exports/mercury-gemma4-e4b-${DATE}.gguf"
MODELFILE="exports/Modelfile.mercury-gemma4-e4b-${DATE}"

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

# 2. Smoke
echo "--- smoke: Seratonin Ollama mercury:e4b ---"
curl -s -m 30 -X POST http://localhost:11434/api/generate \
    -d "{\"model\":\"mercury:e4b\",\"prompt\":\"In one sentence, who are you?\",\"stream\":false,\"options\":{\"num_predict\":40}}" \
    | head -c 400
echo

echo "=== deploy done ==="
