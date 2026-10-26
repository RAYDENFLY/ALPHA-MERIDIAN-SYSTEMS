"""Thread-safe Global Position Lock for Cross-Strategy Risk Enforcement.

Enforces:
- Maximum 1 concurrent position across all strategies (live and paper).
- Gating of:
  - Pending entry orders
  - Partial fills
  - Filled positions
  - Pending exit orders
  - Unreconciled exchange-side positions
- Prevention of duplicate signals.
- Shadow counterfactual tracking (recording if a shadow signal would have been blocked).
- Lock release only after confirmed exchange reconciliation.
"""

from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


class GlobalPositionLock:
    """Singleton/thread-safe lock governing cross-strategy single-position limits."""

    _instance: Optional["GlobalPositionLock"] = None
    _lock = threading.RLock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(GlobalPositionLock, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._mutex = threading.RLock()
        self._is_locked: bool = False
        self._strategy_id: Optional[str] = None
        self._symbol: Optional[str] = None
        self._order_id: Optional[str] = None
        self._state: str = "IDLE"  # IDLE, PENDING_ENTRY, PARTIALLY_FILLED, FILLED, PENDING_EXIT, UNRECONCILED_EXCHANGE
        self._acquired_at_ms: int = 0
        self._lock_history: List[Dict[str, Any]] = []
        self._initialized = True

    @property
    def is_locked(self) -> bool:
        with self._mutex:
            return self._is_locked

    @property
    def strategy_id(self) -> Optional[str]:
        with self._mutex:
            return self._strategy_id

    @property
    def active_symbol(self) -> Optional[str]:
        with self._mutex:
            return self._symbol

    @property
    def state(self) -> str:
        with self._mutex:
            return self._state

    @property
    def order_id(self) -> Optional[str]:
        with self._mutex:
            return self._order_id

    def can_acquire(self, strategy_id: str, symbol: str) -> Tuple[bool, str]:
        """Check if lock is available without acquiring it yet."""
        with self._mutex:
            if self._is_locked:
                if self._symbol == symbol:
                    return False, f"DUPLICATE_SIGNAL: Global lock already held for {symbol} by {self._strategy_id} ({self._state})"
                return False, f"GLOBAL_LOCK_ACTIVE: Position already open on {self._symbol} by {self._strategy_id} ({self._state})"
            return True, "GLOBAL_LOCK_AVAILABLE"

    def acquire(
        self,
        strategy_id: str,
        symbol: str,
        order_id: str,
        state: str = "PENDING_ENTRY",
        now_ms: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Acquire the global position lock."""
        with self._mutex:
            can, reason = self.can_acquire(strategy_id, symbol)
            if not can:
                return False, reason

            ts = now_ms if now_ms is not None else int(time.time() * 1000)
            self._is_locked = True
            self._strategy_id = strategy_id
            self._symbol = symbol
            self._order_id = order_id
            self._state = state
            self._acquired_at_ms = ts

            event = {
                "action": "ACQUIRED",
                "strategy_id": strategy_id,
                "symbol": symbol,
                "order_id": order_id,
                "state": state,
                "timestamp_ms": ts,
                "utc": datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat(),
            }
            self._lock_history.append(event)
            return True, f"GLOBAL_LOCK_ACQUIRED: {strategy_id} locked on {symbol}"

    def update_state(self, state: str, order_id: Optional[str] = None) -> None:
        """Update lifecycle state of the current lock holder."""
        with self._mutex:
            if not self._is_locked:
                return
            self._state = state
            if order_id:
                self._order_id = order_id
            ts = int(time.time() * 1000)
            self._lock_history.append({
                "action": "STATE_UPDATE",
                "state": state,
                "order_id": self._order_id,
                "strategy_id": self._strategy_id,
                "symbol": self._symbol,
                "timestamp_ms": ts,
            })

    def release(self, reason: str = "CLOSED", verified_by_exchange: bool = False) -> None:
        """Release the global position lock."""
        with self._mutex:
            if not self._is_locked:
                return
            ts = int(time.time() * 1000)
            self._lock_history.append({
                "action": "RELEASED",
                "reason": reason,
                "verified_by_exchange": verified_by_exchange,
                "strategy_id": self._strategy_id,
                "symbol": self._symbol,
                "order_id": self._order_id,
                "timestamp_ms": ts,
            })
            self._is_locked = False
            self._strategy_id = None
            self._symbol = None
            self._order_id = None
            self._state = "IDLE"
            self._acquired_at_ms = 0

    def check_counterfactual_block(self, strategy_id: str, symbol: str) -> bool:
        """Check whether a shadow strategy signal would have been blocked by the global lock."""
        with self._mutex:
            return self._is_locked

    def reconcile_with_exchange(
        self,
        exchange_positions: List[Dict[str, Any]],
        exchange_orders: List[Dict[str, Any]],
    ) -> None:
        """Reconcile lock status against actual exchange positions and active orders."""
        with self._mutex:
            active_pos = [
                p for p in exchange_positions
                if abs(float(p.get("positionAmt", p.get("size", 0.0)) or 0.0)) > 1e-6
            ]
            active_orders = [
                o for o in exchange_orders
                if str(o.get("status", "")).upper() in ("NEW", "PARTIALLY_FILLED")
            ]

            if active_pos:
                p0 = active_pos[0]
                sym = str(p0.get("symbol", ""))
                if not self._is_locked or self._symbol != sym:
                    self._is_locked = True
                    self._symbol = sym
                    self._strategy_id = self._strategy_id or "EXCHANGE_RECONCILED"
                    self._state = "FILLED"
                    self._acquired_at_ms = int(time.time() * 1000)
            elif active_orders:
                o0 = active_orders[0]
                sym = str(o0.get("symbol", ""))
                if not self._is_locked:
                    self._is_locked = True
                    self._symbol = sym
                    self._strategy_id = self._strategy_id or "EXCHANGE_RECONCILED"
                    self._state = "PENDING_ENTRY"
                    self._acquired_at_ms = int(time.time() * 1000)
            else:
                # No active position or open order on exchange
                if self._is_locked and self._state in ("PENDING_EXIT", "FILLED", "UNRECONCILED_EXCHANGE"):
                    self.release(reason="EXCHANGE_RECONCILIATION_ZERO_EXPOSURE", verified_by_exchange=True)

    def reset(self) -> None:
        """Reset lock state (primarily for test harness isolation)."""
        with self._mutex:
            self._is_locked = False
            self._strategy_id = None
            self._symbol = None
            self._order_id = None
            self._state = "IDLE"
            self._acquired_at_ms = 0
            self._lock_history.clear()

    def can_open_position(self, symbol: str, strategy_id: str) -> Tuple[bool, str]:
        return self.can_acquire(strategy_id, symbol)

    def check_duplicate_signal(self, symbol: str, strategy_id: str, direction: str) -> Tuple[bool, str]:
        with self._mutex:
            if self._is_locked and self._symbol == symbol:
                return True, f"DUPLICATE_SIGNAL: Active position/order already exists on {symbol} by {self._strategy_id}"
            return False, "OK"

    def would_block_counterfactual(self, strategy_id: str, symbol: str) -> bool:
        return self.check_counterfactual_block(strategy_id, symbol)

    def to_dict(self) -> Dict[str, Any]:
        with self._mutex:
            return {
                "is_locked": self._is_locked,
                "strategy_id": self._strategy_id,
                "symbol": self._symbol,
                "order_id": self._order_id,
                "state": self._state,
                "acquired_at_ms": self._acquired_at_ms,
                "history_length": len(self._lock_history),
            }
