# Polymarket Trading Agent

Autonomous trading agent for [Polymarket](https://polymarket.com) prediction markets. Uses a hybrid strategy: **Claude LLM** for qualitative market research and **FinBERT ML** for quantitative sentiment analysis. Defaults to paper trading with conservative risk controls.

Built as a guided learning project exploring the intersection of AI, finance, and cybersecurity.

## How It Works

The agent runs a 6-stage pipeline every 30 minutes:

```
discover_markets → research_markets → generate_signals → evaluate_risks → execute_trades → monitor_positions
```

1. **Discover** — Finds active, liquid prediction markets via the Gamma API
2. **Research** — Collects news (NewsAPI) and Reddit sentiment for candidate markets
3. **Analyze** — FinBERT scores article sentiment; Claude estimates event probabilities
4. **Risk Check** — Fractional Kelly criterion sizes positions; hard limits enforced
5. **Execute** — Places paper trades (or real orders via CLOB API in live mode)
6. **Monitor** — Tracks open positions, checks for exits, updates portfolio

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
| `REDDIT_CLIENT_ID` | Sentiment scraping | [reddit.com/prefs/apps](https://reddit.com/prefs/apps) |
| `REDDIT_CLIENT_SECRET` | Sentiment scraping | Same as above |
| `POLYGON_PRIVATE_KEY` | Live trading only | Your Polygon wallet |

### 3. Run in paper trading mode

```bash
python main.py              # Continuous loop (every 30 min)
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
├── main.py                      # Entry point
├── config/settings.py           # All configuration (loaded from .env)
├── setup_wallet.py              # Wallet + CLOB credential setup guide
├── src/
│   ├── data/                    # Data collection layer
│   │   ├── polymarket_client.py #   Gamma API + CLOB API wrapper
│   │   ├── news_collector.py    #   NewsAPI integration
│   │   ├── sentiment_scraper.py #   Reddit via PRAW
│   │   └── models.py           #   Pydantic validation models
│   ├── analysis/                # Analysis engine
│   │   ├── finbert_analyzer.py  #   FinBERT sentiment scoring
│   │   ├── llm_researcher.py   #   Claude probability estimation
│   │   └── signal_generator.py #   Combines ML + LLM → signals
│   ├── trading/                 # Trading layer
│   │   ├── risk_manager.py     #   Kelly criterion + position limits
│   │   ├── executor.py         #   Order placement (paper + live)
│   │   └── portfolio.py        #   Position tracking + PnL
│   ├── agent/
│   │   └── orchestrator.py     #   LangGraph state machine
│   └── utils/
│       ├── logger.py           #   Structured logging + secret redaction
│       └── db.py               #   SQLite persistence
├── tests/                       # 60 tests (unit + security)
├── docs/                        # Learning documentation
│   ├── PROGRESS.md             #   Session-by-session progress log
│   ├── BUGS.md                 #   Every bug with root cause + lesson
│   └── GLOSSARY.md             #   Terms explained for learning
└── data/                        # Runtime data (gitignored)
    ├── trades.db               #   SQLite database (auto-created)
    └── agent.log               #   Log file (rotating, 10MB)
```

## Risk Controls

All safety limits are hardcoded in `config/settings.py` — not configurable via environment variables:

| Control | Value | Purpose |
|---------|-------|---------|
| Kelly fraction | 0.25x | Conservative position sizing |
| Max per position | 5% of bankroll | No single market can wipe you out |
| Max total exposure | 50% | Half the bankroll stays as cash |
| Max concurrent positions | 10 | Diversification floor |
| Drawdown halt | 20% | Stops trading if losses mount |
| Minimum edge | 10% | Only trade with significant edge |
| Minimum trade | $1 | Polymarket floor |

## Safety Features

- **Paper trading by default** — `PAPER_TRADING=true` in `.env.example`. Live mode requires `PAPER_TRADING=false` AND typing "yes" at a confirmation prompt.
- **Kill switch** — Create `data/STOP` to halt the agent. Checked before every trade. Cancels all open orders on detection.
- **Secret redaction** — Private keys and API keys are automatically stripped from log output.
- **Crash recovery** — Portfolio positions are persisted to SQLite. On restart, the agent reconstructs its state from the latest snapshot.
- **Input validation** — All API responses validated with Pydantic. Market IDs checked for path traversal. LLM inputs sanitized against prompt injection.
- **Dependency pinning** — All packages pinned to exact versions to prevent supply chain attacks.

See [SECURITY.md](SECURITY.md) for the full threat model and emergency procedures.

## Testing

```bash
# Run all 60 tests
pytest tests/ -v

# Run only security tests
pytest tests/test_security.py -v

# Run a specific test file
pytest tests/test_risk_manager.py -v
```

## Live Trading

> **Warning**: Live trading uses real money. Start small ($10-50) and monitor closely.

1. Set up a dedicated Polygon wallet (not your personal wallet)
2. Run `python setup_wallet.py` to derive CLOB API credentials
3. Fund the wallet with USDC.e on Polygon
4. Set `PAPER_TRADING=false` in `.env`
5. Run `python main.py` and confirm at the prompt

## Documentation

This project prioritizes learning. Every session, bug, and decision is documented:

- **[docs/PROGRESS.md](docs/PROGRESS.md)** — What was built, decisions made, what's next
- **[docs/BUGS.md](docs/BUGS.md)** — 12 bugs tracked with root causes and lessons learned
- **[docs/GLOSSARY.md](docs/GLOSSARY.md)** — 80+ terms across blockchain, trading, ML, security
- **[SECURITY.md](SECURITY.md)** — Threat model, credentials, emergency procedures
- **[docs/reports/](docs/reports/)** — 3 security audit reports with findings

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent orchestration | LangGraph |
| LLM research | Claude (Anthropic API) |
| Sentiment analysis | FinBERT (HuggingFace) |
| Market data | Polymarket Gamma API |
| Trading | Polymarket CLOB (py-clob-client) |
| News | NewsAPI |
| Social sentiment | Reddit (PRAW) |
| Database | SQLite (aiosqlite) |
| Validation | Pydantic |
