"""Independent Research Shadow Evaluator for Model B.

Orchestrates multi-variant shadow evaluation, simulated execution lifecycle,
realistic limit-order fill modeling, and paired statistical analysis.

HARD GOVERNANCE & SAFETY INVARIANTS:
1. Operates strictly in research simulation mode.
2. NEVER submits orders to the active PaperExecutionEngine.
3. NEVER mutates active portfolio equity, balances, or circuit-breaker counters.
4. Independent accounting: Active execution halts do NOT stop research observations.
"""

from datetime import datetime, timezone
import json
import math
from typing import Any, Dict, List, Optional, Tuple

from src.core.logger import logger
from src.core.types import Candle, Direction, SetupCandidate
from src.data.database import DatabaseManager
from src.research.rules import VariantRuleEngine, compute_atr, compute_ema
from src.research.storage import ResearchStorage
from src.research.types import (
    EvaluationDecision,
    ResearchVariant,
    ShadowOrderStatus,
    ShadowSignalRecord,
    ShadowVariantEvaluation,
    VariantPerformanceMetrics,
)


class ModelBShadowEvaluator:
    """Independent shadow evaluator for evaluating multiple Model B LONG/SHORT variants."""

    def __init__(
        self,
        db: DatabaseManager,
        storage: Optional[ResearchStorage] = None,
        fill_buffer_pct: float = 0.00025,
        maker_fee_rate: float = 0.0002,
        taker_fee_rate: float = 0.0005,
        slippage_rate: float = 0.0005,
        pending_expiry_bars: int = 12,
    ) -> None:
        self.db = db
        self.storage = storage or ResearchStorage(db)
        self.fill_buffer_pct = fill_buffer_pct
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate
        self.slippage_rate = slippage_rate
        self.pending_expiry_bars = pending_expiry_bars

    def record_and_evaluate_candidate(
        self,
        candidate: SetupCandidate,
        context: Optional[Dict[str, Any]] = None,
        candles_1h: Optional[List[Candle]] = None,
        candles_4h: Optional[List[Candle]] = None,
        now_ms: Optional[int] = None,
        active_ineligible_reason: Optional[str] = None,
    ) -> Tuple[ShadowSignalRecord, List[ShadowVariantEvaluation]]:
        """Record an observed signal and evaluate all 6 variants simultaneously."""
        ctx = dict(context or {})
        c_time = now_ms if now_ms is not None else candidate.timestamp_ms
        sig_id = f"RSIG_{candidate.symbol}_{candidate.direction.value}_{candidate.timestamp_ms}"

        # Determine active execution eligibility from candidate state
        is_active_eligible = active_ineligible_reason is None and candidate.status.value in ("READY", "SUBMITTED", "FILLED")

        signal = ShadowSignalRecord(
            signal_id=sig_id,
            candidate_id=candidate.candidate_id,
            symbol=candidate.symbol,
            direction=candidate.direction.value,
            timestamp_ms=candidate.timestamp_ms,
            entry_price=candidate.entry_price,
            stop_loss=candidate.stop_loss,
            take_profit_1=candidate.take_profit_1,
            take_profit_2=candidate.take_profit_2,
            gross_reward_to_risk=candidate.gross_reward_to_risk,
            net_reward_to_risk=candidate.net_reward_to_risk,
            active_execution_eligible=is_active_eligible,
            active_ineligible_reason=active_ineligible_reason,
            market_context=ctx,
        )
        self.storage.save_signal(signal)

        evaluations: List[ShadowVariantEvaluation] = []

        # Evaluate each variant deterministically
        for variant in ResearchVariant:
            eval_id = f"REVAL_{variant.value}_{sig_id}"
            decision, rejection_reason, adjusted = VariantRuleEngine.evaluate_variant(
                variant=variant,
                symbol=candidate.symbol,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                stop_loss=candidate.stop_loss,
                take_profit_1=candidate.take_profit_1,
                take_profit_2=candidate.take_profit_2,
                context=ctx,
                candles_1h=candles_1h,
                candles_4h=candles_4h,
            )

            # Compute standard sizing if not adjusted by variant E
            sim_entry = adjusted.get("entry_price", candidate.entry_price)
            sim_sl = adjusted.get("stop_loss", candidate.stop_loss)
            sim_tp1 = adjusted.get("take_profit_1", candidate.take_profit_1)
            sim_tp2 = adjusted.get("take_profit_2", candidate.take_profit_2)

            sl_dist = abs(sim_entry - sim_sl)
            sim_qty = adjusted.get("simulated_quantity")
            if sim_qty is None and sl_dist > 0:
                sim_qty = 1.00 / sl_dist  # $1.00 standard Model B target risk

            sim_notional = adjusted.get("simulated_notional_usdt")
            if sim_notional is None and sim_qty is not None:
                sim_notional = sim_qty * sim_entry

            sim_margin = adjusted.get("simulated_margin_usdt")
            if sim_margin is None and sim_notional is not None:
                sim_margin = sim_notional / 10.0

            order_status = ShadowOrderStatus.PENDING if decision == EvaluationDecision.ACCEPTED else ShadowOrderStatus.REJECTED

            eval_obj = ShadowVariantEvaluation(
                evaluation_id=eval_id,
                signal_id=sig_id,
                variant=variant,
                symbol=candidate.symbol,
                direction=candidate.direction.value,
                timestamp_ms=candidate.timestamp_ms,
                decision=decision,
                rejection_reason=rejection_reason,
                simulated_entry_price=sim_entry,
                simulated_stop_loss=sim_sl,
                simulated_take_profit=sim_tp1,
                simulated_take_profit_2=sim_tp2,
                simulated_quantity=sim_qty,
                simulated_notional_usdt=sim_notional,
                simulated_margin_usdt=sim_margin,
                simulated_leverage=10.0,
                order_status=order_status,
                is_open=(order_status == ShadowOrderStatus.PENDING),
                details={
                    "evaluated_bars_count": 0,
                    "highest_favorable_price": None,
                    "lowest_adverse_price": None,
                    "active_eligible_at_entry": is_active_eligible,
                    "active_ineligible_reason": active_ineligible_reason,
                },
            )
            evaluations.append(eval_obj)

        self.storage.save_evaluations_batch(evaluations)
        return signal, evaluations

    def step_evaluations(
        self,
        candles_by_symbol: Dict[str, List[Candle]],
        now_ms: Optional[int] = None,
    ) -> List[ShadowVariantEvaluation]:
        """Step all open shadow orders and positions against new/replayed candles."""
        open_evals = self.storage.get_open_shadow_orders()
        if not open_evals:
            return []

        updated_evals: List[ShadowVariantEvaluation] = []

        for ev in open_evals:
            candles = candles_by_symbol.get(ev.symbol, [])
            if not candles:
                continue

            # Filter candles that occurred strictly after order creation
            eval_candles = [c for c in candles if c.timestamp_ms >= ev.timestamp_ms]
            if not eval_candles:
                continue

            modified = False

            # Case 1: Order is PENDING (waiting for fill)
            if ev.order_status == ShadowOrderStatus.PENDING:
                for candle in eval_candles:
                    if candle.timestamp_ms < ev.timestamp_ms:
                        continue

                    bars_seen = ev.details.get("evaluated_bars_count", 0) + 1
                    ev.details["evaluated_bars_count"] = bars_seen

                    is_filled = False
                    if ev.direction == "LONG":
                        threshold = ev.simulated_entry_price * (1.0 - self.fill_buffer_pct)
                        if candle.low <= threshold:
                            is_filled = True
                    else:
                        threshold = ev.simulated_entry_price * (1.0 + self.fill_buffer_pct)
                        if candle.high >= threshold:
                            is_filled = True

                    if is_filled:
                        ev.order_status = ShadowOrderStatus.FILLED
                        ev.fill_timestamp_ms = candle.timestamp_ms
                        ev.fill_price = ev.simulated_entry_price
                        ev.fee_paid = (ev.simulated_notional_usdt or 0.0) * self.maker_fee_rate
                        ev.is_open = True
                        ev.details["highest_favorable_price"] = candle.high if ev.direction == "LONG" else candle.low
                        ev.details["lowest_adverse_price"] = candle.low if ev.direction == "LONG" else candle.high
                        modified = True
                        break
                    elif bars_seen >= self.pending_expiry_bars:
                        ev.order_status = ShadowOrderStatus.EXPIRED
                        ev.exit_reason = "EXPIRED_TTL"
                        ev.is_open = False
                        modified = True
                        break

            # Case 2: Position is FILLED and OPEN (evaluating excursions and bracket exits)
            if ev.order_status == ShadowOrderStatus.FILLED and ev.is_open:
                fill_ts = ev.fill_timestamp_ms or ev.timestamp_ms
                post_fill_candles = [c for c in eval_candles if c.timestamp_ms >= fill_ts]

                for candle in post_fill_candles:
                    ev.holding_bars += 1
                    ev.holding_time_min = (candle.timestamp_ms - fill_ts) / 60000.0

                    entry_px = ev.fill_price or ev.simulated_entry_price
                    sl_px = ev.simulated_stop_loss
                    tp_px = ev.simulated_take_profit
                    qty = ev.simulated_quantity or 0.0

                    # Track excursion extrema
                    if ev.direction == "LONG":
                        prev_high = ev.details.get("highest_favorable_price") or candle.high
                        prev_low = ev.details.get("lowest_adverse_price") or candle.low
                        ev.details["highest_favorable_price"] = max(prev_high, candle.high)
                        ev.details["lowest_adverse_price"] = min(prev_low, candle.low)

                        hit_tp = candle.high >= tp_px
                        hit_sl = candle.low <= sl_px

                        if hit_tp and hit_sl:
                            # Conservative collision resolution: SL hits first
                            ev.exit_price = sl_px * (1.0 - self.slippage_rate)
                            ev.exit_reason = "SL_COLLISION"
                            ev.slippage_paid = abs(ev.exit_price - sl_px) * qty
                        elif hit_sl:
                            ev.exit_price = sl_px * (1.0 - self.slippage_rate)
                            ev.exit_reason = "STOP_LOSS"
                            ev.slippage_paid = abs(ev.exit_price - sl_px) * qty
                        elif hit_tp:
                            ev.exit_price = tp_px
                            ev.exit_reason = "TAKE_PROFIT"
                            ev.slippage_paid = 0.0

                    else:  # SHORT
                        prev_low = ev.details.get("highest_favorable_price") or candle.low
                        prev_high = ev.details.get("lowest_adverse_price") or candle.high
                        ev.details["highest_favorable_price"] = min(prev_low, candle.low)
                        ev.details["lowest_adverse_price"] = max(prev_high, candle.high)

                        hit_tp = candle.low <= tp_px
                        hit_sl = candle.high >= sl_px

                        if hit_tp and hit_sl:
                            ev.exit_price = sl_px * (1.0 + self.slippage_rate)
                            ev.exit_reason = "SL_COLLISION"
                            ev.slippage_paid = abs(ev.exit_price - sl_px) * qty
                        elif hit_sl:
                            ev.exit_price = sl_px * (1.0 + self.slippage_rate)
                            ev.exit_reason = "STOP_LOSS"
                            ev.slippage_paid = abs(ev.exit_price - sl_px) * qty
                        elif hit_tp:
                            ev.exit_price = tp_px
                            ev.exit_reason = "TAKE_PROFIT"
                            ev.slippage_paid = 0.0

                    if ev.exit_reason:
                        ev.order_status = ShadowOrderStatus.CLOSED
                        ev.is_open = False
                        ev.exit_timestamp_ms = candle.timestamp_ms

                        # Calculate final PnL, fees, and excursions
                        exit_fee = (qty * ev.exit_price) * self.taker_fee_rate
                        ev.fee_paid += exit_fee

                        if ev.direction == "LONG":
                            ev.pnl_gross = qty * (ev.exit_price - entry_px)
                            fav_px = ev.details.get("highest_favorable_price", entry_px)
                            adv_px = ev.details.get("lowest_adverse_price", entry_px)
                            ev.mfe_pct = max(0.0, (fav_px - entry_px) / entry_px * 100.0)
                            ev.mae_pct = max(0.0, (entry_px - adv_px) / entry_px * 100.0)
                        else:
                            ev.pnl_gross = qty * (entry_px - ev.exit_price)
                            fav_px = ev.details.get("highest_favorable_price", entry_px)
                            adv_px = ev.details.get("lowest_adverse_price", entry_px)
                            ev.mfe_pct = max(0.0, (entry_px - fav_px) / entry_px * 100.0)
                            ev.mae_pct = max(0.0, (adv_px - entry_px) / entry_px * 100.0)

                        ev.pnl_net = ev.pnl_gross - ev.fee_paid - ev.slippage_paid - ev.funding_paid
                        modified = True
                        break

            if modified:
                ev.updated_at_utc = datetime.now(timezone.utc).isoformat()
                updated_evals.append(ev)

        if updated_evals:
            self.storage.save_evaluations_batch(updated_evals)

        return updated_evals

    def replay_historical_candidates(
        self,
        candidates: List[SetupCandidate],
        candles_by_symbol: Dict[str, List[Candle]],
        order_book_snapshots: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> Dict[str, VariantPerformanceMetrics]:
        """Replay historical setup candidates chronologically through all variants."""
        sorted_cands = sorted(candidates, key=lambda c: c.timestamp_ms)
        book_snaps = order_book_snapshots or {}

        logger.info(f"ModelBShadowEvaluator: Replaying {len(sorted_cands)} historical candidates...")

        for cand in sorted_cands:
            sym = cand.symbol
            c1h = candles_by_symbol.get(sym, [])
            c1h_prior = [c for c in c1h if c.timestamp_ms <= cand.timestamp_ms]

            # Reconstruct context
            ctx: Dict[str, Any] = {}
            if hasattr(cand, "details") and isinstance(cand.details, dict):
                ctx.update(cand.details)
            elif hasattr(cand, "details_json") and cand.details_json:
                try:
                    ctx.update(json.loads(cand.details_json))
                except Exception:
                    pass

            # Extract book depth imbalance if available
            snaps = book_snaps.get(sym, [])
            rel_snap = next((s for s in snaps if s.get("timestamp_ms", 0) <= cand.timestamp_ms), None)
            if rel_snap and "imbalance_25bps" in rel_snap:
                ctx["imbalance_25bps"] = rel_snap["imbalance_25bps"]

            # Compute true 4H structure
            if c1h_prior:
                ctx["atr_1h"] = compute_atr(c1h_prior, 14)

            # Record and evaluate signal across all variants
            self.record_and_evaluate_candidate(
                candidate=cand,
                context=ctx,
                candles_1h=c1h_prior,
                now_ms=cand.timestamp_ms,
            )

        # Advance all evaluated positions across full candle timeline
        self.step_evaluations(candles_by_symbol)

        # Compute summary metrics per variant
        summary: Dict[str, VariantPerformanceMetrics] = {}
        for var in ResearchVariant:
            summary[var.value] = self.storage.compute_metrics(var.value)

        return summary
