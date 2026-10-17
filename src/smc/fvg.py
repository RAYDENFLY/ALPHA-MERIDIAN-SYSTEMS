"""Fair Value Gap (FVG) Detection and Mitigation Tracking.

Identifies 3-candle price imbalances where an unfilled gap remains between
Candle 1 and Candle 3 wicks.
"""

from typing import List, Optional
from src.core.types import Candle, Direction, FairValueGap


class FVGDetector:
    """Detects and tracks 3-candle Fair Value Gaps."""

    @staticmethod
    def detect_fvgs(
        candles: List[Candle],
        min_gap_size_atr_ratio: float = 0.1,
        atr_period: int = 14,
    ) -> List[FairValueGap]:
        """Detect FVGs across candles with ATR filtering and mitigation tracking.

        Parameters:
        -----------
        candles : List[Candle]
            Chronological candles.
        min_gap_size_atr_ratio : float
            Minimum gap height relative to ATR.
        atr_period : int
            Period used to compute ATR.

        Returns:
        --------
        List[FairValueGap]
            Detected FVGs with mitigation tracking.
        """
        if len(candles) < 3:
            return []

        # Compute simple ATR
        tr_values: List[float] = []
        for i in range(len(candles)):
            if i == 0:
                tr_values.append(candles[i].high - candles[i].low)
            else:
                prev_c = candles[i - 1]
                tr = max(
                    candles[i].high - candles[i].low,
                    abs(candles[i].high - prev_c.close),
                    abs(candles[i].low - prev_c.close),
                )
                tr_values.append(tr)

        atr_values: List[float] = []
        running_tr = 0.0
        for i, tr in enumerate(tr_values):
            running_tr += tr
            if i < atr_period:
                atr_values.append(running_tr / (i + 1))
            else:
                running_tr -= tr_values[i - atr_period]
                atr_values.append(running_tr / atr_period)

        fvgs: List[FairValueGap] = []

        for i in range(2, len(candles)):
            c1 = candles[i - 2]
            c2 = candles[i - 1]
            c3 = candles[i]
            cur_atr = atr_values[i]
            min_gap = cur_atr * min_gap_size_atr_ratio

            # 1. Bullish FVG: c3.low > c1.high
            if c3.low > c1.high:
                gap_size = c3.low - c1.high
                if gap_size >= min_gap:
                    fvgs.append(
                        FairValueGap(
                            direction=Direction.LONG,
                            timestamp_ms=c2.timestamp_ms,
                            top=c3.low,
                            bottom=c1.high,
                            mitigated=False,
                        )
                    )

            # 2. Bearish FVG: c3.high < c1.low
            elif c3.high < c1.low:
                gap_size = c1.low - c3.high
                if gap_size >= min_gap:
                    fvgs.append(
                        FairValueGap(
                            direction=Direction.SHORT,
                            timestamp_ms=c2.timestamp_ms,
                            top=c1.low,
                            bottom=c3.high,
                            mitigated=False,
                        )
                    )

        # Check mitigation status sequentially
        ts_to_idx = {c.timestamp_ms: i for i, c in enumerate(candles)}
        for fvg in fvgs:
            fvg_idx = ts_to_idx.get(fvg.timestamp_ms)
            if fvg_idx is None:
                continue
            for post_c in candles[fvg_idx + 2:]:
                if fvg.direction == Direction.LONG:
                    if post_c.low <= fvg.top:
                        fvg.mitigated = True
                        fvg.mitigated_timestamp_ms = post_c.timestamp_ms
                        break
                elif fvg.direction == Direction.SHORT:
                    if post_c.high >= fvg.bottom:
                        fvg.mitigated = True
                        fvg.mitigated_timestamp_ms = post_c.timestamp_ms
                        break

        return fvgs
