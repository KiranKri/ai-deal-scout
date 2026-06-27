"""Hacker News scraper for ai-deal-scout.

Uses the Algolia HN search API (no authentication required) to find
deal-related stories across configured query terms.
"""

import logging
import time
from typing import Any

import requests

from config import HN_SEARCH_QUERIES, MAX_HN_RESULTS_PER_QUERY, SCRAPER_SLEEP_SECONDS

logger = logging.getLogger(__name__)

_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search"
_HN_ITEM_BASE = "https://news.ycombinator.com/item?id="


def fetch_hn_deals() -> list[dict[str, Any]]:
    """Fetch deal stories from Hacker News via the Algolia search API.

    Issues one search request per query in ``HN_SEARCH_QUERIES`` and merges
    the results, deduplicating by ``objectID`` so the same story is never
    returned twice even if it matches multiple queries.

    Returns:
        Deduplicated list of deal dicts with keys:
        ``title``, ``url``, ``body``, ``upvotes``, ``source``.
    """
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for query in HN_SEARCH_QUERIES:
        try:
            logger.debug("Querying HN Algolia API: %r", query)
            response = requests.get(
                _ALGOLIA_URL,
                params={
                    "tags": "story",
                    "query": query,
                    "hitsPerPage": MAX_HN_RESULTS_PER_QUERY,
                },
                timeout=15,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            for hit in data.get("hits", []):
                object_id: str = hit.get("objectID", "")
                if not object_id or object_id in seen_ids:
                    continue

                title: str = (hit.get("title") or "").strip()
                url: str = hit.get("url") or f"{_HN_ITEM_BASE}{object_id}"
                raw_body: str = hit.get("story_text") or ""
                body: str = raw_body[:300]
                upvotes: int = hit.get("points") or 0

                results.append(
                    {
                        "title": title,
                        "url": url,
                        "body": body,
                        "upvotes": upvotes,
                        "source": "HackerNews",
                    }
                )
                seen_ids.add(object_id)

            logger.debug(
                "HN query %r returned %d hits", query, len(data.get("hits", []))
            )

        except requests.RequestException as exc:
            logger.error("HN Algolia query %r failed: %s", query, exc)

        time.sleep(SCRAPER_SLEEP_SECONDS)

    logger.info("HackerNews scraper finished: %d unique results", len(results))
    return results
