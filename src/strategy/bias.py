"""Multi-Timeframe Market Bias Engine (4H).

Computes higher-timeframe directional trend and structural alignment
strictly using 4H candles closed prior to or at the evaluation time.
Optimized with binary search and fast vectorization.
"""

import bisect
from typing import List, Optional
import numpy as np
from src.core.types import Candle, MarketBias, StructureState
from src.smc.structure import MarketStructureEngine
from src.smc.swings import detect_swings


def fast_ema(values: np.ndarray, span: int) -> float:
    """Compute exponential moving average last value."""
    if len(values) == 0:
        return 0.0
    alpha = 2.0 / (span + 1.0)
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1.0 - alpha) * ema
    return float(ema)


class MultiTimeframeBiasEngine:
    """Computes higher-timeframe directional bias (4H)."""

    @staticmethod
    def calculate_4h_bias(
        candles_4h: List[Candle],
        as_of_timestamp_ms: Optional[int] = None,
    ) -> MarketBias:
        """Evaluate 4H market bias without lookahead bias."""
        if not candles_4h:
            return MarketBias.NEUTRAL

        if as_of_timestamp_ms is not None:
            close_times = [c.close_time_ms for c in candles_4h]
            idx = bisect.bisect_right(close_times, as_of_timestamp_ms)
            start_idx = max(0, idx - 60)
            usable_candles = candles_4h[start_idx:idx]
        else:
            usable_candles = candles_4h[-60:]

        if len(usable_candles) < 20:
            return MarketBias.NEUTRAL

        closes = np.array([c.close for c in usable_candles], dtype=float)
        ema_20 = fast_ema(closes, 20)
        ema_50 = fast_ema(closes, 50)
        last_close = usable_candles[-1].close

        # Detect 4H structure swings on recent window
        swings_4h = detect_swings(usable_candles, left_bars=3, right_bars=3)
        structure_state, _ = MarketStructureEngine.evaluate_structure(usable_candles, swings_4h)

        # Synthesize bias
        bullish_score = 0
        bearish_score = 0

        if structure_state == StructureState.BULLISH:
            bullish_score += 2
        elif structure_state == StructureState.BEARISH:
            bearish_score += 2

        if last_close > ema_50:
            bullish_score += 1
        elif last_close < ema_50:
            bearish_score += 1

        if ema_20 > ema_50:
            bullish_score += 1
        elif ema_20 < ema_50:
            bearish_score += 1

        if bullish_score >= 3 and bearish_score <= 1:
            return MarketBias.BULLISH
        elif bearish_score >= 3 and bullish_score <= 1:
            return MarketBias.BEARISH
        else:
            return MarketBias.NEUTRAL
