# Step 3: Data Layer — Explained

> Files created: `src/data/polymarket_client.py`, `src/data/news_collector.py`, `src/data/sentiment_scraper.py`
> These three files are how the agent sees the world — they collect all the data the brain needs.

---

## The Big Picture

```
        ┌─────────────────────────────────────────────────┐
        │                  DATA LAYER                     │
        │                                                 │
        │   polymarket_client.py ──→ Market data, prices  │
        │   news_collector.py    ──→ News articles        │
        │   sentiment_scraper.py ──→ Reddit posts         │
        │                                                 │
        │   All feed into the Analysis Engine (Step 5)    │
        └─────────────────────────────────────────────────┘
```

The agent can't make decisions without data. This layer answers three questions:
1. **What markets exist?** (Polymarket client → Gamma API)
2. **What's happening in the news?** (News collector → NewsAPI)
3. **What does the public think?** (Sentiment scraper → Reddit)

---

## File 1: `src/data/polymarket_client.py` — Polymarket API Client

This is the biggest and most important file in the data layer. It talks to two separate Polymarket APIs.

### Two APIs, One Client

| API | URL | Auth | Purpose |
|-----|-----|------|---------|
| **Gamma** | `gamma-api.polymarket.com` | None (public) | Discover markets, get metadata |
| **CLOB** | `clob.polymarket.com` | Private key + API keys | Get prices, place orders, manage positions |

We wrap both in a single `PolymarketClient` class so the rest of the codebase just calls one client.

### The Rate Limiter

```python
class RateLimiter:
    def __init__(self, requests_per_second: int):
        self.min_interval = 1.0 / requests_per_second
        self.last_request_time = 0.0

    def wait(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()
```

**What it does:** Ensures we never exceed the API's rate limit. If we're calling too fast, it sleeps until enough time has passed.

**How it works (example):** If the limit is 20 requests/second:
- `min_interval = 1.0 / 20 = 0.05 seconds` (50ms between requests)
- Each call to `wait()` checks: "Has 50ms passed since my last request?"
  - Yes → proceed immediately
  - No → sleep for the remaining time

**Why this matters:** Polymarket will ban your IP for 2-24 hours if you exceed rate limits. This is the same concept as rate limiting in web security — you've likely seen rate limiters on login pages to prevent brute-force attacks. Same mechanism, different purpose.

**This is a "token bucket" algorithm** — the simplest form of rate limiting. In your cybersecurity coursework, you'll see more sophisticated versions (sliding window, leaky bucket).

### The Constructor (`__init__`)

```python
def __init__(self):
    # Gamma API — public HTTP client, no auth
    self.gamma = httpx.Client(
        base_url=GAMMA_API_URL,
        timeout=30.0,
        headers={"Accept": "application/json"},
    )
```

- **`httpx.Client`** — An HTTP client library (like `requests`, but with async support). We use it for the Gamma API because it's a simple REST API.
- **`base_url`** — Every request starts from this URL. So `self.gamma.get("/markets")` actually hits `https://gamma-api.polymarket.com/markets`.
- **`timeout=30.0`** — If Polymarket doesn't respond in 30 seconds, give up. Without timeouts, the bot could hang forever waiting for a dead server.

```python
    # CLOB API — authenticated trading client
    self.clob = None
    if POLYGON_PRIVATE_KEY:
        self.clob = ClobClient(
            CLOB_API_URL,
            key=POLYGON_PRIVATE_KEY,
            chain_id=POLYGON_CHAIN_ID,
            signature_type=0,  # 0 = EOA wallet
        )
        creds = self.clob.create_or_derive_api_creds()
        self.clob.set_api_creds(creds)
```

- **`ClobClient`** — Polymarket's official Python SDK. Handles all the cryptographic signing for you.
- **`signature_type=0`** — EOA (Externally Owned Account) wallet, like MetaMask. Type 1 would be for Polymarket's email/Magic wallet.
- **`create_or_derive_api_creds()`** — This is the two-tier auth process:
  1. Signs a message with your private key (proves wallet ownership — L1 auth)
  2. Derives API key + secret + passphrase (for subsequent requests — L2 auth)
- **If no private key is set**, the CLOB client stays `None`. The bot can still discover markets (Gamma) but can't trade. This is the paper-trading-only fallback.

### Gamma API Methods (Market Discovery)

```python
def get_markets(self, limit: int = 50, active: bool = True) -> list:
```
- Fetches a list of active markets. Each market is a dict with: question, token IDs, volume, liquidity, deadline.
- `response.raise_for_status()` — Throws an exception if the HTTP status code is 4xx or 5xx. This is defensive programming — fail loud instead of silently processing garbage data.

```python
def search_markets(self, query: str, limit: int = 10) -> list:
```
- Gamma API doesn't have a search endpoint, so we fetch all markets and filter client-side.
- Uses a **list comprehension** with string matching:
  ```python
  filtered = [m for m in all_markets if query_lower in m.get("question", "").lower()]
  ```
- This is a common Python pattern: `[item for item in list if condition]`. It's equivalent to a for-loop that appends matching items to a new list, but more concise.

### CLOB API Methods (Prices)

```python
def get_order_book(self, token_id: str) -> dict | None:
```
- Returns the full order book: all open buy orders (bids) and sell orders (asks), sorted by price.
- Uses the stricter `book_limiter` (10 req/s) since order book endpoints have tighter rate limits.
- `dict | None` — **Union type hint** (Python 3.10+). Means this function returns either a dict OR None. The `None` case handles errors.

```python
def get_midpoint(self, token_id: str) -> float | None:
```
- Returns the average of best bid and best ask. A quick "what's the approximate price?" check.
- Example: best bid = $0.54, best ask = $0.56 → midpoint = $0.55.

### CLOB API Methods (Trading)

```python
def place_order(self, token_id, price, size, side) -> dict | None:
```
- The most important method — this is where money moves.
- `OrderArgs` — A data structure from `py-clob-client` that bundles the order parameters.
- All orders are **limit orders** (GTC by default). The bot never uses market orders, which protects against slippage.

```python
def cancel_all_orders(self) -> bool:
```
- **Emergency function.** Cancels every open order. Used by the kill switch.
- Logged at WARNING level because this is a drastic action.

### Error Handling Pattern

Every method follows the same pattern:
```python
try:
    self.clob_limiter.wait()     # 1. Respect rate limit
    result = self.clob.do_thing() # 2. Make the API call
    return result                  # 3. Return the result
except Exception as e:
    logger.error("...")           # 4. Log the error
    return None                    # 5. Return a safe fallback
```

This is **defensive programming** — assume every external call can fail (network error, API down, invalid response) and handle it gracefully. The bot never crashes from a single failed API call.

### Cybersecurity connection

- **API Key Management**: The private key and API credentials are loaded from environment variables, never hardcoded. The `if POLYGON_PRIVATE_KEY:` guard means the bot gracefully degrades without credentials.
- **Rate Limiting**: You're implementing the same mechanism that WAFs (Web Application Firewalls) use to prevent abuse. Here you're on the client side, respecting the server's limits.
- **Timeout Configuration**: Without timeouts, a hung connection could be used as a resource exhaustion vector. Always set timeouts on HTTP clients.

---

## File 2: `src/data/news_collector.py` — News Articles

### Purpose

Fetches news articles that relate to prediction market topics. If the market asks "Will Bitcoin hit $100k?", this module finds recent Bitcoin news articles for the analysis engine to evaluate.

### Keyword Extraction

```python
def extract_keywords(self, question: str) -> str:
```
- Takes a full question: `"Will Bitcoin hit $100k by March 2026?"`
- Removes stop words: `"will"`, `"by"`, `"the"`, etc.
- Returns key terms: `"bitcoin hit $100k march 2026"`
- These become the search query for NewsAPI.

**What are stop words?** Common words that add grammar but not meaning. In NLP (Natural Language Processing), removing them helps search and analysis focus on the important words. This is a basic NLP technique you'll see in FinBERT's pipeline too.

### The Cache

```python
self.cache = {}
self.cache_ttl = 900  # 15 minutes
```
- **TTL = Time To Live.** Cached results expire after 15 minutes.
- **Why cache?** The agent checks markets every 30 minutes. Without caching, it would fetch the same articles twice per cycle (once for signal generation, once for LLM research). The cache prevents redundant API calls.
- **Cache key** = the lowercased query string. Same query → same cached result.

This is the same caching concept used in DNS (DNS TTL), CDNs, and browser caching. Data is stored temporarily to avoid repeated expensive lookups.

### NewsAPI Integration

```python
response = self.client.get_everything(
    q=query,
    language="en",
    sort_by="relevancy",
    page_size=max_articles,
)
```
- `get_everything()` — NewsAPI's main search endpoint. Searches across thousands of news sources.
- `sort_by="relevancy"` — Most relevant articles first, not most recent. For prediction markets, relevance matters more than recency (though both matter).
- The response contains: title, source, description, content, URL, published date.

### Cybersecurity connection

- **Input sanitization**: The `extract_keywords()` function strips the question down to safe search terms. While not a security measure per se, it prevents unexpected characters from reaching the API.
- **Cache poisoning awareness**: Our simple dict cache is fine for a single-process bot. In a web application, you'd need to worry about cache poisoning attacks. Worth knowing the concept.

---

## File 3: `src/data/sentiment_scraper.py` — Reddit Sentiment

### Purpose

Scrapes Reddit for public opinion on market topics. Reddit is a strong signal for prediction markets because:
- It's real-time — people discuss events as they happen
- It's diverse — subreddits exist for almost every topic
- Upvotes/downvotes provide a built-in relevance filter

### Topic-to-Subreddit Mapping

```python
TOPIC_SUBREDDIT_MAP = {
    "crypto": ["cryptocurrency", "bitcoin", "ethereum", "polymarket"],
    "politics": ["politics", "news", "worldnews", "polymarket"],
    ...
}
```
- A simple lookup table. If the market question contains "crypto", scrape r/cryptocurrency, r/bitcoin, etc.
- Falls back to `DEFAULT_SUBREDDITS` (r/polymarket, r/predictions, r/news) when no topic matches.
- Limited to 5 subreddits per query to avoid excessive API calls.

### PRAW (Python Reddit API Wrapper)

```python
self.reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent="polymarket-agent/1.0 (research bot)",
)
```
- **PRAW** abstracts Reddit's API into Python objects. Instead of making HTTP requests yourself, you call `subreddit.search()`.
- **`user_agent`** — Reddit requires a descriptive user agent string. Using a generic one (like "python") will get rate-limited aggressively. This is an API best practice.
- **Read-only mode** — We only search and read posts, never post or vote. No write credentials needed.

### Scraping Process

```python
for submission in subreddit.search(query, limit=limit, sort="relevance", time_filter="week"):
```
- Searches within a specific subreddit
- `time_filter="week"` — Only posts from the last 7 days. Prediction markets care about recent sentiment, not posts from 6 months ago.
- `sort="relevance"` — Reddit's own relevance ranking, which factors in upvotes
- For each post, we extract: title, full text, score (upvotes minus downvotes), number of comments

### Why return raw text?

The scraper doesn't analyze sentiment itself — it just collects text. The analysis happens in the next step (FinBERT). This is **separation of concerns**: each module does one thing well.

```
Reddit posts (raw text) ──→ FinBERT analyzer ──→ sentiment scores
```

### Cybersecurity connection

- **API Authentication**: Reddit uses OAuth2 client credentials flow — `client_id` and `client_secret`. Same auth pattern used in many enterprise APIs. If you've worked with OAuth in security contexts, this is the same concept.
- **User Agent Best Practice**: Identifying your bot honestly is both ethical and practical. Reddit blocks anonymous/generic user agents. In security, user agent strings are used for fingerprinting and access control.
- **Data collection ethics**: We only read public posts. No private messages, no user profiles, no scraping beyond public search results. The `user_agent` transparently identifies us as a research bot.

---

## How These Three Files Work Together

```
Agent Orchestrator says: "Analyze these markets"
         │
         ├──→ polymarket_client.get_markets()
         │         Returns: market questions, prices, token IDs
         │
         ├──→ news_collector.get_articles_for_markets(markets)
         │         Returns: {market_id: [article1, article2, ...]}
         │
         └──→ sentiment_scraper.get_sentiment_for_markets(markets)
                   Returns: {market_id: [reddit_post1, reddit_post2, ...]}

All three outputs feed into the Analysis Engine (Step 5):
  - Prices    → Signal Generator (to calculate edge)
  - Articles  → FinBERT (sentiment scores) + LLM Researcher (probability)
  - Posts     → FinBERT (sentiment scores)
```

---

## Key Python Concepts Introduced

| Concept | Where | What It Means |
|---------|-------|---------------|
| **Class** | All 3 files | A blueprint for objects. `PolymarketClient()` creates an instance with its own state (connections, cache). |
| **`self`** | All methods | Refers to the current instance. `self.cache` is THIS collector's cache, not a global one. |
| **`__init__`** | Constructor | Runs when you create an instance: `client = PolymarketClient()`. Sets up connections. |
| **`httpx.Client`** | polymarket_client | An HTTP client that reuses connections. Faster than creating a new connection per request. |
| **Type hints** | `-> list`, `-> dict \| None` | Tell you what a function returns. `dict \| None` means "a dict, or None if something went wrong." |
| **List comprehension** | `[m for m in list if cond]` | Compact way to filter/transform lists. Equivalent to a for-loop with an if-statement. |
| **`dict.get(key, default)`** | Many places | Safe dict access. Returns `default` if key is missing instead of crashing with KeyError. |
| **`try/except`** | Every API call | Catches errors so one failed API call doesn't crash the entire bot. |
| **Context manager** | `httpx.Client` | Objects that clean up after themselves. The client closes connections when done. |
| **`time.sleep()`** | RateLimiter | Pauses execution for N seconds. The rate limiter uses this to space out requests. |
