#!/usr/bin/env bash
# up-bigapple.sh — start the Big Apple (M4 Max / macOS) stack.
# Big Apple cannot run TRIBE v2 (CUDA-only). It runs:
#   - Ollama (already a launchd service)
#   - Inference router (port 8766) — backups Seratonin's
#   - Mercury gateway + dashboard (port 9119)
#   - Cortex narration-only backend (port 8773) — text/image scans only, no TRIBE
#
# Pre-reqs (run once via fleet/bootstrap-bigapple.sh):
#   - /opt/homebrew/bin/ollama with gemma4:e4b/26b/31b pulled
#   - /opt/homebrew/bin/uv (for fast venv mgmt)
#   - ~/cortex (git clone of repo)
#   - ~/.hermes/.env (mirrored from Seratonin)
#   - ~/cortex/.venv (uv venv with mercury + cortex installed)
set -euo pipefail

REPO=${CORTEX_REPO:-$HOME/cortex}
MERC=${MERCURY_REPO:-$HOME/mercury}
LOGS=${LOGS:-$HOME/.ascended-base/logs}
mkdir -p "$LOGS"

# Make sure homebrew is on PATH (non-interactive ssh shells lose it)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

# Load secrets
if [[ -f "$HOME/.hermes/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$HOME/.hermes/.env"
  set +a
fi

# Confirm Ollama is alive
if ! curl -sf --max-time 3 http://localhost:11434/api/tags >/dev/null; then
  echo "[up-bigapple] starting Ollama…"
  open -a Ollama || true
  sleep 4
fi

# 1. Inference router
export OLLAMA_BACKENDS="${OLLAMA_BACKENDS:-http://localhost:11434,http://100.98.19.87:11434}"
export ROUTER_PORT="8766"
nohup "$REPO/.venv/bin/python" -m uvicorn inference_router.server:app \
  --host 0.0.0.0 --port 8766 --log-level info \
  > "$LOGS/cortex_router.log" 2>&1 &
echo "[up-bigapple] router PID=$!"

# Wait for router
for i in 1 2 3 4 5; do
  curl -sf --max-time 2 http://localhost:8766/healthz >/dev/null && break
  sleep 2
done

# 2. Cortex narration-only backend (no TRIBE on Mac)
#    Hands text/image scans to Gemma directly. Video scans return a polite error.
export OLLAMA_URL="http://localhost:8766"
export MODEL_FAST="gemma4:e4b"
export MODEL_DEEP="gemma4:26b"
export MODEL_EXPERT="gemma4:31b"
export PYTHONDONTWRITEBYTECODE=1
export CORTEX_DISABLE_TRIBE=1   # backend reads this to skip CUDA paths
nohup "$REPO/.venv/bin/python" -m uvicorn webapp.server:app \
  --host 0.0.0.0 --port 8773 --log-level info \
  > "$LOGS/cortex_8773.log" 2>&1 &
echo "[up-bigapple] cortex backend PID=$!"

# 3. Mercury gateway (Discord)
nohup "$REPO/.venv/bin/mercury" gateway > "$LOGS/mercury_gateway.log" 2>&1 &
echo "[up-bigapple] mercury gateway PID=$!"
sleep 4

# 4. Mercury dashboard
nohup "$REPO/.venv/bin/mercury" dashboard --host 0.0.0.0 --port 9119 --insecure \
  > "$LOGS/mercury_dashboard.log" 2>&1 &
echo "[up-bigapple] mercury dashboard PID=$!"

echo
echo "Big Apple stack starting. Run 'bash fleet/status.sh' on either node to verify."
echo "Local URLs:"
echo "  Cortex:        http://localhost:8773/api/health"
echo "  Router:        http://localhost:8766/healthz"
echo "  Mercury web:   http://localhost:9119/"
echo "Tailnet URL:     http://big-apple:9119/  (or via funnel if enabled)"
