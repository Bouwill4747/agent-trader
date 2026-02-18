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
        logger.info("Database initialized at %s", DATABASE_PATH)


async def insert_trade(trade: dict):
    """Record a trade in the database."""

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO trades
                (timestamp, market_id, token_id, question, side,
                 price, size, total_cost, order_type, status, paper_trade)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade["timestamp"], trade["market_id"], trade["token_id"],
            trade.get("question", ""), trade["side"],
            trade["price"], trade["size"], trade["total_cost"],
            trade.get("order_type", "GTC"), trade.get("status", "pending"),
            trade.get("paper_trade", 1)
        ))
        await db.commit()


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
