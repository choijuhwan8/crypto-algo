"""
Signal service (runs every 1 h on candle close).

For each selected pair it:
  1. Recomputes rolling OLS beta + alpha over the last ROLLING_WINDOW bars.
  2. Computes the current spread and z-score.
  3. Emits LONG_SPREAD / SHORT_SPREAD / EXIT / HOLD.

Signal semantics
----------------
LONG_SPREAD  : z-score < -Z_ENTRY  → long tok_a, short tok_b
SHORT_SPREAD : z-score >  Z_ENTRY  → short tok_a, long tok_b
EXIT         : z-score crosses Z_EXIT (mean) while in a position
HOLD         : no actionable signal
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Dict, Optional, Tuple

import numpy as np

from .config import ROLLING_WINDOW, Z_ENTRY, Z_EXIT
from .data_service import DataService

logger = logging.getLogger(__name__)


class Signal(str, Enum):
    LONG_SPREAD = "LONG_SPREAD"
    SHORT_SPREAD = "SHORT_SPREAD"
    EXIT = "EXIT"
    HOLD = "HOLD"


class SignalService:
    def __init__(self, data_service: DataService) -> None:
        self.ds = data_service
        self._stats: Dict[str, Dict] = {}  # pair_key → latest stats

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_stats(self, tok_a: str, tok_b: str) -> Optional[Dict]:
        """
        Recompute spread stats for the pair using the latest cached data.
        Returns a stats dict or None when data is insufficient.
        """
        df_a = self.ds.get_cache(tok_a)
        df_b = self.ds.get_cache(tok_b)

        if df_a is None or df_b is None:
            logger.warning(f"Missing data for {tok_a}-{tok_b}")
            return None

        common = df_a.index.intersection(df_b.index)
        min_bars = ROLLING_WINDOW // 2
        if len(common) < min_bars:
            logger.warning(
                f"{tok_a}-{tok_b}: only {len(common)} common bars (need {min_bars})"
            )
            return None

        win = min(ROLLING_WINDOW, len(common))
        log_a = np.log(df_a.loc[common, "close"].values[-win:])
        log_b = np.log(df_b.loc[common, "close"].values[-win:])

        # OLS over the window
        x_mean = log_b.mean()
        y_mean = log_a.mean()
        denom = np.sum((log_b - x_mean) ** 2)
        if denom < 1e-12:
            return None
        beta = float(np.sum((log_b - x_mean) * (log_a - y_mean)) / denom)
        alpha = float(y_mean - beta * x_mean)

        spread_series = log_a - (alpha + beta * log_b)
        spread_mean = float(spread_series.mean())
        spread_std = float(spread_series.std())

        if spread_std < 1e-10:
            return None

        current_spread = float(spread_series[-1])
        zscore = (current_spread - spread_mean) / spread_std

        price_a = float(df_a.loc[common, "close"].iloc[-1])
        price_b = float(df_b.loc[common, "close"].iloc[-1])
        ts = common[-1]

        stats = {
            "sym_a": tok_a,
            "sym_b": tok_b,
            "beta": beta,
            "alpha": alpha,
            "spread": current_spread,
            "spread_mean": spread_mean,
            "spread_std": spread_std,
            "zscore": zscore,
            "price_a": price_a,
            "price_b": price_b,
            "timestamp": ts,
        }

        pair_key = f"{tok_a}-{tok_b}"
        self._stats[pair_key] = stats
        return stats

    def generate_signal(
        self,
        tok_a: str,
        tok_b: str,
        current_direction: Optional[str] = None,
    ) -> Tuple[Signal, Dict]:
        """
        Generate signal for a pair.

        Parameters
        ----------
        current_direction : None / "LONG_SPREAD" / "SHORT_SPREAD"
            Current open position direction, or None when flat.

        Returns
        -------
        (Signal, stats_dict)
        """
        stats = self.compute_stats(tok_a, tok_b)
        if stats is None:
            return Signal.HOLD, {}

        z = stats["zscore"]

        if current_direction is None:
            # Entry logic
            if z < -Z_ENTRY:
                return Signal.LONG_SPREAD, stats
            if z > Z_ENTRY:
                return Signal.SHORT_SPREAD, stats
            return Signal.HOLD, stats

        # Exit logic – close when spread reverts past Z_EXIT
        if current_direction == "LONG_SPREAD" and z >= Z_EXIT:
            return Signal.EXIT, stats
        if current_direction == "SHORT_SPREAD" and z <= Z_EXIT:
            return Signal.EXIT, stats

        return Signal.HOLD, stats

    def get_stats(self, pair_key: str) -> Optional[Dict]:
        return self._stats.get(pair_key)

    def all_stats(self) -> Dict[str, Dict]:
        return dict(self._stats)
