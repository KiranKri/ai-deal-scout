"""Tests for the staleness heuristic and the split websearch rotation cursors."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import config
import filter as flt
from scrapers import websearch


# ── is_stale: the three classes observed live in the run logs ───────

_JULY = datetime(2026, 7, 19)
_NOV = datetime(2026, 11, 20)


def test_last_years_black_friday_page_is_stale():
    assert flt.is_stale("Suno Black Friday Deals 2025: 40% Off Pro", now=_JULY)


def test_prebaked_black_friday_page_out_of_season_is_stale():
    # Current year, but Black Friday content surfacing in July.
    assert flt.is_stale("Suno Black Friday Deals 2026: 40% Off Pro", now=_JULY)


def test_ancient_announcement_is_stale():
    assert flt.is_stale("May 19, 2020 – Notion 2.8, now free for personal use", now=_JULY)


def test_current_year_deal_is_not_stale():
    assert not flt.is_stale("Best AI tool deals 2026: Cursor 50% off", now=_JULY)


def test_black_friday_in_november_is_not_stale():
    assert not flt.is_stale("Claude Pro Black Friday deal: 30% off", now=_NOV)


def test_undated_deal_is_not_stale():
    assert not flt.is_stale("Cursor Pro 50% off first month", now=_JULY)


# ── rotation: split cursors, unconditional coverage ─────────────────


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(websearch, "WEBSEARCH_STATE_PATH", str(tmp_path / "ws.json"))
    monkeypatch.setattr(websearch, "WEBSEARCH_SLEEP_SECONDS", 0)


def _resp(results):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"results": results}
    return r


def test_legacy_rotation_key_seeds_both_cursors(tmp_path):
    with open(websearch.WEBSEARCH_STATE_PATH, "w") as fh:
        json.dump({"month": websearch._load_state()["month"],
                   "used": 5, "rotation": 3}, fh)
    state = websearch._load_state()
    assert state["rot_vendor"] == 3 % len(config.VENDOR_SITES)
    assert state["rot_tool"] == 3 % len(config.ROTATING_TOOLS)
    assert state["used"] == 5


def test_cursors_advance_by_queries_actually_issued(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    calls = []

    def rec(*a, **kw):  # unique URL per call so early-stop never triggers
        calls.append(1)
        return _resp([{"title": f"Claude deal {len(calls)} 50% off",
                       "url": f"https://anthropic.com/x{len(calls)}",
                       "content": ""}])

    with patch("scrapers.websearch.requests.post", side_effect=rec):
        websearch.fetch_websearch_deals()
    state = websearch._load_state()
    assert state["rot_vendor"] == config.WEBSEARCH_VENDOR_QUERIES % len(config.VENDOR_SITES)
    assert state["rot_tool"] == config.WEBSEARCH_ROTATING_QUERIES % len(config.ROTATING_TOOLS)
    assert "rotation" not in state  # legacy key dropped on save


def test_full_coverage_regardless_of_list_lengths():
    """The old single cursor skipped half the tools at certain list lengths
    (gcd(step, len) > window).  Split cursors advancing by their own query
    count cover every entry unconditionally — verify for pathological
    lengths including the old failure case (40)."""
    for n_tools in (21, 22, 37, 40, 48):
        cursor, hit = 0, set()
        for _ in range(n_tools):  # n runs of 2 queries each is enough
            for i in range(config.WEBSEARCH_ROTATING_QUERIES):
                hit.add((cursor + i) % n_tools)
            cursor = (cursor + config.WEBSEARCH_ROTATING_QUERIES) % n_tools
        assert len(hit) == n_tools, f"coverage hole at len={n_tools}"


def test_rotating_tools_all_pass_the_tool_keyword_gate():
    """Every rotating tool must be matchable by TOOL_KEYWORDS, otherwise the
    credits spent searching it buy results the filter can never pass
    (the Pika/Gamma bug)."""
    for tool in config.ROTATING_TOOLS:
        title = f"{tool} 50% discount on the pro plan deal"
        assert flt.is_relevant(title), (
            f"ROTATING_TOOLS entry {tool!r} cannot pass is_relevant — "
            f"searching it wastes Tavily credits"
        )


def test_vendor_tools_not_duplicated_in_rotating_list():
    vendor_names = {name.lower() for name, _ in config.VENDOR_SITES}
    for tool in config.ROTATING_TOOLS:
        assert tool.lower() not in vendor_names, (
            f"{tool!r} has a VENDOR_SITES entry; searching it open-web "
            f"duplicates coverage and invites coupon-farm noise"
        )


def test_stale_deals_are_dropped_not_just_logged():
    """DROP_STALE turns the heuristic into a filter.

    Measured gain on the labelled set: P 0.808 -> 0.829, F1 0.837 -> 0.841.
    """
    import config
    assert config.DROP_STALE is True

    from filter import is_stale
    assert is_stale("Suno Black Friday Deals 2025: 40% Off", "")
    assert is_stale("May 19, 2020 - Notion 2.8, now free", "")
    assert not is_stale("Cursor Pro 50% off first month", "")
