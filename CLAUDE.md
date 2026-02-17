# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Autonomous trading agent for Polymarket prediction markets. Hybrid strategy: Claude LLM for qualitative research + FinBERT ML for quantitative sentiment. Conservative risk profile (0.25x Kelly criterion, $100-500 bankroll). Defaults to paper trading.

## Architecture

The agent runs a LangGraph state machine loop every 30 minutes:
`discover_markets → research_markets → generate_signals → evaluate_risks → execute_trades → monitor_positions → loop`

Five layers, each in its own `src/` subdirectory:
- **`src/data/`** — Collects data: Polymarket APIs (Gamma for discovery, CLOB for trading), NewsAPI, Reddit (PRAW)
- **`src/analysis/`** — Generates predictions: FinBERT sentiment scoring + Claude probability estimation → combined trading signals
- **`src/trading/`** — Manages money: risk checks (Kelly criterion), order execution (paper or live via py-clob-client), portfolio tracking
- **`src/agent/`** — Orchestrates the loop: LangGraph state machine in `orchestrator.py`
- **`src/utils/`** — Shared infrastructure: structured logging, SQLite database

Config lives in `config/settings.py`, loaded from `.env` via python-dotenv.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run agent (paper trading by default)
python main.py

# Run tests
pytest tests/ -v

# Run a single test
pytest tests/test_risk_manager.py -v
```

## Key APIs

- **Polymarket CLOB**: `https://clob.polymarket.com` — trading (auth required, `py-clob-client` SDK)
- **Polymarket Gamma**: `https://gamma-api.polymarket.com` — market discovery (public, no auth)
- **Rate limits**: 20 req/s trading, 10 req/s order book — built into `polymarket_client.py`

## Documentation Rules

This is a guided learning project for a cybersecurity student. After every significant change, update:
- `docs/PROGRESS.md` — what was done, decisions made, next steps
- `docs/BUGS.md` — every bug with root cause, fix, and lesson learned
- `docs/GLOSSARY.md` — new terms when they first appear in code

## Safety

- `PAPER_TRADING=true` is the default. Live trading requires explicit `PAPER_TRADING=false` in `.env`
- Kill switch: create `data/STOP` file to halt the agent at next cycle
- Max 5% bankroll per market, max 50% total exposure, 20% drawdown halts trading
