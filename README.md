# ai-deal-scout

**ai-deal-scout** is an open-source Python bot that automatically scans Reddit, Hacker News, and official RSS feeds for deals, discounts, free trials, and promos on popular AI tools (Claude, ChatGPT, Cursor, Midjourney, Perplexity, and more), then delivers matching deals straight to a Telegram chat.

---

## What it does

- Scrapes Reddit RSS feeds, Hacker News search results, and official product RSS feeds on a schedule
- Scores each post against a configurable keyword and boosted-phrase list
- Deduplicates using SHA-256 hashes so the same deal is never sent twice
- Sends relevant deals to a Telegram chat (up to 4 000 characters per message)
- Logs all deals to `logs/latest_deals.md` for easy review
- Automatically cleans up hashes older than 90 days

---

## Folder structure

```
ai-deal-scout/
├── .github/workflows/deal_scout.yml   GitHub Actions workflow
├── src/
│   ├── main.py                        Entry point
│   ├── config.py                      All tuneable constants
│   ├── dedup.py                       Seen-deal deduplication
│   ├── filter.py                      Keyword scoring & relevance check
│   ├── notifier.py                    Telegram sender
│   ├── history.py                     Markdown deal logger
│   └── scrapers/
│       ├── reddit.py                  Reddit RSS scraper
│       ├── hackernews.py              HN Algolia API scraper
│       ├── rss_feed.py                Generic RSS scraper
│       └── bitdegree.py               BitDegree deal scraper
├── data/seen_deals.json               Persistent hash store (git-ignored)
├── logs/latest_deals.md               Human-readable deal log (git-ignored)
├── tests/
│   ├── test_dedup.py
│   └── test_filter.py
├── .env.example                       Secret template
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/your-username/ai-deal-scout.git
cd ai-deal-scout
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure secrets

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Target chat / channel ID |

> `.env` is git-ignored and must never be committed.

---

## Running locally

```bash
cd src
python main.py
```

Logs are written to `logs/latest_deals.md`; seen-deal hashes are persisted to `data/seen_deals.json`.

### Running tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Required secrets (GitHub Actions)

Add the following secrets under **Settings → Secrets and variables → Actions** in your repository:

| Secret name | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Target Telegram chat ID |

---

## Triggering manually in GitHub Actions

1. Go to **Actions** → **Deal Scout** workflow in your repository.
2. Click **Run workflow** (top-right of the workflow run list).
3. Select the branch and click **Run workflow**.

The workflow can also be triggered on a schedule (e.g. twice daily) via the `cron` trigger defined in `.github/workflows/deal_scout.yml`.

---

## Planned features

- **Telegram `/run` command** — send `/run` from the Telegram app on your phone to instantly trigger the GitHub Actions workflow, without needing to open GitHub. Would use a Telegram webhook or polling loop to listen for bot commands and call the GitHub API (`POST /repos/{owner}/{repo}/actions/workflows/{id}/dispatches`) with a `workflow_dispatch` event.

---

## License

MIT
