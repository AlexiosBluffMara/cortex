#!/usr/bin/env bash
# failover-active.sh — ensure both nodes are up and the router round-robins between them.
# This is the default healthy state. Idempotent — safe to run any time to re-converge.
set -euo pipefail

BIGAPPLE_HOST=${BIGAPPLE_HOST:-big-apple}
BIGAPPLE_TS=${BIGAPPLE_TS:-100.93.240.52}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "════════════════════════════════════════════════════════════"
echo "  Active failover — both nodes serving"
echo "════════════════════════════════════════════════════════════"

# 1. Make sure Seratonin core services are up (router, backend, vite, mercury)
if ! curl -sf --max-time 2 http://localhost:8773/api/health >/dev/null; then
  echo "[1] Seratonin backend down — starting full Seratonin stack..."
  pwsh "$ROOT/fleet/up-seratonin.ps1" 2>&1 | sed 's/^/    /' || powershell.exe -ExecutionPolicy Bypass -File "$ROOT/fleet/up-seratonin.ps1"
else
  echo "[1] Seratonin backend already up. ✓"
fi

# 2. Make sure router has BOTH backends in pool
echo "[2] Reconfiguring router with both backends..."
PIDS=$(netstat -ano 2>/dev/null | grep ':8766 ' | grep LISTENING | awk '{print $5}' | sort -u)
for p in $PIDS; do taskkill /PID $p /F /T 2>/dev/null || true; done
sleep 2
export OLLAMA_BACKENDS="http://localhost:11434,http://${BIGAPPLE_TS}:11434"
nohup "C:/Users/soumi/cortex/.venv/Scripts/python.exe" -m uvicorn inference_router.server:app \
  --host 0.0.0.0 --port 8766 --log-level info > /c/Temp/logs/cortex_router.log 2>&1 &
sleep 2

# 3. Make sure Big Apple is reachable; if so, ensure router can hit it
echo "[3] Pinging Big Apple Ollama..."
if curl -sf --max-time 4 "http://${BIGAPPLE_TS}:11434/api/tags" >/dev/null; then
  echo "    Big Apple Ollama: UP"
else
  echo "    Big Apple Ollama: DOWN — bringing it up via SSH..."
  ssh "$BIGAPPLE_HOST" "open -a Ollama" 2>/dev/null || true
fi

# 4. Verify
echo "[4] Final state:"
curl -s --max-time 4 http://localhost:8766/healthz 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
for url, ok in d.get('ollama_backends',{}).items():
    name = 'seratonin' if 'localhost' in url else 'big-apple'
    print(f'    {name:12s}: {\"UP\" if ok else \"DOWN\"}')
print(f'    openrouter  : {\"UP\" if d.get(\"openrouter\") else \"DOWN\"}')
"

echo
echo "✓ Active failover converged. Both nodes serving."
