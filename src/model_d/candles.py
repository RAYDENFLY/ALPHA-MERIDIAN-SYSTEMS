from typing import Any, List, Tuple
import time
from src.core.types import Candle


def validate_closed_candles(candles: List[Candle], minimum: int = 8, now_ms: int = None) -> Tuple[List[Candle], str]:
    now_ms = now_ms or int(time.time() * 1000)
    if len(candles) < minimum: return [], "INSUFFICIENT_HISTORY"
    ordered = list(candles)
    seen = set(); previous = None
    for candle in ordered:
        if candle.timestamp_ms in seen: return [], "DUPLICATE_TIMESTAMP"
        seen.add(candle.timestamp_ms)
        if previous is not None and candle.timestamp_ms <= previous: return [], "OUT_OF_ORDER"
        if candle.open <= 0 or candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close) or candle.volume < 0: return [], "INVALID_OHLCV"
        previous = candle.timestamp_ms
    closed = [c for c in ordered if c.close_time_ms <= now_ms and c.is_closed]
    if len(closed) < minimum: return [], "INSUFFICIENT_CLOSED_HISTORY"
    # Detect gaps using the observed interval, without assuming a timeframe duration.
    intervals = [b.timestamp_ms - a.timestamp_ms for a, b in zip(closed, closed[1:])]
    if intervals and any(i != intervals[0] for i in intervals): return [], "CANDLE_GAP"
    return closed, "OK"
