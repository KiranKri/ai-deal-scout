"""Central configuration for ai-deal-scout."""

KEYWORDS: list[str] = [
    "deal",
    "promo",
    "discount",
    "free trial",
    "lifetime",
    "coupon",
    "offer",
    "claude",
    "cursor",
    "perplexity",
    "midjourney",
    "grok",
    "windsurf",
    "gemini",
    "copilot",
    "chatgpt",
    "openai",
    "elevenlabs",
    "jasper",
    "writesonic",
    "notion ai",
    "grammarly",
]

BOOSTED_PHRASES: list[str] = [
    "claude pro deal",
    "cursor discount",
    "chatgpt promo",
    "perplexity free",
    "midjourney discount",
    "gemini trial",
    "copilot deal",
    "grok free",
    "windsurf promo",
]

REDDIT_RSS_FEEDS: list[str] = [
    "https://www.reddit.com/r/deals/new.rss",
    "https://www.reddit.com/r/promocodes/new.rss",
    "https://www.reddit.com/r/artificial/new.rss",
    "https://www.reddit.com/r/ChatGPT/new.rss",
]

HN_SEARCH_QUERIES: list[str] = [
    "AI deal",
    "AI promo",
    "AI free trial",
    "AI discount",
    "AI subscription",
]

RSS_FEEDS: list[str] = [
    "https://openai.com/news/rss.xml",
]

MIN_UPVOTES: int = 10
MAX_HN_RESULTS_PER_QUERY: int = 15
SCRAPER_SLEEP_SECONDS: float = 1.0
SEEN_DEALS_PATH: str = "data/seen_deals.json"
HISTORY_PATH: str = "logs/latest_deals.md"
TELEGRAM_MAX_CHARS: int = 4000
HASH_CLEANUP_DAYS: int = 90
