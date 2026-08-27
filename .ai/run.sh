#!/usr/bin/env bash
# Autonomous Claude Code runner for dtl
# Usage: ./run.sh "your prompt here"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT="${1:?Usage: ./run.sh \"your prompt here\"}"

echo "[dtl ai run] Starting Claude Code with prompt..."
echo "[dtl ai run] Prompt: $PROMPT"

# Run Claude Code in print mode (non-interactive, autonomous)
# Capture the REAL exit status. `|| EXIT_CODE=$?` records what actually
# happened while stopping `set -e` from aborting first.
#
# Do NOT go back to `|| true` + ${PIPESTATUS[0]}: `|| true` runs a successful
# command, which resets PIPESTATUS, so the recorded status was unconditionally
# 0. Every failure — expired OAuth, crash, wall-clock kill — was reported as
# success.
EXIT_CODE=0
RESULT=$(docker compose -f "$SCRIPT_DIR/docker-compose.yml" \
    run --rm claude-code \
    claude --print -p "$PROMPT" 2>&1) || EXIT_CODE=$?

echo "$RESULT"

# Send notification if configured
if [ -f "$SCRIPT_DIR/notify.py" ]; then
    echo "$RESULT" | python3 "$SCRIPT_DIR/notify.py" "$EXIT_CODE" || true
fi

exit "$EXIT_CODE"
