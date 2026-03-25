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
from src.utils.db import insert_trade, update_trade_outcome

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
    filled: bool = True  # False for GTC "live" orders still pending in the book


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
        estimated_prob: float = 0.0,
        confidence: str = "",
        reasoning: str = "",
        edge: float = 0.0,
        market_theme: str = "",
        resolution_type: str = "",
        resolution_clarity_score: int = 0,
        current_price_yes: float = 0.0,
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
            position_side = "NO" if direction == "BUY_NO" else "YES"
            # Direction-aware edge: always positive when we have an edge.
            # signal.edge = estimated_prob_yes - price_yes (negative for valid BUY_NO).
            # Storing the raw signal.edge for BUY_NO trades makes all calibration data look
            # like negative EV. Store the side-consistent edge instead.
            edge_for_side = edge if direction != "BUY_NO" else -edge
            # Canonical YES price at signal time — the reference point for all analytics.
            # For BUY_YES: current_price_yes == execution price.
            # For BUY_NO: execution price is the NO token price; YES price is 1 - NO price.
            market_price_yes = current_price_yes if current_price_yes > 0 else (
                result.fill_price if direction != "BUY_NO" else 1.0 - result.fill_price
            )
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
                "status": "filled" if (self.paper_mode or result.filled) else "pending",
                "paper_trade": 1 if self.paper_mode else 0,
                "claude_reasoning": reasoning or None,
                "estimated_prob": estimated_prob or None,
                "confidence": confidence or None,
                "edge": edge_for_side or None,
                "market_theme": market_theme or None,
                "resolution_type": resolution_type or None,
                "resolution_clarity_score": resolution_clarity_score or None,
                "position_side": position_side,
                "market_price_yes": market_price_yes or None,
            }
            await insert_trade(trade_record)

            # Only open portfolio position if the order actually filled.
            # GTC orders with status="live" are resting in the book — we don't
            # hold the tokens yet, so we must not track them as open positions.
            if result.filled:
                position_side = "NO" if direction == "BUY_NO" else "YES"
                opened = self.portfolio.open_position(
                    market_id=market_id,
                    token_id=token_id,
                    question=question,
                    side=position_side,
                    shares=result.fill_size,
                    price=result.fill_price,
                    estimated_prob=estimated_prob,
                )
                if not opened:
                    logger.warning("Portfolio rejected position for %s — insufficient cash", market_id)
            else:
                logger.info(
                    "GTC order placed for '%s' — pending fill, position not tracked yet",
                    question[:40]
                )

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

        try:
            response = self.client.place_order(
                token_id=token_id,
                price=price,
                size=shares,
                side=side,
            )
        except Exception as e:
            logger.error("[LIVE] BUY order exception for %s: %s", question[:40], e)
            return OrderResult(
                success=False, order_id="", fill_price=0,
                fill_size=0, paper_trade=False,
                message=f"BUY order exception: {e}",
            )

        if response:
            order_id = response.get("orderID", response.get("id", "unknown"))
            status = response.get("status", "")
            # "matched" = filled immediately; "live" = GTC resting in book (tokens NOT yet received)
            filled = (status == "matched")
            if not filled:
                logger.warning(
                    "[LIVE] GTC order pending (status=%s) — tokens not received yet, not tracking: %s",
                    status, question[:40]
                )
            logger.info("[LIVE] Order placed: %s (status=%s, filled=%s)", order_id, status, filled)
            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=price,
                fill_size=shares,
                paper_trade=False,
                message=f"Live order placed: {order_id} (status={status})",
                filled=filled,
            )
        else:
            logger.error("[LIVE] Order failed for %s — CLOB response: %r", question[:40], response)
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

            if self.paper_mode:
                # Paper mode: assume instant fill — close the position immediately
                self.portfolio.close_position(market_id, price)
                outcome = 1 if reason == "TAKE_PROFIT" else 0
                try:
                    await update_trade_outcome(market_id, outcome=outcome, exit_reason=reason)
                except Exception as e:
                    logger.warning("Failed to record trade outcome for %s: %s", market_id, e)
            else:
                # Live mode: GTC SELL order is now resting in the book.
                # Tokens are still held until the order fills — do NOT close the position yet.
                # _reconcile_sell_orders() will call close_position() when CLOB balance drops to 0.
                self.portfolio.mark_selling(market_id, price=result.fill_price, reason=reason)

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
        import math
        # CLOB only accepts integer share counts. Kelly sizing produces fractions
        # and GTC fills may return slightly fewer tokens than ordered — floor to
        # the nearest whole number so we never try to sell more than we hold.
        shares = math.floor(shares)
        if shares < 1:
            logger.warning(
                "[LIVE] SELL skipped for '%s' — floored shares < 1 (%.3f → 0)",
                question[:40], shares,
            )
            return OrderResult(
                success=False, order_id="", fill_price=0,
                fill_size=0, paper_trade=False,
                message="SELL skipped: floored share count < 1",
            )

        # Price strategy depends on exit reason:
        #   STOP_LOSS  → use the best bid for immediate fill (speed over price)
        #   TAKE_PROFIT → use the midpoint (better price; stale-order logic will
        #                 step down toward bid if it doesn't fill within a cycle)
        if reason == "STOP_LOSS":
            bid = self.client.get_best_bid(token_id)
            if bid and bid > 0:
                if abs(bid - price) > 0.001:
                    logger.info(
                        "[LIVE] STOP_LOSS: using best bid $%.3f (midpoint $%.3f) for immediate fill",
                        bid, price,
                    )
                price = bid

        logger.info(
            "[LIVE] Placing SELL order: %d shares @ $%.3f — %s (reason: %s)",
            shares, price, question[:40], reason
        )

        try:
            response = self.client.place_order(
                token_id=token_id,
                price=price,
                size=shares,
                side="SELL",
            )
        except Exception as e:
            # "not enough balance / allowance" means we don't hold these tokens —
            # most likely the original BUY was a GTC order that never filled.
            logger.error("[LIVE] SELL order failed (phantom position?): %s", e)
            return OrderResult(
                success=False, order_id="", fill_price=0,
                fill_size=0, paper_trade=False,
                message=f"SELL failed: {e}",
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
            logger.error("[LIVE] SELL order failed for %s — CLOB response: %r", question[:40], response)
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
