"""Candle validation, interval verification, and integrity checking.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
from src.core.exceptions import InvalidCandleError
from src.core.logger import logger
from src.core.types import Candle


TIMEFRAME_MS = {
    "1m": 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


@dataclass
class ValidationReport:
    """Summary report of candlestick series validation."""
    symbol: str
    timeframe: str
    total_candles: int
    valid_candles: int
    invalid_candles: int
    missing_intervals: List[Tuple[int, int, int]]  # (gap_start_ms, gap_end_ms, missing_count)
    is_chronological: bool
    has_duplicates: bool
    is_consistent: bool
    error_messages: List[str]


class CandleValidator:
    """Validates structural and chronological consistency of candlesticks."""

    @staticmethod
    def validate_single_candle(c: Candle) -> Tuple[bool, Optional[str]]:
        """Validate a single candle's OHLCV values."""
        if c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
            return False, f"Negative or zero price: O={c.open}, H={c.high}, L={c.low}, C={c.close}"
        if c.volume < 0:
            return False, f"Negative volume: V={c.volume}"
        if c.high < c.low:
            return False, f"High ({c.high}) is lower than Low ({c.low})"
        if c.high < max(c.open, c.close):
            return False, f"High ({c.high}) is less than max(open, close)"
        if c.low > min(c.open, c.close):
            return False, f"Low ({c.low}) is greater than min(open, close)"
        if c.close_time_ms < c.timestamp_ms:
            return False, f"Close time ({c.close_time_ms}) precedes open time ({c.timestamp_ms})"
        return True, None

    @classmethod
    def validate_series(
        cls,
        candles: List[Candle],
        expected_timeframe: Optional[str] = None,
        now_ms: Optional[int] = None,
    ) -> ValidationReport:
        """Validate an entire chronological series of candles.
        Detects ordering issues, OHLC anomalies, duplicates, and interval gaps.
        """
        if not candles:
            return ValidationReport(
                symbol="",
                timeframe=expected_timeframe or "",
                total_candles=0,
                valid_candles=0,
                invalid_candles=0,
                missing_intervals=[],
                is_chronological=True,
                has_duplicates=False,
                is_consistent=True,
                error_messages=["Candle list is empty."],
            )

        symbol = candles[0].symbol
        tf = expected_timeframe or candles[0].timeframe
        expected_step = TIMEFRAME_MS.get(tf.lower())

        valid_count = 0
        invalid_count = 0
        missing_intervals: List[Tuple[int, int, int]] = []
        error_msgs: List[str] = []
        seen_timestamps = set()
        has_duplicates = False
        is_chronological = True

        for i, c in enumerate(candles):
            # Check individual candle
            is_valid, err = cls.validate_single_candle(c)
            if not is_valid:
                invalid_count += 1
                error_msgs.append(f"Candle at idx {i} (ts {c.timestamp_ms}): {err}")
            else:
                valid_count += 1

            # Check duplicates
            if c.timestamp_ms in seen_timestamps:
                has_duplicates = True
                error_msgs.append(f"Duplicate timestamp detected at index {i}: {c.timestamp_ms}")
            seen_timestamps.add(c.timestamp_ms)

            # Check chronological ordering and gaps
            if i > 0:
                prev_c = candles[i - 1]
                if c.timestamp_ms <= prev_c.timestamp_ms:
                    is_chronological = False
                    error_msgs.append(
                        f"Non-chronological ordering at idx {i}: {c.timestamp_ms} <= {prev_c.timestamp_ms}"
                    )
                elif expected_step:
                    step = c.timestamp_ms - prev_c.timestamp_ms
                    if step > expected_step:
                        missing_count = int(step // expected_step) - 1
                        missing_intervals.append((prev_c.timestamp_ms, c.timestamp_ms, missing_count))

            # Check incomplete candle (future close relative to now_ms)
            if now_ms is not None and c.close_time_ms > now_ms:
                c.is_closed = False

        is_consistent = (invalid_count == 0) and is_chronological and not has_duplicates

        return ValidationReport(
            symbol=symbol,
            timeframe=tf,
            total_candles=len(candles),
            valid_candles=valid_count,
            invalid_candles=invalid_count,
            missing_intervals=missing_intervals,
            is_chronological=is_chronological,
            has_duplicates=has_duplicates,
            is_consistent=is_consistent,
            error_messages=error_msgs,
        )

    @staticmethod
    def filter_closed_candles(candles: List[Candle], now_ms: int) -> List[Candle]:
        """Exclude incomplete candles that have not yet closed to prevent look-ahead bias."""
        return [c for c in candles if c.close_time_ms <= now_ms and c.is_closed]
