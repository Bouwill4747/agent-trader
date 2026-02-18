"""
Tests for RSSCollector — keyword extraction, article filtering,
feed caching, and resilience to bad data.

All tests mock feedparser.parse so no real network calls are made.
"""

import time
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from src.data.rss_collector import RSSCollector


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _make_entry(title="", summary="", link="", published=""):
    """Build a minimal feedparser-style entry object."""
    return SimpleNamespace(
        title=title,
        summary=summary,
        link=link,
        published=published,
    )

def _make_feed(*entries):
    """Build a minimal feedparser result with the given entries."""
    feed = MagicMock()
    feed.entries = list(entries)
    return feed


# ──────────────────────────────────────────────
# Keyword extraction
# ──────────────────────────────────────────────

class TestExtractKeywords:
    """_extract_keywords strips stop words and short tokens."""

    def setup_method(self):
        self.rss = RSSCollector()

    def test_basic_extraction(self):
        kw = self.rss._extract_keywords("Will Bitcoin hit $100k by March?")
        assert "bitcoin" in kw
        assert "$100k" in kw
        assert "march" in kw

    def test_stop_words_removed(self):
        kw = self.rss._extract_keywords("Will the Fed cut interest rates?")
        assert "will" not in kw
        assert "the" not in kw
        assert "fed" in kw
        assert "cut" in kw
        assert "interest" in kw

    def test_short_words_excluded(self):
        # Words with 2 or fewer characters are dropped
        kw = self.rss._extract_keywords("Will S&P go up or down?")
        assert "or" not in kw
        assert "go" not in kw

    def test_max_six_keywords(self):
        long_question = "Will Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota win?"
        kw = self.rss._extract_keywords(long_question)
        assert len(kw) <= 6

    def test_empty_question_returns_empty(self):
        assert self.rss._extract_keywords("") == []

    def test_question_with_only_stop_words(self):
        assert self.rss._extract_keywords("Will the be in on at") == []


# ──────────────────────────────────────────────
# Entry parsing
# ──────────────────────────────────────────────

class TestParseEntry:
    """_parse_entry converts feedparser entries to article dicts."""

    def setup_method(self):
        self.rss = RSSCollector()

    def test_valid_entry_parsed(self):
        entry = _make_entry(
            title="Bitcoin surges to new high",
            summary="BTC reached $90k overnight",
            link="https://example.com/btc",
            published="Wed, 18 Feb 2026 08:00:00 GMT",
        )
        article = self.rss._parse_entry(entry, source="CoinDesk")
        assert article is not None
        assert article["title"] == "Bitcoin surges to new high"
        assert article["source"] == "CoinDesk"
        assert article["description"] == "BTC reached $90k overnight"
        assert article["content"] == "BTC reached $90k overnight"
        assert article["url"] == "https://example.com/btc"
        assert article["published_at"] == "Wed, 18 Feb 2026 08:00:00 GMT"

    def test_missing_title_returns_none(self):
        entry = _make_entry(title="", summary="Some content")
        assert self.rss._parse_entry(entry, source="BBC") is None

    def test_missing_optional_fields_use_defaults(self):
        entry = _make_entry(title="Some headline")
        article = self.rss._parse_entry(entry, source="Reuters")
        assert article is not None
        assert article["description"] == ""
        assert article["url"] == ""
        assert article["published_at"] == ""

    def test_whitespace_stripped(self):
        entry = _make_entry(title="  Headline with spaces  ", summary="  desc  ")
        article = self.rss._parse_entry(entry, source="BBC")
        assert article["title"] == "Headline with spaces"
        assert article["description"] == "desc"

    def test_none_fields_become_empty_string(self):
        entry = SimpleNamespace(title="Valid Title", summary=None, link=None, published=None)
        article = self.rss._parse_entry(entry, source="Reuters")
        assert article is not None
        assert article["description"] == ""
        assert article["url"] == ""


# ──────────────────────────────────────────────
# Keyword filtering and scoring
# ──────────────────────────────────────────────

class TestFilterByKeywords:
    """_filter_by_keywords ranks by relevance and drops irrelevant articles."""

    def setup_method(self):
        self.rss = RSSCollector()

    def _article(self, title, description=""):
        return {"title": title, "description": description, "source": "Test",
                "content": description, "url": "", "published_at": ""}

    def test_title_match_included(self):
        articles = [self._article("Bitcoin hits new record")]
        result = self.rss._filter_by_keywords(articles, ["bitcoin"])
        assert len(result) == 1

    def test_description_match_included(self):
        articles = [self._article("Markets rally", "Bitcoin leads the charge")]
        result = self.rss._filter_by_keywords(articles, ["bitcoin"])
        assert len(result) == 1

    def test_no_match_excluded(self):
        articles = [self._article("Sports scores today", "Football results")]
        result = self.rss._filter_by_keywords(articles, ["bitcoin", "crypto"])
        assert len(result) == 0

    def test_title_match_ranks_above_description_match(self):
        articles = [
            self._article("Markets update", "bitcoin mentioned in passing"),   # desc match
            self._article("Bitcoin surges 10%", "crypto markets rally"),       # title match
        ]
        result = self.rss._filter_by_keywords(articles, ["bitcoin"])
        assert result[0]["title"] == "Bitcoin surges 10%"

    def test_more_keyword_matches_rank_higher(self):
        articles = [
            self._article("Bitcoin price update"),                      # 1 keyword
            self._article("Bitcoin ETF crypto fund approved"),          # 2 keywords
        ]
        result = self.rss._filter_by_keywords(articles, ["bitcoin", "crypto"])
        assert result[0]["title"] == "Bitcoin ETF crypto fund approved"

    def test_empty_articles_returns_empty(self):
        assert self.rss._filter_by_keywords([], ["bitcoin"]) == []

    def test_empty_keywords_returns_empty(self):
        articles = [self._article("Bitcoin news")]
        assert self.rss._filter_by_keywords(articles, []) == []


# ──────────────────────────────────────────────
# Feed caching
# ──────────────────────────────────────────────

class TestFeedCaching:
    """Feed results are cached and not re-fetched until TTL expires."""

    def test_fresh_cache_is_used_without_fetching(self):
        rss = RSSCollector()
        # Pre-populate cache as fresh
        fake_articles = [{"title": "Cached article", "source": "Reuters Top",
                          "description": "", "content": "", "url": "", "published_at": ""}]
        for name in rss._feed_cache.__class__():
            pass
        # Inject fresh cache entries for all feeds
        now = time.time()
        for name in list(__import__("src.data.rss_collector", fromlist=["FEEDS"]).FEEDS.keys()):
            rss._feed_cache[name] = (now, fake_articles)

        with patch("feedparser.parse") as mock_parse:
            rss._get_all_feed_articles()
            mock_parse.assert_not_called()

    def test_expired_cache_triggers_refetch(self):
        rss = RSSCollector()
        from src.data.rss_collector import FEEDS

        # Set all cache entries as expired
        old_time = time.time() - rss._cache_ttl - 1
        for name in FEEDS:
            rss._feed_cache[name] = (old_time, [])

        mock_feed = _make_feed(
            _make_entry(title="Fresh article", summary="content", link="http://x.com")
        )
        with patch("feedparser.parse", return_value=mock_feed) as mock_parse:
            rss._get_all_feed_articles()
            assert mock_parse.call_count == len(FEEDS)

    def test_failed_feed_falls_back_to_stale_cache(self):
        rss = RSSCollector()
        from src.data.rss_collector import FEEDS

        stale_article = {"title": "Stale but valid", "source": "BBC News",
                         "description": "", "content": "", "url": "", "published_at": ""}
        # Set stale cache for one feed, expire the timestamp
        old_time = time.time() - rss._cache_ttl - 1
        rss._feed_cache["BBC News"] = (old_time, [stale_article])

        def fake_parse(url):
            if "bbci" in url:
                raise ConnectionError("Network down")
            return _make_feed()  # empty feed for others

        with patch("feedparser.parse", side_effect=fake_parse):
            articles = rss._get_all_feed_articles()

        # Stale BBC article should still appear
        titles = [a["title"] for a in articles]
        assert "Stale but valid" in titles


# ──────────────────────────────────────────────
# get_articles (public API)
# ──────────────────────────────────────────────

class TestGetArticles:
    """get_articles returns filtered, capped results."""

    def _rss_with_mock_feeds(self, entries):
        """Return an RSSCollector with all feeds pre-cached."""
        rss = RSSCollector()
        from src.data.rss_collector import FEEDS
        now = time.time()
        articles = [rss._parse_entry(e, source="Test") for e in entries]
        articles = [a for a in articles if a]
        for name in FEEDS:
            rss._feed_cache[name] = (now, articles)
        return rss

    def test_returns_matching_articles(self):
        entries = [
            _make_entry("Venezuela sanctions lifted", "Rubio Venezuela deal"),
            _make_entry("Sports results today"),
        ]
        rss = self._rss_with_mock_feeds(entries)
        results = rss.get_articles("Will Marco Rubio visit Venezuela?")
        assert any("Venezuela" in a["title"] for a in results)

    def test_max_articles_respected(self):
        entries = [_make_entry(f"Bitcoin news {i}") for i in range(20)]
        rss = self._rss_with_mock_feeds(entries)
        results = rss.get_articles("Will Bitcoin hit $100k?", max_articles=3)
        assert len(results) <= 3

    def test_empty_question_returns_empty(self):
        rss = RSSCollector()
        assert rss.get_articles("") == []

    def test_no_matching_articles_returns_empty(self):
        entries = [_make_entry("Football scores", "Premier League results")]
        rss = self._rss_with_mock_feeds(entries)
        results = rss.get_articles("Will Bitcoin hit $100k?")
        assert results == []


# ──────────────────────────────────────────────
# get_articles_for_markets (batch API)
# ──────────────────────────────────────────────

class TestGetArticlesForMarkets:
    """get_articles_for_markets returns a dict keyed by market_id."""

    def test_returns_dict_keyed_by_market_id(self):
        rss = RSSCollector()
        from src.data.rss_collector import FEEDS
        now = time.time()
        articles = [{"title": "Bitcoin rally", "source": "CoinDesk",
                     "description": "BTC up", "content": "BTC up", "url": "", "published_at": ""}]
        for name in FEEDS:
            rss._feed_cache[name] = (now, articles)

        markets = [
            {"id": "market-1", "question": "Will Bitcoin hit $100k?"},
            {"id": "market-2", "question": "Will Rubio visit Venezuela?"},
        ]
        results = rss.get_articles_for_markets(markets)
        assert "market-1" in results
        assert "market-2" in results
        assert isinstance(results["market-1"], list)

    def test_market_without_question_skipped(self):
        rss = RSSCollector()
        from src.data.rss_collector import FEEDS
        now = time.time()
        for name in FEEDS:
            rss._feed_cache[name] = (now, [])

        markets = [{"id": "market-1"}]  # no 'question' key
        results = rss.get_articles_for_markets(markets)
        assert "market-1" not in results

    def test_uses_condition_id_as_fallback_key(self):
        rss = RSSCollector()
        from src.data.rss_collector import FEEDS
        now = time.time()
        for name in FEEDS:
            rss._feed_cache[name] = (now, [])

        markets = [{"condition_id": "cond-abc", "question": "Will BTC rally?"}]
        results = rss.get_articles_for_markets(markets)
        assert "cond-abc" in results

    def test_empty_markets_returns_empty_dict(self):
        rss = RSSCollector()
        assert rss.get_articles_for_markets([]) == {}
