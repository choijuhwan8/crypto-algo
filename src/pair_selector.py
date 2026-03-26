"""
Pair-selection service (runs every 24 h).

Pipeline (Cornell paper, pages 10-13)
--------------------------------------
1. Same-blockchain filter – pairs must share at least one blockchain ecosystem
2. Correlation filter     – log-price corr > 0.90, return corr > 0.50
3. Cointegration test     – Engle-Granger ADF p-value < 0.05 (optional, see note)
4. IC filter              – best IC across (T1, T2) grid ≤ IC_THRESHOLD
5. Rank by IC and keep top TOP_N_PAIRS

Note on cointegration (Cornell p.26, Section 6.5):
  The paper found ADF too restrictive for fast-evolving crypto markets.
  The unfiltered (no ADF) variant delivered higher Sharpe.  Set
  COINT_PVALUE_MAX=1.0 in .env to disable the ADF gate entirely.

The selected pairs list is stored in-memory and also written to
data/selected_pairs.json for inspection.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import stats
from statsmodels.tsa.stattools import adfuller

from .config import (
    COINT_PVALUE_MAX,
    CORR_LOG_PRICE_MIN,
    CORR_RETURN_MIN,
    DATA_DIR,
    IC_THRESHOLD,
    ROLLING_WINDOW,
    TOP_N_PAIRS,
)
from .data_service import DataService

logger = logging.getLogger(__name__)

# Import blockchain mapper — optional; if backtest/ is not on sys.path the
# filter is silently skipped (live bot path).
try:
    import sys as _sys, os as _os
    _bt_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "backtest")
    if _bt_dir not in _sys.path:
        _sys.path.insert(0, _bt_dir)
    from blockchain_mapper import are_same_blockchain as _same_blockchain
    _BLOCKCHAIN_FILTER_AVAILABLE = True
except ImportError:
    _BLOCKCHAIN_FILTER_AVAILABLE = False

# Use Cornell's full IC search when running in backtest mode
try:
    from ic_calculator import find_best_ic as _find_best_ic
    _IC_SEARCH_AVAILABLE = True
except ImportError:
    _IC_SEARCH_AVAILABLE = False

_PAIRS_CACHE_FILE = os.path.join(DATA_DIR, "selected_pairs.json")


class PairSelector:
    def __init__(self, data_service: DataService) -> None:
        self.ds = data_service
        self._pairs: List[Dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, available_tokens: List[str]) -> List[Dict]:
        """Full pair-selection pass.  Returns selected pairs list."""
        all_pairs = list(itertools.combinations(available_tokens, 2))

        # ── Filter 1: Same-blockchain (Cornell p.10-11) ──────────────────
        if _BLOCKCHAIN_FILTER_AVAILABLE:
            before = len(all_pairs)
            all_pairs = [
                (a, b) for a, b in all_pairs if _same_blockchain(a, b)
            ]
            logger.info(
                f"PairSelector: blockchain filter {before} → {len(all_pairs)} pairs"
            )
        else:
            logger.info(
                f"PairSelector: testing {len(all_pairs)} pairs "
                f"from {len(available_tokens)} tokens (no blockchain filter)"
            )

        candidates: List[Dict] = []
        for tok_a, tok_b in all_pairs:
            result = self._evaluate_pair(tok_a, tok_b)
            if result is not None:
                candidates.append(result)

        # Sort by IC ascending (most negative = strongest mean reversion)
        candidates.sort(key=lambda x: x["ic"])
        self._pairs = candidates[:TOP_N_PAIRS]

        logger.info(
            f"PairSelector: selected {len(self._pairs)} pairs – "
            + str([(p["sym_a"], p["sym_b"]) for p in self._pairs])
        )
        self._save_pairs()
        return self._pairs

    def get_pairs(self) -> List[Dict]:
        return self._pairs

    def load_from_file(self) -> bool:
        """Load previously saved pairs (used on cold restart)."""
        if not os.path.exists(_PAIRS_CACHE_FILE):
            return False
        try:
            with open(_PAIRS_CACHE_FILE) as f:
                self._pairs = json.load(f)
            logger.info(f"PairSelector: loaded {len(self._pairs)} pairs from file")
            return True
        except Exception as exc:
            logger.warning(f"PairSelector: could not load pairs file – {exc}")
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evaluate_pair(self, tok_a: str, tok_b: str) -> Optional[Dict]:
        """Return pair dict if it passes all filters, else None."""
        df_a = self.ds.get_cache(tok_a)
        df_b = self.ds.get_cache(tok_b)
        if df_a is None or df_b is None:
            return None

        common = df_a.index.intersection(df_b.index)
        if len(common) < 500:
            return None

        log_a = np.log(df_a.loc[common, "close"])
        log_b = np.log(df_b.loc[common, "close"])

        # 1. Correlation filter
        corr_price = float(log_a.corr(log_b))
        ret_a = log_a.diff().dropna()
        ret_b = log_b.diff().dropna()
        corr_return = float(ret_a.corr(ret_b))

        if corr_price < CORR_LOG_PRICE_MIN or corr_return < CORR_RETURN_MIN:
            return None

        # 2. Cointegration – OLS spread + ADF
        if log_b.nunique() < 2 or log_a.nunique() < 2:
            return None
        slope, intercept, *_ = stats.linregress(log_b.values, log_a.values)
        spread = log_a.values - (intercept + slope * log_b.values)
        if len(set(spread)) < 2:
            return None
        adf_stat, adf_pval, *_ = adfuller(spread, maxlag=1, autolag=None)

        if adf_pval >= COINT_PVALUE_MAX:
            return None

        # 3. IC filter – use Cornell's full (T1, T2) grid when available,
        #    else fall back to the original single-window IC computation.
        if _IC_SEARCH_AVAILABLE:
            ic_result = _find_best_ic(
                df_a.loc[common, "close"],
                df_b.loc[common, "close"],
            )
            ic = ic_result["best_ic"]
            best_t1 = ic_result["best_t1"]
            best_t2 = ic_result["best_t2"]
        else:
            ic = self._compute_ic(log_a, log_b, float(slope), float(intercept))
            best_t1 = ROLLING_WINDOW
            best_t2 = 24

        if ic > IC_THRESHOLD or np.isnan(ic):
            return None

        return {
            "sym_a": tok_a,
            "sym_b": tok_b,
            "beta": float(slope),
            "alpha": float(intercept),
            "corr_price": round(corr_price, 4),
            "corr_return": round(corr_return, 4),
            "adf_stat": round(float(adf_stat), 4),
            "adf_pvalue": round(float(adf_pval), 4),
            "ic": round(ic, 4),
            "best_t1": best_t1,
            "best_t2": best_t2,
        }

    def _compute_ic(
        self,
        log_a,
        log_b,
        beta: float,
        alpha: float,
        window: int = ROLLING_WINDOW,
        horizon: int = 24,
    ) -> float:
        """IC = corr(z-score_t, spread_return_{t+horizon})."""
        spread = log_a - (alpha + beta * log_b)
        win = min(window, len(spread) // 4)
        spread_mean = spread.rolling(win).mean()
        spread_std = spread.rolling(win).std()
        zscore = (spread - spread_mean) / spread_std

        future_ret = spread.shift(-horizon) - spread

        valid = zscore.dropna().index.intersection(future_ret.dropna().index)
        if len(valid) < 100:
            return 0.0

        ic = float(zscore.loc[valid].corr(future_ret.loc[valid]))
        return ic if not np.isnan(ic) else 0.0

    def _save_pairs(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(_PAIRS_CACHE_FILE, "w") as f:
                json.dump(self._pairs, f, indent=2)
        except Exception as exc:
            logger.warning(f"PairSelector: could not save pairs – {exc}")
