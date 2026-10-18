"""Model B Scheduler and Execution Coordinator.

Orchestrates multi-exchange discovery, 1H candle evaluation, liquidity depth ingestion,
Model B setup generation, risk-gated paper execution, and lifecycle monitoring.
Completely isolated from Model A.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import threading
import time

from src.core.logger import logger
from src.core.types import Candle, CandidateStatus, Direction, SetupCandidate
from src.data.database import DatabaseManager
from src.exchange.base import BaseExchangeAdapter
from src.exchange.normalized import NormalizedInstrument
from src.exchange.service import MultiExchangeService
from src.execution.paper_engine import PaperExecutionEngine
from src.journal.repository import JournalRepository
from src.liquidity.collector import LiquidityCollector
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
    ) -> None:
        self.db = db
        self.journal = journal
        self.exchange = exchange
        self.config = config or ModelBConfig()
        self.paper_config = paper_config or PaperExecutionConfig()

        self.exchange_service = exchange_service or MultiExchangeService()
        self.liquidity_collector = LiquidityCollector(db=self.db, exchange_service=self.exchange_service)
        self.generator = ModelBStrategyGenerator(
            config=self.config,
            exchange_service=self.exchange_service,
            liquidity_collector=self.liquidity_collector,
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

        # State tracking
        self.last_scan_utc: Optional[str] = None
        self.last_successful_scan_utc: Optional[str] = None
        self.last_scan_status: str = "NEVER_RUN"
        self.scan_count: int = 0
        self.last_error: Optional[str] = None
        self.discovered_universe: Dict[str, List[NormalizedInstrument]] = {}
        self.latest_candidates: List[SetupCandidate] = []

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
            vol = inst.volume_24h_usdt or 0.0
            if vol >= self.config.min_24h_volume_usdt:
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
                        found_candidates.append(candidate)
                        self.journal.save_setup(candidate)

                        # Submit READY candidate to Model B Paper Engine
                        if candidate.status == CandidateStatus.READY:
                            self.paper_engine.submit_candidate(candidate, now_ms=current_ms)

            # 4. Evaluate pending orders and open positions against latest candles
            if candles_by_symbol:
                self.paper_engine.evaluate_pending_orders(candles_by_symbol, now_ms=current_ms)
                self.paper_engine.evaluate_open_positions(candles_by_symbol, now_ms=current_ms)

            self.latest_candidates = found_candidates
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

    def get_status(self) -> Dict[str, Any]:
        """Aggregate real-time status, risk metrics, and active positions for Model B."""
        risk_summary = self.risk_manager.get_risk_summary()

        open_positions = [
            pos.to_dict() for pos in self.paper_engine.open_positions.values()
        ]
        pending_orders = [
            order.to_dict() for order in self.paper_engine.pending_orders.values()
        ]

        total_discovered = sum(len(v) for v in self.discovered_universe.values())

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
                "daily_circuit_breaker_active": risk_summary["daily_circuit_breaker_active"],
                "weekly_circuit_breaker_active": risk_summary["weekly_circuit_breaker_active"],
            },
            "paper": {
                "enabled": True,
                "open_positions": open_positions,
                "pending_orders": pending_orders,
            },
            "live_trading_locked": True,
        }
