"""Data Models for Automated Trade Journal and Fill Ledger.

Implements all required fields specified in Section 15.2 and 15.3:
- Full identity and attribution (strategy, client_order_id, position_id, mode)
- Signal and planned trade metrics (planned prices, quantities, risk, expected Net-RR)
- Actual exchange execution details (fill counts, fill types, fees, slippage)
- Verified costs and PnL results (gross PnL, fees, funding, net PnL, realized R)
- Diagnostics, audit trail, and reconciliation flags
- Fill-level ledger for individual exchange fills
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Dict, List, Optional


class JournalMode(str, Enum):
    LIVE = "LIVE"
    SHADOW = "SHADOW"
    PAPER = "PAPER"


class TradeSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(str, Enum):
    WATCHLIST = "WATCHLIST"
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    MISS = "MISS"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class ReconciliationStatus(str, Enum):
    RECONCILED = "RECONCILED"
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
    DISCREPANCY = "DISCREPANCY"
    REQUIRES_RECONCILIATION = "REQUIRES_RECONCILIATION"


class DataQualityFlag(str, Enum):
    EXCHANGE_VERIFIED = "EXCHANGE_VERIFIED"
    LOCALLY_CALCULATED = "LOCALLY_CALCULATED"
    ESTIMATED_FUNDING = "ESTIMATED_FUNDING"
    PENDING_EXCHANGE_FILLS = "PENDING_EXCHANGE_FILLS"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class FillRecord:
    """Represents a single granular fill from AsterDEX (Section 15.3)."""
    fill_id: str
    trade_id: str
    order_id: str
    timestamp_utc: str
    timestamp_ms: int
    symbol: str
    side: str  # BUY, SELL
    fill_role: str  # ENTRY, EXIT
    fill_price: float
    fill_quantity: float
    fill_notional_usd: float
    fee_amount: float = 0.0
    fee_asset: str = "USDT"
    fee_usd: float = 0.0
    liquidity_type: str = "UNKNOWN"  # MAKER, TAKER, UNKNOWN
    slippage_pct: float = 0.0
    exchange_trade_id: str = ""
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FillRecord":
        return cls(
            fill_id=str(data["fill_id"]),
            trade_id=str(data["trade_id"]),
            order_id=str(data["order_id"]),
            timestamp_utc=str(data["timestamp_utc"]),
            timestamp_ms=int(data["timestamp_ms"]),
            symbol=str(data["symbol"]),
            side=str(data["side"]).upper(),
            fill_role=str(data["fill_role"]).upper(),
            fill_price=float(data["fill_price"]),
            fill_quantity=float(data["fill_quantity"]),
            fill_notional_usd=float(data["fill_notional_usd"]),
            fee_amount=float(data.get("fee_amount", 0.0)),
            fee_asset=str(data.get("fee_asset", "USDT")),
            fee_usd=float(data.get("fee_usd", 0.0)),
            liquidity_type=str(data.get("liquidity_type", "UNKNOWN")).upper(),
            slippage_pct=float(data.get("slippage_pct", 0.0)),
            exchange_trade_id=str(data.get("exchange_trade_id", "")),
            created_at_utc=str(data.get("created_at_utc", datetime.now(timezone.utc).isoformat())),
        )


@dataclass
class AutomatedTradeRecord:
    """Master Trade Record fulfilling Section 15.2 specification."""
    # Identity and attribution
    journal_id: str
    trade_id: str
    exchange: str = "Aster DEX"
    symbol: str = ""
    strategy_id: str = "EMA20_RECLAIM_5M_V1"
    config_version: str = "LIVE_MICRO_V1"
    signal_id: str = ""
    order_id: str = ""
    client_order_id: str = ""
    position_id: str = ""
    mode: str = "LIVE"  # LIVE, SHADOW, PAPER
    side: str = "LONG"  # LONG, SHORT
    status: str = "PENDING"  # PENDING, OPEN, CLOSED, MISS, EXPIRED, CANCELLED, ERROR

    # Signal and planned trade
    signal_timestamp_utc: Optional[str] = None
    signal_price: Optional[float] = None
    planned_entry_price: Optional[float] = None
    planned_stop_price: Optional[float] = None
    planned_take_profit_price: Optional[float] = None
    planned_stop_pct: Optional[float] = None
    planned_rr_gross: Optional[float] = None
    planned_rr_net_expected: Optional[float] = None
    planned_risk_usd: float = 0.20
    planned_quantity: Optional[float] = None
    planned_notional_usd: Optional[float] = None
    planned_margin_usd: Optional[float] = None
    leverage: int = 10
    margin_mode: str = "ISOLATED"
    entry_order_type: str = "LIMIT"
    entry_ttl_seconds: int = 1500

    # Exchange-side Protective Orders (Section 15 & TP Bracket Spec)
    tp_order_id: Optional[str] = None
    tp_order_type: str = "LIMIT"
    tp_status: str = "NOT_PLACED"  # NOT_PLACED, PLACED, FILLED, PARTIALLY_FILLED, CANCELLED, REJECTED
    tp_price: Optional[float] = None
    tp_quantity: Optional[float] = None
    tp_filled_quantity: float = 0.0
    tp_fee_usd: float = 0.0
    sl_order_id: Optional[str] = None
    sl_order_type: str = "STOP_MARKET"
    sl_status: str = "NOT_PLACED"  # NOT_PLACED, PLACED, FILLED, CANCELLED, REJECTED
    sl_price: Optional[float] = None
    protection_status: str = "UNPROTECTED"  # UNPROTECTED, PROTECTED, PARTIALLY_PROTECTED (SL ONLY - TP NOT PLACED), TP_NOT_PLACED

    # Actual execution
    first_fill_timestamp_utc: Optional[str] = None
    last_fill_timestamp_utc: Optional[str] = None
    average_entry_price: Optional[float] = None
    filled_quantity: float = 0.0
    entry_fill_count: int = 0
    entry_fill_type: str = "UNKNOWN"  # MAKER, TAKER, MIXED, UNKNOWN
    entry_fee_usd: float = 0.0
    entry_slippage_pct: float = 0.0
    exit_timestamp_utc: Optional[str] = None
    average_exit_price: Optional[float] = None
    exit_quantity: float = 0.0
    exit_fill_count: int = 0
    exit_fill_type: str = "UNKNOWN"
    exit_fee_usd: float = 0.0
    exit_slippage_pct: float = 0.0
    exit_reason: str = ""  # TAKE_PROFIT, STOP_LOSS, TIMEOUT, MANUAL, CANCELLED

    # Costs and results
    gross_pnl_usd: float = 0.0
    funding_usd: float = 0.0
    total_fees_usd: float = 0.0
    net_pnl_usd: float = 0.0
    realized_r: float = 0.0
    equity_before_usd: Optional[float] = None
    equity_after_usd: Optional[float] = None
    max_favorable_excursion_r: float = 0.0
    max_adverse_excursion_r: float = 0.0
    holding_duration_seconds: float = 0.0

    # Audit and diagnostics
    llm_selected: int = 0
    llm_vetoed: int = 0
    llm_reason: str = ""
    trigger_reason: str = ""
    risk_gate_result: str = "PASSED"
    cost_gate_result: str = "PASSED"
    rejection_reason: str = ""
    reconciliation_status: str = "RECONCILED"  # RECONCILED, PENDING_RECONCILIATION, REQUIRES_RECONCILIATION
    data_quality_flags: str = "EXCHANGE_VERIFIED"  # EXCHANGE_VERIFIED, LOCALLY_CALCULATED, ESTIMATED_FUNDING
    notes: str = ""
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutomatedTradeRecord":
        clean_data = {}
        for f_name in cls.__dataclass_fields__:
            if f_name in data:
                clean_data[f_name] = data[f_name]
        return cls(**clean_data)


@dataclass
class JournalFilter:
    """Filter criteria for querying trades and exporting CSVs."""
    start_date: Optional[str] = None  # YYYY-MM-DD or ISO
    end_date: Optional[str] = None
    strategy_id: Optional[str] = None
    mode: Optional[str] = None  # LIVE, SHADOW, PAPER, ALL
    symbol: Optional[str] = None
    status: Optional[str] = None
    closed_only: bool = False
    limit: int = 5000
