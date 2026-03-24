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
from datetime import datetime, timezone
from typing import Dict, List, Optional

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
        }

        if self._kill_active:
            status["ok"] = False
            return status

        # Portfolio draw-down
        if self._check_drawdown():
            status["ok"] = False
            status["dd_breach"] = True

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
