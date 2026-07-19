"""Long-polling Telegram bot runner — no server, no webhook, no public URL.

Run on your own machine::

    python bot/poll.py

This is the fast path for testing with real people.  The webhook server in
``bot_server.py`` needs a public HTTPS URL (Render) and a registered webhook;
polling needs neither — it just asks Telegram "any new messages?" in a loop.

Command handling is shared with ``bot_server.py`` so the two paths cannot
drift apart: this module only replaces the *transport*.

Stop with Ctrl-C.  Subscribers persist to ``data/subscribers.json`` locally,
or to the private GitHub repo when ``GH_REPO_DATA``/``GH_PAT`` are set.
"""

import logging
import os
import sys
import time

# Make the project root importable when launched as ``python bot/poll.py``.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )
    )
except ImportError:  # pragma: no cover - optional dependency
    pass

import requests  # noqa: E402

from bot import subscribers  # noqa: E402
from bot.bot_server import (  # noqa: E402
    handle_help,
    handle_quota,
    handle_run,
    handle_start,
    handle_status,
    handle_stop,
    handle_unknown,
    normalize_command,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("poll")

POLL_TIMEOUT = 30       # seconds Telegram holds the connection open
ERROR_BACKOFF = 5.0     # seconds to wait after a failed poll


def dispatch(text: str, chat_id: int, username: str | None) -> None:
    """Route one message to the shared command handlers.

    Args:
        text:     Message text, already stripped.
        chat_id:  Telegram chat ID of the sender.
        username: Telegram ``@username`` or ``None``.
    """
    # Telegram appends "@BotName" to commands sent in groups.
    command = normalize_command(text)

    if command == "/start":
        handle_start(chat_id, username)
    elif command == "/stop":
        handle_stop(chat_id)
    elif command == "/help":
        handle_help(chat_id)
    elif command == "/run":
        handle_run(chat_id)
    elif command == "/status":
        handle_status(chat_id)
    elif command == "/quota":
        handle_quota(chat_id)
    else:
        handle_unknown(chat_id)


def main() -> None:
    """Poll Telegram for updates until interrupted."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Put it in .env and retry.")

    api = f"https://api.telegram.org/bot{token}"

    # Identify the bot so the operator knows which one is live.
    try:
        me = requests.get(f"{api}/getMe", timeout=10).json()
        if not me.get("ok"):
            sys.exit(f"Token rejected by Telegram: {me.get('description')}")
        username = me["result"]["username"]
    except requests.RequestException as exc:
        sys.exit(f"Cannot reach Telegram: {exc}")

    backend = "local file" if subscribers._use_local() else "private GitHub repo"
    counts = subscribers.get_subscriber_count()

    print()
    print(f"  Bot        : @{username}")
    print(f"  Share this : https://t.me/{username}")
    print(f"  Storage    : {backend}")
    print(f"  Subscribers: {counts['active']} active / {counts['total']} total")
    print()
    print("  Listening for /start, /stop, /help, /run, /status, /quota. Ctrl-C to stop.")
    print()

    # Drop any messages queued while the bot was offline, so a friend's old
    # "/start" from yesterday does not replay on every restart.
    offset: int | None = None
    try:
        pending = requests.get(
            f"{api}/getUpdates", params={"timeout": 0}, timeout=15
        ).json()
        if pending.get("ok") and pending.get("result"):
            offset = pending["result"][-1]["update_id"] + 1
            logger.info("Skipped %d queued update(s)", len(pending["result"]))
    except requests.RequestException:
        pass

    while True:
        try:
            params = {"timeout": POLL_TIMEOUT}
            if offset is not None:
                params["offset"] = offset

            response = requests.get(
                f"{api}/getUpdates", params=params, timeout=POLL_TIMEOUT + 10
            )
            data = response.json()

            if not data.get("ok"):
                logger.error("getUpdates failed: %s", data.get("description"))
                time.sleep(ERROR_BACKOFF)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                user = message["chat"].get("username")
                text = (message.get("text") or "").strip()

                logger.info("<- %s from chat_id=%s (@%s)", text or "<non-text>", chat_id, user)
                try:
                    dispatch(text, chat_id, user)
                except Exception:  # noqa: BLE001
                    logger.exception("handler failed for chat_id=%s", chat_id)

        except KeyboardInterrupt:
            print("\n  Stopped.\n")
            return
        except requests.RequestException as exc:
            logger.warning("poll failed (%s); retrying in %.0fs", exc, ERROR_BACKOFF)
            time.sleep(ERROR_BACKOFF)
        except Exception:  # noqa: BLE001
            logger.exception("unexpected error; continuing")
            time.sleep(ERROR_BACKOFF)


if __name__ == "__main__":
    main()
