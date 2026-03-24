"""
Paper trading bot entry point.

Cadence
-------
  Every  1 h : update candles → recalc spread/z-score/beta → entry / exit
  Every 24 h : recalc correlation + cointegration for candidate universe
  Every  7 d : reselect top pairs + rebalance allocation weights
  Every 30 d : run full validation report (4.5 / 5 / 10 bps stress)

  Telegram polling runs as a concurrent asyncio task.

Usage
-----
  cp .env.example .env       # fill in your keys
  pip install -r requirements.txt
  python main.py
"""
from __future__ import annotations

import asyncio
import logging
import logging.config
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import (
    CANDIDATE_TOKENS,
    DATA_DIR,
    FEE_BPS,
    INITIAL_CAPITAL,
    LOG_DIR,
    TIMEFRAME,
)
from src.data_service import DataService
from src.execution_engine import PaperExecutionEngine
from src.pair_selector import PairSelector
from src.risk_engine import RiskEngine
from src.signal_service import Signal, SignalService
from src.state_manager import StateManager
from src.telegram_bot import TelegramBot
from src.validator import Validator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "bot.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Bot orchestrator
# ---------------------------------------------------------------------------


class PaperBot:
    def __init__(self) -> None:
        logger.info("Initialising PaperBot …")

        self.state = StateManager()
        self.ds = DataService()
        self.pair_selector = PairSelector(self.ds)
        self.signal_svc = SignalService(self.ds)
        self.execution = PaperExecutionEngine(self.state)
        self.risk = RiskEngine(self.state)
        self.telegram = TelegramBot(self.risk, self.state, self.signal_svc)
        self.validator = Validator(self.ds, self.pair_selector)

        self.telegram.set_close_all_callback(self._close_all_positions)

        self._available_tokens: List[str] = []
        self._scheduler: Optional[AsyncIOScheduler] = None

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def start(self) -> None:
        logger.info("=== PaperBot starting ===")
        self.state.append_run_log("START", f"equity={self.state.get_equity():.2f}")

        # 1. Discover available USDT-M tokens
        self._available_tokens = self.ds.get_available_tokens()
        logger.info(f"Available tokens: {self._available_tokens}")

        # 2. Warm up data cache
        logger.info("Warming up data cache – this may take a few minutes …")
        self.ds.warmup(self._available_tokens)

        # 3. Initial pair selection
        logger.info("Running initial pair selection …")
        await asyncio.get_event_loop().run_in_executor(
            None, self.pair_selector.run, self._available_tokens
        )

        # 4. Set up scheduler
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._scheduler.add_job(
            self._hourly_worker, "cron", minute=1,
            id="hourly", name="Hourly signal/execution",
        )
        self._scheduler.add_job(
            self._daily_worker, "cron", hour=0, minute=5,
            id="daily", name="Daily pair refresh",
        )
        self._scheduler.add_job(
            self._weekly_worker, "cron", day_of_week="mon", hour=0, minute=10,
            id="weekly", name="Weekly reselect + rebalance",
        )
        self._scheduler.add_job(
            self._monthly_worker, "cron", day=1, hour=1, minute=0,
            id="monthly", name="Monthly validation report",
        )
        self._scheduler.start()
        logger.info("Scheduler started")

        await self.telegram.send(
            f"*PaperBot started* 🚀\n"
            f"Equity: `${self.state.get_equity():,.2f}`\n"
            f"Pairs selected: `{len(self.pair_selector.get_pairs())}`\n"
            f"Testnet: `{True}`"
        )

        # 5. Run Telegram polling
        logger.info("Starting Telegram polling …")
        await self.telegram.poll()

    # ------------------------------------------------------------------
    # Scheduled workers
    # ------------------------------------------------------------------

    async def _hourly_worker(self) -> None:
        """Every 1 h: update candles → signals → entry / exit."""
        logger.info("--- Hourly worker ---")

        pairs = self.pair_selector.get_pairs()
        if not pairs:
            logger.warning("No pairs selected – skipping hourly worker")
            return

        active_symbols = list(
            {sym for p in pairs for sym in (p["sym_a"], p["sym_b"])}
        )

        # Update candles for active symbols
        for tok in active_symbols:
            self.ds.update_candle(tok)

        # Risk checks
        risk_status = self.risk.run_checks(self.ds, active_symbols)
        if not risk_status["ok"]:
            if risk_status.get("kill_active") and not risk_status.get("_kill_alerted"):
                await self.telegram.alert_kill(risk_status.get("kill_reason", ""))
                risk_status["_kill_alerted"] = True
            logger.warning("Risk check failed – skipping signal loop")
            return

        # Signal loop
        for pair_info in pairs:
            await self._process_pair(pair_info)

        self.state.append_run_log("HOURLY", f"pairs={len(pairs)}")

    async def _process_pair(self, pair_info: dict) -> None:
        tok_a = pair_info["sym_a"]
        tok_b = pair_info["sym_b"]
        pair_key = f"{tok_a}-{tok_b}"

        existing_pos = self.state.get_position(pair_key)

        current_direction = existing_pos.direction if existing_pos else None
        signal, stats = self.signal_svc.generate_signal(tok_a, tok_b, current_direction)

        if not stats:
            return

        zscore = stats["zscore"]

        # --- Handle open position ---
        if existing_pos:
            # Stop-loss check
            if existing_pos.is_stop_loss(stats["price_a"], stats["price_b"]):
                pnl = self.execution.exit_position(
                    existing_pos, stats, reason="stop_loss"
                )
                await self.telegram.alert_stop_loss(pair_key, pnl)
                return

            # Exit signal
            if signal == Signal.EXIT:
                pnl = self.execution.exit_position(
                    existing_pos, stats, reason="signal"
                )
                await self.telegram.alert_exit(pair_key, pnl, "signal")

        # --- New entry ---
        elif signal in (Signal.LONG_SPREAD, Signal.SHORT_SPREAD):
            if not self.risk.can_open_position():
                return

            pos = self.execution.enter_position(
                pair_info, signal.value, stats
            )
            if pos:
                await self.telegram.alert_entry(
                    pair_key=pair_key,
                    direction=signal.value,
                    zscore=zscore,
                    notional=pos.notional_a,
                    fee=pos.notional_a * 2 * FEE_BPS / 10_000,
                )

    async def _daily_worker(self) -> None:
        """Every 24 h: full data refresh + pair re-selection."""
        logger.info("--- Daily worker ---")

        # Refresh all data
        self.ds.warmup(self._available_tokens)

        # Re-run pair selection
        await asyncio.get_event_loop().run_in_executor(
            None, self.pair_selector.run, self._available_tokens
        )

        # Daily summary to Telegram
        await self.telegram.send_daily_summary()

        self.state.append_run_log(
            "DAILY",
            f"pairs={len(self.pair_selector.get_pairs())}"
        )
        logger.info("Daily worker done")

    async def _weekly_worker(self) -> None:
        """Every 7 d: reselect + rebalance (close positions not in new pair list)."""
        logger.info("--- Weekly worker ---")

        old_keys = {
            f"{p['sym_a']}-{p['sym_b']}"
            for p in self.pair_selector.get_pairs()
        }

        # Re-run selection
        await asyncio.get_event_loop().run_in_executor(
            None, self.pair_selector.run, self._available_tokens
        )

        new_keys = {
            f"{p['sym_a']}-{p['sym_b']}"
            for p in self.pair_selector.get_pairs()
        }

        removed = old_keys - new_keys
        if removed:
            logger.info(f"Weekly: closing positions for removed pairs: {removed}")
            for pair_key in removed:
                pos = self.state.get_position(pair_key)
                if pos:
                    # Build minimal stats from current cache
                    stats = self.signal_svc.get_stats(pair_key)
                    if not stats:
                        # Recompute
                        tok_a, tok_b = pair_key.split("-", 1)
                        stats = self.signal_svc.compute_stats(tok_a, tok_b)
                    if stats:
                        pnl = self.execution.exit_position(
                            pos, stats, reason="weekly_rebalance"
                        )
                        await self.telegram.alert_exit(pair_key, pnl, "weekly rebalance")

        await self.telegram.send(
            f"*Weekly Rebalance*\n"
            f"Active pairs: `{len(self.pair_selector.get_pairs())}`\n"
            f"Removed: `{len(removed)}`\n"
            f"Equity: `${self.state.get_equity():,.2f}`"
        )

        self.state.append_run_log(
            "WEEKLY",
            f"pairs={len(self.pair_selector.get_pairs())} removed={len(removed)}"
        )

    async def _monthly_worker(self) -> None:
        """Every 30 d: full validation report + fee stress."""
        logger.info("--- Monthly validation ---")

        report = await asyncio.get_event_loop().run_in_executor(
            None, self.validator.run
        )

        msg = self.validator.format_telegram_message(report)
        await self.telegram.send(msg)

        self.state.append_run_log("MONTHLY_VALIDATION", "done")

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    async def _close_all_positions(self) -> None:
        """Close every open position (called by /closeall Telegram command)."""
        positions = list(self.state.get_open_positions().values())
        if not positions:
            await self.telegram.send("No open positions to close.")
            return

        for pos in positions:
            pair_key = pos.pair_key
            tok_a, tok_b = pos.sym_a, pos.sym_b
            stats = self.signal_svc.compute_stats(tok_a, tok_b)
            if stats:
                pnl = self.execution.exit_position(pos, stats, reason="manual_closeall")
                await self.telegram.alert_exit(pair_key, pnl, "manual close-all")
            else:
                logger.warning(f"_close_all: no stats for {pair_key}, skipping")

        await self.telegram.send(
            f"All positions closed.  Equity: `${self.state.get_equity():,.2f}`"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    bot = PaperBot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Interrupted by user – shutting down")
    except Exception as exc:
        logger.critical(f"Fatal error: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
