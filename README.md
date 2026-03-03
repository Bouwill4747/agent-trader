# Polymarket Trading Agent

Autonomous trading agent for [Polymarket](https://polymarket.com) prediction markets. Uses a hybrid strategy: **Claude LLM** for qualitative market research and **FinBERT ML** for quantitative sentiment analysis. Defaults to paper trading with conservative risk controls.

Built as a guided learning project exploring the intersection of AI, finance, and cybersecurity.

## How It Works

The agent runs a 6-stage pipeline every 60 minutes:

```
discover_markets → research_markets → generate_signals → evaluate_risks → execute_trades → monitor_positions
```

1. **Discover** — Makes three targeted Gamma API calls (one per resolution tier) to find short-, medium-, and long-term markets. Prioritises short-term markets for fast calibration feedback.
2. **Research** — Collects news from NewsAPI, RSS feeds, Stocktwits, Google Trends, CoinGecko, Metaculus, Fear & Greed, FRED, and Finnhub for each candidate market.
3. **Analyze** — FinBERT scores article sentiment; Claude estimates event probabilities independently (without seeing the market price, to avoid anchoring bias).
4. **Risk Check** — Fractional Kelly criterion sizes positions; hard limits enforced including per-theme correlation caps and per-tier cycle exposure caps.
5. **Execute** — Places paper trades (or real orders via CLOB API in live mode). GTC order fills are reconciled each cycle.
6. **Monitor** — Tracks open positions, checks stop-loss/take-profit exits, runs GTC reconciliation, verifies resolutions against Gamma API.

Orchestrated by [LangGraph](https://github.com/langchain-ai/langgraph) as a state machine.

## Quick Start

### 1. Clone and install

```bash
git clone <repo-url>
cd agent_trader
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Set up API keys

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and fill in your keys:

| Key | Required for | Get it from |
|-----|-------------|-------------|
| `ANTHROPIC_API_KEY` | Market analysis | [console.anthropic.com](https://console.anthropic.com) |
| `NEWS_API_KEY` | News collection | [newsapi.org](https://newsapi.org) (free tier) |
| `POLYGON_PRIVATE_KEY` | Live trading only | Your Polygon wallet |

RSS feeds, Stocktwits, CoinGecko, Fear & Greed, and Metaculus are free with no key required.

### 3. Run in paper trading mode

```bash
python main.py              # Continuous loop (every 60 min)
python main.py --once       # Single cycle then exit
```

### 4. Monitor

```bash
# Watch live logs
tail -f data/agent.log

# Emergency stop
touch data/STOP
```

## Project Structure

```
agent_trader/
├── main.py                        # Entry point
├── config/settings.py             # All configuration (loaded from .env)
├── setup_wallet.py                # Wallet + CLOB credential setup guide
├── src/
│   ├── data/                      # Data collection layer
│   │   ├── polymarket_client.py   #   Gamma API + CLOB API wrapper
│   │   ├── news_collector.py      #   NewsAPI integration
│   │   ├── rss_collector.py       #   Free RSS feeds (Reuters, BBC, AP, etc.)
│   │   ├── stocktwits_collector.py#   Stocktwits social sentiment
│   │   ├── trends_collector.py    #   Google Trends search interest
│   │   ├── coingecko_collector.py #   CoinGecko crypto price context
│   │   ├── metaculus_collector.py #   Metaculus crowd forecasts
│   │   ├── fear_greed_collector.py#   CNN Fear & Greed index
│   │   ├── fred_collector.py      #   FRED macro economic data
│   │   ├── finnhub_collector.py   #   Finnhub news + equity quotes
│   │   └── models.py              #   Pydantic validation models
│   ├── analysis/                  # Analysis engine
│   │   ├── finbert_analyzer.py    #   FinBERT sentiment scoring
│   │   ├── llm_researcher.py      #   Claude probability estimation
│   │   └── signal_generator.py   #   Combines ML + LLM → signals
│   ├── trading/                   # Trading layer
│   │   ├── risk_manager.py        #   Kelly criterion + position limits
│   │   ├── executor.py            #   Order placement (paper + live)
│   │   └── portfolio.py           #   Position tracking + PnL
│   ├── agent/
│   │   └── orchestrator.py        #   LangGraph state machine
│   └── utils/
│       ├── logger.py              #   Structured logging + secret redaction
│       └── db.py                  #   SQLite persistence
├── tests/                         # 177 tests (unit + integration + security)
├── docs/                          # Learning documentation
│   ├── PROGRESS.md                #   Session-by-session progress log
│   ├── BUGS.md                    #   Every bug with root cause + lesson
│   └── GLOSSARY.md                #   Terms explained for learning
└── data/                          # Runtime data (gitignored)
    ├── trades.db                  #   SQLite database (auto-created)
    └── agent.log                  #   Log file (rotating, 10MB)
```

## Risk Controls

All safety limits are defined in `config/settings.py`:

| Control | Value | Purpose |
|---------|-------|---------|
| Kelly fraction | 0.25× | Conservative position sizing |
| Max per position | 5% of bankroll | No single market dominates |
| Max per market (total adds) | 15% | Prevents over-averaging |
| Max total exposure | 60% | 40% of bankroll stays as cash |
| Max concurrent positions | 12 | Diversification floor |
| Risk-reducing mode | 15% drawdown | Exits only — no new entries |
| Drawdown halt | 20% | Hard stop if losses mount |
| Minimum edge (floor) | 20% | Only trade with significant edge |
| Banned themes | geopolitics, politics, macro | No LLM edge vs. market |
| Theme correlation cap | 1 position per theme | Prevents thematic clustering |
| Short-term cycle cap | 20% of bankroll | Per-cycle short-term exposure limit |
| Medium-term cycle cap | 40% of bankroll | Per-cycle medium-term exposure limit |
| Minimum trade | $1 | Polymarket floor |

## Market Discovery

The agent uses **tiered discovery** to find markets across all resolution horizons:

| Tier | Resolution | API call | Min volume | Min liquidity |
|------|-----------|----------|-----------|---------------|
| Short | ≤ 14 days | `end_date_max=today+14d`, limit=100 | $300 | $150 |
| Medium | 15–60 days | `end_date_min=today+15d, end_date_max=today+60d`, limit=50 | $500 | $250 |
| Long | > 60 days | `end_date_min=today+61d`, limit=50 | $1,000 | $500 |

Short-term markets are prioritised in the final selection (10 slots: short first, then medium, then long). Markets resolving within 48 hours are excluded — price discovery is unreliable near expiry.

## Safety Features

- **Paper trading by default** — `PAPER_TRADING=true` in `.env.example`. Live mode requires `PAPER_TRADING=false` AND typing "yes" at a confirmation prompt.
- **Kill switch** — Create `data/STOP` to halt the agent. Checked before every trade.
- **Secret redaction** — Private keys and API keys are automatically stripped from log output.
- **Crash recovery** — Portfolio positions are persisted to SQLite. On restart, the agent reconstructs its state from the latest snapshot.
- **GTC reconciliation** — GTC orders (status=live) are not counted as positions until the CLOB confirms a token balance. Checked every cycle.
- **Gamma resolution verification** — Price ≥ 0.95 alone does not trigger a resolution payout. The Gamma `closed` flag is verified before booking any outcome.
- **Input validation** — All API responses validated with Pydantic. Market IDs checked for path traversal. LLM inputs sanitized against prompt injection.
- **Dependency pinning** — All packages pinned to exact versions to prevent supply chain attacks.

See [SECURITY.md](SECURITY.md) for the full threat model and emergency procedures.

## Testing

```bash
# Run all 177 tests
pytest tests/ -v

# Run only security tests
pytest tests/test_security.py -v

# Run a specific test file
pytest tests/test_risk_manager.py -v
pytest tests/test_market_discovery.py -v
```

## Live Trading

> **Warning**: Live trading uses real money. Start small ($10–50) and monitor closely.

1. Set up a dedicated Polygon wallet (not your personal wallet)
2. Run `python setup_wallet.py` to derive CLOB API credentials
3. Fund the wallet with USDC.e on Polygon
4. Set `PAPER_TRADING=false` in `.env`
5. Run `python main.py` and confirm at the prompt

## Documentation

This project prioritises learning. Every session, bug, and decision is documented:

- **[docs/PROGRESS.md](docs/PROGRESS.md)** — What was built each session, decisions made, what's next
- **[docs/BUGS.md](docs/BUGS.md)** — 26+ bugs tracked with root causes and lessons learned
- **[docs/GLOSSARY.md](docs/GLOSSARY.md)** — 100+ terms across blockchain, trading, ML, and security
- **[SECURITY.md](SECURITY.md)** — Threat model, credentials, emergency procedures

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent orchestration | LangGraph |
| LLM research | Claude (Anthropic API) |
| Sentiment analysis | FinBERT (HuggingFace) |
| Market data | Polymarket Gamma API + CLOB (py-clob-client) |
| News | NewsAPI + RSS (free, no key) |
| Financial data | Finnhub, FRED, CoinGecko, Fear & Greed (free) |
| Crowd forecasts | Metaculus (free) |
| Social sentiment | Stocktwits (free) |
| Database | SQLite (aiosqlite) |
| Validation | Pydantic |
