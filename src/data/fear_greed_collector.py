"""
Fear & Greed Index collector — crypto market sentiment from Alternative.me.

The Crypto Fear & Greed Index measures overall market psychology on a 0–100 scale:
  0–24:  Extreme Fear  (panic selling, historically near bottoms)
  25–44: Fear          (cautious market, selling pressure)
  45–55: Neutral       (balanced, no strong directional bias)
  56–74: Greed         (optimistic, buying pressure)
  75–100: Extreme Greed (euphoria, historically near tops)

For any Polymarket crypto price question, this gives Claude instant context on
market psychology — which often drives short-term price action more than
fundamentals. Extreme Fear often precedes bounces; Extreme Greed precedes dips.

API: https://api.alternative.me/fng/ — free, no authentication required.
Results cached for 1 hour (index updates once per day).
"""

import time

import httpx

from src.utils.logger import setup_logger

logger = setup_logger("fear_greed_collector")

_BASE_URL = "https://api.alternative.me/fng/"

# Keywords indicating a crypto-related market
_CRYPTO_KEYWORDS = {
    "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
    "bnb", "xrp", "ripple", "doge", "dogecoin", "cardano", "ada",
    "avalanche", "avax", "chainlink", "link", "polkadot", "dot",
    "litecoin", "ltc", "polygon", "matic", "coin", "token", "defi",
    "blockchain", "altcoin",
}


class FearGreedCollector:
    """Fetches the Crypto Fear & Greed Index for crypto prediction markets."""

    def __init__(self):
        self._cache: tuple = (0, None)  # (timestamp, article_or_none)
        self._cache_ttl = 3600          # 1 hour
        self._current_value: int | None = None  # raw 0-100 score

    def get_current_value(self) -> int | None:
        """Return the current Fear & Greed index value (0-100), or None if unavailable.

        Triggers a fetch if the cache is stale; otherwise returns cached value.
        """
        self._get_index_article()  # populates _current_value as a side effect
        return self._current_value

    def get_index_for_markets(self, markets: list) -> dict:
        """Return the Fear & Greed Index article for any crypto-related market.

        Args:
            markets: List of market dicts with 'question' key.

        Returns:
            Dict mapping market_id → [article_dict]. Empty if not crypto.
        """
        article = self._get_index_article()
        if not article:
            return {}

        results = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "").lower()
            if any(kw in question for kw in _CRYPTO_KEYWORDS):
                results[market_id] = [article]

        return results

    def _get_index_article(self) -> dict | None:
        """Fetch the current Fear & Greed Index, using cache where fresh."""
        now = time.time()
        cached_time, cached_article = self._cache

        if now - cached_time < self._cache_ttl:
            return cached_article

        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(_BASE_URL, params={"limit": 1})
                response.raise_for_status()
                data = response.json()

            entry = data.get("data", [{}])[0]
            value = int(entry.get("value", 50))
            classification = entry.get("value_classification", "Neutral")
            self._current_value = value

            if value <= 24:
                interpretation = (
                    "historically associated with market bottoms — assets may be undervalued. "
                    "Contrarian signal: high fear often precedes price recoveries."
                )
            elif value <= 44:
                interpretation = (
                    "investors are cautious — selling pressure dominates. "
                    "Prices may be under pressure short-term."
                )
            elif value <= 55:
                interpretation = "market is balanced with no strong directional bias."
            elif value <= 74:
                interpretation = (
                    "investors are optimistic — buying pressure dominates. "
                    "Momentum is positive but watch for overextension."
                )
            else:
                interpretation = (
                    "historically associated with market tops — elevated correction risk. "
                    "Contrarian signal: extreme greed often precedes pullbacks."
                )

            summary = (
                f"Crypto Fear & Greed Index: {value}/100 — {classification}. "
                f"Context: {interpretation} "
                f"Index aggregates: price volatility, market momentum, social media volume, "
                f"Bitcoin dominance, and Google Trends data."
            )

            article = {
                "title": f"Fear & Greed Index: {value}/100 ({classification})",
                "source": "Alternative.me",
                "description": summary,
                "content": summary,
                "url": "https://alternative.me/crypto/fear-and-greed-index/",
                "published_at": "",
            }

            logger.info("Fear & Greed: %d/100 (%s)", value, classification)
            self._cache = (now, article)
            return article

        except httpx.HTTPStatusError as e:
            logger.warning("Fear & Greed: HTTP %d", e.response.status_code)
        except httpx.RequestError as e:
            logger.warning("Fear & Greed: connection error: %s", type(e).__name__)
        except Exception as e:
            logger.warning("Fear & Greed: unexpected error: %s", type(e).__name__)

        self._cache = (now, None)
        return None
