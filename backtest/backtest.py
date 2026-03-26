"""
Historical backtester for the crypto pairs trading bot.

Design
------
* HistoricalDataService  – wraps pre-loaded DataFrames and implements the same
  get_cache() interface as DataService so that PairSelector and SignalService
  can be reused unchanged.  At each timestep it serves only data up to
  current_time, guaranteeing zero look-ahead bias.

* BacktestExecutionEngine – mirrors PaperExecutionEngine but works on the
  HistoricalDataService and adds slippage + funding costs.

* Backtester – hour-by-hour simulation loop that mirrors _hourly_worker(),
  _daily_worker(), and _weekly_worker() from main.py.

Usage
-----
  python backtest/backtest.py                          # default: last 6 months
  python backtest/backtest.py --start 2024-01-01 --end 2024-07-01
  python backtest/backtest.py --capital 50000 --start 2024-06-01 --end 2024-12-01
  python -m backtest.backtest                          # also works from project root
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Project root resolution – ensures `src` imports work when running directly
# or via `python -m backtest.backtest` from the project root.
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backtest")

# ---------------------------------------------------------------------------
# Constants (mirrors config.py defaults, overridable via env)
# ---------------------------------------------------------------------------

from src.config import (
    CANDIDATE_TOKENS,
    COINT_PVALUE_MAX,
    CORR_LOG_PRICE_MIN,
    CORR_RETURN_MIN,
    FEE_BPS,
    IC_THRESHOLD,
    INITIAL_CAPITAL,
    LEVERAGE,
    MAX_CONCURRENT_PAIRS,
    MAX_POSITION_PCT,
    ROLLING_WINDOW,
    STOP_LOSS_PCT,
    TOP_N_PAIRS,
    Z_ENTRY,
    Z_EXIT,
)
from src.pair_selector import PairSelector
from src.signal_service import Signal, SignalService
from src.validator import _compute_metrics

SLIPPAGE_BPS = float(os.getenv("BT_SLIPPAGE_BPS", 5))          # 0.05% each side
FUNDING_RATE_8H = float(os.getenv("BT_FUNDING_RATE_8H", 0.01)) # 0.01% per 8 h
MAX_HOLD_HOURS = float(os.getenv("MAX_HOLD_HOURS", 168))
ZSCORE_BREAKDOWN = float(os.getenv("ZSCORE_BREAKDOWN_THRESHOLD", 2.5))
RESULTS_DIR = os.path.dirname(os.path.abspath(__file__))  # outputs land in backtest/


# ===========================================================================
# Historical Data Service
# ===========================================================================

class HistoricalDataService:
    """
    Wraps {token: full_DataFrame} and exposes get_cache(token) sliced to
    [start, current_time] so every consumer sees only past data.

    Implements the same interface as DataService so PairSelector and
    SignalService work without modification.
    """

    def __init__(self, full_data: Dict[str, pd.DataFrame]) -> None:
        self._full: Dict[str, pd.DataFrame] = full_data
        self._current_time: datetime = datetime.min.replace(tzinfo=timezone.utc)

    def set_time(self, t: datetime) -> None:
        self._current_time = t

    def get_cache(self, token: str) -> Optional[pd.DataFrame]:
        df = self._full.get(token)
        if df is None:
            return None
        return df[df.index <= self._current_time]

    def get_price(self, token: str, t: datetime) -> Optional[float]:
        df = self._full.get(token)
        if df is None:
            return None
        past = df[df.index <= t]
        if past.empty:
            return None
        return float(past["close"].iloc[-1])

    def available_tokens(self) -> List[str]:
        return list(self._full.keys())


# ===========================================================================
# Data Downloader
# ===========================================================================

def download_history(
    tokens: List[str],
    start: datetime,
    end: datetime,
    cache_dir: str = "historical_data",
) -> Dict[str, pd.DataFrame]:
    """
    Download hourly OHLCV for each token from Binance USD-M futures.
    Results are cached as CSV files so subsequent runs are instant.
    """
    import ccxt
    import time as _time

    from src.config import (
        BINANCE_API_KEY,
        BINANCE_API_SECRET,
        BINANCE_TESTNET,
    )

    # Resolve relative cache_dir paths to the project root so that output
    # always lands at the project root regardless of the working directory.
    if not os.path.isabs(cache_dir):
        cache_dir = os.path.join(_PROJECT_ROOT, cache_dir)

    os.makedirs(cache_dir, exist_ok=True)

    exchange = ccxt.binanceusdm(
        {"apiKey": BINANCE_API_KEY, "secret": BINANCE_API_SECRET,
         "options": {"defaultType": "future"}}
    )
    if BINANCE_TESTNET:
        # Testnet only has recent data; fall back to mainnet public endpoints
        # for historical download (read-only, no auth needed for public OHLCV)
        exchange = ccxt.binanceusdm({"options": {"defaultType": "future"}})
        logger.info("Downloader: using Binance mainnet public endpoints for history")

    since_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    data: Dict[str, pd.DataFrame] = {}

    for token in tokens:
        symbol = f"{token}/USDT:USDT"
        cache_file = os.path.join(
            cache_dir,
            f"{token}_{start.date()}_{end.date()}.csv",
        )

        if os.path.exists(cache_file):
            df = pd.read_csv(cache_file, index_col="timestamp", parse_dates=True)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            logger.info(f"  {token}: loaded {len(df)} bars from cache")
            data[token] = df
            continue

        logger.info(f"  {token}: downloading …")
        all_rows: list = []
        cursor = since_ms

        while cursor < end_ms:
            try:
                rows = exchange.fetch_ohlcv(
                    symbol, "1h", since=cursor, limit=1_000
                )
            except Exception as exc:
                logger.warning(f"  {token}: fetch error – {exc}")
                break

            if not rows:
                break

            all_rows.extend(rows)
            cursor = rows[-1][0] + 3_600_000

            if cursor >= end_ms:
                break

            _time.sleep(exchange.rateLimit / 1_000)

        if not all_rows:
            logger.warning(f"  {token}: no data downloaded – skipping")
            continue

        df = pd.DataFrame(
            all_rows,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)

        # Trim to requested window (start/end are already tz-aware)
        df = df[(df.index >= pd.Timestamp(start).tz_convert("UTC"))
                & (df.index < pd.Timestamp(end).tz_convert("UTC"))]

        try:
            df.to_csv(cache_file)
        except Exception as exc:
            logger.warning(f"  {token}: could not save cache – {exc}")

        logger.info(f"  {token}: {len(df)} bars")
        data[token] = df

    return data


# ===========================================================================
# Backtest Position
# ===========================================================================

class BTPosition:
    """Lightweight position object used during backtesting."""

    def __init__(
        self,
        pair_key: str,
        sym_a: str,
        sym_b: str,
        direction: str,
        entry_price_a: float,
        entry_price_b: float,
        notional_a: float,
        notional_b: float,
        entry_time: datetime,
        entry_zscore: float,
    ) -> None:
        self.position_id = uuid.uuid4().hex[:8]
        self.pair_key = pair_key
        self.sym_a = sym_a
        self.sym_b = sym_b
        self.direction = direction
        self.entry_price_a = entry_price_a
        self.entry_price_b = entry_price_b
        self.notional_a = notional_a
        self.notional_b = notional_b
        self.entry_time = entry_time
        self.entry_zscore = entry_zscore
        self.pnl: float = 0.0
        self.status: str = "OPEN"

    def unrealized_pnl(self, pa: float, pb: float) -> float:
        if self.direction == "LONG_SPREAD":
            gross = (
                self.notional_a * (pa - self.entry_price_a) / self.entry_price_a
                + self.notional_b * (self.entry_price_b - pb) / self.entry_price_b
            )
        else:
            gross = (
                self.notional_a * (self.entry_price_a - pa) / self.entry_price_a
                + self.notional_b * (pb - self.entry_price_b) / self.entry_price_b
            )
        self.pnl = gross
        return gross

    def pnl_pct(self, pa: float, pb: float) -> float:
        return self.unrealized_pnl(pa, pb) / (self.notional_a + self.notional_b)

    def is_stop_loss(self, pa: float, pb: float) -> bool:
        return self.pnl_pct(pa, pb) < -STOP_LOSS_PCT


# ===========================================================================
# Backtest Execution Engine
# ===========================================================================

class BacktestExecutionEngine:
    """
    Mirrors PaperExecutionEngine but:
    - fetches prices from HistoricalDataService at current_time
    - adds configurable slippage (SLIPPAGE_BPS each side)
    - adds funding cost (FUNDING_RATE_8H × hours_held / 8)
    """

    def __init__(self, ds: HistoricalDataService, equity_ref: list) -> None:
        """equity_ref is a one-element list so mutations are visible to caller."""
        self.ds = ds
        self._equity = equity_ref  # mutable reference

    @property
    def equity(self) -> float:
        return self._equity[0]

    def _set_equity(self, v: float) -> None:
        self._equity[0] = v

    def _slipped_price(self, price: float, side: str) -> float:
        """Buy at ask (slightly above), sell at bid (slightly below)."""
        factor = 1 + SLIPPAGE_BPS / 10_000 if side == "BUY" else 1 - SLIPPAGE_BPS / 10_000
        return price * factor

    def enter_position(
        self,
        pair_info: Dict,
        signal_str: str,
        stats: Dict,
        current_time: datetime,
    ) -> Optional[BTPosition]:
        tok_a, tok_b = pair_info["sym_a"], pair_info["sym_b"]
        pa_raw = stats["price_a"]
        pb_raw = stats["price_b"]

        if signal_str == "LONG_SPREAD":
            pa = self._slipped_price(pa_raw, "BUY")
            pb = self._slipped_price(pb_raw, "SELL")
        else:
            pa = self._slipped_price(pa_raw, "SELL")
            pb = self._slipped_price(pb_raw, "BUY")

        notional = (self.equity * MAX_POSITION_PCT / MAX_CONCURRENT_PAIRS) * LEVERAGE
        if notional < 10:
            return None

        fee = notional * 2 * FEE_BPS / 10_000
        self._set_equity(self.equity - fee)

        return BTPosition(
            pair_key=f"{tok_a}-{tok_b}",
            sym_a=tok_a,
            sym_b=tok_b,
            direction=signal_str,
            entry_price_a=pa,
            entry_price_b=pb,
            notional_a=notional,
            notional_b=notional,
            entry_time=current_time,
            entry_zscore=stats["zscore"],
        )

    def exit_position(
        self,
        position: BTPosition,
        stats: Dict,
        current_time: datetime,
        reason: str = "signal",
    ) -> Dict:
        """Returns a closed-trade dict."""
        pa_raw = stats["price_a"]
        pb_raw = stats["price_b"]

        if position.direction == "LONG_SPREAD":
            pa = self._slipped_price(pa_raw, "SELL")
            pb = self._slipped_price(pb_raw, "BUY")
            gross = (
                position.notional_a * (pa - position.entry_price_a) / position.entry_price_a
                + position.notional_b * (position.entry_price_b - pb) / position.entry_price_b
            )
        else:
            pa = self._slipped_price(pa_raw, "BUY")
            pb = self._slipped_price(pb_raw, "SELL")
            gross = (
                position.notional_a * (position.entry_price_a - pa) / position.entry_price_a
                + position.notional_b * (pb - position.entry_price_b) / position.entry_price_b
            )

        fee = position.notional_a * 2 * FEE_BPS / 10_000
        funding = self._funding_cost(position, current_time)
        net = gross - fee - funding

        self._set_equity(self.equity + net)

        hours_held = (current_time - position.entry_time).total_seconds() / 3600
        return {
            "position_id": position.position_id,
            "pair_key": position.pair_key,
            "direction": position.direction,
            "entry_time": position.entry_time,
            "exit_time": current_time,
            "entry_zscore": position.entry_zscore,
            "entry_price_a": position.entry_price_a,
            "entry_price_b": position.entry_price_b,
            "exit_price_a": pa,
            "exit_price_b": pb,
            "notional": position.notional_a,
            "gross_pnl": round(gross, 4),
            "fees": round(fee, 4),
            "funding": round(funding, 4),
            "pnl": round(net, 4),
            "hours_held": round(hours_held, 1),
            "reason": reason,
        }

    def _funding_cost(self, position: BTPosition, current_time: datetime) -> float:
        """Proxy: FUNDING_RATE_8H × (notional_a + notional_b) × (hours_held / 8)."""
        hours = (current_time - position.entry_time).total_seconds() / 3600
        periods = hours / 8.0
        return (position.notional_a + position.notional_b) * FUNDING_RATE_8H / 100 * periods

    def unrealized_pnl(self, position: BTPosition, stats: Dict) -> float:
        return position.unrealized_pnl(stats["price_a"], stats["price_b"])


# ===========================================================================
# Backtester
# ===========================================================================

class Backtester:
    def __init__(
        self,
        start: datetime,
        end: datetime,
        initial_capital: float = INITIAL_CAPITAL,
        tokens: Optional[List[str]] = None,
        cache_dir: str = "historical_data",
    ) -> None:
        self.start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        self.end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
        self.initial_capital = initial_capital
        self.tokens = tokens or CANDIDATE_TOKENS
        self._cache_dir = cache_dir

        # Download data with 100-day warmup prepended
        download_start = self.start - timedelta(days=100)
        logger.info(f"Downloading historical data for {len(self.tokens)} tokens …")
        full_data = download_history(self.tokens, download_start, self.end, cache_dir)

        if not full_data:
            raise RuntimeError("No historical data downloaded – check your tokens/dates")

        self.ds = HistoricalDataService(full_data)

        # Equity is a mutable one-element list shared with the execution engine
        self._equity_ref: list = [initial_capital]
        self.execution = BacktestExecutionEngine(self.ds, self._equity_ref)

        # Reuse live classes – they call self.ds.get_cache() which is windowed
        self.pair_selector = PairSelector(self.ds)
        self.signal_svc = SignalService(self.ds)

        # State
        self.open_positions: Dict[str, BTPosition] = {}  # pair_key → BTPosition
        self.closed_trades: List[Dict] = []
        self.equity_curve: List[Dict] = []
        self.active_pairs: List[Dict] = []
        self.peak_equity: float = initial_capital
        self._killed: bool = False   # set once when DD kill-switch fires

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> Dict:
        logger.info(f"Backtest: {self.start.date()} → {self.end.date()}")

        total_hours = int((self.end - self.start).total_seconds() / 3600)

        # Initial pair selection at start (uses warmup window)
        self.ds.set_time(self.start)
        self._select_pairs()

        for h in range(total_hours):
            t = self.start + timedelta(hours=h)
            self.ds.set_time(t)

            self._hourly_tick(t)
            self._log_equity(t)

            if t.hour == 0:
                self._daily_tick(t)

            if t.weekday() == 0 and t.hour == 0:
                self._weekly_tick(t)

            if h % 168 == 0:
                logger.info(
                    f"  {t.date()} | equity=${self._equity_ref[0]:,.2f} "
                    f"| open={len(self.open_positions)} "
                    f"| trades={len(self.closed_trades)}"
                )

        # Force-close any remaining open positions at end
        self.ds.set_time(self.end)
        for pos in list(self.open_positions.values()):
            stats = self.signal_svc.compute_stats(pos.sym_a, pos.sym_b)
            if stats:
                trade = self.execution.exit_position(pos, stats, self.end, reason="end_of_backtest")
                self.closed_trades.append(trade)
        self.open_positions.clear()

        return self._generate_report()

    # ------------------------------------------------------------------
    # Tick handlers
    # ------------------------------------------------------------------

    def _hourly_tick(self, t: datetime) -> None:
        # 1. Portfolio drawdown kill-switch
        if self._killed:
            return
        equity = self._equity_ref[0]
        if self.peak_equity > 0:
            dd = (self.peak_equity - equity) / self.peak_equity
            if dd > float(os.getenv("MAX_PORTFOLIO_DD", 0.25)):
                logger.warning(f"[{t}] Kill switch triggered: portfolio DD {dd:.1%} — halting backtest trading")
                self._killed = True
                return
        if equity > self.peak_equity:
            self.peak_equity = equity

        # 2. Process open positions
        for pair_key in list(self.open_positions.keys()):
            pos = self.open_positions[pair_key]
            stats = self.signal_svc.compute_stats(pos.sym_a, pos.sym_b)
            if not stats:
                continue

            # Max hold period
            hours_held = (t - pos.entry_time).total_seconds() / 3600
            if hours_held > MAX_HOLD_HOURS:
                trade = self.execution.exit_position(pos, stats, t, reason="max_hold_period")
                self.closed_trades.append(trade)
                del self.open_positions[pair_key]
                continue

            # Z-score breakdown
            z = stats["zscore"]
            if (pos.direction == "LONG_SPREAD" and z < -ZSCORE_BREAKDOWN) or \
               (pos.direction == "SHORT_SPREAD" and z > ZSCORE_BREAKDOWN):
                trade = self.execution.exit_position(pos, stats, t, reason="zscore_breakdown")
                self.closed_trades.append(trade)
                del self.open_positions[pair_key]
                continue

            # Stop-loss
            if pos.is_stop_loss(stats["price_a"], stats["price_b"]):
                trade = self.execution.exit_position(pos, stats, t, reason="stop_loss")
                self.closed_trades.append(trade)
                del self.open_positions[pair_key]
                continue

            # Normal exit signal
            signal, _ = self.signal_svc.generate_signal(
                pos.sym_a, pos.sym_b, pos.direction
            )
            if signal == Signal.EXIT:
                trade = self.execution.exit_position(pos, stats, t, reason="signal")
                self.closed_trades.append(trade)
                del self.open_positions[pair_key]

        # 3. Entry signals for active pairs not already in a position
        if len(self.open_positions) >= MAX_CONCURRENT_PAIRS:
            return

        for pair_info in self.active_pairs:
            pair_key = f"{pair_info['sym_a']}-{pair_info['sym_b']}"
            if pair_key in self.open_positions:
                continue
            if len(self.open_positions) >= MAX_CONCURRENT_PAIRS:
                break

            signal, stats = self.signal_svc.generate_signal(
                pair_info["sym_a"], pair_info["sym_b"]
            )
            if signal in (Signal.LONG_SPREAD, Signal.SHORT_SPREAD) and stats:
                pos = self.execution.enter_position(pair_info, signal.value, stats, t)
                if pos:
                    self.open_positions[pair_key] = pos

    def _daily_tick(self, t: datetime) -> None:
        pass  # placeholder for future daily logic

    def _weekly_tick(self, t: datetime) -> None:
        self._select_pairs()

    def _select_pairs(self) -> None:
        available = self.ds.available_tokens()
        self.active_pairs = self.pair_selector.run(available)

        # Close positions for pairs that dropped out of the active list
        active_keys = {f"{p['sym_a']}-{p['sym_b']}" for p in self.active_pairs}
        t = self.ds._current_time
        for pair_key in list(self.open_positions.keys()):
            if pair_key not in active_keys:
                pos = self.open_positions[pair_key]
                stats = self.signal_svc.compute_stats(pos.sym_a, pos.sym_b)
                if stats:
                    trade = self.execution.exit_position(
                        pos, stats, t, reason="weekly_rebalance"
                    )
                    self.closed_trades.append(trade)
                del self.open_positions[pair_key]

        logger.info(
            f"  [{t.date()}] Pairs selected: {len(self.active_pairs)} "
            f"– {[(p['sym_a'],p['sym_b']) for p in self.active_pairs]}"
        )

    def _log_equity(self, t: datetime) -> None:
        upnl = 0.0
        for pos in self.open_positions.values():
            stats = self.signal_svc.compute_stats(pos.sym_a, pos.sym_b)
            if stats:
                upnl += pos.unrealized_pnl(stats["price_a"], stats["price_b"])

        self.equity_curve.append({
            "timestamp": t,
            "equity": round(self._equity_ref[0] + upnl, 2),
            "cash": round(self._equity_ref[0], 2),
            "unrealized_pnl": round(upnl, 2),
            "open_positions": len(self.open_positions),
        })

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _generate_report(self) -> Dict:
        df = pd.DataFrame(self.equity_curve)
        if df.empty:
            logger.error("Empty equity curve – no report generated")
            return {}

        final_eq = float(df["equity"].iloc[-1])
        total_return = (final_eq / self.initial_capital) - 1

        df["returns"] = df["equity"].pct_change().fillna(0)
        std = df["returns"].std()
        sharpe = (
            df["returns"].mean() / std * np.sqrt(24 * 365) if std > 0 else 0.0
        )

        df["cummax"] = df["equity"].cummax()
        df["drawdown"] = (df["equity"] - df["cummax"]) / df["cummax"]
        max_dd = float(df["drawdown"].min())

        n = len(self.closed_trades)
        winners = [t for t in self.closed_trades if t["pnl"] > 0]
        win_rate = len(winners) / n if n else 0.0
        gross_profit = sum(t["pnl"] for t in self.closed_trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in self.closed_trades if t["pnl"] < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_duration = (
            sum(t["hours_held"] for t in self.closed_trades) / n if n else 0.0
        )
        total_fees = sum(t["fees"] for t in self.closed_trades)
        total_funding = sum(t["funding"] for t in self.closed_trades)

        # Exit reason breakdown
        reasons: Dict[str, int] = {}
        for t in self.closed_trades:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

        print("\n" + "=" * 70)
        print("BACKTEST RESULTS")
        print("=" * 70)
        print(f"Period          : {self.start.date()} → {self.end.date()}")
        print(f"Initial capital : ${self.initial_capital:,.2f}")
        print(f"Final equity    : ${final_eq:,.2f}")
        print(f"Total return    : {total_return:.2%}")
        print(f"Sharpe ratio    : {sharpe:.2f}")
        print(f"Max drawdown    : {max_dd:.2%}")
        print(f"Total trades    : {n}")
        print(f"Win rate        : {win_rate:.2%}")
        print(f"Profit factor   : {profit_factor:.2f}")
        print(f"Avg duration    : {avg_duration:.1f} h")
        print(f"Total fees paid : ${total_fees:,.2f}")
        print(f"Total funding   : ${total_funding:,.2f}")
        print("Exit reasons    :", reasons)
        print("=" * 70)

        os.makedirs(RESULTS_DIR, exist_ok=True)
        tag = f"{self.start.date()}_{self.end.date()}"
        eq_path = os.path.join(RESULTS_DIR, f"equity_curve_{tag}.csv")
        tr_path = os.path.join(RESULTS_DIR, f"trades_{tag}.csv")

        df.to_csv(eq_path, index=False)
        pd.DataFrame(self.closed_trades).to_csv(tr_path, index=False)
        print(f"\nSaved: {eq_path}")
        print(f"Saved: {tr_path}")

        chart_path = os.path.join(RESULTS_DIR, f"chart_{tag}.png")
        _plot(df, self.initial_capital, chart_path)
        print(f"Saved: {chart_path}")

        return {
            "total_return": total_return,
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_trades": n,
            "avg_duration_h": avg_duration,
            "total_fees": total_fees,
            "total_funding": total_funding,
            "exit_reasons": reasons,
        }


# ===========================================================================
# Plotting
# ===========================================================================

def _plot(df: pd.DataFrame, initial_capital: float, path: str) -> None:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        logger.warning("matplotlib not installed – skipping chart")
        return

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    ts = df["timestamp"].values  # numpy array avoids pandas tz-aware indexing issues

    ax1.plot(ts, df["equity"].values, linewidth=1.5, color="#2196F3")
    ax1.axhline(initial_capital, color="gray", linestyle="--", alpha=0.5, linewidth=1)
    ax1.set_title("Equity Curve", fontsize=13, fontweight="bold")
    ax1.set_ylabel("Equity ($)")
    ax1.grid(True, alpha=0.25)

    ax2.fill_between(
        ts, df["drawdown"].values * 100, 0,
        alpha=0.4, color="#F44336", label="Drawdown %"
    )
    ax2.set_title("Drawdown", fontsize=13, fontweight="bold")
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Date")
    ax2.grid(True, alpha=0.25)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))

    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close(fig)


# ===========================================================================
# CLI entry point
# ===========================================================================

def _parse_args():
    p = argparse.ArgumentParser(description="Crypto pairs trading backtester")
    p.add_argument("--start", default=None, help="Start date YYYY-MM-DD (default: 6 months ago)")
    p.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    p.add_argument("--capital", type=float, default=INITIAL_CAPITAL, help="Initial capital")
    p.add_argument(
        "--cache-dir",
        default=os.path.join(_PROJECT_ROOT, "historical_data"),
        help="Directory for cached OHLCV files",
    )
    return p.parse_args()


def main():
    args = _parse_args()

    end = (
        datetime.strptime(args.end, "%Y-%m-%d")
        if args.end
        else datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    )
    start = (
        datetime.strptime(args.start, "%Y-%m-%d")
        if args.start
        else end - timedelta(days=180)
    )

    bt = Backtester(
        start=start,
        end=end,
        initial_capital=args.capital,
        cache_dir=args.cache_dir,
    )
    bt.run()


if __name__ == "__main__":
    main()
