"""TP/SL Planner and Structural Invalidation Calculator.

Plans structural stop-loss and take-profit targets with ATR buffers and
net reward-to-risk calculations including trading fees and slippage.
"""

from typing import List, Optional, Tuple
from src.core.types import Direction, SnRZone, SwingPoint, SwingType


class TPSLPlanner:
    """Calculates structurally grounded Stop-Loss and Take-Profit targets."""

    @staticmethod
    def plan_trade_levels(
        direction: Direction,
        entry_price: float,
        invalidation_price: float,
        atr: float,
        swings: List[SwingPoint],
        resistance: Optional[SnRZone],
        support: Optional[SnRZone],
        atr_buffer_mult: float = 0.5,
        min_rr: float = 1.5,
        fee_rate: float = 0.0005,  # 0.05% taker
        slippage_rate: float = 0.0005,  # 0.05% slippage
        max_rr: float = 2.5,
        fallback_rr: float = 1.60,
    ) -> Tuple[Optional[float], Optional[float], Optional[float], float, float]:
        """Compute SL, TP1, TP2, Gross R:R, and Net R:R.

        Returns:
        --------
        Tuple[stop_loss, tp1, tp2, gross_rr, net_rr]
        """
        buffer = atr * atr_buffer_mult
        target_fallback_rr = max(min_rr, fallback_rr)

        if direction == Direction.LONG:
            # SL strictly below invalidation
            stop_loss = invalidation_price - buffer
            if stop_loss >= entry_price:
                return None, None, None, 0.0, 0.0

            stop_distance = entry_price - stop_loss

            # Reject if opposing resistance wall blocks path before 1.2R
            if (
                resistance is not None
                and resistance.price_low > entry_price
                and (resistance.price_low - entry_price) < (stop_distance * 1.2)
            ):
                return None, None, None, 0.0, 0.0

            # Structural TP1: Find nearest opposing swing high or resistance within [min_rr, max_rr]
            potential_tps: List[float] = []
            if resistance is not None and resistance.price_center > entry_price:
                potential_tps.append(resistance.price_low)

            for s in swings:
                if s.swing_type == SwingType.HIGH and s.price > entry_price:
                    potential_tps.append(s.price)

            valid_tps = [
                tp
                for tp in potential_tps
                if (stop_distance * min_rr) <= (tp - entry_price) <= (stop_distance * max_rr)
            ]
            if valid_tps:
                tp1 = min(valid_tps)
            else:
                tp1 = entry_price + (stop_distance * target_fallback_rr)

            tp2 = tp1 + stop_distance

            gross_profit = tp1 - entry_price
            gross_loss = stop_distance

            cost_pct = 2 * (fee_rate + slippage_rate)
            cost_amount = entry_price * cost_pct

            net_profit = gross_profit - cost_amount
            net_loss = gross_loss + cost_amount

            gross_rr = gross_profit / gross_loss if gross_loss > 0 else 0.0
            net_rr = net_profit / net_loss if net_loss > 0 else 0.0

            return stop_loss, tp1, tp2, gross_rr, net_rr

        elif direction == Direction.SHORT:
            # SL strictly above invalidation
            stop_loss = invalidation_price + buffer
            if stop_loss <= entry_price:
                return None, None, None, 0.0, 0.0

            stop_distance = stop_loss - entry_price

            # Reject if opposing support wall blocks path before 1.2R
            if (
                support is not None
                and support.price_high < entry_price
                and (entry_price - support.price_high) < (stop_distance * 1.2)
            ):
                return None, None, None, 0.0, 0.0

            # Structural TP1: Find nearest opposing swing low or support within [min_rr, max_rr]
            potential_tps = []
            if support is not None and support.price_center < entry_price:
                potential_tps.append(support.price_high)

            for s in swings:
                if s.swing_type == SwingType.LOW and s.price < entry_price:
                    potential_tps.append(s.price)

            valid_tps = [
                tp
                for tp in potential_tps
                if (stop_distance * min_rr) <= (entry_price - tp) <= (stop_distance * max_rr)
            ]
            if valid_tps:
                tp1 = max(valid_tps)
            else:
                tp1 = entry_price - (stop_distance * target_fallback_rr)

            tp2 = tp1 - stop_distance

            gross_profit = entry_price - tp1
            gross_loss = stop_distance

            cost_pct = 2 * (fee_rate + slippage_rate)
            cost_amount = entry_price * cost_pct

            net_profit = gross_profit - cost_amount
            net_loss = gross_loss + cost_amount

            gross_rr = gross_profit / gross_loss if gross_loss > 0 else 0.0
            net_rr = net_profit / net_loss if net_loss > 0 else 0.0

            return stop_loss, tp1, tp2, gross_rr, net_rr

        return None, None, None, 0.0, 0.0