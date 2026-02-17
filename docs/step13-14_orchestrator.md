# Steps 13-14: Orchestrator & Main Entry Point — Explained

> Files created:
> - `src/agent/orchestrator.py` — LangGraph state machine that runs the trading loop
> - `main.py` — Entry point that starts the agent
>
> These files tie everything together. The orchestrator is the conductor — it calls each module in order and passes data between them.

---

## The State Machine

```
┌──────────────────────────────────────────────────────────────────┐
│                        ONE CYCLE (30 min)                        │
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐      │
│  │  1. DISCOVER  │──→│  2. RESEARCH │──→│  3. GEN SIGNALS  │     │
│  │  Gamma API    │    │  News+Reddit │    │  FinBERT+Claude  │     │
│  └─────────────┘    └─────────────┘    └───────┬─────────┘      │
│                                                  │                │
│  ┌─────────────┐    ┌─────────────┐    ┌───────▼─────────┐      │
│  │  6. MONITOR   │←──│  5. EXECUTE  │←──│  4. RISK CHECK   │     │
│  │  Update PnL   │    │  Place orders │    │  Kelly + limits  │     │
│  └─────────────┘    └─────────────┘    └─────────────────┘      │
│                                                                  │
│  → Sleep 30 minutes → Next cycle                                 │
└──────────────────────────────────────────────────────────────────┘
```

### What is a State Machine?

A state machine is a system that:
1. Has a defined set of **states** (here: 6 nodes)
2. Moves between states based on **transitions** (here: fixed edges)
3. Carries **state data** that each node reads and updates

In security, you've seen state machines in:
- TCP connection states (SYN_SENT → ESTABLISHED → FIN_WAIT → CLOSED)
- Authentication flows (unauthenticated → OTP_sent → authenticated)
- Firewall rule processing (packet → each rule → accept/drop)

Our agent's state machine is linear (each node leads to the next), but LangGraph supports branching and conditional edges too.

### What is LangGraph?

LangGraph is a framework by LangChain for building AI agent workflows as graphs. Key concepts:

- **StateGraph** — The graph definition. You add nodes and edges.
- **Node** — A function that takes state, does work, returns updated state.
- **Edge** — Connects nodes (defines execution order).
- **State** — A TypedDict that flows through all nodes. Each node can read from and write to it.
- **`compile()`** — Locks the graph definition and returns a runnable object.
- **`ainvoke()`** — Runs the graph asynchronously with an initial state.

```python
graph = StateGraph(AgentState)
graph.add_node("discover_markets", self._discover_markets)
graph.add_edge("discover_markets", "research_markets")
graph.set_entry_point("discover_markets")
graph = graph.compile()

# Run it
result = await graph.ainvoke(initial_state)
```

---

## File 1: `src/agent/orchestrator.py`

### The State Object

```python
class AgentState(TypedDict):
    markets: list
    articles: dict
    sentiment: dict
    signals: list
    approved_trades: list
    execution_results: list
    cycle_start: str
    errors: list
```

**TypedDict** — A Python type that defines a dictionary with specific keys and value types. Unlike a regular dict, it tells your IDE (and other developers) exactly what keys exist and what types they hold.

This state flows through every node:
- Node 1 writes `markets`
- Node 2 reads `markets`, writes `articles` and `sentiment`
- Node 3 reads `markets` + `articles` + `sentiment`, writes `signals`
- And so on...

Each node only writes the keys it's responsible for. This is **separation of concerns** at the data level.

### Node 1: `_discover_markets`

```python
markets = self.client.get_markets(limit=50, active=True)

# Filter for tradeable markets
for market in markets:
    volume = float(market.get("volume", 0) or 0)
    liquidity = float(market.get("liquidity", 0) or 0)
    if volume < 1000 or liquidity < 500:
        continue
    filtered.append(market)

# Top 10 by volume
filtered.sort(key=lambda m: float(m.get("volume", 0) or 0), reverse=True)
filtered = filtered[:10]
```

**Why filter?** Polymarket has hundreds of markets, but many have:
- Low volume (nobody's trading → hard to get fills)
- Low liquidity (wide spreads → bad prices)

We only look at the top 10 by volume — these are the markets where our orders will actually fill at reasonable prices.

**`lambda`** — An inline, anonymous function. `key=lambda m: float(m.get("volume", 0))` tells Python's sort function "sort by this market's volume."

**`or 0`** — Handles `None` values. `float(None or 0)` → `float(0)` → `0.0`. Without this, `float(None)` would crash.

### Node 2: `_research_markets`

```python
articles = self.news.get_articles_for_markets(markets)
sentiment = self.sentiment.get_sentiment_for_markets(markets)
```

Simple — calls the data layer modules we built in Step 3. Returns dicts mapping each market ID to its collected data.

### Node 3: `_generate_signals`

```python
signals = await self.signals.generate_signals(markets, articles, sentiment)
```

Calls the analysis engine (Step 8-12). This is the expensive step — it runs FinBERT on all text AND calls Claude for each market. Takes the longest of any node.

### Node 4: `_evaluate_risks`

```python
for signal in signals:
    if signal.direction == "SKIP":
        continue

    decision = self.risk.evaluate_trade(
        estimated_prob=signal.estimated_prob,
        market_price=signal.current_price,
        confidence=signal.confidence,
        bankroll=self.portfolio.total_value,
        current_exposure=self.portfolio.total_exposure,
        num_positions=self.portfolio.num_positions,
        current_drawdown=self.portfolio.drawdown_pct,
    )

    if decision.approved:
        approved.append((signal, decision))
```

Every non-SKIP signal goes through the 6 risk checks. Only approved signals make it to execution. The `approved_trades` list contains tuples of `(signal, risk_decision)` — the signal tells us WHAT to trade, the decision tells us HOW MUCH.

### Node 5: `_execute_trades`

```python
for signal, decision in approved:
    result = await self.executor.execute_trade(
        market_id=signal.market_id,
        token_id=signal.token_id,
        question=signal.question,
        side="BUY",
        price=signal.current_price,
        risk_decision=decision,
    )
```

Places orders for each approved trade. In paper mode, simulates fills. In live mode, sends to Polymarket's CLOB.

### Node 6: `_monitor_positions`

```python
# Update prices
for market_id, pos in self.portfolio.positions.items():
    midpoint = self.client.get_midpoint(pos.token_id)
    if midpoint is not None:
        prices[market_id] = midpoint

self.portfolio.update_prices(prices)
await self.portfolio.save_snapshot()
logger.info("\n%s", self.portfolio.summary())
```

Fetches current prices for all open positions, updates the portfolio, saves a snapshot to the database, and logs a summary. This is your end-of-cycle report.

### The Main Loop

```python
async def run(self):
    while True:
        if self.risk.check_kill_switch():
            logger.warning("Kill switch detected — shutting down")
            break

        await self.run_cycle()

        logger.info("Next cycle in %d seconds...", CYCLE_INTERVAL_SECONDS)
        await asyncio.sleep(CYCLE_INTERVAL_SECONDS)
```

An infinite loop that:
1. Checks the kill switch (`data/STOP` file)
2. Runs one complete cycle
3. Sleeps for 30 minutes
4. Repeats

`asyncio.sleep()` is non-blocking — it yields control back to the event loop instead of blocking the entire process. This matters if we ever add WebSocket listeners or other concurrent tasks.

---

## File 2: `main.py` — Entry Point

### Signal handling

```python
def shutdown_handler(signum, frame):
    logger.info("Shutdown signal received — cleaning up...")
    agent.shutdown()
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)
```

**What are signals?** OS-level notifications sent to processes:
- **SIGINT** — Sent when you press `Ctrl+C`. Default: terminate the process.
- **SIGTERM** — Sent by `kill <pid>` or system shutdown. Polite termination request.

We override the default behavior to:
1. Log that we're shutting down (audit trail)
2. Call `agent.shutdown()` to close HTTP connections cleanly
3. Exit with code 0 (success)

Without this, pressing Ctrl+C would dump a stack trace. With it, we get a clean shutdown.

**Cybersecurity connection:** Signal handling is the same mechanism used in:
- Graceful web server shutdown (Nginx, Apache)
- Process management in containers (Docker sends SIGTERM before SIGKILL)
- Daemon processes that need to flush logs/close connections

### `asyncio.run()`

```python
asyncio.run(agent.run())
```

This is the bridge between synchronous (`main()`) and asynchronous (`agent.run()`) code. It:
1. Creates a new event loop
2. Runs the async function to completion
3. Cleans up the event loop

The entire agent is async from this point down — all database writes, API calls, and the LangGraph pipeline run on the async event loop.

### The `--once` flag

```python
if "--once" in sys.argv:
    asyncio.run(agent.run_cycle())
```

`sys.argv` is the list of command-line arguments. `python main.py --once` sets `sys.argv = ["main.py", "--once"]`.

This is useful for:
- Testing a single cycle without waiting 30 minutes
- Debugging specific market analysis
- Running one-off research

---

## Complete Data Flow (One Cycle)

```
main.py
 └→ Orchestrator.run_cycle()
     │
     ├→ Node 1: discover_markets
     │   └→ PolymarketClient.get_markets()
     │       └→ Gamma API: GET /markets
     │   → Filter: volume > $1000, liquidity > $500
     │   → Take top 10 by volume
     │   → State: markets = [market1, market2, ...]
     │
     ├→ Node 2: research_markets
     │   ├→ NewsCollector.get_articles_for_markets()
     │   │   └→ NewsAPI: GET /everything?q=keywords
     │   └→ SentimentScraper.get_sentiment_for_markets()
     │       └→ Reddit: search relevant subreddits
     │   → State: articles = {id: [...]}, sentiment = {id: [...]}
     │
     ├→ Node 3: generate_signals
     │   ├→ FinBERTAnalyzer.get_aggregate_sentiment(all_texts)
     │   │   └→ Run model inference on news + Reddit text
     │   └→ LLMResearcher.analyze_market(question, context)
     │       └→ Claude API: POST /messages
     │   → Blend: 70% Claude + 30% sentiment
     │   → Calculate edge = estimated_prob - market_price
     │   → State: signals = [BUY_YES, SKIP, BUY_NO, ...]
     │
     ├→ Node 4: evaluate_risks
     │   └→ RiskManager.evaluate_trade() for each signal
     │       → Check: drawdown, positions, edge, confidence
     │       → Kelly sizing → apply caps
     │   → State: approved_trades = [(signal, decision), ...]
     │
     ├→ Node 5: execute_trades
     │   └→ Executor.execute_trade() for each approved trade
     │       ├→ Paper: simulate fill
     │       └→ Live: ClobClient.create_and_post_order()
     │   → Portfolio.open_position()
     │   → DB: insert_trade()
     │   → State: execution_results = [OrderResult, ...]
     │
     └→ Node 6: monitor_positions
         └→ Update prices for all positions
         └→ Portfolio.save_snapshot() → DB
         └→ Log portfolio summary
```

---

## Cybersecurity Connections

| Concept | In This Module | In Cybersecurity |
|---------|---------------|-----------------|
| **State machine** | LangGraph pipeline with defined nodes and edges | TCP states, auth flows, firewall rule processing |
| **Signal handling** | SIGINT/SIGTERM for graceful shutdown | Daemon process management, container orchestration |
| **Kill switch** | File-based emergency stop checked every cycle | Emergency response procedures, circuit breakers |
| **Error isolation** | Each node catches its own exceptions | Service mesh patterns, bulkhead isolation |
| **Audit completeness** | Every cycle logs start/end, every decision logged | Complete audit trails for compliance |
| **Graceful degradation** | If one node fails, errors are logged but agent continues | Resilient system design |

---

## Key Concepts Introduced

| Concept | What It Means |
|---------|---------------|
| **TypedDict** | A dict with predefined keys and types. Provides structure without the overhead of a full class. |
| **State machine** | A system that moves through defined states. Each state has specific behavior and transitions. |
| **LangGraph** | Framework for building AI agent workflows as directed graphs. Nodes = steps, edges = order. |
| **`asyncio.run()`** | Bridge between sync and async code. Creates an event loop and runs an async function to completion. |
| **`asyncio.sleep()`** | Non-blocking pause. Yields control to the event loop instead of blocking the process. |
| **Signal handling** | Intercepting OS signals (Ctrl+C, kill) to run cleanup code before exiting. |
| **`sys.argv`** | List of command-line arguments passed to the script. `sys.argv[0]` is the script name. |
| **`lambda`** | Inline anonymous function. `lambda x: x * 2` is equivalent to `def f(x): return x * 2`. |
| **Tuple unpacking** | `for signal, decision in approved:` — unpacks each tuple in the list into two variables. |
| **Entry point** | `if __name__ == "__main__": main()` — only runs when the script is executed directly, not when imported. |
