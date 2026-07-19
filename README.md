# ai-deal-scout

A Telegram bot that finds deals, discounts and free trials for AI tools — Claude, ChatGPT, Cursor, Perplexity, Copilot, Midjourney and others — and delivers them once a day.

Subscribe at **[t.me/Kkaideal_bot](https://t.me/Kkaideal_bot)** and send `/start`. Nothing to install.

---

## What it does

Every morning at 08:00 IST it:

1. **Scrapes five sources** — Reddit RSS, the Hacker News search API, BitDegree's AI deals page, Product Hunt's RSS, and the open web via Tavily search
2. **Filters for relevance** — keyword scoring with a negative-keyword veto, measured at **0.978 precision / 0.978 recall** against a 725-row labelled evaluation set
3. **Deduplicates** — SHA-256 fingerprints of URL and title, with a 90-day memory
4. **Delivers to Telegram** — batched, rate-limited, with blocked users auto-deactivated

The web-search stage is what makes it useful: it queries vendors' own announcement pages, so a promo that never reaches a forum is still found.

## Bot commands

| Command | What it does |
|---|---|
| `/start` | Subscribe |
| `/stop` | Unsubscribe |
| `/help` | Show commands |
| `/run` | Trigger a run now (rate limited) |
| `/status` | Subscriber counts |
| `/quota` | Search credits used this month |

---

## How it's built

Two halves that share state through a private repo, so nothing needs to be always-on:

| Half | Runs on | When |
|---|---|---|
| Deal pipeline (`src/`) | GitHub Actions | 08:00 IST daily |
| Telegram commands (`bot/drain.py`) | GitHub Actions | every 10 minutes |

No server, no VPS. Telegram queues messages for 24 hours, so polling on a schedule catches every `/start` without anything running continuously.

```
src/
  main.py          orchestrator
  config.py        keyword lists, sources, thresholds
  filter.py        relevance scoring and gating
  dedup.py         seen-deal store
  notifier.py      Telegram formatting and broadcast
  alerts.py        operator alerts
  history.py       markdown run log
  scrapers/        reddit · hackernews · rss_feed · bitdegree · websearch
bot/
  bot_server.py    command handlers (shared by all entry points)
  drain.py         single-pass poller for CI
  poll.py          long-poller for local testing
  subscribers.py   subscriber store (private repo, or local file)
tools/
  eval_filter.py       precision/recall harness
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
| `GH_PAT` | no | Fine-grained token, Contents read+write on your private data repo |
| `GH_REPO_DATA` | no | `owner/repo` for subscriber storage. Without it, subscribers save to a local file |
| `ADMIN_CHAT_ID` | no | Your chat ID — enables admin commands and failure alerts |

Verify everything works:

```bash
python tools/verify_setup.py
```

Then:

```bash
python src/main.py      # run the pipeline
python bot/poll.py      # answer Telegram commands locally
pytest tests/ -q        # 254 tests
```

With zero subscribers the pipeline runs in **dry-run mode** — it scrapes, filters and logs to `logs/latest_deals.md` but marks nothing as seen, so no deals are lost before your first subscriber.

---

## Deploying

Add these as repository secrets under **Settings → Secrets and variables → Actions**:

`TELEGRAM_BOT_TOKEN` · `TAVILY_API_KEY` · `GH_PAT` · `GH_REPO_DATA` · `ADMIN_CHAT_ID`

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

That scores the filter against `data/eval_set.csv` and shows the delta from the saved baseline, including which deals were missed and which junk got through. A test enforces a precision floor, so a loose keyword can't silently regress the filter.

## Search budget

Tavily's free tier is 1,000 credits/month. The bot uses 12 per run, ~360/month, capped at 900. Five independent limits — per-run query cap, wall-clock cap, results per query, a persisted monthly counter, and early stop on empty results — mean no single failure can burn the quota. Below 150 credits remaining it drops to vendor-site searches only rather than switching off.

---

## License

MIT
