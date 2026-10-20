"""Types and data structures for Model B Long vs Short Research Expansion.

Defines schemas for independent shadow signal collection, variant evaluations,
paired comparison records, and simulated research execution metrics.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple


def compute_wilson_ci(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """Calculate the Wilson score confidence interval for a binomial proportion.
    
    Used for expressing statistical uncertainty on trade win rates when N is small.
    """
    if n <= 0:
        return 0.0, 0.0
    z = 1.95996  # 95% confidence
    p = float(k) / float(n)
    denom = 1.0 + (z**2) / n
    center = (p + (z**2) / (2.0 * n)) / denom
    margin = (z * math.sqrt(max(0.0, (p * (1.0 - p) / n) + ((z**2) / (4.0 * (n**2)))))) / denom
    lower = max(0.0, round((center - margin) * 100.0, 1))
    upper = min(100.0, round((center + margin) * 100.0, 1))
    return lower, upper


class ResearchVariant(str, Enum):
    """Evaluated strategy variants for Model B live-market shadow research."""
    VARIANT_A_LONG_BASELINE = "VARIANT_A_LONG_BASELINE"
    VARIANT_B_SHORT_BASELINE = "VARIANT_B_SHORT_BASELINE"
    VARIANT_C_SHORT_4H_TREND = "VARIANT_C_SHORT_4H_TREND"
    VARIANT_D_SHORT_CONFIRMED_CLOSE = "VARIANT_D_SHORT_CONFIRMED_CLOSE"
    VARIANT_E_SHORT_ORDER_FLOW = "VARIANT_E_SHORT_ORDER_FLOW"
    VARIANT_F_SHORT_COMBINED = "VARIANT_F_SHORT_COMBINED"

    @classmethod
    def _missing_(cls, value):
        aliases = {
            "VARIANT_A_BASELINE": cls.VARIANT_A_LONG_BASELINE,
            "VARIANT_B_4H_STRUCTURE": cls.VARIANT_C_SHORT_4H_TREND,
            "VARIANT_C_CONFIRMED_CLOSE": cls.VARIANT_D_SHORT_CONFIRMED_CLOSE,
            "VARIANT_D_ORDER_FLOW": cls.VARIANT_E_SHORT_ORDER_FLOW,
            "VARIANT_E_VOLATILITY_STOP": cls.VARIANT_E_SHORT_ORDER_FLOW,
            "VARIANT_F_COMBINED": cls.VARIANT_F_SHORT_COMBINED,
        }
        return aliases.get(value)


VARIANT_METADATA = {
    ResearchVariant.VARIANT_A_LONG_BASELINE.value: {
        "letter": "A",
        "name": "Current LONG Baseline",
        "side": "LONG",
        "description": "Baseline Model B execution for LONG sweep-reclaim signals",
    },
    ResearchVariant.VARIANT_B_SHORT_BASELINE.value: {
        "letter": "B",
        "name": "Current SHORT Baseline",
        "side": "SHORT",
        "description": "Current Model B unconstrained SHORT execution",
    },
    ResearchVariant.VARIANT_C_SHORT_4H_TREND.value: {
        "letter": "C",
        "name": "SHORT 4H Trend Aligned",
        "side": "SHORT",
        "description": "SHORT filtered against bullish 4H structure and EMA20",
    },
    ResearchVariant.VARIANT_D_SHORT_CONFIRMED_CLOSE.value: {
        "letter": "D",
        "name": "SHORT Confirmed Close",
        "side": "SHORT",
        "description": "SHORT requiring completed 1H bearish candle close",
    },
    ResearchVariant.VARIANT_E_SHORT_ORDER_FLOW.value: {
        "letter": "E",
        "name": "SHORT Order Flow Gate",
        "side": "SHORT",
        "description": "SHORT filtered when book depth imbalance > -5%",
    },
    ResearchVariant.VARIANT_F_SHORT_COMBINED.value: {
        "letter": "F",
        "name": "Combined Validated SHORT",
        "side": "SHORT",
        "description": "SHORT combining 4H trend, confirmed close, order flow gate, and ATR stop",
    },
}


class EvaluationDecision(str, Enum):
    """Decision produced by a variant filter."""
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"


class ShadowOrderStatus(str, Enum):
    """Lifecycle status of a simulated shadow order."""
    CREATED = "CREATED"
    PENDING = "PENDING"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    CLOSED = "CLOSED"


@dataclass
class ShadowSignalRecord:
    """Canonical timestamped signal observation shared across all variants."""
    signal_id: str
    candidate_id: str
    symbol: str
    direction: str  # "LONG" or "SHORT"
    timestamp_ms: int
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float] = None
    gross_reward_to_risk: float = 1.5
    net_reward_to_risk: float = 1.4
    active_execution_eligible: bool = True
    active_ineligible_reason: Optional[str] = None
    market_context: Dict[str, Any] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ShadowVariantEvaluation:
    """Individual variant decision and counterfactual trade outcome for a signal."""
    evaluation_id: str
    signal_id: str
    variant: ResearchVariant
    symbol: str
    direction: str
    timestamp_ms: int
    decision: EvaluationDecision
    rejection_reason: Optional[str] = None
    simulated_entry_price: Optional[float] = None
    simulated_stop_loss: Optional[float] = None
    simulated_take_profit: Optional[float] = None
    simulated_take_profit_2: Optional[float] = None
    simulated_quantity: Optional[float] = None
    simulated_notional_usdt: Optional[float] = None
    simulated_margin_usdt: Optional[float] = None
    simulated_leverage: float = 10.0
    order_status: ShadowOrderStatus = ShadowOrderStatus.CREATED
    fill_timestamp_ms: Optional[int] = None
    fill_price: Optional[float] = None
    exit_timestamp_ms: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    holding_time_min: float = 0.0
    holding_bars: int = 0
    pnl_gross: float = 0.0
    fee_paid: float = 0.0
    slippage_paid: float = 0.0
    funding_paid: float = 0.0
    pnl_net: float = 0.0
    mae_pct: float = 0.0
    mfe_pct: float = 0.0
    is_open: bool = False
    details: Dict[str, Any] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["variant"] = self.variant.value
        d["decision"] = self.decision.value
        d["order_status"] = self.order_status.value
        return d


@dataclass
class VariantPerformanceMetrics:
    """Aggregated statistical performance metrics for a specific variant and cohort."""
    variant: str
    direction: str  # "ALL", "LONG", or "SHORT"
    variant_name: str = ""
    variant_description: str = ""
    total_signals_seen: int = 0
    signals_accepted: int = 0
    signals_rejected: int = 0
    pending_orders: int = 0
    missed_fills: int = 0
    simulated_fills: int = 0
    simulated_closed: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate_pct: float = 0.0
    gross_pnl_usdt: float = 0.0
    net_pnl_usdt: float = 0.0
    profit_factor: float = 0.0
    expectancy_usdt: float = 0.0
    avg_winner_usdt: float = 0.0
    avg_loser_usdt: float = 0.0
    sl_frequency_pct: float = 0.0
    avg_holding_time_min: float = 0.0
    avg_mae_pct: float = 0.0
    avg_mfe_pct: float = 0.0
    total_fee_drag_usdt: float = 0.0
    total_slippage_drag_usdt: float = 0.0
    total_funding_drag_usdt: float = 0.0
    max_drawdown_usdt: float = 0.0
    win_rate_ci_lower_pct: float = 0.0
    win_rate_ci_upper_pct: float = 0.0
    sample_size_adequate: bool = True
    sample_size_warning: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
