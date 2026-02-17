# Steps 4-7: Wallet Setup & Trading Module — Explained

> Files created:
> - `setup_wallet.py` — One-time wallet and API credential setup
> - `src/trading/portfolio.py` — Tracks positions, balances, PnL
> - `src/trading/risk_manager.py` — Kelly criterion, position limits, drawdown protection
> - `src/trading/executor.py` — Places orders (paper or live)
>
> Together, these files manage all the money. The data layer collects info, the analysis engine makes predictions, and THIS module decides how much to bet and actually places the bets.

---

## The Big Picture

```
Signal Generator says: "BUY YES on Market X, edge = 15%, confidence = high"
         │
         ▼
┌─────────────────────────────────────────────┐
│              TRADING MODULE                  │
│                                              │
│  risk_manager.py                             │
│    → "Is this trade safe?"                   │
│    → "How much should we bet?" (Kelly)       │
│    → Checks: drawdown, exposure, limits      │
│                                              │
│  executor.py                                 │
│    → Paper mode: simulate the fill           │
│    → Live mode: send order to Polymarket     │
│                                              │
│  portfolio.py                                │
│    → Record the position                     │
│    → Update cash balance                     │
│    → Track PnL                               │
└─────────────────────────────────────────────┘
```

---

## File 1: `setup_wallet.py` — Wallet & API Setup

### What it does

A step-by-step interactive script you run once. It:
1. Checks if `.env` exists
2. Checks if you have a private key
3. If yes, derives Polymarket API credentials (signs a message with your key)
4. Optionally saves the credentials to `.env`
5. Prints next steps for funding your wallet

### Key concepts

```python
from py_clob_client.client import ClobClient
client = ClobClient(host, key=private_key, chain_id=POLYGON, signature_type=0)
creds = client.create_or_derive_api_creds()
```

**What happens here (the two-tier auth):**
1. The SDK signs an **EIP-712 structured message** with your private key. This proves you own the wallet without revealing the key.
2. Polymarket's server verifies the signature and returns API credentials (key, secret, passphrase).
3. All future API calls use these credentials with **HMAC-SHA256 signatures** instead of your raw private key.

**Why two tiers?** Your private key controls your entire wallet — if it leaks, everything is gone. The API credentials only grant trading access and can be revoked. This is defense in depth — the same principle as SSH key pairs where you use an SSH certificate for daily access instead of the root key.

```python
save = input("Save these to .env? (y/n): ").strip().lower()
```
- Always asks before modifying files. User stays in control.
- `.strip()` removes accidental whitespace, `.lower()` normalizes to lowercase.

### Generating a wallet programmatically

```python
from eth_account import Account
a = Account.create()
print(f"Address: {a.address}")
print(f"Private Key: {a.key.hex()}")
```
- `eth_account` generates an Ethereum/Polygon keypair.
- The private key is a 256-bit random number. The address is derived from the public key (which is derived from the private key) via Keccak-256 hashing.
- **Same cryptographic principles as in cybersecurity**: asymmetric key pairs, hash functions, digital signatures.

---

## File 2: `src/trading/portfolio.py` — Position & Balance Tracking

### The `@dataclass` decorator

```python
@dataclass
class Position:
    market_id: str
    token_id: str
    question: str
    side: str
    shares: float
    avg_price: float
    current_price: float = 0.0
```

**What is a dataclass?** A Python shortcut for classes that mainly hold data. Instead of writing a full `__init__` method, Python auto-generates one from the field declarations. It also auto-generates `__repr__` (for printing) and `__eq__` (for comparison).

Without `@dataclass`, you'd need:
```python
class Position:
    def __init__(self, market_id, token_id, question, side, shares, avg_price, current_price=0.0):
        self.market_id = market_id
        self.token_id = token_id
        # ... every field manually
```

The dataclass version is cleaner and less error-prone.

### The `@property` decorator

```python
@property
def cost_basis(self) -> float:
    """Total amount paid for this position."""
    return self.shares * self.avg_price
```

**What is @property?** It makes a method behave like a regular attribute. Instead of calling `position.cost_basis()`, you access `position.cost_basis` — no parentheses. It's calculated fresh every time you access it.

**Why use it?** `cost_basis` depends on `shares` and `avg_price`, which can change. If we stored it as a regular attribute, it could become stale. As a property, it's always correct.

### Position math

**Opening a position:**
```
You buy 10 YES shares at $0.55 each
  cost = 10 × $0.55 = $5.50
  cash goes down by $5.50
```

**Adding to a position (averaging):**
```
You already own 10 shares at $0.55 (cost basis: $5.50)
You buy 10 more at $0.60 (cost: $6.00)
  total shares = 20
  avg_price = ($5.50 + $6.00) / 20 = $0.575
```
This is called a **weighted average** — the new average price accounts for how much you bought at each price.

**Closing a position:**
```
You sell 20 shares at $0.70
  proceeds = 20 × $0.70 = $14.00
  cost basis = 20 × $0.575 = $11.50
  realized PnL = $14.00 - $11.50 = +$2.50 profit
  cash goes up by $14.00
```

**Resolution (market ends):**
```
Market: "Will X happen?" — Result: YES
You held 20 YES shares at avg $0.575
  winning shares pay out $1.00 each
  proceeds = 20 × $1.00 = $20.00
  PnL = $20.00 - $11.50 = +$8.50
```
If you held YES and NO won, shares pay $0.00 — total loss of cost basis.

### Unrealized vs Realized PnL

| Type | Definition | Example |
|------|-----------|---------|
| **Unrealized PnL** | Paper profit/loss on open positions. What you WOULD make if you sold now. | You bought at $0.55, it's now $0.70. Unrealized: +$0.15/share. |
| **Realized PnL** | Actual profit/loss from closed positions. Money in or out. | You sold at $0.70 after buying at $0.55. Realized: +$0.15/share. |

The portfolio tracks both. Unrealized PnL changes with every price update. Realized PnL only changes when you close a position or a market resolves.

### Drawdown

```python
@property
def drawdown_pct(self) -> float:
    current = self.total_value
    if current > self.peak_bankroll:
        self.peak_bankroll = current  # New peak!
        return 0.0
    return (self.peak_bankroll - current) / self.peak_bankroll
```

**Drawdown** = how far you've fallen from your best point.

Example:
```
Day 1: Portfolio = $100 (peak = $100, drawdown = 0%)
Day 2: Portfolio = $115 (peak = $115, drawdown = 0%)
Day 3: Portfolio = $95  (peak = $115, drawdown = 17.4%)
Day 4: Portfolio = $92  (peak = $115, drawdown = 20.0%) ← HALT TRADING
```

At 20% drawdown, the risk manager stops all trading. This prevents a bad streak from wiping out the bankroll.

---

## File 3: `src/trading/risk_manager.py` — The Safety Net

This is the most important file for protecting your money. Every trade must pass through here.

### The Kelly Criterion

**The problem:** You found a good bet — the market says 55% but you think it's 70%. How much should you bet?

- Too little → you don't make meaningful money
- Too much → one bad outcome wipes you out
- The Kelly criterion → mathematically optimal amount for long-term growth

**The formula (for binary prediction markets):**
```
Kelly % = edge / (1 - market_price)

Where:
  edge = estimated_prob - market_price
  market_price = current price of the share (0.01 to 0.99)
```

**Example:**
```
Market price: $0.55 (market thinks 55% chance)
Your estimate: 0.70 (you think 70% chance)
Edge: 0.70 - 0.55 = 0.15 (15%)

Kelly % = 0.15 / (1 - 0.55) = 0.15 / 0.45 = 33.3%

Full Kelly says: bet 33.3% of your bankroll
```

**But full Kelly is dangerous!** If your probability estimate is even slightly wrong, you over-bet. That's why we use **fractional Kelly**:

```
Fractional Kelly = Kelly % × 0.25

33.3% × 0.25 = 8.3% of bankroll
On a $100 bankroll: $8.33
```

This sacrifices some theoretical growth for much less volatility. Professionals typically use 0.25x to 0.50x Kelly.

### The six safety checks (in order)

```
Trade proposal arrives
    │
    ├── Check 1: Is drawdown ≥ 20%? → HALT all trading
    ├── Check 2: Are we at max positions (10)? → REJECT
    ├── Check 3: Is edge < 10%? → REJECT (not worth the risk)
    ├── Check 4: Is confidence "low"? → REJECT
    ├── Check 5: Kelly sizing → Calculate position size
    └── Check 6: Hard limits
         ├── Position > 5% of bankroll? → Cap at 5%
         ├── Total exposure > 50%? → Cap at remaining room
         └── Position < $1? → Too small, REJECT
```

Each check catches a different kind of risk:
- **Drawdown check** → Prevents trading during a losing streak
- **Position count** → Prevents over-diversification (spreading too thin)
- **Edge threshold** → Prevents marginal trades that aren't worth transaction costs
- **Confidence filter** → The LLM must be reasonably sure
- **Kelly sizing** → Math-based position sizing
- **Hard limits** → Absolute caps regardless of what Kelly says

### Confidence multiplier

```python
confidence_multiplier = {"medium": 0.7, "high": 1.0}.get(confidence, 0.5)
kelly_sized *= confidence_multiplier
```

If the LLM says "medium confidence," we only use 70% of the Kelly-recommended size. This is an extra layer of conservatism — if the agent isn't sure, it bets less.

### Kill switch

```python
def check_kill_switch(self) -> bool:
    if os.path.exists(KILL_SWITCH_PATH):
        return True
    return False
```

Create the file `data/STOP` and the agent halts at the next cycle. This is a **dead man's switch** — a simple, reliable emergency stop that doesn't require accessing the running process. Even if the terminal is frozen, you can create the file from another terminal.

---

## File 4: `src/trading/executor.py` — Order Execution

### Two modes: Paper vs Live

```python
self.paper_mode = PAPER_TRADING  # From .env, defaults to True
```

| Aspect | Paper Mode | Live Mode |
|--------|-----------|-----------|
| Real money? | No | Yes |
| API calls? | No CLOB calls | Places real orders |
| Fill assumption | Instant at requested price | Depends on order book |
| Purpose | Testing, validation | Actual trading |

**Why paper mode first?** You want to verify the agent makes sensible decisions before risking real money. Run paper mode for days, review the logs, check if signals were accurate, then consider live.

### The execution flow

```python
async def execute_trade(self, ..., risk_decision: RiskDecision) -> OrderResult:
```

1. **Check approval**: If `risk_decision.approved` is False, immediately reject
2. **Route to mode**: Paper → `_paper_execute()`, Live → `_live_execute()`
3. **Record to database**: Call `insert_trade()` to create an audit trail
4. **Update portfolio**: Call `portfolio.open_position()` to track the new position

### Paper execution

```python
paper_id = f"PAPER-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{market_id[:8]}"
```
- Generates a fake order ID with timestamp + market ID prefix
- `%Y%m%d%H%M%S` → `20260215143000` (year-month-day-hour-minute-second)
- Assumes instant fill at the requested price (optimistic but fine for testing)

### Live execution

```python
response = self.client.place_order(
    token_id=token_id, price=price, size=shares, side=side,
)
```
- Calls the CLOB API through our Polymarket client
- The order goes into the order book as a GTC (Good-Til-Cancelled) limit order
- The response contains an `orderID` for tracking

### The `@dataclass` pattern for results

```python
@dataclass
class OrderResult:
    success: bool
    order_id: str
    fill_price: float
    fill_size: float
    paper_trade: bool
    message: str
```

Both paper and live execution return the same `OrderResult` structure. The rest of the code doesn't need to know which mode was used — it just checks `result.success` and reads the fields. This is **polymorphism** — different implementations, same interface.

---

## Cybersecurity Connections

| Concept | In This Module | In Cybersecurity |
|---------|---------------|-----------------|
| **Defense in depth** | Six risk checks, each catching different risks | Multiple security layers (firewall → IDS → WAF → app-level) |
| **Kill switch** | File-based emergency stop | Network kill switches, circuit breakers in incident response |
| **Least privilege** | API credentials only grant trading access, not full wallet control | Service accounts should have minimum required permissions |
| **Audit trail** | Every trade recorded to database with timestamp | SIEM logs, security event recording |
| **Graceful degradation** | No private key → paper mode only (doesn't crash) | Services should degrade gracefully, not fail catastrophically |
| **Input validation** | Price must be 0-1, edge must exceed threshold, limits checked | Validate all inputs at trust boundaries |
| **Dead man's switch** | `data/STOP` file halts the agent | Similar to watchdog timers that trigger alerts if a system stops responding |

---

## Key Python Concepts Introduced

| Concept | Where | What It Means |
|---------|-------|---------------|
| **`@dataclass`** | Position, RiskDecision, OrderResult | Auto-generates `__init__`, `__repr__`, `__eq__` from field declarations. Less boilerplate for data-holding classes. |
| **`@property`** | Portfolio metrics | Makes a method accessible as an attribute (no parentheses). Recalculated every time you access it. |
| **f-strings** | Logging throughout | `f"Value: {variable:.2f}"` — inline string formatting. `.2f` = 2 decimal places, `.1%` = percentage with 1 decimal. |
| **`dict[str, Position]`** | `self.positions` | Type hint for a dictionary where keys are strings and values are Position objects. |
| **Weighted average** | `open_position()` | When adding to a position at a different price, the new average accounts for quantity at each price. |
| **Polymorphism** | Paper vs Live executor | Both return the same `OrderResult` type — the caller doesn't need to know which implementation ran. |
| **Guard clauses** | Risk checks | Early returns for failure cases. Keeps the "happy path" code unindented and readable. |
