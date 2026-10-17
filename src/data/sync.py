"""Incremental Real-Time Market Data Synchronizer.

Polls Aster DEX for new closed candles, validates integrity,
and updates SQLite storage without duplicating existing data.
"""

from datetime import datetime, timezone
import time
from typing import Dict, List, Optional
from src.core.logger import logger
from src.core.types import Candle
from src.data.database import DatabaseManager
from src.data.validator import CandleValidator, TIMEFRAME_MS
from src.exchange.base import BaseExchangeAdapter


class CandleSynchronizer:
    """Synchronizes latest closed candles for an active watchlist."""

    def __init__(self, exchange: BaseExchangeAdapter, db: DatabaseManager) -> None:
        self.exchange = exchange
        self.db = db

    def sync_symbol(self, symbol: str, timeframe: str, now_ms: Optional[int] = None) -> List[Candle]:
        """Fetch latest candles, filter incomplete ones, and insert into DB."""
        current_time = now_ms or int(time.time() * 1000)
        step_ms = TIMEFRAME_MS.get(timeframe.lower(), 3600 * 1000)

        # Get latest timestamp from DB
        sync = self.db.get_sync_state(symbol, timeframe)
        start_ts = (sync["latest_timestamp_ms"] + step_ms) if sync and sync["latest_timestamp_ms"] else None

        # Fetch recent candles (up to 50)
        try:
            candles = self.exchange.get_klines(
                symbol=symbol,
                timeframe=timeframe,
                start_time_ms=start_ts,
                limit=50,
            )
        except Exception as e:
            logger.error(f"Sync failed for {symbol} ({timeframe}): {e}")
            return []

        if not candles:
            return []

        # Filter out incomplete (still open) candle
        closed_candles = CandleValidator.filter_closed_candles(candles, now_ms=current_time)

        # Validate series
        report = CandleValidator.validate_series(closed_candles, expected_timeframe=timeframe, now_ms=current_time)
        if report.invalid_candles > 0:
            logger.warning(f"Validation found {report.invalid_candles} invalid candles during sync of {symbol}")
            # Keep only valid candles
            closed_candles = [c for c in closed_candles if CandleValidator.validate_single_candle(c)[0]]

        if closed_candles:
            self.db.insert_candles(closed_candles)
            logger.info(f"[{symbol} {timeframe}] Synced {len(closed_candles)} closed candles.")

        return closed_candles

    def sync_watchlist(
        self,
        symbols: List[str],
        timeframes: List[str] = ["1h", "4h"],
    ) -> Dict[str, Dict[str, int]]:
        """Sync newest candles for all watchlist symbols."""
        results: Dict[str, Dict[str, int]] = {}
        for s in symbols:
            results[s] = {}
            for tf in timeframes:
                synced = self.sync_symbol(s, tf)
                results[s][tf] = len(synced)
        return results
