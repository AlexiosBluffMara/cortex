#!/usr/bin/env bash
# status.sh - check the Seratonin-local Cortex stack and public surfaces.
# Works on Windows (git-bash), macOS, Linux. No deps beyond curl + python3.
set -u

SERATONIN=${SERATONIN_HOST:-localhost}

probe() {
  local label=$1
  local url=$2
  local timeout=${3:-3}
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$timeout" "$url" 2>/dev/null || echo "---")
  case "$code" in
    200|204|301|302|307|308) printf "  %-32s \033[32m%s\033[0m  %s\n" "$label" "OK" "$code" ;;
    000|---)                 printf "  %-32s \033[31m%s\033[0m  %s\n" "$label" "DOWN" "$code" ;;
    *)                       printf "  %-32s \033[33m%s\033[0m  %s\n" "$label" "WEIRD" "$code" ;;
  esac
}

echo "============================================================"
echo "  Ascended Base - Seratonin status"
echo "============================================================"
echo
echo "-- Seratonin stack at $SERATONIN --"
probe "Cortex webapp (8765)"       "http://$SERATONIN:8765/api/health"
probe "Inference router (8766)"    "http://$SERATONIN:8766/healthz"
probe "Cortex backend (8773)"      "http://$SERATONIN:8773/api/health"
probe "Ollama (11434)"             "http://$SERATONIN:11434/api/tags"
probe "Watchdog (8780)"            "http://$SERATONIN:8780/status"
echo
echo "-- Public surface --"
probe "Marketing / app"            "https://redteamkitchen.com" 5
probe "Cortex subdomain"           "https://cortex.redteamkitchen.com" 5
echo
echo "-- Inference backends --"
curl -s --max-time 4 "http://$SERATONIN:8766/healthz" 2>/dev/null \
  | python3 -c "
import sys, json
try:
  d = json.load(sys.stdin)
  for url, ok in (d.get('ollama_backends') or {}).items():
    print(f'  {url:42s} {\"UP\" if ok else \"DOWN\"}')
  print(f'  openrouter                                  {\"UP\" if d.get(\"openrouter\") else \"DOWN\"}')
except Exception as e:
  print(f'  router unreachable: {e}')
" 2>/dev/null
echo
