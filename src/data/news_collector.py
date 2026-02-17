"""
News collector — fetches relevant articles for market research.
Uses NewsAPI to search for articles matching market keywords.
Implements a simple cache to avoid redundant API calls.
"""

import time
import re
from newsapi import NewsApiClient

from config.settings import NEWS_API_KEY
from src.utils.logger import setup_logger

logger = setup_logger("news_collector")


class NewsCollector:
    """Collects news articles relevant to prediction market topics."""

    def __init__(self):
        self.client = None
        if NEWS_API_KEY:
            self.client = NewsApiClient(api_key=NEWS_API_KEY)
            logger.info("NewsAPI client initialized")
        else:
            logger.warning("No NEWS_API_KEY — news collection disabled")

        # Simple cache: {query_string: (timestamp, results)}
        self.cache = {}
        self.cache_ttl = 900  # 15 minutes

    def extract_keywords(self, question: str) -> str:
        """Pull searchable keywords from a market question.

        Example:
            'Will Bitcoin hit $100k by March 2026?' → 'Bitcoin $100k'
            'Will the Fed cut interest rates?' → 'Fed cut interest rates'
        """
        # Remove common filler words
        stop_words = {
            "will", "the", "be", "by", "in", "on", "at", "to", "of",
            "a", "an", "is", "it", "this", "that", "and", "or", "for",
            "before", "after", "during", "yes", "no", "does", "do",
            "has", "have", "can", "could", "would", "should",
        }
        # Keep words that aren't stop words and are at least 2 chars
        words = re.findall(r"[\w$]+", question.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 1]

        # Take the most important keywords (first 5)
        query = " ".join(keywords[:5])
        return query

    def get_articles(self, question: str, max_articles: int = 10) -> list:
        """Fetch news articles relevant to a market question.

        Args:
            question: The market question (e.g., 'Will BTC hit $100k?')
            max_articles: Maximum articles to return

        Returns:
            List of dicts with keys: title, source, description, url, published_at
        """
        if not self.client:
            logger.warning("NewsAPI not configured — returning empty results")
            return []

        query = self.extract_keywords(question)
        if not query:
            return []

        # Evict expired cache entries to prevent unbounded growth
        self._clean_cache()

        # Check cache
        cache_key = query.lower().strip()
        if cache_key in self.cache:
            cached_time, cached_results = self.cache[cache_key]
            if time.time() - cached_time < self.cache_ttl:
                logger.debug("Cache hit for query: '%s'", query)
                return cached_results

        # Fetch from NewsAPI
        try:
            response = self.client.get_everything(
                q=query,
                language="en",
                sort_by="relevancy",
                page_size=max_articles,
            )

            articles = []
            for article in response.get("articles", []):
                articles.append({
                    "title": article.get("title", ""),
                    "source": article.get("source", {}).get("name", "Unknown"),
                    "description": article.get("description", ""),
                    "content": article.get("content", ""),
                    "url": article.get("url", ""),
                    "published_at": article.get("publishedAt", ""),
                })

            # Update cache
            self.cache[cache_key] = (time.time(), articles)

            logger.info(
                "Fetched %d articles for query: '%s'",
                len(articles), query
            )
            return articles

        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("NewsAPI connection error for '%s': %s", query, type(e).__name__)
            return []
        except (ValueError, KeyError, TypeError) as e:
            logger.error("NewsAPI response parse error for '%s': %s", query, e)
            return []

    def _clean_cache(self):
        """Remove expired entries from the cache."""
        now = time.time()
        expired = [k for k, (t, _) in self.cache.items() if now - t >= self.cache_ttl]
        for k in expired:
            del self.cache[k]

    def get_articles_for_markets(self, markets: list) -> dict:
        """Fetch articles for multiple markets at once.

        Args:
            markets: List of market dicts (must have 'question' key)

        Returns:
            Dict mapping market_id → list of articles
        """
        results = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "")
            if question:
                results[market_id] = self.get_articles(question)
        return results
