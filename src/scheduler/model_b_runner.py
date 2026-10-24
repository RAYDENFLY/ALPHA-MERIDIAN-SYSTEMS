"""Model B Scheduler and Execution Coordinator.

Orchestrates multi-exchange discovery, 1H candle evaluation, liquidity depth ingestion,
Model B setup generation, risk-gated paper execution, and lifecycle monitoring.
Completely isolated from Model A.
"""

from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import threading
import time

from src.core.logger import logger
from src.core.types import Candle, CandidateStatus, Direction, ExecutionStatus, SetupCandidate, TradeRecord
from src.data.database import DatabaseManager
from src.catalyst.engine import NewsCatalystEngine
from src.exchange.base import BaseExchangeAdapter
from src.exchange.new_market_scanner import NewMarketOpportunityScanner
from src.exchange.normalized import NormalizedInstrument
from src.exchange.service import MultiExchangeService
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperOrder, PaperOrderStatus, PaperPosition
from src.journal.repository import JournalRepository
from src.liquidity.collector import LiquidityCollector
from src.liquidity.wall_hunter import LiquidityWallHunter
from src.risk.model_b_risk import ModelBRiskManager
from src.scheduler.scanner_telemetry import ScannerTelemetryTracker
from src.strategy.model_b.generator import ModelBStrategyGenerator
from src.strategy.model_b.risk_controls import cooldown_clear
from src.research import ModelBShadowEvaluator
from config.settings import ModelBConfig, PaperExecutionConfig


class ModelBRunner:
    """Coordinator for Model B Aggressive Liquidity Research Engine."""

    def __init__(
        self,
        db: DatabaseManager,
        journal: JournalRepository,
        exchange: Optional[BaseExchangeAdapter] = None,
        config: Optional[ModelBConfig] = None,
        paper_config: Optional[PaperExecutionConfig] = None,
        exchange_service: Optional[MultiExchangeService] = None,
        risk_manager: Optional[ModelBRiskManager] = None,
        paper_engine: Optional[PaperExecutionEngine] = None,
        wall_hunter: Optional[LiquidityWallHunter] = None,
        new_market_scanner: Optional[NewMarketOpportunityScanner] = None,
        catalyst_engine: Optional[NewsCatalystEngine] = None,
        generator: Optional[ModelBStrategyGenerator] = None,
        shadow_evaluator: Optional[ModelBShadowEvaluator] = None,
    ) -> None:
        self.db = db
        self.journal = journal
        self.exchange = exchange
        self.config = config or ModelBConfig()
        self.paper_config = paper_config or PaperExecutionConfig()

        self.exchange_service = exchange_service or MultiExchangeService()
        self.liquidity_collector = LiquidityCollector(db=self.db, exchange_service=self.exchange_service)

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

        self.generator = generator or ModelBStrategyGenerator(
            config=self.config,
            exchange_service=self.exchange_service,
            liquidity_collector=self.liquidity_collector,
            wall_hunter=self.wall_hunter,
            new_market_scanner=self.new_market_scanner,
            catalyst_engine=self.catalyst_engine,
        )
        self.risk_manager = risk_manager or ModelBRiskManager(config=self.config)
        self.paper_engine = paper_engine or PaperExecutionEngine(
            db=self.db,
            journal=self.journal,
            risk_manager=self.risk_manager,
            config=self.paper_config,
            model_id="MODEL_B",
        )
        self.shadow_evaluator = shadow_evaluator or ModelBShadowEvaluator(db=self.db)

        self._lock = threading.Lock()
        self._is_running = False
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        # State tracking and engine telemetry
        self.last_scan_utc: Optional[str] = None
        self.last_successful_scan_utc: Optional[str] = None
        self.last_scan_status: str = "NEVER_RUN"
        self.scan_count: int = 0
        self.last_error: Optional[str] = None
        self.discovered_universe: Dict[str, List[NormalizedInstrument]] = {}
        self.latest_candidates: List[SetupCandidate] = []
        self.engine_telemetry: Dict[str, Any] = self.generator.engine_telemetry
        self.telemetry_tracker = ScannerTelemetryTracker()
        self._rotation_offset: int = 0
        self._rotation_pool_size: int = 0

    def discover_universe(self, force_refresh: bool = False) -> Dict[str, List[NormalizedInstrument]]:
        """Fetch instruments across all supported perpetual venues."""
        t0 = time.time()
        try:
            self.discovered_universe = self.exchange_service.discover_all_instruments(force_refresh=force_refresh)
            dur_ms = (time.time() - t0) * 1000.0
            if self.telemetry_tracker.current:
                self.telemetry_tracker.current.discovery_duration_ms = dur_ms
            return self.discovered_universe
        except Exception as err:
            logger.error(f"ModelBRunner: Universe discovery failed: {err}")
            return self.discovered_universe

    def get_focus_symbols(self, max_symbols: int = 25) -> List[NormalizedInstrument]:
        """Filter discovered universe down to high-volume, low-spread Tier 2 focus instruments."""
        if not self.discovered_universe:
            self.discover_universe()

        # Prioritize AsterDEX contracts, fallback to Binance
        instruments = self.discovered_universe.get("asterdex") or self.discovered_universe.get("binance") or []
        filtered: List[NormalizedInstrument] = []

        for inst in instruments:
            # Reject instruments where volume data is missing, malformed, or unmatched
            if inst.volume_status != "AVAILABLE" or inst.volume_24h_usdt is None:
                continue
            if inst.volume_24h_usdt >= self.config.min_24h_volume_usdt:
                filtered.append(inst)

        # Sort descending by 24h volume
        filtered.sort(key=lambda x: x.volume_24h_usdt or 0.0, reverse=True)
        return filtered[:max_symbols]

    def screen_and_rank_symbols(
        self,
        aster_instruments: List[NormalizedInstrument],
        now_ms: Optional[int] = None,
    ) -> Tuple[List[NormalizedInstrument], int, int]:
        """Stage 1: Fast bulk screen 100% of AsterDEX universe and select top anchors + rotated mid-caps.

        Returns:
            (target_instruments, total_bulk_screened, qualified_pool_count)
        """
        snapshots: Dict[str, Dict[str, Any]] = {}
        t_bulk = time.time()

        # Check bulk snapshot availability on AsterAdapter
        aster_adapter = getattr(self.exchange, "get_bulk_market_snapshot", None)
        if aster_adapter is None and hasattr(self.exchange_service, "aster"):
            aster_adapter = getattr(self.exchange_service.aster, "get_bulk_market_snapshot", None)

        if aster_adapter is not None:
            try:
                snapshots = aster_adapter()
                bulk_lat_ms = (time.time() - t_bulk) * 1000.0
                self.telemetry_tracker.record_request("asterdex:/fapi/v1/ticker/bulkTicker_and_24hr", bulk_lat_ms)
            except Exception as b_err:
                logger.debug(f"ModelBRunner: Bulk market snapshot failed: {b_err}")

        min_vol = getattr(self.config, "stage1_min_24h_volume_usdt", 50000.0)
        max_spread = getattr(self.config, "stage1_max_spread_bps", 35.0)

        qualified_instruments: List[NormalizedInstrument] = []

        if snapshots:
            # Stage 1: Screen 100% of discovered AsterDEX universe against bulk snapshot
            snap_clean = {k.upper().replace(".P", ""): v for k, v in snapshots.items()}
            for inst in aster_instruments:
                sym_clean = inst.symbol.upper().replace(".P", "")
                snap = snap_clean.get(sym_clean)
                if snap:
                    vol = snap.get("volume_24h_usdt", 0.0)
                    spread = snap.get("spread_bps", 0.0)
                    last_px = snap.get("last_price", 0.0)
                    if vol >= min_vol and spread <= max_spread and last_px > 0:
                        inst.volume_24h_usdt = vol
                        inst.volume_status = "AVAILABLE"
                        qualified_instruments.append(inst)
                elif inst.volume_status == "AVAILABLE" and (inst.volume_24h_usdt or 0.0) >= min_vol:
                    qualified_instruments.append(inst)
        else:
            # Fallback when bulk endpoint is unavailable (e.g. mock unit tests)
            for inst in aster_instruments:
                if inst.volume_status == "AVAILABLE" and (inst.volume_24h_usdt or 0.0) >= min_vol:
                    qualified_instruments.append(inst)

        # Sort qualified instruments descending by 24h volume
        qualified_instruments.sort(key=lambda x: x.volume_24h_usdt or 0.0, reverse=True)

        anchor_count = getattr(self.config, "anchor_symbol_count", 10)
        slice_size = getattr(self.config, "rotation_slice_size", 25)

        anchors = qualified_instruments[:anchor_count]
        rotation_pool = qualified_instruments[anchor_count:]
        self._rotation_pool_size = len(rotation_pool)

        rotating_slice: List[NormalizedInstrument] = []
        if rotation_pool:
            pool_len = len(rotation_pool)
            start_idx = self._rotation_offset % pool_len
            end_idx = start_idx + slice_size
            if end_idx <= pool_len:
                rotating_slice = rotation_pool[start_idx:end_idx]
            else:
                rotating_slice = rotation_pool[start_idx:] + rotation_pool[: (end_idx % pool_len)]
            self._rotation_offset = (start_idx + slice_size) % pool_len
            rotation_progress_pct = round((self._rotation_offset / pool_len) * 100.0, 1)
        else:
            rotation_progress_pct = 100.0

        if self.telemetry_tracker.current:
            self.telemetry_tracker.current.rotation_offset = self._rotation_offset
            self.telemetry_tracker.current.rotation_pool_size = self._rotation_pool_size
            self.telemetry_tracker.current.rotation_progress_pct = rotation_progress_pct

        # Combine anchors + rotating slice, preserving order and deduplicating
        seen_syms = set()
        target_list: List[NormalizedInstrument] = []
        for inst in anchors + rotating_slice:
            if inst.symbol not in seen_syms:
                seen_syms.add(inst.symbol)
                target_list.append(inst)

        # Include active monitoring symbols (open positions + pending orders) with highest priority
        pending_syms = {o.symbol for o in self.paper_engine.pending_orders.values()}
        open_syms = set(self.paper_engine.open_positions.keys())
        active_syms = pending_syms | open_syms

        inst_by_sym = {i.symbol: i for i in aster_instruments}
        priority_monitored: List[NormalizedInstrument] = []
        for sym in active_syms:
            if sym not in seen_syms:
                mon_inst = inst_by_sym.get(sym) or NormalizedInstrument(
                    symbol=sym,
                    exchange_name="asterdex",
                    base_asset=sym.replace("USDT", ""),
                    quote_asset="USDT",
                    min_qty=0.001,
                    step_size=0.001,
                    tick_size=0.01,
                    min_notional=5.0,
                    contract_type="PERPETUAL",
                    volume_status="AVAILABLE",
                )
                seen_syms.add(sym)
                priority_monitored.append(mon_inst)

        final_targets = priority_monitored + target_list
        return final_targets, len(snapshots), len(qualified_instruments)

    def _rehydrate_risk_counters(self, now_ms: Optional[int] = None) -> None:
        """Authoritatively rehydrate risk counters from SQLite journal using net PnL."""
        if self.risk_manager and self.journal:
            self.risk_manager.sync_state_from_journal(self.journal, now_ms=now_ms, model_id="MODEL_B")

    def run_scan_cycle(self, now_ms: Optional[int] = None) -> List[SetupCandidate]:
        """Execute a full Model B scan, candidate formulation, and paper order evaluation cycle."""
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            logger.info("ModelBRunner: Scan cycle skipped; another cycle is currently running.")
            return self.latest_candidates

        current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        self.last_scan_utc = datetime.now(timezone.utc).isoformat()
        self.last_scan_status = "RUNNING"
        self.scan_count += 1
        found_candidates: List[SetupCandidate] = []

        cycle_id = f"SCAN_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{self.scan_count}"
        tel = self.telemetry_tracker.start_cycle(cycle_id)

        try:
            # 1. Sync risk manager with SQLite journal
            self._rehydrate_risk_counters(now_ms=current_ms)

            if not self.discovered_universe:
                self.discover_universe()

            aster_all = self.discovered_universe.get("asterdex", [])
            tel.symbols_discovered_asterdex = len(aster_all)
            tel.symbols_discovered_total = sum(len(v) for v in self.discovered_universe.values())
            tel.symbols_eligible_asterdex = sum(1 for inst in aster_all if inst.volume_status == "AVAILABLE")
            tel.symbols_missing_volume = sum(1 for inst in aster_all if inst.volume_status != "AVAILABLE" or inst.volume_24h_usdt is None)

            # 2. Stage 1: Bulk screening and fair rotation (or focus symbols fallback)
            t_eval_start = time.time()
            if "get_focus_symbols" in self.__dict__:
                focus_instruments = self.get_focus_symbols(max_symbols=getattr(self.config, "max_deep_eval_symbols", 35))
                target_instruments = focus_instruments
                bulk_count = len(aster_all)
                qualified_count = len(focus_instruments)
            else:
                target_instruments, bulk_count, qualified_count = self.screen_and_rank_symbols(aster_all, now_ms=current_ms)

            tel.symbols_bulk_screened = bulk_count if bulk_count > 0 else len(aster_all)
            tel.symbols_screened = qualified_count
            tel.symbols_ranked = len(target_instruments)
            logger.info(f"ModelBRunner: Deep evaluating {len(target_instruments)} symbols (Anchors + Rotated slice)...")

            candles_by_symbol: Dict[str, List[Candle]] = {}
            max_workers = getattr(self.config, "scanner_max_workers", 8)

            def _eval_single(inst: NormalizedInstrument) -> Tuple[NormalizedInstrument, List[Candle], Optional[SetupCandidate], float]:
                sym = inst.symbol
                candles: List[Candle] = []
                candles_4h: List[Candle] = []
                kline_lat_ms = 0.0

                if self.exchange:
                    t_kline = time.time()
                    try:
                        candles = self.exchange.get_klines(sym, "1h", limit=50)
                        candles = [c for c in candles if getattr(c, "is_closed", True)]
                        kline_lat_ms = (time.time() - t_kline) * 1000.0
                    except Exception as k_err:
                        kline_lat_ms = (time.time() - t_kline) * 1000.0
                        logger.debug(f"ModelBRunner: Failed to fetch candles for {sym}: {k_err}")

                if not candles and self.db:
                    candles = self.db.get_candles(sym, "1h", limit=50)
                    candles = [c for c in candles if getattr(c, "is_closed", True)]
                if self.exchange:
                    try:
                        candles_4h = self.exchange.get_klines(sym, "4h", limit=50)
                        candles_4h = [c for c in candles_4h if getattr(c, "is_closed", True)]
                    except Exception as htf_err:
                        logger.debug(f"ModelBRunner: Failed to fetch 4H candles for {sym}: {htf_err}")
                if not candles_4h and self.db:
                    candles_4h = self.db.get_candles(sym, "4h", limit=50)
                    candles_4h = [c for c in candles_4h if getattr(c, "is_closed", True)]

                cand = None
                if len(candles) >= 20:
                    try:
                        cand = self.generator.evaluate_symbol(
                            symbol=sym,
                            candles=candles,
                            instrument=inst,
                            now_ms=current_ms,
                            candles_4h=candles_4h,
                        )
                    except Exception as gen_err:
                        logger.debug(f"ModelBRunner: Generator evaluation failed for {sym}: {gen_err}")

                return inst, candles, cand, kline_lat_ms

            # Stage 2: Bounded concurrent 1H candle + setup evaluation
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_eval_single, inst): inst for inst in target_instruments}
                for fut in as_completed(futures):
                    try:
                        inst, candles, candidate, k_lat = fut.result()
                    except Exception as fut_err:
                        logger.debug(f"ModelBRunner: Worker future failed: {fut_err}")
                        continue

                    sym = inst.symbol
                    if k_lat > 0:
                        self.telemetry_tracker.record_request("asterdex:/fapi/v1/klines", k_lat)

                    if inst.volume_status == "AVAILABLE":
                        tel.symbols_valid_ticker += 1

                    if len(candles) >= 20:
                        tel.symbols_valid_candles += 1
                        candles_by_symbol[sym] = candles
                        tel.symbols_evaluated += 1
                        tel.symbols_deep_evaluated += 1
                        tel.symbols_valid_l2 += 1

                        if candidate:
                            # Check active open positions and pending orders for this symbol
                            if sym in self.paper_engine.open_positions:
                                pos = self.paper_engine.open_positions[sym]
                                if candidate.candidate_id == pos.setup_id or not candidate.order_id:
                                    candidate.execution_status = ExecutionStatus.FILLED
                                    candidate.order_id = pos.order_id
                                if candidate.candidate_id != pos.setup_id:
                                    suppress_reason = f"Submission suppressed by deduplication: Active position already open on {sym}"
                                    if suppress_reason not in candidate.rejection_reasons:
                                        candidate.rejection_reasons.append(suppress_reason)
                            elif any(o.symbol == sym for o in self.paper_engine.pending_orders.values()):
                                pend_o = next(o for o in self.paper_engine.pending_orders.values() if o.symbol == sym)
                                if candidate.candidate_id == pend_o.candidate_id or not candidate.order_id:
                                    candidate.execution_status = ExecutionStatus.SUBMITTED
                                    candidate.order_id = pend_o.order_id
                                if candidate.candidate_id != pend_o.candidate_id:
                                    suppress_reason = f"Submission suppressed by deduplication: Active pending order already exists on {sym}"
                                    if suppress_reason not in candidate.rejection_reasons:
                                        candidate.rejection_reasons.append(suppress_reason)
                            else:
                                # Reconcile existing candidate execution state from DB to preserve state across scans
                                existing = self.journal.get_setup_by_id(candidate.candidate_id)
                                if existing:
                                    det = existing.get("details", {})
                                    existing_st = det.get("execution_status")
                                    if existing_st and existing_st != ExecutionStatus.NOT_SUBMITTED.value:
                                        try:
                                            candidate.execution_status = ExecutionStatus(existing_st)
                                        except Exception:
                                            pass
                                        candidate.order_id = det.get("order_id") or candidate.order_id
                                        if det.get("rejection_reasons"):
                                            for r in det["rejection_reasons"]:
                                                if r not in candidate.rejection_reasons:
                                                    candidate.rejection_reasons.append(r)

                            found_candidates.append(candidate)
                            self.journal.save_setup(candidate)

            # Ensure symbols with active pending orders or open positions also receive candles
            # even if they drop out of the top volume focus list
            pending_symbols = {o.symbol for o in self.paper_engine.pending_orders.values()}
            open_symbols = set(self.paper_engine.open_positions.keys())
            active_monitoring_symbols = (pending_symbols | open_symbols) - set(candles_by_symbol.keys())

            for sym in active_monitoring_symbols:
                candles: List[Candle] = []
                if self.exchange:
                    try:
                        candles = self.exchange.get_klines(sym, "1h", limit=50)
                    except Exception as k_err:
                        logger.debug(f"ModelBRunner: Failed to fetch monitoring candles for {sym}: {k_err}")
                if not candles:
                    candles = self.db.get_candles(sym, "1h", limit=50)
                if candles:
                    candles_by_symbol[sym] = candles

            # Deduplicate candidates by (symbol, direction) keeping highest confidence
            dedup_candidates: Dict[Tuple[str, Direction], SetupCandidate] = {}
            for cand in found_candidates:
                key = (cand.symbol, cand.direction)
                c_conf = cand.ml_probability or 0.0
                best_conf = dedup_candidates[key].ml_probability or 0.0 if key in dedup_candidates else -1.0
                if key not in dedup_candidates or c_conf > best_conf:
                    dedup_candidates[key] = cand

            # Submit deduplicated READY candidates to Model B Paper Engine
            for cand in dedup_candidates.values():
                if cand.status == CandidateStatus.READY:
                    if cand.direction == Direction.SHORT and getattr(self.config, "short_regime_gate_enabled", False):
                        indicator_context = (cand.ai_analysis or {}).get("indicator_context", {})
                        allowed, cooldown_reason = cooldown_clear(self.journal, cand.symbol, cand.direction, current_ms, getattr(self.config, "short_cooldown_minutes", 240), cand.entry_price, indicator_context.get("atr_1h"), getattr(self.config, "short_reentry_min_displacement_atr", 0.5))
                        if not allowed:
                            cand.status = CandidateStatus.REJECTED
                            cand.rejection_reasons.append(cooldown_reason)
                            self.journal.save_setup(cand)
                            continue
                    # Skip resubmission if candidate is already in flight, filled, or closed
                    if cand.execution_status in (ExecutionStatus.SUBMITTED, ExecutionStatus.FILLED, ExecutionStatus.CLOSED):
                        continue

                    order = self.paper_engine.submit_candidate(cand, now_ms=current_ms)
                    if order:
                        cand.execution_status = ExecutionStatus.SUBMITTED
                        cand.order_id = order.order_id
                        self.journal.save_setup(cand)
                    else:
                        # Inspect why candidate was not submitted
                        if cand.symbol in self.paper_engine.open_positions:
                            existing_pos = self.paper_engine.open_positions[cand.symbol]
                            cand.execution_status = ExecutionStatus.FILLED
                            cand.order_id = existing_pos.order_id
                            reason = f"Submission suppressed by deduplication: Active position already open on {cand.symbol}"
                            if reason not in cand.rejection_reasons:
                                cand.rejection_reasons.append(reason)
                            self.journal.save_setup(cand)
                        elif any(o.symbol == cand.symbol for o in self.paper_engine.pending_orders.values()):
                            existing_o = next(o for o in self.paper_engine.pending_orders.values() if o.symbol == cand.symbol)
                            cand.execution_status = ExecutionStatus.SUBMITTED
                            cand.order_id = existing_o.order_id
                            reason = f"Submission suppressed by deduplication: Active pending order already exists on {cand.symbol}"
                            if reason not in cand.rejection_reasons:
                                cand.rejection_reasons.append(reason)
                            self.journal.save_setup(cand)
                        else:
                            # Check if rejected by account risk gate
                            recent_orders = self.journal.get_paper_orders(symbol=cand.symbol, model_id="MODEL_B")
                            matching_rejected = next((o for o in recent_orders if o.get("candidate_id") == cand.candidate_id and o.get("status") == "REJECTED"), None)
                            if matching_rejected:
                                cand.execution_status = ExecutionStatus.REJECTED
                                cand.order_id = matching_rejected.get("order_id")
                                reason = matching_rejected.get("rejection_reason") or "Account risk limits exceeded."
                                if reason not in cand.rejection_reasons:
                                    cand.rejection_reasons.append(reason)
                                self.journal.save_setup(cand)
                            else:
                                can_open, r_reason = self.risk_manager.can_open_new_position(current_ms, direction=cand.direction)
                                if not can_open:
                                    cand.execution_status = ExecutionStatus.REJECTED
                                    reason = r_reason or "Account risk limits exceeded."
                                    if reason not in cand.rejection_reasons:
                                        cand.rejection_reasons.append(reason)
                                elif cand.expires_at_ms and current_ms > cand.expires_at_ms:
                                    cand.execution_status = ExecutionStatus.REJECTED
                                    reason = "Candidate TTL expired."
                                    if reason not in cand.rejection_reasons:
                                        cand.rejection_reasons.append(reason)
                                else:
                                    cand.execution_status = ExecutionStatus.NOT_SUBMITTED
                                    reason = "Waiting for entry trigger"
                                    if reason not in cand.rejection_reasons:
                                        cand.rejection_reasons.append(reason)
                                self.journal.save_setup(cand)

            # Forward shadow research evaluation across all variants (independent of active risk halts)
            try:
                for cand in dedup_candidates.values():
                    inelig_reason = None
                    if cand.rejection_reasons:
                        inelig_reason = "; ".join(cand.rejection_reasons)
                    elif cand.status != CandidateStatus.READY:
                        inelig_reason = f"Candidate status is {cand.status.value}"

                    c_1h = candles_by_symbol.get(cand.symbol, [])
                    ctx = {}
                    if cand.details_json:
                        try:
                            ctx.update(json.loads(cand.details_json))
                        except Exception:
                            pass

                    self.shadow_evaluator.record_and_evaluate_candidate(
                        candidate=cand,
                        context=ctx,
                        candles_1h=c_1h,
                        now_ms=current_ms,
                        active_ineligible_reason=inelig_reason,
                    )

                if candles_by_symbol:
                    self.shadow_evaluator.step_evaluations(candles_by_symbol, now_ms=current_ms)
            except Exception as shadow_err:
                logger.debug(f"ModelBRunner: Shadow research evaluation exception: {shadow_err}")

            # 4. Evaluate pending orders and open positions against latest candles
            if candles_by_symbol:
                terminal_orders = self.paper_engine.evaluate_pending_orders(candles_by_symbol, now_ms=current_ms)
                for term_order in terminal_orders:
                    self._sync_candidate_on_order_update(term_order)

                closed_trades = self.paper_engine.evaluate_open_positions(candles_by_symbol, now_ms=current_ms)
                for closed_trade in closed_trades:
                    self._sync_candidate_on_trade_close(closed_trade)

            # Record candidate breakdown in telemetry
            tel.candidates_found = len(found_candidates)
            tel.candidates_ready = sum(1 for c in found_candidates if c.status == CandidateStatus.READY)
            tel.candidates_infeasible = sum(1 for c in found_candidates if c.status != CandidateStatus.READY)
            for c in found_candidates:
                for r in c.rejection_reasons:
                    tel.rejections_by_reason[r] = tel.rejections_by_reason.get(r, 0) + 1
            tel.active_monitored_symbols_count = len(active_monitoring_symbols)
            tel.evaluation_duration_ms = (time.time() - t_eval_start) * 1000.0

            self.latest_candidates = list(dedup_candidates.values()) if dedup_candidates else found_candidates
            self.last_successful_scan_utc = datetime.now(timezone.utc).isoformat()
            self.last_scan_status = "SUCCESS"
            self.last_error = None
            logger.info(
                f"ModelBRunner: Scan cycle completed. Found {len(found_candidates)} candidates "
                f"({sum(1 for c in found_candidates if c.status == CandidateStatus.READY)} READY)."
            )

        except Exception as err:
            logger.error(f"ModelBRunner: Scan cycle failed: {err}", exc_info=True)
            self.last_scan_status = "FAILED"
            self.last_error = str(err)
        finally:
            self.telemetry_tracker.finish_cycle()
            self._lock.release()

        return self.latest_candidates

    def _sync_candidate_on_order_update(self, order: PaperOrder) -> None:
        """Update candidate execution_status in setup_candidates when order transitions."""
        if not order.candidate_id:
            return
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT details_json FROM setup_candidates WHERE candidate_id = ?", (order.candidate_id,))
                row = cur.fetchone()
                if row and row[0]:
                    details = json.loads(row[0])
                    if order.status == PaperOrderStatus.FILLED:
                        details["execution_status"] = ExecutionStatus.FILLED.value
                        details["filled_quantity"] = order.quantity
                        details["filled_at_ms"] = order.filled_time_ms
                    elif order.status == PaperOrderStatus.EXPIRED:
                        details["execution_status"] = ExecutionStatus.REJECTED.value
                        details["order_status"] = "EXPIRED"
                    elif order.status == PaperOrderStatus.INVALIDATED:
                        details["execution_status"] = ExecutionStatus.REJECTED.value
                        details["order_status"] = "INVALIDATED"
                    elif order.status == PaperOrderStatus.REJECTED:
                        details["execution_status"] = ExecutionStatus.REJECTED.value
                        details["order_status"] = "REJECTED"
                    details["order_id"] = order.order_id
                    conn.execute(
                        "UPDATE setup_candidates SET details_json = ? WHERE candidate_id = ?",
                        (json.dumps(details), order.candidate_id),
                    )
                    conn.commit()
        except Exception as sync_err:
            logger.debug(f"ModelBRunner: Failed to sync candidate on order update: {sync_err}")

    def _sync_candidate_on_trade_close(self, trade: TradeRecord) -> None:
        """Update candidate execution_status in setup_candidates when trade closes."""
        if not trade.setup_id:
            return
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT details_json FROM setup_candidates WHERE candidate_id = ?", (trade.setup_id,))
                row = cur.fetchone()
                if row and row[0]:
                    details = json.loads(row[0])
                    details["execution_status"] = ExecutionStatus.CLOSED.value
                    conn.execute(
                        "UPDATE setup_candidates SET details_json = ? WHERE candidate_id = ?",
                        (json.dumps(details), trade.setup_id),
                    )
                    conn.commit()
        except Exception as sync_err:
            logger.debug(f"ModelBRunner: Failed to sync candidate on trade close: {sync_err}")

    def start_background_loop(self, interval_seconds: float = 60.0) -> None:
        """Launch background polling thread for periodic 1H scans."""
        if self._is_running:
            return

        self._is_running = True
        self._stop_event.clear()

        def _loop() -> None:
            logger.info("ModelBRunner: Background loop started.")
            # Initial discovery
            self.discover_universe()

            while not self._stop_event.is_set():
                try:
                    self.run_scan_cycle()
                except Exception as loop_err:
                    logger.error(f"ModelBRunner: Loop iteration error: {loop_err}")

                # Sleep with interrupt check
                for _ in range(int(interval_seconds)):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1.0)

            logger.info("ModelBRunner: Background loop terminated.")

        self._worker_thread = threading.Thread(target=_loop, daemon=True, name="ModelBRunnerWorker")
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop background worker loop gracefully."""
        self._is_running = False
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)

    def get_mark_price(self, symbol: str) -> Tuple[Optional[float], Optional[int], str]:
        """Retrieve fresh mark price across all supported multi-exchange adapters.
        
        Returns:
            (mark_price, timestamp_ms, freshness_status)
            where freshness_status is one of 'FRESH', 'STALE', or 'MISSING'.
        """
        now_ms = int(time.time() * 1000)
        
        # 1. Query AsterAdapter via MultiExchangeService
        if hasattr(self, 'exchange_service') and self.exchange_service and self.exchange_service.aster:
            try:
                p_index = self.exchange_service.aster.get_premium_index(symbol)
                if p_index and float(p_index.get('mark_price', 0.0)) > 0:
                    px = float(p_index['mark_price'])
                    ts = int(p_index.get('timestamp_ms') or now_ms)
                    age_sec = (now_ms - ts) / 1000.0 if ts else 0.0
                    status = 'FRESH' if age_sec <= 60.0 else 'STALE'
                    return px, ts, status
            except Exception as ex_err:
                logger.debug(f'ModelBRunner: aster get_premium_index failed for {symbol}: {ex_err}')

        # 2. Query direct exchange adapter if available
        if self.exchange and hasattr(self.exchange, 'get_premium_index'):
            try:
                p_index = self.exchange.get_premium_index(symbol)
                if p_index and float(p_index.get('mark_price', 0.0)) > 0:
                    px = float(p_index['mark_price'])
                    ts = int(p_index.get('timestamp_ms') or now_ms)
                    age_sec = (now_ms - ts) / 1000.0 if ts else 0.0
                    status = 'FRESH' if age_sec <= 60.0 else 'STALE'
                    return px, ts, status
            except Exception as ex_err:
                logger.debug(f'ModelBRunner: direct exchange get_premium_index failed for {symbol}: {ex_err}')

        # 3. Query BinancePerpAdapter via MultiExchangeService
        if hasattr(self, 'exchange_service') and self.exchange_service and self.exchange_service.binance:
            try:
                p_index = self.exchange_service.binance.get_premium_index(symbol)
                if p_index and float(p_index.get('mark_price', 0.0)) > 0:
                    px = float(p_index['mark_price'])
                    ts = int(p_index.get('timestamp_ms') or now_ms)
                    age_sec = (now_ms - ts) / 1000.0 if ts else 0.0
                    status = 'FRESH' if age_sec <= 60.0 else 'STALE'
                    return px, ts, status
            except Exception as ex_err:
                logger.debug(f'ModelBRunner: binance get_premium_index failed for {symbol}: {ex_err}')

        # 4. Query HyperliquidAdapter via MultiExchangeService
        if hasattr(self, 'exchange_service') and self.exchange_service and self.exchange_service.hyperliquid:
            try:
                p_index = self.exchange_service.hyperliquid.get_premium_index(symbol)
                if p_index and float(p_index.get('mark_price', 0.0)) > 0:
                    px = float(p_index['mark_price'])
                    ts = int(p_index.get('timestamp_ms') or now_ms)
                    age_sec = (now_ms - ts) / 1000.0 if ts else 0.0
                    status = 'FRESH' if age_sec <= 60.0 else 'STALE'
                    return px, ts, status
            except Exception as ex_err:
                logger.debug(f'ModelBRunner: hyperliquid get_premium_index failed for {symbol}: {ex_err}')

        # 5. Fallback to latest local DB candle
        if self.db:
            try:
                candles = self.db.get_candles(symbol, '1h', limit=1)
                if candles and candles[-1].close > 0:
                    px = float(candles[-1].close)
                    ts = int(candles[-1].timestamp_ms)
                    age_sec = (now_ms - ts) / 1000.0
                    status = 'FRESH' if age_sec <= 3600.0 else 'STALE'
                    return px, ts, status
            except Exception:
                pass

        return None, None, 'MISSING'

    def get_enriched_open_positions(self) -> List[Dict[str, Any]]:
        """Return open paper positions enriched with live mark prices, UPnL, ROE, and exchange-style fields."""
        enriched: List[Dict[str, Any]] = []
        now_ms = int(time.time() * 1000)

        for pos in self.paper_engine.open_positions.values():
            sym = pos.symbol
            mark_p, mark_ts, mark_status = self.get_mark_price(sym)

            # Contract side check
            is_long = (pos.direction.value if hasattr(pos.direction, 'value') else str(pos.direction)).upper() == 'LONG'

            # Authoritative Linear Perpetual PnL calculation
            # LONG:  quantity * (mark_price - entry_price)
            # SHORT: quantity * (entry_price - mark_price)
            # When mark price is missing, unrealized PnL is undefined (None)
            margin = pos.margin_usdt or (pos.notional_usdt / (pos.leverage or 10.0))
            if mark_p is not None:
                if is_long:
                    gross_upnl = (mark_p - pos.entry_price) * pos.quantity
                else:
                    gross_upnl = (pos.entry_price - mark_p) * pos.quantity

                est_exit_fee = (pos.quantity * mark_p) * 0.0005
                net_upnl = gross_upnl - pos.entry_fee - est_exit_fee - (pos.funding_paid or 0.0)
                roe_pct = (net_upnl / margin * 100.0) if margin > 0 else 0.0
                roi_pct = (net_upnl / pos.notional_usdt * 100.0) if pos.notional_usdt > 0 else 0.0
            else:
                gross_upnl = None
                est_exit_fee = None
                net_upnl = None
                roe_pct = None
                roi_pct = None

            entry_iso = (
                datetime.fromtimestamp(pos.entry_timestamp_ms / 1000.0, tz=timezone.utc).isoformat()
                if pos.entry_timestamp_ms
                else None
            )
            mark_iso = (
                datetime.fromtimestamp(mark_ts / 1000.0, tz=timezone.utc).isoformat()
                if mark_ts
                else None
            )

            tp_display = f"${pos.take_profit_1:.4f}" if pos.take_profit_1 else (f"${pos.take_profit:.4f}" if pos.take_profit else "None")
            sl_display = f"${pos.stop_loss:.4f}" if pos.stop_loss else "None"

            p_dict = pos.to_dict()
            p_dict.update({
                'side': 'LONG' if is_long else 'SHORT',
                'mode': 'SIMULATED (10x)',
                'margin_mode': 'SIMULATED_ISOLATED',
                'margin_mode_display': f'SIMULATED ({int(pos.leverage or 10)}x)',
                'position_amt': pos.quantity,
                'notional_usdt': round(pos.notional_usdt, 4),
                'entry_price': pos.entry_price,
                'mark_price': round(mark_p, 8) if mark_p is not None else None,
                'mark_price_timestamp_ms': mark_ts,
                'mark_price_time_utc': mark_iso,
                'mark_price_status': mark_status,
                'allocated_margin_usdt': round(margin, 4),
                'allocated_margin_label': f"${margin:.2f}",
                'take_profit_price': pos.take_profit_1 or pos.take_profit,
                'take_profit_label': tp_display,
                'stop_loss_price': pos.stop_loss,
                'stop_loss_label': sl_display,
                'protection_status': 'PROTECTED',
                'liquidation_price': None,
                'liquidation_label': 'NOT MODELED',
                'unrealized_pnl_gross': round(gross_upnl, 4) if gross_upnl is not None else None,
                'unrealized_pnl_usdt': round(net_upnl, 4) if net_upnl is not None else None,
                'unrealized_pnl_net_usdt': round(net_upnl, 4) if net_upnl is not None else None,
                'roe_pct': round(roe_pct, 2) if roe_pct is not None else None,
                'roi_pct': round(roi_pct, 2) if roi_pct is not None else None,
                'entry_time_utc': entry_iso,
                'entry_fee_paid': round(pos.entry_fee, 4),
                'estimated_exit_fee': round(est_exit_fee, 4) if est_exit_fee is not None else None,
                'is_simulated': True,
            })
            enriched.append(p_dict)
        return enriched

    def get_status(self) -> Dict[str, Any]:
        """Aggregate real-time status, risk metrics, and active positions for Model B."""
        risk_summary = self.risk_manager.get_risk_summary()

        open_positions = self.get_enriched_open_positions()
        pending_orders = [
            order.to_dict() for order in self.paper_engine.pending_orders.values()
        ]

        total_discovered = sum(len(v) for v in self.discovered_universe.values())

        # Compute authoritative execution metrics from SQLite journal
        all_orders = self.journal.get_paper_orders(model_id="MODEL_B") if self.journal else []
        all_trades = self.journal.get_trades(open_only=False, limit=5000, model_id="MODEL_B") if self.journal else []
        all_setups = self.journal.get_latest_setups(limit=1000, model_id="MODEL_B") if self.journal else []

        rejected_orders_count = sum(1 for o in all_orders if o.get("status") == "REJECTED")
        expired_orders_count = sum(1 for o in all_orders if o.get("status") == "EXPIRED")
        invalidated_orders_count = sum(1 for o in all_orders if o.get("status") == "INVALIDATED")
        closed_trades_count = sum(1 for t in all_trades if not t.get("is_open"))

        return {
            "model_id": "MODEL_B",
            "model_name": "Aggressive Multi-Exchange Liquidity Research",
            "is_running": self._is_running,
            "last_scan_utc": self.last_scan_utc,
            "last_successful_scan_utc": self.last_successful_scan_utc,
            "last_scan_status": self.last_scan_status,
            "scan_count": self.scan_count,
            "last_error": self.last_error,
            "discovered_markets_count": total_discovered,
            "focus_symbols_count": len(self.get_focus_symbols(max_symbols=25)),
            "engine_telemetry": self.engine_telemetry,
            "capital": {
                "initial_equity_usdt": risk_summary["initial_equity_usdt"],
                "current_equity_usdt": risk_summary["equity_usdt"],
                "margin_mode": "ISOLATED",
                "max_leverage": self.config.max_leverage,
            },
            "risk_limits": {
                "target_trade_risk_usdt": self.config.target_trade_risk_usdt,
                "daily_loss_limit_usdt": self.config.daily_loss_limit_usdt,
                "weekly_loss_limit_usdt": self.config.weekly_loss_limit_usdt,
                "daily_loss_realized_usdt": risk_summary["daily_loss_usdt"],
                "weekly_loss_realized_usdt": risk_summary["weekly_loss_usdt"],
                "max_open_positions": self.config.max_open_positions,
                "open_positions_count": risk_summary["open_positions_count"],
                "pending_orders_count": len(pending_orders),
                "daily_circuit_breaker_active": risk_summary["daily_circuit_breaker_active"],
                "weekly_circuit_breaker_active": risk_summary["weekly_circuit_breaker_active"],
                "daily_circuit_breaker_enforced": risk_summary["daily_circuit_breaker_enforced"],
                "weekly_circuit_breaker_enforced": risk_summary["weekly_circuit_breaker_enforced"],
                "diagnostic_override_active": risk_summary["diagnostic_override_active"],
                "entry_kill_switch": risk_summary["entry_kill_switch"],
            },
            "diagnostic": {
                "override_active": risk_summary["diagnostic_override_active"],
                "label": "DIAGNOSTIC_OVERRIDE_ACTIVE" if risk_summary["diagnostic_override_active"] else None,
                "entry_kill_switch": risk_summary["entry_kill_switch"],
                "started_at_utc": self.risk_manager.diagnostic_state.get("started_at_utc"),
                "paper_only": True,
            },
            "paper": {
                "enabled": True,
                "open_positions": open_positions,
                "pending_orders": pending_orders,
            },
            "execution_metrics": {
                "candidates_total": len(all_setups),
                "candidates_ready": sum(1 for s in all_setups if s.get("status") == "READY"),
                "orders_submitted": len(all_orders),
                "orders_pending": len(pending_orders),
                "positions_open": len(open_positions),
                "trades_closed": closed_trades_count,
                "orders_rejected": rejected_orders_count,
                "orders_expired": expired_orders_count,
                "orders_invalidated": invalidated_orders_count,
            },
            "scanner_performance": self.telemetry_tracker.get_latest(),
            "live_trading_locked": True,
        }
