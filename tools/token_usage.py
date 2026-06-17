"""Compute actual token consumption (today / last 7 days) from local Codex and
Claude Code session logs.

Why local logs: the usage APIs only return window utilization percentages, not
token counts. Real token totals live in the session JSONL transcripts:

  Codex : $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl
          token_count events -> info.last_token_usage (per-turn delta)
  Claude: ~/.claude/projects/<encoded-path>/*.jsonl
          assistant messages -> message.usage.{input,output,cache_*}_tokens

Buckets use a configurable timezone (default UTC+8 / Beijing). "today" is the
local calendar day; "7d" is a rolling 7x24h window ending now.

Usage
-----
  py tools/token_usage.py                 # print today + 7d token totals
  py tools/token_usage.py --tz 0          # use UTC day boundaries
  py tools/token_usage.py --json          # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

CODEX_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def parse_ts(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def empty_bucket():
    return {"input": 0, "output": 0, "cache": 0, "reasoning": 0, "total": 0, "events": 0}


def add(bucket, *, input_t=0, output_t=0, cache=0, reasoning=0, total=None):
    bucket["input"] += int(input_t or 0)
    bucket["output"] += int(output_t or 0)
    bucket["cache"] += int(cache or 0)
    bucket["reasoning"] += int(reasoning or 0)
    bucket["total"] += int(total if total is not None else (input_t or 0) + (output_t or 0))
    bucket["events"] += 1


def iter_jsonl(path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def collect_codex(start_today, start_week):
    today = empty_bucket()
    week = empty_bucket()
    sessions_dir = CODEX_HOME / "sessions"
    if not sessions_dir.is_dir():
        return today, week, 0
    files = 0
    for path in sessions_dir.rglob("*.jsonl"):
        files += 1
        for event in iter_jsonl(path):
            payload = event.get("payload") if isinstance(event, dict) else None
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            ts = parse_ts(event.get("timestamp"))
            if ts is None:
                continue
            info = payload.get("info") or {}
            usage = info.get("last_token_usage") or {}
            if not usage:
                continue
            kwargs = dict(
                input_t=usage.get("input_tokens"),
                output_t=usage.get("output_tokens"),
                cache=usage.get("cached_input_tokens"),
                reasoning=usage.get("reasoning_output_tokens"),
                total=usage.get("total_tokens"),
            )
            if ts >= start_week:
                add(week, **kwargs)
            if ts >= start_today:
                add(today, **kwargs)
    return today, week, files


def collect_claude(start_today, start_week):
    today = empty_bucket()
    week = empty_bucket()
    projects = CLAUDE_HOME / "projects"
    if not projects.is_dir():
        return today, week, 0
    files = 0
    for path in projects.rglob("*.jsonl"):
        files += 1
        for event in iter_jsonl(path):
            if not isinstance(event, dict):
                continue
            message = event.get("message") or {}
            usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                continue
            ts = parse_ts(event.get("timestamp"))
            if ts is None:
                continue
            cache = int(usage.get("cache_creation_input_tokens") or 0) + int(
                usage.get("cache_read_input_tokens") or 0
            )
            input_t = usage.get("input_tokens") or 0
            output_t = usage.get("output_tokens") or 0
            kwargs = dict(
                input_t=input_t,
                output_t=output_t,
                cache=cache,
                total=int(input_t) + int(output_t) + cache,
            )
            if ts >= start_week:
                add(week, **kwargs)
            if ts >= start_today:
                add(today, **kwargs)
    return today, week, files


def fmt(n):
    return f"{n:,}"


def print_report(name, today, week, files):
    print(f"== {name} ==  (scanned {files} session files)")
    for label, b in (("today", today), ("7d", week)):
        print(
            f"  {label:<5} total={fmt(b['total'])}  "
            f"input={fmt(b['input'])}  output={fmt(b['output'])}  "
            f"cache={fmt(b['cache'])}  reasoning={fmt(b['reasoning'])}  "
            f"turns={b['events']}"
        )


def main():
    parser = argparse.ArgumentParser(description="Local token usage (today / 7d) from Codex & Claude logs")
    parser.add_argument("--tz", type=float, default=8.0, help="UTC offset hours for day boundary (default 8 = Beijing)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    tz = timezone(timedelta(hours=args.tz))
    now_local = datetime.now(tz)
    start_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_week = now_local - timedelta(days=7)

    c_today, c_week, c_files = collect_codex(start_today, start_week)
    cc_today, cc_week, cc_files = collect_claude(start_today, start_week)

    if args.json:
        print(json.dumps({
            "tz_offset_hours": args.tz,
            "generated_at": now_local.isoformat(),
            "codex": {"today": c_today, "week": c_week, "files": c_files},
            "claude": {"today": cc_today, "week": cc_week, "files": cc_files},
        }, ensure_ascii=False, indent=2))
        return

    print(f"timezone: UTC{args.tz:+g}   now: {now_local.strftime('%Y-%m-%d %H:%M')}")
    print(f"today window: >= {start_today.strftime('%Y-%m-%d %H:%M')}   7d window: >= {start_week.strftime('%Y-%m-%d %H:%M')}\n")
    print_report("Codex", c_today, c_week, c_files)
    if cc_files:
        print()
        print_report("Claude Code", cc_today, cc_week, cc_files)
    else:
        print("\n== Claude Code ==  (no ~/.claude/projects logs found)")


if __name__ == "__main__":
    main()
