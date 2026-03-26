"""
Risk engine.

Guards:
  * Portfolio max draw-down stop  (MAX_PORTFOLIO_DD)
  * Per-position stop-loss        (STOP_LOSS_PCT – checked in hourly loop)
  * Max concurrent pairs          (MAX_CONCURRENT_PAIRS)
  * Data staleness kill switch    (MAX_DATA_STALENESS_MIN)
  * Manual pause / resume / kill  (via Telegram /pause /resume /closeall)

The kill switch permanently halts trading until the process is restarted.
A pause is a softer stop that can be lifted with /resume.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from .config import MAX_CONCURRENT_PAIRS, MAX_PORTFOLIO_DD

logger = logging.getLogger(__name__)


class RiskEngine:
    def __init__(self, state_manager) -> None:
        self.state = state_manager
        self._paused = False
        self._kill_active = False
        self._kill_reason = ""

    # ------------------------------------------------------------------
    # Manual controls (Telegram)
    # ------------------------------------------------------------------

    def pause(self) -> None:
        self._paused = True
        logger.warning("RiskEngine: bot PAUSED")

    def resume(self) -> None:
        if self._kill_active:
            logger.error("RiskEngine: cannot resume – kill switch is active")
            return
        self._paused = False
        logger.info("RiskEngine: bot RESUMED")

    def activate_kill_switch(self, reason: str) -> None:
        self._kill_active = True
        self._kill_reason = reason
        self._paused = True
        logger.critical(f"RiskEngine: KILL SWITCH – {reason}")

    # ------------------------------------------------------------------
    # Checks (called by hourly loop)
    # ------------------------------------------------------------------

    def run_checks(
        self,
        data_service=None,
        symbols: Optional[List[str]] = None,
    ) -> Dict:
        """
        Run all risk checks.  Returns a status dict with key ``ok``.
        Side effect: activates kill switch when a hard limit is breached.
        """
        status: Dict = {
            "ok": True,
            "paused": self._paused,
            "kill_active": self._kill_active,
            "kill_reason": self._kill_reason,
            "dd_breach": False,
            "stale_data": False,
            "rolling_24h_dd_breach": False,
        }

        if self._kill_active:
            status["ok"] = False
            return status

        # Portfolio draw-down (kill switch)
        if self._check_drawdown():
            status["ok"] = False
            status["dd_breach"] = True

        # Rolling 24h draw-down (pause)
        dd_24h, breached = self._check_rolling_24h_drawdown()
        if breached:
            self._paused = True
            status["rolling_24h_dd_breach"] = True
            status["rolling_24h_dd"] = dd_24h
            logger.warning(f"RiskEngine: 24h rolling DD {dd_24h:.2%} – bot PAUSED")

        # Data freshness
        if data_service and symbols:
            for sym in symbols:
                if not data_service.is_data_fresh(sym):
                    self.activate_kill_switch(f"Stale data detected for {sym}")
                    status["ok"] = False
                    status["stale_data"] = True
                    break

        if self._paused:
            status["ok"] = False

        return status

    def can_open_position(self) -> bool:
        """True when we have capacity for a new pair position."""
        if self._paused or self._kill_active:
            return False
        return len(self.state.get_open_positions()) < MAX_CONCURRENT_PAIRS

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def kill_active(self) -> bool:
        return self._kill_active

    @property
    def kill_reason(self) -> str:
        return self._kill_reason

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def check_max_hold_period(self, position, current_time: datetime) -> bool:
        """Return True if position has exceeded the max hold period."""
        hours_held = (current_time - position.entry_time).total_seconds() / 3600
        max_hours = float(os.getenv("MAX_HOLD_HOURS", 168))
        if hours_held > max_hours:
            logger.info(
                f"Position {position.pair_key} exceeded max hold: {hours_held:.1f}h "
                f"(limit {max_hours:.0f}h)"
            )
            return True
        return False

    def check_realtime_correlation(
        self, position, data_service, lookback_hours: int = 24
    ) -> Tuple[bool, float]:
        """
        Return (is_ok, corr).  is_ok=False means correlation has degraded below
        REALTIME_CORR_MIN and the position should be closed.

        Uses the in-memory cache from DataService — no extra network calls.
        """
        try:
            df_a = data_service.get_cache(position.sym_a)
            df_b = data_service.get_cache(position.sym_b)

            if df_a is None or df_b is None:
                return True, 1.0

            common = df_a.index.intersection(df_b.index)[-lookback_hours:]
            if len(common) < 10:
                return True, 1.0

            log_ret_a = np.log(
                df_a.loc[common, "close"].values[1:]
                / df_a.loc[common, "close"].values[:-1]
            )
            log_ret_b = np.log(
                df_b.loc[common, "close"].values[1:]
                / df_b.loc[common, "close"].values[:-1]
            )

            corr = float(np.corrcoef(log_ret_a, log_ret_b)[0, 1])
            min_corr = float(os.getenv("REALTIME_CORR_MIN", 0.60))
            is_ok = corr >= min_corr
            if not is_ok:
                logger.warning(
                    f"Correlation breakdown on {position.pair_key}: "
                    f"corr={corr:.3f} < {min_corr}"
                )
            return is_ok, corr

        except Exception as exc:
            logger.error(f"check_realtime_correlation {position.pair_key}: {exc}")
            return True, 1.0

    def _check_rolling_24h_drawdown(self) -> Tuple[float, bool]:
        """Return (dd_ratio, breached).  dd_ratio is negative when equity fell."""
        equity_24h_ago = self.state.get_equity_24h_ago()
        if not equity_24h_ago or equity_24h_ago <= 0:
            return 0.0, False
        current = self.state.get_equity()
        dd = (current - equity_24h_ago) / equity_24h_ago
        limit = float(os.getenv("ROLLING_24H_DD_LIMIT", 0.05))
        return dd, dd < -limit

    def _check_drawdown(self) -> bool:
        equity = self.state.get_equity()
        peak = self.state.get_peak_equity()
        if peak <= 0:
            return False
        dd = (peak - equity) / peak
        if dd > MAX_PORTFOLIO_DD:
            self.activate_kill_switch(
                f"Portfolio DD {dd:.1%} exceeds limit {MAX_PORTFOLIO_DD:.1%}"
            )
            return True
        return False
