"""usage-poller.py — periodically estimate Claude usage % and update lights state.

Claude Code's usage is the 5-hour rolling rate-limit window for Pro/Max plans.
There is no public API for "% of window used", but we can approximate by
parsing the latest session transcripts under ~/.claude/projects/<hash>/ for
turn timestamps + token usage in the last 5 hours, divided by the plan limit.

For Soumit's Claude Max $100 tier the practical signal we care about is:
  - hit a rate limit (5h window exhausted)  -> 100%
  - approaching exhaustion (>= 90%)         -> red light

Strategy:
  1. Look at the newest *.jsonl transcript in ~/.claude/projects/*/
  2. Sum input+output tokens for messages whose timestamp is within the
     last 5h.
  3. Compare against MAX_TOKENS_5H_BUDGET (env, default 1.5M).
  4. Write the percentage into the lights-state.json via state-update.sh.

Designed to run on a 5-minute cron tick.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude")))
WINDOW_HOURS = float(os.environ.get("WINDOW_HOURS", "5"))
# Claude Max ($100/mo) practical 5h consumption ceiling. Tune via env.
TOKEN_BUDGET_5H = int(os.environ.get("TOKEN_BUDGET_5H", "50000000"))
STATE_UPDATE = os.environ.get("STATE_UPDATE",
                              str(Path(__file__).parent / "state-update.sh"))


def iter_recent_messages():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    proj_root = CLAUDE_HOME / "projects"
    if not proj_root.exists():
        return
    for proj in proj_root.iterdir():
        if not proj.is_dir():
            continue
        for jsonl in proj.glob("*.jsonl"):
            # Only walk files whose mtime is within the window
            if jsonl.stat().st_mtime < cutoff.timestamp():
                continue
            try:
                with jsonl.open("r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        # Each line is a message record; timestamps live under
                        # 'timestamp' or nested in 'message.usage' for assistant
                        # messages.
                        ts = obj.get("timestamp")
                        if not ts:
                            continue
                        try:
                            ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        if ts_dt < cutoff:
                            continue
                        usage = ((obj.get("message") or {}).get("usage") or {})
                        if usage:
                            yield obj, usage
            except OSError:
                continue


def compute_percent() -> int:
    total = 0
    for _msg, usage in iter_recent_messages():
        total += int(usage.get("input_tokens", 0) or 0)
        total += int(usage.get("output_tokens", 0) or 0)
        total += int(usage.get("cache_read_input_tokens", 0) or 0) // 10  # discounted
        total += int(usage.get("cache_creation_input_tokens", 0) or 0)
    pct = int(min(100, round(100 * total / max(TOKEN_BUDGET_5H, 1))))
    print(f"tokens last {WINDOW_HOURS}h: {total:,} / budget {TOKEN_BUDGET_5H:,} = {pct}%",
          file=sys.stderr)
    return pct


def push(pct: int) -> None:
    subprocess.call(["bash", STATE_UPDATE, "usage", str(pct)])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true",
                   help="Compute, push, exit (default: loop every 300s)")
    p.add_argument("--interval", type=int, default=300)
    args = p.parse_args()

    if args.once:
        push(compute_percent())
        return 0

    while True:
        try:
            push(compute_percent())
        except Exception as exc:
            print(f"poller error: {exc}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
