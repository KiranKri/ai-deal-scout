"""Relevance scoring and filtering for ai-deal-scout.

Uses word-boundary regex matching to avoid false positives from substrings
(e.g. "cursory" matching "cursor", or "retail" matching a future "ai" keyword).
"""

import logging
import re

from urllib.parse import urlparse

from config import (
    BOOSTED_PHRASES,
    DEAL_KEYWORDS,
    MIN_UPVOTES,
    NEGATIVE_KEYWORDS,
    NEWS_DOMAINS,
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


def _url_evidence(url: str) -> str:
    """Return URL text usable as tool evidence, or empty when unsafe.

    Vendor offer pages often name the product only in the host
    (elevenlabs.io/students, cursor.com/.../student-discount), so the URL is
    real evidence.  Discussion paths on those same domains are not: they carry
    the brand without carrying an offer.

    Measured on the labelled set: using every URL cost 18 false positives for
    1 extra true positive; excluding discussion paths keeps the recall gain
    without the noise.
    """
    if not url:
        return ""
    lowered = url.lower()
    if _URL_NOISE.search(lowered):
        return ""
    # Require the PATH to signal an offer, not merely the host to name a brand.
    # A brand-only rule let docs pages, pricing announcements and even a Suno
    # song called "Pay For A Free Trial" through: the host said "AI tool", the
    # page said nothing about an offer.
    if not _URL_OFFER.search(lowered):
        return ""
    return re.sub(r"[/._\-]+", " ", lowered)


# Price-shaped tokens that upgrade a bare "free plan" title into a real promo.
# Used by ``_is_bare_free_plan`` — not a full STRONG_DEAL substitute.
_PRICE_SIGNAL = re.compile(
    r"\d+\s*%|%\s*off|\$\s*\d+|\b\d+\s*\$|\b\d+\s*(?:month|year|week)s?\b",
    re.IGNORECASE,
)


# TOOL_KEYWORDS entries usable as a URL-substring check: single tokens (no
# spaces — domains cannot contain them) that are distinctive enough not to
# collide with unrelated hosts.  Excludes short/generic entries ("gpt", "llm")
# and the deliberately-broad catch-all tier ("ai tool", "ai app", ...), which
# either can't appear in a compact hostname or are too weak as standalone
# evidence.
_URL_TOOL_TOKENS: tuple[str, ...] = tuple(
    kw for kw in TOOL_KEYWORDS if kw.replace("-", "").isalnum() and len(kw) >= 4
)


def _url_tool_evidence(url: str) -> str:
    """Return the TOOL_KEYWORDS token found in *url*'s host, or "".

    Vendor offer pages often name the product only in the domain
    (runwayml.com/educators, grammarly.com/upgrade/business/try) while the
    title itself never says the tool name ("25% off for students and
    educators", "Start a Free Trial").  Used only to satisfy the
    TOOL_KEYWORDS gate — it is not a substitute for the DEAL_KEYWORDS gate,
    so it cannot turn a no-deal title into a match by itself.

    Callers must additionally require a STRONG_DEAL_KEYWORDS match whenever
    this is the only tool evidence (see ``is_relevant``).  Trusting any weak
    DEAL_KEYWORDS hit was tried first and measured worse: help/pricing pages
    on the same host as a real tool ("Codex now offers more flexible
    pricing", Udio's "The subscription trial", "Credits and credit limits")
    matched on "offer"/"trial"/"credits" alone and cost 4 new FPs for the
    same 3 recovered FNs. Gating on a strong signal instead costs only 1 new
    FP (Runway student discount x2 + Grammarly free trial recovered; a
    Suno-generated song literally titled "Pay For A Free Trial" is the one
    that still slips through — not resolvable with keywords).
    """
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    for token in _URL_TOOL_TOKENS:
        if token in host:
            return token
    return ""


def _is_bare_free_plan(combined: str) -> bool:
    """True when the only deal signal is ``free plan`` with no price upgrade.

    Pricing-tier pages ("GitHub Copilot Free Plan") and help docs match
    ``free plan`` + a tool name and used to pass the filter despite carrying
    no redeemable offer.  Real promos that mention free plan also carry a
    percentage, dollar amount, duration, or a STRONG_DEAL keyword.
    """
    deal_hits = [kw for kw in DEAL_KEYWORDS if _match(combined, kw)]
    if not deal_hits or set(deal_hits) != {"free plan"}:
        return False
    if any(_match(combined, kw) for kw in STRONG_DEAL_KEYWORDS):
        return False
    if _PRICE_SIGNAL.search(combined):
        return False
    return True


def _news_domain(url: str) -> str:
    """Return the matching ``NEWS_DOMAINS`` host for *url*, or "".

    Subdomains match their parent (``www.reuters.com`` and
    ``blogs.reuters.com`` both match ``reuters.com``).
    """
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    for domain in NEWS_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return domain
    return ""


def is_relevant(title: str, body: str = "", upvotes: int = 0, url: str = "") -> bool:
    """Determine whether a deal is relevant enough to notify.

    A post is relevant when **all** of the following hold:

    1. No ``NEGATIVE_KEYWORDS`` hit (hard veto).
    2. If the title contains ``?``, it must also carry a strong price
       signal (``STRONG_DEAL_KEYWORDS``).  Bare support questions stay out;
       offer-shaped questions ("50% off?") can pass.
    3. ``Show HN:`` titles require a strong price signal.
    4. At least one ``DEAL_KEYWORDS`` match (word-boundary).
    5. At least one ``TOOL_KEYWORDS`` match (word-boundary) in title or
       body, OR the URL's host names a known tool (``_url_tool_evidence``) —
       vendor pages often name the product only in the domain.
    6. Not a bare ``free plan`` title (pricing page) without a price signal.
    7. If the URL's host is a ``NEWS_DOMAINS`` press outlet, a strong price
       signal (``STRONG_DEAL_KEYWORDS``) is required — general news covers
       AI "deals" (M&A, funding, policy) far more than consumer discounts.
    8. If ``MIN_UPVOTES > 0``, ``upvotes`` must be >= that threshold
       (unknown/zero-upvote sources fail when the gate is enabled).

    Score is computed for logging / ranking only.  ``MIN_SCORE`` is not used
    as a gate: a deal keyword alone already contributes +15, so a separate
    score threshold was unreachable after the deal+tool gates.

    Args:
        title: Deal headline or title string.
        body: Optional body / description text.
        upvotes: Upvote/karma count for the post (0 means no upvote data).
        url: Optional source URL, used for tool-name evidence and the
            news-domain gate.

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
    if not has_tool_kw and _url_tool_evidence(url):
        # URL-only tool evidence (no tool name in title/body) is trustworthy
        # only alongside a *strong* price signal — weak DEAL_KEYWORDS alone
        # ("offer", "trial", "credits") pass on generic pricing/help pages
        # for the same host (openai.com "Codex ... pricing", Udio help docs).
        has_tool_kw = any(_match(combined, kw) for kw in STRONG_DEAL_KEYWORDS)
    if not has_tool_kw:
        logger.debug("is_relevant=False (no TOOL_KEYWORD) title=%r", title)
        return False

    # Bare "free plan" + tool name is almost always a pricing-tier page, not a
    # promo.  Measured on the 764-row eval set: kills 1 FP, 0 new FNs.
    if _is_bare_free_plan(combined):
        logger.debug("is_relevant=False (bare free plan, no price signal) title=%r", title)
        return False

    # News-domain gate: general press covers AI "deals" (M&A, funding,
    # infra, policy) and product launches far more than consumer discounts.
    # Require an explicit strong signal from these hosts specifically.
    news_domain = _news_domain(url)
    if news_domain and not any(_match(combined, kw) for kw in STRONG_DEAL_KEYWORDS):
        logger.debug(
            "is_relevant=False (news domain %r, no strong signal) title=%r",
            news_domain,
            title,
        )
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
