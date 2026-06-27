"""Main orchestrator for ai-deal-scout.

Run from the project root::

    python src/main.py

The pipeline scrapes deal sources, filters and deduplicates results, then
delivers new deals via Telegram and records them in the history log.
"""

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

_IST = ZoneInfo("Asia/Kolkata")


def main() -> None:
    """Execute the full deal-scouting pipeline."""
    import dedup
    import history
    import notifier
    from filter import is_relevant
    from scrapers import run_all_scrapers

    # ------------------------------------------------------------------
    # a. Log run start with IST timestamp
    # ------------------------------------------------------------------
    ist_now = datetime.now(tz=_IST).strftime("%Y-%m-%d %H:%M:%S %Z")
    logger.info("Run started at %s", ist_now)

    # ------------------------------------------------------------------
    # b. Initialize dedup store
    # ------------------------------------------------------------------
    dedup._load()

    # ------------------------------------------------------------------
    # c. Clean up stale hashes
    # ------------------------------------------------------------------
    removed = dedup.cleanup_old_hashes()
    logger.info("Cleanup: removed %d expired hash(es)", removed)

    # ------------------------------------------------------------------
    # d. Scrape all sources
    # ------------------------------------------------------------------
    try:
        raw_deals = run_all_scrapers()
    except Exception:
        logger.exception("run_all_scrapers raised an unrecoverable error; aborting")
        sys.exit(1)

    # ------------------------------------------------------------------
    # e. Filter by relevance
    # ------------------------------------------------------------------
    relevant_deals = [
        d for d in raw_deals
        if is_relevant(d.get("title", ""), d.get("body", ""), d.get("upvotes", 0))
    ]
    logger.info(
        "Filter: %d raw → %d relevant", len(raw_deals), len(relevant_deals)
    )

    # ------------------------------------------------------------------
    # f. Deduplicate against seen history
    # ------------------------------------------------------------------
    new_deals = [
        d for d in relevant_deals
        if not dedup.is_seen(d.get("url", ""), d.get("title", ""))
    ]
    logger.info(
        "Dedup: %d relevant → %d new", len(relevant_deals), len(new_deals)
    )

    # ------------------------------------------------------------------
    # g. Mark new deals as seen
    # ------------------------------------------------------------------
    try:
        for deal in new_deals:
            dedup.mark_seen(deal.get("url", ""), deal.get("title", ""))
    except Exception:
        logger.exception("mark_seen failed; continuing")

    # ------------------------------------------------------------------
    # h. Persist dedup state BEFORE sending to Telegram
    # ------------------------------------------------------------------
    try:
        dedup.save()
    except Exception:
        logger.exception("dedup.save() failed; continuing")

    # ------------------------------------------------------------------
    # i. Append to history log
    # ------------------------------------------------------------------
    try:
        history.append_deals(new_deals)
    except Exception:
        logger.exception("history.append_deals() failed; continuing")

    # ------------------------------------------------------------------
    # j. Send Telegram notification
    # ------------------------------------------------------------------
    try:
        notifier.send_deals(new_deals)
    except Exception:
        logger.exception("notifier.send_deals() failed; continuing")

    # ------------------------------------------------------------------
    # k. Final summary log
    # ------------------------------------------------------------------
    logger.info(
        "Run complete: %d raw → %d relevant → %d new sent",
        len(raw_deals),
        len(relevant_deals),
        len(new_deals),
    )


if __name__ == "__main__":
    main()
