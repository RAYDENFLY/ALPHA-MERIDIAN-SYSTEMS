"""Primary Live Strategy: 15M_SWEEP_RECLAIM_SMC (15m/1H Dealing Range & Liquidity Sweep-Reclaim).

Replaces the myopic 5m EMA20 trend-following trigger with authoritative market-structure laws:
1. Dealing Range Zone Locks:
   - HARD LAW 1: NO SHORTING IN BOTTOM HALF (position_pct >= 65.0% Premium requirement).
   - HARD LAW 2: NO LONGING IN TOP HALF (position_pct <= 38.0% Discount requirement, unless Bull FVG + 1H Bull trend).
2. Liquidity Sweep & Rejection:
   - LONG: Sweeps 15m/1H Swing Low / Support, closes back inside/above with WickR >= 0.30 and RSI/Stoch recovering from oversold (<30-35).
   - SHORT: Sweeps 15m/1H Swing High / Bear FVG, closes back inside/below with WickR >= 0.30 and RSI/Stoch turning down from overbought (>65-70).
3. Order Execution & Protection:
   - MAKER LIMIT order at the reclaimed support/resistance level.
   - Stop Loss placed 1.15% – 1.40% from entry, strictly beyond sweep wick extreme.
   - Sizing strictly capped at $0.20 USD fixed conservative risk on 10x isolated leverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Tuple

from config.settings import LiveMicroCostsConfig, LiveMicroRiskConfig
from src.core.types import Candle, Direction
from src.risk.live_micro_sizing import (
    LiveMicroSizingEngine,
    LiveMicroSizingResult,
    floor_to_step,
    normalize_price_to_tick,
)
from src.strategy.dealing_range import (
    DealingRangeInfo,
    check_dealing_range_zone_lock,
    compute_dealing_range,
)


@dataclass
class SweepReclaim15MSetup:
    """Deterministic signal and candidate parameters for 15M_SWEEP_RECLAIM_SMC."""
    strategy_id: str = "15M_SWEEP_RECLAIM_SMC"
    config_version: str = "LIVE_MICRO_V1"
    symbol: str = ""
    direction: Direction = Direction.LONG
    order_type: str = "LIMIT"
    post_only: bool = True
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    stop_distance_pct: float = 0.0
    sweep_level: float = 0.0
    sweep_wick_extreme: float = 0.0
    wick_ratio: float = 0.0
    rsi_15m: float = 0.0
    dealing_range_position_pct: float = 0.0
    sizing: Optional[LiveMicroSizingResult] = None
    trigger_candle_time_ms: int = 0
    reasons: List[str] = field(default_factory=list)
    rejection_reason: str = ""
    tp_target_type: str = "FIXED_RR_2.25R"
    gross_rr: float = 0.0
    sl_invalidation_type: str = "15M_SWEEP_WICK_PLUS_ATR"
    planned_risk_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "config_version": self.config_version,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "order_type": self.order_type,
            "post_only": self.post_only,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "stop_distance_pct": self.stop_distance_pct,
            "sweep_level": self.sweep_level,
            "sweep_wick_extreme": self.sweep_wick_extreme,
            "wick_ratio": round(self.wick_ratio, 3),
            "rsi_15m": round(self.rsi_15m, 2),
            "dealing_range_position_pct": round(self.dealing_range_position_pct, 2),
            "quantity": self.sizing.quantity if self.sizing else 0.0,
            "notional_usd": self.sizing.notional_usd if self.sizing else 0.0,
            "isolated_margin_usd": self.sizing.isolated_margin_usd if self.sizing else 0.0,
            "expected_net_rr": self.sizing.expected_net_rr if self.sizing else 0.0,
            "conservative_loss_usd": self.sizing.expected_conservative_loss_usd if self.sizing else 0.0,
            "trigger_candle_time_ms": self.trigger_candle_time_ms,
            "reasons": self.reasons,
            "tp_target_type": self.tp_target_type,
            "gross_rr": round(self.gross_rr, 2) if self.gross_rr else (self.sizing.gross_rr if self.sizing else 0.0),
            "sl_invalidation_type": self.sl_invalidation_type,
            "planned_risk_usd": round(self.planned_risk_usd, 4),
        }


def calc_rsi(closes: List[float], period: int = 14) -> List[float]:
    """Calculate Relative Strength Index (RSI) across closes."""
    n = len(closes)
    if n <= period:
        return [50.0] * n
    gains: List[float] = [0.0]
    losses: List[float] = [0.0]
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))

    rsi_values: List[float] = [50.0] * period
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period

    if avg_loss == 0.0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0.0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_values


def calc_atr(candles: List[Candle], period: int = 14) -> float:
    """Calculate Average True Range on candle series."""
    if len(candles) < 2:
        return candles[-1].close * 0.01 if candles else 1.0
    tr_list = []
    for i in range(1, len(candles)):
        h = candles[i].high
        l = candles[i].low
        prev_c = candles[i - 1].close
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
    recent = tr_list[-period:] if len(tr_list) >= period else tr_list
    return float(sum(recent) / len(recent)) if recent else candles[-1].close * 0.01


def find_swings_15m(
    candles: List[Candle],
    left_bars: int = 3,
    right_bars: int = 2,
) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Find confirmed swing highs and lows strictly on closed candles."""
    n = len(candles)
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []
    if n < left_bars + right_bars + 1:
        return swing_highs, swing_lows

    for i in range(left_bars, n - right_bars):
        c = candles[i]
        # High check
        if all(candles[j].high <= c.high for j in range(i - left_bars, i + right_bars + 1) if j != i):
            swing_highs.append((i, c.high))
        # Low check
        if all(candles[j].low >= c.low for j in range(i - left_bars, i + right_bars + 1) if j != i):
            swing_lows.append((i, c.low))

    return swing_highs, swing_lows



@dataclass
class DynamicTPTarget:
    """Individual candidate structural resistance or orderflow target."""
    target_price: float
    gross_rr: float
    target_type: str
    raw_level: float
    is_frontrun: bool = True
    frontrun_buffer_pct: float = 0.0008


@dataclass
class DynamicTPResult:
    """Result of dynamic structural take-profit targeting and room-to-resistance evaluation."""
    take_profit_price: float
    tp_target_type: str
    gross_rr: float
    is_eligible: bool
    rejection_reason: str = ""
    target_level_price: float = 0.0
    candidate_targets: List[DynamicTPTarget] = field(default_factory=list)


def find_prominent_swings(
    candles: List[Candle],
    left_bars: int = 3,
    right_bars: int = 2,
) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Find swing highs and lows that have actual prominence (not completely flat dummy bars)."""
    n = len(candles)
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []
    if n < left_bars + right_bars + 1:
        return swing_highs, swing_lows

    for i in range(left_bars, n - right_bars):
        c = candles[i]
        # High check: must be >= all bars in window and strictly > at least one bar on each side
        if all(candles[j].high <= c.high for j in range(i - left_bars, i + right_bars + 1) if j != i):
            if any(c.high > candles[j].high for j in range(i - left_bars, i)) and any(c.high > candles[j].high for j in range(i + 1, i + right_bars + 1)):
                swing_highs.append((i, c.high))
        # Low check
        if all(candles[j].low >= c.low for j in range(i - left_bars, i + right_bars + 1) if j != i):
            if any(c.low < candles[j].low for j in range(i - left_bars, i)) and any(c.low < candles[j].low for j in range(i + 1, i + right_bars + 1)):
                swing_lows.append((i, c.low))

    return swing_highs, swing_lows



@dataclass
class DynamicSLResult:
    """Result of 3-Tier Dynamic Structural Invalidation SL & Risk-Constant Sizing."""
    stop_loss_price: float
    raw_invalidation_price: float
    stop_distance_pct: float
    sl_invalidation_type: str
    is_eligible: bool
    rejection_reason: Optional[str] = None
    planned_quantity: float = 0.0
    planned_risk_usd: float = 0.0
    sweep_wick_extreme: float = 0.0
    structural_zone_extreme: Optional[float] = None
    protecting_wall_price: Optional[float] = None
    defense_anchors: List[str] = field(default_factory=list)


def compute_dynamic_structural_sl(
    entry_price: float,
    direction: Direction | str,
    candles_15m: List[Candle],
    candles_1h: Optional[List[Candle]] = None,
    sweep_wick_extreme: Optional[float] = None,
    structural_zone_extreme: Optional[float] = None,
    protecting_wall_price: Optional[float] = None,
    atr_15m: Optional[float] = None,
    tick_size: float = 0.01,
    step_size: float = 0.001,
    min_qty: float = 0.001,
    min_risk_budget_usd: float = 0.20,
    max_risk_cap_usd: float = 0.22,
) -> DynamicSLResult:
    """3-Tier Dynamic Structural Invalidation Stop-Loss & Risk-Constant Sizing Engine.

    Instead of a fixed percentage, computes the exact price where the trade thesis
    is structurally invalidated by combining 3 defense anchors:
    1. Sweep Wick Extreme:
       - LONG: min(candle.low) across sweep candle, prior 3 candles (15m), and last 2 candles (1H).
       - SHORT: max(candle.high) across sweep candle, prior 3 candles (15m), and last 2 candles (1H).
    2. Structural Zone Extreme:
       - LONG: Lower boundary of active Bullish FVG / Order Block / Reclaimed Support.
       - SHORT: Upper boundary of active Bearish FVG / Order Block / Reclaimed Resistance.
    3. Protecting L2 Wall:
       - LONG: Nearest significant bid wall below entry within [0.40%, 1.60%] (acts as shield).
       - SHORT: Nearest significant ask wall above entry within [0.40%, 1.60%] (acts as shield).

    Formula:
      LONG:  raw_invalidation_price = min(anchors) - (0.25 * atr_15m)
      SHORT: raw_invalidation_price = max(anchors) + (0.25 * atr_15m)

    Volatility Guard Band ([1.20%, 2.20%]) & Risk-Constant Position Sizing:
      - raw_stop_pct > 2.20%: Veto (STRUCTURAL_INVALIDATION_TOO_WIDE)
      - raw_stop_pct < 1.20%: Widen to 1.20% (FLOOR_WIDENED_1.20PCT)
      - Quantize to symbol tick_size: FLOOR for LONG, CEIL for SHORT
      - Planned quantity: min_risk_budget_usd ($0.20) / stop_distance quantized to step_size
      - If min_qty causes planned_risk > $0.22: Veto (MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL)
    """
    is_long = (direction == Direction.LONG or str(direction).upper() == "LONG")

    # Calculate ATR 15m if not provided
    if atr_15m is None or atr_15m <= 0:
        atr_15m = calc_atr(candles_15m, period=14) if candles_15m else entry_price * 0.005
    if atr_15m <= 0:
        atr_15m = entry_price * 0.005

    defense_anchors: List[str] = []

    if is_long:
        # 1. Sweep Wick Extreme
        wick_lows = [c.low for c in candles_15m[-4:]] if candles_15m else [entry_price]
        if sweep_wick_extreme is not None and sweep_wick_extreme > 0:
            wick_lows.append(sweep_wick_extreme)
        if candles_1h and len(candles_1h) >= 1:
            wick_lows.extend(c.low for c in candles_1h[-2:])

        sweep_wick_low = min(wick_lows)
        anchors = [sweep_wick_low]
        defense_anchors.append(f"SWEEP_WICK_EXTREME: {sweep_wick_low:.4f}")

        # 2. Structural Zone Bottom
        if structural_zone_extreme is not None and structural_zone_extreme > 0:
            anchors.append(structural_zone_extreme)
            defense_anchors.append(f"STRUCTURAL_ZONE_BOTTOM: {structural_zone_extreme:.4f}")

        # 3. Protecting L2 Bid Wall within [0.40%, 1.60%] below entry
        wall_shielded = False
        if protecting_wall_price is not None and protecting_wall_price > 0:
            wall_dist_pct = (entry_price - protecting_wall_price) / (entry_price + 1e-12)
            if 0.0040 <= wall_dist_pct <= 0.0160:
                anchors.append(protecting_wall_price)
                defense_anchors.append(f"PROTECTING_L2_BID_WALL: {protecting_wall_price:.4f} ({wall_dist_pct*100:.2f}%)")
                if protecting_wall_price == min(anchors):
                    wall_shielded = True

        raw_anchor_level = min(anchors)
        raw_invalidation_price = raw_anchor_level - (0.25 * atr_15m)
        raw_stop_dist = entry_price - raw_invalidation_price

    else:  # SHORT
        # 1. Sweep Wick Extreme
        wick_highs = [c.high for c in candles_15m[-4:]] if candles_15m else [entry_price]
        if sweep_wick_extreme is not None and sweep_wick_extreme > 0:
            wick_highs.append(sweep_wick_extreme)
        if candles_1h and len(candles_1h) >= 1:
            wick_highs.extend(c.high for c in candles_1h[-2:])

        sweep_wick_high = max(wick_highs)
        anchors = [sweep_wick_high]
        defense_anchors.append(f"SWEEP_WICK_EXTREME: {sweep_wick_high:.4f}")

        # 2. Structural Zone Top
        if structural_zone_extreme is not None and structural_zone_extreme > 0:
            anchors.append(structural_zone_extreme)
            defense_anchors.append(f"STRUCTURAL_ZONE_TOP: {structural_zone_extreme:.4f}")

        # 3. Protecting L2 Ask Wall within [0.40%, 1.60%] above entry
        wall_shielded = False
        if protecting_wall_price is not None and protecting_wall_price > 0:
            wall_dist_pct = (protecting_wall_price - entry_price) / (entry_price + 1e-12)
            if 0.0040 <= wall_dist_pct <= 0.0160:
                anchors.append(protecting_wall_price)
                defense_anchors.append(f"PROTECTING_L2_ASK_WALL: {protecting_wall_price:.4f} ({wall_dist_pct*100:.2f}%)")
                if protecting_wall_price == max(anchors):
                    wall_shielded = True

        raw_anchor_level = max(anchors)
        raw_invalidation_price = raw_anchor_level + (0.25 * atr_15m)
        raw_stop_dist = raw_invalidation_price - entry_price

    if raw_stop_dist <= 0:
        return DynamicSLResult(
            stop_loss_price=0.0,
            raw_invalidation_price=raw_invalidation_price,
            stop_distance_pct=0.0,
            sl_invalidation_type="INVERTED_STOP",
            is_eligible=False,
            rejection_reason=f"INVERTED_STOP: Invalidation price {raw_invalidation_price:.4f} is on wrong side of entry {entry_price:.4f}",
            sweep_wick_extreme=sweep_wick_low if is_long else sweep_wick_high,
            structural_zone_extreme=structural_zone_extreme,
            protecting_wall_price=protecting_wall_price,
            defense_anchors=defense_anchors,
        )

    raw_stop_ratio = raw_stop_dist / entry_price
    raw_stop_pct = raw_stop_ratio * 100.0

    # Guard Band 1: Maximum Structural Width Cap (> 2.20%)
    if raw_stop_ratio > 0.0220:
        return DynamicSLResult(
            stop_loss_price=0.0,
            raw_invalidation_price=raw_invalidation_price,
            stop_distance_pct=round(raw_stop_pct, 3),
            sl_invalidation_type="STRUCTURAL_INVALIDATION_TOO_WIDE",
            is_eligible=False,
            rejection_reason=f"STRUCTURAL_INVALIDATION_TOO_WIDE: Raw stop distance {raw_stop_pct:.2f}% exceeds 2.20% maximum cap",
            sweep_wick_extreme=sweep_wick_low if is_long else sweep_wick_high,
            structural_zone_extreme=structural_zone_extreme,
            protecting_wall_price=protecting_wall_price,
            defense_anchors=defense_anchors,
        )

    # Guard Band 2: Minimum Breathing-Room Floor (1.20%)
    if raw_stop_ratio < 0.0120:
        floor_dist = entry_price * 0.0120
        if is_long:
            planned_stop_price = entry_price - floor_dist
        else:
            planned_stop_price = entry_price + floor_dist
        sl_invalidation_type = "FLOOR_WIDENED_1.20PCT"
    else:
        planned_stop_price = raw_invalidation_price
        if wall_shielded:
            sl_invalidation_type = "L2_WALL_SHIELDED_SL"
        else:
            sl_invalidation_type = "15M_SWEEP_WICK_PLUS_ATR"

    # Quantize to symbol tick_size: FLOOR for LONG SL, CEIL for SHORT SL
    if is_long:
        stop_loss_price = normalize_price_to_tick(planned_stop_price, tick_size, rounding="FLOOR")
        final_stop_dist = entry_price - stop_loss_price
    else:
        stop_loss_price = normalize_price_to_tick(planned_stop_price, tick_size, rounding="CEIL")
        final_stop_dist = stop_loss_price - entry_price

    if final_stop_dist <= 0:
        return DynamicSLResult(
            stop_loss_price=0.0,
            raw_invalidation_price=raw_invalidation_price,
            stop_distance_pct=0.0,
            sl_invalidation_type="INVALID_STOP_DISTANCE",
            is_eligible=False,
            rejection_reason="INVALID_STOP_DISTANCE: Quantized stop loss is at or beyond entry price",
            sweep_wick_extreme=sweep_wick_low if is_long else sweep_wick_high,
            structural_zone_extreme=structural_zone_extreme,
            protecting_wall_price=protecting_wall_price,
            defense_anchors=defense_anchors,
        )

    final_stop_pct = (final_stop_dist / entry_price) * 100.0

    # Risk-Constant Position Sizing
    raw_qty = min_risk_budget_usd / final_stop_dist
    step = step_size if (step_size is not None and step_size > 0) else 0.001
    step_prec = max(0, int(round(-math.log10(step)))) if (step < 1.0) else 0
    planned_qty = math.floor(raw_qty / step) * step
    planned_qty = round(planned_qty, step_prec)

    effective_min_qty = min_qty if (min_qty is not None and min_qty > 0) else step
    if planned_qty < effective_min_qty:
        planned_qty = effective_min_qty

    planned_risk_usd = planned_qty * final_stop_dist

    # Hard Risk Veto Cap ($0.22)
    if planned_risk_usd > max_risk_cap_usd:
        return DynamicSLResult(
            stop_loss_price=stop_loss_price,
            raw_invalidation_price=raw_invalidation_price,
            stop_distance_pct=round(final_stop_pct, 3),
            sl_invalidation_type="MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL",
            is_eligible=False,
            rejection_reason=(
                f"MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL: Risk ${planned_risk_usd:.3f} at min_qty {effective_min_qty} "
                f"exceeds ${max_risk_cap_usd:.2f} cap (Stop: {final_stop_pct:.2f}%)"
            ),
            planned_quantity=planned_qty,
            planned_risk_usd=round(planned_risk_usd, 4),
            sweep_wick_extreme=sweep_wick_low if is_long else sweep_wick_high,
            structural_zone_extreme=structural_zone_extreme,
            protecting_wall_price=protecting_wall_price,
            defense_anchors=defense_anchors,
        )

    return DynamicSLResult(
        stop_loss_price=stop_loss_price,
        raw_invalidation_price=raw_invalidation_price,
        stop_distance_pct=round(final_stop_pct, 3),
        sl_invalidation_type=sl_invalidation_type,
        is_eligible=True,
        rejection_reason=None,
        planned_quantity=planned_qty,
        planned_risk_usd=round(planned_risk_usd, 4),
        sweep_wick_extreme=sweep_wick_low if is_long else sweep_wick_high,
        structural_zone_extreme=structural_zone_extreme,
        protecting_wall_price=protecting_wall_price,
        defense_anchors=defense_anchors,
    )


def compute_dynamic_structural_tp(
    entry_price: float,
    stop_loss_price: float,
    direction: str | Direction,
    candles_15m: List[Candle],
    candles_1h: Optional[List[Candle]] = None,
    dealing_range_bounds: Optional[Tuple[float, float]] = None,
    dealing_range_position_pct: Optional[float] = None,
    nearest_ask_wall: Optional[Dict[str, Any] | float] = None,
    nearest_bid_wall: Optional[Dict[str, Any] | float] = None,
    unmitigated_bear_fvg: Optional[Tuple[float, float] | List[float]] = None,
    unmitigated_bull_fvg: Optional[Tuple[float, float] | List[float]] = None,
    tick_size: float = 0.01,
    front_run_buffer_pct: float = 0.0008,
    min_room_rr: float = 1.25,
    min_target_rr: float = 1.40,
    max_target_rr: float = 3.00,
    sweet_spot_rr_range: Tuple[float, float] = (1.50, 2.20),
    current_mark_price: Optional[float] = None,
    for_open_position: bool = False,
) -> DynamicTPResult:
    """Dynamically collect structural magnet/resistance levels and select the optimal Take Profit target.
    
    Front-runs major L2 walls and swing highs/lows with 0.05% - 0.08% buffer, enforces the
    minimum >= 1.25R Room-to-Resistance gate, and normalizes output strictly to exchange tick_size.
    When `for_open_position=True`, obstacle veto is bypassed (position is already running), and
    candidate targets are strictly validated to be on the profitable side of `current_mark_price`.
    """
    dir_str = direction.value if hasattr(direction, "value") else str(direction).strip().upper()
    is_long = dir_str == "LONG"

    if is_long:
        risk = entry_price - stop_loss_price
        if risk <= 1e-9:
            return DynamicTPResult(
                take_profit_price=0.0,
                tp_target_type="INVALID",
                gross_rr=0.0,
                is_eligible=False,
                rejection_reason="INVALID_STOP_LOSS: stop_loss must be strictly below entry_price for LONG",
            )

        # 1. Check Room-to-Resistance Obstacles (< min_room_rr, default 1.25R)
        if not for_open_position:
            # A. Opposing Ask Wall
            if nearest_ask_wall is not None:
                ask_p = float(nearest_ask_wall.get("price", 0.0) if isinstance(nearest_ask_wall, dict) else nearest_ask_wall)
                ask_size = float(nearest_ask_wall.get("size_usdt", 0.0) if isinstance(nearest_ask_wall, dict) else 10000.0)
                if ask_p > entry_price:
                    dist_rr = (ask_p - entry_price) / risk
                    if dist_rr < min_room_rr and ask_size >= 5000.0:
                        return DynamicTPResult(
                            take_profit_price=0.0,
                            tp_target_type="L2_ASK_WALL_BLOCKED",
                            gross_rr=round(dist_rr, 2),
                            is_eligible=False,
                            rejection_reason=f"INSUFFICIENT_ROOM_TO_RESISTANCE: Heavy L2 ask wall of ${ask_size:,.0f} at {ask_p} blocks path at {dist_rr:.2f}R (< {min_room_rr:.2f}R minimum room-to-resistance)",
                            target_level_price=ask_p,
                        )

            # B. Opposing 15m Prominent Swing Highs
            sw_highs_15m, _ = find_prominent_swings(candles_15m[:-1], left_bars=3, right_bars=2) if len(candles_15m) >= 6 else ([], [])
            for idx, h in sw_highs_15m:
                if h > entry_price:
                    dist_rr = (h - entry_price) / risk
                    if dist_rr < min_room_rr:
                        return DynamicTPResult(
                            take_profit_price=0.0,
                            tp_target_type="SWING_HIGH_BLOCKED",
                            gross_rr=round(dist_rr, 2),
                            is_eligible=False,
                            rejection_reason=f"INSUFFICIENT_ROOM_TO_RESISTANCE: Structural 15m swing high at {h} blocks path at {dist_rr:.2f}R (< {min_room_rr:.2f}R minimum room-to-resistance)",
                            target_level_price=h,
                        )
        else:
            sw_highs_15m, _ = find_prominent_swings(candles_15m[:-1], left_bars=3, right_bars=2) if len(candles_15m) >= 6 else ([], [])

        # 2. Gather Candidate Targets
        candidates: List[DynamicTPTarget] = []

        # Target A: L2 Orderbook Ask Wall
        if nearest_ask_wall is not None:
            ask_p = float(nearest_ask_wall.get("price", 0.0) if isinstance(nearest_ask_wall, dict) else nearest_ask_wall)
            if ask_p > entry_price:
                raw_t = ask_p * 0.9992  # 0.08% front-run below ask wall
                t_rr = (raw_t - entry_price) / risk
                if t_rr >= min_room_rr:
                    candidates.append(DynamicTPTarget(
                        target_price=raw_t,
                        gross_rr=t_rr,
                        target_type="L2_ASK_WALL_FRONTRUN",
                        raw_level=ask_p,
                    ))

        # Target B: 15m Structural Swing Highs
        for idx, h in sw_highs_15m:
            if h > entry_price:
                raw_t = h * (1.0 - front_run_buffer_pct)
                t_rr = (raw_t - entry_price) / risk
                if t_rr >= min_room_rr:
                    candidates.append(DynamicTPTarget(
                        target_price=raw_t,
                        gross_rr=t_rr,
                        target_type="15M_SWING_HIGH",
                        raw_level=h,
                    ))

        # Target C: 1H Structural Swing Highs
        if candles_1h and len(candles_1h) >= 4:
            sw_highs_1h, _ = find_prominent_swings(candles_1h[:-1], left_bars=2, right_bars=1)
            for idx, h in sw_highs_1h:
                if h > entry_price:
                    raw_t = h * (1.0 - front_run_buffer_pct)
                    t_rr = (raw_t - entry_price) / risk
                    if t_rr >= min_room_rr:
                        candidates.append(DynamicTPTarget(
                            target_price=raw_t,
                            gross_rr=t_rr,
                            target_type="1H_SWING_HIGH",
                            raw_level=h,
                        ))

        # Target D: Unmitigated Bear FVG / Supply Zone Boundary
        if unmitigated_bear_fvg is not None:
            fvg_low = float(unmitigated_bear_fvg[0])
            if fvg_low > entry_price:
                raw_t = fvg_low * (1.0 - front_run_buffer_pct)
                t_rr = (raw_t - entry_price) / risk
                if t_rr >= min_room_rr:
                    candidates.append(DynamicTPTarget(
                        target_price=raw_t,
                        gross_rr=t_rr,
                        target_type="1H_FVG_BOUNDARY",
                        raw_level=fvg_low,
                    ))

        # Target E: Dealing Range Equilibrium / Midline / Range High (50%, 55%, 60%, 75%, 100%)
        if dealing_range_bounds is not None:
            dr_l, dr_h = dealing_range_bounds
            if dr_h > dr_l:
                for pct, tag in [(0.50, "50PCT"), (0.55, "55PCT"), (0.60, "60PCT"), (0.75, "75PCT"), (1.00, "100PCT_RANGE_HIGH")]:
                    eq = dr_l + pct * (dr_h - dr_l)
                    if eq > entry_price:
                        raw_t = eq * (1.0 - 0.0005)
                        t_rr = (raw_t - entry_price) / risk
                        if t_rr >= min_room_rr:
                            candidates.append(DynamicTPTarget(
                                target_price=raw_t,
                                gross_rr=t_rr,
                                target_type=f"DEALING_RANGE_EQ_{tag}",
                                raw_level=eq,
                            ))

        # 3. Select Target: Filter profitable side of mark price (if provided) and prioritize Sweet Spot [1.50R, 2.20R]
        if current_mark_price is not None:
            candidates_in_front = [c for c in candidates if c.target_price > current_mark_price]
        else:
            candidates_in_front = candidates

        valid_targets = [c for c in candidates_in_front if min_target_rr <= c.gross_rr <= max_target_rr]
        sweet_spot = [c for c in valid_targets if sweet_spot_rr_range[0] <= c.gross_rr <= sweet_spot_rr_range[1]]

        if sweet_spot:
            sweet_spot.sort(key=lambda x: x.gross_rr)
            selected = sweet_spot[0]
        elif valid_targets:
            valid_targets.sort(key=lambda x: x.gross_rr)
            selected = valid_targets[0]
        else:
            fb_p = entry_price + risk * 2.0
            if current_mark_price is not None and fb_p <= current_mark_price:
                return DynamicTPResult(
                    take_profit_price=0.0,
                    tp_target_type="REJECTED_BEHIND_MARK",
                    gross_rr=0.0,
                    is_eligible=False,
                    rejection_reason=f"INVALID_TARGET: All structural targets and default 2.0R ({fb_p}) are at or behind mark price {current_mark_price}",
                    candidate_targets=candidates,
                )
            selected = DynamicTPTarget(
                target_price=fb_p,
                gross_rr=2.0,
                target_type="DEFAULT_STRUCTURAL_2.0R",
                raw_level=fb_p,
            )

        final_tp = normalize_price_to_tick(selected.target_price, tick_size, rounding="FLOOR")
        final_rr = (final_tp - entry_price) / risk

        if current_mark_price is not None and final_tp <= current_mark_price:
            return DynamicTPResult(
                take_profit_price=0.0,
                tp_target_type="REJECTED_BEHIND_MARK",
                gross_rr=0.0,
                is_eligible=False,
                rejection_reason=f"INVALID_TARGET: Final TP {final_tp} is at or behind current mark price {current_mark_price} (LONG requires TP > mark price)",
                target_level_price=selected.raw_level,
                candidate_targets=candidates,
            )

        return DynamicTPResult(
            take_profit_price=final_tp,
            tp_target_type=selected.target_type,
            gross_rr=round(final_rr, 2),
            is_eligible=True,
            target_level_price=selected.raw_level,
            candidate_targets=candidates,
        )

    else:  # SHORT
        risk = stop_loss_price - entry_price
        if risk <= 1e-9:
            return DynamicTPResult(
                take_profit_price=0.0,
                tp_target_type="INVALID",
                gross_rr=0.0,
                is_eligible=False,
                rejection_reason="INVALID_STOP_LOSS: stop_loss must be strictly above entry_price for SHORT",
            )

        # 1. Check Room-to-Resistance Obstacles (< min_room_rr, default 1.25R)
        if not for_open_position:
            # A. Opposing Bid Wall
            if nearest_bid_wall is not None:
                bid_p = float(nearest_bid_wall.get("price", 0.0) if isinstance(nearest_bid_wall, dict) else nearest_bid_wall)
                bid_size = float(nearest_bid_wall.get("size_usdt", 0.0) if isinstance(nearest_bid_wall, dict) else 10000.0)
                if bid_p < entry_price:
                    dist_rr = (entry_price - bid_p) / risk
                    if dist_rr < min_room_rr and bid_size >= 5000.0:
                        return DynamicTPResult(
                            take_profit_price=0.0,
                            tp_target_type="L2_BID_WALL_BLOCKED",
                            gross_rr=round(dist_rr, 2),
                            is_eligible=False,
                            rejection_reason=f"INSUFFICIENT_ROOM_TO_RESISTANCE: Heavy L2 bid wall of ${bid_size:,.0f} at {bid_p} blocks path at {dist_rr:.2f}R (< {min_room_rr:.2f}R minimum room-to-resistance)",
                            target_level_price=bid_p,
                        )

            # B. Opposing 15m Prominent Swing Lows
            _, sw_lows_15m = find_prominent_swings(candles_15m[:-1], left_bars=3, right_bars=2) if len(candles_15m) >= 6 else ([], [])
            for idx, l in sw_lows_15m:
                if l < entry_price:
                    dist_rr = (entry_price - l) / risk
                    if dist_rr < min_room_rr:
                        return DynamicTPResult(
                            take_profit_price=0.0,
                            tp_target_type="SWING_LOW_BLOCKED",
                            gross_rr=round(dist_rr, 2),
                            is_eligible=False,
                            rejection_reason=f"INSUFFICIENT_ROOM_TO_RESISTANCE: Structural 15m swing low at {l} blocks path at {dist_rr:.2f}R (< {min_room_rr:.2f}R minimum room-to-resistance)",
                            target_level_price=l,
                        )
        else:
            _, sw_lows_15m = find_prominent_swings(candles_15m[:-1], left_bars=3, right_bars=2) if len(candles_15m) >= 6 else ([], [])

        # 2. Gather Candidate Targets
        candidates: List[DynamicTPTarget] = []

        # Target A: L2 Orderbook Bid Wall
        if nearest_bid_wall is not None:
            bid_p = float(nearest_bid_wall.get("price", 0.0) if isinstance(nearest_bid_wall, dict) else nearest_bid_wall)
            if bid_p < entry_price:
                raw_t = bid_p * 1.0008  # 0.08% front-run above bid wall
                t_rr = (entry_price - raw_t) / risk
                if t_rr >= min_room_rr:
                    candidates.append(DynamicTPTarget(
                        target_price=raw_t,
                        gross_rr=t_rr,
                        target_type="L2_BID_WALL_FRONTRUN",
                        raw_level=bid_p,
                    ))

        # Target B: 15m Structural Swing Lows
        for idx, l in sw_lows_15m:
            if l < entry_price:
                raw_t = l * (1.0 + front_run_buffer_pct)
                t_rr = (entry_price - raw_t) / risk
                if t_rr >= min_room_rr:
                    candidates.append(DynamicTPTarget(
                        target_price=raw_t,
                        gross_rr=t_rr,
                        target_type="15M_SWING_LOW",
                        raw_level=l,
                    ))

        # Target C: 1H Structural Swing Lows
        if candles_1h and len(candles_1h) >= 4:
            _, sw_lows_1h = find_prominent_swings(candles_1h[:-1], left_bars=2, right_bars=1)
            for idx, l in sw_lows_1h:
                if l < entry_price:
                    raw_t = l * (1.0 + front_run_buffer_pct)
                    t_rr = (entry_price - raw_t) / risk
                    if t_rr >= min_room_rr:
                        candidates.append(DynamicTPTarget(
                            target_price=raw_t,
                            gross_rr=t_rr,
                            target_type="1H_SWING_LOW",
                            raw_level=l,
                        ))

        # Target D: Unmitigated Bull FVG / Demand Zone Boundary
        if unmitigated_bull_fvg is not None:
            fvg_high = float(unmitigated_bull_fvg[1])
            if fvg_high < entry_price:
                raw_t = fvg_high * (1.0 + front_run_buffer_pct)
                t_rr = (entry_price - raw_t) / risk
                if t_rr >= min_room_rr:
                    candidates.append(DynamicTPTarget(
                        target_price=raw_t,
                        gross_rr=t_rr,
                        target_type="1H_FVG_BOUNDARY",
                        raw_level=fvg_high,
                    ))

        # Target E: Dealing Range Equilibrium / Midline / Range Low (50%, 45%, 40%, 25%, 0%)
        if dealing_range_bounds is not None:
            dr_l, dr_h = dealing_range_bounds
            if dr_h > dr_l:
                for pct, tag in [(0.50, "50PCT"), (0.45, "45PCT"), (0.40, "40PCT"), (0.25, "25PCT"), (0.00, "0PCT_RANGE_LOW")]:
                    eq = dr_l + pct * (dr_h - dr_l)
                    if eq < entry_price:
                        raw_t = eq * (1.0 + 0.0005)
                        t_rr = (entry_price - raw_t) / risk
                        if t_rr >= min_room_rr:
                            candidates.append(DynamicTPTarget(
                                target_price=raw_t,
                                gross_rr=t_rr,
                                target_type=f"DEALING_RANGE_EQ_{tag}",
                                raw_level=eq,
                            ))

        # 3. Select Target: Filter profitable side of mark price (if provided) and prioritize Sweet Spot [1.50R, 2.20R]
        if current_mark_price is not None:
            candidates_in_front = [c for c in candidates if c.target_price < current_mark_price]
        else:
            candidates_in_front = candidates

        valid_targets = [c for c in candidates_in_front if min_target_rr <= c.gross_rr <= max_target_rr]
        sweet_spot = [c for c in valid_targets if sweet_spot_rr_range[0] <= c.gross_rr <= sweet_spot_rr_range[1]]

        if sweet_spot:
            sweet_spot.sort(key=lambda x: x.gross_rr)
            selected = sweet_spot[0]
        elif valid_targets:
            valid_targets.sort(key=lambda x: x.gross_rr)
            selected = valid_targets[0]
        else:
            fb_p = entry_price - risk * 2.0
            if current_mark_price is not None and fb_p >= current_mark_price:
                return DynamicTPResult(
                    take_profit_price=0.0,
                    tp_target_type="REJECTED_BEHIND_MARK",
                    gross_rr=0.0,
                    is_eligible=False,
                    rejection_reason=f"INVALID_TARGET: All structural targets and default 2.0R ({fb_p}) are at or behind mark price {current_mark_price}",
                    candidate_targets=candidates,
                )
            selected = DynamicTPTarget(
                target_price=fb_p,
                gross_rr=2.0,
                target_type="DEFAULT_STRUCTURAL_2.0R",
                raw_level=fb_p,
            )

        final_tp = normalize_price_to_tick(selected.target_price, tick_size, rounding="CEIL")
        final_rr = (entry_price - final_tp) / risk

        if current_mark_price is not None and final_tp >= current_mark_price:
            return DynamicTPResult(
                take_profit_price=0.0,
                tp_target_type="REJECTED_BEHIND_MARK",
                gross_rr=0.0,
                is_eligible=False,
                rejection_reason=f"INVALID_TARGET: Final TP {final_tp} is at or behind current mark price {current_mark_price} (SHORT requires TP < mark price)",
                target_level_price=selected.raw_level,
                candidate_targets=candidates,
            )

        return DynamicTPResult(
            take_profit_price=final_tp,
            tp_target_type=selected.target_type,
            gross_rr=round(final_rr, 2),
            is_eligible=True,
            target_level_price=selected.raw_level,
            candidate_targets=candidates,
        )


class SweepReclaim15MStrategy:
    """Deterministic 15m/1H Dealing Range & Liquidity Sweep-Reclaim Strategy."""

    strategy_id: str = "15M_SWEEP_RECLAIM_SMC"
    is_live: bool = True

    def __init__(
        self,
        risk_cfg: Optional[LiveMicroRiskConfig] = None,
        costs_cfg: Optional[LiveMicroCostsConfig] = None,
        sizing_engine: Optional[LiveMicroSizingEngine] = None,
    ) -> None:
        self.risk_cfg = risk_cfg or LiveMicroRiskConfig()
        self.costs_cfg = costs_cfg or LiveMicroCostsConfig()
        # Ensure costs_cfg accommodates the 1.20% - 2.20% dynamic structural stop distance
        if getattr(self.costs_cfg, "max_stop_distance_pct", 0.0080) < 0.0220:
            self.costs_cfg.max_stop_distance_pct = 0.0220
        if getattr(self.costs_cfg, "min_stop_distance_pct", 0.0040) < 0.0120:
            self.costs_cfg.min_stop_distance_pct = 0.0120
        self.sizing_engine = sizing_engine or LiveMicroSizingEngine(
            risk_cfg=self.risk_cfg,
            costs_cfg=self.costs_cfg,
        )

    def evaluate(
        self,
        symbol: str,
        candles_15m: List[Candle],
        candles_1h: Optional[List[Candle]] = None,
        tick_size: float = 0.01,
        step_size: float = 0.001,
        min_notional: float = 5.0,
        account_equity: float = 16.0,
        funding_rate_est: Optional[float] = None,
        dealing_range_position_pct: Optional[float] = None,
        has_bull_fvg_pullback: bool = False,
        trend_1h: str = "NEUTRAL",
        direction: Optional[str | Direction] = None,
        orderbook_l2: Optional[Dict[str, Any]] = None,
        nearest_ask_wall: Optional[Dict[str, Any] | float] = None,
        nearest_bid_wall: Optional[Dict[str, Any] | float] = None,
        unmitigated_bear_fvg: Optional[Tuple[float, float] | List[float]] = None,
        unmitigated_bull_fvg: Optional[Tuple[float, float] | List[float]] = None,
        dealing_range_bounds: Optional[Tuple[float, float]] = None,
    ) -> Tuple[Optional[SweepReclaim15MSetup], str]:
        """Evaluate recent 15m candle series and dealing range for sweep-reclaim setup."""
        if len(candles_15m) < 20:
            return None, "INSUFFICIENT_DATA: Need at least 20 15m candles"

        target_dir: Optional[str] = None
        if direction is not None:
            target_dir = direction.value if hasattr(direction, "value") else str(direction).strip().upper()
            if target_dir not in ("LONG", "SHORT"):
                target_dir = None

        curr_candle = candles_15m[-1]
        prev_candle = candles_15m[-2]
        current_price = curr_candle.close

        # 1. Compute 48H/72H Dealing Range Position & Bounds
        dr_bounds = dealing_range_bounds
        if dealing_range_position_pct is not None:
            pos_pct = dealing_range_position_pct
            if dr_bounds is None and candles_1h and len(candles_1h) >= 10:
                dr = compute_dealing_range(candles_1h, current_price, lookback_bars=72)
                dr_bounds = (dr.range_low, dr.range_high)
        elif candles_1h and len(candles_1h) >= 10:
            dr = compute_dealing_range(candles_1h, current_price, lookback_bars=72)
            pos_pct = dr.position_pct
            if dr_bounds is None:
                dr_bounds = (dr.range_low, dr.range_high)
        else:
            # Fallback: estimate from 15m candles (up to 288 bars = 72 hours)
            dr_window = candles_15m[-288:] if len(candles_15m) >= 288 else candles_15m
            h_72 = max(c.high for c in dr_window)
            l_72 = min(c.low for c in dr_window)
            span = h_72 - l_72
            pos_pct = ((current_price - l_72) / (span + 1e-12)) * 100.0 if span > 0 else 50.0
            if dr_bounds is None:
                dr_bounds = (l_72, h_72)

        eff_ask_wall = nearest_ask_wall if nearest_ask_wall is not None else (orderbook_l2.get("nearest_ask_wall") if orderbook_l2 else None)
        eff_bid_wall = nearest_bid_wall if nearest_bid_wall is not None else (orderbook_l2.get("nearest_bid_wall") if orderbook_l2 else None)

        # Calculate 15m Indicators
        closes_15m = [c.close for c in candles_15m]
        rsi_series = calc_rsi(closes_15m, period=14)
        curr_rsi = rsi_series[-1]
        atr_15m = calc_atr(candles_15m, period=14)

        # Detect swings on closed historical 15m bars
        swing_highs, swing_lows = find_swings_15m(candles_15m[:-1], left_bars=3, right_bars=2)

        # ---------------------------------------------------------------------
        # 2. Evaluate LONG Setup: Discount Sweep & Reclaim
        # ---------------------------------------------------------------------
        can_long = False
        long_zone_msg = ""
        if target_dir in (None, "LONG"):
            # Gate A: HARD LAW 2 (NO LONGING IN TOP HALF: pos_pct <= 38.0% unless Bull FVG + Bull trend)
            can_long, long_zone_msg = check_dealing_range_zone_lock(
                position_pct=pos_pct,
                direction="LONG",
                has_bull_fvg_pullback=has_bull_fvg_pullback,
                trend_1h=trend_1h,
                long_max_discount_pct=38.0,
            )

            if not can_long and target_dir == "LONG":
                return None, long_zone_msg

            if can_long and swing_lows:
                # Find candidate swing lows in the lookback window (last 5 to 35 bars)
                recent_lows = [s for s in swing_lows if (len(candles_15m) - 1 - s[0]) <= 35]
                if recent_lows:
                    # Find the most relevant support level that was swept
                    # A sweep occurred if the low of curr_candle or prev_candle pierced below swing low,
                    # but curr_candle closed back above it.
                    best_long_sweep = None
                    for idx, sw_price in reversed(recent_lows):
                        # Check if swept recently (within last 3 bars)
                        sweep_window = candles_15m[-3:]
                        min_sweep_low = min(c.low for c in sweep_window)
                        if min_sweep_low < sw_price and curr_candle.close >= sw_price:
                            best_long_sweep = (sw_price, min_sweep_low)
                            break

                    if best_long_sweep is not None:
                        swept_level, sweep_wick_low = best_long_sweep

                        # Check Rejection Wick Ratio on curr or prev bar (WickR >= 0.30)
                        def get_lower_wick_ratio(c: Candle) -> float:
                            tr = c.high - c.low
                            if tr <= 1e-9:
                                return 0.0
                            lower_wick = min(c.open, c.close) - c.low
                            return lower_wick / tr

                        wick_r = max(get_lower_wick_ratio(curr_candle), get_lower_wick_ratio(prev_candle))

                        # Check RSI: oversold or recovering from oversold (<35 or min recent <30)
                        recent_rsi_min = min(rsi_series[-4:]) if len(rsi_series) >= 4 else curr_rsi
                        rsi_recovering = (curr_rsi < 38.0) or (recent_rsi_min < 32.0 and curr_rsi >= recent_rsi_min)

                        if wick_r >= 0.30 and rsi_recovering:
                            # Construct MAKER LIMIT BUY placed at reclaimed support level or slightly below mark
                            raw_entry = min(curr_candle.close, swept_level * 1.0005)
                            entry_price = normalize_price_to_tick(raw_entry, tick_size, rounding="NEAREST")

                            # Evaluate 3-Tier Dynamic Structural Stop-Loss
                            bid_wall_price = eff_bid_wall.get("price") if isinstance(eff_bid_wall, dict) else (float(eff_bid_wall) if eff_bid_wall else None)
                            sl_res = compute_dynamic_structural_sl(
                                entry_price=entry_price,
                                direction=Direction.LONG,
                                candles_15m=candles_15m,
                                candles_1h=candles_1h,
                                sweep_wick_extreme=sweep_wick_low,
                                structural_zone_extreme=swept_level,
                                protecting_wall_price=bid_wall_price,
                                atr_15m=atr_15m,
                                tick_size=tick_size,
                                step_size=step_size,
                                min_qty=step_size,
                                min_risk_budget_usd=self.risk_cfg.fixed_risk_usd,
                                max_risk_cap_usd=0.22,
                            )
                            if not sl_res.is_eligible:
                                return None, sl_res.rejection_reason

                            stop_loss = sl_res.stop_loss_price
                            stop_dist_pct = (entry_price - stop_loss) / entry_price

                            # Dynamic Structural Take-Profit Targeting & Room-to-Resistance
                            tp_res = compute_dynamic_structural_tp(
                                entry_price=entry_price,
                                stop_loss_price=stop_loss,
                                direction=Direction.LONG,
                                candles_15m=candles_15m,
                                candles_1h=candles_1h,
                                dealing_range_bounds=dr_bounds,
                                dealing_range_position_pct=pos_pct,
                                nearest_ask_wall=eff_ask_wall,
                                unmitigated_bear_fvg=unmitigated_bear_fvg,
                                tick_size=tick_size,
                            )
                            if not tp_res.is_eligible:
                                return None, tp_res.rejection_reason

                            take_profit = tp_res.take_profit_price

                            if stop_loss < entry_price < take_profit:
                                sizing = self.sizing_engine.size_position(
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    direction="LONG",
                                    step_size=step_size,
                                    min_notional=min_notional,
                                    account_equity=account_equity,
                                    funding_rate_est=funding_rate_est,
                                )

                                if sizing.is_feasible:
                                    setup = SweepReclaim15MSetup(
                                        symbol=symbol,
                                        direction=Direction.LONG,
                                        order_type="LIMIT",
                                        post_only=True,
                                        entry_price=entry_price,
                                        stop_loss=stop_loss,
                                        take_profit=take_profit,
                                        stop_distance_pct=round(stop_dist_pct * 100, 3),
                                        sweep_level=swept_level,
                                        sweep_wick_extreme=sweep_wick_low,
                                        wick_ratio=wick_r,
                                        rsi_15m=curr_rsi,
                                        dealing_range_position_pct=pos_pct,
                                        sizing=sizing,
                                        trigger_candle_time_ms=curr_candle.close_time_ms,
                                        tp_target_type=tp_res.tp_target_type,
                                        gross_rr=tp_res.gross_rr,
                                        sl_invalidation_type=sl_res.sl_invalidation_type,
                                        planned_risk_usd=sl_res.planned_risk_usd,
                                        reasons=[
                                            f"Dynamic Structural SL: ${sl_res.stop_loss_price:.4f} ({sl_res.sl_invalidation_type}, {sl_res.stop_distance_pct:.2f}% dist, ${sl_res.planned_risk_usd:.2f} risk)",
                                            f"Discount Sweep-Reclaim: Swept ${swept_level:.4f} low (Wick ${sweep_wick_low:.4f})",
                                            f"Rejection Wick: {wick_r * 100:.1f}% >= 30% gate",
                                            f"RSI 15m: {curr_rsi:.1f} (recovering from {recent_rsi_min:.1f})",
                                            f"Dealing Range: {pos_pct:.1f}% <= 38.0% Discount",
                                            f"Dynamic TP: ${tp_res.take_profit_price:.4f} ({tp_res.tp_target_type}, {tp_res.gross_rr:.2f}R)",
                                            f"Net-RR: {sizing.expected_net_rr:.2f} >= 1.50 (Loss: ${sizing.expected_conservative_loss_usd:.2f})",
                                        ],
                                    )
                                    return setup, "SETUP_VALID"
                                else:
                                    return None, f"SIZING_REJECTED: {sizing.rejection_reason}"

            if target_dir == "LONG":
                return None, "NO_SWEEP_RECLAIM_TRIGGER"

        # ---------------------------------------------------------------------
        # 3. Evaluate SHORT Setup: Premium Sweep & Reject
        # ---------------------------------------------------------------------
        can_short = False
        short_zone_msg = ""
        if target_dir in (None, "SHORT"):
            # Gate B: HARD LAW 1 (NO SHORTING IN BOTTOM HALF: pos_pct >= 65.0% Premium requirement)
            can_short, short_zone_msg = check_dealing_range_zone_lock(
                position_pct=pos_pct,
                direction="SHORT",
                short_min_premium_pct=65.0,
            )

            if not can_short and target_dir == "SHORT":
                return None, short_zone_msg

            if can_short and swing_highs:
                recent_highs = [s for s in swing_highs if (len(candles_15m) - 1 - s[0]) <= 35]
                if recent_highs:
                    best_short_sweep = None
                    for idx, sw_price in reversed(recent_highs):
                        sweep_window = candles_15m[-3:]
                        max_sweep_high = max(c.high for c in sweep_window)
                        if max_sweep_high > sw_price and curr_candle.close <= sw_price:
                            best_short_sweep = (sw_price, max_sweep_high)
                            break

                    if best_short_sweep is not None:
                        swept_level, sweep_wick_high = best_short_sweep

                        def get_upper_wick_ratio(c: Candle) -> float:
                            tr = c.high - c.low
                            if tr <= 1e-9:
                                return 0.0
                            upper_wick = c.high - max(c.open, c.close)
                            return upper_wick / tr

                        wick_r = max(get_upper_wick_ratio(curr_candle), get_upper_wick_ratio(prev_candle))

                        recent_rsi_max = max(rsi_series[-4:]) if len(rsi_series) >= 4 else curr_rsi
                        rsi_overbought = (curr_rsi > 62.0) or (recent_rsi_max > 68.0 and curr_rsi <= recent_rsi_max)

                        if wick_r >= 0.30 and rsi_overbought:
                            raw_entry = max(curr_candle.close, swept_level * 0.9995)
                            entry_price = normalize_price_to_tick(raw_entry, tick_size, rounding="NEAREST")

                            # Evaluate 3-Tier Dynamic Structural Stop-Loss
                            ask_wall_price = eff_ask_wall.get("price") if isinstance(eff_ask_wall, dict) else (float(eff_ask_wall) if eff_ask_wall else None)
                            sl_res = compute_dynamic_structural_sl(
                                entry_price=entry_price,
                                direction=Direction.SHORT,
                                candles_15m=candles_15m,
                                candles_1h=candles_1h,
                                sweep_wick_extreme=sweep_wick_high,
                                structural_zone_extreme=swept_level,
                                protecting_wall_price=ask_wall_price,
                                atr_15m=atr_15m,
                                tick_size=tick_size,
                                step_size=step_size,
                                min_qty=step_size,
                                min_risk_budget_usd=self.risk_cfg.fixed_risk_usd,
                                max_risk_cap_usd=0.22,
                            )
                            if not sl_res.is_eligible:
                                return None, sl_res.rejection_reason

                            stop_loss = sl_res.stop_loss_price
                            stop_dist_pct = (stop_loss - entry_price) / entry_price

                            # Dynamic Structural Take-Profit Targeting & Room-to-Resistance
                            tp_res = compute_dynamic_structural_tp(
                                entry_price=entry_price,
                                stop_loss_price=stop_loss,
                                direction=Direction.SHORT,
                                candles_15m=candles_15m,
                                candles_1h=candles_1h,
                                dealing_range_bounds=dr_bounds,
                                dealing_range_position_pct=pos_pct,
                                nearest_bid_wall=eff_bid_wall,
                                unmitigated_bull_fvg=unmitigated_bull_fvg,
                                tick_size=tick_size,
                            )
                            if not tp_res.is_eligible:
                                return None, tp_res.rejection_reason

                            take_profit = tp_res.take_profit_price

                            if stop_loss > entry_price > take_profit:
                                sizing = self.sizing_engine.size_position(
                                    entry_price=entry_price,
                                    stop_loss=stop_loss,
                                    take_profit=take_profit,
                                    direction="SHORT",
                                    step_size=step_size,
                                    min_notional=min_notional,
                                    account_equity=account_equity,
                                    funding_rate_est=funding_rate_est,
                                )

                                if sizing.is_feasible:
                                    setup = SweepReclaim15MSetup(
                                        symbol=symbol,
                                        direction=Direction.SHORT,
                                        order_type="LIMIT",
                                        post_only=True,
                                        entry_price=entry_price,
                                        stop_loss=stop_loss,
                                        take_profit=take_profit,
                                        stop_distance_pct=round(stop_dist_pct * 100, 3),
                                        sweep_level=swept_level,
                                        sweep_wick_extreme=sweep_wick_high,
                                        wick_ratio=wick_r,
                                        rsi_15m=curr_rsi,
                                        dealing_range_position_pct=pos_pct,
                                        sizing=sizing,
                                        trigger_candle_time_ms=curr_candle.close_time_ms,
                                        tp_target_type=tp_res.tp_target_type,
                                        gross_rr=tp_res.gross_rr,
                                        sl_invalidation_type=sl_res.sl_invalidation_type,
                                        planned_risk_usd=sl_res.planned_risk_usd,
                                        reasons=[
                                            f"Dynamic Structural SL: ${sl_res.stop_loss_price:.4f} ({sl_res.sl_invalidation_type}, {sl_res.stop_distance_pct:.2f}% dist, ${sl_res.planned_risk_usd:.2f} risk)",
                                            f"Premium Sweep-Reject: Swept ${swept_level:.4f} high (Wick ${sweep_wick_high:.4f})",
                                            f"Upper Rejection Wick: {wick_r * 100:.1f}% >= 30% gate",
                                            f"RSI 15m: {curr_rsi:.1f} (turning down from {recent_rsi_max:.1f})",
                                            f"Dealing Range: {pos_pct:.1f}% >= 65.0% Premium",
                                            f"Dynamic TP: ${tp_res.take_profit_price:.4f} ({tp_res.tp_target_type}, {tp_res.gross_rr:.2f}R)",
                                            f"Net-RR: {sizing.expected_net_rr:.2f} >= 1.50 (Loss: ${sizing.expected_conservative_loss_usd:.2f})",
                                        ],
                                    )
                                    return setup, "SETUP_VALID"
                                else:
                                    return None, f"SIZING_REJECTED: {sizing.rejection_reason}"

            if target_dir == "SHORT":
                return None, "NO_SWEEP_REJECT_TRIGGER"

        # Bidirectional scan fallthrough (target_dir is None)
        if not can_long and not can_short:
            return None, f"ZONE_LOCK_RESTRICTION: Equilibrium position ({pos_pct:.1f}%) not eligible for Long (<=38%) or Short (>=65%)"
        if can_long and not can_short:
            return None, "NO_SWEEP_RECLAIM_TRIGGER"
        if can_short and not can_long:
            return None, "NO_SWEEP_REJECT_TRIGGER"

        return None, "NO_SWEEP_RECLAIM_TRIGGER"
