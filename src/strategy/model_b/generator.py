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
    ) -> Optional[SetupCandidate]:
        """Evaluate a single symbol's 1H candles, L2 depth, resting walls, and catalysts."""
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

        # 2. Get order book for wall hunter and new market scanner
        book = order_book
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
        new_market_res = self.new_market_scanner.evaluate_instrument(
            instrument=instrument,
            order_book=book,
            listing_time_ms=listing_time_ms,
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

        # 6. Run structural 1H + liquidity setup evaluator
        signal = evaluate_liquidity_setup(
            symbol=symbol,
            candles=candles,
            snapshot=snapshot,
            min_imbalance=self.config.min_imbalance_ratio,
            min_absorption=self.config.min_absorption_score,
            max_spread_bps=self.config.max_spread_bps,
        )
        if not signal:
            return None

        # 7. Catalyst Conflict Gate (opposing confirmed news vetoes setup)
        rejection_reasons: List[str] = []
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

        # 8. Sizing and risk assessment
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
        is_feasible = assessment.is_feasible and not is_vetoed
        status = CandidateStatus.READY if is_feasible else CandidateStatus.INFEASIBLE
        # Anchor candidate timestamp and logical setup ID to triggering candle timestamp
        anchor_candle_ms = getattr(signal, "timestamp_ms", None) or current_ts
        ts_ms = anchor_candle_ms
        expires_at = ts_ms + int(self.config.pending_order_ttl_hours * 3600 * 1000)
        cid = f"MCAND_B_{symbol}_{signal.direction.value}_{anchor_candle_ms}"

        candidate = SetupCandidate(
            candidate_id=cid,
            symbol=symbol,
            timestamp_ms=ts_ms,
            direction=signal.direction,
            bias_4h=MarketBias.BULLISH if signal.direction == Direction.LONG else MarketBias.BEARISH,
            structure_1h=StructureState.BULLISH if signal.direction == Direction.LONG else StructureState.BEARISH,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            gross_reward_to_risk=1.5,
            net_reward_to_risk=1.4,
            invalidation_price=signal.stop_loss,
            invalidation_reason="Stop loss breached",
            smc_evidence=["Liquidity sweep and reclaim on 1H"],
            snr_evidence=[f"Order book wall status: {wall_evidence['status']}"],
            supporting_reasons=supporting_reasons,
            opposing_reasons=rejection_reasons,
            risk_assessment=assessment,
            ml_probability=aggregated_conf,
            calibration_status="CALIBRATED",
            status=status,
            rejection_reasons=rejection_reasons,
            entry_category=EntryCategory.REVERSAL,
            model_id="MODEL_B",
            expires_at_ms=expires_at,
            ai_analysis={
                "strategy": "Model_B_Liquidity_Sweep",
                "signal_type": signal.signal_type,
                "reasons": supporting_reasons,
                "spread_bps": snapshot.spread_bps,
                "imbalance_25bps": snapshot.imbalance_25bps,
                "cvd_1h": snapshot.cvd_1h,
                "absorption_score": snapshot.absorption_score,
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
