"""
SQLite database for persisting trades, signals, and portfolio state.
Uses aiosqlite for async access — the agent can do other work while DB writes complete.
"""

import aiosqlite
from config.settings import DATABASE_PATH
from src.utils.logger import setup_logger

logger = setup_logger("database")


async def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""

    async with aiosqlite.connect(DATABASE_PATH) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                question TEXT,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                total_cost REAL NOT NULL,
                order_type TEXT DEFAULT 'GTC',
                status TEXT DEFAULT 'pending',
                paper_trade INTEGER DEFAULT 1
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market_id TEXT NOT NULL,
                question TEXT,
                current_price REAL,
                estimated_prob REAL,
                edge REAL,
                sentiment_score REAL,
                confidence TEXT,
                direction TEXT,
                reasoning TEXT,
                acted_on INTEGER DEFAULT 0
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                bankroll REAL NOT NULL,
                total_exposure REAL NOT NULL,
                open_positions INTEGER NOT NULL,
                unrealized_pnl REAL,
                realized_pnl REAL,
                peak_bankroll REAL,
                drawdown_pct REAL,
                positions_json TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                status TEXT DEFAULT 'running',
                markets_analyzed INTEGER DEFAULT 0,
                signals_generated INTEGER DEFAULT 0,
                trades_executed INTEGER DEFAULT 0,
                errors TEXT
            )
        """)

        await db.commit()

        # Migration: add columns to existing trades table.
        # SQLite has no ADD COLUMN IF NOT EXISTS — try each individually.
        new_columns = [
            ("claude_reasoning", "TEXT"),
            ("estimated_prob",   "REAL"),
            ("confidence",       "TEXT"),
            ("edge",             "REAL"),
            ("actual_outcome",   "INTEGER"),   # NULL=open, 1=won, 0=lost
            ("exit_reason",      "TEXT"),      # STOP_LOSS, TAKE_PROFIT, RESOLVED_*
            ("market_theme",            "TEXT"),     # crypto/macro/geopolitics/tech/politics/other
            ("resolution_type",         "TEXT"),     # mechanical_numeric/price_print/formal_recognition/subjective_event
            ("resolution_clarity_score","INTEGER"),  # 1 (vague) to 5 (precise)
        ]
        for col_name, col_type in new_columns:
            try:
                await db.execute(
                    f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}"
                )
                await db.commit()
            except Exception:
                pass  # Column already exists

        logger.info("Database initialized at %s", DATABASE_PATH)


async def insert_trade(trade: dict):
    """Record a trade in the database."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO trades
                (timestamp, market_id, token_id, question, side,
                 price, size, total_cost, order_type, status, paper_trade,
                 claude_reasoning, estimated_prob, confidence, edge,
                 market_theme, resolution_type, resolution_clarity_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade["timestamp"], trade["market_id"], trade["token_id"],
            trade.get("question", ""), trade["side"],
            trade["price"], trade["size"], trade["total_cost"],
            trade.get("order_type", "GTC"), trade.get("status", "pending"),
            trade.get("paper_trade", 1),
            trade.get("claude_reasoning"), trade.get("estimated_prob"),
            trade.get("confidence"), trade.get("edge"),
            trade.get("market_theme"), trade.get("resolution_type"),
            trade.get("resolution_clarity_score"),
        ))
        await db.commit()


async def update_trade_outcome(market_id: str, outcome: int, exit_reason: str):
    """Record the actual outcome on the original BUY trade for a market.

    Finds the most recent BUY trade for market_id where actual_outcome is
    still NULL (i.e. not yet resolved) and sets outcome + exit_reason.

    Args:
        market_id: The market condition ID.
        outcome: 1 = won (profitable), 0 = lost.
        exit_reason: "STOP_LOSS", "TAKE_PROFIT", "RESOLVED_YES", "RESOLVED_NO".
    """
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            UPDATE trades
            SET actual_outcome = ?, exit_reason = ?
            WHERE id = (
                SELECT id FROM trades
                WHERE market_id = ? AND side = 'BUY' AND actual_outcome IS NULL
                ORDER BY id DESC
                LIMIT 1
            )
        """, (outcome, exit_reason, market_id))
        await db.commit()


async def get_calibration_stats() -> list:
    """Return win-rate vs estimated-probability grouped by confidence level.

    Used in report.py to show whether Claude's confidence correlates with
    actual trade outcomes. A well-calibrated model should have:
      high   confidence → win rate close to avg_estimated_prob
      medium confidence → win rate close to avg_estimated_prob
      low    confidence → win rate close to avg_estimated_prob

    Returns:
        List of dicts with keys: confidence, total, wins, win_rate, avg_estimated_prob.
        Only includes rows where actual_outcome IS NOT NULL (resolved trades).
    """
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT
                confidence,
                COUNT(*) AS total,
                SUM(actual_outcome) AS wins,
                ROUND(AVG(actual_outcome), 3) AS win_rate,
                ROUND(AVG(estimated_prob), 3) AS avg_estimated_prob
            FROM trades
            WHERE side = 'BUY'
              AND actual_outcome IS NOT NULL
              AND confidence IS NOT NULL
            GROUP BY confidence
            ORDER BY
                CASE confidence
                    WHEN 'high'   THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low'    THEN 3
                    ELSE 4
                END
        """)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def insert_signal(signal: dict):
    """Record a trading signal in the database."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO signals
                (timestamp, market_id, question, current_price, estimated_prob,
                 edge, sentiment_score, confidence, direction, reasoning, acted_on)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal["timestamp"], signal["market_id"],
            signal.get("question", ""), signal["current_price"],
            signal["estimated_prob"], signal["edge"],
            signal.get("sentiment_score", 0.0), signal["confidence"],
            signal["direction"], signal.get("reasoning", ""),
            signal.get("acted_on", 0)
        ))
        await db.commit()


async def insert_portfolio_snapshot(snapshot: dict):
    """Record a portfolio snapshot (includes positions JSON for crash recovery)."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO portfolio_snapshots
                (timestamp, bankroll, total_exposure, open_positions,
                 unrealized_pnl, realized_pnl, peak_bankroll, drawdown_pct,
                 positions_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot["timestamp"], snapshot["bankroll"],
            snapshot["total_exposure"], snapshot["open_positions"],
            snapshot.get("unrealized_pnl", 0.0),
            snapshot.get("realized_pnl", 0.0),
            snapshot.get("peak_bankroll", snapshot["bankroll"]),
            snapshot.get("drawdown_pct", 0.0),
            snapshot.get("positions_json"),
        ))
        await db.commit()


async def get_latest_snapshot() -> dict | None:
    """Fetch the most recent portfolio snapshot for crash recovery."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_trade_history(limit: int = 50) -> list:
    """Fetch recent trades."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_all_trades() -> list:
    """Fetch ALL trades (oldest first). Used for win/loss statistics."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM trades ORDER BY id ASC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_first_snapshot() -> dict | None:
    """Fetch the earliest portfolio snapshot (start date and starting bankroll)."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY id ASC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def insert_agent_run(run: dict) -> int:
    """Record the start of an agent cycle. Returns the row ID."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO agent_runs (start_time, status) VALUES (?, ?)",
            (run["start_time"], run.get("status", "running")),
        )
        await db.commit()
        return cursor.lastrowid


async def update_agent_run(run_id: int, updates: dict):
    """Update an agent run with end-of-cycle stats."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            UPDATE agent_runs
            SET end_time = ?, status = ?, markets_analyzed = ?,
                signals_generated = ?, trades_executed = ?, errors = ?
            WHERE id = ?
        """, (
            updates.get("end_time"),
            updates.get("status", "completed"),
            updates.get("markets_analyzed", 0),
            updates.get("signals_generated", 0),
            updates.get("trades_executed", 0),
            updates.get("errors"),
            run_id,
        ))
        await db.commit()


async def get_agent_run_stats() -> dict:
    """Get summary statistics from all agent runs."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("""
            SELECT
                COUNT(*) as total_cycles,
                SUM(CASE WHEN status LIKE 'completed%' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                COALESCE(SUM(markets_analyzed), 0) as total_markets,
                COALESCE(SUM(signals_generated), 0) as total_signals,
                COALESCE(SUM(trades_executed), 0) as total_trades
            FROM agent_runs
        """)
        row = await cursor.fetchone()
        return {
            "total_cycles": row[0],
            "successful": row[1],
            "failed": row[2],
            "total_markets": row[3],
            "total_signals": row[4],
            "total_trades": row[5],
        }
