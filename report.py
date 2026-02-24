"""
Performance report CLI tool.
Reads the SQLite database and prints a formatted summary of the agent's trading performance.

Usage: python report.py
"""

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone

import httpx

from config.settings import PAPER_TRADING, INITIAL_BANKROLL, CLOB_API_URL
from src.utils.db import (
    get_latest_snapshot, get_first_snapshot, get_all_trades,
    get_agent_run_stats, get_calibration_stats,
)


def truncate(text: str, length: int = 32) -> str:
    """Shorten text to fit columns, adding '...' if truncated."""
    return text[:length - 3] + "..." if len(text) > length else text


def format_dollar(amount: float) -> str:
    """Format as dollar amount with sign for non-zero values."""
    if amount >= 0:
        return f"+${amount:.2f}"
    return f"-${abs(amount):.2f}"


def fetch_live_prices(positions: list) -> dict:
    """Fetch live midpoint prices from CLOB API for open positions.

    Returns dict mapping token_id -> midpoint price.
    Returns empty dict if CLOB is unreachable.
    """
    prices = {}
    try:
        with httpx.Client(timeout=10.0) as client:
            for pos in positions:
                token_id = pos.get("token_id")
                if not token_id:
                    continue
                try:
                    resp = client.get(
                        f"{CLOB_API_URL}/midpoint",
                        params={"token_id": token_id},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    mid = float(data.get("mid", 0)) if isinstance(data, dict) else float(data)
                    if mid > 0:
                        prices[token_id] = mid
                except (httpx.HTTPError, ValueError, TypeError, KeyError):
                    continue
    except (httpx.HTTPError, OSError):
        pass
    return prices


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


def print_calibration(stats: list):
    """Print Claude's calibration: estimated probability vs actual win rate."""
    print()
    print("  Calibration (Claude accuracy by confidence level)")
    print("  " + "\u2500" * 45)

    total_resolved = sum(r["total"] for r in stats)
    if total_resolved < 5:
        print(f"  Not enough data yet ({total_resolved} resolved trade(s)).")
        print("  Need at least 5 resolved trades to show calibration.")
        return

    header = f"  {'Confidence':<10} {'Trades':>7} {'Wins':>6} {'Win Rate':>10} {'Estimated':>10} {'Delta':>8}"
    print(header)
    print("  " + "\u2500" * 55)

    for row in stats:
        conf = row["confidence"] or "unknown"
        total = row["total"]
        wins = int(row["wins"] or 0)
        win_rate = (row["win_rate"] or 0) * 100
        avg_prob = (row["avg_estimated_prob"] or 0) * 100
        delta = win_rate - avg_prob  # negative = overconfident

        delta_str = f"{delta:+.1f}%"
        print(
            f"  {conf:<10} {total:>7} {wins:>6} {win_rate:>9.1f}% "
            f"{avg_prob:>9.1f}% {delta_str:>8}"
        )

    print()
    print("  Delta = Win Rate − Avg Estimated Prob")
    print("  Negative delta = overconfident (Claude estimated higher than it achieved)")
    print("  Need 20+ trades per level for statistical significance")


def print_report(
    snapshot: dict | None,
    first_snapshot: dict | None,
    trades: list,
    run_stats: dict | None,
    calibration: list | None = None,
):
    """Print the formatted performance report."""

    mode = "PAPER TRADING" if PAPER_TRADING else "LIVE TRADING"
    since = "N/A"
    starting_bankroll = INITIAL_BANKROLL

    if first_snapshot:
        since = first_snapshot["timestamp"][:10]
        starting_bankroll = first_snapshot["bankroll"]

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print()
    print("\u2550" * 50)
    print("  Polymarket Agent \u2014 Performance Report")
    print(f"  Generated: {generated}")
    print(f"  Mode: {mode} | Since: {since}")
    print("\u2550" * 50)

    # --- Portfolio ---
    # Parse positions early so we can update unrealized PnL with live prices
    positions = []
    if snapshot and snapshot.get("positions_json"):
        try:
            raw = json.loads(snapshot["positions_json"])
            if isinstance(raw, dict):
                positions = list(raw.values())
            else:
                positions = raw
        except (json.JSONDecodeError, TypeError):
            pass

    # Fetch live prices for open positions
    live_prices = fetch_live_prices(positions)
    has_live = bool(live_prices)
    if has_live:
        for pos in positions:
            token_id = pos.get("token_id")
            if token_id and token_id in live_prices:
                pos["current_price"] = live_prices[token_id]

    if snapshot:
        # Recalculate unrealized PnL from live prices if available
        if has_live and positions:
            unrealized = sum(
                (pos.get("current_price", pos.get("avg_price", 0))
                 - pos.get("avg_price", 0))
                * pos.get("shares", pos.get("size", 0))
                for pos in positions
            )
            total_exposure = sum(
                pos.get("current_price", pos.get("avg_price", 0))
                * pos.get("shares", pos.get("size", 0))
                for pos in positions
            )
        else:
            unrealized = snapshot.get("unrealized_pnl", 0.0)
            total_exposure = snapshot.get("total_exposure", 0)

        # snapshot["bankroll"] is total_value (cash + positions), not just cash
        snapshot_total = snapshot["bankroll"]
        cash = snapshot_total - snapshot.get("total_exposure", 0)
        current_value = cash + total_exposure  # use live exposure if available
        total_return = (current_value - starting_bankroll) / starting_bankroll * 100
        realized = snapshot.get("realized_pnl", 0.0)
        peak = snapshot.get("peak_bankroll", starting_bankroll)
        if current_value > peak:
            peak = current_value  # fix stale peak from snapshot
        drawdown = (peak - current_value) / peak * 100 if peak > 0 else 0.0

        print()
        print("  Portfolio")
        print("  " + "\u2500" * 35)
        print(f"  Starting Bankroll:   ${starting_bankroll:.2f}")
        print(f"  Current Value:       ${current_value:.2f}")
        print(f"  Cash:                ${cash:.2f}")
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
        print("  No snapshots yet \u2014 run the agent first.")

    # --- Agent Activity ---
    print()
    print("  Agent Activity")
    print("  " + "\u2500" * 35)
    if run_stats and run_stats["total_cycles"] > 0:
        print(f"  Total Cycles:        {run_stats['total_cycles']}")
        print(f"  Successful:          {run_stats['successful']}")
        print(f"  Failed:              {run_stats['failed']}")
        print(f"  Markets Analyzed:    {run_stats['total_markets']}")
        print(f"  Signals Generated:   {run_stats['total_signals']}")
        print(f"  Trades Executed:     {run_stats['total_trades']}")
    else:
        print("  No cycles recorded yet.")

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
    print()
    price_label = "live" if has_live else "snapshot"
    if positions:
        print(f"  Open Positions ({len(positions)}) [{price_label} prices]")
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

    # --- Calibration ---
    if calibration is not None:
        print_calibration(calibration)

    # --- Recent Trades ---
    print()
    # Show the 10 most recent trades (last 10 from the ASC-sorted list)
    recent = list(trades[-10:]) if len(trades) > 10 else list(trades)
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
    run_stats = await get_agent_run_stats()
    calibration = await get_calibration_stats()
    print_report(snapshot, first_snapshot, trades, run_stats, calibration)


if __name__ == "__main__":
    asyncio.run(main())
