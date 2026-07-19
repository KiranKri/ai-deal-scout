"""Subscriber store for ai-deal-scout V2.

Persists ``subscribers.json`` in a separate private GitHub repository via
the GitHub Contents API.  All reads and writes go through ``_get_file`` and
``_put_file`` so the rest of the codebase never touches HTTP directly.
"""

import base64
import copy
import json
import logging
import os
import time
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment config (read once at import time)
# ---------------------------------------------------------------------------

GH_PAT: str = os.environ.get("GH_PAT", "")
GH_REPO_DATA: str = os.environ.get("GH_REPO_DATA", "")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMPTY_STORE: dict = {"subscribers": [], "last_updated": ""}

# Local-file fallback.  When GH_REPO_DATA is unset the store lives on disk
# instead of in a private GitHub repo.  This makes local development and
# friend-testing work with zero cloud setup; production sets GH_REPO_DATA and
# transparently switches to the GitHub backend.
LOCAL_STORE_PATH: str = os.environ.get(
    "SUBSCRIBERS_PATH", os.path.join("data", "subscribers.json")
)


def _use_local() -> bool:
    """True when no GitHub backend is configured, so we fall back to disk."""
    return not (GH_REPO_DATA and GH_PAT)


def _local_read() -> dict:
    """Read the on-disk subscriber store, tolerating a missing/corrupt file."""
    if not os.path.exists(LOCAL_STORE_PATH):
        return copy.deepcopy(EMPTY_STORE)
    try:
        with open(LOCAL_STORE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.error("local subscriber store unreadable (%s); starting empty", exc)
        return copy.deepcopy(EMPTY_STORE)


def _local_write(data: dict) -> bool:
    """Write the on-disk subscriber store.  Returns success."""
    try:
        os.makedirs(os.path.dirname(LOCAL_STORE_PATH) or ".", exist_ok=True)
        with open(LOCAL_STORE_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        return True
    except OSError as exc:
        logger.error("local subscriber store write failed: %s", exc)
        return False


class SubscriberStoreError(RuntimeError):
    """Raised (in strict mode) when subscribers.json cannot be fetched.

    Distinguishes "the store is unreachable/corrupt" from "the store is
    genuinely empty" so the pipeline can abort before marking deals seen
    instead of silently skipping the broadcast.
    """

_IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _ist_now() -> str:
    """Return the current IST datetime as an ISO-8601 string.

    Returns:
        ISO-8601 timestamp string with ``+05:30`` offset, e.g.
        ``"2026-06-27T08:00:00+05:30"``.
    """
    from datetime import datetime
    return datetime.now(tz=_IST).isoformat()


def _github_headers() -> dict:
    """Return HTTP headers required for the GitHub Contents API.

    Returns:
        Dict with ``Authorization`` (Bearer token) and ``Accept`` headers.
    """
    return {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
    }


def _file_url() -> str:
    """Return the GitHub Contents API URL for ``subscribers.json``.

    Returns:
        Full URL string pointing at the file in ``GH_REPO_DATA``.
    """
    return f"https://api.github.com/repos/{GH_REPO_DATA}/contents/subscribers.json"


def _get_file(strict: bool = False) -> tuple[dict, str]:
    """Fetch ``subscribers.json`` from the private GitHub repo.

    Args:
        strict: When True, raise :class:`SubscriberStoreError` on any API
                failure or malformed content instead of returning an empty
                store.  A genuine 404 (file not yet created) is *not* an
                error in either mode.

    Returns:
        ``(data, sha)`` where *data* is the parsed JSON dict and *sha* is the
        blob SHA string.  On 404 returns ``(EMPTY_STORE copy, "")``.  On any
        other failure returns ``(EMPTY_STORE copy, "")`` after logging
        CRITICAL (non-strict mode) or raises (strict mode).
    """
    # No GitHub backend configured -> read from disk.  Local reads cannot
    # fail in the way a network call can, so strict mode is a no-op here.
    if _use_local():
        return _local_read(), ""

    try:
        response = requests.get(_file_url(), headers=_github_headers(), timeout=15)
    except Exception as e:  # noqa: BLE001
        logger.critical("GitHub API GET exception: %s", e)
        if strict:
            raise SubscriberStoreError(str(e)) from e
        return copy.deepcopy(EMPTY_STORE), ""

    if response.status_code == 404:
        return copy.deepcopy(EMPTY_STORE), ""

    if response.status_code != 200:
        logger.critical("GitHub API GET failed: status=%d", response.status_code)
        if strict:
            raise SubscriberStoreError(f"HTTP {response.status_code}")
        return copy.deepcopy(EMPTY_STORE), ""

    try:
        body = response.json()
        sha: str = body.get("sha", "")
        raw = base64.b64decode(body["content"]).decode("utf-8")
        data: dict = json.loads(raw)
    except (KeyError, ValueError, UnicodeDecodeError) as e:
        # Covers non-JSON responses, missing "content", bad base64
        # (binascii.Error is a ValueError subclass), and the >1 MB
        # Contents-API case where "content" comes back empty.
        logger.critical("subscribers.json malformed: %s", e)
        if strict:
            raise SubscriberStoreError(f"malformed content: {e}") from e
        return copy.deepcopy(EMPTY_STORE), ""

    return data, sha


def _put_file(data: dict, sha: str) -> bool:
    """Write ``data`` back to ``subscribers.json`` in the private GitHub repo.

    Uses an exponential-backoff retry loop (max 5 attempts) to handle 409
    Conflict responses caused by concurrent writes.  On each 409 the latest
    SHA is re-fetched before retrying.

    Args:
        data: The full subscribers store dict to serialise and upload.
        sha:  Blob SHA of the file being replaced.  Pass ``""`` for a new file.

    Returns:
        ``True`` on HTTP 200 or 201, ``False`` after all retries are exhausted
        or on any non-retryable error.
    """
    if _use_local():
        return _local_write(data)

    current_sha = sha
    for attempt in range(5):
        payload = {
            "message": "chore: update subscribers [skip ci]",
            "content": base64.b64encode(
                json.dumps(data).encode("utf-8")
            ).decode("utf-8"),
            "sha": current_sha,
        }
        try:
            response = requests.put(
                _file_url(), headers=_github_headers(), json=payload, timeout=15
            )
        except Exception as e:  # noqa: BLE001
            logger.error("_put_file exception: %s", e)
            return False

        if response.status_code in (200, 201):
            return True

        if response.status_code == 409:
            sleep_secs = 1 * (2 ** attempt)
            logger.warning(
                "_put_file 409 conflict on attempt %d; retrying in %ds",
                attempt, sleep_secs,
            )
            time.sleep(sleep_secs)
            _, current_sha = _get_file()
            continue

        logger.error("_put_file failed: %d", response.status_code)
        return False

    logger.error("_put_file exhausted retries")
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_subscribers(strict: bool = False) -> list[dict]:
    """Return the full list of subscriber records from the remote store.

    In non-strict mode both a missing file (HTTP 404) and an API failure
    return ``[]``; API failures are logged as CRITICAL inside ``_get_file``.
    In strict mode API failures raise :class:`SubscriberStoreError`.

    Returns:
        List of subscriber dicts, or ``[]`` on 404 / (non-strict) error.
    """
    data, _ = _get_file(strict=strict)
    return data.get("subscribers", [])


def get_active_chat_ids(strict: bool = False) -> list[int]:
    """Return the chat IDs of all currently active subscribers.

    Args:
        strict: Propagated to ``_get_file``; when True an unreachable or
                corrupt store raises instead of masquerading as "no
                subscribers".  The pipeline uses strict mode so deals are
                never marked seen when nobody could receive them.

    Returns:
        List of integer chat IDs where ``active`` is truthy.
    """
    return [s["chat_id"] for s in get_subscribers(strict=strict) if s.get("active")]


def add_subscriber(chat_id: int, username: str | None) -> str:
    """Add a new subscriber or reactivate an existing inactive one.

    Does **not** write to GitHub when the subscriber is already active
    (avoids an unnecessary API call).

    Args:
        chat_id:  Telegram chat ID of the user.
        username: Telegram ``@username``, or ``None`` if not set.

    Returns:
        One of ``"new"``, ``"already_active"``, ``"resubscribed"``,
        or ``"error"``.
    """
    data, sha = _get_file()

    for sub in data.get("subscribers", []):
        if sub["chat_id"] == chat_id:
            if sub.get("active"):
                return "already_active"
            # Reactivate
            sub["active"] = True
            sub["resubscribe_count"] = sub.get("resubscribe_count", 0) + 1
            sub["subscribed_at"] = _ist_now()
            data["last_updated"] = _ist_now()
            return "resubscribed" if _put_file(data, sha) else "error"

    # New subscriber
    data.setdefault("subscribers", []).append(
        {
            "chat_id": chat_id,
            "username": username,
            "subscribed_at": _ist_now(),
            "active": True,
            "resubscribe_count": 0,
        }
    )
    data["last_updated"] = _ist_now()
    return "new" if _put_file(data, sha) else "error"


def deactivate_subscriber(chat_id: int) -> str:
    """Mark a subscriber as inactive (soft-delete).

    Does **not** write to GitHub when there is nothing to change.

    Args:
        chat_id: Telegram chat ID of the user to deactivate.

    Returns:
        One of ``"deactivated"``, ``"already_inactive"``, ``"not_found"``,
        or ``"error"``.
    """
    data, sha = _get_file()

    for sub in data.get("subscribers", []):
        if sub["chat_id"] == chat_id:
            if not sub.get("active"):
                return "already_inactive"
            sub["active"] = False
            data["last_updated"] = _ist_now()
            return "deactivated" if _put_file(data, sha) else "error"

    return "not_found"


def get_subscriber_count() -> dict:
    """Return a summary dict with total, active, and inactive counts.

    Returns:
        ``{"total": int, "active": int, "inactive": int}``.  All zeros on
        API failure.
    """
    subs = get_subscribers()
    active = sum(1 for s in subs if s.get("active"))
    return {
        "total": len(subs),
        "active": active,
        "inactive": len(subs) - active,
    }


def batch_deactivate(chat_ids: list[int]) -> bool:
    """Deactivate multiple subscribers in a single GitHub commit.

    Args:
        chat_ids: List of Telegram chat IDs to deactivate.

    Returns:
        ``True`` if no write was needed or the write succeeded, ``False``
        on GitHub API failure.
    """
    if not chat_ids:
        return True

    data, sha = _get_file()
    target = set(chat_ids)
    changed = False

    for sub in data.get("subscribers", []):
        if sub["chat_id"] in target and sub.get("active"):
            sub["active"] = False
            changed = True

    if not changed:
        return True

    data["last_updated"] = _ist_now()
    return _put_file(data, sha)
