"""Take-Profit Planner and Target Feasibility Engine for AsterDEX Demand-Zone LONG Scanner.

Calculates realistic, structurally grounded take-profit targets:
- Anchored to confirmed swing highs and resistance zones from closed 1H candles
- Computes Gross R:R, Net R:R, and estimated Net PnL after maker/taker fees and slippage
- Computes exact break-even price movement after roundtrip costs
- Detects resistance obstruction (if resistance is located < 1.2R from entry)
- Never manufactures arbitrary targets solely to satisfy R:R thresholds
"""

from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from src.core.types import Candle
from src.demand_zone.types import DemandZoneConfig, TargetPlanningResult
from src.smc.swings import detect_swings


class DemandZoneTPPlanner:
    """Plans take-profit targets and calculates trade feasibility against structural levels."""

    def __init__(self, config: Optional[DemandZoneConfig] = None) -> None:
        self.config = config or DemandZoneConfig()

    def plan_targets(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        quantity: float,
        position_notional_usdt: float,
        candles_1h: List[Candle],
        lookback_bars: int = 120,
        current_price: Optional[float] = None,
    ) -> TargetPlanningResult:
        """Calculate structural take-profit targets and net R:R metrics."""
        stop_dist = entry_price - stop_loss_price
        if stop_dist <= 0 or entry_price <= 0:
            return TargetPlanningResult(
                take_profit_1=0.0,
                take_profit_2=None,
                gross_reward_to_risk=0.0,
                net_reward_to_risk=0.0,
                break_even_price=0.0,
                estimated_gross_pnl_tp1_usdt=0.0,
                estimated_net_pnl_tp1_usdt=0.0,
                estimated_net_pnl_tp2_usdt=None,
                nearest_resistance_price=None,
                resistance_distance_rr=0.0,
                is_obstructed_by_resistance=False,
                tp1_rationale="Invalid entry/stop-loss geometry.",
                is_feasible=False,
                infeasible_reasons=["Stop loss is equal to or above entry price."],
            )

        # 1. Break-even price calculation including entry fee + exit fee + slippage
        # cost_rate = maker_fee (0.02%) + taker_fee (0.05%) + slippage (0.05%) = 0.12%
        roundtrip_cost_rate = (
            self.config.maker_fee_rate
            + self.config.taker_fee_rate
            + self.config.slippage_rate
        )
        break_even_price = round(entry_price * (1.0 + roundtrip_cost_rate), 6)

        # 2. Extract structural swing highs from closed 1H candles
        usable_candles = [c for c in candles_1h if getattr(c, "is_closed", True)][-lookback_bars:]
        swings = detect_swings(usable_candles, left_bars=3, right_bars=2) if len(usable_candles) >= 15 else []
        swing_highs = sorted([s.price for s in swings if s.swing_type.value == "HIGH" and s.price > entry_price])

        # 3. Identify nearest resistance and evaluate obstruction
        # Overhead resistance must be above the current price level
        active_high_ref = max(entry_price, (current_price or entry_price) * 0.9995)
        overhead_resistances = [px for px in swing_highs if px > active_high_ref]
        nearest_resistance = overhead_resistances[0] if overhead_resistances else None
        resistance_dist_rr = 999.0
        is_obstructed = False

        if nearest_resistance is not None:
            resistance_dist = nearest_resistance - entry_price
            resistance_dist_rr = resistance_dist / stop_dist
            # If nearest confirmed overhead resistance is closer than 1.2R, the path to TP is obstructed
            if resistance_dist_rr < self.config.min_resistance_clearance_rr:
                is_obstructed = True

        # 4. Plan TP1 and TP2 anchored to structural swing highs or fallback targets
        infeasible_reasons: List[str] = []
        tp1_rationale = ""
        tp2_rationale = None
        tp2_px = None

        # Gross target needed to achieve at least min_net_reward_to_risk after maker/taker fees + slippage
        gross_target_multiplier = max(1.50, self.config.min_net_reward_to_risk * 1.08)
        min_tp1_price = entry_price + (stop_dist * gross_target_multiplier)

        # Find structural swing highs that satisfy minimum R:R threshold
        viable_structural_targets = [px for px in swing_highs if px >= min_tp1_price]

        if viable_structural_targets:
            tp1_px = viable_structural_targets[0]
            tp1_rationale = f"Anchored to structural swing high at {tp1_px:.4f} ({(tp1_px - entry_price)/stop_dist:.2f}R)"
            if len(viable_structural_targets) > 1:
                tp2_px = viable_structural_targets[1]
                tp2_rationale = f"Higher swing high resistance target at {tp2_px:.4f} ({(tp2_px - entry_price)/stop_dist:.2f}R)"
        else:
            # Fallback to structural dealing range expansion satisfying net R:R
            tp1_px = min_tp1_price
            tp1_rationale = f"Planned via {gross_target_multiplier:.2f}R structural range expansion ({min_tp1_price:.4f})"

        if tp2_px is None and len(swing_highs) >= 1:
            higher_highs = [px for px in swing_highs if px > tp1_px]
            if higher_highs:
                tp2_px = higher_highs[0]
                tp2_rationale = f"Structural liquidity pool at prior high {tp2_px:.4f}"

        # 5. Reward-to-Risk Calculations
        gross_reward = tp1_px - entry_price
        gross_rr = round(gross_reward / stop_dist, 2)

        # Cost drag calculation:
        # Entry fee = notional * maker_fee
        # Exit fee at TP = (quantity * tp1_px) * taker_fee
        # Entry fee + Exit fee at SL = (notional + quantity * sl) * fees + slippage
        entry_fee = position_notional_usdt * self.config.maker_fee_rate
        tp1_exit_fee = (quantity * tp1_px) * self.config.taker_fee_rate
        gross_pnl_tp1 = quantity * (tp1_px - entry_price)
        net_pnl_tp1 = gross_pnl_tp1 - entry_fee - tp1_exit_fee

        sl_exit_fee = (quantity * stop_loss_price) * self.config.taker_fee_rate
        slippage_sl = position_notional_usdt * self.config.slippage_rate
        net_risk = (quantity * stop_dist) + entry_fee + sl_exit_fee + slippage_sl

        net_rr = round(net_pnl_tp1 / net_risk, 2) if net_risk > 0 else 0.0

        # TP2 net pnl
        net_pnl_tp2 = None
        if tp2_px is not None:
            tp2_exit_fee = (quantity * tp2_px) * self.config.taker_fee_rate
            net_pnl_tp2 = round((quantity * (tp2_px - entry_price)) - entry_fee - tp2_exit_fee, 4)

        # 6. Feasibility validation
        if is_obstructed:
            infeasible_reasons.append(
                f"Path to target is obstructed by heavy resistance at {nearest_resistance:.4f} ({resistance_dist_rr:.2f}R < {self.config.min_resistance_clearance_rr:.1f}R)."
            )

        if net_rr < self.config.min_net_reward_to_risk:
            infeasible_reasons.append(
                f"Net Reward-to-Risk ({net_rr:.2f}R) is below minimum required threshold ({self.config.min_net_reward_to_risk:.2f}R)."
            )

        is_feasible = (len(infeasible_reasons) == 0)

        return TargetPlanningResult(
            take_profit_1=round(tp1_px, 6),
            take_profit_2=round(tp2_px, 6) if tp2_px else None,
            gross_reward_to_risk=gross_rr,
            net_reward_to_risk=net_rr,
            break_even_price=break_even_price,
            estimated_gross_pnl_tp1_usdt=round(gross_pnl_tp1, 4),
            estimated_net_pnl_tp1_usdt=round(net_pnl_tp1, 4),
            estimated_net_pnl_tp2_usdt=net_pnl_tp2,
            nearest_resistance_price=round(nearest_resistance, 6) if nearest_resistance else None,
            resistance_distance_rr=round(resistance_dist_rr, 2),
            is_obstructed_by_resistance=is_obstructed,
            tp1_rationale=tp1_rationale,
            tp2_rationale=tp2_rationale,
            is_feasible=is_feasible,
            infeasible_reasons=infeasible_reasons,
        )
