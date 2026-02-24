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
            resolution_type="mechanical_numeric",   # 2% base buffer
            resolution_clarity_score=5,             # 0% clarity penalty → min_edge=12%
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
            resolution_type="mechanical_numeric", resolution_clarity_score=5,
        )
        medium = risk.evaluate_trade(
            estimated_prob=0.70, market_price=0.55, confidence="medium",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric", resolution_clarity_score=5,
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
        """At MAX_CONCURRENT_POSITIONS (12) → no more trades."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=30.0,
            num_positions=12,       # At the new limit
            current_drawdown=0.0,
        )
        assert decision.approved is False
        assert "Max positions" in decision.reason

    def test_reject_at_exposure_limit(self, risk):
        """60% exposure → no more trades."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=60.0,  # Already at 60% limit
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

    def test_resolution_type_buffer_blocks_low_edge(self, risk):
        """Subjective event + worst clarity (score=1) + spread=8% → min_edge=22%."""
        # cost_floor = 8% × 1.5 = 12%, resolution buffer = 6%, clarity = 4% → min_edge = 22%
        decision = risk.evaluate_trade(
            estimated_prob=0.65,    # 15% edge — below 22% threshold
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="subjective_event",  # +6% base buffer
            resolution_clarity_score=1,          # +4% clarity penalty
            spread=0.08,                         # cost_floor=12%
        )
        assert decision.approved is False
        assert "resolution=subjective_event" in decision.reason
        assert "clarity=1/5" in decision.reason

    def test_mechanical_numeric_precise_passes_with_lower_edge(self, risk):
        """Mechanical numeric + clarity=5 → min 2% total buffer → min_edge=12%."""
        decision = risk.evaluate_trade(
            estimated_prob=0.625,   # 12.5% edge — clears 12% threshold
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,          # No clarity penalty
        )
        assert decision.approved is True

    def test_clarity_score_scales_buffer_continuously(self, risk):
        """clarity_score=3, formal_recognition, spread=10% → min_edge=21%."""
        # cost_floor = 10% × 1.5 = 15%, resolution = 4%, clarity = 2% → min_edge = 21%

        # Edge=15% should fail (15% < 21%)
        rejected = risk.evaluate_trade(
            estimated_prob=0.65, market_price=0.50, confidence="high",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
            resolution_type="formal_recognition",
            resolution_clarity_score=3,   # +2% clarity penalty
            spread=0.10,                  # cost_floor=15%
        )
        assert rejected.approved is False
        assert "clarity=3/5" in rejected.reason

        # Edge=22% should pass (22% > 21%)
        approved = risk.evaluate_trade(
            estimated_prob=0.72, market_price=0.50, confidence="high",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
            resolution_type="formal_recognition",
            resolution_clarity_score=3,
            spread=0.10,
        )
        assert approved.approved is True


    def test_cost_aware_same_edge_different_spreads(self, risk):
        """Same edge, different spreads: liquid market approved, thin market rejected."""
        # edge = 8%, mechanical_numeric, clarity=5

        # Liquid market: spread=2%, cost_floor=3%, min_edge = 3%+2% = 5%
        liquid = risk.evaluate_trade(
            estimated_prob=0.58, market_price=0.50, confidence="high",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric", resolution_clarity_score=5,
            spread=0.02,
        )
        assert liquid.approved is True    # 8% > 5%

        # Thin market: spread=8%, cost_floor=12%, min_edge = 12%+2% = 14%
        thin = risk.evaluate_trade(
            estimated_prob=0.58, market_price=0.50, confidence="high",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric", resolution_clarity_score=5,
            spread=0.08,
        )
        assert thin.approved is False     # 8% < 14%
        assert "spread_cost" in thin.reason


# ──────────────────────────────────────────────
# Position Size Caps
# ──────────────────────────────────────────────

class TestPositionCaps:
    """Verify that hard limits cap the position size."""

    def test_position_capped_at_max(self, risk):
        """Even with massive edge, position can't exceed 10% of bankroll (high-edge cap)."""
        decision = risk.evaluate_trade(
            estimated_prob=0.95,    # 45% edge — triggers 10% cap
            market_price=0.50,
            confidence="high",
            bankroll=1000.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
        )
        assert decision.approved is True
        assert decision.position_size <= 100.0  # 10% of $1000 (high-edge cap)

    def test_position_capped_at_5_percent_low_edge(self, risk):
        """Low-edge trades are still capped at the standard 5% of bankroll."""
        decision = risk.evaluate_trade(
            estimated_prob=0.65,    # 15% edge — standard cap applies
            market_price=0.50,
            confidence="high",
            bankroll=1000.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",  # 2% base buffer
            resolution_clarity_score=5,            # 0% clarity penalty → min_edge=12%
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
            current_exposure=58.0,  # Only 2% room left (60% limit - 58% used)
            num_positions=3,
            current_drawdown=0.0,
        )
        if decision.approved:
            assert decision.position_size <= 2.0 + 1e-9  # Floating-point tolerance
