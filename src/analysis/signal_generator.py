"""
Signal generator — combines FinBERT sentiment + Claude probability into trading signals.
This is where the hybrid strategy comes together.
"""

import json
from datetime import datetime, timezone
from dataclasses import dataclass

from config.settings import MIN_EDGE_THRESHOLD
from src.analysis.finbert_analyzer import FinBERTAnalyzer
from src.analysis.llm_researcher import LLMResearcher
from src.utils.logger import setup_logger
from src.utils.db import insert_signal

logger = setup_logger("signal_generator")


@dataclass
class TradingSignal:
    """A complete trading signal — the analysis engine's output."""
    market_id: str
    token_id: str              # YES token ID
    no_token_id: str           # NO token ID
    question: str
    current_price: float       # Current YES market price
    estimated_prob: float      # Our blended probability estimate
    edge: float                # estimated_prob - current_price
    sentiment_score: float     # FinBERT aggregate (-1.0 to +1.0)
    confidence: str            # low / medium / high
    direction: str             # BUY_YES, BUY_NO, or SKIP
    reasoning: str             # Claude's reasoning


class SignalGenerator:
    """Combines ML sentiment + LLM research into actionable trading signals."""

    def __init__(self):
        self.finbert = FinBERTAnalyzer()
        self.researcher = LLMResearcher()

        # How much weight to give each signal source
        self.llm_weight = 0.7       # Claude's probability estimate
        self.sentiment_weight = 0.3  # FinBERT sentiment adjustment

        logger.info(
            "Signal generator initialized — LLM weight: %.0f%%, Sentiment weight: %.0f%%",
            self.llm_weight * 100, self.sentiment_weight * 100
        )

    def generate_signal(
        self,
        market: dict,
        articles: list[dict],
        reddit_posts: list[dict],
    ) -> TradingSignal:
        """Generate a trading signal for a single market.

        This is the core hybrid strategy:
        1. Run FinBERT on all text (news + Reddit) → sentiment score
        2. Ask Claude for probability estimate (with news + sentiment context)
        3. Blend both into a final probability
        4. Calculate edge vs market price
        5. Decide direction (BUY_YES, BUY_NO, or SKIP)

        Args:
            market: Market dict from Gamma API (must have: id, question, tokens, price)
            articles: News articles from NewsCollector
            reddit_posts: Reddit posts from SentimentScraper

        Returns:
            TradingSignal with the analysis result
        """
        market_id = market.get("id", market.get("condition_id", "unknown"))
        question = market.get("question", "Unknown market")
        current_price = self._get_market_price(market)
        yes_token_id, no_token_id = self._get_token_ids(market)
        deadline = market.get("end_date_iso", market.get("end_date", "Unknown"))

        logger.info("Generating signal for: '%s' (price: $%.3f)", question[:50], current_price)

        # ── Step 1: FinBERT sentiment ──
        all_texts = []
        for article in articles:
            title = article.get("title", "")
            desc = article.get("description", "")
            if title:
                all_texts.append(title)
            if desc:
                all_texts.append(desc)

        for post in reddit_posts:
            text = post.get("text", post.get("title", ""))
            if text:
                all_texts.append(text)

        sentiment_score = 0.0
        if all_texts:
            sentiment_score = self.finbert.get_aggregate_sentiment(all_texts)

        # ── Step 2: Claude analysis ──
        llm_result = self.researcher.analyze_market(
            question=question,
            current_price=current_price,
            deadline=deadline,
            articles=articles,
            sentiment_score=sentiment_score,
            num_posts=len(reddit_posts),
        )

        llm_prob = llm_result["estimated_probability"]
        confidence = llm_result["confidence"]
        reasoning = llm_result["reasoning"]

        # ── Step 3: Blend probabilities ──
        # Convert sentiment (-1 to +1) to a probability adjustment (-0.15 to +0.15)
        sentiment_adjustment = sentiment_score * 0.15

        # Blend: LLM estimate (70%) + sentiment-adjusted market price (30%)
        blended_prob = (
            self.llm_weight * llm_prob +
            self.sentiment_weight * (current_price + sentiment_adjustment)
        )

        # Clamp to valid range
        blended_prob = max(0.01, min(0.99, blended_prob))

        # ── Step 4: Calculate edge ──
        edge = blended_prob - current_price

        # ── Step 5: Determine direction ──
        if edge > MIN_EDGE_THRESHOLD:
            direction = "BUY_YES"
        elif edge < -MIN_EDGE_THRESHOLD:
            direction = "BUY_NO"
        else:
            direction = "SKIP"

        signal = TradingSignal(
            market_id=market_id,
            token_id=yes_token_id,
            no_token_id=no_token_id,
            question=question,
            current_price=current_price,
            estimated_prob=blended_prob,
            edge=edge,
            sentiment_score=sentiment_score,
            confidence=confidence,
            direction=direction,
            reasoning=reasoning,
        )

        logger.info(
            "Signal: %s | Edge: %+.1f%% | Sentiment: %+.3f | Confidence: %s | '%s'",
            direction, edge * 100, sentiment_score, confidence, question[:40]
        )

        return signal

    async def generate_signals(
        self,
        markets: list[dict],
        articles_by_market: dict,
        sentiment_by_market: dict,
    ) -> list[TradingSignal]:
        """Generate signals for multiple markets.

        Args:
            markets: List of market dicts from Gamma API
            articles_by_market: {market_id: [articles]} from NewsCollector
            sentiment_by_market: {market_id: [posts]} from SentimentScraper

        Returns:
            List of TradingSignals (including SKIPs for logging)
        """
        signals = []

        for market in markets:
            market_id = market.get("id", market.get("condition_id", "unknown"))

            articles = articles_by_market.get(market_id, [])
            posts = sentiment_by_market.get(market_id, [])

            signal = self.generate_signal(market, articles, posts)
            signals.append(signal)

            # Record every signal (even SKIPs) for analysis
            await insert_signal({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "market_id": signal.market_id,
                "question": signal.question,
                "current_price": signal.current_price,
                "estimated_prob": signal.estimated_prob,
                "edge": signal.edge,
                "sentiment_score": signal.sentiment_score,
                "confidence": signal.confidence,
                "direction": signal.direction,
                "reasoning": signal.reasoning,
                "acted_on": 0,  # Updated later if we trade
            })

        actionable = [s for s in signals if s.direction != "SKIP"]
        logger.info(
            "Generated %d signals: %d actionable, %d skipped",
            len(signals), len(actionable), len(signals) - len(actionable)
        )

        return signals

    def _get_market_price(self, market: dict) -> float:
        """Extract the YES token price from a market dict."""
        # Gamma API formats vary — try common fields
        price = market.get("outcomePrices")
        if price and isinstance(price, str):
            # Format: "[\"0.55\",\"0.45\"]"
            try:
                prices = json.loads(price)
                return float(prices[0])
            except (json.JSONDecodeError, IndexError, ValueError):
                pass

        # Try direct price fields
        for key in ("bestAsk", "lastTradePrice", "price"):
            val = market.get(key)
            if val is not None:
                try:
                    return float(val)
                except ValueError:
                    continue

        logger.warning("Could not extract price for market %s", market.get("id", "unknown"))
        return 0.5  # Default to 50/50

    def _get_token_ids(self, market: dict) -> tuple[str, str]:
        """Extract both YES and NO token IDs from a market dict.

        Returns:
            Tuple of (yes_token_id, no_token_id)
        """
        # Try clobTokenIds format: [YES_TOKEN, NO_TOKEN]
        token_ids = market.get("clobTokenIds")
        if token_ids and isinstance(token_ids, str):
            try:
                ids = json.loads(token_ids)
                yes_id = ids[0] if len(ids) > 0 else ""
                no_id = ids[1] if len(ids) > 1 else ""
                return yes_id, no_id
            except (json.JSONDecodeError, IndexError):
                pass

        # Try tokens list format
        tokens = market.get("tokens", [])
        if tokens and isinstance(tokens, list):
            yes_id = tokens[0].get("token_id", "") if len(tokens) > 0 else ""
            no_id = tokens[1].get("token_id", "") if len(tokens) > 1 else ""
            return yes_id, no_id

        return market.get("token_id", ""), ""
