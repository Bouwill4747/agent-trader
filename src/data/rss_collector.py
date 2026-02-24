"""
RSS collector — fetches news from free RSS feeds for market research.

Covers general news, finance, politics, crypto, and esports with no API
key or rate limits. Fetches all feeds once per cycle and filters locally
by keyword relevance to each market question.

Feeds used:
  - Reuters (top news, business, politics)
  - BBC News
  - Al Jazeera
  - CNBC
  - CoinDesk  (crypto)
  - CoinTelegraph  (crypto)
  - HLTV  (CS2 / Counter-Strike esports news)
"""

import re
import time

import feedparser

from src.utils.logger import setup_logger

logger = setup_logger("rss_collector")

# Free RSS feeds — no API key, no rate limits
FEEDS = {
    "Reuters Top":      "https://feeds.reuters.com/reuters/topNews",
    "Reuters Business": "https://feeds.reuters.com/reuters/businessNews",
    "Reuters Politics": "https://feeds.reuters.com/reuters/politicsNews",
    "BBC News":         "https://feeds.bbci.co.uk/news/rss.xml",
    "Al Jazeera":       "https://www.aljazeera.com/xml/rss/all.xml",
    "CNBC":             "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "CoinDesk":         "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph":    "https://cointelegraph.com/rss",
    "HLTV":             "https://www.hltv.org/rss/news",
}

# Stop words to strip when extracting keywords
_STOP_WORDS = {
    "will", "the", "be", "by", "in", "on", "at", "to", "of", "a", "an",
    "is", "it", "this", "that", "and", "or", "for", "before", "after",
    "during", "yes", "no", "does", "do", "has", "have", "can", "could",
    "would", "should", "least", "most", "more", "than", "any", "all",
    "end", "win", "lose", "hit", "reach", "over", "under", "first",
}


class RSSCollector:
    """Collects news from free RSS feeds with local keyword filtering."""

    def __init__(self):
        # Cache: {feed_name: (timestamp, [articles])}
        self._feed_cache: dict = {}
        self._cache_ttl = 2700  # 45 minutes — refresh every cycle (60 min)

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def get_articles(self, question: str, max_articles: int = 10) -> list:
        """Return RSS articles relevant to a market question.

        Fetches all feeds (cached), then filters by keyword match.

        Args:
            question: Market question string.
            max_articles: Maximum articles to return.

        Returns:
            List of article dicts (same schema as NewsCollector).
        """
        keywords = self._extract_keywords(question)
        if not keywords:
            return []

        all_articles = self._get_all_feed_articles()
        matched = self._filter_by_keywords(all_articles, keywords)

        if matched:
            logger.info(
                "RSS: %d articles matched for '%s'",
                min(len(matched), max_articles),
                question[:50],
            )

        return matched[:max_articles]

    def get_articles_for_markets(self, markets: list) -> dict:
        """Fetch and filter RSS articles for multiple markets.

        Fetches all feeds once, then filters per market — efficient.

        Args:
            markets: List of market dicts with 'question' key.

        Returns:
            Dict mapping market_id → list of articles.
        """
        # Pre-load all feeds once for the whole batch
        self._get_all_feed_articles()

        results = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "")
            if question:
                results[market_id] = self.get_articles(question)
        return results

    # ──────────────────────────────────────────────
    # Internals
    # ──────────────────────────────────────────────

    def _extract_keywords(self, question: str) -> list[str]:
        """Pull meaningful keywords from a market question."""
        words = re.findall(r"[\w$£€]+", question.lower())
        keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 2]
        return keywords[:6]  # top 6 most specific terms

    def _get_all_feed_articles(self) -> list:
        """Return all articles from all feeds, using cache where fresh."""
        all_articles = []
        now = time.time()

        for name, url in FEEDS.items():
            cached_time, cached_articles = self._feed_cache.get(name, (0, []))

            if now - cached_time < self._cache_ttl:
                all_articles.extend(cached_articles)
                continue

            # Fetch and parse the feed
            try:
                feed = feedparser.parse(url)
                articles = []
                for entry in feed.entries:
                    article = self._parse_entry(entry, source=name)
                    if article:
                        articles.append(article)

                self._feed_cache[name] = (now, articles)
                all_articles.extend(articles)

                if articles:
                    logger.debug("RSS: fetched %d articles from %s", len(articles), name)

            except Exception as e:
                logger.warning("RSS: failed to fetch %s: %s", name, type(e).__name__)
                # Keep stale cache if available rather than dropping entirely
                all_articles.extend(cached_articles)

        return all_articles

    def _parse_entry(self, entry, source: str) -> dict | None:
        """Convert a feedparser entry into a standard article dict."""
        try:
            title = getattr(entry, "title", "") or ""
            description = getattr(entry, "summary", "") or ""
            url = getattr(entry, "link", "") or ""
            published = getattr(entry, "published", "") or ""

            if not title:
                return None

            return {
                "title": title.strip(),
                "source": source,
                "description": description.strip(),
                "content": description.strip(),  # RSS summary doubles as content
                "url": url.strip(),
                "published_at": published,
            }
        except Exception:
            return None

    def _filter_by_keywords(self, articles: list, keywords: list[str]) -> list:
        """Score and rank articles by keyword relevance.

        An article scores +1 for each keyword found in its title,
        +0.5 for each keyword found only in the description.
        Articles with score 0 are discarded.
        """
        scored = []
        for article in articles:
            title_lower = article.get("title", "").lower()
            desc_lower = article.get("description", "").lower()

            score = 0.0
            for kw in keywords:
                if kw in title_lower:
                    score += 1.0
                elif kw in desc_lower:
                    score += 0.5

            if score > 0:
                scored.append((score, article))

        # Sort by score descending, return articles only
        scored.sort(key=lambda x: x[0], reverse=True)
        return [article for _, article in scored]
