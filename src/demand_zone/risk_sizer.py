"""Risk Budgeting and Position Sizing Engine for AsterDEX Demand-Zone LONG Scanner.

Calculates hypothetical limit entry, stop loss, and position sizing against
the operator's $0.30 USDT risk budget:
- Strict position sizing: quantity = risk_budget / (stop_distance + estimated_costs)
- 5x - 10x isolated leverage support for margin estimation only
- Exchange minimum notional ($5.00), step size, and precision constraints
- Liquidation distance buffer verification
- Infeasibility detection without arbitrary stop compression
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from src.exchange.normalized import NormalizedInstrument
from src.demand_zone.types import DemandZone, DemandZoneConfig, RiskSizingResult


class DemandZoneRiskSizer:
    """Calculates position sizing and risk feasibility for demand zone setups."""

    def __init__(self, config: Optional[DemandZoneConfig] = None) -> None:
        self.config = config or DemandZoneConfig()

    def calculate_sizing_and_risk(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        instrument: NormalizedInstrument,
        risk_budget_override: Optional[float] = None,
        leverage_override: Optional[float] = None,
    ) -> RiskSizingResult:
        """Calculate exact position size, margin, fee drag, and feasibility."""
        budget = risk_budget_override or self.config.risk_budget_usdt
        lev = leverage_override or self.config.leverage
        # Clamp leverage strictly between 5.0x and 10.0x for margin estimation
        leverage = min(max(5.0, float(lev)), 10.0)

        infeasible_reasons: List[str] = []

        # 1. Structural stop distance check
        stop_dist = entry_price - stop_loss_price
        if stop_dist <= 0 or entry_price <= 0:
            return RiskSizingResult(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                stop_distance_usdt=0.0,
                stop_distance_pct=0.0,
                risk_budget_usdt=budget,
                quantity=0.0,
                position_notional_usdt=0.0,
                required_margin_usdt=0.0,
                estimated_leverage=leverage,
                estimated_entry_fee_usdt=0.0,
                estimated_exit_fee_usdt=0.0,
                estimated_slippage_usdt=0.0,
                estimated_funding_usdt=0.0,
                total_estimated_loss_usdt=0.0,
                liquidation_price_est=0.0,
                liquidation_distance_pct=0.0,
                is_feasible=False,
                infeasible_reasons=["Invalid stop loss price: Stop loss must be strictly below entry price."],
            )

        stop_dist_pct = (stop_dist / entry_price) * 100.0

        # Preferred stop distance check (2.5% to 4.0%, hard ceiling at hard_max_stop_loss_pct)
        if stop_dist_pct < (self.config.min_stop_loss_dist_pct - 1e-4):
            infeasible_reasons.append(
                f"Stop loss distance ({stop_dist_pct:.2f}%) is below minimum structural buffer ({self.config.min_stop_loss_dist_pct:.1f}%)."
            )
        elif stop_dist_pct > self.config.hard_max_stop_loss_pct:
            # Do NOT force stop arbitrarily into the preferred range; mark as INFEASIBLE
            infeasible_reasons.append(
                f"Structural invalidation distance ({stop_dist_pct:.2f}%) exceeds hard risk cap ({self.config.hard_max_stop_loss_pct:.1f}%)."
            )

        # 2. Estimate per-unit cost rates: Maker entry + Taker SL exit + Slippage + Funding
        cost_rate = (
            self.config.maker_fee_rate
            + self.config.taker_fee_rate
            + self.config.slippage_rate
            + self.config.funding_rate_est
        )  # ~0.0013 (13 bps)

        effective_unit_loss = stop_dist + (entry_price * cost_rate)

        # 3. Unconstrained sizing
        raw_quantity = budget / effective_unit_loss if effective_unit_loss > 0 else 0.0

        # 4. Exchange step size and precision rounding
        step = instrument.step_size or 0.001
        step_precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
        quantity = round(math.floor(raw_quantity / step) * step, step_precision)

        if quantity < instrument.min_qty:
            quantity = instrument.min_qty

        notional = quantity * entry_price
        required_margin = notional / leverage if leverage > 0 else notional

        # 5. Feasibility Checks:
        # A) Minimum Notional Check ($5.00 AsterDEX minimum)
        min_notional = max(instrument.min_notional, self.config.min_notional_usdt)
        if notional < min_notional:
            infeasible_reasons.append(
                f"Position notional ${notional:.2f} below AsterDEX minimum requirement ${min_notional:.2f} for $0.30 budget."
            )

        # B) Maximum Margin Cap Check
        if required_margin > self.config.max_margin_usdt:
            infeasible_reasons.append(
                f"Required margin ${required_margin:.2f} exceeds operator limit ${self.config.max_margin_usdt:.2f}."
            )

        # 6. Detailed Cost Calculations
        entry_fee = notional * self.config.maker_fee_rate
        exit_fee = (quantity * stop_loss_price) * self.config.taker_fee_rate
        slippage = notional * self.config.slippage_rate
        funding = notional * self.config.funding_rate_est
        price_loss = quantity * stop_dist
        total_loss = price_loss + entry_fee + exit_fee + slippage + funding

        # If rounded quantity forces total loss to exceed 1.35x target budget, flag infeasible
        max_budget_tolerance = budget * 1.35
        if total_loss > max_budget_tolerance:
            infeasible_reasons.append(
                f"Estimated loss ${total_loss:.2f} exceeds risk budget tolerance ${max_budget_tolerance:.2f} due to contract minimum lot size."
            )

        # 7. Liquidation Distance Estimation
        # Isolated maintenance margin rate approx 0.5% (0.005)
        mmr = 0.005
        # For LONG: Liq price approx entry * (1 - 1/leverage + mmr)
        liq_price_est = entry_price * (1.0 - (1.0 / leverage) + mmr)
        liq_dist_pct = ((entry_price - liq_price_est) / entry_price) * 100.0

        # Liquidation must be safely below the stop loss
        if liq_price_est >= stop_loss_price:
            infeasible_reasons.append(
                f"Estimated liquidation price (${liq_price_est:.4f}) is above or too close to stop loss (${stop_loss_price:.4f})."
            )

        is_feasible = (len(infeasible_reasons) == 0)

        return RiskSizingResult(
            symbol=symbol,
            entry_price=round(entry_price, 6),
            stop_loss_price=round(stop_loss_price, 6),
            stop_distance_usdt=round(stop_dist, 6),
            stop_distance_pct=round(stop_dist_pct, 2),
            risk_budget_usdt=round(budget, 2),
            quantity=quantity,
            position_notional_usdt=round(notional, 4),
            required_margin_usdt=round(required_margin, 4),
            estimated_leverage=leverage,
            estimated_entry_fee_usdt=round(entry_fee, 4),
            estimated_exit_fee_usdt=round(exit_fee, 4),
            estimated_slippage_usdt=round(slippage, 4),
            estimated_funding_usdt=round(funding, 4),
            total_estimated_loss_usdt=round(total_loss, 4),
            liquidation_price_est=round(liq_price_est, 6),
            liquidation_distance_pct=round(liq_dist_pct, 2),
            is_feasible=is_feasible,
            infeasible_reasons=infeasible_reasons,
        )
