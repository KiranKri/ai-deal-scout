import pytest
from unittest.mock import patch, MagicMock
import bot.bot_server as bot_server


@pytest.fixture
def client():
    bot_server.app.config["TESTING"] = True
    with bot_server.app.test_client() as c:
        yield c

def _update(text, chat_id=111111, username="testuser"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": chat_id, "username": username},
            "chat": {"id": chat_id, "username": username},
            "text": text
        }
    }

def _h(secret="testsecret"):
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


# ── /health ─────────────────────────────────────────────────────

def test_health_returns_200_and_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json["status"] == "ok"


# ── Webhook security ────────────────────────────────────────────

def test_missing_secret_header_returns_403(client):
    assert client.post("/webhook", json={}).status_code == 403

def test_wrong_secret_returns_403(client):
    r = client.post("/webhook", json={},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert r.status_code == 403

def test_correct_secret_returns_200(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server._send", return_value=True):
        r = client.post("/webhook",
                        json=_update("/help"), headers=_h())
        assert r.status_code == 200


# ── /start ──────────────────────────────────────────────────────

def test_start_new_user_sends_welcome(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.subscribers.add_subscriber",
               return_value="new"), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook", json=_update("/start"), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "subscribed" in sent.lower()
        assert "github.com" in sent.lower()

def test_start_already_active_sends_already_subscribed(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.subscribers.add_subscriber",
               return_value="already_active"), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook", json=_update("/start"), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "already" in sent.lower()

def test_start_inactive_user_resubscribes_sends_welcome(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.subscribers.add_subscriber",
               return_value="resubscribed"), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook", json=_update("/start"), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "subscribed" in sent.lower()

def test_start_github_failure_sends_error_not_success(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.subscribers.add_subscriber",
               return_value="error"), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook", json=_update("/start"), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "failed" in sent.lower() or "error" in sent.lower()


# ── /stop ───────────────────────────────────────────────────────

def test_stop_active_user_sends_unsubscribed(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.subscribers.deactivate_subscriber",
               return_value="deactivated"), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook", json=_update("/stop"), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "unsubscribed" in sent.lower()

def test_stop_already_inactive_sends_not_subscribed(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.subscribers.deactivate_subscriber",
               return_value="already_inactive"), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook", json=_update("/stop"), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "not" in sent.lower()

def test_stop_unknown_user_sends_not_subscribed(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.subscribers.deactivate_subscriber",
               return_value="not_found"), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook", json=_update("/stop"), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "not" in sent.lower()


# ── /run ────────────────────────────────────────────────────────

def test_run_admin_triggers_workflow(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.ADMIN_CHAT_ID", 111111), \
         patch("bot.bot_server.GH_REPO_DATA", "KiranKri/ai-deal-scout-data"), \
         patch("bot.bot_server.requests.post",
               return_value=MagicMock(status_code=204)), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook",
                    json=_update("/run", chat_id=111111), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "triggered" in sent.lower()

def test_run_non_admin_sends_unauthorized(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.ADMIN_CHAT_ID", 999999), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook",
                    json=_update("/run", chat_id=111111), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "unauthorized" in sent.lower()


# ── /status ─────────────────────────────────────────────────────

def test_status_admin_shows_counts(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.ADMIN_CHAT_ID", 111111), \
         patch("bot.bot_server.subscribers.get_subscriber_count",
               return_value={"total": 5, "active": 4, "inactive": 1}), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook",
                    json=_update("/status", chat_id=111111), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "5" in sent and "4" in sent

def test_status_non_admin_sends_unauthorized(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.ADMIN_CHAT_ID", 999999), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook",
                    json=_update("/status", chat_id=111111), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "unauthorized" in sent.lower()


# ── Edge cases ───────────────────────────────────────────────────

def test_username_none_in_payload_no_crash(client):
    update = _update("/start")
    update["message"]["chat"].pop("username", None)
    update["message"]["from"].pop("username", None)
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server.subscribers.add_subscriber",
               return_value="new"), \
         patch("bot.bot_server._send", return_value=True):
        r = client.post("/webhook", json=update, headers=_h())
        assert r.status_code == 200

def test_malformed_json_returns_200(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"):
        r = client.post("/webhook", data="not json",
                        content_type="application/json",
                        headers=_h())
        assert r.status_code == 200

def test_missing_message_key_returns_200(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"):
        r = client.post("/webhook",
                        json={"update_id": 1}, headers=_h())
        assert r.status_code == 200

def test_unknown_command_sends_help_hint(client):
    with patch("bot.bot_server.WEBHOOK_SECRET", "testsecret"), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post("/webhook",
                    json=_update("/blah"), headers=_h())
        sent = mock_send.call_args[0][1]
        assert "help" in sent.lower()
