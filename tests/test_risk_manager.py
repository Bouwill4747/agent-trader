"""
Tests for the risk manager — verifies Kelly criterion math,
position limits, drawdown protection, and all safety checks.

No API keys needed — all logic is pure math.
"""

import pytest
from src.trading.risk_manager import RiskManager


@pytest.fixture
def risk():
    """Create a fresh RiskManager for each test."""
    return RiskManager()


# ──────────────────────────────────────────────
# Kelly Criterion Math
# ──────────────────────────────────────────────

class TestKellyCriterion:
    """Verify the core position sizing formula."""

    def test_basic_kelly_sizing(self, risk):
        """Edge=15%, price=$0.55, high confidence → should approve with correct size."""
        decision = risk.evaluate_trade(
            estimated_prob=0.70,    # We think 70%
            market_price=0.55,      # Market says 55%
            confidence="high",      # High confidence
            bankroll=100.0,         # $100 bankroll
            current_exposure=0.0,   # No existing positions
            num_positions=0,
            current_drawdown=0.0,
        )

        assert decision.approved is True
        assert decision.position_size > 0
        assert decision.shares > 0

        # Kelly raw = edge / (1 - price) = 0.15 / 0.45 = 0.333
        assert abs(decision.kelly_raw - 0.333) < 0.01

    def test_kelly_fraction_reduces_size(self, risk):
        """Fractional Kelly (0.25x) should give smaller size than full Kelly."""
        decision = risk.evaluate_trade(
            estimated_prob=0.70,
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
        )

        # Full Kelly would be 33.3% of $100 = $33.33
        # 0.25x Kelly = $33.33 * 0.25 = $8.33
        # With high confidence (1.0x multiplier) → $8.33
        # But capped at MAX_POSITION_PCT (5%) = $5.00
        assert decision.position_size <= 5.0  # 5% cap on $100

    def test_medium_confidence_reduces_size(self, risk):
        """Medium confidence should give a smaller position than high confidence."""
        high = risk.evaluate_trade(
            estimated_prob=0.70, market_price=0.55, confidence="high",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
        )
        medium = risk.evaluate_trade(
            estimated_prob=0.70, market_price=0.55, confidence="medium",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
        )

        # Both should be approved, but medium gets smaller size
        assert high.approved is True
        assert medium.approved is True
        assert medium.kelly_sized < high.kelly_sized


# ──────────────────────────────────────────────
# Safety Checks
# ──────────────────────────────────────────────

class TestSafetyChecks:
    """Verify that each safety limit correctly rejects trades."""

    def test_reject_low_edge(self, risk):
        """Edge below 10% threshold → rejected."""
        decision = risk.evaluate_trade(
            estimated_prob=0.58,    # Only 3% above market
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
        )
        assert decision.approved is False
        assert "Edge too small" in decision.reason

    def test_reject_low_confidence(self, risk):
        """Low confidence → always rejected, even with good edge."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,    # Great edge!
            market_price=0.55,
            confidence="low",       # But low confidence
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
        )
        assert decision.approved is False
        assert "Confidence" in decision.reason

    def test_reject_at_drawdown_limit(self, risk):
        """20% drawdown → halt ALL trading."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.55,
            confidence="high",
            bankroll=80.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.20,  # At the 20% limit
        )
        assert decision.approved is False
        assert "HALTED" in decision.reason

    def test_reject_at_max_positions(self, risk):
        """10 concurrent positions → no more trades."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=30.0,
            num_positions=10,       # At the limit
            current_drawdown=0.0,
        )
        assert decision.approved is False
        assert "Max positions" in decision.reason

    def test_reject_at_exposure_limit(self, risk):
        """50% exposure → no more trades."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=50.0,  # Already at 50% limit
            num_positions=3,
            current_drawdown=0.0,
        )
        assert decision.approved is False
        assert "Exposure limit" in decision.reason

    def test_reject_invalid_price(self, risk):
        """Price of exactly 0 or 1 → rejected (would cause division by zero)."""
        for bad_price in [0.0, 1.0, -0.5, 1.5]:
            decision = risk.evaluate_trade(
                estimated_prob=0.70,
                market_price=bad_price,
                confidence="high",
                bankroll=100.0,
                current_exposure=0.0,
                num_positions=0,
                current_drawdown=0.0,
            )
            # Should either reject for invalid price or edge threshold
            assert decision.approved is False


# ──────────────────────────────────────────────
# Position Size Caps
# ──────────────────────────────────────────────

class TestPositionCaps:
    """Verify that hard limits cap the position size."""

    def test_position_capped_at_5_percent(self, risk):
        """Even with huge edge, position can't exceed 5% of bankroll."""
        decision = risk.evaluate_trade(
            estimated_prob=0.95,    # Massive edge
            market_price=0.50,
            confidence="high",
            bankroll=1000.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
        )
        assert decision.approved is True
        assert decision.position_size <= 50.0  # 5% of $1000

    def test_position_respects_remaining_exposure(self, risk):
        """Position size limited by remaining exposure room."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=48.0,  # Only 2% room left
            num_positions=3,
            current_drawdown=0.0,
        )
        if decision.approved:
            assert decision.position_size <= 2.0 + 1e-9  # Floating-point tolerance
