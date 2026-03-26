"""
Cornell-style validation backtester.

Implements:
  1. Per-pair parameter optimisation (Algorithm 1, Section 6.2, pages 17-20)
     Grid: L ∈{168,360,720,2160,4320}, z_entry ∈{0.8,1.0,1.5,2.0,2.5,3.0},
           z_exit ∈{0, 0.5}  →  60 configs per pair
  2. Portfolio validation backtest using each pair's best (L, z_entry, z_exit)
  3. Full Cornell risk controls: stop-loss 20%, cooldown 5 days, permanent
     deactivation at 20% cumulative DD

Cornell global params (p.20):
  γ_SL  = 0.20   stop-loss threshold (fraction of entry notional)
  Δ_cool = 5 days cooldown after stop-loss
  γ_DE  = 0.20   permanent deactivation drawdown
  fee   = 5 bps  taker fee per leg
  leverage = 5x

Usage
-----
  python backtest/validation_backtest.py --train-start 2022-06-01 \\
         --train-end 2024-05-31 --val-start 2024-06-01 --val-end 2024-12-01
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("validation_bt")

from backtest.backtest import download_history, HistoricalDataService, RESULTS_DIR
from src.config import CANDIDATE_TOKENS, INITIAL_CAPITAL
from backtest.blockchain_mapper import are_same_blockchain

# ---------------------------------------------------------------------------
# Cornell Algorithm 1 constants
# ---------------------------------------------------------------------------

STOP_LOSS_PCT   = 0.20    # γ_SL
COOLDOWN_HOURS  = 5 * 24  # Δ_cool = 5 days in hours
DEACTIVATE_PCT  = 0.20    # γ_DE
FEE_BPS         = 5.0     # taker fee each leg
LEVERAGE        = 5.0

# Grid (Cornell Section 6.2, p.20)
L_GRID       = [168, 360, 720, 2160, 4320]          # hours
ZENTRY_GRID  = [0.8, 1.0, 1.5, 2.0, 2.5, 3.0]
ZEXIT_GRID   = [0.0, 0.5]

HOURS_PER_YEAR = 24 * 365


# ===========================================================================
# Core simulation (Cornell Algorithm 1)
# ===========================================================================

def simulate_pair(
    log_a: np.ndarray,
    log_b: np.ndarray,
    prices_a: np.ndarray,
    prices_b: np.ndarray,
    L: int,
    z_entry: float,
    z_exit: float,
    initial_equity: float = 1_000_000.0,
    leverage: float = LEVERAGE,
    stop_loss_pct: float = STOP_LOSS_PCT,
    cooldown_hours: int = COOLDOWN_HOURS,
    deactivate_pct: float = DEACTIVATE_PCT,
    fee_bps: float = FEE_BPS,
) -> Dict:
    """
    Event-driven simulation for a single pair.  Mirrors Cornell Algorithm 1
    exactly, including crossing-based exit, cooldown, and deactivation.

    Returns dict with: equity_curve, trades, sharpe, total_return, max_dd,
                       n_trades, win_rate, fees_paid
    """
    n = len(log_a)
    equity = initial_equity
    equity_curve = np.full(n, np.nan)

    # Position state
    in_position = False
    side = None          # "long" | "short"
    entry_equity = equity
    entry_beta = 0.0
    z_prev = 0.0

    # Risk state
    cooldown_until = -1   # index
    deactivated = False
    peak_equity = initial_equity   # for rolling peak drawdown deactivation

    trades = []
    fees_paid = 0.0

    for i in range(L, n):
        la = log_a[i - L: i]
        lb = log_b[i - L: i]

        # Rolling OLS
        lb_mean = lb.mean()
        la_mean = la.mean()
        denom = np.sum((lb - lb_mean) ** 2)
        if denom < 1e-12:
            equity_curve[i] = equity
            continue
        beta = float(np.sum((lb - lb_mean) * (la - la_mean)) / denom)
        alpha = float(la_mean - beta * lb_mean)

        spread = la - (alpha + beta * lb)
        s_std = float(spread.std())
        if s_std < 1e-10:
            equity_curve[i] = equity
            continue

        u_t = log_a[i] - (alpha + beta * log_b[i])
        z_t = u_t / s_std

        pa = prices_a[i]
        pb = prices_b[i]

        # ── Mark-to-market when in position ─────────────────────────────
        if in_position:
            if side == "long":
                pnl_pct = (
                    (pa / prices_a[entry_idx] - 1) / (1 + entry_beta)
                    - entry_beta * (pb / prices_b[entry_idx] - 1) / (1 + entry_beta)
                )
            else:
                pnl_pct = (
                    entry_beta * (pb / prices_b[entry_idx] - 1) / (1 + entry_beta)
                    - (pa / prices_a[entry_idx] - 1) / (1 + entry_beta)
                )
            mtm_equity = entry_equity * (1 + leverage * pnl_pct)
        else:
            mtm_equity = equity

        # Update peak equity (used for peak-drawdown deactivation)
        if not in_position:
            peak_equity = max(peak_equity, equity)

        # ── Permanent deactivation check ─────────────────────────────────
        # Trigger if equity falls >deactivate_pct from its running peak.
        # This catches slow bleed-down (many small stops) that the absolute
        # floor (fraction of initial_equity) would miss.
        if not deactivated and mtm_equity < (1 - deactivate_pct) * peak_equity:
            if in_position:
                # force close
                realized = mtm_equity - equity
                equity = mtm_equity
                fees = entry_equity * leverage * 2 * fee_bps / 10_000
                equity -= fees
                fees_paid += fees
                trades.append({
                    "entry_i": entry_idx,
                    "exit_i": i,
                    "side": side,
                    "pnl": realized - fees,
                    "reason": "deactivate",
                })
                in_position = False
            deactivated = True
            equity_curve[i] = equity
            z_prev = z_t
            continue

        # ── Stop-loss check ──────────────────────────────────────────────
        if in_position and mtm_equity < (1 - stop_loss_pct) * entry_equity:
            realized = mtm_equity - equity
            equity = mtm_equity
            fees = entry_equity * leverage * 2 * fee_bps / 10_000
            equity -= fees
            fees_paid += fees
            trades.append({
                "entry_i": entry_idx,
                "exit_i": i,
                "side": side,
                "pnl": realized - fees,
                "reason": "stop_loss",
            })
            in_position = False
            cooldown_until = i + cooldown_hours
            equity_curve[i] = equity
            z_prev = z_t
            continue

        # ── Crossing-based exit (Cornell Algorithm 1, lines 29-33) ───────
        if in_position:
            exit_triggered = False
            if side == "long" and z_prev < -z_exit and z_t >= -z_exit:
                exit_triggered = True
            elif side == "short" and z_prev > z_exit and z_t <= z_exit:
                exit_triggered = True

            if exit_triggered:
                realized = mtm_equity - equity
                equity = mtm_equity
                fees = entry_equity * leverage * 2 * fee_bps / 10_000
                equity -= fees
                fees_paid += fees
                trades.append({
                    "entry_i": entry_idx,
                    "exit_i": i,
                    "side": side,
                    "pnl": realized - fees,
                    "reason": "signal",
                })
                in_position = False

        # ── Entry ────────────────────────────────────────────────────────
        entry_allowed = (
            not in_position
            and not deactivated
            and i > cooldown_until
        )
        if entry_allowed and abs(z_t) > z_entry:
            in_position = True
            side = "long" if z_t < 0 else "short"
            entry_idx = i
            entry_equity = equity
            entry_beta = beta
            # Entry fee
            fees = equity * leverage * 2 * fee_bps / 10_000
            equity -= fees
            fees_paid += fees

        equity_curve[i] = mtm_equity if in_position else equity
        z_prev = z_t

    # Force-close at end
    if in_position:
        equity = equity_curve[n - 1] if not np.isnan(equity_curve[n - 1]) else equity
        trades.append({
            "entry_i": entry_idx,
            "exit_i": n - 1,
            "side": side,
            "pnl": 0.0,
            "reason": "end",
        })

    # Fill leading NaNs
    first_valid = np.argmax(~np.isnan(equity_curve))
    equity_curve[:first_valid] = initial_equity

    # Performance metrics
    valid = equity_curve[~np.isnan(equity_curve)]
    hourly_returns = np.diff(valid) / valid[:-1]
    std = hourly_returns.std()
    sharpe = (
        float(hourly_returns.mean() / std * np.sqrt(HOURS_PER_YEAR))
        if std > 0 else 0.0
    )

    peak = np.maximum.accumulate(valid)
    dd = (valid - peak) / peak
    max_dd = float(dd.min()) if len(dd) else 0.0

    pnls = [t["pnl"] for t in trades if t["reason"] != "end"]
    n_trades = len(pnls)
    win_rate = sum(1 for p in pnls if p > 0) / n_trades if n_trades else 0.0
    total_return = float((valid[-1] - initial_equity) / initial_equity) if len(valid) else 0.0

    return {
        "equity_curve": equity_curve,
        "trades": trades,
        "sharpe": sharpe,
        "total_return": total_return,
        "max_dd": max_dd,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "fees_paid": fees_paid,
        "final_equity": float(valid[-1]) if len(valid) else initial_equity,
    }


# ===========================================================================
# Per-pair parameter optimisation
# ===========================================================================

def optimize_parameters_for_pair(
    sym_a: str,
    sym_b: str,
    ds: HistoricalDataService,
    initial_equity: float = 1_000_000.0,
) -> Optional[Dict]:
    """
    Grid-search 60 configs and return the best by training Sharpe.

    Returns dict: sym_a, sym_b, best_L, best_z_entry, best_z_exit,
                  best_sharpe, best_result
    """
    df_a = ds.get_cache(sym_a)
    df_b = ds.get_cache(sym_b)
    if df_a is None or df_b is None:
        return None

    common = df_a.index.intersection(df_b.index)
    if len(common) < max(L_GRID) + 100:
        logger.warning(f"  {sym_a}-{sym_b}: insufficient bars ({len(common)}), skipping optimisation")
        return None

    log_a  = np.log(df_a.loc[common, "close"].values)
    log_b  = np.log(df_b.loc[common, "close"].values)
    px_a   = df_a.loc[common, "close"].values
    px_b   = df_b.loc[common, "close"].values

    best_sharpe = -np.inf
    best_cfg = {"L": L_GRID[-1], "z_entry": 1.5, "z_exit": 0.0}
    best_result = None
    n_configs = len(L_GRID) * len(ZENTRY_GRID) * len(ZEXIT_GRID)

    for L in L_GRID:
        for z_entry in ZENTRY_GRID:
            for z_exit in ZEXIT_GRID:
                result = simulate_pair(
                    log_a, log_b, px_a, px_b,
                    L=L, z_entry=z_entry, z_exit=z_exit,
                    initial_equity=initial_equity,
                )
                if result["sharpe"] > best_sharpe:
                    best_sharpe = result["sharpe"]
                    best_cfg = {"L": L, "z_entry": z_entry, "z_exit": z_exit}
                    best_result = result

    logger.info(
        f"  {sym_a}-{sym_b}: best L={best_cfg['L']} "
        f"z_entry={best_cfg['z_entry']} z_exit={best_cfg['z_exit']} "
        f"train_sharpe={best_sharpe:.2f} trades={best_result['n_trades']}"
    )

    return {
        "sym_a": sym_a,
        "sym_b": sym_b,
        "best_L": best_cfg["L"],
        "best_z_entry": best_cfg["z_entry"],
        "best_z_exit": best_cfg["z_exit"],
        "train_sharpe": round(best_sharpe, 3),
        "train_trades": best_result["n_trades"],
        "train_return": round(best_result["total_return"], 4),
    }


# ===========================================================================
# Portfolio validation backtest
# ===========================================================================

def run_validation(
    pairs: List[Dict],
    optimized_params: Dict[str, Dict],
    val_ds: HistoricalDataService,
    initial_capital: float = INITIAL_CAPITAL,
) -> Dict:
    """
    Run each pair under its optimised params on the validation window.
    Equal capital allocation per pair.
    """
    n_pairs = len(pairs)
    if n_pairs == 0:
        logger.error("No pairs to validate")
        return {}

    per_pair_capital = initial_capital / n_pairs
    pair_results = {}

    for pair in pairs:
        sym_a = pair["sym_a"]
        sym_b = pair["sym_b"]
        key = f"{sym_a}-{sym_b}"

        cfg = optimized_params.get(key)
        if cfg is None:
            logger.warning(f"  {key}: no optimised params – using defaults")
            L, z_entry, z_exit = 4320, 1.5, 0.0
        else:
            L, z_entry, z_exit = cfg["best_L"], cfg["best_z_entry"], cfg["best_z_exit"]

        df_a = val_ds.get_cache(sym_a)
        df_b = val_ds.get_cache(sym_b)
        if df_a is None or df_b is None:
            continue

        common = df_a.index.intersection(df_b.index)
        if len(common) < L + 10:
            continue

        log_a = np.log(df_a.loc[common, "close"].values)
        log_b = np.log(df_b.loc[common, "close"].values)
        px_a  = df_a.loc[common, "close"].values
        px_b  = df_b.loc[common, "close"].values

        result = simulate_pair(
            log_a, log_b, px_a, px_b,
            L=L, z_entry=z_entry, z_exit=z_exit,
            initial_equity=per_pair_capital,
        )
        result["timestamps"] = common
        result["L"] = L
        result["z_entry"] = z_entry
        result["z_exit"] = z_exit
        pair_results[key] = result

        logger.info(
            f"  {key}: sharpe={result['sharpe']:.2f} "
            f"ret={result['total_return']:.2%} "
            f"dd={result['max_dd']:.2%} "
            f"trades={result['n_trades']}"
        )

    return pair_results


# ===========================================================================
# Reporting
# ===========================================================================

def generate_portfolio_report(
    pair_results: Dict,
    initial_capital: float,
    train_start: datetime,
    train_end: datetime,
    val_start: datetime,
    val_end: datetime,
) -> Dict:
    if not pair_results:
        logger.error("No results to report")
        return {}

    # Aggregate equity curves on a common time grid
    all_curves = []
    for key, r in pair_results.items():
        if "timestamps" not in r:
            continue
        curve = pd.Series(r["equity_curve"], index=r["timestamps"], name=key)
        all_curves.append(curve)

    if not all_curves:
        return {}

    portfolio = pd.concat(all_curves, axis=1).sum(axis=1).sort_index()
    portfolio_df = portfolio.to_frame("equity")
    portfolio_df["returns"] = portfolio_df["equity"].pct_change().fillna(0)
    portfolio_df["cummax"] = portfolio_df["equity"].cummax()
    portfolio_df["drawdown"] = (
        (portfolio_df["equity"] - portfolio_df["cummax"]) / portfolio_df["cummax"]
    )

    final_eq = float(portfolio_df["equity"].iloc[-1])
    total_return = (final_eq - initial_capital) / initial_capital
    std = portfolio_df["returns"].std()
    sharpe = (
        float(portfolio_df["returns"].mean() / std * np.sqrt(HOURS_PER_YEAR))
        if std > 0 else 0.0
    )
    max_dd = float(portfolio_df["drawdown"].min())

    all_trades = []
    for key, r in pair_results.items():
        for t in r.get("trades", []):
            t["pair"] = key
            all_trades.append(t)

    pnls = [t["pnl"] for t in all_trades if t["reason"] not in ("end",)]
    n_trades = len(pnls)
    win_rate = sum(1 for p in pnls if p > 0) / n_trades if n_trades else 0.0
    total_fees = sum(r.get("fees_paid", 0) for r in pair_results.values())
    exit_reasons: Dict[str, int] = {}
    for t in all_trades:
        exit_reasons[t["reason"]] = exit_reasons.get(t["reason"], 0) + 1

    print("\n" + "=" * 70)
    print("CORNELL VALIDATION BACKTEST RESULTS")
    print("=" * 70)
    print(f"Training : {train_start.date()} → {train_end.date()}")
    print(f"Validation: {val_start.date()} → {val_end.date()}")
    print(f"Pairs traded    : {len(pair_results)}")
    print(f"Initial capital : ${initial_capital:,.2f}")
    print(f"Final equity    : ${final_eq:,.2f}")
    print(f"Total return    : {total_return:.2%}")
    print(f"Sharpe ratio    : {sharpe:.2f}")
    print(f"Max drawdown    : {max_dd:.2%}")
    print(f"Total trades    : {n_trades}")
    print(f"Win rate        : {win_rate:.2%}")
    print(f"Total fees paid : ${total_fees:,.2f}")
    print(f"Exit reasons    : {exit_reasons}")
    print("=" * 70)

    # Per-pair summary
    print("\nPer-pair validation Sharpe (top 10):")
    pair_sharpes = [
        (k, r["sharpe"], r["n_trades"], r["total_return"])
        for k, r in pair_results.items()
    ]
    pair_sharpes.sort(key=lambda x: -x[1])
    for rank, (k, s, nt, ret) in enumerate(pair_sharpes[:10], 1):
        cfg = f"L={pair_results[k]['L']} ze={pair_results[k]['z_entry']}"
        print(f"  {rank:2}. {k:20} sharpe={s:6.2f}  ret={ret:+.2%}  trades={nt:3}  [{cfg}]")

    # Save outputs
    os.makedirs(RESULTS_DIR, exist_ok=True)
    tag = f"cornell_{val_start.date()}_{val_end.date()}"
    eq_path = os.path.join(RESULTS_DIR, f"equity_curve_{tag}.csv")
    tr_path = os.path.join(RESULTS_DIR, f"trades_{tag}.csv")
    pp_path = os.path.join(RESULTS_DIR, f"per_pair_{tag}.csv")

    portfolio_df.to_csv(eq_path)

    if all_trades:
        pd.DataFrame(all_trades).to_csv(tr_path, index=False)

    pd.DataFrame([
        {
            "pair": k,
            "sharpe": r["sharpe"],
            "total_return": r["total_return"],
            "max_dd": r["max_dd"],
            "n_trades": r["n_trades"],
            "win_rate": r["win_rate"],
            "L": r.get("L"),
            "z_entry": r.get("z_entry"),
            "z_exit": r.get("z_exit"),
        }
        for k, r in pair_results.items()
    ]).sort_values("sharpe", ascending=False).to_csv(pp_path, index=False)

    # Chart
    chart_path = os.path.join(RESULTS_DIR, f"chart_{tag}.png")
    _plot_portfolio(portfolio_df, initial_capital, chart_path)

    print(f"\nSaved: {eq_path}")
    print(f"Saved: {tr_path}")
    print(f"Saved: {pp_path}")
    print(f"Saved: {chart_path}")

    return {
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "n_trades": n_trades,
        "total_fees": total_fees,
        "n_pairs": len(pair_results),
        "exit_reasons": exit_reasons,
    }


def _plot_portfolio(df: pd.DataFrame, initial_capital: float, path: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    ts = df.index.values
    ax1.plot(ts, df["equity"].values, linewidth=1.5, color="#2196F3")
    ax1.axhline(initial_capital, color="gray", linestyle="--", alpha=0.5)
    ax1.set_title("Portfolio Equity Curve (Cornell Validation)", fontweight="bold")
    ax1.set_ylabel("Equity ($)")
    ax1.grid(True, alpha=0.25)

    ax2.fill_between(ts, df["drawdown"].values * 100, 0, alpha=0.4, color="#F44336")
    ax2.set_title("Drawdown", fontweight="bold")
    ax2.set_ylabel("Drawdown (%)")
    ax2.grid(True, alpha=0.25)

    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close(fig)


# ===========================================================================
# CLI
# ===========================================================================

def _parse_args():
    p = argparse.ArgumentParser(description="Cornell-style validation backtest")
    p.add_argument("--train-start", default="2022-06-01")
    p.add_argument("--train-end",   default="2024-05-31")
    p.add_argument("--val-start",   default="2024-06-01")
    p.add_argument("--val-end",     default="2024-12-01")
    p.add_argument("--capital",     type=float, default=INITIAL_CAPITAL)
    p.add_argument("--cache-dir",   default=os.path.join(_PROJECT_ROOT, "historical_data"))
    p.add_argument("--top-n",       type=int, default=20,
                   help="Max pairs to trade (ranked by train Sharpe)")
    return p.parse_args()


def main():
    args = _parse_args()

    def _dt(s):
        d = datetime.strptime(s, "%Y-%m-%d")
        return d.replace(tzinfo=timezone.utc)

    train_start = _dt(args.train_start)
    train_end   = _dt(args.train_end)
    val_start   = _dt(args.val_start)
    val_end     = _dt(args.val_end)

    # ── Download data ────────────────────────────────────────────────────
    dl_start = train_start - timedelta(days=10)   # small buffer
    logger.info("Downloading training data …")
    train_data = download_history(CANDIDATE_TOKENS, dl_start, train_end, args.cache_dir)
    logger.info("Downloading validation data …")
    val_data   = download_history(CANDIDATE_TOKENS, train_start, val_end,   args.cache_dir)

    if not train_data:
        raise RuntimeError("No training data – check tokens/dates")

    train_ds = HistoricalDataService(train_data)
    train_ds.set_time(train_end)   # full training window visible

    val_ds = HistoricalDataService(val_data)
    val_ds.set_time(val_end)       # full validation window visible

    tokens = list(train_data.keys())

    # ── Build candidate pairs (same-blockchain filter) ───────────────────
    import itertools
    all_pairs_raw = list(itertools.combinations(tokens, 2))
    same_chain = [(a, b) for a, b in all_pairs_raw if are_same_blockchain(a, b)]
    logger.info(
        f"Candidate pairs after blockchain filter: "
        f"{len(same_chain)} / {len(all_pairs_raw)}"
    )

    # ── Optimise parameters on training window ───────────────────────────
    logger.info("Optimising per-pair parameters (60 configs each) …")
    optimized_params: Dict[str, Dict] = {}
    opt_rows = []

    for sym_a, sym_b in same_chain:
        cfg = optimize_parameters_for_pair(sym_a, sym_b, train_ds, 1_000_000.0)
        # Require both positive Sharpe AND positive training return.
        # Pairs with positive Sharpe but negative return are high-frequency losers
        # (many small wins, catastrophic tail losses) that will blow up in validation.
        if cfg and cfg["train_sharpe"] > 0 and cfg["train_return"] > 0:
            key = f"{sym_a}-{sym_b}"
            optimized_params[key] = cfg
            opt_rows.append(cfg)

    if not optimized_params:
        logger.error("No pairs survived optimisation with positive Sharpe")
        return

    # Save optimised params
    opt_df = pd.DataFrame(opt_rows).sort_values("train_sharpe", ascending=False)
    opt_path = os.path.join(
        RESULTS_DIR, f"optimized_params_{train_start.date()}_{train_end.date()}.csv"
    )
    os.makedirs(RESULTS_DIR, exist_ok=True)
    opt_df.to_csv(opt_path, index=False)
    logger.info(f"Saved optimised params: {opt_path}")

    print("\nTop 10 pairs by training Sharpe:")
    print(opt_df.head(10).to_string(index=False))

    # Select top-N pairs for validation
    top_pairs = [
        {"sym_a": r["sym_a"], "sym_b": r["sym_b"]}
        for _, r in opt_df.head(args.top_n).iterrows()
    ]

    # ── Validation backtest ──────────────────────────────────────────────
    logger.info(f"\nRunning validation backtest on {len(top_pairs)} pairs …")
    pair_results = run_validation(top_pairs, optimized_params, val_ds, args.capital)

    generate_portfolio_report(
        pair_results,
        args.capital,
        train_start, train_end,
        val_start, val_end,
    )


if __name__ == "__main__":
    main()
