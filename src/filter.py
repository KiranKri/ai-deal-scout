"""Relevance scoring and filtering for ai-deal-scout.

Uses word-boundary regex matching to avoid false positives from substrings
(e.g. "cursory" matching "cursor", or "retail" matching a future "ai" keyword).
"""

import logging
import re

from config import (
    BOOSTED_PHRASES,
    DEAL_KEYWORDS,
    MIN_UPVOTES,
    NEGATIVE_KEYWORDS,
    VETO_QUESTION_TITLES,
    NO_INFLECTION,
    STRONG_DEAL_KEYWORDS,
    WEAK_SOURCE_PREFIXES,
    TOOL_KEYWORDS,
)

logger = logging.getLogger(__name__)


def _match(text: str, keyword: str) -> bool:
    """Whole-word boundary match; handles special characters safely.

    ``\\b`` is only applied next to word characters.  A keyword that starts
    or ends with a non-word character (e.g. ``"% off"``) gets no boundary on
    that side — otherwise ``\\b%`` would *require* a word character before
    the ``%``, so ``"% off Cursor"`` at string start or ``"50 % off"`` would
    silently fail to match.

    Inflection policy (fail-safe):

    - **Single-word** keywords not in ``NO_INFLECTION``: optional
      ``s|es|d|ed|ing`` (deals, discounted, offering).
    - **Multi-word** phrases: optional plural ``s|es`` only — so
      ``"cuts ai deal"`` still matches ``"cuts AI deals"``, but
      ``"on us"`` never matches ``"on using"`` (no ``ing`` on phrases).
    - ``NO_INFLECTION`` and non-alnum endings: exact match only.

    Args:
        text: Lowercased haystack string.
        keyword: Keyword or phrase to look for (lowercased before matching).

    Returns:
        True when the keyword appears as a whole word in *text*.
    """
    kw = keyword.lower()
    if not kw:
        return False
    prefix = r"\b" if (kw[0].isalnum() or kw[0] == "_") else ""

    ends_word = kw[-1].isalnum() or kw[-1] == "_"
    multi = " " in kw
    if ends_word and not multi and kw not in NO_INFLECTION:
        inflection = r"(?:s|es|d|ed|ing)?"
        suffix = r"\b"
    elif ends_word and multi and kw not in NO_INFLECTION:
        # Plurals of the last token only — never d/ed/ing on phrases.
        inflection = r"(?:s|es)?"
        suffix = r"\b"
    elif ends_word:
        inflection = ""
        suffix = r"\b"
    else:
        inflection = ""
        suffix = ""

    return bool(re.search(prefix + re.escape(kw) + inflection + suffix, text))


_STALE_YEAR = re.compile(r"\b(20[12]\d)\b")
_STALE_SEASONAL = re.compile(r"\b(black friday|cyber monday)\b", re.IGNORECASE)


def is_stale(title: str, body: str = "", now: "datetime | None" = None) -> bool:
    """Heuristic staleness check on data already collected — zero API cost.

    Catches the classes observed live in the run logs: "Suno Black Friday
    Deals 2025" surfacing in July 2026 (SEO pages update their timestamps, so
    Tavily's ``days`` filter does not protect against them) and a 2020 Notion
    announcement arriving via HN.

    Rules:

    - Any year mentioned that is **before** the current year → stale.
      (The current year is fine: "2026 deals" pages are legitimate.)
    - "Black Friday" / "Cyber Monday" outside October–December → stale,
      whether it is last year's leftover page or a pre-baked page for an
      event months away.

    Args:
        title: Deal headline.
        body: Optional snippet text.
        now: Injectable clock for tests; defaults to the current time.

    Returns:
        True when the deal looks expired or out of season.
    """
    from datetime import datetime

    now = now or datetime.now()
    text = f"{title} {body}"
    years = [int(y) for y in _STALE_YEAR.findall(text)]
    if years and max(years) < now.year:
        return True
    if _STALE_SEASONAL.search(text) and not (10 <= now.month <= 12):
        return True
    return False


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

    1. No ``NEGATIVE_KEYWORDS`` hit (hard veto).
    2. If the title contains ``?``, it must also carry a strong price
       signal (``STRONG_DEAL_KEYWORDS``).  Bare support questions stay out;
       offer-shaped questions ("50% off?") can pass.
    3. ``Show HN:`` titles require a strong price signal.
    4. At least one ``DEAL_KEYWORDS`` match (word-boundary).
    5. At least one ``TOOL_KEYWORDS`` match (word-boundary) — tool name may
       appear in title or body.
    6. If ``MIN_UPVOTES > 0``, ``upvotes`` must be >= that threshold
       (unknown/zero-upvote sources fail when the gate is enabled).

    Score is computed for logging / ranking only.  ``MIN_SCORE`` is not used
    as a gate: a deal keyword alone already contributes +15, so a separate
    score threshold was unreachable after the deal+tool gates.

    Args:
        title: Deal headline or title string.
        body: Optional body / description text.
        upvotes: Upvote/karma count for the post (0 means no upvote data).

    Returns:
        True if the deal passes all relevance checks, False otherwise.
    """
    combined = (title + " " + body).lower()

    vetoed = next((n for n in NEGATIVE_KEYWORDS if _match(combined, n)), None)
    if vetoed:
        logger.debug("is_relevant=False (NEGATIVE_KEYWORD %r) title=%r", vetoed, title)
        return False

    # Question titles: allow only when a strong price signal is also present.
    # Bare "?" used to hard-reject offer headlines like
    # "Claude Pro free for students? 50% off through Sept".
    if VETO_QUESTION_TITLES and "?" in title:
        if not any(_match(combined, kw) for kw in STRONG_DEAL_KEYWORDS):
            logger.debug("is_relevant=False (question title, no strong signal) title=%r", title)
            return False

    # "Show HN:" launches are product announcements far more often than deals.
    # Let them through only on an explicit price signal.
    if title.strip().lower().startswith(WEAK_SOURCE_PREFIXES):
        if not any(_match(combined, kw) for kw in STRONG_DEAL_KEYWORDS):
            logger.debug(
                "is_relevant=False (weak-source prefix, no strong signal) title=%r",
                title,
            )
            return False

    has_deal_kw = any(_match(combined, kw) for kw in DEAL_KEYWORDS)
    if not has_deal_kw:
        logger.debug("is_relevant=False (no DEAL_KEYWORD) title=%r", title)
        return False

    has_tool_kw = any(_match(combined, kw) for kw in TOOL_KEYWORDS)
    if not has_tool_kw:
        logger.debug("is_relevant=False (no TOOL_KEYWORD) title=%r", title)
        return False

    # Upvote gate: MIN_UPVOTES=0 means disabled (explicit, not a dead
    # comparison).  When enabled, require upvotes >= threshold including
    # zero-upvote posts (sources that never report votes cannot bypass).
    if MIN_UPVOTES > 0 and upvotes < MIN_UPVOTES:
        logger.debug(
            "is_relevant=False (upvotes=%d < MIN_UPVOTES=%d) title=%r",
            upvotes,
            MIN_UPVOTES,
            title,
        )
        return False

    score = score_deal(title, body)
    logger.debug("is_relevant=True (score=%d, upvotes=%d) title=%r", score, upvotes, title)
    return True
