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

import remote_state
from config import HASH_CLEANUP_DAYS, SEEN_DEALS_PATH

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

_EMPTY_STORE: dict = {"hashes": {}, "last_updated": ""}

_REMOTE_FILENAME = "seen_deals.json"

# In-memory cache for the current process.  Previously every is_seen() and
# mark_seen() call re-read the whole store from disk — O(N x M) I/O for N deals
# against M hashes.  With the store now behind a network call that would be one
# HTTP request per deal, so it is loaded once and flushed by save().
_cache: dict | None = None
_cache_sha: str = ""


def _load() -> dict:
    """Load the seen-deals store, from the private repo or from disk.

    Cached for the lifetime of the process; call :func:`reset_cache` in tests
    or after an external write.  Returns an empty store when the file is
    missing or corrupt — losing dedup state causes duplicate sends, which is
    strictly better than aborting the run.
    """
    global _cache, _cache_sha
    if _cache is not None:
        return _cache

    data, sha = remote_state.load(
        _REMOTE_FILENAME, _EMPTY_STORE, local_path=SEEN_DEALS_PATH
    )
    if "hashes" not in data or "last_updated" not in data:
        logger.warning("seen_deals has unexpected structure; resetting")
        data = {"hashes": {}, "last_updated": ""}

    _cache, _cache_sha = data, sha
    logger.debug(
        "seen_deals loaded from %s (%d hashes)",
        "private repo" if remote_state.use_remote() else SEEN_DEALS_PATH,
        len(data.get("hashes", {})),
    )
    return _cache


def reset_cache() -> None:
    """Drop the in-memory store so the next access re-reads it."""
    global _cache, _cache_sha
    _cache, _cache_sha = None, ""


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
    # Empty strings are never hashed: hash("") is identical for URL and
    # title, so one empty-titled deal would alias every later one.
    url_norm = url.strip()
    title_norm = title.strip()
    seen = (bool(url_norm) and _hash_url(url) in hashes) or (
        bool(title_norm) and _hash_title(title) in hashes
    )
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
    if url.strip():
        store["hashes"][_hash_url(url)] = now
    if title.strip():
        store["hashes"][_hash_title(title)] = now
    store["last_updated"] = now
    # Mutates the cache only.  main.py calls save() once after the batch —
    # persisting per deal would be one network round trip per deal.
    logger.debug("Marked seen: url=%r title=%r", url, title)


def save(store: dict | None = None) -> None:
    """Write the store to disk and update ``last_updated``.

    Args:
        store: Optional pre-loaded store dict.  If omitted the store is
               loaded from disk first (useful for an explicit flush).
    """
    global _cache, _cache_sha
    if store is None:
        store = _load()
    store["last_updated"] = _now_ist_iso()
    _cache = store

    ok = remote_state.save(
        _REMOTE_FILENAME, store, sha=_cache_sha, local_path=SEEN_DEALS_PATH
    )
    if ok:
        # Re-read the SHA so a later save() in the same process is not stale.
        _, _cache_sha = remote_state.load(
            _REMOTE_FILENAME, _EMPTY_STORE, local_path=SEEN_DEALS_PATH
        )
        logger.debug("seen_deals saved (%d hashes)", len(store["hashes"]))
    else:
        logger.error(
            "seen_deals save FAILED (%d hashes) — deals may re-send next run",
            len(store["hashes"]),
        )


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
