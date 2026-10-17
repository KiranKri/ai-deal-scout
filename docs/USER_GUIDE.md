# AI Deal Scout — How to Use It

Two audiences here. Jump to yours.

- **[Part A — Subscribers](#part-a--for-subscribers)** (you just want the deals)
- **[Part B — Operators](#part-b--for-operators)** (you're deploying and running it)

---

# Part A — For subscribers

## Subscribe

1. Open Telegram and go to [t.me/Kkaideal_bot](https://t.me/Kkaideal_bot) (or open the invite link the operator gave you).
2. Send `/start`.
3. You'll get a confirmation once the next scheduled check runs. Commands are checked on a schedule, not instantly, and GitHub often runs that schedule late, so it can take a few hours. Deals arrive **once a day**; the run is scheduled for 08:00 IST and usually lands a few hours after that.

## Commands

| Command | What it does |
|---|---|
| `/start` | Subscribe, or resubscribe if you'd previously stopped |
| `/stop` | Unsubscribe — you stay in the database but stop receiving messages |
| `/help` | Show the command list |

## What a deal message looks like

```
🤖 AI Deal Scout — 3 new deal(s) found:

🔥 50% off Cursor Pro annual plan
📌 Source: Reddit
🔗 https://reddit.com/r/deals/...
---
🔥 Perplexity Pro free for 12 months with Revolut
📌 Source: HackerNews
🔗 https://news.ycombinator.com/item?id=...
👍 143 upvotes
---
```

If a run finds nothing, you'll still get a short `✅ AI Deal Scout ran — no new deals found.` so you know the bot is alive.

## Things to know

- **You won't get the same deal twice.** Deals are fingerprinted by URL and title and remembered for 90 days.
- **Long batches arrive as several messages.** Messages are capped at 4000 characters and split on deal boundaries, so no deal is cut in half.
- **If you block the bot, it notices.** After one blocked delivery your account is auto-deactivated so the bot stops trying.
- **Deals aren't verified.** The bot surfaces what it finds on Reddit, Hacker News, Product Hunt, BitDegree and the open web. Check the source and the vendor's own page before you buy anything.

---

# Part B — For operators

The default setup needs no server: two GitHub Actions workflows do everything. A Render webhook server is an optional alternative, covered last in [B9](#b9-optional-webhook-server-on-render).

| Workflow | Runs | Does |
|---|---|---|
| `deal_scout.yml` | Once a day, 08:00 IST | Scrape → filter → dedup → broadcast |
| `telegram_drain.yml` | Scheduled every 15 minutes (GitHub often runs it less often) | Answers `/start`, `/stop`, `/help`, `/run`, `/quota`, `/status` |

## Prerequisites

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A GitHub account (for Actions and the private data repo)
- Optional: a [Tavily](https://tavily.com) API key for web search (free tier, no card). Without it the other four sources still run.

---

## B1. Local development

```bash
git clone https://github.com/KiranKri/ai-deal-scout.git
cd ai-deal-scout

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` (see the variable table in the [README](../README.md#running-it-yourself)), then check every credential with a real API call:

```bash
python tools/verify_setup.py
```

Run the pipeline without sending anything or marking anything seen:

```bash
python src/main.py --dry-run --limit 20
```

A normal run (`python src/main.py`) with no active subscribers is also a dry run. With subscribers it broadcasts and records what it sent. The final log line shows the funnel:

```
Run complete: 187 raw → 12 relevant → 4 new → delivered to 43/43 subscribers
```

Outputs:
- `logs/latest_deals.md` — human-readable history of deals actually sent
- `seen_deals.json` — the SHA-256 fingerprint store. In the private data repo when `GH_PAT` and `GH_REPO_DATA` are set; otherwise `data/seen_deals.json` (git-ignored)

Answer Telegram commands locally with `python bot/poll.py`.

Run the tests:

```bash
pip install pytest
pytest tests/ -q
```

---

## B2. One-time setup: the private data repo

Machine state lives in a **separate private repo** so the public repo never exposes chat IDs.

1. Create a private repo, e.g. `KiranKri/ai-deal-scout-data`.
2. Add a file at its root called `subscribers.json` containing exactly:

   ```json
   {"subscribers": [], "last_updated": ""}
   ```

   (`seen_deals.json` is created automatically on the first run.)

3. Create a **fine-grained Personal Access Token** with:
   - Repository access: the private data repo **and** the main `ai-deal-scout` repo
   - Permissions: `Contents: Read and write` (data repo), `Actions: Read and write` (main repo, needed for `/run`)
4. Note the token — this is your `GH_PAT`.

`bot/drain.py` refuses to run in CI without `GH_PAT` and `GH_REPO_DATA`, so subscriber IDs can't be committed into the public repo by mistake.

---

## B3. Configure GitHub Actions

In the main repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your BotFather token |
| `GH_PAT` | The PAT from B2 |
| `GH_REPO_DATA` | `owner/repo` of the private data repo |
| `ADMIN_CHAT_ID` | Your numeric Telegram chat ID — enables admin commands and failure alerts |
| `TAVILY_API_KEY` | Optional — enables web search |
| `RUN_ALLOWLIST` | Optional — extra chat IDs (comma-separated) allowed to use `/run` and `/quota` |

**Finding your `ADMIN_CHAT_ID`:** message [@userinfobot](https://t.me/userinfobot) on Telegram.

Both workflows declare `permissions: contents: write` so they can commit their small state files back (`data/websearch_state.json`, `logs/latest_deals.md`, `data/telegram_offset.json`, `data/run_quota.json`).

**Schedules** (cron is UTC, so subtract 5h30m from your intended IST time):

- Pipeline: `cron: '30 2 * * *'` in `.github/workflows/deal_scout.yml` = 08:00 IST
- Command checker: `cron: '*/15 * * * *'` in `.github/workflows/telegram_drain.yml`. Each run costs Actions minutes, which are unlimited on public repos but capped at 2,000/month on private ones, so don't poll much more often than this on a private repo.

---

## B4. Running the pipeline

**Three ways:**

1. **Automatic** — the cron is scheduled daily for 08:00 IST (GitHub may start it a few hours late).
2. **From GitHub** — Actions tab → *AI Deal Scout* → **Run workflow**.
3. **From Telegram** — send `/run`. Allowed for the admin and chat IDs in the `RUN_ALLOWLIST` secret; you'll get `✅ Pipeline triggered. Check Telegram in ~2 mins.`

Runs never overlap: a concurrency group queues a second run behind the first.

---

## B5. Admin commands

| Command | Who | Response |
|---|---|---|
| `/run` | Admin and `RUN_ALLOWLIST` | Dispatches `deal_scout.yml` on `main`. Limited to 1 per user per hour and 10 per IST day |
| `/quota` | Admin and `RUN_ALLOWLIST` | Tavily credits used this month, and today's `/run` count |
| `/status` | Admin only | Total / active / inactive subscriber counts |

Everyone else gets `Unauthorized.`

---

## B6. Tuning what gets found

Everything lives in `src/config.py`.

**Add a new AI tool to track:** add it to `TOOL_KEYWORDS`.
**Add a new deal phrasing:** add it to `DEAL_KEYWORDS` (base form only; inflections are handled).
**Block a junk phrase:** add it to `NEGATIVE_KEYWORDS`.
**Add a subreddit:** add its `.../new.rss` URL to `REDDIT_RSS_FEEDS`.
**Add a feed:** add it to `RSS_FEEDS` (currently the Product Hunt AI feed).
**Block a spam domain:** add it to `BLOCKED_DOMAINS`.

**Threshold knobs:**

| Setting | Default | Effect |
|---|---|---|
| `MIN_UPVOTES` | 0 (off) | When raised, drops posts below that score. Reddit/RSS/web results report 0 upvotes, so enabling it would exclude them |
| `MAX_HN_RESULTS_PER_QUERY` | 30 | More Hacker News candidates per query, slower run |
| `SCRAPER_SLEEP_SECONDS` | 1.0 | Politer to sources, slower run |
| `HASH_CLEANUP_DAYS` | 90 | Longer memory, bigger `seen_deals.json` |
| `TELEGRAM_MAX_CHARS` | 4000 | Telegram's hard limit is 4096 — don't exceed 4000 |
| `DROP_STALE` | `True` | Drops deals that look expired or out of season |
| `WEBSEARCH_MAX_QUERIES_PER_RUN` | 12 | Tavily credits spent per run |
| `WEBSEARCH_MONTHLY_QUOTA` | 900 | Hard monthly cap (free tier is 1,000) |

**After changing anything, measure it:**

```bash
python tools/eval_filter.py --compare
```

This scores the filter against `data/eval_set.csv` and diffs against `data/eval_baseline.json`, listing missed deals and junk that got through. If the result is an improvement you want to keep, run `python tools/eval_filter.py --baseline` to save it. Then do a `python src/main.py --dry-run` and read the output before the next cron — a loose keyword can flood every subscriber.

**Adding labelled examples:** see "How the evaluation set was labelled" in the [README](../README.md). Prompt and reply files are kept in `data/labels/`.

---

## B7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/start` gets no reply | The command checker hasn't run yet (scheduled every 15 minutes, but GitHub can delay it by hours), or its workflow is failing | Actions tab → *Telegram Commands* → latest run |
| Command checker fails immediately | `GH_PAT` / `GH_REPO_DATA` not set, so it refuses to store subscribers in the public repo | Add both secrets (B3) |
| Replies arrive but nobody is stored | `GH_PAT` lacks Contents write on the data repo, or `GH_REPO_DATA` is wrong | Check the drain log for `GitHub API` errors |
| Pipeline run aborts | Subscriber store unreachable — it stops before marking anything seen, and alerts the admin | Fix the PAT/repo; the next run retries |
| Workflow runs, no Telegram messages | Zero active subscribers | Log shows `No active subscribers — DRY RUN` |
| `/run` says "Failed to trigger pipeline" | PAT lacks `Actions: write` on the main repo, or the branch isn't `main` | The handler hardcodes `{"ref": "main"}` |
| Same deals sent repeatedly | `seen_deals.json` wasn't persisted to the data repo | Check for `save` errors in the run log |
| Deals found but not delivered | Delivery failed for everyone — deals aren't marked seen, so they retry next run, and the admin gets an alert | Check the `Telegram error` lines |
| Zero web-search results | `TAVILY_API_KEY` missing, or the monthly quota reached | `/quota`; the log says which |
| Zero deals for several consecutive runs | A source changed or keywords are too narrow | Check per-source `raw results` lines in the log |

**Where to look:**
- Pipeline: GitHub → Actions → *AI Deal Scout* → the run
- Commands: GitHub → Actions → *Telegram Commands* → the run
- Deal history: `logs/latest_deals.md`

---

## B8. Resetting state

**Forget all seen deals** (they'll be re-sent on the next run — do this only if subscribers are few or you've muted the broadcast): edit `seen_deals.json` in the private data repo to

```json
{"hashes": {}, "last_updated": ""}
```

For local runs without a data repo, the file is `data/seen_deals.json` and is not tracked by git.

**Clear the deal history log:**

```bash
: > logs/latest_deals.md
```

**Wipe all subscribers:** edit `subscribers.json` in the private data repo back to `{"subscribers": [], "last_updated": ""}`.

---

## B9. Optional: webhook server on Render

Only needed if you want instant replies instead of waiting for the next scheduled poll. Don't run it alongside the drain workflow answering the same bot: Telegram won't deliver `getUpdates` while a webhook is set.

`render.yaml` is in the repo, so Render picks up the config on import.

1. In Render: **New → Web Service → connect the repo**.
2. Set these environment variables in the dashboard (all `sync: false`):

   | Variable | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | Your BotFather token |
   | `WEBHOOK_SECRET` | A random string you invent — e.g. `openssl rand -hex 32` |
   | `ADMIN_CHAT_ID` | Your numeric Telegram chat ID |
   | `GH_PAT` | The PAT from B2 |
   | `GH_REPO_DATA` | `owner/repo` of the private data repo |

3. Deploy and confirm it's alive:

   ```bash
   curl https://your-service.onrender.com/health
   ```

   > `render.yaml` sets `--workers 1` on purpose — the subscriber store has no cross-process locking. Leave it at 1.

4. Register the webhook with the same secret:

   ```bash
   curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://your-service.onrender.com/webhook", "secret_token": "<WEBHOOK_SECRET>"}'
   curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
   ```

   The `secret_token` **must** match `WEBHOOK_SECRET` exactly, otherwise every update gets a 403 and the bot appears dead.

5. To go back to the default setup, call `deleteWebhook` and let the drain workflow resume.

The free Render tier idles after inactivity, so the first webhook after a cold start may be delayed or dropped.

---

## Security notes

- `.env` is git-ignored and must never be committed. If a token leaks, revoke it in BotFather (`/revoke`) and GitHub immediately.
- `GH_PAT` should be fine-grained and scoped to exactly the two repos it needs — never a classic token with full `repo` scope.
- Subscriber chat IDs are personal data and belong only in the private data repo. Run `python tools/preflight_public.py` before making any fork public.
- If you use the Render webhook, `/webhook` is protected only by the shared secret header. Rotate `WEBHOOK_SECRET` by updating Render's env var **and** re-running `setWebhook`, in that order.
