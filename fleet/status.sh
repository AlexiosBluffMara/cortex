#!/usr/bin/env bash
# status.sh — check both Seratonin and Big Apple from anywhere on the tailnet.
# Works on Windows (git-bash), macOS, Linux. No deps beyond curl + python3.
set -u

SERATONIN=${SERATONIN_HOST:-100.98.19.87}
BIGAPPLE=${BIGAPPLE_HOST:-100.93.240.52}

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

echo "════════════════════════════════════════════════════════════"
echo "  Ascended Base — fleet status"
echo "════════════════════════════════════════════════════════════"
echo
echo "── Seratonin (Windows · RTX 5090) at $SERATONIN ──"
probe "Cortex backend (8773)"     "http://$SERATONIN:8773/api/health"
probe "Inference router (8766)"   "http://$SERATONIN:8766/healthz"
probe "Vite frontend (5173)"      "http://$SERATONIN:5173/"
probe "Mercury dashboard (9119)"  "http://$SERATONIN:9119/"
probe "Ollama (11434)"            "http://$SERATONIN:11434/api/tags"
echo
echo "── Big Apple (macOS · M4 Max) at $BIGAPPLE ──"
probe "Cortex backend (8773)"     "http://$BIGAPPLE:8773/api/health"
probe "Inference router (8766)"   "http://$BIGAPPLE:8766/healthz"
probe "Vite frontend (5173)"      "http://$BIGAPPLE:5173/"
probe "Mercury dashboard (9119)"  "http://$BIGAPPLE:9119/"
probe "Ollama (11434)"            "http://$BIGAPPLE:11434/api/tags"
echo
echo "── Public surface ──"
probe "Demo (Tailscale Funnel)"        "https://seratonin.scylla-betta.ts.net" 5
probe "Marketing (CF Pages)"           "https://redteamkitchen.com" 5
probe "Cortex subdomain (redirect)"    "https://cortex.redteamkitchen.com" 5
echo
echo "── Inference fleet (which nodes can take a narration job?) ──"
curl -s --max-time 4 http://$SERATONIN:8766/healthz 2>/dev/null \
  | python3 -c "
import sys, json
try:
  d = json.load(sys.stdin); b = d.get('ollama_backends', {})
  for url, ok in b.items():
    name = 'seratonin' if 'localhost' in url or '11434' in url and 'localhost' in url else 'big-apple'
    name = 'seratonin' if '11434' in url and 'localhost' in url else ('big-apple' if '100.93' in url else url)
    print(f'  {name:14s} {\"UP\" if ok else \"DOWN\"}')
  print(f'  openrouter      {\"UP\" if d.get(\"openrouter\") else \"DOWN\"}')
except Exception as e:
  print(f'  router unreachable: {e}')
" 2>/dev/null
echo
