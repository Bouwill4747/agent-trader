"""
Reddit sentiment scraper — collects posts and comments from relevant subreddits.
Uses PRAW (Python Reddit API Wrapper) for authenticated access.
Returns raw text for downstream analysis by FinBERT.
"""

import os
import praw
from config.settings import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET
from src.utils.logger import setup_logger

logger = setup_logger("sentiment_scraper")

# Map general topics to relevant subreddits
TOPIC_SUBREDDIT_MAP = {
    "crypto": ["cryptocurrency", "bitcoin", "ethereum", "polymarket"],
    "bitcoin": ["bitcoin", "cryptocurrency", "btc"],
    "ethereum": ["ethereum", "cryptocurrency", "ethtrader"],
    "politics": ["politics", "news", "worldnews", "polymarket"],
    "election": ["politics", "elections", "polymarket"],
    "economy": ["economics", "wallstreetbets", "stocks", "finance"],
    "sports": ["sportsbook", "sports", "nba", "nfl"],
    "ai": ["artificial", "machinelearning", "technology"],
    "tech": ["technology", "programming", "futurology"],
    "fed": ["economics", "finance", "wallstreetbets"],
    "war": ["worldnews", "geopolitics", "news"],
    "climate": ["climate", "environment", "science"],
}

# Fallback subreddits when no topic match
DEFAULT_SUBREDDITS = ["polymarket", "predictions", "news"]


class SentimentScraper:
    """Scrapes Reddit for sentiment data on prediction market topics."""

    def __init__(self):
        self.reddit = None
        if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
            try:
                user_agent = os.getenv(
                    "REDDIT_USER_AGENT",
                    "python:market-research:v1.0 (research tool)"
                )
                self.reddit = praw.Reddit(
                    client_id=REDDIT_CLIENT_ID,
                    client_secret=REDDIT_CLIENT_SECRET,
                    user_agent=user_agent,
                    timeout=30,
                )
                logger.info("Reddit client initialized")
            except (ConnectionError, TimeoutError, OSError) as e:
                logger.error("Reddit initialization connection error: %s", type(e).__name__)
            except (ValueError, KeyError) as e:
                logger.error("Reddit initialization config error: %s", e)
        else:
            logger.warning("No Reddit credentials — sentiment scraping disabled")

    def find_subreddits(self, question: str) -> list:
        """Map a market question to relevant subreddits.

        Searches for topic keywords in the question and returns
        matching subreddits from TOPIC_SUBREDDIT_MAP.
        """
        question_lower = question.lower()
        matched_subs = set()

        for topic, subreddits in TOPIC_SUBREDDIT_MAP.items():
            if topic in question_lower:
                matched_subs.update(subreddits)

        if not matched_subs:
            matched_subs.update(DEFAULT_SUBREDDITS)

        return list(matched_subs)[:5]  # Max 5 subreddits per query

    def scrape_subreddit(self, subreddit_name: str, query: str, limit: int = 10) -> list:
        """Scrape recent posts from a subreddit matching a search query.

        Args:
            subreddit_name: Name of the subreddit (without r/)
            query: Search terms
            limit: Max posts to fetch

        Returns:
            List of dicts with keys: title, text, score, num_comments, subreddit
        """
        if not self.reddit:
            return []

        try:
            subreddit = self.reddit.subreddit(subreddit_name)
            posts = []

            for submission in subreddit.search(query, limit=limit, sort="relevance", time_filter="week"):
                # Combine title + selftext for analysis
                text = submission.title
                if submission.selftext:
                    text += " " + submission.selftext

                posts.append({
                    "title": submission.title,
                    "text": text,
                    "score": submission.score,
                    "num_comments": submission.num_comments,
                    "subreddit": subreddit_name,
                    "url": submission.url,
                    "created_utc": submission.created_utc,
                })

            return posts

        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Reddit connection error for r/%s: %s", subreddit_name, type(e).__name__)
            return []
        except (ValueError, KeyError, AttributeError) as e:
            logger.error("Reddit parse error for r/%s (query: '%s'): %s", subreddit_name, query, e)
            return []

    def get_sentiment_data(self, question: str, posts_per_sub: int = 5) -> list:
        """Get Reddit posts relevant to a market question.

        This is the main method other modules call. It:
        1. Finds relevant subreddits for the question
        2. Scrapes each subreddit for matching posts
        3. Returns all posts for FinBERT to analyze

        Args:
            question: The market question
            posts_per_sub: How many posts to fetch per subreddit

        Returns:
            List of post dicts with text ready for sentiment analysis
        """
        if not self.reddit:
            logger.warning("Reddit not configured — returning empty results")
            return []

        subreddits = self.find_subreddits(question)
        all_posts = []

        for sub in subreddits:
            # Use first few words of question as search query
            search_query = " ".join(question.split()[:6])
            posts = self.scrape_subreddit(sub, search_query, limit=posts_per_sub)
            all_posts.extend(posts)

        logger.info(
            "Scraped %d posts from %d subreddits for: '%s'",
            len(all_posts), len(subreddits), question[:50]
        )
        return all_posts

    def get_sentiment_for_markets(self, markets: list) -> dict:
        """Scrape Reddit data for multiple markets.

        Args:
            markets: List of market dicts (must have 'question' key)

        Returns:
            Dict mapping market_id → list of Reddit posts
        """
        results = {}
        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))
            question = market.get("question", "")
            if question:
                results[market_id] = self.get_sentiment_data(question)
        return results
