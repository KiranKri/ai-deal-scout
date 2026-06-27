"""Relevance scoring and filtering for ai-deal-scout.

Uses word-boundary regex matching to avoid false positives from substrings
(e.g. "cursory" matching "cursor", or "retail" matching a future "ai" keyword).
"""

import logging
import re

from config import BOOSTED_PHRASES, DEAL_KEYWORDS, MIN_UPVOTES, TOOL_KEYWORDS

logger = logging.getLogger(__name__)


def _match(text: str, keyword: str) -> bool:
    """Whole-word boundary match; handles special characters safely.

    Args:
        text: Lowercased haystack string.
        keyword: Keyword or phrase to look for (lowercased before matching).

    Returns:
        True when the keyword appears as a whole word in *text*.
    """
    pattern = rf"\b{re.escape(keyword.lower())}\b"
    return bool(re.search(pattern, text))


def score_deal(title: str, body: str = "") -> int:
    """Compute a relevance score for a deal.

    Scoring rules (each keyword / phrase counted at most once):

    - +15 per ``DEAL_KEYWORDS`` match
    - +5  per ``TOOL_KEYWORDS`` match
    - +20 per ``BOOSTED_PHRASES`` match

    All matches use whole-word boundary regex so that substrings cannot
    trigger false positives.

    Args:
        title: Deal headline or title string.
        body: Optional body / description text.

    Returns:
        Non-negative integer score.
    """
    combined = (title + " " + body).lower()
    score = 0

    for kw in DEAL_KEYWORDS:
        if _match(combined, kw):
            score += 15
            logger.debug("DEAL_KEYWORD match +15: %r", kw)

    for kw in TOOL_KEYWORDS:
        if _match(combined, kw):
            score += 5
            logger.debug("TOOL_KEYWORD match +5: %r", kw)

    for phrase in BOOSTED_PHRASES:
        if _match(combined, phrase):
            score += 20
            logger.debug("BOOSTED_PHRASE match +20: %r", phrase)

    logger.debug("score_deal=%d title=%r", score, title)
    return score


def is_relevant(title: str, body: str = "", upvotes: int = 0) -> bool:
    """Determine whether a deal is relevant enough to notify.

    A post is relevant when **all** of the following hold:

    1. At least one ``DEAL_KEYWORDS`` match (word-boundary) — ensures the
       post contains actual deal intent, not just a tool mention.
    2. At least one ``TOOL_KEYWORDS`` match (word-boundary) — eliminates
       TV deals, retail discounts, and other non-AI content.
    3. ``score_deal`` returns **at least 15**.
    4. If ``upvotes`` is provided (> 0), it must meet or exceed
       ``MIN_UPVOTES``.

    Args:
        title: Deal headline or title string.
        body: Optional body / description text.
        upvotes: Upvote/karma count for the post (0 means no upvote data).

    Returns:
        True if the deal passes all relevance checks, False otherwise.
    """
    combined = (title + " " + body).lower()

    has_deal_kw = any(_match(combined, kw) for kw in DEAL_KEYWORDS)
    if not has_deal_kw:
        logger.debug("is_relevant=False (no DEAL_KEYWORD) title=%r", title)
        return False

    has_tool_kw = any(_match(combined, kw) for kw in TOOL_KEYWORDS)
    if not has_tool_kw:
        logger.debug("is_relevant=False (no TOOL_KEYWORD) title=%r", title)
        return False

    score = score_deal(title, body)
    if score < 15:
        logger.debug("is_relevant=False (score=%d < 15) title=%r", score, title)
        return False

    if upvotes > 0 and upvotes < MIN_UPVOTES:
        logger.debug(
            "is_relevant=False (upvotes=%d < MIN_UPVOTES=%d) title=%r",
            upvotes,
            MIN_UPVOTES,
            title,
        )
        return False

    logger.debug("is_relevant=True (score=%d, upvotes=%d) title=%r", score, upvotes, title)
    return True
