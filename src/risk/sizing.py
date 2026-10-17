"""Position Sizing and Order Feasibility Engine for $16 USDT Account.

Calculates exact lot size based on target isolated margin ($5.0-$5.5 USDT)
with max 10x leverage, exchange lot step size, and minimum notional constraints.
Evaluates estimated loss at SL (including entry/exit fees and slippage) against hard risk limits.
Enforces hard maximum isolated margin cap ($5.50) to prevent silent over-allocation.
"""

from typing import Optional
from src.core.types import Direction, RiskAssessment
from src.exchange.metadata import SymbolSpecs


class PositionSizer:
    """Calculates risk-controlled position size and validates order feasibility."""

    @staticmethod
    def calculate_sizing(
        symbol_specs: SymbolSpecs,
        entry_price: float,
        stop_loss: float,
        target_isolated_margin: float = 6.0,
        available_equity_usdt: float = 16.0,
        max_leverage: int = 10,
        hard_risk_limit_usdt: Optional[float] = 1.50,
        fee_rate: float = 0.0005,
        slippage_rate: float = 0.0005,
        risk_budget_usdt: Optional[float] = None,
        target_isolated_margin_max: Optional[float] = 6.5,
        direction: Optional[Direction] = None,
        enforce_risk_budget: bool = False,
        allow_risk_step_down: bool = True,
    ) -> RiskAssessment:
        """Calculate and validate position sizing for a candidate trade.

        Parameters:
        -----------
        symbol_specs : SymbolSpecs
            Exchange lot size, tick size, and minimum notional rules.
        entry_price : float
            Execution entry price.
        stop_loss : float
            Structural stop loss price (never altered for sizing).
        target_isolated_margin : float
            Target isolated margin in USDT (default $6.00).
        available_equity_usdt : float
            Current available equity in USDT (e.g. $16.0).
        max_leverage : int
            Exchange isolated leverage cap (max 10).
        hard_risk_limit_usdt : Optional[float]
            Hard risk limit for loss at SL in USDT (default $1.50).
        fee_rate : float
            Taker fee rate per side (default 0.05% = 0.0005).
        slippage_rate : float
            Estimated slippage per side (default 0.05% = 0.0005).
        risk_budget_usdt : Optional[float]
            Effective account trade risk budget in USDT (consulted for tracking and enforced when enforce_risk_budget=True).
        target_isolated_margin_max : Optional[float]
            Upper bound of target margin range (default $6.50).
        direction : Optional[Direction]
            Trade direction (LONG or SHORT).
        enforce_risk_budget : bool
            When True, also enforces estimated_loss <= risk_budget_usdt in addition to hard_risk_limit_usdt.
        allow_risk_step_down : bool
            When True, steps down quantity from target isolated margin if initial quantity exceeds the active risk cap,
            provided the reduced quantity still satisfies min_qty and min_notional.
        """
        symbol = symbol_specs.symbol
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0 or entry_price <= 0:
            return RiskAssessment(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance_pct=0.0,
                risk_budget_usdt=risk_budget_usdt or 0.0,
                position_notional_usdt=0.0,
                quantity=0.0,
                required_margin_usdt=0.0,
                effective_leverage=0.0,
                position_leverage=float(min(max(1, int(max_leverage)), 10)),
                equity_exposure_ratio=0.0,
                estimated_fee_usdt=0.0,
                estimated_slippage_usdt=0.0,
                is_feasible=False,
                infeasible_reason="Invalid stop distance (<= 0).",
                target_isolated_margin_usdt=target_isolated_margin,
                estimated_loss_usdt=0.0,
                estimated_loss_pct=0.0,
            )

        stop_distance_pct = stop_distance / entry_price

        # Enforce maximum leverage cap of 10x
        leverage = min(max(1, int(max_leverage)), 10)

        # 1. Target Notional derived from target isolated margin and leverage
        target_notional = target_isolated_margin * leverage
        raw_quantity = target_notional / entry_price

        # 2. Round down to exchange lot step size
        quantity = symbol_specs.round_quantity(raw_quantity)

        # 3. If target margin range is provided (e.g. 6.0 to 6.5), check if stepping up stays within upper bound
        effective_max_margin = (
            target_isolated_margin_max
            if target_isolated_margin_max is not None
            else (target_isolated_margin + 0.50)
        )
        if effective_max_margin > target_isolated_margin:
            current_margin = (quantity * entry_price) / leverage
            if current_margin < target_isolated_margin:
                candidate_qty = symbol_specs.round_quantity(quantity + symbol_specs.step_size)
                candidate_margin = (candidate_qty * entry_price) / leverage
                if candidate_margin <= effective_max_margin:
                    quantity = candidate_qty

        # 4. If rounded quantity is below minimum lot size, clamp to minimum lot size
        if quantity < symbol_specs.min_qty:
            quantity = symbol_specs.min_qty

        def _compute_metrics_for_qty(q: float):
            notional = q * entry_price
            margin = notional / leverage
            pos_lev = round(notional / margin, 2) if margin > 0 else float(leverage)
            eq_ratio = round(notional / available_equity_usdt, 4) if available_equity_usdt > 0 else 0.0
            ent_notional = notional
            ext_notional = q * stop_loss
            est_fee = (ent_notional * fee_rate) + (ext_notional * fee_rate)
            est_slip = (ent_notional * slippage_rate) + (ext_notional * slippage_rate)
            tx_cost = est_fee + est_slip
            nom_loss = q * stop_distance
            est_loss = nom_loss + tx_cost
            loss_pct = (est_loss / available_equity_usdt * 100.0) if available_equity_usdt > 0 else 0.0
            return notional, margin, pos_lev, eq_ratio, est_fee, est_slip, tx_cost, est_loss, loss_pct

        (
            actual_notional,
            actual_margin,
            position_leverage,
            equity_exposure_ratio,
            estimated_fee,
            estimated_slippage,
            total_transaction_cost,
            estimated_loss,
            loss_pct_equity,
        ) = _compute_metrics_for_qty(quantity)
        effective_leverage = equity_exposure_ratio

        # Consult effective risk budget: reject immediately if remaining account risk budget is exhausted (<= $0.01)
        if risk_budget_usdt is not None and risk_budget_usdt <= 0.01:
            return RiskAssessment(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance_pct=stop_distance_pct,
                risk_budget_usdt=risk_budget_usdt,
                position_notional_usdt=actual_notional,
                quantity=quantity,
                required_margin_usdt=actual_margin,
                effective_leverage=effective_leverage,
                position_leverage=position_leverage,
                equity_exposure_ratio=equity_exposure_ratio,
                estimated_fee_usdt=estimated_fee,
                estimated_slippage_usdt=estimated_slippage,
                is_feasible=False,
                infeasible_reason=(
                    f"Effective risk budget exhausted (${risk_budget_usdt:.2f} remaining). Trade is INFEASIBLE."
                ),
                target_isolated_margin_usdt=target_isolated_margin,
                estimated_loss_usdt=estimated_loss,
                estimated_loss_pct=loss_pct_equity,
            )

        # Determine active per-trade loss cap ($1.50 hard SL cap, or strict risk_budget_usdt if enforce_risk_budget=True)
        active_loss_cap: Optional[float] = hard_risk_limit_usdt
        if enforce_risk_budget and risk_budget_usdt is not None and risk_budget_usdt > 0:
            active_loss_cap = min(active_loss_cap, risk_budget_usdt) if active_loss_cap is not None else risk_budget_usdt

        # 5b. Legitimate risk-based quantity step-down when initial $6.00-margin lot slightly exceeds the active risk cap
        # (e.g. $60 notional at ~2.4% stop distance produces ~$1.56 loss > $1.50 cap; stepping down to ~$57 notional keeps loss <= $1.50)
        # Note: Only reduce if the reduced quantity still satisfies min_qty, min_notional, and preserves at least $4.50 isolated margin
        # (unless enforce_risk_budget is explicitly requested for micro-sizing).
        if allow_risk_step_down and active_loss_cap is not None and estimated_loss > active_loss_cap:
            per_unit_loss = stop_distance + (entry_price * (fee_rate + slippage_rate)) + (stop_loss * (fee_rate + slippage_rate))
            if per_unit_loss > 0:
                max_safe_raw_qty = active_loss_cap / per_unit_loss
                reduced_qty = symbol_specs.round_quantity(max_safe_raw_qty)
                # Ensure float precision does not leave estimated_loss slightly above active_loss_cap
                while reduced_qty >= symbol_specs.min_qty:
                    _, _, _, _, _, _, _, cand_loss, _ = _compute_metrics_for_qty(reduced_qty)
                    if cand_loss <= active_loss_cap + 1e-9 or reduced_qty <= symbol_specs.min_qty:
                        break
                    next_q = symbol_specs.round_quantity(reduced_qty - symbol_specs.step_size)
                    if next_q >= reduced_qty or next_q < symbol_specs.min_qty:
                        break
                    reduced_qty = next_q

                min_acceptable_margin = 0.50 if enforce_risk_budget else min(4.50, target_isolated_margin * 0.75)
                if reduced_qty >= symbol_specs.min_qty:
                    (
                        r_notional,
                        r_margin,
                        r_pos_lev,
                        r_eq_ratio,
                        r_fee,
                        r_slip,
                        r_tx_cost,
                        r_loss,
                        r_loss_pct,
                    ) = _compute_metrics_for_qty(reduced_qty)
                    if (
                        r_notional >= symbol_specs.min_notional
                        and r_margin >= min_acceptable_margin
                        and r_margin <= effective_max_margin
                        and r_loss <= active_loss_cap + 1e-9
                    ):
                        quantity = reduced_qty
                        actual_notional = r_notional
                        actual_margin = r_margin
                        position_leverage = r_pos_lev
                        equity_exposure_ratio = r_eq_ratio
                        effective_leverage = r_eq_ratio
                        estimated_fee = r_fee
                        estimated_slippage = r_slip
                        total_transaction_cost = r_tx_cost
                        estimated_loss = r_loss
                        loss_pct_equity = r_loss_pct

        # Feasibility validation checks:

        # Check A: Target margin exceeds available capital
        if target_isolated_margin > available_equity_usdt:
            return RiskAssessment(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance_pct=stop_distance_pct,
                risk_budget_usdt=risk_budget_usdt or 0.0,
                position_notional_usdt=actual_notional,
                quantity=quantity,
                required_margin_usdt=actual_margin,
                effective_leverage=effective_leverage,
                position_leverage=position_leverage,
                equity_exposure_ratio=equity_exposure_ratio,
                estimated_fee_usdt=estimated_fee,
                estimated_slippage_usdt=estimated_slippage,
                is_feasible=False,
                infeasible_reason=(
                    f"Target margin (${target_isolated_margin:.2f}) exceeds available capital (${available_equity_usdt:.2f})."
                ),
                target_isolated_margin_usdt=target_isolated_margin,
                estimated_loss_usdt=estimated_loss,
                estimated_loss_pct=loss_pct_equity,
            )

        # Check B: Exchange MIN_NOTIONAL test (e.g. $5.00 on Aster DEX)
        if actual_notional < symbol_specs.min_notional:
            return RiskAssessment(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance_pct=stop_distance_pct,
                risk_budget_usdt=risk_budget_usdt or 0.0,
                position_notional_usdt=actual_notional,
                quantity=quantity,
                required_margin_usdt=actual_margin,
                effective_leverage=effective_leverage,
                position_leverage=position_leverage,
                equity_exposure_ratio=equity_exposure_ratio,
                estimated_fee_usdt=estimated_fee,
                estimated_slippage_usdt=estimated_slippage,
                is_feasible=False,
                infeasible_reason=(
                    f"Position notional (${actual_notional:.2f}) is below exchange minimum notional "
                    f"(${symbol_specs.min_notional:.2f}). Trade is INFEASIBLE."
                ),
                target_isolated_margin_usdt=target_isolated_margin,
                estimated_loss_usdt=estimated_loss,
                estimated_loss_pct=loss_pct_equity,
            )

        # FINDING-01: Hard cap isolated margin check ($6.50 default maximum)
        # Prevents tokens with large minimum quantity from silently over-allocating account margin
        if actual_margin > effective_max_margin:
            return RiskAssessment(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance_pct=stop_distance_pct,
                risk_budget_usdt=risk_budget_usdt or 0.0,
                position_notional_usdt=actual_notional,
                quantity=quantity,
                required_margin_usdt=actual_margin,
                effective_leverage=effective_leverage,
                position_leverage=position_leverage,
                equity_exposure_ratio=equity_exposure_ratio,
                estimated_fee_usdt=estimated_fee,
                estimated_slippage_usdt=estimated_slippage,
                is_feasible=False,
                infeasible_reason=(
                    f"Minimum lot size ({quantity}) requires isolated margin of ${actual_margin:.2f}, "
                    f"which exceeds maximum target margin limit (${effective_max_margin:.2f}). Trade is INFEASIBLE."
                ),
                target_isolated_margin_usdt=target_isolated_margin,
                estimated_loss_usdt=estimated_loss,
                estimated_loss_pct=loss_pct_equity,
            )

        # Check C: Exchange MAX_QTY test
        if symbol_specs.max_qty > 0 and quantity > symbol_specs.max_qty:
            return RiskAssessment(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance_pct=stop_distance_pct,
                risk_budget_usdt=risk_budget_usdt or 0.0,
                position_notional_usdt=actual_notional,
                quantity=quantity,
                required_margin_usdt=actual_margin,
                effective_leverage=effective_leverage,
                position_leverage=position_leverage,
                equity_exposure_ratio=equity_exposure_ratio,
                estimated_fee_usdt=estimated_fee,
                estimated_slippage_usdt=estimated_slippage,
                is_feasible=False,
                infeasible_reason=(
                    f"Order quantity ({quantity}) exceeds exchange maximum quantity ({symbol_specs.max_qty})."
                ),
                target_isolated_margin_usdt=target_isolated_margin,
                estimated_loss_usdt=estimated_loss,
                estimated_loss_pct=loss_pct_equity,
            )

        # Check D: Capital & Margin Availability test (margin + transaction costs <= 95% equity)
        if (actual_margin + total_transaction_cost) > (available_equity_usdt * 0.95):
            return RiskAssessment(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance_pct=stop_distance_pct,
                risk_budget_usdt=risk_budget_usdt or 0.0,
                position_notional_usdt=actual_notional,
                quantity=quantity,
                required_margin_usdt=actual_margin,
                effective_leverage=effective_leverage,
                position_leverage=position_leverage,
                equity_exposure_ratio=equity_exposure_ratio,
                estimated_fee_usdt=estimated_fee,
                estimated_slippage_usdt=estimated_slippage,
                is_feasible=False,
                infeasible_reason=(
                    f"Required margin (${actual_margin:.2f}) + costs (${total_transaction_cost:.2f}) "
                    f"exceeds 95% of available capital (${available_equity_usdt:.2f})."
                ),
                target_isolated_margin_usdt=target_isolated_margin,
                estimated_loss_usdt=estimated_loss,
                estimated_loss_pct=loss_pct_equity,
            )

        # Check E: Hard Risk Limit / Active Risk Budget violation test
        if hard_risk_limit_usdt is not None and estimated_loss > hard_risk_limit_usdt + 1e-9:
            return RiskAssessment(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance_pct=stop_distance_pct,
                risk_budget_usdt=risk_budget_usdt or 0.0,
                position_notional_usdt=actual_notional,
                quantity=quantity,
                required_margin_usdt=actual_margin,
                effective_leverage=effective_leverage,
                position_leverage=position_leverage,
                equity_exposure_ratio=equity_exposure_ratio,
                estimated_fee_usdt=estimated_fee,
                estimated_slippage_usdt=estimated_slippage,
                is_feasible=False,
                infeasible_reason=(
                    f"Order produces estimated loss at SL of ${estimated_loss:.2f} "
                    f"({loss_pct_equity:.1f}% of equity), which exceeds hard risk limit of ${hard_risk_limit_usdt:.2f}."
                ),
                target_isolated_margin_usdt=target_isolated_margin,
                estimated_loss_usdt=estimated_loss,
                estimated_loss_pct=loss_pct_equity,
            )

        if enforce_risk_budget and risk_budget_usdt is not None and estimated_loss > risk_budget_usdt + 1e-9:
            return RiskAssessment(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance_pct=stop_distance_pct,
                risk_budget_usdt=risk_budget_usdt,
                position_notional_usdt=actual_notional,
                quantity=quantity,
                required_margin_usdt=actual_margin,
                effective_leverage=effective_leverage,
                position_leverage=position_leverage,
                equity_exposure_ratio=equity_exposure_ratio,
                estimated_fee_usdt=estimated_fee,
                estimated_slippage_usdt=estimated_slippage,
                is_feasible=False,
                infeasible_reason=(
                    f"Order produces estimated loss at SL of ${estimated_loss:.2f}, "
                    f"which exceeds effective trade risk budget of ${risk_budget_usdt:.2f}."
                ),
                target_isolated_margin_usdt=target_isolated_margin,
                estimated_loss_usdt=estimated_loss,
                estimated_loss_pct=loss_pct_equity,
            )

        return RiskAssessment(
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            stop_distance_pct=stop_distance_pct,
            risk_budget_usdt=risk_budget_usdt or 0.0,
            position_notional_usdt=actual_notional,
            quantity=quantity,
            required_margin_usdt=actual_margin,
            effective_leverage=effective_leverage,
            position_leverage=position_leverage,
            equity_exposure_ratio=equity_exposure_ratio,
            estimated_fee_usdt=estimated_fee,
            estimated_slippage_usdt=estimated_slippage,
            is_feasible=True,
            infeasible_reason=None,
            target_isolated_margin_usdt=target_isolated_margin,
            estimated_loss_usdt=estimated_loss,
            estimated_loss_pct=loss_pct_equity,
        )
