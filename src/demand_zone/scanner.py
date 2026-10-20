"""AsterDEX Demand-Zone LONG Scanner Master Engine.

Orchestrates real-time scanning of AsterDEX USDT perpetual contracts:
1. Dynamic Universe Discovery & 24H Volume Screening (>= $1M - $5M USDT turnover)
2. BTC Market Context Evaluation (Ref zone: $79,864 - $80,400; Policies A, B, C)
3. Technical Support & Demand-Zone Detection on Closed 1H Candles
4. $0.30 Risk Budget Position Sizing & Margin Feasibility (5x - 10x leverage)
5. Take-Profit Planning, Net R:R, and Resistance Obstruction Checks
6. Order-Book Depth, Resting Bid Walls, and Spoofing Risk Scoring
7. Deterministic Statuses (READY, APPROACHING, WATCHLIST, INFEASIBLE, INVALIDATED, STALE, ERROR)
8. Isolated Database Persistence and Observability
"""

from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional, Tuple

from src.core.logger import logger
from src.core.types import Candle
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook
from src.data.database import DatabaseManager
from src.exchange.aster_adapter import AsterAdapter
from src.demand_zone.btc_context import BTCContextEvaluator
from src.demand_zone.order_book_analyzer import DemandZoneOrderBookAnalyzer
from src.demand_zone.risk_sizer import DemandZoneRiskSizer
from src.demand_zone.storage import DemandZoneStorage
from src.demand_zone.tp_planner import DemandZoneTPPlanner
from src.demand_zone.types import (
    BTCContextObservation,
    BTCZonePolicy,
    DemandZone,
    DemandZoneCandidate,
    DemandZoneCandidateStatus,
    DemandZoneConfig,
)
from src.demand_zone.zone_detector import DemandZoneDetector


class AsterDexDemandZoneScanner:
    """Master scanner for identifying actionable AsterDEX Demand-Zone LONG setups."""

    def __init__(
        self,
        db: DatabaseManager,
        adapter: Optional[AsterAdapter] = None,
        config: Optional[DemandZoneConfig] = None,
        storage: Optional[DemandZoneStorage] = None,
    ) -> None:
        self.db = db
        self.adapter = adapter or AsterAdapter()
        self.config = config or DemandZoneConfig()
        self.storage = storage or DemandZoneStorage(db)

        # Sub-engines
        self.btc_evaluator = BTCContextEvaluator(self.config)
        self.zone_detector = DemandZoneDetector(self.config)
        self.risk_sizer = DemandZoneRiskSizer(self.config)
        self.tp_planner = DemandZoneTPPlanner(self.config)
        self.ob_analyzer = DemandZoneOrderBookAnalyzer(self.config)

        # In-memory candidate cache & telemetry
        self.latest_candidates: List[DemandZoneCandidate] = []
        self.latest_btc_context: Optional[BTCContextObservation] = None
        self.latest_telemetry: Dict[str, Any] = {}

    def scan_universe(
        self,
        max_symbols_to_evaluate: int = 50,
        candles_by_symbol: Optional[Dict[str, List[Candle]]] = None,
        books_by_symbol: Optional[Dict[str, NormalizedOrderBook]] = None,
        now_ms: Optional[int] = None,
    ) -> List[DemandZoneCandidate]:
        """Execute a full demand-zone scanning cycle across AsterDEX USDT perpetuals."""
        t_start = time.time()
        current_time_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        dt_utc = datetime.fromtimestamp(current_time_ms / 1000.0, tz=timezone.utc).isoformat()

        logger.info("AsterDexDemandZoneScanner: Starting demand-zone LONG scan...")

        # 1. Discover all active AsterDEX USDT perpetual instruments
        try:
            instruments = self.adapter.discover_instruments()
        except Exception as e:
            logger.warning(f"AsterDexDemandZoneScanner: Failed to discover instruments: {e}")
            instruments = []

        # 2. Fetch live bulk tickers (instant Stage 1 volume & live spread screen)
        try:
            bulk_snaps = self.adapter.get_bulk_market_snapshot()
        except Exception as e:
            logger.warning(f"AsterDexDemandZoneScanner: Failed to fetch bulk tickers: {e}")
            bulk_snaps = {}

        # 3. Evaluate BTC Market Context
        btc_snap = bulk_snaps.get("BTCUSDT", {})
        btc_price = float(btc_snap.get("last_price") or btc_snap.get("mid_price") or 80000.0)
        btc_candles = (candles_by_symbol.get("BTCUSDT") if candles_by_symbol else None)
        if not btc_candles:
            try:
                btc_candles = self.adapter.get_klines("BTCUSDT", "1h", limit=30)
            except Exception:
                btc_candles = []

        btc_context = self.btc_evaluator.evaluate(
            current_btc_price=btc_price,
            candles_1h=btc_candles,
            now_ms=current_time_ms,
        )
        self.latest_btc_context = btc_context

        # 4. Stage 1 Screening: Filter by 24H Quote Turnover and Max Spread
        eligible_symbols: List[Tuple[NormalizedInstrument, Dict[str, Any]]] = []
        inst_map = {inst.symbol.upper(): inst for inst in instruments}

        rejections_summary: Dict[str, int] = {
            "BELOW_MIN_VOLUME": 0,
            "EXCESSIVE_SPREAD": 0,
            "INACTIVE_CONTRACT": 0,
            "MISSING_PRICE_DATA": 0,
            "NO_VALID_DEMAND_ZONE": 0,
            "STOP_TOO_WIDE_INFEASIBLE": 0,
            "BELOW_MIN_NOTIONAL_INFEASIBLE": 0,
            "SUB_THRESHOLD_RR_INFEASIBLE": 0,
            "OBSTRUCTED_RESISTANCE": 0,
            "ZONE_INVALIDATED_BROKEN": 0,
        }

        for sym, snap in bulk_snaps.items():
            inst = inst_map.get(sym)
            if not inst:
                continue

            vol_24h = float(snap.get("volume_24h_usdt") or inst.volume_24h_usdt or 0.0)
            spread_bps = float(snap.get("spread_bps") or 0.0)
            mid_px = float(snap.get("mid_price") or snap.get("last_price") or 0.0)

            # Check 24H volume minimum ($1,000,000 hard minimum)
            if vol_24h < self.config.min_24h_volume_usdt:
                rejections_summary["BELOW_MIN_VOLUME"] += 1
                continue

            # Check spread
            if spread_bps > self.config.max_spread_bps:
                rejections_summary["EXCESSIVE_SPREAD"] += 1
                continue

            if mid_px <= 0:
                rejections_summary["MISSING_PRICE_DATA"] += 1
                continue

            eligible_symbols.append((inst, snap))

        # Sort eligible symbols by 24h turnover descending
        eligible_symbols.sort(key=lambda x: float(x[1].get("volume_24h_usdt") or 0.0), reverse=True)
        screened_symbols = eligible_symbols[:max_symbols_to_evaluate]

        logger.info(
            f"AsterDexDemandZoneScanner: Discovered {len(instruments)} instruments. "
            f"{len(eligible_symbols)} satisfy >= ${self.config.min_24h_volume_usdt:,.0f} turnover. Evaluating top {len(screened_symbols)}..."
        )

        # 5. Evaluate each candidate symbol through demand zone, risk, target, and book engines
        generated_candidates: List[DemandZoneCandidate] = []

        for inst, snap in screened_symbols:
            sym = inst.symbol
            curr_px = float(snap.get("last_price") or snap.get("mid_price") or 0.0)
            vol_24h = float(snap.get("volume_24h_usdt") or inst.volume_24h_usdt or 0.0)
            spread_bps = float(snap.get("spread_bps") or 0.0)

            # Get 1H candles
            c1h = (candles_by_symbol.get(sym) if candles_by_symbol else None)
            if not c1h:
                try:
                    c1h = self.adapter.get_klines(sym, "1h", limit=100)
                except Exception as k_err:
                    logger.debug(f"Failed to fetch klines for {sym}: {k_err}")
                    c1h = []

            # Get Order Book
            book = (books_by_symbol.get(sym) if books_by_symbol else None)
            if not book:
                try:
                    book = self.adapter.get_order_book(sym, limit=50)
                except Exception as b_err:
                    logger.debug(f"Failed to fetch order book for {sym}: {b_err}")
                    book = None

            cand_record = self._evaluate_symbol_candidate(
                instrument=inst,
                current_price=curr_px,
                volume_24h=vol_24h,
                spread_bps=spread_bps,
                candles_1h=c1h,
                order_book=book,
                btc_context=btc_context,
                now_ms=current_time_ms,
                rejections_summary=rejections_summary,
            )
            if cand_record:
                generated_candidates.append(cand_record)

        # 6. Sort generated candidates by status priority and composite setup score
        status_rank = {
            DemandZoneCandidateStatus.READY: 1,
            DemandZoneCandidateStatus.APPROACHING: 2,
            DemandZoneCandidateStatus.WATCHLIST: 3,
            DemandZoneCandidateStatus.INFEASIBLE: 4,
            DemandZoneCandidateStatus.INVALIDATED: 5,
            DemandZoneCandidateStatus.STALE: 6,
            DemandZoneCandidateStatus.ERROR: 7,
        }
        generated_candidates.sort(
            key=lambda c: (status_rank.get(c.status, 9), -c.composite_setup_score)
        )

        # 7. Persist candidates to SQLite storage
        if generated_candidates:
            self.storage.save_candidates_batch(generated_candidates)

        # 8. Record telemetry
        duration_ms = (time.time() - t_start) * 1000.0
        ready_count = sum(1 for c in generated_candidates if c.status == DemandZoneCandidateStatus.READY)
        approaching_count = sum(1 for c in generated_candidates if c.status == DemandZoneCandidateStatus.APPROACHING)
        watchlist_count = sum(1 for c in generated_candidates if c.status == DemandZoneCandidateStatus.WATCHLIST)
        infeasible_count = sum(1 for c in generated_candidates if c.status == DemandZoneCandidateStatus.INFEASIBLE)

        scan_telemetry = {
            "scan_id": f"DZSCAN_{current_time_ms}",
            "timestamp_ms": current_time_ms,
            "datetime_utc": dt_utc,
            "duration_ms": round(duration_ms, 2),
            "total_markets_discovered": len(instruments),
            "liquid_markets_screened": len(screened_symbols),
            "ready_count": ready_count,
            "approaching_count": approaching_count,
            "watchlist_count": watchlist_count,
            "infeasible_count": infeasible_count,
            "btc_price": btc_context.current_price,
            "btc_zone_status": btc_context.zone_status.value,
            "btc_policy": btc_context.active_policy.value,
            "config": self.config.to_dict(),
            "rejections_summary": rejections_summary,
        }
        self.latest_telemetry = scan_telemetry
        self.latest_candidates = generated_candidates
        self.storage.record_scan_telemetry(scan_telemetry)

        logger.info(
            f"AsterDexDemandZoneScanner: Scan complete in {duration_ms:.1f}ms. "
            f"Found {ready_count} READY, {approaching_count} APPROACHING, "
            f"{watchlist_count} WATCHLIST, {infeasible_count} INFEASIBLE."
        )

        return generated_candidates

    def _evaluate_symbol_candidate(
        self,
        instrument: NormalizedInstrument,
        current_price: float,
        volume_24h: float,
        spread_bps: float,
        candles_1h: List[Candle],
        order_book: Optional[NormalizedOrderBook],
        btc_context: BTCContextObservation,
        now_ms: int,
        rejections_summary: Dict[str, int],
    ) -> Optional[DemandZoneCandidate]:
        """Evaluate a single liquid instrument across all demand-zone modules."""
        sym = instrument.symbol
        dt_utc = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).isoformat()
        evidence_points: List[str] = []
        rejection_reasons: List[str] = []
        machine_reasons: List[str] = []

        if not candles_1h or len(candles_1h) < 20:
            rejections_summary["NO_VALID_DEMAND_ZONE"] += 1
            return None

        # 1. Detect Support / Demand Zones
        zones = self.zone_detector.detect_demand_zones(
            symbol=sym,
            candles_1h=candles_1h,
            current_price=current_price,
        )
        best_zone = self.zone_detector.select_best_demand_zone(zones, current_price)

        if not best_zone:
            rejections_summary["NO_VALID_DEMAND_ZONE"] += 1
            return None

        # 2. Calculate Distance to Demand
        dist_to_demand_pct, ref_price = self.zone_detector.compute_distance_to_demand(
            current_price=current_price,
            zone=best_zone,
            reference_rule=self.config.zone_reference_rule,
        )

        # 3. Formulate Proposed Limit Entry and Stop Loss
        # Proposed entry at upper boundary of demand zone (where buyers first emerge)
        entry_price = best_zone.upper_price
        # Structural invalidation just below zone lower boundary
        invalidation_level = best_zone.invalidation_level
        # Initial stop-loss: Ensure at least preferred minimum buffer (e.g. 2.5% below entry),
        # or wider if structural invalidation requires it ("Do not force the stop into this range if structure requires wider")
        pref_stop_price = entry_price * (1.0 - (self.config.min_stop_loss_dist_pct / 100.0))
        stop_loss_price = min(invalidation_level, pref_stop_price)

        entry_rationale = (
            f"Limit entry at demand zone upper boundary ({entry_price:.4f}) guarded by "
            f"{best_zone.reaction_count} prior reactions and {best_zone.displacement_score:.1f}x ATR displacement."
        )

        # 4. Position Sizing & Risk Feasibility ($0.30 planned budget)
        risk_result = self.risk_sizer.calculate_sizing_and_risk(
            symbol=sym,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            instrument=instrument,
        )

        # 5. Take-Profit Planning & Target Feasibility
        tp_result = self.tp_planner.plan_targets(
            symbol=sym,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            quantity=risk_result.quantity,
            position_notional_usdt=risk_result.position_notional_usdt,
            candles_1h=candles_1h,
            current_price=current_price,
        )

        # 6. Order-Book & Liquidity Assessment
        ob_assessment = self.ob_analyzer.analyze_order_book(
            symbol=sym,
            book=order_book,
            intended_order_notional=risk_result.position_notional_usdt,
            demand_zone=best_zone,
            now_ms=now_ms,
        )

        # 7. Evidence Synthesis
        evidence_points.append(
            f"Validated 1H Demand Zone: [{best_zone.lower_price:.4f} - {best_zone.upper_price:.4f}] "
            f"(Score: {best_zone.quality_score:.0f}/100, Displacement: {best_zone.displacement_score:.1f}x ATR)"
        )
        evidence_points.append(f"Current Price: {current_price:.4f} ({dist_to_demand_pct:+.2f}% to demand zone)")
        if best_zone.liquidity_swept:
            evidence_points.append("Liquidity sweep & reclaim of prior key swing low confirmed.")
        if best_zone.is_fresh:
            evidence_points.append(f"Fresh demand zone with {best_zone.test_count} previous tests.")

        evidence_points.append(
            f"24H Turnover: ${volume_24h:,.0f} USDT | Spread: {spread_bps:.1f} bps | Book Depth Score: {ob_assessment.liquidity_quality_score:.0f}/100"
        )
        if ob_assessment.supporting_bid_walls:
            evidence_points.append(
                f"Resting bid wall confirmed near demand: ${ob_assessment.supporting_bid_walls[0]['notional_usdt']:,.0f} at {ob_assessment.supporting_bid_walls[0]['price']}"
            )
        if ob_assessment.spoofing_warning:
            evidence_points.append(f"Warning: {ob_assessment.spoofing_warning_reason}")

        # 8. Deterministic Status Determination
        status = DemandZoneCandidateStatus.READY

        # Invalidation check: Has price broken below zone lower boundary?
        if current_price < best_zone.lower_price * 0.998:
            status = DemandZoneCandidateStatus.INVALIDATED
            rejection_reasons.append(
                f"Zone invalidated: Current price ({current_price:.4f}) has closed below demand lower boundary ({best_zone.lower_price:.4f})."
            )
            machine_reasons.append("DEMAND_ZONE_BROKEN")
            rejections_summary["ZONE_INVALIDATED_BROKEN"] += 1

        # Risk feasibility check
        elif not risk_result.is_feasible:
            status = DemandZoneCandidateStatus.INFEASIBLE
            rejection_reasons.extend(risk_result.infeasible_reasons)
            if any("minimum" in r for r in risk_result.infeasible_reasons):
                machine_reasons.append("MIN_NOTIONAL_BREACH")
                rejections_summary["BELOW_MIN_NOTIONAL_INFEASIBLE"] += 1
            if any("exceeds hard risk cap" in r for r in risk_result.infeasible_reasons):
                machine_reasons.append("STOP_TOO_WIDE")
                rejections_summary["STOP_TOO_WIDE_INFEASIBLE"] += 1

        # Target feasibility check
        elif not tp_result.is_feasible:
            status = DemandZoneCandidateStatus.INFEASIBLE
            rejection_reasons.extend(tp_result.infeasible_reasons)
            if tp_result.is_obstructed_by_resistance:
                machine_reasons.append("RESISTANCE_OBSTRUCTION")
                rejections_summary["OBSTRUCTED_RESISTANCE"] += 1
            else:
                machine_reasons.append("SUB_THRESHOLD_RR")
                rejections_summary["SUB_THRESHOLD_RR_INFEASIBLE"] += 1

        # Proximity Check: Preferred Distance 1.5% to 3.5%
        elif dist_to_demand_pct > self.config.max_distance_to_demand_pct:
            # Setup is valid, but price is still approaching from above
            status = DemandZoneCandidateStatus.APPROACHING
            rejection_reasons.append(
                f"Approaching demand: Distance ({dist_to_demand_pct:.2f}%) exceeds preferred entry threshold ({self.config.max_distance_to_demand_pct:.1f}%)."
            )
            machine_reasons.append("OUTSIDE_ENTRY_PROXIMITY")

        elif dist_to_demand_pct < 0.0:
            # Price has dipped inside the demand zone
            status = DemandZoneCandidateStatus.READY
            evidence_points.append(f"Price currently inside demand zone ({dist_to_demand_pct:+.2f}%). Immediate entry zone.")

        else:
            # Within preferred approach range (1.5% to 3.5% or <= 1.5%)
            status = DemandZoneCandidateStatus.READY

        # BTC Context Policy Check
        if status == DemandZoneCandidateStatus.READY and not btc_context.allows_ready_status:
            status = DemandZoneCandidateStatus.WATCHLIST
            rejection_reasons.append(
                f"Held in WATCHLIST by BTC Context Policy ({btc_context.active_policy.value}): {btc_context.status_explanation}"
            )
            machine_reasons.append("BTC_CONTEXT_CONFIRMATION_PENDING")

        # 9. Composite Setup Score (0 - 100)
        # Weighted sum: 40% Zone Quality + 25% Liquidity Score + 20% Net R:R + 15% BTC Policy Boost
        rr_score = min(20.0, (tp_result.net_reward_to_risk / 2.5) * 20.0)
        base_score = (best_zone.quality_score * 0.40) + (ob_assessment.liquidity_quality_score * 0.25) + rr_score
        composite_score = round(min(100.0, base_score + btc_context.priority_boost), 1)

        # 10. Generate Candidate Object
        # Anchor candidate ID to symbol, direction, and demand zone timestamp to prevent multi-pass duplicates
        cand_id = f"DZCAND_{sym}_LONG_{best_zone.formation_timestamp_ms}"

        return DemandZoneCandidate(
            candidate_id=cand_id,
            symbol=sym,
            timestamp_ms=now_ms,
            datetime_utc=dt_utc,
            status=status,
            current_price=round(current_price, 6),
            demand_zone_upper=best_zone.upper_price,
            demand_zone_lower=best_zone.lower_price,
            demand_zone_reference=ref_price,
            distance_to_demand_pct=dist_to_demand_pct,
            proposed_entry_price=round(entry_price, 6),
            entry_rationale=entry_rationale,
            proposed_stop_loss_price=round(stop_loss_price, 6),
            stop_loss_distance_pct=risk_result.stop_distance_pct,
            structural_invalidation_level=invalidation_level,
            take_profit_1=tp_result.take_profit_1,
            take_profit_2=tp_result.take_profit_2,
            risk_budget_usdt=risk_result.risk_budget_usdt,
            position_quantity=risk_result.quantity,
            position_notional_usdt=risk_result.position_notional_usdt,
            required_margin_usdt=risk_result.required_margin_usdt,
            estimated_leverage=risk_result.estimated_leverage,
            total_estimated_loss_usdt=risk_result.total_estimated_loss_usdt,
            gross_reward_to_risk=tp_result.gross_reward_to_risk,
            net_reward_to_risk=tp_result.net_reward_to_risk,
            break_even_price=tp_result.break_even_price,
            estimated_net_pnl_tp1_usdt=tp_result.estimated_net_pnl_tp1_usdt,
            volume_24h_usdt=volume_24h,
            bid_ask_spread_bps=spread_bps,
            liquidity_quality_score=ob_assessment.liquidity_quality_score,
            demand_zone_score=best_zone.quality_score,
            composite_setup_score=composite_score,
            btc_current_price=btc_context.current_price,
            btc_zone_status=btc_context.zone_status.value,
            btc_policy_used=btc_context.active_policy.value,
            evidence_points=evidence_points,
            rejection_reasons=rejection_reasons,
            machine_reasons=machine_reasons,
            details={
                "demand_zone": best_zone.to_dict(),
                "risk_sizing": risk_result.to_dict(),
                "target_planning": tp_result.to_dict(),
                "order_book_assessment": ob_assessment.to_dict(),
                "btc_context": btc_context.to_dict(),
            },
        )
