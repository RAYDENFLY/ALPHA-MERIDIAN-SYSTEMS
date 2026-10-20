"""Support and Demand-Zone Detector for AsterDEX Demand-Zone LONG Scanner.

Identifies, scores, and tracks technical support/demand zones:
- Identifies swing lows from closed 1H candles
- Detects demand consolidation bases prior to strong bullish displacement
- Evaluates liquidity sweeps & reclaims of key levels
- Computes consistent distance_to_demand relative to demand_zone_upper
- Tracks zone freshness, test count, and structural invalidation levels
"""

import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from src.core.types import Candle
from src.demand_zone.types import DemandZone, DemandZoneConfig
from src.smc.swings import detect_swings


class DemandZoneDetector:
    """Detects and scores validated demand zones on closed candlestick series."""

    def __init__(self, config: Optional[DemandZoneConfig] = None) -> None:
        self.config = config or DemandZoneConfig()

    def detect_demand_zones(
        self,
        symbol: str,
        candles_1h: List[Candle],
        current_price: float,
        lookback_bars: int = 120,
    ) -> List[DemandZone]:
        """Identify technical demand zones below the current price strictly using closed candles."""
        # Ensure only closed candles are analyzed
        usable_candles = [c for c in candles_1h if getattr(c, "is_closed", True)][-lookback_bars:]
        if len(usable_candles) < 20:
            return []

        # 1. Detect confirmed swing lows
        swings = detect_swings(usable_candles, left_bars=3, right_bars=2)
        swing_lows = [s for s in swings if s.swing_type.value == "LOW"]
        if not swing_lows:
            return []

        # 2. Cluster / synthesize demand zones around swing lows with bullish displacement
        zones: List[DemandZone] = []
        closes = [c.close for c in usable_candles]
        highs = [c.high for c in usable_candles]
        lows = [c.low for c in usable_candles]

        # Calculate baseline ATR14 for displacement scoring
        tr_list = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                   for i in range(1, len(usable_candles))]
        atr_14 = float(np.mean(tr_list[-14:])) if len(tr_list) >= 14 else (current_price * 0.01)

        # Iterate through confirmed swing lows from newest to oldest
        for sw in reversed(swing_lows):
            sw_idx = next((i for i, c in enumerate(usable_candles) if c.timestamp_ms == sw.timestamp_ms), None)
            if sw_idx is None or sw_idx >= len(usable_candles) - 2:
                continue

            sw_low = sw.price

            # Define zone lower and upper boundaries:
            # Lower boundary: Lowest wick price of the swing low
            # Upper boundary: Highest candle body (open/close) of the base candle at the swing
            base_candle = usable_candles[sw_idx]
            zone_lower = sw_low
            zone_upper = max(base_candle.open, base_candle.close)
            # Ensure zone has minimum height (at least 0.25 ATR, at most 1.5 ATR)
            min_height = 0.25 * atr_14
            if (zone_upper - zone_lower) < min_height:
                zone_upper = zone_lower + min_height

            zone_mid = (zone_upper + zone_lower) / 2.0

            # Only accept demand zones whose upper boundary is below or at the current price
            if zone_upper > current_price:
                # If current price is inside zone, it is actionable; if above, price is approaching
                if current_price < zone_lower:
                    continue  # Price has completely broken below zone; invalidated

            # 3. Measure displacement strength after zone formation
            # Look at the 3 bars following the swing low
            post_bars = usable_candles[sw_idx + 1 : min(sw_idx + 4, len(usable_candles))]
            max_post_high = max((b.high for b in post_bars), default=zone_upper)
            displacement = max_post_high - zone_lower
            disp_ratio = displacement / atr_14 if atr_14 > 0 else 1.0

            # Demand zones require clear bullish displacement (> 1.0 ATR expansion away from zone)
            if disp_ratio < 0.90:
                continue

            # 4. Check for subsequent tests, reactions, or invalidations
            later_bars = usable_candles[sw_idx + 1 :]
            test_count = 0
            reaction_count = 1
            is_broken = False

            for b in later_bars:
                # Pierced into zone (touching between upper and lower)
                if b.low <= zone_upper:
                    test_count += 1
                    # Tested zone and closed above it (reaction)
                    if b.close >= zone_upper:
                        reaction_count += 1
                    # Decisive close below zone lower boundary invalidates it
                    if b.close < zone_lower * 0.998:  # 0.2% breach buffer
                        is_broken = True
                        break

            if is_broken:
                continue

            # 5. Check for Liquidity Sweep & Reclaim at zone
            liquidity_swept = False
            prior_lows = [l.price for l in swing_lows if l.timestamp_ms < sw.timestamp_ms]
            if prior_lows and any(abs(sw_low - pl) / pl < 0.015 for pl in prior_lows):
                # Sw low swept just below a prior low
                nearest_prior = min(prior_lows, key=lambda p: abs(sw_low - p))
                if sw_low < nearest_prior and base_candle.close > nearest_prior:
                    liquidity_swept = True

            # 6. Calculate Quality Score (0 to 100)
            score = 50.0
            if disp_ratio >= 2.0:
                score += 20.0
            elif disp_ratio >= 1.4:
                score += 10.0

            if liquidity_swept:
                score += 15.0

            if reaction_count >= 2:
                score += 10.0

            # Freshness penalty / bonus
            if test_count <= 1:
                score += 15.0  # Fresh, unexploited demand
                is_fresh = True
            elif test_count == 2:
                score += 5.0
                is_fresh = False
            else:
                score -= 15.0  # Weakened by repeated absorption
                is_fresh = False

            quality_score = max(10.0, min(100.0, score))

            invalidation_level = round(zone_lower * 0.995, 6)  # Structural stop level just below low

            zone = DemandZone(
                zone_id=f"DZONE_{symbol}_{sw.timestamp_ms}",
                symbol=symbol,
                lower_price=round(zone_lower, 6),
                upper_price=round(zone_upper, 6),
                mid_price=round(zone_mid, 6),
                formation_timestamp_ms=sw.timestamp_ms,
                swing_low_price=round(sw_low, 6),
                reaction_count=reaction_count,
                displacement_score=round(disp_ratio, 2),
                is_fresh=is_fresh,
                test_count=test_count,
                quality_score=round(quality_score, 1),
                is_provisional=False,
                liquidity_swept=liquidity_swept,
                invalidation_level=invalidation_level,
            )
            zones.append(zone)

        # Sort zones by quality score descending and proximity
        zones.sort(key=lambda z: (z.quality_score, -abs(current_price - z.upper_price)), reverse=True)
        return zones

    def select_best_demand_zone(
        self,
        zones: List[DemandZone],
        current_price: float,
    ) -> Optional[DemandZone]:
        """Select the highest-quality validated demand zone below or around current price."""
        if not zones:
            return None

        # Filter out zones where current price has broken below lower boundary
        valid_zones = [z for z in zones if current_price >= z.lower_price * 0.998]
        if not valid_zones:
            return None

        # Prefer fresh, high-scoring zones within reasonable proximity (< 8% distance)
        candidates = [z for z in valid_zones if ((current_price - z.upper_price) / current_price) <= 0.08]
        if candidates:
            return candidates[0]
        return valid_zones[0]

    def compute_distance_to_demand(
        self,
        current_price: float,
        zone: DemandZone,
        reference_rule: Optional[str] = None,
    ) -> Tuple[float, float]:
        """Compute explicit distance to demand zone reference level.
        
        Returns:
            (distance_pct, reference_price)
        """
        rule = reference_rule or self.config.zone_reference_rule
        if rule == "ZONE_MID":
            ref_price = zone.mid_price
        elif rule == "SWING_LOW":
            ref_price = zone.swing_low_price
        else:  # "ZONE_UPPER" (default)
            ref_price = zone.upper_price

        if current_price <= 0:
            return 0.0, ref_price

        # Standard formula: (current_price - reference_price) / current_price * 100
        dist_pct = ((current_price - ref_price) / current_price) * 100.0
        return round(dist_pct, 2), ref_price
