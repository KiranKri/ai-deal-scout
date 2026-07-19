"""Tests for the Brave web-search scraper — budget caps and spam filtering."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import config
from scrapers import websearch


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point the quota/rotation state at a temp file for every test."""
    monkeypatch.setattr(config, "WEBSEARCH_STATE_PATH", str(tmp_path / "ws.json"))
    monkeypatch.setattr(websearch, "WEBSEARCH_STATE_PATH", str(tmp_path / "ws.json"))
    monkeypatch.setattr(websearch, "WEBSEARCH_SLEEP_SECONDS", 0)
    yield


def _resp(results, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"results": results}
    return r


def _hit(title, url, desc="Great deal on an AI tool"):
    return {"title": title, "url": url, "content": desc}


# --- no key -----------------------------------------------------------------

def test_no_api_key_returns_empty_and_makes_no_request(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with patch("scrapers.websearch.requests.post") as g:
        assert websearch.fetch_websearch_deals() == []
        g.assert_not_called()


# --- budget caps ------------------------------------------------------------

def test_never_exceeds_max_queries_per_run(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    calls = []

    def rec(*a, **kw):
        calls.append(kw.get("json", {}).get("query"))
        return _resp([_hit(f"Deal {len(calls)}", f"https://x.com/{len(calls)}")])

    with patch("scrapers.websearch.requests.post", side_effect=rec):
        websearch.fetch_websearch_deals()
    assert len(calls) <= config.WEBSEARCH_MAX_QUERIES_PER_RUN


def test_monthly_quota_blocks_further_searching(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    state = {"month": websearch._load_state()["month"],
             "used": config.WEBSEARCH_MONTHLY_QUOTA, "rotation": 0}
    with open(websearch.WEBSEARCH_STATE_PATH, "w") as fh:
        json.dump(state, fh)

    with patch("scrapers.websearch.requests.post") as g:
        assert websearch.fetch_websearch_deals() == []
        g.assert_not_called()


def test_quota_counter_persists_across_runs(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    with patch("scrapers.websearch.requests.post",
               return_value=_resp([_hit("D", "https://a.com/1")])):
        websearch.fetch_websearch_deals()
    first = websearch._load_state()["used"]
    assert first > 0

    with patch("scrapers.websearch.requests.post",
               return_value=_resp([_hit("D", "https://a.com/2")])):
        websearch.fetch_websearch_deals()
    assert websearch._load_state()["used"] > first


def test_early_stop_after_consecutive_empty_queries(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    calls = []

    def rec(*a, **kw):
        calls.append(1)
        return _resp([])

    with patch("scrapers.websearch.requests.post", side_effect=rec):
        websearch.fetch_websearch_deals()
    # The guard is checked at the top of each iteration, so the loop breaks
    # on entering iteration N+1 — meaning exactly N calls are made.
    assert len(calls) == config.WEBSEARCH_EARLY_STOP_EMPTY


def test_time_budget_stops_the_loop(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setattr(websearch, "WEBSEARCH_TIME_BUDGET_SECONDS", 0)
    with patch("scrapers.websearch.requests.post") as g:
        websearch.fetch_websearch_deals()
        assert g.call_count == 0


# --- spam filtering ---------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.retailmenot.com/view/claude",
    "https://couponbirds.com/codes/cursor",
    "https://www.pinterest.com/pin/123",
    "https://knoji.com/perplexity-promo/",
])
def test_coupon_farms_are_blocked(url):
    assert websearch._is_blocked(url)


@pytest.mark.parametrize("url", [
    "https://www.anthropic.com/news/claude-student-plan",
    "https://openai.com/index/chatgpt-free-tier",
    "https://cursor.com/pricing",
])
def test_vendor_domains_are_not_blocked(url):
    assert not websearch._is_blocked(url)


def test_blocked_results_are_excluded_from_output(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    with patch("scrapers.websearch.requests.post", return_value=_resp([
        _hit("Fake Claude codes", "https://retailmenot.com/claude"),
        _hit("Claude student plan", "https://anthropic.com/news/students"),
    ])):
        out = websearch.fetch_websearch_deals()
    urls = {d["url"] for d in out}
    assert "https://anthropic.com/news/students" in urls
    assert not any("retailmenot" in u for u in urls)


# --- shape / robustness -----------------------------------------------------

def test_output_matches_the_standard_deal_contract(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    with patch("scrapers.websearch.requests.post", return_value=_resp([
        _hit("Claude Pro 50% off", "https://anthropic.com/x")])):
        out = websearch.fetch_websearch_deals()
    assert out
    for d in out:
        assert set(d) == {"title", "url", "body", "upvotes", "source"}
        assert d["source"] == "WebSearch"
        assert isinstance(d["upvotes"], int)


@pytest.mark.parametrize("status", [401, 403, 429, 432, 500])
def test_api_errors_never_raise(monkeypatch, status):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    with patch("scrapers.websearch.requests.post", return_value=_resp([], status)):
        assert websearch.fetch_websearch_deals() == []


def test_network_exception_never_raises(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    import requests as rq
    with patch("scrapers.websearch.requests.post",
               side_effect=rq.RequestException("boom")):
        assert websearch.fetch_websearch_deals() == []


def test_vendor_queries_restrict_by_domain_not_site_operator():
    """Tavily ignores `site:` in the query; domains must go in include_domains."""
    qs = websearch._build_queries(0)
    assert len(qs) <= config.WEBSEARCH_MAX_QUERIES_PER_RUN
    vendor = [(q, d) for q, d in qs if d]
    assert vendor, "expected at least one domain-restricted vendor query"
    for q, domains in vendor:
        assert not q.startswith("site:"), "site: does not work on Tavily"
        assert all("." in d for d in domains)
    assert any(not d for _, d in qs), "expected open-web queries too"


def test_include_domains_is_sent_to_the_api(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    seen = []

    def rec(*a, **kw):
        seen.append(kw.get("json", {}).get("include_domains"))
        return _resp([])

    with patch("scrapers.websearch.requests.post", side_effect=rec):
        websearch.fetch_websearch_deals()
    assert any(d for d in seen), "vendor queries must pass include_domains"


def test_rotation_changes_queries_between_runs():
    a = websearch._build_queries(0)
    b = websearch._build_queries(7)
    assert a != b, "rotation cursor must change which tools are searched"


# --- graceful degradation near the quota line -------------------------------

def test_reserve_mode_keeps_only_vendor_queries():
    """Below the reserve threshold, open-web queries are dropped."""
    full = websearch._build_queries(0, vendor_only=False)
    reserve = websearch._build_queries(0, vendor_only=True)
    assert len(reserve) < len(full), "reserve mode must issue fewer queries"
    assert all(domains for _, domains in reserve), \
        "every reserve query must be domain-restricted (vendor only)"
    assert any(not domains for _, domains in full), \
        "normal mode should still include open-web queries"


def test_reserve_mode_triggers_below_threshold(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    state = {"month": websearch._load_state()["month"],
             "used": config.WEBSEARCH_MONTHLY_QUOTA - 10, "rotation": 0}
    with open(websearch.WEBSEARCH_STATE_PATH, "w") as fh:
        json.dump(state, fh)

    seen = []

    def rec(*a, **kw):
        seen.append(kw.get("json", {}).get("include_domains"))
        return _resp([])

    with patch("scrapers.websearch.requests.post", side_effect=rec):
        websearch.fetch_websearch_deals()

    assert seen, "reserve mode should still search, not stop"
    assert all(d for d in seen), "reserve mode must use vendor domains only"


def test_bot_still_works_when_quota_fully_exhausted(monkeypatch):
    """Quota exhaustion disables web search only; other sources are untouched."""
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    state = {"month": websearch._load_state()["month"],
             "used": config.WEBSEARCH_MONTHLY_QUOTA, "rotation": 0}
    with open(websearch.WEBSEARCH_STATE_PATH, "w") as fh:
        json.dump(state, fh)
    with patch("scrapers.websearch.requests.post") as p:
        assert websearch.fetch_websearch_deals() == []
        p.assert_not_called()
