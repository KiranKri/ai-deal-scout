"""Relevance scoring and filtering for ai-deal-scout.

Scores deal titles/bodies against deal keywords, tool keywords, and boosted
phrases, then decides whether a post clears the relevance bar.
"""

import logging

from config import BOOSTED_PHRASES, DEAL_KEYWORDS, MIN_UPVOTES, TOOL_KEYWORDS

logger = logging.getLogger(__name__)


def score_deal(title: str, body: str = "") -> int:
    """Compute a relevance score for a deal.

    Scoring rules (each keyword/phrase counted at most once):

    - +15 per ``DEAL_KEYWORDS`` match in combined ``title + body``
    - +5  per ``TOOL_KEYWORDS`` match in combined ``title + body``
    - +20 per ``BOOSTED_PHRASES`` match in combined ``title + body``

    Args:
        title: Deal headline or title string.
        body: Optional body / description text.

    Returns:
        Non-negative integer score.
    """
    combined = (title + " " + body).lower()
    score = 0

    for keyword in DEAL_KEYWORDS:
        if keyword.lower() in combined:
            score += 15
            logger.debug("DEAL_KEYWORD match +15: %r", keyword)

    for keyword in TOOL_KEYWORDS:
        if keyword.lower() in combined:
            score += 5
            logger.debug("TOOL_KEYWORD match +5: %r", keyword)

    for phrase in BOOSTED_PHRASES:
        if phrase.lower() in combined:
            score += 20
            logger.debug("BOOSTED_PHRASE match +20: %r", phrase)

    logger.debug("score_deal=%d title=%r", score, title)
    return score


def is_relevant(title: str, body: str = "", upvotes: int = 0) -> bool:
    """Determine whether a deal is relevant enough to notify.

    A post is relevant when **all** of the following hold:

    1. At least one ``DEAL_KEYWORDS`` match exists in ``title + body``
       (pure tool-name mentions are rejected).
    2. ``score_deal`` returns **at least 15**.
    3. If ``upvotes`` is provided (> 0), it must meet or exceed
       ``MIN_UPVOTES``.

    Args:
        title: Deal headline or title string.
        body: Optional body / description text.
        upvotes: Upvote/karma count for the post (0 means no upvote data).

    Returns:
        True if the deal passes all relevance checks, False otherwise.
    """
    combined = (title + " " + body).lower()

    has_deal_keyword = any(kw.lower() in combined for kw in DEAL_KEYWORDS)
    if not has_deal_keyword:
        logger.debug("is_relevant=False (no DEAL_KEYWORD) title=%r", title)
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
