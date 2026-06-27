import json
import base64
import logging
import pytest
from unittest.mock import patch, MagicMock
import bot.subscribers as subscribers


def _encode(data: dict) -> str:
    return base64.b64encode(json.dumps(data).encode()).decode()

def _mock_get(status=200, data=None, sha="abc123"):
    r = MagicMock()
    r.status_code = status
    if data is not None:
        r.json.return_value = {"content": _encode(data), "sha": sha}
    else:
        r.json.return_value = {}
    return r

def _mock_put(status=200):
    r = MagicMock()
    r.status_code = status
    return r

def _empty_store():
    return {"subscribers": [], "last_updated": ""}

@pytest.fixture(autouse=True)
def reset_state():
    subscribers._last_sha = None
    yield


# ── get_subscribers ─────────────────────────────────────────────

def test_get_subscribers_returns_list():
    data = {"subscribers": [{"chat_id": 123, "active": True}],
            "last_updated": ""}
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, data)):
        result = subscribers.get_subscribers()
        assert len(result) == 1
        assert result[0]["chat_id"] == 123

def test_get_subscribers_404_returns_empty():
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(404)):
        assert subscribers.get_subscribers() == []

def test_get_subscribers_500_returns_empty_and_logs_critical(caplog):
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(500)), \
         caplog.at_level(logging.CRITICAL):
        result = subscribers.get_subscribers()
        assert result == []
        assert any(r.levelno >= logging.CRITICAL
                   for r in caplog.records)


# ── get_active_chat_ids ─────────────────────────────────────────

def test_get_active_chat_ids_filters_correctly():
    data = {"subscribers": [
        {"chat_id": 1, "active": True},
        {"chat_id": 2, "active": False},
        {"chat_id": 3, "active": True}
    ], "last_updated": ""}
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, data)):
        assert subscribers.get_active_chat_ids() == [1, 3]


# ── add_subscriber ──────────────────────────────────────────────

def test_add_subscriber_new_user_returns_new():
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(404)), \
         patch("bot.subscribers.requests.put",
               return_value=_mock_put(201)) as mock_put:
        result = subscribers.add_subscriber(999, "newuser")
        assert result == "new"
        assert mock_put.call_count == 1

def test_add_subscriber_already_active_no_write():
    data = {"subscribers": [
        {"chat_id": 123, "active": True, "resubscribe_count": 0}
    ], "last_updated": ""}
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, data)), \
         patch("bot.subscribers.requests.put") as mock_put:
        result = subscribers.add_subscriber(123, "user")
        assert result == "already_active"
        mock_put.assert_not_called()

def test_add_subscriber_inactive_resubscribes_and_increments():
    data = {"subscribers": [
        {"chat_id": 123, "active": False, "resubscribe_count": 1,
         "subscribed_at": "2026-01-01T00:00:00+05:30",
         "username": "user"}
    ], "last_updated": ""}
    written = {}
    def capture_put(url, **kwargs):
        body = kwargs.get("json", {})
        content = body.get("content", "")
        written.update(json.loads(base64.b64decode(content)))
        return _mock_put(200)
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, data)), \
         patch("bot.subscribers.requests.put",
               side_effect=capture_put):
        result = subscribers.add_subscriber(123, "user")
        assert result == "resubscribed"
        sub = next(s for s in written["subscribers"]
                   if s["chat_id"] == 123)
        assert sub["active"] is True
        assert sub["resubscribe_count"] == 2

def test_add_subscriber_username_none_stored_as_null():
    written = {}
    def capture_put(url, **kwargs):
        body = kwargs.get("json", {})
        content = body.get("content", "")
        written.update(json.loads(base64.b64decode(content)))
        return _mock_put(201)
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(404)), \
         patch("bot.subscribers.requests.put",
               side_effect=capture_put):
        subscribers.add_subscriber(555, None)
        sub = next(s for s in written["subscribers"]
                   if s["chat_id"] == 555)
        assert sub["username"] is None

def test_add_subscriber_github_failure_returns_error():
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(500)):
        assert subscribers.add_subscriber(123, "user") == "error"


# ── deactivate_subscriber ───────────────────────────────────────

def test_deactivate_subscriber_success():
    data = {"subscribers": [
        {"chat_id": 123, "active": True}
    ], "last_updated": ""}
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, data)), \
         patch("bot.subscribers.requests.put",
               return_value=_mock_put(200)):
        assert subscribers.deactivate_subscriber(123) == "deactivated"

def test_deactivate_already_inactive_no_write():
    data = {"subscribers": [
        {"chat_id": 123, "active": False}
    ], "last_updated": ""}
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, data)), \
         patch("bot.subscribers.requests.put") as mock_put:
        assert subscribers.deactivate_subscriber(123) == "already_inactive"
        mock_put.assert_not_called()

def test_deactivate_unknown_chat_id_returns_not_found():
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, _empty_store())):
        assert subscribers.deactivate_subscriber(999) == "not_found"


# ── get_subscriber_count ────────────────────────────────────────

def test_get_subscriber_count_correct_totals():
    data = {"subscribers": [
        {"active": True}, {"active": True}, {"active": False}
    ], "last_updated": ""}
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, data)):
        assert subscribers.get_subscriber_count() == {
            "total": 3, "active": 2, "inactive": 1}

def test_get_subscriber_count_on_api_failure_returns_zeros():
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(500)):
        assert subscribers.get_subscriber_count() == {
            "total": 0, "active": 0, "inactive": 0}


# ── _put_file exponential backoff ───────────────────────────────

def test_put_file_retries_with_exponential_backoff():
    # Fails 3 times, succeeds on 4th
    put_responses = [_mock_put(409), _mock_put(409),
                     _mock_put(409), _mock_put(200)]
    get_responses = [_mock_get(200, _empty_store())] * 5
    sleep_calls = []
    with patch("bot.subscribers.requests.get",
               side_effect=get_responses), \
         patch("bot.subscribers.requests.put",
               side_effect=put_responses) as mock_put, \
         patch("bot.subscribers.time.sleep",
               side_effect=lambda n: sleep_calls.append(n)):
        result = subscribers._put_file(_empty_store(), "sha1")
        assert result is True
        assert mock_put.call_count == 4
        # Delays must be strictly increasing (exponential)
        assert sleep_calls == sorted(sleep_calls)
        assert len(sleep_calls) == 3

def test_put_file_all_retries_exhausted_returns_false():
    put_responses = [_mock_put(409)] * 5
    get_responses = [_mock_get(200, _empty_store())] * 5
    with patch("bot.subscribers.requests.get",
               side_effect=get_responses), \
         patch("bot.subscribers.requests.put",
               side_effect=put_responses), \
         patch("bot.subscribers.time.sleep"):
        result = subscribers._put_file(_empty_store(), "sha1")
        assert result is False


# ── batch_deactivate ────────────────────────────────────────────

def test_batch_deactivate_single_commit_for_10_ids():
    data = {"subscribers": [
        {"chat_id": i, "active": True} for i in range(10)
    ], "last_updated": ""}
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, data)), \
         patch("bot.subscribers.requests.put",
               return_value=_mock_put(200)) as mock_put:
        result = subscribers.batch_deactivate(list(range(10)))
        assert result is True
        assert mock_put.call_count == 1

def test_batch_deactivate_empty_list_returns_true():
    assert subscribers.batch_deactivate([]) is True

def test_batch_deactivate_only_deactivates_matching_ids():
    data = {"subscribers": [
        {"chat_id": 1, "active": True},
        {"chat_id": 2, "active": True},
        {"chat_id": 3, "active": True}
    ], "last_updated": ""}
    written = {}
    def capture_put(url, **kwargs):
        body = kwargs.get("json", {})
        content = body.get("content", "")
        written.update(json.loads(base64.b64decode(content)))
        return _mock_put(200)
    with patch("bot.subscribers.requests.get",
               return_value=_mock_get(200, data)), \
         patch("bot.subscribers.requests.put",
               side_effect=capture_put):
        subscribers.batch_deactivate([1, 3])
        active = [s for s in written["subscribers"] if s["active"]]
        assert len(active) == 1
        assert active[0]["chat_id"] == 2
