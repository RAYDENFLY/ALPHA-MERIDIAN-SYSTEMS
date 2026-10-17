"""Liquidity Sweep Detection.

Detects stop hunts / liquidity grabs where candle wick pierces a confirmed swing
level but closes back within the range.
"""

from typing import List, Optional
from src.core.types import Candle, Direction, SMCEvent, SMCEventType, SwingPoint, SwingType


class LiquiditySweepDetector:
    """Detects confirmed liquidity sweeps against historical confirmed swings."""

    @staticmethod
    def detect_sweeps(
        candles: List[Candle],
        swings: List[SwingPoint],
        max_lookback_bars: int = 50,
    ) -> List[SMCEvent]:
        """Detect liquidity sweep events.

        Parameters:
        -----------
        candles : List[Candle]
            Chronologically ordered candles.
        swings : List[SwingPoint]
            Confirmed swing points.
        max_lookback_bars : int
            Maximum number of candles between swing confirmation and sweep event.

        Returns:
        --------
        List[SMCEvent]
            Detected liquidity sweep events.
        """
        sweeps: List[SMCEvent] = []
        if len(candles) < 2 or not swings:
            return sweeps

        # Build chronological index of confirmed swings
        confirmed_swings_by_idx = {s.confirmed_index: s for s in swings}
        active_highs: List[SwingPoint] = []
        active_lows: List[SwingPoint] = []

        for i, c in enumerate(candles):
            # Register newly confirmed swings
            if i in confirmed_swings_by_idx:
                s = confirmed_swings_by_idx[i]
                if s.swing_type == SwingType.HIGH:
                    active_highs.append(s)
                elif s.swing_type == SwingType.LOW:
                    active_lows.append(s)

            # Filter old swings past max lookback
            active_highs = [s for s in active_highs if (i - s.index) <= max_lookback_bars and not s.broken]
            active_lows = [s for s in active_lows if (i - s.index) <= max_lookback_bars and not s.broken]

            # 1. Check Bearish Sweep (Sweep of buy-side liquidity / swing high)
            # Candle wick reaches above swing high, but candle close is below swing high
            for s_high in active_highs:
                if c.high > s_high.price and c.close < s_high.price and c.timestamp_ms > s_high.confirmed_timestamp_ms:
                    sweeps.append(
                        SMCEvent(
                            event_type=SMCEventType.LIQUIDITY_SWEEP,
                            direction=Direction.SHORT,
                            timestamp_ms=c.timestamp_ms,
                            price_level=s_high.price,
                            zone_high=c.high,
                            zone_low=s_high.price,
                            reference_timestamp_ms=s_high.timestamp_ms,
                            description=(
                                f"Bearish Liquidity Sweep: Wick ({c.high:.4f}) pierced swing high "
                                f"({s_high.price:.4f}) but closed back inside ({c.close:.4f})"
                            ),
                        )
                    )

            # 2. Check Bullish Sweep (Sweep of sell-side liquidity / swing low)
            # Candle wick reaches below swing low, but candle close is above swing low
            for s_low in active_lows:
                if c.low < s_low.price and c.close > s_low.price and c.timestamp_ms > s_low.confirmed_timestamp_ms:
                    sweeps.append(
                        SMCEvent(
                            event_type=SMCEventType.LIQUIDITY_SWEEP,
                            direction=Direction.LONG,
                            timestamp_ms=c.timestamp_ms,
                            price_level=s_low.price,
                            zone_high=s_low.price,
                            zone_low=c.low,
                            reference_timestamp_ms=s_low.timestamp_ms,
                            description=(
                                f"Bullish Liquidity Sweep: Wick ({c.low:.4f}) pierced swing low "
                                f"({s_low.price:.4f}) but closed back inside ({c.close:.4f})"
                            ),
                        )
                    )

        return sweeps
