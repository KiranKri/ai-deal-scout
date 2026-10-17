# AI Deal Scout — Architecture

**Last updated:** 2026-10-17

**Tech stack:** Python 3.11 · requests · BeautifulSoup · feedparser · Tavily API · Telegram Bot API · GitHub Actions

Built with AI-assisted coding.

---

## 1. What it does

AI Deal Scout finds **deals, discounts, promo codes and free trials on AI tools** (Claude, ChatGPT, Cursor, Perplexity, Copilot, Midjourney and others) and sends them to Telegram subscribers once a day.

It runs as two scheduled GitHub Actions workflows. There is no server.

| Half | Entry point | Workflow | Schedule |
|---|---|---|---|
| **Deal pipeline** | `src/main.py` | `deal_scout.yml` | Once a day, cron `30 2 * * *` UTC = 08:00 IST (plus manual dispatch) |
| **Telegram command checker** | `bot/drain.py` | `telegram_drain.yml` | Every 15 minutes (`*/15 * * * *`) |

Telegram queues undelivered updates for 24 hours, so polling on a schedule picks up every `/start` and `/stop` without anything running continuously. A new subscriber waits up to one polling interval for their confirmation.

**Render is an optional alternative.** `render.yaml` and the Flask app in `bot/bot_server.py` can serve the same commands over a webhook for instant replies. The default deployment does not use it.

The two halves share state only through a **private GitHub repo** (`GH_REPO_DATA`) holding `subscribers.json` and `seen_deals.json`.

---

## 2. Flow

```
 Reddit RSS ──┐
 Hacker News ─┤
 Product Hunt ┼─▶ cross-source ─▶ Filter ─▶ Dedup ─▶ Telegram
 BitDegree ───┤   title dedup    (relevance,  (SHA-256,  (batched
 Tavily search┘   + domain       staleness)   90 days)   broadcast)
                  blocklist
```

Detailed pipeline order in `src/main.py`:

1. Fetch active subscribers **strictly**. If the private store is unreachable the run aborts and alerts the admin *before* any deal is marked seen.
2. `dedup.cleanup_old_hashes()` — drop hashes older than 90 days.
3. `run_all_scrapers()` — each scraper isolated in its own `try/except`; then a domain blocklist and a cross-source title dedup.
4. `filter.is_relevant()` — keep deal-intent posts about AI tools.
5. `filter.is_stale()` — drop expired or out-of-season deals (`DROP_STALE`).
6. `dedup.is_seen()` — keep only deals not sent before.
7. `notifier.send_deals()` — broadcast.
8. **Only if at least one subscriber received the batch**: `mark_seen()` + `dedup.save()`, then `history.append_deals()`.

With zero subscribers, or with `--dry-run`, the run is a **dry run**: it scrapes and filters but marks nothing seen and sends nothing, so no deals are lost before the first subscriber joins.

---

## 3. Modules

### Sources — `src/scrapers/`

Every scraper returns dicts shaped `{"title", "url", "body", "upvotes", "source"}`.

| Module | Source | Notes |
|---|---|---|
| `reddit.py` | Reddit RSS (5 subreddits) | `feedparser`, no credentials; RSS has no scores, so `upvotes=0` |
| `hackernews.py` | Algolia HN search API (16 queries) | Generic and tool-name-led queries; the only source with real points |
| `rss_feed.py` | Product Hunt AI feed | Generic `feedparser` loop over `RSS_FEEDS` |
| `bitdegree.py` | BitDegree AI deals page | HTML scrape with a three-strategy fallback cascade and a junk filter; never raises |
| `websearch.py` | Tavily Search API | Vendor-site queries plus rotating long-tail tools |

`scrapers/__init__.py` runs them all, drops results from blocklisted spam domains, and removes duplicates by normalised title.

**Web search budget.** Tavily's free tier is 1,000 credits/month. A run uses at most 12 (10 vendor-site queries + 2 rotating tools). Five independent limits stop a failure from burning the quota: a per-run query cap, a wall-clock cap, results per query, a persisted monthly counter (cap 900), and early stop after consecutive empty queries. Below 150 credits remaining it searches vendor sites only. Without `TAVILY_API_KEY` it returns nothing and the other sources still run. State is kept in `data/websearch_state.json`.

### `src/filter.py` — relevance

Matching is word-boundary regex, so "cursory" cannot match "cursor". `is_relevant()` requires:

1. No `NEGATIVE_KEYWORDS` hit (hard veto).
2. A title containing `?` also needs a strong price signal (keeps support questions out).
3. `Show HN:` titles need a strong price signal.
4. At least one `DEAL_KEYWORDS` match.
5. At least one `TOOL_KEYWORDS` match, or a URL host that names a known tool (accepted only alongside a strong price signal).
6. Not a bare "free plan" pricing page.
7. Titles on known news domains need a strong price signal.
8. `MIN_UPVOTES` gate (currently 0, disabled).

`score_deal()` is computed for logging and ranking only. `is_stale()` flags years before the current year and Black Friday/Cyber Monday outside October–December. All lists and thresholds live in `src/config.py`.

### `src/dedup.py` and `src/remote_state.py` — seen-deal store

`seen_deals.json` holds `{"hashes": {sha256: iso_ist_timestamp}, "last_updated": iso}`. Both the **URL hash and the title hash** are stored, and `is_seen()` is true if either matches, so a repost under a new URL is still caught. Hashes are pruned after 90 days; corrupt or missing state degrades to an empty store.

`remote_state.py` reads and writes this file through the GitHub Contents API (with SHA-based conflict retry) when `GH_PAT` and `GH_REPO_DATA` are set, and falls back to local disk otherwise. The file is deliberately not tracked in the public repo.

### `src/notifier.py` — Telegram delivery

- HTML parse mode with `html.escape()` on all text. Markdown caused silent failures on Reddit slugs full of underscores and brackets.
- `format_deal()` renders 🔥 title / 📌 source / 🔗 URL / 👍 upvotes (when known), ending in `---`.
- `chunk_messages()` splits on `---` so a deal is never cut across messages (4,000-character limit).
- `send_deals()` formats once, then sends to subscribers in batches of 25 with pacing sleeps. It retries once on HTTP 429, and collects HTTP 403 (bot blocked) users for a single `batch_deactivate()` commit.
- Returns `(delivered, total)`; `main.py` uses it to decide whether to mark deals seen.

### `src/alerts.py` and `src/history.py`

`alert_admin()` sends operational alerts (store unreachable, zero scraper results, zero deliveries) to `ADMIN_CHAT_ID`. It is best-effort and never raises. `history.append_deals()` appends a dated block to `logs/latest_deals.md`.

### `bot/` — Telegram commands

- `drain.py` makes one non-blocking `getUpdates` pass, dispatches each command, and exits. Its read cursor is saved to `data/telegram_offset.json` and committed back by the workflow, so commands are not replayed. In CI it refuses to run without the private data repo configured, so subscriber IDs cannot be written into the public repo.
- `bot_server.py` holds the command handlers shared by every entry point, plus an optional Flask webhook (`/health`, `/webhook`) for Render.
- `poll.py` is a long-poller for local testing.
- `subscribers.py` stores subscribers in the private repo through the Contents API, with exponential-backoff retry on HTTP 409 conflicts and early returns that avoid redundant writes.

| Command | Access |
|---|---|
| `/start`, `/stop`, `/help` | Anyone |
| `/run` | Admin and `RUN_ALLOWLIST`; dispatches `deal_scout.yml`; 1 per user per hour, 10 per IST day (state in `data/run_quota.json`) |
| `/quota` | Admin and `RUN_ALLOWLIST`; Tavily credits used this month |
| `/status` | Admin only; subscriber counts |

### `tools/`

`eval_filter.py` (precision/recall harness), `label.py` (LLM labelling and voting), `verify_setup.py` (checks each credential with a real API call), `preflight_public.py` (secret-leak gate before making a fork public).

---

## 4. How the evaluation set is labelled

`data/eval_set.csv` (764 rows: 68 real deals, 696 non-deals) is the ground truth the filter is measured against. To avoid trusting a single model's opinion, new rows are labelled by vote (`tools/label.py`):

1. `export` prints recent deals under a fixed grading prompt: 1 = a real, currently claimable offer on an AI tool; 0 = anything else (pricing pages, support threads, questions, launches without an offer, news, tutorials, expired offers).
2. The same prompt is given to four LLMs — DeepSeek, Gemini, Grok and Kimi — each answering only `N: 0|1` lines, saved as `data/labels/labels_<model>.txt`.
3. `merge` counts votes per item. Unanimous items and strict majorities are appended to the eval set with the tally in `notes` (`consensus 4/4`, `majority 3/4`). An even split is not written; it is printed for a human to decide.

The set currently has 47 rows at 4/4 consensus and 14 at 3/4 majority. 639 rows are hard negatives (real discounts on non-AI products, and similar). The merge step also prints the unanimous-agreement rate and warns below 60%, which would indicate an ambiguous prompt.

The eval run (`python tools/eval_filter.py`) currently reports precision 0.968, recall 0.897, F1 0.931 (61 TP, 2 FP, 7 FN). The filter was tuned against this set, so these are in-sample figures. A test (`tests/test_eval_floor.py`) fails if precision or recall drops below a fixed floor (0.55 and 0.70), which is deliberately loose and only catches large regressions.

---

## 5. Deployment

**`deal_scout.yml`** — Python 3.11, `pip install -r requirements.txt`, runs `python src/main.py` with `TELEGRAM_BOT_TOKEN`, `GH_PAT`, `GH_REPO_DATA`, `ADMIN_CHAT_ID` and `TAVILY_API_KEY`. It commits `data/websearch_state.json` and `logs/latest_deals.md` back with `[skip ci]`. A concurrency group queues runs rather than overlapping them, so a manual `/run` during the cron run cannot double-broadcast.

**`telegram_drain.yml`** — Python 3.11 with a pip cache, installs only `requirements-bot.txt` (`requests`, `python-dotenv`), runs `python bot/drain.py`, and commits `data/telegram_offset.json` and `data/run_quota.json`. Same queueing concurrency group.

**Render (optional)** — `render.yaml` runs `gunicorn bot.bot_server:app` with one worker (the subscriber store has no cross-process locking) and a `/health` check. Not required.

---

## 6. Design decisions

| Decision | Rationale |
|---|---|
| Mark deals seen only after confirmed delivery | Marking first silently consumed 89 real deals when a stale subscriber ID made every send fail. The reverse failure is a visible duplicate, not silent loss |
| Fetch subscribers before touching dedup state | An unreachable store aborts the run instead of burning the day's deals |
| Hash both URL and title | Catches cross-posts and reposts under new URLs |
| Machine state in a private repo | Keeps chat IDs out of the public repo and avoids merge conflicts with the frequent drain commits |
| Scheduled polling instead of a webhook server | No always-on host; the trade-off is a delay of up to 15 minutes on `/start` confirmation |
| Poll every 15 minutes | Each run costs Actions minutes; private repos are capped at 2,000/month, and 10-minute polling would exhaust that in about 17 days |
| HTML parse mode over Markdown | Markdown special characters in Reddit slugs caused silent send failures |
| Vendor-site search weighted over open-web search | Measured about 3.75 deals/credit versus about 1.75, and the open-web side was the spam surface |
| Precision favoured over recall for staleness drops | A junk message costs subscriber trust; a missed deal is invisible |
| Several LLMs label by vote | One model's labels are one opinion; disagreement marks the genuinely ambiguous cases |

---

## 7. Tests

`pytest tests/ -q` — **282 tests** across 14 files in `tests/`: dedup, filter (including an overfit guard), eval precision floor, web search, notifier broadcast, subscribers, bot server, run allowlist and rate limits, local store and poller, entry-point parity, staleness and rotation, slim dependencies, and review-fix regressions.
