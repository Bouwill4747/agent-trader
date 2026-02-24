"""
Tests for trade reasoning journal and calibration tracking.

Verifies:
- insert_trade() stores reasoning, estimated_prob, confidence, edge correctly
- update_trade_outcome() finds the right BUY trade and sets actual_outcome
- update_trade_outcome() does not overwrite already-resolved trades
- get_calibration_stats() returns correct win rates by confidence level
- init_db() migration is idempotent (safe to run on an existing DB)
"""

import asyncio
import os
import tempfile
import pytest

import aiosqlite

# Patch DATABASE_PATH before importing db module
_tmp = tempfile.mkstemp(suffix=".db")[1]
os.environ["DATABASE_PATH_OVERRIDE"] = _tmp

# We need to patch the setting before import
import importlib
import config.settings as _settings
_settings.DATABASE_PATH = _tmp

from src.utils.db import (
    init_db,
    insert_trade,
    update_trade_outcome,
    get_calibration_stats,
)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def make_buy_trade(market_id="m1", estimated_prob=0.70, confidence="high",
                   reasoning="Test reasoning", edge=0.20):
    """Return a minimal BUY trade dict with reasoning fields."""
    from datetime import datetime, timezone
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "market_id": market_id,
        "token_id": "t1",
        "question": "Test market?",
        "side": "BUY",
        "price": 0.50,
        "size": 10,
        "total_cost": 5.0,
        "paper_trade": 1,
        "estimated_prob": estimated_prob,
        "confidence": confidence,
        "claude_reasoning": reasoning,
        "edge": edge,
    }


def make_sell_trade(market_id="m1"):
    """Return a minimal SELL trade dict."""
    from datetime import datetime, timezone
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "market_id": market_id,
        "token_id": "t1",
        "question": "Test market?",
        "side": "SELL",
        "price": 0.70,
        "size": 10,
        "total_cost": 7.0,
        "paper_trade": 1,
    }


@pytest.fixture(autouse=True)
def fresh_db():
    """Reset the test database before each test."""
    # Remove existing DB and reinitialise
    if os.path.exists(_tmp):
        os.remove(_tmp)
    run(init_db())
    yield
    if os.path.exists(_tmp):
        os.remove(_tmp)


# ──────────────────────────────────────────────
# insert_trade — reasoning fields stored
# ──────────────────────────────────────────────

class TestInsertTradeReasoning:

    def test_reasoning_stored(self):
        """BUY trade with reasoning should persist all new fields."""
        trade = make_buy_trade(
            estimated_prob=0.75,
            confidence="high",
            reasoning="BTC momentum is strong, Metaculus says 70%.",
            edge=0.25,
        )
        run(insert_trade(trade))

        async def fetch():
            async with aiosqlite.connect(_tmp) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT * FROM trades WHERE side='BUY'")
                return dict(await cur.fetchone())

        row = run(fetch())
        assert row["claude_reasoning"] == "BTC momentum is strong, Metaculus says 70%."
        assert abs(row["estimated_prob"] - 0.75) < 0.001
        assert row["confidence"] == "high"
        assert abs(row["edge"] - 0.25) < 0.001
        assert row["actual_outcome"] is None

    def test_sell_trade_no_reasoning(self):
        """SELL trade without reasoning fields stores NULLs cleanly."""
        run(insert_trade(make_sell_trade()))

        async def fetch():
            async with aiosqlite.connect(_tmp) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT * FROM trades WHERE side='SELL'")
                return dict(await cur.fetchone())

        row = run(fetch())
        assert row["claude_reasoning"] is None
        assert row["estimated_prob"] is None
        assert row["confidence"] is None

    def test_reasoning_none_when_omitted(self):
        """Trade dict without reasoning keys stores NULL gracefully."""
        from datetime import datetime, timezone
        trade = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "market_id": "m2",
            "token_id": "t2",
            "side": "BUY",
            "price": 0.40,
            "size": 5,
            "total_cost": 2.0,
            "paper_trade": 1,
        }
        run(insert_trade(trade))  # Should not raise


# ──────────────────────────────────────────────
# update_trade_outcome
# ──────────────────────────────────────────────

class TestUpdateTradeOutcome:

    def test_outcome_win_recorded(self):
        """TAKE_PROFIT outcome sets actual_outcome=1."""
        run(insert_trade(make_buy_trade(market_id="m1")))
        run(update_trade_outcome("m1", outcome=1, exit_reason="TAKE_PROFIT"))

        async def fetch():
            async with aiosqlite.connect(_tmp) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT actual_outcome, exit_reason FROM trades WHERE side='BUY'"
                )
                return dict(await cur.fetchone())

        row = run(fetch())
        assert row["actual_outcome"] == 1
        assert row["exit_reason"] == "TAKE_PROFIT"

    def test_outcome_loss_recorded(self):
        """STOP_LOSS outcome sets actual_outcome=0."""
        run(insert_trade(make_buy_trade(market_id="m2")))
        run(update_trade_outcome("m2", outcome=0, exit_reason="STOP_LOSS"))

        async def fetch():
            async with aiosqlite.connect(_tmp) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT actual_outcome, exit_reason FROM trades WHERE side='BUY'"
                )
                return dict(await cur.fetchone())

        row = run(fetch())
        assert row["actual_outcome"] == 0
        assert row["exit_reason"] == "STOP_LOSS"

    def test_resolved_yes_recorded(self):
        """RESOLVED_YES sets actual_outcome=1."""
        run(insert_trade(make_buy_trade(market_id="m3")))
        run(update_trade_outcome("m3", outcome=1, exit_reason="RESOLVED_YES"))

        async def fetch():
            async with aiosqlite.connect(_tmp) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT actual_outcome FROM trades WHERE side='BUY'")
                return dict(await cur.fetchone())

        row = run(fetch())
        assert row["actual_outcome"] == 1

    def test_does_not_overwrite_resolved_trade(self):
        """Calling update_trade_outcome twice on same market should not change first outcome."""
        run(insert_trade(make_buy_trade(market_id="m4")))
        run(update_trade_outcome("m4", outcome=1, exit_reason="TAKE_PROFIT"))
        # Second call — already resolved, should be a no-op (WHERE actual_outcome IS NULL)
        run(update_trade_outcome("m4", outcome=0, exit_reason="STOP_LOSS"))

        async def fetch():
            async with aiosqlite.connect(_tmp) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT actual_outcome FROM trades WHERE side='BUY'")
                return dict(await cur.fetchone())

        row = run(fetch())
        assert row["actual_outcome"] == 1  # First outcome preserved

    def test_targets_correct_market(self):
        """update_trade_outcome only updates the matching market_id."""
        run(insert_trade(make_buy_trade(market_id="m5")))
        run(insert_trade(make_buy_trade(market_id="m6")))
        run(update_trade_outcome("m5", outcome=1, exit_reason="TAKE_PROFIT"))

        async def fetch():
            async with aiosqlite.connect(_tmp) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT market_id, actual_outcome FROM trades WHERE side='BUY' ORDER BY id"
                )
                return [dict(r) for r in await cur.fetchall()]

        rows = run(fetch())
        assert rows[0]["market_id"] == "m5"
        assert rows[0]["actual_outcome"] == 1
        assert rows[1]["market_id"] == "m6"
        assert rows[1]["actual_outcome"] is None  # Untouched


# ──────────────────────────────────────────────
# get_calibration_stats
# ──────────────────────────────────────────────

class TestCalibrationStats:

    def _insert_resolved(self, market_id, confidence, estimated_prob, won):
        trade = make_buy_trade(
            market_id=market_id,
            confidence=confidence,
            estimated_prob=estimated_prob,
        )
        run(insert_trade(trade))
        run(update_trade_outcome(
            market_id,
            outcome=1 if won else 0,
            exit_reason="TAKE_PROFIT" if won else "STOP_LOSS",
        ))

    def test_empty_returns_no_rows(self):
        """No resolved trades → empty list."""
        stats = run(get_calibration_stats())
        assert stats == []

    def test_win_rate_correct(self):
        """3 wins out of 4 high-confidence trades → win_rate = 0.75."""
        self._insert_resolved("a1", "high", 0.80, won=True)
        self._insert_resolved("a2", "high", 0.75, won=True)
        self._insert_resolved("a3", "high", 0.70, won=True)
        self._insert_resolved("a4", "high", 0.65, won=False)

        stats = run(get_calibration_stats())
        assert len(stats) == 1
        row = stats[0]
        assert row["confidence"] == "high"
        assert row["total"] == 4
        assert row["wins"] == 3
        assert abs(row["win_rate"] - 0.75) < 0.01

    def test_multiple_confidence_levels(self):
        """Stats grouped correctly by confidence level."""
        self._insert_resolved("b1", "high",   0.80, won=True)
        self._insert_resolved("b2", "medium", 0.60, won=True)
        self._insert_resolved("b3", "medium", 0.55, won=False)
        self._insert_resolved("b4", "low",    0.40, won=False)

        stats = run(get_calibration_stats())
        by_conf = {r["confidence"]: r for r in stats}

        assert by_conf["high"]["total"] == 1
        assert by_conf["high"]["wins"] == 1
        assert by_conf["medium"]["total"] == 2
        assert by_conf["medium"]["wins"] == 1
        assert by_conf["low"]["total"] == 1
        assert by_conf["low"]["wins"] == 0

    def test_unresolved_trades_excluded(self):
        """Open trades (actual_outcome IS NULL) are excluded from calibration."""
        run(insert_trade(make_buy_trade(market_id="open1", confidence="high")))
        # No update_trade_outcome call → still NULL
        stats = run(get_calibration_stats())
        assert stats == []


# ──────────────────────────────────────────────
# Migration idempotency
# ──────────────────────────────────────────────

class TestMigration:

    def test_init_db_twice_is_safe(self):
        """Running init_db() twice does not raise — ALTER TABLE is idempotent."""
        run(init_db())  # Second call (first was in autouse fixture)
        # If we get here without exception, migration is safe
