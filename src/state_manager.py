"""
State manager.

Persistence layer for:
  * Equity / peak equity
  * Open positions (in-memory; re-created from closed_positions on restart)
  * Closed positions list  → data/state.json
  * Order log              → data/orders.json  (append-only)
  * Equity curve           → data/equity_curve.json
  * Run log                → run_log.csv  (CSV append)

All monetary amounts are stored as Python floats rounded to 2 dp.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import DATA_DIR, INITIAL_CAPITAL, LOG_DIR, RUN_LOG_FILE

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(DATA_DIR, "state.json")
_ORDERS_FILE = os.path.join(DATA_DIR, "orders.jsonl")
_EQUITY_FILE = os.path.join(DATA_DIR, "equity_curve.json")


class StateManager:
    def __init__(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)

        self._equity: float = INITIAL_CAPITAL
        self._peak_equity: float = INITIAL_CAPITAL
        self._total_fees: float = 0.0
        self._open_positions: Dict[str, Any] = {}   # pair_key → Position
        self._closed_positions: List[Dict] = []
        self._equity_curve: List[Dict] = []

        self._load()

    # ------------------------------------------------------------------
    # Equity
    # ------------------------------------------------------------------

    def get_equity(self) -> float:
        return self._equity

    def get_peak_equity(self) -> float:
        return self._peak_equity

    def update_equity(self, delta: float) -> None:
        self._equity = round(self._equity + delta, 8)
        if self._equity > self._peak_equity:
            self._peak_equity = self._equity
        self._equity_curve.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "equity": self._equity,
            }
        )
        self._save()

    def deduct_fees(self, amount: float) -> None:
        self._total_fees += amount
        self._equity = round(self._equity - amount, 8)
        self._save()

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def add_position(self, position) -> None:
        self._open_positions[position.pair_key] = position

    def close_position(self, position, realized_pnl: float, reason: str = "signal") -> None:
        self._open_positions.pop(position.pair_key, None)
        self._closed_positions.append(
            {
                "position_id": position.position_id,
                "pair_key": position.pair_key,
                "direction": position.direction,
                "entry_time": position.entry_time.isoformat(),
                "exit_time": datetime.now(timezone.utc).isoformat(),
                "entry_price_a": position.entry_price_a,
                "entry_price_b": position.entry_price_b,
                "notional_a": position.notional_a,
                "notional_b": position.notional_b,
                "entry_zscore": position.entry_zscore,
                "realized_pnl": round(realized_pnl, 4),
                "reason": reason,
            }
        )
        self.update_equity(realized_pnl)

    def get_open_positions(self) -> Dict[str, Any]:
        return self._open_positions

    def get_position(self, pair_key: str) -> Optional[Any]:
        return self._open_positions.get(pair_key)

    def get_closed_positions(self) -> List[Dict]:
        return self._closed_positions

    # ------------------------------------------------------------------
    # Order log
    # ------------------------------------------------------------------

    def log_order(self, order) -> None:
        row = {
            "order_id": order.order_id,
            "pair_key": order.pair_key,
            "symbol": order.symbol,
            "side": order.side,
            "notional": round(order.notional, 4),
            "price": order.price,
            "quantity": round(order.quantity, 6),
            "fee": round(order.fee, 4),
            "ts": order.timestamp.isoformat(),
        }
        try:
            with open(_ORDERS_FILE, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as exc:
            logger.warning(f"log_order failed: {exc}")

    # ------------------------------------------------------------------
    # Run log
    # ------------------------------------------------------------------

    def append_run_log(self, event: str, details: str = "") -> None:
        exists = os.path.exists(RUN_LOG_FILE)
        try:
            with open(RUN_LOG_FILE, "a", newline="") as f:
                w = csv.writer(f)
                if not exists:
                    w.writerow(["timestamp", "event", "equity", "details"])
                w.writerow(
                    [
                        datetime.now(timezone.utc).isoformat(),
                        event,
                        round(self._equity, 2),
                        details,
                    ]
                )
        except Exception as exc:
            logger.warning(f"append_run_log failed: {exc}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_summary(self) -> Dict:
        open_unrealized = sum(
            getattr(p, "pnl", 0.0) for p in self._open_positions.values()
        )
        closed_pnls = [c["realized_pnl"] for c in self._closed_positions]
        n_trades = len(closed_pnls)
        winners = sum(1 for p in closed_pnls if p > 0)
        total_return = (self._equity - INITIAL_CAPITAL) / INITIAL_CAPITAL

        dd = 0.0
        if self._peak_equity > 0:
            dd = (self._peak_equity - self._equity) / self._peak_equity

        return {
            "equity": round(self._equity, 2),
            "initial_capital": INITIAL_CAPITAL,
            "total_return_pct": round(total_return * 100, 2),
            "open_positions": len(self._open_positions),
            "open_unrealized_pnl": round(open_unrealized, 2),
            "total_trades": n_trades,
            "win_rate_pct": round(winners / n_trades * 100, 1) if n_trades else 0.0,
            "total_fees_paid": round(self._total_fees, 2),
            "peak_equity": round(self._peak_equity, 2),
            "current_dd_pct": round(dd * 100, 2),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        open_positions_data = [
            {
                "position_id": p.position_id,
                "pair_key": p.pair_key,
                "sym_a": p.sym_a,
                "sym_b": p.sym_b,
                "direction": p.direction,
                "entry_price_a": p.entry_price_a,
                "entry_price_b": p.entry_price_b,
                "current_price_a": getattr(p, "current_price_a", None),
                "current_price_b": getattr(p, "current_price_b", None),
                "current_zscore": getattr(p, "current_zscore", None),
                "last_updated": getattr(p, "last_updated", None),
                "notional_a": p.notional_a,
                "notional_b": p.notional_b,
                "entry_time": p.entry_time.isoformat(),
                "entry_zscore": p.entry_zscore,
                "pnl": p.pnl,
                "pnl_a": getattr(p, "pnl_a", None),
                "pnl_b": getattr(p, "pnl_b", None),
                "status": p.status,
            }
            for p in self._open_positions.values()
        ]
        state = {
            "equity": self._equity,
            "peak_equity": self._peak_equity,
            "total_fees": self._total_fees,
            "open_positions": open_positions_data,
            "closed_positions": self._closed_positions[-2_000:],
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as exc:
            logger.error(f"StateManager._save failed: {exc}")

        # Equity curve (keep last 50 000 points)
        curve_data = self._equity_curve[-50_000:]
        try:
            with open(_EQUITY_FILE, "w") as f:
                json.dump(curve_data, f)
        except Exception as exc:
            logger.warning(f"equity curve save failed: {exc}")

    def _load(self) -> None:
        if not os.path.exists(_STATE_FILE):
            logger.info("StateManager: no existing state – starting fresh")
            return
        try:
            with open(_STATE_FILE) as f:
                data = json.load(f)
            self._equity = data.get("equity", INITIAL_CAPITAL)
            self._peak_equity = data.get("peak_equity", INITIAL_CAPITAL)
            self._total_fees = data.get("total_fees", 0.0)
            self._closed_positions = data.get("closed_positions", [])
            # Restore open positions
            from .execution_engine import Position
            for p in data.get("open_positions", []):
                pos = Position(
                    position_id=p["position_id"],
                    pair_key=p["pair_key"],
                    sym_a=p["sym_a"],
                    sym_b=p["sym_b"],
                    direction=p["direction"],
                    entry_price_a=p["entry_price_a"],
                    entry_price_b=p["entry_price_b"],
                    notional_a=p["notional_a"],
                    notional_b=p["notional_b"],
                    entry_time=datetime.fromisoformat(p["entry_time"]),
                    entry_zscore=p["entry_zscore"],
                    pnl=p["pnl"],
                    status=p["status"],
                )
                self._open_positions[pos.pair_key] = pos
            logger.info(
                f"StateManager: loaded state – equity={self._equity:.2f} "
                f"open_positions={len(self._open_positions)}"
            )
        except Exception as exc:
            logger.error(f"StateManager._load failed: {exc}")

        if os.path.exists(_EQUITY_FILE):
            try:
                with open(_EQUITY_FILE) as f:
                    self._equity_curve = json.load(f)
            except Exception:
                pass
