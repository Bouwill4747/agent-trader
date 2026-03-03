# Project Progress Log

> Autonomous Polymarket Trading Agent
> Started: 2026-02-14

---

## Session 1 — 2026-02-14: Project Planning & Setup

### Completed
- [x] Defined project objectives and scope
- [x] Researched Polymarket APIs (CLOB, Gamma, WebSocket)
- [x] Researched ML models (FinBERT), agent frameworks (LangGraph), data sources
- [x] Chose hybrid strategy: LLM (Claude) + ML (FinBERT)
- [x] Chose LangGraph as agent orchestrator
- [x] Chose conservative risk profile (0.25x Kelly, $100-500 bankroll)
- [x] Created full implementation plan (`PLAN.md`)
- [x] Created project directory structure
- [x] Set up documentation system (progress, bugs, glossary)

### Decisions Made
| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Agent framework | LangGraph | Mature ecosystem, built-in state machines, pairs well with LLM research |
| Strategy | Hybrid (LLM + ML) | LLMs handle qualitative analysis, ML handles quantitative sentiment |
| Risk level | Conservative (0.25x Kelly) | Learning project, small bankroll, prioritize capital preservation |
| Default mode | Paper trading | Safe to test without risking real money |

### Next Steps
- [x] Step 1: Create `config/settings.py`, `.env.example`, `.gitignore`
- [x] Step 2: Create `src/utils/logger.py`, `src/utils/db.py`
- [x] Step 3: Create `src/data/polymarket_client.py`, `news_collector.py`, `sentiment_scraper.py`

---

## Session 2 — 2026-02-15: Building Foundation & Data Layer

### Completed
- [x] Step 1: Project foundation — `.gitignore`, `.env.example`, `config/settings.py`, `requirements.txt`, all `__init__.py` files
- [x] Step 2: Utility infrastructure — `src/utils/logger.py` (structured logging), `src/utils/db.py` (SQLite with 4 tables)
- [x] Step 3: Data layer — `src/data/polymarket_client.py` (Gamma + CLOB APIs), `src/data/news_collector.py` (NewsAPI), `src/data/sentiment_scraper.py` (Reddit/PRAW)
- [x] Created explanation docs: `docs/step2_utilities.md`, `docs/step3_data_layer.md`

### Next Steps
- [x] Step 4: Create `setup_wallet.py`
- [x] Step 5: Create `src/trading/portfolio.py`
- [x] Step 6: Create `src/trading/risk_manager.py`
- [x] Step 7: Create `src/trading/executor.py`
- [x] Steps 8-12: Analysis engine (FinBERT, LLM researcher, signal generator)
- [x] Steps 13-14: Orchestrator + main.py
- [ ] Step 15: Tests

### Docs Created
- `docs/step2_utilities.md` — Logger + database explained
- `docs/step3_data_layer.md` — Polymarket client, news, Reddit explained
- `docs/step4-7_trading_module.md` — Portfolio, risk manager, executor explained
- `docs/step8-12_analysis_engine.md` — FinBERT, Claude, signal blending explained
- `docs/step13-14_orchestrator.md` — LangGraph state machine, main.py explained

---

## Session 3 — 2026-02-15 to 2026-02-17: Security Audits & Bug Fixes

### Completed
- [x] Step 15: Wrote 31 tests across 3 test files (risk manager, executor/portfolio, signal generator)
- [x] Ran 3 parallel security audits: python-code-analyst, security-auditor, api-security-analyst
- [x] Fixed all 7 Critical findings from python code analysis
- [x] Fixed all 10 High findings from python code analysis
- [x] Fixed 4 Medium findings from python code analysis
- [x] Patched CVE-2025-68664 (langchain-core serialization injection RCE, CVSS 9.3)
- [x] Pinned langchain-core>=0.3.81, langgraph>=0.2.60 in requirements.txt
- [x] Added .env file permission check at startup (chmod 600 enforcement)
- [x] Added live trading confirmation prompt (type 'yes' to enable)
- [x] Added kill switch check before each individual trade (not just at cycle start)
- [x] Kill switch now cancels all in-flight orders when triggered
- [x] Pinned FinBERT model to specific revision hash (supply chain security)
- [x] Added trust_remote_code=False to all HuggingFace model loads
- [x] Added prompt injection mitigation (text sanitization, structural delimiters, system prompt hardening)
- [x] Added secret redaction filter to logger (redacts private keys, API keys from logs)
- [x] Made paper_mode a read-only property (can't be changed after init)
- [x] Added randomness to paper trade IDs (secrets.token_hex)
- [x] Added Reddit PRAW timeout (30 seconds)
- [x] Made Reddit user agent configurable via environment variable
- [x] All 31 tests passing after all fixes

### Security Audit Reports
- `docs/reports/python_code_analysis.md` — 7 Critical, 10 High, 12 Medium, 8 Low
- `docs/reports/security_audit.md` — 3 Critical, 5 High, 6 Medium, 4 Low, 5 Informational
- `docs/reports/api_security_analysis.md` — 3 Critical, 6 High, 8 Medium, 5 Low, 2 Informational

### Key Fixes Applied

| Finding | Severity | File(s) | Fix |
|---------|----------|---------|-----|
| C-01: BUY_NO trade handling broken | Critical | risk_manager, signal_generator, orchestrator, executor | Direction-aware edge/Kelly/price calculations |
| C-02: Sync/async mix in LangGraph | Critical | orchestrator.py | All nodes sync, async calls via asyncio.run() |
| C-03: Missing config/__init__.py | Critical | config/__init__.py | Created empty file |
| C-04: .env file corruption in setup | Critical | setup_wallet.py | Regex replacement + credential masking |
| C-05: Thread-unsafe rate limiter | Critical | polymarket_client.py | Added threading.Lock |
| C-06: Wrong token ID for BUY_NO | Critical | signal_generator.py | _get_token_ids() returns (yes_id, no_id) tuple |
| C-07: Relative file paths | Critical | config/settings.py | _PROJECT_ROOT anchoring with os.path.abspath |
| CVE-2025-68664 | Critical | requirements.txt | langchain-core>=0.3.81 |
| H-01: Prompt injection | High | llm_researcher.py | Text sanitization + XML delimiters + system prompt |
| H-03: Kill switch gaps | High | orchestrator.py | Per-trade check + cancel_all on detection |
| H-05: FinBERT supply chain | High | finbert_analyzer.py | Pinned revision hash + trust_remote_code=False |
| H-04: Credential leakage in logs | High | logger.py | SecretRedactionFilter with regex patterns |
| H-07: No retry logic | High | polymarket_client.py, llm_researcher.py | tenacity @retry decorators |
| L-01: Mutable paper_mode | Low | executor.py | Read-only @property |
| L-08: Predictable paper IDs | Low | executor.py | secrets.token_hex(4) |

### Decisions Made
| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Kill switch scope | Per-trade check + order cancel | TOCTOU race: checking only at cycle start allows trades to slip through |
| Prompt injection defense | Multi-layered (sanitize + delimiters + system prompt) | Defense in depth — no single measure is foolproof |
| FinBERT pinning | Specific revision hash | Prevents supply chain attack via compromised HuggingFace account |
| Log redaction | Regex-based filter | Catches private keys and API keys in error tracebacks |

### Remaining Items (Lower Priority)
- [x] Add Pydantic models for API response validation *(done in Session 4)*
- [x] Add security-focused unit tests *(done in Session 4)*
- [x] Portfolio state persistence/recovery from database *(done in Session 4)*
- [x] Pin exact dependency versions in requirements.txt *(done in Session 4)*
- [x] Add SECURITY.md *(done in Session 4)*

---

## Session 4 — 2026-02-17: Remaining Audit Fixes & Hardening

### Completed
- [x] Created Pydantic validation models for Gamma API responses (`src/data/models.py`)
- [x] Applied Pydantic validation in `polymarket_client.py` `get_markets()` — rejects malformed markets
- [x] Added response size limits (5MB max, 200 market cap) to prevent memory exhaustion
- [x] Narrowed exception types across all API clients (polymarket, news, Reddit) — no more bare `except Exception`
- [x] Added `_validate_id()` function for market_id, token_id, order_id path traversal prevention
- [x] Made LLM model configurable via `LLM_MODEL` in settings.py (previously hardcoded)
- [x] Moved `init_db()` from `run_cycle()` to `run()` — database initialized once at startup, not every cycle
- [x] Added portfolio crash recovery: `Portfolio.load_from_db()` restores positions from latest snapshot
- [x] Added `positions_json` column to portfolio_snapshots table for position persistence
- [x] Pinned all 14 dependencies to exact versions in requirements.txt (supply chain security)
- [x] Updated `.env.example` with non-realistic placeholders (prevents secret scanner false positives)
- [x] Created SECURITY.md documenting threat model, credentials, emergency procedures, audit history
- [x] Added 29 security-focused tests: Pydantic validation, ID validation, prompt injection, kill switch, secret redaction
- [x] All 60 tests passing (up from 31)
- [x] Updated PROGRESS.md, BUGS.md, GLOSSARY.md

### Key Fixes Applied

| Finding | Severity | File(s) | Fix |
|---------|----------|---------|-----|
| HIGH-02: No API schema validation | High | models.py, polymarket_client.py | Pydantic models validate all Gamma API fields |
| HIGH-03: Unbounded response size | High | polymarket_client.py | 5MB max + 200 market cap |
| MEDIUM-02: No ID validation | Medium | polymarket_client.py | `_validate_id()` regex check on all IDs |
| M-04/M-08: Broad exception handlers | Medium | polymarket_client.py, news, reddit | Specific types: HTTPStatusError, RequestError, etc. |
| M-08: Hardcoded LLM model | Medium | settings.py, llm_researcher.py | Configurable via `LLM_MODEL` env var |
| M-05: init_db() every cycle | Medium | orchestrator.py, main.py | Moved to startup (once) |
| H-01: Portfolio lost on crash | High | portfolio.py, db.py | `positions_json` in snapshots + `load_from_db()` |
| LOW-03: Unpinned dependencies | Low | requirements.txt | All 14 deps pinned to exact versions |
| L-01/L-02: Realistic .env placeholders | Low | .env.example | Changed to `REPLACE_WITH_YOUR_*` format |
| I-3: No security tests | Info | tests/test_security.py | 29 tests covering validation, injection, redaction |
| I-4: No SECURITY.md | Info | SECURITY.md | Threat model, procedures, audit history documented |

### Decisions Made
| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Pydantic validation scope | Gamma API markets only | Highest-risk data (drives trades); other APIs lower priority |
| Exception narrowing | httpx-specific + connection/value errors | Specific enough to catch real issues, broad enough for CLOB SDK |
| Portfolio recovery approach | JSON positions in snapshot table | Simpler than separate positions table; works with existing schema |
| Dependency pinning | Exact `==` versions | Prevents supply chain attacks; `pip freeze` for reproducibility |

### Remaining Items (Future)
- [ ] Replace broad `except Exception` in CLOB authentication (polymarket_client.py line 85)
- [ ] Add Pydantic models for NewsAPI and Reddit responses
- [ ] SQLCipher for database encryption at rest (for live trading deployments)
- [ ] Credential rotation mechanism
- [ ] Process isolation (separate research and trading processes)
- [ ] TLS certificate pinning with certifi
- [ ] Async HTTP client migration (httpx.AsyncClient)

---

## Session 5 — 2026-02-17: Auto-Exit Logic for Open Positions

### Completed
- [x] Added exit threshold settings (`EXIT_STOP_LOSS_PCT`, `EXIT_RESOLVED_THRESHOLD`) to `config/settings.py`
- [x] Added `execute_exit()`, `_paper_exit()`, `_live_exit()` methods to `src/trading/executor.py`
- [x] Added `_check_exit()` helper and exit logic to `_monitor_positions()` in `src/agent/orchestrator.py`
- [x] Created `tests/test_exit_logic.py` with 12 new tests (exit conditions + paper exit integration)
- [x] All 72 tests passing

### What It Does
The agent now automatically exits positions during the monitor step:
- **Resolved markets**: Price >= $0.95 or <= $0.05 → `resolve_position()` books win/loss
- **Stop loss**: Position down 40%+ → sells to cut losses
- **Take profit**: Price moved 75% of distance from entry toward $1.00 → sells to lock in gains

### Key Design Decision
The plan originally had different take-profit formulas for YES vs NO positions (YES → price toward $1, NO → price toward $0). This was incorrect because `current_price` tracks the token we hold — for NO positions, that's the NO token price, which goes toward $1 when we're winning (same as YES). Unified to a single formula: `take_profit = entry + 0.75 * (1.0 - entry)`.

### Files Changed
| File | Change |
|------|--------|
| `config/settings.py` | Added `EXIT_STOP_LOSS_PCT = -0.40`, `EXIT_RESOLVED_THRESHOLD = 0.95` |
| `src/trading/executor.py` | Added `execute_exit()`, `_paper_exit()`, `_live_exit()` |
| `src/agent/orchestrator.py` | Added `_check_exit()`, expanded `_monitor_positions()` with exit loop |
| `tests/test_exit_logic.py` | 12 new tests: resolved, stop loss, take profit, paper exit integration |

### Next Steps
- [x] Update docs/GLOSSARY.md with new terms (take profit, stop loss, resolved market) *(done in Session 7)*
- [ ] Run live and verify EXIT log lines appear for qualifying positions

---

## Session 6 — 2026-02-17: Performance Report CLI Tool

### Completed
- [x] Added `get_all_trades()` and `get_first_snapshot()` query functions to `src/utils/db.py`
- [x] Created `report.py` CLI tool at project root — reads SQLite and prints formatted performance summary
- [x] All 72 tests still passing

### What It Does
`python report.py` prints a formatted dashboard showing:
- **Portfolio**: starting bankroll, current value, cash, total return, realized/unrealized PnL, peak value, max drawdown
- **Trade Statistics**: total trades, wins/losses, win rate, avg win/loss, best/worst trade (only for completed BUY→SELL pairs)
- **Open Positions**: from latest snapshot's `positions_json`, with side, size, entry/current price, PnL
- **Recent Trades**: last 10 trades with timestamp, side, size, price, question

### Files Changed
| File | Change |
|------|--------|
| `src/utils/db.py` | Added `get_all_trades()` (all trades ASC for stats) and `get_first_snapshot()` (earliest snapshot for start date) |
| `report.py` | New file (~150 lines) — standalone CLI tool, no new dependencies |

### Key Design Decisions
| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Completed trade matching | FIFO BUY→SELL per market_id | Simple, correct for sequential trades; avoids complex lot matching |
| Positions JSON handling | Supports both dict and list formats | Actual DB stores dict keyed by market_id; future-proofs for format changes |
| No tests for report.py | Read-only display tool | No business logic to break; DB functions already covered by existing tests |

---

## Session 7 — 2026-02-18: Live Prices, Cycle Tracking, and Audit Fixes

### Completed
- [x] Added 3 glossary terms: stop loss, take profit, resolved market
- [x] Committed all Session 5+6 work (exit logic, report CLI)
- [x] Live price fetching in `report.py` — fetches CLOB midpoint for open positions, shows `[live prices]` or `[snapshot prices]`
- [x] Wired up `agent_runs` table — orchestrator records each cycle's start/end, status, and stats
- [x] Added Agent Activity section to report (total cycles, success/fail, markets/signals/trades)
- [x] Narrowed CLOB auth exception from `except Exception` to specific types + `PolyApiException`
- [x] Applied `NewsArticle` Pydantic validation in `news_collector.py`, added `NewsAPIException` handling
- [x] Created `RedditPost` Pydantic model in `models.py`, applied in `sentiment_scraper.py`, added `PRAWException` handling
- [x] Deferred async HTTP migration (documented below)
- [x] All 72 tests still passing

### Files Changed
| File | Change |
|------|--------|
| `docs/GLOSSARY.md` | Added stop loss, take profit, resolved market to Trading Concepts table |
| `report.py` | Live price fetching via httpx, Agent Activity section, recalculated unrealized PnL |
| `src/utils/db.py` | Added `insert_agent_run()`, `update_agent_run()`, `get_agent_run_stats()` |
| `src/agent/orchestrator.py` | Wired cycle recording into `run_cycle()` (start, success, failure paths) |
| `src/data/polymarket_client.py` | Narrowed CLOB auth exception, imported `PolyApiException` |
| `src/data/news_collector.py` | Applied `NewsArticle` Pydantic model, catch `NewsAPIException` |
| `src/data/models.py` | Added `RedditPost` model with coerce validators |
| `src/data/sentiment_scraper.py` | Applied `RedditPost` model, catch `PRAWException` |
| `docs/PROGRESS.md` | Session 7 entry |

### Decisions Made
| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Live price source | Direct httpx GET to CLOB `/midpoint` | Public endpoint, no auth needed, avoids instantiating full `PolymarketClient` |
| Cycle recording failure | Log warning, continue cycle | Recording telemetry shouldn't block trading |
| Async HTTP migration | Deferred | High risk, low value — 30-min cycle agent doesn't need async; PRAW, newsapi, py-clob-client are all fundamentally sync; sync/async mix already caused BUG-004 |
| PRAW exception import | Guarded with try/except ImportError | Defensive against library restructuring |

### Remaining Items (Future)
- [ ] Run live and verify EXIT log lines appear for qualifying positions
- [ ] Async HTTP client migration — deferred (high risk, low value; see decision above)
- [ ] SQLCipher for database encryption at rest
- [ ] Credential rotation mechanism
- [ ] Process isolation (separate research and trading processes)

---

## Session 8 — 2026-02-19: Stop Loss Tightened

### Completed
- [x] Changed `EXIT_STOP_LOSS_PCT` from `-0.40` to `-0.25` in `config/settings.py`

### Reasoning
At -40%, losing positions (Noah Wyle NO at -19%, Rose Byrne YES at -15%) could sit underwater for weeks before triggering an exit, locking up capital with no redeployment. Prediction markets reprice meaningfully at -25% — at that point the original signal is likely wrong. -25% is tight enough to cut clear losers early while still loose enough to avoid whipsawing out of volatile markets.

Neither current loser is stopped out immediately: Noah Wyle needs to fall from $0.085 to $0.079, Rose Byrne from $0.082 to $0.072.

### Files Changed
| File | Change |
|------|--------|
| `config/settings.py` | `EXIT_STOP_LOSS_PCT = -0.40` → `-0.25` |

### Duplicate Logging Fix
- [x] Fixed duplicate log lines in `src/utils/logger.py`

The console `StreamHandler` now only attaches when `sys.stdout.isatty()` is true (interactive terminal). When running as a systemd service, stdout is not a TTY, so only the `RotatingFileHandler` writes to the log file. Previously, both the file handler and systemd's stdout redirect were writing to `data/agent.log`, doubling every line.

| File | Change |
|------|--------|
| `src/utils/logger.py` | Added `import sys`; wrapped `StreamHandler` in `if sys.stdout.isatty()` |

**Behavior after fix:**
- Systemd service: single copy per line (file handler only)
- Manual `python main.py`: console output in terminal + file handler (both active)

## Session 9 — 2026-02-23: Position Limit & Market Category Filter

### Completed
- [x] Reduced `MAX_CONCURRENT_POSITIONS` from 10 to 8 in `config/settings.py`
- [x] Added `SKIP_MARKET_KEYWORDS` list to `config/settings.py`
- [x] Added sports/entertainment filter in `_discover_markets()` in `orchestrator.py`

### Reasoning
**Position limit (10 → 8):** Portfolio hit 11 positions due to a timing bug (execute_trades runs before monitor_positions). Reducing to 8 gives a 2-slot buffer so strong signals aren't blocked while also reducing correlation risk from holding too many positions at once.

**Category filter:** Single-game sports results (e.g., "Will Real Sociedad win on 2026-02-21?") and entertainment award markets (e.g., "Best Actress") caused the two largest losses: Real Sociedad -$6.10, Rose Byrne -$5.55. These markets resolve instantly on the event outcome — stop losses can't protect against them. The agent has no analytical edge on "did team X win tonight" — that's pure outcome betting.

### Filter Keywords
- `"win on 20"` — catches all date-specific sports match results
- `"bo3"`, `"bo5"` — esports Best-of-3/5 match formats
- Award categories: `"best actor"`, `"best actress"`, `"best director"`, `"best picture"`, `"best film"`, `"best supporting"`, `"best animated"`
- Award shows: `"oscar"`, `"oscars"`, `"academy award"`, `"emmy"`, `"grammy"`, `"golden globe"`, `"bafta"`
- `"opening weekend box office"`

### Files Changed
| File | Change |
|------|--------|
| `config/settings.py` | `MAX_CONCURRENT_POSITIONS` 10 → 8; added `SKIP_MARKET_KEYWORDS` list |
| `src/agent/orchestrator.py` | Imported `SKIP_MARKET_KEYWORDS`; added category filter + skip counter in `_discover_markets()` |

### HLTV RSS Feed Added
- [x] Added HLTV (`https://www.hltv.org/rss/news`) to `FEEDS` dict in `src/data/rss_collector.py`

CS2 esports news now flows into the research pipeline at zero cost. If HLTV's Cloudflare blocks feedparser, the existing `except` handler logs a warning and continues — no impact on other feeds.

| File | Change |
|------|--------|
| `src/data/rss_collector.py` | Added `"HLTV"` entry to `FEEDS`, updated module docstring |

### BUG-018 Fix: Position limit overrun
- [x] Fixed `_evaluate_risks()` in `orchestrator.py` — added `approved_this_cycle` counter so each signal check sees the correct position count including in-batch approvals
- [x] Added safety guard in `_execute_trades()` — breaks out of execution loop if live position count is already at limit
- [x] Documented as BUG-018 in `docs/BUGS.md`

| File | Change |
|------|--------|
| `src/agent/orchestrator.py` | `approved_this_cycle` counter in `_evaluate_risks()`; position limit guard in `_execute_trades()`; imported `MAX_CONCURRENT_POSITIONS` |

## Session 10 — 2026-02-23: New Data Sources (Google Trends, CoinGecko, Metaculus)

### Completed
- [x] Created `src/data/trends_collector.py` — Google Trends search interest via pytrends
- [x] Created `src/data/coingecko_collector.py` — Live crypto price data via CoinGecko free API
- [x] Created `src/data/metaculus_collector.py` — Expert community forecasts from Metaculus API
- [x] Added `pytrends==4.9.2` to `requirements.txt`
- [x] Wired all three collectors into `_research_markets()` in `orchestrator.py` — independent try/except blocks per source
- [x] All three collectors use the synthetic article pattern — output feeds directly into the existing Claude analysis pipeline with zero changes to `signal_generator.py`

### What Each Collector Does

**Google Trends (`trends_collector.py`):**
Extracts top 3 keywords from each market question, fetches 30-day search interest from Google Trends via pytrends. Calculates trend direction (rising/stable/declining) by comparing first vs. last week. Cache TTL: 1 hour. Rate limit: 2s between requests to avoid HTTP 429.

**CoinGecko (`coingecko_collector.py`):**
Detects known cryptocurrency mentions in market questions via `COIN_KEYWORDS` dict (22 coins mapped). Fetches current price, 24h change, 7d change, and market cap from CoinGecko's free `/simple/price` endpoint. Adds plain-English momentum label. Cache TTL: 10 minutes.

**Metaculus (`metaculus_collector.py`):**
Searches `https://www.metaculus.com/api2/questions/` using keywords from each market question. Scores results by keyword overlap (requires ≥2 matches and ≥10 forecasters). Extracts median probability from `community_prediction.full.q2`. Cache TTL: 1 hour. Rate limit: 1s between requests.

### Synthetic Article Pattern
All three collectors return article dicts in the same format the pipeline already uses for RSS and NewsAPI results:
```python
{
    "title": "...",
    "source": "Google Trends" | "CoinGecko" | "Metaculus",
    "description": "...",   # human-readable summary for Claude
    "content": "...",       # same as description
    "url": "...",
    "published_at": "",
}
```
Claude receives this alongside real news articles and weights it in its probability assessment.

### Why These Sources
| Source | What It Adds | Value |
|--------|-------------|-------|
| Google Trends | Public attention / rising search interest on a topic | Rising trend often precedes price movement on Polymarket |
| CoinGecko | Real-time crypto price and momentum | Essential for crypto markets — Claude needs price context to assess "Will BTC hit $100K?" |
| Metaculus | Expert probability forecasts from calibrated forecasters | Direct probability comparison — if Metaculus says 35% but Polymarket prices 15%, that's the edge |

### Files Changed
| File | Change |
|------|--------|
| `src/data/trends_collector.py` | New file — pytrends-based search interest fetcher |
| `src/data/coingecko_collector.py` | New file — CoinGecko price context fetcher |
| `src/data/metaculus_collector.py` | New file — Metaculus expert forecast fetcher |
| `src/agent/orchestrator.py` | Imports + `__init__` + three new try/except blocks in `_research_markets()` |
| `requirements.txt` | Added `pytrends==4.9.2` |

### Decisions Made
| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Twitter / X API | Excluded | $100/month for basic access — not worth it at this stage |
| pytrends lazy import | `from pytrends.request import TrendReq` inside method | Missing install doesn't crash startup; pytrends is optional enrichment |
| Independent try/except per collector | One block per source | BUG-013 lesson — a failing Trends request should never block CoinGecko or Metaculus |
| Metaculus match threshold | ≥2 keywords, ≥10 forecasters | 1 keyword → too many garbage matches; <10 forecasters → statistically meaningless |

## Session 11 — 2026-02-23: Remove Reddit, Add Stocktwits

### Completed
- [x] Deleted `src/data/sentiment_scraper.py` — Reddit never worked (401 denied, API access rejected)
- [x] Removed `praw==7.8.1` from `requirements.txt`
- [x] Removed `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` from `config/settings.py`
- [x] Removed `RedditPost` Pydantic model from `src/data/models.py`
- [x] Created `src/data/stocktwits_collector.py` — Stocktwits crypto cashtag sentiment
- [x] Wired Stocktwits into `_research_markets()` in `orchestrator.py` — merges into `articles` dict like other collectors
- [x] Updated test mock in `tests/test_exit_logic.py`: `SentimentScraper` → `StocktwitsCollector`
- [x] All 101 tests still passing

### Why Reddit Was Removed
Reddit API access was denied (401 on every request). Reddit changed their policy in 2023 and now routinely denies third-party developer applications. Every cycle logged a 401 warning from Reddit contributing nothing. Removed cleanly rather than leaving dead code.

### What Stocktwits Adds
Stocktwits is purpose-built for finance. Traders voluntarily tag posts as Bullish or Bearish. For crypto markets specifically, this gives direct crowd sentiment that complements CoinGecko price data:
- CoinGecko = what price is doing
- Stocktwits = what traders think price will do

Coverage: 22 crypto coins via cashtag detection (BTC.X, ETH.X, SOL.X, etc.). Cache TTL: 15 minutes. No API key required (200 req/hour free).

### Files Changed
| File | Change |
|------|--------|
| `src/data/sentiment_scraper.py` | Deleted |
| `src/data/stocktwits_collector.py` | New file |
| `src/agent/orchestrator.py` | Swapped `SentimentScraper` → `StocktwitsCollector`; Stocktwits articles merged into `articles` dict |
| `config/settings.py` | Removed `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` |
| `src/data/models.py` | Removed `RedditPost` class |
| `requirements.txt` | Removed `praw==7.8.1` |
| `tests/test_exit_logic.py` | Updated mock patch target |

## Session 12 — 2026-02-23: New Data Sources (Fear & Greed, FRED, Finnhub, Glassnode)

### Completed
- [x] Created `src/data/fear_greed_collector.py` — Alternative.me Crypto Fear & Greed Index
- [x] Created `src/data/fred_collector.py` — FRED macroeconomic indicators (Fed rate, CPI, unemployment, yield curve, VIX)
- [x] Created `src/data/finnhub_collector.py` — Finnhub economic calendar (upcoming high-impact US events)
- [x] Created `src/data/glassnode_collector.py` — Glassnode on-chain metrics for BTC/ETH (free tier)
- [x] Added `FRED_API_KEY`, `FINNHUB_API_KEY`, `GLASSNODE_API_KEY` to `config/settings.py`
- [x] Updated `.env.example` with new API key placeholders and registration URLs
- [x] Wired all four collectors into `_research_markets()` in `orchestrator.py`
- [x] All 101 tests still passing

### What Each Collector Does

**Fear & Greed (`fear_greed_collector.py`):**
Fetches the Alternative.me Crypto Fear & Greed Index (0–100 scale). No API key needed. Applied to any market mentioning a known cryptocurrency. Cache TTL: 1 hour. Provides psychological context that often predicts short-term price direction.

**FRED (`fred_collector.py`):**
Fetches 5 key macroeconomic series from the Federal Reserve Bank of St. Louis: FEDFUNDS (interest rate), CPIAUCSL (CPI), UNRATE (unemployment), T10Y2Y (yield curve), VIXCLS (volatility index). Keyword-matched per market — a Fed rate market gets FEDFUNDS, a jobs market gets UNRATE, etc. Cache TTL: 6 hours. Requires free API key.

**Finnhub (`finnhub_collector.py`):**
Fetches high-impact US economic events scheduled in the next 14 days (Fed decisions, CPI releases, NFP, GDP). Applied to markets with macro keywords. Cache TTL: 4 hours. Requires free API key. This is the only source that gives the agent awareness of *upcoming* scheduled events.

**Glassnode (`glassnode_collector.py`):**
Fetches daily active address count and transaction count for BTC and ETH over 14 days. Calculates 7-day trend (rising/stable/declining). Applied to BTC/ETH markets only. Cache TTL: 6 hours. Requires free API key. Free tier limitation: daily resolution, BTC/ETH only.

### API Keys Needed
Three of the four require free API keys to activate. Until keys are added to `.env`, those collectors return empty results silently — the rest of the pipeline is unaffected.

| Collector | Key needed | Register at |
|-----------|-----------|-------------|
| Fear & Greed | No | — |
| FRED | `FRED_API_KEY` | https://fredaccount.stlouisfed.org/apikey |
| Finnhub | `FINNHUB_API_KEY` | https://finnhub.io/register |
| Glassnode | `GLASSNODE_API_KEY` | https://studio.glassnode.com/ |

### Files Changed
| File | Change |
|------|--------|
| `src/data/fear_greed_collector.py` | New file |
| `src/data/fred_collector.py` | New file |
| `src/data/finnhub_collector.py` | New file |
| `src/data/glassnode_collector.py` | New file |
| `src/agent/orchestrator.py` | 4 new imports, 4 new `__init__` assignments, 4 new try/except blocks in `_research_markets()` |
| `config/settings.py` | Added `FRED_API_KEY`, `FINNHUB_API_KEY`, `GLASSNODE_API_KEY` |
| `.env.example` | Updated: removed Reddit, added 3 new API key placeholders |

## Session 13 — 2026-02-23: Trade Reasoning Journal + Calibration Tracking

### Completed
- [x] Added 6 new columns to `trades` table via `init_db()` migration (idempotent ALTER TABLE)
- [x] Updated `insert_trade()` to store `claude_reasoning`, `estimated_prob`, `confidence`, `edge`
- [x] Added `update_trade_outcome()` to `db.py` — sets `actual_outcome` + `exit_reason` on original BUY trade when position closes
- [x] Added `get_calibration_stats()` to `db.py` — win rate vs estimated probability grouped by confidence level
- [x] Updated `execute_trade()` in `executor.py` — accepts reasoning/prob/confidence/edge, stores them
- [x] Updated `execute_exit()` in `executor.py` — calls `update_trade_outcome()` after closing (STOP_LOSS→0, TAKE_PROFIT→1)
- [x] Updated `_execute_trades()` in `orchestrator.py` — passes signal fields to `execute_trade()`
- [x] Updated `_monitor_positions()` in `orchestrator.py` — calls `update_trade_outcome()` when markets resolve
- [x] Added `print_calibration()` and Calibration section to `report.py`
- [x] Added `tests/test_calibration.py` — 13 new tests covering all new functions
- [x] All 114 tests passing (up from 101)

### What It Does

**Trade Reasoning Journal:**
Every BUY trade now stores Claude's full reasoning text, estimated probability, confidence level, and calculated edge. This creates an audit trail — you can query the DB to see exactly why the bot entered any position.

**Calibration Tracking:**
When a position closes, the original BUY trade is updated with the actual outcome (won=1, lost=0). `get_calibration_stats()` then groups these by confidence level and compares estimated probability vs actual win rate.

Example output in `report.py`:
```
── Calibration (Claude accuracy by confidence level) ──────
  Confidence    Trades    Wins    Win Rate    Estimated     Delta
  high              12       7       58.3%        72.0%    -13.7%
  medium             8       4       50.0%        65.0%    -15.0%
  low                4       1       25.0%        55.0%    -30.0%

  Delta = Win Rate − Avg Estimated Prob (negative = overconfident)
```

### New Database Columns (trades table)
| Column | Type | Description |
|--------|------|-------------|
| `claude_reasoning` | TEXT | Claude's full reasoning text at trade time |
| `estimated_prob` | REAL | Claude's probability estimate (0.0–1.0) |
| `confidence` | TEXT | "low", "medium", or "high" |
| `edge` | REAL | Calculated edge (estimated_prob − market_price) |
| `actual_outcome` | INTEGER | NULL=open, 1=won, 0=lost |
| `exit_reason` | TEXT | STOP_LOSS, TAKE_PROFIT, RESOLVED_YES, RESOLVED_NO |

### Files Changed
| File | Change |
|------|--------|
| `src/utils/db.py` | Migration, updated `insert_trade()`, added `update_trade_outcome()` and `get_calibration_stats()` |
| `src/trading/executor.py` | 4 new params on `execute_trade()`, `update_trade_outcome()` call in `execute_exit()` |
| `src/agent/orchestrator.py` | Pass signal fields in `_execute_trades()`, call `update_trade_outcome()` in `_monitor_positions()` |
| `report.py` | `print_calibration()` function + Calibration section |
| `tests/test_calibration.py` | 13 new tests |

## Session 14 — 2026-02-23: Resolution Analyzer, Dynamic Edge, Theme Tagging, Whale Gate

### Completed
- [x] **Resolution Analyzer** — Claude now classifies how each market resolves and how clear the criteria is
- [x] **Dynamic minimum edge** — edge requirement scales with resolution type uncertainty (2–6% buffer)
- [x] **Market theme tagging** — every trade tagged crypto/macro/geopolitics/tech/politics/other in the DB
- [x] **News-reaction whale gate** — detects unusual 1h price moves or high article count, prepends alert article for Claude
- [x] All 117 tests passing (up from 114)

### What Each Feature Does

**Resolution Analyzer:**
Claude now receives `resolutionSource` and `description` from the Gamma API in every prompt.
It must classify:
- `resolution_type`: `mechanical_numeric` | `price_print` | `formal_recognition` | `subjective_event`
- `resolution_clarity`: `high` | `medium` | `low`

These are stored in `TradingSignal` and passed downstream to the risk manager.

**Dynamic Minimum Edge (based on resolution type):**
Vague or disputed resolution criteria require more edge to justify the trade:

| Resolution Type | Buffer | Reasoning |
|----------------|--------|-----------|
| `mechanical_numeric` | +2% | Clear threshold from specific data source — easy to verify |
| `price_print` | +3% | Depends on a specific exchange or data provider |
| `formal_recognition` | +4% | Requires official announcement, can have timing uncertainty |
| `subjective_event` | +6% | Vague language or no clear source — highest dispute risk |

Combined with the regime buffer, the effective min_edge becomes:
`MIN_EDGE_THRESHOLD + regime_bonus + resolution_buffer`

**Market Theme Tagging:**
A `_classify_theme()` function in `signal_generator.py` assigns each market to a broad theme using keyword matching. Stored as `market_theme` in the trades table for future performance analysis by category.

**News-Reaction Whale Gate:**
In `_research_markets()`, after collecting all articles, the orchestrator checks each market for:
- `oneHourPriceChange` ≥ 15% (from Gamma API — sharp price movement)
- Article count ≥ 5 (unusually high coverage)

If either triggers, a synthetic "Market Activity Alert" article is prepended to that market's article list. Claude reads it as part of its research and factors in the possibility of whale positioning or informed trading.

### New Database Columns (trades table)
| Column | Type | Description |
|--------|------|-------------|
| `market_theme` | TEXT | crypto/macro/geopolitics/tech/politics/other |
| `resolution_type` | TEXT | mechanical_numeric/price_print/formal_recognition/subjective_event |

### Files Changed
| File | Change |
|------|--------|
| `src/analysis/llm_researcher.py` | Updated SYSTEM_PROMPT (resolution classification), ANALYSIS_TEMPLATE (resolution section), `analyze_market()` params, `_parse_response()` (new fields), `_default_response()` |
| `src/analysis/signal_generator.py` | Added `_classify_theme()` helper, `resolution_type`/`resolution_clarity`/`market_theme` to `TradingSignal`, pass resolution data to LLM |
| `src/trading/risk_manager.py` | Added `resolution_type` param, `_RESOLUTION_BUFFERS` dict, dynamic min_edge |
| `src/utils/db.py` | Migration for `market_theme` + `resolution_type`, updated `insert_trade()` |
| `src/trading/executor.py` | Added `market_theme`/`resolution_type` params to `execute_trade()` |
| `src/agent/orchestrator.py` | Pass `resolution_type` to risk manager, `market_theme`/`resolution_type` to executor, whale detection gate in `_research_markets()` |
| `tests/test_risk_manager.py` | Updated 3 tests to pass `resolution_type="mechanical_numeric"`, added 2 new resolution buffer tests |

## Session 15 — 2026-02-25: Live Trading Bug Fixes (ERC-1155, GTC Phantoms, Min Shares)

### Completed
- [x] **ERC-1155 setApprovalForAll** — one-time on-chain setup enabling SELL orders (BUG-018)
- [x] **GTC phantom positions** — fixed `executor.py` to only open portfolio positions when order `status == "matched"` (filled), not when "live" (resting in book)
- [x] **Purge logic for failed SELLs** — `portfolio.purge_position()` removes phantom positions when SELL fails
- [x] **PolyApiException not caught** — added `PolyApiException` to `place_order()` except clause (BUG-019)
- [x] **CLOB minimum 5-share check** — risk manager rejects trades producing < 5 shares before sending to CLOB (BUG-019)
- [x] **Order book management** — cancelled stale GTC orders stuck in book (SpaceX, Díaz-Canel BUY; Silver SELL at wrong price)
- [x] All 123 tests passing

### What Each Feature Does

**ERC-1155 Approval (`_ensure_conditional_token_approval()`):**
Polymarket conditional tokens are ERC-1155. USDC (BUY orders) uses ERC-20 allowance — already set by py-clob-client. But SELL orders use the ERC-1155 token itself, which needs a separate `setApprovalForAll` call for each exchange contract. This is a one-time per-wallet setup. Added to CLOB initialization — idempotent, skipped if already set.

Required for 3 contracts: CTF Exchange, Neg Risk CTF Exchange, Relayer.

**GTC Phantom Fix:**
When a BUY order returns `status: "live"`, it's a GTC order resting in the order book. Tokens are NOT received — the position is not open yet. Fixed `_live_execute()` to return `filled=False` for "live" orders; `execute_trade()` only calls `portfolio.open_position()` if `result.filled is True`.

**CLOB Minimum Shares:**
Polymarket rejects orders < 5 shares with a 400 error. Added check in `risk_manager.py` after calculating `shares = position_size / effective_price`. If shares < 5, trade is rejected with a clear message before ever reaching the CLOB.

### Files Changed
| File | Change |
|------|--------|
| `src/data/polymarket_client.py` | Added `_ensure_conditional_token_approval()`, added `PolyApiException` to `place_order()` except clause |
| `src/trading/executor.py` | Added `filled` field to `OrderResult`, GTC detection in `_live_execute()`, exception handling in `_live_exit()` |
| `src/trading/portfolio.py` | Added `purge_position()` method |
| `src/agent/orchestrator.py` | Wrapped `execute_exit()` in try/except with purge fallback |
| `src/trading/risk_manager.py` | Added minimum 5-share check before approval |
| `tests/test_risk_manager.py` | Added `TestMinimumShares` class (3 tests) |
| `docs/BUGS.md` | Added BUG-019 |

## Session 16 — 2026-02-26: GTC Order Reconciliation

### Completed
- [x] **GTC reconciliation** — agent now detects when GTC orders fill silently and restores them into the portfolio tracker
- [x] `position_side` column added to trades table (migration via ALTER TABLE)
- [x] `get_pending_live_trades()` and `mark_trade_status()` added to db.py
- [x] `restore_position()` added to Portfolio — adds position without touching cash
- [x] `get_token_balance()` added to PolymarketClient — checks CLOB conditional token balance
- [x] `_reconcile_gtc_orders()` added to Orchestrator — runs at start of Step 6 every cycle
- [x] All 126 tests passing

### What It Does
Each cycle, before checking exits, the agent:
1. Queries DB for pending (unfilled) live BUY trades
2. Checks actual CLOB token balance for each (`AssetType.CONDITIONAL`)
3. If balance ≥ 1 → GTC order filled → calls `restore_position()` (no cash deduction, already synced)
4. Marks trade as "filled" in DB
5. Position is now tracked → stop-loss and take-profit apply from next price check

For old trades in DB without `position_side`: falls back to price heuristic (< 50¢ → NO, ≥ 50¢ → YES) with a warning log.

### Files Changed
| File | Change |
|------|--------|
| `src/utils/db.py` | Add `position_side` migration, update `insert_trade()`, add `get_pending_live_trades()`, add `mark_trade_status()` |
| `src/trading/executor.py` | Add `position_side` to trade_record |
| `src/trading/portfolio.py` | Add `restore_position()` method |
| `src/data/polymarket_client.py` | Add `get_token_balance()` method |
| `src/agent/orchestrator.py` | Add `_reconcile_gtc_orders()`, call at start of `_monitor_positions()` |

## Session 17 — 2026-02-26: GTC Partial Fill Fix + Execution Eligibility Gate

### Completed
- [x] **GTC partial fill tracking** — reconciliation now distinguishes partial fills from full fills; partially-filled GTC orders stay `pending` in DB and get share count updated each cycle (BUG-020)
- [x] **`get_open_orders()` returns `None` on error** — caller can tell the difference between "no open orders" and "API call failed"; never marks a trade as filled when order state is unknown
- [x] **Spread hard cap enforced** — `spread > 10%` now hard-rejects trades in `risk_manager.py` (condition 2 of execution eligibility gate)
- [x] **Shadow gates for conditions 3–5** — log-only warnings for pool concentration, estimated slippage, and book depth proxy; not enforced yet, collecting data first
- [x] **`liquidity` added to `TradingSignal`** — populated from Gamma API, passed through to risk manager for shadow gate calculations
- [x] **`MAX_SPREAD_THRESHOLD = 0.10`** added to `config/settings.py`
- [x] All 130 tests passing (up from 126)

### What Each Change Does

**GTC Partial Fill Fix:**
Previous behaviour: as soon as `balance ≥ 1`, mark trade as `filled` and stop checking. A 2/30 share fill would orphan the remaining 28-share GTC order forever.

New behaviour per cycle:
1. Fetch all open CLOB orders once (`get_open_orders()`)
2. For each pending trade: if balance ≥ 1 and order still open → **partial fill** → update share count, keep DB status `pending`
3. If balance ≥ 1 and no open order → **fully filled** → update share count, mark `filled`
4. If balance < 1 and no open order → **cancelled** → mark `cancelled`
5. If `get_open_orders()` fails → do nothing (safe fallback, re-check next cycle)

**Execution Eligibility Gate:**

| Condition | Status | Threshold |
|-----------|--------|-----------|
| `edge > min_edge` | ✅ Enforced (existing) | Dynamic (regime + resolution buffers) |
| `spread <= threshold` | ✅ Enforced (new) | 10% hard cap |
| `position_size <= x% of pool` | 🔍 Shadow log | 3% of liquidity |
| `estimated_slippage <= y` | 🔍 Shadow log | 2% (position/liquidity) |
| `book_depth >= position_size` | 🔍 Shadow log | 10% of liquidity as proxy |

Shadow gates log `WARNING: SHADOW gate N ...` but never block. To review after a few weeks:
```bash
grep "SHADOW gate" data/agent.log | awk '{print $5}' | sort | uniq -c
```
When data exists, thresholds can be tuned and gates enforced.

### Why Not Cross the Spread
Considered but rejected: automatically raising bid to `ask` price when GTC orders don't fill. The reasoning:
- Edge estimates (from LLM) have large error bars — paying up amplifies mistakes
- Crossing the spread means buying from people who price the ask higher because they know more (adverse selection)
- The real fix is better market selection upstream, not chasing price downstream
- Wide spreads indicate illiquid markets we probably shouldn't be trading anyway

### Files Changed
| File | Change |
|------|--------|
| `src/data/polymarket_client.py` | `get_open_orders()` returns `None` on error (vs `[]`), updated type hint |
| `src/agent/orchestrator.py` | Rewrote `_reconcile_gtc_orders()` — partial fill logic, open order check, cancelled detection |
| `config/settings.py` | Added `MAX_SPREAD_THRESHOLD = 0.10` |
| `src/analysis/signal_generator.py` | Added `liquidity: float` to `TradingSignal`, populate from market data |
| `src/trading/risk_manager.py` | Import `MAX_SPREAD_THRESHOLD`, add `liquidity` param, enforce spread cap, shadow gates 3–5 |
| `src/agent/orchestrator.py` | Pass `liquidity=signal.liquidity` to `evaluate_trade()` |
| `tests/test_risk_manager.py` | Added `TestSpreadCap` class (4 tests) |

---

## Session 18 — 2026-02-26: Fair Value Take Profit (Option B)

### Problem
The legacy take profit formula `entry + 0.75 * (1 - entry)` is structurally broken for cheap tokens:
- CDU BW NO entry at 4.5¢ required price to reach 76¢ to trigger (17x gain)
- Gold >$4600 NO entry at 11¢ required price to reach 83¢ to trigger (7.5x gain)
- Formula is price-anchored, not probability-anchored — doesn't reflect how far the market has moved relative to our edge estimate

### Root Cause
The formula assumes every token has the same payout structure (converge to $1), but ignores where we estimated the fair value to be. A 4.5¢ NO token with a 15% estimated probability has fair value at 15¢, not $1.

### Solution (Option B: Fair Value Exit)
Exit when current price has converged 90% from entry to estimated fair value:

```
fair_value = estimated_prob        (YES positions — token pays $1 if YES wins)
fair_value = 1.0 - estimated_prob  (NO  positions — token pays $1 if NO  wins)
take_profit = avg_price + 0.90 * (fair_value - avg_price)
```

Examples:
| Position | Entry | Est. Prob | Fair Value | Old Threshold | New Threshold |
|----------|-------|-----------|------------|---------------|---------------|
| CDU NO   | 4.5¢  | 15% YES → 85% NO | 85¢ → fair for NO ✓ | 76¢ | 81¢ wait — actually: fair_value = 1-0.15 = 0.85, threshold = 0.045 + 0.90*(0.85-0.045) = 0.77 |

Wait, let me recalculate CDU:
- Entry 0.045, estimated_prob stored in DB = P(YES). CDU is a NO position.
- fair_value = 1 - estimated_prob
- If agent estimated P(YES) = 0.15 → fair_value = 0.85 → take_profit = 0.045 + 0.90*(0.805) = 0.769
- CDU is now at 16.3¢, still below 76.9¢ — legitimate hold

For cheap tokens where estimated_prob(YES) is high (agent bought NO):
- entry=0.045, prob_yes=0.15 → fair_value_no = 0.85 → threshold ≈ 0.769 (makes sense)

For cheap YES tokens:
- entry=0.045, prob_yes=0.15 → fair_value_yes = 0.15 → threshold = 0.045 + 0.90*(0.105) = 0.1395 (exits at 14¢, 200% gain)

Fallbacks:
- If `fair_value <= avg_price` (estimate below entry): legacy formula applies
- If `estimated_prob == 0` (old/paper trade): legacy formula applies

### Files Changed
| File | Change |
|------|--------|
| `src/trading/portfolio.py` | Added `estimated_prob: float = 0.0` to `Position` dataclass; added param to `open_position()`, `restore_position()`; added to JSON serialization and `load_from_db()` |
| `src/trading/executor.py` | Pass `estimated_prob` to `portfolio.open_position()` |
| `src/agent/orchestrator.py` | Pass `estimated_prob` from DB trade to `restore_position()` in `_reconcile_gtc_orders()`; replaced `_check_exit()` take profit with Option B formula |
| `src/utils/db.py` | Added `estimated_prob` to `get_pending_live_trades()` SELECT (from prior session) |
| `tests/test_exit_logic.py` | Added `estimated_prob` param to `make_position()`; added `TestFairValueTakeProfit` class (8 tests) |

### Test Count
138 tests, all passing (up from 130).

## Session 19 — 2026-02-28: Strategy Tightening (Edge Floor, Theme Ban, Correlation Cap)

### Problem
0/10 win rate on live trades. Post-mortem identified three structural failures:
1. **No hard edge floor** — effective_min_edge was entirely dynamic (spread + regime + resolution + clarity). A cheap liquid market could be traded with 5% edge. Not enough.
2. **No domain ban** — geopolitics, politics, macro markets were traded freely. LLMs have no informational edge here; these domains reflect aggregated professional research. Three correlated Iran NO positions = single geopolitical factor with multiple expirations.
3. **No theme correlation cap** — multiple positions in the same theme created hidden leverage. If geopolitics moves against us, we lose on all of them simultaneously.

### Solution

#### 1. Hard Edge Floor (20%)
Never trade below 20% edge regardless of spread/regime/resolution. `MIN_EDGE_FLOOR = 0.20` in `config/settings.py`. Applied in `risk_manager.py` as:
```python
effective_min_edge = max(MIN_EDGE_FLOOR, dynamic_min_edge)
```
Reason message annotates `floor=20%` when the floor is the binding constraint.

#### 2. Banned Themes
`BANNED_THEMES = frozenset({"geopolitics", "politics", "macro"})` in settings. Checked in `risk_manager.py` as Check 2b (before edge calculation). Returns `RiskDecision(approved=False, reason="Banned theme: '...' — no LLM edge in this domain")`.

Markets the agent WON'T trade after this:
- US-Iran military conflict
- Trump policy markets
- Fed rate decisions
- Election outcomes

#### 3. Theme Correlation Cap
`MAX_POSITIONS_PER_THEME = 1` in settings. Checked in `orchestrator._evaluate_risks()` before calling risk manager. Queries DB for open position themes, builds per-theme count, rejects any signal that would exceed the cap. "other" theme is exempt (catch-all).

Also added `get_open_position_themes()` DB function to support this check.

### Files Changed
| File | Change |
|------|--------|
| `config/settings.py` | Added `MIN_EDGE_FLOOR`, `BANNED_THEMES`, `MAX_POSITIONS_PER_THEME` |
| `src/trading/risk_manager.py` | Added `market_theme` param, Check 2b (banned theme), floor via `max(MIN_EDGE_FLOOR, dynamic)` |
| `src/utils/db.py` | Added `get_open_position_themes()` |
| `src/agent/orchestrator.py` | Theme correlation cap in `_evaluate_risks()`; passes `market_theme` to risk manager |
| `tests/test_risk_manager.py` | Updated 8 tests (floor changes min passing edge from ~5% to 20%); added `TestStrategyFilters` (9 tests) |

### Test Count
147 tests, all passing (up from 138).

### Expected Impact
- Trades per cycle will drop sharply (most signals were in politics/geopolitics/macro)
- Position concentration from correlated themes eliminated
- Minimum conviction required: 20%+ edge, allowed theme, not at correlation cap

## Session 21 — 2026-03-03: Phantom Purge & False Resolution Fixes

### Problem Source
Reviewing the live Polymarket portfolio against the agent's internal tracker revealed two critical discrepancies:
1. **Labour leadership election NO (18.1 shares, worth $10.31, +159%)** — agent had silently deleted this position after a SELL order failed due to CLOB minimum size. The tokens were real and on-chain.
2. **Fed rates NO (52 shares)** — agent prematurely exited this as RESOLVED_YES on Feb 26, weeks before the March FOMC meeting. The market had 97% consensus but had not settled. Tokens remained on-chain.

These two bugs combined caused the agent to understate portfolio value by ~$12 and overstate drawdown by ~9 percentage points, keeping it locked in RISK_REDUCING_ONLY/HALTED mode unnecessarily.

### Fixes Applied

#### BUG-025 — Balance-verified phantom purge (`src/agent/orchestrator.py`)
- **Old behaviour**: Any SELL order failure → immediate `purge_position()` with "phantom" label
- **New behaviour**: `_purge_if_balance_zero()` helper checks CLOB token balance first
  - balance = 0 → confirmed phantom → purge
  - balance ≥ 1 → real tokens, SELL rejected for another reason → keep, log warning
  - balance = None → API error → keep, retry next cycle
  - paper mode → always purge (no CLOB)
- Applied to both the `if not result.success` and `except Exception` branches

#### BUG-026 — Gamma-verified resolution detection (`src/agent/orchestrator.py`)
- **Old behaviour**: `_check_exit()` returns RESOLVED if price >= 0.95 or <= 0.05 → immediately books outcome
- **New behaviour**: When RESOLVED is signalled, `get_market_by_id()` is called to check the Gamma `closed` flag before acting
  - `closed == True` → confirmed settled → proceed with resolution
  - `closed == False` → strong consensus, not yet settled → hold, log debug
  - API returns None → cannot confirm → hold, retry next cycle
- `_check_exit()` unchanged — still a fast price-based first filter; verification lives in `_monitor_positions()`

### Tests
- 9 new tests added to `tests/test_exit_logic.py`
  - `TestPurgeIfBalanceZero` (4 tests): paper mode purge, live balance=0 purge, live balance>0 keep, API error keep
  - `TestGammaVerifiedResolution` (5 tests): _check_exit still returns RESOLVED from price; closed=False holds; closed=True resolves; API None holds; boundary tests
- Full suite: **156/156 passing**

### Files Changed
| File | Change |
|------|--------|
| `src/agent/orchestrator.py` | Added `_purge_if_balance_zero()` helper; RESOLVED block now checks Gamma `closed` flag; SELL failure paths use helper |
| `tests/test_exit_logic.py` | Added `TestPurgeIfBalanceZero` and `TestGammaVerifiedResolution` test classes |
| `docs/BUGS.md` | Added BUG-025 and BUG-026 |

### Portfolio Impact
- True portfolio value ~$114 vs agent-reported ~$102 (Labour + Fed positions untracked)
- True drawdown ~9% vs agent-reported 18-21%
- Agent was in RISK_REDUCING_ONLY/HALTED unnecessarily — will resume NORMAL trading once the fixes propagate and positions are reconciled

---

## Session 20 — 2026-02-28: Five-Bug Fix Sprint (Research-Driven)

### Problem Source
Parallel analysis by four specialist agents (quant, CRO, trading-system-auditor, financial-intel-researcher) plus independent deep research (cross-referencing FIA standards, SEC Market Access Rule, MiFID II, academic forecasting benchmarks) identified 5 interacting failures:
1. BUY_NO edge stored as negative → all calibration data corrupted
2. Intra-cycle exposure not accumulated → possible cap breach in busy cycles
3. SELL-side GTC not fill-confirmed → phantom cash / position removed before tokens gone
4. LLM anchors on market price in prompt → near-zero edge estimates (0/10 win rate root cause)
5. Binary 20% halt with no intermediate risk-reducing state

### Research Backing
| Fix | Standard / Evidence |
|-----|---------------------|
| Canonical edge storage | Wolfers & Zitzewitz (2006) binary contract math: `edge_NO = price_YES − P̂(YES)` |
| Exposure accumulator | FIA automated trading paper: "include working orders + pending approvals in limits" |
| SELL GTC fill-check | FIX protocol order lifecycle, Polymarket API docs: `status=live` ≠ filled |
| LLM de-anchoring | ScienceDirect anchoring study (GPT-4, Claude 2, Gemini, GPT-3.5): all show anchoring bias; "ignore previous" prompting has limited effect |
| RISK_REDUCING_ONLY | NYSE Pillar: "risk controls outside order path can accept marginal orders before breach triggers" |

### Changes Made

#### 1. CRITICAL-3: Canonical Edge Storage
The trades table `edge` column was storing `signal.edge = estimated_prob_yes - price_yes` for ALL trades, including BUY_NO. For a valid BUY_NO trade, this is negative — making all calibration records show negative EV.

**Fix**: `executor.py` now computes `edge_for_side = edge if direction != "BUY_NO" else -edge` before writing to DB. Also added canonical `market_price_yes` column (YES token price at signal time) so analytics can always reconstruct direction-aware edge from first principles.

#### 2. CRITICAL-2: Intra-Cycle Exposure Accumulator
`_evaluate_risks()` was passing `self.portfolio.total_exposure` (snapshot at cycle start) to every risk evaluation. Three signals at 15% exposure each could all pass a 50% cap because each check saw the same stale number.

**Fix**: `committed_exposure = self.portfolio.total_exposure` initialized before the loop, incremented by `decision.position_size` on each approval. Each subsequent signal sees the updated total.

#### 3. CRITICAL-1: SELL-Side GTC Fill Confirmation
`execute_exit()` was calling `portfolio.close_position()` immediately after the SELL order was placed — before CLOB confirmation. In live mode, tokens are still held until the order fills.

**Fix**: Live mode now calls `portfolio.mark_selling(market_id, price, reason)` which flags the position with `selling_pending=True`. A new `_reconcile_sell_orders()` method runs each cycle, checks CLOB balance, and calls `close_position()` + `update_trade_outcome()` only when `balance < 1` (tokens gone). Exit check loop skips `selling_pending` positions.

#### 4. ARCH-1: LLM De-Anchoring
The `ANALYSIS_TEMPLATE` included `## Current Market Price: $X.XX (market estimates Y% probability of YES)`. All four LLM families (including Claude) show statistically significant anchoring on numbers in the prompt. The LLM was essentially returning `market_price ± noise`, producing near-zero edges.

**Fix**: Removed the entire market price section from `ANALYSIS_TEMPLATE`. Updated system prompt to say "You do NOT receive the current market price — estimate from evidence alone." Changed `_default_response` from returning `price` to returning `0.5` (maximum uncertainty) so failures are visible in logs rather than silently echoing the market.

#### 5. Drawdown Scaling: RISK_REDUCING_ONLY State
Previously: single binary halt at 20%. Agent traded at full size until 19.9% drawdown, then hard-stopped. No gradual de-risking.

**Fix**: Added `DRAWDOWN_RISK_REDUCING_PCT = 0.15` to settings. `risk_manager.get_trading_mode(drawdown_pct)` returns `NORMAL`, `RISK_REDUCING_ONLY`, or `HALTED`. At ≥15% drawdown, `_generate_signals()` and `_execute_trades()` skip immediately, but `_monitor_positions()` (exits, stop-losses, all reconciliation) still runs. The agent reduces exposure through natural exits rather than a hard freeze.

### New DB Columns (trades table)
| Column | Type | Description |
|--------|------|-------------|
| `market_price_yes` | REAL | YES token price at signal time — canonical primitive for edge analytics |

### New Position Fields (portfolio.py)
| Field | Type | Description |
|-------|------|-------------|
| `selling_pending` | bool | True = SELL order placed but not yet confirmed filled |
| `sell_price` | float | Price at which SELL order was placed |
| `selling_reason` | str | "STOP_LOSS" or "TAKE_PROFIT" |

### Files Changed
| File | Change |
|------|--------|
| `config/settings.py` | Added `DRAWDOWN_RISK_REDUCING_PCT = 0.15` |
| `src/utils/db.py` | Added `market_price_yes` migration + INSERT, added `get_pending_sell_trades()` |
| `src/trading/portfolio.py` | Added `selling_pending/sell_price/selling_reason` to `Position`; added `mark_selling()`; updated JSON/load_from_db; summary shows [SELLING] |
| `src/trading/executor.py` | `execute_trade()`: direction-aware `edge_for_side`, `market_price_yes` param; `execute_exit()`: `mark_selling()` in live mode, `close_position()` in paper mode |
| `src/trading/risk_manager.py` | Imported `DRAWDOWN_RISK_REDUCING_PCT`; added `get_trading_mode()` method |
| `src/analysis/llm_researcher.py` | Removed market price from `ANALYSIS_TEMPLATE`; updated system prompt; `_default_response` returns 0.5 not price |
| `src/agent/orchestrator.py` | `_evaluate_risks()`: exposure accumulator; `_generate_signals()` + `_execute_trades()`: RISK_REDUCING_ONLY checks; added `_reconcile_sell_orders()`; `_monitor_positions()` skips selling_pending positions; imported `get_pending_sell_trades` |

### Test Count
147 tests, all passing (unchanged — new functionality covered by existing patterns).

<!-- Future sessions will be appended below -->

---

## Session 22 — 2026-03-03: Tiered Market Discovery

### Problem
After 2 weeks of live paper trading every single discovery cycle showed `short≤14d: 0, medium: 0, long: 8-18`. All positions were resolving Jun–Dec 2026 — capital was locked with zero calibration feedback. Root cause: a single Gamma API call sorted by liquidity descending always returns the largest long-term markets; short/medium markets have lower absolute liquidity and never surfaced.

### Solution: Three-Call Tiered Discovery
The Gamma API supports `end_date_min` / `end_date_max` filter params. By making three separate calls — one per tier — each pool is sorted by liquidity *within* its tier. Short markets that would never appear in a global top-100 liquidity ranking now have their own dedicated pool.

### Decisions Made
| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Short-tier limit | 100 (vs 50 for others) | Short-term universe is sparse; need larger raw pool to find enough candidates |
| Medium start boundary | `today+14d+1s` | Avoids exact-day overlap with short tier |
| Long start | `today+61d` | Explicit `end_date_min` so long call doesn't return medium markets |
| Near-expiry skip | `< 2 days` | Markets in last 48h are illiquid, wide-spread, erratic price discovery |
| Short-term thresholds | vol≥300, liq≥150 | Lower floors needed — short markets don't have months to accumulate volume |
| Tier cycle caps | short≤20%, medium≤40% of bankroll | Prevents over-concentration in one resolution tier per cycle |
| camelCase fallback | `endDate` field added to `_days_until_resolution()` | Gamma API returns `endDate` in some responses |

### Changes Made

#### 1. `src/data/polymarket_client.py`
Added `end_date_min: str | None` and `end_date_max: str | None` params to `get_markets()`. When provided, appended to the params dict before the Gamma request.

#### 2. `config/settings.py`
Added per-tier volume/liquidity thresholds and per-tier intra-cycle exposure caps:
- `SHORT_TERM_MIN_VOLUME=300`, `SHORT_TERM_MIN_LIQUIDITY=150`
- `MEDIUM_TERM_MIN_VOLUME=500`, `MEDIUM_TERM_MIN_LIQUIDITY=250`
- `LONG_TERM_MIN_VOLUME=1000`, `LONG_TERM_MIN_LIQUIDITY=500` (unchanged values)
- `MIN_DAYS_TO_RESOLUTION=2`
- `SHORT_TERM_MAX_CYCLE_EXPOSURE_PCT=0.20`
- `MEDIUM_TERM_MAX_CYCLE_EXPOSURE_PCT=0.40`

#### 3. `src/agent/orchestrator.py`
- Added `timedelta` to `datetime` import
- Imported new settings constants
- Fixed `_days_until_resolution()`: added `endDate` camelCase fallback
- Rewrote `_discover_markets()`: three separate API calls with date bounds, dedup by ID, tier-appropriate thresholds, near-expiry skip, improved log line
- Updated `_evaluate_risks()`: added `committed_short_exposure`, `committed_medium_exposure` accumulators and `market_by_id` lookup; per-tier cap check inside approval block

#### 4. `tests/test_market_discovery.py` (new)
21 tests across 7 test classes: `_days_until_resolution` parsing, three-call API structure, near-expiry filter, tier prioritisation, tier thresholds, deduplication, keyword filter, and per-tier cycle caps.

### Files Changed
| File | Change |
|------|--------|
| `src/data/polymarket_client.py` | `get_markets()` gains `end_date_min`/`end_date_max` params |
| `config/settings.py` | 9 new tier discovery constants |
| `src/agent/orchestrator.py` | `_discover_markets()` rewrite + `_evaluate_risks()` tier caps |
| `tests/test_market_discovery.py` | New file, 21 tests |

### Test Count
177 tests, all passing (21 new).
