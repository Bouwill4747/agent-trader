"""
Reset the paper trading portfolio back to a clean state.

Clears all trades, snapshots, agent runs, and orders from the database.
The agent will restart at INITIAL_BANKROLL ($100) on next startup.

Usage:
    1. sudo systemctl stop agent-trader
    2. python reset_portfolio.py
    3. sudo systemctl start agent-trader
"""

import asyncio
import os
import shutil
from datetime import datetime

import aiosqlite

from config.settings import DATABASE_PATH


async def reset():
    if not os.path.exists(DATABASE_PATH):
        print(f"No database found at {DATABASE_PATH} — nothing to reset.")
        return

    # Backup first
    backup_path = DATABASE_PATH + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(DATABASE_PATH, backup_path)
    print(f"Backup saved to: {backup_path}")

    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Count existing records before wipe
        tables = ["trades", "portfolio_snapshots", "agent_runs", "orders"]
        for table in tables:
            try:
                cur = await db.execute(f"SELECT COUNT(*) FROM {table}")
                count = (await cur.fetchone())[0]
                print(f"  {table}: {count} rows")
            except Exception:
                pass

        print("\nClearing all tables...")
        for table in tables:
            try:
                await db.execute(f"DELETE FROM {table}")
                print(f"  Cleared: {table}")
            except Exception as e:
                print(f"  Skipped {table}: {e}")

        await db.commit()

    print(f"\nReset complete. Agent will start fresh at $100 on next startup.")
    print(f"Backup kept at: {backup_path}")


if __name__ == "__main__":
    asyncio.run(reset())
