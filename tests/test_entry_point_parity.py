"""Guards against the three Telegram entry points drifting apart.

The handlers are shared; the *routing tables* were not — which is how
``/quota`` shipped on the webhook but not on poll/drain.  These tests fail
the build the next time a command is added to one dispatcher and not the
other, and pin the shared ``normalize_command`` behaviour.
"""

import inspect
import re
from unittest.mock import patch

import pytest

import bot.bot_server as bot_server
import bot.poll as poll


_NOT_COMMANDS = {"/webhook", "/health"}  # Flask route paths, not bot commands


def _commands_in(source: str) -> set[str]:
    """Every string literal that looks like a bot command in *source*."""
    return set(re.findall(r'"(/[a-z]+)"', source)) - _NOT_COMMANDS


def test_webhook_and_poll_route_identical_command_sets():
    web = _commands_in(inspect.getsource(bot_server.webhook))
    pol = _commands_in(inspect.getsource(poll.dispatch))
    assert web == pol, (
        f"entry points diverged: webhook-only={web - pol}, poll-only={pol - web}"
    )


def test_help_text_only_advertises_routable_commands():
    help_src = inspect.getsource(bot_server.handle_help)
    advertised = _commands_in(help_src)
    routable = _commands_in(inspect.getsource(bot_server.webhook))
    assert advertised <= routable, (
        f"/help advertises commands the webhook cannot route: {advertised - routable}"
    )
    assert advertised <= _commands_in(inspect.getsource(poll.dispatch)), (
        "/help advertises commands poll/drain cannot route"
    )


# ── /quota reachable from every transport ───────────────────────────


def test_quota_routes_via_poll_dispatch():
    with patch("bot.poll.handle_quota") as h:
        poll.dispatch("/quota", 1, "u")
        h.assert_called_once_with(1)


def test_quota_routes_via_webhook(monkeypatch):
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "s")
    client = bot_server.app.test_client()
    with patch("bot.bot_server.handle_quota") as h:
        client.post(
            "/webhook",
            json={"message": {"chat": {"id": 7}, "text": "/quota"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "s"},
        )
        h.assert_called_once_with(7)


# ── normalize_command shared behaviour ──────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/start", "/start"),
        ("/start@MyBot", "/start"),
        ("/START", "/start"),
        ("/quota@MyBot extra words", "/quota"),
        ("", ""),
        ("hello there", "hello"),
    ],
)
def test_normalize_command(text, expected):
    assert bot_server.normalize_command(text) == expected


def test_webhook_handles_group_suffixed_start(monkeypatch):
    """'/start@MyBot' (group form) must not fall through to handle_unknown."""
    monkeypatch.setattr(bot_server, "WEBHOOK_SECRET", "s")
    client = bot_server.app.test_client()
    with patch("bot.bot_server.handle_start") as h, \
         patch("bot.bot_server.handle_unknown") as u:
        client.post(
            "/webhook",
            json={"message": {"chat": {"id": 9, "username": "x"},
                              "text": "/start@MyBot"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "s"},
        )
        h.assert_called_once()
        u.assert_not_called()
