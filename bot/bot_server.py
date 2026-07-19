"""Flask webhook server for the ai-deal-scout V2 Telegram bot.

Receives Telegram webhook updates, validates the shared secret, and routes
each command to the appropriate handler.  Always returns HTTP 200 to
Telegram after the secret check passes to prevent retry floods.
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, request

from bot import subscribers

# Local runs read .env; on Render the dashboard supplies real env vars.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - optional dependency
    pass

# ---------------------------------------------------------------------------
# Module-level config
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
try:
    ADMIN_CHAT_ID: int = int(os.environ.get("ADMIN_CHAT_ID", "0").strip() or "0")
except ValueError:
    logging.getLogger(__name__).error(
        "ADMIN_CHAT_ID is not a valid integer; admin commands disabled"
    )
    ADMIN_CHAT_ID = 0
GH_PAT: str = os.environ.get("GH_PAT", "")
GH_REPO_DATA: str = os.environ.get("GH_REPO_DATA", "")

# Extra chat IDs allowed to /run (comma-separated). Admin is always allowed.
def _parse_allowlist(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logging.getLogger(__name__).warning("Ignoring non-integer RUN_ALLOWLIST entry %r", part)
    return ids


RUN_ALLOWLIST: set[int] = _parse_allowlist(os.environ.get("RUN_ALLOWLIST", ""))
RUN_RATE_LIMIT_SECONDS: int = int(os.environ.get("RUN_RATE_LIMIT_SECONDS", "3600"))
RUN_GLOBAL_DAILY_CEILING: int = int(os.environ.get("RUN_GLOBAL_DAILY_CEILING", "10"))
RUN_QUOTA_STATE_PATH: str = os.environ.get(
    "RUN_QUOTA_STATE_PATH", "data/run_quota.json"
)
WEBSEARCH_STATE_PATH: str = os.environ.get(
    "WEBSEARCH_STATE_PATH", "data/websearch_state.json"
)
WEBSEARCH_MONTHLY_QUOTA: int = int(os.environ.get("WEBSEARCH_MONTHLY_QUOTA", "900"))

TELEGRAM_API: str = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)
logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def normalize_command(text: str) -> str:
    """Extract the bare command from a message.

    Handles the ``/start@MyBot`` form Telegram sends from group chats and
    normalises case.  Returns ``""`` for empty/non-text messages.

    Shared by the webhook route and ``poll.dispatch`` so the two transports
    cannot diverge on command parsing.
    """
    return text.split()[0].split("@")[0].lower() if text else ""


def _send(chat_id: int, text: str) -> bool:
    """Send a plain-text message to a Telegram chat.

    Args:
        chat_id: Telegram chat ID to send the message to.
        text:    Message body (plain text, no parse_mode).

    Returns:
        ``True`` on HTTP 200, ``False`` on any error.  Never raises.
    """
    try:
        response = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        if response.status_code != 200:
            logger.error(
                "_send failed: chat_id=%d status=%d body=%s",
                chat_id, response.status_code, response.text,
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("_send exception: chat_id=%d error=%s", chat_id, exc)
        return False


def _can_run(chat_id: int) -> bool:
    """Return True when *chat_id* is admin or on the RUN_ALLOWLIST."""
    if ADMIN_CHAT_ID and chat_id == ADMIN_CHAT_ID:
        return True
    return chat_id in RUN_ALLOWLIST


def _user_key(chat_id: int) -> str:
    """Return a stable, non-reversible key for a chat ID.

    ``run_quota.json`` is committed to THIS repo (the rate limit must survive
    the ephemeral CI runner), and this repo is intended to be public.  Storing
    raw chat IDs there would leak subscriber identities exactly as an earlier
    bug did via ``subscribers.json``.

    Rate limiting only needs a *stable* key, never the original value, so a
    truncated SHA-256 is sufficient and leaks nothing.
    """
    return hashlib.sha256(str(chat_id).encode()).hexdigest()[:16]


def _load_run_quota() -> dict:
    """Load per-user /run timestamps and daily counter.  Never raises."""
    today = datetime.now(tz=_IST).strftime("%Y-%m-%d")
    default: dict = {"day": today, "global_count": 0, "users": {}}
    if not os.path.exists(RUN_QUOTA_STATE_PATH):
        return default
    try:
        with open(RUN_QUOTA_STATE_PATH, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("run_quota unreadable (%s); resetting", exc)
        return default
    if state.get("day") != today:
        return default
    default["global_count"] = int(state.get("global_count", 0))
    default["users"] = {
        str(k): str(v) for k, v in (state.get("users") or {}).items()
    }
    return default


def _save_run_quota(state: dict) -> None:
    """Persist run-quota state.  Never raises."""
    try:
        os.makedirs(os.path.dirname(RUN_QUOTA_STATE_PATH) or ".", exist_ok=True)
        with open(RUN_QUOTA_STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError as exc:
        logger.error("failed to save run_quota: %s", exc)


def _check_run_rate_limit(chat_id: int) -> str | None:
    """Return an error message if *chat_id* is rate-limited, else None."""
    state = _load_run_quota()
    if state["global_count"] >= RUN_GLOBAL_DAILY_CEILING:
        return (
            f"⚠️ Global daily /run ceiling reached "
            f"({RUN_GLOBAL_DAILY_CEILING}/{RUN_GLOBAL_DAILY_CEILING} today IST). "
            f"Try again after midnight IST."
        )
    last_iso = state["users"].get(_user_key(chat_id))
    if last_iso:
        try:
            last = datetime.fromisoformat(last_iso)
            if last.tzinfo is None:
                last = last.replace(tzinfo=_IST)
            elapsed = (datetime.now(tz=_IST) - last).total_seconds()
            remaining = RUN_RATE_LIMIT_SECONDS - elapsed
            if remaining > 0:
                mins = int(remaining // 60) + (1 if remaining % 60 else 0)
                retry_at = last + timedelta(seconds=RUN_RATE_LIMIT_SECONDS)
                return (
                    f"⚠️ Rate limited: one /run per user per hour. "
                    f"Try again in ~{mins} min "
                    f"(after {retry_at.strftime('%H:%M %Z')})."
                )
        except ValueError:
            pass
    return None


def _record_run(chat_id: int) -> None:
    """Record a successful /run for rate-limit accounting."""
    state = _load_run_quota()
    state["global_count"] = int(state.get("global_count", 0)) + 1
    state["users"][_user_key(chat_id)] = datetime.now(tz=_IST).isoformat()
    _save_run_quota(state)


def _tavily_quota_line() -> str:
    """Human-readable Tavily monthly usage from websearch_state.json."""
    month = datetime.now(tz=_IST).strftime("%Y-%m")
    used = 0
    if os.path.exists(WEBSEARCH_STATE_PATH):
        try:
            with open(WEBSEARCH_STATE_PATH, encoding="utf-8") as fh:
                st = json.load(fh)
            if st.get("month") == month:
                used = int(st.get("used", 0))
        except (OSError, ValueError, TypeError):
            pass
    remaining = max(0, WEBSEARCH_MONTHLY_QUOTA - used)
    return (
        f"Tavily (websearch) this month ({month} IST): "
        f"{used}/{WEBSEARCH_MONTHLY_QUOTA} used, {remaining} left "
        f"(as of the last pipeline run committed to the repo)"
    )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------



def _notify_admin_membership(chat_id: int, username: str | None, status: str) -> None:
    """Tell the admin when someone subscribes or unsubscribes.

    Subscriber growth was previously only observable by *asking* — running
    /status, or reading the private data repo by hand.  A bot nobody has
    joined and a bot whose subscribe path is broken look identical from the
    outside, so silence had to be interrogated rather than trusted.

    The admin's own /start is skipped: it is self-testing noise, not signal.
    Best-effort — a failed alert must never break the subscribe path itself.
    """
    if not ADMIN_CHAT_ID or chat_id == ADMIN_CHAT_ID:
        return
    counts = subscribers.get_subscriber_count()
    verb = {
        "new": "NEW subscriber",
        "resubscribed": "RE-subscribed",
        "deactivated": "unsubscribed",
    }.get(status, status)
    who = f"@{username}" if username else f"chat {chat_id}"
    try:
        _send(
            ADMIN_CHAT_ID,
            f"👤 {verb}: {who}\n"
            f"Active: {counts['active']} (total {counts['total']})",
        )
    except Exception:  # noqa: BLE001
        logger.exception("membership alert failed")


def handle_start(chat_id: int, username: str | None) -> None:
    """Handle the ``/start`` command — subscribe the user.

    Args:
        chat_id:  Telegram chat ID of the sender.
        username: Telegram ``@username``, or ``None`` if not set.
    """
    status = subscribers.add_subscriber(chat_id, username)
    if status in ("new", "resubscribed"):
        _send(
            chat_id,
            "✅ You're subscribed to AI Deal Scout!\n"
            "You'll get AI tool deals every morning at 8AM IST — "
            "even on days with no deals, so silence means something is wrong.\n"
            "Send /stop anytime to unsubscribe.\n"
            "GitHub: github.com/KiranKri/ai-deal-scout",
        )
        _notify_admin_membership(chat_id, username, status)
    elif status == "already_active":
        _send(chat_id, "✅ You're already subscribed! Deals arrive daily at 8AM IST.")
    else:
        _send(chat_id, "⚠️ Subscription failed. Please try again later.")
        # Operator alert: a real person tried to subscribe and the store
        # write failed — without this, only the user ever knows.
        if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
            _send(
                ADMIN_CHAT_ID,
                f"⚠️ deal-scout: /start FAILED for chat_id={chat_id} "
                f"(subscriber store write error) — check GH_PAT / data repo",
            )


def handle_stop(chat_id: int) -> None:
    """Handle the ``/stop`` command — unsubscribe the user.

    Args:
        chat_id: Telegram chat ID of the sender.
    """
    status = subscribers.deactivate_subscriber(chat_id)
    if status == "deactivated":
        _send(chat_id, "✅ Unsubscribed. Send /start anytime to resubscribe.")
        _notify_admin_membership(chat_id, None, status)
    elif status in ("already_inactive", "not_found"):
        _send(chat_id, "You're not currently subscribed. Send /start to subscribe.")
    else:
        _send(chat_id, "⚠️ Something went wrong. Please try again.")


def handle_help(chat_id: int) -> None:
    """Handle the ``/help`` command — show available commands.

    Args:
        chat_id: Telegram chat ID of the sender.
    """
    _send(
        chat_id,
        "🤖 AI Deal Scout\n\n"
        "I find deals, promo codes and free trials for AI tools.\n"
        "Runs daily at 8AM IST.\n\n"
        "Commands:\n"
        "/start — Subscribe\n"
        "/stop — Unsubscribe\n"
        "/help — Show this message\n"
        "/run — Trigger a pipeline run (allowlisted testers; rate-limited)\n"
        "/quota — Tavily websearch credits used this month",
    )


def handle_run(chat_id: int) -> None:
    """Handle the ``/run`` command — trigger the GitHub Actions workflow.

    Allowed for ``ADMIN_CHAT_ID`` and any ID in ``RUN_ALLOWLIST``.
    Rate-limited per user (default 1/hour) and globally per IST day
    to protect the Tavily monthly query budget (~12 credits per run).

    Args:
        chat_id: Telegram chat ID of the sender.
    """
    if not _can_run(chat_id):
        _send(chat_id, "Unauthorized.")
        return

    limited = _check_run_rate_limit(chat_id)
    if limited:
        _send(chat_id, limited)
        return

    owner = GH_REPO_DATA.split("/")[0] if GH_REPO_DATA else ""
    workflow_url = (
        f"https://api.github.com/repos/{owner}/ai-deal-scout"
        f"/actions/workflows/deal_scout.yml/dispatches"
    )

    try:
        response = requests.post(
            workflow_url,
            headers={
                "Authorization": f"Bearer {GH_PAT}",
                "Accept": "application/vnd.github+json",
            },
            json={"ref": "main"},
            timeout=15,
        )
        if response.status_code == 204:
            _record_run(chat_id)
            _send(
                chat_id,
                "✅ Pipeline triggered. Check Telegram in ~2 mins.\n"
                f"({_tavily_quota_line()})",
            )
        else:
            logger.error("workflow dispatch failed: %s", response.status_code)
            _send(chat_id, "⚠️ Failed to trigger pipeline. Check logs.")
    except Exception as exc:  # noqa: BLE001
        logger.error("workflow dispatch exception: %s", exc)
        _send(chat_id, "⚠️ Failed to trigger pipeline. Check logs.")


def handle_status(chat_id: int) -> None:
    """Handle the ``/status`` command — show subscriber counts.

    Admin-only.

    Args:
        chat_id: Telegram chat ID of the sender.
    """
    if chat_id != ADMIN_CHAT_ID:
        _send(chat_id, "Unauthorized.")
        return

    counts = subscribers.get_subscriber_count()
    _send(
        chat_id,
        f"📊 AI Deal Scout Status\n"
        f"Total subscribers: {counts['total']}\n"
        f"Active: {counts['active']}\n"
        f"Inactive: {counts['inactive']}",
    )


def handle_quota(chat_id: int) -> None:
    """Handle ``/quota`` — show Tavily monthly usage and /run ceilings.

    Available to anyone who can /run (admin + allowlist).
    """
    if not _can_run(chat_id):
        _send(chat_id, "Unauthorized.")
        return
    state = _load_run_quota()
    _send(
        chat_id,
        f"📊 Quota\n"
        f"{_tavily_quota_line()}\n"
        f"Manual /run today (IST): {state['global_count']}/{RUN_GLOBAL_DAILY_CEILING}\n"
        f"Per-user cooldown: {RUN_RATE_LIMIT_SECONDS // 60} minutes",
    )


def handle_unknown(chat_id: int) -> None:
    """Handle any unrecognised command.

    Args:
        chat_id: Telegram chat ID of the sender.
    """
    _send(chat_id, "Unknown command. Send /help to see available commands.")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/health", methods=["GET"])
def health():
    """Return a simple health-check response.

    No authentication required.

    Returns:
        JSON ``{"status": "ok", "timestamp": "<IST time>"}`` with HTTP 200.
    """
    ist_now = datetime.now(tz=_IST).isoformat()
    return jsonify({"status": "ok", "timestamp": ist_now}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """Receive and dispatch Telegram webhook updates.

    Validates the ``X-Telegram-Bot-Api-Secret-Token`` header first.
    After validation always returns HTTP 200 to prevent Telegram retry
    floods — errors are handled internally and logged.

    Returns:
        ``("", 200)`` on success or handled error, ``("Forbidden", 403)``
        on secret mismatch.
    """
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not token or not WEBHOOK_SECRET or not hmac.compare_digest(token, WEBHOOK_SECRET):
        return "Forbidden", 403

    try:
        update = request.get_json(silent=True)
        if update is None or "message" not in update:
            return "", 200

        msg = update["message"]
        chat_id: int = msg["chat"]["id"]
        username: str | None = msg["chat"].get("username")
        text: str = msg.get("text", "").strip()

        # Same normalisation as poll.dispatch: handles "/start@MyBot" from
        # group chats and case differences.
        command = normalize_command(text)

        if command == "/start":
            handle_start(chat_id, username)
        elif command == "/stop":
            handle_stop(chat_id)
        elif command == "/help":
            handle_help(chat_id)
        elif command == "/run":
            handle_run(chat_id)
        elif command == "/status":
            handle_status(chat_id)
        elif command == "/quota":
            handle_quota(chat_id)
        else:
            handle_unknown(chat_id)

    except Exception:
        logger.exception("webhook error")

    return "", 200


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
