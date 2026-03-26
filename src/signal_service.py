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
import os
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

    def compute_stats(self, tok_a: str, tok_b: str, window: Optional[int] = None) -> Optional[Dict]:
        """
        Recompute spread stats for the pair using the latest cached data.
        Returns a stats dict or None when data is insufficient.

        Parameters
        ----------
        window : override ROLLING_WINDOW for this pair (Cornell per-pair L)
        """
        df_a = self.ds.get_cache(tok_a)
        df_b = self.ds.get_cache(tok_b)

        if df_a is None or df_b is None:
            logger.warning(f"Missing data for {tok_a}-{tok_b}")
            return None

        rolling_window = window if window is not None else ROLLING_WINDOW

        common = df_a.index.intersection(df_b.index)
        min_bars = rolling_window // 2
        if len(common) < min_bars:
            logger.warning(
                f"{tok_a}-{tok_b}: only {len(common)} common bars (need {min_bars})"
            )
            return None

        win = min(rolling_window, len(common))
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
        window: Optional[int] = None,
        z_entry: Optional[float] = None,
        z_exit: Optional[float] = None,
    ) -> Tuple[Signal, Dict]:
        """
        Generate signal for a pair.

        Parameters
        ----------
        current_direction : None / "LONG_SPREAD" / "SHORT_SPREAD"
            Current open position direction, or None when flat.
        window  : per-pair OLS window override (Cornell L)
        z_entry : per-pair entry threshold override
        z_exit  : per-pair exit threshold override

        Returns
        -------
        (Signal, stats_dict)
        """
        stats = self.compute_stats(tok_a, tok_b, window=window)
        if stats is None:
            return Signal.HOLD, {}

        z = stats["zscore"]
        ze_entry = z_entry if z_entry is not None else Z_ENTRY
        ze_exit  = z_exit  if z_exit  is not None else Z_EXIT

        if current_direction is None:
            # Entry logic
            if z < -ze_entry:
                return Signal.LONG_SPREAD, stats
            if z > ze_entry:
                return Signal.SHORT_SPREAD, stats
            return Signal.HOLD, stats

        # Exit logic – close when spread reverts past ze_exit
        if current_direction == "LONG_SPREAD" and z >= ze_exit:
            return Signal.EXIT, stats
        if current_direction == "SHORT_SPREAD" and z <= ze_exit:
            return Signal.EXIT, stats

        return Signal.HOLD, stats

    def check_zscore_breakdown(self, position, current_zscore: float) -> bool:
        """
        Return True if the mean-reversion thesis has broken down.

        A LONG_SPREAD position assumes the spread will rise back toward zero,
        so a deeply negative z-score moving further away signals failure.
        A SHORT_SPREAD position assumes the spread will fall, so a deeply
        positive z-score moving further away signals failure.
        """
        threshold = float(os.getenv("ZSCORE_BREAKDOWN_THRESHOLD", 2.5))
        if position.direction == "LONG_SPREAD" and current_zscore < -threshold:
            logger.warning(
                f"Z-score breakdown on {position.pair_key} (LONG_SPREAD): "
                f"z={current_zscore:.2f} < -{threshold}"
            )
            return True
        if position.direction == "SHORT_SPREAD" and current_zscore > threshold:
            logger.warning(
                f"Z-score breakdown on {position.pair_key} (SHORT_SPREAD): "
                f"z={current_zscore:.2f} > {threshold}"
            )
            return True
        return False

    def get_stats(self, pair_key: str) -> Optional[Dict]:
        return self._stats.get(pair_key)

    def all_stats(self) -> Dict[str, Dict]:
        return dict(self._stats)
