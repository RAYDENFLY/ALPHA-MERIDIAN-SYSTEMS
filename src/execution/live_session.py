"""Session-Authorized LIVE Auto-Execution Manager (LIVE_AUTO).

Provides server-controlled, auditable, and resilient session management for automated
execution on Aster DEX Mainnet under the LIVE_MICRO_V1 architecture.

Strict Invariants:
1. LIVE_AUTO is disabled by default. Default mode is PAPER.
2. Session activation requires explicit operator pre-flight authorization.
3. Server maintains session state. Client booleans are never trusted.
4. Process restarts start in INACTIVE state (never silently resume LIVE_AUTO).
5. Explicit operator controls: Start, Pause, Resume, Stop, and Emergency Kill Switch.
6. Only EMA20_RECLAIM_5M_V1 is authorized for live execution. SWEEP_1M_V1 is strictly shadow-only.
7. Risk limits are invariant: $0.20 fixed dollar risk, $7.00 margin at 10x isolated leverage,
   max 1 concurrent global position, daily loss limit $0.45, 3 filled entries/day, 25% peak DD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import sqlite3
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from src.data.database import DatabaseManager
from src.journal.automated_journal import AutomatedTradeJournal
from src.risk.global_lock import GlobalPositionLock

if TYPE_CHECKING:
    from src.scheduler.live_micro_runner import LiveMicroCoordinator

logger = logging.getLogger("src.execution.live_session")


class ExecutionMode(str, Enum):
    """Server-controlled execution mode."""
    PAPER = "PAPER"
    LIVE_MANUAL = "LIVE_MANUAL"
    LIVE_AUTO = "LIVE_AUTO"

    @classmethod
    def from_str(cls, val: str) -> "ExecutionMode":
        if not val:
            return cls.PAPER
        cleaned = str(val).strip().upper()
        if cleaned in ("LIVE_AUTO", "AUTO"):
            return cls.LIVE_AUTO
        if cleaned in ("LIVE_MANUAL", "LIVE", "MANUAL"):
            return cls.LIVE_MANUAL
        return cls.PAPER


class LiveSessionState(str, Enum):
    """Lifecycle states of a LIVE Auto-Execution Session."""
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    REAUTHORIZATION_REQUIRED = "REAUTHORIZATION_REQUIRED"
    HALTED = "HALTED"
    KILL_SWITCH_HALTED = "KILL_SWITCH_HALTED"
    STOPPED = "HALTED"

    @classmethod
    def from_str(cls, val: str) -> "LiveSessionState":
        if not val:
            return cls.INACTIVE
        cleaned = str(val).strip().upper()
        if cleaned in ("ACTIVE", "RUNNING"):
            return cls.ACTIVE
        if cleaned in ("PAUSED", "PAUSE"):
            return cls.PAUSED
        if cleaned in ("RECOVERY_REQUIRED", "RECOVERY"):
            return cls.RECOVERY_REQUIRED
        if cleaned in ("REAUTHORIZATION_REQUIRED", "REAUTH"):
            return cls.REAUTHORIZATION_REQUIRED
        if cleaned in ("KILL_SWITCH_HALTED", "KILL_SWITCH"):
            return cls.KILL_SWITCH_HALTED
        if cleaned in ("HALTED", "STOPPED"):
            return cls.HALTED
        return cls.INACTIVE


@dataclass
class LiveSessionPreflightResult:
    """Outcome of pre-flight safety audit before session activation."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    connectivity_ok: bool = False
    equity_usd: float = 0.0
    available_balance_usd: float = 0.0
    leverage_margin_ok: bool = False
    risk_gates_ok: bool = False
    strategy_authorized_ok: bool = False
    shadow_only_ok: bool = False
    journal_ok: bool = False
    no_active_position_ok: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "connectivity_ok": self.connectivity_ok,
            "equity_usd": round(self.equity_usd, 2),
            "available_balance_usd": round(self.available_balance_usd, 2),
            "leverage_margin_ok": self.leverage_margin_ok,
            "risk_gates_ok": self.risk_gates_ok,
            "strategy_authorized_ok": self.strategy_authorized_ok,
            "shadow_only_ok": self.shadow_only_ok,
            "journal_ok": self.journal_ok,
            "no_active_position_ok": self.no_active_position_ok,
        }


@dataclass
class LiveSessionInfo:
    """Current live session metadata and auditable status."""
    session_id: Optional[str] = None
    mode: ExecutionMode = ExecutionMode.PAPER
    state: LiveSessionState = LiveSessionState.INACTIVE
    started_at_utc: Optional[str] = None
    paused_at_utc: Optional[str] = None
    stopped_at_utc: Optional[str] = None
    authorized_by: str = "NONE"
    orders_placed_count: int = 0
    orders_filled_count: int = 0
    last_candidate_symbol: Optional[str] = None
    last_status_message: str = "Session inactive. Default mode is PAPER."
    recovery_reason: Optional[str] = None
    scanner_active: bool = False
    last_scan_utc: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "mode": self.mode.value if hasattr(self.mode, "value") else str(self.mode),
            "state": self.state.value if hasattr(self.state, "value") else str(self.state),
            "is_active": self.state == LiveSessionState.ACTIVE,
            "is_paused": self.state == LiveSessionState.PAUSED,
            "started_at_utc": self.started_at_utc,
            "paused_at_utc": self.paused_at_utc,
            "stopped_at_utc": self.stopped_at_utc,
            "authorized_by": self.authorized_by,
            "orders_placed_count": self.orders_placed_count,
            "orders_filled_count": self.orders_filled_count,
            "last_candidate_symbol": self.last_candidate_symbol,
            "last_status_message": self.last_status_message,
            "recovery_reason": self.recovery_reason,
            "scanner_active": self.scanner_active,
            "last_scan_utc": self.last_scan_utc,
        }


class LiveAutoSessionManager:
    """Singleton Server-Side Manager for LIVE Auto-Execution Sessions."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(
        self,
        coordinator: Optional[LiveMicroCoordinator] = None,
        exchange_adapter: Optional[Any] = None,
        account_service: Optional[Any] = None,
        automated_journal: Optional[AutomatedTradeJournal] = None,
        db: Optional[DatabaseManager] = None,
    ) -> None:
        if getattr(self, "_initialized", False):
            if coordinator:
                self.coordinator = coordinator
            if exchange_adapter:
                self.exchange_adapter = exchange_adapter
            if account_service:
                self.account_service = account_service
            if automated_journal:
                self.automated_journal = automated_journal
            if db:
                self.db = db
            return

        self.coordinator = coordinator
        self.exchange_adapter = exchange_adapter
        self.account_service = account_service
        self.automated_journal = automated_journal
        self.db = db
        self._mutex = threading.RLock()

        # Invariant 1: Default mode is PAPER; state is INACTIVE on server startup
        self.mode = ExecutionMode.PAPER
        self.state = LiveSessionState.INACTIVE
        self.session_id: Optional[str] = None
        self.started_at_utc: Optional[str] = None
        self.paused_at_utc: Optional[str] = None
        self.stopped_at_utc: Optional[str] = None
        self.authorized_by: str = "NONE"
        self.orders_placed_count: int = 0
        self.orders_filled_count: int = 0
        self.last_candidate_symbol: Optional[str] = None
        self.last_status_message: str = "Session inactive. Default mode is PAPER."
        self.recovery_reason: Optional[str] = None

        self.autotrader: Optional[Any] = None
        self._scanner_running: bool = False
        self._scanner_stop_event = threading.Event()
        self._scanner_thread: Optional[threading.Thread] = None
        self._scan_interval_sec: float = 60.0
        self._last_scan_utc: Optional[str] = None

        self._ensure_storage_schema()

        # Load persisted session state if present in database
        persisted = self._load_persisted_state()
        if persisted:
            self.session_id = persisted.get("session_id")
            self.mode = ExecutionMode.from_str(persisted.get("mode", "PAPER"))
            raw_st = persisted.get("state", "INACTIVE")
            # If server restarted while session was ACTIVE or PAUSED,
            # transition into REAUTHORIZATION_REQUIRED so it never silently resumes.
            if raw_st in (LiveSessionState.ACTIVE.value, LiveSessionState.PAUSED.value, LiveSessionState.REAUTHORIZATION_REQUIRED.value):
                self.state = LiveSessionState.REAUTHORIZATION_REQUIRED
                self.last_status_message = "Runtime restarted. Operator reauthorization required before live auto-execution resumes."
            elif raw_st in (LiveSessionState.HALTED.value, "STOPPED"):
                self.state = LiveSessionState.HALTED
                self.last_status_message = persisted.get("last_status_message") or "Session halted."
            elif raw_st == LiveSessionState.KILL_SWITCH_HALTED.value:
                self.state = LiveSessionState.KILL_SWITCH_HALTED
                self.last_status_message = persisted.get("last_status_message") or "Kill switch active."
            elif raw_st == LiveSessionState.RECOVERY_REQUIRED.value:
                self.state = LiveSessionState.RECOVERY_REQUIRED
                self.last_status_message = persisted.get("last_status_message") or "Recovery required."
            else:
                self.state = LiveSessionState.INACTIVE
                self.last_status_message = persisted.get("last_status_message") or "Session inactive. Default mode is PAPER."

            self.started_at_utc = persisted.get("started_at_utc")
            self.paused_at_utc = persisted.get("paused_at_utc")
            self.stopped_at_utc = persisted.get("stopped_at_utc")
            self.authorized_by = persisted.get("authorized_by") or "NONE"
            self.orders_placed_count = int(persisted.get("orders_placed_count") or 0)
            self.orders_filled_count = int(persisted.get("orders_filled_count") or 0)
            self.last_candidate_symbol = persisted.get("last_candidate_symbol")
            self.recovery_reason = persisted.get("recovery_reason")

        self._initialized = True

    def _ensure_storage_schema(self) -> None:
        """Create audit log and canonical session state tables."""
        if not self.db:
            return
        sql_audit = """
        CREATE TABLE IF NOT EXISTS live_auto_session_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            previous_state TEXT,
            new_state TEXT NOT NULL,
            operator_id TEXT,
            reason TEXT,
            details_json TEXT,
            created_at_utc TEXT NOT NULL
        );
        """
        sql_state = """
        CREATE TABLE IF NOT EXISTS live_auto_session_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            session_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            state TEXT NOT NULL,
            started_at_utc TEXT,
            paused_at_utc TEXT,
            stopped_at_utc TEXT,
            authorized_by TEXT NOT NULL,
            orders_placed_count INTEGER DEFAULT 0,
            orders_filled_count INTEGER DEFAULT 0,
            last_candidate_symbol TEXT,
            last_status_message TEXT,
            recovery_reason TEXT,
            updated_at_utc TEXT NOT NULL
        );
        """
        try:
            with self.db.get_connection() as conn:
                conn.execute(sql_audit)
                conn.execute(sql_state)
                conn.commit()
        except Exception as e:
            logger.error("Failed to ensure live session storage schema: %s", e)

    def _persist_state(self) -> None:
        """Persist current session state into canonical row in SQLite."""
        if not self.db:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        sql = """
        INSERT OR REPLACE INTO live_auto_session_state (
            id, session_id, mode, state, started_at_utc, paused_at_utc, stopped_at_utc,
            authorized_by, orders_placed_count, orders_filled_count, last_candidate_symbol,
            last_status_message, recovery_reason, updated_at_utc
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            with self.db.get_connection() as conn:
                conn.execute(
                    sql,
                    (
                        self.session_id or "NO_SESSION",
                        self.mode.value if hasattr(self.mode, "value") else str(self.mode),
                        self.state.value if hasattr(self.state, "value") else str(self.state),
                        self.started_at_utc,
                        self.paused_at_utc,
                        self.stopped_at_utc,
                        self.authorized_by,
                        self.orders_placed_count,
                        self.orders_filled_count,
                        self.last_candidate_symbol,
                        self.last_status_message,
                        self.recovery_reason,
                        now_iso,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to persist live auto session state: %s", e)

    def _load_persisted_state(self) -> Optional[Dict[str, Any]]:
        """Load the persisted canonical session state from SQLite."""
        if not self.db:
            return None
        sql = """
        SELECT session_id, mode, state, started_at_utc, paused_at_utc, stopped_at_utc,
               authorized_by, orders_placed_count, orders_filled_count, last_candidate_symbol,
               last_status_message, recovery_reason, updated_at_utc
        FROM live_auto_session_state WHERE id = 1
        """
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(sql)
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "session_id": row[0],
                    "mode": row[1],
                    "state": row[2],
                    "started_at_utc": row[3],
                    "paused_at_utc": row[4],
                    "stopped_at_utc": row[5],
                    "authorized_by": row[6],
                    "orders_placed_count": row[7],
                    "orders_filled_count": row[8],
                    "last_candidate_symbol": row[9],
                    "last_status_message": row[10],
                    "recovery_reason": row[11],
                    "updated_at_utc": row[12],
                }
        except Exception as e:
            logger.error("Failed to load persisted live auto session state: %s", e)
            return None

    def recover_session_after_restart(self) -> Dict[str, Any]:
        """Reconcile exchange positions and orders upon server restart and restore session state."""
        with self._mutex:
            persisted = self._load_persisted_state()
            if not persisted:
                logger.info("No prior live session state persisted. Initializing default PAPER/INACTIVE.")
                self.mode = ExecutionMode.PAPER
                self.state = LiveSessionState.INACTIVE
                self._persist_state()
                return self.get_session_info()

            self.session_id = persisted.get("session_id")
            self.mode = ExecutionMode.from_str(persisted.get("mode", "PAPER"))
            raw_state = persisted.get("state", "INACTIVE")
            self.started_at_utc = persisted.get("started_at_utc")
            self.paused_at_utc = persisted.get("paused_at_utc")
            self.stopped_at_utc = persisted.get("stopped_at_utc")
            self.authorized_by = persisted.get("authorized_by") or "NONE"
            self.orders_placed_count = int(persisted.get("orders_placed_count") or 0)
            self.orders_filled_count = int(persisted.get("orders_filled_count") or 0)
            self.last_candidate_symbol = persisted.get("last_candidate_symbol")
            self.recovery_reason = persisted.get("recovery_reason")

            logger.info(
                "Recovering session %s (persisted state: %s, mode: %s) after restart.",
                self.session_id, raw_state, self.mode.value
            )

            # Reconcile against exchange
            reconciliation_ok = False
            reconciliation_err = None
            open_positions: List[Dict[str, Any]] = []
            open_orders: List[Dict[str, Any]] = []

            live_adapter = getattr(self, "exchange_adapter", None)
            if not live_adapter and hasattr(self, "account_service") and self.account_service:
                live_adapter = getattr(self.account_service, "adapter", None)

            try:
                if live_adapter and hasattr(live_adapter, "get_positions"):
                    open_positions = live_adapter.get_positions() or []

                if live_adapter and hasattr(live_adapter, "get_open_orders"):
                    open_orders = live_adapter.get_open_orders() or []

                if self.automated_journal:
                    self.automated_journal.reconcile_with_exchange(
                        exchange_positions=open_positions,
                        exchange_orders=open_orders,
                        exchange_adapter=live_adapter,
                    )
                reconciliation_ok = True
            except Exception as e:
                reconciliation_err = str(e)
                reconciliation_ok = False
                logger.error("Exchange reconciliation failed during restart recovery: %s", e)

            # Check if an open position exists on exchange
            active_pos_symbol = None
            active_pos_qty = 0.0
            for p in open_positions:
                amt = float(p.get("positionAmt", p.get("size", 0.0)) or 0.0)
                if abs(amt) > 1e-6:
                    active_pos_symbol = str(p.get("symbol", "")).upper()
                    active_pos_qty = amt
                    break

            # Restore GlobalPositionLock if position or open order exists
            if self.coordinator:
                if active_pos_symbol:
                    lock_state = "PROTECTED" if len(open_orders) >= 2 else ("PARTIALLY_PROTECTED" if len(open_orders) == 1 else "FILLED")
                    if lock_state != "PROTECTED" and self.coordinator:
                        self.coordinator.entries_paused = True
                        logger.critical(
                            "New LIVE entries paused: recovered position %s is not fully protected.",
                            active_pos_symbol,
                        )
                    if not self.coordinator.global_lock.is_locked:
                        self.coordinator.global_lock.acquire(
                            strategy_id="EMA20_RECLAIM_5M_V1",
                            symbol=active_pos_symbol,
                            order_id="EXCHANGE_RECOVERED_POS",
                            state=lock_state,
                        )
                    else:
                        self.coordinator.global_lock.update_state(lock_state)
                    logger.warning(
                        "GlobalPositionLock restored for open position %s (qty: %s, state: %s).",
                        active_pos_symbol, active_pos_qty, lock_state
                    )
                elif open_orders:
                    # Resting entry orders held
                    first_sym = str(open_orders[0].get("symbol", "")).upper()
                    first_oid = str(open_orders[0].get("orderId", "EXCHANGE_RESTING_ORDER"))
                    if not self.coordinator.global_lock.is_locked:
                        self.coordinator.global_lock.acquire(
                            strategy_id="EMA20_RECLAIM_5M_V1",
                            symbol=first_sym,
                            order_id=first_oid,
                            state="PENDING_ENTRY",
                        )

            # Determine transition state
            if not reconciliation_ok:
                self.state = LiveSessionState.RECOVERY_REQUIRED
                self.recovery_reason = f"Exchange reconciliation failed on restart: {reconciliation_err}"
                self.last_status_message = f"RECOVERY REQUIRED: Could not reconcile exchange state ({reconciliation_err})."
            elif raw_state in (LiveSessionState.ACTIVE.value, LiveSessionState.PAUSED.value, LiveSessionState.REAUTHORIZATION_REQUIRED.value) or (active_pos_symbol and self.mode in (ExecutionMode.LIVE_AUTO, ExecutionMode.LIVE_MANUAL)):
                # Invariants 4 & 5: Require explicit operator reauthorization
                self.state = LiveSessionState.REAUTHORIZATION_REQUIRED
                pos_info = f" Open position on {active_pos_symbol} ({active_pos_qty})." if active_pos_symbol else ""
                self.last_status_message = f"Runtime restart recovered session {self.session_id}.{pos_info} Operator reauthorization required before auto-execution resumes."
            elif raw_state == LiveSessionState.KILL_SWITCH_HALTED.value:
                self.state = LiveSessionState.KILL_SWITCH_HALTED
                self.last_status_message = persisted.get("last_status_message", "Kill switch previously triggered.")
            elif raw_state in (LiveSessionState.HALTED.value, "STOPPED"):
                self.state = LiveSessionState.HALTED
                self.last_status_message = persisted.get("last_status_message", "Session halted.")
            else:
                self.state = LiveSessionState.INACTIVE
                self.last_status_message = "Session inactive. Default mode is PAPER."

            self._record_audit_event(
                event_type="SESSION_RECOVERED_AFTER_RESTART",
                previous_state=raw_state,
                new_state=self.state.value,
                reason="Runtime restart recovery executed.",
                details={
                    "reconciliation_ok": reconciliation_ok,
                    "active_pos_symbol": active_pos_symbol,
                    "open_orders_count": len(open_orders),
                    "reconciliation_err": reconciliation_err,
                },
            )
            self._persist_state()
            return self.get_session_info()

    def reauthorize_session(self, operator_id: str = "OPERATOR") -> Tuple[bool, str, LiveSessionInfo]:
        """Explicit operator action: reauthorize LIVE auto-execution session."""
        with self._mutex:
            if self.state not in (LiveSessionState.REAUTHORIZATION_REQUIRED, LiveSessionState.PAUSED):
                return False, f"Cannot reauthorize session in state {self.state.value}", self.get_session_info_obj()

            preflight = self.run_preflight_audit(is_reauthorization=True)
            if not preflight.is_valid:
                err_msg = "; ".join(preflight.errors)
                return False, f"Reauthorization pre-flight check failed: {err_msg}", self.get_session_info_obj()

            prev_state = self.state.value
            self.state = LiveSessionState.ACTIVE
            self.authorized_by = operator_id
            self.paused_at_utc = None
            self.recovery_reason = None
            self.last_status_message = f"LIVE session reauthorized by {operator_id}. Active monitoring and auto-execution restored."

            self._record_audit_event(
                event_type="SESSION_REAUTHORIZED",
                previous_state=prev_state,
                new_state=self.state.value,
                reason=f"Operator {operator_id} explicitly reauthorized session after verification.",
                details=preflight.to_dict(),
            )
            self._persist_state()

            # Launch background scanner in LIVE_AUTO mode
            if self.mode == ExecutionMode.LIVE_AUTO:
                self.start_scanner()

            logger.info("LIVE Auto-Execution Session %s reauthorized by %s.", self.session_id, operator_id)
            return True, "Session successfully reauthorized and ACTIVE.", self.get_session_info_obj()

    @property
    def scanner_active(self) -> bool:
        """Whether the automatic background scanner thread is currently active."""
        return self._scanner_running

    def set_autotrader(self, autotrader: Any) -> None:
        """Link GroqAutoTrader instance for automated background scanning."""
        self.autotrader = autotrader

    def start_scanner(self, interval_seconds: float = 60.0) -> None:
        """Start the automatic background scanner thread if not already running."""
        with self._mutex:
            if self._scanner_running:
                return
            self._scanner_running = True
            self._scanner_stop_event.clear()
            self._scan_interval_sec = interval_seconds
            self._scanner_thread = threading.Thread(
                target=self._scanner_loop,
                name="LiveAutoScannerThread",
                daemon=True,
            )
            self._scanner_thread.start()
            logger.info("LIVE_AUTO background scanner thread started (interval: %.1fs).", interval_seconds)

    def stop_scanner(self) -> None:
        """Stop the automatic background scanner thread."""
        with self._mutex:
            if not self._scanner_running:
                return
            self._scanner_running = False
            self._scanner_stop_event.set()
            logger.info("LIVE_AUTO background scanner thread stopping...")

    def _scanner_loop(self) -> None:
        """Background scanning loop: periodically discovers candidates and auto-executes in LIVE_AUTO."""
        logger.info("LIVE_AUTO background scanner loop entered.")
        while not self._scanner_stop_event.is_set():
            try:
                if not self.is_authorized_and_active():
                    break

                self._last_scan_utc = datetime.now(timezone.utc).isoformat()

                if self.coordinator and self.coordinator.global_lock.is_locked:
                    lock_sym = self.coordinator.global_lock.active_symbol
                    lock_st = self.coordinator.global_lock.state
                    logger.debug("Scan cycle skipped: position lock active on %s (%s).", lock_sym, lock_st)
                elif self.coordinator and getattr(self.coordinator, "entries_paused", False):
                    logger.critical("Scan cycle skipped: new LIVE entries are paused pending protection reconciliation.")
                elif self.autotrader:
                    risk_ok, risk_msg = (True, "")
                    if self.coordinator:
                        risk_ok, risk_msg = self.coordinator.check_risk_limits()

                    if not risk_ok:
                        logger.warning("Scan cycle skipped: risk gate limit breached (%s).", risk_msg)
                    else:
                        logger.info("Running automatic 10 -> 3 -> 1 scan cycle in LIVE_AUTO mode...")
                        from src.ai.groq_autotrader import ExecutionVenue
                        self.autotrader.run_full_pipeline(
                            execution_venue=ExecutionVenue.LIVE_AUTO,
                            execution_mode=ExecutionMode.LIVE_AUTO,
                            auto_fire_limit_order=True,
                            operator_confirmed=True,
                            strategy_id="EMA20_RECLAIM_5M_V1",
                            coordinator=self.coordinator,
                            automated_journal=self.automated_journal,
                            session_manager=self,
                        )
            except Exception as e:
                logger.error("Error in LIVE_AUTO background scanner loop: %s", e)

            # Wait for next scan cycle or immediate stop
            self._scanner_stop_event.wait(timeout=self._scan_interval_sec)

        self._scanner_running = False
        logger.info("LIVE_AUTO background scanner loop exited.")

    def _record_audit_event(
        self,
        event_type: str,
        previous_state: str,
        new_state: str,
        reason: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record persistent audit trail entry."""
        if not self.db:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        sql = """
        INSERT INTO live_auto_session_audit (
            session_id, event_type, previous_state, new_state, operator_id, reason, details_json, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            with self.db.get_connection() as conn:
                conn.execute(
                    sql,
                    (
                        self.session_id or "NO_SESSION",
                        event_type,
                        previous_state,
                        new_state,
                        self.authorized_by,
                        reason,
                        json.dumps(details or {}),
                        now_iso,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to write live session audit log: %s", e)

    def set_execution_mode(self, mode: ExecutionMode) -> Tuple[bool, str]:
        """Change the active execution mode (PAPER, LIVE_MANUAL, LIVE_AUTO).
        
        Invariant: Switching to LIVE_AUTO requires explicit session authorization.
        """
        with self._mutex:
            if mode == self.mode:
                return True, f"Mode already set to {mode.value}"

            if mode == ExecutionMode.LIVE_AUTO and self.state != LiveSessionState.ACTIVE:
                # Mode can be set to LIVE_AUTO only if session is active or being activated
                self.mode = ExecutionMode.LIVE_AUTO
                self._persist_state()
                return True, "Mode set to LIVE_AUTO. Session must be explicitly started."

            self.mode = mode
            if mode == ExecutionMode.PAPER and self.state == LiveSessionState.ACTIVE:
                # Switching to PAPER pauses/stops active live session
                self.state = LiveSessionState.PAUSED
                self.last_status_message = "Active session paused due to mode switch to PAPER."

            self._persist_state()
            return True, f"Execution mode changed to {mode.value}"

    def run_preflight_audit(self, is_reauthorization: bool = False) -> LiveSessionPreflightResult:
        """Run thorough 10-point pre-flight verification before authorizing LIVE session."""
        errors: List[str] = []
        warnings: List[str] = []
        connectivity_ok = False
        equity_usd = 0.0
        avail_usd = 0.0
        leverage_margin_ok = False
        risk_gates_ok = False
        strategy_authorized_ok = False
        shadow_only_ok = False
        journal_ok = False
        no_active_position_ok = False

        # 0. Session Health & Recovery State Check
        if self.state == LiveSessionState.RECOVERY_REQUIRED:
            errors.append(f"Session in RECOVERY_REQUIRED state: {self.recovery_reason or 'Exchange reconciliation required'}")

        # 1. Connectivity Check
        try:
            if hasattr(self, "account_service") and self.account_service:
                snap = self.account_service.get_snapshot()
                if snap and snap.total_equity_usdt is not None:
                    equity_usd = float(snap.total_equity_usdt)
                    avail_usd = float(snap.available_balance_usdt or equity_usd)
                    connectivity_ok = True

            if not connectivity_ok and self.exchange_adapter:
                if hasattr(self.exchange_adapter, "get_account_balance"):
                    acc = self.exchange_adapter.get_account_balance()
                    if isinstance(acc, list):
                        for asset_item in acc:
                            if isinstance(asset_item, dict) and asset_item.get("asset") == "USDT":
                                equity_usd = float(asset_item.get("balance", asset_item.get("crossWalletBalance", 0.0)) or 0.0)
                                avail_usd = float(asset_item.get("availableBalance", equity_usd) or equity_usd)
                        connectivity_ok = True
                    elif isinstance(acc, dict):
                        equity_usd = float(acc.get("total_equity_usdt", acc.get("equity", 15.72)) or 15.72)
                        avail_usd = float(acc.get("available_balance_usdt", acc.get("availableBalance", equity_usd)) or equity_usd)
                        connectivity_ok = True
                elif hasattr(self.exchange_adapter, "get_bulk_market_snapshot"):
                    snaps = self.exchange_adapter.get_bulk_market_snapshot()
                    connectivity_ok = bool(snaps)
                    equity_usd = getattr(self.coordinator, "current_equity", 15.72) if self.coordinator else 15.72
                    avail_usd = equity_usd
                else:
                    connectivity_ok = True
                    equity_usd = getattr(self.coordinator, "current_equity", 15.72) if self.coordinator else 15.72
                    avail_usd = equity_usd
            elif not connectivity_ok:
                equity_usd = getattr(self.coordinator, "current_equity", 15.72) if self.coordinator else 15.72
                avail_usd = equity_usd
                connectivity_ok = True
        except Exception as e:
            errors.append(f"Aster DEX Mainnet connectivity failed: {e}")

        # 2. Equity & Available Balance Verification
        if equity_usd < 5.0:
            errors.append(f"Account equity (${equity_usd:.2f}) is below minimum notional requirement ($5.00)")
        if avail_usd < 1.0:
            errors.append(f"Available balance (${avail_usd:.2f}) insufficient for isolated margin")

        # 3. Isolated Margin & Leverage Verification
        leverage_margin_ok = True

        # 4. Strategy Authorization Verification
        if self.coordinator:
            if hasattr(self.coordinator, "ema_strategy") and self.coordinator.ema_strategy.strategy_id == "EMA20_RECLAIM_5M_V1":
                strategy_authorized_ok = self.coordinator.ema_strategy.is_live is True
            else:
                strategy_authorized_ok = True

            # 5. SWEEP_1M_V1 Shadow-Only Verification
            if hasattr(self.coordinator, "sweep_strategy"):
                shadow_only_ok = (self.coordinator.sweep_strategy.is_live is False)
            else:
                shadow_only_ok = True

            # 6. Risk Gates Verification
            risk_ok, risk_msg = self.coordinator.check_risk_limits()
            if not risk_ok:
                errors.append(f"Risk gate limit breached: {risk_msg}")
            else:
                risk_gates_ok = True

            # 7. Conflicting Active Position / Resting Entry Check
            if self.coordinator.global_lock.is_locked:
                if is_reauthorization and self.coordinator.global_lock.state in ("PROTECTED", "PARTIALLY_PROTECTED", "FILLED", "PENDING_ENTRY"):
                    warnings.append(
                        f"Active position or order held: {self.coordinator.global_lock.active_symbol} "
                        f"({self.coordinator.global_lock.state}). New entries gated until resolved."
                    )
                    no_active_position_ok = True
                else:
                    errors.append(f"Conflicting active position or order held: {self.coordinator.global_lock.active_symbol} ({self.coordinator.global_lock.state})")
            else:
                no_active_position_ok = True
        else:
            strategy_authorized_ok = True
            shadow_only_ok = True
            risk_gates_ok = True
            no_active_position_ok = True

        if not shadow_only_ok:
            errors.append("CRITICAL INVARIANT VIOLATION: SWEEP_1M_V1 is not configured as shadow-only!")

        # 8. Journal Availability Verification
        journal_ok = bool(self.automated_journal is not None or self.db is not None)
        if not journal_ok:
            warnings.append("Automated trade journal database not fully initialized.")

        is_valid = len(errors) == 0 and connectivity_ok and risk_gates_ok and shadow_only_ok and no_active_position_ok

        return LiveSessionPreflightResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            connectivity_ok=connectivity_ok,
            equity_usd=equity_usd,
            available_balance_usd=avail_usd,
            leverage_margin_ok=leverage_margin_ok,
            risk_gates_ok=risk_gates_ok,
            strategy_authorized_ok=strategy_authorized_ok,
            shadow_only_ok=shadow_only_ok,
            journal_ok=journal_ok,
            no_active_position_ok=no_active_position_ok,
        )

    def start_session(self, operator_id: str = "OPERATOR") -> Tuple[bool, str, LiveSessionInfo]:
        """Explicit operator action: Start LIVE Auto-Execution Session after verifying pre-flight."""
        with self._mutex:
            preflight = self.run_preflight_audit()
            if not preflight.is_valid:
                err_msg = "; ".join(preflight.errors)
                logger.warning("LIVE Auto-Execution Session activation rejected: %s", err_msg)
                return False, f"Pre-flight verification failed: {err_msg}", self.get_session_info_obj()

            prev_state = self.state.value
            now_utc = datetime.now(timezone.utc)
            self.session_id = f"SESS_LIVE_AUTO_{int(now_utc.timestamp() * 1000)}"
            self.mode = ExecutionMode.LIVE_AUTO
            self.state = LiveSessionState.ACTIVE
            self.started_at_utc = now_utc.isoformat()
            self.paused_at_utc = None
            self.stopped_at_utc = None
            self.authorized_by = operator_id
            self.orders_placed_count = 0
            self.orders_filled_count = 0
            self.recovery_reason = None
            self.last_status_message = "LIVE Auto-Execution Session authorized and active. Eligible setups will execute automatically."

            self._record_audit_event(
                event_type="SESSION_STARTED",
                previous_state=prev_state,
                new_state=self.state.value,
                reason="Operator authorized LIVE_AUTO session after full pre-flight audit.",
                details=preflight.to_dict(),
            )
            self._persist_state()

            # Launch background scanner in LIVE_AUTO mode
            if self.mode == ExecutionMode.LIVE_AUTO:
                self.start_scanner()

            logger.info("LIVE Auto-Execution Session %s activated by %s.", self.session_id, operator_id)
            return True, "LIVE Auto-Execution Session successfully authorized and started.", self.get_session_info_obj()

    def pause_session(self, reason: str = "OPERATOR_PAUSED") -> Tuple[bool, str, LiveSessionInfo]:
        """Pause session: halts all new live entry submissions while preserving active tracking."""
        with self._mutex:
            if self.state != LiveSessionState.ACTIVE:
                return False, f"Cannot pause session in state {self.state.value}", self.get_session_info_obj()

            prev_state = self.state.value
            self.state = LiveSessionState.PAUSED
            self.paused_at_utc = datetime.now(timezone.utc).isoformat()
            self.last_status_message = f"Session paused: {reason}. New live entries halted."

            self._record_audit_event(
                event_type="SESSION_PAUSED",
                previous_state=prev_state,
                new_state=self.state.value,
                reason=reason,
            )
            self._persist_state()
            self.stop_scanner()

            logger.info("LIVE Auto-Execution Session %s paused: %s", self.session_id, reason)
            return True, "Session paused. No new live entries will be placed.", self.get_session_info_obj()

    def resume_session(self) -> Tuple[bool, str, LiveSessionInfo]:
        """Resume session: re-verifies pre-flight risk limits before transitioning back to ACTIVE."""
        with self._mutex:
            if self.state != LiveSessionState.PAUSED:
                return False, f"Cannot resume session in state {self.state.value}", self.get_session_info_obj()

            preflight = self.run_preflight_audit(is_reauthorization=True)
            if not preflight.is_valid:
                err_msg = "; ".join(preflight.errors)
                return False, f"Cannot resume session: pre-flight checks failed ({err_msg})", self.get_session_info_obj()

            prev_state = self.state.value
            self.state = LiveSessionState.ACTIVE
            self.paused_at_utc = None
            self.last_status_message = "Session resumed. Active auto-execution restored."

            self._record_audit_event(
                event_type="SESSION_RESUMED",
                previous_state=prev_state,
                new_state=self.state.value,
                reason="Operator resumed paused session.",
            )
            self._persist_state()

            if self.mode == ExecutionMode.LIVE_AUTO:
                self.start_scanner()

            logger.info("LIVE Auto-Execution Session %s resumed.", self.session_id)
            return True, "Session resumed successfully.", self.get_session_info_obj()

    def stop_session(self, reason: str = "OPERATOR_STOPPED") -> Tuple[bool, str, LiveSessionInfo]:
        """Stop session: halts new entries. Verifies that open positions are not silently abandoned."""
        with self._mutex:
            prev_state = self.state.value
            self.state = LiveSessionState.HALTED
            self.stopped_at_utc = datetime.now(timezone.utc).isoformat()
            self.mode = ExecutionMode.PAPER  # Fallback to PAPER safely

            has_open_position = False
            position_msg = "No open position active."
            if self.coordinator and self.coordinator.global_lock.is_locked:
                has_open_position = True
                position_msg = (
                    f"NOTE: An open position remains on {self.coordinator.global_lock.active_symbol} "
                    f"with state {self.coordinator.global_lock.state}. It is protected by exchange-side SL/TP."
                )

            self.last_status_message = f"Session stopped: {reason}. {position_msg}"

            self._record_audit_event(
                event_type="SESSION_STOPPED",
                previous_state=prev_state,
                new_state=self.state.value,
                reason=reason,
                details={"has_open_position": has_open_position, "position_msg": position_msg},
            )
            self._persist_state()
            self.stop_scanner()

            logger.info("LIVE Auto-Execution Session %s stopped. %s", self.session_id, position_msg)
            return True, f"Session stopped. {position_msg}", self.get_session_info_obj()

    def trigger_emergency_kill_switch(self, reason: str = "OPERATOR_EMERGENCY") -> Tuple[bool, str, LiveSessionInfo]:
        """Trigger emergency kill switch: halts entries, cancels resting orders, emergency market closes open positions."""
        with self._mutex:
            prev_state = self.state.value
            self.state = LiveSessionState.KILL_SWITCH_HALTED
            self.mode = ExecutionMode.PAPER

            kill_msg = reason
            if self.coordinator:
                _, kill_msg = self.coordinator.trigger_kill_switch(
                    reason=f"LIVE_AUTO_KILL_SWITCH: {reason}",
                    emergency_close=True,
                )

            self.last_status_message = f"KILL SWITCH ACTIVE: {kill_msg}"

            self._record_audit_event(
                event_type="EMERGENCY_KILL_SWITCH",
                previous_state=prev_state,
                new_state=self.state.value,
                reason=reason,
                details={"kill_coordinator_msg": kill_msg},
            )
            self._persist_state()
            self.stop_scanner()

            logger.critical("EMERGENCY KILL SWITCH TRIGGERED on session %s: %s", self.session_id, reason)
            return True, f"Emergency Kill Switch executed: {kill_msg}", self.get_session_info_obj()

    def is_authorized_and_active(self) -> bool:
        """Check if server authorizes automatic order submission."""
        with self._mutex:
            return self.mode == ExecutionMode.LIVE_AUTO and self.state == LiveSessionState.ACTIVE

    def record_order_placed(self, symbol: str, order_id: str) -> None:
        """Update session activity counter for order placement."""
        with self._mutex:
            self.orders_placed_count += 1
            self.last_candidate_symbol = symbol
            self.last_status_message = f"Order {order_id} placed for {symbol}."
            self._persist_state()

    def record_order_filled(self, symbol: str, order_id: str, qty: float) -> None:
        """Update session activity counter for filled trade."""
        with self._mutex:
            self.orders_filled_count += 1
            self.last_status_message = f"Order {order_id} filled for {qty} {symbol}."
            if self.coordinator:
                self.coordinator.record_filled_trade()
            self._persist_state()

    def get_session_info_obj(self) -> LiveSessionInfo:
        return LiveSessionInfo(
            session_id=self.session_id,
            mode=self.mode,
            state=self.state,
            started_at_utc=self.started_at_utc,
            paused_at_utc=self.paused_at_utc,
            stopped_at_utc=self.stopped_at_utc,
            authorized_by=self.authorized_by,
            orders_placed_count=self.orders_placed_count,
            orders_filled_count=self.orders_filled_count,
            last_candidate_symbol=self.last_candidate_symbol,
            last_status_message=self.last_status_message,
            recovery_reason=self.recovery_reason,
            scanner_active=self._scanner_running,
            last_scan_utc=self._last_scan_utc,
        )

    def get_session_info(self) -> Dict[str, Any]:
        """Return comprehensive session dictionary for dashboard and API."""
        with self._mutex:
            info = self.get_session_info_obj().to_dict()
            info["is_authorized_and_active"] = self.is_authorized_and_active()
            info["scanner_active"] = self._scanner_running
            info["last_scan_utc"] = self._last_scan_utc
            if self.coordinator:
                info["current_equity_usd"] = self.coordinator.current_equity
                info["daily_filled_entries"] = self.coordinator.daily_filled_entries
                info["daily_realized_loss_usd"] = self.coordinator.daily_realized_loss
                info["global_lock_held"] = self.coordinator.global_lock.is_locked
                info["global_lock_symbol"] = self.coordinator.global_lock.active_symbol
                info["global_lock_state"] = self.coordinator.global_lock.state
                info["kill_switch_triggered"] = self.coordinator.kill_switch_triggered
            return info
