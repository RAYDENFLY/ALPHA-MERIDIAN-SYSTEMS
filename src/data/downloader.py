"""Resumable and Idempotent Historical Market Data Downloader.

Downloads 365 days of 1H and 4H candles and funding rates from Aster DEX.
Stores raw candles in SQLite and updates synchronization metadata.
"""

from datetime import datetime, timezone
import time
from typing import Callable, Dict, List, Optional
from src.core.logger import logger
from src.core.types import Candle
from src.data.database import DatabaseManager
from src.data.validator import CandleValidator, TIMEFRAME_MS
from src.exchange.base import BaseExchangeAdapter


class HistoricalDownloader:
    """Manages downloading, resumable backfills, and validation for symbols and timeframes."""

    def __init__(
        self,
        exchange: BaseExchangeAdapter,
        db: DatabaseManager,
        batch_size: int = 1000,
        request_delay_sec: float = 0.1,
    ) -> None:
        self.exchange = exchange
        self.db = db
        self.batch_size = batch_size
        self.request_delay_sec = request_delay_sec

    def download_symbol_timeframe(
        self,
        symbol: str,
        timeframe: str,
        days_back: int = 365,
        end_time_ms: Optional[int] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> int:
        """Download historical candles with automatic resumption and deduplication.
        Returns total number of candles inserted during this run.
        """
        now_ms = end_time_ms or int(time.time() * 1000)
        target_start_ms = now_ms - (days_back * 24 * 3600 * 1000)
        step_ms = TIMEFRAME_MS.get(timeframe.lower(), 3600 * 1000)

        # Check existing sync state
        sync = self.db.get_sync_state(symbol, timeframe)
        earliest_in_db = sync["earliest_timestamp_ms"] if sync else None
        latest_in_db = sync["latest_timestamp_ms"] if sync else None

        total_inserted = 0

        # Backfill backwards if DB start > target start
        if earliest_in_db is None:
            current_cursor = target_start_ms
            stop_cursor = now_ms
            logger.info(f"[{symbol} {timeframe}] Initial backfill from {target_start_ms} to {now_ms}")
            inserted = self._fetch_range(symbol, timeframe, current_cursor, stop_cursor, step_ms, progress_callback)
            total_inserted += inserted
        else:
            # 1. Backfill backward if earlier data needed
            if earliest_in_db > target_start_ms:
                logger.info(f"[{symbol} {timeframe}] Backfilling earlier history: {target_start_ms} to {earliest_in_db}")
                inserted = self._fetch_range(
                    symbol, timeframe, target_start_ms, earliest_in_db, step_ms, progress_callback
                )
                total_inserted += inserted

            # 2. Forward fill from latest in db to now
            if latest_in_db < (now_ms - step_ms):
                logger.info(f"[{symbol} {timeframe}] Forward syncing recent candles: {latest_in_db} to {now_ms}")
                inserted = self._fetch_range(
                    symbol, timeframe, latest_in_db + step_ms, now_ms, step_ms, progress_callback
                )
                total_inserted += inserted

        # Run validation check on downloaded series
        all_candles = self.db.get_candles(symbol, timeframe, start_time_ms=target_start_ms, end_time_ms=now_ms)
        report = CandleValidator.validate_series(all_candles, expected_timeframe=timeframe, now_ms=now_ms)
        if not report.is_consistent:
            logger.warning(
                f"[{symbol} {timeframe}] Validation anomalies detected: {len(report.error_messages)} issues. "
                f"Missing gaps: {len(report.missing_intervals)}"
            )
        else:
            logger.info(f"[{symbol} {timeframe}] Validation clean: {report.valid_candles} consecutive candles.")

        return total_inserted

    def _fetch_range(
        self,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
        step_ms: int,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> int:
        """Fetch candles chunk by chunk from start_ms to end_ms."""
        current_start = start_ms
        inserted_total = 0

        while current_start < end_ms:
            time.sleep(self.request_delay_sec)
            try:
                chunk = self.exchange.get_klines(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time_ms=current_start,
                    end_time_ms=end_ms,
                    limit=self.batch_size,
                )
            except Exception as e:
                logger.error(f"Error fetching klines for {symbol} at {current_start}: {e}")
                break

            if not chunk:
                logger.info(f"[{symbol} {timeframe}] No further klines returned starting at {current_start}.")
                break

            inserted = self.db.insert_candles(chunk)
            inserted_total += inserted

            last_candle = chunk[-1]
            if progress_callback:
                progress_callback(symbol, inserted_total, len(chunk))

            # If the last candle reached or passed end_ms, stop
            if last_candle.timestamp_ms >= end_ms or len(chunk) < 2:
                break

            # Advance cursor to next candle
            next_start = last_candle.timestamp_ms + step_ms
            if next_start <= current_start:
                next_start = current_start + step_ms
            current_start = next_start

        return inserted_total

    def download_funding_rates(
        self,
        symbol: str,
        days_back: int = 365,
        end_time_ms: Optional[int] = None,
    ) -> int:
        """Download historical funding rates for a symbol."""
        now_ms = end_time_ms or int(time.time() * 1000)
        start_ms = now_ms - (days_back * 24 * 3600 * 1000)

        current_start = start_ms
        total_inserted = 0

        while current_start < now_ms:
            time.sleep(self.request_delay_sec)
            try:
                rates = self.exchange.get_funding_rates(
                    symbol=symbol,
                    start_time_ms=current_start,
                    end_time_ms=now_ms,
                    limit=1000,
                )
            except Exception as e:
                logger.error(f"Error fetching funding rates for {symbol}: {e}")
                break

            if not rates:
                break

            inserted = self.db.insert_funding_rates(symbol, rates)
            total_inserted += inserted

            last_time = int(rates[-1].get("fundingTime", 0))
            if last_time >= now_ms or len(rates) < 2:
                break
            current_start = last_time + 1

        logger.info(f"[{symbol}] Downloaded and stored {total_inserted} funding rate records.")
        return total_inserted

    def download_watchlist(
        self,
        symbols: List[str],
        timeframes: List[str] = ["1h", "4h"],
        days_back: int = 365,
        download_funding: bool = True,
    ) -> Dict[str, Dict[str, int]]:
        """Download complete 365-day dataset for all enabled symbols."""
        results: Dict[str, Dict[str, int]] = {}
        for sym in symbols:
            results[sym] = {}
            for tf in timeframes:
                logger.info(f"Starting historical sync: {sym} ({tf})...")
                cnt = self.download_symbol_timeframe(sym, tf, days_back=days_back)
                results[sym][tf] = cnt
            if download_funding:
                self.download_funding_rates(sym, days_back=days_back)

        return results
