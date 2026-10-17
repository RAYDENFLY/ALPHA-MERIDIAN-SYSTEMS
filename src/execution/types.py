"""Domain types and state machine for Paper Trading Execution.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from src.core.exceptions import InvalidOrderStateTransitionError
from src.core.types import Direction


class PaperOrderStatus(str, Enum):
    """Lifecycle states of a simulated paper order."""
    CREATED = "CREATED"
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"


# Valid state machine transitions
ALLOWED_TRANSITIONS: Dict[PaperOrderStatus, Set[PaperOrderStatus]] = {
    PaperOrderStatus.CREATED: {PaperOrderStatus.PENDING, PaperOrderStatus.REJECTED},
    PaperOrderStatus.PENDING: {
        PaperOrderStatus.PARTIALLY_FILLED,
        PaperOrderStatus.FILLED,
        PaperOrderStatus.CANCELLED,
        PaperOrderStatus.EXPIRED,
        PaperOrderStatus.INVALIDATED,
    },
    PaperOrderStatus.PARTIALLY_FILLED: {
        PaperOrderStatus.FILLED,
        PaperOrderStatus.CANCELLED,
    },
    # Terminal states (no transitions allowed)
    PaperOrderStatus.FILLED: set(),
    PaperOrderStatus.CANCELLED: set(),
    PaperOrderStatus.EXPIRED: set(),
    PaperOrderStatus.REJECTED: set(),
    PaperOrderStatus.INVALIDATED: set(),
}


@dataclass
class PaperOrder:
    """Represents a simulated paper order with strict state transitions."""
    order_id: str
    candidate_id: str
    symbol: str
    direction: Direction
    order_type: str
    price: float
    stop_loss: float
    take_profit: float
    quantity: float
    notional_usdt: float
    margin_usdt: float
    leverage: float
    created_bar_time_ms: int
    status: PaperOrderStatus = PaperOrderStatus.CREATED
    evaluated_bars_count: int = 0
    filled_time_ms: Optional[int] = None
    fill_price: Optional[float] = None
    slippage_paid: float = 0.0
    fee_paid: float = 0.0
    rejection_reason: str = ""
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    entry_category: str = "Unclassified"
    take_profit_2: Optional[float] = None

    def transition_to(self, new_status: PaperOrderStatus, reason: Optional[str] = None) -> None:
        """Validate and apply a state transition.

        Raises:
            InvalidOrderStateTransitionError: If the transition is prohibited.
        """
        if new_status == self.status:
            return

        valid_targets = ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in valid_targets:
            raise InvalidOrderStateTransitionError(
                f"Illegal order state transition for order {self.order_id}: "
                f"{self.status.value} -> {new_status.value}."
            )

        self.status = new_status
        self.updated_at_utc = datetime.now(timezone.utc).isoformat()
        if reason:
            self.rejection_reason = reason

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary representation."""
        return {
            "order_id": self.order_id,
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "direction": self.direction.value if hasattr(self.direction, "value") else str(self.direction),
            "entry_category": self.entry_category,
            "order_type": self.order_type,
            "price": self.price,
            "stop_price": self.stop_loss,
            "take_profit": self.take_profit,
            "take_profit_2": self.take_profit_2,
            "quantity": self.quantity,
            "notional_usdt": self.notional_usdt,
            "margin_usdt": self.margin_usdt,
            "leverage": self.leverage,
            "status": self.status.value,
            "created_bar_time_ms": self.created_bar_time_ms,
            "evaluated_bars_count": self.evaluated_bars_count,
            "filled_time_ms": self.filled_time_ms,
            "fill_price": self.fill_price,
            "slippage_paid": self.slippage_paid,
            "fee_paid": self.fee_paid,
            "rejection_reason": self.rejection_reason,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperOrder":
        """Deserialize from dictionary representation."""
        direction_str = data["direction"]
        direction = Direction.LONG if str(direction_str).upper() == "LONG" else Direction.SHORT

        status_str = data["status"]
        status = PaperOrderStatus(status_str)

        tp2_raw = data.get("take_profit_2")
        tp2_val = float(tp2_raw) if tp2_raw is not None else None

        return cls(
            order_id=data["order_id"],
            candidate_id=data["candidate_id"],
            symbol=data["symbol"],
            direction=direction,
            order_type=data["order_type"],
            price=float(data["price"]),
            stop_loss=float(data.get("stop_price") or data.get("stop_loss", 0.0)),
            take_profit=float(data.get("take_profit", 0.0)),
            quantity=float(data["quantity"]),
            notional_usdt=float(data["notional_usdt"]),
            margin_usdt=float(data["margin_usdt"]),
            leverage=float(data["leverage"]),
            created_bar_time_ms=int(data["created_bar_time_ms"]),
            status=status,
            evaluated_bars_count=int(data.get("evaluated_bars_count", 0)),
            filled_time_ms=int(data["filled_time_ms"]) if data.get("filled_time_ms") else None,
            fill_price=float(data["fill_price"]) if data.get("fill_price") else None,
            slippage_paid=float(data.get("slippage_paid", 0.0)),
            fee_paid=float(data.get("fee_paid", 0.0)),
            rejection_reason=data.get("rejection_reason", ""),
            created_at_utc=data.get("created_at_utc", ""),
            updated_at_utc=data.get("updated_at_utc", ""),
            entry_category=data.get("entry_category", "Unclassified"),
            take_profit_2=tp2_val,
        )


@dataclass
class PaperPosition:
    """Represents an active open paper trading position with TP1 partial close and trailing-stop state."""
    trade_id: str
    order_id: str
    setup_id: str
    symbol: str
    direction: Direction
    entry_timestamp_ms: int
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    notional_usdt: float
    margin_usdt: float
    leverage: float
    entry_fee: float
    funding_paid: float = 0.0
    funding_data_missing: bool = False
    is_open: bool = True
    settled_funding_timestamps: List[int] = field(default_factory=list)
    entry_category: str = "Unclassified"
    initial_stop_loss: Optional[float] = None
    initial_quantity: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    tp1_hit: bool = False
    tp1_hit_timestamp_ms: Optional[int] = None
    realized_partial_pnl_gross: float = 0.0
    realized_partial_fee_paid: float = 0.0
    trailing_active: bool = False
    highest_favorable_price: Optional[float] = None
    lowest_favorable_price: Optional[float] = None
    trailing_distance: Optional[float] = None
    breakeven_stop_price: Optional[float] = None

