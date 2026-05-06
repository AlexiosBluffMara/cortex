#!/usr/bin/env bash
# deploy-pi-models.sh — push quantized Gemma GGUFs to Baby Pi's Ollama.
# Run on Seratonin/WSL2 once the Pi is reachable on Tailscale.

set -euo pipefail
ROOT="/mnt/d/cortex/baby-pi"
EXPORTS="$ROOT/exports"
PI_HOST="${PI_HOST:-baby-pi}"
PI_USER="${PI_USER:-soumitlahiri}"

# Map: ollama_tag <- gguf_file_glob
declare -A MODELS=(
    ["gemma4-pi:q4"]="gemma-4-e2b-Q4_K_M.gguf"
    ["gemma4-pi:q2"]="gemma-4-e2b-Q2_K.gguf"
    ["gemma4-pi:t1"]="gemma-4-e2b-IQ1_M.gguf"
    ["gemma4-pi:t0"]="gemma-4-e2b-IQ1_S.gguf"
)

# Our QAT-trained TRUE ternary Gemma — different deploy path: copy raw GGUF
# to ~/cortex-pi/models/, no Ollama wrap (bitnet.cpp serves it on :8082).
TERNARY_TRAINED="/mnt/d/cortex/training/exports/ternary-gemma-e2b-i2s.gguf"

echo "=== deploy to $PI_USER@$PI_HOST ==="
ssh "$PI_USER@$PI_HOST" 'mkdir -p ~/cortex-pi/models'

for tag in "${!MODELS[@]}"; do
    file="$EXPORTS/${MODELS[$tag]}"
    [[ -f "$file" ]] || { echo "skip $tag: $file missing"; continue; }
    echo "--- $tag <- $(basename "$file") ($(du -h "$file" | cut -f1)) ---"
    scp -C "$file" "$PI_USER@$PI_HOST:~/cortex-pi/models/$(basename "$file")"

    # Build a Modelfile and ollama-create remotely
    ssh "$PI_USER@$PI_HOST" "cat > ~/cortex-pi/models/Modelfile.${tag//:/-}" <<EOF
FROM ./$(basename "$file")
TEMPLATE """{{ if .System }}<start_of_turn>system
{{ .System }}<end_of_turn>
{{ end }}{{ range .Messages }}<start_of_turn>{{ .Role }}
{{ .Content }}<end_of_turn>
{{ end }}<start_of_turn>model
"""
PARAMETER stop "<end_of_turn>"
PARAMETER num_ctx 4096
EOF
    ssh "$PI_USER@$PI_HOST" "cd ~/cortex-pi/models && ollama create $tag -f Modelfile.${tag//:/-}"
done

# Smoke test each
echo ""
echo "=== smoke ==="
for tag in "${!MODELS[@]}"; do
    [[ -f "$EXPORTS/${MODELS[$tag]}" ]] || continue
    t0=$(date +%s%N)
    out=$(ssh "$PI_USER@$PI_HOST" "curl -s -m 60 http://127.0.0.1:11434/api/generate \
        -d '{\"model\":\"$tag\",\"prompt\":\"In one sentence, what is the capital of France?\",\"stream\":false,\"options\":{\"num_predict\":40}}'" \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get(\"response\",\"\")[:120], \"|\", d.get(\"eval_count\",0), \"toks in\", d.get(\"eval_duration\",0)/1e9, \"s\")')
    t1=$(date +%s%N)
    dt=$(( (t1 - t0) / 1000000 ))
    printf "  %-15s %7dms  %s\n" "$tag" "$dt" "$out"
done

# --- 2. True-ternary QAT-trained Gemma (bitnet.cpp serves it on :8082) ---
if [[ -f "$TERNARY_TRAINED" ]]; then
    echo ""
    echo "--- ternary Gemma (QAT-trained) -> bitnet.cpp on Pi ---"
    ssh "$PI_USER@$PI_HOST" 'mkdir -p ~/cortex-pi/models/ternary-gemma'
    scp -C "$TERNARY_TRAINED" "$PI_USER@$PI_HOST:~/cortex-pi/models/ternary-gemma/ternary-gemma-e2b-i2s.gguf"
    # Drop a second bitnet-cpp llama-server unit on :8082 for our trained model
    ssh "$PI_USER@$PI_HOST" 'bash -s' <<'PIEOF'
SERVER=$(ls ~/cortex-pi/BitNet/build/bin/llama-server 2>/dev/null || true)
GGUF=~/cortex-pi/models/ternary-gemma/ternary-gemma-e2b-i2s.gguf
if [[ -n "$SERVER" && -f "$GGUF" ]]; then
sudo tee /etc/systemd/system/cortex-ternary-gemma.service >/dev/null <<EOF
[Unit]
Description=Cortex QAT-trained ternary Gemma 4 e2b — port 8082
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/cortex-pi/BitNet
ExecStart=$SERVER -m $GGUF --host 0.0.0.0 --port 8082 -t 4 -c 4096
Restart=on-failure
RestartSec=5
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now cortex-ternary-gemma
fi
PIEOF
else
    echo "skip ternary-gemma deploy: $TERNARY_TRAINED not built yet (QAT training pending)"
fi

echo "=== deploy done ==="
