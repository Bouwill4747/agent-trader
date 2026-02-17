# Step 2: Utility Infrastructure — Explained

> Files created: `src/utils/logger.py`, `src/utils/db.py`
> These two files are the foundation — every other module in the project imports from them.

---

## File 1: `src/utils/logger.py` — Structured Logging

### Why not just use `print()`?

`print()` works, but it has problems:
- No timestamps — you can't tell when something happened
- No severity levels — a debug message looks the same as a critical error
- No file output — if the agent crashes, you lose everything
- No control — you can't turn off verbose messages in production

A proper logger solves all of this.

### Line-by-line breakdown

```python
import os
import logging
from logging.handlers import RotatingFileHandler
```
- `logging` — Python's built-in logging library. Not a pip install, it ships with Python.
- `RotatingFileHandler` — A special handler that automatically rotates log files when they get too big. Without this, a long-running bot would eventually fill your disk.

```python
from config.settings import LOG_PATH
```
- Imports the log file path (`data/agent.log`) from our central config. This is why we built `settings.py` first — everything references it.

```python
def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
```
- A **factory function** — it creates and returns configured logger objects.
- `name` — Each module passes its own name. This shows up in log lines so you know which module generated each message.
- `level` — Controls verbosity. The hierarchy is: `DEBUG > INFO > WARNING > ERROR > CRITICAL`. Setting level to INFO means DEBUG messages are hidden.
- `-> logging.Logger` — This is a **type hint**. It tells you (and your IDE) that this function returns a Logger object. Doesn't affect runtime, just documentation.

```python
logger = logging.getLogger(name)
logger.setLevel(getattr(logging, level.upper(), logging.INFO))
```
- `getLogger(name)` — Python's logging system is a singleton registry. If you call `getLogger("database")` twice, you get the same logger object both times. This prevents duplicate loggers.
- `getattr(logging, "INFO")` — Converts the string "INFO" to the constant `logging.INFO` (which equals 20). This is a Python trick for dynamic attribute access.

```python
if logger.handlers:
    return logger
```
- **Guard clause** — if this logger already has handlers (console + file), don't add them again. Without this, you'd get duplicate log lines every time a module is imported.

```python
formatter = logging.Formatter(
    "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
```
- Defines how each log line looks. The `%` placeholders are filled by the logging system:
  - `%(asctime)s` → `2026-02-15 17:30:00`
  - `%(name)-20s` → `database            ` (padded to 20 chars for alignment)
  - `%(levelname)-8s` → `WARNING ` (padded to 8 chars)
  - `%(message)s` → Your actual message
- Example output: `2026-02-15 17:30:00 | risk_manager         | WARNING  | Position limit exceeded`

```python
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
```
- Creates the `data/` directory if it doesn't exist. `exist_ok=True` means "don't error if it already exists." Without this, the file handler would crash on first run.

```python
file_handler = RotatingFileHandler(
    LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5
)
```
- `maxBytes=10 * 1024 * 1024` — 10 megabytes. When the log file hits this size, it rotates.
- `backupCount=5` — Keeps 5 old log files: `agent.log.1`, `agent.log.2`, etc. Oldest gets deleted.
- So max disk usage = 6 files x 10MB = 60MB. Predictable and bounded.

```python
console_handler = logging.StreamHandler()
```
- Also prints to your terminal (stdout). You see logs live when running the agent.

### How other modules use it

```python
from src.utils.logger import setup_logger

logger = setup_logger("risk_manager")

logger.debug("Calculating position size...")      # Verbose, hidden by default
logger.info("Placed order: 10 YES @ $0.55")       # Normal operations
logger.warning("Approaching max exposure limit")   # Caution
logger.error("API call failed: 429 Too Many Req")  # Something broke
```

### Cybersecurity connection

This is **audit logging** — a core security practice. In your field:
- SIEM systems (Splunk, ELK) aggregate logs just like this
- SOC analysts read structured logs to investigate incidents
- Compliance frameworks (SOC 2, HIPAA) require audit trails
- The structured format (`|` delimited) makes logs easy to parse programmatically

---

## File 2: `src/utils/db.py` — SQLite Database

### Why SQLite?

| Feature | SQLite | PostgreSQL | MySQL |
|---------|--------|-----------|-------|
| Setup needed | None — just a file | Install server, create DB, manage users | Same |
| Good for single app | Perfect | Overkill | Overkill |
| Concurrent writes | Limited | Excellent | Good |
| Our use case | One bot, one machine | Would need if scaling | Would need if scaling |

SQLite is the right choice here. It's not a "toy" database — it's used in every iPhone, Android device, and most web browsers.

### Line-by-line breakdown

```python
import aiosqlite
```
- **aiosqlite** — An async wrapper around Python's built-in `sqlite3` module.
- **What is async?** Normal (synchronous) code waits for each operation to finish before moving on. Async code can start a database write, go do something else (like fetch market data), and come back when the write finishes. This matters for a bot that makes many I/O operations.

```python
from config.settings import DATABASE_PATH
from src.utils.logger import setup_logger

logger = setup_logger("database")
```
- Imports the DB path from config (`data/trades.db`)
- Creates its own logger so DB operations show up as `database` in the logs

### `init_db()` — Creating tables

```python
async def init_db():
```
- `async def` — This is an **async function** (also called a coroutine). You call it with `await init_db()` instead of just `init_db()`. This is part of Python's async/await pattern.

```python
async with aiosqlite.connect(DATABASE_PATH) as db:
```
- **Context manager** (`async with ... as`). Opens a connection to the database, and automatically closes it when the block ends — even if an error occurs. Same pattern as `with open("file.txt") as f:` for files. Prevents resource leaks.

```python
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ...
)
```
- `IF NOT EXISTS` — Makes the command **idempotent**. Run it once or 1000 times, same result. The agent calls this every time it starts up, and it's safe.
- `PRIMARY KEY AUTOINCREMENT` — Each row gets a unique, auto-incrementing ID. First trade = 1, second = 2, etc.

### The four tables

#### `trades` — Every order the agent places
| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER | Auto-incrementing unique ID |
| `timestamp` | TEXT | When the order was placed (ISO format) |
| `market_id` | TEXT | Which Polymarket market |
| `token_id` | TEXT | The specific outcome token (YES or NO) |
| `question` | TEXT | Human-readable market question |
| `side` | TEXT | "BUY" or "SELL" |
| `price` | REAL | Price per share ($0.00 - $1.00) |
| `size` | REAL | Number of shares |
| `total_cost` | REAL | price × size |
| `order_type` | TEXT | "GTC", "FOK", etc. (defaults to GTC) |
| `status` | TEXT | "pending", "filled", "cancelled" |
| `paper_trade` | INTEGER | 1 = simulated, 0 = real money |

#### `signals` — Every analysis the agent produces
Even signals that don't result in trades are recorded. This lets you analyze: "Was the agent right about markets it chose NOT to trade?"

| Column | Type | Purpose |
|--------|------|---------|
| `estimated_prob` | REAL | What we think the probability is |
| `edge` | REAL | estimated_prob minus current_price |
| `sentiment_score` | REAL | FinBERT's score (-1.0 to +1.0) |
| `confidence` | TEXT | "low", "medium", "high" |
| `direction` | TEXT | "BUY_YES", "BUY_NO", "SKIP" |
| `acted_on` | INTEGER | 1 = we traded, 0 = we skipped |

#### `portfolio_snapshots` — Periodic state of the portfolio
Taken at the end of each cycle. Lets you chart bankroll over time, track drawdown, etc.

#### `agent_runs` — Metadata about each 30-minute cycle
How many markets were analyzed, how many signals generated, how many trades placed, any errors.

### Helper functions

```python
async def insert_trade(trade: dict):
```
- Takes a dictionary, inserts it into the `trades` table.
- Uses `?` **parameterized queries** instead of f-strings. This prevents **SQL injection** — a top-10 OWASP vulnerability. Never build SQL with string concatenation.

```python
trade.get("question", "")
```
- `dict.get(key, default)` — Returns the value if the key exists, otherwise returns the default. Safer than `trade["question"]` which would crash if the key is missing.

```python
await db.commit()
```
- SQLite (like most databases) uses **transactions**. Changes aren't saved until you `commit()`. If the program crashes before commit, the data is rolled back. This is called **ACID compliance** — Atomicity, Consistency, Isolation, Durability.

```python
async def get_trade_history(limit: int = 50) -> list:
```
- Fetches the most recent trades, newest first (`ORDER BY id DESC`).
- `db.row_factory = aiosqlite.Row` — Makes rows act like dictionaries instead of plain tuples. So you get `row["price"]` instead of `row[6]`.

### Cybersecurity connection

- **SQL Injection prevention**: We use parameterized queries (`?` placeholders) instead of string formatting. This is the same defense you'd use in any web app. The `?` tells SQLite "this is data, not SQL code."
- **ACID compliance**: Database transactions are atomic — they either fully complete or fully roll back. No half-written data. Same principle as secure file operations.
- **Audit trail**: The `trades` and `signals` tables form a complete audit log of every decision the agent made. Forensics-friendly.

---

## How these two files connect to the rest of the project

```
                    config/settings.py
                     ↓            ↓
              src/utils/logger.py   src/utils/db.py
               ↓    ↓    ↓          ↓    ↓    ↓
          (every module imports logger and uses db)
```

Every file we build from here will start with:
```python
from src.utils.logger import setup_logger
logger = setup_logger("module_name")
```

And the trading/portfolio modules will call:
```python
from src.utils.db import insert_trade, insert_signal
```

These two utilities are the plumbing. Not glamorous, but nothing works without them.
