"""Reddit RSS scraper for ai-deal-scout.

Fetches deal posts from subreddit RSS feeds using feedparser only —
no Reddit API credentials required.
"""

import logging
import time
from typing import Any

import feedparser

from config import REDDIT_RSS_FEEDS, SCRAPER_SLEEP_SECONDS

logger = logging.getLogger(__name__)

# Reddit aggressively 429s feedparser's default User-Agent on RSS endpoints;
# identify ourselves explicitly.
_USER_AGENT = "ai-deal-scout/1.0 (+https://github.com/KiranKri/ai-deal-scout)"


def fetch_reddit_deals() -> list[dict[str, Any]]:
    """Fetch deal posts from all configured Reddit RSS feeds.

    Iterates over every URL in ``REDDIT_RSS_FEEDS``, parses the feed with
    feedparser, and collects entries into a flat list.  Feeds that fail to
    parse or raise an exception are skipped with a warning so that a single
    bad feed never aborts the entire run.

    Returns:
        Deduplicated list of deal dicts with keys:
        ``title``, ``url``, ``body``, ``upvotes``, ``source``.
    """
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for feed_url in REDDIT_RSS_FEEDS:
        try:
            logger.debug("Fetching Reddit feed: %s", feed_url)
            feed = feedparser.parse(feed_url, agent=_USER_AGENT)

            # bozo is a warning, not a verdict: feedparser sets it for
            # recoverable issues while still populating entries.  Only skip
            # when there is also nothing usable.
            if feed.bozo and not feed.entries:
                logger.warning(
                    "Reddit feed unusable (bozo, 0 entries) for %s: %s",
                    feed_url,
                    feed.get("bozo_exception", "unknown error"),
                )
                time.sleep(SCRAPER_SLEEP_SECONDS)
                continue

            for entry in feed.entries:
                url: str = entry.get("link", "")
                if not url or url in seen_urls:
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
                        "source": "Reddit",
                    }
                )
                seen_urls.add(url)

            logger.debug("Reddit feed %s yielded %d entries", feed_url, len(feed.entries))

        except Exception as exc:  # noqa: BLE001
            logger.error("Reddit feed %s failed: %s", feed_url, exc, exc_info=True)

        time.sleep(SCRAPER_SLEEP_SECONDS)

    logger.info("Reddit scraper finished: %d unique results", len(results))
    return results
