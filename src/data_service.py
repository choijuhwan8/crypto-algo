"""
Data service: warm-up fetch + rolling candle cache for all candidate symbols.

Design decisions
----------------
* Uses ccxt binanceusdm (USD-M futures).  Sandbox mode is enabled when
  BINANCE_TESTNET=true so no real orders can be placed.
* fetch_ohlcv paginates automatically to collect WARMUP_HOURS candles.
* update_candle appends the latest *closed* bar to the in-memory cache and
  trims to WARMUP_HOURS so memory stays bounded.
* is_data_fresh() checks last-fetch time; the risk engine calls this to
  activate the kill switch when data goes stale.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import ccxt
import pandas as pd

from .config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    BINANCE_TESTNET,
    CANDIDATE_TOKENS,
    MAX_DATA_STALENESS_MIN,
    TIMEFRAME,
    WARMUP_HOURS,
)

logger = logging.getLogger(__name__)

# Binance returns at most 1 500 candles per REST call
_BATCH = 1_500


class DataService:
    def __init__(self) -> None:
        self.exchange = ccxt.binanceusdm(
            {
                "apiKey": BINANCE_API_KEY,
                "secret": BINANCE_API_SECRET,
                "options": {"defaultType": "future"},
            }
        )
        if BINANCE_TESTNET:
            self.exchange.set_sandbox_mode(True)
            logger.info("DataService: Binance testnet / sandbox enabled")
        else:
            logger.info("DataService: Binance MAINNET")

        self._cache: Dict[str, pd.DataFrame] = {}
        self._last_fetch: Dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def warmup(self, symbols: List[str]) -> None:
        """Fetch WARMUP_HOURS candles for every symbol in *symbols*."""
        logger.info(f"Warming up {len(symbols)} symbols …")
        for sym in symbols:
            try:
                self.fetch_ohlcv(sym, limit=WARMUP_HOURS)
                logger.info(f"  {sym}: {len(self._cache.get(sym, []))} bars loaded")
            except Exception as exc:
                logger.warning(f"  {sym}: warmup failed – {exc}")
            time.sleep(self.exchange.rateLimit / 1_000)

    def fetch_ohlcv(
        self,
        token: str,
        timeframe: str = TIMEFRAME,
        limit: int = WARMUP_HOURS,
    ) -> pd.DataFrame:
        """
        Paginate Binance REST to collect *limit* closed hourly candles.
        Stores result in cache and returns the DataFrame.
        """
        usdt_symbol = f"{token}/USDT:USDT"
        all_rows: list = []
        since: Optional[int] = None

        while len(all_rows) < limit:
            batch_size = min(_BATCH, limit - len(all_rows))
            try:
                rows = self.exchange.fetch_ohlcv(
                    usdt_symbol, timeframe, since=since, limit=batch_size
                )
            except ccxt.BaseError as exc:
                logger.error(f"fetch_ohlcv {token}: {exc}")
                break

            if not rows:
                break

            if since is None:
                # First batch – prepend (we paginate backwards below)
                all_rows = rows + all_rows
                # Walk backwards: set since to earliest timestamp we have
                since = rows[0][0] - batch_size * 3_600_000
            else:
                all_rows = rows + all_rows
                since = rows[0][0] - batch_size * 3_600_000

            if len(rows) < batch_size:
                break

            time.sleep(self.exchange.rateLimit / 1_000)

        # Keep at most the last *limit* candles
        df = _to_df(all_rows[-limit:])
        self._cache[token] = df
        self._last_fetch[token] = datetime.now(timezone.utc)
        return df

    def update_candle(self, token: str) -> Optional[pd.DataFrame]:
        """
        Append the latest *closed* candle to the cache.
        Trims cache to WARMUP_HOURS.  Returns updated DataFrame or None on error.
        """
        usdt_symbol = f"{token}/USDT:USDT"
        try:
            rows = self.exchange.fetch_ohlcv(usdt_symbol, TIMEFRAME, limit=3)
        except ccxt.BaseError as exc:
            logger.error(f"update_candle {token}: {exc}")
            return None

        if not rows or len(rows) < 2:
            return None

        # rows[-1] is the *open* (incomplete) candle; rows[-2] is latest closed
        closed_rows = rows[:-1]
        df_new = _to_df(closed_rows)

        if token in self._cache:
            existing = self._cache[token]
            combined = pd.concat([existing, df_new])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.sort_index(inplace=True)
            self._cache[token] = combined.tail(WARMUP_HOURS)
        else:
            self._cache[token] = df_new

        self._last_fetch[token] = datetime.now(timezone.utc)
        return self._cache[token]

    def get_cache(self, token: str) -> Optional[pd.DataFrame]:
        return self._cache.get(token)

    def is_data_fresh(self, token: str) -> bool:
        if token not in self._last_fetch:
            return False
        age_min = (
            datetime.now(timezone.utc) - self._last_fetch[token]
        ).total_seconds() / 60
        return age_min < MAX_DATA_STALENESS_MIN

    def get_available_tokens(self) -> List[str]:
        """Return candidate tokens that exist as USDT-M perpetuals on Binance."""
        try:
            markets = self.exchange.load_markets()
        except Exception as exc:
            logger.error(f"load_markets failed: {exc}")
            return CANDIDATE_TOKENS  # fall back to full list

        available = [t for t in CANDIDATE_TOKENS if f"{t}/USDT:USDT" in markets]
        logger.info(f"Available tokens: {len(available)} / {len(CANDIDATE_TOKENS)}")
        return available


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _to_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df.astype(float)
