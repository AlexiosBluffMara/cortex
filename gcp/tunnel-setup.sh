#!/bin/bash
# Run AFTER: cloudflared tunnel login
# Creates named tunnel, configures ingress for both Cortex and Mercury,
# routes DNS (adds CNAME records in Cloudflare automatically), and
# writes the Windows startup config.

set -euo pipefail

CF="C:/Program Files (x86)/cloudflared/cloudflared.exe"
TUNNEL_NAME="rtk-5090"          # one tunnel, two hostnames
CORTEX_PORT=8765
MERCURY_PORT=8080
CONFIG_DIR="$HOME/.cloudflared"

echo "=== Creating named tunnel: $TUNNEL_NAME ==="
"$CF" tunnel create $TUNNEL_NAME

# Get the tunnel UUID (used in credentials filename)
TUNNEL_UUID=$("$CF" tunnel list --output json | python3 -c "
import json,sys
tunnels = json.load(sys.stdin)
for t in tunnels:
    if t['name'] == '${TUNNEL_NAME}':
        print(t['id'])
        break
")
echo "Tunnel UUID: $TUNNEL_UUID"

echo "=== Writing tunnel config ==="
cat > "$CONFIG_DIR/config.yml" <<EOF
tunnel: $TUNNEL_UUID
credentials-file: $CONFIG_DIR/$TUNNEL_UUID.json
logfile: $CONFIG_DIR/tunnel.log
loglevel: info

ingress:
  - hostname: cortex.redteamkitchen.com
    service: http://localhost:$CORTEX_PORT
    originRequest:
      connectTimeout: 30s
      noTLSVerify: false
  - hostname: mercury.redteamkitchen.com
    service: http://localhost:$MERCURY_PORT
    originRequest:
      connectTimeout: 10s
      noTLSVerify: false
  # catch-all required by cloudflared
  - service: http_status:404
EOF

echo "=== Routing DNS (adds CNAMEs to Cloudflare automatically) ==="
"$CF" tunnel route dns $TUNNEL_NAME cortex.redteamkitchen.com
"$CF" tunnel route dns $TUNNEL_NAME mercury.redteamkitchen.com

echo "=== Installing as Windows service (auto-starts on boot) ==="
"$CF" service install

echo ""
echo "=== Done! ==="
echo "Tunnel UUID : $TUNNEL_UUID"
echo "Cortex URL  : https://cortex.redteamkitchen.com"
echo "Mercury URL : https://mercury.redteamkitchen.com"
echo ""
echo "Start now with:  '$CF' tunnel run $TUNNEL_NAME"
echo "Or (service):    sc start cloudflared"
