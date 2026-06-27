"""Scrapers package for ai-deal-scout.

Exposes ``run_all_scrapers`` as the single entry-point for collecting
raw deal data from every configured source.
"""

import logging
import traceback
from typing import Any

from scrapers.bitdegree import fetch_bitdegree_deals
from scrapers.hackernews import fetch_hn_deals
from scrapers.reddit import fetch_reddit_deals
from scrapers.rss_feed import fetch_rss_deals

logger = logging.getLogger(__name__)


def run_all_scrapers() -> list[dict[str, Any]]:
    """Run every scraper and return a combined list of raw deal dicts.

    Each scraper is called inside its own ``try/except`` block so that a
    failure in one source never prevents the others from running.
    Unexpected exceptions are logged with a full traceback; the result for
    that source is treated as an empty list.

    Returns:
        Concatenated list of deal dicts from all sources.  Entries are in
        source order (Reddit → HackerNews → RSS → BitDegree) with no
        cross-source deduplication at this stage.
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

    logger.info("run_all_scrapers total: %d raw results", len(all_results))
    return all_results
