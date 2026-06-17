"""Fetch the real Claude Code usage quota (5h + 7d windows) for the locally
logged-in Anthropic account and optionally push it into the Wio dashboard.

How it works
------------
Claude Code authenticates with an OAuth token. The exact plan usage is served by
the same undocumented endpoint Claude Code itself polls:

  GET https://api.anthropic.com/api/oauth/usage
      -> JSON with five_hour / seven_day, each { utilization (0-100), resets_at }

(Method adapted from Rida2000/wio-claude-buddy: host/buddy_ble_bridge.py.)

The OAuth access token lives in:
  * macOS  : Keychain item "Claude Code-credentials" (the file copy is often stale)
  * Win/Lin: ~/.claude/.credentials.json -> claudeAiOauth.accessToken
             (override the dir with CLAUDE_CONFIG_DIR)

This endpoint is undocumented and aggressively rate-limited; poll it at >= 60s.

Usage
-----
  py tools/claude_usage.py                 # print usage as JSON
  py tools/claude_usage.py --post          # also push into the running dashboard
  py tools/claude_usage.py --watch 300     # refresh every 300s and keep posting
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "quota.json"

CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
CREDS_FILE = CLAUDE_HOME / ".credentials.json"

USAGE_URL = os.environ.get("CLAUDE_USAGE_URL", "https://api.anthropic.com/api/oauth/usage")
ANTHROPIC_BETA = "oauth-2025-04-20"

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8765/api/quota")


def _oauth() -> dict:
    """Return the live ``claudeAiOauth`` block.

    On macOS Claude Code keeps the current (refreshed) token in the Keychain;
    the copy in ~/.claude/.credentials.json is often stale. Prefer the Keychain,
    fall back to the file (the only source on Windows / Linux)."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                d = json.loads(r.stdout.strip())
                return d.get("claudeAiOauth", d)
        except Exception:
            pass
    try:
        d = json.loads(CREDS_FILE.read_text("utf-8"))
        return d.get("claudeAiOauth", d)
    except Exception:
        return {}


def load_token() -> str:
    return _oauth().get("accessToken", "")


def token_expired(skew_seconds: int = 60) -> bool:
    """Best-effort check using the stored expiresAt (epoch ms)."""
    exp = _oauth().get("expiresAt")
    if not exp:
        return False
    try:
        return time.time() + skew_seconds >= float(exp) / 1000.0
    except (TypeError, ValueError):
        return False


def claude_code_ua() -> str:
    """Mimic the Claude Code CLI user agent; the endpoint expects it."""
    ver = "2.0.0"
    try:
        out = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=5
        ).stdout.strip().split()
        if out:
            ver = out[0]
    except Exception:
        pass
    return f"claude-code/{ver}"


def _clock(ts: int | None, with_date: bool) -> str:
    if not ts:
        return "--"
    dt = datetime.fromtimestamp(ts).astimezone()
    return dt.strftime("%m/%d %H:%M") if with_date else dt.strftime("%H:%M")


def _norm_window(raw: dict | None) -> dict | None:
    """Normalize one usage window {utilization(0-100), resets_at ISO}."""
    if not isinstance(raw, dict) or raw.get("utilization") is None:
        return None
    used = float(raw["utilization"])
    resets_at = None
    ra = raw.get("resets_at")
    if ra:
        try:
            dt = datetime.fromisoformat(str(ra).replace("Z", "+00:00"))
            resets_at = int(dt.timestamp())
        except ValueError:
            resets_at = None
    return {"used_percent": used, "resets_at": resets_at}


def fetch_usage(token: str | None = None) -> dict:
    """Call GET /api/oauth/usage and return {primary, secondary, plan_type}."""
    token = token or load_token()
    if not token:
        raise RuntimeError(
            f"no Claude OAuth token found (looked in {CREDS_FILE}). Log in with "
            "`claude` / `claude login` first."
        )
    req = urllib.request.Request(USAGE_URL, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", claude_code_ua())
    req.add_header("anthropic-beta", ANTHROPIC_BETA)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read(400).decode("utf-8", "replace")
        raise RuntimeError(f"usage endpoint error ({e.code}): {detail}") from None

    primary = _norm_window(data.get("five_hour"))
    secondary = _norm_window(data.get("seven_day"))
    if primary is None and secondary is None:
        raise RuntimeError(
            f"usage endpoint returned no five_hour/seven_day windows: {json.dumps(data)[:400]}"
        )
    return {
        "primary": primary,
        "secondary": secondary,
        "plan_type": _oauth().get("subscriptionType"),
    }


def to_dashboard_claude(rl: dict) -> dict:
    primary = rl.get("primary") or {}
    secondary = rl.get("secondary") or {}
    short_used = round(primary.get("used_percent", 0))
    week_used = round(secondary.get("used_percent", 0))
    return {
        "remaining": short_used,
        "short": {
            "label": "5h",
            "pct": short_used,
            "reset": _clock(primary.get("resets_at"), with_date=False),
        },
        "week": {
            "label": f"7d {week_used}%",
            "pct": week_used,
            "reset": _clock(secondary.get("resets_at"), with_date=True),
        },
    }


def post_to_dashboard(claude_block: dict, url: str) -> None:
    try:
        quota = json.loads(DATA_FILE.read_text("utf-8"))
    except Exception:
        quota = {"footer": {}, "platforms": {}}
    quota.setdefault("platforms", {})["claude"] = claude_block
    now = datetime.now().astimezone().strftime("%H:%M")
    quota["updatedAt"] = now
    quota.setdefault("footer", {})["time"] = now

    data = json.dumps(quota, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def run_once(args) -> int:
    rl = fetch_usage()
    claude_block = to_dashboard_claude(rl)
    print(json.dumps({"rate_limits": rl, "dashboard_claude": claude_block}, ensure_ascii=False, indent=2))
    if args.post:
        post_to_dashboard(claude_block, args.dashboard_url)
        print(f"posted to {args.dashboard_url}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Claude Code personal-account usage quota")
    parser.add_argument("--post", action="store_true", help="push result into the dashboard")
    parser.add_argument("--dashboard-url", default=DASHBOARD_URL)
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="loop every N seconds (>=60)")
    args = parser.parse_args()

    if not args.watch:
        try:
            return run_once(args)
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            if token_expired():
                print(
                    "hint: the stored Claude token looks expired. Open Claude Code once "
                    "(or run `claude`) to refresh it, then re-run this tool.",
                    file=sys.stderr,
                )
            return 1

    while True:
        try:
            run_once(args)
        except Exception as error:  # keep the loop alive
            print(f"error: {error}", file=sys.stderr)
        time.sleep(max(60, args.watch))


if __name__ == "__main__":
    raise SystemExit(main())
