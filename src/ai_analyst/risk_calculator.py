"""Quantitative Risk & Trade Plan Calculator for Groq AI Analyst.

Calculates:
- Entry ladder (Limit 1 primary, Limit 2 deep discount)
- Structural Stop Loss (SL) and invalidation buffer
- Structural Take-Profit targets (TP1, TP2)
- Mathematical position sizing from risk budget ($1.00 standard or custom)
- Exchange fee drag (maker 0.02%, taker 0.05%), slippage (0.05%), and funding
- Break-even price and net reward-to-risk
- AsterDEX $5.00 minimum notional and $15.00 margin allocation constraints
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from src.ai_analyst.types import (
    EntryLadderLevel,
    ExecutionPlanDetails,
    QuantitativeMarketSnapshot,
    TradeScenario,
)
from src.exchange.normalized import NormalizedInstrument


class AIRiskCalculator:
    """Computes exact quantitative execution plans and risk metrics for AI recommendations."""

    MAKER_FEE_RATE = 0.0002   # 0.02%
    TAKER_FEE_RATE = 0.0005   # 0.05%
    SLIPPAGE_RATE = 0.0005    # 0.05%
    FUNDING_RATE = 0.0001     # 0.01%
    MIN_NOTIONAL = 5.00       # $5.00 USDT AsterDEX minimum order size
    MAX_MARGIN = 15.00        # $15.00 USDT operator margin limit

    def compute_plan(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss_price: float,
        tp1_price: float,
        tp2_price: Optional[float] = None,
        risk_budget_usdt: float = 1.00,
        leverage: float = 10.0,
        instrument: Optional[NormalizedInstrument] = None,
    ) -> ExecutionPlanDetails:
        """Calculate complete trade plan, position size, and cost drag."""
        is_long = direction.upper() == "LONG"
        stop_dist = abs(entry_price - stop_loss_price)
        stop_dist_pct = (stop_dist / entry_price * 100.0) if entry_price > 0 else 0.0

        # 1. Total roundtrip cost rate
        roundtrip_cost_rate = self.MAKER_FEE_RATE + self.TAKER_FEE_RATE + self.SLIPPAGE_RATE + self.FUNDING_RATE
        effective_unit_loss = stop_dist + (entry_price * roundtrip_cost_rate)

        # 2. Position sizing from risk budget
        raw_quantity = (risk_budget_usdt / effective_unit_loss) if effective_unit_loss > 0 else 0.0

        # Step size rounding (adaptive if instrument spec is not provided)
        if instrument and instrument.step_size > 0:
            step = instrument.step_size
        else:
            if entry_price >= 10000.0:
                step = 0.00001
            elif entry_price >= 1000.0:
                step = 0.0001
            elif entry_price >= 10.0:
                step = 0.001
            else:
                step = 0.01

        step_precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
        quantity = round(math.floor(raw_quantity / step) * step, step_precision)
        if raw_quantity > 0 and quantity == 0.0:
            quantity = step
        if instrument and quantity < instrument.min_qty:
            quantity = instrument.min_qty

        notional = quantity * entry_price
        req_margin = (notional / leverage) if leverage > 0 else notional

        # 3. Cost breakdown
        maker_fee = notional * self.MAKER_FEE_RATE
        taker_fee = (quantity * stop_loss_price) * self.TAKER_FEE_RATE
        slippage = notional * self.SLIPPAGE_RATE
        funding = notional * self.FUNDING_RATE
        total_costs = maker_fee + taker_fee + slippage + funding

        # 4. Break-even price
        if is_long:
            break_even = entry_price * (1.0 + (self.MAKER_FEE_RATE + self.TAKER_FEE_RATE + self.SLIPPAGE_RATE))
        else:
            break_even = entry_price * (1.0 - (self.MAKER_FEE_RATE + self.TAKER_FEE_RATE + self.SLIPPAGE_RATE))

        # 5. Net R:R calculation
        gross_pnl_tp1 = quantity * abs(tp1_price - entry_price)
        exit_fee_tp1 = (quantity * tp1_price) * self.TAKER_FEE_RATE
        net_pnl_tp1 = gross_pnl_tp1 - maker_fee - exit_fee_tp1
        net_risk = (quantity * stop_dist) + maker_fee + taker_fee + slippage
        net_rr = round(net_pnl_tp1 / net_risk, 2) if net_risk > 0 else 0.0

        # 6. Entry ladder (60% at primary, 40% at discount limit)
        if is_long:
            limit_2_px = round(entry_price * 0.995, 6)
        else:
            limit_2_px = round(entry_price * 1.005, 6)

        entry_ladder = [
            EntryLadderLevel(
                level_name="Limit 1 (Primary Entry)",
                price=round(entry_price, 6),
                weight_pct=60.0,
                allocation_usdt=round(notional * 0.60, 2),
            ),
            EntryLadderLevel(
                level_name="Limit 2 (Discount Scaling)",
                price=limit_2_px,
                weight_pct=40.0,
                allocation_usdt=round(notional * 0.40, 2),
            ),
        ]

        is_notional_ok = notional >= self.MIN_NOTIONAL
        is_margin_ok = req_margin <= self.MAX_MARGIN

        return ExecutionPlanDetails(
            symbol=symbol,
            direction=direction.upper(),
            primary_entry=round(entry_price, 6),
            entry_ladder=entry_ladder,
            stop_loss=round(stop_loss_price, 6),
            stop_distance_pct=round(stop_dist_pct, 2),
            take_profit_1=round(tp1_price, 6),
            take_profit_2=round(tp2_price, 6) if tp2_price else None,
            break_even_price=round(break_even, 6),
            risk_budget_usdt=round(risk_budget_usdt, 2),
            suggested_quantity=quantity,
            position_notional_usdt=round(notional, 2),
            required_margin_usdt=round(req_margin, 2),
            leverage=leverage,
            estimated_maker_fee_usdt=round(maker_fee, 4),
            estimated_taker_fee_usdt=round(taker_fee, 4),
            estimated_slippage_usdt=round(slippage, 4),
            estimated_funding_usdt=round(funding, 4),
            total_estimated_costs_usdt=round(total_costs, 4),
            net_reward_to_risk=net_rr,
            is_notional_compliant=is_notional_ok,
            is_margin_compliant=is_margin_ok,
        )
