"""Telegram notifier for ai-deal-scout.

Formats deal dicts into human-readable messages and delivers them via
the Telegram Bot API using raw ``requests`` calls only.

Uses HTML parse_mode (not Markdown) to avoid silent failures caused by
special Markdown characters in deal titles and URLs (e.g. underscores in
Reddit slugs, brackets in titles, etc.).

V2 adds broadcast support: ``send_deals`` now accepts a ``chat_ids`` list
and fans out to all active subscribers with rate-limiting and automatic
deactivation of blocked (403) users.
"""

import html
import logging
import os
import time
from typing import Any

import requests

from bot import subscribers as subscribers_module
from config import TELEGRAM_MAX_CHARS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Telegram API
# ---------------------------------------------------------------------------

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# ---------------------------------------------------------------------------
# Rate-limiting / broadcast constants
# ---------------------------------------------------------------------------

BATCH_SIZE = 25       # users per batch
BATCH_SLEEP = 1.0     # seconds between batches
RETRY_SLEEP = 5.0     # seconds to wait on 429
MESSAGE_SLEEP = 0.3   # seconds after every message send (pacing)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _credentials() -> tuple[str, str]:
    """Return (token, chat_id) from environment, or empty strings if unset."""
    return os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")


def _esc(text: str) -> str:
    """HTML-escape a string for Telegram HTML parse mode.

    Escapes ``&``, ``<``, and ``>`` so that user-supplied text (titles,
    URLs, source names) cannot accidentally break the HTML structure or
    trigger a Telegram API parse error.
    """
    return html.escape(text, quote=False)


# ---------------------------------------------------------------------------
# Public formatting helpers (unchanged from V1)
# ---------------------------------------------------------------------------


def format_deal(deal: dict[str, Any]) -> str:
    """Format a single deal dict as a Telegram HTML text block.

    Uses ``<b>`` for bold title.  All user-supplied fields are HTML-escaped
    so that special characters in titles or URLs cannot cause a parse error.

    Args:
        deal: Deal dict with keys ``title``, ``source``, ``url``,
              ``upvotes``.

    Returns:
        Multi-line HTML string ending with the ``---`` separator.
    """
    title = _esc(deal.get("title", ""))
    source = _esc(deal.get("source", ""))
    url = _esc(deal.get("url", ""))

    lines: list[str] = [
        f"\U0001f525 <b>{title}</b>",
        f"\U0001f4cc Source: {source}",
        f"\U0001f517 {url}",
    ]
    if deal.get("upvotes", 0) > 0:
        lines.append(f"\U0001f44d {deal['upvotes']} upvotes")
    lines.append("---")
    return "\n".join(lines)


def chunk_messages(text: str, max_chars: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Split a formatted deals string into Telegram-sized chunks.

    Splits on the ``---`` separator so that individual deals are never
    broken across two messages.

    Args:
        text: Concatenated output of one or more ``format_deal`` calls.
        max_chars: Maximum characters per chunk (default ``TELEGRAM_MAX_CHARS``).

    Returns:
        List of strings each at most ``max_chars`` characters long.
    """
    blocks: list[str] = [
        b.strip() + "\n---" for b in text.split("---") if b.strip()
    ]

    chunks: list[str] = []
    current = ""

    for block in blocks:
        candidate = (current + "\n" + block) if current else block
        if len(candidate) > max_chars:
            if current:
                chunks.append(current)
            current = block
        else:
            current = candidate

    if current:
        chunks.append(current)

    logger.info(
        "chunk_messages: input length=%d, chunks produced=%d",
        len(text), len(chunks)
    )
    return chunks


def send_message(text: str) -> bool:
    """Send a single HTML-formatted message to the configured Telegram chat.

    Reads ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` from the
    environment.  If either is missing, logs a warning and returns
    ``False`` without raising.

    Uses ``parse_mode="HTML"`` to safely handle special characters in
    deal titles and URLs without silent Telegram parse failures.

    Args:
        text: Message text with optional HTML tags (``<b>``, ``<i>``, etc.).

    Returns:
        ``True`` on HTTP 200 with Telegram ``ok: true``, ``False`` otherwise.
    """
    token, chat_id = _credentials()

    if not token or not chat_id:
        logger.warning(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; skipping Telegram send"
        )
        return False

    url = _TELEGRAM_API.format(token=token)
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    logger.debug(
        "send_message: payload chat_id=%s parse_mode=HTML text=%r",
        chat_id, text
    )

    try:
        response = requests.post(url, json=payload, timeout=15)
        logger.info(
            "Telegram API: status=%d ok=%s",
            response.status_code,
            response.json().get("ok") if response.content else "n/a",
        )
        logger.debug("Telegram API full response: %s", response.text)

        if not response.ok:
            logger.error(
                "Telegram send failed: HTTP %d — %s",
                response.status_code, response.text
            )
            return False

        data = response.json()
        if not data.get("ok"):
            logger.error(
                "Telegram API returned ok=false: %s", data.get("description", data)
            )
            return False

        logger.debug("Telegram message accepted (%d chars)", len(text))
        return True

    except requests.RequestException as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# V2 private helper
# ---------------------------------------------------------------------------


def _send_with_retry(chat_id: int, text: str) -> int:
    """Send one message to one chat_id with a single 429 retry.

    Uses HTML parse_mode, consistent with ``format_deal`` output.

    Args:
        chat_id: Telegram chat ID to send to.
        text:    Message text (may contain HTML tags).

    Returns:
        HTTP status code of the final attempt (200 on success).
        Returns 0 on a ``requests`` exception.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    url = _TELEGRAM_API.format(token=token)
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 429:
            logger.warning(
                "429 for chat_id=%d, retrying after %ds", chat_id, RETRY_SLEEP
            )
            time.sleep(RETRY_SLEEP)
            r = requests.post(url, json=payload, timeout=10)
        if r.status_code not in (200, 403, 429):
            logger.error(
                "Telegram error %d for chat_id=%d", r.status_code, chat_id
            )
        return r.status_code
    except requests.RequestException as e:
        logger.error(
            "Telegram request failed for chat_id=%d: %s", chat_id, e
        )
        return 0


# ---------------------------------------------------------------------------
# V2 broadcast
# ---------------------------------------------------------------------------


def send_deals(deals: list[dict[str, Any]], chat_ids: list[int]) -> None:
    """Broadcast deals to all subscribers with rate limiting.

    Formats content once and fans it out to every chat ID in ``chat_ids``.
    Users are processed in batches of ``BATCH_SIZE`` with a ``BATCH_SLEEP``
    pause between batches to stay within Telegram's rate limits.

    A ``MESSAGE_SLEEP`` pause is inserted after every message send to pace
    delivery.  For small deal sets the header and all deal blocks fit in a
    single chunk; for larger sets ``chunk_messages`` splits them naturally.

    Users that return HTTP 403 (bot blocked) are collected and deactivated
    in a single ``batch_deactivate`` call at the end.

    Args:
        deals:    List of new deal dicts to broadcast.  Empty list sends a
                  "no new deals" notice.
        chat_ids: List of active subscriber chat IDs to send to.
    """
    if not chat_ids:
        logger.warning("No active subscribers — skipping Telegram send")
        return

    # Format content once, reuse for every recipient.
    # The header is embedded in the body so small deal sets fit in one chunk,
    # which keeps the per-user call count predictable for rate-limit purposes.
    if not deals:
        content_messages: list[str] = [
            "✅ AI Deal Scout ran — no new deals found."
        ]
    else:
        header = (
            f"\U0001f916 <b>AI Deal Scout — "
            f"{len(deals)} new deal(s) found:</b>"
        )
        body = "".join(format_deal(d) for d in deals)
        content_messages = chunk_messages(header + "\n\n" + body)

    logger.info(
        "send_deals: %d deal(s) → %d message(s) → %d subscriber(s)",
        len(deals), len(content_messages), len(chat_ids),
    )

    blocked_ids: list[int] = []
    success_count = 0

    for batch_start in range(0, len(chat_ids), BATCH_SIZE):
        batch = chat_ids[batch_start : batch_start + BATCH_SIZE]

        for chat_id in batch:
            user_success = True
            for msg in content_messages:
                status = _send_with_retry(chat_id, msg)
                # Pace every outbound message to avoid hitting rate limits.
                # This fires unconditionally so single-message sends are also
                # spaced, keeping the per-user sleep count predictable.
                time.sleep(MESSAGE_SLEEP)

                if status == 403:
                    blocked_ids.append(chat_id)
                    user_success = False
                    break
                elif status != 200:
                    user_success = False
                    break

            if user_success:
                success_count += 1

        # Sleep between batches (not after the last one)
        if batch_start + BATCH_SIZE < len(chat_ids):
            time.sleep(BATCH_SLEEP)

    if blocked_ids:
        logger.info("Auto-deactivating %d blocked user(s)", len(blocked_ids))
        subscribers_module.batch_deactivate(blocked_ids)

    logger.info(
        "Broadcast complete: %d/%d subscribers", success_count, len(chat_ids)
    )
