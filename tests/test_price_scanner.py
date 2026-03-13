"""
Tests for the background price scanner.

Covers:
 - Scanner thread is a daemon
 - Positions with selling_pending are skipped
 - RESOLVED_SCANNER exit triggered immediately at price ≥ 0.95 or ≤ 0.05
 - STRUCTURAL_COLLAPSE exit triggered at price ≤ avg × 50%
 - TAKE_PROFIT requires 2 consecutive confirmations
 - TAKE_PROFIT counter resets when signal disappears
 - STOP_LOSS is NOT triggered by the scanner
 - _scanner_trigger_exit sets selling_pending atomically under lock
 - Concurrent claim: second trigger is a no-op when selling_pending already set
 - Counter cleaned up after successful TAKE_PROFIT exit

No API keys needed — all external calls are mocked.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.agent.orchestrator import Orchestrator
from src.trading.portfolio import Portfolio, Position


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def make_orchestrator():
    """Create an Orchestrator with all external dependencies mocked."""
    with patch("src.agent.orchestrator.PolymarketClient"), \
         patch("src.agent.orchestrator.NewsCollector"), \
         patch("src.agent.orchestrator.StocktwitsCollector"), \
         patch("src.agent.orchestrator.SignalGenerator"), \
         patch("src.agent.orchestrator.RiskManager"), \
         patch("src.trading.executor.PAPER_TRADING", True):
        orch = Orchestrator()
    return orch


def make_position(market_id="mkt1", avg_price=0.40, current_price=0.40,
                  estimated_prob=0.75, selling_pending=False) -> Position:
    return Position(
        market_id=market_id,
        token_id="tok1",
        question="Will X happen?",
        side="YES",
        shares=10.0,
        avg_price=avg_price,
        current_price=current_price,
        estimated_prob=estimated_prob,
        selling_pending=selling_pending,
    )


# ──────────────────────────────────────────────
# Thread behaviour
# ──────────────────────────────────────────────

class TestScannerThread:
    def test_start_price_scanner_spawns_daemon_thread(self):
        orch = make_orchestrator()
        threads_before = {t.name for t in threading.enumerate()}
        orch._start_price_scanner()
        threads_after = {t.name for t in threading.enumerate()}
        assert "price-scanner" in threads_after - threads_before
        # Verify daemon flag
        scanner_thread = next(t for t in threading.enumerate() if t.name == "price-scanner")
        assert scanner_thread.daemon is True

    def test_start_price_scanner_idempotent(self):
        """Calling _start_price_scanner twice creates only one thread."""
        orch = make_orchestrator()
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            orch._start_price_scanner()
            orch._start_price_scanner()
            assert mock_thread.call_count == 1


# ──────────────────────────────────────────────
# _run_price_scan — position filtering
# ──────────────────────────────────────────────

class TestRunPriceScan:
    def setup_method(self):
        self.orch = make_orchestrator()
        self.orch.portfolio = Portfolio(starting_bankroll=100)

    def test_selling_pending_position_skipped(self):
        pos = make_position(selling_pending=True)
        self.orch.portfolio.positions["mkt1"] = pos
        self.orch.client.get_midpoint = MagicMock(return_value=0.97)
        self.orch._scanner_exit_check = MagicMock()
        self.orch._run_price_scan()
        self.orch._scanner_exit_check.assert_not_called()

    def test_midpoint_none_skipped(self):
        pos = make_position()
        self.orch.portfolio.positions["mkt1"] = pos
        self.orch.client.get_midpoint = MagicMock(return_value=None)
        self.orch._scanner_exit_check = MagicMock()
        self.orch._run_price_scan()
        self.orch._scanner_exit_check.assert_not_called()

    def test_current_price_updated(self):
        pos = make_position(current_price=0.40)
        self.orch.portfolio.positions["mkt1"] = pos
        self.orch.client.get_midpoint = MagicMock(return_value=0.72)
        self.orch._scanner_exit_check = MagicMock()
        self.orch._run_price_scan()
        assert pos.current_price == pytest.approx(0.72)

    def test_exit_check_called_with_midpoint(self):
        pos = make_position()
        self.orch.portfolio.positions["mkt1"] = pos
        self.orch.client.get_midpoint = MagicMock(return_value=0.55)
        self.orch._scanner_exit_check = MagicMock()
        self.orch._run_price_scan()
        self.orch._scanner_exit_check.assert_called_once_with("mkt1", pos, 0.55)


# ──────────────────────────────────────────────
# _scanner_exit_check — tiered logic
# ──────────────────────────────────────────────

class TestScannerExitCheck:
    def setup_method(self):
        self.orch = make_orchestrator()
        self.orch.portfolio = Portfolio(starting_bankroll=100)
        self.orch._scanner_trigger_exit = MagicMock()

    def test_resolved_high_price_immediate_exit(self):
        """Price ≥ 0.95 triggers RESOLVED_SCANNER immediately."""
        pos = make_position(avg_price=0.40, estimated_prob=0.75)
        self.orch._scanner_exit_check("mkt1", pos, 0.96)
        self.orch._scanner_trigger_exit.assert_called_once_with(
            "mkt1", pos, 0.96, "RESOLVED_SCANNER"
        )

    def test_resolved_low_price_immediate_exit(self):
        """Price ≤ 0.05 (opposing side won) triggers RESOLVED_SCANNER."""
        pos = make_position(avg_price=0.40, estimated_prob=0.75)
        self.orch._scanner_exit_check("mkt1", pos, 0.04)
        self.orch._scanner_trigger_exit.assert_called_once_with(
            "mkt1", pos, 0.04, "RESOLVED_SCANNER"
        )

    def test_structural_collapse_immediate_exit(self):
        """Price ≤ avg × 50% triggers STRUCTURAL_COLLAPSE."""
        pos = make_position(avg_price=0.40)  # collapse at ≤ 0.20
        self.orch._scanner_exit_check("mkt1", pos, 0.19)
        self.orch._scanner_trigger_exit.assert_called_once_with(
            "mkt1", pos, 0.19, "STRUCTURAL_COLLAPSE"
        )

    def test_structural_collapse_boundary_not_triggered(self):
        """Price just above 50% of avg (0.21 when avg=0.40) does NOT collapse."""
        pos = make_position(avg_price=0.40)
        self.orch._scanner_exit_check("mkt1", pos, 0.21)  # 0.21 > 0.20 threshold
        self.orch._scanner_trigger_exit.assert_not_called()

    def test_stop_loss_not_triggered_by_scanner(self):
        """A -25% loss (stop-loss territory) is ignored by the scanner."""
        # avg=0.40, stop-loss at current_price ≤ 0.30 (–25%)
        pos = make_position(avg_price=0.40, estimated_prob=0.75)
        self.orch._scanner_exit_check("mkt1", pos, 0.28)  # –30%, stop-loss range
        self.orch._scanner_trigger_exit.assert_not_called()

    def test_take_profit_first_scan_no_exit(self):
        """First TAKE_PROFIT signal increments counter but does not exit."""
        pos = make_position(avg_price=0.40, estimated_prob=0.95)
        # estimated_prob=0.95, avg=0.40 → fair_value=0.95, take_profit at ≈ 0.94
        with patch.object(self.orch, "_check_exit", return_value="TAKE_PROFIT"):
            self.orch._scanner_exit_check("mkt1", pos, 0.94)
        self.orch._scanner_trigger_exit.assert_not_called()
        assert self.orch._take_profit_hits["mkt1"] == 1

    def test_take_profit_second_scan_triggers_exit(self):
        """Second consecutive TAKE_PROFIT signal triggers the exit."""
        pos = make_position(avg_price=0.40, estimated_prob=0.95)
        with patch.object(self.orch, "_check_exit", return_value="TAKE_PROFIT"):
            self.orch._scanner_exit_check("mkt1", pos, 0.94)  # scan 1
            self.orch._scanner_exit_check("mkt1", pos, 0.94)  # scan 2
        self.orch._scanner_trigger_exit.assert_called_once_with(
            "mkt1", pos, 0.94, "TAKE_PROFIT"
        )

    def test_take_profit_counter_resets_on_miss(self):
        """Counter resets when price drops back below take-profit level."""
        pos = make_position(avg_price=0.40, estimated_prob=0.95)
        with patch.object(self.orch, "_check_exit", return_value="TAKE_PROFIT"):
            self.orch._scanner_exit_check("mkt1", pos, 0.94)  # scan 1: counter=1
        assert self.orch._take_profit_hits["mkt1"] == 1

        # Price dips — _check_exit returns None
        with patch.object(self.orch, "_check_exit", return_value=None):
            self.orch._scanner_exit_check("mkt1", pos, 0.70)  # counter reset
        assert "mkt1" not in self.orch._take_profit_hits

        # Starts fresh — one more scan does not trigger
        with patch.object(self.orch, "_check_exit", return_value="TAKE_PROFIT"):
            self.orch._scanner_exit_check("mkt1", pos, 0.94)  # scan 1 again
        self.orch._scanner_trigger_exit.assert_not_called()

    def test_take_profit_counter_cleaned_up_after_exit(self):
        """Counter entry is removed from dict after exit fires."""
        pos = make_position(avg_price=0.40, estimated_prob=0.95)
        with patch.object(self.orch, "_check_exit", return_value="TAKE_PROFIT"):
            self.orch._scanner_exit_check("mkt1", pos, 0.94)  # scan 1
            self.orch._scanner_exit_check("mkt1", pos, 0.94)  # scan 2 → exit
        assert "mkt1" not in self.orch._take_profit_hits

    def test_resolved_takes_priority_over_collapse(self):
        """RESOLVED_SCANNER fires even if price also meets collapse threshold."""
        # avg=0.40, price=0.04 (both resolved AND collapsed)
        pos = make_position(avg_price=0.40)
        self.orch._scanner_exit_check("mkt1", pos, 0.04)
        # Should be RESOLVED_SCANNER, not STRUCTURAL_COLLAPSE
        self.orch._scanner_trigger_exit.assert_called_once_with(
            "mkt1", pos, 0.04, "RESOLVED_SCANNER"
        )


# ──────────────────────────────────────────────
# _scanner_trigger_exit — concurrency safety
# ──────────────────────────────────────────────

class TestScannerTriggerExit:
    def setup_method(self):
        self.orch = make_orchestrator()
        self.orch.portfolio = Portfolio(starting_bankroll=100)
        # Mock execute_exit to return a successful result without DB calls
        mock_result = MagicMock()
        mock_result.success = True
        self.orch.executor.execute_exit = AsyncMock(return_value=mock_result)

    def test_sets_selling_pending_atomically(self):
        pos = make_position()
        self.orch.portfolio.positions["mkt1"] = pos
        self.orch._scanner_trigger_exit("mkt1", pos, 0.96, "RESOLVED_SCANNER")
        # selling_pending may be True (set under lock) or position gone (paper close)
        # Either outcome is valid — the key is no exception was raised
        assert "mkt1" not in self.orch.portfolio.positions or \
               self.orch.portfolio.positions["mkt1"].selling_pending

    def test_already_selling_pending_is_noop(self):
        """Second trigger while selling_pending=True does nothing."""
        pos = make_position(selling_pending=True)
        self.orch.portfolio.positions["mkt1"] = pos
        self.orch._scanner_trigger_exit("mkt1", pos, 0.96, "RESOLVED_SCANNER")
        # execute_exit should NOT have been called
        self.orch.executor.execute_exit.assert_not_called()

    def test_position_already_gone_is_noop(self):
        """Trigger on a position that was already removed does nothing."""
        pos = make_position()
        # Don't add pos to portfolio.positions — simulates already-closed position
        self.orch._scanner_trigger_exit("mkt1", pos, 0.96, "RESOLVED_SCANNER")
        self.orch.executor.execute_exit.assert_not_called()

    def test_execute_exit_called_with_correct_args(self):
        pos = make_position(avg_price=0.40, current_price=0.96)
        self.orch.portfolio.positions["mkt1"] = pos
        self.orch._scanner_trigger_exit("mkt1", pos, 0.96, "RESOLVED_SCANNER")
        self.orch.executor.execute_exit.assert_called_once_with(
            market_id="mkt1",
            token_id="tok1",
            question="Will X happen?",
            shares=10.0,
            price=0.96,
            reason="RESOLVED_SCANNER",
        )

    def test_selling_pending_released_on_execute_error(self):
        """If execute_exit raises, selling_pending is reset so position can retry."""
        pos = make_position()
        self.orch.portfolio.positions["mkt1"] = pos
        self.orch.executor.execute_exit = AsyncMock(side_effect=RuntimeError("CLOB down"))

        self.orch._scanner_trigger_exit("mkt1", pos, 0.96, "RESOLVED_SCANNER")

        # Position should still be there (paper mode didn't close it) with selling_pending=False
        assert "mkt1" in self.orch.portfolio.positions
        assert self.orch.portfolio.positions["mkt1"].selling_pending is False
