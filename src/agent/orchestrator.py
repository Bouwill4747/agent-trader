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
import json
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from langgraph.graph import StateGraph, END

from config.settings import (
    CYCLE_INTERVAL_SECONDS, KILL_SWITCH_PATH,
    EXIT_STOP_LOSS_PCT, EXIT_RESOLVED_THRESHOLD,
    SKIP_MARKET_KEYWORDS, MAX_CONCURRENT_POSITIONS,
    PAPER_TRADING, MAX_POSITIONS_PER_THEME,
    SHORT_TERM_MAX_DAYS, MEDIUM_TERM_MAX_DAYS,
    SHORT_TERM_MIN_VOLUME, SHORT_TERM_MIN_LIQUIDITY,
    MEDIUM_TERM_MIN_VOLUME, MEDIUM_TERM_MIN_LIQUIDITY,
    LONG_TERM_MIN_VOLUME, LONG_TERM_MIN_LIQUIDITY,
    MIN_DAYS_TO_RESOLUTION,
    SHORT_TERM_MAX_CYCLE_EXPOSURE_PCT, MEDIUM_TERM_MAX_CYCLE_EXPOSURE_PCT,
    PRICE_SCAN_INTERVAL_SECONDS, SCANNER_HARD_LOSS_PCT, SCANNER_TAKE_PROFIT_CONFIRMATIONS,
)
from src.data.polymarket_client import PolymarketClient
from src.data.news_collector import NewsCollector
from src.data.rss_collector import RSSCollector
from src.data.stocktwits_collector import StocktwitsCollector
from src.data.coingecko_collector import CoinGeckoCollector
from src.data.fear_greed_collector import FearGreedCollector
from src.data.fred_collector import FREDCollector
from src.data.finnhub_collector import FinnhubCollector
from src.analysis.signal_generator import SignalGenerator, TradingSignal
from src.trading.risk_manager import RiskManager
from src.trading.executor import Executor
from src.trading.portfolio import Portfolio
from src.utils.logger import setup_logger
from src.utils.db import (
    init_db, insert_agent_run, update_agent_run, update_trade_outcome,
    get_pending_live_trades, get_pending_sell_trades, get_open_filled_trades,
    mark_trade_status, get_calibration_stats, get_trade_summary,
    get_open_position_themes, sync_insert_scanner_event, sync_prune_scanner_events,
)

logger = setup_logger("orchestrator")

MAX_CONSECUTIVE_ERRORS = 5


# ──────────────────────────────────────────────
# Market duration helper
# ──────────────────────────────────────────────

def _days_until_resolution(market: dict) -> int:
    """Return how many days until this market resolves.

    Uses end_date_iso first, falls back to end_date.
    Returns 999 if the date is missing or unparseable (treated as long-term).
    """
    date_str = (market.get("end_date_iso")
                or market.get("endDate")
                or market.get("end_date"))
    if not date_str:
        return 999
    try:
        date_str = str(date_str).replace("Z", "+00:00")
        if "T" in date_str:
            end = datetime.fromisoformat(date_str)
        else:
            end = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, (end - now).days)
    except (ValueError, TypeError):
        return 999


# ──────────────────────────────────────────────
# Market ID helper
# ──────────────────────────────────────────────

def _get_market_id(market: dict) -> str:
    """Extract the canonical market ID, falling back to condition_id."""
    return market.get("id") or market.get("condition_id", "")


def _classify_market_tier(days: int) -> str:
    """Classify a market into a resolution tier based on days to resolution.

    Returns:
        'short'  — resolves within SHORT_TERM_MAX_DAYS
        'medium' — resolves within MEDIUM_TERM_MAX_DAYS
        'long'   — resolves after MEDIUM_TERM_MAX_DAYS
    """
    if days <= SHORT_TERM_MAX_DAYS:
        return "short"
    elif days <= MEDIUM_TERM_MAX_DAYS:
        return "medium"
    return "long"


# ──────────────────────────────────────────────
# Deduplication helper
# ──────────────────────────────────────────────

def _deduplicate_articles(articles: list) -> list:
    """Remove duplicate articles from a list.

    An article is a duplicate if its URL exactly matches a previous one,
    or if its normalized title prefix (first 8 words, lowercased, no
    punctuation) matches a previous one.  When duplicates exist, the
    first occurrence (usually from the primary source) is kept.

    Args:
        articles: List of article dicts with optional 'title' and 'url' keys.

    Returns:
        De-duplicated list preserving original order.
    """
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    result = []

    for article in articles:
        url = article.get("url", "")
        title = article.get("title", "")

        # Normalize title: lowercase, strip punctuation, first 8 words
        norm = re.sub(r"[^\w\s]", "", title.lower())
        norm = " ".join(norm.split()[:8])

        if url and url in seen_urls:
            continue
        if norm and norm in seen_titles:
            continue

        if url:
            seen_urls.add(url)
        if norm:
            seen_titles.add(norm)

        result.append(article)

    return result


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
    regime: str                 # Market regime: extreme_fear/fear/neutral/greed/extreme_greed


# ──────────────────────────────────────────────
# The orchestrator class
# ──────────────────────────────────────────────

class Orchestrator:
    """Runs the autonomous trading agent loop."""

    def __init__(self):
        # Initialize all components
        self.client = PolymarketClient()
        self.news = NewsCollector()
        self.rss = RSSCollector()
        self.stocktwits = StocktwitsCollector()
        self.coingecko = CoinGeckoCollector()
        self.fear_greed = FearGreedCollector()
        self.fred = FREDCollector()
        self.finnhub = FinnhubCollector()
        self.signals = SignalGenerator()
        self.risk = RiskManager()
        self.portfolio = Portfolio()
        self.executor = Executor(self.client, self.portfolio)

        # Error tracking for escalation (H-09)
        self._consecutive_errors = 0

        # Price scanner state
        self._scanner_running = False
        self._take_profit_hits: dict[str, int] = defaultdict(int)
        self._last_prune_time: float = 0.0

        # Full wallet reconciliation — runs every 5 cycles
        self._cycle_count: int = 0
        self._wallet_reconcile_interval: int = 5

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
        """Node 1: Find active, liquid markets on Polymarket.

        Three separate API calls — one per resolution tier — so each pool is
        sorted by liquidity *within* its tier. A single liquidity-sorted call
        always returns large long-term markets; short/medium-term markets never
        appear unless we filter by end_date.
        """
        logger.info("── Step 1: Discovering markets ──")

        try:
            today = datetime.now(timezone.utc)
            fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
            short_boundary = today + timedelta(days=SHORT_TERM_MAX_DAYS)
            medium_start   = today + timedelta(days=SHORT_TERM_MAX_DAYS, seconds=1)  # no overlap at boundary
            medium_end     = today + timedelta(days=MEDIUM_TERM_MAX_DAYS)
            long_start     = today + timedelta(days=MEDIUM_TERM_MAX_DAYS + 1)        # explicitly >60d only

            short_candidates = self.client.get_markets(
                limit=100,                          # larger pool — short-tier universe is sparse
                end_date_min=fmt(today),
                end_date_max=fmt(short_boundary),
            )
            medium_candidates = self.client.get_markets(
                limit=50,
                end_date_min=fmt(medium_start),
                end_date_max=fmt(medium_end),
            )
            long_candidates = self.client.get_markets(
                limit=50,
                end_date_min=fmt(long_start),       # explicitly long-only (was unfiltered before)
            )

            # Deduplicate by market id across all three pools
            seen_ids: set[str] = set()
            all_candidates: list = []
            for m in short_candidates + medium_candidates + long_candidates:
                mid = _get_market_id(m)
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    all_candidates.append(m)

            # Filter for tradeable markets.
            # Store (market, days) tuples so _days_until_resolution is called once per
            # market here and not again during tier grouping below.
            filtered: list[tuple[dict, int]] = []
            skipped_category = 0
            for market in all_candidates:
                try:
                    volume = float(market.get("volume", 0) or 0)
                    liquidity = float(market.get("liquidity", 0) or 0)
                except (ValueError, TypeError):
                    continue

                days = _days_until_resolution(market)

                # Skip markets resolving too soon — illiquid near expiry, poor price discovery
                if days < MIN_DAYS_TO_RESOLUTION:
                    continue

                # Tier-appropriate thresholds — short-term markets have lower absolute volume
                tier = _classify_market_tier(days)
                if tier == "short":
                    min_vol, min_liq = SHORT_TERM_MIN_VOLUME, SHORT_TERM_MIN_LIQUIDITY
                elif tier == "medium":
                    min_vol, min_liq = MEDIUM_TERM_MIN_VOLUME, MEDIUM_TERM_MIN_LIQUIDITY
                else:
                    min_vol, min_liq = LONG_TERM_MIN_VOLUME, LONG_TERM_MIN_LIQUIDITY

                if volume < min_vol or liquidity < min_liq:
                    continue

                # Skip already-resolved markets (price at $0 or $1)
                prices_str = market.get("outcomePrices")
                if prices_str:
                    try:
                        prices = [float(p) for p in json.loads(prices_str)]
                        yes_price = prices[0] if prices else 0.5
                        if yes_price <= 0.02 or yes_price >= 0.98:
                            continue
                    except (json.JSONDecodeError, ValueError, IndexError):
                        pass

                # Skip sports match results and entertainment award markets
                question_lower = market.get("question", "").lower()
                if any(kw in question_lower for kw in SKIP_MARKET_KEYWORDS):
                    skipped_category += 1
                    continue

                filtered.append((market, days))

            # Tier markets by days until resolution — prioritize short-term.
            # Short-term markets resolve quickly, giving fast calibration feedback.
            #   Tier 1 (≤SHORT_TERM_MAX_DAYS):  weekly data releases, near-term events
            #   Tier 2 (15–MEDIUM_TERM_MAX_DAYS): monthly events, near-term macro
            #   Tier 3 (>MEDIUM_TERM_MAX_DAYS):  long-horizon markets (slowest feedback)
            # Days already cached in the tuple — no re-parsing needed.
            short_term  = [m for m, d in filtered if d <= SHORT_TERM_MAX_DAYS]
            medium_term = [m for m, d in filtered if SHORT_TERM_MAX_DAYS < d <= MEDIUM_TERM_MAX_DAYS]
            long_term   = [m for m, d in filtered if d > MEDIUM_TERM_MAX_DAYS]

            # Sort each tier by volume (higher volume = more liquid, better data)
            for tier in (short_term, medium_term, long_term):
                tier.sort(key=lambda m: float(m.get("volume", 0) or 0), reverse=True)

            # Fill 10 slots: short-term first, then medium, then long
            selected = (short_term + medium_term + long_term)[:10]

            logger.info(
                "Discovered %d markets (short≤14d: %d, medium: %d, long: %d) "
                "from %d candidates (%d short, %d medium, %d long raw), "
                "%d skipped as sports/entertainment",
                len(selected), len(short_term), len(medium_term), len(long_term),
                len(all_candidates),
                len(short_candidates), len(medium_candidates), len(long_candidates),
                skipped_category,
            )
            return {"markets": selected}

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

        articles = {}
        errors = state.get("errors", [])

        # Fetch all sources independently — one failing shouldn't block the others
        try:
            articles = self.news.get_articles_for_markets(markets)
        except Exception as e:
            logger.error("News collection failed: %s", e)
            errors.append(str(e))

        try:
            rss_articles = self.rss.get_articles_for_markets(markets)
            # Merge RSS into articles dict (RSS supplements NewsAPI per market)
            for market_id, rss_list in rss_articles.items():
                existing = articles.get(market_id, [])
                articles[market_id] = existing + rss_list
        except Exception as e:
            logger.error("RSS collection failed: %s", e)
            errors.append(str(e))

        try:
            st_articles = self.stocktwits.get_sentiment_for_markets(markets)
            for market_id, st_list in st_articles.items():
                articles[market_id] = articles.get(market_id, []) + st_list
        except Exception as e:
            logger.error("Stocktwits collection failed: %s", e)
            errors.append(str(e))

        # Google Trends disabled — TooManyRequestsError on every call, wastes ~30s/cycle.
        # Re-enable with a proper rate-limit backoff if needed in future.

        try:
            cg_articles = self.coingecko.get_price_context_for_markets(markets)
            for market_id, cg_list in cg_articles.items():
                articles[market_id] = articles.get(market_id, []) + cg_list
        except Exception as e:
            logger.error("CoinGecko collection failed: %s", e)
            errors.append(str(e))

        # Metaculus disabled — returns 403/429 on every call (API access blocked).
        # Re-enable if credentials are added or access is restored.

        try:
            fg_articles = self.fear_greed.get_index_for_markets(markets)
            for market_id, fg_list in fg_articles.items():
                articles[market_id] = articles.get(market_id, []) + fg_list
        except Exception as e:
            logger.error("Fear & Greed collection failed: %s", e)
            errors.append(str(e))

        try:
            fred_articles = self.fred.get_macro_data_for_markets(markets)
            for market_id, fred_list in fred_articles.items():
                articles[market_id] = articles.get(market_id, []) + fred_list
        except Exception as e:
            logger.error("FRED collection failed: %s", e)
            errors.append(str(e))

        try:
            fh_news = self.finnhub.get_news_for_markets(markets)
            for market_id, fh_list in fh_news.items():
                articles[market_id] = articles.get(market_id, []) + fh_list
        except Exception as e:
            logger.error("Finnhub news collection failed: %s", e)
            errors.append(str(e))

        try:
            fh_quotes = self.finnhub.get_quotes_for_markets(markets)
            for market_id, fh_list in fh_quotes.items():
                articles[market_id] = articles.get(market_id, []) + fh_list
        except Exception as e:
            logger.error("Finnhub quotes collection failed: %s", e)
            errors.append(str(e))

        # Deduplicate articles per market — same story can arrive from multiple sources
        for market_id in articles:
            articles[market_id] = _deduplicate_articles(articles[market_id])

        # ── News-reaction gate: whale / informed-trading detection ──
        #
        # If a market shows unusual activity (sharp 1h price move OR high article
        # count), prepend a synthetic alert article so Claude factors it into
        # its probability estimate. This flags potential whale positioning or
        # breaking news that hasn't fully propagated yet.
        for market in markets:
            mid = market.get("id", market.get("condition_id", "unknown"))
            market_articles = articles.get(mid, [])
            article_count = len(market_articles)

            # oneHourPriceChange is a Gamma API extra field (float, may be absent)
            try:
                hour_change = float(market.get("oneHourPriceChange") or 0.0)
            except (ValueError, TypeError):
                hour_change = 0.0

            significant_move = abs(hour_change) >= 0.15
            high_coverage    = article_count >= 5

            if significant_move or high_coverage:
                signals = []
                if significant_move:
                    direction = "UP" if hour_change > 0 else "DOWN"
                    signals.append(f"1h price moved {hour_change:+.0%} ({direction})")
                if high_coverage:
                    signals.append(f"high article count ({article_count} sources)")

                note = (
                    f"MARKET ACTIVITY ALERT: {'; '.join(signals)}. "
                    f"This may indicate informed trading, whale positioning, or "
                    f"a major breaking development. "
                    f"Consider whether the market has already priced this in, "
                    f"or whether the move is an overreaction."
                )
                alert = {
                    "title": "Market Activity Alert: Unusual signals detected",
                    "source": "Market Monitor",
                    "description": note,
                    "content": note,
                    "url": "",
                    "published_at": "",
                }
                articles[mid] = [alert] + market_articles
                logger.info(
                    "Whale alert for '%s': %s",
                    market.get("question", mid)[:50], "; ".join(signals)
                )

        total_articles = sum(len(v) for v in articles.values())

        logger.info(
            "Collected %d articles after dedup (NewsAPI + RSS + Stocktwits + Trends + "
            "CoinGecko + Metaculus + Finnhub) for %d markets",
            total_articles, len(markets)
        )

        # Detect market regime from Fear & Greed index (already fetched above)
        regime = self._detect_regime()

        result = {"articles": articles, "sentiment": {}, "regime": regime}
        if errors:
            result["errors"] = errors
        return result

    def _detect_regime(self) -> str:
        """Detect current market regime from the Fear & Greed index.

        Returns one of: extreme_fear, fear, neutral, greed, extreme_greed.
        Falls back to 'neutral' if the index is unavailable.
        """
        value = self.fear_greed.get_current_value()
        if value is None:
            return "neutral"

        if value <= 20:
            regime = "extreme_fear"
        elif value <= 40:
            regime = "fear"
        elif value <= 59:
            regime = "neutral"
        elif value <= 79:
            regime = "greed"
        else:
            regime = "extreme_greed"

        logger.info(
            "Market regime: %s (Fear & Greed: %d/100)",
            regime.upper().replace("_", " "), value
        )
        return regime

    def _generate_signals(self, state: AgentState) -> dict:
        """Node 3: Run FinBERT + Claude analysis, produce trading signals."""
        logger.info("── Step 3: Generating signals ──")

        # Sync cash before the drawdown check so we use real wallet balance,
        # not the stale snapshot value loaded at startup. Without this, the
        # drawdown appears lower than reality and HALTED mode doesn't trigger.
        self._sync_cash_from_clob()

        # Skip signal generation in RISK_REDUCING_ONLY and HALTED modes.
        # Only _monitor_positions() (exits, stop-losses, reconciliation) should run.
        mode = self.risk.get_trading_mode(self.portfolio.drawdown_pct)
        if mode != "NORMAL":
            logger.warning(
                "Trading mode: %s (drawdown %.1f%%) — skipping signal generation",
                mode, self.portfolio.drawdown_pct * 100,
            )
            return {"signals": []}

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

            buy_yes = sum(1 for s in signals if s.direction == "BUY_YES")
            buy_no  = sum(1 for s in signals if s.direction == "BUY_NO")
            skipped = sum(1 for s in signals if s.direction == "SKIP")
            total_directional = buy_yes + buy_no
            no_ratio = (buy_no / total_directional * 100) if total_directional else 0
            logger.info(
                "Signals: BUY_YES=%d  BUY_NO=%d  SKIP=%d  (NO_ratio=%.0f%%)",
                buy_yes, buy_no, skipped, no_ratio,
            )

            return {"signals": signals}

        except Exception as e:
            logger.error("Signal generation failed: %s", e)
            return {"signals": [], "errors": state.get("errors", []) + [str(e)]}

    def _evaluate_risks(self, state: AgentState) -> dict:
        """Node 4: Run each signal through the risk manager."""
        logger.info("── Step 4: Evaluating risks ──")

        signals = state.get("signals", [])
        regime = state.get("regime", "neutral")
        approved = []
        # Track approvals within this batch so each subsequent check sees the
        # correct position count. Without this, two signals evaluated at 9/8
        # both pass, then both get executed, landing at 10 — one over the limit.
        approved_this_cycle = 0
        # Running exposure accumulator — starts at current portfolio exposure and
        # grows with each approval. Prevents intra-cycle overshoot of the 50% cap
        # (FIA standard: limits must include working orders + pending approvals).
        committed_exposure = self.portfolio.total_exposure
        committed_short_exposure  = 0.0   # short-term new entries this cycle
        committed_medium_exposure = 0.0   # medium-term new entries this cycle
        total_value = self.portfolio.total_value

        # Lookup table so we can classify each signal's tier without extra API calls
        market_by_id = {
            _get_market_id(m): m
            for m in state.get("markets", [])
        }

        # Build per-theme counts from currently open positions for correlation cap.
        # Filter by portfolio.positions to only count truly-tracked open positions.
        open_themes = asyncio.run(get_open_position_themes())
        theme_counts: dict[str, int] = {}
        for market_id, theme in open_themes.items():
            if market_id in self.portfolio.positions and theme:
                theme_counts[theme] = theme_counts.get(theme, 0) + 1
        # Track new approvals in this batch per theme
        approved_theme_counts: dict[str, int] = {}

        for signal in signals:
            if signal.direction == "SKIP":
                continue

            # ── Pre-filter: Theme correlation cap ──
            # Allow at most MAX_POSITIONS_PER_THEME open positions with the same theme.
            # "other" is a catch-all — not capped. Empty theme is not capped.
            signal_theme = signal.market_theme
            if signal_theme and signal_theme != "other":
                current_count = (
                    theme_counts.get(signal_theme, 0)
                    + approved_theme_counts.get(signal_theme, 0)
                )
                if current_count >= MAX_POSITIONS_PER_THEME:
                    logger.info(
                        "REJECTED: '%s' — theme '%s' correlation cap (%d/%d)",
                        signal.question[:40], signal_theme,
                        current_count, MAX_POSITIONS_PER_THEME,
                    )
                    continue

            existing = self.portfolio.positions.get(signal.market_id)
            market_exposure = existing.cost_basis if existing else 0.0

            decision = self.risk.evaluate_trade(
                estimated_prob=signal.estimated_prob,
                market_price=signal.current_price,
                confidence=signal.confidence,
                bankroll=self.portfolio.total_value,
                current_exposure=committed_exposure,
                num_positions=self.portfolio.num_positions + approved_this_cycle,
                current_drawdown=self.portfolio.drawdown_pct,
                direction=signal.direction,
                regime=regime,
                resolution_type=signal.resolution_type,
                resolution_clarity_score=signal.resolution_clarity_score,
                spread=signal.spread,
                market_exposure=market_exposure,
                liquidity=signal.liquidity,
                market_theme=signal.market_theme,
            )

            if decision.approved:
                # Per-tier intra-cycle exposure cap
                mkt = market_by_id.get(signal.market_id, {})
                tier = _classify_market_tier(_days_until_resolution(mkt))
                if tier == "short":
                    if committed_short_exposure + decision.position_size > total_value * SHORT_TERM_MAX_CYCLE_EXPOSURE_PCT:
                        logger.info(
                            "REJECTED (tier cap): '%s' — short-term cycle cap %.0f%% would be exceeded",
                            signal.question[:40], SHORT_TERM_MAX_CYCLE_EXPOSURE_PCT * 100,
                        )
                        continue
                elif tier == "medium":
                    if committed_medium_exposure + decision.position_size > total_value * MEDIUM_TERM_MAX_CYCLE_EXPOSURE_PCT:
                        logger.info(
                            "REJECTED (tier cap): '%s' — medium-term cycle cap %.0f%% would be exceeded",
                            signal.question[:40], MEDIUM_TERM_MAX_CYCLE_EXPOSURE_PCT * 100,
                        )
                        continue

                approved.append((signal, decision))
                approved_this_cycle += 1
                committed_exposure += decision.position_size
                if tier == "short":
                    committed_short_exposure += decision.position_size
                elif tier == "medium":
                    committed_medium_exposure += decision.position_size
                if signal_theme and signal_theme != "other":
                    approved_theme_counts[signal_theme] = (
                        approved_theme_counts.get(signal_theme, 0) + 1
                    )
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

        # Re-check mode here in case drawdown moved during this cycle's signal generation
        mode = self.risk.get_trading_mode(self.portfolio.drawdown_pct)
        if mode != "NORMAL":
            logger.warning("Trading mode: %s — skipping trade execution", mode)
            return {"execution_results": []}

        approved = state.get("approved_trades", [])
        results = []

        for signal, decision in approved:
            # Safety guard: re-check live position count before each trade.
            # Catches any edge case where the approved_this_cycle counter
            # didn't account for something (e.g. a position opened externally).
            if self.portfolio.num_positions >= MAX_CONCURRENT_POSITIONS:
                logger.warning("Position limit reached mid-execution — skipping remaining trades")
                break

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
                estimated_prob=signal.estimated_prob,
                confidence=signal.confidence,
                reasoning=signal.reasoning,
                edge=signal.edge,
                market_theme=signal.market_theme,
                resolution_type=signal.resolution_type,
                resolution_clarity_score=signal.resolution_clarity_score,
                current_price_yes=signal.current_price,
            ))
            results.append(result)

            # Save snapshot after each successful trade so positions survive crashes
            if result.success:
                asyncio.run(self.portfolio.save_snapshot())

        successful = sum(1 for r in results if r.success)
        logger.info("%d orders placed successfully out of %d attempts", successful, len(results))

        return {"execution_results": results}

    def _reconcile_gtc_orders(self) -> None:
        """Detect GTC orders that filled (fully or partially) and track them.

        GTC orders return status='live' when placed — executor correctly does NOT
        open a portfolio position at that point. But the order may fill later, silently,
        including partial fills where only some shares match.

        Each cycle we:
          1. Fetch all open CLOB orders (one call) to know which orders are still live.
          2. For each pending trade, check how many tokens we actually hold.
          3. If balance >= 1 and order still open  → partial fill: track current balance,
             keep DB status 'pending' so we re-check next cycle.
          4. If balance >= 1 and no open order     → fully filled: track final balance,
             mark DB status 'filled'.
          5. If balance < 1 and no open order      → order was cancelled with no fill:
             mark DB status 'cancelled'.

        We never mark 'filled' when we cannot determine open order state (API failure),
        to avoid stopping tracking of a still-live partial fill.
        """
        if self.executor.paper_mode:
            return

        pending = asyncio.run(get_pending_live_trades())
        if not pending:
            return

        # Fetch all open orders once — None means the API call failed.
        # We distinguish None (unknown) from [] (confirmed no open orders).
        open_orders = self.client.get_open_orders()
        open_orders_known = open_orders is not None
        open_token_ids: set[str] = set()
        # token_id → CLOB order_id, for targeted cancellation of stale orders
        open_order_id_by_token: dict[str, str] = {}
        if open_orders_known:
            for order in open_orders:
                # py-clob-client returns token_id as 'asset_id'
                tid = order.get("asset_id") or order.get("token_id", "")
                if tid:
                    open_token_ids.add(tid)
                    clob_id = order.get("id") or order.get("order_id", "")
                    if clob_id:
                        open_order_id_by_token[tid] = clob_id

        STALE_ORDER_DAYS = 3  # Cancel unfilled GTC buys older than this

        for trade in pending:
            market_id = trade["market_id"]
            token_id  = trade["token_id"]

            # Cancel GTC buy orders that have been open longer than STALE_ORDER_DAYS
            # without filling. Releases locked USDC collateral back to wallet.
            try:
                trade_age = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(trade["timestamp"])
                ).days
            except (TypeError, ValueError):
                trade_age = 0

            if trade_age >= STALE_ORDER_DAYS and open_orders_known:
                clob_order_id = open_order_id_by_token.get(token_id)
                if clob_order_id:
                    cancelled = self.client.cancel_order(clob_order_id)
                    if cancelled:
                        asyncio.run(mark_trade_status(trade["id"], "cancelled"))
                        logger.info(
                            "GTC STALE CANCELLED (%d days): '%s'",
                            trade_age, trade.get("question", "?")[:40],
                        )
                        continue
                elif token_id not in open_token_ids:
                    # Order already gone from CLOB and no tokens — mark cancelled
                    asyncio.run(mark_trade_status(trade["id"], "cancelled"))
                    logger.info(
                        "GTC STALE (already gone): '%s'", trade.get("question", "?")[:40]
                    )
                    continue

            # Check actual token balance on CLOB
            balance = self.client.get_token_balance(token_id)
            if balance is None:
                continue  # Can't determine — skip this cycle

            still_open = open_orders_known and token_id in open_token_ids

            # No tokens and no open order → cancelled with no fill
            if balance < 1 and open_orders_known and not still_open:
                asyncio.run(mark_trade_status(trade["id"], "cancelled"))
                logger.info(
                    "GTC CANCELLED (no fill): '%s'",
                    trade.get("question", "?")[:40],
                )
                continue

            if balance < 1:
                continue  # Still waiting to fill

            # We have tokens — determine position side
            position_side = trade.get("position_side")
            if not position_side:
                position_side = "NO" if trade["price"] < 0.50 else "YES"
                logger.warning(
                    "position_side missing for trade %d — inferred %s from entry price $%.3f",
                    trade["id"], position_side, trade["price"],
                )

            if market_id in self.portfolio.positions:
                # Already tracked — update share count if more shares filled since last cycle
                existing = self.portfolio.positions[market_id]
                if abs(existing.shares - balance) > 0.01:
                    logger.info(
                        "GTC partial fill update: '%s' %.2f → %.2f %s shares",
                        trade.get("question", "?")[:40], existing.shares, balance, position_side,
                    )
                    existing.shares = balance
            else:
                # Not yet tracked — restore into portfolio
                restored = self.portfolio.restore_position(
                    market_id=market_id,
                    token_id=token_id,
                    question=trade.get("question", ""),
                    side=position_side,
                    shares=balance,       # actual balance, not original order size
                    price=trade["price"], # original entry price for PnL tracking
                    estimated_prob=trade.get("estimated_prob") or 0.0,
                )
                if restored:
                    logger.info(
                        "GTC RECONCILED: '%s' — %.2f %s shares @ $%.3f now tracked",
                        trade.get("question", "?")[:40], balance, position_side, trade["price"],
                    )

            # Mark fully filled only when we're sure no open order remains.
            # If open_orders fetch failed, keep 'pending' — better to re-check
            # than to silently stop tracking a live partial fill.
            if open_orders_known and not still_open:
                asyncio.run(mark_trade_status(trade["id"], "filled"))

    def _reconcile_sell_orders(self) -> None:
        """Confirm GTC SELL order fills and close positions when tokens are gone.

        When execute_exit() places a live SELL order it calls portfolio.mark_selling()
        instead of close_position(), because the tokens are still held until the
        order fills. This method checks CLOB token balance each cycle:

          balance >= 1 → order still open (or partially filled) — keep waiting
          balance < 1  → tokens gone → order filled → close position, book PnL

        Skipped in paper mode (paper exits fill instantly in execute_exit).
        """
        if self.executor.paper_mode:
            return

        pending_sells = asyncio.run(get_pending_sell_trades())
        if not pending_sells:
            return

        # Fetch all open orders once so we can cancel stale SELL limits.
        open_orders = self.client.get_open_orders() or []
        open_order_by_token: dict[str, str] = {}
        for o in open_orders:
            tid = o.get("asset_id") or o.get("token_id", "")
            oid = o.get("id") or o.get("order_id", "")
            if tid and oid:
                open_order_by_token[tid] = oid

        for trade in pending_sells:
            market_id = trade["market_id"]
            token_id = trade["token_id"]

            balance = self.client.get_token_balance(token_id)
            if balance is None:
                continue  # CLOB API error — recheck next cycle

            if balance >= 1:
                # Partial fill detection: if CLOB balance < tracked shares, some
                # shares were sold. Correct downward so the next SELL order uses
                # the real remaining quantity.
                pos = self.portfolio.positions.get(market_id)
                if pos and pos.shares > balance + 0.5:
                    clob_shares = math.floor(balance)
                    logger.warning(
                        "PARTIAL FILL detected for '%s' — tracked=%d, CLOB balance=%.4f → correcting to %d shares",
                        pos.question[:50], int(pos.shares), balance, clob_shares,
                    )
                    with self.portfolio._lock:
                        current = self.portfolio.positions.get(market_id)
                        if current is not None:
                            current.shares = float(clob_shares)

                # Tokens still held — check if our GTC SELL limit is stale.
                # TAKE_PROFIT: cancel and re-place at midpoint (better price, patient).
                # STOP_LOSS:   cancel and re-place at bid (speed over price).
                # Trigger when limit is more than $0.05 above current midpoint.
                pos = self.portfolio.positions.get(market_id)
                if pos and pos.sell_price > 0:
                    midpoint = self.client.get_midpoint(token_id)
                    if midpoint is not None and pos.sell_price > midpoint + 0.05:
                        clob_order_id = open_order_by_token.get(token_id)
                        if clob_order_id:
                            cancelled = self.client.cancel_order(clob_order_id)
                            if cancelled:
                                sell_reason = pos.selling_reason or "TAKE_PROFIT"
                                logger.info(
                                    "SELL LIMIT STALE: cancelled order for '%s' "
                                    "(limit=$%.3f >> market=$%.3f) — re-firing at %s",
                                    pos.question[:40], pos.sell_price, midpoint,
                                    "bid" if sell_reason == "STOP_LOSS" else "midpoint",
                                )
                                # Release claim so the next hourly cycle or scanner
                                # re-triggers the exit at the appropriate price.
                                with self.portfolio._lock:
                                    current = self.portfolio.positions.get(market_id)
                                    if current is not None:
                                        current.selling_pending = False
                                        current.sell_price = 0.0
                                        current.selling_reason = ""
                                asyncio.run(mark_trade_status(trade["id"], "cancelled"))
                continue  # Tokens still held — order not yet fully filled

            # Tokens are gone: SELL filled. Close the position.
            pos = self.portfolio.positions.get(market_id)
            if pos and pos.selling_pending:
                close_price = pos.sell_price or trade["price"]
                sell_reason = pos.selling_reason or "STOP_LOSS"
                outcome = 1 if sell_reason == "TAKE_PROFIT" else 0
                self.portfolio.close_position(market_id, close_price)
                try:
                    asyncio.run(update_trade_outcome(
                        market_id, outcome=outcome, exit_reason=sell_reason
                    ))
                except Exception as e:
                    logger.warning("Failed to record outcome for %s: %s", market_id, e)
                asyncio.run(mark_trade_status(trade["id"], "filled"))
                logger.info(
                    "SELL RECONCILED: '%s' — %.0f shares @ $%.3f (%s)",
                    trade.get("question", "?")[:40], trade["size"],
                    close_price, sell_reason,
                )
            elif not pos:
                # Position already gone (e.g. resolved) — just mark the SELL trade filled
                asyncio.run(mark_trade_status(trade["id"], "filled"))

    def _close_orphaned_sells(self) -> None:
        """Close positions where the sell completed but the portfolio was not updated.

        Handles the edge case where a SELL DB record is already status='filled'
        (executor confirmed the order) but the portfolio position still has
        selling_pending=True — caused by a stale snapshot captured mid-close (BUG-028).

        Paper mode: sells complete instantly — any selling_pending is a stale snapshot.
        Live mode: check CLOB token balance and open orders before closing.
        """
        selling = [(mid, pos) for mid, pos in self.portfolio.positions.items()
                   if pos.selling_pending]
        if not selling:
            return

        if self.executor.paper_mode:
            # Paper sells complete instantly — any selling_pending is a stale snapshot.
            for market_id, pos in selling:
                close_price = pos.sell_price or pos.current_price
                sell_reason = pos.selling_reason or "STOP_LOSS"
                outcome = 1 if sell_reason == "TAKE_PROFIT" else 0
                self.portfolio.close_position(market_id, close_price)
                try:
                    asyncio.run(update_trade_outcome(
                        market_id, outcome=outcome, exit_reason=sell_reason
                    ))
                except Exception as e:
                    logger.warning("Failed to record outcome for %s: %s", market_id, e)
                logger.info(
                    "ORPHANED SELL CLOSED (paper): '%s' — %.0f shares @ $%.3f (%s)",
                    pos.question[:40], pos.shares, close_price, sell_reason,
                )
            return

        # Live mode: verify tokens are actually gone before closing
        open_orders = self.client.get_open_orders()
        open_token_ids: set[str] = set()
        if open_orders:
            for order in open_orders:
                tid = order.get("asset_id") or order.get("token_id", "")
                if tid:
                    open_token_ids.add(tid)

        for market_id, pos in selling:
            balance = self.client.get_token_balance(pos.token_id)
            if balance is None:
                continue  # API error — recheck next cycle
            if balance >= 1:
                # Tokens still held — check whether a SELL order is actually live.
                # If not, selling_pending is a ghost (order never placed or failed
                # silently before BUG-033 was fixed) — clear it so the exit check
                # can re-evaluate the position next cycle.
                if pos.token_id not in open_token_ids:
                    logger.warning(
                        "Ghost selling_pending detected for '%s' — tokens held but no "
                        "open SELL order on CLOB. Clearing flag so exit can re-fire.",
                        pos.question[:50],
                    )
                    with self.portfolio._lock:
                        current = self.portfolio.positions.get(market_id)
                        if current is not None:
                            current.selling_pending = False
                            current.sell_price = 0.0
                            current.selling_reason = ""
                continue  # Tokens still held — sell not yet filled
            if pos.token_id in open_token_ids:
                continue  # Open order still live — waiting for fill

            # Tokens gone, no open order → sell completed but portfolio not updated
            close_price = pos.sell_price or pos.current_price
            sell_reason = pos.selling_reason or "STOP_LOSS"
            outcome = 1 if sell_reason == "TAKE_PROFIT" else 0
            self.portfolio.close_position(market_id, close_price)
            try:
                asyncio.run(update_trade_outcome(
                    market_id, outcome=outcome, exit_reason=sell_reason
                ))
            except Exception as e:
                logger.warning("Failed to record outcome for %s: %s", market_id, e)
            logger.info(
                "ORPHANED SELL CLOSED: '%s' — %.0f shares @ $%.3f (%s)",
                pos.question[:40], pos.shares, close_price, sell_reason,
            )

    def _reconcile_missing_positions(self) -> None:
        """Restore filled BUY trades that are missing from the portfolio.

        Compares all filled, unresolved BUY trades in the DB against
        portfolio.positions. Any trade that's filled/open in the DB but
        absent from the portfolio may have been accidentally purged (BUG-025)
        or never added after a restart. Verifies CLOB balance before restoring.
        """
        if self.executor.paper_mode:
            return

        filled_trades = asyncio.run(get_open_filled_trades())
        if not filled_trades:
            return

        for trade in filled_trades:
            market_id = str(trade["market_id"])
            if market_id in self.portfolio.positions:
                continue  # Already tracked — nothing to do

            token_id = trade.get("token_id", "")
            balance = self.client.get_token_balance(token_id)
            if balance is None or balance < 1:
                continue  # API error or genuinely empty — don't restore phantom

            # Verify the market is still live by fetching its current midpoint.
            # A 404 / None return means the orderbook is gone (market resolved or
            # delisted) — don't restore a zombie position.
            midpoint = self.client.get_midpoint(token_id)
            if midpoint is None:
                logger.debug(
                    "Skipping restore for '%s' — no orderbook (market likely closed)",
                    trade.get("question", "?")[:45],
                )
                continue

            position_side = trade.get("position_side") or (
                "NO" if trade["price"] < 0.50 else "YES"
            )
            restored = self.portfolio.restore_position(
                market_id=market_id,
                token_id=token_id,
                question=trade.get("question", ""),
                side=position_side,
                shares=balance,
                price=trade["price"],
                estimated_prob=trade.get("estimated_prob") or 0.0,
            )
            if restored:
                # Set current_price immediately so _check_exit doesn't see None
                self.portfolio.positions[market_id].current_price = midpoint
                logger.info(
                    "MISSING POSITION RESTORED: '%s' — %.2f %s shares @ $%.3f (now $%.3f)",
                    trade.get("question", "?")[:45], balance, position_side,
                    trade["price"], midpoint,
                )

    def _reconcile_full_wallet(self) -> None:
        """Full wallet reconciliation — runs every N cycles.

        Fetches every token position held in the wallet from Polymarket's data
        API and compares against tracked portfolio positions. Fixes three classes
        of desync that the per-position reconcilers miss:

          1. Unknown tokens (position in wallet, not in portfolio) → add
          2. Balance lower than tracked (partial SELL fill)        → correct shares
          3. Zero-balance positions still tracked (zombie)         → purge

        This is expensive (one HTTP call + one Gamma lookup per unknown token)
        so it runs every _wallet_reconcile_interval cycles, not every cycle.
        """
        if self.executor.paper_mode:
            return

        wallet_address = self.client.clob.get_address()
        wallet_positions = self.client.get_wallet_positions(wallet_address)
        if wallet_positions is None:
            logger.warning("WALLET RECONCILE: data API unavailable — skipping")
            return

        # Build a map from token_id → wallet position
        wallet_by_token: dict[str, dict] = {p["token_id"]: p for p in wallet_positions}

        # --- Pass 1: check tracked positions against wallet ---
        for market_id, pos in list(self.portfolio.positions.items()):
            wallet_pos = wallet_by_token.get(pos.token_id)

            if wallet_pos is None or wallet_pos["shares"] < 0.5:
                # Wallet shows nothing — zombie position
                logger.warning(
                    "WALLET RECONCILE: '%s' has no wallet balance — purging zombie",
                    pos.question[:50],
                )
                with self.portfolio._lock:
                    self.portfolio.positions.pop(market_id, None)
                continue

            wallet_shares = wallet_pos["shares"]
            if pos.shares > wallet_shares + 0.5:
                # Tracked more than we actually hold (partial SELL fill)
                import math
                corrected = math.floor(wallet_shares)
                logger.warning(
                    "WALLET RECONCILE: '%s' tracked=%.2f wallet=%.2f — correcting to %d shares",
                    pos.question[:50], pos.shares, wallet_shares, corrected,
                )
                with self.portfolio._lock:
                    current = self.portfolio.positions.get(market_id)
                    if current is not None:
                        current.shares = float(corrected)

        # --- Pass 2: find wallet tokens not tracked at all ---
        tracked_tokens = {pos.token_id for pos in self.portfolio.positions.values()}
        for token_id, wp in wallet_by_token.items():
            if token_id in tracked_tokens:
                continue
            if wp["shares"] < 0.5:
                continue

            # Unknown token — look up market and add position
            question = wp["question"]
            market_id = wp["market_id"]
            side = wp["side"]
            avg_price = wp["avg_price"]
            current_price = wp["current_price"]

            logger.warning(
                "WALLET RECONCILE: untracked token found — '%s' %.2f %s shares @ $%.3f — adding",
                question[:50], wp["shares"], side, avg_price,
            )

            import math
            floored_shares = math.floor(wp["shares"])
            if floored_shares < 1:
                logger.info(
                    "WALLET RECONCILE: skipping '%s' — dust position (%.2f shares, < 1 whole share)",
                    question[:45], wp["shares"],
                )
                continue

            # Verify live midpoint before restoring (skip resolved markets)
            midpoint = self.client.get_midpoint(token_id)
            if midpoint is None:
                logger.warning(
                    "WALLET RECONCILE: skipping '%s' — no orderbook (market likely resolved)",
                    question[:45],
                )
                continue

            restored = self.portfolio.restore_position(
                market_id=market_id,
                token_id=token_id,
                question=question,
                side=side,
                shares=math.floor(wp["shares"]),
                price=avg_price,
                estimated_prob=0.0,
            )
            if restored:
                self.portfolio.positions[market_id].current_price = midpoint or current_price
                logger.info(
                    "WALLET RECONCILE: restored '%s' — %d %s shares @ $%.3f",
                    question[:45], floored_shares, side, avg_price,
                )

        logger.info(
            "WALLET RECONCILE complete — wallet=%d positions, tracked=%d positions",
            len([p for p in wallet_positions if p["shares"] >= 0.5]),
            len(self.portfolio.positions),
        )

    def _monitor_positions(self, state: AgentState) -> dict:
        """Node 6: Check existing positions, auto-exit when thresholds hit."""
        logger.info("── Step 6: Monitoring positions ──")

        # Full wallet reconciliation every N cycles
        self._cycle_count += 1
        if self._cycle_count % self._wallet_reconcile_interval == 0:
            logger.info("── Full wallet reconciliation (cycle %d) ──", self._cycle_count)
            self._reconcile_full_wallet()

        # Detect GTC orders that filled since last cycle
        self._reconcile_gtc_orders()
        # Confirm GTC SELL fills and close positions when tokens are gone
        self._reconcile_sell_orders()
        # Close positions where sell completed on-chain but portfolio wasn't updated
        self._close_orphaned_sells()
        # Restore filled trades that are missing from the portfolio (BUG-031)
        self._reconcile_missing_positions()

        # Update prices for all open positions
        prices = {}
        for market_id, pos in self.portfolio.positions.items():
            midpoint = self.client.get_midpoint(pos.token_id)
            if midpoint is not None:
                prices[market_id] = midpoint

        self.portfolio.update_prices(prices)

        # Check exit conditions for each position
        # Iterate over a copy since exits delete from the dict
        for market_id, pos in list(self.portfolio.positions.items()):
            # Skip positions with a SELL already in-flight — _reconcile_sell_orders()
            # will close them once the CLOB confirms the fill
            if pos.selling_pending:
                continue
            exit_reason = self._check_exit(pos, pos.current_price)
            if exit_reason is None:
                continue

            if exit_reason.startswith("RESOLVED"):
                # Verify via Gamma API that the market is actually closed before
                # treating as resolved. Price near $0/$1 can happen pre-resolution
                # when consensus is strong (e.g. 97% YES before the Fed meeting
                # even occurs). Closing early locks in a loss needlessly. (BUG-026)
                market_info = self.client.get_market_by_id(market_id)
                if market_info is None:
                    logger.warning(
                        "RESOLVED signal for '%s' (price=%.3f) but Gamma "
                        "unreachable — holding until confirmed",
                        pos.question[:40], pos.current_price,
                    )
                    continue
                if not market_info.get("closed", False):
                    logger.debug(
                        "RESOLVED signal for '%s' (price=%.3f) — market not "
                        "closed yet, holding",
                        pos.question[:40], pos.current_price,
                    )
                    continue
                # Confirmed closed — atomically claim before resolving so the
                # scanner cannot simultaneously exit the same position
                with self.portfolio._lock:
                    current = self.portfolio.positions.get(market_id)
                    if current is None or current.selling_pending:
                        continue
                    current.selling_pending = True
                won = pos.current_price >= EXIT_RESOLVED_THRESHOLD
                logger.info(
                    "EXIT [%s]: '%s' — side=%s, price=$%.3f, won=%s",
                    exit_reason, pos.question[:40], pos.side, pos.current_price, won
                )
                self.portfolio.resolve_position(market_id, won=won)
                try:
                    asyncio.run(update_trade_outcome(
                        market_id, outcome=1 if won else 0, exit_reason=exit_reason
                    ))
                except Exception as e:
                    logger.warning("Failed to record resolved outcome for %s: %s", market_id, e)
            else:
                # Stop loss or take profit — atomically claim before selling so
                # the scanner cannot simultaneously exit the same position
                with self.portfolio._lock:
                    current = self.portfolio.positions.get(market_id)
                    if current is None or current.selling_pending:
                        continue
                    current.selling_pending = True
                try:
                    result = asyncio.run(self.executor.execute_exit(
                        market_id=market_id,
                        token_id=pos.token_id,
                        question=pos.question,
                        shares=pos.shares,
                        price=pos.current_price,
                        reason=exit_reason,
                    ))
                    if not result.success:
                        # SELL failed — verify balance before purging (BUG-025).
                        # Failure can be CLOB minimum size, not a phantom position.
                        self._purge_if_balance_zero(market_id, pos)
                except Exception as e:
                    logger.error("EXIT error for '%s': %s", pos.question[:40], e)
                    self._purge_if_balance_zero(market_id, pos)

        # Sync cash from CLOB so the tracker never drifts from the real wallet.
        # Covers the gap between detecting a resolved win (price-based) and
        # Polymarket actually completing the on-chain USDC redemption.
        self._sync_cash_from_clob()

        # Save snapshot every cycle (not just on exits) so restarts always
        # load fresh state. Runs after cash sync so saved cash is accurate.
        asyncio.run(self.portfolio.save_snapshot())

        # Log portfolio summary
        logger.info("\n%s", self.portfolio.summary())

        # Log calibration report
        self._log_calibration_report()

        return {}

    def _purge_if_balance_zero(self, market_id: str, pos) -> None:
        """Purge a position only when CLOB confirms no tokens are held.

        In paper mode we always purge (no real CLOB to check).
        In live mode we query the actual on-chain balance:
          - balance = 0    → genuine phantom (GTC never filled) — purge
          - balance >= 1   → tokens are real, SELL was rejected for another
                             reason (e.g. size below CLOB minimum) — keep
          - balance = None → API error — keep and retry next cycle
        """
        if PAPER_TRADING:
            logger.warning(
                "EXIT failed for '%s' — purging phantom position",
                pos.question[:40],
            )
            self.portfolio.purge_position(market_id)
            return

        balance = self.client.get_token_balance(pos.token_id)
        if balance is None:
            logger.warning(
                "EXIT failed for '%s' — CLOB balance unavailable, keeping position",
                pos.question[:40],
            )
            return

        if balance < 1:
            logger.warning(
                "EXIT failed for '%s' — CLOB balance=%.2f, purging phantom",
                pos.question[:40], balance,
            )
            self.portfolio.purge_position(market_id)
        else:
            # Real tokens held — SELL was rejected (e.g. size mismatch, allowance).
            # Correct tracked shares to match CLOB balance so the next exit attempt
            # uses the right amount, then release the claim so the next cycle retries.
            import math
            clob_shares = math.floor(balance)
            with self.portfolio._lock:
                current = self.portfolio.positions.get(market_id)
                if current is not None:
                    # If tracked shares > CLOB balance, correct downward so the
                    # next SELL uses a whole-number amount we actually hold.
                    if current.shares > balance + 0.01:
                        logger.warning(
                            "EXIT failed for '%s' — tracked=%.4f > CLOB balance=%.4f, "
                            "correcting shares to %d",
                            pos.question[:40], current.shares, balance, clob_shares,
                        )
                        current.shares = float(clob_shares)
                    else:
                        logger.warning(
                            "EXIT failed for '%s' — CLOB balance=%.4f (real tokens, "
                            "SELL rejected — unknown reason). Keeping position.",
                            pos.question[:40], balance,
                        )
                    current.selling_pending = False

    def _sync_cash_from_clob(self) -> None:
        """Pull real USDC balance from CLOB and correct portfolio cash.

        This is the single source of truth for how much money we can actually
        spend.  It runs every cycle so any drift (won position not yet
        redeemed, manual deposits/withdrawals) self-corrects within one hour.
        Skipped in paper-trading mode (no real wallet).
        """
        if PAPER_TRADING:
            return
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            result = self.client.clob.get_balance_allowance(params=params)
            real_usdc = int(result.get("balance", 0)) / 1_000_000  # USDC has 6 decimals
            drift = real_usdc - self.portfolio.cash
            if abs(drift) > 0.01:   # ignore sub-cent rounding differences
                logger.info(
                    "Cash sync: tracker $%.2f → actual $%.2f (drift $%+.2f)",
                    self.portfolio.cash, real_usdc, drift,
                )
                self.portfolio.cash = real_usdc
        except Exception as e:
            logger.warning("Cash sync failed — using tracker value: %s", e)

    def _log_calibration_report(self) -> None:
        """Log a calibration summary every cycle.

        Shows whether Claude's probability estimates are predictive:
        - Overall win rate vs avg estimated probability
        - Edge on winners vs losers (are we finding real edge or noise?)
        - Breakdown by confidence level (high/medium/low)

        Only reports once ≥ 3 resolved live trades exist.
        Confidence breakdown shown once ≥ 5 resolved trades exist.
        """
        MIN_TRADES = 3
        try:
            summary = asyncio.run(get_trade_summary())
            total = summary.get("total") or 0
            if total < MIN_TRADES:
                logger.info(
                    "Calibration: %d resolved trade(s) — need %d to report",
                    total, MIN_TRADES,
                )
                return

            wins          = int(summary.get("wins") or 0)
            losses        = total - wins
            win_rate      = summary.get("win_rate") or 0.0
            stop_losses   = int(summary.get("stop_losses") or 0)
            take_profits  = int(summary.get("take_profits") or 0)
            resolved      = int(summary.get("resolved") or 0)
            avg_prob_wins   = summary.get("avg_prob_wins")
            avg_prob_losses = summary.get("avg_prob_losses")
            avg_edge_wins   = summary.get("avg_edge_wins")
            avg_edge_losses = summary.get("avg_edge_losses")

            lines = [
                "── Calibration Report ──",
                f"  Closed trades : {total} total — {wins} wins / {losses} losses — win rate: {win_rate:.0%}",
                f"  Exit reasons  : {stop_losses} stop-loss | {take_profits} take-profit | {resolved} resolved",
            ]

            if avg_prob_wins is not None or avg_prob_losses is not None:
                pw = f"{avg_prob_wins:.0%}"  if avg_prob_wins  is not None else "n/a"
                pl = f"{avg_prob_losses:.0%}" if avg_prob_losses is not None else "n/a"
                lines.append(f"  Avg est. prob : wins={pw}  losses={pl}")

            if avg_edge_wins is not None or avg_edge_losses is not None:
                ew = f"{avg_edge_wins:.1%}"  if avg_edge_wins  is not None else "n/a"
                el = f"{avg_edge_losses:.1%}" if avg_edge_losses is not None else "n/a"
                lines.append(f"  Avg edge      : wins={ew}  losses={el}")

            # Calibration check: are estimated probs close to actual win rates?
            if avg_prob_wins is not None and win_rate > 0:
                bias = win_rate - avg_prob_wins
                bias_str = f"{'overconfident' if bias < 0 else 'underconfident'} by {abs(bias):.0%}"
                lines.append(f"  Prob accuracy : win_rate={win_rate:.0%} vs avg_prob={avg_prob_wins:.0%} → {bias_str}")

            # Confidence breakdown (only meaningful with more data)
            if total >= 5:
                conf_stats = asyncio.run(get_calibration_stats())
                if conf_stats:
                    lines.append("  By confidence :")
                    for row in conf_stats:
                        conf = row.get("confidence") or "unknown"
                        t    = row.get("total", 0)
                        wr   = row.get("win_rate") or 0.0
                        ep   = row.get("avg_estimated_prob")
                        ep_str = f"{ep:.0%}" if ep is not None else "n/a"
                        lines.append(
                            f"    {conf:6s}: {t} trade(s)  win={wr:.0%}  avg_prob={ep_str}"
                        )

            logger.info("\n".join(lines))

        except Exception as e:
            logger.warning("Calibration report failed: %s", e)

    def _check_exit(self, pos, current_price: float) -> str | None:
        """Check whether a position should be exited.

        Returns:
            Exit reason string, or None to hold.
        """
        if current_price is None:
            return None  # Price unavailable (dead market / API error) — hold

        # --- Resolved market detection ---
        # current_price is the price of the token we HOLD (YES price for YES
        # positions, NO price for NO positions). Near $1 = our token wins;
        # near $0 = our token is worthless (opposite side won).
        if current_price >= EXIT_RESOLVED_THRESHOLD:
            # Our token is worth ~$1 — identify the market outcome by side
            return "RESOLVED_YES" if pos.side == "YES" else "RESOLVED_NO"
        if current_price <= (1 - EXIT_RESOLVED_THRESHOLD):
            # Our token is worth ~$0 — opposite outcome
            return "RESOLVED_NO" if pos.side == "YES" else "RESOLVED_YES"

        # --- Stop loss ---
        if pos.cost_basis > 0:
            pnl_pct = pos.unrealized_pnl / pos.cost_basis
            if pnl_pct <= EXIT_STOP_LOSS_PCT:
                return "STOP_LOSS"

        # --- Take profit (Option B: probability-anchored fair value exit) ---
        # fair_value is what the token should be worth if our probability estimate
        # is correct at resolution time:
        #   YES token pays $1 if YES wins → fair_value = estimated_prob
        #   NO  token pays $1 if NO  wins → fair_value = 1.0 - estimated_prob
        # We exit when current_price has converged 90% from entry to fair value.
        # Fallback: legacy 75%-to-$1 formula for positions without an estimate.
        if pos.estimated_prob > 0:
            fair_value = pos.estimated_prob if pos.side == "YES" else (1.0 - pos.estimated_prob)
            if fair_value > pos.avg_price:
                take_profit_price = pos.avg_price + 0.90 * (fair_value - pos.avg_price)
            else:
                # Estimated fair value at or below entry — no upside; use legacy fallback
                take_profit_price = pos.avg_price + 0.75 * (1.0 - pos.avg_price)
        else:
            # No probability stored (old trade or paper trade) — legacy formula
            take_profit_price = pos.avg_price + 0.75 * (1.0 - pos.avg_price)
        if current_price >= take_profit_price:
            return "TAKE_PROFIT"

        return None

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

        # Record cycle start in database
        run_id = None
        try:
            run_id = await insert_agent_run({"start_time": cycle_start})
        except Exception as e:
            logger.warning("Failed to record cycle start: %s", e)

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
            "regime": "neutral",
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

            # Record cycle completion
            if run_id:
                await update_agent_run(run_id, {
                    "end_time": datetime.now(timezone.utc).isoformat(),
                    "status": "completed_with_errors" if errors else "completed",
                    "markets_analyzed": len(result.get("markets", [])),
                    "signals_generated": len(result.get("signals", [])),
                    "trades_executed": len(result.get("execution_results", [])),
                    "errors": "; ".join(str(e) for e in errors) if errors else None,
                })

        except Exception as e:
            self._consecutive_errors += 1
            logger.error(
                "Cycle failed (%d consecutive): %s",
                self._consecutive_errors, e
            )

            # Record cycle failure
            if run_id:
                await update_agent_run(run_id, {
                    "end_time": datetime.now(timezone.utc).isoformat(),
                    "status": "failed",
                    "errors": str(e),
                })

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

        # Start background price scanner (after portfolio is ready)
        self._start_price_scanner()

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

    # ──────────────────────────────────────────────
    # Background price scanner
    # ──────────────────────────────────────────────

    def _start_price_scanner(self) -> None:
        """Start a background daemon thread that polls position prices every minute.

        The scanner captures sharp price moves between hourly cycles — e.g. a
        market spiking to $0.97 after a news event.  It handles three fast exits:
          Tier 1a — RESOLVED_SCANNER : price ≥ 0.95 or ≤ 0.05     → immediate
          Tier 1b — STRUCTURAL_COLLAPSE: price ≤ avg × (1 − 50%)   → immediate
          Tier 1c — TAKE_PROFIT       : 2 consecutive scans confirm → exit

        Stop-loss exits (-25%) are left to the hourly cycle which runs the full
        context check (Gamma verification, portfolio snapshot, calibration log).
        """
        if self._scanner_running:
            return
        self._scanner_running = True
        t = threading.Thread(target=self._price_scan_loop, daemon=True, name="price-scanner")
        t.start()
        logger.info("Price scanner started (interval: %ds)", PRICE_SCAN_INTERVAL_SECONDS)

    def _price_scan_loop(self) -> None:
        """Background loop: sleep → scan → repeat until process exits."""
        while True:
            time.sleep(PRICE_SCAN_INTERVAL_SECONDS)
            try:
                self._run_price_scan()
            except Exception as e:
                logger.warning("Price scan error: %s", e)

    def _run_price_scan(self) -> None:
        """Fetch midpoint prices for open positions and evaluate exit conditions."""
        if time.time() - self._last_prune_time > 3600:
            sync_prune_scanner_events(days=30)
            self._last_prune_time = time.time()

        # Snapshot market IDs under lock — avoids iterating a live dict
        with self.portfolio._lock:
            market_ids = list(self.portfolio.positions.keys())

        for market_id in market_ids:
            # Re-check under lock each iteration — position may have closed
            with self.portfolio._lock:
                pos = self.portfolio.positions.get(market_id)
            if pos is None or pos.selling_pending:
                continue

            midpoint = self.client.get_midpoint(pos.token_id)
            if midpoint is None:
                continue

            # CPython GIL makes float attribute writes safe across threads
            pos.current_price = midpoint
            self._scanner_exit_check(market_id, pos, midpoint)

    def _scanner_exit_check(self, market_id: str, pos, current_price: float) -> None:
        """Evaluate tiered exit conditions for one position.

        Tiers (in priority order):
          1a RESOLVED_SCANNER   — price ≥ 0.95 or ≤ 0.05        → immediate
          1b STRUCTURAL_COLLAPSE — price ≤ avg_price × 50%       → immediate
          1c TAKE_PROFIT         — _check_exit confirms TP twice  → exit
          (STOP_LOSS is ignored here — handled by the hourly cycle)
        """
        # Tier 1a: market effectively resolved — verify via Gamma before selling
        # to avoid exiting at $0.97 when the market hasn't actually closed yet.
        # (The hourly cycle does the same check — BUG-031)
        if (current_price >= EXIT_RESOLVED_THRESHOLD or
                current_price <= (1 - EXIT_RESOLVED_THRESHOLD)):
            market_info = self.client.get_market_by_id(market_id)
            if market_info is None:
                logger.debug(
                    "RESOLVED_SCANNER for '%s' (price=%.3f) — Gamma unreachable, holding",
                    pos.question[:40], current_price,
                )
                return
            if not market_info.get("closed", False):
                logger.debug(
                    "RESOLVED_SCANNER for '%s' (price=%.3f) — market not closed yet, holding",
                    pos.question[:40], current_price,
                )
                return
            self._scanner_trigger_exit(market_id, pos, current_price, "RESOLVED_SCANNER")
            return

        # Tier 1b: structural collapse — sharp adverse move
        if pos.avg_price > 0 and current_price <= pos.avg_price * (1 - SCANNER_HARD_LOSS_PCT):
            self._scanner_trigger_exit(market_id, pos, current_price, "STRUCTURAL_COLLAPSE")
            return

        # Tier 1c: take-profit confirmed on two consecutive scans
        exit_signal = self._check_exit(pos, current_price)
        if exit_signal == "TAKE_PROFIT":
            self._take_profit_hits[market_id] += 1
            if self._take_profit_hits[market_id] >= SCANNER_TAKE_PROFIT_CONFIRMATIONS:
                self._take_profit_hits.pop(market_id, None)
                self._scanner_trigger_exit(market_id, pos, current_price, "TAKE_PROFIT")
        else:
            # Price moved away from take-profit level — reset confirmation counter
            self._take_profit_hits.pop(market_id, None)

    def _scanner_trigger_exit(self, market_id: str, pos, current_price: float, reason: str) -> None:
        """Atomically claim and execute an exit from the scanner thread.

        Sets selling_pending under the portfolio lock before calling execute_exit
        so the hourly cycle cannot simultaneously close the same position.
        """
        with self.portfolio._lock:
            current = self.portfolio.positions.get(market_id)
            if current is None or current.selling_pending:
                return  # Already gone or claimed by hourly cycle
            current.selling_pending = True

        logger.info(
            "SCANNER EXIT [%s]: '%s' — price=$%.3f, avg=$%.3f",
            reason, pos.question[:40], current_price, pos.avg_price,
        )

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "market_id": market_id,
            "question": pos.question,
            "trigger": reason,
            "price": current_price,
            "avg_price": pos.avg_price,
            "shares": pos.shares,
            "order_success": None,
            "error": None,
        }

        try:
            result = asyncio.run(self.executor.execute_exit(
                market_id=market_id,
                token_id=pos.token_id,
                question=pos.question,
                shares=pos.shares,
                price=current_price,
                reason=reason,
            ))
            event["order_success"] = 1 if (result and result.success) else 0
            if result and not result.success:
                event["error"] = result.message
                # Order returned but failed (e.g. CLOB rejected) — release the
                # claim so the hourly cycle or next scan can retry.
                with self.portfolio._lock:
                    current = self.portfolio.positions.get(market_id)
                    if current is not None:
                        current.selling_pending = False
        except Exception as e:
            logger.error("Scanner exit failed for '%s': %s", pos.question[:40], e)
            event["order_success"] = 0
            event["error"] = str(e)
            # Release the claim so the position can be retried
            with self.portfolio._lock:
                current = self.portfolio.positions.get(market_id)
                if current is not None:
                    current.selling_pending = False
        finally:
            sync_insert_scanner_event(event)

    def shutdown(self):
        """Clean up resources."""
        self.client.close()
        logger.info("Agent shut down cleanly")
