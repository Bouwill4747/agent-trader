"""
Tests for the executor — verifies paper trading, order recording,
and portfolio integration.

Uses mocked Polymarket client — no API keys needed.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from src.trading.executor import Executor, OrderResult
from src.trading.portfolio import Portfolio
from src.trading.risk_manager import RiskDecision


@pytest.fixture
def mock_client():
    """Create a mocked PolymarketClient."""
    client = MagicMock()
    client.clob = None  # No CLOB = paper mode forced
    return client


@pytest.fixture
def portfolio():
    """Create a fresh portfolio with $100."""
    return Portfolio(starting_bankroll=100.0)


@pytest.fixture
def executor(mock_client, portfolio):
    """Create an executor in paper mode."""
    with patch("src.trading.executor.PAPER_TRADING", True):
        return Executor(mock_client, portfolio)


def make_approved_decision(size=5.0, shares=10.0):
    """Helper to create an approved RiskDecision."""
    return RiskDecision(
        approved=True,
        position_size=size,
        shares=shares,
        reason="Trade approved",
        kelly_raw=0.33,
        kelly_sized=0.083,
    )


def make_rejected_decision():
    """Helper to create a rejected RiskDecision."""
    return RiskDecision(
        approved=False,
        position_size=0,
        shares=0,
        reason="Edge too small",
    )


# ──────────────────────────────────────────────
# Paper Trading
# ──────────────────────────────────────────────

class TestPaperTrading:
    """Verify paper mode simulates trades correctly."""

    def test_paper_trade_succeeds(self, executor):
        """Paper trade should always succeed with correct fill data."""
        decision = make_approved_decision(size=5.0, shares=10.0)

        with patch("src.trading.executor.insert_trade", new_callable=AsyncMock):
            result = asyncio.run(
                executor.execute_trade(
                    market_id="market-123",
                    token_id="token-yes-123",
                    question="Will BTC hit $100k?",
                    side="BUY",
                    price=0.50,
                    risk_decision=decision,
                )
            )

        assert result.success is True
        assert result.paper_trade is True
        assert result.fill_price == 0.50
        assert result.fill_size == 10.0
        assert "PAPER" in result.order_id

    def test_rejected_trade_returns_failure(self, executor):
        """Rejected risk decision should immediately return failure."""
        decision = make_rejected_decision()

        result = asyncio.run(
            executor.execute_trade(
                market_id="market-123",
                token_id="token-yes-123",
                question="Test market",
                side="BUY",
                price=0.50,
                risk_decision=decision,
            )
        )

        assert result.success is False
        assert "rejected" in result.message.lower()

    def test_paper_trade_updates_portfolio(self, executor, portfolio):
        """Paper trade should create a position in the portfolio."""
        decision = make_approved_decision(size=5.0, shares=10.0)

        with patch("src.trading.executor.insert_trade", new_callable=AsyncMock):
            asyncio.run(
                executor.execute_trade(
                    market_id="market-123",
                    token_id="token-yes-123",
                    question="Will BTC hit $100k?",
                    side="BUY",
                    price=0.50,
                    risk_decision=decision,
                )
            )

        # Portfolio should now have a position
        assert portfolio.num_positions == 1
        assert "market-123" in portfolio.positions
        assert portfolio.cash < 100.0  # Cash reduced


# ──────────────────────────────────────────────
# Portfolio Integration
# ──────────────────────────────────────────────

class TestPortfolioIntegration:
    """Verify executor correctly updates the portfolio."""

    def test_multiple_trades_tracked(self, executor, portfolio):
        """Multiple trades should create multiple positions."""
        with patch("src.trading.executor.insert_trade", new_callable=AsyncMock):
            for i in range(3):
                decision = make_approved_decision(size=5.0, shares=10.0)
                asyncio.run(
                    executor.execute_trade(
                        market_id=f"market-{i}",
                        token_id=f"token-{i}",
                        question=f"Test market {i}",
                        side="BUY",
                        price=0.50,
                        risk_decision=decision,
                    )
                )

        assert portfolio.num_positions == 3
        assert portfolio.cash == 85.0  # 100 - (3 * 10 * 0.50)


# ──────────────────────────────────────────────
# Portfolio Standalone
# ──────────────────────────────────────────────

class TestPortfolio:
    """Test portfolio math independently."""

    def test_initial_state(self):
        """Fresh portfolio should have full cash and no positions."""
        p = Portfolio(starting_bankroll=100.0)
        assert p.cash == 100.0
        assert p.num_positions == 0
        assert p.total_value == 100.0
        assert p.drawdown_pct == 0.0

    def test_open_position(self):
        """Opening a position reduces cash and creates a holding."""
        p = Portfolio(starting_bankroll=100.0)
        result = p.open_position("m1", "t1", "Test?", "YES", shares=10, price=0.50)

        assert result is True
        assert p.cash == 95.0           # 100 - (10 * 0.50)
        assert p.num_positions == 1
        assert p.positions["m1"].shares == 10
        assert p.positions["m1"].avg_price == 0.50

    def test_close_position_with_profit(self):
        """Closing at a higher price should generate profit."""
        p = Portfolio(starting_bankroll=100.0)
        p.open_position("m1", "t1", "Test?", "YES", shares=10, price=0.50)
        p.close_position("m1", price=0.70)

        assert p.num_positions == 0
        assert p.cash == 102.0          # 95.0 + (10 * 0.70)
        assert p.realized_pnl == 2.0    # (0.70 - 0.50) * 10

    def test_close_position_with_loss(self):
        """Closing at a lower price should record a loss."""
        p = Portfolio(starting_bankroll=100.0)
        p.open_position("m1", "t1", "Test?", "YES", shares=10, price=0.50)
        p.close_position("m1", price=0.30)

        assert p.realized_pnl == -2.0   # (0.30 - 0.50) * 10

    def test_resolve_winning_position(self):
        """Winning resolution pays $1.00 per share."""
        p = Portfolio(starting_bankroll=100.0)
        p.open_position("m1", "t1", "Test?", "YES", shares=10, price=0.55)
        p.resolve_position("m1", won=True)

        assert p.num_positions == 0
        assert p.realized_pnl == 4.5    # (1.00 - 0.55) * 10

    def test_resolve_losing_position(self):
        """Losing resolution pays $0.00 per share — total loss."""
        p = Portfolio(starting_bankroll=100.0)
        p.open_position("m1", "t1", "Test?", "YES", shares=10, price=0.55)
        p.resolve_position("m1", won=False)

        assert p.num_positions == 0
        assert p.realized_pnl == -5.5   # (0.00 - 0.55) * 10

    def test_drawdown_calculation(self):
        """Drawdown should track decline from peak."""
        p = Portfolio(starting_bankroll=100.0)

        # Open and close with a loss
        p.open_position("m1", "t1", "Test?", "YES", shares=20, price=0.50)
        p.close_position("m1", price=0.30)

        # Cash: 100 - 10 + 6 = 96. PnL = -4.
        # Peak was 100, current is 96 → drawdown = 4%
        assert p.total_value == 96.0
        assert abs(p.drawdown_pct - 0.04) < 0.01

    def test_insufficient_cash_rejected(self):
        """Can't open position larger than available cash."""
        p = Portfolio(starting_bankroll=10.0)
        result = p.open_position("m1", "t1", "Test?", "YES", shares=100, price=0.50)

        assert result is False
        assert p.num_positions == 0

    def test_position_averaging(self):
        """Adding to a position should recalculate average price."""
        p = Portfolio(starting_bankroll=100.0)
        p.open_position("m1", "t1", "Test?", "YES", shares=10, price=0.50)
        p.open_position("m1", "t1", "Test?", "YES", shares=10, price=0.60)

        pos = p.positions["m1"]
        assert pos.shares == 20
        assert abs(pos.avg_price - 0.55) < 0.01  # (5 + 6) / 20
