"""
Information Coefficient (IC) calculator.

Cornell paper (pages 11-13, Filter 4 / Section 4.6):

  For each pair and each (T1, T2) combination:
    1. Rolling OLS over the last T1 bars: log_PA = α + β·log_PB + u
    2. Z-score: Z_t = u_t / σ_u
    3. Future spread return: R_{t,T2} = (log_PA_{t+T2} - log_PA_t)
                                        - β·(log_PB_{t+T2} - log_PB_t)
    4. IC = corr(Z_t, R_{t,T2})  over the full training window

  Best IC = most negative value across all (T1, T2) combos.
  Negative IC → positive Z predicts downward spread move → mean-reverting.

Cornell's parameter grids:
  T1 ∈ {7d, 15d, 1M, 3M, 6M} = {168, 360, 720, 2160, 4320} hours
  T2 ∈ {1h, 3h, 6h, 12h, 1d, 3d, 7d} = {1, 3, 6, 12, 24, 72, 168} hours
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Cornell Table (p.12): T1 and T2 grids
T1_GRID: List[int] = [168, 360, 720, 2160, 4320]   # hours
T2_GRID: List[int] = [1, 3, 6, 12, 24, 72, 168]    # hours

T1_LABELS = {168: "7d", 360: "15d", 720: "1M", 2160: "3M", 4320: "6M"}
T2_LABELS = {1: "1h", 3: "3h", 6: "6h", 12: "12h", 24: "1d", 72: "3d", 168: "7d"}


def calculate_ic_for_window(
    log_prices_a: np.ndarray,
    log_prices_b: np.ndarray,
    t1: int,
    t2: int,
) -> float:
    """
    Compute IC = corr(Z_t, R_{t,t2}) over the full series.

    Parameters
    ----------
    log_prices_a, log_prices_b : aligned numpy arrays of log prices
    t1 : rolling OLS window in hours
    t2 : forecast horizon in hours

    Returns
    -------
    IC value (float), or NaN if insufficient data.
    """
    n = len(log_prices_a)
    if n < t1 + t2 + 10:
        return float("nan")

    zscores = np.full(n, np.nan)
    future_returns = np.full(n, np.nan)

    for i in range(t1, n - t2):
        la = log_prices_a[i - t1: i]
        lb = log_prices_b[i - t1: i]

        # OLS: la = alpha + beta * lb
        lb_mean = lb.mean()
        la_mean = la.mean()
        denom = np.sum((lb - lb_mean) ** 2)
        if denom < 1e-12:
            continue
        beta = float(np.sum((lb - lb_mean) * (la - la_mean)) / denom)
        alpha = float(la_mean - beta * lb_mean)

        spread = la - (alpha + beta * lb)
        s_std = spread.std()
        if s_std < 1e-10:
            continue

        u_t = log_prices_a[i] - (alpha + beta * log_prices_b[i])
        zscores[i] = u_t / s_std

        # Future spread return (Cornell eq. on p.12)
        future_returns[i] = (
            (log_prices_a[i + t2] - log_prices_a[i])
            - beta * (log_prices_b[i + t2] - log_prices_b[i])
        )

    mask = np.isfinite(zscores) & np.isfinite(future_returns)
    if mask.sum() < 30:
        return float("nan")

    ic = float(np.corrcoef(zscores[mask], future_returns[mask])[0, 1])
    return ic if not np.isnan(ic) else float("nan")


def find_best_ic(
    prices_a: pd.Series,
    prices_b: pd.Series,
    t1_grid: Optional[List[int]] = None,
    t2_grid: Optional[List[int]] = None,
) -> Dict:
    """
    Search all (T1, T2) combinations and return the most negative IC.

    Parameters
    ----------
    prices_a, prices_b : aligned pd.Series of close prices (common index)
    t1_grid : list of T1 values in hours (default: Cornell's grid)
    t2_grid : list of T2 values in hours (default: Cornell's grid)

    Returns
    -------
    dict with keys: best_ic, best_t1, best_t2, all_results
      all_results: dict of (t1, t2) → ic
    """
    if t1_grid is None:
        t1_grid = T1_GRID
    if t2_grid is None:
        t2_grid = T2_GRID

    log_a = np.log(prices_a.values.astype(float))
    log_b = np.log(prices_b.values.astype(float))

    best_ic = float("inf")
    best_t1 = t1_grid[0]
    best_t2 = t2_grid[0]
    all_results: Dict[Tuple[int, int], float] = {}

    for t1 in t1_grid:
        for t2 in t2_grid:
            ic = calculate_ic_for_window(log_a, log_b, t1, t2)
            all_results[(t1, t2)] = ic
            if not np.isnan(ic) and ic < best_ic:
                best_ic = ic
                best_t1 = t1
                best_t2 = t2

    if best_ic == float("inf"):
        best_ic = float("nan")

    return {
        "best_ic": best_ic,
        "best_t1": best_t1,
        "best_t2": best_t2,
        "best_t1_label": T1_LABELS.get(best_t1, str(best_t1)),
        "best_t2_label": T2_LABELS.get(best_t2, str(best_t2)),
        "all_results": all_results,
    }


def ic_summary_table(
    prices: Dict[str, pd.Series],
    pairs: List[Tuple[str, str]],
    ic_threshold: float = -0.10,
    t1_grid: Optional[List[int]] = None,
    t2_grid: Optional[List[int]] = None,
) -> pd.DataFrame:
    """
    Compute best IC for a list of pairs and return a ranked DataFrame.

    Parameters
    ----------
    prices : dict of token → price Series (close, common or varied index)
    pairs  : list of (sym_a, sym_b)
    ic_threshold : keep only pairs with best_ic <= threshold
    """
    rows = []
    for sym_a, sym_b in pairs:
        if sym_a not in prices or sym_b not in prices:
            continue
        common = prices[sym_a].index.intersection(prices[sym_b].index)
        if len(common) < 500:
            continue
        result = find_best_ic(
            prices[sym_a].loc[common],
            prices[sym_b].loc[common],
            t1_grid=t1_grid,
            t2_grid=t2_grid,
        )
        if not np.isnan(result["best_ic"]):
            rows.append({
                "sym_a": sym_a,
                "sym_b": sym_b,
                "best_ic": result["best_ic"],
                "best_t1": result["best_t1"],
                "best_t2": result["best_t2"],
                "best_t1_label": result["best_t1_label"],
                "best_t2_label": result["best_t2_label"],
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df[df["best_ic"] <= ic_threshold].sort_values("best_ic").reset_index(drop=True)
    df.index += 1  # 1-based rank
    return df
