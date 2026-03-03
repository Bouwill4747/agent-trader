"""
Central configuration for the trading agent.
All other modules import from here — never hardcode values elsewhere.
"""

import os
from dotenv import load_dotenv

# Anchor all paths to the project root (one level up from config/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load .env file into environment variables
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


# === API Credentials ===

POLYGON_PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY", "")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")


# === API Endpoints ===

GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137


# === Agent Settings ===

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "100"))
CYCLE_INTERVAL_SECONDS = 3600  # 60 minutes


# === Risk Limits ===

KELLY_FRACTION = 0.25           # Use 25% of Kelly-recommended size
MAX_POSITION_PCT = 0.05         # Max 5% of bankroll per single trade
MAX_MARKET_EXPOSURE_PCT = 0.15  # Max 15% of bankroll in any single market (across all adds)
MAX_TOTAL_EXPOSURE_PCT = 0.60   # Max 60% of bankroll in open positions
MAX_CONCURRENT_POSITIONS = 12
MAX_DRAWDOWN_PCT = 0.20         # Full halt at 20% drawdown
DRAWDOWN_RISK_REDUCING_PCT = 0.15  # Enter RISK_REDUCING_ONLY (exits only) at 15% drawdown
MIN_TRADE_SIZE = 1.0            # Polymarket minimum: $1
MIN_SIGNAL_EDGE = 0.05          # Minimum edge to generate a signal (before spread cost)
MAX_SPREAD_THRESHOLD = float(os.getenv("MAX_SPREAD_THRESHOLD", "0.10"))  # Hard cap: reject if spread > 10%
MIN_EDGE_FLOOR = 0.20           # Absolute hard floor: never trade below 20% edge
BANNED_THEMES = frozenset({"geopolitics", "politics", "macro"})  # No LLM edge — market aggregates all public info
MAX_POSITIONS_PER_THEME = 1     # Correlation cap: prevent thematic clustering across positions

# === Market Category Filters ===
# Markets whose questions match any keyword (case-insensitive) are skipped
# at discovery. Avoids short-duration sports results and entertainment
# awards where news analysis gives no real edge over the market.
SKIP_MARKET_KEYWORDS = [
    # Single-game date-specific sports results ("Will X win on 2026-02-21?")
    "win on 20",
    # Esports match formats (Best of 3 / Best of 5)
    "bo3", "bo5",

    # --- Major sports leagues ---
    "nba", "nfl", "nhl", "mlb", "mls",
    "nba finals", "super bowl", "world series", "stanley cup",

    # --- Sports types ---
    "basketball", "football game", "baseball game", "hockey game",
    "tennis", "golf tournament", "cricket", "rugby",
    "boxing match", "ufc", "mma fight",
    "formula 1", "grand prix", "nascar",
    "swimming", "gymnastics", "track and field",

    # --- Sports award/outcome language ---
    "most valuable player", "mvp award",
    "most improved", "rookie of the year",
    "all-star game", "slam dunk contest",
    "playoff series", "championship series",
    "win the championship", "win the title",
    "advance to the finals", "reach the finals",

    # --- Match-specific language ---
    "match winner", "set winner", "game winner",
    "beat ", "defeat ",  # "Will X beat Y" / "Will X defeat Y"

    # --- Entertainment awards ---
    "best actor", "best actress", "best director", "best picture",
    "best film", "best supporting", "best animated",
    "oscar", "oscars", "academy award",
    "emmy", "grammy", "golden globe", "bafta",
    "opening weekend box office",
    "number one at the box office",
]

# === Market Discovery — Tier Resolution Boundaries ===
# Single source of truth for tier day-limits. Used in both discovery and risk evaluation.
SHORT_TERM_MAX_DAYS  = 14   # Tier 1: ≤14 days (weekly data, near-term events)
MEDIUM_TERM_MAX_DAYS = 60   # Tier 2: 15–60 days (monthly events, near-term macro)
# Tier 3: >60 days (long-horizon, slowest calibration feedback)

# === Market Discovery — Per-Tier Thresholds ===
# Short-term markets have less time to accumulate volume, so lower floors are needed.
SHORT_TERM_MIN_VOLUME     = 300    # markets resolving ≤14 days
SHORT_TERM_MIN_LIQUIDITY  = 150
MEDIUM_TERM_MIN_VOLUME    = 500    # markets resolving 15-60 days
MEDIUM_TERM_MIN_LIQUIDITY = 250
LONG_TERM_MIN_VOLUME      = 1000   # markets resolving >60 days (previous values)
LONG_TERM_MIN_LIQUIDITY   = 500
MIN_DAYS_TO_RESOLUTION    = 2      # Skip markets resolving within 48h (illiquid near expiry)

# Per-tier intra-cycle exposure caps.
# Prevents same-cycle overloading of short-term or medium-term positions.
SHORT_TERM_MAX_CYCLE_EXPOSURE_PCT  = 0.20  # Max 20% bankroll in new short-term entries per cycle
MEDIUM_TERM_MAX_CYCLE_EXPOSURE_PCT = 0.40  # Max 40% bankroll in new medium-term entries per cycle
# Long-term: governed by existing MAX_TOTAL_EXPOSURE_PCT = 0.60


# === Exit Thresholds ===

EXIT_STOP_LOSS_PCT = -0.25       # Exit when position is down 25%
EXIT_RESOLVED_THRESHOLD = 0.95   # Price >= 0.95 or <= 0.05 = market resolved


# === LLM Settings ===

LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929")


# === Rate Limits (requests per second) ===

CLOB_RATE_LIMIT = 20
ORDERBOOK_RATE_LIMIT = 10


# === File Paths (anchored to project root) ===

DATABASE_PATH = os.path.join(_PROJECT_ROOT, "data", "trades.db")
LOG_PATH = os.path.join(_PROJECT_ROOT, "data", "agent.log")
KILL_SWITCH_PATH = os.path.join(_PROJECT_ROOT, "data", "STOP")
