"""
Tests for the auto-exit logic — verifies stop loss, take profit,
and resolved market detection.

Uses mocked components — no API keys needed.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call

from src.agent.orchestrator import Orchestrator
from src.trading.executor import Executor, OrderResult
from src.trading.portfolio import Portfolio, Position


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def make_position(market_id="m1", token_id="t1", question="Test?",
                  side="YES", shares=10, avg_price=0.50, current_price=0.50,
                  estimated_prob=0.0):
    """Create a Position with the given parameters."""
    return Position(
        market_id=market_id,
        token_id=token_id,
        question=question,
        side=side,
        shares=shares,
        avg_price=avg_price,
        current_price=current_price,
        estimated_prob=estimated_prob,
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

    def test_resolved_no_token_wins(self):
        """NO position with token near $1 → market resolved NO → RESOLVED_NO."""
        pos = make_position(side="NO", avg_price=0.40, current_price=0.97)
        assert self.orch._check_exit(pos, 0.97) == "RESOLVED_NO"

    def test_resolved_yes_no_token_worthless(self):
        """NO position with token near $0 → market resolved YES → RESOLVED_YES."""
        pos = make_position(side="NO", avg_price=0.40, current_price=0.03)
        assert self.orch._check_exit(pos, 0.03) == "RESOLVED_YES"

    def test_resolved_at_boundary(self):
        """Price exactly at threshold should trigger."""
        pos = make_position(side="YES", avg_price=0.60, current_price=0.95)
        assert self.orch._check_exit(pos, 0.95) == "RESOLVED_YES"

        # NO token at $1 boundary — NO won
        pos_no_wins = make_position(side="NO", avg_price=0.40, current_price=0.95)
        assert self.orch._check_exit(pos_no_wins, 0.95) == "RESOLVED_NO"

        # NO token at $0 boundary — YES won, our NO is worthless
        pos_no_loses = make_position(side="NO", avg_price=0.40, current_price=0.05)
        assert self.orch._check_exit(pos_no_loses, 0.05) == "RESOLVED_YES"

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
# Option B: Fair Value Take Profit Tests
# ──────────────────────────────────────────────

class TestFairValueTakeProfit:
    """Unit tests for the probability-anchored take profit formula (Option B).

    Formula: take_profit = avg_price + 0.90 * (fair_value - avg_price)
      YES: fair_value = estimated_prob
      NO:  fair_value = 1.0 - estimated_prob
    Legacy fallback used when estimated_prob == 0.
    """

    def setup_method(self):
        self.orch = make_orchestrator()

    def test_yes_cheap_token_triggers_well_below_one(self):
        """YES entry 4.5¢, prob=15% → take_profit = 0.045 + 0.90*(0.15-0.045) ≈ 0.1395.
        Old formula would require 76¢. New formula exits at ~14¢."""
        # take_profit = 0.045 + 0.90*(0.105) = 0.045 + 0.0945 = 0.1395
        pos = make_position(side="YES", avg_price=0.045, current_price=0.14,
                            estimated_prob=0.15)
        assert self.orch._check_exit(pos, 0.14) == "TAKE_PROFIT"

    def test_yes_cheap_token_not_triggered_before_threshold(self):
        """Price at 12¢ should not trigger (threshold ≈ 13.95¢)."""
        pos = make_position(side="YES", avg_price=0.045, current_price=0.12,
                            estimated_prob=0.15)
        assert self.orch._check_exit(pos, 0.12) is None

    def test_no_token_uses_complement_probability(self):
        """NO entry 11¢, estimated_prob(YES)=18% → fair_value=82%.
        take_profit = 0.11 + 0.90*(0.82-0.11) = 0.11 + 0.639 = 0.749.
        Confirms old 4x heuristic (0.44) would NOT have triggered."""
        # take_profit ≈ 0.749
        pos = make_position(side="NO", avg_price=0.11, current_price=0.75,
                            estimated_prob=0.18)
        assert self.orch._check_exit(pos, 0.75) == "TAKE_PROFIT"

    def test_no_token_not_triggered_at_4x(self):
        """At 4x entry (0.44) should NOT trigger — fair value is 82¢, not 44¢."""
        pos = make_position(side="NO", avg_price=0.11, current_price=0.44,
                            estimated_prob=0.18)
        assert self.orch._check_exit(pos, 0.44) is None

    def test_fair_value_below_entry_falls_back_to_legacy(self):
        """When estimated_prob < avg_price (upside inverted), use legacy formula."""
        # YES entry 0.60, estimated_prob=0.40 → fair_value 0.40 < entry 0.60.
        # No upside on Option B — fallback: 0.60 + 0.75*(0.40) = 0.90.
        pos = make_position(side="YES", avg_price=0.60, current_price=0.91,
                            estimated_prob=0.40)
        assert self.orch._check_exit(pos, 0.91) == "TAKE_PROFIT"

    def test_fair_value_below_entry_no_premature_exit(self):
        """Fallback threshold at 0.90 — price of 0.80 should hold."""
        pos = make_position(side="YES", avg_price=0.60, current_price=0.80,
                            estimated_prob=0.40)
        assert self.orch._check_exit(pos, 0.80) is None

    def test_no_estimated_prob_uses_legacy_formula(self):
        """estimated_prob=0 (old/paper trade) → legacy 75%-to-$1 formula."""
        # Legacy: 0.40 + 0.75*(0.60) = 0.85
        pos = make_position(side="YES", avg_price=0.40, current_price=0.86,
                            estimated_prob=0.0)
        assert self.orch._check_exit(pos, 0.86) == "TAKE_PROFIT"

    def test_exact_threshold_triggers(self):
        """Price exactly at take_profit threshold should trigger."""
        # YES entry 0.045, prob=0.15 → threshold = 0.1395
        pos = make_position(side="YES", avg_price=0.045, current_price=0.1395,
                            estimated_prob=0.15)
        assert self.orch._check_exit(pos, 0.1395) == "TAKE_PROFIT"


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


# ──────────────────────────────────────────────
# BUG-025: Balance-verified phantom purge
# ──────────────────────────────────────────────

class TestPurgeIfBalanceZero:
    """Unit tests for Orchestrator._purge_if_balance_zero() (BUG-025).

    A failed SELL should only purge a position when the CLOB confirms
    zero token balance. Real tokens (e.g. size below CLOB minimum) must
    not be discarded.
    """

    def setup_method(self):
        self.orch = make_orchestrator()
        self.portfolio = Portfolio(starting_bankroll=100.0)
        self.orch.portfolio = self.portfolio

    def _add_position(self, market_id="m1", token_id="t1"):
        self.portfolio.open_position(
            market_id, token_id, "Test?", "NO", shares=5, price=0.20
        )

    def test_paper_mode_always_purges(self):
        """In paper mode there is no CLOB — always purge on SELL failure."""
        self._add_position()
        pos = self.portfolio.positions["m1"]
        with patch("src.agent.orchestrator.PAPER_TRADING", True):
            self.orch._purge_if_balance_zero("m1", pos)
        assert "m1" not in self.portfolio.positions

    def test_live_balance_zero_purges(self):
        """Balance = 0 → tokens never existed → purge."""
        self._add_position()
        pos = self.portfolio.positions["m1"]
        self.orch.client.get_token_balance.return_value = 0.0
        with patch("src.agent.orchestrator.PAPER_TRADING", False):
            self.orch._purge_if_balance_zero("m1", pos)
        assert "m1" not in self.portfolio.positions

    def test_live_balance_real_keeps_position(self):
        """Balance >= 1 → tokens are real → SELL rejected for other reason → keep."""
        self._add_position()
        pos = self.portfolio.positions["m1"]
        self.orch.client.get_token_balance.return_value = 5.0
        with patch("src.agent.orchestrator.PAPER_TRADING", False):
            self.orch._purge_if_balance_zero("m1", pos)
        assert "m1" in self.portfolio.positions

    def test_live_balance_api_error_keeps_position(self):
        """Balance = None (API error) → cannot confirm phantom → keep, retry later."""
        self._add_position()
        pos = self.portfolio.positions["m1"]
        self.orch.client.get_token_balance.return_value = None
        with patch("src.agent.orchestrator.PAPER_TRADING", False):
            self.orch._purge_if_balance_zero("m1", pos)
        assert "m1" in self.portfolio.positions


# ──────────────────────────────────────────────
# BUG-026: Gamma-verified resolution detection
# ──────────────────────────────────────────────

class TestGammaVerifiedResolution:
    """Unit tests for the Gamma API confirmation added to _check_exit (BUG-026).

    _check_exit() is still a pure price-based signal. Verification that the
    market is actually closed happens one level up in _monitor_positions().
    These tests confirm the RESOLVED path honours the Gamma closed flag.
    """

    def setup_method(self):
        self.orch = make_orchestrator()

    def test_price_near_zero_gamma_not_closed_returns_resolved(self):
        """_check_exit still signals RESOLVED when price is extreme."""
        pos = make_position(side="NO", avg_price=0.04, current_price=0.03)
        # _check_exit is pure — it doesn't consult Gamma; still returns RESOLVED
        assert self.orch._check_exit(pos, 0.03) == "RESOLVED_YES"

    def test_price_near_one_gamma_not_closed_returns_resolved(self):
        """_check_exit signals RESOLVED for high price regardless of Gamma."""
        pos = make_position(side="YES", avg_price=0.60, current_price=0.97)
        assert self.orch._check_exit(pos, 0.97) == "RESOLVED_YES"

    def test_gamma_closed_false_holds_position(self):
        """When Gamma says closed=False, _monitor_positions must NOT resolve."""
        portfolio = Portfolio(starting_bankroll=100.0)
        portfolio.open_position("m1", "t1", "Fed rates?", "NO", shares=52, price=0.026)
        portfolio.positions["m1"].current_price = 0.026  # near zero → RESOLVED_YES
        self.orch.portfolio = portfolio

        self.orch.client.get_midpoint.return_value = 0.026
        self.orch.client.get_market_by_id.return_value = {"closed": False, "active": True}

        with patch("src.agent.orchestrator.PAPER_TRADING", False), \
             patch("src.agent.orchestrator.asyncio") as mock_asyncio:
            mock_asyncio.run.return_value = None
            self.orch._reconcile_gtc_orders = MagicMock()
            self.orch._reconcile_sell_orders = MagicMock()
            self.orch._sync_cash_from_clob = MagicMock()
            self.orch._log_calibration_report = MagicMock()
            self.orch._monitor_positions({})

        # Position must still be open — market was not actually closed
        assert "m1" in self.orch.portfolio.positions

    def test_gamma_closed_true_resolves_position(self):
        """When Gamma says closed=True, _monitor_positions resolves the position."""
        portfolio = Portfolio(starting_bankroll=100.0)
        portfolio.open_position("m1", "t1", "Settled market?", "NO", shares=10, price=0.04)
        portfolio.positions["m1"].current_price = 0.03  # near zero → RESOLVED_YES

        self.orch.portfolio = portfolio
        self.orch.client.get_midpoint.return_value = 0.03
        self.orch.client.get_market_by_id.return_value = {"closed": True}

        with patch("src.agent.orchestrator.PAPER_TRADING", False), \
             patch("src.agent.orchestrator.asyncio") as mock_asyncio:
            mock_asyncio.run.return_value = None
            self.orch._reconcile_gtc_orders = MagicMock()
            self.orch._reconcile_sell_orders = MagicMock()
            self.orch._sync_cash_from_clob = MagicMock()
            self.orch._log_calibration_report = MagicMock()
            self.orch._monitor_positions({})

        # Position closed — market was confirmed settled
        assert "m1" not in self.orch.portfolio.positions

    def test_gamma_api_unavailable_holds_position(self):
        """When Gamma returns None (unreachable), hold the position."""
        portfolio = Portfolio(starting_bankroll=100.0)
        portfolio.open_position("m1", "t1", "Unreachable?", "NO", shares=10, price=0.04)
        portfolio.positions["m1"].current_price = 0.03

        self.orch.portfolio = portfolio
        self.orch.client.get_midpoint.return_value = 0.03
        self.orch.client.get_market_by_id.return_value = None  # API down

        with patch("src.agent.orchestrator.PAPER_TRADING", False), \
             patch("src.agent.orchestrator.asyncio") as mock_asyncio:
            mock_asyncio.run.return_value = None
            self.orch._reconcile_gtc_orders = MagicMock()
            self.orch._reconcile_sell_orders = MagicMock()
            self.orch._sync_cash_from_clob = MagicMock()
            self.orch._log_calibration_report = MagicMock()
            self.orch._monitor_positions({})

        # Position kept — cannot confirm without Gamma
        assert "m1" in self.orch.portfolio.positions
