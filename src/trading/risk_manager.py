"""
Risk manager — the agent's safety net.
Decides HOW MUCH to bet (position sizing) and WHETHER to bet at all (limit checks).
Uses fractional Kelly criterion for mathematically-informed sizing.
"""

from dataclasses import dataclass
from config.settings import (
    KELLY_FRACTION,
    MAX_POSITION_PCT,
    MAX_MARKET_EXPOSURE_PCT,
    MAX_TOTAL_EXPOSURE_PCT,
    MAX_CONCURRENT_POSITIONS,
    MAX_DRAWDOWN_PCT,
    DRAWDOWN_RISK_REDUCING_PCT,
    MIN_TRADE_SIZE,
    MAX_SPREAD_THRESHOLD,
    MIN_EDGE_FLOOR,
    BANNED_THEMES,
)
from src.utils.logger import setup_logger

logger = setup_logger("risk_manager")


@dataclass
class RiskDecision:
    """The risk manager's verdict on a proposed trade."""
    approved: bool
    position_size: float    # Dollar amount to risk
    shares: float           # Number of shares to buy
    reason: str             # Why approved or rejected
    kelly_raw: float = 0.0  # Full Kelly suggestion (before scaling)
    kelly_sized: float = 0.0  # After applying fraction


class RiskManager:
    """Evaluates trades and enforces risk limits."""

    def __init__(self):
        logger.info(
            "Risk manager initialized — Kelly: %.0f%%, Max position: %.0f%%, "
            "Max exposure: %.0f%%, Max drawdown: %.0f%%",
            KELLY_FRACTION * 100,
            MAX_POSITION_PCT * 100,
            MAX_TOTAL_EXPOSURE_PCT * 100,
            MAX_DRAWDOWN_PCT * 100,
        )

    def evaluate_trade(
        self,
        estimated_prob: float,
        market_price: float,
        confidence: str,
        bankroll: float,
        current_exposure: float,
        num_positions: int,
        current_drawdown: float,
        direction: str = "BUY_YES",
        regime: str = "neutral",
        resolution_type: str = "subjective_event",
        resolution_clarity_score: int = 1,
        spread: float = 0.05,
        market_exposure: float = 0.0,
        liquidity: float = 0.0,
        market_theme: str = "",
    ) -> RiskDecision:
        """Decide whether to take a trade and how much to risk.

        Args:
            estimated_prob: Our probability estimate (0.0 to 1.0)
            market_price: Current market price (0.0 to 1.0)
            confidence: Signal confidence ("low", "medium", "high")
            bankroll: Total portfolio value right now
            current_exposure: Dollar amount currently at risk
            num_positions: Number of open positions
            current_drawdown: Current drawdown percentage (0.0 to 1.0)
            direction: "BUY_YES" or "BUY_NO" — determines edge calculation
            resolution_type: How market resolves — sets base resolution buffer
            resolution_clarity_score: 1 (vague) to 5 (precise) — adds clarity penalty
                clarity_penalty = (5 - score) × 1%
            spread: Estimated bid-ask spread (fraction). Sets the cost floor:
                cost_floor = spread × 1.5  (spread + half-spread for slippage).
                Hard rejected if spread > MAX_SPREAD_THRESHOLD (10%).
            liquidity: Total pool liquidity in USD (from Gamma API).
                Used for shadow gate logging — conditions 3–5 are observed
                but not yet enforced. Search logs for "SHADOW" to review.

        Returns:
            RiskDecision with approved/rejected, position size, and reason
        """

        # ── Check 1: Drawdown limit ──
        if current_drawdown >= MAX_DRAWDOWN_PCT:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"HALTED: Drawdown {current_drawdown:.1%} exceeds limit {MAX_DRAWDOWN_PCT:.1%}"
            )

        # ── Check 2: Position count limit ──
        if num_positions >= MAX_CONCURRENT_POSITIONS:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"Max positions reached: {num_positions}/{MAX_CONCURRENT_POSITIONS}"
            )

        # ── Check 2b: Banned theme filter ──
        # Markets in these domains aggregate all public information efficiently.
        # An LLM has no informational edge vs professional traders who also read the news.
        if market_theme and market_theme in BANNED_THEMES:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"Banned theme: '{market_theme}' — no LLM edge in this domain"
            )

        # ── Check 3: Calculate edge (direction-aware) ──
        if direction == "BUY_NO":
            # For BUY_NO: we profit when YES price goes DOWN
            # Edge = how overpriced YES is: market_price - estimated_prob
            edge = market_price - estimated_prob
            effective_price = 1.0 - market_price  # Price of the NO token
        else:
            # For BUY_YES: we profit when YES price goes UP
            edge = estimated_prob - market_price
            effective_price = market_price

        # ── Check 3b: Regime-based adjustments ──
        #
        # Market regime (driven by Fear & Greed index) affects how aggressively
        # we trade. In panic conditions, assets overshoot and noise is high;
        # in euphoric conditions, corrections are near.
        #
        #   extreme_fear  (0-20):  -60% size, need +5% extra edge
        #   fear          (21-40): -40% size, need +3% extra edge
        #   neutral       (41-59): no change
        #   greed         (60-79): no change
        #   extreme_greed (80-100):-30% size, need +2% extra edge
        #
        _REGIME_ADJUSTMENTS = {
            "extreme_fear":  (0.40, 0.05),
            "fear":          (0.60, 0.03),
            "neutral":       (1.00, 0.00),
            "greed":         (1.00, 0.00),
            "extreme_greed": (0.70, 0.02),
        }
        regime_size_mult, regime_edge_bonus = _REGIME_ADJUSTMENTS.get(regime, (1.0, 0.0))

        # ── Resolution type + clarity uncertainty buffer ──
        #
        # Two components combine to require more edge for riskier resolution:
        #
        # 1. Base buffer by resolution type:
        #   mechanical_numeric  +2%: clear numeric threshold, easy to verify
        #   price_print         +3%: depends on a specific exchange/data source
        #   formal_recognition  +4%: requires official body announcement
        #   subjective_event    +6%: vague language, no clear data source
        #
        # 2. Clarity penalty: (5 - clarity_score) × 1%
        #   score=5 → +0%  (precise: named source + exact criteria)
        #   score=4 → +1%  (clear with minor ambiguity)
        #   score=3 → +2%  (moderate interpretation required)
        #   score=2 → +3%  (source or criteria unclear)
        #   score=1 → +4%  (vague/discretionary — no explicit source)
        #
        # Total buffer range: min 2% (mechanical_numeric, score=5)
        #                     max 10% (subjective_event, score=1)
        #
        _RESOLUTION_BUFFERS = {
            "mechanical_numeric": 0.02,
            "price_print":        0.03,
            "formal_recognition": 0.04,
            "subjective_event":   0.06,
        }
        resolution_buffer = _RESOLUTION_BUFFERS.get(resolution_type, 0.06)

        # Clamp score to [1, 5] defensively before computing penalty
        _score = max(1, min(5, int(resolution_clarity_score) if resolution_clarity_score else 1))
        clarity_penalty = (5 - _score) * 0.01

        # Cost floor: the minimum edge needed to cover the round-trip transaction cost.
        # cost_floor = spread × 1.5  (spread to cross the book + ~half-spread for slippage)
        cost_floor = spread * 1.5

        dynamic_min_edge = cost_floor + regime_edge_bonus + resolution_buffer + clarity_penalty

        # Hard floor: never trade below 20% edge regardless of spread/regime/resolution.
        # Even cheap liquid markets can lose money when the thesis is wrong — the floor
        # enforces that only high-conviction signals reach execution.
        effective_min_edge = max(MIN_EDGE_FLOOR, dynamic_min_edge)

        if edge < effective_min_edge:
            details = [f"spread_cost={cost_floor:.0%}"]
            if regime_edge_bonus > 0:
                details.append(f"regime={regime} +{regime_edge_bonus:.0%}")
            if resolution_buffer > 0:
                details.append(f"resolution={resolution_type} +{resolution_buffer:.0%}")
            if clarity_penalty > 0:
                details.append(f"clarity={_score}/5 +{clarity_penalty:.0%}")
            if MIN_EDGE_FLOOR > dynamic_min_edge:
                details.append(f"floor={MIN_EDGE_FLOOR:.0%}")
            reason = (
                f"Edge too small: {edge:.1%} < {effective_min_edge:.1%}"
                f" ({', '.join(details)})"
            )
            return RiskDecision(approved=False, position_size=0, shares=0, reason=reason)

        # ── Check 3c: Spread hard cap (condition 2 of execution eligibility gate) ──
        #
        # Even with sufficient edge, a spread above 10% means execution quality
        # is too poor — the cost_floor alone won't protect us from a wide bid-ask.
        # This is a separate, explicit gate rather than relying on cost_floor to
        # implicitly reject via the edge check.
        if spread > MAX_SPREAD_THRESHOLD:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"Spread too wide: {spread:.0%} > {MAX_SPREAD_THRESHOLD:.0%} — execution quality too poor"
            )

        # ── Check 4: Low confidence filter ──
        if confidence == "low":
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason="Confidence too low — skipping"
            )

        # ── Check 5: Kelly criterion position sizing ──
        #
        # For binary prediction markets:
        #   Odds (decimal) = 1 / market_price
        #   Kelly % = (edge * odds) / (odds - 1)
        #
        # Simplified for markets priced 0-1:
        #   Kelly % = edge / (1 - market_price)
        #
        # Then we apply the Kelly fraction (0.25x) for safety.

        if market_price <= 0 or market_price >= 1:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"Invalid market price: {market_price}"
            )

        kelly_raw = edge / (1 - effective_price)
        kelly_sized = kelly_raw * KELLY_FRACTION

        # Apply confidence scaling
        confidence_multiplier = {"medium": 0.7, "high": 1.0}.get(confidence, 0.5)
        kelly_sized *= confidence_multiplier

        # Calculate dollar amount
        position_size = bankroll * kelly_sized

        # Apply regime size multiplier (reduces size in fearful/euphoric markets)
        if regime_size_mult < 1.0:
            position_size *= regime_size_mult

        # ── Check 6: Apply hard limits ──

        # Max per-position limit — scales with edge strength.
        # Stronger edge unlocks a larger cap, rewarding high-conviction signals.
        #   edge < 20%  → 5% of bankroll (standard)
        #   edge 20-35% → 7% of bankroll
        #   edge > 35%  → 10% of bankroll
        if edge >= 0.35:
            max_position = bankroll * min(MAX_POSITION_PCT * 2.0, 0.10)
        elif edge >= 0.20:
            max_position = bankroll * MAX_POSITION_PCT * 1.4
        else:
            max_position = bankroll * MAX_POSITION_PCT
        if position_size > max_position:
            position_size = max_position

        # Max total exposure limit
        exposure_pct = current_exposure / bankroll if bankroll > 0 else 1.0
        remaining_exposure = (MAX_TOTAL_EXPOSURE_PCT - exposure_pct) * bankroll
        if remaining_exposure <= 0:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"Exposure limit reached: {exposure_pct:.1%} >= {MAX_TOTAL_EXPOSURE_PCT:.1%}"
            )
        if position_size > remaining_exposure:
            position_size = remaining_exposure

        # Per-market concentration cap — prevents piling into one market
        if bankroll > 0:
            market_exposure_pct = market_exposure / bankroll
            if market_exposure_pct >= MAX_MARKET_EXPOSURE_PCT:
                return RiskDecision(
                    approved=False, position_size=0, shares=0,
                    reason=f"Per-market cap reached: {market_exposure_pct:.1%} >= {MAX_MARKET_EXPOSURE_PCT:.1%} in this market"
                )
            remaining_market = (MAX_MARKET_EXPOSURE_PCT - market_exposure_pct) * bankroll
            if position_size > remaining_market:
                position_size = remaining_market

        # Minimum trade size
        if position_size < MIN_TRADE_SIZE:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"Position too small: ${position_size:.2f} < ${MIN_TRADE_SIZE:.2f}"
            )

        # Calculate shares (use effective_price — YES price for BUY_YES, NO price for BUY_NO)
        shares = position_size / effective_price

        # Polymarket CLOB minimum: 5 shares per order
        if shares < 5:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"Too few shares: {shares:.2f} < 5 (CLOB minimum) — increase position size or raise price"
            )

        # ── Shadow gate: Execution eligibility conditions 3–5 (logging only) ──
        #
        # These conditions are NOT yet enforced. They log warnings so we can
        # observe how often they would fire before setting thresholds.
        # Search logs for "SHADOW gate" to review before tightening.
        #
        # Condition 3: position size <= 3% of pool liquidity
        #   Prevents consuming a large share of the pool (market impact)
        # Condition 4: estimated slippage <= 2%
        #   Simple model: slippage ≈ position_size / liquidity
        # Condition 5: estimated book depth >= position size
        #   Proxy: 10% of pool liquidity available at any given price level
        #   (Replace with live order book data when ready to enforce)
        if liquidity > 0:
            pct_of_pool = position_size / liquidity
            if pct_of_pool > 0.03:
                logger.warning(
                    "SHADOW gate 3 (pool concentration): $%.2f = %.1f%% of $%.0f pool"
                    " (threshold: 3%%)",
                    position_size, pct_of_pool * 100, liquidity,
                )

            estimated_slippage = position_size / liquidity
            if estimated_slippage > 0.02:
                logger.warning(
                    "SHADOW gate 4 (slippage): ~%.1f%% estimated on $%.0f pool"
                    " (threshold: 2%%)",
                    estimated_slippage * 100, liquidity,
                )

            depth_proxy = liquidity * 0.10  # assume 10% of liquidity sits at any price level
            if depth_proxy < position_size:
                logger.warning(
                    "SHADOW gate 5 (book depth): proxy $%.2f < position $%.2f"
                    " on $%.0f pool (threshold: depth >= position, proxy=10%% of liquidity)",
                    depth_proxy, position_size, liquidity,
                )

        logger.info(
            "APPROVED: $%.2f (%.0f shares) — Edge: %.1f%%, Kelly: %.1f%% → %.1f%%, Confidence: %s",
            position_size, shares, edge * 100,
            kelly_raw * 100, kelly_sized * 100, confidence
        )

        return RiskDecision(
            approved=True,
            position_size=position_size,
            shares=shares,
            reason="Trade approved",
            kelly_raw=kelly_raw,
            kelly_sized=kelly_sized,
        )

    def get_trading_mode(self, current_drawdown: float) -> str:
        """Return the current trading mode based on drawdown level.

        Modes (ordered by severity):
          NORMAL            — drawdown below DRAWDOWN_RISK_REDUCING_PCT
          RISK_REDUCING_ONLY — between DRAWDOWN_RISK_REDUCING_PCT and MAX_DRAWDOWN_PCT
                              (exits/stop-losses run; no new entries)
          HALTED            — drawdown >= MAX_DRAWDOWN_PCT (full stop)
        """
        if current_drawdown >= MAX_DRAWDOWN_PCT:
            return "HALTED"
        if current_drawdown >= DRAWDOWN_RISK_REDUCING_PCT:
            return "RISK_REDUCING_ONLY"
        return "NORMAL"

    def check_kill_switch(self) -> bool:
        """Check if the emergency kill switch file exists."""
        import os
        from config.settings import KILL_SWITCH_PATH

        if os.path.exists(KILL_SWITCH_PATH):
            logger.warning("KILL SWITCH ACTIVATED — file found at %s", KILL_SWITCH_PATH)
            return True
        return False
