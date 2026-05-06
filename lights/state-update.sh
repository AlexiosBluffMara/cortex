#!/usr/bin/env bash
# state-update.sh — atomic state-file mutator for the lights daemon.
# Called from Claude Code hooks + Mercury hooks.
#
# Usage:
#   state-update.sh claude-start
#   state-update.sh claude-prompt-submit
#   state-update.sh claude-stop          # turn idle (waiting on user)
#   state-update.sh claude-end           # session over
#   state-update.sh mercury-start
#   state-update.sh mercury-end
#   state-update.sh usage <percent>      # 0-100
#
# State lives at ${LIGHTS_STATE:-$HOME/.cortex/lights-state.json}.

set -euo pipefail

STATE="${LIGHTS_STATE:-$HOME/.cortex/lights-state.json}"
mkdir -p "$(dirname "$STATE")"
[[ -f "$STATE" ]] || cat > "$STATE" <<'EOF'
{"claude_active": false, "claude_idle": false, "mercury_active": false, "usage_percent": 0, "last_update": ""}
EOF

ev="${1:-}"
val="${2:-}"
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Pick the right jq mutation per event
case "$ev" in
    claude-start)         jq=".claude_active = true | .claude_idle = false";;
    claude-prompt-submit) jq=".claude_active = true | .claude_idle = false";;
    claude-stop)          jq=".claude_active = true | .claude_idle = true";;
    claude-end)           jq=".claude_active = false | .claude_idle = false";;
    mercury-start)        jq=".mercury_active = true";;
    mercury-end)          jq=".mercury_active = false";;
    usage)                jq=".usage_percent = (\"$val\" | tonumber)";;
    *) echo "unknown event: $ev"; exit 2;;
esac

tmp=$(mktemp)
jq "$jq | .last_update = \"$ts\"" "$STATE" > "$tmp" && mv "$tmp" "$STATE"
