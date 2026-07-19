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
from scrapers.websearch import _is_blocked, fetch_websearch_deals

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
        ("WebSearch", fetch_websearch_deals),
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

    # Domain blocklist, applied to EVERY source — previously only websearch
    # checked it, so coupon-farm links arriving via HN/Reddit/RSS bypassed
    # 50+ domains of accumulated judgment (observed live: wildfire.deals and
    # friends in the June run history came through HN unblocked).
    # Only URLs with a resolvable host are judged here: unlike websearch,
    # a hostless URL at this layer is a source quirk, not spam.
    def _spam(url: str) -> bool:
        from urllib.parse import urlparse

        try:
            host = urlparse(url).hostname
        except ValueError:
            return False
        return bool(host) and _is_blocked(url)

    unblocked = [d for d in all_results if not _spam(d.get("url", ""))]
    blocked_count = len(all_results) - len(unblocked)
    if blocked_count:
        logger.info("Domain blocklist removed %d result(s)", blocked_count)
    all_results = unblocked

    # Cross-source title-similarity dedup.  On a collision keep the copy
    # with the most metadata (highest upvotes) rather than whichever source
    # happened to run first — otherwise a Reddit copy (upvotes=0) discards
    # the HN copy's real score and exempts the deal from MIN_UPVOTES.
    by_norm: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for deal in all_results:
        norm = _normalise_title(deal.get("title", ""))
        if not norm:
            logger.debug("Cross-source title dedup removed: %s", deal.get("title"))
            continue
        existing = by_norm.get(norm)
        if existing is None:
            by_norm[norm] = deal
            order.append(norm)
        elif deal.get("upvotes", 0) > existing.get("upvotes", 0):
            logger.debug(
                "Cross-source dedup: replacing %r (%s, %d↑) with %s copy (%d↑)",
                existing.get("title"), existing.get("source"),
                existing.get("upvotes", 0), deal.get("source"),
                deal.get("upvotes", 0),
            )
            by_norm[norm] = deal
        else:
            logger.debug("Cross-source title dedup removed: %s", deal.get("title"))

    deduped: list[dict[str, Any]] = [by_norm[n] for n in order]

    logger.info(
        "Cross-source dedup: %d → %d", len(all_results), len(deduped)
    )
    return deduped
