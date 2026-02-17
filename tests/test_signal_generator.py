"""
Tests for the signal generator — verifies probability blending,
edge calculation, and direction logic.

Uses mocked FinBERT and Claude responses — no API keys needed.
"""

import pytest
from unittest.mock import patch, MagicMock
from src.analysis.signal_generator import SignalGenerator, TradingSignal


@pytest.fixture
def generator():
    """Create a SignalGenerator with mocked ML/LLM components."""
    with patch.object(SignalGenerator, '__init__', lambda self: None):
        gen = SignalGenerator.__new__(SignalGenerator)
        gen.finbert = MagicMock()
        gen.researcher = MagicMock()
        gen.llm_weight = 0.7
        gen.sentiment_weight = 0.3
        return gen


def make_market(market_id="test-123", question="Will BTC hit $100k?",
                price="0.55", volume="50000"):
    """Helper to create a fake market dict."""
    return {
        "id": market_id,
        "question": question,
        "outcomePrices": f'["{price}","{1 - float(price):.2f}"]',
        "clobTokenIds": '["token-yes-123","token-no-456"]',
        "volume": volume,
        "end_date_iso": "2026-03-01T00:00:00Z",
    }


# ──────────────────────────────────────────────
# Direction Logic
# ──────────────────────────────────────────────

class TestDirectionLogic:
    """Verify BUY_YES, BUY_NO, and SKIP decisions."""

    def test_buy_yes_when_underpriced(self, generator):
        """If we estimate higher than market → BUY_YES."""
        generator.finbert.get_aggregate_sentiment.return_value = 0.2
        generator.researcher.analyze_market.return_value = {
            "estimated_probability": 0.80,
            "confidence": "high",
            "reasoning": "Strong evidence for YES",
            "key_factors": ["factor1"],
        }

        market = make_market(price="0.55")
        signal = generator.generate_signal(market, [], [])

        assert signal.direction == "BUY_YES"
        assert signal.edge > 0.10  # Above 10% threshold

    def test_buy_no_when_overpriced(self, generator):
        """If we estimate much lower than market → BUY_NO."""
        generator.finbert.get_aggregate_sentiment.return_value = -0.3
        generator.researcher.analyze_market.return_value = {
            "estimated_probability": 0.25,
            "confidence": "high",
            "reasoning": "Strong evidence against YES",
            "key_factors": ["factor1"],
        }

        market = make_market(price="0.60")
        signal = generator.generate_signal(market, [], [])

        assert signal.direction == "BUY_NO"
        assert signal.edge < -0.10  # Negative edge beyond threshold

    def test_skip_when_edge_too_small(self, generator):
        """If edge is within ±10% → SKIP."""
        generator.finbert.get_aggregate_sentiment.return_value = 0.0
        generator.researcher.analyze_market.return_value = {
            "estimated_probability": 0.57,
            "confidence": "medium",
            "reasoning": "Close to market price",
            "key_factors": [],
        }

        market = make_market(price="0.55")
        signal = generator.generate_signal(market, [], [])

        assert signal.direction == "SKIP"
        assert abs(signal.edge) < 0.10


# ──────────────────────────────────────────────
# Probability Blending
# ──────────────────────────────────────────────

class TestBlending:
    """Verify the 70/30 blending formula."""

    def test_blending_weights(self, generator):
        """70% Claude + 30% sentiment-adjusted price."""
        generator.finbert.get_aggregate_sentiment.return_value = 0.0  # Neutral
        generator.researcher.analyze_market.return_value = {
            "estimated_probability": 0.80,
            "confidence": "high",
            "reasoning": "Test",
            "key_factors": [],
        }

        market = make_market(price="0.50")
        signal = generator.generate_signal(market, [], [])

        # Sentiment adjustment = 0.0 * 0.15 = 0.0
        # Blended = 0.70 * 0.80 + 0.30 * (0.50 + 0.0) = 0.56 + 0.15 = 0.71
        assert abs(signal.estimated_prob - 0.71) < 0.02

    def test_positive_sentiment_increases_estimate(self, generator):
        """Positive sentiment should push the estimate higher."""
        generator.researcher.analyze_market.return_value = {
            "estimated_probability": 0.60,
            "confidence": "medium",
            "reasoning": "Test",
            "key_factors": [],
        }

        # Need dummy articles so the code path calls get_aggregate_sentiment
        dummy_articles = [{"title": "Test headline", "description": "Test desc"}]

        # Neutral sentiment
        generator.finbert.get_aggregate_sentiment.return_value = 0.0
        market = make_market(price="0.50")
        neutral = generator.generate_signal(market, dummy_articles, [])

        # Positive sentiment
        generator.finbert.get_aggregate_sentiment.return_value = 0.8
        positive = generator.generate_signal(market, dummy_articles, [])

        assert positive.estimated_prob > neutral.estimated_prob

    def test_negative_sentiment_decreases_estimate(self, generator):
        """Negative sentiment should push the estimate lower."""
        generator.researcher.analyze_market.return_value = {
            "estimated_probability": 0.60,
            "confidence": "medium",
            "reasoning": "Test",
            "key_factors": [],
        }

        dummy_articles = [{"title": "Test headline", "description": "Test desc"}]

        # Neutral
        generator.finbert.get_aggregate_sentiment.return_value = 0.0
        market = make_market(price="0.50")
        neutral = generator.generate_signal(market, dummy_articles, [])

        # Negative
        generator.finbert.get_aggregate_sentiment.return_value = -0.8
        negative = generator.generate_signal(market, dummy_articles, [])

        assert negative.estimated_prob < neutral.estimated_prob


# ──────────────────────────────────────────────
# Signal Structure
# ──────────────────────────────────────────────

class TestSignalStructure:
    """Verify signals contain all required fields."""

    def test_signal_has_all_fields(self, generator):
        """Every signal must have all TradingSignal fields populated."""
        generator.finbert.get_aggregate_sentiment.return_value = 0.1
        generator.researcher.analyze_market.return_value = {
            "estimated_probability": 0.70,
            "confidence": "high",
            "reasoning": "Detailed analysis here",
            "key_factors": ["economy", "policy"],
        }

        market = make_market()
        signal = generator.generate_signal(market, [], [])

        assert isinstance(signal, TradingSignal)
        assert signal.market_id == "test-123"
        assert signal.question == "Will BTC hit $100k?"
        assert 0 < signal.current_price < 1
        assert 0 < signal.estimated_prob < 1
        assert signal.confidence in ("low", "medium", "high")
        assert signal.direction in ("BUY_YES", "BUY_NO", "SKIP")
        assert len(signal.reasoning) > 0
