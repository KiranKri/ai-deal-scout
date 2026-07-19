"""Drain queued Telegram commands once, then exit.

Designed to run on a schedule (GitHub Actions) rather than as a long-lived
server.  Telegram queues undelivered updates for 24 hours, so a job that runs
every ~10 minutes picks up every ``/start`` and ``/stop`` without anything
being always-on: no VPS, no Render, no laptop.  The pipeline itself runs
once daily at 08:00 IST.

    python bot/drain.py

Difference from ``poll.py``: that one blocks forever with long-polling for
local use.  This one makes a single non-blocking pass and exits, which is what
a cron job needs.  Both share the command handlers in ``bot_server.py``, so
behaviour cannot drift between them.

The read cursor is persisted to ``data/telegram_offset.json`` and committed
back by the workflow.  Without that, every run would re-process the same
updates and spam users with repeated confirmations.
"""

import json
import logging
import os
import sys

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
from bot.poll import dispatch  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("drain")

OFFSET_PATH = os.environ.get(
    "TELEGRAM_OFFSET_PATH", os.path.join("data", "telegram_offset.json")
)

# Telegram caps getUpdates at 100 per call; loop until drained.
MAX_BATCHES = 10


def _load_offset() -> int | None:
    """Read the persisted update cursor, or None on first run."""
    if not os.path.exists(OFFSET_PATH):
        return None
    try:
        with open(OFFSET_PATH, encoding="utf-8") as fh:
            value = json.load(fh).get("offset")
        return int(value) if value is not None else None
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("offset file unreadable (%s); starting from scratch", exc)
        return None


def _save_offset(offset: int) -> None:
    """Persist the update cursor.  Never raises."""
    try:
        os.makedirs(os.path.dirname(OFFSET_PATH) or ".", exist_ok=True)
        with open(OFFSET_PATH, "w", encoding="utf-8") as fh:
            json.dump({"offset": offset}, fh, indent=2)
    except OSError as exc:
        logger.error("failed to save offset: %s", exc)


def drain_once(token: str) -> int:
    """Process every queued update and return how many were handled.

    Args:
        token: Telegram bot token.

    Returns:
        Count of messages dispatched.  Zero is the normal case.
    """
    api = f"https://api.telegram.org/bot{token}"
    offset = _load_offset()
    handled = 0

    for _ in range(MAX_BATCHES):
        params: dict = {"timeout": 0, "limit": 100}
        if offset is not None:
            params["offset"] = offset

        try:
            data = requests.get(f"{api}/getUpdates", params=params, timeout=30).json()
        except requests.RequestException as exc:
            logger.error("getUpdates failed: %s", exc)
            break

        if not data.get("ok"):
            logger.error("getUpdates rejected: %s", data.get("description"))
            break

        updates = data.get("result", [])
        if not updates:
            break

        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            username = message["chat"].get("username")
            text = (message.get("text") or "").strip()

            logger.info("<- %r from chat_id=%s (@%s)", text, chat_id, username)
            try:
                dispatch(text, chat_id, username)
                handled += 1
            except Exception:  # noqa: BLE001
                logger.exception("handler failed for chat_id=%s", chat_id)

        # Persist after every batch so a crash mid-drain cannot replay
        # already-answered commands.
        if offset is not None:
            _save_offset(offset)

    return handled


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set.")

    # Guard: on an ephemeral CI runner the local store would be committed back
    # into THIS repo, which is intended to be public.  Subscriber chat IDs are
    # personal data and must live in the private repo behind GH_REPO_DATA.
    if os.getenv("CI") and subscribers._use_local():
        sys.exit(
            "Refusing to run: GH_REPO_DATA/GH_PAT are not set, so subscribers "
            "would be written to local disk and committed into this repo. "
            "Create a private data repo and set both secrets. "
            "See docs/USER_GUIDE.md section B2."
        )

    handled = drain_once(token)
    logger.info("drain complete: %d message(s) handled", handled)


if __name__ == "__main__":
    main()
