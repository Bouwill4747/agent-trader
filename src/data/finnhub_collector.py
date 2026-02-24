"""
Finnhub collector — real-time stock/index quotes and financial news.

Two data sources, both on the free tier:

1. Financial news (/news) — general + crypto categories.
   Professional financial headlines from Bloomberg, Reuters, CNBC, etc.
   Applied to markets where headline keywords match the market question.
   Cache TTL: 30 minutes.

2. Real-time stock quotes (/quote) — SPY, QQQ always; additional stocks
   detected from the market question (TSLA, NVDA, AAPL, etc.).
   Provides live price context for markets about equities or broad market.
   Cache TTL: 5 minutes.

API: https://finnhub.io/api/v1/ — free tier, requires API key.
Get a free key at: https://finnhub.io/register
"""

import re
import time
from datetime import datetime, timezone

import httpx

from config.settings import FINNHUB_API_KEY
from src.utils.logger import setup_logger

logger = setup_logger("finnhub_collector")

_BASE_URL = "https://finnhub.io/api/v1"

# Stopwords excluded from keyword matching
_STOPWORDS = {
    "will", "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "to", "for", "of", "in",
    "on", "at", "by", "with", "from", "than", "that", "this", "and", "or",
    "but", "not", "no", "its", "it", "if", "as", "so", "we", "they", "he",
    "she", "you", "i", "my", "our", "up", "down", "get", "go", "more", "hit",
    "reach", "exceed", "above", "below", "end", "year", "month", "week", "day",
    "before", "after", "during", "over", "under",
}

# Keyword → Finnhub symbol mapping for quote detection
_STOCK_KEYWORDS: dict[str, list[str]] = {
    "SPY":   ["s&p 500", "s&p500", "s&p", "stock market", "spy", "dow jones", "dow"],
    "QQQ":   ["nasdaq", "qqq", "tech stock", "technology index", "tech index"],
    "TSLA":  ["tesla", "tsla"],
    "NVDA":  ["nvidia", "nvda"],
    "AAPL":  ["apple", "aapl"],
    "MSFT":  ["microsoft", "msft"],
    "AMZN":  ["amazon", "amzn"],
    "META":  ["meta", "facebook"],
    "GOOGL": ["google", "alphabet", "googl"],
}

# Human-readable name for each symbol
_STOCK_META: dict[str, str] = {
    "SPY":   "S&P 500 ETF",
    "QQQ":   "Nasdaq-100 ETF",
    "TSLA":  "Tesla",
    "NVDA":  "NVIDIA",
    "AAPL":  "Apple",
    "MSFT":  "Microsoft",
    "AMZN":  "Amazon",
    "META":  "Meta Platforms",
    "GOOGL": "Alphabet (Google)",
}

# Crypto keywords — triggers the crypto news category
_CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency",
    "defi", "blockchain", "altcoin",
]


class FinnhubCollector:
    """Fetches Finnhub financial news and real-time stock quotes."""

    def __init__(self):
        self._news_cache: tuple = (0, [])   # (timestamp, [article_dicts])
        self._quote_cache: tuple = (0, {})  # (timestamp, {symbol: quote_data})
        self._news_ttl = 1800   # 30 min
        self._quote_ttl = 300   # 5 min

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def get_news_for_markets(self, markets: list) -> dict:
        """Return relevant Finnhub news articles per market.

        Fetches general + crypto news categories. Articles are keyword-matched
        against each market question; up to 5 matching articles per market.

        Returns:
            Dict mapping market_id → [article_dicts].
        """
        if not FINNHUB_API_KEY:
            return {}

        all_articles = self._fetch_news()
        if not all_articles:
            return {}

        results: dict[str, list] = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "")
            keywords = _market_keywords(question)

            matched = [a for a in all_articles if _article_matches(a, keywords)]
            if matched:
                results[market_id] = matched[:5]

        return results

    def get_quotes_for_markets(self, markets: list) -> dict:
        """Return real-time stock/index quote articles per market.

        SPY and QQQ are always included as broad market context.
        Additional symbols are included when the market question mentions them.

        Returns:
            Dict mapping market_id → [article_dicts].
        """
        if not FINNHUB_API_KEY:
            return {}

        results: dict[str, list] = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "").lower()

            # Always include broad market indices; add individual stocks on match
            symbols: set[str] = {"SPY", "QQQ"}
            for symbol, kw_list in _STOCK_KEYWORDS.items():
                if any(kw in question for kw in kw_list):
                    symbols.add(symbol)

            quotes = self._get_quotes(symbols)
            articles = [
                self._quote_to_article(symbol, data)
                for symbol, data in quotes.items()
                if data
            ]

            if articles:
                results[market_id] = articles

        return results

    # ──────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────

    def _fetch_news(self) -> list:
        """Fetch general + crypto news from Finnhub, with cache."""
        now = time.time()
        cached_time, cached_articles = self._news_cache
        if now - cached_time < self._news_ttl:
            return cached_articles

        articles = []
        try:
            with httpx.Client(timeout=15) as client:
                for category in ("general", "crypto"):
                    try:
                        resp = client.get(
                            f"{_BASE_URL}/news",
                            params={"category": category, "token": FINNHUB_API_KEY},
                        )
                        resp.raise_for_status()
                        items = resp.json()
                        parsed = self._parse_news_items(items, category)
                        articles.extend(parsed)
                        logger.debug("Finnhub news/%s: %d items", category, len(parsed))
                    except httpx.HTTPStatusError as e:
                        logger.warning("Finnhub news/%s: HTTP %d", category, e.response.status_code)
                    except Exception as e:
                        logger.warning("Finnhub news/%s: %s", category, type(e).__name__)
        except Exception as e:
            logger.warning("Finnhub: connection error: %s", type(e).__name__)

        logger.info("Finnhub: fetched %d news articles (general + crypto)", len(articles))
        self._news_cache = (now, articles)
        return articles

    def _parse_news_items(self, items: list, category: str) -> list:
        """Convert raw Finnhub news items to standard article dicts."""
        result = []
        for item in items[:20]:  # cap at 20 per category
            try:
                ts = item.get("datetime", 0)
                published = (
                    datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    if ts else ""
                )
                result.append({
                    "title": item.get("headline", ""),
                    "source": f"Finnhub/{item.get('source', category)}",
                    "description": item.get("summary", ""),
                    "content": item.get("summary", ""),
                    "url": item.get("url", ""),
                    "published_at": published,
                })
            except Exception:
                continue
        return result

    def _get_quotes(self, symbols: set) -> dict:
        """Fetch quotes for the given symbols, with cache.

        Returns dict mapping symbol → quote_data (or None on failure).
        """
        now = time.time()
        cached_time, cached_quotes = self._quote_cache

        # If cache is fresh, serve from it
        if now - cached_time < self._quote_ttl:
            return {s: cached_quotes.get(s) for s in symbols}

        # Refresh: fetch all requested symbols
        fresh: dict = {}
        try:
            with httpx.Client(timeout=10) as client:
                for symbol in symbols:
                    try:
                        resp = client.get(
                            f"{_BASE_URL}/quote",
                            params={"symbol": symbol, "token": FINNHUB_API_KEY},
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        if data.get("c", 0) > 0:  # c = current price, 0 means no data
                            fresh[symbol] = data
                            logger.debug("Finnhub quote: %s = $%.2f", symbol, data["c"])
                    except httpx.HTTPStatusError as e:
                        logger.warning("Finnhub quote/%s: HTTP %d", symbol, e.response.status_code)
                    except Exception as e:
                        logger.warning("Finnhub quote/%s: %s", symbol, type(e).__name__)
        except Exception as e:
            logger.warning("Finnhub quotes: connection error: %s", type(e).__name__)

        # Merge fresh data into cache (preserves symbols not fetched this round)
        merged = dict(cached_quotes)
        merged.update(fresh)
        self._quote_cache = (now, merged)
        return {s: fresh.get(s) for s in symbols}

    def _quote_to_article(self, symbol: str, data: dict) -> dict:
        """Format a Finnhub quote as a standard article dict for Claude."""
        current = data.get("c", 0)
        prev_close = data.get("pc", 0)
        high = data.get("h", 0)
        low = data.get("l", 0)
        change = current - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0

        name = _STOCK_META.get(symbol, symbol)
        direction = "up" if change >= 0 else "down"

        content = (
            f"Real-time quote for {name} ({symbol}):\n"
            f"Current: ${current:.2f} | "
            f"Day range: ${low:.2f} – ${high:.2f} | "
            f"Previous close: ${prev_close:.2f}\n"
            f"Change: {change:+.2f} ({change_pct:+.2f}%) — {direction} from yesterday."
        )

        return {
            "title": f"{name} ({symbol}): ${current:.2f} ({change_pct:+.1f}%)",
            "source": "Finnhub/Quote",
            "description": content,
            "content": content,
            "url": "https://finnhub.io/",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }


# ──────────────────────────────────────────────
# Keyword matching helpers (module-level, reused by orchestrator dedup)
# ──────────────────────────────────────────────

def _market_keywords(question: str) -> set[str]:
    """Extract meaningful keywords from a market question, excluding stopwords."""
    words = re.findall(r'\b[a-z]+\b', question.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _article_matches(article: dict, keywords: set[str]) -> bool:
    """Return True if the article headline/summary shares keywords with the market."""
    if not keywords:
        return False
    text = (article.get("title", "") + " " + article.get("description", "")).lower()
    article_words = set(re.findall(r'\b[a-z]+\b', text))
    return bool(keywords & article_words)
