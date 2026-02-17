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
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")


# === API Endpoints ===

GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137


# === Agent Settings ===

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "100"))
CYCLE_INTERVAL_SECONDS = 1800  # 30 minutes


# === Risk Limits ===

KELLY_FRACTION = 0.25           # Use 25% of Kelly-recommended size
MAX_POSITION_PCT = 0.05         # Max 5% of bankroll per market
MAX_TOTAL_EXPOSURE_PCT = 0.50   # Max 50% of bankroll in open positions
MAX_CONCURRENT_POSITIONS = 10
MAX_DRAWDOWN_PCT = 0.20         # Halt trading at 20% drawdown
MIN_TRADE_SIZE = 1.0            # Polymarket minimum: $1
MIN_EDGE_THRESHOLD = 0.10       # Only trade when edge > 10%


# === LLM Settings ===

LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929")


# === Rate Limits (requests per second) ===

CLOB_RATE_LIMIT = 20
ORDERBOOK_RATE_LIMIT = 10


# === File Paths (anchored to project root) ===

DATABASE_PATH = os.path.join(_PROJECT_ROOT, "data", "trades.db")
LOG_PATH = os.path.join(_PROJECT_ROOT, "data", "agent.log")
KILL_SWITCH_PATH = os.path.join(_PROJECT_ROOT, "data", "STOP")
