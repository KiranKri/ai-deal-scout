"""Relevance scoring and filtering for ai-deal-scout.

Scores deal titles/bodies against configured keywords and boosted phrases,
then decides whether a deal clears the relevance bar.
"""

import logging

from config import BOOSTED_PHRASES, KEYWORDS, MIN_UPVOTES

logger = logging.getLogger(__name__)


def score_deal(title: str, body: str = "") -> int:
    """Compute a relevance score for a deal.

    Scoring rules:
    - +10 for each KEYWORD found in the combined ``title + body`` text
      (case-insensitive, each keyword counted once).
    - +20 for each BOOSTED_PHRASE found in the combined text
      (case-insensitive, each phrase counted once).

    Args:
        title: Deal headline or title string.
        body: Optional body / description text.

    Returns:
        Non-negative integer score.
    """
    combined = (title + " " + body).lower()
    score = 0

    for keyword in KEYWORDS:
        if keyword.lower() in combined:
            score += 10
            logger.debug("Keyword match +10: %r", keyword)

    for phrase in BOOSTED_PHRASES:
        if phrase.lower() in combined:
            score += 20
            logger.debug("Boosted phrase match +20: %r", phrase)

    logger.debug("score_deal=%d title=%r", score, title)
    return score


def is_relevant(title: str, body: str = "", upvotes: int = 0) -> bool:
    """Determine whether a deal is relevant enough to notify.

    A deal is relevant when:
    - ``score_deal`` returns **at least 20**, **and**
    - if ``upvotes`` is provided (> 0), it must meet or exceed ``MIN_UPVOTES``.

    Args:
        title: Deal headline or title string.
        body: Optional body / description text.
        upvotes: Upvote/karma count for the post (0 means no upvote data).

    Returns:
        True if the deal passes all relevance checks, False otherwise.
    """
    score = score_deal(title, body)
    if score < 20:
        logger.debug("is_relevant=False (score=%d < 20) title=%r", score, title)
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
