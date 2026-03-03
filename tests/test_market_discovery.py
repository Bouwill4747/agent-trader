"""
Tests for tiered market discovery.

Covers:
 - _days_until_resolution() parsing (ISO string, camelCase, missing, bad)
 - Three-call API structure (correct params per tier)
 - Near-expiry filter (< MIN_DAYS_TO_RESOLUTION skipped)
 - Tier-appropriate volume/liquidity thresholds
 - Deduplication across pools
 - Sports/entertainment keyword filter still fires
 - Per-tier intra-cycle exposure caps in _evaluate_risks()

No API keys needed — all external calls are mocked.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

from src.agent.orchestrator import Orchestrator, _days_until_resolution
from src.analysis.signal_generator import TradingSignal
from src.trading.risk_manager import RiskDecision


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


def _market(mid, days, volume=5000, liquidity=1000, question="Will X happen?"):
    """Build a minimal Gamma market dict resolving `days` from now."""
    end = datetime.now(timezone.utc) + timedelta(days=days)
    return {
        "id": mid,
        "condition_id": mid,
        "question": question,
        "volume": volume,
        "liquidity": liquidity,
        "outcomePrices": "[0.5, 0.5]",
        "end_date_iso": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _signal(market_id, size=5.0, direction="BUY_YES", days=None, question="Will X?"):
    """Build a TradingSignal with a given market_id."""
    s = MagicMock(spec=TradingSignal)
    s.market_id    = market_id
    s.direction    = direction
    s.question     = question
    s.estimated_prob = 0.75
    s.current_price  = 0.50
    s.confidence     = "high"
    s.resolution_type = "mechanical_numeric"
    s.resolution_clarity_score = 5
    s.spread         = 0.02
    s.liquidity      = 5000
    s.market_theme   = "other"
    return s


def _approved_decision(size=5.0):
    d = MagicMock(spec=RiskDecision)
    d.approved = True
    d.position_size = size
    d.reason = ""
    return d


# ──────────────────────────────────────────────
# _days_until_resolution
# ──────────────────────────────────────────────

class TestDaysUntilResolution:
    """Unit tests for the date-parsing helper."""

    def test_end_date_iso(self):
        """Standard ISO field parsed correctly."""
        future = datetime.now(timezone.utc) + timedelta(days=10)
        market = {"end_date_iso": future.strftime("%Y-%m-%dT%H:%M:%SZ")}
        result = _days_until_resolution(market)
        assert 9 <= result <= 10

    def test_endDate_camel(self):
        """camelCase endDate field (Gamma API alternate response) parsed."""
        future = datetime.now(timezone.utc) + timedelta(days=7)
        market = {"endDate": future.strftime("%Y-%m-%dT%H:%M:%SZ")}
        result = _days_until_resolution(market)
        assert 6 <= result <= 7

    def test_missing_date_returns_999(self):
        """No date fields → falls back to 999 (treated as long-term)."""
        assert _days_until_resolution({}) == 999

    def test_unparseable_date_returns_999(self):
        """Garbage string → falls back to 999."""
        assert _days_until_resolution({"end_date_iso": "not-a-date"}) == 999

    def test_end_date_iso_preferred_over_endDate(self):
        """end_date_iso takes priority over camelCase endDate."""
        future_soon  = datetime.now(timezone.utc) + timedelta(days=3)
        future_later = datetime.now(timezone.utc) + timedelta(days=30)
        market = {
            "end_date_iso": future_soon.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDate":      future_later.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        result = _days_until_resolution(market)
        assert result <= 4  # took the soon date


# ──────────────────────────────────────────────
# Three-call API structure
# ──────────────────────────────────────────────

class TestThreeApiCalls:
    """Verify _discover_markets() makes three correctly-parametrised calls."""

    def setup_method(self):
        self.orch = make_orchestrator()
        self.orch.client.get_markets = MagicMock(return_value=[])

    def test_three_api_calls_made(self):
        """get_markets() is called exactly three times."""
        self.orch._discover_markets({})
        assert self.orch.client.get_markets.call_count == 3

    def test_short_call_limit_100(self):
        """Short-tier call uses limit=100 (larger pool for sparse universe)."""
        self.orch._discover_markets({})
        first_call = self.orch.client.get_markets.call_args_list[0]
        assert first_call.kwargs.get("limit") == 100

    def test_short_call_has_end_date_max(self):
        """Short-tier call includes end_date_max ~ today+14d."""
        self.orch._discover_markets({})
        first_call = self.orch.client.get_markets.call_args_list[0]
        assert "end_date_max" in first_call.kwargs
        assert "end_date_min" in first_call.kwargs

    def test_medium_call_has_both_bounds(self):
        """Medium-tier call has both end_date_min and end_date_max."""
        self.orch._discover_markets({})
        second_call = self.orch.client.get_markets.call_args_list[1]
        assert "end_date_min" in second_call.kwargs
        assert "end_date_max" in second_call.kwargs

    def test_long_call_has_end_date_min(self):
        """Long-tier call includes end_date_min to exclude short/medium markets."""
        self.orch._discover_markets({})
        third_call = self.orch.client.get_markets.call_args_list[2]
        assert "end_date_min" in third_call.kwargs
        assert "end_date_max" not in third_call.kwargs


# ──────────────────────────────────────────────
# Near-expiry filter
# ──────────────────────────────────────────────

class TestNearExpiryFilter:
    """Markets resolving < 48h are skipped."""

    def setup_method(self):
        self.orch = make_orchestrator()

    def test_near_expiry_market_skipped(self):
        """Market resolving in 1 day is dropped from selection."""
        near = _market("near", days=1, volume=5000, liquidity=1000)
        safe = _market("safe", days=7, volume=5000, liquidity=1000)

        self.orch.client.get_markets = MagicMock(side_effect=[
            [near, safe],  # short call
            [],            # medium call
            [],            # long call
        ])
        result = self.orch._discover_markets({})
        ids = [m.get("id") for m in result["markets"]]
        assert "near" not in ids
        assert "safe" in ids


# ──────────────────────────────────────────────
# Short-term surfaced and prioritised
# ──────────────────────────────────────────────

class TestTierPrioritisation:

    def setup_method(self):
        self.orch = make_orchestrator()

    def test_short_term_markets_surfaced(self):
        """Short-term market (7d) appears in selected list."""
        short = _market("short7", days=7, volume=500, liquidity=200)
        long_ = _market("long90", days=90, volume=5000, liquidity=2000)

        self.orch.client.get_markets = MagicMock(side_effect=[
            [short],   # short call returns the short market
            [],        # medium
            [long_],   # long
        ])
        result = self.orch._discover_markets({})
        ids = [m.get("id") for m in result["markets"]]
        assert "short7" in ids

    def test_short_term_prioritised_over_long(self):
        """Short-term market appears before long-term market in the ordered list."""
        short = _market("short7", days=7, volume=500, liquidity=200)
        long_ = _market("long90", days=90, volume=5000, liquidity=2000)

        self.orch.client.get_markets = MagicMock(side_effect=[
            [short],   # short call
            [],        # medium
            [long_],   # long
        ])
        result = self.orch._discover_markets({})
        ids = [m.get("id") for m in result["markets"]]
        assert ids.index("short7") < ids.index("long90")


# ──────────────────────────────────────────────
# Tier-appropriate thresholds
# ──────────────────────────────────────────────

class TestTierThresholds:

    def setup_method(self):
        self.orch = make_orchestrator()

    def test_low_vol_short_term_rejected(self):
        """Short-term market below SHORT_TERM_MIN_VOLUME is filtered out."""
        # volume=100 < SHORT_TERM_MIN_VOLUME=300
        low_vol = _market("low", days=7, volume=100, liquidity=200)
        ok      = _market("ok",  days=7, volume=500, liquidity=200)

        self.orch.client.get_markets = MagicMock(side_effect=[
            [low_vol, ok], [], [],
        ])
        result = self.orch._discover_markets({})
        ids = [m.get("id") for m in result["markets"]]
        assert "low" not in ids
        assert "ok" in ids

    def test_low_liq_short_term_rejected(self):
        """Short-term market below SHORT_TERM_MIN_LIQUIDITY is filtered out."""
        # liquidity=50 < SHORT_TERM_MIN_LIQUIDITY=150
        low_liq = _market("liq", days=7, volume=500, liquidity=50)
        ok      = _market("ok",  days=7, volume=500, liquidity=200)

        self.orch.client.get_markets = MagicMock(side_effect=[
            [low_liq, ok], [], [],
        ])
        result = self.orch._discover_markets({})
        ids = [m.get("id") for m in result["markets"]]
        assert "liq" not in ids
        assert "ok" in ids

    def test_long_term_uses_higher_threshold(self):
        """Long-term market with volume=500 (below 1000 floor) is rejected."""
        low_long = _market("low_long", days=90, volume=500, liquidity=200)
        ok_long  = _market("ok_long",  days=90, volume=2000, liquidity=600)

        self.orch.client.get_markets = MagicMock(side_effect=[
            [], [], [low_long, ok_long],
        ])
        result = self.orch._discover_markets({})
        ids = [m.get("id") for m in result["markets"]]
        assert "low_long" not in ids
        assert "ok_long" in ids


# ──────────────────────────────────────────────
# Deduplication
# ──────────────────────────────────────────────

class TestDeduplication:

    def setup_method(self):
        self.orch = make_orchestrator()

    def test_deduplication_across_pools(self):
        """A market appearing in two pools is only counted once."""
        m = _market("dup", days=14, volume=500, liquidity=200)

        self.orch.client.get_markets = MagicMock(side_effect=[
            [m],   # short returns it
            [m],   # medium also returns same id
            [],
        ])
        result = self.orch._discover_markets({})
        ids = [m_.get("id") for m_ in result["markets"]]
        assert ids.count("dup") == 1


# ──────────────────────────────────────────────
# Sports keyword filter still fires
# ──────────────────────────────────────────────

class TestKeywordFilter:

    def setup_method(self):
        self.orch = make_orchestrator()

    def test_sports_keyword_still_skipped(self):
        """nba keyword in question is still rejected after tiering changes."""
        sports = _market("nba_game", days=7, volume=5000, liquidity=1000,
                         question="Will the NBA finals end by June?")
        ok = _market("ok", days=7, volume=5000, liquidity=1000,
                     question="Will unemployment rate fall below 4% by April?")

        self.orch.client.get_markets = MagicMock(side_effect=[
            [sports, ok], [], [],
        ])
        result = self.orch._discover_markets({})
        ids = [m.get("id") for m in result["markets"]]
        assert "nba_game" not in ids
        assert "ok" in ids


# ──────────────────────────────────────────────
# Per-tier intra-cycle exposure caps
# ──────────────────────────────────────────────

class TestTierCaps:
    """Unit tests for per-tier cycle exposure caps in _evaluate_risks()."""

    def setup_method(self):
        self.orch = make_orchestrator()
        # Replace portfolio with a MagicMock so we can set read-only properties
        portfolio = MagicMock()
        portfolio.total_value    = 100.0
        portfolio.total_exposure = 0.0
        portfolio.drawdown_pct   = 0.0
        portfolio.num_positions  = 0
        portfolio.positions      = {}
        self.orch.portfolio = portfolio

    def _make_state(self, signals, markets):
        return {
            "signals":  signals,
            "markets":  markets,
            "regime":   "neutral",
        }

    def test_tier_cap_short_term_blocked(self):
        """Second short-term trade is blocked when cap (20% × $100 = $20) is exhausted."""
        short_mkt1 = _market("s1", days=7)
        short_mkt2 = _market("s2", days=7)

        sig1 = _signal("s1", question="Will A?")
        sig2 = _signal("s2", question="Will B?")

        # Risk manager approves both with $15 each → first OK, second would be $30 total > $20
        self.orch.risk.evaluate_trade = MagicMock(
            return_value=_approved_decision(size=15.0)
        )
        self.orch.risk.get_trading_mode = MagicMock(return_value="NORMAL")

        with patch("src.agent.orchestrator.get_open_position_themes",
                   return_value={}):
            result = self.orch._evaluate_risks(
                self._make_state([sig1, sig2], [short_mkt1, short_mkt2])
            )

        # Only the first trade passes the cap
        assert len(result["approved_trades"]) == 1
        assert result["approved_trades"][0][0].market_id == "s1"

    def test_tier_cap_medium_term_blocked(self):
        """Medium-term trade blocked when 40% cap ($40) exhausted."""
        med_mkt1 = _market("m1", days=30)
        med_mkt2 = _market("m2", days=30)

        sig1 = _signal("m1", question="Will C?")
        sig2 = _signal("m2", question="Will D?")

        # $30 each: first OK, second $60 total > $40
        self.orch.risk.evaluate_trade = MagicMock(
            return_value=_approved_decision(size=30.0)
        )
        self.orch.risk.get_trading_mode = MagicMock(return_value="NORMAL")

        with patch("src.agent.orchestrator.get_open_position_themes",
                   return_value={}):
            result = self.orch._evaluate_risks(
                self._make_state([sig1, sig2], [med_mkt1, med_mkt2])
            )

        assert len(result["approved_trades"]) == 1
        assert result["approved_trades"][0][0].market_id == "m1"

    def test_tier_cap_long_term_no_extra_cap(self):
        """Long-term trades are not subject to the short/medium tier caps."""
        long_mkt1 = _market("l1", days=90)
        long_mkt2 = _market("l2", days=90)

        sig1 = _signal("l1", question="Will E?")
        sig2 = _signal("l2", question="Will F?")

        # $15 each; short/medium accumulators stay at 0 so no tier cap fires
        # (global exposure cap: $30 < 60% of $100 = $60 → both pass)
        self.orch.risk.evaluate_trade = MagicMock(
            return_value=_approved_decision(size=15.0)
        )
        self.orch.risk.get_trading_mode = MagicMock(return_value="NORMAL")

        with patch("src.agent.orchestrator.get_open_position_themes",
                   return_value={}):
            result = self.orch._evaluate_risks(
                self._make_state([sig1, sig2], [long_mkt1, long_mkt2])
            )

        # Both long-term trades should pass (no tier cap for long)
        assert len(result["approved_trades"]) == 2
