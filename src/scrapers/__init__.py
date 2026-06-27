"""Scrapers package for ai-deal-scout.

Exposes ``run_all_scrapers`` as the single entry-point for collecting
raw deal data from every configured source.
"""

import logging
import re
import traceback
from typing import Any

from scrapers.bitdegree import fetch_bitdegree_deals
from scrapers.hackernews import fetch_hn_deals
from scrapers.reddit import fetch_reddit_deals
from scrapers.rss_feed import fetch_rss_deals

logger = logging.getLogger(__name__)

_STOPWORDS: frozenset[str] = frozenset(
    {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "is", "at"}
)


def _normalise_title(title: str) -> str:
    """Lowercase, strip punctuation, and remove common stopwords.

    Used for fuzzy cross-source deduplication so that the same story
    reported by both Reddit and HackerNews is counted only once.

    Args:
        title: Raw deal title string.

    Returns:
        Normalised string suitable for set-based comparison.
    """
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", "", title)
    words = [w for w in title.split() if w not in _STOPWORDS]
    return " ".join(words)


def run_all_scrapers() -> list[dict[str, Any]]:
    """Run every scraper and return a deduplicated list of raw deal dicts.

    Each scraper is called inside its own ``try/except`` block so that a
    failure in one source never prevents the others from running.
    Unexpected exceptions are logged with a full traceback; the result for
    that source is treated as an empty list.

    After all results are collected a cross-source title-similarity dedup
    pass removes entries whose normalised titles have already been seen,
    preventing the same story from appearing multiple times when it is
    picked up by more than one scraper.

    Returns:
        Deduplicated list of deal dicts from all sources.
    """
    all_results: list[dict[str, Any]] = []

    scrapers = [
        ("Reddit", fetch_reddit_deals),
        ("HackerNews", fetch_hn_deals),
        ("RSS", fetch_rss_deals),
        ("BitDegree", fetch_bitdegree_deals),
    ]

    for source_name, scraper_fn in scrapers:
        try:
            results = scraper_fn()
            logger.info("%s: %d raw results", source_name, len(results))
            all_results.extend(results)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "%s scraper raised an unexpected exception: %s\n%s",
                source_name,
                exc,
                traceback.format_exc(),
            )

    # Cross-source title-similarity dedup
    seen_titles: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for deal in all_results:
        norm = _normalise_title(deal.get("title", ""))
        if norm and norm not in seen_titles:
            seen_titles.add(norm)
            deduped.append(deal)
        else:
            logger.debug("Cross-source title dedup removed: %s", deal.get("title"))

    logger.info(
        "Cross-source dedup: %d → %d", len(all_results), len(deduped)
    )
    return deduped
