"""Telegram notifier for ai-deal-scout.

Formats deal dicts into human-readable messages and delivers them via
the Telegram Bot API using raw ``requests`` calls only.
"""

import logging
import os
from typing import Any

import requests

from config import TELEGRAM_MAX_CHARS

def _credentials() -> tuple[str, str]:
    """Return (token, chat_id) from environment, or empty strings if unset."""
    return os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def format_deal(deal: dict[str, Any]) -> str:
    """Format a single deal dict as a Telegram-ready text block.

    Args:
        deal: Deal dict with keys ``title``, ``source``, ``url``,
              ``upvotes``.

    Returns:
        Multi-line string ending with the ``---`` separator.
    """
    lines: list[str] = [
        f"\U0001f525 {deal.get('title', '')}",
        f"\U0001f4cc Source: {deal.get('source', '')}",
        f"\U0001f517 {deal.get('url', '')}",
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
    # Each deal block ends with "---"; split produces the deal content
    # between separators.  Re-attach the separator to each block so the
    # reader sees the divider in every chunk.
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

    return chunks


def send_message(text: str) -> bool:
    """Send a single text message to the configured Telegram chat.

    Reads ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` from the
    environment.  If either is missing, logs a warning and returns
    ``False`` without raising.

    Args:
        text: Message text (Markdown formatting supported).

    Returns:
        ``True`` on HTTP 200, ``False`` on any error.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.warning(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; skipping Telegram send"
        )
        return False

    url = _TELEGRAM_API.format(token=token)
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        logger.debug("Telegram message sent (%d chars)", len(text))
        return True
    except requests.RequestException as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def send_deals(deals: list[dict[str, Any]]) -> None:
    """Send all new deals to Telegram, or a "no new deals" notice.

    Formats every deal, splits the result into Telegram-safe chunks,
    and sends each chunk as a separate message.  If Telegram credentials
    are absent the function logs a single warning and returns immediately.

    Args:
        deals: List of new deal dicts to notify about.
    """
    token, chat_id = _credentials()
    if not token or not chat_id:
        logger.warning(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; skipping Telegram send"
        )
        return

    if not deals:
        send_message("✅ AI Deal Scout ran — no new deals found.")
        return

    send_message(f"\U0001f916 *AI Deal Scout — {len(deals)} new deal(s) found:*")

    all_text = "\n".join(format_deal(d) for d in deals)
    chunks = chunk_messages(all_text)

    sent = 0
    for chunk in chunks:
        if send_message(chunk):
            sent += 1

    logger.info(
        "send_deals: %d chunk(s) sent for %d deal(s)", sent, len(deals)
    )
