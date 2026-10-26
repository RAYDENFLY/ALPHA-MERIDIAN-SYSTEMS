"""Deterministic Sizing, Fee, and Net-RR Gate Engine for LIVE_MICRO_V1.

Implements the mandatory two-stage cost calculations:
1. Conservative Sizing Case (Case A):
   - Entry: taker fee.
   - Stop exit: taker fee.
   - Estimated slippage on entry and stop.
   - Estimated funding fee.
   - Strict guarantee: Conservative loss <= $0.20 USD fixed risk.
   - Stop distance bounds: [0.40%, 0.80%].
   - Margin constraints: max $7.00 USD isolated margin, max 45% equity, 10x leverage.

2. Expected Net-RR Gate (Case B):
   - Entry: expected maker fill.
   - Take-profit: expected maker fill.
   - Stop exit: taker.
   - Expected Net-RR >= 1.50.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP
import math
from typing import Optional, Tuple
from config.settings import LiveMicroCostsConfig, LiveMicroRiskConfig


@dataclass
class LiveMicroSizingResult:
    """Detailed sizing, fee breakdown, and feasibility outcome."""
    is_feasible: bool
    rejection_reason: str = ""
    quantity: float = 0.0
    notional_usd: float = 0.0
    isolated_margin_usd: float = 0.0
    leverage: int = 10
    stop_distance_pct: float = 0.0
    expected_conservative_loss_usd: float = 0.0
    expected_net_profit_usd: float = 0.0
    gross_rr: float = 0.0
    expected_net_rr: float = 0.0
    expected_entry_fee_usd: float = 0.0

    @property
    def passed(self) -> bool:
        return self.is_feasible
    expected_exit_sl_fee_usd: float = 0.0
    expected_exit_tp_fee_usd: float = 0.0
    expected_slippage_usd: float = 0.0
    expected_funding_usd: float = 0.0


def floor_to_step(value: float, step: float) -> float:
    """Floor value to exact step precision to prevent exchange precision rejections."""
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    factor = 10 ** precision
    stepped = math.floor((value + 1e-12) / step) * step
    return round(stepped, precision)


def normalize_price_to_tick(price: float, tick_size: float, rounding: str = "NEAREST") -> float:
    """Normalize a price to the exact exchange tick size increment.
    
    Parameters:
    - price: The raw price (must be positive).
    - tick_size: The exchange tick size (e.g. 0.0100 for SOL, 0.01 for ETH, 0.1 for BTC).
    - rounding: Rounding direction ("NEAREST", "FLOOR", "CEIL").
    
    Returns:
    - float: Normalized price strictly conforming to tick size.
    
    Raises:
    - ValueError: If price <= 0 or tick_size <= 0.
    """
    if price <= 0:
        raise ValueError(f"Price must be positive, got {price}")
    if tick_size <= 0:
        raise ValueError(f"Tick size must be positive, got {tick_size}")

    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick_size))

    r_mode = rounding.upper()
    if r_mode in ("FLOOR", "DOWN", "ROUND_DOWN"):
        round_mode = ROUND_DOWN
    elif r_mode in ("CEIL", "UP", "ROUND_UP"):
        round_mode = ROUND_UP
    else:
        round_mode = ROUND_HALF_UP

    num_ticks = (d_price / d_tick).quantize(Decimal("1"), rounding=round_mode)
    quantized_price = num_ticks * d_tick

    tick_str = f"{tick_size:.10f}".rstrip("0")
    precision = len(tick_str.split(".")[1]) if "." in tick_str else 0
    return float(round(quantized_price, precision))


class LiveMicroSizingEngine:
    """Calculates conservative sizing and enforces net-RR gate for LIVE_MICRO_V1."""

    def __init__(
        self,
        risk_cfg: Optional[LiveMicroRiskConfig] = None,
        costs_cfg: Optional[LiveMicroCostsConfig] = None,
    ) -> None:
        self.risk_cfg = risk_cfg or LiveMicroRiskConfig()
        self.costs_cfg = costs_cfg or LiveMicroCostsConfig()

    def calculate_sizing(
        self,
        symbol: str = "",
        entry_price: float = 0.0,
        stop_loss_price: float = 0.0,
        take_profit_price: float = 0.0,
        direction: str = "LONG",
        account_equity_usdt: float = 16.0,
        step_size: float = 0.001,
        min_qty: float = 0.001,
        min_notional: float = 5.0,
        funding_rate_est: Optional[float] = None,
    ) -> LiveMicroSizingResult:
        """Alias conforming to standard naming."""
        return self.size_position(
            entry_price=entry_price,
            stop_loss=stop_loss_price,
            take_profit=take_profit_price,
            direction=direction,
            step_size=step_size,
            min_notional=min_notional,
            account_equity=account_equity_usdt,
            funding_rate_est=funding_rate_est,
        )

    def size_position(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        direction: str = "LONG",
        step_size: float = 0.001,
        min_notional: float = 5.0,
        account_equity: float = 16.0,
        funding_rate_est: Optional[float] = None,
    ) -> LiveMicroSizingResult:
        """Size a position deterministically under Case A (Conservative) and Case B (Expected Net-RR)."""
        if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            return LiveMicroSizingResult(is_feasible=False, rejection_reason="INVALID_PRICES: Non-positive price provided")

        is_long = direction.upper() == "LONG"
        stop_dist = entry_price - stop_loss if is_long else stop_loss - entry_price
        tp_dist = take_profit - entry_price if is_long else entry_price - take_profit

        if stop_dist <= 0:
            return LiveMicroSizingResult(
                is_feasible=False,
                rejection_reason=f"INVERTED_STOP: Stop price {stop_loss} is on wrong side of entry {entry_price} for {direction}",
            )
        if tp_dist <= 0:
            return LiveMicroSizingResult(
                is_feasible=False,
                rejection_reason=f"INVERTED_TAKE_PROFIT: TP price {take_profit} is on wrong side of entry {entry_price} for {direction}",
            )

        # 1. Stop Distance Range Check: [0.40%, 0.80%]
        stop_dist_pct = stop_dist / entry_price
        min_stop_pct = self.costs_cfg.min_stop_distance_pct
        max_stop_pct = self.costs_cfg.max_stop_distance_pct

        if stop_dist_pct < min_stop_pct or stop_dist_pct > max_stop_pct:
            return LiveMicroSizingResult(
                is_feasible=False,
                rejection_reason=(
                    f"STOP_DISTANCE_OUT_OF_BOUNDS: Stop distance {stop_dist_pct * 100:.3f}% "
                    f"outside allowed [{min_stop_pct * 100:.2f}%, {max_stop_pct * 100:.2f}%] range"
                ),
                stop_distance_pct=round(stop_dist_pct * 100, 3),
            )

        # 2. Case A: Conservative Sizing Calculation
        # All-in loss at SL = Gross Loss + Taker Entry Fee + Taker Exit Fee + 2x Slippage + Funding Fee
        taker_fee_rate = self.costs_cfg.taker_fee_rate
        slippage_rate = self.costs_cfg.slippage_rate
        fund_rate = funding_rate_est if funding_rate_est is not None else self.costs_cfg.funding_fee_rate_est

        # Per unit conservative loss
        unit_gross_loss = stop_dist
        unit_entry_fee = entry_price * taker_fee_rate
        unit_exit_fee_sl = stop_loss * taker_fee_rate
        unit_slippage = (entry_price + stop_loss) * slippage_rate
        unit_funding = entry_price * abs(fund_rate)
        unit_conservative_loss = unit_gross_loss + unit_entry_fee + unit_exit_fee_sl + unit_slippage + unit_funding

        if unit_conservative_loss <= 0:
            return LiveMicroSizingResult(is_feasible=False, rejection_reason="MATH_ERROR: Unit conservative loss <= 0")

        fixed_risk = self.risk_cfg.fixed_risk_usd  # Locked at $0.20
        raw_qty = fixed_risk / unit_conservative_loss
        qty = floor_to_step(raw_qty, step_size)

        if qty <= 0:
            return LiveMicroSizingResult(
                is_feasible=False,
                rejection_reason=f"QUANTITY_ZERO: Raw quantity {raw_qty:.6f} rounded down to 0 at step {step_size}",
                stop_distance_pct=round(stop_dist_pct * 100, 3),
            )

        # 3. Margin & Leverage Caps
        leverage = self.risk_cfg.leverage
        max_margin_usd = self.risk_cfg.max_isolated_margin_usd  # $7.00
        max_margin_equity = (self.risk_cfg.max_isolated_margin_equity_pct / 100.0) * account_equity  # 45% of equity
        effective_max_margin = min(max_margin_usd, max_margin_equity)

        max_allowed_qty = floor_to_step((effective_max_margin * leverage) / entry_price, step_size)
        if qty > max_allowed_qty:
            qty = max_allowed_qty

        if qty <= 0:
            return LiveMicroSizingResult(
                is_feasible=False,
                rejection_reason=f"MARGIN_CAP_EXCEEDED: Position exceeds margin cap (${effective_max_margin:.2f} max margin)",
                stop_distance_pct=round(stop_dist_pct * 100, 3),
            )

        notional = qty * entry_price
        isolated_margin = notional / leverage

        # 4. Minimum Notional Check
        if notional < min_notional:
            return LiveMicroSizingResult(
                is_feasible=False,
                rejection_reason=f"BELOW_MIN_NOTIONAL: Sized notional ${notional:.2f} < ${min_notional:.2f} exchange minimum",
                quantity=qty,
                notional_usd=round(notional, 4),
                isolated_margin_usd=round(isolated_margin, 4),
                stop_distance_pct=round(stop_dist_pct * 100, 3),
            )

        # Recalculate conservative realized loss with actual stepped qty
        actual_cons_loss = qty * unit_conservative_loss
        if actual_cons_loss > fixed_risk + 1e-6:
            # Step down one unit if rounding pushed over $0.20 budget
            qty = floor_to_step(qty - step_size, step_size)
            if qty <= 0 or (qty * entry_price) < min_notional:
                return LiveMicroSizingResult(
                    is_feasible=False,
                    rejection_reason=f"EXCEEDS_RISK_BUDGET: Sized loss ${actual_cons_loss:.4f} > ${fixed_risk:.2f}",
                )
            notional = qty * entry_price
            isolated_margin = notional / leverage
            actual_cons_loss = qty * unit_conservative_loss

        # 5. Case B: Expected Net-RR Gate
        # Entry: Maker, TP: Maker, SL: Taker
        maker_fee_rate = self.costs_cfg.maker_fee_rate
        expected_gross_profit = qty * tp_dist
        expected_maker_entry_fee = qty * entry_price * maker_fee_rate
        expected_maker_tp_fee = qty * take_profit * maker_fee_rate
        expected_net_profit = expected_gross_profit - (expected_maker_entry_fee + expected_maker_tp_fee)

        expected_net_rr = expected_net_profit / actual_cons_loss if actual_cons_loss > 0 else 0.0
        gross_rr = tp_dist / stop_dist if stop_dist > 0 else 0.0

        min_net_rr = self.costs_cfg.min_net_rr  # 1.50
        if expected_net_rr < min_net_rr:
            return LiveMicroSizingResult(
                is_feasible=False,
                rejection_reason=f"NET_RR_BELOW_GATE: Expected Net-RR {expected_net_rr:.2f} < {min_net_rr:.2f} threshold",
                quantity=qty,
                notional_usd=round(notional, 4),
                isolated_margin_usd=round(isolated_margin, 4),
                stop_distance_pct=round(stop_dist_pct * 100, 3),
                expected_conservative_loss_usd=round(actual_cons_loss, 4),
                expected_net_profit_usd=round(expected_net_profit, 4),
                gross_rr=round(gross_rr, 2),
                expected_net_rr=round(expected_net_rr, 2),
            )

        return LiveMicroSizingResult(
            is_feasible=True,
            rejection_reason="",
            quantity=qty,
            notional_usd=round(notional, 4),
            isolated_margin_usd=round(isolated_margin, 4),
            leverage=leverage,
            stop_distance_pct=round(stop_dist_pct * 100, 3),
            expected_conservative_loss_usd=round(actual_cons_loss, 4),
            expected_net_profit_usd=round(expected_net_profit, 4),
            gross_rr=round(gross_rr, 2),
            expected_net_rr=round(expected_net_rr, 2),
            expected_entry_fee_usd=round(qty * unit_entry_fee, 4),
            expected_exit_sl_fee_usd=round(qty * unit_exit_fee_sl, 4),
            expected_exit_tp_fee_usd=round(expected_maker_tp_fee, 4),
            expected_slippage_usd=round(qty * unit_slippage, 4),
            expected_funding_usd=round(qty * unit_funding, 4),
        )
