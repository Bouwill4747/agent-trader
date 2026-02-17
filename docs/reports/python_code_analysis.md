# Python Code Analysis Report

**Project**: Polymarket Autonomous Trading Agent
**Date**: 2026-02-15
**Analyst**: Claude Opus 4.6 (Automated Code Review)
**Files Reviewed**: 17 Python files (excluding `__init__.py` stubs and virtualenv)

---

## Summary

This is a well-structured codebase with clear separation of concerns, good docstrings, and a sensible architecture for an autonomous trading agent. However, the analysis uncovered several **critical financial safety bugs** (Kelly criterion applied to BUY_NO trades using wrong formula, risk manager ignoring negative edges, and missing `config/__init__.py`), **concurrency hazards** (synchronous blocking calls inside LangGraph async nodes, thread-unsafe rate limiter), and **error-handling gaps** that could cause the agent to lose money silently or halt unexpectedly. There are 7 critical issues, 10 high-severity warnings, and numerous medium/low suggestions detailed below.

---

## Critical Issues (7 findings)

### C-01: Risk Manager Does Not Handle BUY_NO Trades Correctly

**File**: `/home/will/agent_trader/src/trading/risk_manager.py`, lines 86-91
**File**: `/home/will/agent_trader/src/trading/risk_manager.py`, lines 117

**Problem**: The `evaluate_trade()` method calculates edge as `estimated_prob - market_price` and rejects trades where `edge < MIN_EDGE_THRESHOLD` (0.10). For BUY_NO signals, the edge is *negative* (e.g., -0.25), so the check `edge < 0.10` will always be `True`, rejecting every BUY_NO trade. Even if a BUY_NO trade somehow passed, the Kelly formula `edge / (1 - market_price)` computes a negative Kelly fraction, producing a negative position size.

The signal generator correctly identifies BUY_NO opportunities (line 133 of `signal_generator.py`: `elif edge < -MIN_EDGE_THRESHOLD`), but the risk manager has no awareness of trade direction.

**Impact**: **100% of BUY_NO trades are silently rejected.** The agent can only ever buy YES tokens, cutting its strategy in half. In markets where the YES outcome is overpriced, the agent does nothing instead of profiting from buying NO.

**Fix**:
```python
def evaluate_trade(self, estimated_prob, market_price, confidence,
                   bankroll, current_exposure, num_positions,
                   current_drawdown, direction="BUY_YES"):
    # ... earlier checks ...

    # Check 3: Calculate edge (direction-aware)
    if direction == "BUY_NO":
        # For BUY_NO: we profit when price goes DOWN
        # Edge is how overpriced YES is: market_price - estimated_prob
        edge = market_price - estimated_prob
        effective_price = 1 - market_price  # Price of NO token
    else:
        edge = estimated_prob - market_price
        effective_price = market_price

    if edge < MIN_EDGE_THRESHOLD:
        return RiskDecision(approved=False, ...)

    # Kelly using the effective price for the token we're buying
    kelly_raw = edge / (1 - effective_price)
    # ... rest of sizing ...
    shares = position_size / effective_price
```

---

### C-02: Synchronous Blocking Calls Inside Async LangGraph Nodes

**File**: `/home/will/agent_trader/src/agent/orchestrator.py`, lines 103-131 (`_discover_markets`, `_research_markets`)
**File**: `/home/will/agent_trader/src/agent/orchestrator.py`, lines 191-225 (`_evaluate_risks`)
**File**: `/home/will/agent_trader/src/agent/orchestrator.py`, lines 252-271 (`_monitor_positions`)

**Problem**: Several LangGraph nodes are defined as synchronous methods (`def` not `async def`) but they call synchronous I/O-bound operations: `self.client.get_markets()` (HTTP via httpx), `self.news.get_articles_for_markets()` (HTTP to NewsAPI), `self.sentiment.get_sentiment_for_markets()` (HTTP to Reddit), and `self.client.get_midpoint()` (HTTP to CLOB API). These methods also call `time.sleep()` inside the rate limiter.

Meanwhile, other nodes like `_generate_signals` and `_execute_trades` are `async def`. LangGraph's `ainvoke()` will run sync nodes in a thread executor, but the shared mutable state (rate limiter's `last_request_time`, portfolio positions) is not thread-safe.

Additionally, `_discover_markets` and `_research_markets` are sync functions, while `_generate_signals` is async. When LangGraph calls `ainvoke()`, the sync nodes get wrapped differently. This mixing creates subtle issues: the sync `_research_markets` will block the event loop's thread pool, stalling the entire 30-minute cycle for network I/O.

**Impact**: The event loop is blocked during HTTP calls to Polymarket, NewsAPI, and Reddit. In the worst case, rate limiter state corruption could cause API bans, and portfolio state corruption could cause double-counting positions.

**Fix**: Either make ALL nodes `async def` and use `httpx.AsyncClient` (ideal), or make ALL nodes synchronous and use `graph.invoke()` instead of `graph.ainvoke()`. Consistency is critical. The most pragmatic fix:

```python
# Change orchestrator to use invoke() (synchronous) consistently
async def run_cycle(self):
    # ...
    try:
        # Run in a thread to not block the sleep loop
        result = await asyncio.to_thread(self.graph.invoke, initial_state)
```

---

### C-03: Missing `config/__init__.py` -- Import May Fail

**File**: `/home/will/agent_trader/config/` (directory)

**Problem**: The `config/` directory has no `__init__.py` file. Every module in the project does `from config.settings import ...`. This works in Python 3.3+ due to implicit namespace packages, but only if the Python path is set correctly (i.e., CWD is the project root). If the agent is started from any other directory, or if it is installed as a package, the import will fail with `ModuleNotFoundError: No module named 'config'`.

**Impact**: The agent will crash on startup if invoked from any directory other than the project root. This also prevents the project from being installable via `pip install -e .`.

**Fix**: Create `/home/will/agent_trader/config/__init__.py` (empty file is sufficient).

---

### C-04: `setup_wallet.py` Corrupts `.env` When Credentials Already Have Values

**File**: `/home/will/agent_trader/setup_wallet.py`, lines 90-101

**Problem**: The `.env` update logic uses simple string replacement:
```python
env_content = env_content.replace(
    "POLYMARKET_API_KEY=",
    f"POLYMARKET_API_KEY={creds.api_key}"
)
```
If the user runs `setup_wallet.py` twice, or if the keys already have values, the replacement targets `POLYMARKET_API_KEY=<existing_value>` which does NOT match the pattern `POLYMARKET_API_KEY=`, so the replace does nothing and the old credentials are kept silently. Worse, if the key already contains a partial value like `POLYMARKET_API_KEY=old`, the replace produces `POLYMARKET_API_KEY=newold` -- silently concatenating new and old values.

**Impact**: Credential corruption. The agent may authenticate with garbage credentials and either fail silently or, worse, authenticate to the wrong account.

**Fix**: Use regex replacement or parse the `.env` file properly:
```python
import re
env_content = re.sub(
    r'^POLYMARKET_API_KEY=.*$',
    f'POLYMARKET_API_KEY={creds.api_key}',
    env_content,
    flags=re.MULTILINE
)
```

---

### C-05: RateLimiter Is Not Thread-Safe

**File**: `/home/will/agent_trader/src/data/polymarket_client.py`, lines 28-40

**Problem**: The `RateLimiter.wait()` method has a classic TOCTOU (time-of-check/time-of-use) race condition:
```python
elapsed = time.time() - self.last_request_time      # Check
if elapsed < self.min_interval:
    time.sleep(self.min_interval - elapsed)          # Wait
self.last_request_time = time.time()                  # Use
```
If two threads call `wait()` concurrently (which happens because LangGraph runs sync nodes in a thread pool -- see C-02), both can pass the check simultaneously, then both update `last_request_time`, resulting in two requests issued back-to-back without the intended delay.

**Impact**: API rate limit violations leading to temporary or permanent bans from Polymarket's API.

**Fix**: Add a threading lock:
```python
import threading

class RateLimiter:
    def __init__(self, requests_per_second: int):
        self.min_interval = 1.0 / requests_per_second
        self.last_request_time = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_request_time = time.time()
```

---

### C-06: `execute_trade` Uses Wrong Price for BUY_NO Orders

**File**: `/home/will/agent_trader/src/agent/orchestrator.py`, lines 234-244

**Problem**: In `_execute_trades`, the side is hardcoded to `"BUY"` and the price is `signal.current_price` (the YES token price). For BUY_NO signals, the agent should be buying the NO token, which trades at approximately `1 - current_price`. Using the YES price to buy NO shares results in completely wrong order pricing.

```python
side = "BUY"  # We always buy (YES or NO shares)
result = await self.executor.execute_trade(
    ...
    price=signal.current_price,  # This is the YES price, wrong for NO
    ...
)
```

Furthermore, `signal.token_id` always points to the YES token (extracted by `_get_token_id` which takes `ids[0]`). For BUY_NO orders, the NO token ID (`ids[1]`) should be used.

**Impact**: If BUY_NO trades ever pass the risk manager (currently they don't due to C-01, but if that's fixed first), the agent would place orders at the wrong price with the wrong token, either failing to fill or buying the wrong outcome.

**Fix**:
```python
for signal, decision in approved:
    side = "BUY"
    if signal.direction == "BUY_YES":
        price = signal.current_price
        token_id = signal.token_id  # YES token
    elif signal.direction == "BUY_NO":
        price = 1.0 - signal.current_price  # NO token price
        token_id = self._get_no_token_id(signal)  # Need NO token ID
    else:
        continue

    result = await self.executor.execute_trade(
        market_id=signal.market_id,
        token_id=token_id,
        ...
        price=price,
        risk_decision=decision,
    )
```

---

### C-07: Database Path Is Relative -- Breaks If CWD Changes

**File**: `/home/will/agent_trader/config/settings.py`, lines 58-60

**Problem**: All file paths are relative:
```python
DATABASE_PATH = "data/trades.db"
LOG_PATH = "data/agent.log"
KILL_SWITCH_PATH = "data/STOP"
```
If the process's current working directory is not the project root (e.g., when running via systemd, cron, or from a parent directory), all three paths resolve to the wrong location. The database would be created in an unexpected directory, the kill switch would not be found, and logs would be written elsewhere.

**Impact**: The kill switch (the most critical safety mechanism) could fail silently. The agent would not see `data/STOP` and would continue trading despite the operator trying to halt it.

**Fix**:
```python
import os

# Anchor all paths to the project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASE_PATH = os.path.join(_PROJECT_ROOT, "data", "trades.db")
LOG_PATH = os.path.join(_PROJECT_ROOT, "data", "agent.log")
KILL_SWITCH_PATH = os.path.join(_PROJECT_ROOT, "data", "STOP")
```

---

## High-Severity Warnings (10 findings)

### H-01: Portfolio State Is Purely In-Memory -- Lost on Crash

**File**: `/home/will/agent_trader/src/trading/portfolio.py`
**File**: `/home/will/agent_trader/src/agent/orchestrator.py`, line 64

**Problem**: The `Portfolio` object is initialized fresh in `Orchestrator.__init__()` with the configured bankroll. All position data, cash balance, realized PnL, and peak bankroll are stored only in memory. If the agent crashes (OOM, power failure, unhandled exception), all portfolio state is lost. On restart, the agent starts with a fresh portfolio and no knowledge of existing positions, potentially opening duplicate positions or exceeding exposure limits.

The database stores `portfolio_snapshots` and `trades`, but there is no code to *reconstruct* the portfolio from these tables on startup.

**Impact**: After a crash, the agent has no memory of its positions. With live trading, this means it could place duplicate orders and exceed all risk limits.

**Fix**: Add a `Portfolio.load_from_db()` class method that reconstructs state from the `trades` and `portfolio_snapshots` tables at startup.

---

### H-02: `PolymarketClient` Lacks CLOB Credential Setting

**File**: `/home/will/agent_trader/src/data/polymarket_client.py`, lines 56-69

**Problem**: The `PolymarketClient.__init__()` calls `self.clob.create_or_derive_api_creds()` which generates credentials by signing a message. This operation hits the network and if it fails (e.g., network timeout, Polymarket API outage), the entire CLOB client is set to `None`. However, the `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, and `POLYMARKET_API_PASSPHRASE` settings from `config/settings.py` are never used. They are loaded from `.env` but never passed to the client.

**Impact**: The credentials derived by `setup_wallet.py` and saved to `.env` are never utilized. Every startup requires re-derivation from the private key, which is an unnecessary network call that can fail.

**Fix**: If API credentials exist in settings, use `self.clob.set_api_creds()` with those stored credentials instead of re-deriving every time:
```python
from config.settings import POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE

if POLYMARKET_API_KEY and POLYMARKET_API_SECRET:
    from py_clob_client.clob_types import ApiCreds
    creds = ApiCreds(
        api_key=POLYMARKET_API_KEY,
        api_secret=POLYMARKET_API_SECRET,
        api_passphrase=POLYMARKET_API_PASSPHRASE,
    )
    self.clob.set_api_creds(creds)
else:
    creds = self.clob.create_or_derive_api_creds()
    self.clob.set_api_creds(creds)
```

---

### H-03: `Executor._live_execute` Assumes Fill at Requested Price

**File**: `/home/will/agent_trader/src/trading/executor.py`, lines 141-176

**Problem**: When placing a live order, `_live_execute` returns `fill_price=price` (the requested price) and `fill_size=shares` even though the order may be a limit order that has not yet filled. The `status` in the database is set to `"pending"` (line 100), but the portfolio is immediately updated with these assumed values (lines 106-114). The response from `place_order` is checked only for truthiness, not for actual fill information.

**Impact**: The portfolio will show positions at prices that may never fill. Cash balance will be decremented for orders that are still pending. Subsequent risk decisions will use inaccurate portfolio data, potentially allowing over-exposure.

**Fix**: For live orders, do not immediately update the portfolio. Instead, track pending orders and update portfolio only upon confirmed fill. At minimum, add order status tracking.

---

### H-04: `generate_signal` Calls Synchronous Claude API in Async Context

**File**: `/home/will/agent_trader/src/analysis/signal_generator.py`, line 100-107
**File**: `/home/will/agent_trader/src/analysis/llm_researcher.py`, lines 118-124

**Problem**: `SignalGenerator.generate_signal()` is a synchronous method that calls `self.researcher.analyze_market()`, which in turn calls `self.client.messages.create()` -- a synchronous HTTP request to the Anthropic API. This is called from `generate_signals()` (async method, line 181) in a loop over all markets, sequentially. Each Claude API call can take 5-30 seconds, and with 10 markets, the signal generation step alone could take 5+ minutes, all blocking.

**Impact**: The entire 30-minute cycle budget is consumed by sequential, synchronous Claude API calls. During this time, no other processing occurs.

**Fix**: Use the Anthropic async client (`anthropic.AsyncAnthropic`) and `await client.messages.create()`, or use `asyncio.to_thread()` to run the synchronous calls in parallel:
```python
import asyncio

async def generate_signals(self, markets, articles_by_market, sentiment_by_market):
    tasks = []
    for market in markets:
        task = asyncio.to_thread(self.generate_signal, market, articles, posts)
        tasks.append(task)
    signals = await asyncio.gather(*tasks)
```

---

### H-05: FinBERT `analyze_batch` Can OOM on Large Batches

**File**: `/home/will/agent_trader/src/analysis/finbert_analyzer.py`, lines 87-131

**Problem**: `analyze_batch()` tokenizes and runs inference on the entire `texts` list at once. If a market has many news articles (say 50 articles x 2 texts each = 100 texts) plus Reddit posts (potentially 25 subreddits x 5 posts = 125 texts), the batch could be 200+ texts of varying length, all padded to 512 tokens. This creates a tensor of shape `[200+, 512]` which, with FinBERT's ~110M parameters, could consume several GB of GPU/CPU memory.

**Impact**: Out-of-memory crash, especially on machines without GPU. The agent would terminate without cleanup.

**Fix**: Process in fixed-size mini-batches:
```python
def analyze_batch(self, texts: list[str], batch_size: int = 16) -> list[dict]:
    results = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        # ... tokenize and run inference on batch ...
        results.extend(batch_results)
    return results
```

---

### H-06: Signal Generator Uses `import json` Inside Method Bodies

**File**: `/home/will/agent_trader/src/analysis/signal_generator.py`, lines 213, 239

**Problem**: `_get_market_price()` and `_get_token_id()` contain `import json` statements inside the method body. While Python caches imports so this is not a performance issue per se, it is a code smell and deviates from PEP 8 conventions. More importantly, it hides the dependency and makes it easy to miss during refactoring.

**Impact**: Low direct impact (Python caches), but indicates copy-paste development. The `json` import should be at the top of the file.

**Fix**: Move `import json` to the top of `/home/will/agent_trader/src/analysis/signal_generator.py`.

---

### H-07: No Retry Logic for Any External API Call

**Files**: `/home/will/agent_trader/src/data/polymarket_client.py`, `/home/will/agent_trader/src/data/news_collector.py`, `/home/will/agent_trader/src/data/sentiment_scraper.py`, `/home/will/agent_trader/src/analysis/llm_researcher.py`

**Problem**: Every API call (Polymarket Gamma, Polymarket CLOB, NewsAPI, Reddit, Claude) has a single try/except that catches the error and returns a default (empty list, None, etc.). There is no retry logic. `tenacity` is listed in `requirements.txt` but never used anywhere in the codebase. A single transient network timeout causes the entire market's data to be lost for that cycle.

**Impact**: Transient failures (DNS hiccups, 429 rate limits, 503 service unavailable) cause entire analysis cycles to run with missing data, leading to poor trading decisions.

**Fix**: Use tenacity for retries on all external calls:
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
)
def get_markets(self, limit=50, active=True):
    ...
```

---

### H-08: `httpx.Client` Never Closed in Error Paths

**File**: `/home/will/agent_trader/src/data/polymarket_client.py`, line 48

**Problem**: The `httpx.Client` (line 48, `self.gamma`) is created in `__init__` but only closed in `close()`. If an exception is raised during initialization (e.g., CLOB auth fails and some other error occurs), the httpx client is leaked. Additionally, `PolymarketClient` does not implement `__enter__`/`__exit__` for context manager support.

**Impact**: HTTP connection pool leaks. Over many failed initialization attempts (e.g., in tests), file descriptors accumulate.

**Fix**: Implement `__del__` or `__enter__`/`__exit__`:
```python
def __del__(self):
    self.close()
```

---

### H-09: Orchestrator Catches and Swallows All Exceptions in `run_cycle`

**File**: `/home/will/agent_trader/src/agent/orchestrator.py`, lines 305-314

**Problem**: The `run_cycle()` method wraps the entire `graph.ainvoke()` in a bare `except Exception as e` that only logs the error. Similarly, each graph node wraps its logic in try/except and returns empty data. This means that severe errors (e.g., database corruption, authentication failures) are silently swallowed, and the agent continues to the next cycle with empty data, potentially missing critical issues.

There is no escalation mechanism. If the same error occurs every cycle (e.g., bad API key), the agent runs forever doing nothing useful, consuming API quota for Claude calls.

**Impact**: Silent failure loop. The agent appears to be running but accomplishes nothing. No alerts, no escalation.

**Fix**: Add error counting and escalation:
```python
self._consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 5

async def run_cycle(self):
    try:
        result = await self.graph.ainvoke(initial_state)
        self._consecutive_errors = 0
    except Exception as e:
        self._consecutive_errors += 1
        logger.error("Cycle failed (%d consecutive): %s",
                     self._consecutive_errors, e)
        if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            logger.critical("Too many consecutive failures -- halting agent")
            break
```

---

### H-10: Tests Use Deprecated `asyncio.get_event_loop().run_until_complete()`

**File**: `/home/will/agent_trader/tests/test_executor.py`, lines 72, 93, 112, 141

**Problem**: The tests use `asyncio.get_event_loop().run_until_complete()` which has been deprecated since Python 3.10 and emits `DeprecationWarning` in Python 3.12. In Python 3.12+, calling `get_event_loop()` when no current event loop exists raises a `DeprecationWarning` and will become an error in a future version.

**Impact**: Tests may fail on newer Python versions. Already emits warnings on Python 3.12.

**Fix**: Use `pytest-asyncio` and `async def` test methods, or use `asyncio.run()`:
```python
# Option A: pytest-asyncio (recommended)
import pytest

@pytest.mark.asyncio
async def test_paper_trade_succeeds(self, executor):
    result = await executor.execute_trade(...)

# Option B: asyncio.run()
def test_paper_trade_succeeds(self, executor):
    result = asyncio.run(executor.execute_trade(...))
```

---

## Medium-Severity Suggestions (12 findings)

### M-01: `NewsCollector.cache` Grows Without Bound

**File**: `/home/will/agent_trader/src/data/news_collector.py`, line 29

**Problem**: The `self.cache` dictionary grows indefinitely. Expired entries are never removed -- the TTL check only prevents reading stale data but the stale entries remain in memory. Over hours/days of running, this accumulates thousands of cache entries.

**Fix**: Evict expired entries periodically, or use `functools.lru_cache`, or add a max size:
```python
def _clean_cache(self):
    now = time.time()
    expired = [k for k, (t, _) in self.cache.items() if now - t >= self.cache_ttl]
    for k in expired:
        del self.cache[k]
```

---

### M-02: `_parse_response` Does Not Handle All Markdown Fence Styles

**File**: `/home/will/agent_trader/src/analysis/llm_researcher.py`, lines 164-176

**Problem**: The code strips markdown fences by checking `startswith("```")` and removing the first and last lines. This fails if Claude returns:
- Code fences with language tag: ` ```json\n{...}\n``` ` (this actually works)
- Trailing whitespace after closing fence
- Text before or after the fenced block (e.g., "Here's my analysis:\n```json\n...")
- Multiple code blocks

**Fix**: Use regex for more robust extraction:
```python
import re
json_match = re.search(r'\{[\s\S]*\}', text)
if json_match:
    cleaned = json_match.group()
```

---

### M-03: `Executor` Side Detection Logic Is Fragile

**File**: `/home/will/agent_trader/src/trading/executor.py`, line 106

**Problem**:
```python
position_side = "YES" if "YES" in side.upper() or side.upper() == "BUY" else "NO"
```
The side parameter from the orchestrator is always `"BUY"` (hardcoded at orchestrator line 235). So this expression always evaluates to `"YES"` regardless of whether the signal was BUY_YES or BUY_NO. This means even if BUY_NO trades were implemented, the portfolio would record them as YES positions.

**Fix**: Pass the signal direction explicitly to the executor and use it to determine the position side.

---

### M-04: `Portfolio.open_position` Returns `None` on Insufficient Cash (Not `False`)

**File**: `/home/will/agent_trader/src/trading/portfolio.py`, lines 72-76

**Problem**: When cash is insufficient, `open_position` logs a warning and implicitly returns `None` (not `False`). The docstring and the explicit `return True` at line 106 suggest it should return a boolean, but the insufficient-cash path has no `return` statement. Actually, upon re-reading: there IS a `return False` at line 77. However, the caller in `executor.py` (lines 107-114) never checks the return value of `portfolio.open_position()`. If the portfolio rejects the position (insufficient cash), the executor still reports `success=True` and records the trade in the database.

**Impact**: The database shows a successful trade, but the portfolio did not actually open the position. State divergence.

**Fix**: Check the return value:
```python
# In executor.py, after the DB insert:
opened = self.portfolio.open_position(...)
if not opened:
    logger.error("Portfolio rejected position -- insufficient cash")
    # Consider updating the result
```

---

### M-05: `init_db()` Called Every Cycle Instead of Once at Startup

**File**: `/home/will/agent_trader/src/agent/orchestrator.py`, line 291

**Problem**: `await init_db()` is called inside `run_cycle()`, meaning it runs every 30 minutes. While `CREATE TABLE IF NOT EXISTS` is idempotent, it still opens a database connection, executes 4 DDL queries, commits, and closes. This is unnecessary overhead on every cycle.

**Fix**: Move the `init_db()` call to `Orchestrator.__init__()` or to the start of `run()`, outside the loop:
```python
async def run(self):
    await init_db()  # Once at startup
    while True:
        if self.risk.check_kill_switch():
            break
        await self.run_cycle()
        await asyncio.sleep(CYCLE_INTERVAL_SECONDS)
```

---

### M-06: `get_aggregate_sentiment` Weight Is Biased Toward Confidence, Not Toward "Positive"

**File**: `/home/will/agent_trader/src/analysis/finbert_analyzer.py`, lines 153-167

**Problem**: The weighting scheme uses `result["confidence"]` as the weight. Since confidence is defined as `scores[best_label]` (the probability of the winning label), a text classified as "neutral" with 95% confidence has a weight of 0.95, while a text classified as "positive" with 60% confidence has a weight of 0.60. This means high-confidence neutral texts dominate the weighted average, pulling the aggregate toward 0.0 regardless of the positive/negative texts.

This may or may not be the intended behavior. If the goal is "more confident predictions should count more," it is correct. But the docstring says "confidence -- high-confidence scores matter more than uncertain ones," which is ambiguous.

**Impact**: Neutral-dominated text sets (common in financial news) produce aggregate scores very close to 0.0, making sentiment almost irrelevant to the blending formula.

**Fix**: Consider using `1.0 - result["neutral"]` as weight instead, so only positive/negative opinions contribute meaningfully. Or document the current behavior explicitly.

---

### M-07: `search_markets` Client-Side Filter Is Ineffective

**File**: `/home/will/agent_trader/src/data/polymarket_client.py`, lines 124-146

**Problem**: `search_markets()` fetches up to `limit` markets (default 10) and then filters client-side. The small limit means most matching markets may be outside the first 10 results. For example, searching "Bitcoin" with `limit=10` fetches 10 markets sorted by whatever Gamma's default is, then filters -- potentially returning 0 results even though many Bitcoin markets exist.

**Fix**: Either increase the limit significantly for search (e.g., `limit=200`), or iterate pages:
```python
def search_markets(self, query: str, limit: int = 10) -> list:
    # Fetch more markets to search through
    response = self.gamma.get(
        "/markets",
        params={"limit": 200, "active": True, "closed": False},
    )
    # ... filter ... then return filtered[:limit]
```

---

### M-08: `LLMResearcher` Uses Hardcoded Model Name

**File**: `/home/will/agent_trader/src/analysis/llm_researcher.py`, line 120

**Problem**: The model ID `"claude-sonnet-4-5-20250929"` is hardcoded. If this model is deprecated or the user wants to use a different model, they must modify source code. This should be configurable.

**Fix**: Add `LLM_MODEL` to `config/settings.py`:
```python
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929")
```

---

### M-09: Mutable Default Argument Pattern (Mild)

**File**: `/home/will/agent_trader/src/analysis/llm_researcher.py`, line 82

**Problem**: `articles: list[dict] = None` is fine (None is immutable), but the function later does `articles or []` which creates a new list each time. This is the correct pattern. No bug here, but noted for completeness -- the default is safe.

*No fix needed -- this is actually handled correctly.*

---

### M-10: `PolymarketClient.close()` Does Not Close CLOB Client

**File**: `/home/will/agent_trader/src/data/polymarket_client.py`, lines 272-275

**Problem**: `close()` only closes the Gamma httpx client but does not close or cleanup the CLOB client. If the CLOB client holds open connections or websockets, they leak.

**Fix**:
```python
def close(self):
    self.gamma.close()
    # ClobClient may not have a close method -- check SDK docs
    self.clob = None
    logger.info("Polymarket client connections closed")
```

---

### M-11: Gamma API Rate Limiter Shared With CLOB Operations

**File**: `/home/will/agent_trader/src/data/polymarket_client.py`, line 84

**Problem**: `get_markets()` uses `self.clob_limiter.wait()` (20 req/s) but it calls the Gamma API, not the CLOB API. According to the project docs, Gamma has no documented rate limit, and the CLOB has 20 req/s. Using the CLOB rate limiter for Gamma calls means Gamma requests share the rate limit budget with CLOB trading requests, unnecessarily throttling market discovery.

**Fix**: Create a separate limiter for Gamma or don't limit Gamma calls (it's a public API):
```python
self.gamma_limiter = RateLimiter(10)  # Conservative for public API
```

---

### M-12: `_discover_markets` Filters by Volume/Liquidity But Values May Be Strings

**File**: `/home/will/agent_trader/src/agent/orchestrator.py`, lines 113-114

**Problem**: The code does `float(market.get("volume", 0) or 0)`. The `or 0` is needed because the Gamma API sometimes returns `None` or empty string for these fields. This is handled correctly. However, if the API returns a non-numeric string (e.g., `"N/A"`), `float()` will raise `ValueError` and the entire `_discover_markets` node will fail.

**Fix**: Wrap in try/except:
```python
try:
    volume = float(market.get("volume", 0) or 0)
    liquidity = float(market.get("liquidity", 0) or 0)
except (ValueError, TypeError):
    continue
```

---

## Low-Severity Observations (8 findings)

### L-01: Missing Type Annotations on Several Methods

**Files**: Various

Methods like `PolymarketClient.get_markets()`, `NewsCollector.get_articles()`, and `SentimentScraper.scrape_subreddit()` have return types in docstrings but not in function signatures. Adding them improves IDE support and catches type errors early.

### L-02: No `conftest.py` for Shared Test Fixtures

**File**: `/home/will/agent_trader/tests/`

The `risk` fixture is duplicated between test files. A `conftest.py` would allow sharing fixtures.

### L-03: Inconsistent Error Return Types

Some methods return `None` on error (e.g., `get_market_by_id`), some return `[]` (e.g., `get_markets`), some return `{}`, and some return `False`. Consider standardizing on a `Result` pattern or raising exceptions with a consistent retry wrapper.

### L-04: `POLYGON_CHAIN_ID` Imported But `POLYGON` Constant Also Used

**File**: `/home/will/agent_trader/src/data/polymarket_client.py`, line 13 vs. line 61

The `POLYGON` constant from `py_clob_client.constants` is imported but only used in `setup_wallet.py`. In `polymarket_client.py`, `POLYGON_CHAIN_ID` (137) is used. Both resolve to the same value. This redundancy could lead to divergence.

### L-05: Logger Names Not Consistent With Module Paths

Logger names like `"polymarket_client"`, `"database"`, `"executor"` don't match the module path structure (`src.data.polymarket_client`). Using `__name__` would be more standard and easier to filter.

### L-06: No `__all__` Exports in Any Module

No module defines `__all__`, making it unclear what the public API is for each module.

### L-07: `dataclass` Used Without `frozen=True` or `slots=True`

**Files**: `risk_manager.py`, `executor.py`, `signal_generator.py`, `portfolio.py`

The dataclasses (`RiskDecision`, `OrderResult`, `TradingSignal`, `Position`) are mutable. For value objects like `RiskDecision` and `OrderResult`, `frozen=True` would prevent accidental mutation.

### L-08: Paper Trade Order IDs Can Collide

**File**: `/home/will/agent_trader/src/trading/executor.py`, line 125

```python
paper_id = f"PAPER-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{market_id[:8]}"
```

If two trades happen in the same second for markets with the same first 8 characters, the IDs collide. Use a UUID or add a counter.

---

## Security Findings (5 findings)

### S-01: API Credentials Logged in Plain Text (Low Severity)

**File**: `/home/will/agent_trader/setup_wallet.py`, lines 79-81

The setup script prints API key, secret, and passphrase to the terminal in plain text. If terminal logging is enabled (e.g., `script` command, tmux logging), credentials are persisted to disk.

**Remediation**: Mask credentials in output:
```python
print(f"    API Key:        {creds.api_key[:8]}...{creds.api_key[-4:]}")
```

### S-02: Private Key Loaded Into Process Memory (Informational)

**File**: `/home/will/agent_trader/config/settings.py`, line 15

`POLYGON_PRIVATE_KEY` is loaded as a global string and persists in process memory for the entire runtime. This is unavoidable for signing transactions, but it means a memory dump or debug session could expose the private key.

**Remediation**: Consider loading the key only when needed and clearing it after use, though this is difficult in Python due to string immutability. At minimum, ensure the process runs with minimal privileges and core dumps are disabled.

### S-03: No Input Validation on Market Data From API

**Files**: `/home/will/agent_trader/src/analysis/signal_generator.py`, `/home/will/agent_trader/src/agent/orchestrator.py`

Market data from the Gamma API is used directly without schema validation. A malicious or buggy API response could inject unexpected types or values. For example, an `outcomePrices` field with extra elements, or a `volume` field containing a JavaScript injection string, would pass through unchecked.

**Remediation**: Add Pydantic models for API response validation (Pydantic is already in requirements.txt but unused).

### S-04: SQLite Injection Not a Risk (Positive Finding)

**File**: `/home/will/agent_trader/src/utils/db.py`

All SQL queries use parameterized queries with `?` placeholders. No string formatting is used in SQL construction. This is correct and safe.

### S-05: `.env` File Should Be in `.gitignore`

**File**: `/home/will/agent_trader/.gitignore`

Need to verify `.env` is in `.gitignore`. If not, credentials could be committed to version control. (The `.env.example` file correctly does NOT contain real credentials.)

---

## Positive Observations

1. **Consistent error handling pattern**: Every external API call is wrapped in try/except with logging. While retry logic is missing (H-07), the defensive approach prevents cascading failures.

2. **Good docstrings**: Nearly every class and method has a clear docstring explaining purpose, parameters, and return values. The learning-project context is well-served by this documentation quality.

3. **Safe PAPER_TRADING default**: The default is `true`, requiring explicit opt-in for live trading. The executor also falls back to paper mode if CLOB credentials are unavailable.

4. **Kill switch mechanism**: The `data/STOP` file approach is simple and effective (modulo the relative path issue in C-07). It is checked at the top of each cycle.

5. **Parameterized SQL**: All database queries use safe parameterized queries, preventing SQL injection.

6. **Kelly criterion with safety margins**: Using 0.25x Kelly fraction with a 5% per-position cap and 50% total exposure limit is a conservative, textbook approach to position sizing.

7. **Lazy FinBERT loading**: The model is loaded on first use rather than at import time, which is the right pattern for a 400MB model.

8. **Well-structured tests**: The test suite covers Kelly math, safety checks, position caps, portfolio operations, and paper trading execution. The use of `@dataclass` for test helpers and clear test names is exemplary.

9. **Clean separation of concerns**: Each module has a single responsibility. The data layer, analysis layer, trading layer, and orchestration layer are properly decoupled.

10. **Rate limiting implemented**: While the implementation has a thread-safety issue (C-05), the fact that rate limiting exists at all shows awareness of API constraints.

---

## Recommended Priority Order for Fixes

| Priority | Issue | Effort | Impact |
|----------|-------|--------|--------|
| 1 | C-07: Relative file paths | 10 min | Kill switch reliability |
| 2 | C-03: Missing config/__init__.py | 1 min | Startup reliability |
| 3 | C-01 + C-06: BUY_NO handling | 2 hr | Strategy completeness |
| 4 | C-05: Thread-safe rate limiter | 15 min | API ban prevention |
| 5 | H-01: Portfolio persistence | 3 hr | Crash recovery |
| 6 | C-02: Sync/async consistency | 2 hr | Event loop correctness |
| 7 | H-07: Retry logic with tenacity | 1 hr | Network resilience |
| 8 | H-09: Error escalation | 30 min | Silent failure prevention |
| 9 | C-04: setup_wallet .env corruption | 20 min | Credential safety |
| 10 | H-05: FinBERT batch size limits | 30 min | OOM prevention |

---

*Report generated by automated code analysis. All line references verified against source files as of 2026-02-15.*
