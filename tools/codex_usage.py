"""Fetch the real Codex usage quota (5h + 7d windows) for the locally logged-in
ChatGPT account and optionally push it into the Wio dashboard.

How it works
------------
A ChatGPT-login Codex account (``auth_mode = "chatgpt"`` in ~/.codex/auth.json)
does not store its quota in any file. There are two official sources:

  * GET https://chatgpt.com/backend-api/wham/usage  (default, no token cost)
        -> JSON rate_limit.five_hour / .weekly with percent_left + reset_at.
  * The x-codex-* response headers on /backend-api/codex/responses (fallback):
        x-codex-primary-*   -> 5h window,   x-codex-secondary-*  -> 7d window.

This script:
  1. Loads ~/.codex/auth.json (override with CODEX_HOME).
  2. Refreshes the OAuth access token when needed (auth.openai.com), exactly like
     the Codex CLI, and writes the rotated tokens back so the login stays valid.
  3. Queries the usage endpoint (or the responses headers with --method headers).
  4. Prints the parsed usage and, with --post, updates data/quota.json via the
     dashboard's POST /api/quota.

Usage
-----
  py tools/codex_usage.py                 # print usage as JSON
  py tools/codex_usage.py --post          # also push into the running dashboard
  py tools/codex_usage.py --method headers  # use responses x-codex-* headers
  py tools/codex_usage.py --watch 300     # refresh every 300s and keep posting
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "quota.json"

CODEX_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
AUTH_FILE = CODEX_HOME / "auth.json"

# Official ChatGPT-login Codex backend. Override with CODEX_BACKEND if needed.
DEFAULT_BACKEND = os.environ.get("CODEX_BACKEND", "https://chatgpt.com/backend-api/codex")
USAGE_URL = os.environ.get("CODEX_USAGE_URL", "https://chatgpt.com/backend-api/wham/usage")
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_CLIENT_ID = os.environ.get("CODEX_OAUTH_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8765/api/quota")
DEFAULT_MODEL = os.environ.get("CODEX_USAGE_MODEL", "gpt-5.5")


def _b64url_json(segment: str) -> dict:
    pad = "=" * (-len(segment) % 4)
    import base64

    return json.loads(base64.urlsafe_b64decode(segment + pad).decode("utf-8"))


def decode_jwt(token: str) -> dict:
    try:
        return _b64url_json(token.split(".")[1])
    except Exception:
        return {}


def load_auth() -> dict:
    return json.loads(AUTH_FILE.read_text("utf-8"))


def save_auth(auth: dict) -> None:
    tmp = AUTH_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(auth, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(tmp, AUTH_FILE)


def token_expired(access_token: str, skew_seconds: int = 120) -> bool:
    claims = decode_jwt(access_token)
    exp = claims.get("exp")
    if not exp:
        return True
    return time.time() + skew_seconds >= float(exp)


def refresh_tokens(auth: dict) -> dict:
    """Rotate the OAuth tokens via auth.openai.com and persist them."""
    tokens = auth.get("tokens") or {}
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("auth.json has no refresh_token; run `codex login` first.")

    payload = json.dumps(
        {
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "openid profile email",
        }
    ).encode("utf-8")

    req = urllib.request.Request(OAUTH_TOKEN_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read(500).decode("utf-8", "replace")
        raise RuntimeError(f"token refresh failed ({e.code}): {detail}") from None

    tokens["access_token"] = body.get("access_token", tokens.get("access_token"))
    if body.get("id_token"):
        tokens["id_token"] = body["id_token"]
    if body.get("refresh_token"):
        tokens["refresh_token"] = body["refresh_token"]
    auth["tokens"] = tokens
    auth["last_refresh"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    save_auth(auth)
    return auth


def build_request(backend: str, access_token: str, account_id: str | None) -> urllib.request.Request:
    body = {
        "model": DEFAULT_MODEL,
        "instructions": "ping",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}
        ],
        "stream": True,
        "store": False,
        "tools": [],
    }
    req = urllib.request.Request(
        backend.rstrip("/") + "/responses",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {access_token}")
    if account_id:
        req.add_header("chatgpt-account-id", account_id)
    req.add_header("Content-Type", "application/json")
    req.add_header("OpenAI-Beta", "responses=experimental")
    req.add_header("originator", "codex_cli_rs")
    req.add_header("session_id", str(uuid.uuid4()))
    req.add_header("Accept", "text/event-stream")
    req.add_header("User-Agent", "codex_cli_rs/quota-probe")
    return req


def _window_from_headers(headers, prefix: str) -> dict | None:
    used = headers.get(f"{prefix}-used-percent")
    if used is None:
        return None
    window = headers.get(f"{prefix}-window-minutes")
    reset_at = headers.get(f"{prefix}-reset-at")
    reset_after = headers.get(f"{prefix}-reset-after-seconds") or headers.get(
        f"{prefix}-reset-in-seconds"
    )
    out = {"used_percent": float(used)}
    if window is not None:
        out["window_minutes"] = int(float(window))
    if reset_at is not None:
        out["resets_at"] = int(float(reset_at))
    elif reset_after is not None:
        out["resets_at"] = int(time.time() + float(reset_after))
    return out


def parse_rate_limit_headers(headers) -> dict | None:
    lower = {k.lower(): v for k, v in headers.items()}
    primary = _window_from_headers(lower, "x-codex-primary")
    secondary = _window_from_headers(lower, "x-codex-secondary")
    if primary is None and secondary is None:
        return None
    return {"primary": primary, "secondary": secondary, "plan_type": lower.get("x-codex-plan-type")}


def parse_rate_limit_sse(resp) -> dict | None:
    """Read a few SSE lines looking for a rate_limits payload, then stop."""
    deadline = time.time() + 12
    for _ in range(200):
        if time.time() > deadline:
            break
        raw = resp.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if chunk in ("", "[DONE]"):
            continue
        try:
            event = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        rl = event.get("rate_limits")
        if not rl and isinstance(event.get("response"), dict):
            rl = event["response"].get("rate_limits")
        if rl and (rl.get("primary") or rl.get("secondary")):
            return rl
    return None


def _norm_window(raw: dict | None) -> dict | None:
    """Normalize a wham/usage window into {used_percent, window_minutes, resets_at}."""
    if not isinstance(raw, dict):
        return None
    if raw.get("used_percent") is None and raw.get("percent_left") is None \
            and isinstance(raw.get("primary_window"), dict):
        raw = raw["primary_window"]
    used = raw.get("used_percent")
    if used is None:
        left = raw.get("percent_left")
        if left is None:
            left = raw.get("remaining_percent")
        used = (100 - float(left)) if left is not None else None
    if used is None:
        return None
    reset = raw.get("reset_at")
    if reset is None:
        reset = raw.get("reset_time_ms")
    resets_at = None
    if reset is not None:
        try:
            r = int(reset)
            resets_at = r // 1000 if r > 10**11 else r
        except (TypeError, ValueError):
            resets_at = None
    window_seconds = raw.get("limit_window_seconds")
    out = {"used_percent": float(used), "resets_at": resets_at}
    if isinstance(window_seconds, (int, float)):
        out["window_minutes"] = int(window_seconds // 60)
    return out


def fetch_usage_wham(auth: dict, allow_refresh: bool = True) -> dict:
    """Query GET /backend-api/wham/usage (no model-token cost)."""
    tokens = auth.get("tokens") or {}
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")

    if allow_refresh and token_expired(access_token):
        auth = refresh_tokens(auth)
        tokens = auth["tokens"]
        access_token = tokens["access_token"]

    def _attempt(tok):
        req = urllib.request.Request(USAGE_URL, method="GET")
        req.add_header("Authorization", f"Bearer {tok}")
        req.add_header("Accept", "application/json")
        if account_id:
            req.add_header("ChatGPT-Account-Id", account_id)
        req.add_header("Origin", "https://chatgpt.com")
        req.add_header("Referer", "https://chatgpt.com/")
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        data = _attempt(access_token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and allow_refresh:
            auth = refresh_tokens(auth)
            data = _attempt(auth["tokens"]["access_token"])
        else:
            detail = e.read(400).decode("utf-8", "replace")
            raise RuntimeError(f"usage endpoint error ({e.code}): {detail}") from None

    rl = data.get("rate_limit") or data.get("rate_limits") or data
    primary = _norm_window(rl.get("five_hour") or rl.get("five_hour_limit") or rl.get("primary") or rl.get("primary_window"))
    secondary = _norm_window(rl.get("weekly") or rl.get("weekly_limit") or rl.get("secondary") or rl.get("secondary_window"))
    if primary is None and secondary is None:
        raise RuntimeError(f"usage endpoint returned no 5h/weekly windows: {json.dumps(data)[:400]}")
    return {"primary": primary, "secondary": secondary, "plan_type": data.get("plan_type")}


def fetch_rate_limits(backend: str, auth: dict, allow_refresh: bool = True) -> dict:
    tokens = auth.get("tokens") or {}
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id") or decode_jwt(tokens.get("id_token", "")).get(
        "https://api.openai.com/auth", {}
    ).get("chatgpt_account_id")

    if allow_refresh and token_expired(access_token):
        auth = refresh_tokens(auth)
        tokens = auth["tokens"]
        access_token = tokens["access_token"]

    def _attempt(tok):
        req = build_request(backend, tok, account_id)
        with urllib.request.urlopen(req, timeout=45) as resp:
            rl = parse_rate_limit_headers(resp.headers)
            if rl is None:
                rl = parse_rate_limit_sse(resp)
            return rl

    try:
        rl = _attempt(access_token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and allow_refresh:
            auth = refresh_tokens(auth)
            access_token = auth["tokens"]["access_token"]
            rl = _attempt(access_token)
        else:
            detail = e.read(400).decode("utf-8", "replace")
            raise RuntimeError(f"backend error ({e.code}): {detail}") from None

    if rl is None:
        raise RuntimeError(
            "No rate-limit data returned. The configured backend does not expose "
            "x-codex-* headers (a relay/proxy will strip them). Point CODEX_BACKEND "
            "at the official https://chatgpt.com/backend-api/codex endpoint."
        )
    return rl


def _clock(ts: int | None, with_date: bool) -> str:
    if not ts:
        return "--"
    dt = datetime.fromtimestamp(ts).astimezone()
    return dt.strftime("%m/%d %H:%M") if with_date else dt.strftime("%H:%M")


def to_dashboard_codex(rl: dict) -> dict:
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


def post_to_dashboard(codex_block: dict, url: str) -> None:
    try:
        quota = json.loads(DATA_FILE.read_text("utf-8"))
    except Exception:
        quota = {"footer": {}, "platforms": {}}
    quota.setdefault("platforms", {})["codex"] = codex_block
    now = datetime.now().astimezone().strftime("%H:%M")
    quota["updatedAt"] = now
    quota.setdefault("footer", {})["time"] = now

    data = json.dumps(quota, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def run_once(args) -> int:
    auth = load_auth()
    if auth.get("auth_mode") != "chatgpt":
        print("warning: auth_mode is not 'chatgpt'; quota headers may be unavailable.", file=sys.stderr)
    if args.method == "headers":
        rl = fetch_rate_limits(args.backend, auth, allow_refresh=not args.no_refresh)
    else:
        rl = fetch_usage_wham(auth, allow_refresh=not args.no_refresh)
    codex_block = to_dashboard_codex(rl)

    print(json.dumps({"rate_limits": rl, "dashboard_codex": codex_block}, ensure_ascii=False, indent=2))

    if args.post:
        post_to_dashboard(codex_block, args.dashboard_url)
        print(f"posted to {args.dashboard_url}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Codex personal-account usage quota")
    parser.add_argument(
        "--method",
        choices=["usage", "headers"],
        default="usage",
        help="usage = GET wham/usage (default, no token cost); headers = parse x-codex-* response headers",
    )
    parser.add_argument("--backend", default=DEFAULT_BACKEND, help="Codex responses backend base URL (for --method headers)")
    parser.add_argument("--post", action="store_true", help="push result into the dashboard")
    parser.add_argument("--dashboard-url", default=DASHBOARD_URL)
    parser.add_argument("--no-refresh", action="store_true", help="never refresh the OAuth token")
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="loop every N seconds")
    args = parser.parse_args()

    if not args.watch:
        try:
            return run_once(args)
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            if "app_session_terminated" in str(error) or "token_invalidated" in str(error):
                print(
                    "hint: this account's local token is no longer valid for OpenAI "
                    "(commonly because it was re-logged-in elsewhere). Re-authenticate with "
                    "`codex login` against the official account, then re-run this tool.",
                    file=sys.stderr,
                )
            return 1

    while True:
        try:
            run_once(args)
        except Exception as error:  # keep the loop alive
            print(f"error: {error}", file=sys.stderr)
        time.sleep(max(30, args.watch))


if __name__ == "__main__":
    raise SystemExit(main())
