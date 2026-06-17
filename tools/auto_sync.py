"""Automatic loop: fetch real Codex quota -> update data/quota.json -> POST to the
dashboard, which writes the file and triggers a BLE push to the Wio Terminal.

It reuses codex_usage.py (window utilization + reset times) and token_usage.py
(actual token totals from local logs). Run the dashboard server with BLE first:

    py server.py --ble --ble-interval 15

Then, in another terminal:

    py tools/auto_sync.py --interval 60        # refresh + push every 60s
    py tools/auto_sync.py --once               # do it a single time

The POST to /api/quota makes the server rewrite quota.json and immediately
trigger a BLE sync, so device updates happen as soon as new data is fetched.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import codex_usage as cu
import claude_usage as clu
import token_usage as tu

# USD per token. Codex CLI uses gpt-5.5 (standard short-context tier):
#   input $5.00 / cached input $0.50 / output $30.00 per 1M tokens.
PRICING = {
    "codex": {"input": 5.00 / 1e6, "cached": 0.50 / 1e6, "output": 30.00 / 1e6},
    # Claude pricing placeholder (Sonnet-class); only used once Claude logs exist.
    "claude": {"input": 3.00 / 1e6, "cached": 0.30 / 1e6, "output": 15.00 / 1e6},
}


def estimate_cost(bucket: dict, price: dict) -> float:
    cached = int(bucket.get("cache") or 0)
    non_cached_input = max(0, int(bucket.get("input") or 0) - cached)
    output = int(bucket.get("output") or 0)
    return (
        non_cached_input * price["input"]
        + cached * price["cached"]
        + output * price["output"]
    )


def human_tokens(n: int) -> str:
    n = int(n or 0)
    for unit, size in (("T", 10**12), ("G", 10**9), ("M", 10**6), ("K", 10**3)):
        if n >= size:
            return f"{n / size:.2f}{unit}Token"
    return f"{n}Token"


def build_quota(args) -> dict:
    # Codex real quota is best-effort, same as Claude below: if Codex CLI isn't
    # logged in (no ~/.codex/auth.json) or the endpoint fails, keep the previous
    # values instead of breaking the whole push.
    codex_block = None
    if not args.no_codex:
        try:
            auth = cu.load_auth()
            rl = cu.fetch_usage_wham(auth, allow_refresh=not args.no_refresh)
            codex_block = cu.to_dashboard_codex(rl)
        except Exception as error:
            print(f"[codex] usage fetch skipped: {error}", file=sys.stderr)

    tz = timezone(timedelta(hours=args.tz))
    now = datetime.now(tz)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_week = now - timedelta(days=7)
    cx_today, _, _ = tu.collect_codex(start_today, start_week)
    cc_today, _, _ = tu.collect_claude(start_today, start_week)

    try:
        quota = json.loads(cu.DATA_FILE.read_text("utf-8"))
    except Exception:
        quota = {"footer": {}, "platforms": {}}

    if codex_block is not None:
        quota.setdefault("platforms", {})["codex"] = codex_block
    else:
        quota.setdefault("platforms", {}).setdefault("codex", {
            "remaining": 0,
            "short": {"label": "5h", "pct": 0, "reset": "--"},
            "week": {"label": "7d --", "pct": 0, "reset": "--"},
        })

    # Claude real quota is best-effort: the endpoint is rate-limited and may be
    # missing entirely if Claude Code isn't logged in. Keep the previous values
    # (or placeholders) on failure instead of breaking the Codex push.
    if not args.no_claude:
        try:
            quota["platforms"]["claude"] = clu.to_dashboard_claude(clu.fetch_usage())
        except Exception as error:
            print(f"[claude] usage fetch skipped: {error}", file=sys.stderr)

    hhmm = now.strftime("%H:%M")
    quota["updatedAt"] = hhmm
    footer = quota.setdefault("footer", {})
    footer["time"] = hhmm
    footer["tokens"] = human_tokens(cx_today["total"] + cc_today["total"])

    today_cost = estimate_cost(cx_today, PRICING["codex"]) + estimate_cost(cc_today, PRICING["claude"])
    footer["cost"] = f"${today_cost:.2f}"
    return quota


def post(quota: dict, url: str) -> None:
    data = json.dumps(quota, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def cycle(args) -> None:
    quota = build_quota(args)
    codex = quota["platforms"]["codex"]
    try:
        post(quota, args.dashboard_url)
        pushed = "posted+BLE"
    except Exception as error:
        cu.DATA_FILE.write_text(json.dumps(quota, ensure_ascii=False, indent=2) + "\n", "utf-8")
        pushed = f"file-only (server down? {error})"
    claude = quota.get("platforms", {}).get("claude") or {}
    claude_txt = ""
    if claude.get("short"):
        claude_txt = (
            f"  claude 5h={claude['short']['pct']}% "
            f"7d={claude.get('week', {}).get('pct', 0)}%"
        )
    stamp = datetime.now().strftime("%H:%M:%S")
    print(
        f"[{stamp}] codex 5h={codex['short']['pct']}% (reset {codex['short']['reset']})  "
        f"7d={codex['week']['pct']}% (reset {codex['week']['reset']})"
        f"{claude_txt}  "
        f"today={quota['footer']['cost']}  tokens={quota['footer']['tokens']}  -> {pushed}",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto loop: fetch quota -> quota.json -> BLE push")
    parser.add_argument("--interval", type=int, default=60, help="seconds between refreshes")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--tz", type=float, default=8.0, help="UTC offset for day boundary / clock")
    parser.add_argument("--no-refresh", action="store_true", help="never refresh the OAuth token")
    parser.add_argument("--no-claude", action="store_true", help="skip the Claude usage probe")
    parser.add_argument("--no-codex", action="store_true", help="skip the Codex usage probe")
    parser.add_argument("--dashboard-url", default=cu.DASHBOARD_URL)
    args = parser.parse_args()

    if args.once:
        try:
            cycle(args)
            return 0
        except Exception as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    print(f"auto-sync every {args.interval}s -> {args.dashboard_url} (Ctrl+C to stop)", flush=True)
    while True:
        try:
            cycle(args)
        except Exception as error:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] error: {error}", file=sys.stderr, flush=True)
        time.sleep(max(15, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
