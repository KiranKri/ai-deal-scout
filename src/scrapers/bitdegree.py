"""BitDegree AI deals scraper for ai-deal-scout.

Scrapes https://www.bitdegree.org/ai/deals using a multi-strategy
BeautifulSoup approach so that minor site-structure changes are handled
gracefully without raising exceptions.
"""

import logging
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_BITDEGREE_URL = "https://www.bitdegree.org/ai/deals"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ai-deal-scout/1.0)"}
_TIMEOUT = 15
_SOURCE = "BitDegree"


def _absolutise(href: str) -> str:
    """Resolve a possibly-relative href against the BitDegree base URL.

    The site uses relative links like ``/ai/claude-deal``; sent verbatim to
    Telegram they render as broken plain text.
    """
    if not href:
        return ""
    return urljoin(_BITDEGREE_URL, href)

# Navigation / UI labels that are not real deal titles.
_JUNK_TITLE_PREFIXES: list[str] = [
    "faq",
    "what",
    "verified",
    "exclusive",
    "claim",
    "get deal",
    "show",
    "hide",
    "how",
    "why",
    "who",
    "all ai",
]


def _is_junk(item: dict) -> bool:
    """Return True if a scraped item looks like navigation noise, not a deal.

    Args:
        item: A raw deal dict with at least ``url`` and ``title`` keys.

    Returns:
        True when the item should be discarded.
    """
    url: str = (item.get("url") or "").strip()
    title: str = (item.get("title") or "").strip()

    if not url or url in ("/", "https://www.bitdegree.org/", _BITDEGREE_URL):
        return True
    if len(title) < 10:
        return True
    title_lower = title.lower()
    if any(title_lower.startswith(prefix) for prefix in _JUNK_TITLE_PREFIXES):
        return True
    return False


def _extract_text(tag: Tag | None) -> str:
    """Return stripped text from a BeautifulSoup tag, or empty string."""
    if tag is None:
        return ""
    return tag.get_text(separator=" ", strip=True)


def _strategy_article(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Strategy 1: collect deals from <article> tags.

    Looks for h2/h3 headings and paragraph text inside each <article>.

    Args:
        soup: Parsed page HTML.

    Returns:
        List of deal dicts (may be empty).
    """
    items: list[dict[str, Any]] = []
    for article in soup.find_all("article"):
        heading = article.find(["h2", "h3"])
        title: str = _extract_text(heading)
        if not title:
            continue

        anchor = article.find("a", href=True)
        url: str = _absolutise(anchor["href"]) if anchor else ""

        paragraph = article.find("p")
        body: str = _extract_text(paragraph)[:300] or _card_context(article)

        items.append(
            {
                "title": _label(title, url),
                "url": url,
                "body": body,
                "upvotes": 0,
                "source": _SOURCE,
            }
        )
    return items


def _strategy_card(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Strategy 2: collect deals from elements whose class contains 'deal' or 'card'.

    Args:
        soup: Parsed page HTML.

    Returns:
        List of deal dicts (may be empty).
    """
    items: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    candidates = soup.find_all(
        lambda tag: tag.name in ("div", "section", "li")
        and any(
            "deal" in c.lower() or "card" in c.lower()
            for c in (tag.get("class") or [])
        )
    )

    for element in candidates:
        heading = element.find(["h2", "h3", "h4"])
        title: str = _extract_text(heading)
        if not title or title in seen_titles:
            continue

        anchor = element.find("a", href=True)
        url: str = _absolutise(anchor["href"]) if anchor else ""

        body: str = _extract_text(element.find("p"))[:300] or _card_context(element)

        items.append(
            {
                "title": _label(title, url),
                "url": url,
                "body": body,
                "upvotes": 0,
                "source": _SOURCE,
            }
        )
        seen_titles.add(title)

    return items


def _tool_from_url(url: str) -> str:
    """Derive a human-readable tool name from a BitDegree URL slug.

    BitDegree headings are bare price fragments ("UP TO 58% OFF") that tell a
    subscriber nothing about *which* product is discounted.  The slug does::

        /ai/descript-review      -> "Descript"
        /ai/goon/jasper          -> "Jasper"
        /ai/copy-ai-review       -> "Copy Ai"

    Args:
        url: Absolute or relative BitDegree URL.

    Returns:
        Title-cased product name, or empty string if none can be derived.
    """
    if not url:
        return ""
    slug = url.rstrip("/").split("/")[-1]
    for suffix in ("-review", "-deal", "-deals", "-coupon", "-discount", "-pricing"):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            break
    slug = slug.replace("-", " ").replace("_", " ").strip()
    if not slug or slug in ("ai", "deals", "goon"):
        return ""
    return slug.title()


def _label(title: str, url: str) -> str:
    """Prefix a bare price title with the product name when one is derivable.

    ``"UP TO 58% OFF"`` becomes ``"Descript - UP TO 58% OFF"``.  Titles that
    already name the product are returned unchanged.
    """
    tool = _tool_from_url(url)
    if not tool:
        return title
    if tool.lower() in title.lower():
        return title
    return f"{tool} - {title}"


def _card_context(tag: Tag | None, max_chars: int = 300) -> str:
    """Return the text of the nearest enclosing card, for tool-name recovery.

    BitDegree headings are frequently bare price fragments — ``"50% OFF"``,
    ``"50% OFF FIRST MONTH"`` — with the product name rendered elsewhere in
    the card.  The relevance filter requires a TOOL_KEYWORD somewhere in
    title + body, so a heading-only scrape loses every one of these.

    Walking up to two ancestors captures the surrounding card text (which
    does carry the product name) into ``body``, letting these deals through
    without weakening the filter itself.

    Args:
        tag: The element the title was extracted from.
        max_chars: Truncation limit for the returned context.

    Returns:
        Stripped context text, or empty string.
    """
    node: Tag | None = tag
    for _ in range(3):
        if node is None:
            break
        text = node.get_text(separator=" ", strip=True)
        # Enough text to plausibly contain a product name, but not the
        # whole page.
        if len(text) > 40:
            return text[:max_chars]
        node = node.parent if isinstance(node.parent, Tag) else None
    return ""


def _strategy_ai_links(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Strategy 3: collect all <a> tags whose href contains '/ai/'.

    Args:
        soup: Parsed page HTML.

    Returns:
        List of deal dicts (may be empty).
    """
    items: list[dict[str, Any]] = []
    seen_hrefs: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href: str = anchor["href"]
        if "/ai/" not in href:
            continue
        if href in seen_hrefs:
            continue

        title: str = anchor.get_text(strip=True)
        if not title:
            continue

        items.append(
            {
                "title": _label(title, _absolutise(href)),
                "url": _absolutise(href),
                # Recover the product name from the surrounding card so the
                # TOOL_KEYWORD gate can see it.
                "body": _card_context(anchor),
                "upvotes": 0,
                "source": _SOURCE,
            }
        )
        seen_hrefs.add(href)

    return items


def fetch_bitdegree_deals() -> list[dict[str, Any]]:
    """Scrape AI deals from BitDegree using a multi-strategy selector.

    Strategies are tried in order; the first one that returns at least one
    result wins.  If no strategy yields results a warning is logged and an
    empty list is returned.  This function never raises an exception.

    Returns:
        List of deal dicts with keys:
        ``title``, ``url``, ``body``, ``upvotes``, ``source``.
    """
    try:
        logger.debug("Fetching BitDegree deals page: %s", _BITDEGREE_URL)
        response = requests.get(_BITDEGREE_URL, headers=_HEADERS, timeout=_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("BitDegree: request failed: %s", exc)
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    strategies = [
        ("article tags", _strategy_article),
        ("deal/card classes", _strategy_card),
        ("ai/ links", _strategy_ai_links),
    ]

    for strategy_name, strategy_fn in strategies:
        try:
            results = strategy_fn(soup)
            if results:
                logger.info(
                    "BitDegree: strategy '%s' returned %d items",
                    strategy_name,
                    len(results),
                )
                clean = [item for item in results if not _is_junk(item)]
                junk_count = len(results) - len(clean)
                if junk_count:
                    logger.info(
                        "BitDegree: filtered out %d junk item(s)", junk_count
                    )
                return clean
            logger.debug("BitDegree: strategy '%s' returned 0 items", strategy_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BitDegree: strategy '%s' raised: %s", strategy_name, exc)

    logger.warning(
        "BitDegree: no deal cards found, site structure may have changed"
    )
    return []
