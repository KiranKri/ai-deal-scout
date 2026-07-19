"""Verify every external credential actually works.

    python tools/verify_setup.py

Checks each service with a real (cheap, read-only) call rather than just
looking for a non-empty string, because a present-but-wrong key is the
failure mode that wastes the most time.

Reads from .env for local runs; in CI the workflow's env: block supplies the
same names.  Missing optional credentials are reported as SKIP, not FAIL —
the bot degrades gracefully without them.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )
    )
except ImportError:
    print("python-dotenv not installed; reading process environment only")

import re  # noqa: E402

import requests  # noqa: E402


def _redact(text: str) -> str:
    """Strip credential-shaped substrings from error text.

    requests puts the full request URL in its exception messages, which for
    Telegram embeds the bot token.  Printing that to a terminal — or into a
    pasted log — re-leaks the secret this tool exists to protect.
    """
    text = re.sub(r"bot\d{8,10}:[A-Za-z0-9_-]+", "bot<REDACTED>", text)
    text = re.sub(r"tvly-[A-Za-z0-9_-]+", "tvly-<REDACTED>", text)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9]+", "gh<REDACTED>", text)
    return text

OK, BAD, SKIP = "  OK  ", " FAIL ", " SKIP "


def check_telegram() -> tuple[str, str]:
    """Confirm the bot token is live and report the bot's @username."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return BAD, "TELEGRAM_BOT_TOKEN not set — the bot cannot send anything"
    try:
        data = requests.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=10
        ).json()
    except requests.RequestException as exc:
        return BAD, _redact(f"cannot reach Telegram: {exc}")
    if not data.get("ok"):
        return BAD, f"token rejected: {data.get('description')}"
    user = data["result"]
    return OK, f"@{user['username']} — share link https://t.me/{user['username']}"


def check_tavily() -> tuple[str, str]:
    """Spend one credit to confirm the search key works."""
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return SKIP, "TAVILY_API_KEY not set — web search disabled, other sources still run"
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": "test", "max_results": 1},
            timeout=20,
        )
    except requests.RequestException as exc:
        return BAD, _redact(f"cannot reach Tavily: {exc}")
    if r.status_code in (401, 403):
        return BAD, "key rejected — regenerate at tavily.com"
    if r.status_code == 432:
        return BAD, "plan limit reached this month"
    if r.status_code != 200:
        return BAD, f"unexpected HTTP {r.status_code}"
    return OK, "key valid (1 credit spent on this check)"


def check_github_data_repo() -> tuple[str, str]:
    """Confirm the PAT can READ and WRITE subscribers.json in the private repo."""
    pat = os.getenv("GH_PAT", "").strip()
    repo = os.getenv("GH_REPO_DATA", "").strip()
    if not (pat and repo):
        return SKIP, (
            "GH_PAT/GH_REPO_DATA not set locally — local runs use "
            "data/subscribers.json; CI uses the private repo"
        )

    url = f"https://api.github.com/repos/{repo}/contents/subscribers.json"
    headers = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        return BAD, _redact(f"cannot reach GitHub: {exc}")

    if r.status_code == 404:
        return BAD, (
            f"subscribers.json not found in {repo} — create it containing "
            '{"subscribers": [], "last_updated": ""}'
        )
    if r.status_code == 401:
        return BAD, "PAT rejected — expired or revoked"
    if r.status_code == 403:
        return BAD, f"PAT lacks access to {repo} (needs Contents: read and write)"
    if r.status_code != 200:
        return BAD, f"unexpected HTTP {r.status_code}"

    # A read alone does not prove write access, which is what actually matters.
    body = r.json()
    import base64
    import json as _json

    try:
        data = _json.loads(base64.b64decode(body["content"]).decode())
        count = len(data.get("subscribers", []))
    except Exception:  # noqa: BLE001
        return BAD, "subscribers.json is present but not valid JSON"

    w = requests.put(
        url,
        headers=headers,
        json={
            "message": "chore: verify write access [skip ci]",
            "content": body["content"],  # byte-identical: no real change
            "sha": body["sha"],
        },
        timeout=15,
    )
    if w.status_code not in (200, 201):
        return BAD, (
            f"read OK but WRITE failed (HTTP {w.status_code}) — "
            "PAT needs Contents: read and write"
        )
    return OK, f"{repo} readable and writable — {count} subscriber(s) stored"


def check_admin_id() -> tuple[str, str]:
    """Admin ID gates /run, /status and operational alerts."""
    raw = os.getenv("ADMIN_CHAT_ID", "").strip()
    if not raw:
        return SKIP, "ADMIN_CHAT_ID not set — admin commands and alerts disabled"
    if not raw.lstrip("-").isdigit():
        return BAD, f"ADMIN_CHAT_ID is not a number: {raw!r}"
    return OK, f"admin chat {raw}"


def main() -> None:
    print("\n  Verifying ai-deal-scout credentials\n")
    results = []
    for name, fn in [
        ("Telegram bot", check_telegram),
        ("Tavily search", check_tavily),
        ("GitHub data repo", check_github_data_repo),
        ("Admin chat ID", check_admin_id),
    ]:
        status, detail = fn()
        results.append(status)
        print(f"  [{status}] {name:18} {detail}")

    print()
    if BAD in results:
        print("  Something is broken above — fix before relying on a scheduled run.\n")
        sys.exit(1)
    print("  No blocking problems.\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
