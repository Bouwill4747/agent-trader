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
    def get_markets(self, limit: int = 50, active: bool = True) -> list:
        """Fetch active markets from Gamma API.

        Validates each market with Pydantic (HIGH-02) and enforces
        response size limits (HIGH-03).
        """
        try:
            self.gamma_limiter.wait()
            response = self.gamma.get(
                "/markets",
                params={
                    "limit": min(limit, self.MAX_MARKETS),
                    "active": active,
                    "closed": False,
                    "order": "liquidity",
                    "ascending": False,
                },
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
                return float(midpoint)

            # Fallback: direct HTTP call (no auth required)
            resp = self.gamma.get(
                f"{CLOB_API_URL}/midpoint",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("mid", 0)) if isinstance(data, dict) else float(data)

        except (ConnectionError, TimeoutError, OSError, httpx.HTTPError) as e:
            logger.error("Midpoint connection error for %s: %s", token_id, type(e).__name__)
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
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Order placement connection error: %s", type(e).__name__)
            return None
        except (ValueError, KeyError) as e:
            logger.error("Order placement error: %s", e)
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

    def get_open_orders(self) -> list:
        """Get all currently open orders."""
        if not self.clob:
            return []
        try:
            self.clob_limiter.wait()
            orders = self.clob.get_orders()
            return orders if orders else []
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Fetch orders connection error: %s", type(e).__name__)
            return []
        except (ValueError, KeyError) as e:
            logger.error("Fetch orders parse error: %s", e)
            return []

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
