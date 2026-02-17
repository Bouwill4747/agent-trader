# Autonomous Polymarket Trading Agent — Implementation Plan

## Context

Build an autonomous trading agent that participates in Polymarket prediction markets. The agent uses a **hybrid strategy**: LLMs (Claude) for qualitative research and probability estimation, plus ML models (FinBERT) for quantitative sentiment signals. It operates conservatively ($100-500 bankroll) with strict risk controls.

---

## How It Works (High-Level)

```
┌─────────────────────────────────────────────────────────────────┐
│                        AGENT LOOP (every 30 min)                │
│                                                                 │
│  1. DISCOVER    ──→  Find active markets on Polymarket          │
│  2. RESEARCH    ──→  Collect news + Reddit sentiment            │
│  3. ANALYZE     ──→  FinBERT scores + Claude probability est.   │
│  4. DECIDE      ──→  Generate trading signals (edge > 10%)      │
│  5. RISK CHECK  ──→  Kelly criterion sizing + exposure limits   │
│  6. EXECUTE     ──→  Place orders (paper or live)               │
│  7. MONITOR     ──→  Track positions, check exits               │
│                                                                 │
│  ↻ Repeat                                                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
agent_trader/
├── .env.example                 # Template for secrets (never commit .env!)
├── .gitignore                   # Keeps secrets and data out of git
├── requirements.txt             # Python dependencies
├── PLAN.md                      # This file
├── config/
│   └── settings.py              # All configuration constants
├── setup_wallet.py              # One-time wallet & API key setup guide
├── main.py                      # Entry point — starts the agent
├── src/
│   ├── __init__.py
│   ├── data/                    # DATA LAYER — collects information
│   │   ├── __init__.py
│   │   ├── polymarket_client.py # Gamma API + CLOB API wrapper
│   │   ├── news_collector.py    # News API integration
│   │   └── sentiment_scraper.py # Reddit/social sentiment via PRAW
│   ├── analysis/                # BRAIN — makes predictions
│   │   ├── __init__.py
│   │   ├── finbert_analyzer.py  # FinBERT sentiment scoring (ML)
│   │   ├── llm_researcher.py    # Claude-based market research (LLM)
│   │   └── signal_generator.py  # Combines ML + LLM into trading signals
│   ├── trading/                 # EXECUTION — manages money
│   │   ├── __init__.py
│   │   ├── risk_manager.py      # Kelly criterion, position sizing, limits
│   │   ├── executor.py          # Order placement & management via CLOB
│   │   └── portfolio.py         # Position tracking, PnL, balance
│   ├── agent/                   # ORCHESTRATOR — runs the loop
│   │   ├── __init__.py
│   │   └── orchestrator.py      # LangGraph workflow — main agent loop
│   └── utils/                   # SHARED TOOLS
│       ├── __init__.py
│       ├── logger.py            # Structured logging
│       └── db.py                # SQLite for trade history & state
├── data/                        # Runtime data (gitignored)
│   └── trades.db                # SQLite database (auto-created)
└── tests/
    ├── __init__.py
    ├── test_risk_manager.py
    ├── test_signal_generator.py
    └── test_executor.py
```

---

## Phase 0: Prerequisites & Wallet Setup

**What you need before coding:**

1. **Python 3.10+** installed
2. **A Polygon wallet** — dedicated to the bot (not your personal wallet)
3. **Funds**: MATIC for gas (~$2) + USDC.e for trading ($100-500)
4. **API keys**: Polymarket (derived from wallet), Anthropic (Claude), NewsAPI, Reddit

**File: `.env.example`** — template for all secrets:
```
POLYGON_PRIVATE_KEY=0x...
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_API_PASSPHRASE=
ANTHROPIC_API_KEY=sk-ant-...
NEWS_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
PAPER_TRADING=true
INITIAL_BANKROLL=100
```

**`setup_wallet.py`** will guide you through generating API credentials using `py-clob-client`.

---

## Phase 1: Data Layer — Collecting Information

### `src/data/polymarket_client.py`
**What it does**: Talks to Polymarket's two APIs.
- **Gamma API** (`https://gamma-api.polymarket.com`) — public, no auth needed
  - Discover active markets, get metadata (question, deadline, volume)
  - Get token IDs (needed for trading)
- **CLOB API** (`https://clob.polymarket.com`) — requires auth
  - Get order books, prices, spreads
  - Place/cancel orders, get trade history
- Built-in rate limiting (20 req/s safe limit)

### `src/data/news_collector.py`
**What it does**: Fetches news articles about market topics.
- Uses NewsAPI to search for relevant articles
- Extracts keywords from market questions (e.g., "Will BTC hit $100k?" → "Bitcoin")
- Caches results (15-min TTL) to avoid redundant API calls

### `src/data/sentiment_scraper.py`
**What it does**: Scrapes Reddit for public sentiment.
- Uses PRAW (Python Reddit API Wrapper)
- Maps market topics to relevant subreddits
- Returns raw posts/comments for downstream analysis

---

## Phase 2: Analysis Engine — The Brain

### `src/analysis/finbert_analyzer.py`
**What it does**: ML-based sentiment scoring.
- Loads FinBERT model from Hugging Face (`ProsusAI/finbert`)
- Scores each news article/post: positive / negative / neutral + confidence
- Returns aggregate sentiment per market: -1.0 (very negative) to +1.0 (very positive)
- **Why FinBERT?** It's specifically trained on financial text — understands "bearish", "overvalued", etc.

### `src/analysis/llm_researcher.py`
**What it does**: Uses Claude as a research analyst.
- For each market, sends Claude:
  - The market question + current price + deadline
  - Recent news articles
  - Sentiment data
- Asks Claude to estimate the probability of YES (0.0 to 1.0) with reasoning
- Parses structured JSON response

### `src/analysis/signal_generator.py`
**What it does**: Combines both into a trading decision.
- Merges FinBERT score + Claude's probability estimate
- Calculates **edge** = our estimated probability - current market price
- Only generates signals when edge > 10% (configurable)
- Output: `TradingSignal` with direction (BUY_YES / BUY_NO / SKIP), confidence, reasoning

---

## Phase 3: Risk Manager & Execution — Managing Money

### `src/trading/risk_manager.py`
**What it does**: Prevents the agent from blowing up.
- **Fractional Kelly criterion (0.25x)**: Mathematically optimal position sizing, scaled down for safety
  ```
  kelly = edge / (odds - 1)
  position_size = bankroll * kelly * 0.25
  ```
- **Hard limits**:
  - Max 5% of bankroll per market ($5 on a $100 bankroll)
  - Max 50% total exposure across all positions
  - Max 10 concurrent positions
  - Min $1 per trade
- **Drawdown protection**: Halts trading if portfolio drops 20% from peak

### `src/trading/executor.py`
**What it does**: Places orders on Polymarket.
- **Paper trading mode** (default): Simulates fills without touching real money
- **Live trading mode**: Places GTC limit orders via CLOB API
- Handles retries with exponential backoff
- Emergency cancel-all function

### `src/trading/portfolio.py`
**What it does**: Tracks everything.
- All positions, orders, balances
- Unrealized PnL calculations
- Persists state to SQLite

---

## Phase 4: Agent Orchestrator — The Loop

### `src/agent/orchestrator.py`
**What it does**: LangGraph state machine that runs the full cycle.

```
discover_markets → research_markets → generate_signals → evaluate_risks → execute_trades → monitor_positions → (loop)
```

- Runs every 30 minutes (configurable)
- State persisted to SQLite between cycles
- **Kill switch**: Create `data/STOP` file to halt the agent safely

---

## Phase 5: Monitoring & Logging

### `src/utils/logger.py`
- Structured JSON logging (file + console)
- Rotating log files (10MB max, 5 backups)

### `src/utils/db.py`
- SQLite at `data/trades.db`
- Tables: `trades`, `signals`, `portfolio_snapshots`, `agent_runs`

---

## Dependencies

```
# Polymarket
py-clob-client>=0.6.0       # Official Python SDK for Polymarket CLOB
httpx>=0.27.0                # Async HTTP client for Gamma API

# LLM & Agent
anthropic>=0.40.0            # Claude API client
langgraph>=0.2.0             # Agent state machine framework
langchain-anthropic>=0.3.0   # LangChain + Claude integration
langchain-core>=0.3.0        # LangChain core

# ML & NLP
transformers>=4.40.0         # Hugging Face (loads FinBERT)
torch>=2.2.0                 # PyTorch (FinBERT backend)
sentencepiece>=0.2.0         # Tokenizer dependency

# Data Collection
praw>=7.7.0                  # Reddit API wrapper
newsapi-python>=0.2.7        # NewsAPI client

# Blockchain
web3>=7.0.0                  # Ethereum/Polygon interaction
eth-account>=0.13.0          # Wallet management

# Storage & Utils
python-dotenv>=1.0.0         # Load .env files
pydantic>=2.7.0              # Data validation
aiosqlite>=0.20.0            # Async SQLite
tenacity>=9.0.0              # Retry logic with backoff
```

---

## Build Order

| Step | What to Build | Why This Order |
|------|--------------|----------------|
| 1 | `config/settings.py`, `.env.example`, `.gitignore` | Foundation — everything imports config |
| 2 | `src/utils/logger.py`, `src/utils/db.py` | Infrastructure — everything logs and stores data |
| 3 | `src/data/polymarket_client.py` | Core — can't do anything without market data |
| 4 | `setup_wallet.py` | You need credentials before testing anything |
| 5 | `src/trading/portfolio.py` | Track state before we start trading |
| 6 | `src/trading/risk_manager.py` | Must exist before executor |
| 7 | `src/trading/executor.py` (paper mode) | Test trading flow without real money |
| 8 | `src/data/news_collector.py` | Feed the analysis engine |
| 9 | `src/data/sentiment_scraper.py` | Feed the analysis engine |
| 10 | `src/analysis/finbert_analyzer.py` | Quantitative signal component |
| 11 | `src/analysis/llm_researcher.py` | Qualitative signal component |
| 12 | `src/analysis/signal_generator.py` | Combines both into actionable signals |
| 13 | `src/agent/orchestrator.py` | Ties everything together |
| 14 | `main.py` | Entry point |
| 15 | Tests | Validate critical paths |

---

## Testing Strategy

1. **Unit tests**: Risk manager math, signal logic, portfolio tracking
2. **Integration tests**: Gamma API (no auth), news collection
3. **Paper trading**: Run 24-48 hours, verify signals look reasonable
4. **Small live test**: $10 on one high-liquidity market
5. **Gradual scale-up**: Increase as confidence grows

---

## Security Considerations

- Private key in `.env` only — never committed to git
- Dedicated bot wallet (not personal funds)
- Paper trading is the **default** — live requires `PAPER_TRADING=false`
- Rate limiting prevents API bans
- Kill switch file for emergency halt
- All activity logged for audit

---

## Ethical & Legal Notes

- Verify Polymarket is available in your jurisdiction
- Agent uses only public data (news, social media, market data)
- No market manipulation — small positions, limit orders only
- All trading activity logged for transparency
