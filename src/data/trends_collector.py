"""
Google Trends collector — measures public search interest for market topics.

Uses the pytrends library (unofficial Google Trends API) to fetch 30-day
search interest for keywords extracted from each market question. Results
are returned as synthetic article dicts so they feed directly into the
existing Claude + FinBERT analysis pipeline with no other changes needed.

Output format per market (if data found):
  "Google Trends data for 'india pakistan': Current search interest: 72/100
   (30-day average: 45/100). Trend direction: rising."

A rising trend on a geopolitical topic is meaningful signal — it suggests
the event is gaining public attention, which often precedes price moves on
Polymarket.

Note: pytrends is an unofficial wrapper around Google's UI. Google may
occasionally block requests (HTTP 429). All failures are caught and logged
— the rest of the pipeline is unaffected.
"""

import re
import time

from src.utils.logger import setup_logger

logger = setup_logger("trends_collector")

# Stop words — stripped before building the search query
_STOP_WORDS = {
    "will", "the", "be", "by", "in", "on", "at", "to", "of", "a", "an",
    "is", "it", "this", "that", "and", "or", "for", "before", "after",
    "during", "yes", "no", "does", "do", "has", "have", "can", "could",
    "would", "should", "least", "most", "more", "than", "any", "all",
    "end", "win", "lose", "hit", "reach", "over", "under", "first",
    "new", "next", "get", "make", "take", "use", "say", "see", "come",
}


class TrendsCollector:
    """Fetches Google Trends search interest for market question keywords."""

    def __init__(self):
        self._cache: dict = {}       # {query: (timestamp, article_or_none)}
        self._cache_ttl = 3600       # 1 hour — trends data changes slowly
        self._last_request = 0.0
        self._min_interval = 2.0     # Seconds between requests — avoids 429s

    def get_trends_for_markets(self, markets: list) -> dict:
        """Return a synthetic trend article per market (where data exists).

        Args:
            markets: List of market dicts with 'question' key.

        Returns:
            Dict mapping market_id → [article_dict]. Empty list if no data.
        """
        results = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "")
            if not question:
                continue

            keywords = self._extract_keywords(question)
            if not keywords:
                continue

            query = " ".join(keywords[:3])
            article = self._get_trend_article(query)
            if article:
                results[market_id] = [article]

        return results

    def _get_trend_article(self, query: str) -> dict | None:
        """Fetch trend data for a query, using cache where fresh."""
        now = time.time()

        # Return cached result if still fresh
        cached_time, cached_article = self._cache.get(query, (0, None))
        if now - cached_time < self._cache_ttl:
            return cached_article

        # Rate limit — don't hammer Google
        elapsed = now - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        try:
            # Import here so a missing pytrends install doesn't crash startup
            from pytrends.request import TrendReq
            from pytrends.exceptions import TooManyRequestsError

            pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
            pytrends.build_payload([query], timeframe="today 1-m")
            data = pytrends.interest_over_time()
            self._last_request = time.time()

            if data.empty or query not in data.columns:
                self._cache[query] = (now, None)
                return None

            series = data[query]
            avg_interest = int(series.mean())
            current = int(series.iloc[-1])

            # Compare last week to first week to determine trend direction
            first_week = series.iloc[:4].mean()
            last_week = series.iloc[-4:].mean()
            if last_week > first_week * 1.15:
                direction = "rising"
            elif last_week < first_week * 0.85:
                direction = "declining"
            else:
                direction = "stable"

            summary = (
                f"Google Trends data for '{query}': "
                f"Current search interest {current}/100 "
                f"(30-day average: {avg_interest}/100). "
                f"Search trend over the past month: {direction}."
            )

            article = {
                "title": f"Google Trends: {query} — interest {current}/100, {direction}",
                "source": "Google Trends",
                "description": summary,
                "content": summary,
                "url": "",
                "published_at": "",
            }

            logger.info(
                "Trends: '%s' — interest %d/100, %s",
                query, current, direction
            )
            self._cache[query] = (now, article)
            return article

        except Exception as e:
            # TooManyRequestsError, connection errors, etc. — all non-fatal
            logger.warning("Trends: failed for '%s': %s", query, type(e).__name__)
            self._last_request = time.time()
            self._cache[query] = (now, None)
            return None

    def _extract_keywords(self, question: str) -> list[str]:
        """Extract the most meaningful keywords from a market question."""
        words = re.findall(r"[\w$£€]+", question.lower())
        return [w for w in words if w not in _STOP_WORDS and len(w) > 2][:3]
