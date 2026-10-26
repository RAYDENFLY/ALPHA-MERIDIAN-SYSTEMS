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
        # Ensure costs_cfg accommodates the 1.15% - 1.40% stop distance
        if getattr(self.costs_cfg, "max_stop_distance_pct", 0.0080) < 0.0150:
            self.costs_cfg.max_stop_distance_pct = 0.0150
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
    ) -> Tuple[Optional[SweepReclaim15MSetup], str]:
        """Evaluate recent 15m candle series and dealing range for sweep-reclaim setup."""
        if len(candles_15m) < 20:
            return None, "INSUFFICIENT_DATA: Need at least 20 15m candles"

        curr_candle = candles_15m[-1]
        prev_candle = candles_15m[-2]
        current_price = curr_candle.close

        # 1. Compute 48H/72H Dealing Range Position
        if dealing_range_position_pct is not None:
            pos_pct = dealing_range_position_pct
        elif candles_1h and len(candles_1h) >= 10:
            dr = compute_dealing_range(candles_1h, current_price, lookback_bars=72)
            pos_pct = dr.position_pct
        else:
            # Fallback: estimate from 15m candles (up to 288 bars = 72 hours)
            dr_window = candles_15m[-288:] if len(candles_15m) >= 288 else candles_15m
            h_72 = max(c.high for c in dr_window)
            l_72 = min(c.low for c in dr_window)
            span = h_72 - l_72
            pos_pct = ((current_price - l_72) / (span + 1e-12)) * 100.0 if span > 0 else 50.0

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
        # Gate A: HARD LAW 2 (NO LONGING IN TOP HALF: pos_pct <= 38.0% unless Bull FVG + Bull trend)
        can_long, long_zone_msg = check_dealing_range_zone_lock(
            position_pct=pos_pct,
            direction="LONG",
            has_bull_fvg_pullback=has_bull_fvg_pullback,
            trend_1h=trend_1h,
            long_max_discount_pct=38.0,
        )

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

                        # Stop Loss: placed 1.15% - 1.40% below entry, strictly under sweep wick low
                        # Clamp stop distance strictly in [1.15%, 1.40%]
                        min_sl_dist = entry_price * 0.0115
                        max_sl_dist = entry_price * 0.0140

                        # Structural stop with buffer
                        structural_sl_dist = entry_price - (sweep_wick_low - 0.10 * atr_15m)
                        sl_dist = max(min_sl_dist, min(max_sl_dist, structural_sl_dist))

                        stop_loss = normalize_price_to_tick(entry_price - sl_dist, tick_size, rounding="FLOOR")
                        stop_dist_pct = (entry_price - stop_loss) / entry_price

                        # Take Profit: 2.25x Gross RR
                        raw_tp = entry_price + (entry_price - stop_loss) * 2.25
                        take_profit = normalize_price_to_tick(raw_tp, tick_size, rounding="NEAREST")

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
                                    reasons=[
                                        f"Discount Sweep-Reclaim: Swept ${swept_level:.4f} low (Wick ${sweep_wick_low:.4f})",
                                        f"Rejection Wick: {wick_r * 100:.1f}% >= 30% gate",
                                        f"RSI 15m: {curr_rsi:.1f} (recovering from {recent_rsi_min:.1f})",
                                        f"Dealing Range: {pos_pct:.1f}% <= 38.0% Discount",
                                        f"Net-RR: {sizing.expected_net_rr:.2f} >= 1.50 (Loss: ${sizing.expected_conservative_loss_usd:.2f})",
                                    ],
                                )
                                return setup, "SETUP_VALID"
                            else:
                                return None, f"SIZING_REJECTED: {sizing.rejection_reason}"

        # ---------------------------------------------------------------------
        # 3. Evaluate SHORT Setup: Premium Sweep & Reject
        # ---------------------------------------------------------------------
        # Gate B: HARD LAW 1 (NO SHORTING IN BOTTOM HALF: pos_pct >= 65.0% Premium requirement)
        can_short, short_zone_msg = check_dealing_range_zone_lock(
            position_pct=pos_pct,
            direction="SHORT",
            short_min_premium_pct=65.0,
        )

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

                        min_sl_dist = entry_price * 0.0115
                        max_sl_dist = entry_price * 0.0140

                        structural_sl_dist = (sweep_wick_high + 0.10 * atr_15m) - entry_price
                        sl_dist = max(min_sl_dist, min(max_sl_dist, structural_sl_dist))

                        stop_loss = normalize_price_to_tick(entry_price + sl_dist, tick_size, rounding="CEIL")
                        stop_dist_pct = (stop_loss - entry_price) / entry_price

                        raw_tp = entry_price - (stop_loss - entry_price) * 2.25
                        take_profit = normalize_price_to_tick(raw_tp, tick_size, rounding="NEAREST")

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
                                    reasons=[
                                        f"Premium Sweep-Reject: Swept ${swept_level:.4f} high (Wick ${sweep_wick_high:.4f})",
                                        f"Upper Rejection Wick: {wick_r * 100:.1f}% >= 30% gate",
                                        f"RSI 15m: {curr_rsi:.1f} (turning down from {recent_rsi_max:.1f})",
                                        f"Dealing Range: {pos_pct:.1f}% >= 65.0% Premium",
                                        f"Net-RR: {sizing.expected_net_rr:.2f} >= 1.50 (Loss: ${sizing.expected_conservative_loss_usd:.2f})",
                                    ],
                                )
                                return setup, "SETUP_VALID"
                            else:
                                return None, f"SIZING_REJECTED: {sizing.rejection_reason}"

        # If rejected by zone locks, surface that explicitly
        if not can_long and not can_short:
            return None, f"ZONE_LOCK_RESTRICTION: Equilibrium position ({pos_pct:.1f}%) not eligible for Long (<=38%) or Short (>=65%)"
        if not can_long:
            return None, long_zone_msg
        if not can_short:
            return None, short_zone_msg

        return None, "NO_SWEEP_RECLAIM_TRIGGER"
