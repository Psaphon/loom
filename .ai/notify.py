#!/usr/bin/env python3
"""Telegram notification sender for dtl autonomous mode.

Usage:
    echo "message" | python3 notify.py 0          # success
    echo "message" | python3 notify.py 1          # failure
    python3 notify.py 0 "inline message"          # inline
    python3 notify.py --test                      # send test message

Reads config from .ai/config.json in the same directory.
Token and chat ID can also be set via TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID environment variables.
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message[:4096],
        "parse_mode": "Markdown",
    }).encode()
    req = urllib.request.Request(url, data=data)
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[notify] Telegram send failed: {e}", file=sys.stderr)
        return False


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def main() -> None:
    config = load_config()
    notify = config.get("notify", {})

    token = os.environ.get("TELEGRAM_BOT_TOKEN", notify.get("telegram_token") or "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", notify.get("telegram_chat_id") or "")

    if not token or not chat_id:
        print("[notify] Telegram not configured. Set token and chat_id in .ai/config.json", file=sys.stderr)
        print("[notify] or via TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars.", file=sys.stderr)
        sys.exit(1)

    project = config.get("project_name", "unknown")

    # --test flag
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        ok = send_telegram(token, chat_id, f"*dtl* — test notification for `{project}`")
        sys.exit(0 if ok else 1)

    # Normal mode: status code + message
    status = sys.argv[1] if len(sys.argv) > 1 else "0"
    if len(sys.argv) > 2:
        message = " ".join(sys.argv[2:])
    elif not sys.stdin.isatty():
        message = sys.stdin.read()
    else:
        message = "(no output captured)"

    icon = "complete" if status == "0" else "FAILED"
    # Truncate for Telegram (4096 char limit, leave room for header)
    if len(message) > 3000:
        message = message[:3000] + "\n... (truncated)"

    text = f"*dtl ai run* — `{project}`\n\nStatus: {icon}\n\n```\n{message}\n```"
    ok = send_telegram(token, chat_id, text)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
