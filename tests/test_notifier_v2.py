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
    """Exactly 1 batch — no inter-batch sleep needed."""
    chat_ids = list(range(25))
    batch_sleeps = []
    def fake_sleep(n):
        if n >= 1.0:
            batch_sleeps.append(n)
    with patch("src.notifier.requests.post",
               return_value=_mock_resp(200)), \
         patch("src.notifier.time.sleep", side_effect=fake_sleep):
        notifier.send_deals([FAKE_DEAL], chat_ids)
    assert len(batch_sleeps) == 0


# ── MESSAGE_SLEEP ───────────────────────────────────────────────

def test_message_sleep_between_messages_to_same_user():
    """0.3s sleep between header + chunk sent to same chat_id."""
    msg_sleeps = []
    def fake_sleep(n):
        if 0.1 <= n <= 0.9:
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
