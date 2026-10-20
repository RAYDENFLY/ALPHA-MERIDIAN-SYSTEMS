"""BTC Market Context Evaluator for AsterDEX Demand-Zone LONG Scanner.

Evaluates current BTC price action relative to the operator-configured demand reference zone:
- Default Lower: $79,864
- Default Upper: $80,400

Determines:
- Zone relation: ABOVE_ZONE, INSIDE_ZONE, BELOW_ZONE
- Approach status and proximity
- Sweep / Reclaim detection
- Bearish impulse and volatility warning
- Policy enforcement: Policy A (Observe), Policy B (Prioritize), Policy C (Require Confirmation)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from src.core.logger import logger
from src.core.types import Candle
from src.demand_zone.types import (
    BTCContextObservation,
    BTCZonePolicy,
    BTCZoneStatus,
    DemandZoneConfig,
)


class BTCContextEvaluator:
    """Evaluates BTC market state and applies configurable governance policies."""

    def __init__(self, config: Optional[DemandZoneConfig] = None) -> None:
        self.config = config or DemandZoneConfig()
        self.history: List[Dict[str, Any]] = []

    def evaluate(
        self,
        current_btc_price: float,
        candles_1h: Optional[List[Candle]] = None,
        now_ms: Optional[int] = None,
        policy_override: Optional[BTCZonePolicy] = None,
    ) -> BTCContextObservation:
        """Evaluate BTC market status against configured reference zone."""
        policy = policy_override or self.config.btc_policy
        zone_lower = self.config.btc_zone_lower
        zone_upper = self.config.btc_zone_upper
        c_time = now_ms or (candles_1h[-1].timestamp_ms if candles_1h else int(datetime.now(timezone.utc).timestamp() * 1000))

        # 1. Determine zone status
        if current_btc_price > zone_upper:
            zone_status = BTCZoneStatus.ABOVE_ZONE
        elif current_btc_price < zone_lower:
            zone_status = BTCZoneStatus.BELOW_ZONE
        else:
            zone_status = BTCZoneStatus.INSIDE_ZONE

        # 2. Distance calculations
        dist_to_upper_pct = round(((current_btc_price - zone_upper) / current_btc_price) * 100.0, 2)
        dist_to_lower_pct = round(((current_btc_price - zone_lower) / current_btc_price) * 100.0, 2)

        # 3. Approach & Momentum checks
        is_approaching = False
        sweep_reclaim = False
        bearish_impulse = False
        volatility_alert = False

        # Approaching: Price is above zone and within 3% proximity
        if zone_status == BTCZoneStatus.ABOVE_ZONE and 0.0 < dist_to_upper_pct <= 3.0:
            if candles_1h and len(candles_1h) >= 3:
                recent_closes = [c.close for c in candles_1h[-3:]]
                is_approaching = (recent_closes[-1] <= recent_closes[0])
            else:
                is_approaching = True

        if candles_1h and len(candles_1h) >= 3:
            recent_closes = [c.close for c in candles_1h[-3:]]
            recent_lows = [c.low for c in candles_1h[-5:]]
            recent_highs = [c.high for c in candles_1h[-5:]]

            # Sweep-Reclaim: Low pierced below zone_lower, but current price or latest close > zone_lower
            pierced_below = any(l < zone_lower for l in recent_lows)
            reclaimed_above = current_btc_price >= zone_lower
            if pierced_below and reclaimed_above:
                sweep_reclaim = True

            # Bearish Impulse: consecutive heavy red bars with high velocity
            ret_3h = (recent_closes[-1] - candles_1h[-3].open) / candles_1h[-3].open
            if ret_3h <= -0.025:  # >= 2.5% drop in 3 hours
                bearish_impulse = True

            # Volatility Alert: Range expansion on latest bar > 2.0x 14-bar ATR
            if len(candles_1h) >= 15:
                trs = [max(c.high - c.low, abs(c.high - candles_1h[i-1].close), abs(c.low - candles_1h[i-1].close))
                       for i, c in enumerate(candles_1h[-14:], start=len(candles_1h)-14)]
                avg_tr = float(np.mean(trs))
                latest_tr = candles_1h[-1].high - candles_1h[-1].low
                if avg_tr > 0 and latest_tr >= (2.0 * avg_tr):
                    volatility_alert = True

        # 4. Policy Decision Logic
        allows_ready = True
        priority_boost = 0.0
        explanation_parts = []

        if policy == BTCZonePolicy.OBSERVE_CONTINUOUSLY:
            allows_ready = True
            explanation_parts.append("Policy A (Continuous): Altcoin setups evaluated regardless of BTC position.")

        elif policy == BTCZonePolicy.PRIORITIZE_ON_APPROACH:
            allows_ready = True
            if zone_status == BTCZoneStatus.INSIDE_ZONE:
                priority_boost = 15.0
                explanation_parts.append("Policy B (Prioritize): BTC inside reference zone ($79,864 - $80,400). Priority boosted.")
            elif is_approaching:
                priority_boost = 10.0
                explanation_parts.append("Policy B (Prioritize): BTC approaching reference zone. Priority boosted.")
            elif sweep_reclaim:
                priority_boost = 20.0
                explanation_parts.append("Policy B (Prioritize): BTC confirmed sweep & reclaim of zone lower boundary.")
            else:
                explanation_parts.append(f"Policy B (Prioritize): BTC {zone_status.value} reference zone. Baseline priority.")

        elif policy == BTCZonePolicy.REQUIRE_BTC_CONFIRMATION:
            if zone_status == BTCZoneStatus.INSIDE_ZONE or sweep_reclaim:
                allows_ready = True
                explanation_parts.append("Policy C (Confirmation Required): BTC tested or reclaimed reference zone. Setups eligible for READY.")
            else:
                allows_ready = False
                explanation_parts.append(
                    f"Policy C (Confirmation Required): BTC is {zone_status.value} ($79,864 - $80,400). Setups held in WATCHLIST pending BTC test."
                )

        if bearish_impulse:
            explanation_parts.append("Warning: Broad market elevated bearish momentum detected on BTC.")
            if policy == BTCZonePolicy.REQUIRE_BTC_CONFIRMATION:
                allows_ready = False

        status_explanation = " | ".join(explanation_parts)

        obs = BTCContextObservation(
            current_price=round(current_btc_price, 2),
            zone_lower=zone_lower,
            zone_upper=zone_upper,
            zone_status=zone_status,
            distance_to_upper_pct=dist_to_upper_pct,
            distance_to_lower_pct=dist_to_lower_pct,
            is_approaching=is_approaching,
            sweep_reclaim_detected=sweep_reclaim,
            bearish_impulse_detected=bearish_impulse,
            volatility_alert=volatility_alert,
            active_policy=policy,
            allows_ready_status=allows_ready,
            priority_boost=priority_boost,
            status_explanation=status_explanation,
            timestamp_ms=c_time,
            observed_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        self.history.append(obs.to_dict())
        if len(self.history) > 50:
            self.history.pop(0)
        return obs
