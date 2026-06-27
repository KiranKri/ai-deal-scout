"""Telegram notifier for ai-deal-scout.

Formats deal dicts into human-readable messages and delivers them via
the Telegram Bot API using raw ``requests`` calls only.

Uses HTML parse_mode (not Markdown) to avoid silent failures caused by
special Markdown characters in deal titles and URLs (e.g. underscores in
Reddit slugs, brackets in titles, etc.).
"""

import html
import logging
import os
from typing import Any

import requests

from config import TELEGRAM_MAX_CHARS

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _credentials() -> tuple[str, str]:
    """Return (token, chat_id) from environment, or empty strings if unset."""
    return os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")


def _esc(text: str) -> str:
    """HTML-escape a string for Telegram HTML parse_mode.

    Escapes ``&``, ``<``, and ``>`` so that user-supplied text (titles,
    URLs, source names) cannot accidentally break the HTML structure or
    trigger a Telegram API parse error.
    """
    return html.escape(text, quote=False)


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

    # Debug: log exactly what format_deal produces for the first deal
    first_formatted = format_deal(deals[0])
    logger.info("format_deal output for first deal:\n%s", first_formatted)

    header = f"\U0001f916 <b>AI Deal Scout — {len(deals)} new deal(s) found:</b>"
    send_message(header)

    all_text = "\n".join(format_deal(d) for d in deals)
    chunks = chunk_messages(all_text)

    logger.info(
        "send_deals: %d deal(s) → %d chunk(s), sizes: %s",
        len(deals), len(chunks), [len(c) for c in chunks]
    )

    sent = 0
    for i, chunk in enumerate(chunks):
        logger.info(
            "send_deals: sending chunk %d/%d (%d chars)",
            i + 1, len(chunks), len(chunk)
        )
        if send_message(chunk):
            sent += 1

    logger.info(
        "send_deals: %d/%d chunk(s) successfully sent for %d deal(s)",
        sent, len(chunks), len(deals)
    )
