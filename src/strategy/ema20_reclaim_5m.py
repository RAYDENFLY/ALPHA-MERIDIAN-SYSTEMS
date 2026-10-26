"""Strategy B: EMA20_RECLAIM_5M_V1 (5m EMA20 Reclaim-Pullback).

Primary Live Strategy Candidate.
Deterministic rules:
1. Trigger timeframe: 5m candles.
2. Prior pullback: Price dips below 5m EMA20 (for LONG) or rallies above 5m EMA20 (for SHORT).
3. Reclaim confirmation: Current 5m closed bar closes back across EMA20 (close > EMA20 for LONG, close < EMA20 for SHORT).
4. Structural Stop Loss: Lowest low of pullback bars (LONG) or highest high of pullback bars (SHORT).
5. Stop distance constraint: 0.40% <= stop_distance <= 0.80%. Rejects if outside.
6. Target calculation: Sized to satisfy Expected Net-RR >= 1.50 under conservative cost model.
7. Conservative sizing: Loss at stop <= $0.20 USD fixed risk.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from src.core.types import Candle, Direction
from src.risk.live_micro_sizing import LiveMicroSizingEngine, LiveMicroSizingResult, normalize_price_to_tick
from config.settings import LiveMicroCostsConfig, LiveMicroRiskConfig


@dataclass
class EMA20ReclaimSetup:
    """Deterministic signal and candidate parameters for EMA20_RECLAIM_5M_V1."""
    strategy_id: str = "EMA20_RECLAIM_5M_V1"
    config_version: str = "LIVE_MICRO_V1"
    symbol: str = ""
    direction: Direction = Direction.LONG
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    stop_distance_pct: float = 0.0
    sizing: Optional[LiveMicroSizingResult] = None
    trigger_candle_time_ms: int = 0
    ema20_val: float = 0.0
    pullback_bars_count: int = 0
    reasons: List[str] = field(default_factory=list)
    rejection_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "config_version": self.config_version,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "stop_distance_pct": self.stop_distance_pct,
            "quantity": self.sizing.quantity if self.sizing else 0.0,
            "notional_usd": self.sizing.notional_usd if self.sizing else 0.0,
            "isolated_margin_usd": self.sizing.isolated_margin_usd if self.sizing else 0.0,
            "expected_net_rr": self.sizing.expected_net_rr if self.sizing else 0.0,
            "conservative_loss_usd": self.sizing.expected_conservative_loss_usd if self.sizing else 0.0,
            "trigger_candle_time_ms": self.trigger_candle_time_ms,
            "ema20_val": self.ema20_val,
            "reasons": self.reasons,
        }


def calc_ema(values: List[float], period: int = 20) -> List[float]:
    """Calculate Exponential Moving Average matching input length exactly."""
    n = len(values)
    if n == 0:
        return []
    multiplier = 2.0 / (period + 1)
    ema_vals = [values[0]]
    for i in range(1, n):
        if i < period - 1:
            ema_vals.append(sum(values[: i + 1]) / (i + 1))
        elif i == period - 1:
            ema_vals.append(sum(values[:period]) / period)
        else:
            ema_vals.append((values[i] - ema_vals[-1]) * multiplier + ema_vals[-1])
    return ema_vals


class EMA20ReclaimStrategy:
    """Deterministic 5m EMA20 Reclaim-Pullback Strategy Evaluator."""

    strategy_id: str = "EMA20_RECLAIM_5M_V1"
    is_live: bool = True

    def __init__(
        self,
        risk_cfg: Optional[LiveMicroRiskConfig] = None,
        costs_cfg: Optional[LiveMicroCostsConfig] = None,
        sizing_engine: Optional[LiveMicroSizingEngine] = None,
    ) -> None:
        self.risk_cfg = risk_cfg or LiveMicroRiskConfig()
        self.costs_cfg = costs_cfg or LiveMicroCostsConfig()
        self.sizing_engine = sizing_engine or LiveMicroSizingEngine(
            risk_cfg=self.risk_cfg,
            costs_cfg=self.costs_cfg,
        )

    def evaluate(
        self,
        symbol: str,
        candles_5m: List[Candle],
        tick_size: float = 0.01,
        step_size: float = 0.001,
        min_notional: float = 5.0,
        account_equity: float = 16.0,
        funding_rate_est: Optional[float] = None,
        htf_trend: Optional[str] = None,  # Optional 1H or 15M trend filter
    ) -> Tuple[Optional[EMA20ReclaimSetup], str]:
        """Evaluate recent 5m candle series for EMA20 reclaim setup."""
        if len(candles_5m) < 25:
            return None, "INSUFFICIENT_DATA: Need at least 25 5m candles"

        closes = [c.close for c in candles_5m]
        ema20_series = calc_ema(closes, period=20)
        if not ema20_series or len(ema20_series) < 5:
            return None, "EMA_CALCULATION_FAILED: Insufficient EMA bars"

        curr_candle = candles_5m[-1]
        curr_ema20 = ema20_series[-1]
        prev_candle = candles_5m[-2]
        prev_ema20 = ema20_series[-2]

        # ---------------------------------------------------------------------
        # 1. Evaluate LONG Reclaim
        # Condition:
        # - Prior pullback below EMA20 (within last 2 to 6 bars: low < EMA20 or close < EMA20)
        # - Reclaim bar: current candle closed firmly above EMA20 (close > curr_ema20 and close > open)
        # ---------------------------------------------------------------------
        can_eval_long = htf_trend is None or htf_trend.upper() in ("BULLISH", "NEUTRAL", "SIDEWAYS")
        if can_eval_long and curr_candle.close > curr_ema20 and curr_candle.close > curr_candle.open:
            # Check if there was a pullback below EMA20 in the preceding 2 to 6 bars
            pullback_bars = []
            lookback = min(len(candles_5m) - 1, 6)
            for j in range(2, lookback + 1):
                c = candles_5m[-j]
                ema_val = ema20_series[-j]
                if c.low < ema_val or c.close < ema_val:
                    pullback_bars.append(c)

            if pullback_bars:
                # Lowest low of the pullback below EMA20
                raw_pullback_low = min(c.low for c in pullback_bars)
                raw_entry_price = curr_candle.close

                # Deterministic tick-size normalization
                entry_price = normalize_price_to_tick(raw_entry_price, tick_size, rounding="NEAREST")
                stop_loss = normalize_price_to_tick(raw_pullback_low, tick_size, rounding="FLOOR")

                if stop_loss >= entry_price:
                    return None, f"INVALID_STOP_PRICE: Normalized stop {stop_loss} >= entry {entry_price} for LONG"

                stop_dist = entry_price - stop_loss
                if stop_dist > 0:
                    stop_dist_pct = stop_dist / entry_price
                    # Check stop bounds [0.40%, 0.80%]
                    min_stop = self.costs_cfg.min_stop_distance_pct
                    max_stop = self.costs_cfg.max_stop_distance_pct

                    if min_stop <= stop_dist_pct <= max_stop:
                        # Set Target Price: Default 2.25x gross RR
                        target_dist = stop_dist * 2.25
                        raw_tp = entry_price + target_dist
                        take_profit = normalize_price_to_tick(raw_tp, tick_size, rounding="NEAREST")

                        if take_profit <= entry_price:
                            return None, f"INVALID_TAKE_PROFIT: Normalized TP {take_profit} <= entry {entry_price} for LONG"

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
                            setup = EMA20ReclaimSetup(
                                symbol=symbol,
                                direction=Direction.LONG,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                stop_distance_pct=round(stop_dist_pct * 100, 3),
                                sizing=sizing,
                                trigger_candle_time_ms=curr_candle.close_time_ms,
                                ema20_val=curr_ema20,
                                pullback_bars_count=len(pullback_bars),
                                reasons=[
                                    f"5m EMA20 Reclaim: Close ${entry_price:.4f} > EMA20 ${curr_ema20:.4f}",
                                    f"Pullback Low: ${stop_loss:.4f} ({stop_dist_pct * 100:.2f}% distance)",
                                    f"Net-RR: {sizing.expected_net_rr:.2f} >= 1.50 gate (Loss: ${sizing.expected_conservative_loss_usd:.2f})",
                                ],
                            )
                            return setup, "SETUP_VALID"
                        else:
                            return None, f"SIZING_REJECTED: {sizing.rejection_reason}"
                    else:
                        return None, (
                            f"STOP_DISTANCE_OUT_OF_BOUNDS: Stop distance {stop_dist_pct * 100:.3f}% "
                            f"not in [{min_stop * 100:.2f}%, {max_stop * 100:.2f}%]"
                        )

        # ---------------------------------------------------------------------
        # 2. Evaluate SHORT Reclaim
        # Condition:
        # - Prior rally above EMA20 (within last 2 to 6 bars: high > EMA20 or close > EMA20)
        # - Reclaim bar: current candle closed firmly below EMA20 (close < curr_ema20 and close < open)
        # ---------------------------------------------------------------------
        can_eval_short = htf_trend is None or htf_trend.upper() in ("BEARISH", "NEUTRAL", "SIDEWAYS")
        if can_eval_short and curr_candle.close < curr_ema20 and curr_candle.close < curr_candle.open:
            pullback_bars = []
            lookback = min(len(candles_5m) - 1, 6)
            for j in range(2, lookback + 1):
                c = candles_5m[-j]
                ema_val = ema20_series[-j]
                if c.high > ema_val or c.close > ema_val:
                    pullback_bars.append(c)

            if pullback_bars:
                raw_pullback_high = max(c.high for c in pullback_bars)
                raw_entry_price = curr_candle.close

                # Deterministic tick-size normalization
                entry_price = normalize_price_to_tick(raw_entry_price, tick_size, rounding="NEAREST")
                stop_loss = normalize_price_to_tick(raw_pullback_high, tick_size, rounding="CEIL")

                if stop_loss <= entry_price:
                    return None, f"INVALID_STOP_PRICE: Normalized stop {stop_loss} <= entry {entry_price} for SHORT"

                stop_dist = stop_loss - entry_price
                if stop_dist > 0:
                    stop_dist_pct = stop_dist / entry_price
                    min_stop = self.costs_cfg.min_stop_distance_pct
                    max_stop = self.costs_cfg.max_stop_distance_pct

                    if min_stop <= stop_dist_pct <= max_stop:
                        target_dist = stop_dist * 2.25
                        raw_tp = entry_price - target_dist
                        take_profit = normalize_price_to_tick(raw_tp, tick_size, rounding="NEAREST")

                        if take_profit >= entry_price:
                            return None, f"INVALID_TAKE_PROFIT: Normalized TP {take_profit} >= entry {entry_price} for SHORT"

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
                            setup = EMA20ReclaimSetup(
                                symbol=symbol,
                                direction=Direction.SHORT,
                                entry_price=entry_price,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                stop_distance_pct=round(stop_dist_pct * 100, 3),
                                sizing=sizing,
                                trigger_candle_time_ms=curr_candle.close_time_ms,
                                ema20_val=curr_ema20,
                                pullback_bars_count=len(pullback_bars),
                                reasons=[
                                    f"5m EMA20 Short Reclaim: Close ${entry_price:.4f} < EMA20 ${curr_ema20:.4f}",
                                    f"Pullback High: ${stop_loss:.4f} ({stop_dist_pct * 100:.2f}% distance)",
                                    f"Net-RR: {sizing.expected_net_rr:.2f} >= 1.50 gate (Loss: ${sizing.expected_conservative_loss_usd:.2f})",
                                ],
                            )
                            return setup, "SETUP_VALID"
                        else:
                            return None, f"SIZING_REJECTED: {sizing.rejection_reason}"
                    else:
                        return None, (
                            f"STOP_DISTANCE_OUT_OF_BOUNDS: Stop distance {stop_dist_pct * 100:.3f}% "
                            f"not in [{min_stop * 100:.2f}%, {max_stop * 100:.2f}%]"
                        )

        return None, "NO_RECLAIM_TRIGGER"
