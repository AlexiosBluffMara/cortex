#!/usr/bin/env bash
# failover-to-bigapple.sh — gaming mode.
# Move all narration + Mercury to Big Apple so Seratonin can game without
# the demo degrading. Cortex backend stays on Seratonin (TRIBE needs CUDA),
# but every Gemma narration request is forced to Big Apple's Ollama.
#
# Effect after running:
#   * Seratonin Ollama is REMOVED from the inference router pool
#   * Big Apple Ollama is the ONLY local backend (OpenRouter is still failover)
#   * Mercury runs on Big Apple, not Seratonin
#   * Public URL stays at seratonin.scylla-betta.ts.net (the Vite dev server
#     and Cortex backend are tiny CPU loads — they coexist with gaming fine)
#
# Run from Seratonin (or Big Apple — script SSHes to whichever node it needs).
set -euo pipefail

SERATONIN_HOST=${SERATONIN_HOST:-seratonin}
BIGAPPLE_HOST=${BIGAPPLE_HOST:-big-apple}
SERATONIN_TS=${SERATONIN_TS:-100.98.19.87}
BIGAPPLE_TS=${BIGAPPLE_TS:-100.93.240.52}

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "════════════════════════════════════════════════════════════"
echo "  Failover → Big Apple (gaming mode)"
echo "════════════════════════════════════════════════════════════"

# 1. Make sure Big Apple's Ollama has the models loaded
echo "[1/5] Pre-warming Big Apple Ollama (gemma4:e4b)..."
ssh "$BIGAPPLE_HOST" "/opt/homebrew/bin/ollama run gemma4:e4b 'hi' >/dev/null 2>&1" || echo "  (warmup failed — model may not be present yet)"

# 2. Bring Big Apple stack up (Mercury + router + cortex narration backend)
echo "[2/5] Starting Big Apple stack..."
ssh "$BIGAPPLE_HOST" "bash -lc 'cd ~/cortex && bash fleet/up-bigapple.sh'" 2>&1 | sed 's/^/    /'

# 3. Reconfigure Seratonin's inference router to use ONLY Big Apple
#    (router_health is read by the demo's status pill — it'll show seratonin DOWN)
echo "[3/5] Repointing Seratonin router → Big Apple primary..."
# Kill old router
PIDS=$(netstat -ano 2>/dev/null | grep ':8766 ' | grep LISTENING | awk '{print $5}' | sort -u)
for p in $PIDS; do taskkill /PID $p /F /T 2>/dev/null || true; done
sleep 2
# Restart with Big Apple-only backend list
cd "$ROOT"
export OLLAMA_BACKENDS="http://${BIGAPPLE_TS}:11434"
export ROUTER_PORT="8766"
nohup "C:/Users/soumi/cortex/.venv/Scripts/python.exe" -m uvicorn inference_router.server:app \
  --host 0.0.0.0 --port 8766 --log-level info > /c/Temp/logs/cortex_router.log 2>&1 &
echo "    new router PID=$!"

# 4. Stop Mercury locally to free CPU for gaming
echo "[4/5] Stopping local Mercury (CPU saver for gaming)..."
PIDS=$(netstat -ano 2>/dev/null | grep ':9119 ' | grep LISTENING | awk '{print $5}' | sort -u)
for p in $PIDS; do taskkill /PID $p /F /T 2>/dev/null || true; done
# Mercury gateway has no port — kill by image name
taskkill /IM mercury.exe /F /T 2>/dev/null || true

# 5. Smoke-test
echo "[5/5] Smoke test — narration via router (should hit Big Apple)..."
sleep 3
curl -s --max-time 30 -X POST http://localhost:8766/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"gemma4:e4b","prompt":"Say hello in 5 words","stream":false}' \
  2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('  router said:', d.get('response','(empty)')[:80])"

echo
echo "✓ Failover to Big Apple complete."
echo "  - Cortex backend on Seratonin (TRIBE needs the 5090; uses minimal idle GPU)"
echo "  - All narration → Big Apple via router"
echo "  - Mercury CLI/Discord/dashboard → Big Apple at http://big-apple:9119"
echo "  - Public URL still https://seratonin.scylla-betta.ts.net (Vite + FastAPI light load)"
echo
echo "Game on. When done, run: bash fleet/failover-to-seratonin.sh"
