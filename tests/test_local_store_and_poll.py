"""Local subscriber backend + polling transport.

These cover the zero-cloud-setup path used for friend testing: no Render,
no webhook, no private GitHub repo.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from bot import poll, subscribers


@pytest.fixture(autouse=True)
def _local_backend(tmp_path, monkeypatch):
    """Force the local-file backend at a temp path."""
    monkeypatch.setattr(subscribers, "GH_REPO_DATA", "")
    monkeypatch.setattr(subscribers, "GH_PAT", "")
    monkeypatch.setattr(subscribers, "LOCAL_STORE_PATH", str(tmp_path / "subs.json"))
    yield


# --- backend selection ------------------------------------------------------

def test_local_backend_selected_without_github_config():
    assert subscribers._use_local() is True


def test_github_backend_selected_when_configured(monkeypatch):
    monkeypatch.setattr(subscribers, "GH_REPO_DATA", "owner/repo")
    monkeypatch.setattr(subscribers, "GH_PAT", "pat")
    assert subscribers._use_local() is False


def test_local_backend_makes_no_http_calls():
    with patch("bot.subscribers.requests.get") as g, \
         patch("bot.subscribers.requests.put") as p:
        subscribers.add_subscriber(1, "kiran")
        subscribers.get_active_chat_ids()
        g.assert_not_called()
        p.assert_not_called()


# --- subscribe / unsubscribe round trip -------------------------------------

def test_subscribe_persists_to_disk():
    assert subscribers.add_subscriber(4242, "friend") == "new"
    with open(subscribers.LOCAL_STORE_PATH) as fh:
        data = json.load(fh)
    assert data["subscribers"][0]["chat_id"] == 4242
    assert data["subscribers"][0]["active"] is True


def test_subscribe_twice_is_idempotent():
    subscribers.add_subscriber(1, "a")
    assert subscribers.add_subscriber(1, "a") == "already_active"
    assert len(subscribers.get_subscribers()) == 1


def test_stop_then_start_reactivates():
    subscribers.add_subscriber(7, "b")
    assert subscribers.deactivate_subscriber(7) == "deactivated"
    assert subscribers.get_active_chat_ids() == []
    assert subscribers.add_subscriber(7, "b") == "resubscribed"
    assert subscribers.get_active_chat_ids() == [7]


def test_stop_for_unknown_user():
    assert subscribers.deactivate_subscriber(999) == "not_found"


def test_active_chat_ids_excludes_inactive():
    subscribers.add_subscriber(1, "a")
    subscribers.add_subscriber(2, "b")
    subscribers.deactivate_subscriber(1)
    assert subscribers.get_active_chat_ids() == [2]


def test_counts_reflect_state():
    subscribers.add_subscriber(1, "a")
    subscribers.add_subscriber(2, "b")
    subscribers.deactivate_subscriber(2)
    assert subscribers.get_subscriber_count() == {"total": 2, "active": 1, "inactive": 1}


def test_corrupt_local_file_does_not_raise():
    with open(subscribers.LOCAL_STORE_PATH, "w") as fh:
        fh.write("{not json")
    assert subscribers.get_subscribers() == []


# --- polling dispatch -------------------------------------------------------

def test_dispatch_routes_each_command():
    for text, target in [
        ("/start", "handle_start"),
        ("/stop", "handle_stop"),
        ("/help", "handle_help"),
        ("/run", "handle_run"),
        ("/status", "handle_status"),
    ]:
        with patch(f"bot.poll.{target}") as h:
            poll.dispatch(text, 1, "u")
            assert h.called, f"{text} should call {target}"


def test_dispatch_strips_bot_suffix_used_in_groups():
    """Telegram sends '/start@MyBot' in group chats."""
    with patch("bot.poll.handle_start") as h:
        poll.dispatch("/start@Kkaideal_bot", 1, "u")
        h.assert_called_once()


def test_dispatch_is_case_insensitive():
    with patch("bot.poll.handle_start") as h:
        poll.dispatch("/START", 1, "u")
        h.assert_called_once()


def test_dispatch_unknown_command():
    with patch("bot.poll.handle_unknown") as h:
        poll.dispatch("hello there", 1, "u")
        h.assert_called_once()


def test_dispatch_empty_text_does_not_crash():
    with patch("bot.poll.handle_unknown") as h:
        poll.dispatch("", 1, "u")
        h.assert_called_once()


# --- end to end: friend subscribes, then gets deals -------------------------

def test_friend_subscribes_then_appears_in_broadcast_list():
    """The full friend-test path, minus Telegram itself."""
    with patch("bot.bot_server._send", return_value=True):
        from bot.bot_server import handle_start, handle_stop

        handle_start(555, "friend")
        assert 555 in subscribers.get_active_chat_ids()

        handle_stop(555)
        assert 555 not in subscribers.get_active_chat_ids()


# --- drain: scheduled, stateless command handling ---------------------------

def test_drain_refuses_local_storage_in_ci(monkeypatch, capsys):
    """Committing chat IDs into a public repo must be impossible."""
    from bot import drain
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(subscribers, "GH_REPO_DATA", "")
    monkeypatch.setattr(subscribers, "GH_PAT", "")
    with pytest.raises(SystemExit) as exc:
        drain.main()
    assert "private" in str(exc.value).lower()


def test_drain_allows_local_storage_outside_ci(monkeypatch):
    """Local runs may use the on-disk store; nothing gets committed."""
    from bot import drain
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    with patch("bot.drain.drain_once", return_value=0) as d:
        drain.main()
        d.assert_called_once()


def test_drain_advances_and_persists_offset(tmp_path, monkeypatch):
    from bot import drain
    monkeypatch.setattr(drain, "OFFSET_PATH", str(tmp_path / "off.json"))
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params.get("offset"))
        r = MagicMock()
        if len(calls) == 1:
            r.json.return_value = {"ok": True, "result": [
                {"update_id": 10, "message": {"chat": {"id": 1, "username": "a"},
                                              "text": "/help"}}]}
        else:
            r.json.return_value = {"ok": True, "result": []}
        return r

    with patch("bot.drain.requests.get", side_effect=fake_get), \
         patch("bot.drain.dispatch") as disp:
        handled = drain.drain_once("token")

    assert handled == 1
    disp.assert_called_once()
    assert drain._load_offset() == 11, "cursor must advance past the handled update"
    assert calls[1] == 11, "second batch must resume from the new cursor"


def test_drain_does_not_reprocess_handled_updates(tmp_path, monkeypatch):
    """The whole point of persisting the cursor: no duplicate confirmations."""
    from bot import drain
    monkeypatch.setattr(drain, "OFFSET_PATH", str(tmp_path / "off.json"))
    drain._save_offset(11)
    with patch("bot.drain.requests.get") as g:
        g.return_value = MagicMock()
        g.return_value.json.return_value = {"ok": True, "result": []}
        drain.drain_once("token")
        assert g.call_args.kwargs["params"]["offset"] == 11


def test_drain_survives_network_failure(tmp_path, monkeypatch):
    from bot import drain
    import requests as rq
    monkeypatch.setattr(drain, "OFFSET_PATH", str(tmp_path / "off.json"))
    with patch("bot.drain.requests.get", side_effect=rq.RequestException("down")):
        assert drain.drain_once("token") == 0


def test_drain_survives_corrupt_offset_file(tmp_path, monkeypatch):
    from bot import drain
    p = tmp_path / "off.json"
    p.write_text("{not json")
    monkeypatch.setattr(drain, "OFFSET_PATH", str(p))
    assert drain._load_offset() is None


# ---------------------------------------------------------------------------
# run_quota.json is COMMITTED to this repo so the /run cooldown survives the
# ephemeral CI runner.  This repo is intended to be public, so that file must
# never carry subscriber identities — the same leak that subscribers.json had.
# ---------------------------------------------------------------------------


def test_run_quota_never_stores_raw_chat_ids(tmp_path, monkeypatch):
    from bot import bot_server

    quota = tmp_path / "run_quota.json"
    monkeypatch.setattr(bot_server, "RUN_QUOTA_STATE_PATH", str(quota))

    chat_id = 8564560896  # realistic Telegram chat ID
    bot_server._record_run(chat_id)

    raw = quota.read_text()
    assert str(chat_id) not in raw, (
        "raw chat ID written to a file this repo commits publicly"
    )
    assert bot_server._user_key(chat_id) in raw


def test_user_key_is_stable_and_opaque():
    from bot import bot_server

    a = bot_server._user_key(12345)
    assert a == bot_server._user_key(12345), "key must be stable across calls"
    assert a != bot_server._user_key(12346), "different users must not collide"
    assert "12345" not in a


def test_rate_limit_still_works_with_hashed_keys(tmp_path, monkeypatch):
    """Hashing must not break the cooldown it protects."""
    from bot import bot_server

    quota = tmp_path / "run_quota.json"
    monkeypatch.setattr(bot_server, "RUN_QUOTA_STATE_PATH", str(quota))
    bot_server._record_run(999)
    state = bot_server._load_run_quota()
    assert bot_server._user_key(999) in state["users"]
