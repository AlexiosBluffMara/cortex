#!/usr/bin/env bash
# down-bigapple.sh — stop the Big Apple stack cleanly.
# Leaves Ollama (launchd service) and Tailscale alone.
set -u
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

kill_port() {
  local port=$1
  local name=$2
  local pid
  pid=$(lsof -ti :$port 2>/dev/null || true)
  if [[ -n "$pid" ]]; then
    echo "[down-bigapple] $name (port $port) PID=$pid"
    kill -TERM $pid 2>/dev/null || true
    sleep 1
    kill -KILL $pid 2>/dev/null || true
  fi
}

kill_port 8773 cortex-backend
kill_port 8766 inference-router
kill_port 9119 mercury-dashboard

# Mercury gateway has no port; kill by process name
pkill -f "mercury gateway" 2>/dev/null || true

echo "Big Apple stack stopped. Ollama + Tailscale left running."
