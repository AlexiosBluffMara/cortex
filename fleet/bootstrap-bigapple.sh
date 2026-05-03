#!/usr/bin/env bash
# bootstrap-bigapple.sh — one-time setup of Big Apple as a failover node.
# Run from anywhere on the tailnet that can SSH big-apple. Idempotent.
#
# Installs (homebrew):
#   - python@3.12 (if missing) - Big Apple already has /usr/bin/python3
#   - uv (fast python pkg mgr)
# Clones / pulls:
#   - ~/cortex   (this repo)
#   - ~/mercury  (Mercury / Hermes fork)
# Sets up:
#   - ~/cortex/.venv via uv  (cortex + mercury installed editable)
#   - ~/.hermes/.env (mirrored from Seratonin if not present)
#   - Pulls gemma4:e4b/26b/31b in Ollama
#
# Run: bash fleet/bootstrap-bigapple.sh
set -euo pipefail

BIGAPPLE_HOST=${BIGAPPLE_HOST:-big-apple}

echo "════════════════════════════════════════════════════════════"
echo "  Bootstrap Big Apple as a failover node"
echo "════════════════════════════════════════════════════════════"

# Make sure SSH works
ssh -o ConnectTimeout=4 "$BIGAPPLE_HOST" "echo SSH OK" || { echo "SSH to $BIGAPPLE_HOST failed"; exit 1; }

REMOTE_SETUP=$(cat <<'REMOTE'
set -euo pipefail
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

# 1. Brew check / install
if ! command -v brew >/dev/null 2>&1; then
  echo "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# 2. Install required brew packages
brew list uv >/dev/null 2>&1 || brew install uv
brew list git >/dev/null 2>&1 || brew install git
brew list lsof >/dev/null 2>&1 || brew install lsof

# 3. Clone or pull cortex
if [[ ! -d "$HOME/cortex" ]]; then
  echo "Cloning cortex..."
  git clone https://github.com/AlexiosBluffMara/cortex.git "$HOME/cortex"
else
  cd "$HOME/cortex" && git pull
fi

# 4. Clone or pull mercury
if [[ ! -d "$HOME/mercury" ]]; then
  echo "Cloning mercury..."
  git clone https://github.com/AlexiosBluffMara/mercury.git "$HOME/mercury" || true
fi

# 5. Set up venv
cd "$HOME/cortex"
if [[ ! -d ".venv" ]]; then
  uv venv --python 3.12
fi

# Activate-like: install cortex narrator-only deps + mercury
.venv/bin/python -m pip install --quiet --upgrade pip
uv pip install --quiet -e .[mac] 2>/dev/null || uv pip install --quiet fastapi uvicorn httpx requests numpy pydantic
if [[ -d "$HOME/mercury" ]]; then
  uv pip install --quiet -e "$HOME/mercury" || true
fi

# 6. Pull Ollama models
for m in gemma4:e4b gemma4:26b gemma4:31b; do
  if ! /opt/homebrew/bin/ollama list 2>/dev/null | grep -q "^$m"; then
    echo "Pulling $m..."
    /opt/homebrew/bin/ollama pull "$m" || echo "  (pull failed, may already exist as alias)"
  fi
done

echo
echo "Big Apple bootstrap done. Stack ready to start with:"
echo "  bash ~/cortex/fleet/up-bigapple.sh"
REMOTE
)

ssh "$BIGAPPLE_HOST" "bash -lc '$REMOTE_SETUP'"

# Mirror ~/.hermes/.env to Big Apple if it's missing there
if ssh "$BIGAPPLE_HOST" "[[ ! -s ~/.hermes/.env ]]" 2>/dev/null; then
  echo "Copying ~/.hermes/.env to Big Apple..."
  ssh "$BIGAPPLE_HOST" "mkdir -p ~/.hermes"
  scp ~/.hermes/.env "$BIGAPPLE_HOST:~/.hermes/.env"
  echo "  copied."
fi

echo "✓ Bootstrap complete. Run 'bash fleet/status.sh' to see both nodes."
