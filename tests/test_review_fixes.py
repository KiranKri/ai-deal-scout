"""Regression tests for the fixes from docs/REVIEW_FINDINGS.md.

Covers: C1/H1 (strict subscriber fetch), H4 (BitDegree URL absolutisation),
M2 ("% off" boundary), M4 (cross-source dedup keeps higher-upvote copy),
L3 (empty-string dedup aliasing), M7 (send_deals returns delivery counts).
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

import bot.subscribers as subscribers
import dedup
import filter as flt
from scrapers import run_all_scrapers
from scrapers.bitdegree import _strategy_ai_links, _strategy_article


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    if body is not None:
        r.json.return_value = body
    else:
        r.json.side_effect = ValueError("no json")
    return r


# ── C1 / H1: subscriber store failures ──────────────────────────────


def test_get_file_malformed_base64_does_not_raise(_force_github_backend):
    body = {"sha": "abc", "content": "!!!not-base64!!!"}
    with patch("bot.subscribers.requests.get", return_value=_resp(200, body)):
        data, sha = subscribers._get_file()
    assert data == subscribers.EMPTY_STORE
    assert sha == ""


def test_get_file_oversize_empty_content_does_not_raise(_force_github_backend):
    # Contents API >1MB: content == "" with encoding "none" → json.loads("")
    body = {"sha": "abc", "content": "", "encoding": "none"}
    with patch("bot.subscribers.requests.get", return_value=_resp(200, body)):
        data, _ = subscribers._get_file()
    assert data == subscribers.EMPTY_STORE


def test_get_file_missing_content_key_does_not_raise(_force_github_backend):
    with patch("bot.subscribers.requests.get", return_value=_resp(200, {"sha": "abc"})):
        data, _ = subscribers._get_file()
    assert data == subscribers.EMPTY_STORE


def test_strict_raises_on_api_failure(_force_github_backend):
    with patch("bot.subscribers.requests.get", return_value=_resp(500, {})):
        with pytest.raises(subscribers.SubscriberStoreError):
            subscribers.get_active_chat_ids(strict=True)


def test_strict_raises_on_network_exception(_force_github_backend):
    with patch(
        "bot.subscribers.requests.get", side_effect=ConnectionError("down")
    ):
        with pytest.raises(subscribers.SubscriberStoreError):
            subscribers._get_file(strict=True)


def test_strict_404_is_not_an_error(_force_github_backend):
    # A file that doesn't exist yet is genuinely empty, not a failure.
    with patch("bot.subscribers.requests.get", return_value=_resp(404, {})):
        assert subscribers.get_active_chat_ids(strict=True) == []


def test_non_strict_api_failure_still_soft_fails(_force_github_backend):
    with patch("bot.subscribers.requests.get", return_value=_resp(500, {})):
        assert subscribers.get_active_chat_ids() == []


# ── M2: "% off" word-boundary fix ───────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "% off cursor pro this week",
        "get 50 % off claude",
        "50% off cursor",
        "(% off) claude",
    ],
)
def test_percent_off_variants_match(text):
    assert flt._match(text, "% off")


def test_percent_off_no_space_variant_scores_as_deal():
    # "50%off" is covered by the separate "%off" keyword in DEAL_KEYWORDS.
    assert flt._match("50%off cursor annual", "%off")
    assert flt.is_relevant("50%off cursor annual") is True


def test_word_boundaries_still_enforced_for_word_keywords():
    assert not flt._match("cursory glance at deals", "cursor")
    assert flt._match("cursor deal", "cursor")


# ── M4: cross-source dedup keeps the higher-upvote copy ─────────────


def test_cross_source_collision_keeps_higher_upvotes():
    reddit = [{"title": "Claude Pro 50% off deal", "url": "r", "body": "",
               "upvotes": 0, "source": "Reddit"}]
    hn = [{"title": "Claude Pro 50% off deal!", "url": "h", "body": "",
           "upvotes": 87, "source": "HackerNews"}]
    with patch("scrapers.fetch_reddit_deals", return_value=reddit), \
         patch("scrapers.fetch_hn_deals", return_value=hn), \
         patch("scrapers.fetch_rss_deals", return_value=[]), \
         patch("scrapers.fetch_bitdegree_deals", return_value=[]), \
         patch("scrapers.fetch_websearch_deals", return_value=[]):
        out = run_all_scrapers()
    assert len(out) == 1
    assert out[0]["upvotes"] == 87
    assert out[0]["source"] == "HackerNews"


def test_cross_source_collision_keeps_first_when_equal():
    a = [{"title": "Cursor discount deal", "url": "a", "body": "",
          "upvotes": 0, "source": "Reddit"}]
    b = [{"title": "Cursor discount deal", "url": "b", "body": "",
          "upvotes": 0, "source": "RSS"}]
    with patch("scrapers.fetch_reddit_deals", return_value=a), \
         patch("scrapers.fetch_hn_deals", return_value=[]), \
         patch("scrapers.fetch_rss_deals", return_value=b), \
         patch("scrapers.fetch_bitdegree_deals", return_value=[]), \
         patch("scrapers.fetch_websearch_deals", return_value=[]):
        out = run_all_scrapers()
    assert len(out) == 1
    assert out[0]["url"] == "a"


# ── H4: BitDegree relative hrefs ────────────────────────────────────


def test_ai_links_strategy_absolutises_relative_hrefs():
    soup = BeautifulSoup(
        '<a href="/ai/claude-deal">Claude Pro discount deal here</a>',
        "html.parser",
    )
    items = _strategy_ai_links(soup)
    assert items[0]["url"] == "https://www.bitdegree.org/ai/claude-deal"


def test_article_strategy_absolutises_relative_hrefs():
    soup = BeautifulSoup(
        "<article><h2>Great Claude Pro deal</h2>"
        '<a href="/ai/claude-deal">go</a><p>desc</p></article>',
        "html.parser",
    )
    items = _strategy_article(soup)
    assert items[0]["url"] == "https://www.bitdegree.org/ai/claude-deal"


def test_absolute_hrefs_left_untouched():
    soup = BeautifulSoup(
        '<a href="https://example.com/ai/tool-deal-page">Cursor annual discount</a>',
        "html.parser",
    )
    items = _strategy_ai_links(soup)
    assert items[0]["url"] == "https://example.com/ai/tool-deal-page"


# ── L3: empty-string dedup aliasing ─────────────────────────────────


@pytest.fixture
def isolated_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(dedup, "SEEN_DEALS_PATH", str(tmp_path / "seen.json"))


def test_empty_title_deals_do_not_alias(isolated_seen):
    dedup.mark_seen("https://a.com", "")
    assert dedup.is_seen("https://b.com", "") is False
    assert dedup.is_seen("https://a.com", "anything") is True


def test_empty_url_deals_do_not_alias(isolated_seen):
    dedup.mark_seen("", "Deal one title")
    assert dedup.is_seen("", "Deal two title") is False
    assert dedup.is_seen("https://x.com", "Deal one title") is True


# ── M7: send_deals returns delivery counts ──────────────────────────


def _mock_send(status):
    r = MagicMock()
    r.status_code = status
    return r


def test_send_deals_returns_success_and_total():
    import src.notifier as notifier

    with patch("src.notifier.requests.post", return_value=_mock_send(200)), \
         patch("src.notifier.time.sleep"):
        result = notifier.send_deals([], [1, 2, 3])
    assert result == (3, 3)


def test_send_deals_partial_failure_reflected_in_count():
    import src.notifier as notifier

    statuses = iter([200, 500, 200])
    with patch("src.notifier.requests.post",
               side_effect=lambda *a, **k: _mock_send(next(statuses))), \
         patch("src.notifier.time.sleep"):
        result = notifier.send_deals([], [1, 2, 3])
    assert result == (2, 3)


def test_send_deals_empty_chat_ids_returns_zero():
    import src.notifier as notifier

    with patch("src.notifier.requests.post") as post:
        assert notifier.send_deals([], []) == (0, 0)
        post.assert_not_called()


# ---------------------------------------------------------------------------
# Zero-subscriber dry run: deals must NOT be burned when nobody can receive
# them, or the first real subscriber inherits an empty 90-day dedup window.
# ---------------------------------------------------------------------------


def _run_main_with(chat_ids, deals, tmp_path, monkeypatch):
    """Run main.main() with scrapers/subscribers stubbed; return the dedup store."""
    import importlib
    import config

    store = tmp_path / "seen.json"
    hist = tmp_path / "hist.md"
    monkeypatch.setattr(config, "SEEN_DEALS_PATH", str(store))
    monkeypatch.setattr(config, "HISTORY_PATH", str(hist))

    import dedup as dedup_mod
    import history as history_mod
    importlib.reload(dedup_mod)
    importlib.reload(history_mod)

    import main as main_mod

    monkeypatch.setattr(main_mod, "get_active_chat_ids", lambda strict=False: chat_ids)
    monkeypatch.setattr(main_mod, "_alert_admin", lambda *a, **k: None)
    monkeypatch.setitem(__import__("sys").modules, "scrapers", MagicMock(run_all_scrapers=lambda: deals))

    with patch("notifier.send_deals", return_value=(len(chat_ids), len(chat_ids))):
        main_mod.main([])

    return json.loads(store.read_text()) if store.exists() else {"hashes": {}}


DEAL = {
    "title": "Cursor Pro 50% off first month",
    "url": "https://example.com/cursor",
    "body": "",
    "upvotes": 0,
    "source": "Reddit",
}


def test_zero_subscribers_does_not_mark_deals_seen(tmp_path, monkeypatch):
    """With no subscribers the dedup store must stay empty (dry run)."""
    store = _run_main_with([], [DEAL], tmp_path, monkeypatch)
    assert store.get("hashes") == {}, (
        "deals were marked seen with zero subscribers — the first real "
        "subscriber would never receive them"
    )


def test_with_subscribers_deals_are_marked_seen(tmp_path, monkeypatch):
    """The dry-run guard must not suppress normal marking."""
    store = _run_main_with([12345], [DEAL], tmp_path, monkeypatch)
    assert len(store.get("hashes", {})) > 0


# ---------------------------------------------------------------------------
# Recall pass: inflection tolerance + BitDegree tool-name recovery
# ---------------------------------------------------------------------------

import pytest as _pytest


@pytest.fixture
def _force_github_backend(monkeypatch):
    """Exercise the GitHub backend explicitly.

    subscribers.py falls back to a local file when GH_REPO_DATA/GH_PAT are
    unset, so without this the mocked HTTP calls are never reached.
    """
    monkeypatch.setattr(subscribers, "GH_REPO_DATA", "owner/data-repo")
    monkeypatch.setattr(subscribers, "GH_PAT", "fake-pat")



@_pytest.mark.parametrize("base,inflected", [
    ("deal", "deals"),
    ("discount", "discounts"),
    ("discount", "discounted"),
    ("offer", "offers"),
    ("offer", "offering"),
    ("coupon", "coupons"),
    ("promo", "promos"),
    ("saving", "savings"),
])
def test_inflected_forms_now_match(base, inflected):
    """Plurals and participles must match their base keyword."""
    assert flt._match(f"huge {inflected} on cursor pro", base), (
        f"{inflected!r} should match keyword {base!r}"
    )


def test_no_inflection_keywords_stay_singular():
    """Keywords whose plural means something else must not inflect.

    "sale" is a discount event; "sales" is a business function.  Inflecting
    it matched 4 OpenAI corporate posts ("Driving sales productivity",
    "ChatGPT for sales teams") in the historical corpus.
    """
    assert flt._match("flash sale on cursor pro", "sale")
    assert not flt._match("driving sales productivity at openai", "sale")


def test_negative_keywords_veto_ma_news():
    """M&A coverage must be vetoed even when it scores highly."""
    for title in [
        "Anthropic chief back in talks with Pentagon about AI deal",
        "Circular AI deals among OpenAI, Nvidia, AMD are raising eyebrows",
        "FTC opens antitrust probe of Microsoft AI deal",
        "Google DeepMind workers vote to unionize over military AI deals",
    ]:
        assert not flt.is_relevant(title, "", 0), f"should be vetoed: {title!r}"


def test_veto_does_not_block_genuine_deals():
    """The veto list must not catch real deals."""
    for title in [
        "Cursor Pro 50% off first month",
        "Claude Pro free for students this semester",
        "Perplexity offering free year to new users",
    ]:
        assert flt.is_relevant(title, "", 0), f"should pass: {title!r}"


def test_inflection_does_not_break_word_boundary():
    """Inflection tolerance must not reintroduce substring false positives."""
    # "dealership" must not match "deal" — the \b still closes the match
    assert not flt._match("visited the dealership today", "deal")
    assert not flt._match("a cursory glance at the code", "cursor")
    assert not flt._match("saleable goods in stock", "sale")


def test_real_world_inflected_titles_pass_filter():
    """Titles that previously failed on morphology alone must now pass."""
    for title in [
        "Cursor discounts for students this month",
        "Perplexity offering a free month to new users",
        "Big deals on Claude Pro subscriptions",
    ]:
        assert flt.is_relevant(title, "", 0), f"should pass: {title!r}"


def test_ai_news_still_rejected_despite_inflection():
    """M&A / zombie headlines and pure retail noise must stay out."""
    for title in [
        "GitHub cuts AI deals with Google, Anthropic",
        "Silicon Valley's AI deals are creating zombie startups",
    ]:
        assert not flt.is_relevant(title, "", 0), f"should be vetoed: {title!r}"
    assert not flt.is_relevant(
        "Hisense 75in QLED 4K TV at $498 (38% off)", "", 0
    )
    assert not flt.is_relevant(
        "iRobot Roomba 105 Vac Robot Vacuum at $249 (45% off)", "", 0
    )


def test_bitdegree_bare_price_title_recovers_tool_name_from_card():
    """A bare '50% OFF' heading must still reach the filter via card context."""
    html = """
    <div class="deal-card">
      <a href="/ai/claude-deal"><h3>50% OFF FIRST MONTH</h3></a>
      <p>Claude Pro subscription discount for new users, limited time.</p>
    </div>
    """
    from scrapers.bitdegree import _strategy_card
    items = _strategy_card(BeautifulSoup(html, "html.parser"))
    assert items, "card strategy should find the deal"
    d = items[0]
    # The scraper now prefixes the product name onto bare price headings,
    # and the card context still carries it in the body.
    assert d["title"] == "Claude - 50% OFF FIRST MONTH"
    assert "claude" in d["body"].lower()
    assert flt.is_relevant(d["title"], d["body"], 0), (
        "bare-price heading should pass once card context supplies the tool name"
    )


# ---------------------------------------------------------------------------
# Show HN gate + BitDegree tool labelling
# ---------------------------------------------------------------------------


def test_show_hn_without_price_signal_is_rejected():
    """Show HN product launches are not deals."""
    for t in [
        "Show HN: I built a free AI jigsaw puzzle generator, earn credits",
        "Show HN: Bard PDF - Chat with Pdf in Google Bard or Gemini",
        "Show HN: GuMCP - Open-source MCP servers, hosted for free",
        "Show HN: Gemini free tier is all you need",
    ]:
        assert not flt.is_relevant(t, "", 0), f"should be rejected: {t!r}"


def test_show_hn_with_price_signal_is_kept():
    """A Show HN post with an explicit price signal is a genuine deal."""
    for t in [
        "Show HN: GPT Everywhere - Mac Support | 50% Discount for 20 First",
        "Show HN: WildfireDeals - Daily AI Tool Deals (50-90% Off)",
        "Show HN: Free Trial OpenAI Sora2 AI Video Generator",
    ]:
        assert flt.is_relevant(t, "", 0), f"should be kept: {t!r}"


def test_non_show_hn_titles_unaffected_by_gate():
    """The Show HN rule must not touch ordinary titles."""
    assert flt.is_relevant("GitHub Copilot is free until August 22", "", 0)


def test_bitdegree_title_gains_tool_name_from_url():
    """Bare price headings must name the product."""
    from scrapers.bitdegree import _label, _tool_from_url

    assert _tool_from_url("https://www.bitdegree.org/ai/descript-review") == "Descript"
    assert _tool_from_url("https://www.bitdegree.org/ai/goon/jasper") == "Jasper"
    assert _label("UP TO 58% OFF", "https://www.bitdegree.org/ai/descript-review") == (
        "Descript - UP TO 58% OFF"
    )


def test_bitdegree_label_does_not_duplicate_existing_name():
    """A title that already names the tool is left alone."""
    from scrapers.bitdegree import _label

    t = "Descript 58% off annual"
    assert _label(t, "https://www.bitdegree.org/ai/descript-review") == t


# ---------------------------------------------------------------------------
# Deals must survive a failed broadcast.
#
# Observed live: a stale subscriber ID made every send return HTTP 400 while
# the pipeline had already marked 89 deals seen. They were inside the 90-day
# dedup window, delivered to nobody, with no retry path.
# ---------------------------------------------------------------------------


def _run_pipeline(chat_ids, deals, delivered, tmp_path, monkeypatch):
    """Run main() with a controlled delivery outcome; return the dedup store."""
    import importlib
    import config

    store = tmp_path / "seen.json"
    monkeypatch.setattr(config, "SEEN_DEALS_PATH", str(store))
    monkeypatch.setattr(config, "HISTORY_PATH", str(tmp_path / "hist.md"))

    import dedup as dedup_mod
    import history as history_mod
    importlib.reload(dedup_mod)
    importlib.reload(history_mod)

    import main as main_mod
    monkeypatch.setattr(main_mod, "get_active_chat_ids", lambda strict=False: chat_ids)
    monkeypatch.setattr(main_mod, "_alert_admin", lambda *a, **k: None)
    monkeypatch.setitem(
        __import__("sys").modules, "scrapers",
        MagicMock(run_all_scrapers=lambda: deals),
    )

    with patch("notifier.send_deals", return_value=(delivered, len(chat_ids))):
        main_mod.main([])

    return json.loads(store.read_text()) if store.exists() else {"hashes": {}}


REAL_DEAL = {
    "title": "Cursor Pro 50% off first month",
    "url": "https://example.com/cursor-deal",
    "body": "",
    "upvotes": 0,
    "source": "Reddit",
}


def test_failed_delivery_does_not_burn_deals(tmp_path, monkeypatch):
    """0 delivered => nothing marked seen => retried next run."""
    store = _run_pipeline([999], [REAL_DEAL], delivered=0,
                          tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert store.get("hashes") == {}, (
        "deals were marked seen despite delivering to nobody — "
        "they are now unreachable inside the 90-day window"
    )


def test_successful_delivery_marks_deals_seen(tmp_path, monkeypatch):
    """At least one delivery => marked, so subscribers are not re-spammed."""
    store = _run_pipeline([999], [REAL_DEAL], delivered=1,
                          tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert len(store.get("hashes", {})) > 0


def test_partial_delivery_still_marks(tmp_path, monkeypatch):
    """Some recipients got it; re-sending to everyone would be worse."""
    store = _run_pipeline([1, 2, 3], [REAL_DEAL], delivered=1,
                          tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert len(store.get("hashes", {})) > 0


def test_stale_subscriber_id_scenario_end_to_end(tmp_path, monkeypatch):
    """The exact live failure: active subscriber exists but every send 400s."""
    deals = [dict(REAL_DEAL, url=f"https://x.com/{i}", title=f"Deal {i} 50% off cursor")
             for i in range(5)]
    store = _run_pipeline([2], deals, delivered=0,
                          tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert store.get("hashes") == {}, "all 5 deals must survive for retry"


# ---------------------------------------------------------------------------
# --dry-run: inspect results locally without consuming them.
# ---------------------------------------------------------------------------


def test_dry_run_flag_does_not_mark_or_send(tmp_path, monkeypatch, capsys):
    """--dry-run must leave the dedup store untouched even WITH subscribers."""
    import importlib
    import config

    store = tmp_path / "seen.json"
    monkeypatch.setattr(config, "SEEN_DEALS_PATH", str(store))
    monkeypatch.setattr(config, "HISTORY_PATH", str(tmp_path / "h.md"))

    import dedup as dedup_mod
    import history as history_mod
    importlib.reload(dedup_mod)
    importlib.reload(history_mod)
    dedup_mod.reset_cache()

    import main as main_mod
    monkeypatch.setattr(main_mod, "get_active_chat_ids", lambda strict=False: [12345])
    monkeypatch.setattr(main_mod, "_alert_admin", lambda *a, **k: None)
    monkeypatch.setitem(
        __import__("sys").modules, "scrapers",
        MagicMock(run_all_scrapers=lambda: [REAL_DEAL]),
    )

    with patch("notifier.send_deals") as send:
        main_mod.main(["--dry-run"])
        send.assert_not_called()

    data = json.loads(store.read_text()) if store.exists() else {"hashes": {}}
    assert data.get("hashes") == {}, "--dry-run consumed deals"
    assert "Cursor Pro" in capsys.readouterr().out


def test_dry_run_limit_truncates_output(capsys):
    import main as main_mod
    deals = [dict(REAL_DEAL, title=f"Deal {i} 50% off") for i in range(10)]
    main_mod._print_deals(deals, limit=3)
    out = capsys.readouterr().out
    assert "Deal 0" in out and "Deal 2" in out
    assert "Deal 5" not in out
    assert "of 10" in out


def test_normal_run_still_sends(tmp_path, monkeypatch):
    """No flag => unchanged behaviour."""
    store = _run_pipeline([999], [REAL_DEAL], delivered=1,
                          tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert len(store.get("hashes", {})) > 0
