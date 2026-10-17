"""Support and Resistance Zone Interaction, Strength, and Proximity Analysis.

Evaluates touches, rejections, breakouts, and distance without lookahead bias.
"""

from typing import List, Optional, Tuple
from src.core.types import Candle, SnRZone


class SnRInteractionEngine:
    """Evaluates how candles interact with support and resistance zones."""

    @staticmethod
    def evaluate_interactions(
        candles: List[Candle],
        zones: List[SnRZone],
    ) -> List[SnRZone]:
        """Update zone interaction counts and active status chronologically."""
        if not candles or not zones:
            return zones

        # We evaluate candles that occur after the zone was formed
        for z in zones:
            touch_cnt = z.touch_count
            rejection_cnt = 0
            breakout_cnt = 0

            for c in candles:
                if c.timestamp_ms <= z.last_interaction_timestamp_ms:
                    continue

                # Check if candle intersects zone
                intersects = (c.low <= z.price_high) and (c.high >= z.price_low)
                if not intersects:
                    continue

                candle_range = max(c.high - c.low, 1e-8)
                touch_cnt += 1
                z.last_interaction_timestamp_ms = c.timestamp_ms

                # Rejection detection
                # Bullish rejection at support: low tests zone, close finishes in top 40% of candle
                if c.low <= z.price_center and c.close > z.price_center:
                    lower_wick = min(c.open, c.close) - c.low
                    if (lower_wick / candle_range) >= 0.40:
                        rejection_cnt += 1

                # Bearish rejection at resistance: high tests zone, close finishes in bottom 40% of candle
                elif c.high >= z.price_center and c.close < z.price_center:
                    upper_wick = c.high - max(c.open, c.close)
                    if (upper_wick / candle_range) >= 0.40:
                        rejection_cnt += 1

                # Breakout detection: close fully outside zone
                if c.close > z.price_high or c.close < z.price_low:
                    breakout_cnt += 1

            z.touch_count = touch_cnt
            z.rejection_count = rejection_cnt
            z.breakout_count = breakout_cnt
            # Strength formula: touches + 1.5 * rejections - 0.5 * breakouts
            z.strength = max(0.5, touch_cnt + 1.5 * rejection_cnt - 0.5 * breakout_cnt)

        return zones

    @staticmethod
    def get_nearest_levels(
        current_price: float,
        zones: List[SnRZone],
    ) -> Tuple[Optional[SnRZone], Optional[SnRZone]]:
        """Find the closest active support level below and resistance level above current price."""
        supports = [z for z in zones if z.price_high < current_price and z.is_active]
        resistances = [z for z in zones if z.price_low > current_price and z.is_active]

        nearest_support = max(supports, key=lambda z: z.price_center) if supports else None
        nearest_resistance = min(resistances, key=lambda z: z.price_center) if resistances else None

        return nearest_support, nearest_resistance
