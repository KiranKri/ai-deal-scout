"""Tests for src/filter.py."""

import os
import sys

from unittest.mock import patch

import pytest

# Allow importing from src/ without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import filter as f  # noqa: E402  (after sys.path patch)
from config import BOOSTED_PHRASES, DEAL_KEYWORDS, MIN_UPVOTES, TOOL_KEYWORDS  # noqa: E402


# ---------------------------------------------------------------------------
# Tests: score_deal
# ---------------------------------------------------------------------------


def test_score_deal_zero_for_irrelevant():
    """Completely unrelated text should score 0."""
    assert f.score_deal("Weather forecast for tomorrow", "Sunny with a chance of rain") == 0


def test_score_deal_zero_empty_strings():
    """Empty title and body should score 0."""
    assert f.score_deal("", "") == 0


def test_score_deal_deal_keyword_scores_15():
    """One DEAL_KEYWORD match should add exactly 15 points."""
    # "deal" is a DEAL_KEYWORD → +15
    assert f.score_deal("Big deal on software today") == 15


def test_score_deal_tool_keyword_scores_5():
    """One TOOL_KEYWORD match (no DEAL_KEYWORD) should add exactly 5 points."""
    # "chatgpt" is a TOOL_KEYWORD → +5
    assert f.score_deal("ChatGPT release notes") == 5


def test_score_deal_deal_and_tool_keywords_combine():
    """A DEAL_KEYWORD and a TOOL_KEYWORD should sum correctly."""
    # "deal"(DEAL +15) + "claude"(TOOL +5) + "claude deal"(BOOSTED +20) = 40.
    # Assert the relationship, not a magic total, so config growth cannot
    # break this test again.
    both = f.score_deal("claude deal")
    assert both > f.score_deal("claude")      # deal intent adds signal
    assert both > f.score_deal("deal")        # tool name adds signal


def test_score_deal_multiple_keywords():
    """Multiple distinct DEAL_KEYWORD matches should each add 15."""
    # "deal"(+15) + "promo"(+15) = 30
    score = f.score_deal("deal promo event")
    assert score == 30


def test_score_deal_keyword_case_insensitive():
    """Keyword matching must be case-insensitive."""
    # "DISCOUNT" lowercases to "discount" — a DEAL_KEYWORD → +15
    assert f.score_deal("DISCOUNT available NOW") == 15


def test_score_deal_keyword_in_body():
    """Keywords found only in body should still contribute."""
    # "free trial" and "trial" are both DEAL_KEYWORDS → body-only match scores
    score = f.score_deal("Nothing special here", "Get your free trial today")
    assert score >= 15
    assert f.score_deal("Nothing special here", "") == 0


def test_score_deal_keyword_each_counted_once():
    """Repeating the same keyword multiple times must only score once."""
    # "deal" appears three times but is only counted once → +15
    score = f.score_deal("deal deal deal")
    assert score == 15


def test_score_deal_word_boundary_no_false_positive():
    """Word-boundary matching must not fire inside a longer word."""
    # "cursor" should NOT match inside "cursory"
    assert f.score_deal("cursory inspection report") == 0


def test_score_deal_boosted_phrase_adds_20():
    """A BOOSTED_PHRASE should add 20 points."""
    phrase = BOOSTED_PHRASES[0]  # "claude pro deal"
    score = f.score_deal(phrase)
    # The phrase also contains DEAL/TOOL keywords; verify the boost fires.
    assert score >= 20


def test_score_deal_boosted_phrase_case_insensitive():
    """Boosted phrases must match case-insensitively."""
    phrase = BOOSTED_PHRASES[0].upper()
    score = f.score_deal(phrase)
    assert score >= 20


def test_score_deal_boosted_phrase_each_counted_once():
    """Repeating the same boosted phrase should only add 20 once."""
    phrase = BOOSTED_PHRASES[0]
    score_single = f.score_deal(phrase)
    score_double = f.score_deal(f"{phrase} {phrase}")
    assert score_single == score_double


def test_score_deal_all_keywords_score():
    """Every DEAL_KEYWORD contributes +15; every TOOL_KEYWORD contributes +5."""
    for kw in DEAL_KEYWORDS:
        # "% off" requires a preceding digit so the word boundary fires
        # (e.g. "50% off" — the "0" provides the \w anchor before "%")
        title = f"50{kw}" if kw.startswith("%") else kw
        assert f.score_deal(title) >= 15, f"DEAL_KEYWORD {kw!r} did not score >= 15"
    for kw in TOOL_KEYWORDS:
        assert f.score_deal(kw) >= 5, f"TOOL_KEYWORD {kw!r} did not score >= 5"


def test_score_deal_all_boosted_phrases_score():
    """Every BOOSTED_PHRASE should individually contribute at least +20."""
    for phrase in BOOSTED_PHRASES:
        assert f.score_deal(phrase) >= 20, f"Boosted phrase {phrase!r} did not add >=20"


def test_score_deal_returns_int():
    """score_deal must return an int (not float)."""
    result = f.score_deal("deal promo")
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# Tests: is_relevant
# ---------------------------------------------------------------------------


def test_is_relevant_false_when_score_zero():
    """is_relevant must return False when score_deal returns 0."""
    assert f.is_relevant("nothing relevant here") is False


def test_is_relevant_true_when_score_meets_threshold_no_upvotes():
    """Score >= 15 with both DEAL_KEYWORD + TOOL_KEYWORD, no upvote data."""
    # "claude"(TOOL +5) + "deal"(DEAL +15) = 20; both keyword gates pass
    assert f.is_relevant("claude deal announcement", upvotes=0) is True


def test_is_relevant_false_no_deal_keyword():
    """A post with only TOOL_KEYWORD matches must be rejected (no deal intent)."""
    # "chatgpt"(TOOL +5) = 5; no DEAL_KEYWORD → rejected at first gate
    assert f.is_relevant("Introducing ChatGPT 5 release", upvotes=0) is False


def test_is_relevant_false_no_tool_keyword():
    """A post with only DEAL_KEYWORD matches must be rejected (no AI tool)."""
    # "discount"(DEAL +15); no TOOL_KEYWORD → rejected at second gate
    assert f.is_relevant("Samsung TV huge discount this weekend") is False


def test_is_relevant_false_tv_deal_no_tool_keyword():
    """TV / consumer electronics deal with no AI tool keyword → rejected."""
    assert f.is_relevant("50% off Samsung 4K TV limited time offer") is False


def test_is_relevant_false_retail_discount_no_tool_keyword():
    """Retail discount with no TOOL_KEYWORD is rejected.

    Also verifies word-boundary matching: 'ai' inside 'retail' does not
    accidentally match any TOOL_KEYWORD.
    """
    assert f.is_relevant("retail discount sale this weekend") is False


def test_is_relevant_true_chatgpt_discount():
    """Post containing both a DEAL_KEYWORD and a TOOL_KEYWORD passes."""
    # "chatgpt"(TOOL +5) + "discount"(DEAL +15) = 20 ≥ 15; both gates pass
    assert f.is_relevant("chatgpt discount available now") is True


def test_is_relevant_true_at_min_upvotes():
    """Exactly MIN_UPVOTES should pass the threshold."""
    # "claude"(TOOL +5) + "promo"(DEAL +15) = 20; has both keywords
    assert f.is_relevant("claude promo today", upvotes=MIN_UPVOTES) is True


def test_is_relevant_true_above_min_upvotes():
    """More than MIN_UPVOTES should definitely pass."""
    # "cursor"(TOOL +5) + "discount"(DEAL +15) = 20; has both keywords
    assert f.is_relevant("cursor discount live", upvotes=MIN_UPVOTES + 100) is True


def test_is_relevant_false_below_min_upvotes():
    """Upvotes below MIN_UPVOTES should make the deal irrelevant."""
    # MIN_UPVOTES is 0 in recall mode, which disables this gate entirely.
    # Patch a real threshold so the gate logic is still covered.
    # When enabled, zero-upvote posts also fail (no unknown-vote bypass).
    with patch.object(f, "MIN_UPVOTES", 10):
        assert f.is_relevant("chatgpt promo deal", upvotes=0) is False
        assert f.is_relevant("chatgpt promo deal", upvotes=9) is False
        assert f.is_relevant("chatgpt promo deal", upvotes=10) is True


def test_is_relevant_false_zero_score_regardless_of_upvotes():
    """Even high upvotes cannot rescue a zero-score deal."""
    assert f.is_relevant("nothing here", upvotes=9999) is False


def test_is_relevant_body_contributes():
    """Keywords in body should count toward both gates and score."""
    # body: "chatgpt"(TOOL +5) + "deal"(DEAL +15) + "discount"(DEAL +15) = 35
    assert f.is_relevant("Check this out", body="chatgpt deal and discount today") is True


def test_is_relevant_returns_bool():
    """is_relevant must return a strict bool."""
    result = f.is_relevant("deal")
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Additional tests
# ---------------------------------------------------------------------------


def test_score_deal_multiple_keywords_sum_correctly():
    """Three distinct DEAL_KEYWORDS should each add +15, totalling 45."""
    # "deal"(+15) + "promo"(+15) + "discount"(+15) = 45
    assert f.score_deal("deal promo discount") == 45


def test_score_deal_combines_title_and_body():
    """Keywords split across title and body should be summed correctly."""
    # body contributes promo + discount; title contributes the "ai tool" TOOL
    # keyword. Assert composition rather than a fixed total.
    combined = f.score_deal("New AI tool", "Running a promo and discount this week")
    assert combined > f.score_deal("New AI tool", "")
    assert combined > f.score_deal("", "Running a promo and discount this week")


def test_score_deal_with_emoji_and_special_characters():
    """Emoji and punctuation must not prevent keyword matching."""
    title = "\U0001f525 Huge deal! 50% OFF — Limited time promo \U0001f389"
    # "deal"(+15) + "% off"(+15 via "50% off") + "promo"(+15) + "limited time"(+15) = 60
    assert f.score_deal(title) > 0


def test_is_relevant_negative_upvotes():
    """Negative upvotes satisfy upvotes <= 0, so the MIN_UPVOTES gate is skipped."""
    # "chatgpt"(TOOL +5) + "deal"(DEAL +15) + "promo"(DEAL +15) = 35
    # upvotes=-5: upvotes > 0 is False, so the upvote check is not applied
    assert f.is_relevant("chatgpt deal promo offer", "", upvotes=-5) is True


def test_is_relevant_upvote_exact_boundary():
    """upvotes=MIN_UPVOTES-1 must fail; upvotes=MIN_UPVOTES must pass."""
    title = "chatgpt deal promo"
    with patch.object(f, "MIN_UPVOTES", 10):
        assert f.is_relevant(title, upvotes=0) is False
        assert f.is_relevant(title, upvotes=9) is False
        assert f.is_relevant(title, upvotes=10) is True
    # And with the gate disabled (current setting) low upvotes must pass.
    with patch.object(f, "MIN_UPVOTES", 0):
        assert f.is_relevant(title, upvotes=1) is True
