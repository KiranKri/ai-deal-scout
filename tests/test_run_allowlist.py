"""Tests for /run allowlist, rate limits, and /quota (Grok fix Priority 4)."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import bot.bot_server as bot_server

_IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def client():
    bot_server.app.config["TESTING"] = True
    with bot_server.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _isolated_run_quota(tmp_path, monkeypatch):
    path = str(tmp_path / "run_quota.json")
    monkeypatch.setattr(bot_server, "RUN_QUOTA_STATE_PATH", path)
    monkeypatch.setattr(bot_server, "RUN_RATE_LIMIT_SECONDS", 3600)
    monkeypatch.setattr(bot_server, "RUN_GLOBAL_DAILY_CEILING", 10)
    yield path


def _update(text, chat_id=111111, username="tester"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": chat_id, "username": username},
            "chat": {"id": chat_id, "username": username},
            "text": text,
        },
    }


def _h(secret="testsecret"):
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


def _dispatch_ok(*a, **k):
    r = MagicMock()
    r.status_code = 204
    return r


# ── allowlist ─────────────────────────────────────────────────────


def test_allowlisted_user_can_run(client, monkeypatch):
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "testsecret")
    monkeypatch.setattr(bot_server, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot_server, "RUN_ALLOWLIST", {42})
    monkeypatch.setattr(bot_server, "GH_REPO_DATA", "owner/data")
    monkeypatch.setattr(bot_server, "GH_PAT", "token")
    with patch("bot.bot_server.requests.post", return_value=_dispatch_ok()), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post(
            "/webhook",
            json=_update("/run", chat_id=42),
            headers=_h(),
        )
        assert mock_send.called
        msg = mock_send.call_args[0][1]
        assert "triggered" in msg.lower() or "pipeline" in msg.lower()


def test_non_allowlisted_user_refused(client, monkeypatch):
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "testsecret")
    monkeypatch.setattr(bot_server, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot_server, "RUN_ALLOWLIST", {42})
    with patch("bot.bot_server._send", return_value=True) as mock_send, \
         patch("bot.bot_server.requests.post") as post:
        client.post(
            "/webhook",
            json=_update("/run", chat_id=777),
            headers=_h(),
        )
        assert "unauthorized" in mock_send.call_args[0][1].lower()
        post.assert_not_called()


def test_admin_can_run_without_allowlist(client, monkeypatch):
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "testsecret")
    monkeypatch.setattr(bot_server, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot_server, "RUN_ALLOWLIST", set())
    monkeypatch.setattr(bot_server, "GH_REPO_DATA", "owner/data")
    monkeypatch.setattr(bot_server, "GH_PAT", "token")
    with patch("bot.bot_server.requests.post", return_value=_dispatch_ok()), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post(
            "/webhook",
            json=_update("/run", chat_id=999),
            headers=_h(),
        )
        assert "triggered" in mock_send.call_args[0][1].lower()


# ── rate limits ───────────────────────────────────────────────────


def test_second_run_within_window_refused(client, monkeypatch, _isolated_run_quota):
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "testsecret")
    monkeypatch.setattr(bot_server, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot_server, "RUN_ALLOWLIST", {42})
    monkeypatch.setattr(bot_server, "GH_REPO_DATA", "owner/data")
    monkeypatch.setattr(bot_server, "GH_PAT", "token")

    # Seed last run 5 minutes ago
    state = {
        "day": datetime.now(tz=_IST).strftime("%Y-%m-%d"),
        "global_count": 1,
        "users": {bot_server._user_key(42): (datetime.now(tz=_IST) - timedelta(minutes=5)).isoformat()},
    }
    with open(_isolated_run_quota, "w", encoding="utf-8") as fh:
        json.dump(state, fh)

    with patch("bot.bot_server.requests.post", return_value=_dispatch_ok()) as post, \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post(
            "/webhook",
            json=_update("/run", chat_id=42),
            headers=_h(),
        )
        msg = mock_send.call_args[0][1].lower()
        assert "rate limited" in msg or "try again" in msg
        post.assert_not_called()


def test_window_expiry_permits_again(client, monkeypatch, _isolated_run_quota):
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "testsecret")
    monkeypatch.setattr(bot_server, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot_server, "RUN_ALLOWLIST", {42})
    monkeypatch.setattr(bot_server, "GH_REPO_DATA", "owner/data")
    monkeypatch.setattr(bot_server, "GH_PAT", "token")
    monkeypatch.setattr(bot_server, "RUN_RATE_LIMIT_SECONDS", 3600)

    state = {
        "day": datetime.now(tz=_IST).strftime("%Y-%m-%d"),
        "global_count": 1,
        "users": {
            str(42): (datetime.now(tz=_IST) - timedelta(hours=2)).isoformat()
        },
    }
    with open(_isolated_run_quota, "w", encoding="utf-8") as fh:
        json.dump(state, fh)

    with patch("bot.bot_server.requests.post", return_value=_dispatch_ok()), \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post(
            "/webhook",
            json=_update("/run", chat_id=42),
            headers=_h(),
        )
        assert "triggered" in mock_send.call_args[0][1].lower()


def test_global_daily_ceiling_enforced(client, monkeypatch, _isolated_run_quota):
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "testsecret")
    monkeypatch.setattr(bot_server, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot_server, "RUN_ALLOWLIST", {42})
    monkeypatch.setattr(bot_server, "RUN_GLOBAL_DAILY_CEILING", 2)

    state = {
        "day": datetime.now(tz=_IST).strftime("%Y-%m-%d"),
        "global_count": 2,
        "users": {},
    }
    with open(_isolated_run_quota, "w", encoding="utf-8") as fh:
        json.dump(state, fh)

    with patch("bot.bot_server.requests.post") as post, \
         patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post(
            "/webhook",
            json=_update("/run", chat_id=42),
            headers=_h(),
        )
        msg = mock_send.call_args[0][1].lower()
        assert "ceiling" in msg or "daily" in msg
        post.assert_not_called()


# ── /quota ────────────────────────────────────────────────────────


def test_quota_command_allowlisted(client, monkeypatch, tmp_path):
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "testsecret")
    monkeypatch.setattr(bot_server, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot_server, "RUN_ALLOWLIST", {42})
    ws = tmp_path / "ws.json"
    ws.write_text(
        json.dumps({
            "month": datetime.now(tz=_IST).strftime("%Y-%m"),
            "used": 48,
            "rotation": 0,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot_server, "WEBSEARCH_STATE_PATH", str(ws))
    monkeypatch.setattr(bot_server, "WEBSEARCH_MONTHLY_QUOTA", 900)

    with patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post(
            "/webhook",
            json=_update("/quota", chat_id=42),
            headers=_h(),
        )
        msg = mock_send.call_args[0][1]
        assert "48" in msg
        assert "900" in msg


def test_quota_refused_for_strangers(client, monkeypatch):
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "testsecret")
    monkeypatch.setattr(bot_server, "ADMIN_CHAT_ID", 999)
    monkeypatch.setattr(bot_server, "RUN_ALLOWLIST", {42})
    with patch("bot.bot_server._send", return_value=True) as mock_send:
        client.post(
            "/webhook",
            json=_update("/quota", chat_id=1),
            headers=_h(),
        )
        assert "unauthorized" in mock_send.call_args[0][1].lower()
