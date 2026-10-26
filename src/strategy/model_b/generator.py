"""Model B Strategy Generator.

Scans the eligible universal perpetual universe on 1H bar close, validates liquidity
depth and absorption, checks feasibility, and constructs Model B SetupCandidate instances.
"""

from typing import Any, Dict, List, Optional
import math
import time

from src.core.logger import logger
from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    EntryCategory,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
)
from src.catalyst.engine import NewsCatalystEngine
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.new_market_scanner import NewMarketOpportunityScanner
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook
from src.exchange.service import MultiExchangeService
from src.liquidity.collector import LiquidityCollector
from src.liquidity.wall_hunter import LiquidityWallHunter
from src.strategy.model_b.evaluator import evaluate_liquidity_setup
from src.strategy.model_b.risk_controls import closed_context, short_gate, enforce_stop_floor, ema
from config.settings import ModelBConfig


class ModelBStrategyGenerator:
    """Independent strategy generator for Model B Aggressive Liquidity Research."""

    def __init__(
        self,
        config: Optional[ModelBConfig] = None,
        exchange_service: Optional[MultiExchangeService] = None,
        liquidity_collector: Optional[LiquidityCollector] = None,
        wall_hunter: Optional[LiquidityWallHunter] = None,
        new_market_scanner: Optional[NewMarketOpportunityScanner] = None,
        catalyst_engine: Optional[NewsCatalystEngine] = None,
    ) -> None:
        self.config = config or ModelBConfig()
        self.exchange_service = exchange_service or MultiExchangeService()
        self.liquidity_collector = liquidity_collector
        self.wall_hunter = wall_hunter or LiquidityWallHunter(
            wall_multiple_threshold=self.config.wall_depth_ratio_threshold,
            min_persistence_sec=self.config.wall_min_persistence_sec,
        )
        self.new_market_scanner = new_market_scanner or NewMarketOpportunityScanner(
            max_listing_age_days=self.config.new_market_max_age_days,
            target_risk_usdt=self.config.target_trade_risk_usdt,
            max_margin_usdt=self.config.max_margin_usdt,
        )
        self.catalyst_engine = catalyst_engine or NewsCatalystEngine()
        self.engine_telemetry: Dict[str, Any] = {
            "wall_hunter": {"invocations": 0, "persistent_detected": 0, "spoof_detected": 0},
            "new_market_scanner": {"evaluated": 0, "new_markets_found": 0, "infeasible_rejected": 0},
            "news_catalyst": {"queries": 0, "aligned_confirmed": 0, "vetoed": 0, "unverified_ignored": 0},
            "rejections_by_reason": {},
        }

    def size_position(
        self,
        entry_price: float,
        stop_loss: float,
        instrument: NormalizedInstrument,
        fee_rate: float = 0.0005,
    ) -> RiskAssessment:
        """Calculate position size and risk feasibility against Model B risk policy."""
        stop_dist = abs(entry_price - stop_loss)
        if stop_dist <= 0 or entry_price <= 0:
            return RiskAssessment(
                is_feasible=False,
                quantity=0.0,
                position_notional_usdt=0.0,
                target_isolated_margin_usdt=0.0,
                position_leverage=1.0,
                rejection_reasons=["Invalid entry or stop-loss price."],
            )

        # 1. Unconstrained sizing from target risk ($1.00 USDT)
        raw_qty = self.config.target_trade_risk_usdt / stop_dist

        # 2. Round to exchange step size
        step = instrument.step_size or 0.001
        step_precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
        quantity = round(math.floor(raw_qty / step) * step, step_precision)

        if quantity < instrument.min_qty:
            quantity = instrument.min_qty

        notional = quantity * entry_price
        rejections: List[str] = []

        # 3. Minimum notional check
        if notional < instrument.min_notional:
            rejections.append(
                f"Position notional ${notional:.2f} below exchange minimum ${instrument.min_notional:.2f}."
            )

        # 4. Leverage and margin check (clamped to max 10.0x per Model B risk policy)
        leverage = min(max(1.0, float(self.config.max_leverage)), 10.0)
        required_margin = notional / leverage if leverage > 0 else notional

        if required_margin > self.config.max_margin_usdt:
            rejections.append(
                f"Required margin ${required_margin:.2f} exceeds Model B cap ${self.config.max_margin_usdt:.2f}."
            )

        # 5. True loss calculation including maker entry + taker exit fees + adverse slippage
        price_loss = quantity * stop_dist
        entry_fee = notional * fee_rate
        exit_fee = (quantity * stop_loss) * fee_rate
        est_slippage = notional * 0.0005
        est_funding = notional * 0.0001
        estimated_loss = price_loss + entry_fee + exit_fee + est_slippage + est_funding

        # Budget check: If minimum lot size forces risk to exceed 1.25x target, reject as INFEASIBLE
        max_allowed_loss = self.config.target_trade_risk_usdt * 1.25
        if estimated_loss > max_allowed_loss:
            rejections.append(
                f"Estimated loss ${estimated_loss:.2f} exceeds risk budget cap ${max_allowed_loss:.2f}."
            )

        is_feasible = len(rejections) == 0

        return RiskAssessment(
            symbol=instrument.symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            stop_distance_pct=round(stop_dist / entry_price * 100.0, 4) if entry_price > 0 else 0.0,
            risk_budget_usdt=self.config.target_trade_risk_usdt,
            position_notional_usdt=round(notional, 4),
            quantity=quantity,
            required_margin_usdt=round(required_margin, 4),
            effective_leverage=round(notional / self.config.target_account_equity_usdt, 2),
            position_leverage=leverage,
            estimated_fee_usdt=round(entry_fee + exit_fee, 4),
            estimated_slippage_usdt=round(est_slippage, 4),
            estimated_funding_usdt=round(est_funding, 4),
            is_feasible=is_feasible,
            infeasible_reason="; ".join(rejections) if rejections else None,
            target_isolated_margin_usdt=round(required_margin, 4),
            estimated_loss_usdt=round(estimated_loss, 4),
            estimated_loss_pct=round(estimated_loss / self.config.target_account_equity_usdt * 100.0, 4),
            rejection_reasons=rejections,
        )

    def evaluate_symbol(
        self,
        symbol: str,
        candles: List[Candle],
        instrument: NormalizedInstrument,
        now_ms: Optional[int] = None,
        order_book: Optional[NormalizedOrderBook] = None,
        listing_time_ms: Optional[int] = None,
        candles_4h: Optional[List[Candle]] = None,
    ) -> Optional[SetupCandidate]:
        """Evaluate a single symbol's 1H candles, L2 depth, resting walls, and catalysts."""
        # Check symbol blacklist (e.g. structural chop pairs)
        blacklist = [s.upper() for s in getattr(self.config, "symbol_blacklist", []) or []]
        if symbol.upper() in blacklist:
            return None

        if not self.liquidity_collector:
            return None

        current_ts = now_ms if now_ms is not None else int(time.time() * 1000)

        # 1. Fetch liquidity snapshot
        snapshot = self.liquidity_collector.collect_for_symbol(
            symbol=symbol,
            candle=candles[-1] if candles else None,
        )
        if not snapshot:
            return None

        # Check volume screen
        vol_24h = instrument.volume_24h_usdt or 0.0
        if vol_24h > 0 and vol_24h < self.config.min_24h_volume_usdt:
            return None

        # Check spread screen
        if snapshot.spread_bps > self.config.max_spread_bps:
            return None

        # 2. Get order book for wall hunter and new market scanner (reuse book from collector to avoid duplicate fetch)
        book = order_book
        if book is None and self.liquidity_collector and hasattr(self.liquidity_collector, "get_latest_book"):
            cand_book = self.liquidity_collector.get_latest_book(symbol)
            if isinstance(cand_book, NormalizedOrderBook):
                book = cand_book
        if book is None and self.exchange_service:
            books_map = self.exchange_service.get_aggregated_order_books(symbol)
            book = books_map.get(instrument.exchange_name) or books_map.get("asterdex")

        # 3. Engine 1: Liquidity Wall Hunter evaluation
        wall_evidence: Dict[str, Any] = {"status": "UNAVAILABLE", "persistent_walls": [], "spoof_pulled": False}
        if book is not None:
            self.engine_telemetry["wall_hunter"]["invocations"] += 1
            self.wall_hunter.analyze_order_book(book, now_ms=current_ts)
            persistent = self.wall_hunter.get_persistent_walls(symbol)
            active = self.wall_hunter._active_walls.get(symbol.upper(), [])
            spoof_detected = any(w.is_canceled_pre_touch for w in active)
            if persistent:
                self.engine_telemetry["wall_hunter"]["persistent_detected"] += len(persistent)
            if spoof_detected:
                self.engine_telemetry["wall_hunter"]["spoof_detected"] += 1
            wall_evidence = {
                "status": "CONFIRMED" if persistent else ("SPOOF_DETECTED" if spoof_detected else "NEUTRAL"),
                "persistent_walls": [
                    {
                        "side": w.side,
                        "price": w.price_level,
                        "notional": w.depth_notional_usdt,
                        "multiple": w.multiple_of_baseline,
                        "persistence_sec": w.persistence_seconds,
                    }
                    for w in persistent
                ],
                "spoof_pulled": spoof_detected,
            }

        # 4. Engine 2: New Market Opportunity Scanner evaluation
        effective_listing_ms = getattr(instrument, "listing_time_ms", None) or listing_time_ms
        new_market_res = self.new_market_scanner.evaluate_instrument(
            instrument=instrument,
            order_book=book,
            listing_time_ms=effective_listing_ms,
            now_ms=current_ts,
        )
        self.engine_telemetry["new_market_scanner"]["evaluated"] += 1
        if new_market_res.listing_age_days is not None and new_market_res.listing_age_days <= 14.0:
            self.engine_telemetry["new_market_scanner"]["new_markets_found"] += 1
        if not new_market_res.is_feasible:
            self.engine_telemetry["new_market_scanner"]["infeasible_rejected"] += 1

        # 5. Engine 3: News & Catalyst Engine evaluation
        catalyst_bias_info = self.catalyst_engine.evaluate_catalyst_bias(symbol)
        self.engine_telemetry["news_catalyst"]["queries"] += 1
        unverified = [e for e in self.catalyst_engine.get_catalysts_for_symbol(symbol) if not e.is_eligible_for_trade_bias]
        if unverified:
            self.engine_telemetry["news_catalyst"]["unverified_ignored"] += len(unverified)

        # 6. Run structural trigger-timeframe + liquidity setup evaluator
        trigger_tf = str(getattr(self.config, "trigger_timeframe", "1h") or "1h")
        trend_tf = str(getattr(self.config, "trend_timeframe", "4h") or "4h")
        signal = evaluate_liquidity_setup(
            symbol=symbol,
            candles=candles,
            snapshot=snapshot,
            min_imbalance=self.config.min_imbalance_ratio,
            min_absorption=self.config.min_absorption_score,
            max_spread_bps=self.config.max_spread_bps,
            timeframe_label=trigger_tf.upper(),
            min_rejection_wick_ratio=getattr(self.config, "min_rejection_wick_ratio", 0.0),
            min_volume_sma_mult=getattr(self.config, "min_volume_sma_mult", 0.0),
            tp1_rr_multiple=float(getattr(self.config, "tp1_rr_multiple", 1.50) or 1.50),
            require_confirmed_body=getattr(self.config, "require_confirmed_body", True),
            allowed_directions=getattr(self.config, "allowed_directions", None),
        )
        if not signal:
            return None

        context = closed_context(candles, candles_4h, getattr(self.config, "atr_period", 14), now_ms=current_ts)
        regime_rejection = None
        if signal.direction == Direction.SHORT and getattr(self.config, "short_regime_gate_enabled", False):
            bullish_catalyst = bool(catalyst_bias_info and catalyst_bias_info.get("bias") == "BULLISH")
            allowed, regime_rejection = short_gate(context, snapshot.cvd_1h, snapshot.imbalance_25bps, self.config.min_imbalance_ratio, bullish_catalyst)
        if context is None and getattr(self.config, "short_regime_gate_enabled", False) and signal.direction == Direction.SHORT:
            regime_rejection = regime_rejection or "short_htf_context_unavailable"

        # 7. Rejections: Feasibility, HTF Bias, Order-flow, and Catalysts
        rejection_reasons: List[str] = []
        if regime_rejection:
            rejection_reasons.append(regime_rejection)

        # Enforce NewMarketOpportunityScanner feasibility (e.g. min $5k depth within 10 bps)
        if not new_market_res.is_feasible:
            rejection_reasons.extend(new_market_res.rejection_reasons)

        # True 4H Structure & EMA20/EMA50 Bias
        bias_4h = MarketBias.NEUTRAL
        structure_1h = StructureState.SIDEWAYS
        if context is not None:
            bias_4h = context.get("bias_4h", MarketBias.NEUTRAL)
            structure_1h = context.get("structure_1h", StructureState.SIDEWAYS)
        else:
            if candles_4h and len(candles_4h) >= 20:
                closes4 = [c.close for c in candles_4h]
                e4_20 = ema(closes4, 20)
                e4_50 = ema(closes4, 50) if len(closes4) >= 50 else e4_20
                if e4_20 is not None:
                    if candles_4h[-1].close > e4_20 and (e4_50 is None or e4_20 >= e4_50):
                        bias_4h = MarketBias.BULLISH
                    elif candles_4h[-1].close < e4_20 and (e4_50 is None or e4_20 <= e4_50):
                        bias_4h = MarketBias.BEARISH
            if candles and len(candles) >= 20:
                closes1 = [c.close for c in candles]
                e1_20 = ema(closes1, 20)
                e1_50 = ema(closes1, 50) if len(closes1) >= 50 else e1_20
                if e1_20 is not None:
                    if candles[-1].close > e1_20 and (e1_50 is None or e1_20 >= e1_50):
                        structure_1h = StructureState.BULLISH
                    elif candles[-1].close < e1_20 and (e1_50 is None or e1_20 <= e1_50):
                        structure_1h = StructureState.BEARISH

        # Reject counter-trend setups strongly opposing 4H bias (unless bypassed by responsive V1-Pro)
        require_htf_ema = getattr(self.config, "require_htf_ema_alignment", True)
        if require_htf_ema:
            if signal.direction == Direction.LONG and bias_4h == MarketBias.BEARISH:
                rejection_reasons.append("COUNTER_TREND_4H_BEARISH_OPPOSES_LONG")
            elif signal.direction == Direction.SHORT and bias_4h == MarketBias.BULLISH:
                rejection_reasons.append("COUNTER_TREND_4H_BULLISH_OPPOSES_SHORT")

        # Order-Flow & Opposing Wall Veto
        req_side = "BID" if signal.direction == Direction.LONG else "ASK"
        opp_side = "ASK" if signal.direction == Direction.LONG else "BID"
        persistent_walls = wall_evidence.get("persistent_walls", [])
        supporting_wall_notional = sum(w["notional"] for w in persistent_walls if w.get("side") == req_side)
        opposing_wall_notional = sum(w["notional"] for w in persistent_walls if w.get("side") == opp_side)

        has_opposing_wall = opposing_wall_notional > 0 and (supporting_wall_notional == 0 or opposing_wall_notional > supporting_wall_notional * 1.5)
        has_opposing_imbalance = (signal.direction == Direction.LONG and snapshot.imbalance_25bps < -0.10) or (signal.direction == Direction.SHORT and snapshot.imbalance_25bps > 0.10)
        if has_opposing_wall or has_opposing_imbalance:
            rejection_reasons.append("OPPOSING_ORDER_FLOW_IMBALANCE")

        is_vetoed = False
        if catalyst_bias_info is not None:
            c_bias = catalyst_bias_info.get("bias")
            if signal.direction == Direction.LONG and c_bias == "BEARISH":
                is_vetoed = True
                rejection_reasons.append(
                    f"Vetoed by confirmed opposing catalyst: {catalyst_bias_info.get('category')} ({catalyst_bias_info.get('source')})"
                )
            elif signal.direction == Direction.SHORT and c_bias == "BULLISH":
                is_vetoed = True
                rejection_reasons.append(
                    f"Vetoed by confirmed opposing catalyst: {catalyst_bias_info.get('category')} ({catalyst_bias_info.get('source')})"
                )

        if is_vetoed:
            self.engine_telemetry["news_catalyst"]["vetoed"] += 1

        # 8. Sizing and risk assessment with TP1/TP2 rescaling
        if context and context.get("atr_1h") is not None:
            signal.stop_loss = enforce_stop_floor(signal.entry_price, signal.stop_loss, signal.direction, context["atr_1h"], self.config.min_stop_distance_pct, self.config.min_stop_atr_multiple)
        else:
            signal.stop_loss = enforce_stop_floor(signal.entry_price, signal.stop_loss, signal.direction, None, self.config.min_stop_distance_pct, 0.0)

        # Recalculate TP1/TP2 after stop floor enforcement to maintain favorable R:R
        tp1_mult = float(getattr(self.config, "tp1_rr_multiple", 1.50) or 1.50)
        widened_risk = abs(signal.entry_price - signal.stop_loss)
        if widened_risk > 0:
            if signal.direction == Direction.LONG:
                signal.take_profit_1 = round(signal.entry_price + (widened_risk * tp1_mult), 8)
                signal.take_profit_2 = round(max(signal.take_profit_2 or 0.0, signal.entry_price + (widened_risk * 2.5)), 8)
            else:
                signal.take_profit_1 = round(signal.entry_price - (widened_risk * tp1_mult), 8)
                signal.take_profit_2 = round(min(signal.take_profit_2 or 1e9, signal.entry_price - (widened_risk * 2.5)), 8)

        dynamic_gross_rr = round(abs(signal.take_profit_1 - signal.entry_price) / widened_risk, 4) if widened_risk > 0 else tp1_mult
        dynamic_net_rr = round(max(0.1, dynamic_gross_rr - 0.1), 4)

        assessment = self.size_position(
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            instrument=instrument,
        )
        if assessment.rejection_reasons:
            rejection_reasons.extend(assessment.rejection_reasons)

        for r in rejection_reasons:
            self.engine_telemetry["rejections_by_reason"][r] = self.engine_telemetry["rejections_by_reason"].get(r, 0) + 1

        # 9. Deterministic Confidence Aggregation
        conf = signal.confidence_score
        supporting_reasons = list(signal.reasons)

        # Wall confirmation / pre-touch cancellation adjustment
        if wall_evidence["status"] == "CONFIRMED":
            req_side = "BID" if signal.direction == Direction.LONG else "ASK"
            supporting_walls = [w for w in wall_evidence["persistent_walls"] if w["side"] == req_side]
            if supporting_walls:
                conf += 0.05
                supporting_reasons.append(
                    f"Persistent supporting {req_side} wall confirmed: {supporting_walls[0]['price']} ({supporting_walls[0]['persistence_sec']:.0f}s)"
                )
        elif wall_evidence["spoof_pulled"]:
            conf -= 0.10
            rejection_reasons.append("Pre-touch wall cancellation detected near entry")

        # Aligned confirmed catalyst boost
        if catalyst_bias_info is not None and not is_vetoed:
            c_bias = catalyst_bias_info.get("bias")
            if (signal.direction == Direction.LONG and c_bias == "BULLISH") or (signal.direction == Direction.SHORT and c_bias == "BEARISH"):
                conf += 0.10
                self.engine_telemetry["news_catalyst"]["aligned_confirmed"] += 1
                supporting_reasons.append(
                    f"Aligned confirmed catalyst: {catalyst_bias_info.get('category')} (confidence={catalyst_bias_info.get('confidence'):.2f})"
                )

        aggregated_conf = round(min(0.95, max(0.50, conf)), 2)
        is_feasible = assessment.is_feasible and not is_vetoed and not rejection_reasons
        status = CandidateStatus.READY if is_feasible else CandidateStatus.INFEASIBLE
        # Anchor candidate timestamp and logical setup ID to triggering candle timestamp
        anchor_candle_ms = getattr(signal, "timestamp_ms", None) or current_ts
        ts_ms = anchor_candle_ms
        active_model_id = str(getattr(self.config, "model_id", "MODEL_B") or "MODEL_B")
        active_cohort = str(getattr(self.config, "cohort_version", "MODEL_B_V2_REMEDIATED") or "MODEL_B_V2_REMEDIATED")
        expiry_bars = getattr(self.config, "pending_expiry_bars", None)
        if trigger_tf.lower() == "15m":
            bar_duration_ms = 15 * 60 * 1000
        elif trigger_tf.lower() == "5m":
            bar_duration_ms = 5 * 60 * 1000
        else:
            bar_duration_ms = 3600 * 1000
        if expiry_bars is not None and int(expiry_bars) > 0:
            expires_at = ts_ms + int(expiry_bars) * bar_duration_ms
        else:
            expires_at = ts_ms + int(self.config.pending_order_ttl_hours * 3600 * 1000)
        cid_prefix = "MCAND_B_V3" if active_model_id == "MODEL_B_V3" else ("MCAND_B_FAST" if active_model_id == "MODEL_B_FAST" else "MCAND_B")
        cid = f"{cid_prefix}_{symbol}_{signal.direction.value}_{anchor_candle_ms}"

        candidate = SetupCandidate(
            candidate_id=cid,
            symbol=symbol,
            timestamp_ms=ts_ms,
            direction=signal.direction,
            bias_4h=bias_4h,
            structure_1h=structure_1h,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            gross_reward_to_risk=dynamic_gross_rr,
            net_reward_to_risk=dynamic_net_rr,
            invalidation_price=signal.stop_loss,
            invalidation_reason="Stop loss breached",
            smc_evidence=[f"Liquidity sweep and reclaim on {trigger_tf.upper()}"],
            snr_evidence=[f"Order book wall status: {wall_evidence['status']}"],
            supporting_reasons=supporting_reasons,
            opposing_reasons=rejection_reasons,
            risk_assessment=assessment,
            ml_probability=None,
            calibration_status="RULE_BASED_UNCALIBRATED",
            status=status,
            rejection_reasons=rejection_reasons,
            entry_category=EntryCategory.REVERSAL,
            model_id=active_model_id,
            expires_at_ms=expires_at,
            ai_analysis={
                "strategy": "Model_B_V1_Pro" if (active_cohort == "MODEL_B_V1_PRO" or active_model_id == "MODEL_B_V3") else ("Model_B_Fast_Scalp" if active_model_id == "MODEL_B_FAST" else "Model_B_Liquidity_Sweep"),
                "cohort_version": active_cohort,
                "trigger_timeframe": trigger_tf,
                "trend_timeframe": trend_tf,
                "signal_type": signal.signal_type,
                "reasons": supporting_reasons,
                "spread_bps": snapshot.spread_bps,
                "imbalance_25bps": snapshot.imbalance_25bps,
                "cvd_1h": snapshot.cvd_1h,
                "absorption_score": snapshot.absorption_score,
                "indicator_context": context or {"status": "UNAVAILABLE"},
                "exchange": snapshot.exchange_name,
                "engine_evidence": {
                    "wall_hunter": wall_evidence,
                    "new_market_scanner": {
                        "is_new_market": new_market_res.listing_age_days is not None and new_market_res.listing_age_days <= 14.0,
                        "is_feasible": new_market_res.is_feasible,
                        "rejection_reasons": new_market_res.rejection_reasons,
                    },
                    "news_catalyst": {
                        "status": "CONFIRMED_ALIGNED" if (catalyst_bias_info and not is_vetoed) else ("VETOED" if is_vetoed else "UNAVAILABLE"),
                        "details": catalyst_bias_info,
                    },
                },
            },
        )
        return candidate
