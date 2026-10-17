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

    def sync_symbol(
        self,
        symbol: str,
        timeframe: str,
        now_ms: Optional[int] = None,
        max_pages: int = 20,
    ) -> List[Candle]:
        """Fetch latest candles, paginating forward if behind by >50 bars, filter incomplete ones, and insert into DB."""
        current_time = now_ms or int(time.time() * 1000)
        step_ms = TIMEFRAME_MS.get(timeframe.lower(), 3600 * 1000)

        # Get latest timestamp from DB
        sync = self.db.get_sync_state(symbol, timeframe)
        start_ts = (sync["latest_timestamp_ms"] + step_ms) if sync and sync["latest_timestamp_ms"] else None

        all_closed_candles: List[Candle] = []
        pages_fetched = 0
        next_start_ts = start_ts

        while pages_fetched < max(1, max_pages):
            if next_start_ts is not None and next_start_ts > current_time:
                break
            try:
                batch = self.exchange.get_klines(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time_ms=next_start_ts,
                    limit=500 if next_start_ts is not None and (current_time - next_start_ts) > 50 * step_ms else 50,
                )
            except Exception as e:
                logger.error(f"Sync failed for {symbol} ({timeframe}): {e}")
                break

            pages_fetched += 1
            if not batch:
                break

            closed_batch = CandleValidator.filter_closed_candles(batch, now_ms=current_time)
            if closed_batch:
                report = CandleValidator.validate_series(
                    closed_batch, expected_timeframe=timeframe, now_ms=current_time
                )
                if report.invalid_candles > 0:
                    logger.warning(
                        f"Validation found {report.invalid_candles} invalid candles during sync of {symbol}"
                    )
                    closed_batch = [
                        c for c in closed_batch if CandleValidator.validate_single_candle(c)[0]
                    ]
                if closed_batch:
                    self.db.insert_candles(closed_batch)
                    all_closed_candles.extend(closed_batch)

            if next_start_ts is None:
                break
            last_raw_ts = batch[-1].timestamp_ms
            if last_raw_ts + step_ms <= next_start_ts or last_raw_ts + step_ms > current_time - step_ms:
                break
            if len(batch) < 50:
                break
            next_start_ts = last_raw_ts + step_ms

        if all_closed_candles:
            logger.info(f"[{symbol} {timeframe}] Synced {len(all_closed_candles)} closed candles.")

        return all_closed_candles

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
