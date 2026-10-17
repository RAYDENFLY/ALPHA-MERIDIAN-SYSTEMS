"""Scanner Scheduler and Execution Coordinator.

Periodically syncs latest market data, scans for SMC/SnR setups,
evaluates dynamic structural invalidations and staleness,
applies local ML probability scoring and risk gating, and journals results.
"""

from datetime import datetime, timezone
import time
from typing import Dict, List, Optional
from src.backtest.engine import BacktestEngine
from src.core.logger import logger
from src.core.types import CandidateStatus, Direction, ExecutionStatus, MarketBias, SetupCandidate, StructureState
from src.data.database import DatabaseManager
from src.data.sync import CandleSynchronizer
from src.exchange.base import BaseExchangeAdapter
from src.exchange.metadata import SymbolSpecs
from src.journal.repository import JournalRepository
from src.ml.evaluator import SetupEvaluator
from src.ml.models import ModelRegistry
from src.risk.manager import RiskManager
from src.risk.sizing import PositionSizer
from config.settings import AIConfig, AIMode, ExecutionMode, PaperExecutionConfig
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.events import PaperEvent, PaperEventDispatcher, PaperEventType

from src.integrations.qwen_client import QwenClient
from src.strategy.generator import SetupGenerator



def rank_candidates(
    candidates: List[SetupCandidate],
    sort_by: str = "confidence_desc",
) -> List[SetupCandidate]:
    """
    Ranks candidates deterministically.

    sort_by options:
    - 'confidence_desc':
        1. Candidates with valid ml_probability first (highest to lowest).
        2. Missing ml_probability (None) explicitly placed at the bottom.
        3. Deterministic tie-breakers: net_reward_to_risk DESC, timestamp_ms DESC, symbol ASC.
    - 'confidence_asc':
        1. Candidates with valid ml_probability first (lowest to highest).
        2. Missing ml_probability (None) explicitly placed at the bottom (not treated as 0).
        3. Deterministic tie-breakers: net_reward_to_risk DESC, timestamp_ms DESC, symbol ASC.
    - 'latest':
        1. timestamp_ms DESC (most recent first).
        2. Deterministic tie-breaker: symbol ASC.
    """
    if sort_by == "confidence_desc":
        return sorted(
            candidates,
            key=lambda c: (
                0 if c.ml_probability is not None else 1,
                -(c.ml_probability or 0.0),
                -c.net_reward_to_risk,
                -c.timestamp_ms,
                c.symbol,
            ),
        )
    elif sort_by == "confidence_asc":
        return sorted(
            candidates,
            key=lambda c: (
                0 if c.ml_probability is not None else 1,
                (c.ml_probability or 0.0),
                -c.net_reward_to_risk,
                -c.timestamp_ms,
                c.symbol,
            ),
        )
    elif sort_by == "latest":
        return sorted(
            candidates,
            key=lambda c: (
                -c.timestamp_ms,
                c.symbol,
            ),
        )
    else:
        # Default fallback to confidence_desc
        return rank_candidates(candidates, sort_by="confidence_desc")


class ScannerRunner:
    """Coordinates scanner cycles across active watchlist symbols."""

    def __init__(
        self,
        exchange: BaseExchangeAdapter,
        db: DatabaseManager,
        journal: JournalRepository,
        risk_manager: RiskManager,
        specs_map: Dict[str, SymbolSpecs],
        models_dir=None,
        cost_buffer: float = 0.02,
        min_ml_confidence: Optional[float] = None,
        stale_threshold_hours: float = 3.0,
        target_isolated_margin: float = 6.0,
        target_isolated_margin_max: float = 6.5,
        hard_risk_limit_usdt: float = 1.50,
        max_leverage: int = 10,
        ai_config: Optional[AIConfig] = None,
        qwen_client: Optional[QwenClient] = None,
        mode: ExecutionMode = ExecutionMode.RESEARCH,

        paper_config: Optional[PaperExecutionConfig] = None,
        paper_engine: Optional[PaperExecutionEngine] = None,
        event_dispatcher: Optional[PaperEventDispatcher] = None,
    ) -> None:
        self.exchange = exchange
        self.db = db
        self.journal = journal
        self.risk_manager = risk_manager
        self.specs_map = specs_map
        self.cost_buffer = cost_buffer
        self.min_ml_confidence = min_ml_confidence
        self.stale_threshold_hours = stale_threshold_hours
        self.target_isolated_margin = target_isolated_margin
        self.target_isolated_margin_max = target_isolated_margin_max
        self.hard_risk_limit_usdt = hard_risk_limit_usdt
        self.max_leverage = max_leverage
        self.ai_config = ai_config or AIConfig()
        self.qwen_client = qwen_client or (QwenClient(self.ai_config) if self.ai_config.enabled else None)
        self.shadow_records: List[Dict[str, Any]] = []

        self.mode = mode
        self.paper_config = paper_config or PaperExecutionConfig()
        self.event_dispatcher = event_dispatcher or PaperEventDispatcher(self.paper_config.discord_webhook_url)
        if self.mode == ExecutionMode.PAPER:
            self.paper_engine = paper_engine or PaperExecutionEngine(
                db=self.db,
                journal=self.journal,
                risk_manager=self.risk_manager,
                config=self.paper_config,
                event_dispatcher=self.event_dispatcher,
            )
        else:
            self.paper_engine = paper_engine
            if self.paper_engine is not None and getattr(self.paper_engine, "event_dispatcher", None) is None:
                self.paper_engine.event_dispatcher = self.event_dispatcher


        self.synchronizer = CandleSynchronizer(exchange, db)
        self.generator = SetupGenerator()
        self.model_registry = ModelRegistry(models_dir) if models_dir else ModelRegistry()
        self.evaluator = SetupEvaluator(self.model_registry)

        self.scanner_state: str = "IDLE"  # "IDLE" | "RUNNING" | "STALE" | "ERROR"
        self.last_scan_utc: Optional[str] = None
        self.last_successful_scan_utc: Optional[str] = None
        self.latest_candle_time_utc: Optional[str] = None
        self.candle_age_seconds: Optional[int] = None
        self.last_error: Optional[str] = None
        self.has_completed_scan: bool = False
        self.scan_count: int = 0
        self.last_scan_status: str = "NEVER_RUN"  # "NEVER_RUN" | "RUNNING" | "SUCCESS" | "FAILED"
        self.last_symbols_attempted: List[str] = []

        self.last_results: List[SetupCandidate] = []
        self.active_setups_by_symbol: Dict[str, SetupCandidate] = {}
        self.active_setups_by_id: Dict[str, SetupCandidate] = {}

    def _get_required_ml_probability(self, candidate: SetupCandidate) -> float:
        """Compute setup-specific break-even probability threshold (or explicit override)."""
        if self.min_ml_confidence is not None:
            return self.min_ml_confidence
        return BacktestEngine.calculate_break_even_probability(
            candidate, cost_buffer=self.cost_buffer
        )

    def recheck_active_setups(self, now_ms: int) -> None:
        """Re-evaluates existing candidate setups for structural invalidation and expiration."""
        # 0. Reconcile stale or expired setups in database journal
        if hasattr(self.journal, "reconcile_stale_setups"):
            try:
                self.journal.reconcile_stale_setups(now_ms=now_ms, stale_threshold_hours=self.stale_threshold_hours)
            except Exception as e:
                logger.warning(f"Error during journal stale setups reconciliation: {e}")

        # Synchronize from active_setups_by_id if callers populated active_setups_by_id directly
        for cid, cand in list(self.active_setups_by_id.items()):
            if cand.symbol:
                existing = self.active_setups_by_symbol.get(cand.symbol)
                if existing is None or cand.timestamp_ms >= existing.timestamp_ms:
                    self.active_setups_by_symbol[cand.symbol] = cand

        for sym, cand in list(self.active_setups_by_symbol.items()):
            # Only recheck active/pending states (READY or WATCHLIST)
            if cand.status not in (CandidateStatus.READY, CandidateStatus.VALID, CandidateStatus.WATCHLIST):
                continue

            # 1. Expiration check
            if cand.expires_at_ms and now_ms > cand.expires_at_ms:
                cand.status = CandidateStatus.STALE
                exp_utc = (
                    datetime.fromtimestamp(cand.expires_at_ms / 1000.0, tz=timezone.utc).isoformat()
                    if cand.expires_at_ms
                    else "N/A"
                )
                cand.rejection_reasons.append(
                    f"Setup expired at {exp_utc} (12h TTL exceeded)."
                )
                self.journal.save_setup(cand)
                continue


            # 2. Structural invalidation check against subsequent 1H candles
            candles = self.db.get_candles(cand.symbol, "1h")
            if not candles:
                continue

            subsequent_candles = [c for c in candles if c.timestamp_ms > cand.timestamp_ms]
            is_invalidated = False
            for bar in subsequent_candles:
                if cand.direction == Direction.LONG:
                    if bar.low <= cand.invalidation_price:
                        cand.status = CandidateStatus.INVALIDATED
                        cand.rejection_reasons.append(
                            f"Structural invalidation breached: 1H bar at {bar.datetime_utc.isoformat()} "
                            f"low ({bar.low}) <= invalidation price ({cand.invalidation_price})."
                        )
                        is_invalidated = True
                        break
                elif cand.direction == Direction.SHORT:
                    if bar.high >= cand.invalidation_price:
                        cand.status = CandidateStatus.INVALIDATED
                        cand.rejection_reasons.append(
                            f"Structural invalidation breached: 1H bar at {bar.datetime_utc.isoformat()} "
                            f"high ({bar.high}) >= invalidation price ({cand.invalidation_price})."
                        )
                        is_invalidated = True
                        break

            if is_invalidated:
                self.journal.save_setup(cand)

        # Keep active_setups_by_id in sync with active_setups_by_symbol
        self.active_setups_by_id = {c.candidate_id: c for c in self.active_setups_by_symbol.values()}

    def sync_candidate_execution_status(self, cand: SetupCandidate) -> SetupCandidate:
        """Synchronize candidate's execution_status with active paper or exchange orders."""
        if self.paper_engine:
            sym = cand.symbol.upper()
            # 1. Check open positions
            if sym in self.paper_engine.open_positions:
                pos = self.paper_engine.open_positions[sym]
                if pos.setup_id == cand.candidate_id or pos.entry_timestamp_ms == cand.timestamp_ms:
                    cand.execution_status = (
                        ExecutionStatus.PROTECTED
                        if (pos.stop_loss and pos.take_profit)
                        else ExecutionStatus.FILLED
                    )
                    cand.order_id = pos.trade_id
                    cand.filled_quantity = pos.quantity
                    cand.filled_at_ms = pos.entry_timestamp_ms
                    return cand

            # 2. Check pending limit orders
            for o in self.paper_engine.pending_orders.values():
                if o.symbol == sym and (o.candidate_id == cand.candidate_id or o.created_bar_time_ms == cand.timestamp_ms):
                    cand.execution_status = ExecutionStatus.SUBMITTED
                    cand.order_id = o.order_id
                    return cand

        # Default fallback: execution not submitted
        cand.execution_status = ExecutionStatus.NOT_SUBMITTED
        return cand

    def run_scan(
        self,
        symbols: List[str],
        sync_live: bool = True,
    ) -> List[SetupCandidate]:
        """Execute a full scan cycle across symbols."""
        now_ms = int(time.time() * 1000)
        self.last_scan_utc = datetime.now(timezone.utc).isoformat()
        self.scanner_state = "RUNNING"
        self.last_scan_status = "RUNNING"
        self.last_symbols_attempted = list(symbols)
        self.last_error = None
        logger.info(f"Running scanner cycle for {len(symbols)} symbols...")

        try:
            # 1. Optionally synchronize latest closed candles from exchange
            sync_errors: Dict[str, str] = {}
            if sync_live:
                for sym in symbols:
                    try:
                        self.synchronizer.sync_symbol(sym, "1h")
                        self.synchronizer.sync_symbol(sym, "4h")
                    except Exception as e:
                        logger.warning(f"Live candle sync failed for {sym}: {e}")
                        sync_errors[sym] = str(e)

            # 2. Recheck previously active candidates for structural invalidation or expiration
            try:
                self.recheck_active_setups(now_ms)
            except Exception as e:
                logger.warning(f"Error rechecking active setups: {e}")

            # 2b. In PAPER mode, evaluate active pending orders and open positions against latest candles
            if self.mode == ExecutionMode.PAPER and self.paper_engine is not None:
                try:
                    candles_by_sym = {s: self.db.get_candles(s, "1h") for s in symbols}
                    self.paper_engine.evaluate_pending_orders(candles_by_sym, now_ms)
                    self.paper_engine.evaluate_open_positions(candles_by_sym, now_ms)
                except Exception as pe_err:
                    logger.error(f"Error evaluating paper positions/orders: {pe_err}")


            current_cycle_candidates: List[SetupCandidate] = []
            max_candle_time_ms = 0

            for sym in symbols:
                try:
                    c1_list = self.db.get_candles(sym, "1h")
                    c4_list = self.db.get_candles(sym, "4h")

                    if len(c1_list) < 40:
                        continue

                    latest_c1 = c1_list[-1]
                    if latest_c1.timestamp_ms > max_candle_time_ms:
                        max_candle_time_ms = latest_c1.timestamp_ms

                    # Check candle freshness
                    candle_age_s = int((now_ms - latest_c1.close_time_ms) / 1000)
                    is_stale_data = candle_age_s > (self.stale_threshold_hours * 3600)

                    # Generate setup candidate as of latest closed 1H bar
                    candidate = self.generator.generate_candidate(
                        symbol=sym,
                        candles_1h=c1_list,
                        candles_4h=c4_list,
                    )

                    if candidate is None:
                        continue

                    # Fetch recent funding rate
                    rates = self.db.get_funding_rates(sym)
                    recent_funding = float(rates[-1]["funding_rate"]) if rates else 0.0

                    # 3. Local ML Evaluation and Probability Calibration
                    candidate = self.evaluator.evaluate_setup(
                        candidate, c1_list, funding_rate=recent_funding
                    )
                    required_prob = self._get_required_ml_probability(candidate)
                    ml_prob = candidate.ml_probability
                    ml_passes = (ml_prob is not None) and (ml_prob >= required_prob)

                    # 4. Position Sizing and Capital Feasibility ($16 USDT)
                    specs = self.specs_map.get(sym)
                    if specs is None:
                        try:
                            specs = self.exchange.get_symbol_specs(sym)
                            self.specs_map[sym] = specs
                        except Exception:
                            pass

                    if specs is not None:
                        raw_eff_risk = getattr(self.risk_manager, "get_effective_trade_risk", lambda _ts: 0.09)(now_ms)
                        eff_risk_val = float(raw_eff_risk) if isinstance(raw_eff_risk, (int, float)) else 0.09
                        assessment = PositionSizer.calculate_sizing(
                            symbol_specs=specs,
                            entry_price=candidate.entry_price,
                            stop_loss=candidate.stop_loss,
                            target_isolated_margin=self.target_isolated_margin,
                            target_isolated_margin_max=self.target_isolated_margin_max,
                            available_equity_usdt=self.risk_manager.equity_usdt,
                            max_leverage=self.max_leverage,
                            hard_risk_limit_usdt=self.hard_risk_limit_usdt,
                            risk_budget_usdt=eff_risk_val,
                            direction=candidate.direction,
                        )
                        candidate.risk_assessment = assessment

                        if not assessment.is_feasible:
                            candidate.status = CandidateStatus.INFEASIBLE
                            candidate.rejection_reasons.append(
                                assessment.infeasible_reason or "Order sizing infeasible."
                            )
                        else:
                            # Non-ML Risk Governance Gate: Check account-level limits (open positions, loss limits, entries)
                            can_open, reason = self.risk_manager.can_open_new_position(now_ms)
                            if not can_open:
                                candidate.status = CandidateStatus.WATCHLIST
                                candidate.rejection_reasons.append(
                                    reason or "Account limits reached (awaiting position closure or quota reset)."
                                )
                            else:
                                # Mandatory gates passed: strategy valid, sizing feasible, account limits permitted
                                candidate.status = CandidateStatus.READY

                            # Informative ML Ranking / Confidence Observation (NON-BLOCKING)
                            prob_str = f"{ml_prob:.1%}" if ml_prob is not None else "N/A"
                            if ml_prob is not None and ml_prob < required_prob:
                                candidate.opposing_reasons.append(
                                    f"Informative: ML score ({prob_str}) below theoretical break-even ({required_prob:.1%}). "
                                    "Unproven positive edge; score is informative ranking only."
                                )
                            elif ml_prob is not None:
                                candidate.supporting_reasons.append(
                                    f"Informative: ML score ({prob_str}) meets theoretical break-even ({required_prob:.1%})."
                                )
                    else:
                        # Missing symbol specifications: sizing feasibility cannot be verified
                        candidate.status = CandidateStatus.INFEASIBLE
                        candidate.rejection_reasons.append(
                            f"Exchange symbol specifications unavailable for {sym} to validate position sizing."
                        )

                    # 5. Immediate structural invalidation, staleness override, or sync failure fail-safe
                    if is_stale_data:
                        candidate.status = CandidateStatus.STALE
                        age_min = candle_age_s // 60
                        candidate.rejection_reasons.insert(
                            0,
                            f"Market candle data is stale: Last candle closed at {latest_c1.datetime_utc.strftime('%Y-%m-%d %H:%M UTC')} "
                            f"({age_min}m ago, exceeding {self.stale_threshold_hours}h limit). Live sync required."
                        )
                    elif sym in sync_errors:
                        # Fail-safe: if live sync was requested but failed, do not promote to READY
                        if candidate.status in (CandidateStatus.READY, CandidateStatus.VALID):
                            candidate.status = CandidateStatus.WATCHLIST
                            candidate.rejection_reasons.insert(
                                0,
                                f"Live sync failed for {sym} ({sync_errors[sym]}). Candidate held on WATCHLIST pending verified data."
                            )
                    else:
                        if candidate.direction == Direction.LONG and latest_c1.low <= candidate.invalidation_price:
                            candidate.status = CandidateStatus.INVALIDATED
                            candidate.rejection_reasons.append(
                                f"Immediate candle low ({latest_c1.low}) breached invalidation level ({candidate.invalidation_price})."
                            )
                        elif candidate.direction == Direction.SHORT and latest_c1.high >= candidate.invalidation_price:
                            candidate.status = CandidateStatus.INVALIDATED
                            candidate.rejection_reasons.append(
                                f"Immediate candle high ({latest_c1.high}) breached invalidation level ({candidate.invalidation_price})."
                            )

                    # Save to Journal (preserves full historical record)
                    self.journal.save_setup(candidate)

                    # Emit Discord notification if candidate qualifies as READY/VALID
                    if candidate.status in (CandidateStatus.READY, CandidateStatus.VALID):
                        try:
                            self.event_dispatcher.emit_setup_ready(candidate)
                        except Exception as ev_err:
                            logger.warning(f"Failed to emit SETUP_READY event for {sym}: {ev_err}")
                    elif candidate.status == CandidateStatus.INVALIDATED:
                        try:
                            self.event_dispatcher.emit_setup_rejected(
                                symbol=sym,
                                reason=candidate.invalidation_reason or "Candidate invalidated by price action.",
                                candidate_id=candidate.candidate_id,
                                direction=candidate.direction.value,
                                status="INVALIDATED",
                                ref_price=candidate.reference_price,
                            )
                        except Exception as ev_err:
                            logger.warning(f"Failed to emit SETUP_REJECTED event for {sym}: {ev_err}")

                    # Optional Read-Only AI Qualitative Enrichment (Non-blocking, non-executing)
                    if self.ai_config.enabled and self.qwen_client is not None:
                        try:
                            if self.ai_config.mode == AIMode.AI_ANALYSIS:
                                ai_res = self.qwen_client.analyze_candidate(candidate)
                                candidate.ai_analysis = ai_res.to_dict()
                            elif self.ai_config.mode == AIMode.SHADOW:
                                ai_res = self.qwen_client.analyze_candidate(candidate)
                                self.shadow_records.append(ai_res.to_dict())
                                candidate.ai_analysis = None
                        except Exception as ai_err:
                            logger.warning(f"AI enrichment error safely bypassed for {sym}: {ai_err}")

                    # Active setup: single latest candidate per symbol based on candle timestamp_ms
                    existing = self.active_setups_by_symbol.get(sym)
                    if existing is None or candidate.timestamp_ms >= existing.timestamp_ms:
                        if existing and existing.candidate_id in self.active_setups_by_id:
                            self.active_setups_by_id.pop(existing.candidate_id, None)
                        self.active_setups_by_symbol[sym] = candidate
                        self.active_setups_by_id[candidate.candidate_id] = candidate
                    current_cycle_candidates.append(candidate)

                except Exception as e:
                    logger.error(f"Error evaluating candidate for {sym}: {e}")
                    self.last_error = f"Evaluation failed for {sym}: {str(e)}"
                    try:
                        self.event_dispatcher.emit_system_error(
                            component="ScannerRunner",
                            error_message=f"Evaluation failed for {sym}: {str(e)}",
                            symbol=sym,
                        )
                    except Exception:
                        pass
                    err_cand = SetupCandidate(
                        candidate_id=f"SETUP_{sym}_{now_ms}_ERROR",
                        symbol=sym,
                        timestamp_ms=now_ms,
                        direction=Direction.NEUTRAL,
                        bias_4h=MarketBias.NEUTRAL,
                        structure_1h=StructureState.SIDEWAYS,
                        entry_price=0.0,
                        stop_loss=0.0,
                        take_profit_1=0.0,
                        status=CandidateStatus.ERROR,
                        rejection_reasons=[f"Evaluation error for {sym}: {str(e)}"],
                        invalidation_reason="Error during candidate evaluation",
                    )
                    existing = self.active_setups_by_symbol.get(sym)
                    if existing is None or err_cand.timestamp_ms >= existing.timestamp_ms:
                        if existing and existing.candidate_id in self.active_setups_by_id:
                            self.active_setups_by_id.pop(existing.candidate_id, None)
                        self.active_setups_by_symbol[sym] = err_cand
                        self.active_setups_by_id[err_cand.candidate_id] = err_cand
                    current_cycle_candidates.append(err_cand)

            # Update candle freshness tracking
            if max_candle_time_ms > 0:
                self.latest_candle_time_utc = datetime.fromtimestamp(
                    max_candle_time_ms / 1000.0, tz=timezone.utc
                ).isoformat()
                self.candle_age_seconds = max(0, int((now_ms - max_candle_time_ms) / 1000))
            else:
                self.candle_age_seconds = None

            # Determine overall scanner state
            if self.candle_age_seconds is not None and self.candle_age_seconds > (self.stale_threshold_hours * 3600):
                self.scanner_state = "STALE"
            else:
                self.scanner_state = "IDLE"

            # Deterministic Ranking across active setups (one per symbol)
            all_candidates = list(self.active_setups_by_symbol.values())
            ranked = rank_candidates(all_candidates, sort_by="confidence_desc")
            self.last_results = ranked
            self.has_completed_scan = True
            self.scan_count += 1
            self.last_scan_status = "SUCCESS"
            self.last_successful_scan_utc = datetime.now(timezone.utc).isoformat()

            logger.info(
                f"Scan cycle complete. Discovered {len(current_cycle_candidates)} new candidates, "
                f"{len(ranked)} total tracked candidates."
            )

            # 6. In PAPER mode, submit top READY candidates to simulated paper engine
            if self.mode == ExecutionMode.PAPER and self.paper_engine is not None:
                for cand in ranked:
                    if cand.status == CandidateStatus.READY:
                        try:
                            p_order = self.paper_engine.submit_candidate(cand, now_ms)
                            if p_order:
                                cand.execution_status = ExecutionStatus.SUBMITTED
                                cand.order_id = p_order.order_id
                        except Exception as pe_sub_err:
                            logger.error(f"Error submitting paper candidate {cand.candidate_id}: {pe_sub_err}")

            # 7. Synchronize execution status for all candidates
            for cand in ranked:
                self.sync_candidate_execution_status(cand)

            return ranked

        except Exception as top_err:
            logger.error(f"Critical scan cycle failure: {top_err}")
            self.scanner_state = "ERROR"
            self.last_scan_status = "FAILED"
            self.last_error = str(top_err)
            raise