#!/usr/bin/env bash
# flip-to-mlx-primary.sh -- run on Seratonin after MLX model pull completes.
# Loads the MLX launchd daemon, verifies port 8090, smoke-tests inference.

set -e

LOG=/tmp/flip-to-mlx.log
exec > >(tee -a "$LOG") 2>&1
echo "=== flip-to-mlx-primary started $(date) ==="

# 1. Verify model is present
MODEL_DIR="$HOME/.cache/huggingface/hub/models--mlx-community--gemma-4-26b-a4b-it-4bit"
if [[ ! -d "$MODEL_DIR" ]]; then
    echo "ERROR: model dir missing: $MODEL_DIR"; exit 1
fi
SIZE_GB=$(du -sg "$MODEL_DIR" | awk '{print $1}')
echo "model dir size: ${SIZE_GB} GB"
if [[ $SIZE_GB -lt 12 ]]; then
    echo "WARN: model dir < 12 GB, pull may not be complete"
fi

# 2. Load the daemon
launchctl unload ~/Library/LaunchAgents/ai.mlx.server.plist 2>/dev/null || true
# Re-write plist with KeepAlive=true now that the model is on disk
cat > ~/Library/LaunchAgents/ai.mlx.server.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>ai.mlx.server</string>
<key>ProgramArguments</key><array>
  <string>/Users/soumitlahiri/cortex-mlx/bin/python</string>
  <string>-m</string><string>mlx_lm.server</string>
  <string>--host</string><string>0.0.0.0</string>
  <string>--port</string><string>8090</string>
  <string>--model</string><string>mlx-community/gemma-4-26b-a4b-it-4bit</string>
</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>/tmp/mlx.log</string>
<key>StandardErrorPath</key><string>/tmp/mlx.err</string>
<key>EnvironmentVariables</key><dict>
  <key>PATH</key><string>/Users/soumitlahiri/cortex-mlx/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
</dict>
</dict></plist>
EOF
launchctl load -w ~/Library/LaunchAgents/ai.mlx.server.plist
echo "MLX daemon loaded"

# 3. Wait for port 8090 to come up
echo "waiting for :8090 ..."
for i in {1..30}; do
    if nc -z -G 2 127.0.0.1 8090 2>/dev/null; then
        echo "port 8090 OPEN after ${i}s"
        break
    fi
    sleep 2
done

# 4. Smoke test (OpenAI-compatible API)
echo "--- MLX smoke test ---"
curl -s -X POST http://127.0.0.1:8090/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"gemma-4-26b-a4b-it-4bit","messages":[{"role":"user","content":"In one sentence, what is the visual cortex?"}],"max_tokens":80,"temperature":0.7}' \
    | head -c 800
echo ""

echo "=== flip-to-mlx-primary complete $(date) ==="
