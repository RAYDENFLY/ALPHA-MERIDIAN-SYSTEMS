"""Strategy A: SWEEP_1M_V1 (1m Sweep and Reclaim).

Shadow-Only Counterfactual Strategy.
CRITICAL CONSTRAINT: This strategy is SHADOW-ONLY. It must NEVER submit live orders.

Deterministic rules:
1. Trigger timeframe: 1m candles.
2. Identifies structural swing highs and lows with confirmed left/right bars.
3. Liquidity Sweep: Wick penetrates beyond the swing level.
4. Immediate Reclaim: Bar closes back across the swing level.
5. Stop Loss: Set at the sweep wick extreme (with minimal buffer).
6. Target: Opposite liquidity pool / swing level.
7. Counterfactual Tracking:
   - Tracks whether hypothetical limit entry filled within TTL bars.
   - Evaluates 'MISS': Price reaches +0.5R in TP direction BEFORE entry fills.
   - Evaluates 'EXPIRED': TTL elapses without fill and without +0.5R.
   - Evaluates 'FILLED': Tracks whether TP (+R) or SL (-1R) is hit subsequently.
   - Evaluates whether Global Position Lock would have blocked the trade.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from src.core.types import Candle, Direction
from src.risk.global_lock import GlobalPositionLock
from src.risk.live_micro_sizing import LiveMicroSizingEngine, LiveMicroSizingResult
from config.settings import LiveMicroCostsConfig, LiveMicroRiskConfig


@dataclass
class Sweep1MSetup:
    """Hypothetical setup produced by SWEEP_1M_V1 for shadow/counterfactual evaluation."""
    strategy_id: str = "SWEEP_1M_V1"
    config_version: str = "LIVE_MICRO_V1"
    symbol: str = ""
    direction: Direction = Direction.LONG
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    stop_distance_pct: float = 0.0
    ttl_bars: int = 10
    trigger_candle_time_ms: int = 0
    swept_level: float = 0.0
    sweep_wick_extreme: float = 0.0
    sizing: Optional[LiveMicroSizingResult] = None
    reasons: List[str] = field(default_factory=list)
    
    # Counterfactual outcomes
    is_live_authorized: bool = False  # Strictly False by invariant
    blocked_by_global_lock: bool = False
    fill_status: str = "PENDING"  # PENDING, FILLED, MISS, EXPIRED
    counterfactual_r: float = 0.0
    price_path: List[float] = field(default_factory=list)
    outcome_summary: str = ""

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
            "ttl_bars": self.ttl_bars,
            "trigger_candle_time_ms": self.trigger_candle_time_ms,
            "swept_level": self.swept_level,
            "sweep_wick_extreme": self.sweep_wick_extreme,
            "quantity": self.sizing.quantity if self.sizing else 0.0,
            "notional_usd": self.sizing.notional_usd if self.sizing else 0.0,
            "expected_net_rr": self.sizing.expected_net_rr if self.sizing else 0.0,
            "is_live_authorized": self.is_live_authorized,
            "blocked_by_global_lock": self.blocked_by_global_lock,
            "fill_status": self.fill_status,
            "counterfactual_r": self.counterfactual_r,
            "outcome_summary": self.outcome_summary,
            "reasons": self.reasons,
        }


def find_swings_1m(
    candles: List[Candle],
    left_bars: int = 3,
    right_bars: int = 2,
) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Find confirmed swing highs and lows in 1m candles."""
    n = len(candles)
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []

    if n < left_bars + right_bars + 1:
        return swing_highs, swing_lows

    for i in range(left_bars, n - right_bars):
        c = candles[i]
        # Check swing high
        if all(candles[j].high <= c.high for j in range(i - left_bars, i + right_bars + 1) if j != i):
            swing_highs.append((i, c.high))
        # Check swing low
        if all(candles[j].low >= c.low for j in range(i - left_bars, i + right_bars + 1) if j != i):
            swing_lows.append((i, c.low))

    return swing_highs, swing_lows


class Sweep1MStrategy:
    """Deterministic 1m Sweep & Reclaim Shadow Strategy Evaluator."""

    strategy_id: str = "SWEEP_1M_V1"
    is_live: bool = False

    def __init__(
        self,
        risk_cfg: Optional[LiveMicroRiskConfig] = None,
        costs_cfg: Optional[LiveMicroCostsConfig] = None,
        sizing_engine: Optional[LiveMicroSizingEngine] = None,
        ttl_bars: int = 10,
    ) -> None:
        self.risk_cfg = risk_cfg or LiveMicroRiskConfig()
        self.costs_cfg = costs_cfg or LiveMicroCostsConfig()
        self.sizing_engine = sizing_engine or LiveMicroSizingEngine(
            risk_cfg=self.risk_cfg,
            costs_cfg=self.costs_cfg,
        )
        self.ttl_bars = ttl_bars
        self.global_lock = GlobalPositionLock()

    def evaluate(
        self,
        symbol: str,
        candles_1m: List[Candle],
        step_size: float = 0.001,
        min_notional: float = 5.0,
        account_equity: float = 16.0,
        funding_rate_est: Optional[float] = None,
    ) -> Tuple[Optional[Sweep1MSetup], str]:
        """Evaluate 1m candles for a sweep & reclaim pattern (SHADOW-ONLY)."""
        if len(candles_1m) < 20:
            return None, "INSUFFICIENT_DATA: Need at least 20 1m candles"

        curr_candle = candles_1m[-1]
        prior_candles = candles_1m[:-1]
        swing_highs, swing_lows = find_swings_1m(prior_candles, left_bars=3, right_bars=2)

        # ---------------------------------------------------------------------
        # 1. Evaluate LONG Sweep-Reclaim
        # Condition:
        # - Candle sweeps below a recent confirmed swing low: curr_candle.low < key_low
        # - Candle reclaims above the swing low: curr_candle.close > key_low and close > open
        # ---------------------------------------------------------------------
        if swing_lows:
            # Find the most recent unviolated swing low within the last 15 bars
            for s_idx, key_low in reversed(swing_lows[-4:]):
                if curr_candle.low < key_low and curr_candle.close > key_low and curr_candle.close > curr_candle.open:
                    entry_price = key_low  # Hypothetical limit entry at swept level
                    sweep_wick = curr_candle.low
                    stop_loss = sweep_wick * 0.9995  # 0.05% buffer below sweep low
                    stop_dist = entry_price - stop_loss

                    if stop_dist > 0:
                        stop_dist_pct = stop_dist / entry_price
                        # Estimate TP at 2.5R to measure counterfactual net expectancy
                        take_profit = entry_price + (stop_dist * 2.5)

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

                        blocked = self.global_lock.check_counterfactual_block("SWEEP_1M_V1", symbol)
                        setup = Sweep1MSetup(
                            symbol=symbol,
                            direction=Direction.LONG,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            stop_distance_pct=round(stop_dist_pct * 100, 3),
                            ttl_bars=self.ttl_bars,
                            trigger_candle_time_ms=curr_candle.close_time_ms,
                            swept_level=key_low,
                            sweep_wick_extreme=sweep_wick,
                            sizing=sizing,
                            is_live_authorized=False,  # Invariant: Never live
                            blocked_by_global_lock=blocked,
                            reasons=[
                                f"1m Long Sweep-Reclaim: Low ${sweep_wick:.4f} < Level ${key_low:.4f}, Close ${curr_candle.close:.4f}",
                                f"Shadow-only setup: Blocked by global lock = {blocked}",
                            ],
                        )
                        return setup, "SHADOW_SETUP_GENERATED"

        # ---------------------------------------------------------------------
        # 2. Evaluate SHORT Sweep-Reclaim
        # Condition:
        # - Candle sweeps above a recent confirmed swing high: curr_candle.high > key_high
        # - Candle reclaims below the swing high: curr_candle.close < key_high and close < open
        # ---------------------------------------------------------------------
        if swing_highs:
            for s_idx, key_high in reversed(swing_highs[-4:]):
                if curr_candle.high > key_high and curr_candle.close < key_high and curr_candle.close < curr_candle.open:
                    entry_price = key_high
                    sweep_wick = curr_candle.high
                    stop_loss = sweep_wick * 1.0005
                    stop_dist = stop_loss - entry_price

                    if stop_dist > 0:
                        stop_dist_pct = stop_dist / entry_price
                        take_profit = entry_price - (stop_dist * 2.5)

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

                        blocked = self.global_lock.check_counterfactual_block("SWEEP_1M_V1", symbol)
                        setup = Sweep1MSetup(
                            symbol=symbol,
                            direction=Direction.SHORT,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            stop_distance_pct=round(stop_dist_pct * 100, 3),
                            ttl_bars=self.ttl_bars,
                            trigger_candle_time_ms=curr_candle.close_time_ms,
                            swept_level=key_high,
                            sweep_wick_extreme=sweep_wick,
                            sizing=sizing,
                            is_live_authorized=False,
                            blocked_by_global_lock=blocked,
                            reasons=[
                                f"1m Short Sweep-Reclaim: High ${sweep_wick:.4f} > Level ${key_high:.4f}, Close ${curr_candle.close:.4f}",
                                f"Shadow-only setup: Blocked by global lock = {blocked}",
                            ],
                        )
                        return setup, "SHADOW_SETUP_GENERATED"

        return None, "NO_SWEEP_RECLAIM_TRIGGER"

    def evaluate_counterfactual_outcome(
        self,
        setup: Sweep1MSetup,
        forward_candles_1m: List[Candle],
    ) -> Sweep1MSetup:
        """
        Evaluate hypothetical execution outcome across subsequent 1m forward candles:
        - Check if entry limit filled within TTL bars.
        - Check 'MISS': Reached +0.5R in TP direction BEFORE entry filled.
        - Check 'EXPIRED': TTL elapsed without fill and without +0.5R.
        - Check 'FILLED': Realized R (+R if TP hit, -1R if SL hit).
        """
        is_long = setup.direction == Direction.LONG
        entry = setup.entry_price
        sl = setup.stop_loss
        tp = setup.take_profit
        r_dist = abs(entry - sl)
        half_r_level = entry + (0.5 * r_dist) if is_long else entry - (0.5 * r_dist)

        filled = False
        fill_bar_idx = -1
        path = []

        # 1. Check entry fill or MISS across forward candles within TTL
        eval_window = forward_candles_1m[: setup.ttl_bars]
        for idx, bar in enumerate(eval_window):
            path.append(bar.close)

            # Check MISS condition before fill: did price reach +0.5R before entry was filled?
            if not filled:
                missed = (bar.high >= half_r_level) if is_long else (bar.low <= half_r_level)
                can_fill = (bar.low <= entry <= bar.high)

                if missed and not can_fill:
                    setup.fill_status = "MISS"
                    setup.counterfactual_r = 0.5
                    setup.price_path = path
                    setup.outcome_summary = f"MISS: Price reached +0.5R (${half_r_level:.4f}) at bar +{idx+1} before entry limit fill"
                    return setup

                if can_fill:
                    filled = True
                    fill_bar_idx = idx

        if not filled:
            setup.fill_status = "EXPIRED"
            setup.counterfactual_r = 0.0
            setup.price_path = path
            setup.outcome_summary = f"EXPIRED: TTL of {setup.ttl_bars} 1m bars elapsed without fill or +0.5R move"
            return setup

        # 2. If filled, evaluate subsequent outcome from fill_bar onward
        setup.fill_status = "FILLED"
        post_fill_candles = forward_candles_1m[fill_bar_idx:]
        for idx, bar in enumerate(post_fill_candles):
            path.append(bar.close)
            hit_tp = (bar.high >= tp) if is_long else (bar.low <= tp)
            hit_sl = (bar.low <= sl) if is_long else (bar.high >= sl)

            if hit_sl and hit_tp:
                # Conservative tie-breaker: stop hit first
                setup.counterfactual_r = -1.0
                setup.outcome_summary = f"CLOSED_SL: Stop loss touched at bar +{idx+1} (intra-bar collision)"
                setup.price_path = path
                return setup
            elif hit_sl:
                setup.counterfactual_r = -1.0
                setup.outcome_summary = f"CLOSED_SL: Stop loss hit at bar +{idx+1} (-1.0R)"
                setup.price_path = path
                return setup
            elif hit_tp:
                r_multiple = abs(tp - entry) / r_dist if r_dist > 0 else 2.5
                setup.counterfactual_r = round(r_multiple, 2)
                setup.outcome_summary = f"CLOSED_TP: Take profit hit at bar +{idx+1} (+{setup.counterfactual_r}R)"
                setup.price_path = path
                return setup

        # Forward candles ended before TP or SL hit
        last_close = forward_candles_1m[-1].close if forward_candles_1m else entry
        unrealized_r = (last_close - entry) / r_dist if is_long else (entry - last_close) / r_dist
        setup.counterfactual_r = round(unrealized_r, 2)
        setup.outcome_summary = f"IN_PROGRESS: Mark ${last_close:.4f} at forward horizon ({setup.counterfactual_r:+.2f}R)"
        setup.price_path = path
        return setup
