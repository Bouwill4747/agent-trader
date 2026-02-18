"""
Trade executor — places orders on Polymarket (paper or live).
Paper mode simulates fills at current prices. Live mode sends real orders to the CLOB.
"""

import secrets
from datetime import datetime, timezone
from dataclasses import dataclass

from config.settings import PAPER_TRADING
from src.data.polymarket_client import PolymarketClient
from src.trading.portfolio import Portfolio
from src.trading.risk_manager import RiskDecision
from src.utils.logger import setup_logger
from src.utils.db import insert_trade

logger = setup_logger("executor")


@dataclass
class OrderResult:
    """Result of an order execution attempt."""
    success: bool
    order_id: str
    fill_price: float
    fill_size: float
    paper_trade: bool
    message: str


class Executor:
    """Executes trades — paper or live — and records them."""

    def __init__(self, client: PolymarketClient, portfolio: Portfolio):
        self.client = client
        self.portfolio = portfolio
        self._paper_mode = PAPER_TRADING

        if not self._paper_mode and not client.clob:
            logger.warning(
                "Live trading requested but CLOB client not available — "
                "falling back to paper mode"
            )
            self._paper_mode = True

        mode = "PAPER" if self._paper_mode else "LIVE"
        logger.info("Executor initialized in %s mode", mode)

    @property
    def paper_mode(self) -> bool:
        """Read-only: paper/live mode cannot change after initialization."""
        return self._paper_mode

    async def execute_trade(
        self,
        market_id: str,
        token_id: str,
        question: str,
        side: str,
        price: float,
        risk_decision: RiskDecision,
        direction: str = "BUY_YES",
    ) -> OrderResult:
        """Execute a trade based on a risk-approved decision.

        Args:
            market_id: The market condition ID
            token_id: The outcome token ID (YES or NO token)
            question: Human-readable market question
            side: "BUY" or "SELL"
            price: Target price per share
            risk_decision: Approved RiskDecision with position size and shares
            direction: "BUY_YES" or "BUY_NO" — determines position side

        Returns:
            OrderResult with execution details
        """
        if not risk_decision.approved:
            return OrderResult(
                success=False, order_id="", fill_price=0,
                fill_size=0, paper_trade=self.paper_mode,
                message=f"Trade rejected: {risk_decision.reason}"
            )

        if self.paper_mode:
            result = await self._paper_execute(
                market_id, token_id, question, side,
                price, risk_decision.shares
            )
        else:
            result = await self._live_execute(
                market_id, token_id, question, side,
                price, risk_decision.shares
            )

        # Record in database
        if result.success:
            trade_record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "market_id": market_id,
                "token_id": token_id,
                "question": question,
                "side": side,
                "price": result.fill_price,
                "size": result.fill_size,
                "total_cost": result.fill_price * result.fill_size,
                "order_type": "GTC",
                "status": "filled" if self.paper_mode else "pending",
                "paper_trade": 1 if self.paper_mode else 0,
            }
            await insert_trade(trade_record)

            # Determine position side from direction (not from "BUY" string)
            position_side = "NO" if direction == "BUY_NO" else "YES"
            opened = self.portfolio.open_position(
                market_id=market_id,
                token_id=token_id,
                question=question,
                side=position_side,
                shares=result.fill_size,
                price=result.fill_price,
            )
            if not opened:
                logger.warning("Portfolio rejected position for %s — insufficient cash", market_id)

        return result

    async def _paper_execute(
        self, market_id: str, token_id: str, question: str,
        side: str, price: float, shares: float
    ) -> OrderResult:
        """Simulate a trade fill at the current market price."""

        # In paper mode, we assume instant fill at the requested price
        paper_id = f"PAPER-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"

        logger.info(
            "[PAPER] %s %.0f shares of '%s' @ $%.3f (total: $%.2f)",
            side, shares, question[:40], price, shares * price
        )

        return OrderResult(
            success=True,
            order_id=paper_id,
            fill_price=price,
            fill_size=shares,
            paper_trade=True,
            message="Paper trade filled",
        )

    async def _live_execute(
        self, market_id: str, token_id: str, question: str,
        side: str, price: float, shares: float
    ) -> OrderResult:
        """Place a real order on the Polymarket CLOB."""

        logger.info(
            "[LIVE] Placing %s order: %.0f shares @ $%.3f — %s",
            side, shares, price, question[:40]
        )

        response = self.client.place_order(
            token_id=token_id,
            price=price,
            size=shares,
            side=side,
        )

        if response:
            order_id = response.get("orderID", response.get("id", "unknown"))
            logger.info("[LIVE] Order placed: %s", order_id)
            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=price,
                fill_size=shares,
                paper_trade=False,
                message=f"Live order placed: {order_id}",
            )
        else:
            logger.error("[LIVE] Order failed for %s", question[:40])
            return OrderResult(
                success=False, order_id="", fill_price=0,
                fill_size=0, paper_trade=False,
                message="Live order placement failed",
            )

    async def execute_exit(
        self,
        market_id: str,
        token_id: str,
        question: str,
        shares: float,
        price: float,
        reason: str,
    ) -> OrderResult:
        """Exit (sell) an existing position.

        Args:
            market_id: The market condition ID
            token_id: The outcome token ID
            question: Human-readable market question
            shares: Number of shares to sell
            price: Current market price (sell price)
            reason: Why we're exiting (STOP_LOSS, TAKE_PROFIT)

        Returns:
            OrderResult with execution details
        """
        logger.info(
            "EXIT [%s]: Selling %.0f shares of '%s' @ $%.3f",
            reason, shares, question[:40], price
        )

        if self.paper_mode:
            result = await self._paper_exit(
                market_id, token_id, question, shares, price, reason
            )
        else:
            result = await self._live_exit(
                market_id, token_id, question, shares, price, reason
            )

        if result.success:
            # Record SELL trade in database
            trade_record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "market_id": market_id,
                "token_id": token_id,
                "question": question,
                "side": "SELL",
                "price": result.fill_price,
                "size": result.fill_size,
                "total_cost": result.fill_price * result.fill_size,
                "order_type": "GTC",
                "status": "filled" if self.paper_mode else "pending",
                "paper_trade": 1 if self.paper_mode else 0,
            }
            await insert_trade(trade_record)

            # Close position in portfolio (restores cash, books PnL)
            self.portfolio.close_position(market_id, price)

        return result

    async def _paper_exit(
        self, market_id: str, token_id: str, question: str,
        shares: float, price: float, reason: str
    ) -> OrderResult:
        """Simulate selling shares in paper mode."""
        paper_id = f"PAPER-EXIT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"

        logger.info(
            "[PAPER] SELL %.0f shares of '%s' @ $%.3f (reason: %s)",
            shares, question[:40], price, reason
        )

        return OrderResult(
            success=True,
            order_id=paper_id,
            fill_price=price,
            fill_size=shares,
            paper_trade=True,
            message=f"Paper exit filled ({reason})",
        )

    async def _live_exit(
        self, market_id: str, token_id: str, question: str,
        shares: float, price: float, reason: str
    ) -> OrderResult:
        """Place a real SELL order on the Polymarket CLOB."""
        logger.info(
            "[LIVE] Placing SELL order: %.0f shares @ $%.3f — %s (reason: %s)",
            shares, price, question[:40], reason
        )

        response = self.client.place_order(
            token_id=token_id,
            price=price,
            size=shares,
            side="SELL",
        )

        if response:
            order_id = response.get("orderID", response.get("id", "unknown"))
            logger.info("[LIVE] SELL order placed: %s", order_id)
            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=price,
                fill_size=shares,
                paper_trade=False,
                message=f"Live exit order placed: {order_id} ({reason})",
            )
        else:
            logger.error("[LIVE] SELL order failed for %s", question[:40])
            return OrderResult(
                success=False, order_id="", fill_price=0,
                fill_size=0, paper_trade=False,
                message=f"Live exit order failed ({reason})",
            )

    async def cancel_all(self) -> bool:
        """Emergency: cancel all open orders."""
        if self.paper_mode:
            logger.warning("[PAPER] Cancel all — no real orders to cancel")
            return True
        return self.client.cancel_all_orders()
