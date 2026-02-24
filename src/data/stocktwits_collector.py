"""
Stocktwits collector — fetches social sentiment for crypto-related markets.

Stocktwits is a finance-focused social network where traders post short-form
analysis tagged with $CASHTAG symbols (e.g. $BTC, $ETH). Posts are optionally
tagged Bullish or Bearish by the poster, giving a direct crowd sentiment signal.

For any Polymarket question that mentions a known cryptocurrency, this fetches
the most recent posts for that cashtag and calculates a bullish/bearish ratio.

Output example:
  "Stocktwits social sentiment for $BTC: 14 Bullish vs 6 Bearish (70% bullish)
   — moderately bullish. Sample posts: [up only | this will pump | ...]"

Why this is valuable: Stocktwits is purpose-built for finance. Unlike general
social media, every user is here to talk about markets. The bullish/bearish tags
are voluntary but widely used — a 70%+ bullish reading on a crypto cashtag is
meaningful signal when assessing "Will BTC reach $X?" markets.

API: https://api.stocktwits.com/api/2/ — free, no auth required (200 req/hour).
Results cached for 15 minutes.
"""

import time

import httpx

from src.utils.logger import setup_logger

logger = setup_logger("stocktwits_collector")

# Maps market question keywords to Stocktwits cashtag symbols
# Crypto symbols use .X suffix on Stocktwits (e.g. BTC.X)
KEYWORD_TO_SYMBOL: dict[str, str] = {
    "bitcoin": "BTC.X",
    "btc": "BTC.X",
    "ethereum": "ETH.X",
    "eth": "ETH.X",
    "bnb": "BNB.X",
    "binance": "BNB.X",
    "solana": "SOL.X",
    "sol": "SOL.X",
    "xrp": "XRP.X",
    "ripple": "XRP.X",
    "dogecoin": "DOGE.X",
    "doge": "DOGE.X",
    "cardano": "ADA.X",
    "ada": "ADA.X",
    "avalanche": "AVAX.X",
    "avax": "AVAX.X",
    "chainlink": "LINK.X",
    "link": "LINK.X",
    "polkadot": "DOT.X",
    "dot": "DOT.X",
    "litecoin": "LTC.X",
    "ltc": "LTC.X",
    "polygon": "MATIC.X",
    "matic": "MATIC.X",
}

_BASE_URL = "https://api.stocktwits.com/api/2"


class StocktwitsCollector:
    """Fetches Stocktwits social sentiment for crypto prediction markets."""

    def __init__(self):
        self._cache: dict = {}    # {symbol: (timestamp, article_or_none)}
        self._cache_ttl = 900     # 15 minutes

    def get_sentiment_for_markets(self, markets: list) -> dict:
        """Return a synthetic sentiment article for crypto markets.

        Args:
            markets: List of market dicts with 'question' key.

        Returns:
            Dict mapping market_id → [article_dict]. Empty if not crypto.
        """
        results = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "")
            if not question:
                continue

            symbol = self._detect_symbol(question)
            if not symbol:
                continue

            article = self._get_sentiment_article(symbol)
            if article:
                results[market_id] = [article]

        return results

    def _detect_symbol(self, question: str) -> str | None:
        """Return the Stocktwits symbol if the question mentions a known coin."""
        question_lower = question.lower()
        for keyword, symbol in KEYWORD_TO_SYMBOL.items():
            if keyword in question_lower:
                return symbol
        return None

    def _get_sentiment_article(self, symbol: str) -> dict | None:
        """Fetch Stocktwits sentiment for a symbol, using cache where fresh."""
        now = time.time()

        cached_time, cached_article = self._cache.get(symbol, (0, None))
        if now - cached_time < self._cache_ttl:
            return cached_article

        try:
            url = f"{_BASE_URL}/streams/symbol/{symbol}.json"
            with httpx.Client(timeout=10) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()

            messages = data.get("messages", [])
            if not messages:
                self._cache[symbol] = (now, None)
                return None

            # Count bullish/bearish tags and collect post excerpts
            bullish = 0
            bearish = 0
            excerpts = []

            for msg in messages[:30]:  # Analyse last 30 posts
                sentiment_tag = msg.get("entities", {}).get("sentiment")
                if sentiment_tag:
                    if sentiment_tag.get("basic") == "Bullish":
                        bullish += 1
                    elif sentiment_tag.get("basic") == "Bearish":
                        bearish += 1

                # Collect short excerpts for Claude to read
                body = msg.get("body", "").strip()
                if body and len(excerpts) < 5:
                    excerpts.append(body[:100])

            total_tagged = bullish + bearish
            if total_tagged == 0:
                self._cache[symbol] = (now, None)
                return None

            bullish_pct = round(100 * bullish / total_tagged)

            sentiment_label = (
                "strongly bullish" if bullish_pct >= 70 else
                "moderately bullish" if bullish_pct >= 55 else
                "neutral" if bullish_pct >= 45 else
                "moderately bearish" if bullish_pct >= 30 else
                "strongly bearish"
            )

            ticker = symbol.replace(".X", "")
            excerpts_text = " | ".join(excerpts) if excerpts else "no excerpts"

            summary = (
                f"Stocktwits social sentiment for ${ticker}: "
                f"{bullish} Bullish vs {bearish} Bearish ({bullish_pct}% bullish) — {sentiment_label}. "
                f"Sample posts: {excerpts_text}"
            )

            article = {
                "title": f"Stocktwits: ${ticker} — {bullish_pct}% bullish ({sentiment_label})",
                "source": "Stocktwits",
                "description": summary,
                "content": summary,
                "url": f"https://stocktwits.com/symbol/{symbol}",
                "published_at": "",
            }

            logger.info(
                "Stocktwits: %s — %d bullish, %d bearish (%d%% bullish, %s)",
                symbol, bullish, bearish, bullish_pct, sentiment_label
            )
            self._cache[symbol] = (now, article)
            return article

        except httpx.HTTPStatusError as e:
            logger.warning("Stocktwits: HTTP %d for %s", e.response.status_code, symbol)
        except httpx.RequestError as e:
            logger.warning("Stocktwits: connection error for %s: %s", symbol, type(e).__name__)
        except Exception as e:
            logger.warning("Stocktwits: unexpected error for %s: %s", symbol, type(e).__name__)

        self._cache[symbol] = (now, None)
        return None
