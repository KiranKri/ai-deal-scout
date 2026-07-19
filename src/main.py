"""Main orchestrator for ai-deal-scout.

Run from the project root::

    python src/main.py

The pipeline scrapes deal sources, filters and deduplicates results, then
delivers new deals via Telegram and records them in the history log.
"""

import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Add project root to sys.path so the bot/ package is importable when this
# script is launched directly as ``python src/main.py``.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env into the environment for LOCAL runs.  In GitHub Actions there is
# no .env file and the workflow's env: block supplies the real values, so this
# is a harmless no-op there.  Without it, os.getenv() returns empty locally
# even when .env is correctly filled in.
try:
    from dotenv import load_dotenv

    load_dotenv(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )
    )
except ImportError:  # pragma: no cover - optional dependency
    logging.getLogger("main").debug("python-dotenv not installed; skipping .env")

from bot.subscribers import SubscriberStoreError, get_active_chat_ids

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

_IST = ZoneInfo("Asia/Kolkata")


# Kept as a module-level name (rather than a direct import at call sites) so
# tests can monkeypatch ``main._alert_admin``.
from alerts import alert_admin as _alert_admin  # noqa: E402


def _print_deals(deals: list[dict], limit: int = 0) -> None:
    """Print deals to the terminal, grouped by source, for local review.

    The Telegram message is the product; this is the inspection view.  Grouping
    by source makes it obvious which scraper is contributing noise.
    """
    if not deals:
        print("\n  No new deals.\n")
        return

    shown = deals[:limit] if limit else deals
    by_source: dict[str, list[dict]] = {}
    for deal in shown:
        by_source.setdefault(deal.get("source", "?"), []).append(deal)

    print(f"\n  {len(shown)} deal(s)"
          + (f" (of {len(deals)}, --limit {limit})" if limit else "") + "\n")
    for source in sorted(by_source, key=lambda s: -len(by_source[s])):
        items = by_source[source]
        print(f"  {source}  ({len(items)})")
        for deal in items:
            votes = deal.get("upvotes", 0)
            suffix = f"  [{votes} pts]" if votes else ""
            print(f"    - {deal.get('title', '')[:96]}{suffix}")
            print(f"      {deal.get('url', '')[:110]}")
        print()


def _parse_args(argv: list[str] | None = None):
    """Command-line options for local inspection runs."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Find AI tool deals and broadcast them to Telegram."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Find and log deals without sending anything and without marking "
            "them seen. Use this to inspect results on your own machine — a "
            "normal run would consume the deals, so subscribers would never "
            "receive them."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Show only the first N deals in the dry-run summary (0 = all).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Execute the full deal-scouting pipeline."""
    args = _parse_args(argv)
    import dedup
    import history
    import notifier
    from filter import is_relevant, is_stale
    from scrapers import run_all_scrapers

    # ------------------------------------------------------------------
    # a. Log run start with IST timestamp
    # ------------------------------------------------------------------
    ist_now = datetime.now(tz=_IST).strftime("%Y-%m-%d %H:%M:%S %Z")
    logger.info("Run started at %s", ist_now)

    # ------------------------------------------------------------------
    # b. Fetch subscribers FIRST (strict) — if the store is unreachable we
    #    must abort *before* any deal is marked seen, otherwise this run's
    #    deals are permanently lost while the broadcast silently no-ops.
    # ------------------------------------------------------------------
    try:
        chat_ids = get_active_chat_ids(strict=True)
    except SubscriberStoreError:
        logger.exception("Subscriber store unavailable; aborting before dedup marking")
        _alert_admin("subscriber store unreachable — run aborted, deals will retry next run")
        sys.exit(1)

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

    if not raw_deals:
        _alert_admin("all scrapers returned 0 raw results — sources may be down/blocked")

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
    # e2. Staleness check — LOG-ONLY for now.  Catches last year's Black
    #     Friday SEO pages and multi-year-old announcements (both observed
    #     live).  Promote to a hard drop once a week of logs shows no
    #     false stale flags.
    # ------------------------------------------------------------------
    stale_flagged = [
        d for d in relevant_deals
        if is_stale(d.get("title", ""), d.get("body", ""))
    ]
    if stale_flagged:
        logger.warning(
            "Staleness (log-only): %d deal(s) look expired/out-of-season: %s",
            len(stale_flagged),
            "; ".join(d.get("title", "")[:70] for d in stale_flagged[:10]),
        )

    # ------------------------------------------------------------------
    # f+g. Sequential dedup: check and mark immediately so same-run
    #      cross-source duplicates (e.g. same story on Reddit + HN)
    #      are caught before the next deal is evaluated.
    #
    #      DRY RUN: with zero active subscribers there is nobody to deliver
    #      to, so marking deals seen would burn them into the 90-day dedup
    #      window for an audience of no one — the first real subscriber
    #      would then receive nothing that was found before they joined.
    #      Instead we scrape, filter, and write the history log (so the
    #      output is still reviewable during development) but leave the
    #      dedup store untouched.
    # ------------------------------------------------------------------
    # Dry run when explicitly asked, or when nobody is subscribed.
    dry_run = args.dry_run or not chat_ids
    if args.dry_run:
        logger.warning(
            "--dry-run: deals will be printed and logged, but NOT sent and "
            "NOT marked seen, so nothing is consumed."
        )
    if dry_run and not args.dry_run:
        logger.warning(
            "No active subscribers — DRY RUN: deals will be logged but not "
            "marked seen, so nothing is lost before the first subscriber."
        )

    # Collect without marking.  Same-run duplicates are already removed by the
    # cross-source pass in run_all_scrapers, so nothing is lost by deferring.
    new_deals: list[dict] = []
    for deal in relevant_deals:
        if not dedup.is_seen(deal.get("url", ""), deal.get("title", "")):
            new_deals.append(deal)

    logger.info("Dedup: %d relevant → %d new", len(relevant_deals), len(new_deals))

    # ------------------------------------------------------------------
    # h. Broadcast FIRST, mark seen only on confirmed delivery.
    #
    #    Marking before sending burns deals whenever delivery fails: they are
    #    inside the 90-day window but nobody received them, and there is no
    #    retry.  Observed live — a stale subscriber ID made every send return
    #    HTTP 400 and silently consumed 89 real deals in one run.
    #
    #    Delivering to at least one subscriber is the condition for marking.
    #    The residual risk is the reverse (deliver, then fail to persist),
    #    which causes a duplicate next run — visibly annoying but not silent
    #    data loss, and therefore the better failure to have.
    # ------------------------------------------------------------------
    delivered, total = 0, len(chat_ids)
    try:
        if dry_run:
            logger.warning(
                "No active subscribers — skipping Telegram send (dry run)")
        else:
            logger.info("Broadcasting to %d subscribers", total)
            delivered, total = notifier.send_deals(new_deals, chat_ids)
    except Exception:
        logger.exception("Broadcast step failed")

    if dry_run:
        logger.info("Dry run: %d deal(s) left unmarked for a future subscriber",
                    len(new_deals))
        if args.dry_run:
            _print_deals(new_deals, args.limit)
    elif delivered > 0:
        for deal in new_deals:
            try:
                dedup.mark_seen(deal.get("url", ""), deal.get("title", ""))
            except Exception:
                logger.exception("mark_seen failed for %s", deal.get("url", ""))
        try:
            dedup.save()
        except Exception:
            logger.exception("dedup.save() failed; continuing")
        logger.info("Marked %d deal(s) seen after delivery to %d/%d subscriber(s)",
                    len(new_deals), delivered, total)
    elif new_deals:
        logger.error(
            "Delivered to 0/%d subscribers — NOT marking %d deal(s) seen; "
            "they will be retried on the next run",
            total, len(new_deals),
        )

    if new_deals and total and delivered == 0:
        _alert_admin(
            f"{len(new_deals)} new deal(s) found but delivered to 0/{total} "
            f"subscribers — deals kept for retry"
        )

    # ------------------------------------------------------------------
    # j. Append to history log (after the send so the record is truthful).
    #    Skipped for dry runs and 0-delivered runs: those deals stay
    #    unmarked and will be re-found next run, and re-logging them every
    #    time filled the history file with duplicates (85 duplicated titles
    #    observed before this guard).
    # ------------------------------------------------------------------
    if dry_run or (new_deals and delivered == 0):
        logger.info(
            "History: skipped (%s) — %d deal(s) will be re-found next run",
            "dry run" if dry_run else "0 delivered",
            len(new_deals),
        )
    else:
        try:
            history.append_deals(new_deals)
        except Exception:
            logger.exception("history.append_deals() failed; continuing")

    # ------------------------------------------------------------------
    # k. Final summary log
    # ------------------------------------------------------------------
    logger.info(
        "Run complete: %d raw → %d relevant → %d new → delivered to %d/%d subscribers",
        len(raw_deals),
        len(relevant_deals),
        len(new_deals),
        delivered,
        total,
    )


if __name__ == "__main__":
    main()
