"""Flask webhook server for the ai-deal-scout V2 Telegram bot.

Receives Telegram webhook updates, validates the shared secret, and routes
each command to the appropriate handler.  Always returns HTTP 200 to
Telegram after the secret check passes to prevent retry floods.
"""

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, request

from bot import subscribers

# ---------------------------------------------------------------------------
# Module-level config
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
ADMIN_CHAT_ID: int = int(os.environ.get("ADMIN_CHAT_ID", "0"))
GH_PAT: str = os.environ.get("GH_PAT", "")
GH_REPO_DATA: str = os.environ.get("GH_REPO_DATA", "")

TELEGRAM_API: str = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)
logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


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
            "You'll get AI tool deals twice daily (8AM + 8PM IST).\n"
            "Send /stop anytime to unsubscribe.\n"
            "GitHub: github.com/KiranKri/ai-deal-scout",
        )
    elif status == "already_active":
        _send(chat_id, "✅ You're already subscribed! Deals arrive at 8AM + 8PM IST.")
    else:
        _send(chat_id, "⚠️ Subscription failed. Please try again later.")


def handle_stop(chat_id: int) -> None:
    """Handle the ``/stop`` command — unsubscribe the user.

    Args:
        chat_id: Telegram chat ID of the sender.
    """
    status = subscribers.deactivate_subscriber(chat_id)
    if status == "deactivated":
        _send(chat_id, "✅ Unsubscribed. Send /start anytime to resubscribe.")
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
        "Runs twice daily: 8AM + 8PM IST.\n\n"
        "Commands:\n"
        "/start — Subscribe\n"
        "/stop — Unsubscribe\n"
        "/help — Show this message",
    )


def handle_run(chat_id: int) -> None:
    """Handle the ``/run`` command — trigger the GitHub Actions workflow.

    Admin-only.  Dispatches ``workflow_dispatch`` on ``deal_scout.yml``
    in the main ``ai-deal-scout`` repo.

    Args:
        chat_id: Telegram chat ID of the sender.
    """
    if chat_id != ADMIN_CHAT_ID:
        _send(chat_id, "Unauthorized.")
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
            _send(chat_id, "✅ Pipeline triggered. Check Telegram in ~2 mins.")
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
    if not token or token != WEBHOOK_SECRET:
        return "Forbidden", 403

    try:
        update = request.get_json(silent=True)
        if update is None or "message" not in update:
            return "", 200

        msg = update["message"]
        chat_id: int = msg["chat"]["id"]
        username: str | None = msg["chat"].get("username")
        text: str = msg.get("text", "").strip()

        if text == "/start":
            handle_start(chat_id, username)
        elif text == "/stop":
            handle_stop(chat_id)
        elif text == "/help":
            handle_help(chat_id)
        elif text == "/run":
            handle_run(chat_id)
        elif text == "/status":
            handle_status(chat_id)
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
