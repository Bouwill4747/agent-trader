"""
Polymarket API client — wraps both Gamma (discovery) and CLOB (trading) APIs.

Gamma API: Public, no auth. Used to find markets, get metadata.
CLOB API: Requires auth. Used to get prices, place orders, manage positions.
"""

import re
import time
import threading
import httpx
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, ApiCreds
try:
    from py_clob_client.exceptions import PolyApiException
except ImportError:
    PolyApiException = Exception
from py_clob_client.order_builder.constants import BUY, SELL
from py_clob_client.constants import POLYGON

from src.data.models import GammaMarket
from config.settings import (
    GAMMA_API_URL,
    CLOB_API_URL,
    POLYGON_CHAIN_ID,
    POLYGON_PRIVATE_KEY,
    POLYMARKET_API_KEY,
    POLYMARKET_API_SECRET,
    POLYMARKET_API_PASSPHRASE,
    CLOB_RATE_LIMIT,
    ORDERBOOK_RATE_LIMIT,
)
from src.utils.logger import setup_logger

logger = setup_logger("polymarket_client")


class RateLimiter:
    """Thread-safe token-bucket rate limiter. Prevents API bans."""

    def __init__(self, requests_per_second: int):
        self.min_interval = 1.0 / requests_per_second
        self.last_request_time = 0.0
        self._lock = threading.Lock()

    def wait(self):
        """Block until enough time has passed since the last request."""
        with self._lock:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_request_time = time.time()


_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_\-]+$')


def _validate_id(value: str, name: str = "id") -> str:
    """Validate that an identifier contains only safe characters (MEDIUM-02)."""
    if not value:
        raise ValueError(f"Empty {name}")
    if len(value) > 256:
        raise ValueError(f"{name} exceeds max length: {len(value)}")
    if not _ID_PATTERN.match(value):
        raise ValueError(f"Invalid characters in {name}: {value!r}")
    return value


class PolymarketClient:
    """Unified client for Gamma (discovery) and CLOB (trading) APIs."""

    def __init__(self):
        # Gamma API — public HTTP client, no auth
        self.gamma = httpx.Client(
            base_url=GAMMA_API_URL,
            timeout=30.0,
            headers={"Accept": "application/json"},
        )

        # CLOB API — authenticated trading client
        self.clob = None
        if POLYGON_PRIVATE_KEY:
            try:
                self.clob = ClobClient(
                    CLOB_API_URL,
                    key=POLYGON_PRIVATE_KEY,
                    chain_id=POLYGON_CHAIN_ID,
                    signature_type=0,  # 0 = EOA wallet (MetaMask-style)
                )

                # Use stored credentials if available, otherwise derive fresh
                if POLYMARKET_API_KEY and POLYMARKET_API_SECRET:
                    creds = ApiCreds(
                        api_key=POLYMARKET_API_KEY,
                        api_secret=POLYMARKET_API_SECRET,
                        api_passphrase=POLYMARKET_API_PASSPHRASE,
                    )
                    self.clob.set_api_creds(creds)
                    logger.info("CLOB client authenticated with stored credentials")
                else:
                    creds = self.clob.create_or_derive_api_creds()
                    self.clob.set_api_creds(creds)
                    logger.info("CLOB client authenticated (derived fresh credentials)")

                # Ensure ERC-1155 approval is set for conditional tokens.
                # Without setApprovalForAll, SELL orders always fail with
                # "not enough balance / allowance". This is a one-time on-chain
                # setup per wallet — idempotent, skipped if already approved.
                self._ensure_conditional_token_approval()

            except (PolyApiException, ValueError, KeyError, ConnectionError, TimeoutError, OSError) as e:
                logger.error("CLOB authentication failed: %s", e)
                self.clob = None
        else:
            logger.warning("No private key — CLOB client disabled (paper trading only)")

        # Rate limiters — separate for each API to avoid unnecessary throttling
        self.gamma_limiter = RateLimiter(CLOB_RATE_LIMIT)
        self.clob_limiter = RateLimiter(CLOB_RATE_LIMIT)
        self.book_limiter = RateLimiter(ORDERBOOK_RATE_LIMIT)

    # ──────────────────────────────────────────────
    # GAMMA API — Market Discovery (public, no auth)
    # ──────────────────────────────────────────────

    # Response size limits (HIGH-03)
    MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5MB
    MAX_MARKETS = 200

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    )
    def get_markets(self, limit: int = 50, active: bool = True,
                    end_date_min: str | None = None,
                    end_date_max: str | None = None) -> list:
        """Fetch active markets from Gamma API.

        Args:
            limit: Max markets to return.
            active: Only return active markets.
            end_date_min: ISO8601 string — only markets resolving after this date.
            end_date_max: ISO8601 string — only markets resolving before this date.

        Validates each market with Pydantic (HIGH-02) and enforces
        response size limits (HIGH-03).
        """
        try:
            self.gamma_limiter.wait()
            params = {
                "limit": min(limit, self.MAX_MARKETS),
                "active": active,
                "closed": False,
                "order": "liquidity",
                "ascending": False,
            }
            if end_date_min:
                params["end_date_min"] = end_date_min
            if end_date_max:
                params["end_date_max"] = end_date_max
            response = self.gamma.get(
                "/markets",
                params=params,
            )
            response.raise_for_status()

            # Check response size before parsing (HIGH-03)
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > self.MAX_RESPONSE_BYTES:
                logger.error("Gamma response too large: %s bytes", content_length)
                return []

            raw = response.json()
            if not isinstance(raw, list):
                logger.error("Gamma API returned non-list: %s", type(raw).__name__)
                return []

            if len(raw) > self.MAX_MARKETS:
                logger.warning("Truncating %d markets to %d", len(raw), self.MAX_MARKETS)
                raw = raw[:self.MAX_MARKETS]

            # Validate each market with Pydantic (HIGH-02)
            markets = []
            for item in raw:
                try:
                    validated = GammaMarket(**item)
                    markets.append(validated.model_dump(by_alias=True))
                except ValidationError as e:
                    logger.debug("Skipping invalid market: %s", e.errors()[0]["msg"])

            logger.info("Fetched %d valid markets from Gamma API (of %d raw)", len(markets), len(raw))
            return markets
        except httpx.HTTPStatusError as e:
            logger.error("Gamma API HTTP %d on /markets", e.response.status_code)
            return []
        except httpx.RequestError as e:
            logger.error("Gamma API connection error: %s", type(e).__name__)
            return []

    def get_market_by_id(self, market_id: str) -> dict | None:
        """Fetch a single market by its condition ID."""
        try:
            _validate_id(market_id, "market_id")
            self.gamma_limiter.wait()
            response = self.gamma.get(f"/markets/{market_id}")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("Gamma API HTTP %d on /markets/%s", e.response.status_code, market_id)
            return None
        except httpx.RequestError as e:
            logger.error("Gamma API connection error for /markets/%s: %s", market_id, type(e).__name__)
            return None

    def get_events(self, limit: int = 20, active: bool = True) -> list:
        """Fetch active events (an event can contain multiple markets)."""
        try:
            self.gamma_limiter.wait()
            response = self.gamma.get(
                "/events",
                params={"limit": limit, "active": active},
            )
            response.raise_for_status()
            events = response.json()
            logger.info("Fetched %d events from Gamma API", len(events))
            return events
        except httpx.HTTPStatusError as e:
            logger.error("Gamma API HTTP %d on /events", e.response.status_code)
            return []
        except httpx.RequestError as e:
            logger.error("Gamma API connection error on /events: %s", type(e).__name__)
            return []

    def search_markets(self, query: str, limit: int = 10) -> list:
        """Search markets by keyword (e.g., 'Bitcoin', 'election')."""
        try:
            self.gamma_limiter.wait()
            response = self.gamma.get(
                "/markets",
                params={"limit": limit, "active": True, "closed": False},
            )
            response.raise_for_status()
            all_markets = response.json()

            # Client-side filter — Gamma API doesn't have a search endpoint
            query_lower = query.lower()
            filtered = [
                m for m in all_markets
                if query_lower in m.get("question", "").lower()
                or query_lower in m.get("description", "").lower()
            ]
            logger.info("Search '%s': %d matches out of %d markets", query, len(filtered), len(all_markets))
            return filtered
        except httpx.HTTPStatusError as e:
            logger.error("Market search HTTP %d", e.response.status_code)
            return []
        except httpx.RequestError as e:
            logger.error("Market search connection error: %s", type(e).__name__)
            return []

    # ──────────────────────────────────────────────
    # CLOB API — Price Data (public endpoints)
    # ──────────────────────────────────────────────

    def get_order_book(self, token_id: str) -> dict | None:
        """Get the full order book (bids and asks) for a token."""
        if not self.clob:
            logger.warning("CLOB client not initialized — cannot fetch order book")
            return None
        try:
            _validate_id(token_id, "token_id")
            self.book_limiter.wait()
            book = self.clob.get_order_book(token_id)
            return book
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Order book connection error for %s: %s", token_id, type(e).__name__)
            return None
        except (ValueError, KeyError) as e:
            logger.error("Order book parse error for %s: %s", token_id, e)
            return None

    def get_midpoint(self, token_id: str) -> float | None:
        """Get the midpoint price (average of best bid and best ask).

        Uses the CLOB SDK if authenticated, otherwise falls back to a
        direct HTTP call (the midpoint endpoint is public).
        """
        try:
            _validate_id(token_id, "token_id")
            self.clob_limiter.wait()

            if self.clob:
                midpoint = self.clob.get_midpoint(token_id)
                # SDK returns {"mid": "0.55"} dict, not a raw number
                if isinstance(midpoint, dict):
                    return float(midpoint.get("mid", 0))
                return float(midpoint)

            # Fallback: direct HTTP call (no auth required)
            resp = self.gamma.get(
                f"{CLOB_API_URL}/midpoint",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("mid", 0)) if isinstance(data, dict) else float(data)

        except (PolyApiException, ConnectionError, TimeoutError, OSError, httpx.HTTPError) as e:
            logger.error("Midpoint error for %s: %s", token_id, e)
            return None
        except (ValueError, TypeError) as e:
            logger.error("Midpoint parse error for %s: %s", token_id, e)
            return None

    def get_price(self, token_id: str) -> dict | None:
        """Get current best bid and ask prices."""
        if not self.clob:
            return None
        try:
            self.clob_limiter.wait()
            price = self.clob.get_price(token_id)
            return price
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Price connection error for %s: %s", token_id, type(e).__name__)
            return None
        except (ValueError, KeyError) as e:
            logger.error("Price parse error for %s: %s", token_id, e)
            return None

    # ──────────────────────────────────────────────
    # CLOB API — Trading (authenticated)
    # ──────────────────────────────────────────────

    def _ensure_conditional_token_approval(self) -> None:
        """Set ERC-1155 setApprovalForAll for Polymarket's exchange contracts.

        Polymarket conditional tokens are ERC-1155. To SELL them via the CLOB,
        the exchange contracts must be approved as operators on the CTF contract.
        This is a one-time on-chain setup — if already approved, nothing happens.

        Contracts approved:
          CTF Exchange       (0x4bFb41d5B...) — regular markets
          Neg Risk Exchange  (0xC5d563A3...) — neg-risk markets
          Relayer            (0xd91E80cF...) — order relay
        """
        try:
            from web3 import Web3

            RPC_URLS = [
                "https://rpc-mainnet.matic.quiknode.pro",
                "https://rpc.ankr.com/polygon",
                "https://polygon.llamarpc.com",
            ]
            w3 = None
            for rpc in RPC_URLS:
                try:
                    candidate = Web3(Web3.HTTPProvider(rpc))
                    if candidate.is_connected():
                        w3 = candidate
                        break
                except Exception:
                    continue
            if w3 is None:
                logger.warning("ERC-1155 approval check skipped — no Polygon RPC available")
                return

            wallet = self.clob.get_address()
            ctf_address = self.clob.get_conditional_address()
            operators = [
                self.clob.get_exchange_address(),          # CTF Exchange
                "0xC5d563A36AE78145C45a50134d48A1215220f80a",  # Neg Risk Exchange
                "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",  # Relayer
            ]

            abi = [
                {"name": "isApprovedForAll", "type": "function",
                 "inputs": [{"name": "owner", "type": "address"}, {"name": "operator", "type": "address"}],
                 "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view"},
                {"name": "setApprovalForAll", "type": "function",
                 "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
                 "outputs": [], "stateMutability": "nonpayable"},
            ]
            ctf = w3.eth.contract(address=ctf_address, abi=abi)

            nonce = w3.eth.get_transaction_count(wallet)
            gas_price = w3.eth.gas_price

            for operator in operators:
                if ctf.functions.isApprovedForAll(wallet, operator).call():
                    continue  # already approved
                logger.info("Setting ERC-1155 approval for operator %s...", operator)
                tx = ctf.functions.setApprovalForAll(operator, True).build_transaction({
                    "from": wallet, "nonce": nonce,
                    "gas": 60000, "gasPrice": gas_price,
                })
                signed = w3.eth.account.sign_transaction(tx, private_key=POLYGON_PRIVATE_KEY)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                logger.info("ERC-1155 approval set — tx: %s", tx_hash.hex())
                nonce += 1

            logger.info("ERC-1155 conditional token approvals verified")

        except Exception as e:
            logger.warning("ERC-1155 approval check failed (SELL may not work): %s", e)

    def place_order(self, token_id: str, price: float, size: float, side: str) -> dict | None:
        """Place a limit order on the CLOB.

        Args:
            token_id: The outcome token to trade
            price: Price per share (0.01 to 0.99)
            size: Number of shares
            side: "BUY" or "SELL"

        Returns:
            Order response dict or None if failed
        """
        if not self.clob:
            logger.error("Cannot place order — CLOB client not initialized")
            return None

        order_side = BUY if side.upper() == "BUY" else SELL

        try:
            self.clob_limiter.wait()
            response = self.clob.create_and_post_order(
                OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=order_side,
                )
            )
            logger.info(
                "Order placed: %s %.0f shares @ $%.2f — %s",
                side, size, price, response
            )
            return response
        except PolyApiException as e:
            logger.error("Order placement rejected by CLOB: %s", e)
            return None
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Order placement connection error: %s", type(e).__name__)
            return None
        except (ValueError, KeyError) as e:
            logger.error("Order placement error: %s", e)
            return None

    def get_token_balance(self, token_id: str) -> float | None:
        """Return how many conditional tokens we hold for token_id.

        Used by the GTC reconciliation step to detect orders that filled
        silently after placement. Returns None on any error.
        """
        if not self.clob:
            return None
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            self.clob_limiter.wait()
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL, token_id=token_id
            )
            result = self.clob.get_balance_allowance(params=params)
            # CLOB returns token balances scaled by 1,000,000 (same as USDC)
            raw = int(result.get("balance", 0))
            return raw / 1_000_000
        except Exception as e:
            logger.warning("Token balance check failed for %s: %s", token_id[:12], e)
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order by its ID."""
        if not self.clob:
            return False
        try:
            _validate_id(order_id, "order_id")
            self.clob_limiter.wait()
            self.clob.cancel(order_id)
            logger.info("Order cancelled: %s", order_id)
            return True
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Cancel connection error for %s: %s", order_id, type(e).__name__)
            return False
        except (ValueError, KeyError) as e:
            logger.error("Cancel failed for %s: %s", order_id, e)
            return False

    def cancel_all_orders(self) -> bool:
        """Emergency: cancel all open orders."""
        if not self.clob:
            return False
        try:
            self.clob_limiter.wait()
            self.clob.cancel_all()
            logger.warning("ALL ORDERS CANCELLED — emergency stop triggered")
            return True
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Cancel all connection error: %s", type(e).__name__)
            return False
        except (ValueError, KeyError) as e:
            logger.error("Cancel all failed: %s", e)
            return False

    def get_open_orders(self) -> list | None:
        """Get all currently open orders.

        Returns:
            List of open order dicts, empty list if no orders,
            or None if the API call failed (caller should not assume
            anything about open order state in that case).
        """
        if not self.clob:
            return []
        try:
            self.clob_limiter.wait()
            orders = self.clob.get_orders()
            return orders if orders else []
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Fetch orders connection error: %s", type(e).__name__)
            return None
        except (ValueError, KeyError) as e:
            logger.error("Fetch orders parse error: %s", e)
            return None

    # ──────────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────────

    def close(self):
        """Close HTTP connections."""
        self.gamma.close()
        logger.info("Polymarket client connections closed")

    def __del__(self):
        """Ensure HTTP connections are cleaned up."""
        try:
            self.gamma.close()
        except Exception:
            pass
