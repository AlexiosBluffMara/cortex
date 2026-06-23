#!/usr/bin/env bash
# setup-mac-node.sh — Cortex secondary node bootstrap for seratonin (M4 Max, 48 GB)
#
# Run this on the Mac. Two ways:
#   1. Via Parsec/SSH/local keyboard:
#        bash <(curl -fsSL https://raw.githubusercontent.com/AlexiosBluffMara/cortex/main/scripts/setup-mac-node.sh)
#      (requires the file to be pushed to the repo first)
#   2. Or copy this file onto the Mac via Tailscale ssh (if Remote Login is on)
#      and run:  bash setup-mac-node.sh
#
# Idempotent — safe to re-run. Each step skips if already done.

set -euo pipefail

# ── Tunables (edit if your setup differs) ────────────────────────────────
SERATONIN_IP="100.98.19.87"        # Windows desktop on Tailscale
TAILSCALE_NAME_LOCAL="seratonin"
OLLAMA_HOST_BIND="0.0.0.0:11434"   # Tailscale ACL is the firewall
OLLAMA_KEEP_ALIVE="24h"
OLLAMA_MAX_LOADED_MODELS="3"
SYNCTHING_GUI_PORT="8384"
LITESTREAM_VERSION="0.3.13"
MODELS_TO_PULL=(
  "gemma4:26b"           # heavy narration
  "gemma4:e4b"           # fast narration / vision-gate hybrid
  "embeddinggemma:300m"  # embeddings
  # tribe-v2 stays on the 5090 only — not pulled here
)

log()  { printf "\033[1;36m[setup-mac-node]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[setup-mac-node]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[setup-mac-node]\033[0m %s\n" "$*" >&2; exit 1; }

# ── Pre-flight ───────────────────────────────────────────────────────────
[[ "$(uname -s)" == "Darwin" ]] || die "This script is macOS-only."
[[ "$(uname -m)" == "arm64" ]] || warn "Apple Silicon expected; running on $(uname -m). Continuing anyway."

if ! command -v xcode-select >/dev/null 2>&1 || ! xcode-select -p >/dev/null 2>&1; then
  log "Installing Xcode CLT (will prompt for confirmation)..."
  xcode-select --install || true
  read -rp "Press Enter once Xcode CLT install finishes..."
fi

# ── 1. Homebrew ──────────────────────────────────────────────────────────
if ! command -v brew >/dev/null 2>&1; then
  log "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -d /opt/homebrew/bin ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  fi
fi
log "brew: $(brew --version | head -1)"

# ── 2. Core packages ─────────────────────────────────────────────────────
log "Installing core packages (ollama syncthing restic jq)…"
brew list --formula ollama   >/dev/null 2>&1 || brew install ollama
brew list --formula syncthing >/dev/null 2>&1 || brew install syncthing
brew list --formula restic   >/dev/null 2>&1 || brew install restic
brew list --formula jq       >/dev/null 2>&1 || brew install jq

# Litestream isn't in homebrew-core; tap or download release
if ! command -v litestream >/dev/null 2>&1; then
  log "Installing litestream from GitHub release…"
  curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-darwin-arm64.zip" -o /tmp/litestream.zip
  unzip -o /tmp/litestream.zip -d /usr/local/bin/
  chmod +x /usr/local/bin/litestream
  rm -f /tmp/litestream.zip
fi
log "litestream: $(litestream version)"

# ── 3. Always-on power settings ──────────────────────────────────────────
log "Configuring power management for always-on…"
# These all need sudo. We batch them into one sudo invocation.
sudo bash <<'PMSET_EOF'
pmset -a sleep 0
pmset -a disablesleep 1
pmset -a displaysleep 0
pmset -a disksleep 0
pmset -a hibernatemode 0
pmset -a powernap 0
pmset -a tcpkeepalive 1
pmset -a womp 1                # wake-on-magic-packet
PMSET_EOF
log "pmset updated. Current state:"
pmset -g | sed 's/^/  /'

# ── 4. Ollama as a launchd daemon (always running) ───────────────────────
PLIST_PATH="$HOME/Library/LaunchAgents/ai.ollama.serve.plist"
log "Writing Ollama launchd plist → $PLIST_PATH"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.ollama.serve</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(brew --prefix)/bin/ollama</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OLLAMA_HOST</key><string>${OLLAMA_HOST_BIND}</string>
    <key>OLLAMA_KEEP_ALIVE</key><string>${OLLAMA_KEEP_ALIVE}</string>
    <key>OLLAMA_MAX_LOADED_MODELS</key><string>${OLLAMA_MAX_LOADED_MODELS}</string>
    <key>OLLAMA_NUM_PARALLEL</key><string>2</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/ollama.log</string>
  <key>StandardErrorPath</key><string>/tmp/ollama.err</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"
sleep 3

if curl -fsS "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
  log "Ollama is running on 127.0.0.1:11434 ✓"
else
  warn "Ollama did not come up; check /tmp/ollama.err"
fi

# ── 5. Pull models (long step; runs in background, status reported) ──────
log "Pulling models in background (this can take 30–60 min on first run)…"
for m in "${MODELS_TO_PULL[@]}"; do
  if ollama list 2>/dev/null | awk '{print $1}' | grep -q "^${m}$"; then
    log "  already have: $m"
  else
    log "  pulling: $m (background)"
    nohup ollama pull "$m" >>/tmp/ollama-pulls.log 2>&1 &
  fi
done

# ── 6. Syncthing (one-time pair-up will need browser) ────────────────────
log "Starting Syncthing…"
SC_PLIST="$HOME/Library/LaunchAgents/io.syncthing.app.plist"
cat > "$SC_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>io.syncthing.app</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(brew --prefix)/bin/syncthing</string>
    <string>serve</string>
    <string>--no-browser</string>
    <string>--gui-address=127.0.0.1:${SYNCTHING_GUI_PORT}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/syncthing.log</string>
  <key>StandardErrorPath</key><string>/tmp/syncthing.err</string>
</dict>
</plist>
EOF
launchctl unload "$SC_PLIST" 2>/dev/null || true
launchctl load -w "$SC_PLIST"
sleep 3
log "Syncthing UI: open http://127.0.0.1:${SYNCTHING_GUI_PORT} on the Mac (or via Parsec)"
log "Pair the Mac with seratonin manually one time — copy device IDs between the two web UIs."

# ── 7. SSH key — pull seratonin's public key for passwordless ops ────────
log "Set up authorized_keys for seratonin (manual one-line if needed):"
log "  on seratonin (Windows git-bash):  cat ~/.ssh/id_ed25519.pub"
log "  → paste into ~/.ssh/authorized_keys on this Mac"

# ── 8. Print summary ─────────────────────────────────────────────────────
cat <<SUMMARY

=========================================================================
seratonin secondary-inference node setup complete (or in-progress).

Ollama:        running on 0.0.0.0:11434 (Tailscale-accessible)
               models pulling in background; tail /tmp/ollama-pulls.log
Syncthing:     running, web UI at http://127.0.0.1:${SYNCTHING_GUI_PORT}
Power:         sleep disabled, lid-close-on-AC stays running
Tailscale:     this Mac is at $(tailscale ip 2>/dev/null | head -1 || echo "<run: tailscale ip>")

Next, on seratonin (Windows):
  curl -fsS http://${TAILSCALE_NAME_LOCAL}:11434/api/tags
  → should list the models (after pulls finish).

Then update inference-router env:
  OLLAMA_BACKENDS="http://localhost:11434,http://${TAILSCALE_NAME_LOCAL}:11434"

=========================================================================
SUMMARY
