"""Tavily Search scraper for ai-deal-scout.

Closes the biggest recall gap in the pipeline: every other source only sees
what a stranger happened to post on a forum.  This one queries the open web,
including vendors' own announcement pages, so a promo that never reached
Reddit or Hacker News is still found.

Budget is capped on five independent axes so that no single failure — a slow
API, a runaway loop, a bad query — can burn the monthly quota:

1. ``WEBSEARCH_MAX_QUERIES_PER_RUN``   hard query ceiling per run
2. ``WEBSEARCH_TIME_BUDGET_SECONDS``   wall-clock ceiling per run
3. ``WEBSEARCH_RESULTS_PER_QUERY``     results parsed per query
4. ``WEBSEARCH_MONTHLY_QUOTA``         persisted month-to-date counter
5. ``WEBSEARCH_EARLY_STOP_EMPTY``      stop when N queries in a row yield nothing

If ``TAVILY_API_KEY`` is unset the module logs once and returns ``[]``, so the
rest of the pipeline is unaffected.

Tavily was chosen over Brave because Brave removed its free tier in Feb 2026
and now requires a card with no spending cap — unsuitable for an unattended
cron job.  Tavily's free tier is 1000 credits/month with no card.
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from config import (
    BLOCKED_DOMAINS,
    ROTATING_TOOLS,
    VENDOR_SITES,
    WEBSEARCH_EARLY_STOP_EMPTY,
    WEBSEARCH_MAX_AGE_DAYS,
    WEBSEARCH_MAX_QUERIES_PER_RUN,
    WEBSEARCH_MONTHLY_QUOTA,
    WEBSEARCH_RESERVE_THRESHOLD,
    WEBSEARCH_RESULTS_PER_QUERY,
    WEBSEARCH_ROTATING_QUERIES,
    WEBSEARCH_SLEEP_SECONDS,
    WEBSEARCH_STATE_PATH,
    WEBSEARCH_TIME_BUDGET_SECONDS,
    WEBSEARCH_VENDOR_QUERIES,
)

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_SOURCE = "WebSearch"
_IST = ZoneInfo("Asia/Kolkata")

# Deal intent, expressed once and reused for every query.
_INTENT = '(discount OR promo OR coupon OR "free trial" OR "free month" OR deal)'


# ---------------------------------------------------------------------------
# Persisted state: monthly quota counter + rotation cursor
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    """Load the quota / rotation state, resetting the counter on a new month.

    Vendor and tool rotations use **separate cursors**, each advanced by the
    number of queries of that kind actually issued.  The previous single
    cursor advanced by ``max(vendor, rotating)`` modulo ``len(ROTATING_TOOLS)``
    and only achieved full coverage because 8 and 37 happened to be coprime —
    simulation showed that at e.g. 40 tools, half the list was permanently
    skipped.  Separate per-list cursors make coverage unconditional.

    Returns:
        ``{"month": "YYYY-MM", "used": int, "rot_vendor": int, "rot_tool": int}``.
        A corrupt or missing file yields a fresh state rather than raising.
        A legacy ``"rotation"`` key seeds both cursors.
    """
    month = datetime.now(tz=_IST).strftime("%Y-%m")
    default = {"month": month, "used": 0, "rot_vendor": 0, "rot_tool": 0}

    if not os.path.exists(WEBSEARCH_STATE_PATH):
        return default
    try:
        with open(WEBSEARCH_STATE_PATH, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("websearch state unreadable (%s); starting fresh", exc)
        return default

    # Legacy migration: a single "rotation" cursor seeds both new cursors.
    legacy = int(state.get("rotation", 0))
    rot_vendor = int(state.get("rot_vendor", legacy)) % max(len(VENDOR_SITES), 1)
    rot_tool = int(state.get("rot_tool", legacy)) % max(len(ROTATING_TOOLS), 1)

    if state.get("month") != month:
        logger.info("websearch: new month (%s), quota counter reset", month)
        return {"month": month, "used": 0, "rot_vendor": rot_vendor, "rot_tool": rot_tool}

    default.update(
        {
            "used": int(state.get("used", 0)),
            "rot_vendor": rot_vendor,
            "rot_tool": rot_tool,
        }
    )
    return default


def _save_state(state: dict) -> None:
    """Persist the quota / rotation state.  Never raises."""
    try:
        os.makedirs(os.path.dirname(WEBSEARCH_STATE_PATH) or ".", exist_ok=True)
        with open(WEBSEARCH_STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError as exc:
        logger.error("websearch: failed to save state: %s", exc)


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _build_queries(
    rot_vendor: int, rot_tool: int | None = None, vendor_only: bool = False
) -> list[tuple[str, list[str]]]:
    """Build this run's queries as ``(query_text, include_domains)`` pairs.

    Tavily does **not** honour Google's ``site:`` operator inside the query
    string — domain restriction goes through the ``include_domains`` request
    parameter instead.  Vendor searches therefore carry a domain list; open-web
    searches carry an empty one.

    Vendor searches are high precision: a vendor's own domain does not host
    coupon spam.  Rotating searches cover the long tail at fixed cost — N tools
    per run, cycling through ``ROTATING_TOOLS`` so each is covered roughly
    weekly rather than all of them every run.

    Args:
        rot_vendor:  Cursor into ``VENDOR_SITES``, persisted across runs.
        rot_tool:    Cursor into ``ROTATING_TOOLS``.  Defaults to
                     ``rot_vendor`` for backward compatibility.
        vendor_only: When True, drop the open-web rotating queries and keep
                     only vendor-site searches.  Used near the quota line:
                     vendor searches yield far more real deals per credit.

    Returns:
        List of ``(query, include_domains)`` pairs, capped at
        ``WEBSEARCH_MAX_QUERIES_PER_RUN``.  Vendor queries always carry a
        non-empty domain list; open-web queries carry ``[]`` — callers use
        that to tell the two kinds apart.
    """
    if rot_tool is None:
        rot_tool = rot_vendor
    queries: list[tuple[str, list[str]]] = []

    # Vendor searches, rotated so every vendor is covered even though
    # VENDOR_SITES is longer than the per-run allowance.
    if VENDOR_SITES:
        for i in range(min(WEBSEARCH_VENDOR_QUERIES, len(VENDOR_SITES))):
            name, domain = VENDOR_SITES[(rot_vendor + i) % len(VENDOR_SITES)]
            queries.append((f"{name} {_INTENT}", [domain]))

    # Open-web searches for long-tail tools.  Skipped in reserve mode.
    if ROTATING_TOOLS and not vendor_only:
        for i in range(min(WEBSEARCH_ROTATING_QUERIES, len(ROTATING_TOOLS))):
            tool = ROTATING_TOOLS[(rot_tool + i) % len(ROTATING_TOOLS)]
            queries.append((f"{tool} {_INTENT}", []))

    return queries[:WEBSEARCH_MAX_QUERIES_PER_RUN]


def _is_blocked(url: str) -> bool:
    """Return True when a URL's host is a known coupon farm or social site.

    These domains rank highly for exactly the queries this bot runs and carry
    overwhelmingly fake or expired codes.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return True
    if not host:
        return True
    host = host[4:] if host.startswith("www.") else host
    return any(host == b or host.endswith("." + b) for b in BLOCKED_DOMAINS)


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------


def _search(
    query: str, api_key: str, include_domains: list[str] | None = None
) -> list[dict[str, Any]]:
    """Run one Tavily search and return raw result dicts.

    Tavily is a POST/JSON API: the key travels in the body, and results carry
    ``title``, ``url`` and ``content`` (the snippet).

    Args:
        query:           Search query string.
        api_key:         Tavily API key.
        include_domains: Restrict results to these domains (vendor searches).

    Returns:
        List of Tavily ``results`` entries, or ``[]`` on any failure.
        Never raises.
    """
    try:
        response = requests.post(
            _TAVILY_URL,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": WEBSEARCH_RESULTS_PER_QUERY,
                "days": WEBSEARCH_MAX_AGE_DAYS,
                "search_depth": "basic",   # 1 credit; "advanced" costs 2
                "topic": "general",
                "include_answer": False,
                "include_raw_content": False,
                "include_domains": include_domains or [],
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        logger.warning("websearch: request failed for %r: %s", query, exc)
        return []

    if response.status_code == 429:
        logger.warning("websearch: rate limited (429) on %r", query)
        return []
    if response.status_code in (401, 403):
        logger.error("websearch: TAVILY_API_KEY rejected (%d)", response.status_code)
        return []
    if response.status_code == 432:
        logger.error("websearch: Tavily plan limit reached (432) — quota exhausted")
        return []
    if response.status_code != 200:
        logger.warning("websearch: HTTP %d for %r", response.status_code, query)
        return []

    try:
        return response.json().get("results", []) or []
    except ValueError as exc:
        logger.warning("websearch: unparseable response for %r: %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch_websearch_deals() -> list[dict[str, Any]]:
    """Search the web for AI tool deals within a hard budget.

    Returns:
        List of deal dicts with the standard keys ``title``, ``url``,
        ``body``, ``upvotes``, ``source``.  Empty when no API key is set,
        when the monthly quota is exhausted, or on total API failure.
        Never raises.
    """
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        logger.info(
            "websearch: TAVILY_API_KEY not set — skipping web search "
            "(the rest of the pipeline is unaffected)"
        )
        return []

    state = _load_state()
    remaining_quota = WEBSEARCH_MONTHLY_QUOTA - state["used"]
    if remaining_quota <= 0:
        logger.warning(
            "websearch: monthly quota exhausted (%d/%d used) — skipping",
            state["used"], WEBSEARCH_MONTHLY_QUOTA,
        )
        return []

    # Near the quota line, degrade to vendor-only rather than stopping dead.
    reserve_mode = remaining_quota < WEBSEARCH_RESERVE_THRESHOLD
    if reserve_mode:
        logger.warning(
            "websearch: only %d credits left (< %d) — RESERVE MODE: "
            "vendor-site searches only, skipping open-web queries",
            remaining_quota, WEBSEARCH_RESERVE_THRESHOLD,
        )

    queries = _build_queries(
        state["rot_vendor"], state["rot_tool"], vendor_only=reserve_mode
    )
    budget = min(len(queries), remaining_quota)
    queries = queries[:budget]

    logger.info(
        "websearch: %d queries this run (quota %d/%d used this month)",
        len(queries), state["used"], WEBSEARCH_MONTHLY_QUOTA,
    )

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    started = time.monotonic()
    consecutive_empty = 0
    queries_used = 0
    vendor_used = 0
    tool_used = 0
    blocked_count = 0

    for query, include_domains in queries:
        elapsed = time.monotonic() - started
        if elapsed > WEBSEARCH_TIME_BUDGET_SECONDS:
            logger.info(
                "websearch: time budget reached (%.0fs) after %d quer(ies)",
                elapsed, queries_used,
            )
            break

        if consecutive_empty >= WEBSEARCH_EARLY_STOP_EMPTY:
            logger.info(
                "websearch: early stop — %d consecutive queries yielded nothing",
                consecutive_empty,
            )
            break

        hits = _search(query, api_key, include_domains)
        queries_used += 1
        # Vendor queries carry a domain list; open-web (tool) queries do not.
        if include_domains:
            vendor_used += 1
        else:
            tool_used += 1

        added = 0
        for hit in hits:
            url = (hit.get("url") or "").strip()
            title = (hit.get("title") or "").strip()
            if not url or not title or url in seen_urls:
                continue
            # Unrendered CMS placeholders, e.g. "{{IW4QaZoc2}}" — seen live.
            if "{{" in title or "}}" in title:
                continue
            if _is_blocked(url):
                blocked_count += 1
                continue

            results.append(
                {
                    "title": title,
                    "url": url,
                    # Tavily calls the snippet "content".
                    "body": (hit.get("content") or "")[:300],
                    "upvotes": 0,
                    "source": _SOURCE,
                }
            )
            seen_urls.add(url)
            added += 1

        consecutive_empty = 0 if added else consecutive_empty + 1
        logger.debug("websearch: %r -> %d new", query, added)
        time.sleep(WEBSEARCH_SLEEP_SECONDS)

    # Persist quota usage and advance each rotation cursor by the number of
    # queries of that kind actually issued, so an early stop or time-budget
    # break never skips list entries, and coverage does not depend on the
    # list lengths sharing factors with a fixed step.
    state["used"] += queries_used
    state["rot_vendor"] = (state["rot_vendor"] + vendor_used) % max(len(VENDOR_SITES), 1)
    state["rot_tool"] = (state["rot_tool"] + tool_used) % max(len(ROTATING_TOOLS), 1)
    state.pop("rotation", None)  # drop the legacy single cursor
    _save_state(state)

    logger.info(
        "websearch finished: %d results from %d quer(ies) in %.0fs "
        "(%d blocked as spam, quota now %d/%d)",
        len(results), queries_used, time.monotonic() - started,
        blocked_count, state["used"], WEBSEARCH_MONTHLY_QUOTA,
    )
    return results
