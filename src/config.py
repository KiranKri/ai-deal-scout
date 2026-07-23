"""Central configuration for ai-deal-scout.

Tuned for RECALL.  With zero subscribers and a dry-run pipeline, the cost of a
false positive is one junk line in ``logs/latest_deals.md``; the cost of a false
negative is a deal we never learn exists.  Tighten these once the eval set has
real positives in it (see docs/ACTION_PLAN.md).
"""

# ---------------------------------------------------------------------------
# Deal-intent vocabulary
#
# Inflected forms (deals / discounted / offering) are handled automatically by
# filter._match, so list the base form only.
# ---------------------------------------------------------------------------

DEAL_KEYWORDS: list[str] = [
    # core
    "deal",
    "promo",
    "promo code",
    "discount",
    "coupon",
    "offer",
    "sale",
    "savings",
    "voucher",
    "rebate",
    "perk",            # "Perplexity Pro Perks" - official offers page
    "promotion",       # inflection turns "promo"->"promos", never "promotions"
    "referral program",
    "back to school",
    "for students",    # "ElevenReader for Students"
    "welcome offer",
    "redeem",
    "save",            # "Writesonic - SAVE 30%" / "Otter Ai - SAVE 51%"
    "free plan",       # "FREE GITHUB TEAM PLAN"
    "free pro",
    "free version",
    "free forever",
    "student pack",    # "ElevenLabs - AI Student Pack"
    "pro plan",
    "team plan",
    "education plan",
    "months free",
    "month free",
    "year free",
    "free to all",     # "1-year perplexity pro free to all Airtel users"
    # price movement
    "% off",
    "%off",
    "price drop",
    "price cut",
    "half price",
    "reduced",
    "slashed",
    "markdown",
    # free / trial
    "free trial",
    "free month",
    "free year",
    "free access",
    "free tier",
    "free credits",
    "for free",
    "free for",      # "free for students", "free for educators"
    "now free",
    "free until",
    "free upgrade",
    "no cost",
    # "on us" removed: with phrase inflection it matched "on using" (tutorials).
    # Multi-word phrases no longer inflect, but the phrase is still too weak alone.
    "trial",
    "freebie",
    # subscription framing
    "lifetime",
    "lifetime access",
    "limited time",
    "flash sale",
    "black friday",
    "cyber monday",
    "bundle",
    "giveaway",
    "credits",
    # eligibility programmes — high-yield for AI tools specifically
    "student plan",
    "student discount",
    "nonprofit discount",
    "nonprofit pricing",
    "educator discount",
    "student pricing",
    "startup credits",
    "referral",
    "waived",
    "upgrade free",
]

# ---------------------------------------------------------------------------
# AI tool vocabulary
#
# Kept deliberately broad.  A generic tier ("ai tool", "llm", "copilot") lets
# deals on tools we have not enumerated still reach the log, at the cost of
# some noise.
# ---------------------------------------------------------------------------

TOOL_KEYWORDS: list[str] = [
    # assistants / chat
    "claude",
    "anthropic",
    "chatgpt",
    "openai",
    # bare "gpt" kept: product titles ("GPT Everywhere") and Show HN launches.
    # Documented FP risk on exam-prep noise; rare in our sources.
    "gpt",
    "gpt-4",
    "gpt-5",
    "gemini",
    "google ai",
    "grok",
    "perplexity",
    "copilot",
    "deepseek",
    "mistral",
    "llama",
    "kimi",
    "qwen",
    # coding
    "cursor",
    "windsurf",
    "replit",
    "codeium",
    "tabnine",
    "lovable",
    "bolt.new",
    "bolt new",
    "v0",
    "devin",
    "claude code",
    # image / video / audio
    "midjourney",
    "dall-e",
    "stable diffusion",
    "runway",
    # bare "pika"/"sora" removed — too generic alone; product context rare in titles
    "elevenlabs",
    "elevenreader",   # ElevenLabs reading app — "ElevenReader for Students" was a real FN
    "suno",
    "udio",
    "heygen",
    "synthesia",
    "descript",
    "topaz",
    "leonardo",
    "ideogram",
    # bare "flux" removed — ordinary English / capacitor noise
    # writing / productivity
    "jasper",
    "writesonic",
    "copy.ai",
    "copy ai",
    "notion ai",
    "notion",
    "grammarly",
    "quillbot",
    # bare "gamma"/"tome"/"consensus" removed — ordinary English; too many FPs
    "otter.ai",
    "otter ai",
    # bare "otter" removed — matches swimming/animal noise; brand forms kept
    "fathom",
    # search / research
    "you.com",
    "ai credits",
    "ai perks",
    "phind",
    "elicit",
    # bare "napkin" kept only as product name is uncommon; still risky — leave
    # agents / automation
    "langchain",
    "n8n",
    "zapier ai",
    "relevance ai",
    # generic catch-all tier — broad on purpose
    "ai tool",
    "ai assistant",
    "ai subscription",
    "ai app",
    "ai saas",
    "llm",
    "genai",
    "generative ai",
]

# ---------------------------------------------------------------------------
# Boosted phrases
#
# Deliberately two-word (tool + intent) rather than three.  The previous
# three-word phrases ("claude pro deal") required an exact contiguous match and
# could effectively never fire.
# ---------------------------------------------------------------------------

BOOSTED_PHRASES: list[str] = [
    "claude deal",
    "claude discount",
    "claude free",
    "chatgpt deal",
    "chatgpt discount",
    "chatgpt free",
    "cursor deal",
    "cursor discount",
    "cursor free",
    "perplexity deal",
    "perplexity free",
    "midjourney deal",
    "midjourney discount",
    "gemini deal",
    "gemini free",
    "copilot deal",
    "copilot free",
    "grok deal",
    "grok free",
    "windsurf deal",
    "elevenlabs deal",
    "notion deal",
    "ai deal",
    "ai discount",
    "ai promo",
]

# ---------------------------------------------------------------------------
# Negative keywords — hard veto
#
# "AI deal" in the press overwhelmingly means M&A, not a discount.  These
# terms veto a match outright regardless of score.  Measured against the
# historical corpus, this removes the "GitHub cuts AI deals with Google" and
# "Anthropic in talks with Pentagon" class of false positive.
# ---------------------------------------------------------------------------

# Trimmed from 74 entries.  Removed single-token M&A/business words that
# vetoed genuine promos in live titles (verified against logs/latest_deals.md
# and synthetic partnership headlines): commission, revenue, signs, backed,
# billion, stake, partners with, on a mission, our mission.  Kept multi-word
# M&A phrases that actually fire on the historical noise corpus.
NEGATIVE_KEYWORDS: list[str] = [
    "antitrust",
    "probe",
    "lawsuit",
    "sues",
    "merger",
    "acquisition",
    "acquires",
    "in talks",
    "funding round",
    "valuation",
    "ipo",
    "pentagon",
    "unionize",
    "zombie",
    "raising eyebrows",
    "board of directors",
    "sales team",
    "sales productivity",
    "for sales",
    # --- Ask HN threads are questions, never deals ---
    "ask hn",
    # --- M&A / licensing / corporate deal coverage (multi-word, high precision) ---
    "inks",
    "licensing deal",
    "cloud deal",
    "cuts ai deal",     # "GitHub cuts AI deals with Google, Anthropic"
    "strikes deal",
    "signs deal",
    "deal with google",
    "deal with microsoft",
    "deal with amazon",
    "deal with nvidia",
    # --- a deal ENDING is not a deal ---
    "stops free",
    "ends free",
    "stopped free",
    "ended free",
    "no longer free",
    "downgraded",
    "fiasco",
    "rate limit purge",
    "not available in",
    "overshot",
    "deprived",
    "hallucination",
    "is dead",
    # --- support threads and questions, not deals (found live in run 1) ---
    "unable to",
    "not approved",
    "was charged",
    "charged $",
    "stuck on",
    "not working",
    "doesn't work",
    "does not work",
    "feature request",
    "bug report",
    "troubleshoot",
    "why am i",
    "how can i",
    "how do i",
    "anyone else",
    "is it worth",
    # --- generic product/marketing pages that are not offers ---
    "release updates",
    "billing support",
    "plan information",
    "plan details",          # "Free plan details – Runway" help docs
    "now available in",
    "pair programmer",
    "marketplace experiment",
    # --- support / complaint threads that mention plan names (eval FPs) ---
    "why did i",             # "Why did I not get the first-month off coupon?"
    "stopped working",       # "Copilot free plan stopped working"
    "rate limit exceeded",
    "but no pro",            # "I have chatgpt 5X pro plan, but no pro model"
    "did not get",
    "didn't get",
    # --- corporate / infra "deal" that is not a consumer promo ---
    "compute deal",          # "Higher usage limits for Claude and a compute deal with SpaceX"
    "power deal",
    "training deal",
    "wants to get",          # "Perplexity wants to get discounted AI products into the US government"
    # --- free product release ≠ time-limited promo ---
    "for free on windows",   # "Perplexity releases Comet browser for free on Windows..."
    "for free on macos",
    "for free on mac",
    "extension for free",    # open-source free, not a discount
    "copilot-like",          # free clone/extension, not Copilot promo
    "copilot like",
    # --- generic aggregator / SEO fluff ---
    "exclusive ai tool deals",
    # --- past-tense price-cut news / SEO "how to" (not a redeemable offer) ---
    # Keeps "DeepSeek to Make Permanent 75% Discount" (TP) while vetoing
    # "DeepSeek made its 75% discount permanent" (news FP).
    "made its",
    "here's how",
    "heres how",
]

# When True, titles containing "?" must also match a STRONG_DEAL_KEYWORD
# (see filter.is_relevant).  Hard-rejecting every "?" killed real offer
# headlines; the strong-signal exception keeps support threads out.
VETO_QUESTION_TITLES: bool = True

# Drop deals the staleness heuristic flags, rather than only logging them.
# Measured on the 764-row labelled set: precision 0.808 -> 0.829, recall
# 0.868 -> 0.853, F1 0.837 -> 0.841.  It costs one row labelled a deal —
# "Suno Black Friday Deals 2025" seen in July 2026 — which is arguably a bad
# label rather than a bad drop.  Precision is worth slightly more than recall
# here because a junk message costs subscriber trust while a miss is invisible.
DROP_STALE: bool = True

# ---------------------------------------------------------------------------
# Strong price signals
#
# "Show HN:" posts are developers launching their own product.  Most are not
# deals ("AI jigsaw puzzle generator"), but some are ("50% Discount for first
# 20").  Rather than banning Show HN outright, require one of these explicit
# price signals before a Show HN post is allowed through.
# ---------------------------------------------------------------------------

STRONG_DEAL_KEYWORDS: list[str] = [
    "% off",
    "%off",
    "discount",
    "coupon",
    "promo code",
    "lifetime deal",
    "free trial",
    "free credits",
    "half price",
    "price drop",
    "giveaway",
    "voucher",
]

# Title prefixes that require a STRONG_DEAL_KEYWORD to qualify.
WEAK_SOURCE_PREFIXES: tuple[str, ...] = ("show hn:", "show hn ")

# Single-word keywords that must not take the plural/participle suffix.
# "sale"→"sales" (business function), "trial"→"trials" rarely means a free
# trial, "credits"→"credited", "save"→"saved" ("saved my life").
# Multi-word phrases never inflect regardless of this set.
NO_INFLECTION: frozenset[str] = frozenset({"sale", "credits", "trial", "save"})

# ---------------------------------------------------------------------------
# Sources
#
# Ranked by expected genuine AI-tool deals per run.  r/deals and r/promocodes
# were removed: across 669 historical titles they contributed consumer
# electronics (TVs, vacuums, toothbrushes) and not one AI deal.
# ---------------------------------------------------------------------------

# Cut from 19 feeds to 5 (2026-07-19, measured over 14 logged runs):
# tier 2 (SaaSDeals/AppSumo/software) and all 7 tier-3 search feeds produced
# ZERO kept deals ever; r/midjourney, r/artificial, r/LocalLLaMA likewise;
# r/singularity's only keeper was product news, not a deal.  The five kept
# feeds are the only ones with any signal plus the on-topic communities where
# a future deal is most plausible.  Saves ~14s/run at zero measured recall.
REDDIT_RSS_FEEDS: list[str] = [
    "https://www.reddit.com/r/ChatGPTPro/new.rss",
    "https://www.reddit.com/r/ChatGPT/new.rss",
    "https://www.reddit.com/r/ClaudeAI/new.rss",
    "https://www.reddit.com/r/cursor/new.rss",
    "https://www.reddit.com/r/perplexity_ai/new.rss",
]

# ---------------------------------------------------------------------------
# Hacker News (Algolia)
#
# Every previous query began with "AI", which structurally excluded any post
# naming the tool directly — "Cursor is 50% off this week" was unreachable.
# Tool-name queries now run alongside the generic ones.
# ---------------------------------------------------------------------------

HN_SEARCH_QUERIES: list[str] = [
    # generic deal intent
    "AI deal",
    "AI discount",
    "AI free trial",
    "AI promo",
    "AI subscription",
    "lifetime deal AI",
    "free credits AI",
    # tool-name led — these were previously unreachable
    "Claude discount",
    "ChatGPT discount",
    "Cursor discount",
    "Perplexity free",
    "Copilot free",
    "Midjourney discount",
    "Gemini free",
    # programme led
    "student discount AI",
    "startup credits AI",
]

# ---------------------------------------------------------------------------
# Vendor / aggregator RSS
#
# The OpenAI blog feed was removed previously because it drowned the filter in
# non-deal posts (593 of 728 historical items).  That was a filter failure, not
# a source failure, but it stays out until the eval set can prove the filter
# holds.  Aggregators below are deal-only by construction.
# ---------------------------------------------------------------------------

RSS_FEEDS: list[str] = [
    # appsumo.com/rss/ removed: serves malformed XML (invalid token at 27:65)
    "https://www.producthunt.com/feed?category=artificial-intelligence",
]

# ---------------------------------------------------------------------------
# Web search (Tavily Search API)
#
# The bot previously only saw what strangers posted to Reddit/HN.  It never
# checked a vendor's own announcement page, which is why e.g. a Kimi promo
# could run for a week unnoticed.  Web search closes that gap.
#
# Budget is capped on five independent axes so no single failure can run away:
# queries per run, wall-clock per run, results per query, a persisted monthly
# quota, and early-stop on consecutive empty queries.
# ---------------------------------------------------------------------------

# Rebalanced 8+4 → 10+2 (2026-07-19): measured yield was ~3.75 deals/credit
# for vendor queries vs ~1.75 for open-web, and the open-web side was the
# entire spam surface (couponchief, Rent-the-Runway noise on "Runway").
# Identical total cost: 12 credits/run.
WEBSEARCH_MAX_QUERIES_PER_RUN: int = 12
WEBSEARCH_VENDOR_QUERIES: int = 10     # high-precision site: searches
WEBSEARCH_ROTATING_QUERIES: int = 2    # long-tail tools, rotated each run
WEBSEARCH_RESULTS_PER_QUERY: int = 10
WEBSEARCH_TIME_BUDGET_SECONDS: float = 60.0
WEBSEARCH_MONTHLY_QUOTA: int = 900     # Tavily free tier is 1000/mo; leave headroom
WEBSEARCH_EARLY_STOP_EMPTY: int = 3    # stop after N consecutive empty queries

# Graceful degradation near the quota line.  Below this many credits remaining
# the run drops the open-web "rotating tool" queries and keeps only the
# vendor-site searches, which are far higher precision per credit.  Web search
# therefore fades out rather than switching off, and the reserve lasts weeks
# longer at the reduced rate.
WEBSEARCH_RESERVE_THRESHOLD: int = 150
WEBSEARCH_MAX_AGE_DAYS: int = 7        # only results published in the last N days
WEBSEARCH_SLEEP_SECONDS: float = 0.5   # be polite; Tavily has no hard per-second cap
WEBSEARCH_STATE_PATH: str = "data/websearch_state.json"

# Official vendor domains, searched with site: every run.  Highest precision
# available: vendors do not spam about themselves.
VENDOR_SITES: list[tuple[str, str]] = [
    ("Anthropic", "anthropic.com"),
    ("OpenAI", "openai.com"),
    ("Cursor", "cursor.com"),
    ("Perplexity", "perplexity.ai"),
    ("Google Gemini", "gemini.google.com"),
    ("GitHub Copilot", "github.com"),
    ("Midjourney", "midjourney.com"),
    ("ElevenLabs", "elevenlabs.io"),
    # moonshot.cn and deepseek.com: 0 deals in 14 logged runs — swapped for
    # the two non-vendor tools that kept appearing in the logs (lovable.dev,
    # udio.com).  Kimi/Qwen/DeepSeek remain covered via ROTATING_TOOLS.
    ("Lovable", "lovable.dev"),
    ("Udio", "udio.com"),
    ("Mistral", "mistral.ai"),
    ("Suno", "suno.com"),
    ("Runway", "runwayml.com"),
    ("Replit", "replit.com"),
    ("Notion", "notion.so"),
    ("Grammarly", "grammarly.com"),
]

# Long-tail tools searched on the open web, N per run, cycling so every tool
# is covered at fixed cost.
#
# Trimmed 37 → 21 (2026-07-19): tools with a VENDOR_SITES entry were removed
# (searching them again on the open web duplicated coverage and was where the
# coupon-farm noise came from — the bare "Runway" query even returned
# Rent-the-Runway fashion deals), as were Pika and Gamma — both were removed
# from TOOL_KEYWORDS, so their results could never pass the filter and every
# credit spent on them was wasted.  DeepSeek stays: its vendor slot was
# swapped out, so rotating is now its only coverage (same for Kimi/Qwen).
ROTATING_TOOLS: list[str] = [
    "Kimi", "Qwen", "DeepSeek",
    "HeyGen", "Synthesia", "Descript", "Topaz Labs", "Leonardo AI",
    "Ideogram", "Windsurf", "Codeium", "Tabnine", "Bolt.new",
    "Jasper", "Writesonic", "Copy.ai", "QuillBot", "Otter.ai",
    "Phind", "Elicit", "You.com",
]

# Coupon farms and SEO spam.  These rank highly for exactly the queries this
# bot runs and are overwhelmingly fake or expired codes.  Blocked outright.
BLOCKED_DOMAINS: list[str] = [
    "retailmenot.com", "couponbirds.com", "coupons.com", "slickdeals.net",
    "dealspotr.com", "knoji.com", "couponfollow.com", "wethrift.com",
    "promocodes.com", "savings.com", "offers.com", "coupert.com",
    "hotdeals.com", "couponcabin.com", "dontpayfull.com", "couponxoo.com",
    "tenereteam.com", "sociablekit.com", "couponannie.com", "valuecom.com",
    "pinterest.com", "facebook.com", "quora.com", "linkedin.com",
    # --- found live in the first real web-search run ---
    "simplycodes.com", "joinsecret.com", "demandsage.com", "resourify.com",
    "startupworld.com", "generalif.com", "freelance-stack.io", "skool.com",
    "couponkirin.com", "grabon.in", "sitejabber.com", "trustpilot.com",
    # video and social: overwhelmingly clickbait "get X for FREE!" content
    "youtube.com", "youtu.be", "x.com", "twitter.com", "tiktok.com",
    "instagram.com", "medium.com",
    # --- found in run history 2026-07, not previously blocked ---
    "couponchief.com", "techjury.net", "vectortemplates.com",
    "worthepenny.com", "namobot.com", "mightydeals.com",
    "affiliateweapons.com", "shipthedeal.com", "colormango.com",
    "bloggingspark.com", "aistudentdiscount.com", "comparateur-ia.com",
]

# General news/press outlets that legitimately cover AI companies but publish
# far more "AI deal" (M&A, funding, policy, infra) and product-launch stories
# than consumer discounts.  Not blocked outright — a real promo roundup
# ("TechCrunch: best AI deals this week") should still get through — but a
# title from one of these hosts must also carry a STRONG_DEAL_KEYWORD.
# Measured on the 764-row eval set: every non-strong-signal row from these
# hosts is a false positive (GitHub/Google/Anthropic "AI deals", Perplexity
# "discounted AI products into the US government", etc.); every true
# positive from these hosts already carries a strong signal (e.g. DeepSeek's
# "75% Discount", Amazon's "free credits").
NEWS_DOMAINS: list[str] = [
    "reuters.com", "bloomberg.com", "theregister.com", "wired.com", "ft.com",
    "wsj.com", "theverge.com", "cnbc.com", "arstechnica.com",
    "thedailybeast.com", "404media.co", "chinatalk.media", "noahpinion.blog",
    "gamereactor.eu", "forbes.com", "techcrunch.com", "businessinsider.com",
    "engadget.com", "zdnet.com", "venturebeat.com", "thenextweb.com",
    "ghacks.net", "axios.com", "techradar.com", "pcmag.com",
    "digitaltrends.com", "tomsguide.com", "tomshardware.com", "gizmodo.com",
    "mashable.com", "slashdot.org", "cnet.com", "nytimes.com",
    "washingtonpost.com", "theguardian.com", "bbc.com", "apnews.com",
    "npr.org", "cnn.com", "marketwatch.com", "fortune.com", "semafor.com",
    "theinformation.com", "qz.com", "vox.com", "pcworld.com",
    "infoworld.com", "computerworld.com", "nbcnews.com",
]

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Recall mode: HN upvote gating discarded genuine low-traffic deal posts.
# Set back to 10 once the eval set shows precision needs it.
MIN_UPVOTES: int = 0

# Kept for score_deal ranking / eval reporting only — not used as a gate in
# is_relevant (deal+tool keyword gates already imply score >= 20).
MIN_SCORE: int = 15

MAX_HN_RESULTS_PER_QUERY: int = 30
SCRAPER_SLEEP_SECONDS: float = 1.0
SEEN_DEALS_PATH: str = "data/seen_deals.json"
HISTORY_PATH: str = "logs/latest_deals.md"
TELEGRAM_MAX_CHARS: int = 4000
HASH_CLEANUP_DAYS: int = 90

# ---------------------------------------------------------------------------
# Manual /run allowlist (Telegram bot)
#
# Comma-separated chat IDs that may trigger the pipeline in addition to
# ADMIN_CHAT_ID.  Set via env RUN_ALLOWLIST on Render.
# Rate limits (enforced in bot_server) protect the Tavily monthly quota.
# ---------------------------------------------------------------------------
RUN_RATE_LIMIT_SECONDS: int = 3600       # per-user cooldown (1 hour)
RUN_GLOBAL_DAILY_CEILING: int = 10       # total /run dispatches per IST day
RUN_QUOTA_STATE_PATH: str = "data/run_quota.json"
