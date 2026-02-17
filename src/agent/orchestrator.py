"""
Agent orchestrator — LangGraph state machine that runs the trading loop.

The loop:
  discover_markets → research_markets → generate_signals →
  evaluate_risks → execute_trades → monitor_positions → (repeat)

Runs every 30 minutes. State persists to SQLite between cycles.

All LangGraph nodes are synchronous for consistency. Async DB operations
are wrapped with asyncio.run() when called from within sync graph nodes.
The graph itself runs in a thread via asyncio.to_thread() to avoid
blocking the main async event loop.
"""

import asyncio
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import StateGraph, END

from config.settings import CYCLE_INTERVAL_SECONDS, KILL_SWITCH_PATH
from src.data.polymarket_client import PolymarketClient
from src.data.news_collector import NewsCollector
from src.data.sentiment_scraper import SentimentScraper
from src.analysis.signal_generator import SignalGenerator, TradingSignal
from src.trading.risk_manager import RiskManager
from src.trading.executor import Executor
from src.trading.portfolio import Portfolio
from src.utils.logger import setup_logger
from src.utils.db import init_db

logger = setup_logger("orchestrator")

MAX_CONSECUTIVE_ERRORS = 5


# ──────────────────────────────────────────────
# State definition — what flows between nodes
# ──────────────────────────────────────────────

class AgentState(TypedDict):
    """State that flows through the LangGraph pipeline.

    Each node reads from and writes to this shared state dict.
    """
    markets: list               # Discovered markets from Gamma API
    articles: dict              # {market_id: [articles]} from NewsAPI
    sentiment: dict             # {market_id: [posts]} from Reddit
    signals: list               # TradingSignal objects
    approved_trades: list       # Signals that passed risk checks
    execution_results: list     # OrderResult objects
    cycle_start: str            # ISO timestamp of cycle start
    errors: list                # Any errors encountered during the cycle


# ──────────────────────────────────────────────
# The orchestrator class
# ──────────────────────────────────────────────

class Orchestrator:
    """Runs the autonomous trading agent loop."""

    def __init__(self):
        # Initialize all components
        self.client = PolymarketClient()
        self.news = NewsCollector()
        self.sentiment = SentimentScraper()
        self.signals = SignalGenerator()
        self.risk = RiskManager()
        self.portfolio = Portfolio()
        self.executor = Executor(self.client, self.portfolio)

        # Error tracking for escalation (H-09)
        self._consecutive_errors = 0

        # Build the LangGraph workflow
        self.graph = self._build_graph()

        logger.info("Orchestrator initialized — all components ready")

    def _build_graph(self) -> StateGraph:
        """Construct the LangGraph state machine.

        Each node is a synchronous function that takes state and returns updated state.
        Edges define the order: discover → research → signals → risk → execute → monitor.
        """
        graph = StateGraph(AgentState)

        # Add nodes (each maps to a method below — all sync)
        graph.add_node("discover_markets", self._discover_markets)
        graph.add_node("research_markets", self._research_markets)
        graph.add_node("generate_signals", self._generate_signals)
        graph.add_node("evaluate_risks", self._evaluate_risks)
        graph.add_node("execute_trades", self._execute_trades)
        graph.add_node("monitor_positions", self._monitor_positions)

        # Define edges (the order of execution)
        graph.set_entry_point("discover_markets")
        graph.add_edge("discover_markets", "research_markets")
        graph.add_edge("research_markets", "generate_signals")
        graph.add_edge("generate_signals", "evaluate_risks")
        graph.add_edge("evaluate_risks", "execute_trades")
        graph.add_edge("execute_trades", "monitor_positions")
        graph.add_edge("monitor_positions", END)

        return graph.compile()

    # ──────────────────────────────────────────────
    # Pipeline nodes — each step in the loop (ALL SYNC)
    # ──────────────────────────────────────────────

    def _discover_markets(self, state: AgentState) -> dict:
        """Node 1: Find active, liquid markets on Polymarket."""
        logger.info("── Step 1: Discovering markets ──")

        try:
            markets = self.client.get_markets(limit=50, active=True)

            # Filter for tradeable markets with decent liquidity
            filtered = []
            for market in markets:
                try:
                    volume = float(market.get("volume", 0) or 0)
                    liquidity = float(market.get("liquidity", 0) or 0)
                except (ValueError, TypeError):
                    continue

                # Skip low-liquidity markets (hard to trade, wide spreads)
                if volume < 1000 or liquidity < 500:
                    continue

                filtered.append(market)

            # Take top 10 by volume
            filtered.sort(key=lambda m: float(m.get("volume", 0) or 0), reverse=True)
            filtered = filtered[:10]

            logger.info("Discovered %d tradeable markets (from %d total)", len(filtered), len(markets))
            return {"markets": filtered}

        except Exception as e:
            logger.error("Market discovery failed: %s", e)
            return {"markets": [], "errors": state.get("errors", []) + [str(e)]}

    def _research_markets(self, state: AgentState) -> dict:
        """Node 2: Collect news and sentiment data for discovered markets."""
        logger.info("── Step 2: Researching markets ──")

        markets = state.get("markets", [])
        if not markets:
            logger.warning("No markets to research")
            return {"articles": {}, "sentiment": {}}

        try:
            articles = self.news.get_articles_for_markets(markets)
            sentiment = self.sentiment.get_sentiment_for_markets(markets)

            total_articles = sum(len(v) for v in articles.values())
            total_posts = sum(len(v) for v in sentiment.values())

            logger.info(
                "Collected %d articles and %d Reddit posts for %d markets",
                total_articles, total_posts, len(markets)
            )

            return {"articles": articles, "sentiment": sentiment}

        except Exception as e:
            logger.error("Research failed: %s", e)
            return {
                "articles": {}, "sentiment": {},
                "errors": state.get("errors", []) + [str(e)]
            }

    def _generate_signals(self, state: AgentState) -> dict:
        """Node 3: Run FinBERT + Claude analysis, produce trading signals."""
        logger.info("── Step 3: Generating signals ──")

        markets = state.get("markets", [])
        articles = state.get("articles", {})
        sentiment = state.get("sentiment", {})

        if not markets:
            return {"signals": []}

        try:
            # generate_signals is async — run it in a new event loop
            signals = asyncio.run(self.signals.generate_signals(
                markets, articles, sentiment
            ))

            actionable = [s for s in signals if s.direction != "SKIP"]
            logger.info(
                "Generated %d signals (%d actionable)",
                len(signals), len(actionable)
            )

            return {"signals": signals}

        except Exception as e:
            logger.error("Signal generation failed: %s", e)
            return {"signals": [], "errors": state.get("errors", []) + [str(e)]}

    def _evaluate_risks(self, state: AgentState) -> dict:
        """Node 4: Run each signal through the risk manager."""
        logger.info("── Step 4: Evaluating risks ──")

        signals = state.get("signals", [])
        approved = []

        for signal in signals:
            if signal.direction == "SKIP":
                continue

            decision = self.risk.evaluate_trade(
                estimated_prob=signal.estimated_prob,
                market_price=signal.current_price,
                confidence=signal.confidence,
                bankroll=self.portfolio.total_value,
                current_exposure=self.portfolio.total_exposure,
                num_positions=self.portfolio.num_positions,
                current_drawdown=self.portfolio.drawdown_pct,
                direction=signal.direction,
            )

            if decision.approved:
                approved.append((signal, decision))
                logger.info(
                    "APPROVED: '%s' — $%.2f (%s)",
                    signal.question[:40], decision.position_size, signal.direction
                )
            else:
                logger.info(
                    "REJECTED: '%s' — %s",
                    signal.question[:40], decision.reason
                )

        logger.info("%d trades approved out of %d signals", len(approved), len(signals))
        return {"approved_trades": approved}

    def _execute_trades(self, state: AgentState) -> dict:
        """Node 5: Place approved orders.

        Checks the kill switch before EACH trade (not just at cycle start)
        to minimize the window between kill switch activation and trade halt.
        """
        logger.info("── Step 5: Executing trades ──")

        approved = state.get("approved_trades", [])
        results = []

        for signal, decision in approved:
            # Check kill switch before each individual trade (H-3)
            if self.risk.check_kill_switch():
                logger.warning("Kill switch detected mid-cycle — cancelling remaining trades")
                asyncio.run(self.executor.cancel_all())
                break

            side = "BUY"  # We always buy (YES or NO shares)

            # Use correct token and price based on direction
            if signal.direction == "BUY_NO":
                token_id = signal.no_token_id
                price = 1.0 - signal.current_price  # NO token price
            else:
                token_id = signal.token_id
                price = signal.current_price

            # execute_trade is async — run it in a new event loop
            result = asyncio.run(self.executor.execute_trade(
                market_id=signal.market_id,
                token_id=token_id,
                question=signal.question,
                side=side,
                price=price,
                direction=signal.direction,
                risk_decision=decision,
            ))
            results.append(result)

        successful = sum(1 for r in results if r.success)
        logger.info("%d orders placed successfully out of %d attempts", successful, len(results))

        return {"execution_results": results}

    def _monitor_positions(self, state: AgentState) -> dict:
        """Node 6: Check existing positions and update portfolio."""
        logger.info("── Step 6: Monitoring positions ──")

        # Update prices for all open positions
        prices = {}
        for market_id, pos in self.portfolio.positions.items():
            midpoint = self.client.get_midpoint(pos.token_id)
            if midpoint is not None:
                prices[market_id] = midpoint

        self.portfolio.update_prices(prices)

        # Save snapshot to database (async call)
        asyncio.run(self.portfolio.save_snapshot())

        # Log portfolio summary
        logger.info("\n%s", self.portfolio.summary())

        return {}

    # ──────────────────────────────────────────────
    # Main loop
    # ──────────────────────────────────────────────

    async def run_cycle(self):
        """Run one complete cycle of the trading loop."""

        cycle_start = datetime.now(timezone.utc).isoformat()
        logger.info("=" * 60)
        logger.info("CYCLE START: %s", cycle_start)
        logger.info("=" * 60)

        # Check kill switch
        if self.risk.check_kill_switch():
            logger.warning("Kill switch active — skipping cycle")
            return

        # Run the sync graph in a thread to avoid blocking the event loop
        initial_state = {
            "markets": [],
            "articles": {},
            "sentiment": {},
            "signals": [],
            "approved_trades": [],
            "execution_results": [],
            "cycle_start": cycle_start,
            "errors": [],
        }

        try:
            result = await asyncio.to_thread(self.graph.invoke, initial_state)
            errors = result.get("errors", [])
            if errors:
                logger.warning("Cycle completed with %d errors: %s", len(errors), errors)
            else:
                logger.info("Cycle completed successfully")

            # Reset error counter on successful cycle
            self._consecutive_errors = 0

        except Exception as e:
            self._consecutive_errors += 1
            logger.error(
                "Cycle failed (%d consecutive): %s",
                self._consecutive_errors, e
            )

            if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.critical(
                    "Too many consecutive failures (%d) — halting agent",
                    self._consecutive_errors
                )
                raise

        logger.info("=" * 60)
        logger.info("CYCLE END")
        logger.info("=" * 60)

    async def run(self):
        """Run the agent loop continuously."""

        logger.info("Agent starting — cycle interval: %d seconds", CYCLE_INTERVAL_SECONDS)

        # Initialize database once at startup (M-05)
        await init_db()

        # Try to restore portfolio from last snapshot (H-01)
        restored = await Portfolio.load_from_db()
        if restored:
            self.portfolio = restored
            self.executor.portfolio = restored
            logger.info("Portfolio restored from previous session")
        else:
            logger.info("No previous portfolio found — starting fresh")

        while True:
            # Check kill switch before each cycle
            if self.risk.check_kill_switch():
                logger.warning("Kill switch detected — cancelling orders and shutting down")
                await self.executor.cancel_all()
                break

            try:
                await self.run_cycle()
            except Exception:
                # run_cycle raises on too many consecutive failures
                logger.critical("Agent halted due to repeated failures")
                break

            logger.info("Next cycle in %d seconds...", CYCLE_INTERVAL_SECONDS)
            await asyncio.sleep(CYCLE_INTERVAL_SECONDS)

    def shutdown(self):
        """Clean up resources."""
        self.client.close()
        logger.info("Agent shut down cleanly")
