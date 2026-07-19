import pytest
import logging
from unittest.mock import patch, MagicMock, call
import src.notifier as notifier

FAKE_DEAL = {
    "title": "50% off Cursor Pro",
    "url": "https://example.com",
    "source": "Reddit",
    "upvotes": 42,
    "body": "Limited time offer"
}

def _mock_resp(status):
    r = MagicMock()
    r.status_code = status
    return r


# ── Zero / empty inputs ─────────────────────────────────────────

def test_zero_chat_ids_logs_warning_and_skips(caplog):
    with caplog.at_level(logging.WARNING), \
         patch("src.notifier.requests.post") as mock_post:
        notifier.send_deals([FAKE_DEAL], [])
        mock_post.assert_not_called()
        assert any("subscriber" in m.lower() or "skip" in m.lower()
                   for m in caplog.messages)

def test_empty_deals_sends_no_new_message_to_all_users():
    chat_ids = [1, 2, 3]
    sent_to = []
    def capture(url, **kwargs):
        sent_to.append(kwargs.get("json", {}).get("chat_id"))
        return _mock_resp(200)
    with patch("src.notifier.requests.post", side_effect=capture):
        notifier.send_deals([], chat_ids)
    for cid in chat_ids:
        assert cid in sent_to


# ── Batching ────────────────────────────────────────────────────

def test_30_users_produces_batch_sleep():
    chat_ids = list(range(30))
    batch_sleeps = []
    def fake_sleep(n):
        if n >= 1.0:
            batch_sleeps.append(n)
    with patch("src.notifier.requests.post",
               return_value=_mock_resp(200)), \
         patch("src.notifier.time.sleep", side_effect=fake_sleep):
        notifier.send_deals([FAKE_DEAL], chat_ids)
    assert len(batch_sleeps) >= 1

def test_25_users_no_batch_sleep():
    """Exactly 1 batch — no inter-batch sleep needed.

    Sleeps are identified by comparing against the constants rather than by
    magnitude: MESSAGE_SLEEP and BATCH_SLEEP are independently tunable and a
    magnitude heuristic breaks silently whenever either is changed.
    """
    chat_ids = list(range(25))
    batch_sleeps = []
    def fake_sleep(n):
        if n == notifier.BATCH_SLEEP:
            batch_sleeps.append(n)
    with patch("src.notifier.requests.post",
               return_value=_mock_resp(200)), \
         patch("src.notifier.time.sleep", side_effect=fake_sleep):
        notifier.send_deals([FAKE_DEAL], chat_ids)
    assert len(batch_sleeps) == 0


# ── MESSAGE_SLEEP ───────────────────────────────────────────────

def test_message_sleep_between_messages_to_same_user():
    """Consecutive chunks to one chat must be paced by MESSAGE_SLEEP.

    Telegram permits only ~1 message/second to the same chat, so this pacing
    is what stops a multi-chunk batch being partially rejected with 429.
    """
    msg_sleeps = []
    def fake_sleep(n):
        if n == notifier.MESSAGE_SLEEP:
            msg_sleeps.append(n)
    with patch("src.notifier.requests.post",
               return_value=_mock_resp(200)), \
         patch("src.notifier.time.sleep", side_effect=fake_sleep):
        notifier.send_deals([FAKE_DEAL], [1])
    assert len(msg_sleeps) >= 1


# ── 403 handling ────────────────────────────────────────────────

def test_single_403_triggers_batch_deactivate_with_that_id():
    with patch("src.notifier.requests.post",
               return_value=_mock_resp(403)), \
         patch("src.notifier.subscribers_module.batch_deactivate") as mock_bd:
        notifier.send_deals([FAKE_DEAL], [123])
        mock_bd.assert_called_once_with([123])

def test_multiple_403s_batch_deactivate_called_once_with_all():
    """10 blocked users → batch_deactivate called ONCE with all 10."""
    chat_ids = list(range(10))
    with patch("src.notifier.requests.post",
               return_value=_mock_resp(403)), \
         patch("src.notifier.subscribers_module.batch_deactivate") as mock_bd:
        notifier.send_deals([FAKE_DEAL], chat_ids)
        mock_bd.assert_called_once()
        blocked = mock_bd.call_args[0][0]
        assert set(blocked) == set(chat_ids)

def test_no_403s_batch_deactivate_not_called():
    with patch("src.notifier.requests.post",
               return_value=_mock_resp(200)), \
         patch("src.notifier.subscribers_module.batch_deactivate") as mock_bd:
        notifier.send_deals([FAKE_DEAL], [1, 2, 3])
        mock_bd.assert_not_called()


# ── 429 handling ────────────────────────────────────────────────

def test_429_retries_once_then_succeeds():
    responses = [_mock_resp(429), _mock_resp(200)]
    with patch("src.notifier.requests.post",
               side_effect=responses) as mock_post, \
         patch("src.notifier.time.sleep"):
        notifier.send_deals([FAKE_DEAL], [1])
        assert mock_post.call_count == 2

def test_429_after_retry_logs_and_continues():
    """Still 429 after retry → skip user, continue to next."""
    responses = [_mock_resp(429), _mock_resp(429),
                 _mock_resp(200)]
    with patch("src.notifier.requests.post",
               side_effect=responses), \
         patch("src.notifier.time.sleep"):
        # [1] gets double-429, [2] gets 200
        notifier.send_deals([FAKE_DEAL], [1, 2])


# ── Resilience ───────────────────────────────────────────────────

def test_one_failed_user_does_not_stop_broadcast():
    def side_effect(url, **kwargs):
        cid = kwargs.get("json", {}).get("chat_id")
        return _mock_resp(500 if cid == 2 else 200)
    with patch("src.notifier.requests.post",
               side_effect=side_effect):
        notifier.send_deals([FAKE_DEAL], [1, 2, 3])


# ── Broadcast summary log ────────────────────────────────────────

def test_broadcast_summary_logged(caplog):
    with patch("src.notifier.requests.post",
               return_value=_mock_resp(200)), \
         caplog.at_level(logging.INFO):
        notifier.send_deals([FAKE_DEAL], [1, 2, 3])
    assert any("broadcast" in m.lower() or "complete" in m.lower()
               for m in caplog.messages)


# ---------------------------------------------------------------------------
# Partial delivery.  Observed live: 91 deals -> 6 chunks; the first landed and
# a later one was rejected, yet the run reported "delivered to 0/1" and left
# every deal unmarked, so the whole batch would have re-sent on the next run.
# ---------------------------------------------------------------------------


def test_partial_delivery_counts_as_delivered():
    """One chunk landing means the user got something — not a total failure."""
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        # First chunk succeeds, everything after is rate limited.
        return _mock_resp(200 if calls["n"] == 1 else 429)

    many = [dict(FAKE_DEAL, url=f"https://x.com/{i}", title=f"Deal {i} 50% off")
            for i in range(60)]
    with patch("src.notifier.requests.post", side_effect=flaky), \
         patch("src.notifier.time.sleep"):
        delivered, total = notifier.send_deals(many, [999])

    assert total == 1
    assert delivered == 1, (
        "a user who received part of the batch must count as delivered, "
        "otherwise main.py never marks the deals seen and re-sends forever"
    )


def test_total_failure_still_reports_zero():
    """If nothing at all landed, the run must still report 0 delivered."""
    with patch("src.notifier.requests.post", return_value=_mock_resp(429)), \
         patch("src.notifier.time.sleep"):
        delivered, total = notifier.send_deals([FAKE_DEAL], [999])
    assert (delivered, total) == (0, 1)


def test_message_sleep_clears_telegram_per_chat_limit():
    """Pacing must be >= 1s: Telegram allows ~1 msg/sec to the same chat."""
    assert notifier.MESSAGE_SLEEP >= 1.0, (
        f"MESSAGE_SLEEP={notifier.MESSAGE_SLEEP} sends multiple chunks to one "
        f"chat faster than Telegram accepts, causing partial delivery"
    )


def test_blocked_user_is_not_counted_as_delivered():
    """403 mid-batch: user blocked the bot, nothing more should count."""
    with patch("src.notifier.requests.post", return_value=_mock_resp(403)), \
         patch("src.notifier.time.sleep"), \
         patch("src.notifier.subscribers_module.batch_deactivate"):
        delivered, total = notifier.send_deals([FAKE_DEAL], [999])
    assert delivered == 0
