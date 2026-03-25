# Bug & Fix Log

> Tracks every bug encountered, root cause, fix applied, and lesson learned.

---

## Template

```
### BUG-XXX: [Short description]
- **Date**: YYYY-MM-DD
- **File(s)**: path/to/file.py
- **Severity**: Low / Medium / High / Critical
- **Symptom**: What happened (error message, unexpected behavior)
- **Root Cause**: Why it happened
- **Fix**: What was changed to resolve it
- **Lesson Learned**: What to remember for next time
```

---

## Bugs

### BUG-001: Floating-point precision in exposure limit check
- **Date**: 2026-02-15
- **File(s)**: `tests/test_risk_manager.py`, `src/trading/risk_manager.py`
- **Severity**: Low
- **Symptom**: Test assertion `position_size <= 2.0` failed because value was `2.0000000000000018`
- **Root Cause**: Floating-point arithmetic in Python (IEEE 754). When calculating `(0.50 - 0.48) * 100`, the result isn't exactly `2.0` — it's `2.0000000000000018` due to how CPUs represent decimal numbers in binary. This is a fundamental property of all floating-point math, not a bug in Python.
- **Fix**: Added floating-point tolerance to test assertion: `<= 2.0 + 1e-9` (one billionth of a dollar tolerance)
- **Lesson Learned**: Never compare floats with exact equality (`==`, `<=`). Always use a small tolerance (epsilon). This is especially critical in financial code where fractions of a cent accumulate. In production, consider using Python's `Decimal` type for money calculations.

### BUG-002: Sentiment mock not called due to empty input lists
- **Date**: 2026-02-15
- **File(s)**: `tests/test_signal_generator.py`, `src/analysis/signal_generator.py`
- **Severity**: Low
- **Symptom**: Tests `test_positive_sentiment_increases_estimate` and `test_negative_sentiment_decreases_estimate` both returned identical estimates (0.57) regardless of sentiment setting. Mock `get_aggregate_sentiment` was never called.
- **Root Cause**: The test passed empty lists `[]` for articles and posts. In `signal_generator.py`, the code only calls `get_aggregate_sentiment(all_texts)` when `all_texts` is non-empty. With no articles/posts, `all_texts = []` is falsy, so sentiment defaults to 0.0 regardless of mock return value.
- **Fix**: Pass dummy articles `[{"title": "Test headline", "description": "Test desc"}]` so that `all_texts` is populated and the FinBERT mock actually gets called.
- **Lesson Learned**: When testing with mocks, make sure the code path that uses the mock is actually reached. A mock that's never called is a test that proves nothing. Trace through the real code to verify your test setup triggers the right branches.

### BUG-003: BUY_NO trades calculated with wrong edge and price
- **Date**: 2026-02-15
- **File(s)**: `src/trading/risk_manager.py`, `src/analysis/signal_generator.py`, `src/agent/orchestrator.py`, `src/trading/executor.py`
- **Severity**: Critical
- **Symptom**: BUY_NO trades would always have negative edge (rejected by risk manager) or use the wrong token/price when executed.
- **Root Cause**: The risk manager only computed `edge = estimated_prob - market_price`, which is correct for BUY_YES but inverted for BUY_NO. The orchestrator always used the YES token ID and YES price, even for BUY_NO signals. The executor always recorded positions as "YES" regardless of direction.
- **Fix**: Added `direction` parameter throughout the pipeline. Risk manager now computes direction-aware edge (`market_price - estimated_prob` for BUY_NO) and uses `effective_price = 1.0 - market_price` for Kelly sizing. Signal generator returns both YES and NO token IDs. Orchestrator selects correct token/price. Executor records correct position side.
- **Lesson Learned**: When a system has two modes (YES/NO, buy/sell), trace the full data flow end-to-end to verify both paths work. One test for each direction would have caught this early.

### BUG-004: Sync/async mix caused event loop errors in LangGraph
- **Date**: 2026-02-15
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: Critical
- **Symptom**: `RuntimeError: This event loop is already running` when LangGraph tried to run async nodes in its internal thread pool.
- **Root Cause**: LangGraph nodes were a mix of sync (`def`) and async (`async def`) functions. LangGraph runs all nodes in a thread pool executor, but async functions need an event loop. When `graph.ainvoke()` was called from an async context, nested event loops conflicted.
- **Fix**: Made ALL nodes sync. Wrapped async DB calls (`insert_signal`, `save_snapshot`) with `asyncio.run()` inside the sync nodes. The graph itself runs via `asyncio.to_thread()` from the async `run_cycle()` method.
- **Lesson Learned**: LangGraph requires all nodes to be the same type (all sync or all async). When mixing frameworks (async HTTP + sync ML inference), pick one style and adapt the other. `asyncio.run()` creates a fresh event loop, which works from sync contexts.

### BUG-005: Thread-unsafe rate limiter caused TOCTOU race condition
- **Date**: 2026-02-15
- **File(s)**: `src/data/polymarket_client.py`
- **Severity**: Critical
- **Symptom**: Under concurrent access (LangGraph thread pool), two threads could check `time.time() - self.last_request_time` simultaneously, both pass the check, and both send requests, exceeding the rate limit.
- **Root Cause**: The `wait()` method read `last_request_time`, did a comparison, and updated it in separate steps with no locking. This is a classic Time-Of-Check-Time-Of-Use (TOCTOU) race condition.
- **Fix**: Added `threading.Lock` to `RateLimiter`. All check-and-update logic runs inside `with self._lock:`.
- **Lesson Learned**: Any shared mutable state accessed from multiple threads needs synchronization. The TOCTOU pattern is the same in security (file race conditions) and concurrency (shared counters). `threading.Lock` is the simplest solution for Python.

### BUG-006: .env file corruption when saving credentials
- **Date**: 2026-02-15
- **File(s)**: `setup_wallet.py`
- **Severity**: Critical
- **Symptom**: Running `setup_wallet.py` multiple times could corrupt the `.env` file by appending duplicate keys or breaking the file format.
- **Root Cause**: Used `str.replace()` which replaces ALL occurrences of the placeholder, including partial matches. If a value contained a substring matching another placeholder, the replacement would cascade.
- **Fix**: Switched to `re.sub()` with `^KEY=.*` pattern and `re.MULTILINE` flag, which only matches the specific line.
- **Lesson Learned**: Use anchored regex for config file modifications, not string replacement. Also mask credentials in console output (`key[:8]...key[-4:]`) to prevent shoulder-surfing and terminal log capture.

### BUG-007: CVE-2025-68664 — langchain-core serialization injection RCE
- **Date**: 2026-02-15
- **File(s)**: `requirements.txt`
- **Severity**: Critical (CVSS 9.3)
- **Symptom**: N/A (not exploited, found by security auditor)
- **Root Cause**: langchain-core versions before 0.3.81 are vulnerable to a serialization injection attack that allows remote code execution. The vulnerability is in the deserialization pipeline — a crafted payload can execute arbitrary Python code during object reconstruction.
- **Fix**: Pinned `langchain-core>=0.3.81` in requirements.txt. Added comment referencing the CVE.
- **Lesson Learned**: Always check dependencies for known CVEs before deployment. Use tools like `pip-audit` or `safety` for automated scanning. Pin minimum versions for security patches, not just for compatibility.

### BUG-008: Unbounded cache in NewsCollector causes memory leak
- **Date**: 2026-02-15
- **File(s)**: `src/data/news_collector.py`
- **Severity**: Medium
- **Symptom**: Over hours of running, the agent's memory usage would grow continuously.
- **Root Cause**: The `self.cache` dictionary grew without bound. Expired entries (past TTL) were never cleaned up — the TTL check only prevented reading stale data, but stale entries remained in memory.
- **Fix**: Added `_clean_cache()` method that evicts entries older than `cache_ttl`. Called before every cache lookup.
- **Lesson Learned**: In-memory caches need eviction policies. For long-running services, always consider: max size, TTL cleanup, and whether to use a proper cache library (`cachetools`, `functools.lru_cache`).

### BUG-009: FinBERT OOM on large text batches
- **Date**: 2026-02-15
- **File(s)**: `src/analysis/finbert_analyzer.py`
- **Severity**: High
- **Symptom**: Potential out-of-memory crash when processing many texts (200+ articles + Reddit posts).
- **Root Cause**: `analyze_batch()` tokenized and ran inference on the entire text list at once. With 200 texts padded to 512 tokens, the tensor would be `[200, 512]` which could exhaust CPU memory.
- **Fix**: Process in fixed-size mini-batches of 16 texts. Results are accumulated across batches.
- **Lesson Learned**: Always batch ML inference. The batch size should be tuned for available memory. 16 is a safe default for CPU-only inference with FinBERT (~110M parameters).

### BUG-010: No API response schema validation — silent bad data
- **Date**: 2026-02-17
- **File(s)**: `src/data/polymarket_client.py`, `src/data/models.py`
- **Severity**: High
- **Symptom**: Malformed API responses (prices > 1.0, NaN volumes, missing IDs) would pass through unchecked, potentially causing incorrect position sizing or wrong trades.
- **Root Cause**: All API responses from Gamma were consumed as raw JSON with no validation. Pydantic was in requirements.txt but never imported.
- **Fix**: Created `GammaMarket` Pydantic model with validators for prices (0-1 range), volume/liquidity (NaN coercion), token IDs (valid JSON), and required identifiers. Applied in `get_markets()`.
- **Lesson Learned**: Always validate data at trust boundaries. External API responses are untrusted input, even from your own provider. Pydantic `field_validator` with `mode="before"` is the right tool for coercing messy API data.

### BUG-011: Path traversal possible via market_id parameter
- **Date**: 2026-02-17
- **File(s)**: `src/data/polymarket_client.py`
- **Severity**: Medium
- **Symptom**: A `market_id` like `../events` could cause the Gamma API URL to resolve to an unintended endpoint.
- **Root Cause**: IDs from the Gamma API response were interpolated directly into URL paths (`f"/markets/{market_id}"`) with no validation.
- **Fix**: Added `_validate_id()` function with regex pattern `^[a-zA-Z0-9_\-]+$` and length cap (256). Applied to all methods accepting IDs.
- **Lesson Learned**: Validate all identifiers from external sources before using them in URLs, file paths, or queries. Even if the HTTP library URL-encodes special characters, defense in depth is important.

### BUG-012: Portfolio state lost on crash — positions forgotten on restart
- **Date**: 2026-02-17
- **File(s)**: `src/trading/portfolio.py`, `src/utils/db.py`
- **Severity**: High
- **Symptom**: After a crash, the agent restarts with a fresh portfolio and no knowledge of existing positions, risking duplicate orders and exposure limit violations.
- **Root Cause**: Portfolio positions were purely in-memory. The database stored snapshots with aggregate values but not the individual position data needed for reconstruction.
- **Fix**: Added `positions_json` column to portfolio_snapshots table. `save_snapshot()` now serializes all open positions to JSON. `load_from_db()` classmethod reconstructs the full portfolio from the latest snapshot on startup.
- **Lesson Learned**: For any stateful service that handles money, persistence is a safety requirement, not a feature. Serialize state at every checkpoint and verify you can reconstruct from it.

### BUG-013: FinBERT zero-sentiment — Reddit error discards news articles
- **Date**: 2026-02-17
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: FinBERT sentiment was `+0.000` for every market despite NewsAPI successfully returning 25+ articles per cycle. The entire sentiment signal (30% of blended weight) was dead.
- **Root Cause**: In `_research_markets()`, news collection and Reddit scraping shared a single `try/except` block. NewsAPI succeeded (fetched articles), but the next line — Reddit PRAW — threw a 401 (no API credentials yet). The `except` handler returned `{"articles": {}, "sentiment": {}}`, discarding all successfully fetched articles. With no articles passed to the signal generator, FinBERT never received any text to score.
- **Fix**: Separated news and Reddit into independent `try/except` blocks. Each data source now fails independently — a Reddit outage no longer kills news + FinBERT.
- **Lesson Learned**: Never put independent operations in the same try/except block. This is the "single point of failure" anti-pattern — one flaky dependency (Reddit, which we didn't even have credentials for) silently disabled an unrelated healthy dependency (NewsAPI + FinBERT). Each external call should fail in isolation.

### BUG-014: Gamma API returning resolved markets with active=True
- **Date**: 2026-02-17
- **File(s)**: `src/data/polymarket_client.py`, `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: Only 2 out of 50 markets passed the liquidity filter. The 48 rejected markets were old, resolved markets from 2020-2021 (e.g., "Will Trump win 2020 election?") with `active=True` but `liquidity=0`.
- **Root Cause**: The Gamma API `active=True` parameter doesn't exclude resolved/closed markets. Without `closed=False`, the API returns historical markets. Without sorting by liquidity, the first 50 results are old high-volume markets with zero current liquidity.
- **Fix**: Added `closed=False`, `order=liquidity`, `ascending=False` to Gamma API query params. Added price filter in orchestrator to skip markets with YES price <= $0.02 or >= $0.98 (already resolved). Result: 10+ tradeable markets discovered per cycle.
- **Lesson Learned**: Don't trust API filter parameters to do what their names suggest. Always verify actual responses against your assumptions. The word "active" meant "not archived" to Polymarket, not "currently tradeable" as we assumed.

---

### BUG-015: Report double-counting portfolio value
- **Date**: 2026-02-18
- **File(s)**: `report.py`, `src/trading/portfolio.py`
- **Severity**: Medium
- **Symptom**: Report showed Current Value $175.88 and Cash $155.88, but actual cash was $135.88 and total value was $155.88. Portfolio appeared 13% richer than reality.
- **Root Cause**: `save_snapshot()` stores `bankroll = self.total_value` (cash + position value = $155.88). But `report.py` treated `bankroll` as cash and added `total_exposure` on top, double-counting position value.
- **Fix**: Report now derives cash as `snapshot["bankroll"] - snapshot["total_exposure"]`, then adds live position exposure to get current value.
- **Lesson Learned**: When one module saves data and another reads it, document what each field actually means. "bankroll" sounds like cash, but it was total value. A comment or better field name would have prevented this.

### BUG-016: Peak value never updated after position exits
- **Date**: 2026-02-18
- **File(s)**: `src/trading/portfolio.py`
- **Severity**: Medium
- **Symptom**: After a $55.88 profit from a resolved market, peak_bankroll still showed $100.00 (the starting value). Drawdown calculations would be wrong.
- **Root Cause**: `peak_bankroll` was only updated inside the `drawdown_pct` property getter (lazy evaluation). After `resolve_position()` or `close_position()` added proceeds to cash, peak was never rechecked before the next snapshot was saved.
- **Fix**: Added explicit `peak_bankroll` update in both `close_position()` and `resolve_position()` immediately after the position is removed and cash is updated.
- **Lesson Learned**: Don't rely on property getters for side effects. If a value needs to stay in sync, update it explicitly at every mutation point. Lazy evaluation is fine for read-only calculations, not for stateful tracking.

### BUG-017: CLOB SDK get_midpoint returns dict, code expects float
- **Date**: 2026-02-18
- **File(s)**: `src/data/polymarket_client.py`
- **Severity**: Medium
- **Symptom**: `get_midpoint()` returned `None` for all tokens when using authenticated CLOB client. Log showed: `Midpoint parse error: float() argument must be a string or a real number, not 'dict'`
- **Root Cause**: The `py_clob_client` SDK's `get_midpoint()` returns `{"mid": "0.225"}` (a dict with a string value), but our code called `float(midpoint)` directly on the dict. The httpx fallback path already handled the dict format correctly, but the SDK path didn't.
- **Fix**: Added `isinstance(midpoint, dict)` check — extract `midpoint.get("mid", 0)` before converting to float. Also added `PolyApiException` to the exception handler for 404s (token not found).
- **Lesson Learned**: Always check what format a third-party SDK actually returns — don't assume it matches the raw HTTP API. The same endpoint can return different shapes depending on whether you use the SDK wrapper or call it directly.

---

### BUG-018: Position limit overrun — 11 positions when max is 10
- **Date**: 2026-02-23
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: Portfolio reached 11 open positions despite `MAX_CONCURRENT_POSITIONS = 10`. Log showed `Max positions reached: 11/10` on subsequent cycles.
- **Root Cause**: In `_evaluate_risks()`, every signal was evaluated with the same `num_positions=self.portfolio.num_positions` value. That value doesn't update within the loop — so if the portfolio had 9 positions and 2 signals both passed the limit check (each seeing 9 < 10), both got approved and both got executed, landing at 11.
- **Fix**: Added `approved_this_cycle` counter in `_evaluate_risks()`. Each approval increments it, and it's added to `num_positions` for all subsequent checks in the same batch. Also added a live position count guard in `_execute_trades()` as a safety net — if the actual portfolio count is already at the limit, remaining approved trades are skipped.
- **Lesson Learned**: When iterating over a batch of decisions that mutate shared state, you must track in-flight changes within the loop — not just read from the shared state once at the start. The same pattern can cause bugs in any loop that approves/allocates from a shared pool.

### BUG-019: PolyApiException crashes cycle — minimum order size not caught
- **Date**: 2026-02-25
- **File(s)**: `src/data/polymarket_client.py`, `src/trading/risk_manager.py`
- **Severity**: High
- **Symptom**: Cycle crashed with `PolyApiException[status_code=400, error_message={'error': 'order ... is invalid. Size (4.65) lower than the minimum: 5'}]`. Risk manager log showed "5 shares" (due to `%.0f` rounding in format string) but actual value was 4.648.
- **Root Cause**: Two issues: (1) `place_order()` in `polymarket_client.py` only caught `ConnectionError, TimeoutError, OSError, ValueError, KeyError` — `PolyApiException` (the CLOB SDK's own exception type) was not in the list and propagated up to crash the full cycle. (2) Polymarket CLOB requires a minimum of 5 shares per order, but the risk manager had no check for this — small bankrolls or high-priced tokens could produce valid-looking 4.x share positions.
- **Fix**: (1) Added `PolyApiException` as the first except clause in `place_order()` so any CLOB rejection returns `None` rather than crashing. (2) Added a minimum-shares check in `risk_manager.py` after calculating `shares = position_size / effective_price` — if `shares < 5`, trade is rejected with a clear message before reaching the CLOB.
- **Lesson Learned**: Always include the SDK's native exception type in your catch list, not just generic Python I/O errors. Format strings with `%.0f` hide precision — the actual float value can be different from what the log shows.

---

### BUG-020: GTC reconciliation marks partial fills as fully filled — orphans remaining open order
- **Date**: 2026-02-26
- **File(s)**: `src/agent/orchestrator.py`, `src/data/polymarket_client.py`
- **Severity**: Medium
- **Symptom**: Gold >$4,600 GTC order placed for ~30 shares at $0.11. Only 2 shares matched immediately. Reconciliation saw `balance = 2 ≥ 1`, declared the trade fully filled, marked DB status as `filled`, and stopped checking. The remaining ~28 shares sitting in the CLOB order book were never tracked again — invisible to the agent forever.
- **Root Cause**: `_reconcile_gtc_orders()` used a single condition: `if balance >= 1 → mark filled`. It had no concept of partial fills — no check of whether an open order still existed for that token. In Polymarket's CLOB, a GTC order that partially fills stays in the book as "live" with reduced remaining size. The reconciliation had no visibility into this.
- **Fix**: Added a call to `get_open_orders()` once per reconciliation run. Built a set of `token_ids` with active orders. Updated logic: if `balance ≥ 1` AND order still open → partial fill → update shares in tracker, keep status `pending`; if `balance ≥ 1` AND no open order → fully filled → update shares, mark `filled`; if `balance < 1` AND no open order → cancelled, mark `cancelled`. Changed `get_open_orders()` to return `None` on API failure (vs `[]`) so a failed fetch never incorrectly triggers "fully filled".
- **Lesson Learned**: "Some tokens received" ≠ "order fully filled". Always check whether the order is still live in the book. Distinguish between `[]` (confirmed empty) and `None` (unknown) when polling external state — safe defaults should err toward doing less, not more.

---

### BUG-021: BUY_NO trades stored with negative edge — corrupts all calibration data
- **Date**: 2026-02-28
- **File(s)**: `src/trading/executor.py`
- **Severity**: Critical
- **Symptom**: 29/30 filled trades showed negative EV in the database. Calibration report claimed the agent had never had positive expected value on any trade.
- **Root Cause**: `executor.py` stored `edge = signal.edge` unconditionally. `signal.edge = estimated_prob_yes - price_yes`. For a valid BUY_NO trade, `estimated_prob_yes < price_yes` (the market is overpriced), so `signal.edge` is negative — but the trade has POSITIVE edge from the NO side. The math: `edge_NO = price_YES - P̂(YES) = -signal.edge`. Storing the raw signal edge made all BUY_NO trades look like negative EV bets.
- **Fix**: Compute `edge_for_side = edge if direction != "BUY_NO" else -edge` before writing to DB. Also added `market_price_yes` canonical column so analytics can always reconstruct edge from first principles.
- **Lesson Learned**: Edge is direction-dependent. `estimated_prob - market_price` is only correct for BUY_YES. For BUY_NO, flip the sign. Always store the side-consistent edge, not the raw difference.

### BUG-022: Intra-cycle exposure accumulator missing — cap can be breached
- **Date**: 2026-02-28
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: Critical
- **Symptom**: In a cycle with 3+ approved signals, all exposure checks used `self.portfolio.total_exposure` (the pre-cycle snapshot). Each check saw the same stale number. Three 15% positions could all pass a 50% cap check simultaneously, landing at 95% exposure.
- **Root Cause**: The FIA standard requires pre-trade limits to "include working orders and all pending approvals." The code had `approved_this_cycle` for position count but no equivalent for dollar exposure.
- **Fix**: Added `committed_exposure = self.portfolio.total_exposure` before the approval loop. Incremented by `decision.position_size` on each approval. Each subsequent check receives the updated total as `current_exposure`.
- **Lesson Learned**: Per-trade limit checks are not enough when multiple trades are evaluated in a batch. Always track an accumulator that includes all in-cycle approvals. This mirrors standard institutional pre-trade risk controls.

### BUG-023: SELL GTC not fill-confirmed — phantom cash in live mode
- **Date**: 2026-02-28
- **File(s)**: `src/trading/executor.py`, `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: In live mode, `execute_exit()` called `portfolio.close_position()` immediately after placing a SELL order. Tokens are still held until the GTC SELL fills. Result: portfolio tracker showed cash that didn't exist, and tokens were orphaned in the CLOB.
- **Root Cause**: Paper mode and live mode had different fill semantics. Paper exits fill instantly. Live GTC SELLs rest in the book until matched. The code treated both identically.
- **Fix**: `execute_exit()` now calls `portfolio.mark_selling()` for live mode instead of `close_position()`. Position stays tracked with `selling_pending=True`. New `_reconcile_sell_orders()` method runs each cycle, checks CLOB balance, and calls `close_position()` only when balance drops to 0 (tokens gone = order filled).
- **Lesson Learned**: Every exit order type has a fill latency. GTC SELLs are no different from GTC BUYs — order acknowledgement ≠ fill. Mirror the same reconciliation loop used for BUY orders.

### BUG-024: LLM anchors on market price in prompt — near-zero edge estimates
- **Date**: 2026-02-28
- **File(s)**: `src/analysis/llm_researcher.py`
- **Severity**: High
- **Symptom**: 0/10 win rate on live trades. Reviewing logs showed Claude's probability estimates clustered tightly around the market price (~±5%). Edge was consistently near zero. The agent rarely found tradeable signals.
- **Root Cause**: `ANALYSIS_TEMPLATE` included `## Current Market Price: $X.XX (market estimates Y% probability of YES)`. Anchoring bias research (ScienceDirect 2024, tested on GPT-4, Claude 2, Gemini, GPT-3.5) confirms all LLMs show statistically significant anchoring on numbers shown in the prompt, even when instructed to ignore them.
- **Fix**: Removed the entire `## Current Market Price` section from `ANALYSIS_TEMPLATE`. Updated system prompt to explicitly state "You do NOT receive the current market price — estimate from evidence alone." `_default_response` now returns 0.5 (maximum uncertainty) instead of the market price, so failures are visible in logs.
- **Lesson Learned**: If you ask an LLM to estimate a value and give it the "answer" first, it anchors on the answer. The independent estimate step and the market comparison step must be fully separated. Failing loudly (0.5 → near-zero edge → rejected) is better than failing silently (price → zero edge → no logs).

### BUG-025: Position purged as phantom on SELL order failure
- **Date**: 2026-03-03
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: Critical
- **Symptom**: Labour leadership election NO position (18.1 shares, worth $10.31, +159%) was silently deleted from the portfolio tracker. Agent reported the position as "phantom — GTC order never filled." The tokens existed on-chain and the position was a real winner.
- **Root Cause**: In `_monitor_positions()`, when `execute_exit()` returned `success=False`, the code unconditionally called `portfolio.purge_position()` with the comment "SELL failed — we don't actually hold these tokens." This logic was wrong: the SELL was rejected because the position had only 3 tracked shares (below the CLOB 5-share minimum), not because the tokens didn't exist. The actual on-chain balance was 18.1 shares. A failed SELL ≠ a phantom position.
- **Fix**: Replaced the automatic purge with `_purge_if_balance_zero()`, a helper that calls `client.get_token_balance()` first. Only purges if balance < 1 (confirmed empty). If balance ≥ 1: keeps the position and logs a warning. If the API returns None (error): keeps the position and retries next cycle. Paper mode always purges (no CLOB to check). Applied to both the `if not result.success` branch and the `except Exception` branch.
- **Lesson Learned**: A failed exit order is not proof that the position is fake. The CLOB can reject SELLs for many reasons (minimum size, liquidity, network error). Always verify on-chain state before deleting a position. Token balance = 0 is the only reliable phantom signal.

### BUG-026: RESOLVED detection fires on price proximity, not actual settlement
- **Date**: 2026-03-03
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: "Will there be no change in Fed interest rates after the March 2026 meeting?" was exited as `RESOLVED_YES` on 2026-02-26 — weeks before the March FOMC meeting. The market had only moved to 97% YES (strong consensus), not actually settled. Tokens remained on-chain and Polymarket still showed the position open.
- **Root Cause**: `_check_exit()` treated price >= 0.95 or price <= 0.05 as resolved. With `EXIT_RESOLVED_THRESHOLD = 0.95`, a NO token at 2.6¢ triggered `current_price <= (1 - 0.95) = 0.05`. A pre-resolution market can easily reach 97% consensus without settling. The agent confused "market has strong opinion" with "market has settled."
- **Fix**: Added Gamma API confirmation in `_monitor_positions()` before acting on a RESOLVED signal. When `_check_exit()` returns RESOLVED, `get_market_by_id()` is called to check the `closed` flag. If `closed == False`: hold, log debug. If `closed == True`: proceed with resolution. If API returns None: hold and retry next cycle. `_check_exit()` itself is unchanged — it is still a fast price-based first filter.
- **Lesson Learned**: Price proximity is a hint, not a fact. On Polymarket, a market can trade at 98% YES for days or weeks before officially resolving. The only authoritative source for market settlement is the Gamma API `closed` field. Never book a realized outcome without checking it.

---

### BUG-032: Scanner RESOLVED exit bypasses Gamma verification — sells at $0.97 instead of $1.00
- **Date**: 2026-03-14
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: Critical
- **Symptom**: The background price scanner fired `RESOLVED_SCANNER` and sold positions as soon as the token price hit 0.95, before the market actually closed. A NO token at $0.97 would be sold immediately instead of waiting for the $1.00 payout on settlement — a $0.03/share leak on every winning position.
- **Root Cause**: `_scanner_exit_check()` triggered `RESOLVED_SCANNER` immediately on price threshold with no Gamma API check. The hourly cycle (`_monitor_positions()`) correctly calls `get_market_by_id()` and checks `closed == True` before acting (BUG-026 fix). The scanner never had an equivalent guard.
- **Fix**: Added `get_market_by_id()` + `market_info.get("closed", False)` check inside `_scanner_exit_check()` Tier 1a, mirroring the hourly cycle logic. If Gamma is unreachable or market is not yet closed, the scanner holds and lets the hourly cycle handle it.
- **Lesson Learned**: Any code path that can trigger a sell must independently verify resolution. The hourly cycle and the scanner are both capable of exiting positions — both must share the same verification logic. Assume nothing about what the other path does.

### BUG-033: `selling_pending` stuck forever on CLOB soft failure — position becomes untradeable
- **Date**: 2026-03-14
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: After a failed take-profit sell (e.g. Taylor Swift NO on 2026-03-14), `selling_pending=True` was never cleared. The position became permanently locked: the scanner skipped it (`if pos.selling_pending: continue`) and the hourly cycle also skipped it. The position sat frozen indefinitely with unrealized gains.
- **Root Cause**: `_scanner_trigger_exit()` only reset `selling_pending = False` inside the `except Exception` block (hard failure / network error). When `execute_exit()` returned normally with `result.success = False` (CLOB rejected the order without raising an exception), no reset happened. The try block exited cleanly with `selling_pending` still True.
- **Fix**: Added `selling_pending = False` reset inside the try block when `result and not result.success`. The position is now unlocked for retry on the next scan or next hourly cycle.
- **Lesson Learned**: `selling_pending` is a claim flag. It must be released on ALL failure paths — not just exceptions. A function that returns `success=False` without throwing is just as much a failure as one that throws. When using try/except to manage state, audit every exit path (normal return, falsy return, exception) for cleanup.

### BUG-034: Missing try/except on live BUY — network error leaves CLOB order placed but agent unaware
- **Date**: 2026-03-14
- **File(s)**: `src/trading/executor.py`
- **Severity**: High
- **Symptom**: If a network error occurred after `place_order()` sent the request but before the response arrived, the CLOB order would exist on-chain but the agent would crash without recording it. The position would never be tracked, and USDC would be locked in an invisible order.
- **Root Cause**: `_live_execute()` called `self.client.place_order()` with no try/except. Unlike `_live_exit()` which wrapped its `place_order()` call in try/except (added in BUG-019 fix), the BUY path was never given the same treatment.
- **Fix**: Wrapped `place_order()` in `_live_execute()` with try/except, logging the exception and returning `OrderResult(success=False)`. Mirrors the existing pattern in `_live_exit()`.
- **Lesson Learned**: Symmetric code paths need symmetric error handling. BUY and SELL both call `place_order()` — both must handle its failure modes. When fixing a bug in one path, always check if the paired path has the same issue.

### BUG-035: Negative cash on portfolio restore — snapshot race condition with scanner price updates
- **Date**: 2026-03-14
- **File(s)**: `src/trading/portfolio.py`
- **Severity**: High
- **Symptom**: On agent restart, `portfolio.cash` could be computed as a negative value. This would cause `total_value` to undercount the portfolio, trigger inflated drawdown calculations, and potentially falsely enter HALTED mode.
- **Root Cause**: `load_from_db()` computes `cash = bankroll - position_value` where `position_value` uses `pos.current_price` restored from `positions_json`. The snapshot's `bankroll` and `positions_json` are written in the same `save_snapshot()` call but NOT atomically — the scanner thread can update `pos.current_price` between when `self.total_value` (which becomes `bankroll`) is computed and when `_positions_to_json()` is called. If prices moved up between those two lines, `positions_json` captures higher prices than `bankroll` was computed with, making `cash = bankroll - position_value` go negative.
- **Fix**: Added `max(0.0, ...)` clamp: `portfolio.cash = max(0.0, snapshot.get("bankroll", INITIAL_BANKROLL) - position_value)`. Prevents negative cash; self-corrects on first price scan.
- **Lesson Learned**: Snapshot saves that read multiple mutable fields without a lock are not atomic. The scanner thread is always racing. Either hold the lock for the entire snapshot write, or clamp/validate derived values on read. The clamp is the minimal safe fix; the real fix is atomic snapshot capture.

### BUG-036: `--once` mode skips portfolio restore — runs with blank portfolio, can duplicate positions
- **Date**: 2026-03-14
- **File(s)**: `main.py`
- **Severity**: Medium
- **Symptom**: Running `python main.py --once` called `run_cycle()` directly without first restoring the portfolio from the database. The agent ran a full cycle with an empty portfolio — seeing 0 positions, 0 exposure — and could have opened duplicate positions for markets already held, or approved trades that would breach exposure limits.
- **Root Cause**: `agent.run()` performs portfolio restore before starting the cycle loop (added in BUG-012 fix). The `--once` path in `main.py` bypassed `run()` entirely and called `run_cycle()` directly, skipping the restore step.
- **Fix**: Added `Portfolio.load_from_db()` call to the `--once` async block in `main.py`, setting `agent.portfolio` and `agent.executor.portfolio` before `run_cycle()`. Mirrors what `run()` does.
- **Lesson Learned**: Any alternative entry point that bypasses the normal startup sequence inherits all the risks of skipping those steps. When adding shortcuts like `--once`, explicitly check which setup steps they need and add them. Don't assume the shortcut shares setup with the normal path.

### BUG-037: `drawdown_pct` mutates `peak_bankroll` without a lock — race condition with scanner thread
- **Date**: 2026-03-14
- **File(s)**: `src/trading/portfolio.py`
- **Severity**: Low
- **Symptom**: No observed crash, but a latent race condition. The scanner thread calls `pos.current_price` updates continuously. If `drawdown_pct` is called from the main thread while the scanner is running, the read-modify-write on `self.peak_bankroll` is not atomic — a concurrent scanner price update could produce an inconsistent peak value.
- **Root Cause**: `drawdown_pct` is a property with a side effect: it updates `self.peak_bankroll = current` when a new high is reached. This mutation happens without holding `self._lock`. Python's GIL protects individual bytecode operations but not compound read-check-write sequences involving method calls like `self.total_value`.
- **Fix**: Wrapped the peak update and drawdown calculation inside `with self._lock:`.
- **Lesson Learned**: Properties that mutate state are fragile under concurrent access. If a `@property` has side effects, it needs the same thread protection as any other mutation. When in doubt about GIL safety for compound operations, use the lock.

### BUG-038: Ghost `selling_pending` survives restart — position locked indefinitely after BUG-033
- **Date**: 2026-03-15
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: Medium
- **Symptom**: Taylor Swift NO position showed `[SELLING]` for multiple cycles with no SELL trade in the DB and `sell_price=0.0`. No SELL order was ever placed on the CLOB. The exit check skipped the position every cycle, and no reconciler cleared it. Unrealized gains were locked and could not be realised.
- **Root Cause**: BUG-033 fixed the runtime path (claim released on failure), but did not fix positions that were already stuck in the broken state before that fix was deployed. On restart, `load_from_db()` restored the snapshot with `selling_pending=True, sell_price=0.0, selling_reason=""` — a ghost state where the claim flag is set but no actual SELL order exists on the CLOB and no pending SELL trade exists in the DB. `_reconcile_sell_orders()` requires a pending SELL trade in the DB to act; `_close_orphaned_sells()` saw `balance >= 1` (tokens still held) and continued past the position without checking whether an order was actually live.
- **Fix**: Added a ghost-detection branch inside `_close_orphaned_sells()` for the live-mode path. When `balance >= 1` (tokens still held) AND the token is not in `open_token_ids` (no live SELL order on the CLOB), the position is in a ghost state — the flag is cleared under the portfolio lock and a warning is logged, allowing the exit check to re-evaluate and re-fire the SELL on the next cycle.
- **Lesson Learned**: A bug fix that corrects a runtime code path does not automatically heal positions that entered the broken state before the fix was deployed. Persisted state (DB snapshots) carries forward the old corruption. Reconcilers need to handle not just "order filled but portfolio not updated" but also "flag set but no order ever placed". Any time a claim flag is added, define what a valid claimed state looks like and add a sanity check that detects and recovers from invalid ones.

### BUG-039: Hourly monitor doesn't clear `selling_pending` on failed SELL — same ghost as BUG-033/038
- **Date**: 2026-03-15
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: After BUG-038 was fixed (ghost cleared, exit re-fired), the hourly cycle immediately re-set `selling_pending=True` and attempted a SELL. The SELL failed with CLOB error `not enough balance / allowance` (tracked shares=17, actual CLOB balance=16.96). `_purge_if_balance_zero()` correctly kept the position (tokens are real) but returned without clearing `selling_pending`. Position stuck in ghost state again — same symptom as BUG-038, one cycle later.
- **Root Cause**: Two problems in the hourly `_monitor_positions` path: (1) `selling_pending = True` is set at line 1199 before `execute_exit()`, but `_purge_if_balance_zero()` — called when the SELL fails — only handled the phantom case (`balance < 1`) and the "keep and log" case (`balance >= 1`) without clearing the flag on the keep path. (2) Tracked `pos.shares` (17) was greater than actual CLOB balance (16.96), causing the CLOB to reject the sell with `not enough balance`. The share count discrepancy likely originates from fee deductions or rounding on the original GTC BUY fill.
- **Fix**: In `_purge_if_balance_zero`, when `balance >= 1` (keep path): (a) correct `pos.shares` to `math.floor(balance)` if it differs from the tracked count, (b) always clear `selling_pending = False` under the portfolio lock so the next cycle can retry with the corrected share count.
- **Lesson Learned**: The `selling_pending` claim flag must be released on every failure path in every code path that sets it — scanner, hourly cycle, and any future paths. `_purge_if_balance_zero` is a shared failure handler but was missing the flag release. Also: CLOB fills may return fractional or slightly fewer shares than ordered (fees, rounding). Tracked share counts should be reconciled against CLOB balance before placing exit orders, not just at purge time.

### BUG-040: Partial SELL fills not detected — tracked shares never corrected
- **Date**: 2026-03-17
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: Taylor Swift NO position tracked 15 shares internally, but Polymarket UI showed only 1 share on-chain. The agent continued placing SELL orders for 15 shares (all rejected or ignored) while 14 shares had already been sold via partial fills of the GTC limit order. Unrealized PnL and share count were both wrong.
- **Root Cause**: `_reconcile_sell_orders()` handles two cases: `balance < 1` (order fully filled → close position) and `balance >= 1` (tokens still held → keep waiting). The `balance >= 1` branch never checked whether the balance had dropped below the tracked share count. Partial fills reduce the on-chain balance without triggering a full close, so the tracked `pos.shares` drifted away from reality indefinitely. The stale-order cancel/re-place loop kept firing for the original quantity.
- **Fix**: Added partial fill detection at the top of the `balance >= 1` branch in `_reconcile_sell_orders()`. If `pos.shares > balance + 0.5`, log `PARTIAL FILL detected` and correct `pos.shares` to `math.floor(balance)` under the portfolio lock. The next SELL order will be placed for the corrected (real) quantity.
- **Lesson Learned**: GTC limit orders can partially fill over time — buyers absorb some shares but not all. Any reconciler that only distinguishes "fully filled" vs "not filled" will miss this. Always compare tracked quantity to actual on-chain balance, not just whether balance crossed zero.

### BUG-041: No full wallet reconciliation — portfolio drifts from on-chain reality
- **Date**: 2026-03-25
- **File(s)**: `src/agent/orchestrator.py`, `src/data/polymarket_client.py`
- **Severity**: High
- **Symptom**: Portfolio showed 9 positions but Polymarket wallet had 9 different positions — share counts wrong across all positions, SpaceX had 5 shares sold but agent tracked 10, Amy Klobuchar position (46.7 shares) completely untracked, Ilhan Omar (zombie) still shown after tokens were gone, Silver $95 closed but still tracked. Required manual DB surgery to resync.
- **Root Cause**: All existing reconcilers (`_reconcile_sell_orders`, `_close_orphaned_sells`, `_reconcile_missing_positions`) only operate on positions the agent **already knows about**. They compare known positions against CLOB state, not the other way around. A position that appears or disappears outside the normal BUY/SELL flow (manual trade, cancelled GTC with partial fill, market resolution) is invisible to all reconcilers. No mechanism started from the wallet and worked backwards to the portfolio.
- **Fix**: Added `_reconcile_full_wallet()` that calls `data-api.polymarket.com/positions` every 5 cycles to fetch all wallet token holdings. Compares against tracked positions and: (1) purges zombies with no wallet balance, (2) corrects share counts where wallet < tracked (partial fills), (3) adds untracked positions found in wallet. Added `get_wallet_positions()` to `PolymarketClient` and `get_market_id_for_token()` for Gamma lookup. Added `_cycle_count` counter to orchestrator.
- **Lesson Learned**: Event-driven tracking ("track what I did") always drifts from truth. Periodically verify state against ground truth (the actual wallet). The wallet is always right. Build reconcilers that start from the wallet, not from internal state.

---

## Stats

| Metric | Count |
|--------|-------|
| Total bugs | 41 |
| Critical | 10 |
| High | 18 |
| Medium | 10 |
| Low | 3 |

### BUG-027: Drawdown check uses stale cash — HALTED not triggering
- **Date**: 2026-03-04
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: Agent showed drawdown 17.5% (RISK_REDUCING_ONLY) when actual drawdown was 21.5% (HALTED). New trades would have been permitted despite being over the 20% halt threshold.
- **Root Cause**: `_generate_signals()` (step 3) checked `portfolio.drawdown_pct` before `_sync_cash_from_clob()` had run. Cash sync only ran in step 6. On startup, portfolio loaded from a 3-day-old snapshot with stale prices — total_value appeared ~$4 higher than reality, making drawdown appear 4pp lower.
- **Fix**: Added `_sync_cash_from_clob()` call at the top of `_generate_signals()` before the `get_trading_mode()` check. Cash sync now runs at step 3 (before drawdown check) and step 6 (after exits). Idempotent, cheap.
- **Lesson Learned**: Drawdown is only as accurate as the cash it's computed from. Any check that gates trading decisions must see current-cycle cash, not snapshot cash. Sync early.

### BUG-028: Portfolio snapshots not saved during HALTED/RISK_REDUCING_ONLY periods
- **Date**: 2026-03-04
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: High
- **Symptom**: After restarting the agent, portfolio loaded a 3-day-old snapshot. Cash, position prices, and drawdown were all stale. Snapshots had not been saved since the last position exit on March 1.
- **Root Cause**: `save_snapshot()` was inside the loop that handles position exits. When the agent is HALTED or RISK_REDUCING_ONLY (no new entries, no exits), the loop body never executes → no snapshot saved.
- **Fix**: Moved `save_snapshot()` to an unconditional position at the end of `_monitor_positions()`, after `_sync_cash_from_clob()`. Snapshot now saves every cycle regardless of trading activity.
- **Lesson Learned**: State persistence should never be conditional on trading activity. Snapshots are crash recovery insurance — they're most valuable during quiet periods where nothing changes.

### BUG-029: Stale GTC BUY orders lock USDC indefinitely
- **Date**: 2026-03-04
- **File(s)**: `src/agent/orchestrator.py`, `src/utils/db.py`
- **Severity**: Medium
- **Symptom**: Persistent $7.03 cash drift. Portfolio tracker showed more cash than wallet held. Amy Klobuchar (×2 orders, $7.66 total) and Silver SI (×1 order, $2.17) GTC BUY orders from 6+ days ago had USDC locked in the CLOB. `_reconcile_gtc_orders()` kept checking them but never cancelled.
- **Root Cause**: `_reconcile_gtc_orders()` had no timeout — it would check forever. Orders that went unfilled stayed `status='pending'` in the DB, locking USDC collateral that was invisible to the portfolio cash tracker.
- **Fix**: Added `STALE_ORDER_DAYS = 3` threshold. Orders older than 3 days are looked up in `get_open_orders()` by token ID, then cancelled via `client.cancel_order(clob_order_id)`. If the order is already gone from CLOB (expired/rejected silently), it's marked `cancelled` in DB anyway. `get_pending_live_trades()` now returns `timestamp` for age calculation.
- **Lesson Learned**: GTC orders on Polymarket can sit indefinitely. Without a timeout, locked USDC becomes invisible "ghost collateral" that inflates apparent cash. 3 days is a reasonable TTL — if a market hasn't filled in 3 days, it's unlikely to fill at that price.

### BUG-030: `_close_orphaned_sells()` skips paper mode — ghost position persists
- **Date**: 2026-03-04
- **File(s)**: `src/agent/orchestrator.py`
- **Severity**: Medium
- **Symptom**: Hyperliquid position showed `[SELLING]` for 2+ cycles after BUG-030 fix was deployed. The ghost position was never cleaned up despite the fix being present.
- **Root Cause**: The `_close_orphaned_sells()` function had `if self.executor.paper_mode: return` as its first guard, before building the `selling` list. In paper trading mode (the default), the function returned immediately on every call. Paper sells complete instantly — there are no real tokens to verify on the CLOB — so any `selling_pending=True` position in a paper portfolio is definitionally a stale snapshot artifact.
- **Fix**: Moved the `selling = [...]` list construction to before the paper_mode check. Paper mode now closes all `selling_pending` positions immediately (no CLOB verification needed). Live mode path unchanged: still checks CLOB token balance and open orders before closing.
- **Lesson Learned**: Guard clauses that skip entire functions can silently prevent the branch they were meant to protect. The paper-mode guard was intended to skip the CLOB balance check, not the entire function. Restructure guards to be as narrow as the behaviour they're protecting.

### BUG-031: Portfolio missing filled positions — agent incorrectly HALTED
- **Date**: 2026-03-04
- **File(s)**: `src/agent/orchestrator.py`, `src/utils/db.py`
- **Severity**: Critical
- **Symptom**: Agent was HALTED at 22.7% drawdown. Polymarket showed 13 open positions worth ~$113 total. Agent tracked only 11 positions worth ~$97. Three real positions were invisible: Labour leadership (18.1 shares @ 60¢ = **$10.85**, +172%), Democrats NC Senate (11.8 shares @ 20¢ = $2.36), Taylor Swift (1.26 tracked vs 17.0 actual shares). Total untracked value: **~$16.38**. Actual drawdown was ~9.3%, not 22.7%.
- **Root Cause (3 separate issues)**:
  1. Labour (BUG-025 aftermath): position was purged when SELL failed (tokens were real, BUG-025 was the original bug). The BUG-025 fix added CLOB balance checking before purge but the already-purged position was never restored.
  2. Democrats NC Senate: filled BUY trade in DB (`status='filled'`, `actual_outcome=NULL`) but never in portfolio. Likely restored at entry time but lost from snapshot during a crash/restart cycle.
  3. Taylor Swift: DB shows 16.98 shares but snapshot had 1.26 — snapshot saved with wrong count, probably during a partial-reconcile window.
- **Immediate Fix**: One-time script to insert a corrected portfolio snapshot with all 3 positions properly included, and bankroll recalculated from actual position values. Drawdown corrected: 22.4% → 9.3%.
- **Structural Fix**: Added `_reconcile_missing_positions()` to orchestrator — runs each cycle in step 6. Queries all `status='filled' AND actual_outcome IS NULL` BUY trades, compares against portfolio, verifies CLOB token balance (≥1), then calls `restore_position()` for any genuine untracked positions. Added `get_open_filled_trades()` to `db.py`.
- **Lesson Learned**: The portfolio snapshot is the sole source of truth on restart. Any bug that causes a position to be removed from the snapshot (purge, crash, partial write) will compound into incorrect drawdown, incorrect HALT decisions, and invisible P&L. A daily integrity check comparing DB trades against portfolio is essential for live trading.
