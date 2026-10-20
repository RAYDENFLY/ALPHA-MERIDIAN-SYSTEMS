"""Non-Lookahead Swing High and Swing Low Detection.

A pivot at index i with L left bars and R right bars is confirmed ONLY at index (i + R).
No future candle beyond (i + R) is accessed.
"""

from typing import List, Optional
from src.core.types import Candle, SwingPoint, SwingType


def detect_swings(
    candles: List[Candle],
    left_bars: int = 3,
    right_bars: int = 3,
    max_index: Optional[int] = None,
) -> List[SwingPoint]:
    """Detect confirmed swing points up to max_index.

    Parameters:
    -----------
    candles : List[Candle]
        Chronologically ordered candles.
    left_bars : int
        Number of bars to the left that must be lower (for high) or higher (for low).
    right_bars : int
        Number of bars to the right required for confirmation.
    max_index : Optional[int]
        The latest candle index that can be inspected. Defaults to len(candles) - 1.

    Returns:
    --------
    List[SwingPoint]
        List of confirmed swings, strictly ordered by confirmation time.
    """
    if len(candles) < (left_bars + right_bars + 1):
        return []

    # Find the latest closed candle. An unclosed (in-progress) candle can never confirm a swing pivot.
    last_closed_idx = len(candles) - 1
    while last_closed_idx >= 0 and not getattr(candles[last_closed_idx], "is_closed", True):
        last_closed_idx -= 1

    if last_closed_idx < 0:
        return []

    limit = (last_closed_idx + 1) if max_index is None else min(last_closed_idx + 1, max_index + 1)
    swings: List[SwingPoint] = []

    # A swing at index i can only be confirmed once candle (i + right_bars) has closed.
    # Therefore, the latest index i we can evaluate is limit - 1 - right_bars.
    for i in range(left_bars, limit - right_bars):
        c_i = candles[i]
        confirmed_idx = i + right_bars
        confirmed_ts = candles[confirmed_idx].timestamp_ms

        # 1. Check Swing High
        is_swing_high = True
        for j in range(i - left_bars, i):
            if candles[j].high >= c_i.high:
                is_swing_high = False
                break
        if is_swing_high:
            for k in range(i + 1, i + right_bars + 1):
                if candles[k].high > c_i.high:
                    is_swing_high = False
                    break

        if is_swing_high:
            swings.append(
                SwingPoint(
                    index=i,
                    timestamp_ms=c_i.timestamp_ms,
                    confirmed_index=confirmed_idx,
                    confirmed_timestamp_ms=confirmed_ts,
                    price=c_i.high,
                    swing_type=SwingType.HIGH,
                )
            )
            continue  # An index cannot be both high and low simultaneously

        # 2. Check Swing Low
        is_swing_low = True
        for j in range(i - left_bars, i):
            if candles[j].low <= c_i.low:
                is_swing_low = False
                break
        if is_swing_low:
            for k in range(i + 1, i + right_bars + 1):
                if candles[k].low < c_i.low:
                    is_swing_low = False
                    break

        if is_swing_low:
            swings.append(
                SwingPoint(
                    index=i,
                    timestamp_ms=c_i.timestamp_ms,
                    confirmed_index=confirmed_idx,
                    confirmed_timestamp_ms=confirmed_ts,
                    price=c_i.low,
                    swing_type=SwingType.LOW,
                )
            )

    return swings


def get_latest_confirmed_swings(
    swings: List[SwingPoint],
    confirmed_before_or_at_ts: int,
) -> tuple[Optional[SwingPoint], Optional[SwingPoint]]:
    """Return the most recent confirmed swing high and swing low available at a given timestamp."""
    latest_high: Optional[SwingPoint] = None
    latest_low: Optional[SwingPoint] = None

    for s in swings:
        if s.confirmed_timestamp_ms <= confirmed_before_or_at_ts:
            if s.swing_type == SwingType.HIGH:
                if latest_high is None or s.confirmed_timestamp_ms > latest_high.confirmed_timestamp_ms:
                    latest_high = s
            elif s.swing_type == SwingType.LOW:
                if latest_low is None or s.confirmed_timestamp_ms > latest_low.confirmed_timestamp_ms:
                    latest_low = s

    return latest_high, latest_low
