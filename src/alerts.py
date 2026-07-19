"""Operator alerting for ai-deal-scout.

One channel: a Telegram message to ``ADMIN_CHAT_ID``.  Used by the pipeline
(``main.py``) and the notifier so silent failures — nobody received anything,
a subscriber was auto-deactivated, a store is unreachable — become visible
without any external monitoring service.

Best-effort by design: alerting must never take the pipeline down.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)


def alert_admin(text: str) -> None:
    """Send an operational alert to the admin chat via Telegram.

    Requires ``ADMIN_CHAT_ID`` and ``TELEGRAM_BOT_TOKEN`` in the environment.
    Logs at WARNING (not DEBUG) when they are missing — an unset admin ID is
    itself a silent-failure risk: every alert in the system silently no-ops.
    Never raises.
    """
    admin = os.getenv("ADMIN_CHAT_ID", "").strip()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not admin or not token:
        logger.warning(
            "alert_admin: ADMIN_CHAT_ID/TELEGRAM_BOT_TOKEN unset — "
            "operator alert DROPPED: %s", text,
        )
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": int(admin), "text": f"⚠️ deal-scout: {text}"},
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        logger.exception("alert_admin failed")
