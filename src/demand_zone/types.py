"""Types and data structures for AsterDEX Demand-Zone LONG Scanner.

Defines schemas for:
- Demand zones and structural boundaries
- BTC market context and policy modes
- Position sizing and risk budgets
- Take-profit planning and feasibility
- Order-book depth and spoofing risk scoring
- Deterministic candidate statuses and explanation models
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple


class DemandZoneCandidateStatus(str, Enum):
    """Deterministic lifecycle and eligibility statuses for demand zone setups."""
    READY = "READY"
    APPROACHING = "APPROACHING"
    WATCHLIST = "WATCHLIST"
    INFEASIBLE = "INFEASIBLE"
    INVALIDATED = "INVALIDATED"
    STALE = "STALE"
    ERROR = "ERROR"


class BTCZonePolicy(str, Enum):
    """Configurable operating policies for BTC market context filter."""
    OBSERVE_CONTINUOUSLY = "OBSERVE_CONTINUOUSLY"       # Policy A: Continuous altcoin observation
    PRIORITIZE_ON_APPROACH = "PRIORITIZE_ON_APPROACH"   # Policy B: Boost setup priority near BTC zone
    REQUIRE_BTC_CONFIRMATION = "REQUIRE_BTC_CONFIRMATION" # Policy C: Require BTC zone test before READY


class BTCZoneStatus(str, Enum):
    """Position of current BTC price relative to the configured demand reference zone."""
    ABOVE_ZONE = "ABOVE_ZONE"
    INSIDE_ZONE = "INSIDE_ZONE"
    BELOW_ZONE = "BELOW_ZONE"


@dataclass
class DemandZoneConfig:
    """Configurable parameters for AsterDEX Demand-Zone LONG Scanner."""
    # 24H Volume Filter
    min_24h_volume_usdt: float = 1_000_000.0  # Hard minimum ($1M to $5M configurable)
    preferred_volume_usdt: float = 5_000_000.0 # Preferred liquidity threshold
    max_spread_bps: float = 15.0              # Maximum allowable bid-ask spread

    # Demand-Zone Proximity
    min_distance_to_demand_pct: float = 1.5   # Preferred approach lower bound
    max_distance_to_demand_pct: float = 3.5   # Preferred approach upper bound
    zone_reference_rule: str = "ZONE_UPPER"   # "ZONE_UPPER", "ZONE_MID", or "SWING_LOW"

    # Risk & Sizing Constraints
    risk_budget_usdt: float = 0.30            # Default planned loss per trade ($0.25 - $0.35)
    min_stop_loss_dist_pct: float = 2.5       # Preferred min stop distance
    max_stop_loss_dist_pct: float = 4.0       # Preferred max stop distance
    hard_max_stop_loss_pct: float = 5.0       # Hard ceiling beyond which setup is INFEASIBLE
    leverage: float = 10.0                    # 5x - 10x for margin estimation only
    min_notional_usdt: float = 5.0            # Exchange minimum order notional
    max_margin_usdt: float = 15.0             # Operator margin allocation limit

    # Take-Profit & Target Constraints
    min_net_reward_to_risk: float = 1.5       # Minimum net R:R threshold
    min_resistance_clearance_rr: float = 1.2  # Minimum distance to nearest opposing resistance

    # BTC Context Reference
    btc_zone_lower: float = 79864.0           # Operator configured BTC reference zone lower
    btc_zone_upper: float = 80400.0           # Operator configured BTC reference zone upper
    btc_policy: BTCZonePolicy = BTCZonePolicy.PRIORITIZE_ON_APPROACH

    # Cost Estimation Model
    maker_fee_rate: float = 0.0002            # 0.02% maker entry
    taker_fee_rate: float = 0.0005            # 0.05% taker exit
    slippage_rate: float = 0.0005             # 0.05% expected adverse slippage on stop
    funding_rate_est: float = 0.0001          # 0.01% estimated funding drag

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["btc_policy"] = self.btc_policy.value
        return d


@dataclass
class DemandZone:
    """Technically validated support/demand zone."""
    zone_id: str
    symbol: str
    lower_price: float
    upper_price: float
    mid_price: float
    formation_timestamp_ms: int
    swing_low_price: float
    reaction_count: int = 1
    displacement_score: float = 1.0           # Ratio of displacement candle ATR
    is_fresh: bool = True
    test_count: int = 0
    quality_score: float = 75.0               # 0.0 - 100.0
    is_provisional: bool = False              # False if derived strictly from closed candles
    liquidity_swept: bool = False             # True if prior swing low was swept & reclaimed
    invalidation_level: float = 0.0           # Structural price level that invalidates zone

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BTCContextObservation:
    """Current BTC market context and policy alignment observation."""
    current_price: float
    zone_lower: float
    zone_upper: float
    zone_status: BTCZoneStatus
    distance_to_upper_pct: float
    distance_to_lower_pct: float
    is_approaching: bool
    sweep_reclaim_detected: bool
    bearish_impulse_detected: bool
    volatility_alert: bool
    active_policy: BTCZonePolicy
    allows_ready_status: bool
    priority_boost: float = 0.0
    status_explanation: str = ""
    timestamp_ms: int = 0
    observed_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["zone_status"] = self.zone_status.value
        d["active_policy"] = self.active_policy.value
        return d


@dataclass
class RiskSizingResult:
    """Position sizing, margin requirements, and cost estimates for $0.30 risk budget."""
    symbol: str
    entry_price: float
    stop_loss_price: float
    stop_distance_usdt: float
    stop_distance_pct: float
    risk_budget_usdt: float
    quantity: float
    position_notional_usdt: float
    required_margin_usdt: float
    estimated_leverage: float
    estimated_entry_fee_usdt: float
    estimated_exit_fee_usdt: float
    estimated_slippage_usdt: float
    estimated_funding_usdt: float
    total_estimated_loss_usdt: float
    liquidation_price_est: float
    liquidation_distance_pct: float
    is_feasible: bool
    infeasible_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TargetPlanningResult:
    """Take-profit levels, reward-to-risk calculations, and structural feasibility."""
    take_profit_1: float
    take_profit_2: Optional[float]
    gross_reward_to_risk: float
    net_reward_to_risk: float
    break_even_price: float
    estimated_gross_pnl_tp1_usdt: float
    estimated_net_pnl_tp1_usdt: float
    estimated_net_pnl_tp2_usdt: Optional[float]
    nearest_resistance_price: Optional[float]
    resistance_distance_rr: float
    is_obstructed_by_resistance: bool
    tp1_rationale: str
    tp2_rationale: Optional[str] = None
    is_feasible: bool = True
    infeasible_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OrderBookLiquidityAssessment:
    """L2 order-book depth, spread, resting walls, and spoofing risk scoring."""
    symbol: str
    best_bid: float
    best_ask: float
    spread_bps: float
    bid_depth_25bps_usdt: float
    ask_depth_25bps_usdt: float
    bid_depth_50bps_usdt: float
    ask_depth_50bps_usdt: float
    depth_imbalance_25bps: float              # (bids - asks) / (bids + asks)
    order_size_depth_ratio: float             # bid_depth_25bps / intended_notional
    supporting_bid_walls: List[Dict[str, Any]] = field(default_factory=list)
    wall_persistence_status: str = "NEUTRAL"  # "CONFIRMED_PERSISTENT", "UNSTABLE", "NEUTRAL"
    spoofing_warning: bool = False
    spoofing_warning_reason: Optional[str] = None
    liquidity_quality_score: float = 70.0     # 0.0 - 100.0
    assessment_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DemandZoneCandidate:
    """Full candidate setup record generated by AsterDEX Demand-Zone LONG Scanner."""
    candidate_id: str
    symbol: str
    timestamp_ms: int
    datetime_utc: str
    status: DemandZoneCandidateStatus

    # Prices & Distance
    current_price: float
    demand_zone_upper: float
    demand_zone_lower: float
    demand_zone_reference: float
    distance_to_demand_pct: float

    # Trade Levels
    proposed_entry_price: float
    entry_rationale: str
    proposed_stop_loss_price: float
    stop_loss_distance_pct: float
    structural_invalidation_level: float
    take_profit_1: float
    take_profit_2: Optional[float]

    # Risk & Sizing
    risk_budget_usdt: float
    position_quantity: float
    position_notional_usdt: float
    required_margin_usdt: float
    estimated_leverage: float
    total_estimated_loss_usdt: float

    # Rewards & Ratios
    gross_reward_to_risk: float
    net_reward_to_risk: float
    break_even_price: float
    estimated_net_pnl_tp1_usdt: float

    # Liquidity & 24H Turnover
    volume_24h_usdt: float
    bid_ask_spread_bps: float
    liquidity_quality_score: float
    demand_zone_score: float
    composite_setup_score: float

    # BTC Context
    btc_current_price: float
    btc_zone_status: str
    btc_policy_used: str

    # Explanations & Evidence
    evidence_points: List[str] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)
    machine_reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d
