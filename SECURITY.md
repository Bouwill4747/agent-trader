# Security Policy

## Threat Model

This trading agent handles **cryptocurrency private keys**, **real financial orders**, and **multiple API credentials**. The primary threats are:

1. **Credential theft** — Private key or API keys exfiltrated via log files, memory dumps, or supply chain attacks
2. **Adversarial manipulation** — Prompt injection via crafted news/Reddit content causing bad trades
3. **Unauthorized live trading** — Accidental or malicious switch from paper to live mode
4. **Dependency vulnerabilities** — RCE or data exfiltration via compromised PyPI packages

## Credential Management

| Credential | Storage | Usage |
|------------|---------|-------|
| `POLYGON_PRIVATE_KEY` | `.env` (chmod 600) | Signs blockchain transactions |
| `POLYMARKET_API_*` | `.env` | CLOB trading authentication |
| `ANTHROPIC_API_KEY` | `.env` | Claude API access |
| `NEWS_API_KEY` | `.env` | NewsAPI access |
| `REDDIT_CLIENT_*` | `.env` | Reddit API access |

**Rules:**
- `.env` is in `.gitignore` — never committed to version control
- `.env` permissions are enforced to `600` at startup (`main.py`)
- Log files redact secrets via `SecretRedactionFilter` (Ethereum keys, API keys, passwords)
- `setup_wallet.py` masks credentials in terminal output

## Safety Controls

### Paper Trading Default
- `PAPER_TRADING=true` is the default in `.env.example` and `config/settings.py`
- Switching to live trading requires explicit `PAPER_TRADING=false` AND typing "yes" at the confirmation prompt
- `paper_mode` is a read-only property on the Executor — cannot be changed at runtime

### Kill Switch
- Create `data/STOP` to halt the agent
- Checked before each cycle AND before each individual trade
- On detection: cancels all in-flight orders, then shuts down
- File path is absolute (anchored to `_PROJECT_ROOT`) — works from any working directory

### Risk Limits (hardcoded, not configurable via env)
- 0.25x Kelly fraction (conservative sizing)
- 5% max per position
- 50% max total exposure
- 10 max concurrent positions
- 20% drawdown halts all trading
- 10% minimum edge threshold

### Error Escalation
- 5 consecutive cycle failures halts the agent
- Prevents silent failure loops

## Emergency Procedures

### Stop the Agent Immediately
```bash
# Option 1: Kill switch (graceful — cancels orders first)
touch data/STOP

# Option 2: SIGINT (graceful shutdown handler)
kill -INT <pid>

# Option 3: Force kill (last resort — orders may remain open)
kill -9 <pid>
```

### Cancel All Open Orders
```python
from src.data.polymarket_client import PolymarketClient
client = PolymarketClient()
client.cancel_all_orders()
```

### Rotate Credentials
1. Generate new private key wallet (if compromised)
2. Update `.env` with new credentials
3. Run `python setup_wallet.py` to re-derive CLOB credentials
4. Restart the agent

## Input Validation

- **API responses**: Validated with Pydantic models (`src/data/models.py`) — prices clamped to [0, 1], volumes validated as non-negative, token IDs checked for safe characters
- **ID parameters**: Regex-validated to prevent path traversal (`_validate_id` in `polymarket_client.py`)
- **External text**: Sanitized before LLM prompts — control characters stripped, length truncated, XML delimiters separate data from instructions
- **SQL**: All queries use parameterized `?` placeholders — no SQL injection risk

## Dependency Security

- All dependencies pinned to exact versions in `requirements.txt`
- `langchain-core` pinned to `>=1.2.13` for CVE-2025-68664 (serialization injection RCE)
- FinBERT model pinned to specific revision hash with `trust_remote_code=False`
- Run `pip audit` periodically to check for new CVEs

## Reporting Vulnerabilities

If you discover a security vulnerability in this project, please:
1. Do NOT open a public GitHub issue
2. Email the maintainer directly with details
3. Include steps to reproduce and potential impact

## Security Audit History

| Date | Scope | Findings | Status |
|------|-------|----------|--------|
| 2026-02-15 | Full codebase | 7C, 10H, 12M, 8L (python analysis) | All Critical/High fixed |
| 2026-02-15 | Security audit | 3C, 5H, 6M, 4L, 5I | All Critical/High fixed |
| 2026-02-15 | API security | 3C, 6H, 8M, 5L, 2I | All Critical/High fixed |
