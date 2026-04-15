#!/usr/bin/env bash
# Autonomous Claude Code runner for dtl
# Usage: ./run.sh "your prompt here"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT="${1:?Usage: ./run.sh \"your prompt here\"}"

echo "[dtl ai run] Starting Claude Code with prompt..."
echo "[dtl ai run] Prompt: $PROMPT"

# Run Claude Code in print mode (non-interactive, autonomous)
RESULT=$(docker compose -f "$SCRIPT_DIR/docker-compose.yml" \
    run --rm claude-code \
    claude --print -p "$PROMPT" 2>&1) || true
EXIT_CODE=${PIPESTATUS[0]:-$?}

echo "$RESULT"

# Send notification if configured
if [ -f "$SCRIPT_DIR/notify.py" ]; then
    echo "$RESULT" | python3 "$SCRIPT_DIR/notify.py" "$EXIT_CODE" || true
fi

exit "$EXIT_CODE"
