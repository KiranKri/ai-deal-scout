# AI Deal Scout — What It Does and How It Does It

**Version:** V2 (public multi-subscriber broadcast)
**Last updated:** 2026-07-18

---

## 1. What it does

AI Deal Scout is a Python bot that automatically hunts for **deals, discounts, promo codes, free trials, and price drops on AI tools** (Claude, ChatGPT, Cursor, Midjourney, Perplexity, Gemini, Copilot, and others), and pushes the matches to Telegram subscribers.

It has two independently running halves:

| Half | Runs on | Trigger | Job |
|---|---|---|---|
| **Scout pipeline** (`src/`) | GitHub Actions | Cron `30 2,14 * * *` (08:00 & 20:00 IST) + manual dispatch | Scrape → filter → dedup → broadcast |
| **Bot server** (`bot/`) | Render (Flask + gunicorn) | Telegram webhook, always-on | Handle `/start`, `/stop`, `/help`, `/run`, `/status` |

The two halves communicate only through shared state: a **private GitHub repo holding `subscribers.json`**, which the bot server writes and the pipeline reads.

---

## 2. High-level flow

```
                    ┌─────────────────────────┐
   Telegram user ──▶│  Flask bot server       │──▶ subscribers.json
   /start /stop     │  (Render, always-on)    │    (private GitHub repo)
                    └─────────────────────────┘             │
                                                            │ read
   GitHub Actions cron (08:00 / 20:00 IST)                  ▼
                    ┌──────────────────────────────────────────────┐
                    │  src/main.py pipeline                        │
                    │                                              │
                    │  1. scrapers/  Reddit RSS                    │
                    │                HN Algolia API                │
                    │                Generic RSS (empty)           │
                    │                BitDegree HTML scrape         │
                    │  2. cross-source title dedup                 │
                    │  3. filter.is_relevant()  keyword gates      │
                    │  4. dedup.is_seen()  SHA-256 hash store      │
                    │  5. history.append_deals()  markdown log     │
                    │  6. notifier.send_deals()  fan-out broadcast │
                    └──────────────────────────────────────────────┘
                                     │
                                     ▼
                          Telegram subscribers
```

---

## 3. Module-by-module

### `src/main.py` — orchestrator

Runs the pipeline in a fixed order, with deliberate failure isolation (each stage after scraping is wrapped in `try/except` so one failure doesn't kill the run):

1. Log run start with an IST timestamp.
2. `dedup._load()` — warm the seen-deal store.
3. `dedup.cleanup_old_hashes()` — drop hashes older than 90 days.
4. `scrapers.run_all_scrapers()` — the only stage that aborts the run (`sys.exit(1)`) if it raises.
5. `filter.is_relevant()` — keep only deal-intent + AI-tool posts.
6. **Sequential** dedup: check `is_seen` and `mark_seen` inside the same loop, so two copies of the same story arriving from different sources in one run are caught.
7. `dedup.save()` — persist state **before** sending, so a crash mid-broadcast doesn't cause a re-send storm.
8. `history.append_deals()` — append to `logs/latest_deals.md`.
9. `subscribers.get_active_chat_ids()` → `notifier.send_deals()` — broadcast.
10. Summary log: `raw → relevant → new sent`.

`sys.path` is patched at the top so that `bot/` is importable when the script is launched as `python src/main.py`.

### `src/config.py` — all tuneables in one place

- `DEAL_KEYWORDS` (13 terms: deal, promo, discount, free trial, lifetime, coupon, `% off`, …)
- `TOOL_KEYWORDS` (15 AI product names)
- `BOOSTED_PHRASES` (9 high-signal combos like `"claude pro deal"`)
- Source lists: `REDDIT_RSS_FEEDS` (4 subreddits), `HN_SEARCH_QUERIES` (5 queries), `RSS_FEEDS` (currently empty, reserved)
- Thresholds: `MIN_UPVOTES=10`, `MAX_HN_RESULTS_PER_QUERY=15`, `SCRAPER_SLEEP_SECONDS=1.0`, `TELEGRAM_MAX_CHARS=4000`, `HASH_CLEANUP_DAYS=90`

### `src/scrapers/` — four sources, one interface

Every scraper returns a list of dicts with the same shape:

```python
{"title": str, "url": str, "body": str, "upvotes": int, "source": str}
```

- **`reddit.py`** — `feedparser` over 4 subreddit `.rss` endpoints. No API credentials needed. Checks the `bozo` flag, dedups by URL within the run, truncates body to 300 chars, sets `upvotes=0` (RSS doesn't expose scores).
- **`hackernews.py`** — Algolia HN search API (`hn.algolia.com/api/v1/search`), one request per query, `tags=story`. Dedups by `objectID`. Falls back to the HN item permalink when a story has no external URL. **This is the only source that supplies real upvote counts** (`points`).
- **`rss_feed.py`** — generic `feedparser` loop over `RSS_FEEDS`. Currently a no-op because the list is empty (an OpenAI feed was removed in an earlier commit).
- **`bitdegree.py`** — HTML scrape of `bitdegree.org/ai/deals` with a **three-strategy cascade**: (1) `<article>` tags, (2) elements with `deal`/`card` in the class name, (3) any `<a href>` containing `/ai/`. First strategy returning results wins. A `_is_junk()` filter then strips nav noise (empty/`/` URLs, titles under 10 chars, titles starting with `faq`, `claim`, `get deal`, `how`, `why`, …). The function never raises.

`scrapers/__init__.py` exposes `run_all_scrapers()`, which:
- calls each scraper in its own `try/except` (a dead source degrades, it doesn't crash the run),
- then does a **cross-source fuzzy dedup** by normalising titles (lowercase → strip punctuation → drop stopwords like *the/a/and/of/to*) and keeping only first occurrences.

### `src/filter.py` — relevance gating

`_match()` uses **word-boundary regex** (`\b{escaped_keyword}\b`) so "cursory" can't match "cursor".

`score_deal()`: +15 per DEAL_KEYWORD, +5 per TOOL_KEYWORD, +20 per BOOSTED_PHRASE (each counted at most once).

`is_relevant()` requires **all** of:
1. ≥1 DEAL_KEYWORD match — proves deal intent
2. ≥1 TOOL_KEYWORD match — excludes TV/retail discounts
3. `score_deal() >= 15`
4. If `upvotes > 0`, it must be ≥ `MIN_UPVOTES` (10)

Note gate 4's guard: `upvotes == 0` means "no upvote data available", so Reddit/RSS/BitDegree items bypass the quality threshold entirely — only HN items are score-gated.

### `src/dedup.py` — persistent seen-deal store

`data/seen_deals.json`, shape `{"hashes": {sha256: iso_ist_timestamp}, "last_updated": iso}`.

- Both the **URL hash and the title hash** are stored for each deal, and `is_seen()` returns True if *either* is present — so a re-post of the same story under a new URL is still caught.
- Hashing normalises via `.strip().lower()` before SHA-256.
- `cleanup_old_hashes(days=90)` prunes anything older than the cutoff; unparseable timestamps are kept rather than dropped (fail-safe).
- Corrupt or missing JSON degrades to an empty store rather than raising (`JSONDecodeError`, `OSError`, `UnicodeDecodeError` all handled).
- State is persisted back to the repo by the GitHub Actions "Commit state updates" step.

### `src/history.py` — human-readable log

Appends a dated block to `logs/latest_deals.md`:

```
## Run: 2026-07-18 08:00:00 IST
- [50% off Cursor Pro](https://…) | Reddit | 👍 0
```

No-op runs write nothing.

### `src/notifier.py` — Telegram delivery

- **HTML parse mode**, not Markdown. This was a deliberate fix — Reddit slugs are full of underscores and titles contain brackets, which caused *silent* Markdown parse failures. All user text goes through `html.escape()`.
- `format_deal()` renders 🔥 title / 📌 source / 🔗 url / 👍 upvotes, terminated by `---`.
- `chunk_messages()` splits on the `---` separator so a deal is never cut across two messages, packing up to 4000 chars per message.
- `send_deals(deals, chat_ids)` is the V2 broadcast entry point:
  - formats content **once**, reuses it for every recipient;
  - iterates subscribers in batches of `BATCH_SIZE=25` with `BATCH_SLEEP=1.0s` between batches;
  - `MESSAGE_SLEEP=0.3s` after every individual send for pacing;
  - `_send_with_retry()` retries once on HTTP 429 after `RETRY_SLEEP=5.0s`;
  - HTTP 403 (user blocked the bot) → chat ID collected and passed to `subscribers.batch_deactivate()` in a single commit at the end;
  - an empty deal list still sends a "✅ ran — no new deals found" notice.
- `send_message()` is the legacy V1 single-chat sender (reads `TELEGRAM_CHAT_ID`), retained but no longer on the main path.

### `bot/bot_server.py` — Flask webhook

Two routes:

- `GET /health` → `{"status": "ok", "timestamp": "<IST>"}`. Used by Render's health check.
- `POST /webhook` → validates the `X-Telegram-Bot-Api-Secret-Token` header against `WEBHOOK_SECRET`. On mismatch returns 403; **after** the secret check it always returns HTTP 200 regardless of what happens internally, so Telegram never enters a retry flood.

Command routing:

| Command | Access | Behaviour |
|---|---|---|
| `/start` | public | `add_subscriber()` → "new" / "resubscribed" / "already_active" |
| `/stop` | public | `deactivate_subscriber()` — soft delete, sets `active: false` |
| `/help` | public | command list |
| `/run` | admin only | `POST` to GitHub `workflow_dispatch` on `deal_scout.yml`, ref `main` |
| `/status` | admin only | total / active / inactive subscriber counts |
| anything else | public | "Unknown command" |

Admin check is `chat_id != ADMIN_CHAT_ID`.

### `bot/subscribers.py` — remote subscriber store

Subscriber state lives in a **separate private GitHub repo**, accessed through the Contents API — chosen so the public repo never leaks user chat IDs, and so Render's ephemeral filesystem isn't a problem.

- `_get_file()` → GET, base64-decode, parse; caches the blob SHA. 404 → empty store; other failures → empty store + CRITICAL log.
- `_put_file(data, sha)` → PUT with base64 content and the SHA. **Exponential-backoff retry loop, max 5 attempts**, specifically for HTTP 409 conflicts from concurrent writes; on each conflict it re-fetches the current SHA before retrying (1s, 2s, 4s, 8s, 16s).
- Record shape: `{chat_id, username, subscribed_at, active, resubscribe_count}`.
- Write-avoidance: `add_subscriber` on an already-active user and `deactivate_subscriber` on an already-inactive user both return early **without** an API call.
- `batch_deactivate(chat_ids)` collapses N blocked users into a single commit.

---

## 4. Deployment topology

**GitHub Actions** (`.github/workflows/deal_scout.yml`)
- Python 3.11, `pip install -r requirements.txt`
- Env: `TELEGRAM_BOT_TOKEN`, `GH_PAT`, `GH_REPO_DATA`
- Runs `python src/main.py`
- Commits `data/seen_deals.json` and `logs/latest_deals.md` back with `[skip ci]`
- Needs `permissions: contents: write`

**Render** (`render.yaml`)
- `gunicorn bot.bot_server:app --bind 0.0.0.0:$PORT --workers 1`
- Health check path `/health`
- Env (all `sync: false`, set in dashboard): `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, `ADMIN_CHAT_ID`, `GH_PAT`, `GH_REPO_DATA`
- Single worker is intentional — the subscriber store has no cross-process locking.

**Dependencies:** `requests`, `beautifulsoup4`, `feedparser`, `pytz`, `flask`, `gunicorn` (unpinned).

---

## 5. Design decisions worth knowing

| Decision | Rationale |
|---|---|
| Dedup state persisted **before** the broadcast | A crash mid-send loses a few deals; the alternative loses nothing but risks re-spamming every subscriber |
| Hash both URL *and* title | Catches cross-posts and reposts under new URLs |
| HTML parse mode over Markdown | Markdown special chars in Reddit slugs caused silent, unlogged send failures |
| Subscribers in a separate private repo | Public repo can't leak chat IDs; Render's disk is ephemeral |
| Format message content once, fan out N times | Keeps per-user cost to one HTTP call per chunk |
| Word-boundary regex in the filter | Prevents substring false positives ("cursory" → "cursor") |
| BitDegree three-strategy cascade | Site restructures degrade to zero results instead of an exception |
| Webhook returns 200 after secret check | Telegram retries aggressively on non-2xx; internal errors shouldn't amplify |

---

## 6. Test coverage

`tests/` (pytest, run from project root; `conftest.py` puts `src/` on `sys.path`):

- `test_dedup.py` — hash store behaviour
- `test_filter.py` — scoring and relevance gates
- `test_notifier_v2.py` — broadcast fan-out, empty inputs, 403/429 handling
- `test_subscribers.py` — GitHub-backed store
- `test_bot_server.py` — webhook routing and auth

Not currently covered: the four scrapers, and `main.py` end-to-end.
