"""Tests for src/dedup.py."""

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

# Allow importing from src/ without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dedup  # noqa: E402  (after sys.path patch)

_IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_store(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Redirect SEEN_DEALS_PATH to a temp file for every test."""
    store_path = str(tmp_path / "seen_deals.json")
    monkeypatch.setattr(dedup, "SEEN_DEALS_PATH", store_path)
    # Also patch the import inside config so _load uses the same path.
    import config as cfg

    monkeypatch.setattr(cfg, "SEEN_DEALS_PATH", store_path)
    yield store_path


# ---------------------------------------------------------------------------
# Tests: is_seen
# ---------------------------------------------------------------------------


def test_is_seen_returns_false_for_new_deal():
    """A brand-new URL and title should not be seen."""
    assert dedup.is_seen("https://example.com/deal", "Amazing AI Deal") is False


def test_is_seen_false_empty_store(isolated_store):
    """is_seen should return False when the store file doesn't exist yet."""
    assert not os.path.exists(isolated_store)
    assert dedup.is_seen("https://example.com/x", "Some Title") is False


# ---------------------------------------------------------------------------
# Tests: mark_seen + is_seen
# ---------------------------------------------------------------------------


def test_mark_seen_makes_url_seen():
    """After mark_seen, is_seen should return True for the same URL."""
    url = "https://example.com/deal1"
    title = "Deal One"
    dedup.mark_seen(url, title)
    assert dedup.is_seen(url, "different title") is True


def test_mark_seen_makes_title_seen():
    """After mark_seen, is_seen should return True for the same title."""
    url = "https://example.com/deal2"
    title = "Deal Two"
    dedup.mark_seen(url, title)
    assert dedup.is_seen("https://different.com/url", title) is True


def test_mark_seen_both_hashes_stored(isolated_store):
    """mark_seen records both hashes; save() is what persists them.

    mark_seen mutates the in-memory store only.  When the store lives in the
    private repo, persisting per deal would be one network round trip per
    deal, so main.py flushes once after the batch instead.
    """
    url = "https://example.com/deal3"
    title = "Deal Three"
    dedup.mark_seen(url, title)
    dedup.save()

    with open(isolated_store, encoding="utf-8") as fh:
        data = json.load(fh)

    url_hash = dedup._hash_url(url)
    title_hash = dedup._hash_title(title)
    assert url_hash in data["hashes"]
    assert title_hash in data["hashes"]


def test_mark_seen_normalises_case():
    """is_seen should match regardless of case differences."""
    dedup.mark_seen("https://EXAMPLE.COM/DEAL", "GREAT DEAL")
    assert dedup.is_seen("https://example.com/deal", "great deal") is True


# ---------------------------------------------------------------------------
# Tests: save / load round-trip
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(isolated_store):
    """Data written by save() should be faithfully restored by _load()."""
    store = {"hashes": {"abc123": "2025-01-01T08:00:00+05:30"}, "last_updated": "2025-01-01T08:00:00+05:30"}
    _write_store(isolated_store, store)

    loaded = dedup._load()
    assert loaded["hashes"] == store["hashes"]
    assert loaded["last_updated"] == store["last_updated"]


def test_load_returns_empty_on_missing_file(isolated_store):
    """_load() should return an empty store when the file doesn't exist."""
    loaded = dedup._load()
    assert loaded == {"hashes": {}, "last_updated": ""}


def test_load_returns_empty_on_corrupt_file(isolated_store):
    """_load() should return an empty store on JSON parse errors."""
    with open(isolated_store, "w", encoding="utf-8") as fh:
        fh.write("not valid json{{{{")
    loaded = dedup._load()
    assert loaded == {"hashes": {}, "last_updated": ""}


def test_save_updates_last_updated(isolated_store):
    """save() must update the last_updated field."""
    dedup.save()
    with open(isolated_store, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["last_updated"] != ""


# ---------------------------------------------------------------------------
# Tests: cleanup_old_hashes
# ---------------------------------------------------------------------------


def _ist_ts(delta_days: int) -> str:
    """Return an IST ISO timestamp offset by *delta_days* from now."""
    dt = datetime.now(tz=_IST) - timedelta(days=delta_days)
    return dt.isoformat()


def test_cleanup_removes_old_hashes(isolated_store):
    """Hashes older than the threshold should be removed."""
    old_ts = _ist_ts(100)   # 100 days ago → older than default 90
    new_ts = _ist_ts(10)    # 10 days ago  → should survive

    store = {
        "hashes": {
            "oldhash1": old_ts,
            "oldhash2": old_ts,
            "newhash1": new_ts,
        },
        "last_updated": "",
    }
    _write_store(isolated_store, store)

    removed = dedup.cleanup_old_hashes(days=90)
    assert removed == 2

    with open(isolated_store, encoding="utf-8") as fh:
        data = json.load(fh)
    assert "oldhash1" not in data["hashes"]
    assert "oldhash2" not in data["hashes"]
    assert "newhash1" in data["hashes"]


def test_cleanup_keeps_recent_hashes(isolated_store):
    """Hashes newer than the threshold must not be removed."""
    store = {
        "hashes": {"recenthash": _ist_ts(5)},
        "last_updated": "",
    }
    _write_store(isolated_store, store)

    removed = dedup.cleanup_old_hashes(days=90)
    assert removed == 0


def test_cleanup_returns_zero_on_empty_store(isolated_store):
    """cleanup_old_hashes on an empty store should return 0."""
    removed = dedup.cleanup_old_hashes()
    assert removed == 0


def test_cleanup_persists_result(isolated_store):
    """After cleanup, the store on disk should reflect removed hashes."""
    old_ts = _ist_ts(200)
    store = {"hashes": {"stale": old_ts}, "last_updated": ""}
    _write_store(isolated_store, store)

    dedup.cleanup_old_hashes(days=90)

    with open(isolated_store, encoding="utf-8") as fh:
        data = json.load(fh)
    assert "stale" not in data["hashes"]


# ---------------------------------------------------------------------------
# Additional tests
# ---------------------------------------------------------------------------


def test_is_seen_with_empty_string():
    """is_seen with empty strings should return False on a fresh store."""
    assert dedup.is_seen("", "") is False


def test_is_seen_with_whitespace_only_strings():
    """is_seen with whitespace-only strings should return False on a fresh store."""
    assert dedup.is_seen("   ", "   ") is False


def test_is_seen_url_match_with_different_title():
    """URL hash alone is sufficient for a positive match — title need not match."""
    dedup.mark_seen("https://example.com/deal", "Original Title")
    assert dedup.is_seen("https://example.com/deal", "Completely Different Title") is True


def test_is_seen_title_match_with_different_url():
    """Title hash alone is sufficient for a positive match — URL need not match."""
    dedup.mark_seen("https://example.com/deal", "Original Title")
    assert dedup.is_seen("https://different.com", "Original Title") is True


def test_mark_seen_idempotent(isolated_store):
    """Calling mark_seen twice with the same args must not create duplicate hash keys."""
    url = "https://example.com"
    title = "Some Deal"
    dedup.mark_seen(url, title)
    dedup.mark_seen(url, title)
    dedup.save()

    with open(isolated_store, encoding="utf-8") as fh:
        data = json.load(fh)

    url_hash = dedup._hash_url(url)
    assert list(data["hashes"].keys()).count(url_hash) == 1


def test_save_uses_ist_timestamp(isolated_store):
    """save() must record last_updated with the IST UTC offset (+05:30)."""
    dedup.mark_seen("https://example.com", "Test Deal")
    dedup.save()

    with open(isolated_store, encoding="utf-8") as fh:
        data = json.load(fh)

    assert "+05:30" in data["last_updated"]


def test_cleanup_boundary_precision(isolated_store):
    """Entry at now-90d-1min is removed; entry at now-89d survives."""
    just_past_cutoff = (
        datetime.now(tz=_IST) - timedelta(days=90, minutes=1)
    ).isoformat()
    well_within = (datetime.now(tz=_IST) - timedelta(days=89)).isoformat()

    store = {
        "hashes": {
            "stale_boundary": just_past_cutoff,
            "recent_boundary": well_within,
        },
        "last_updated": "",
    }
    _write_store(isolated_store, store)

    removed = dedup.cleanup_old_hashes(days=90)

    assert removed == 1
    with open(isolated_store, encoding="utf-8") as fh:
        data = json.load(fh)
    assert "stale_boundary" not in data["hashes"]
    assert "recent_boundary" in data["hashes"]


# ---------------------------------------------------------------------------
# Store now lives in the private repo (or on disk locally), read once per run.
# ---------------------------------------------------------------------------


def test_mark_seen_does_not_write_per_deal(isolated_store, monkeypatch):
    """Marking must not hit the backend once per deal.

    Under the remote backend that would be one HTTP PUT per deal; a 90-deal
    run would make 90 network writes instead of one.
    """
    import remote_state
    writes = []
    monkeypatch.setattr(
        remote_state, "save",
        lambda *a, **k: (writes.append(1), True)[1],
    )
    for i in range(10):
        dedup.mark_seen(f"https://example.com/{i}", f"Deal {i}")
    assert writes == [], "mark_seen wrote to the backend; it should only cache"

    dedup.save()
    assert len(writes) == 1, "save() should flush exactly once"


def test_store_is_read_once_per_run(isolated_store, monkeypatch):
    """is_seen must not re-read the store for every deal."""
    import remote_state
    reads = []
    real_load = remote_state.load

    def counting_load(*a, **k):
        reads.append(1)
        return real_load(*a, **k)

    monkeypatch.setattr(remote_state, "load", counting_load)
    dedup.reset_cache()
    for i in range(20):
        dedup.is_seen(f"https://example.com/{i}", f"Deal {i}")
    assert len(reads) == 1, f"store read {len(reads)} times for 20 deals"


def test_reset_cache_forces_reread(isolated_store):
    dedup.is_seen("https://a.com", "A")
    dedup.reset_cache()
    assert dedup._cache is None
