"""
CoinGecko collector — fetches real crypto price data for coin-related markets.

For any market question that mentions a known cryptocurrency, this fetches
the current price, 24-hour change, and 7-day change from CoinGecko's free
public API (no API key required).

Results are returned as synthetic article dicts so they feed directly into
the Claude analysis pipeline. Claude can then factor in whether, e.g.,
BNB is currently up 12% this week when assessing "Will BNB reach $1600?"

Output example:
  "CoinGecko market data for BNB (binancecoin): Price $680.42 USD.
   24h change: +3.2%. 7-day change: +11.8%. Market cap: $99.2B."

CoinGecko free tier: ~30 req/min. Results cached for 10 minutes.
"""

import time

import httpx

from src.utils.logger import setup_logger

logger = setup_logger("coingecko_collector")

# Maps keywords found in market questions to CoinGecko coin IDs
COIN_KEYWORDS: dict[str, str] = {
    "bitcoin": "bitcoin",
    "btc": "bitcoin",
    "ethereum": "ethereum",
    "eth": "ethereum",
    "bnb": "binancecoin",
    "binance": "binancecoin",
    "solana": "solana",
    "sol": "solana",
    "xrp": "ripple",
    "ripple": "ripple",
    "dogecoin": "dogecoin",
    "doge": "dogecoin",
    "cardano": "cardano",
    "ada": "cardano",
    "avalanche": "avalanche-2",
    "avax": "avalanche-2",
    "chainlink": "chainlink",
    "link": "chainlink",
    "polkadot": "polkadot",
    "dot": "polkadot",
    "litecoin": "litecoin",
    "ltc": "litecoin",
    "polygon": "matic-network",
    "matic": "matic-network",
}

_BASE_URL = "https://api.coingecko.com/api/v3"


class CoinGeckoCollector:
    """Fetches crypto price context for coin-related prediction markets."""

    def __init__(self):
        self._cache: dict = {}    # {coin_id: (timestamp, article_or_none)}
        self._cache_ttl = 600     # 10 minutes — prices change but not that fast

    def get_price_context_for_markets(self, markets: list) -> dict:
        """Return a synthetic price article for any crypto-related market.

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

            coin_id = self._detect_coin(question)
            if not coin_id:
                continue

            article = self._get_price_article(coin_id)
            if article:
                results[market_id] = [article]

        return results

    def _detect_coin(self, question: str) -> str | None:
        """Return the CoinGecko coin ID if the question mentions a known coin."""
        question_lower = question.lower()
        for keyword, coin_id in COIN_KEYWORDS.items():
            if keyword in question_lower:
                return coin_id
        return None

    def _get_price_article(self, coin_id: str) -> dict | None:
        """Fetch price data for a coin, using cache where fresh."""
        now = time.time()

        cached_time, cached_article = self._cache.get(coin_id, (0, None))
        if now - cached_time < self._cache_ttl:
            return cached_article

        try:
            url = f"{_BASE_URL}/simple/price"
            params = {
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_market_cap": "true",
                "include_24hr_change": "true",
                "include_7d_change": "true",
                "price_change_percentage": "24h,7d",
            }

            with httpx.Client(timeout=10) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            if coin_id not in data:
                self._cache[coin_id] = (now, None)
                return None

            coin_data = data[coin_id]
            price = coin_data.get("usd", 0)
            change_24h = coin_data.get("usd_24h_change", 0) or 0
            change_7d = coin_data.get("usd_7d_change", 0) or 0
            market_cap = coin_data.get("usd_market_cap", 0) or 0

            # Format market cap as readable string
            if market_cap >= 1e9:
                mcap_str = f"${market_cap / 1e9:.1f}B"
            elif market_cap >= 1e6:
                mcap_str = f"${market_cap / 1e6:.1f}M"
            else:
                mcap_str = f"${market_cap:,.0f}"

            # Describe momentum in plain text for Claude to interpret
            momentum_7d = (
                "strong upward momentum" if change_7d > 10 else
                "moderate gains" if change_7d > 3 else
                "slight gains" if change_7d > 0 else
                "slight decline" if change_7d > -3 else
                "moderate decline" if change_7d > -10 else
                "strong downward momentum"
            )

            summary = (
                f"CoinGecko live market data for {coin_id}: "
                f"Price ${price:,.2f} USD. "
                f"24-hour change: {change_24h:+.2f}%. "
                f"7-day change: {change_7d:+.2f}% ({momentum_7d}). "
                f"Market cap: {mcap_str}."
            )

            article = {
                "title": f"CoinGecko: {coin_id} ${price:,.2f} ({change_7d:+.1f}% 7d)",
                "source": "CoinGecko",
                "description": summary,
                "content": summary,
                "url": "",
                "published_at": "",
            }

            logger.info(
                "CoinGecko: %s $%.2f | 24h: %+.1f%% | 7d: %+.1f%%",
                coin_id, price, change_24h, change_7d
            )
            self._cache[coin_id] = (now, article)
            return article

        except httpx.HTTPStatusError as e:
            logger.warning("CoinGecko: HTTP %d for %s", e.response.status_code, coin_id)
        except httpx.RequestError as e:
            logger.warning("CoinGecko: connection error for %s: %s", coin_id, type(e).__name__)
        except Exception as e:
            logger.warning("CoinGecko: unexpected error for %s: %s", coin_id, type(e).__name__)

        self._cache[coin_id] = (now, None)
        return None
