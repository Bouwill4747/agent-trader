"""
Tests for the auto-exit logic — verifies stop loss, take profit,
and resolved market detection.

Uses mocked components — no API keys needed.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from src.agent.orchestrator import Orchestrator
from src.trading.executor import Executor, OrderResult
from src.trading.portfolio import Portfolio, Position


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def make_position(market_id="m1", token_id="t1", question="Test?",
                  side="YES", shares=10, avg_price=0.50, current_price=0.50):
    """Create a Position with the given parameters."""
    return Position(
        market_id=market_id,
        token_id=token_id,
        question=question,
        side=side,
        shares=shares,
        avg_price=avg_price,
        current_price=current_price,
    )


def make_orchestrator():
    """Create an Orchestrator with all external dependencies mocked out."""
    with patch("src.agent.orchestrator.PolymarketClient"), \
         patch("src.agent.orchestrator.NewsCollector"), \
         patch("src.agent.orchestrator.StocktwitsCollector"), \
         patch("src.agent.orchestrator.SignalGenerator"), \
         patch("src.agent.orchestrator.RiskManager"), \
         patch("src.trading.executor.PAPER_TRADING", True):
        orch = Orchestrator()
    return orch


# ──────────────────────────────────────────────
# Exit Condition Tests
# ──────────────────────────────────────────────

class TestExitConditions:
    """Unit tests for Orchestrator._check_exit()."""

    def setup_method(self):
        self.orch = make_orchestrator()

    def test_resolved_yes(self):
        """Price >= 0.95 should trigger RESOLVED_YES."""
        pos = make_position(side="YES", avg_price=0.60, current_price=0.97)
        assert self.orch._check_exit(pos, 0.97) == "RESOLVED_YES"

    def test_resolved_no(self):
        """Price <= 0.05 should trigger RESOLVED_NO."""
        pos = make_position(side="NO", avg_price=0.40, current_price=0.03)
        assert self.orch._check_exit(pos, 0.03) == "RESOLVED_NO"

    def test_resolved_at_boundary(self):
        """Price exactly at threshold should trigger."""
        pos = make_position(side="YES", avg_price=0.60, current_price=0.95)
        assert self.orch._check_exit(pos, 0.95) == "RESOLVED_YES"

        pos_no = make_position(side="NO", avg_price=0.40, current_price=0.05)
        assert self.orch._check_exit(pos_no, 0.05) == "RESOLVED_NO"

    def test_stop_loss_triggered(self):
        """Position down 40%+ should trigger STOP_LOSS."""
        # Bought at $0.50, now at $0.25 → PnL = -50%
        pos = make_position(side="YES", avg_price=0.50, current_price=0.25)
        assert self.orch._check_exit(pos, 0.25) == "STOP_LOSS"

    def test_stop_loss_not_triggered(self):
        """Position down only 20% should hold."""
        # Bought at $0.50, now at $0.40 → PnL = -20%
        pos = make_position(side="YES", avg_price=0.50, current_price=0.40)
        assert self.orch._check_exit(pos, 0.40) is None

    def test_take_profit_yes(self):
        """YES position should exit when price reaches 75% of distance to $1."""
        # Bought YES at $0.40 → take_profit = 0.40 + 0.75*(1.0-0.40) = 0.85
        pos = make_position(side="YES", avg_price=0.40, current_price=0.86)
        assert self.orch._check_exit(pos, 0.86) == "TAKE_PROFIT"

    def test_take_profit_yes_not_triggered(self):
        """YES position below take-profit threshold should hold."""
        # Bought YES at $0.40 → take_profit = 0.85, current = 0.70
        pos = make_position(side="YES", avg_price=0.40, current_price=0.70)
        assert self.orch._check_exit(pos, 0.70) is None

    def test_take_profit_no(self):
        """NO token take profit: price moves 75% toward $1 (same as YES)."""
        # Bought NO at $0.40 → take_profit = 0.40 + 0.75*(1.0-0.40) = 0.85
        pos = make_position(side="NO", avg_price=0.40, current_price=0.86)
        assert self.orch._check_exit(pos, 0.86) == "TAKE_PROFIT"

    def test_take_profit_no_not_triggered(self):
        """NO position below take-profit threshold should hold."""
        # Bought NO at $0.40 → take_profit = 0.85, current = 0.60
        pos = make_position(side="NO", avg_price=0.40, current_price=0.60)
        assert self.orch._check_exit(pos, 0.60) is None

    def test_hold_position(self):
        """Healthy position in the middle should return None."""
        pos = make_position(side="YES", avg_price=0.50, current_price=0.55)
        assert self.orch._check_exit(pos, 0.55) is None


# ──────────────────────────────────────────────
# Paper Exit Integration Tests
# ──────────────────────────────────────────────

class TestPaperExit:
    """Integration tests: execute_exit() in paper mode updates portfolio correctly."""

    def test_paper_exit_updates_portfolio(self):
        """Paper exit should close position, restore cash, and record PnL."""
        portfolio = Portfolio(starting_bankroll=100.0)
        client = MagicMock()
        client.clob = None

        with patch("src.trading.executor.PAPER_TRADING", True):
            executor = Executor(client, portfolio)

        # Open a position: 10 shares of YES at $0.50 = $5.00 cost
        portfolio.open_position("m1", "t1", "Test?", "YES", shares=10, price=0.50)
        assert portfolio.cash == 95.0
        assert portfolio.num_positions == 1

        # Exit at $0.70 (profit)
        with patch("src.trading.executor.insert_trade", new_callable=AsyncMock):
            result = asyncio.run(executor.execute_exit(
                market_id="m1",
                token_id="t1",
                question="Test?",
                shares=10,
                price=0.70,
                reason="TAKE_PROFIT",
            ))

        assert result.success is True
        assert result.paper_trade is True
        assert "PAPER-EXIT" in result.order_id
        assert result.fill_price == 0.70
        assert result.fill_size == 10

        # Portfolio should be updated
        assert portfolio.num_positions == 0
        assert portfolio.cash == 102.0    # 95.0 + (10 * 0.70)
        assert portfolio.realized_pnl == 2.0  # (0.70 - 0.50) * 10

    def test_paper_exit_with_loss(self):
        """Paper exit at a loss should record negative PnL."""
        portfolio = Portfolio(starting_bankroll=100.0)
        client = MagicMock()
        client.clob = None

        with patch("src.trading.executor.PAPER_TRADING", True):
            executor = Executor(client, portfolio)

        portfolio.open_position("m1", "t1", "Test?", "YES", shares=10, price=0.50)

        with patch("src.trading.executor.insert_trade", new_callable=AsyncMock):
            result = asyncio.run(executor.execute_exit(
                market_id="m1",
                token_id="t1",
                question="Test?",
                shares=10,
                price=0.30,
                reason="STOP_LOSS",
            ))

        assert result.success is True
        assert portfolio.num_positions == 0
        assert portfolio.realized_pnl == -2.0  # (0.30 - 0.50) * 10
        assert portfolio.cash == 98.0           # 95.0 + (10 * 0.30)
