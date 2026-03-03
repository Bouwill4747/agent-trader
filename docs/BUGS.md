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

## Stats

| Metric | Count |
|--------|-------|
| Total bugs | 26 |
| Critical | 8 |
| High | 10 |
| Medium | 6 |
| Low | 2 |
