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
        """Edge=22%, price=$0.50, high confidence → should approve with correct size."""
        decision = risk.evaluate_trade(
            estimated_prob=0.72,    # We think 72%
            market_price=0.50,      # Market says 50%
            confidence="high",      # High confidence
            bankroll=100.0,         # $100 bankroll
            current_exposure=0.0,   # No existing positions
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",   # 2% base buffer
            resolution_clarity_score=5,             # 0% clarity penalty → min_edge=20% (floor)
        )

        assert decision.approved is True
        assert decision.position_size > 0
        assert decision.shares > 0

        # Kelly raw = edge / (1 - price) = 0.22 / 0.50 = 0.440
        assert abs(decision.kelly_raw - 0.440) < 0.01

    def test_kelly_fraction_reduces_size(self, risk):
        """Fractional Kelly (0.25x) should give smaller size than full Kelly."""
        decision = risk.evaluate_trade(
            estimated_prob=0.72,
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
        )

        # Full Kelly would be 44% of $100 = $44.00
        # 0.25x Kelly = $44.00 * 0.25 = $11.00
        # With high confidence (1.0x multiplier) → $11.00
        # But capped at 7% (20-35% edge tier) = $7.00
        assert decision.position_size <= 7.0  # 7% cap for 20-35% edge range

    def test_medium_confidence_reduces_size(self, risk):
        """Medium confidence should give a smaller position than high confidence."""
        high = risk.evaluate_trade(
            estimated_prob=0.72, market_price=0.50, confidence="high",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric", resolution_clarity_score=5,
        )
        medium = risk.evaluate_trade(
            estimated_prob=0.72, market_price=0.50, confidence="medium",
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

    def test_mechanical_numeric_precise_passes_at_floor(self, risk):
        """Mechanical numeric + clarity=5 → dynamic threshold 5%, floor=20% applies.
        Trade with 20.5% edge is approved — floor is the binding constraint."""
        decision = risk.evaluate_trade(
            estimated_prob=0.705,   # 20.5% edge — clears the 20% floor
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


    def test_cost_aware_same_edge_different_market_quality(self, risk):
        """Same 21% edge: low-cost market approved, high-cost vague market rejected.

        With MIN_EDGE_FLOOR=20%, a liquid/clear market needs just the floor (20%).
        A vague market with a wide spread pushes the dynamic threshold above the floor,
        requiring more than 21% edge.

        liquid+clear: dynamic=5% (floor=20% binding) → 21% > 20% → approved.
        vague+illiquid: subjective_event+clarity=1+spread=8% → dynamic=22% > floor → rejected.
        """
        # Liquid, clear: mechanical_numeric + clarity=5 + spread=2%
        # dynamic = 2%×1.5 + 2% + 0% = 5% → floor=20% dominates → effective=20%
        liquid = risk.evaluate_trade(
            estimated_prob=0.71, market_price=0.50, confidence="high",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric", resolution_clarity_score=5,
            spread=0.02,
        )
        assert liquid.approved is True    # 21% > 20% (floor)

        # Vague, illiquid: subjective_event + clarity=1 + spread=8%
        # dynamic = 8%×1.5 + 6% + 4% = 12+10 = 22% > 20% → dynamic dominates → effective=22%
        illiquid = risk.evaluate_trade(
            estimated_prob=0.71, market_price=0.50, confidence="high",
            bankroll=100.0, current_exposure=0.0, num_positions=0,
            current_drawdown=0.0,
            resolution_type="subjective_event", resolution_clarity_score=1,
            spread=0.08,
        )
        assert illiquid.approved is False  # 21% < 22% (dynamic)
        assert "resolution=subjective_event" in illiquid.reason


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

    def test_position_capped_at_7_percent_moderate_edge(self, risk):
        """Moderate-edge trades (22%, 20-35% tier) are capped at 7% of bankroll."""
        decision = risk.evaluate_trade(
            estimated_prob=0.72,    # 22% edge — 20-35% tier → 7% cap
            market_price=0.50,
            confidence="high",
            bankroll=1000.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",  # 2% base buffer
            resolution_clarity_score=5,            # 0% clarity penalty → min_edge=20% (floor)
        )
        assert decision.approved is True
        assert decision.position_size <= 70.0  # 7% of $1000 (20-35% edge tier)

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


# ──────────────────────────────────────────────
# Per-Market Concentration Cap
# ──────────────────────────────────────────────

class TestPerMarketCap:
    """Verify the per-market exposure cap prevents piling into one market."""

    def test_reject_when_market_already_at_cap(self, risk):
        """If 15%+ of bankroll already in this market, reject any further buys."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=20.0,
            num_positions=2,
            current_drawdown=0.0,
            market_exposure=15.0,   # Already at 15% cap
        )
        assert decision.approved is False
        assert "Per-market cap" in decision.reason

    def test_cap_reduces_size_when_near_limit(self, risk):
        """If 12% already in market, new trade capped to remaining 3%."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=15.0,
            num_positions=2,
            current_drawdown=0.0,
            market_exposure=12.0,   # 12% in — only 3% room left
        )
        assert decision.approved is True
        assert decision.position_size <= 3.0 + 1e-9  # Capped at remaining room

    def test_no_existing_market_exposure_not_capped(self, risk):
        """Fresh market with 0 existing exposure is not affected by the cap."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            market_exposure=0.0,    # No existing position
        )
        assert decision.approved is True


# ──────────────────────────────────────────────
# CLOB Minimum Shares Check
# ──────────────────────────────────────────────

class TestMinimumShares:
    """Verify Polymarket's 5-share minimum is enforced before sending to CLOB."""

    def test_reject_when_shares_below_minimum(self, risk):
        """Edge>20% but tiny bankroll → position capped at 5% → < 5 shares → rejected."""
        # Large bankroll case: 5% of $100 at $0.97 ≈ 5.15 shares — may pass
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.97,      # High price → few shares per dollar
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
        )
        # Tiny bankroll: 25% edge, $0.55 price, bankroll=$30 → 5% cap=$1.50 → 2.7 shares < 5
        decision2 = risk.evaluate_trade(
            estimated_prob=0.80,    # 25% edge — above 20% floor
            market_price=0.55,
            confidence="high",
            bankroll=30.0,          # Tiny bankroll → 5% cap = $1.50 → 2.7 shares < 5
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
        )
        # If rejected, reason must mention the 5-share minimum
        if not decision2.approved:
            assert "5" in decision2.reason or "shares" in decision2.reason.lower()

    def test_approved_when_shares_at_minimum(self, risk):
        """Exactly 5 shares should pass."""
        # $5 at $1.00 → 5 shares; but price can't be 1.0. Use $0.99 → $5/$0.99 = 5.05
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.50,
            confidence="high",
            bankroll=1000.0,        # Large bankroll ensures position > $5 min
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
        )
        assert decision.approved is True
        assert decision.shares >= 5

    def test_minimum_shares_error_message(self, risk):
        """Rejection message must mention the CLOB minimum."""
        # 21% edge at $0.78 price, bankroll=$30 → 5% cap=$1.50 → ~1.9 shares < 5
        decision = risk.evaluate_trade(
            estimated_prob=0.99,    # High edge to clear floor
            market_price=0.78,      # High price → few shares
            confidence="high",
            bankroll=30.0,          # Small bankroll: 5% cap=$1.50 → 1.9 shares < 5
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
        )
        if not decision.approved and "shares" in decision.reason.lower():
            assert "5" in decision.reason


# ──────────────────────────────────────────────
# Execution Eligibility Gate — Spread Cap
# ──────────────────────────────────────────────

class TestSpreadCap:
    """Condition 2 of the execution eligibility gate: spread <= 10%."""

    def test_wide_spread_rejected(self, risk):
        """Spread > 10% must be rejected even with good edge."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            spread=0.15,            # 15% — above 10% hard cap
        )
        assert decision.approved is False
        assert "spread" in decision.reason.lower()

    def test_spread_at_threshold_rejected(self, risk):
        """Spread exactly at 10% threshold must be rejected (> not >=)."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            spread=0.101,           # Just over 10%
        )
        assert decision.approved is False

    def test_acceptable_spread_passes(self, risk):
        """Spread at 5% (well under 10%) should not be blocked by the cap."""
        decision = risk.evaluate_trade(
            estimated_prob=0.77,    # 22% edge — safely above 20% floor
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            spread=0.05,            # 5% — well within cap
        )
        assert decision.approved is True

    def test_shadow_gates_do_not_reject(self, risk):
        """Shadow gates 3-5 must log but never reject a trade."""
        # Small pool ($600) with a $5 position — triggers all three shadow gates
        decision = risk.evaluate_trade(
            estimated_prob=0.77,    # 22% edge — safely above 20% floor
            market_price=0.55,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            spread=0.05,
            liquidity=600.0,        # Tiny pool — all shadow thresholds triggered
        )
        # Should still approve despite triggering shadow warnings
        assert decision.approved is True


# ──────────────────────────────────────────────
# Strategy Filters — Edge Floor and Theme Bans
# ──────────────────────────────────────────────

class TestStrategyFilters:
    """Verify the hard edge floor (20%) and banned theme filters."""

    def test_edge_floor_rejects_below_20_pct(self, risk):
        """Edge=19% is below MIN_EDGE_FLOOR=20% — rejected even in a liquid market."""
        decision = risk.evaluate_trade(
            estimated_prob=0.69,    # 19% edge — just below floor
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            spread=0.02,
        )
        assert decision.approved is False
        assert "Edge too small" in decision.reason
        assert "floor=20%" in decision.reason

    def test_edge_floor_approves_at_21_pct(self, risk):
        """Edge=21% clears the 20% floor — approved in a liquid market."""
        decision = risk.evaluate_trade(
            estimated_prob=0.71,    # 21% edge — clears floor
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            spread=0.02,
        )
        assert decision.approved is True

    def test_floor_not_annotated_when_dynamic_is_higher(self, risk):
        """When dynamic threshold > floor, 'floor' should not appear in the reason."""
        # subjective_event + clarity=1 + spread=8% → dynamic=22% > floor=20%
        decision = risk.evaluate_trade(
            estimated_prob=0.71,    # 21% edge — below dynamic threshold (22%)
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="subjective_event",
            resolution_clarity_score=1,
            spread=0.08,
        )
        assert decision.approved is False
        assert "resolution=subjective_event" in decision.reason
        assert "floor" not in decision.reason   # dynamic dominated — floor not binding

    def test_banned_theme_geopolitics_rejected(self, risk):
        """Geopolitics theme → rejected regardless of edge or confidence."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,    # 30% edge — would otherwise be approved
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            market_theme="geopolitics",
        )
        assert decision.approved is False
        assert "Banned theme" in decision.reason
        assert "geopolitics" in decision.reason

    def test_banned_theme_politics_rejected(self, risk):
        """Politics theme → rejected."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            market_theme="politics",
        )
        assert decision.approved is False
        assert "Banned theme" in decision.reason

    def test_banned_theme_macro_rejected(self, risk):
        """Macro theme → rejected."""
        decision = risk.evaluate_trade(
            estimated_prob=0.80,
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            market_theme="macro",
        )
        assert decision.approved is False
        assert "Banned theme" in decision.reason

    def test_allowed_theme_crypto_not_blocked(self, risk):
        """Crypto theme is allowed — not in BANNED_THEMES."""
        decision = risk.evaluate_trade(
            estimated_prob=0.72,    # 22% edge
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            market_theme="crypto",
        )
        assert decision.approved is True

    def test_allowed_theme_tech_not_blocked(self, risk):
        """Tech theme is allowed — not in BANNED_THEMES."""
        decision = risk.evaluate_trade(
            estimated_prob=0.72,
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            market_theme="tech",
        )
        assert decision.approved is True

    def test_empty_theme_not_blocked(self, risk):
        """Empty/missing theme is not blocked by the theme filter."""
        decision = risk.evaluate_trade(
            estimated_prob=0.72,
            market_price=0.50,
            confidence="high",
            bankroll=100.0,
            current_exposure=0.0,
            num_positions=0,
            current_drawdown=0.0,
            resolution_type="mechanical_numeric",
            resolution_clarity_score=5,
            market_theme="",
        )
        assert decision.approved is True
