"""Candidate Setup Generator.

Integrates Multi-Timeframe Bias, All-Weather Regime Adaptation (Variance Ratio & 7D Macro Trend),
SMC Dealing Range (Premium/Discount & Bullish BOS Expansion),
SMC Patterns (Order Blocks, FVGs, Liquidity Sweeps, BOS/CHoCH),
Support & Resistance, and ATR-Bounded TP/SL Planning.
Supports NO TRADE when high-expectancy confluence requirements are not satisfied.
"""

from typing import List, Optional
import numpy as np
from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    FairValueGap,
    MarketBias,
    OrderBlock,
    SetupCandidate,
    SMCEvent,
    SMCEventType,
    StructureState,
    SwingPoint,
    SwingType,
)
from src.smc.fvg import FVGDetector
from src.smc.liquidity import LiquiditySweepDetector
from src.smc.order_blocks import OrderBlockDetector
from src.smc.structure import MarketStructureEngine
from src.smc.swings import detect_swings, get_latest_confirmed_swings
from src.snr.interaction import SnRInteractionEngine
from src.snr.zones import cluster_swing_zones
from src.strategy.bias import MultiTimeframeBiasEngine
from src.strategy.classifier import classify_entry_category
from src.strategy.explainer import SetupExplainer
from src.strategy.planner import TPSLPlanner


def compute_atr(candles: List[Candle], period: int = 14) -> float:
    """Compute Average True Range for recent candles."""
    if len(candles) < 2:
        return 0.01 * candles[-1].close if candles else 1.0
    tr_list = []
    for i in range(1, len(candles)):
        h = candles[i].high
        l = candles[i].low
        prev_c = candles[i - 1].close
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
    recent_tr = tr_list[-period:] if len(tr_list) >= period else tr_list
    return float(np.mean(recent_tr)) if recent_tr else 0.01 * candles[-1].close


class SetupGenerator:
    """Generates all-weather candidate trading setups from SMC Dealing Range, Regime, & SnR rules."""

    def __init__(
        self,
        swing_left_bars: int = 3,
        swing_right_bars: int = 3,
        atr_period: int = 14,
        sl_buffer_atr_multiple: float = 0.5,
        min_reward_to_risk: float = 1.5,
        fvg_min_size_ratio: float = 0.1,
        sr_cluster_tolerance_pct: float = 0.015,
        premium_threshold: float = 0.88,
        discount_threshold: float = 0.12,
        min_extension_48h: float = 0.062,
        min_impulse_12h: float = 0.022,
        short_macro_cap_168h: float = 0.045,
        bull_breakout_pos_72h: float = 0.92,
        bull_breakout_ret_24h: float = 0.050,
        bull_breakout_ret_48h: float = 0.065,
        bull_breakout_ret_168h: float = 0.080,
        bull_variance_ratio_min: float = 1.17,
    ) -> None:
        self.swing_left = swing_left_bars
        self.swing_right = swing_right_bars
        self.atr_period = atr_period
        self.sl_buffer_atr_multiple = sl_buffer_atr_multiple
        self.min_reward_to_risk = min_reward_to_risk
        self.fvg_min_size_ratio = fvg_min_size_ratio
        self.sr_tolerance = sr_cluster_tolerance_pct
        self.premium_threshold = premium_threshold
        self.discount_threshold = discount_threshold
        self.min_extension_48h = min_extension_48h
        self.min_impulse_12h = min_impulse_12h
        self.short_macro_cap_168h = short_macro_cap_168h
        self.bull_breakout_pos_72h = bull_breakout_pos_72h
        self.bull_breakout_ret_24h = bull_breakout_ret_24h
        self.bull_breakout_ret_48h = bull_breakout_ret_48h
        self.bull_breakout_ret_168h = bull_breakout_ret_168h
        self.bull_variance_ratio_min = bull_variance_ratio_min

    def generate_candidate(
        self,
        symbol: str,
        candles_1h: List[Candle],
        candles_4h: List[Candle],
        as_of_index_1h: Optional[int] = None,
    ) -> Optional[SetupCandidate]:
        """Generate a candidate LONG or SHORT setup as of a specific 1H candle.
        Returns None (NO TRADE) if no valid setup qualifies.
        """
        limit = len(candles_1h) if as_of_index_1h is None else (as_of_index_1h + 1)
        start_idx = max(0, limit - 170)
        sub_1h = candles_1h[start_idx:limit]

        if len(sub_1h) < 170:
            return None

        current_candle = sub_1h[-1]
        now_ts = current_candle.timestamp_ms
        current_price = current_candle.close
        if current_price <= 0:
            return None

        # 0. Fast All-Weather SMC 72H Dealing Range & Regime Pre-Screen (Zero Lookahead)
        window_72 = sub_1h[-72:]
        hi_72 = max(c.high for c in window_72)
        lo_72 = min(c.low for c in window_72)
        span_72 = hi_72 - lo_72
        if span_72 <= 1e-8:
            return None

        pos_in_range_72 = (current_price - lo_72) / span_72
        c_48_ago = sub_1h[-49].close
        c_24_ago = sub_1h[-25].close
        c_12_ago = sub_1h[-13].close
        c_168_ago = sub_1h[0].close

        ret_48 = (current_price - c_48_ago) / c_48_ago if c_48_ago > 0 else 0.0
        ret_24 = (current_price - c_24_ago) / c_24_ago if c_24_ago > 0 else 0.0
        ret_12 = (current_price - c_12_ago) / c_12_ago if c_12_ago > 0 else 0.0
        ret_168 = (current_price - c_168_ago) / c_168_ago if c_168_ago > 0 else 0.0

        # Mode 1 (SHORT): Premium Dealing Range Exhaustion — gated by 7D macro cap (ret_168 <= +4.5%)
        # to prevent shorting into runaway Bull Market expansions (Q4 fix).
        in_premium_exhaustion = (
            pos_in_range_72 >= self.premium_threshold
            and ret_48 >= self.min_extension_48h
            and ret_12 >= self.min_impulse_12h
            and ret_168 <= self.short_macro_cap_168h
            and current_candle.close < current_candle.open
        )

        # Mode 2A (LONG): Deep Discount Dealing Range Rejection in positive 7D trend
        in_discount_rejection = (
            pos_in_range_72 <= self.discount_threshold
            and ret_48 <= -self.min_extension_48h
            and ret_12 <= -self.min_impulse_12h
            and ret_168 >= 0.020
            and current_candle.close > current_candle.open
        )

        # Mode 2B (LONG): Bull Regime Structural Breakout Expansion
        # Confirmed by Variance Ratio VR(24h/12h) >= 1.17 and healthy non-climax volume (1.1x - 2.3x)
        in_bull_expansion = False
        vr_val = 1.0
        if (
            pos_in_range_72 >= self.bull_breakout_pos_72h
            and ret_24 >= self.bull_breakout_ret_24h
            and ret_48 >= self.bull_breakout_ret_48h
            and ret_168 >= self.bull_breakout_ret_168h
            and current_candle.close > current_candle.open
        ):
            avg_vol_20 = sum(x.volume for x in sub_1h[-20:]) / 20.0
            rvol = current_candle.volume / avg_vol_20 if avg_vol_20 > 0 else 1.0
            if 1.10 <= rvol <= 2.30:
                closes = np.array([c.close for c in sub_1h], dtype=float)
                r12_arr = closes[12:] / closes[:-12] - 1.0
                r24_arr = closes[24:] / closes[:-24] - 1.0
                vr_val = float(np.var(r24_arr) / (2.0 * np.var(r12_arr) + 1e-12))
                if vr_val >= self.bull_variance_ratio_min:
                    in_bull_expansion = True

        if not (in_premium_exhaustion or in_discount_rejection or in_bull_expansion):
            return None

        # 1. Higher Timeframe 4H Bias (no lookahead: evaluated at 1H candle close_time_ms)
        bias_4h = MultiTimeframeBiasEngine.calculate_4h_bias(
            candles_4h, as_of_timestamp_ms=current_candle.close_time_ms
        )

        # 2. 1H Swings and Structure
        swings_1h = detect_swings(sub_1h, self.swing_left, self.swing_right)
        structure_1h, struct_events = MarketStructureEngine.evaluate_structure(
            sub_1h, swings_1h
        )

        # 3. Liquidity Sweeps
        sweep_events = LiquiditySweepDetector.detect_sweeps(sub_1h, swings_1h)

        # 4. Order Blocks and FVGs
        all_smc_events = struct_events + sweep_events
        order_blocks = OrderBlockDetector.identify_order_blocks(sub_1h, struct_events)
        fvgs = FVGDetector.detect_fvgs(
            sub_1h,
            min_gap_size_atr_ratio=self.fvg_min_size_ratio,
            atr_period=self.atr_period,
        )

        # 5. Support and Resistance Zones
        sr_zones = cluster_swing_zones(swings_1h, tolerance_pct=self.sr_tolerance)
        sr_zones = SnRInteractionEngine.evaluate_interactions(sub_1h, sr_zones)
        nearest_support, nearest_resistance = SnRInteractionEngine.get_nearest_levels(
            current_price, sr_zones
        )

        # 6. Lookback window for recent SMC events (last 12 bars)
        recent_cutoff_ts = current_candle.timestamp_ms - (12 * 3600 * 1000)
        recent_events = [e for e in all_smc_events if e.timestamp_ms >= recent_cutoff_ts]

        atr = compute_atr(sub_1h, period=self.atr_period)
        latest_high, latest_low = get_latest_confirmed_swings(swings_1h, now_ts)

        # Evaluate SHORT in Premium Zone
        if in_premium_exhaustion:
            candidate_short = self._evaluate_short(
                symbol=symbol,
                current_candle=current_candle,
                bias_4h=bias_4h,
                structure_1h=structure_1h,
                recent_events=recent_events,
                order_blocks=order_blocks,
                fvgs=fvgs,
                swings=swings_1h,
                latest_high=latest_high,
                nearest_support=nearest_support,
                nearest_resistance=nearest_resistance,
                atr=atr,
                pos_in_range_72=pos_in_range_72,
                ret_48=ret_48,
            )
            if candidate_short is not None:
                return candidate_short

        # Evaluate LONG (Discount Rejection or Bull Regime Structural Expansion)
        if in_discount_rejection or in_bull_expansion:
            candidate_long = self._evaluate_long(
                symbol=symbol,
                current_candle=current_candle,
                bias_4h=bias_4h,
                structure_1h=structure_1h,
                recent_events=recent_events,
                order_blocks=order_blocks,
                fvgs=fvgs,
                swings=swings_1h,
                latest_low=latest_low,
                nearest_support=nearest_support,
                nearest_resistance=nearest_resistance,
                atr=atr,
                pos_in_range_72=pos_in_range_72,
                ret_48=ret_48,
                is_bull_expansion=in_bull_expansion,
                vr_val=vr_val,
            )
            if candidate_long is not None:
                return candidate_long

        return None

    def _evaluate_long(
        self,
        symbol: str,
        current_candle: Candle,
        bias_4h: MarketBias,
        structure_1h: StructureState,
        recent_events: List[SMCEvent],
        order_blocks: List[OrderBlock],
        fvgs: List[FairValueGap],
        swings: List[SwingPoint],
        latest_low: Optional[SwingPoint],
        nearest_support,
        nearest_resistance,
        atr: float,
        pos_in_range_72: float = 0.10,
        ret_48: float = -0.06,
        is_bull_expansion: bool = False,
        vr_val: float = 1.0,
    ) -> Optional[SetupCandidate]:
        # Bull Regime Expansion requires confirmed 4H BULLISH bias;
        # Discount Rejection allows non-BEARISH or macro uptrend bounce.
        if is_bull_expansion and bias_4h != MarketBias.BULLISH:
            return None
        if not is_bull_expansion and bias_4h == MarketBias.BEARISH:
            return None

        now_ts = current_candle.timestamp_ms
        entry_price = current_candle.close

        active_obs = [
            ob
            for ob in order_blocks
            if ob.direction == Direction.LONG
            and (not ob.mitigated or ob.mitigated_timestamp_ms == now_ts)
        ]
        active_fvgs = [
            fvg
            for fvg in fvgs
            if fvg.direction == Direction.LONG
            and (not fvg.mitigated or fvg.mitigated_timestamp_ms == now_ts)
        ]

        # Bound structural invalidation so total stop_distance is ~2.40% of entry (fits $1.50 risk cap on $6.00 target margin with risk step-down)
        target_sl_dist = min(max(1.8 * atr, 0.022 * entry_price), 0.0240 * entry_price)
        buffer = atr * self.sl_buffer_atr_multiple
        invalidation_price = entry_price - max(target_sl_dist - buffer, 0.005 * entry_price)

        confirmed_swings = [s for s in swings if s.confirmed_timestamp_ms <= now_ts]
        sl, tp1, tp2, gross_rr, net_rr = TPSLPlanner.plan_trade_levels(
            direction=Direction.LONG,
            entry_price=entry_price,
            invalidation_price=invalidation_price,
            atr=atr,
            swings=confirmed_swings,
            resistance=nearest_resistance,
            support=nearest_support,
            atr_buffer_mult=self.sl_buffer_atr_multiple,
            min_rr=self.min_reward_to_risk,
            fallback_rr=1.60,
        )

        if sl is None or tp1 is None or gross_rr < self.min_reward_to_risk:
            return None

        active_ob = active_obs[-1] if active_obs else None
        active_fvg = active_fvgs[-1] if active_fvgs else None

        smc_ev, snr_ev, supp, opp, inv_reason = SetupExplainer.build_explanation(
            symbol=symbol,
            direction=Direction.LONG,
            bias_4h=bias_4h,
            structure_1h=structure_1h,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp1,
            invalidation_price=invalidation_price,
            gross_rr=gross_rr,
            net_rr=net_rr,
            recent_smc_events=recent_events,
            active_ob=active_ob,
            active_fvg=active_fvg,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
        )
        fallback_tp1 = entry_price + ((entry_price - sl) * max(self.min_reward_to_risk, 1.60))
        if abs(tp1 - fallback_tp1) <= 1e-6:
            supp.append(f"TP1 target planned via fallback R:R ({gross_rr:.2f}R)")
        else:
            supp.append(f"TP1 target anchored to structural swing/resistance ({gross_rr:.2f}R)")
        if is_bull_expansion:
            smc_ev.append(
                f"SMC Bullish BOS Regime Expansion (VR={vr_val:.2f}, 48H {ret_48:+.1%})"
            )
        else:
            smc_ev.append(
                f"SMC Discount Dealing Range Rejection ({pos_in_range_72:.1%} of 72H range, 48H {ret_48:+.1%})"
            )

        candidate_id = f"SETUP_{symbol}_{now_ts}_LONG"
        expires_at = now_ts + (12 * 3600 * 1000)

        entry_cat = classify_entry_category(
            direction=Direction.LONG,
            bias_4h=bias_4h,
            structure_1h=structure_1h,
            smc_evidence=smc_ev,
            recent_events=recent_events,
            is_bull_expansion=is_bull_expansion,
            is_discount_rejection=(not is_bull_expansion),
            has_active_ob=bool(active_ob),
            has_active_fvg=bool(active_fvg),
        )

        return SetupCandidate(
            candidate_id=candidate_id,
            symbol=symbol,
            timestamp_ms=now_ts,
            direction=Direction.LONG,
            bias_4h=bias_4h,
            structure_1h=structure_1h,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            gross_reward_to_risk=gross_rr,
            net_reward_to_risk=net_rr,
            invalidation_price=invalidation_price,
            invalidation_reason=inv_reason,
            smc_evidence=smc_ev,
            snr_evidence=snr_ev,
            supporting_reasons=supp,
            opposing_reasons=opp,
            status=CandidateStatus.READY,
            entry_category=entry_cat,
            expires_at_ms=expires_at,
        )

    def _evaluate_short(
        self,
        symbol: str,
        current_candle: Candle,
        bias_4h: MarketBias,
        structure_1h: StructureState,
        recent_events: List[SMCEvent],
        order_blocks: List[OrderBlock],
        fvgs: List[FairValueGap],
        swings: List[SwingPoint],
        latest_high: Optional[SwingPoint],
        nearest_support,
        nearest_resistance,
        atr: float,
        pos_in_range_72: float = 0.90,
        ret_48: float = 0.06,
    ) -> Optional[SetupCandidate]:
        now_ts = current_candle.timestamp_ms
        entry_price = current_candle.close

        active_obs = [
            ob
            for ob in order_blocks
            if ob.direction == Direction.SHORT
            and (not ob.mitigated or ob.mitigated_timestamp_ms == now_ts)
        ]
        active_fvgs = [
            fvg
            for fvg in fvgs
            if fvg.direction == Direction.SHORT
            and (not fvg.mitigated or fvg.mitigated_timestamp_ms == now_ts)
        ]

        # Bound structural invalidation so total stop_distance is ~2.40% of entry (fits $1.50 risk cap on $6.00 target margin with risk step-down)
        target_sl_dist = min(max(1.8 * atr, 0.022 * entry_price), 0.0240 * entry_price)
        buffer = atr * self.sl_buffer_atr_multiple
        invalidation_price = entry_price + max(target_sl_dist - buffer, 0.005 * entry_price)

        confirmed_swings = [s for s in swings if s.confirmed_timestamp_ms <= now_ts]
        sl, tp1, tp2, gross_rr, net_rr = TPSLPlanner.plan_trade_levels(
            direction=Direction.SHORT,
            entry_price=entry_price,
            invalidation_price=invalidation_price,
            atr=atr,
            swings=confirmed_swings,
            resistance=nearest_resistance,
            support=nearest_support,
            atr_buffer_mult=self.sl_buffer_atr_multiple,
            min_rr=self.min_reward_to_risk,
            fallback_rr=1.60,
        )

        if sl is None or tp1 is None or gross_rr < self.min_reward_to_risk:
            return None

        active_ob = active_obs[-1] if active_obs else None
        active_fvg = active_fvgs[-1] if active_fvgs else None

        smc_ev, snr_ev, supp, opp, inv_reason = SetupExplainer.build_explanation(
            symbol=symbol,
            direction=Direction.SHORT,
            bias_4h=bias_4h,
            structure_1h=structure_1h,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp1,
            invalidation_price=invalidation_price,
            gross_rr=gross_rr,
            net_rr=net_rr,
            recent_smc_events=recent_events,
            active_ob=active_ob,
            active_fvg=active_fvg,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
        )
        fallback_tp1 = entry_price - ((sl - entry_price) * max(self.min_reward_to_risk, 1.60))
        if abs(tp1 - fallback_tp1) <= 1e-6:
            supp.append(f"TP1 target planned via fallback R:R ({gross_rr:.2f}R)")
        else:
            supp.append(f"TP1 target anchored to structural swing/support ({gross_rr:.2f}R)")
        smc_ev.append(
            f"SMC Premium Dealing Range Exhaustion ({pos_in_range_72:.1%} of 72H range, 48H {ret_48:+.1%})"
        )

        candidate_id = f"SETUP_{symbol}_{now_ts}_SHORT"
        expires_at = now_ts + (12 * 3600 * 1000)

        entry_cat = classify_entry_category(
            direction=Direction.SHORT,
            bias_4h=bias_4h,
            structure_1h=structure_1h,
            smc_evidence=smc_ev,
            recent_events=recent_events,
            is_premium_exhaustion=True,
            has_active_ob=bool(active_ob),
            has_active_fvg=bool(active_fvg),
        )

        return SetupCandidate(
            candidate_id=candidate_id,
            symbol=symbol,
            timestamp_ms=now_ts,
            direction=Direction.SHORT,
            bias_4h=bias_4h,
            structure_1h=structure_1h,
            entry_price=entry_price,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            gross_reward_to_risk=gross_rr,
            net_reward_to_risk=net_rr,
            invalidation_price=invalidation_price,
            invalidation_reason=inv_reason,
            smc_evidence=smc_ev,
            snr_evidence=snr_ev,
            supporting_reasons=supp,
            opposing_reasons=opp,
            status=CandidateStatus.READY,
            entry_category=entry_cat,
            expires_at_ms=expires_at,
        )