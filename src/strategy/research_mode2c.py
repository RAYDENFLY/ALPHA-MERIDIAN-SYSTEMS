"""Research-Only Strategy Generator for Mode 2C: Mid-Range Liquidity Sweep.

NOTICE: RESEARCH EXPERIMENT ONLY.
DO NOT IMPORT OR ENABLE IN PRODUCTION SCANNER.

Implements deterministic SMC intraday mid-range liquidity sweep rules:
1. Higher-timeframe directional bias using closed 4H candles only.
2. Identifiable prior swing highs/lows and equal-high/equal-low liquidity sweeps.
3. Closed 1H bar sweep confirmation and reclaim (zero lookahead).
4. Strict 4H macro trend alignment.
5. 1H Order Block or Fair Value Gap confluence near the swept level.
6. ATR-grounded TP/SL planning with opposing S&R wall clearance (>= 1.2R).
7. Duplicate sweep suppression (prevents repeated entries on the same level within 24h).
8. Explicit StrategyDiagnostic rejection reason attribution for every failed bar.
"""

from typing import Dict, List, Optional, Set, Tuple
import numpy as np

from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    EntryCategory,
    FairValueGap,
    MarketBias,
    OrderBlock,
    SetupCandidate,
    SMCEvent,
    SMCEventType,
    StrategyDiagnostic,
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
from src.strategy.explainer import SetupExplainer
from src.strategy.generator import compute_atr
from src.strategy.planner import TPSLPlanner


class ResearchMode2CGenerator:
    """Deterministic candidate generator for Mode 2C Mid-Range Liquidity Sweeps."""

    def __init__(
        self,
        mid_range_min_pos: float = 0.20,
        mid_range_max_pos: float = 0.80,
        swing_left: int = 5,
        swing_right: int = 2,
        sweep_lookback_bars: int = 48,
        atr_period: int = 14,
        sl_buffer_atr_multiple: float = 0.35,
        min_reward_to_risk: float = 1.50,
        sr_cluster_tolerance_pct: float = 0.015,
        fvg_min_size_ratio: float = 0.25,
        allow_equal_high_low_tolerance_atr: float = 0.20,
    ) -> None:
        self.mid_range_min_pos = mid_range_min_pos
        self.mid_range_max_pos = mid_range_max_pos
        self.swing_left = swing_left
        self.swing_right = swing_right
        self.sweep_lookback_bars = sweep_lookback_bars
        self.atr_period = atr_period
        self.sl_buffer_atr_multiple = sl_buffer_atr_multiple
        self.min_reward_to_risk = min_reward_to_risk
        self.sr_tolerance = sr_cluster_tolerance_pct
        self.fvg_min_size_ratio = fvg_min_size_ratio
        self.eq_tolerance_atr = allow_equal_high_low_tolerance_atr

        # Duplicate signal suppression: tracks (symbol, swing_ts) to prevent re-entering same sweep
        self.seen_sweep_signatures: Set[Tuple[str, int]] = set()
        self.last_diagnostic: Optional[StrategyDiagnostic] = None

    def reset_suppression_state(self) -> None:
        """Clear the duplicate suppression cache between isolated test runs."""
        self.seen_sweep_signatures.clear()
        self.last_diagnostic = None

    def generate_candidate(
        self,
        symbol: str,
        candles_1h: List[Candle],
        candles_4h: List[Candle],
        as_of_index_1h: Optional[int] = None,
    ) -> Optional[SetupCandidate]:
        """Evaluate a closed 1H bar for a Mode 2C Mid-Range Liquidity Sweep setup.

        Returns None if conditions are not satisfied, setting self.last_diagnostic.
        """
        limit = len(candles_1h) if as_of_index_1h is None else (as_of_index_1h + 1)
        start_idx = max(0, limit - 170)
        sub_1h = candles_1h[start_idx:limit]

        if len(sub_1h) < 170:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="INSUFFICIENT_DATA",
                reason_detail=f"1H candle count ({len(sub_1h)}) below minimum 170 required bars.",
            )
            return None

        current_candle = sub_1h[-1]
        now_ts = current_candle.timestamp_ms
        current_price = current_candle.close

        if current_price <= 0:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="INVALID_DATA",
                reason_detail="Current candle close price is non-positive.",
            )
            return None

        # 1. Dealing-Range Mid-Range Boundary Check
        window_72 = sub_1h[-72:]
        hi_72 = max(c.high for c in window_72)
        lo_72 = min(c.low for c in window_72)
        span_72 = hi_72 - lo_72
        if span_72 <= 1e-8:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="INSUFFICIENT_DATA",
                reason_detail="72H dealing range span is zero or invalid.",
            )
            return None

        pos_in_range_72 = (current_price - lo_72) / span_72
        if not (self.mid_range_min_pos <= pos_in_range_72 <= self.mid_range_max_pos):
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="EQUILIBRIUM_ZONE_72H",
                reason_detail=(
                    f"Price pos_72 ({pos_in_range_72:.4f}) is outside Mode 2C mid-range window "
                    f"[{self.mid_range_min_pos:.2f}, {self.mid_range_max_pos:.2f}]."
                ),
                metadata={"pos_72": round(pos_in_range_72, 4), "span_72": span_72},
            )
            return None

        # 2. Higher Timeframe 4H Bias (Closed 4H candles only; evaluated at 1H close_time_ms)
        bias_4h = MultiTimeframeBiasEngine.calculate_4h_bias(
            candles_4h, as_of_timestamp_ms=current_candle.close_time_ms
        )
        if bias_4h == MarketBias.NEUTRAL:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="HTF_BIAS_MISMATCH",
                reason_detail="4H bias is NEUTRAL. Mode 2C requires confirmed directional trend.",
                metadata={"bias_4h": bias_4h.value},
            )
            return None

        # 3. Detect 1H Swings and Equal Highs/Lows
        swings_1h = detect_swings(sub_1h, self.swing_left, self.swing_right)
        confirmed_swings = [s for s in swings_1h if s.confirmed_timestamp_ms <= now_ts]
        if not confirmed_swings:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="NO_LIQUIDITY_SWEEP",
                reason_detail="No confirmed 1H swing points found in candle history.",
            )
            return None

        atr = compute_atr(sub_1h, period=self.atr_period)

        # 4. Search for Recent Liquidity Sweep on Identifiable Swings
        # Evaluate directional sweep matching 4H bias:
        # If bias is BULLISH -> look for Sell-Side Liquidity Sweep (pierce swing low, reclaim above)
        # If bias is BEARISH -> look for Buy-Side Liquidity Sweep (pierce swing high, reclaim below)
        recent_swings = [
            s for s in confirmed_swings
            if (len(sub_1h) - 1 - s.index) <= self.sweep_lookback_bars
        ]

        if bias_4h == MarketBias.BULLISH:
            candidate = self._evaluate_bullish_mid_range_sweep(
                symbol=symbol,
                current_candle=current_candle,
                sub_1h=sub_1h,
                swings=recent_swings,
                all_confirmed_swings=confirmed_swings,
                bias_4h=bias_4h,
                atr=atr,
                pos_in_range_72=pos_in_range_72,
            )
        else:
            candidate = self._evaluate_bearish_mid_range_sweep(
                symbol=symbol,
                current_candle=current_candle,
                sub_1h=sub_1h,
                swings=recent_swings,
                all_confirmed_swings=confirmed_swings,
                bias_4h=bias_4h,
                atr=atr,
                pos_in_range_72=pos_in_range_72,
            )

        if candidate is not None:
            self.last_diagnostic = None
            return candidate

        # If helper returned None without setting last_diagnostic, provide fallback
        if self.last_diagnostic is None:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="NO_SMC_CONFLUENCE",
                reason_detail="No qualified Mode 2C liquidity sweep setup found.",
            )
        return None

    def _evaluate_bullish_mid_range_sweep(
        self,
        symbol: str,
        current_candle: Candle,
        sub_1h: List[Candle],
        swings: List[SwingPoint],
        all_confirmed_swings: List[SwingPoint],
        bias_4h: MarketBias,
        atr: float,
        pos_in_range_72: float,
    ) -> Optional[SetupCandidate]:
        """Evaluate Bullish (Sell-Side) Liquidity Sweep in positive 4H trend."""
        now_ts = current_candle.timestamp_ms
        target_swings = [s for s in swings if s.swing_type == SwingType.LOW]
        if not target_swings:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="NO_LIQUIDITY_SWEEP",
                reason_detail="No confirmed swing lows within lookback window.",
            )
            return None

        # Check last 3 closed bars for a sell-side sweep
        eval_window = sub_1h[-3:]
        qualifying_sweep: Optional[Tuple[SwingPoint, Candle, float]] = None

        for s_low in reversed(target_swings):
            # Check duplicate suppression
            sig = (symbol, s_low.timestamp_ms)
            if sig in self.seen_sweep_signatures:
                continue

            for bar in eval_window:
                if bar.timestamp_ms > s_low.confirmed_timestamp_ms:
                    # Sweep condition: low went below swing low, but candle closed back above
                    if bar.low < s_low.price and bar.close > s_low.price:
                        qualifying_sweep = (s_low, bar, s_low.price)
                        break
            if qualifying_sweep:
                break

        if not qualifying_sweep:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="NO_LIQUIDITY_SWEEP",
                reason_detail="No confirmed sell-side liquidity sweep (wick pierce + reclaim) within recent 3 bars.",
            )
            return None

        swept_swing, sweep_bar, swept_price = qualifying_sweep

        # Sweep Confirmation and Reclaim check on CURRENT closed candle
        # Current candle must maintain reclaim (close > swept_price) and close bullish
        if current_candle.close <= swept_price:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="SWEEP_NOT_CONFIRMED",
                reason_detail=(
                    f"Current 1H close ({current_candle.close:.4f}) failed to sustain reclaim "
                    f"above swept swing low ({swept_price:.4f})."
                ),
            )
            return None

        # Reclaim momentum confirmation: candle close > open OR current close > sweep bar close
        if not (current_candle.close > current_candle.open or current_candle.close >= sweep_bar.close):
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="SWEEP_NOT_CONFIRMED",
                reason_detail="Reclaim candle lacks bullish closing momentum.",
            )
            return None

        # SMC Confluence: Check 1H Order Block or Fair Value Gap
        structure_1h, struct_events = MarketStructureEngine.evaluate_structure(sub_1h, swings)
        order_blocks = OrderBlockDetector.identify_order_blocks(sub_1h, struct_events)
        fvgs = FVGDetector.detect_fvgs(
            sub_1h, min_gap_size_atr_ratio=self.fvg_min_size_ratio, atr_period=self.atr_period
        )

        active_bull_obs = [
            ob for ob in order_blocks
            if ob.direction == Direction.LONG and (not ob.mitigated or ob.mitigated_timestamp_ms == now_ts)
        ]
        active_bull_fvgs = [
            fvg for fvg in fvgs
            if fvg.direction == Direction.LONG and (not fvg.mitigated or fvg.mitigated_timestamp_ms == now_ts)
        ]

        # Confluence check: OB or FVG must be located within 1.2 ATR of swept level or entry price
        has_confluence = False
        active_ob = active_bull_obs[-1] if active_bull_obs else None
        active_fvg = active_bull_fvgs[-1] if active_bull_fvgs else None

        for ob in active_bull_obs:
            if abs(ob.high - swept_price) <= (1.2 * atr) or ob.low <= swept_price <= ob.high:
                has_confluence = True
                active_ob = ob
                break

        if not has_confluence:
            for fvg in active_bull_fvgs:
                if abs(fvg.top - swept_price) <= (1.2 * atr) or fvg.bottom <= swept_price <= fvg.top:
                    has_confluence = True
                    active_fvg = fvg
                    break

        if not has_confluence:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="NO_SMC_CONFLUENCE",
                reason_detail="Liquidity sweep occurred without aligning Bullish Order Block or Fair Value Gap within 1.2 ATR.",
            )
            return None

        # SnR Interaction and Trade Planning
        sr_zones = cluster_swing_zones(all_confirmed_swings, tolerance_pct=self.sr_tolerance)
        sr_zones = SnRInteractionEngine.evaluate_interactions(sub_1h, sr_zones)
        nearest_support, nearest_resistance = SnRInteractionEngine.get_nearest_levels(
            current_candle.close, sr_zones
        )

        entry_price = current_candle.close
        # Invalidation anchored below the sweep candle low
        invalidation_price = min(sweep_bar.low, current_candle.low) - (atr * self.sl_buffer_atr_multiple)

        sl, tp1, tp2, gross_rr, net_rr = TPSLPlanner.plan_trade_levels(
            direction=Direction.LONG,
            entry_price=entry_price,
            invalidation_price=invalidation_price,
            atr=atr,
            swings=all_confirmed_swings,
            resistance=nearest_resistance,
            support=nearest_support,
            atr_buffer_mult=self.sl_buffer_atr_multiple,
            min_rr=self.min_reward_to_risk,
            fallback_rr=1.60,
        )

        if sl is None or tp1 is None or gross_rr < self.min_reward_to_risk:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="INSUFFICIENT_RISK_REWARD",
                reason_detail=(
                    f"Planned Gross R:R ({gross_rr:.2f}) below minimum {self.min_reward_to_risk:.2f} "
                    "or opposing resistance wall blocks path within 1.2R buffer."
                ),
                metadata={"gross_rr": gross_rr, "min_rr": self.min_reward_to_risk},
            )
            return None

        # Build Explanation & SetupCandidate
        recent_cutoff_ts = now_ts - (12 * 3600 * 1000)
        recent_events = [e for e in struct_events if e.timestamp_ms >= recent_cutoff_ts]

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

        smc_ev.append(
            f"Mode 2C Mid-Range Sell-Side Liquidity Sweep ({swept_price:.4f} reclaimed, pos_72={pos_in_range_72:.1%})"
        )
        supp.append(f"4H BULLISH trend alignment with 1H mid-range liquidity grab at {swept_price:.4f}")

        # Mark signature in suppression cache
        self.seen_sweep_signatures.add((symbol, swept_swing.timestamp_ms))

        candidate_id = f"SETUP_{symbol}_{now_ts}_LONG_M2C"
        expires_at = now_ts + (12 * 3600 * 1000)

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
            entry_category=EntryCategory.RANGE_MEAN_REVERSION,
            expires_at_ms=expires_at,
        )

    def _evaluate_bearish_mid_range_sweep(
        self,
        symbol: str,
        current_candle: Candle,
        sub_1h: List[Candle],
        swings: List[SwingPoint],
        all_confirmed_swings: List[SwingPoint],
        bias_4h: MarketBias,
        atr: float,
        pos_in_range_72: float,
    ) -> Optional[SetupCandidate]:
        """Evaluate Bearish (Buy-Side) Liquidity Sweep in negative 4H trend."""
        now_ts = current_candle.timestamp_ms
        target_swings = [s for s in swings if s.swing_type == SwingType.HIGH]
        if not target_swings:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="NO_LIQUIDITY_SWEEP",
                reason_detail="No confirmed swing highs within lookback window.",
            )
            return None

        # Check last 3 closed bars for a buy-side sweep
        eval_window = sub_1h[-3:]
        qualifying_sweep: Optional[Tuple[SwingPoint, Candle, float]] = None

        for s_high in reversed(target_swings):
            # Check duplicate suppression
            sig = (symbol, s_high.timestamp_ms)
            if sig in self.seen_sweep_signatures:
                continue

            for bar in eval_window:
                if bar.timestamp_ms > s_high.confirmed_timestamp_ms:
                    # Sweep condition: high went above swing high, but candle closed back below
                    if bar.high > s_high.price and bar.close < s_high.price:
                        qualifying_sweep = (s_high, bar, s_high.price)
                        break
            if qualifying_sweep:
                break

        if not qualifying_sweep:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="NO_LIQUIDITY_SWEEP",
                reason_detail="No confirmed buy-side liquidity sweep (wick pierce + reclaim) within recent 3 bars.",
            )
            return None

        swept_swing, sweep_bar, swept_price = qualifying_sweep

        # Sweep Confirmation and Reclaim check on CURRENT closed candle
        if current_candle.close >= swept_price:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="SWEEP_NOT_CONFIRMED",
                reason_detail=(
                    f"Current 1H close ({current_candle.close:.4f}) failed to sustain reclaim "
                    f"below swept swing high ({swept_price:.4f})."
                ),
            )
            return None

        # Reclaim momentum confirmation: candle close < open OR current close <= sweep bar close
        if not (current_candle.close < current_candle.open or current_candle.close <= sweep_bar.close):
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="SWEEP_NOT_CONFIRMED",
                reason_detail="Reclaim candle lacks bearish closing momentum.",
            )
            return None

        # SMC Confluence: Check 1H Order Block or Fair Value Gap
        structure_1h, struct_events = MarketStructureEngine.evaluate_structure(sub_1h, swings)
        order_blocks = OrderBlockDetector.identify_order_blocks(sub_1h, struct_events)
        fvgs = FVGDetector.detect_fvgs(
            sub_1h, min_gap_size_atr_ratio=self.fvg_min_size_ratio, atr_period=self.atr_period
        )

        active_bear_obs = [
            ob for ob in order_blocks
            if ob.direction == Direction.SHORT and (not ob.mitigated or ob.mitigated_timestamp_ms == now_ts)
        ]
        active_bear_fvgs = [
            fvg for fvg in fvgs
            if fvg.direction == Direction.SHORT and (not fvg.mitigated or fvg.mitigated_timestamp_ms == now_ts)
        ]

        has_confluence = False
        active_ob = active_bear_obs[-1] if active_bear_obs else None
        active_fvg = active_bear_fvgs[-1] if active_bear_fvgs else None

        for ob in active_bear_obs:
            if abs(ob.low - swept_price) <= (1.2 * atr) or ob.low <= swept_price <= ob.high:
                has_confluence = True
                active_ob = ob
                break

        if not has_confluence:
            for fvg in active_bear_fvgs:
                if abs(fvg.bottom - swept_price) <= (1.2 * atr) or fvg.bottom <= swept_price <= fvg.top:
                    has_confluence = True
                    active_fvg = fvg
                    break

        if not has_confluence:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="NO_SMC_CONFLUENCE",
                reason_detail="Liquidity sweep occurred without aligning Bearish Order Block or Fair Value Gap within 1.2 ATR.",
            )
            return None

        # SnR Interaction and Trade Planning
        sr_zones = cluster_swing_zones(all_confirmed_swings, tolerance_pct=self.sr_tolerance)
        sr_zones = SnRInteractionEngine.evaluate_interactions(sub_1h, sr_zones)
        nearest_support, nearest_resistance = SnRInteractionEngine.get_nearest_levels(
            current_candle.close, sr_zones
        )

        entry_price = current_candle.close
        # Invalidation anchored above the sweep candle high
        invalidation_price = max(sweep_bar.high, current_candle.high) + (atr * self.sl_buffer_atr_multiple)

        sl, tp1, tp2, gross_rr, net_rr = TPSLPlanner.plan_trade_levels(
            direction=Direction.SHORT,
            entry_price=entry_price,
            invalidation_price=invalidation_price,
            atr=atr,
            swings=all_confirmed_swings,
            resistance=nearest_resistance,
            support=nearest_support,
            atr_buffer_mult=self.sl_buffer_atr_multiple,
            min_rr=self.min_reward_to_risk,
            fallback_rr=1.60,
        )

        if sl is None or tp1 is None or gross_rr < self.min_reward_to_risk:
            self.last_diagnostic = StrategyDiagnostic(
                reason_code="INSUFFICIENT_RISK_REWARD",
                reason_detail=(
                    f"Planned Gross R:R ({gross_rr:.2f}) below minimum {self.min_reward_to_risk:.2f} "
                    "or opposing support wall blocks path within 1.2R buffer."
                ),
                metadata={"gross_rr": gross_rr, "min_rr": self.min_reward_to_risk},
            )
            return None

        # Build Explanation & SetupCandidate
        recent_cutoff_ts = now_ts - (12 * 3600 * 1000)
        recent_events = [e for e in struct_events if e.timestamp_ms >= recent_cutoff_ts]

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

        smc_ev.append(
            f"Mode 2C Mid-Range Buy-Side Liquidity Sweep ({swept_price:.4f} reclaimed, pos_72={pos_in_range_72:.1%})"
        )
        supp.append(f"4H BEARISH trend alignment with 1H mid-range liquidity grab at {swept_price:.4f}")

        self.seen_sweep_signatures.add((symbol, swept_swing.timestamp_ms))

        candidate_id = f"SETUP_{symbol}_{now_ts}_SHORT_M2C"
        expires_at = now_ts + (12 * 3600 * 1000)

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
            entry_category=EntryCategory.RANGE_MEAN_REVERSION,
            expires_at_ms=expires_at,
        )


class CombinedBaselineAndMode2CGenerator:
    """Research generator testing the combination of Baseline (Modes 1, 2A, 2B) and Mode 2C.

    Evaluation hierarchy:
    1. If price meets Mode 1, 2A, or 2B baseline criteria: baseline takes priority.
    2. If baseline returns None and price is in mid-range: evaluate Mode 2C.
    """

    def __init__(
        self,
        baseline_gen,
        mode2c_gen: ResearchMode2CGenerator,
    ) -> None:
        self.baseline_gen = baseline_gen
        self.mode2c_gen = mode2c_gen
        self.last_diagnostic: Optional[StrategyDiagnostic] = None

    def generate_candidate(
        self,
        symbol: str,
        candles_1h: List[Candle],
        candles_4h: List[Candle],
        as_of_index_1h: Optional[int] = None,
    ) -> Optional[SetupCandidate]:
        cand = self.baseline_gen.generate_candidate(
            symbol=symbol,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            as_of_index_1h=as_of_index_1h,
        )
        if cand is not None:
            self.last_diagnostic = None
            return cand

        # If baseline was in equilibrium zone, try Mode 2C
        base_diag = getattr(self.baseline_gen, "last_diagnostic", None)
        if base_diag and base_diag.reason_code == "EQUILIBRIUM_ZONE_72H":
            m2c_cand = self.mode2c_gen.generate_candidate(
                symbol=symbol,
                candles_1h=candles_1h,
                candles_4h=candles_4h,
                as_of_index_1h=as_of_index_1h,
            )
            self.last_diagnostic = self.mode2c_gen.last_diagnostic
            return m2c_cand

        self.last_diagnostic = base_diag
        return None
