# Step 15: Tests — Explained

> Files created:
> - `tests/test_risk_manager.py` — 11 tests for Kelly criterion, safety checks, position caps
> - `tests/test_signal_generator.py` — 7 tests for blending, direction logic, signal structure
> - `tests/test_executor.py` — 13 tests for paper trading, portfolio math, position management
>
> Total: **31 tests**, all passing. No API keys required — everything is mocked.

---

## Why We Test

Testing isn't just about catching bugs — it's about confidence:
- **Refactoring safety**: Change code without fear of breaking things
- **Documentation**: Tests show how functions are supposed to be used
- **Regression prevention**: Once a bug is fixed, a test ensures it never comes back
- **Design validation**: If something is hard to test, the design might be wrong

In cybersecurity, you know this as **validation and verification** — proving that controls work as intended.

---

## Key Testing Concepts

### Mocking

```python
from unittest.mock import MagicMock, patch

generator.finbert = MagicMock()
generator.finbert.get_aggregate_sentiment.return_value = 0.5
```

**What is mocking?** Replacing a real object with a fake one that you control. Instead of calling the real FinBERT model (which needs 400MB downloaded and takes seconds), we create a fake that instantly returns whatever value we specify.

**Why mock?**
- **Speed**: Tests run in 3 seconds instead of minutes
- **Isolation**: Test YOUR code, not the external API
- **Control**: Force specific scenarios (what if Claude returns an error?)
- **No keys needed**: Don't need real API credentials for testing

**Cybersecurity parallel**: This is like setting up a test environment for penetration testing — you create controlled targets that simulate real systems without risking production.

### Fixtures

```python
@pytest.fixture
def risk():
    return RiskManager()
```

A **fixture** is a setup function that runs before each test. It creates a fresh, clean object so tests don't affect each other. `@pytest.fixture` is a decorator that registers the function with pytest.

When a test function has a parameter with the same name (`def test_something(self, risk):`), pytest automatically calls the fixture and passes the result.

### `@patch` decorator

```python
with patch("src.trading.executor.PAPER_TRADING", True):
    executor = Executor(mock_client, portfolio)
```

`patch` temporarily replaces a value during the test, then restores it afterward. Here we force `PAPER_TRADING = True` regardless of what's in `.env`. This ensures the test is deterministic — same result every time.

### `AsyncMock`

```python
with patch("src.trading.executor.insert_trade", new_callable=AsyncMock):
```

For async functions (`async def`), regular `MagicMock` doesn't work — you need `AsyncMock`. It returns an awaitable result when called with `await`.

---

## Test File 1: `test_risk_manager.py` — 11 Tests

### TestKellyCriterion (3 tests)

Tests the core position sizing math:

1. **`test_basic_kelly_sizing`** — Given edge=15%, price=$0.55, verify Kelly formula:
   ```
   Kelly raw = 0.15 / (1 - 0.55) = 0.333 (33.3%)
   ```
   Asserts the raw Kelly value matches within tolerance.

2. **`test_kelly_fraction_reduces_size`** — Full Kelly would be $33.33 on $100, but 0.25x fraction + 5% cap limits it to $5.00.

3. **`test_medium_confidence_reduces_size`** — Same trade parameters, but medium confidence (0.7x multiplier) gives a smaller position than high confidence (1.0x).

### TestSafetyChecks (6 tests)

Each test triggers a specific rejection reason:

| Test | What Triggers Rejection |
|------|------------------------|
| `test_reject_low_edge` | Edge = 3% (below 10% threshold) |
| `test_reject_low_confidence` | Confidence = "low" (even with 25% edge) |
| `test_reject_at_drawdown_limit` | Drawdown = 20% (all trading halts) |
| `test_reject_at_max_positions` | 10 open positions (at the limit) |
| `test_reject_at_exposure_limit` | 50% exposure (at the limit) |
| `test_reject_invalid_price` | Prices of 0, 1, -0.5, 1.5 (would cause math errors) |

### TestPositionCaps (2 tests)

Verify hard limits override Kelly's recommendations:
- Even with massive edge, position capped at 5% of bankroll
- Position shrinks to fit remaining exposure room

---

## Test File 2: `test_signal_generator.py` — 7 Tests

### TestDirectionLogic (3 tests)

Tests the BUY_YES / BUY_NO / SKIP decision:

| Test | Setup | Expected |
|------|-------|----------|
| `test_buy_yes_when_underpriced` | Claude says 80%, market says 55% | BUY_YES (edge +25%) |
| `test_buy_no_when_overpriced` | Claude says 25%, market says 60% | BUY_NO (edge -35%) |
| `test_skip_when_edge_too_small` | Claude says 57%, market says 55% | SKIP (edge only 2%) |

### TestBlending (3 tests)

Tests the 70/30 blending formula:

1. **`test_blending_weights`** — With neutral sentiment (0.0):
   ```
   Blended = 0.70 × 0.80 + 0.30 × (0.50 + 0.0) = 0.56 + 0.15 = 0.71
   ```

2. **`test_positive_sentiment_increases_estimate`** — Same Claude output, but positive sentiment → higher final estimate.

3. **`test_negative_sentiment_decreases_estimate`** — Same Claude output, but negative sentiment → lower final estimate.

### TestSignalStructure (1 test)

Verifies every field on the TradingSignal dataclass is populated and valid.

---

## Test File 3: `test_executor.py` — 13 Tests

### TestPaperTrading (3 tests)

1. **`test_paper_trade_succeeds`** — Paper trade fills at requested price, has "PAPER" in order ID
2. **`test_rejected_trade_returns_failure`** — Rejected risk decision → immediate failure, no execution
3. **`test_paper_trade_updates_portfolio`** — After paper trade, portfolio has a new position and less cash

### TestPortfolioIntegration (1 test)

**`test_multiple_trades_tracked`** — Three trades → three positions, cash = $85 (correct math).

### TestPortfolio (9 tests)

These test the portfolio in isolation (no executor needed):

| Test | What It Verifies |
|------|-----------------|
| `test_initial_state` | Fresh portfolio: $100 cash, 0 positions, 0% drawdown |
| `test_open_position` | Buy 10 shares @ $0.50 → cash drops by $5.00 |
| `test_close_position_with_profit` | Sell at $0.70 → realized PnL = +$2.00 |
| `test_close_position_with_loss` | Sell at $0.30 → realized PnL = -$2.00 |
| `test_resolve_winning_position` | YES wins → shares pay $1.00 each → PnL = +$4.50 |
| `test_resolve_losing_position` | YES loses → shares worth $0.00 → PnL = -$5.50 |
| `test_drawdown_calculation` | After loss, drawdown = (peak - current) / peak = 4% |
| `test_insufficient_cash_rejected` | Can't buy $50 of shares with only $10 cash |
| `test_position_averaging` | Buy at $0.50 then $0.60 → avg price = $0.55 |

---

## Bugs Found During Testing

### BUG-001: Floating-point precision
- `2.0000000000000018 <= 2.0` failed
- Fix: Added epsilon tolerance `<= 2.0 + 1e-9`
- Lesson: Never compare floats exactly

### BUG-002: Mock not called due to empty inputs
- Sentiment tests passed empty article lists, so `get_aggregate_sentiment` was never reached
- Fix: Pass dummy articles to trigger the code path
- Lesson: Verify your mocks are actually being called — an untouched mock proves nothing

---

## Running the Tests

```bash
# Activate virtual environment
source venv/bin/activate

# Run all tests
PYTHONPATH=. pytest tests/ -v

# Run one test file
PYTHONPATH=. pytest tests/test_risk_manager.py -v

# Run one specific test
PYTHONPATH=. pytest tests/test_risk_manager.py::TestKellyCriterion::test_basic_kelly_sizing -v
```

The `PYTHONPATH=.` prefix tells Python to look for imports starting from the project root directory. Without it, `from src.trading.risk_manager import RiskManager` would fail.

---

## Cybersecurity Connections

| Testing Concept | Security Parallel |
|----------------|-------------------|
| **Unit testing** | Testing individual security controls (does this firewall rule block the right traffic?) |
| **Mocking** | Test environments that simulate production without risk (pen test sandboxes) |
| **Edge case testing** | Boundary testing in fuzzing (what happens with invalid input?) |
| **Regression testing** | After patching a vulnerability, verify it stays fixed |
| **Test isolation** | Each test runs independently — like isolated network segments |
| **Code coverage** | Security audit coverage — have you tested all critical paths? |
