#!/usr/bin/env bash
# failover-to-seratonin.sh — gaming over, restore primary on Seratonin.
# Re-add Seratonin's local Ollama as primary in the router pool.
# Stop Big Apple's services (router, Mercury, Cortex narration backend).
# Restart Mercury locally on Seratonin.
set -euo pipefail

BIGAPPLE_HOST=${BIGAPPLE_HOST:-big-apple}
BIGAPPLE_TS=${BIGAPPLE_TS:-100.93.240.52}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "════════════════════════════════════════════════════════════"
echo "  Failover → Seratonin (gaming over)"
echo "════════════════════════════════════════════════════════════"

# 1. Stop Big Apple stack
echo "[1/4] Stopping Big Apple stack..."
ssh "$BIGAPPLE_HOST" "bash -lc 'cd ~/cortex && bash fleet/down-bigapple.sh'" 2>&1 | sed 's/^/    /' || true

# 2. Restart Seratonin router with both backends in pool
echo "[2/4] Restarting Seratonin router (both backends in pool)..."
PIDS=$(netstat -ano 2>/dev/null | grep ':8766 ' | grep LISTENING | awk '{print $5}' | sort -u)
for p in $PIDS; do taskkill /PID $p /F /T 2>/dev/null || true; done
sleep 2
cd "$ROOT"
export OLLAMA_BACKENDS="http://localhost:11434,http://${BIGAPPLE_TS}:11434"
export ROUTER_PORT="8766"
nohup "C:/Users/soumi/cortex/.venv/Scripts/python.exe" -m uvicorn inference_router.server:app \
  --host 0.0.0.0 --port 8766 --log-level info > /c/Temp/logs/cortex_router.log 2>&1 &
echo "    router PID=$!"

# 3. Restart Mercury on Seratonin
echo "[3/4] Restarting Mercury on Seratonin..."
nohup "D:/mercury/.venv/Scripts/mercury.exe" gateway > /c/Temp/logs/mercury_gateway.log 2>&1 &
sleep 4
nohup "D:/mercury/.venv/Scripts/mercury.exe" dashboard --host 0.0.0.0 --port 9119 --insecure > /c/Temp/logs/mercury_dashboard.log 2>&1 &

# 4. Smoke test
echo "[4/4] Smoke test — narration via router (should hit Seratonin or Big Apple)..."
sleep 3
curl -s --max-time 30 -X POST http://localhost:8766/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma4:e4b","prompt":"Say hello","stream":false}' \
  2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('  router said:', d.get('response','(empty)')[:80])"

echo
echo "✓ Restored. Seratonin is primary again."
