"""Regression tests for filter de-overfitting (Grok fix pass).

Protects: multi-word inflection, negative-keyword recall, question titles,
ambiguous tool tokens, and dead-gate removal.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import filter as flt  # noqa: E402
from config import MIN_UPVOTES, NO_INFLECTION  # noqa: E402


# ── Priority 1: inflection ───────────────────────────────────────────


def test_on_us_does_not_match_on_using():
    """Phrase inflection must not attach 'ing' → 'on using' tutorials."""
    assert not flt._match("a guide on using chatgpt", "on us")
    assert not flt.is_relevant("A guide on using ChatGPT effectively at work", "")
    assert not flt.is_relevant("Thoughts on using Claude for research", "")


def test_single_word_inflection_still_works():
    assert flt._match("big deals on cursor pro", "deal")
    assert flt._match("perplexity offering a free month", "offer")
    assert flt._match("cursor discounts for students", "discount")


def test_multi_word_plural_still_matches_ma_veto():
    """'cuts ai deal' + plural must still catch 'cuts AI deals'."""
    assert flt._match("github cuts ai deals with google", "cuts ai deal")
    assert not flt.is_relevant(
        "GitHub cuts AI deals with Google, Anthropic", ""
    )


def test_save_does_not_match_saved():
    assert "save" in NO_INFLECTION
    assert not flt._match("saved my life with claude", "save")
    assert flt._match("writesonic - save 30%", "save")


def test_free_trials_matches_free_trial_plural():
    """Multi-word may take s/es so 'free trials' still counts as deal intent."""
    assert flt._match("free trials of claude pro", "free trial")


# ── Priority 2: negatives / questions / tools / gates ────────────────


def test_partnership_promo_not_vetoed():
    assert flt.is_relevant(
        "Anthropic partners with Coursera — free Claude Pro for students", ""
    )


def test_broad_negatives_removed_do_not_fire():
    for title in [
        "OpenAI signs free ChatGPT Plus deal for university students",
        "Claude Pro: 50% off backed by student ID verification",
        "Cursor lifetime deal — no commission, direct from vendor",
        "Anthropic billion-token free credits for startups",
    ]:
        assert flt.is_relevant(title, ""), f"should pass after de-overfit: {title!r}"


def test_ma_multiword_negatives_still_veto():
    for title in [
        "GitHub cuts AI deals with Google, Anthropic",
        "Anthropic chief back in talks with Pentagon about AI deal",
        "FTC Opens Antitrust Probe of Microsoft AI Deal",
    ]:
        assert not flt.is_relevant(title, ""), f"should still veto: {title!r}"


def test_question_title_with_strong_price_signal_passes():
    assert flt.is_relevant(
        "Claude Pro free for students? 50% off through Sept", ""
    )


def test_question_title_without_strong_signal_rejected():
    assert not flt.is_relevant("Is Perplexity Dead?", "")
    assert not flt.is_relevant("Wait, Claude is free for students now?", "")


def test_ambiguous_tool_tokens_do_not_pass_alone():
    assert not flt.is_relevant(
        "Scientific consensus free trial research tools", ""
    )
    assert not flt.is_relevant(
        "Tome of spells free for students lifetime deal", ""
    )
    assert not flt.is_relevant(
        "50% off Otter swimming lessons free trial", ""
    )


def test_branded_otter_ai_still_passes():
    assert flt.is_relevant("Otter Ai - 20% OFF", "")


def test_min_upvotes_zero_disables_gate_explicitly():
    """MIN_UPVOTES=0 must skip the gate (not use a dead comparison)."""
    assert MIN_UPVOTES == 0
    assert flt.is_relevant("claude deal announcement", upvotes=0) is True
    assert flt.is_relevant("claude deal announcement", upvotes=1) is True


def test_min_upvotes_when_enabled_applies_to_zero_upvotes(monkeypatch):
    monkeypatch.setattr(flt, "MIN_UPVOTES", 10)
    assert flt.is_relevant("chatgpt promo deal", upvotes=0) is False
    assert flt.is_relevant("chatgpt promo deal", upvotes=9) is False
    assert flt.is_relevant("chatgpt promo deal", upvotes=10) is True


def test_no_score_gate_after_deal_and_tool():
    """MIN_SCORE is not a gate; deal+tool keyword gates are sufficient."""
    # Would have score 20; must pass without consulting MIN_SCORE.
    assert flt.is_relevant("claude deal", "") is True


# ── Precision pass: news / support / bare free-plan FPs ──────────────


def test_support_and_complaint_threads_vetoed():
    """Support titles that mention plan/coupon names are not deals."""
    for title in [
        "Why did I not get the first-month off coupon? – ElevenLabs",
        "GitHub Copilot free plan stopped working – rate limit exceeded",
        "I have chatgpt 5X pro plan, but no pro model",
    ]:
        assert not flt.is_relevant(title, ""), f"should veto support: {title!r}"


def test_corporate_and_product_release_not_deals():
    """Infra 'deal' and free product launches are not consumer promos."""
    for title in [
        "Higher usage limits for Claude and a compute deal with SpaceX",
        "Perplexity wants to get discounted AI products into the US government too",
        "Perplexity releases Comet browser for free on Windows and macOS",
        "Emacs extension for free Copilot-like AI autocomplete",
        "Free plan details – Runway",
        "Exclusive AI Tool Deals",
    ]:
        assert not flt.is_relevant(title, ""), f"should veto non-deal: {title!r}"


def test_past_tense_price_cut_news_vetoed_present_tense_kept():
    """News 'made its discount permanent' out; promo 'Make Permanent' stays."""
    assert not flt.is_relevant(
        "DeepSeek made its 75% discount permanent. The AI price war continues", ""
    )
    assert not flt.is_relevant(
        "DeepSeek's new model is 75% off right now, here's how to get it", ""
    )
    assert flt.is_relevant(
        "DeepSeek to Make Permanent 75% Discount on Flagship AI Model", ""
    )


def test_bare_free_plan_pricing_page_rejected():
    """'{Tool} Free Plan' alone is a tier page, not a redeemable promo."""
    assert not flt.is_relevant("GitHub Copilot Free Plan", "")
    # Real promos that mention free plan keep a price signal.
    assert flt.is_relevant("GitHub Copilot free plan — 50% off first month", "")


def test_real_deals_not_regressed_by_precision_pass():
    """True positives from the eval set must still pass after the FP cut."""
    for title in [
        "Cursor (AI code editor) - 50% off your first month, any tier",
        "1-year perplexity pro free to all Airtel users in India",
        "GitHub Copilot is free until August 22",
        "DeepSeek to Make Permanent 75% Discount on Flagship AI Model",
        "Show HN: WildfireDeals – Daily AI Tool Deals (50-90% Off)",
        "Perplexity Ai - FREE 1-YEAR PRO PLAN",
        "ElevenLabs — AI Student Pack",
        "Anthropic partners with Coursera — free Claude Pro for students",
    ]:
        assert flt.is_relevant(title, ""), f"should still pass: {title!r}"


# ── News-domain gate (P1) ────────────────────────────────────────────


def test_news_domain_without_strong_signal_rejected():
    """General press 'AI deal' coverage (M&A/funding/policy) is not a promo."""
    cases = [
        ("GitHub cuts AI deals with Google, Anthropic", "https://www.bloomberg.com/news/x"),
        ("Reddit has a new AI training deal to sell user content", "https://www.theverge.com/x"),
        ("Silicon Valley's AI deals are creating zombie startups", "https://www.cnbc.com/x"),
        ("Microsoft 365 confirms new premium tier, stuffed with AI and few discounts",
         "https://www.theregister.com/x"),
    ]
    for title, url in cases:
        assert not flt.is_relevant(title, "", 0, url), f"should veto news FP: {title!r}"


def test_news_domain_with_strong_signal_still_passes():
    """A real promo reported by press outlets must still get through."""
    assert flt.is_relevant(
        "DeepSeek to Make Permanent 75% Discount on Flagship AI Model",
        "", 0, "https://www.bloomberg.com/news/articles/deepseek-discount",
    )
    assert flt.is_relevant(
        "Amazon offers free credits for startups to use AI models including Anthropic",
        "", 0, "https://www.reuters.com/technology/amazon-credits",
    )


def test_news_domain_gate_ignores_non_news_hosts():
    """Same weak-signal title from a non-news host is unaffected by this gate."""
    assert flt.is_relevant(
        "Cursor (AI code editor) - 50% off your first month, any tier",
        "", 0, "https://cursor.com/blog/deal",
    )
    # No URL at all (most scrapers don't always populate one) must not
    # accidentally trip the news-domain gate.
    assert flt.is_relevant("Cursor (AI code editor) - 50% off your first month, any tier", "")


# ── URL tool-name evidence (recall fix) ──────────────────────────────


def test_url_tool_evidence_with_strong_signal_recovers_recall_fns():
    """Vendor pages that name the tool only in the domain, not the title."""
    cases = [
        ("Student and Educator Discounts",
         "https://help.runwayml.com/hc/en-us/articles/x-Student-and-Educator-Discounts"),
        ("25% off for students and educators", "https://runwayml.com/educators"),
        ("Start a Free Trial", "https://www.grammarly.com/upgrade/business/try"),
    ]
    for title, url in cases:
        assert flt.is_relevant(title, "", 0, url), f"should recover via URL evidence: {title!r}"


def test_url_tool_evidence_without_strong_signal_still_rejected():
    """Weak DEAL_KEYWORDS ('offer', 'trial', 'credits') must not combine with
    URL-only tool evidence — these are generic pricing/help pages on hosts
    that also sell real deals, not deals themselves."""
    cases = [
        ("Codex now offers more flexible pricing for teams", "https://openai.com/index/codex-pricing"),
        ("The subscription trial", "https://help.udio.com/hc/en-us/articles/x"),
        ("Credits and credit limits", "https://help.udio.com/hc/en-us/articles/y"),
    ]
    for title, url in cases:
        assert not flt.is_relevant(title, "", 0, url), f"should stay rejected: {title!r}"


def test_url_tool_evidence_does_not_replace_deal_keyword_gate():
    """A tool-naming URL alone, with no deal signal anywhere, must not pass."""
    assert not flt.is_relevant(
        "Your connected workspace for wiki, docs & projects | Notion",
        "", 0, "https://www.notion.so/startups",
    )
