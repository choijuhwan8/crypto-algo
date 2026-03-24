"""
Paper execution engine.

* Simulates instant fills at last close price (no slippage model yet).
* Applies fee scenarios: default FEE_BPS, plus optional stress passes at
  5 bps and 10 bps (used by the validator).
* Enforces MIN_NOTIONAL and MAX_CONCURRENT_PAIRS.
* Position sizing: equity × MAX_POSITION_PCT × LEVERAGE per pair-leg A;
  leg B is scaled by |beta| so the legs are dollar-neutral in log-space.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import (
    FEE_BPS,
    INITIAL_CAPITAL,
    LEVERAGE,
    MAX_CONCURRENT_PAIRS,
    MAX_POSITION_PCT,
    STOP_LOSS_PCT,
)

logger = logging.getLogger(__name__)

MIN_NOTIONAL = 10.0  # USD – minimum per leg


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass
class Order:
    order_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    pair_key: str = ""
    symbol: str = ""
    side: str = ""          # "BUY" | "SELL"
    notional: float = 0.0
    price: float = 0.0
    quantity: float = 0.0
    fee: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Position:
    position_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    pair_key: str = ""
    sym_a: str = ""
    sym_b: str = ""
    direction: str = ""     # "LONG_SPREAD" | "SHORT_SPREAD"
    entry_price_a: float = 0.0
    entry_price_b: float = 0.0
    notional_a: float = 0.0
    notional_b: float = 0.0
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    entry_zscore: float = 0.0
    pnl: float = 0.0
    status: str = "OPEN"

    # ------------------------------------------------------------------

    def unrealized_pnl(self, price_a: float, price_b: float) -> float:
        if self.direction == "LONG_SPREAD":
            pnl_a = self.notional_a * (price_a - self.entry_price_a) / self.entry_price_a
            pnl_b = self.notional_b * (self.entry_price_b - price_b) / self.entry_price_b
        else:
            pnl_a = self.notional_a * (self.entry_price_a - price_a) / self.entry_price_a
            pnl_b = self.notional_b * (price_b - self.entry_price_b) / self.entry_price_b
        self.pnl = pnl_a + pnl_b
        return self.pnl

    def pnl_pct(self, price_a: float, price_b: float) -> float:
        invested = self.notional_a + self.notional_b
        if invested <= 0:
            return 0.0
        return self.unrealized_pnl(price_a, price_b) / invested

    def is_stop_loss(self, price_a: float, price_b: float) -> bool:
        return self.pnl_pct(price_a, price_b) < -STOP_LOSS_PCT


# ------------------------------------------------------------------
# Engine
# ------------------------------------------------------------------


class PaperExecutionEngine:
    def __init__(self, state_manager) -> None:
        self.state = state_manager
        self._orders: List[Order] = []

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def enter_position(
        self,
        pair_info: Dict,
        signal_str: str,
        stats: Dict,
        fee_bps: float = FEE_BPS,
    ) -> Optional[Position]:
        """
        Open a new position for *pair_info*.

        Returns the Position on success, None on any rejection.
        """
        sym_a = pair_info["sym_a"]
        sym_b = pair_info["sym_b"]
        pair_key = f"{sym_a}-{sym_b}"

        open_positions = self.state.get_open_positions()
        if pair_key in open_positions:
            logger.debug(f"enter_position: already open for {pair_key}")
            return None

        if len(open_positions) >= MAX_CONCURRENT_PAIRS:
            logger.debug(f"enter_position: max concurrent pairs reached")
            return None

        equity = self.state.get_equity()
        notional_base = max(
            equity * MAX_POSITION_PCT / MAX_CONCURRENT_PAIRS, MIN_NOTIONAL
        )
        notional_a = notional_base * LEVERAGE
        beta = abs(pair_info.get("beta", stats.get("beta", 1.0)))
        notional_b = notional_a * beta

        if notional_a < MIN_NOTIONAL or notional_b < MIN_NOTIONAL:
            logger.warning(f"enter_position: notional too small ({notional_a:.1f})")
            return None

        price_a = stats["price_a"]
        price_b = stats["price_b"]

        if signal_str == "LONG_SPREAD":
            order_a = _make_order(pair_key, sym_a, "BUY", notional_a, price_a, fee_bps)
            order_b = _make_order(pair_key, sym_b, "SELL", notional_b, price_b, fee_bps)
        else:
            order_a = _make_order(pair_key, sym_a, "SELL", notional_a, price_a, fee_bps)
            order_b = _make_order(pair_key, sym_b, "BUY", notional_b, price_b, fee_bps)

        total_fee = order_a.fee + order_b.fee

        pos = Position(
            pair_key=pair_key,
            sym_a=sym_a,
            sym_b=sym_b,
            direction=signal_str,
            entry_price_a=price_a,
            entry_price_b=price_b,
            notional_a=notional_a,
            notional_b=notional_b,
            entry_zscore=stats.get("zscore", 0.0),
        )

        self._orders.extend([order_a, order_b])
        self.state.add_position(pos)
        self.state.deduct_fees(total_fee)
        self.state.log_order(order_a)
        self.state.log_order(order_b)

        logger.info(
            f"ENTRY {signal_str} {pair_key} | "
            f"z={pos.entry_zscore:.2f} | notional={notional_a:.0f} | fee={total_fee:.2f}"
        )
        return pos

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def exit_position(
        self,
        position: Position,
        stats: Dict,
        fee_bps: float = FEE_BPS,
        reason: str = "signal",
    ) -> float:
        """
        Close *position*.  Returns realised P&L (after fees).
        """
        price_a = stats["price_a"]
        price_b = stats["price_b"]

        if position.direction == "LONG_SPREAD":
            order_a = _make_order(
                position.pair_key, position.sym_a, "SELL",
                position.notional_a, price_a, fee_bps
            )
            order_b = _make_order(
                position.pair_key, position.sym_b, "BUY",
                position.notional_b, price_b, fee_bps
            )
        else:
            order_a = _make_order(
                position.pair_key, position.sym_a, "BUY",
                position.notional_a, price_a, fee_bps
            )
            order_b = _make_order(
                position.pair_key, position.sym_b, "SELL",
                position.notional_b, price_b, fee_bps
            )

        total_fee = order_a.fee + order_b.fee
        gross_pnl = position.unrealized_pnl(price_a, price_b)
        net_pnl = gross_pnl - total_fee

        position.status = "CLOSED"
        self._orders.extend([order_a, order_b])
        self.state.close_position(position, net_pnl, reason=reason)
        self.state.deduct_fees(total_fee)
        self.state.log_order(order_a)
        self.state.log_order(order_b)

        logger.info(
            f"EXIT {position.direction} {position.pair_key} | "
            f"net_pnl={net_pnl:.2f} | reason={reason}"
        )
        return net_pnl

    def get_orders(self) -> List[Order]:
        return list(self._orders)


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _make_order(
    pair_key: str, symbol: str, side: str,
    notional: float, price: float, fee_bps: float
) -> Order:
    fee = notional * (fee_bps / 10_000)
    qty = notional / price if price > 0 else 0.0
    return Order(
        pair_key=pair_key,
        symbol=symbol,
        side=side,
        notional=notional,
        price=price,
        quantity=qty,
        fee=fee,
    )
