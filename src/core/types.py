"""Core domain types, dataclasses, and enums for SMC-SNR ML Perpetual Setup Scanner.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class MarketBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class StructureState(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    SIDEWAYS = "SIDEWAYS"


class SwingType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class SMCEventType(str, Enum):
    BOS = "BOS"
    CHOCH = "CHOCH"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    ORDER_BLOCK = "ORDER_BLOCK"
    FAIR_VALUE_GAP = "FAIR_VALUE_GAP"


class CandidateStatus(str, Enum):
    READY = "READY"
    VALID = "READY"  # Backward-compatibility alias for READY
    WATCHLIST = "WATCHLIST"
    INFEASIBLE = "INFEASIBLE"
    INVALIDATED = "INVALIDATED"
    STALE = "STALE"
    ERROR = "ERROR"
    REJECTED = "REJECTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_upper = value.upper()
            if val_upper == "VALID":
                return cls.READY
            for member in cls:
                if member.value == val_upper:
                    return member
        return super()._missing_(value)


class ExecutionStatus(str, Enum):
    """Execution status decoupled from strategy qualification (CandidateStatus)."""
    NOT_SUBMITTED = "NOT_SUBMITTED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTED = "PROTECTED"
    CLOSE_PENDING = "CLOSE_PENDING"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_upper = value.upper()
            for member in cls:
                if member.value == val_upper:
                    return member
        return super()._missing_(value)


class EntryCategory(str, Enum):
    """Structural classification of setup entry derived from SMC/SnR market evidence."""
    BREAKOUT = "Breakout"
    PULLBACK = "Pullback"
    REVERSAL = "Reversal"
    RANGE_MEAN_REVERSION = "Range/Mean Reversion"
    UNCLASSIFIED = "Unclassified"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_clean = value.strip().upper().replace(" ", "_").replace("/", "_")
            if "BREAKOUT" in val_clean:
                return cls.BREAKOUT
            if "PULLBACK" in val_clean:
                return cls.PULLBACK
            if "REVERSAL" in val_clean:
                return cls.REVERSAL
            if "RANGE" in val_clean or "MEAN_REVERSION" in val_clean:
                return cls.RANGE_MEAN_REVERSION
            if "UNCLASSIFIED" in val_clean or "OTHER" in val_clean:
                return cls.UNCLASSIFIED
            for member in cls:
                if member.value.upper() == value.strip().upper():
                    return member
            return cls.UNCLASSIFIED
        return cls.UNCLASSIFIED



@dataclass
class Candle:
    """Represents a validated OHLCV candlestick with UTC timestamp."""
    symbol: str
    timeframe: str
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time_ms: int
    quote_volume: float = 0.0
    trades_count: int = 0
    is_closed: bool = True

    @property
    def datetime_utc(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_ms / 1000.0, tz=timezone.utc)

    @property
    def hl2(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def hlc3(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def ohlc4(self) -> float:
        return (self.open + self.high + self.low + self.close) / 4.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp_ms": self.timestamp_ms,
            "datetime_utc": self.datetime_utc.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "close_time_ms": self.close_time_ms,
            "quote_volume": self.quote_volume,
            "trades_count": self.trades_count,
            "is_closed": self.is_closed,
        }

    @classmethod
    def from_raw_kline(cls, raw: List[Any], symbol: str, timeframe: str) -> "Candle":
        """Parse raw kline array from Aster DEX / Binance format:
        [0: open_time, 1: open, 2: high, 3: low, 4: close, 5: volume, 6: close_time,
         7: quote_volume, 8: trades, 9: taker_buy_base, 10: taker_buy_quote, 11: ignore]
        """
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            timestamp_ms=int(raw[0]),
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
            close_time_ms=int(raw[6]),
            quote_volume=float(raw[7]) if len(raw) > 7 else 0.0,
            trades_count=int(raw[8]) if len(raw) > 8 else 0,
            is_closed=True,
        )


@dataclass
class SwingPoint:
    """Represents a confirmed swing high or swing low.
    confirmed_timestamp_ms marks when the swing is confirmed (no lookahead).
    """
    index: int
    timestamp_ms: int
    confirmed_index: int
    confirmed_timestamp_ms: int
    price: float
    swing_type: SwingType
    broken: bool = False
    broken_timestamp_ms: Optional[int] = None


@dataclass
class SMCEvent:
    """Represents a detected Smart Money Concept event."""
    event_type: SMCEventType
    direction: Direction
    timestamp_ms: int
    price_level: float
    zone_high: float
    zone_low: float
    reference_timestamp_ms: Optional[int] = None
    description: str = ""


@dataclass
class OrderBlock:
    """Represents an identified Order Block zone."""
    direction: Direction
    timestamp_ms: int
    high: float
    low: float
    volume: float = 0.0
    mitigated: bool = False
    mitigated_timestamp_ms: Optional[int] = None


@dataclass
class FairValueGap:
    """Represents a 3-candle Fair Value Gap."""
    direction: Direction
    timestamp_ms: int
    top: float
    bottom: float
    mitigated: bool = False
    mitigated_timestamp_ms: Optional[int] = None


@dataclass
class SnRZone:
    """Support and Resistance zone aggregated from historical swing interactions."""
    zone_id: str
    price_center: float
    price_low: float
    price_high: float
    touch_count: int = 0
    rejection_count: int = 0
    breakout_count: int = 0
    last_interaction_timestamp_ms: int = 0
    is_active: bool = True
    strength: float = 1.0


@dataclass
class RiskAssessment:
    """Results of position sizing and risk validation."""
    symbol: str
    entry_price: float
    stop_loss: float
    stop_distance_pct: float
    risk_budget_usdt: float
    position_notional_usdt: float
    quantity: float
    required_margin_usdt: float
    effective_leverage: float
    estimated_fee_usdt: float
    estimated_slippage_usdt: float
    is_feasible: bool
    infeasible_reason: Optional[str] = None
    target_isolated_margin_usdt: float = 6.0
    estimated_loss_usdt: float = 0.0
    estimated_loss_pct: float = 0.0
    position_leverage: float = 10.0
    equity_exposure_ratio: float = 0.0
    estimated_funding_usdt: float = 0.0

    @property
    def actual_margin_usdt(self) -> float:
        return self.required_margin_usdt

    @property
    def leverage(self) -> float:
        return self.position_leverage



@dataclass
class StrategyDiagnostic:
    """Detailed diagnostic metadata explaining why a candidate setup was or was not generated."""
    reason_code: str
    reason_detail: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SetupCandidate:
    """A proposed LONG or SHORT trading candidate generated by strategy rules."""
    candidate_id: str
    symbol: str
    timestamp_ms: int
    direction: Direction
    bias_4h: MarketBias
    structure_1h: StructureState
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float] = None
    gross_reward_to_risk: float = 0.0
    net_reward_to_risk: float = 0.0
    invalidation_price: float = 0.0
    invalidation_reason: str = ""
    smc_evidence: List[str] = field(default_factory=list)
    snr_evidence: List[str] = field(default_factory=list)
    supporting_reasons: List[str] = field(default_factory=list)
    opposing_reasons: List[str] = field(default_factory=list)
    risk_assessment: Optional[RiskAssessment] = None
    ml_probability: Optional[float] = None
    calibration_status: str = "INSUFFICIENT_DATA"
    eval_sample_count: int = 0
    status: CandidateStatus = CandidateStatus.WATCHLIST
    rejection_reasons: List[str] = field(default_factory=list)
    entry_category: EntryCategory = EntryCategory.UNCLASSIFIED
    expires_at_ms: int = 0
    ai_analysis: Optional[Dict[str, Any]] = None
    execution_status: ExecutionStatus = ExecutionStatus.NOT_SUBMITTED
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    filled_quantity: float = 0.0
    filled_at_ms: Optional[int] = None
    mark_price: Optional[float] = None
    mark_price_time_utc: Optional[str] = None
    reference_price: Optional[float] = None
    reference_price_time_utc: Optional[str] = None
    reference_price_type: str = "1H_CLOSED_CANDLE"
    reference_data_age_hours: Optional[float] = None
    is_reference_data_stale: bool = False
    break_even_price: Optional[float] = None
    estimated_fee_rate: float = 0.0005
    estimated_slippage_rate: float = 0.0005
    unrealized_pnl_usdt: Optional[float] = None
    roe_pct: Optional[float] = None

    @property
    def datetime_utc(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_ms / 1000.0, tz=timezone.utc)

    @property
    def is_executable(self) -> bool:
        return self.status in (CandidateStatus.READY, CandidateStatus.VALID)

    @property
    def confidence_display(self) -> str:
        if self.ml_probability is not None:
            return f"{self.ml_probability * 100:.1f}%"
        return "N/A"

    @property
    def stop_loss_pct(self) -> float:
        """Percentage distance from entry price to stop loss."""
        if self.entry_price > 0:
            return round(abs(self.entry_price - self.stop_loss) / self.entry_price * 100.0, 2)
        return 0.0

    @property
    def take_profit_1_pct(self) -> float:
        """Percentage distance from entry price to take profit 1."""
        if self.entry_price > 0:
            return round(abs(self.take_profit_1 - self.entry_price) / self.entry_price * 100.0, 2)
        return 0.0

    @property
    def take_profit_2_pct(self) -> Optional[float]:
        """Percentage distance from entry price to take profit 2 if configured."""
        if self.take_profit_2 is not None and self.entry_price > 0:
            return round(abs(self.take_profit_2 - self.entry_price) / self.entry_price * 100.0, 2)
        return None

    @property
    def calculated_gross_rr(self) -> float:
        """Compute gross R:R from actual entry, SL, and TP1 prices.
        
        LONG: (take_profit_1 - entry_price) / (entry_price - stop_loss)
        SHORT: (entry_price - take_profit_1) / (stop_loss - entry_price)
        """
        if self.direction == Direction.LONG:
            risk = self.entry_price - self.stop_loss
            reward = self.take_profit_1 - self.entry_price
        else:
            risk = self.stop_loss - self.entry_price
            reward = self.entry_price - self.take_profit_1
        if risk > 0:
            return round(reward / risk, 3)
        return 0.0

    @property
    def calculated_break_even_fee_only(self) -> float:
        """Calculate exact fee-only break-even exit price where Net PnL == 0.
        
        Fee assumptions:
        - Taker fee rate on entry: f_open = estimated_fee_rate (default 0.0005 = 0.05%)
        - Taker fee rate on exit: f_close = estimated_fee_rate (default 0.0005 = 0.05%)
        
        LONG:
            Net PnL = Q * (P_x - P_e) - Q * P_e * f_open - Q * P_x * f_close = 0
            => P_x * (1 - f_close) = P_e * (1 + f_open)
            => P_x = P_e * (1 + f_open) / (1 - f_close)
            
        SHORT:
            Net PnL = Q * (P_e - P_x) - Q * P_e * f_open - Q * P_x * f_close = 0
            => P_x * (1 + f_close) = P_e * (1 - f_open)
            => P_x = P_e * (1 - f_open) / (1 + f_close)
        """
        f_open = self.estimated_fee_rate
        f_close = self.estimated_fee_rate
        if self.direction == Direction.LONG:
            denom = 1.0 - f_close
            return round(self.entry_price * (1.0 + f_open) / denom, 6) if denom > 0 else self.entry_price
        else:
            denom = 1.0 + f_close
            return round(self.entry_price * (1.0 - f_open) / denom, 6) if denom > 0 else self.entry_price

    @property
    def calculated_break_even_with_slippage(self) -> float:
        """Calculate break-even exit price including estimated taker fees AND execution slippage.
        
        Assumes:
        - f_open = f_close = estimated_fee_rate (0.0005 = 0.05%)
        - s_open = s_close = estimated_slippage_rate (0.0005 = 0.05%)
        
        LONG:
            P_x = P_e * [(1 + s_open) * (1 + f_open)] / [(1 - s_close) * (1 - f_close)]
            
        SHORT:
            P_x = P_e * [(1 - s_open) * (1 - f_open)] / [(1 + s_close) * (1 + f_close)]
        """
        f = self.estimated_fee_rate
        s = self.estimated_slippage_rate
        if self.direction == Direction.LONG:
            num = (1.0 + s) * (1.0 + f)
            denom = (1.0 - s) * (1.0 - f)
            return round(self.entry_price * (num / denom), 6) if denom > 0 else self.entry_price
        else:
            num = (1.0 - s) * (1.0 - f)
            denom = (1.0 + s) * (1.0 + f)
            return round(self.entry_price * (num / denom), 6) if denom > 0 else self.entry_price

    @property
    def calculated_break_even_price(self) -> float:
        """Default fee-only break-even price."""
        return self.calculated_break_even_fee_only

    def to_dict(self) -> Dict[str, Any]:
        be_fee_only = self.break_even_price if self.break_even_price is not None else self.calculated_break_even_fee_only
        be_slip = self.calculated_break_even_with_slippage
        ref_price = self.reference_price if self.reference_price is not None else self.mark_price
        ref_time = self.reference_price_time_utc if self.reference_price_time_utc is not None else self.mark_price_time_utc

        return {
            "candidate_id": self.candidate_id,
            "symbol": self.symbol,
            "timestamp_ms": self.timestamp_ms,
            "datetime_utc": self.datetime_utc.isoformat(),
            "direction": self.direction.value,
            "bias_4h": self.bias_4h.value,
            "structure_1h": self.structure_1h.value,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_1": self.take_profit_1,
            "take_profit_1_pct": self.take_profit_1_pct,
            "take_profit_2": self.take_profit_2,
            "take_profit_2_pct": self.take_profit_2_pct,
            "gross_reward_to_risk": round(self.gross_reward_to_risk, 3),
            "calculated_gross_rr": self.calculated_gross_rr,
            "net_reward_to_risk": round(self.net_reward_to_risk, 3),
            "break_even_price": round(be_fee_only, 6) if be_fee_only is not None else None,
            "break_even_price_fee_only": round(be_fee_only, 6) if be_fee_only is not None else None,
            "break_even_price_with_slippage": round(be_slip, 6) if be_slip is not None else None,
            "reference_price": round(ref_price, 6) if ref_price is not None else None,
            "reference_price_time_utc": ref_time,
            "reference_price_type": self.reference_price_type,
            "reference_data_age_hours": self.reference_data_age_hours,
            "is_reference_data_stale": self.is_reference_data_stale,
            "mark_price": round(ref_price, 6) if ref_price is not None else None,
            "mark_price_time_utc": ref_time,
            "estimated_fee_rate": self.estimated_fee_rate,
            "estimated_slippage_rate": self.estimated_slippage_rate,
            "unrealized_pnl_usdt": round(self.unrealized_pnl_usdt, 4) if self.unrealized_pnl_usdt is not None else None,
            "roe_pct": round(self.roe_pct, 2) if self.roe_pct is not None else None,
            "invalidation_price": self.invalidation_price,
            "invalidation_reason": self.invalidation_reason,
            "smc_evidence": self.smc_evidence,
            "snr_evidence": self.snr_evidence,
            "supporting_reasons": self.supporting_reasons,
            "opposing_reasons": self.opposing_reasons,
            "ml_probability": round(self.ml_probability, 4) if self.ml_probability is not None else None,
            "confidence_display": self.confidence_display,
            "is_executable": self.is_executable,
            "calibration_status": self.calibration_status,
            "eval_sample_count": self.eval_sample_count,
            "status": self.status.value,
            "entry_category": self.entry_category.value if hasattr(self.entry_category, "value") else str(self.entry_category),
            "rejection_reasons": self.rejection_reasons,
            "expires_at_ms": self.expires_at_ms,
            "expires_at_utc": datetime.fromtimestamp(self.expires_at_ms / 1000.0, tz=timezone.utc).isoformat() if self.expires_at_ms else None,
            "execution_status": self.execution_status.value if hasattr(self.execution_status, "value") else str(self.execution_status),
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "filled_quantity": self.filled_quantity,
            "filled_at_ms": self.filled_at_ms,
            "filled_at_utc": datetime.fromtimestamp(self.filled_at_ms / 1000.0, tz=timezone.utc).isoformat() if self.filled_at_ms else None,
            "risk_assessment": {
                "target_isolated_margin_usdt": round(self.risk_assessment.target_isolated_margin_usdt, 4),
                "actual_margin_usdt": round(self.risk_assessment.actual_margin_usdt, 4),
                "required_margin_usdt": round(self.risk_assessment.required_margin_usdt, 4),
                "position_leverage": round(self.risk_assessment.position_leverage, 2),
                "leverage": round(self.risk_assessment.leverage, 2),
                "equity_exposure_ratio": round(self.risk_assessment.equity_exposure_ratio, 2),
                "effective_leverage": round(self.risk_assessment.effective_leverage, 2),
                "position_notional_usdt": round(self.risk_assessment.position_notional_usdt, 4),
                "quantity": self.risk_assessment.quantity,
                "estimated_loss_usdt": round(self.risk_assessment.estimated_loss_usdt, 4),
                "estimated_loss_pct": round(self.risk_assessment.estimated_loss_pct, 2),
                "estimated_fee_usdt": round(self.risk_assessment.estimated_fee_usdt, 4),
                "estimated_slippage_usdt": round(self.risk_assessment.estimated_slippage_usdt, 4),
                "estimated_funding_usdt": round(self.risk_assessment.estimated_funding_usdt, 4),
                "risk_budget_usdt": round(self.risk_assessment.risk_budget_usdt, 4),
                "is_feasible": self.risk_assessment.is_feasible,
                "infeasible_reason": self.risk_assessment.infeasible_reason,
            } if self.risk_assessment else None,
            "ai_analysis": self.ai_analysis,
        }


@dataclass
class TradeRecord:
    """Record of a simulated or paper trade execution."""
    trade_id: str
    symbol: str
    direction: Direction
    entry_timestamp_ms: int
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    notional_usdt: float
    exit_timestamp_ms: Optional[int] = None
    exit_price: Optional[float] = None
    pnl_gross: float = 0.0
    pnl_net: float = 0.0
    fee_paid: float = 0.0
    funding_paid: float = 0.0
    exit_reason: str = ""
    is_open: bool = True
    setup_id: Optional[str] = None
    timeframe: str = "1h"
    order_status: str = "FILLED"
    leverage: float = 10.0
    margin_usdt: float = 5.0
    slippage_paid: float = 0.0
    fill_model_version: str = "v1_conservative"
    data_source: str = "asterdex_live"
    rejection_reason: str = ""
    funding_data_missing: bool = False
    details_json: str = "{}"
    entry_category: EntryCategory = EntryCategory.UNCLASSIFIED

    @property
    def outcome(self) -> str:
        """Outcome label strictly categorizing closed vs open executions.
        
        WIN: pnl_net > 0.0001
        LOSS: pnl_net < -0.0001
        BREAKEVEN: abs(pnl_net) <= 0.0001
        OPEN: position currently active
        """
        if self.is_open or self.exit_timestamp_ms is None:
            return "OPEN"
        if self.pnl_net > 0.0001:
            return "WIN"
        elif self.pnl_net < -0.0001:
            return "LOSS"
        else:
            return "BREAKEVEN"

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.exit_timestamp_ms is not None and self.entry_timestamp_ms is not None:
            return max(0.0, (self.exit_timestamp_ms - self.entry_timestamp_ms) / 1000.0)
        return None

    @property
    def duration_display(self) -> str:
        sec = self.duration_seconds
        if sec is None:
            return "Open"
        hours = sec / 3600.0
        if hours >= 1.0:
            return f"{hours:.1f}h"
        mins = sec / 60.0
        return f"{mins:.0f}m"

    @property
    def total_fees(self) -> float:
        return self.fee_paid + self.funding_paid + self.slippage_paid

    def to_dict(self) -> Dict[str, Any]:
        cat_val = self.entry_category.value if hasattr(self.entry_category, "value") else str(self.entry_category)
        dir_val = self.direction.value if hasattr(self.direction, "value") else str(self.direction)
        entry_utc = (
            datetime.fromtimestamp(self.entry_timestamp_ms / 1000.0, tz=timezone.utc).isoformat()
            if self.entry_timestamp_ms
            else None
        )
        exit_utc = (
            datetime.fromtimestamp(self.exit_timestamp_ms / 1000.0, tz=timezone.utc).isoformat()
            if self.exit_timestamp_ms
            else None
        )

        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": dir_val,
            "entry_category": cat_val,
            "entry_timestamp_ms": self.entry_timestamp_ms,
            "entry_time_utc": entry_utc,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "quantity": self.quantity,
            "notional_usdt": self.notional_usdt,
            "exit_timestamp_ms": self.exit_timestamp_ms,
            "exit_time_utc": exit_utc,
            "exit_price": self.exit_price,
            "pnl_gross": round(self.pnl_gross, 4),
            "pnl_net": round(self.pnl_net, 4),
            "fee_paid": round(self.fee_paid, 4),
            "funding_paid": round(self.funding_paid, 4),
            "slippage_paid": round(self.slippage_paid, 4),
            "total_fees": round(self.total_fees, 4),
            "exit_reason": self.exit_reason,
            "outcome": self.outcome,
            "duration_seconds": self.duration_seconds,
            "duration_display": self.duration_display,
            "is_open": self.is_open,
            "setup_id": self.setup_id,
            "timeframe": self.timeframe,
            "order_status": self.order_status,
            "leverage": self.leverage,
            "margin_usdt": self.margin_usdt,
            "details_json": self.details_json,
        }


