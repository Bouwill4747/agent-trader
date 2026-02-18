"""
Performance report CLI tool.
Reads the SQLite database and prints a formatted summary of the agent's trading performance.

Usage: python report.py
"""

import asyncio
import json
from collections import defaultdict
from datetime import datetime

from config.settings import PAPER_TRADING, INITIAL_BANKROLL
from src.utils.db import get_latest_snapshot, get_first_snapshot, get_all_trades


def truncate(text: str, length: int = 32) -> str:
    """Shorten text to fit columns, adding '...' if truncated."""
    return text[:length - 3] + "..." if len(text) > length else text


def format_dollar(amount: float) -> str:
    """Format as dollar amount with sign for non-zero values."""
    if amount >= 0:
        return f"+${amount:.2f}"
    return f"-${abs(amount):.2f}"


def compute_trade_stats(trades: list) -> dict:
    """Match BUYs with SELLs per market to compute completed-trade PnL."""
    buys_by_market = defaultdict(list)
    completed = []

    for t in trades:
        if t["side"] == "BUY":
            buys_by_market[t["market_id"]].append(t)
        elif t["side"] == "SELL" and buys_by_market[t["market_id"]]:
            buy = buys_by_market[t["market_id"]].pop(0)
            pnl = (t["price"] - buy["price"]) * t["size"]
            completed.append(pnl)

    if not completed:
        return None

    wins = [p for p in completed if p > 0]
    losses = [p for p in completed if p <= 0]

    return {
        "total": len(completed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(completed) * 100,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "best": max(completed),
        "worst": min(completed),
    }


def print_report(snapshot: dict | None, first_snapshot: dict | None, trades: list):
    """Print the formatted performance report."""

    mode = "PAPER TRADING" if PAPER_TRADING else "LIVE TRADING"
    since = "N/A"
    starting_bankroll = INITIAL_BANKROLL

    if first_snapshot:
        since = first_snapshot["timestamp"][:10]
        starting_bankroll = first_snapshot["bankroll"]

    print()
    print("\u2550" * 50)
    print(f"  Polymarket Agent \u2014 Performance Report")
    print(f"  Mode: {mode} | Since: {since}")
    print("\u2550" * 50)

    # --- Portfolio ---
    if snapshot:
        current_value = snapshot["bankroll"] + snapshot.get("total_exposure", 0)
        total_return = (current_value - starting_bankroll) / starting_bankroll * 100
        realized = snapshot.get("realized_pnl", 0.0)
        unrealized = snapshot.get("unrealized_pnl", 0.0)
        peak = snapshot.get("peak_bankroll", starting_bankroll)
        drawdown = snapshot.get("drawdown_pct", 0.0)

        print()
        print("  Portfolio")
        print("  " + "\u2500" * 35)
        print(f"  Starting Bankroll:   ${starting_bankroll:.2f}")
        print(f"  Current Value:       ${current_value:.2f}")
        print(f"  Cash:                ${snapshot['bankroll']:.2f}")
        print(f"  Total Return:        {total_return:+.2f}%")
        print(f"  Realized PnL:        {format_dollar(realized)}")
        print(f"  Unrealized PnL:      {format_dollar(unrealized)}")
        print(f"  Peak Value:          ${peak:.2f}")
        print(f"  Max Drawdown:        {drawdown:.1f}%")
    else:
        print()
        print("  Portfolio")
        print("  " + "\u2500" * 35)
        print(f"  Starting Bankroll:   ${starting_bankroll:.2f}")
        print("  No snapshots yet — run the agent first.")

    # --- Trade Statistics ---
    print()
    print("  Trade Statistics")
    print("  " + "\u2500" * 35)

    if not trades:
        print("  No trades yet.")
    else:
        stats = compute_trade_stats(trades)
        print(f"  Total Trades:        {len(trades)}")
        if stats:
            print(f"  Wins / Losses:       {stats['wins']} / {stats['losses']}")
            print(f"  Win Rate:            {stats['win_rate']:.1f}%")
            print(f"  Avg Win:             {format_dollar(stats['avg_win'])}")
            print(f"  Avg Loss:            {format_dollar(stats['avg_loss'])}")
            print(f"  Best Trade:          {format_dollar(stats['best'])}")
            print(f"  Worst Trade:         {format_dollar(stats['worst'])}")
        else:
            print("  No completed trades yet (no SELL orders).")

    # --- Open Positions ---
    positions = []
    if snapshot and snapshot.get("positions_json"):
        try:
            raw = json.loads(snapshot["positions_json"])
            # Positions may be a dict keyed by market_id or a list
            if isinstance(raw, dict):
                positions = list(raw.values())
            else:
                positions = raw
        except (json.JSONDecodeError, TypeError):
            pass

    print()
    if positions:
        print(f"  Open Positions ({len(positions)})")
        print("  " + "\u2500" * 35)
        for pos in positions:
            question = truncate(pos.get("question", pos.get("market_id", "?")), 32)
            side = pos.get("side", "?")
            shares = pos.get("shares", pos.get("size", 0))
            entry = pos.get("avg_price", pos.get("entry_price", 0))
            current = pos.get("current_price", entry)
            pnl = (current - entry) * shares
            print(f"  {question:<32} | {side:<3} | {shares:g} @ ${entry:.3f} | Now: ${current:.3f} | PnL: {format_dollar(pnl)}")
    else:
        print("  Open Positions (0)")
        print("  " + "\u2500" * 35)
        print("  No open positions.")

    # --- Recent Trades ---
    print()
    recent = trades[:10]  # trades come newest-first from get_all_trades reversed, but we sorted ASC
    # Show the 10 most recent trades (last 10 from the ASC-sorted list)
    recent = trades[-10:] if len(trades) > 10 else trades
    recent.reverse()  # newest first for display

    if recent:
        print(f"  Recent Trades (last {len(recent)})")
        print("  " + "\u2500" * 35)
        for t in recent:
            ts = t["timestamp"][:16] if t.get("timestamp") else "?"
            side = t.get("side", "?")
            size = t.get("size", 0)
            price = t.get("price", 0)
            question = truncate(t.get("question", "?"), 30)
            print(f"  {ts}  {side:<4} {size:>3g} @ ${price:.3f}  {question}")
    else:
        print("  Recent Trades")
        print("  " + "\u2500" * 35)
        print("  No trades yet.")

    print()


async def main():
    snapshot = await get_latest_snapshot()
    first_snapshot = await get_first_snapshot()
    trades = await get_all_trades()
    print_report(snapshot, first_snapshot, trades)


if __name__ == "__main__":
    asyncio.run(main())
