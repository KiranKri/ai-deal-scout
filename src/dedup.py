"""Deduplication layer for ai-deal-scout.

Tracks seen deals via SHA-256 hashes stored in a JSON file so that
the same deal is never re-notified across runs.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import HASH_CLEANUP_DAYS, SEEN_DEALS_PATH

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

_EMPTY_STORE: dict = {"hashes": {}, "last_updated": ""}


def _load() -> dict:
    """Load the seen-deals store from disk.

    Returns an empty store structure if the file is missing or corrupt.
    """
    if not os.path.exists(SEEN_DEALS_PATH):
        logger.debug("seen_deals file not found; starting with empty store")
        return {"hashes": {}, "last_updated": ""}
    try:
        with open(SEEN_DEALS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if "hashes" not in data or "last_updated" not in data:
            logger.warning("seen_deals file has unexpected structure; resetting")
            return {"hashes": {}, "last_updated": ""}
        return data
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.error("Failed to load seen_deals: %s; starting fresh", exc)
        return {"hashes": {}, "last_updated": ""}


def _hash_url(url: str) -> str:
    """Return the SHA-256 hex digest of a normalised URL.

    Args:
        url: The deal URL to hash.

    Returns:
        Lowercase hex SHA-256 digest.
    """
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()


def _hash_title(title: str) -> str:
    """Return the SHA-256 hex digest of a normalised title.

    Args:
        title: The deal title to hash.

    Returns:
        Lowercase hex SHA-256 digest.
    """
    return hashlib.sha256(title.strip().lower().encode()).hexdigest()


def _now_ist_iso() -> str:
    """Return the current IST time as an ISO-8601 string."""
    return datetime.now(tz=_IST).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_seen(url: str, title: str) -> bool:
    """Check whether a deal has already been processed.

    A deal is considered seen if *either* its URL hash or its title hash
    is present in the store.

    Args:
        url: Deal URL.
        title: Deal title.

    Returns:
        True if the deal was seen before, False otherwise.
    """
    store = _load()
    hashes = store.get("hashes", {})
    url_hash = _hash_url(url)
    title_hash = _hash_title(title)
    seen = url_hash in hashes or title_hash in hashes
    logger.debug("is_seen=%s for url=%r", seen, url)
    return seen


def mark_seen(url: str, title: str) -> None:
    """Record a deal as seen and persist the store immediately.

    Both the URL hash and the title hash are stored with the current IST
    timestamp so that ``cleanup_old_hashes`` can expire them later.

    Args:
        url: Deal URL.
        title: Deal title.
    """
    store = _load()
    now = _now_ist_iso()
    store["hashes"][_hash_url(url)] = now
    store["hashes"][_hash_title(title)] = now
    store["last_updated"] = now
    save(store)
    logger.debug("Marked seen: url=%r title=%r", url, title)


def save(store: dict | None = None) -> None:
    """Write the store to disk and update ``last_updated``.

    Args:
        store: Optional pre-loaded store dict.  If omitted the store is
               loaded from disk first (useful for an explicit flush).
    """
    if store is None:
        store = _load()
    store["last_updated"] = _now_ist_iso()
    os.makedirs(os.path.dirname(SEEN_DEALS_PATH), exist_ok=True)
    try:
        with open(SEEN_DEALS_PATH, "w", encoding="utf-8") as fh:
            json.dump(store, fh, indent=2)
        logger.debug("seen_deals saved (%d hashes)", len(store["hashes"]))
    except OSError as exc:
        logger.error("Failed to save seen_deals: %s", exc)


def cleanup_old_hashes(days: int = HASH_CLEANUP_DAYS) -> int:
    """Remove hashes older than *days* days and persist the result.

    Args:
        days: Age threshold in days.  Hashes timestamped before
              ``now - days`` are deleted.

    Returns:
        Number of hashes removed.
    """
    store = _load()
    cutoff = datetime.now(tz=_IST) - timedelta(days=days)
    old_hashes: dict[str, str] = store.get("hashes", {})
    kept: dict[str, str] = {}
    removed = 0

    for digest, ts_str in old_hashes.items():
        try:
            ts = datetime.fromisoformat(ts_str)
            # Ensure the stored timestamp is timezone-aware before comparison.
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_IST)
            if ts >= cutoff:
                kept[digest] = ts_str
            else:
                removed += 1
        except ValueError:
            logger.warning("Unparseable timestamp %r for hash %s; keeping", ts_str, digest)
            kept[digest] = ts_str

    store["hashes"] = kept
    save(store)
    logger.info("cleanup_old_hashes: removed %d hashes older than %d days", removed, days)
    return removed
