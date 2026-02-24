"""
Metaculus collector — fetches expert community forecasts for market questions.

Metaculus is a forecasting platform where trained forecasters make calibrated
probability predictions on real-world questions. Their community forecasts are
publicly accessible via a free REST API.

For each Polymarket question, this searches Metaculus for the closest matching
open question and returns the community's median probability estimate.

This is high-value signal: if Polymarket prices a question at 15% but
Metaculus forecasters (who are systematically well-calibrated) say 35%,
that gap is exactly the kind of mispricing the bot is looking for.

Output example:
  "Metaculus community forecast for 'Will the US invade Iran by March 31?':
   Median probability: 4% (based on 112 forecasters). Metaculus question:
   'US military strikes on Iran in Q1 2026?'"

API: https://www.metaculus.com/api2/ — free, no authentication required.
Results cached for 1 hour (forecasts change slowly).
"""

import re
import time

import httpx

from src.utils.logger import setup_logger

logger = setup_logger("metaculus_collector")

_BASE_URL = "https://www.metaculus.com/api2"

# Stop words for keyword extraction
_STOP_WORDS = {
    "will", "the", "be", "by", "in", "on", "at", "to", "of", "a", "an",
    "is", "it", "this", "that", "and", "or", "for", "before", "after",
    "during", "yes", "no", "does", "do", "has", "have", "can", "could",
    "would", "should", "least", "most", "more", "than", "any", "all",
    "end", "win", "lose", "hit", "reach", "over", "under", "first",
}


class MetaculusCollector:
    """Searches Metaculus for expert probability forecasts matching Polymarket questions."""

    def __init__(self):
        self._cache: dict = {}    # {question_text: (timestamp, article_or_none)}
        self._cache_ttl = 3600    # 1 hour — forecasts change slowly
        self._last_request = 0.0
        self._min_interval = 1.0  # Be respectful to Metaculus servers

    def get_forecasts_for_markets(self, markets: list) -> dict:
        """Return a Metaculus forecast article for each market where a match is found.

        Args:
            markets: List of market dicts with 'question' key.

        Returns:
            Dict mapping market_id → [article_dict]. Empty if no match found.
        """
        results = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "")
            if not question:
                continue

            article = self._get_forecast_article(question)
            if article:
                results[market_id] = [article]

        return results

    def _get_forecast_article(self, question: str) -> dict | None:
        """Search Metaculus and return a forecast article if a good match exists."""
        now = time.time()

        # Return cached result if still fresh
        cached_time, cached_article = self._cache.get(question, (0, None))
        if now - cached_time < self._cache_ttl:
            return cached_article

        # Rate limit
        elapsed = now - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        keywords = self._extract_keywords(question)
        if not keywords:
            self._cache[question] = (now, None)
            return None

        search_query = " ".join(keywords[:4])

        try:
            url = f"{_BASE_URL}/questions/"
            params = {
                "search": search_query,
                "status": "open",
                "forecast_type": "binary",
                "limit": 5,
                "format": "json",
            }

            with httpx.Client(timeout=10) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            self._last_request = time.time()
            results = data.get("results", [])

            if not results:
                self._cache[question] = (now, None)
                return None

            # Score each result by keyword overlap with our question
            best = self._best_match(results, keywords)
            if best is None:
                self._cache[question] = (now, None)
                return None

            # Extract community probability
            prob = self._extract_probability(best)
            if prob is None:
                self._cache[question] = (now, None)
                return None

            num_forecasters = best.get("number_of_predictions", 0)
            meta_title = best.get("title", "unknown")
            prob_pct = round(prob * 100, 1)

            # Only use if there are enough forecasters to be meaningful
            if num_forecasters < 10:
                self._cache[question] = (now, None)
                return None

            summary = (
                f"Metaculus expert community forecast: {prob_pct}% probability "
                f"(based on {num_forecasters} forecasters). "
                f"Matched question: '{meta_title}'. "
                f"Metaculus forecasters are systematically well-calibrated — "
                f"this estimate should be weighted heavily alongside market price."
            )

            article = {
                "title": f"Metaculus forecast: {prob_pct}% probability ({num_forecasters} forecasters)",
                "source": "Metaculus",
                "description": summary,
                "content": summary,
                "url": f"https://www.metaculus.com/questions/{best.get('id', '')}",
                "published_at": "",
            }

            logger.info(
                "Metaculus: '%s' → %.0f%% (%d forecasters) [matched: '%s']",
                question[:40], prob_pct, num_forecasters, meta_title[:40]
            )
            self._cache[question] = (now, article)
            return article

        except httpx.HTTPStatusError as e:
            logger.warning("Metaculus: HTTP %d for '%s'", e.response.status_code, question[:40])
        except httpx.RequestError as e:
            logger.warning("Metaculus: connection error: %s", type(e).__name__)
        except Exception as e:
            logger.warning("Metaculus: unexpected error: %s", type(e).__name__)

        self._last_request = time.time()
        self._cache[question] = (now, None)
        return None

    def _best_match(self, results: list, keywords: list[str]) -> dict | None:
        """Return the result with the most keyword overlap, or None if score is too low."""
        scored = []
        for result in results:
            title = result.get("title", "").lower()
            score = sum(1 for kw in keywords if kw in title)
            if score > 0:
                scored.append((score, result))

        if not scored:
            return None

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_result = scored[0]

        # Require at least 2 keyword matches to avoid garbage matches
        if best_score < 2:
            return None

        return best_result

    def _extract_probability(self, question: dict) -> float | None:
        """Extract the community median probability from a Metaculus question."""
        try:
            cp = question.get("community_prediction")
            if not cp:
                return None

            # Metaculus returns nested prediction data
            full = cp.get("full")
            if full and isinstance(full, dict):
                q2 = full.get("q2")  # median
                if q2 is not None:
                    return float(q2)

            # Fallback: top-level q2
            q2 = cp.get("q2")
            if q2 is not None:
                return float(q2)

            return None
        except (TypeError, ValueError):
            return None

    def _extract_keywords(self, question: str) -> list[str]:
        """Extract meaningful keywords from a market question."""
        words = re.findall(r"[\w$£€]+", question.lower())
        return [w for w in words if w not in _STOP_WORDS and len(w) > 2][:4]
