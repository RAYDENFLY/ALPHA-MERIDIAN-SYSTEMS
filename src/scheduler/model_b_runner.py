"""Model B Scheduler and Execution Coordinator.

Orchestrates multi-exchange discovery, 1H candle evaluation, liquidity depth ingestion,
Model B setup generation, risk-gated paper execution, and lifecycle monitoring.
Completely isolated from Model A.
"""

from typing import Any, Dict, List, Optional, Tuple
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
from src.strategy.model_b.generator import ModelBStrategyGenerator
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

    def discover_universe(self, force_refresh: bool = False) -> Dict[str, List[NormalizedInstrument]]:
        """Fetch instruments across all supported perpetual venues."""
        try:
            self.discovered_universe = self.exchange_service.discover_all_instruments(force_refresh=force_refresh)
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

        try:
            # 1. Sync risk manager with SQLite journal
            self.risk_manager.sync_state_from_journal(self.journal, now_ms=current_ms, model_id="MODEL_B")

            # 2. Get focus symbols for Tier 2 evaluation
            focus_instruments = self.get_focus_symbols(max_symbols=20)
            logger.info(f"ModelBRunner: Evaluating {len(focus_instruments)} focus perpetuals...")

            candles_by_symbol: Dict[str, List[Candle]] = {}

            # 3. Fetch recent 1H candles for each focus symbol
            for inst in focus_instruments:
                sym = inst.symbol
                candles: List[Candle] = []

                if self.exchange:
                    try:
                        candles = self.exchange.get_klines(sym, "1h", limit=50)
                    except Exception as k_err:
                        logger.debug(f"ModelBRunner: Failed to fetch candles for {sym}: {k_err}")

                if not candles:
                    # Fallback to local DB candles if exchange client unavailable
                    candles = self.db.get_candles(sym, "1h", limit=50)

                if len(candles) >= 20:
                    candles_by_symbol[sym] = candles
                    # Evaluate Model B setup
                    candidate = self.generator.evaluate_symbol(
                        symbol=sym,
                        candles=candles,
                        instrument=inst,
                        now_ms=current_ms,
                    )
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
                                can_open, r_reason = self.risk_manager.can_open_new_position(current_ms)
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


            # 4. Evaluate pending orders and open positions against latest candles
            if candles_by_symbol:
                terminal_orders = self.paper_engine.evaluate_pending_orders(candles_by_symbol, now_ms=current_ms)
                for term_order in terminal_orders:
                    self._sync_candidate_on_order_update(term_order)

                closed_trades = self.paper_engine.evaluate_open_positions(candles_by_symbol, now_ms=current_ms)
                for closed_trade in closed_trades:
                    self._sync_candidate_on_trade_close(closed_trade)

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

    def get_enriched_open_positions(self) -> List[Dict[str, Any]]:
        """Return open paper positions enriched with live mark prices, UPnL, ROE, and exchange-style fields."""
        enriched: List[Dict[str, Any]] = []
        for pos in self.paper_engine.open_positions.values():
            sym = pos.symbol
            mark_p = pos.entry_price  # fallback
            if self.exchange and hasattr(self.exchange, "get_premium_index"):
                try:
                    p_index = self.exchange.get_premium_index(sym)
                    if p_index and float(p_index.get("mark_price", 0.0)) > 0:
                        mark_p = float(p_index["mark_price"])
                except Exception as ex_err:
                    logger.debug(f"ModelBRunner: Failed to fetch live mark price for {sym}: {ex_err}")

            if mark_p == pos.entry_price and self.db:
                try:
                    candles = self.db.get_candles(sym, "1h", limit=1)
                    if candles:
                        mark_p = candles[-1].close
                except Exception:
                    pass

            is_long = (pos.direction.value if hasattr(pos.direction, "value") else str(pos.direction)).upper() == "LONG"
            if is_long:
                gross_upnl = (mark_p - pos.entry_price) * pos.quantity
            else:
                gross_upnl = (pos.entry_price - mark_p) * pos.quantity

            est_exit_fee = (pos.quantity * mark_p) * 0.0005
            net_upnl = gross_upnl - pos.entry_fee - est_exit_fee - (pos.funding_paid or 0.0)

            margin = pos.margin_usdt or (pos.notional_usdt / (pos.leverage or 10.0))
            roe_pct = (net_upnl / margin * 100.0) if margin > 0 else 0.0
            roi_pct = (net_upnl / pos.notional_usdt * 100.0) if pos.notional_usdt > 0 else 0.0

            entry_iso = (
                datetime.fromtimestamp(pos.entry_timestamp_ms / 1000.0, tz=timezone.utc).isoformat()
                if pos.entry_timestamp_ms
                else None
            )

            tp_display = f"${pos.take_profit_1:.4f}" if pos.take_profit_1 else (f"${pos.take_profit:.4f}" if pos.take_profit else "None")
            sl_display = f"${pos.stop_loss:.4f}" if pos.stop_loss else "None"

            p_dict = pos.to_dict()
            p_dict.update({
                "side": "LONG" if is_long else "SHORT",
                "mode": "SIMULATED (10x)",
                "margin_mode": "SIMULATED_ISOLATED",
                "margin_mode_display": f"SIMULATED ({int(pos.leverage or 10)}x)",
                "position_amt": pos.quantity,
                "notional_usdt": round(pos.notional_usdt, 4),
                "entry_price": pos.entry_price,
                "mark_price": mark_p,
                "allocated_margin_usdt": round(margin, 4),
                "allocated_margin_label": f"${margin:.2f}",
                "take_profit_price": pos.take_profit_1 or pos.take_profit,
                "take_profit_label": tp_display,
                "stop_loss_price": pos.stop_loss,
                "stop_loss_label": sl_display,
                "protection_status": "PROTECTED",
                "liquidation_price": None,
                "liquidation_label": "NOT MODELED",
                "unrealized_pnl_gross": round(gross_upnl, 4),
                "unrealized_pnl_usdt": round(net_upnl, 4),
                "unrealized_pnl_net_usdt": round(net_upnl, 4),
                "roe_pct": round(roe_pct, 2),
                "roi_pct": round(roi_pct, 2),
                "entry_time_utc": entry_iso,
                "entry_fee_paid": round(pos.entry_fee, 4),
                "estimated_exit_fee": round(est_exit_fee, 4),
                "is_simulated": True,
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
            "live_trading_locked": True,
        }
