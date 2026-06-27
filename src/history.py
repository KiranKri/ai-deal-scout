"""Deal history logger for ai-deal-scout.

Appends each run's new deals to a Markdown file so the full history is
human-readable without querying any external service.
"""

import logging
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import HISTORY_PATH

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")


def append_deals(deals: list[dict[str, Any]]) -> None:
    """Append a run block to the Markdown history file.

    Does nothing when ``deals`` is empty so that no-op runs leave no
    trace in the history file.

    Each block has the form::

        ## Run: 2025-01-01 08:00:00 IST
        - [Title](url) | Source | 👍 N
        - ...

    Args:
        deals: New deals found in this run.  If empty the function
               returns immediately without writing anything.
    """
    if not deals:
        logger.debug("history.append_deals: no deals to write")
        return

    timestamp = datetime.now(tz=_IST).strftime("%Y-%m-%d %H:%M:%S IST")

    lines: list[str] = [f"## Run: {timestamp}"]
    for deal in deals:
        title: str = deal.get("title", "")
        url: str = deal.get("url", "")
        source: str = deal.get("source", "")
        upvotes: int = deal.get("upvotes", 0)
        lines.append(f"- [{title}]({url}) | {source} | \U0001f44d {upvotes}")
    lines.append("")  # blank line after block

    os.makedirs(os.path.dirname(HISTORY_PATH) or ".", exist_ok=True)

    try:
        with open(HISTORY_PATH, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        logger.info("history: wrote %d deal(s) to %s", len(deals), HISTORY_PATH)
    except OSError as exc:
        logger.error("history: failed to write %s: %s", HISTORY_PATH, exc)
