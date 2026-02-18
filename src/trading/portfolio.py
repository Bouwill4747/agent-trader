"""
Portfolio tracker — manages positions, balances, and PnL calculations.
This is the agent's "bank account" — it knows what we own and what it's worth.
"""

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field

from config.settings import INITIAL_BANKROLL
from src.utils.logger import setup_logger
from src.utils.db import insert_portfolio_snapshot, get_latest_snapshot

logger = setup_logger("portfolio")


@dataclass
class Position:
    """A single open position in a market."""
    market_id: str
    token_id: str
    question: str
    side: str           # "YES" or "NO"
    shares: float       # Number of shares held
    avg_price: float    # Average entry price
    current_price: float = 0.0

    @property
    def cost_basis(self) -> float:
        """Total amount paid for this position."""
        return self.shares * self.avg_price

    @property
    def current_value(self) -> float:
        """What the position is worth right now."""
        return self.shares * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        """Profit/loss if we sold at the current price."""
        return self.current_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized PnL as a percentage."""
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_basis) * 100


class Portfolio:
    """Tracks all positions, cash balance, and performance metrics."""

    def __init__(self, starting_bankroll: float = None):
        self.cash = starting_bankroll or INITIAL_BANKROLL
        self.positions: dict[str, Position] = {}  # market_id → Position
        self.realized_pnl = 0.0
        self.peak_bankroll = self.cash
        self.trade_count = 0

        logger.info("Portfolio initialized with $%.2f", self.cash)

    # ──────────────────────────────────────────────
    # Position Management
    # ──────────────────────────────────────────────

    def open_position(self, market_id: str, token_id: str, question: str,
                      side: str, shares: float, price: float):
        """Record a new position or add to an existing one."""

        total_cost = shares * price

        if total_cost > self.cash:
            logger.warning(
                "Insufficient cash: need $%.2f, have $%.2f",
                total_cost, self.cash
            )
            return False

        if market_id in self.positions:
            # Adding to existing position — recalculate average price
            pos = self.positions[market_id]
            total_shares = pos.shares + shares
            pos.avg_price = (
                (pos.shares * pos.avg_price) + (shares * price)
            ) / total_shares
            pos.shares = total_shares
        else:
            # New position
            self.positions[market_id] = Position(
                market_id=market_id,
                token_id=token_id,
                question=question,
                side=side,
                shares=shares,
                avg_price=price,
                current_price=price,
            )

        self.cash -= total_cost
        self.trade_count += 1

        logger.info(
            "Opened: %s %.0f %s shares @ $%.3f ($%.2f) — Cash: $%.2f",
            market_id[:8], shares, side, price, total_cost, self.cash
        )
        return True

    def close_position(self, market_id: str, price: float):
        """Close an entire position at the given price."""

        if market_id not in self.positions:
            logger.warning("No position to close for market %s", market_id)
            return False

        pos = self.positions[market_id]
        proceeds = pos.shares * price
        pnl = proceeds - pos.cost_basis

        self.cash += proceeds
        self.realized_pnl += pnl
        self.trade_count += 1

        del self.positions[market_id]

        # Update peak after close changes total value
        current = self.total_value
        if current > self.peak_bankroll:
            self.peak_bankroll = current

        logger.info(
            "Closed: %s %.0f shares @ $%.3f — PnL: $%.2f — Cash: $%.2f",
            market_id[:8], pos.shares, price, pnl, self.cash
        )

        return True

    def resolve_position(self, market_id: str, won: bool):
        """Handle a market that has resolved (YES won or NO won).

        If we held the winning side, shares pay out at $1.00 each.
        If we held the losing side, shares are worth $0.00.
        """
        if market_id not in self.positions:
            return

        pos = self.positions[market_id]
        payout_price = 1.0 if won else 0.0
        proceeds = pos.shares * payout_price
        pnl = proceeds - pos.cost_basis

        self.cash += proceeds
        self.realized_pnl += pnl

        del self.positions[market_id]

        # Update peak after resolution changes total value
        current = self.total_value
        if current > self.peak_bankroll:
            self.peak_bankroll = current

        outcome = "WON" if won else "LOST"
        logger.info(
            "Resolved %s: %s — Payout: $%.2f — PnL: $%.2f",
            outcome, pos.question[:40], proceeds, pnl
        )

    # ──────────────────────────────────────────────
    # Price Updates
    # ──────────────────────────────────────────────

    def update_prices(self, prices: dict[str, float]):
        """Update current prices for all positions.

        Args:
            prices: Dict of {market_id: current_price}
        """
        for market_id, price in prices.items():
            if market_id in self.positions:
                self.positions[market_id].current_price = price

    # ──────────────────────────────────────────────
    # Portfolio Metrics
    # ──────────────────────────────────────────────

    @property
    def total_exposure(self) -> float:
        """Total capital currently at risk (sum of all position cost bases)."""
        return sum(pos.cost_basis for pos in self.positions.values())

    @property
    def total_value(self) -> float:
        """Total portfolio value = cash + current value of all positions."""
        position_value = sum(pos.current_value for pos in self.positions.values())
        return self.cash + position_value

    @property
    def total_unrealized_pnl(self) -> float:
        """Sum of unrealized PnL across all positions."""
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    @property
    def drawdown_pct(self) -> float:
        """Current drawdown from peak portfolio value, as a percentage."""
        current = self.total_value
        if current > self.peak_bankroll:
            self.peak_bankroll = current
            return 0.0
        if self.peak_bankroll == 0:
            return 0.0
        return (self.peak_bankroll - current) / self.peak_bankroll

    @property
    def exposure_pct(self) -> float:
        """What percentage of total value is currently at risk."""
        total = self.total_value
        if total == 0:
            return 0.0
        return self.total_exposure / total

    @property
    def num_positions(self) -> int:
        """Number of open positions."""
        return len(self.positions)

    # ──────────────────────────────────────────────
    # Snapshots & Reporting
    # ──────────────────────────────────────────────

    def _positions_to_json(self) -> str:
        """Serialize open positions to JSON for database persistence."""
        positions_data = {}
        for market_id, pos in self.positions.items():
            positions_data[market_id] = {
                "market_id": pos.market_id,
                "token_id": pos.token_id,
                "question": pos.question,
                "side": pos.side,
                "shares": pos.shares,
                "avg_price": pos.avg_price,
                "current_price": pos.current_price,
            }
        return json.dumps(positions_data)

    @classmethod
    async def load_from_db(cls) -> "Portfolio | None":
        """Reconstruct portfolio state from the latest database snapshot.

        Returns a Portfolio with restored cash, PnL, and positions,
        or None if no snapshot exists. (H-01 python analysis)
        """
        snapshot = await get_latest_snapshot()
        if not snapshot:
            return None

        portfolio = cls.__new__(cls)
        portfolio.positions = {}
        portfolio.realized_pnl = snapshot.get("realized_pnl", 0.0)
        portfolio.peak_bankroll = snapshot.get("peak_bankroll", INITIAL_BANKROLL)
        portfolio.trade_count = 0

        # Restore positions from JSON
        positions_json = snapshot.get("positions_json")
        if positions_json:
            try:
                positions_data = json.loads(positions_json)
                for market_id, pos_data in positions_data.items():
                    portfolio.positions[market_id] = Position(
                        market_id=pos_data["market_id"],
                        token_id=pos_data["token_id"],
                        question=pos_data.get("question", ""),
                        side=pos_data["side"],
                        shares=pos_data["shares"],
                        avg_price=pos_data["avg_price"],
                        current_price=pos_data.get("current_price", pos_data["avg_price"]),
                    )
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.error("Failed to restore positions from snapshot: %s", e)

        # Restore cash: bankroll (total value) minus position values
        position_value = sum(pos.current_value for pos in portfolio.positions.values())
        portfolio.cash = snapshot.get("bankroll", INITIAL_BANKROLL) - position_value

        logger.info(
            "Portfolio restored from DB: $%.2f cash, %d positions, $%.2f realized PnL",
            portfolio.cash, len(portfolio.positions), portfolio.realized_pnl
        )
        return portfolio

    async def save_snapshot(self):
        """Save current portfolio state to the database."""
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bankroll": self.total_value,
            "total_exposure": self.total_exposure,
            "open_positions": self.num_positions,
            "unrealized_pnl": self.total_unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "peak_bankroll": self.peak_bankroll,
            "drawdown_pct": self.drawdown_pct,
            "positions_json": self._positions_to_json(),
        }
        await insert_portfolio_snapshot(snapshot)
        logger.debug("Portfolio snapshot saved")

    def summary(self) -> str:
        """Human-readable portfolio summary."""
        lines = [
            f"Portfolio Summary",
            f"  Cash:          ${self.cash:.2f}",
            f"  Positions:     {self.num_positions}",
            f"  Exposure:      ${self.total_exposure:.2f} ({self.exposure_pct:.1%})",
            f"  Total Value:   ${self.total_value:.2f}",
            f"  Unrealized:    ${self.total_unrealized_pnl:+.2f}",
            f"  Realized:      ${self.realized_pnl:+.2f}",
            f"  Peak:          ${self.peak_bankroll:.2f}",
            f"  Drawdown:      {self.drawdown_pct:.1%}",
            f"  Trades:        {self.trade_count}",
        ]
        if self.positions:
            lines.append(f"  --- Open Positions ---")
            for pos in self.positions.values():
                lines.append(
                    f"  {pos.question[:35]:35s} | {pos.side:3s} | "
                    f"{pos.shares:.0f} @ ${pos.avg_price:.3f} | "
                    f"Now: ${pos.current_price:.3f} | "
                    f"PnL: ${pos.unrealized_pnl:+.2f}"
                )
        return "\n".join(lines)
