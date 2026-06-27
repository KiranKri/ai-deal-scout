"""Generic RSS feed scraper for ai-deal-scout.

Fetches entries from AI company blog/news RSS feeds using feedparser.
"""

import logging
import time
from typing import Any

import feedparser

from config import RSS_FEEDS, SCRAPER_SLEEP_SECONDS

logger = logging.getLogger(__name__)


def fetch_rss_deals() -> list[dict[str, Any]]:
    """Fetch entries from all configured AI company RSS feeds.

    Iterates over every URL in ``RSS_FEEDS``, parses the feed with
    feedparser, and collects entries.  Feeds that fail or set the bozo flag
    are skipped with a warning.

    Returns:
        Flat list of deal dicts with keys:
        ``title``, ``url``, ``body``, ``upvotes``, ``source``.
    """
    results: list[dict[str, Any]] = []

    for feed_url in RSS_FEEDS:
        try:
            logger.debug("Fetching RSS feed: %s", feed_url)
            feed = feedparser.parse(feed_url)

            if feed.bozo:
                logger.warning(
                    "RSS feed parse warning (bozo) for %s: %s",
                    feed_url,
                    feed.get("bozo_exception", "unknown error"),
                )
                time.sleep(SCRAPER_SLEEP_SECONDS)
                continue

            for entry in feed.entries:
                url: str = entry.get("link", "")
                if not url:
                    continue

                title: str = entry.get("title", "").strip()
                raw_summary: str = entry.get("summary", "") or ""
                body: str = raw_summary[:300]

                results.append(
                    {
                        "title": title,
                        "url": url,
                        "body": body,
                        "upvotes": 0,
                        "source": "RSS",
                    }
                )

            logger.debug("RSS feed %s yielded %d entries", feed_url, len(feed.entries))

        except Exception as exc:  # noqa: BLE001
            logger.error("RSS feed %s failed: %s", feed_url, exc, exc_info=True)

        time.sleep(SCRAPER_SLEEP_SECONDS)

    logger.info("RSS scraper finished: %d results", len(results))
    return results
