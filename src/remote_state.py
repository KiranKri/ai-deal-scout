"""Shared JSON state store: private GitHub repo, or local disk.

Machine-generated state (which deals have been sent, who is subscribed) must
not live in the public repo:

* **Conflicts.** Two workflows commit every 10 minutes.  Any file they touch
  collides with local work constantly, and a hash blob cannot be merged
  meaningfully — every conflict is resolved by throwing one side away.
* **Privacy.** Subscriber identity must never reach a public repo.

So this state goes to the private repo behind ``GH_REPO_DATA``.  When those
credentials are absent — local development, tests — it falls back to disk, so
nothing needs cloud setup to run.

``bot/subscribers.py`` predates this module and keeps its own copy of the same
logic plus subscriber-specific behaviour (strict mode, conflict retry); it is
left alone rather than refactored mid-flight.
"""

import base64
import copy
import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_API = "https://api.github.com/repos/{repo}/contents/{path}"
_MAX_PUT_ATTEMPTS = 5


def _pat() -> str:
    return os.environ.get("GH_PAT", "").strip()


def _repo() -> str:
    return os.environ.get("GH_REPO_DATA", "").strip()


def use_remote() -> bool:
    """True when a private data repo is configured for state storage."""
    return bool(_pat() and _repo())


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_pat()}",
        "Accept": "application/vnd.github+json",
    }


def _url(filename: str) -> str:
    return _API.format(repo=_repo(), path=filename)


# ---------------------------------------------------------------------------
# Local backend
# ---------------------------------------------------------------------------


def _local_load(path: str, default: dict) -> dict:
    """Read JSON from disk, returning *default* when missing or corrupt."""
    if not os.path.exists(path):
        return copy.deepcopy(default)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.error("%s unreadable (%s); starting fresh", path, exc)
        return copy.deepcopy(default)


def _local_save(path: str, data: dict) -> bool:
    """Write JSON to disk.  Returns success; never raises."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        return True
    except OSError as exc:
        logger.error("failed to write %s: %s", path, exc)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load(filename: str, default: dict, local_path: str | None = None) -> tuple[dict, str]:
    """Load a JSON state file from the private repo, or from disk.

    Args:
        filename:   Name of the file in the private repo, e.g. ``seen_deals.json``.
        default:    Returned (deep-copied) when the file is absent or unreadable.
        local_path: Path used in local mode.  Defaults to ``data/<filename>``.

    Returns:
        ``(data, sha)``.  *sha* is the blob SHA needed by :func:`save` in
        remote mode, and an empty string in local mode.  Never raises: a
        failure to read state should degrade, not abort the run.
    """
    path = local_path or os.path.join("data", filename)

    if not use_remote():
        return _local_load(path, default), ""

    try:
        response = requests.get(_url(filename), headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        logger.error("GET %s failed: %s", filename, exc)
        return copy.deepcopy(default), ""

    if response.status_code == 404:
        logger.info("%s not in %s yet; starting fresh", filename, _repo())
        return copy.deepcopy(default), ""
    if response.status_code != 200:
        logger.error("GET %s: HTTP %d", filename, response.status_code)
        return copy.deepcopy(default), ""

    try:
        body = response.json()
        raw = base64.b64decode(body["content"]).decode("utf-8")
        return json.loads(raw), body.get("sha", "")
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        # Covers non-JSON responses, bad base64, and the >1 MB Contents-API
        # case where "content" comes back empty.
        logger.error("%s malformed: %s", filename, exc)
        return copy.deepcopy(default), ""


def save(
    filename: str, data: dict, sha: str = "", local_path: str | None = None
) -> bool:
    """Write a JSON state file to the private repo, or to disk.

    Retries on HTTP 409/422 (stale SHA from a concurrent write) with
    exponential backoff, re-reading the current SHA each time.

    Args:
        filename:   Name of the file in the private repo.
        data:       State to serialise.
        sha:        Blob SHA from :func:`load`.  Empty for a new file.
        local_path: Path used in local mode.

    Returns:
        True on success.  Never raises.
    """
    path = local_path or os.path.join("data", filename)

    if not use_remote():
        return _local_save(path, data)

    current_sha = sha
    for attempt in range(_MAX_PUT_ATTEMPTS):
        payload: dict[str, Any] = {
            "message": f"chore: update {filename} [skip ci]",
            "content": base64.b64encode(
                json.dumps(data, indent=2).encode("utf-8")
            ).decode("utf-8"),
        }
        if current_sha:
            payload["sha"] = current_sha

        try:
            response = requests.put(
                _url(filename), headers=_headers(), json=payload, timeout=15
            )
        except requests.RequestException as exc:
            logger.error("PUT %s failed: %s", filename, exc)
            return False

        if response.status_code in (200, 201):
            return True

        # 409 and 422 both indicate a stale SHA when another writer got there
        # first; re-read and retry rather than losing the write.
        if response.status_code in (409, 422):
            wait = 2 ** attempt
            logger.warning(
                "PUT %s conflict (HTTP %d) attempt %d; retrying in %ds",
                filename, response.status_code, attempt + 1, wait,
            )
            time.sleep(wait)
            _, current_sha = load(filename, {}, local_path)
            continue

        logger.error("PUT %s: HTTP %d", filename, response.status_code)
        return False

    logger.error("PUT %s: exhausted retries", filename)
    return False
