# ai-deal-scout

A Telegram bot that finds deals, discounts and free trials for AI tools — Claude, ChatGPT, Cursor, Perplexity, Copilot, Midjourney and others — and delivers them once a day.

Subscribe at **[t.me/Kkaideal_bot](https://t.me/Kkaideal_bot)** and send `/start`. Nothing to install.

**Tech stack:** Python 3.11 · requests · BeautifulSoup · feedparser · Tavily API · Telegram Bot API · GitHub Actions

A deal message looks like this:

```
🔥 50% off Cursor Pro annual plan
📌 Source: Reddit
🔗 https://reddit.com/r/deals/...
---
🔥 Perplexity Pro free for 12 months with Revolut
📌 Source: HackerNews
🔗 https://news.ycombinator.com/item?id=...
👍 143 upvotes
```

---

## What it does

Once a day (scheduled for 08:00 IST) it:

1. **Scrapes five sources** — Reddit RSS, the Hacker News search API, BitDegree's AI deals page, Product Hunt's RSS, and the open web via Tavily search
2. **Filters for relevance** — keyword gates with a negative-keyword veto, measured against a 764-row labelled evaluation set (see [Results](#results))
3. **Deduplicates** — SHA-256 fingerprints of URL and title, with a 90-day memory
4. **Delivers to Telegram** — batched, rate-limited, with blocked users auto-deactivated

The web-search stage is what makes it useful: it queries vendors' own announcement pages, so a promo that never reaches a forum is still found.

```
 Reddit RSS ─┐
 Hacker News ┤
 Product Hunt┼─▶  Filter  ─▶  Dedup  ─▶  Telegram
 BitDegree   ┤   (relevance)  (90 days)   (subscribers)
 Tavily search┘
```

## Results

Measured with `python tools/eval_filter.py` on the 764-row labelled set (63 real deals, 701 non-deals):

| Precision | Recall | F1 | TP | FP | FN |
|---|---|---|---|---|---|
| 0.983 | 0.889 | 0.933 | 56 | 1 | 7 |

An audit of the labels moved 15 rows (the audit trail is in the `notes` column). Re-scoring the unchanged filter against the corrected labels gave precision 0.889, recall 0.889 and F1 0.889, so the earlier 0.968 / 0.897 / 0.931 was flattered by labels that counted deal directories and plain free trials as deals. The numbers above come after four general filter rules written in response; they were chosen after seeing the eval failures, so they are optimistic.

The filter was tuned against this same set, so treat these as in-sample figures, not a forecast for unseen posts. A test fails if precision or recall falls below a loose floor (0.55 / 0.70), so large regressions can't slip through.

## Bot commands

| Command | What it does |
|---|---|
| `/start` | Subscribe |
| `/stop` | Unsubscribe |
| `/help` | Show commands |
| `/run` | Trigger a run now (admin and allowlisted testers, rate limited) |
| `/status` | Subscriber counts (admin only) |
| `/quota` | Search credits used this month (admin and allowlisted testers) |

---

## How it's built

Two halves that share state through a private repo, so nothing needs to be always-on:

| Half | Runs on | When |
|---|---|---|
| Deal pipeline (`src/`) | GitHub Actions | once a day, scheduled for 08:00 IST |
| Telegram command checker (`bot/drain.py`) | GitHub Actions | scheduled every 15 minutes |

No server, no VPS. Telegram queues messages for 24 hours, so polling on a schedule catches every `/start` without anything running continuously. The cost is a wait for the `/start` confirmation. GitHub Actions starts scheduled jobs late or skips them under load, so in practice the checker runs every few hours and the daily run lands a few hours after 08:00 IST.

Render is an optional alternative: `render.yaml` and `bot/bot_server.py` can serve the same commands over a webhook if you want instant replies. It isn't needed or used by the default setup.

```
src/
  main.py          orchestrator
  config.py        keyword lists, sources, thresholds
  filter.py        relevance scoring and gating
  dedup.py         seen-deal store
  notifier.py      Telegram formatting and broadcast
  alerts.py        operator alerts
  history.py       markdown run log
  remote_state.py  JSON state in the private data repo (or local disk)
  scrapers/        reddit · hackernews · rss_feed · bitdegree · websearch
bot/
  bot_server.py    command handlers (shared by all entry points); optional Render webhook
  drain.py         single-pass poller for CI
  poll.py          long-poller for local testing
  subscribers.py   subscriber store (private repo, or local file)
tools/
  eval_filter.py       precision/recall harness
  label.py             export deals to LLMs, merge their votes into the eval set
  verify_setup.py      checks every credential with a real API call
  preflight_public.py  secret-leak gate before going public
```

---

## Running it yourself

```bash
git clone https://github.com/KiranKri/ai-deal-scout.git
cd ai-deal-scout
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | From [@BotFather](https://t.me/BotFather) |
| `TAVILY_API_KEY` | no | Web search — free tier at [tavily.com](https://tavily.com). Without it the other four sources still run |
| `GH_PAT` | no | Fine-grained token: Contents read+write on your private data repo, plus Actions read+write on this repo (needed for `/run`) |
| `GH_REPO_DATA` | no | `owner/repo` for subscriber storage. Without it, subscribers save to a local file |
| `ADMIN_CHAT_ID` | no | Your chat ID — enables admin commands and failure alerts |
| `RUN_ALLOWLIST` | no | Extra chat IDs (comma-separated) allowed to use `/run` and `/quota` |

Verify everything works:

```bash
python tools/verify_setup.py
```

Then:

```bash
python src/main.py      # run the pipeline
python bot/poll.py      # answer Telegram commands locally
pip install pytest && pytest tests/ -q   # 282 tests
```

With zero subscribers the pipeline runs in **dry-run mode** — it scrapes, filters and logs to `logs/latest_deals.md` but marks nothing as seen, so no deals are lost before your first subscriber.

---

## Deploying

Add these as repository secrets under **Settings → Secrets and variables → Actions**:

`TELEGRAM_BOT_TOKEN` · `TAVILY_API_KEY` · `GH_PAT` · `GH_REPO_DATA` · `ADMIN_CHAT_ID` · `RUN_ALLOWLIST` (optional)

Then create a **private** repo for subscriber data containing one file, `subscribers.json`:

```json
{"subscribers": [], "last_updated": ""}
```

Subscriber chat IDs are personal data and must never live in this public repo. `tools/preflight_public.py` enforces that — run it before making any fork public.

---

## Tuning what gets found

Everything lives in `src/config.py`: keyword lists, the negative-keyword veto, source URLs, spam-domain blocklist, and search budget.

After changing anything, measure it:

```bash
python tools/eval_filter.py --compare
```

That scores the filter against `data/eval_set.csv` and shows the delta from the saved baseline, including which deals were missed and which junk got through. A test enforces precision and recall floors, so a badly loose keyword can't silently regress the filter.

## How the evaluation set was labelled

`data/eval_set.csv` is only useful if its labels are trustworthy, so new rows aren't labelled by a single model:

1. `python tools/label.py export` prints recent deals with a fixed grading prompt (1 = real, claimable offer on an AI tool; 0 = anything else).
2. The same prompt is pasted into four LLMs — DeepSeek, Gemini, Grok and Kimi — each replying with only `N: 0|1` lines, saved as `data/labels/labels_<model>.txt`.
3. `python tools/label.py merge data/labels/labels_*.txt` takes a vote per item. Unanimous items and clear majorities go into the set, with the vote recorded in the `notes` column (`consensus 4/4`, `majority 3/4`). An even split is left out and printed for a human to decide.

In the current set, 47 rows are 4/4 consensus and 14 are 3/4 majority. Most of the remainder (639 rows) are hard negatives, such as real discounts on non-AI products.

## Search budget

Tavily's free tier is 1,000 credits/month. The bot uses 12 per run, ~360/month, capped at 900. Five independent limits — per-run query cap, wall-clock cap, results per query, a persisted monthly counter, and early stop on empty results — mean no single failure can burn the quota. Below 150 credits remaining it drops to vendor-site searches only rather than switching off.

---

## License

MIT

Built with AI-assisted coding.
