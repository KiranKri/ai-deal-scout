# AI Deal Scout — How to Use It

Two audiences here. Jump to yours.

- **[Part A — Subscribers](#part-a--for-subscribers)** (you just want the deals)
- **[Part B — Operators](#part-b--for-operators)** (you're deploying and running it)

---

# Part A — For subscribers

## Subscribe

1. Open Telegram, search for the AI Deal Scout bot (or open the invite link the operator gave you).
2. Send `/start`.
3. You'll get a confirmation. Deals arrive **twice daily at 08:00 and 20:00 IST**.

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

- **You'll never get the same deal twice.** Deals are fingerprinted by URL and title for 90 days.
- **Long batches arrive as several messages.** Messages are capped at 4000 characters and split on deal boundaries, so no deal is ever cut in half.
- **If you block the bot, it notices.** After one blocked delivery your account is auto-deactivated so the bot stops trying.
- **Deals aren't verified.** The bot surfaces what it finds on Reddit, Hacker News, and BitDegree. Check the source and the vendor's own page before you buy anything.

---

# Part B — For operators

## Prerequisites

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A GitHub account (for the Actions cron and the private data repo)
- A Render account, or any host that can run a Flask app (for the webhook server)

---

## B1. Local development

```bash
git clone https://github.com/KiranKri/ai-deal-scout.git
cd ai-deal-scout

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

Create your `.env`:

```bash
cp .env.example .env
```

```ini
TELEGRAM_BOT_TOKEN=123456:ABC-your-token-here
TELEGRAM_CHAT_ID=your_own_chat_id      # legacy V1 single-chat path only
```

Run the pipeline once:

```bash
python src/main.py
```

Watch the log line at the end — it tells you the funnel:

```
Run complete: 187 raw → 12 relevant → 4 new sent
```

Outputs land in:
- `logs/latest_deals.md` — human-readable deal history
- `data/seen_deals.json` — the SHA-256 fingerprint store

Run the tests:

```bash
pip install pytest
pytest tests/ -v
```

---

## B2. One-time setup: the private data repo

Subscriber data lives in a **separate private repo** so the public repo never exposes chat IDs.

1. Create a private repo, e.g. `KiranKri/ai-deal-scout-data`.
2. Add a file at its root called `subscribers.json` containing exactly:

   ```json
   {"subscribers": [], "last_updated": ""}
   ```

3. Create a **fine-grained Personal Access Token** with:
   - Repository access: the private data repo **and** the main `ai-deal-scout` repo
   - Permissions: `Contents: Read and write` (data repo), `Actions: Read and write` (main repo, needed for `/run`)
4. Note the token — this is your `GH_PAT`.

---

## B3. Deploy the webhook server (Render)

`render.yaml` is already in the repo, so Render will pick up the config on import.

1. In Render: **New → Web Service → connect the repo**. It reads `render.yaml`.
2. Set these environment variables in the dashboard (all are `sync: false`, meaning Render won't read them from the file):

   | Variable | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | Your BotFather token |
   | `WEBHOOK_SECRET` | A random string you invent — e.g. `openssl rand -hex 32` |
   | `ADMIN_CHAT_ID` | Your own Telegram numeric chat ID |
   | `GH_PAT` | The PAT from B2 |
   | `GH_REPO_DATA` | `owner/repo` of the private data repo, e.g. `KiranKri/ai-deal-scout-data` |

3. Deploy. Confirm it's alive:

   ```bash
   curl https://your-service.onrender.com/health
   # {"status":"ok","timestamp":"2026-07-18T..."}
   ```

   > **Don't skip this.** `render.yaml` sets `--workers 1` on purpose — the subscriber store has no cross-process locking. Leave it at 1.

**Finding your `ADMIN_CHAT_ID`:** message [@userinfobot](https://t.me/userinfobot) on Telegram, or send any message to your bot and check `https://api.telegram.org/bot<TOKEN>/getUpdates`.

---

## B4. Register the Telegram webhook

Point Telegram at your Render URL and set the shared secret:

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
        "url": "https://your-service.onrender.com/webhook",
        "secret_token": "<the same WEBHOOK_SECRET you set in Render>"
      }'
```

Verify:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

Look for `"url"` matching yours and `"last_error_message"` absent. Then send `/start` to your bot — you should get the subscription confirmation, and `subscribers.json` in the private repo should gain a record.

> The `secret_token` **must** match `WEBHOOK_SECRET` exactly. If it doesn't, every update gets a 403 and the bot appears dead with no error on the Telegram side.

---

## B5. Configure the GitHub Actions pipeline

In the main repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your BotFather token |
| `GH_PAT` | The PAT from B2 |
| `GH_REPO_DATA` | `owner/repo` of the private data repo |

The workflow already declares `permissions: contents: write` so it can commit state back.

**Schedule:** `cron: '30 2,14 * * *'` (UTC) = 08:00 and 20:00 IST. Edit `.github/workflows/deal_scout.yml` to change it — remember cron is UTC, so subtract 5h30m from your intended IST time.

---

## B6. Running the pipeline

**Three ways:**

1. **Automatic** — the cron fires twice daily.
2. **From GitHub** — Actions tab → *AI Deal Scout* → **Run workflow** → pick branch → **Run workflow**.
3. **From Telegram** — send `/run` to the bot. Admin-only; you'll get `✅ Pipeline triggered. Check Telegram in ~2 mins.`

---

## B7. Admin commands

| Command | Response |
|---|---|
| `/run` | Dispatches the GitHub Actions workflow on `main` |
| `/status` | `📊 Total: 47 / Active: 43 / Inactive: 4` |

Both reject non-admin chat IDs with `Unauthorized.`

---

## B8. Tuning what gets found

Everything lives in `src/config.py`. Restart isn't needed — the pipeline reads it fresh each run.

**Add a new AI tool to track:**

```python
TOOL_KEYWORDS = [..., "suno", "runway", "v0"]
```

**Add a new deal phrasing:**

```python
DEAL_KEYWORDS = [..., "bogo", "student pricing"]
```

**Add a subreddit:**

```python
REDDIT_RSS_FEEDS = [..., "https://www.reddit.com/r/SaaSDeals/new.rss"]
```

**Add a vendor blog RSS feed:**

```python
RSS_FEEDS = ["https://openai.com/blog/rss.xml"]   # currently empty
```

**Threshold knobs:**

| Setting | Default | Effect of raising it |
|---|---|---|
| `MIN_UPVOTES` | 10 | Stricter quality bar — but only applies to Hacker News items, since Reddit RSS doesn't expose scores |
| `MAX_HN_RESULTS_PER_QUERY` | 15 | More HN candidates per query, slower run |
| `SCRAPER_SLEEP_SECONDS` | 1.0 | Politer to sources, slower run |
| `HASH_CLEANUP_DAYS` | 90 | Longer memory, bigger `seen_deals.json` |
| `TELEGRAM_MAX_CHARS` | 4000 | Telegram's hard limit is 4096 — don't exceed 4000 |

> **After editing keywords, do a dry run.** Comment out the `notifier.send_deals()` call in `main.py`, run locally, and read `logs/latest_deals.md`. A loose keyword can flood every subscriber at the next cron.

---

## B9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/start` gets no reply | Webhook not registered, or `secret_token` ≠ `WEBHOOK_SECRET` | `getWebhookInfo`, re-run `setWebhook` |
| Bot replies but nobody is stored | `GH_PAT` lacks Contents write on the data repo, or `GH_REPO_DATA` is wrong | Check Render logs for `GitHub API GET failed` / `_put_file failed` |
| Workflow runs, no Telegram messages | Zero active subscribers | Log shows `No active subscribers — skipping Telegram send` |
| `/run` says "Failed to trigger pipeline" | PAT lacks `Actions: write` on the main repo, or the branch isn't `main` | The handler hardcodes `{"ref": "main"}` |
| Same deals sent repeatedly | The "Commit state updates" step failed, so `seen_deals.json` was never persisted | Check the Actions log for a `git push` rejection |
| Zero deals for several consecutive runs | BitDegree restructured, or keywords are too narrow | Log shows `BitDegree: no deal cards found, site structure may have changed` |
| Deals stopped mid-broadcast | Telegram rate limit or network failure | The loop breaks for that user; check for `Telegram error <code>` lines |
| Render service sleeps | Free tier idles after inactivity | The health check keeps it warm; the first webhook after a cold start may be dropped |

**Where to look:**
- Pipeline: GitHub → Actions → the run → *Run AI Deal Scout* step
- Bot server: Render → your service → Logs
- Deal history: `logs/latest_deals.md` in the repo

---

## B10. Resetting state

**Forget all seen deals** (they'll be re-sent on the next run — do this only if subscribers are few or you've muted the broadcast):

```bash
echo '{"hashes": {}, "last_updated": ""}' > data/seen_deals.json
git add data/seen_deals.json
git commit -m "chore: reset seen_deals"
git push
```

**Clear the deal history log:**

```bash
: > logs/latest_deals.md
```

**Wipe all subscribers:** edit `subscribers.json` in the private data repo back to `{"subscribers": [], "last_updated": ""}`.

---

## Security notes

- `.env` is git-ignored and must never be committed. If a token leaks, revoke it in BotFather (`/revoke`) and GitHub immediately.
- `GH_PAT` should be fine-grained and scoped to exactly the two repos it needs — never a classic token with full `repo` scope.
- Rotate `WEBHOOK_SECRET` by updating Render's env var **and** re-running `setWebhook` with the new value, in that order.
- The `/webhook` endpoint is only protected by the shared secret header. Anyone who learns it can drive the bot.
