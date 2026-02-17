"""
Risk manager — the agent's safety net.
Decides HOW MUCH to bet (position sizing) and WHETHER to bet at all (limit checks).
Uses fractional Kelly criterion for mathematically-informed sizing.
"""

from dataclasses import dataclass
from config.settings import (
    KELLY_FRACTION,
    MAX_POSITION_PCT,
    MAX_TOTAL_EXPOSURE_PCT,
    MAX_CONCURRENT_POSITIONS,
    MAX_DRAWDOWN_PCT,
    MIN_TRADE_SIZE,
    MIN_EDGE_THRESHOLD,
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

        if edge < MIN_EDGE_THRESHOLD:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"Edge too small: {edge:.1%} < {MIN_EDGE_THRESHOLD:.1%}"
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

        # ── Check 6: Apply hard limits ──

        # Max per-position limit
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

        # Minimum trade size
        if position_size < MIN_TRADE_SIZE:
            return RiskDecision(
                approved=False, position_size=0, shares=0,
                reason=f"Position too small: ${position_size:.2f} < ${MIN_TRADE_SIZE:.2f}"
            )

        # Calculate shares (use effective_price — YES price for BUY_YES, NO price for BUY_NO)
        shares = position_size / effective_price

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

    def check_kill_switch(self) -> bool:
        """Check if the emergency kill switch file exists."""
        import os
        from config.settings import KILL_SWITCH_PATH

        if os.path.exists(KILL_SWITCH_PATH):
            logger.warning("KILL SWITCH ACTIVATED — file found at %s", KILL_SWITCH_PATH)
            return True
        return False
