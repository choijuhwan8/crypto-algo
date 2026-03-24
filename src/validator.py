"""
30-day validation report.

Runs a lightweight walk-forward backtest on the *currently cached* data
for each selected pair using three fee scenarios: 4.5, 5, and 10 bps.

Metrics computed per pair × fee scenario
-----------------------------------------
  total_return_pct, sharpe, max_dd_pct, n_trades, win_rate_pct, avg_pnl

The report is printed to the log and saved as data/validation_report.json.
It is also sent to Telegram as a summary message.

Strategy logic mirrors the notebook (rolling OLS → z-score → threshold entry/exit).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from .config import (
    DATA_DIR,
    FEE_BPS,
    INITIAL_CAPITAL,
    LEVERAGE,
    MAX_POSITION_PCT,
    MAX_CONCURRENT_PAIRS,
    ROLLING_WINDOW,
    STOP_LOSS_PCT,
    Z_ENTRY,
    Z_EXIT,
)
from .data_service import DataService
from .pair_selector import PairSelector

logger = logging.getLogger(__name__)

FEE_SCENARIOS = [4.5, 5.0, 10.0]          # bps
REPORT_FILE = os.path.join(DATA_DIR, "validation_report.json")


class Validator:
    def __init__(self, data_service: DataService, pair_selector: PairSelector) -> None:
        self.ds = data_service
        self.ps = pair_selector

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> Dict:
        """Run validation for all selected pairs.  Returns report dict."""
        pairs = self.ps.get_pairs()
        if not pairs:
            logger.warning("Validator: no pairs selected – skipping")
            return {}

        report: Dict = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pairs": {},
        }

        for pair_info in pairs:
            pk = f"{pair_info['sym_a']}-{pair_info['sym_b']}"
            result = self._backtest_pair(pair_info)
            if result:
                report["pairs"][pk] = result

        _save_report(report)
        _log_report(report)
        return report

    def format_telegram_message(self, report: Dict) -> str:
        if not report.get("pairs"):
            return "Validation report: no data."

        lines = [
            f"*Monthly Validation Report*\n_{report.get('generated_at', '')[:10]}_\n"
        ]
        for pk, scenarios in report["pairs"].items():
            lines.append(f"`{pk}`")
            for fee_key, m in scenarios.items():
                lines.append(
                    f"  @{fee_key}: Sharpe={m['sharpe']:.2f} "
                    f"ret={m['total_return_pct']:+.1f}% "
                    f"dd={m['max_dd_pct']:.1f}% "
                    f"trades={m['n_trades']}"
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal: per-pair backtest
    # ------------------------------------------------------------------

    def _backtest_pair(self, pair_info: Dict) -> Optional[Dict]:
        tok_a = pair_info["sym_a"]
        tok_b = pair_info["sym_b"]

        df_a = self.ds.get_cache(tok_a)
        df_b = self.ds.get_cache(tok_b)
        if df_a is None or df_b is None:
            return None

        common = df_a.index.intersection(df_b.index)
        if len(common) < ROLLING_WINDOW + 100:
            return None

        log_a = np.log(df_a.loc[common, "close"].values)
        log_b = np.log(df_b.loc[common, "close"].values)
        prices_a = df_a.loc[common, "close"].values
        prices_b = df_b.loc[common, "close"].values
        n = len(log_a)

        results: Dict[str, Dict] = {}

        for fee_bps in FEE_SCENARIOS:
            pnl_series, n_trades = _simulate(
                log_a, log_b, prices_a, prices_b,
                n, fee_bps=fee_bps,
                beta=pair_info["beta"], alpha=pair_info["alpha"],
            )
            metrics = _compute_metrics(pnl_series, n_trades)
            results[f"{fee_bps:.1f}bps"] = metrics

        return results


# ------------------------------------------------------------------
# Vectorised simulation (rolling-window approach)
# ------------------------------------------------------------------

def _simulate(
    log_a: np.ndarray,
    log_b: np.ndarray,
    prices_a: np.ndarray,
    prices_b: np.ndarray,
    n: int,
    fee_bps: float,
    beta: float,
    alpha: float,
    window: int = ROLLING_WINDOW,
) -> Tuple[List[float], int]:
    """Simple event-driven simulation on numpy arrays."""
    equity = INITIAL_CAPITAL
    position: Optional[Dict] = None
    pnl_list: List[float] = [0.0]
    n_trades = 0

    notional_base = (equity * MAX_POSITION_PCT / MAX_CONCURRENT_PAIRS) * LEVERAGE

    for i in range(window, n):
        la = log_a[max(0, i - window): i]
        lb = log_b[max(0, i - window): i]

        # Rolling OLS (re-use provided beta/alpha for speed)
        spread_w = la - (alpha + beta * lb)
        s_mean = spread_w.mean()
        s_std = spread_w.std()

        if s_std < 1e-10:
            continue

        current_spread = log_a[i] - (alpha + beta * log_b[i])
        z = (current_spread - s_mean) / s_std

        pa = prices_a[i]
        pb = prices_b[i]

        if position is None:
            # Entry
            if z > Z_ENTRY:
                position = dict(direction="SHORT_SPREAD", pa=pa, pb=pb, z=z)
                equity -= notional_base * 2 * fee_bps / 10_000
            elif z < -Z_ENTRY:
                position = dict(direction="LONG_SPREAD", pa=pa, pb=pb, z=z)
                equity -= notional_base * 2 * fee_bps / 10_000
        else:
            # Stop-loss check
            if position["direction"] == "LONG_SPREAD":
                gross = notional_base * (pa / position["pa"] - 1) + \
                        notional_base * (position["pb"] / pb - 1)
            else:
                gross = notional_base * (position["pa"] / pa - 1) + \
                        notional_base * (pb / position["pb"] - 1)

            pct = gross / (notional_base * 2)
            hit_stop = pct < -STOP_LOSS_PCT

            # Exit condition
            exit_signal = (
                (position["direction"] == "LONG_SPREAD" and z >= Z_EXIT) or
                (position["direction"] == "SHORT_SPREAD" and z <= Z_EXIT)
            )

            if exit_signal or hit_stop:
                net = gross - notional_base * 2 * fee_bps / 10_000
                equity += net
                pnl_list.append(net)
                n_trades += 1
                position = None

    return pnl_list, n_trades


def _compute_metrics(pnl_list: List[float], n_trades: int) -> Dict:
    arr = np.array(pnl_list)
    total_return = float(arr.sum() / INITIAL_CAPITAL * 100)

    equity_curve = INITIAL_CAPITAL + np.cumsum(arr)
    peak = np.maximum.accumulate(equity_curve)
    dd = (peak - equity_curve) / peak
    max_dd = float(dd.max() * 100) if len(dd) > 0 else 0.0

    hourly_returns = arr / INITIAL_CAPITAL
    sharpe = 0.0
    if hourly_returns.std() > 0:
        # Annualise from hourly (8 760 hours/year)
        sharpe = float(
            hourly_returns.mean() / hourly_returns.std() * np.sqrt(8_760)
        )

    winners = int((arr > 0).sum())
    win_rate = round(winners / n_trades * 100, 1) if n_trades > 0 else 0.0
    avg_pnl = round(float(arr[arr != 0].mean()), 2) if n_trades > 0 else 0.0

    return {
        "total_return_pct": round(total_return, 2),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd, 2),
        "n_trades": n_trades,
        "win_rate_pct": win_rate,
        "avg_pnl": avg_pnl,
    }


def _save_report(report: Dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(REPORT_FILE, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"Validation report saved to {REPORT_FILE}")
    except Exception as exc:
        logger.warning(f"Could not save validation report: {exc}")


def _log_report(report: Dict) -> None:
    logger.info("=" * 60)
    logger.info("MONTHLY VALIDATION REPORT")
    logger.info(f"Generated: {report.get('generated_at', '')}")
    for pk, scenarios in report.get("pairs", {}).items():
        logger.info(f"  {pk}")
        for fee_key, m in scenarios.items():
            logger.info(
                f"    @{fee_key}: Sharpe={m['sharpe']:.2f} "
                f"ret={m['total_return_pct']:+.1f}% "
                f"dd={m['max_dd_pct']:.1f}% "
                f"trades={m['n_trades']} "
                f"wr={m['win_rate_pct']:.0f}%"
            )
    logger.info("=" * 60)
