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

<!-- Future sessions will be appended below -->
