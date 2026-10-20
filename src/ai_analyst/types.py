"""Data structures and Pydantic models for Groq AI Trading Analyst.

Logically isolated from scanner execution, paper trading, and live order systems.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import os
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class AIAnalystTimeframe(str, Enum):
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class MarketBiasEnum(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    CHOPPY = "CHOPPY"


class RecommendationEnum(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL_STAND_ASIDE = "NEUTRAL_STAND_ASIDE"


@dataclass
class QuantitativeMarketSnapshot:
    """Pre-computed quantitative structure, volatility, and order-book metrics."""
    symbol: str
    timeframe: str
    current_price: float
    volume_24h_usdt: float
    spread_bps: float
    atr_14: float
    atr_pct: float
    swing_high: Optional[float]
    swing_low: Optional[float]
    demand_zone_lower: Optional[float]
    demand_zone_upper: Optional[float]
    supply_zone_lower: Optional[float]
    supply_zone_upper: Optional[float]
    recent_liquidity_sweep: bool
    orderbook_bid_depth_25bps: float
    orderbook_ask_depth_25bps: float
    orderbook_imbalance_25bps: float
    btc_price: float
    btc_zone_status: str
    relative_strength_vs_btc_24h: float
    data_timestamp_ms: int
    candle_close_time_ms: int
    data_age_sec: float
    is_stale: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TradeScenario:
    """Scenario details for LONG or SHORT thesis."""
    direction: str                          # "LONG" or "SHORT"
    confluence_score: float                # 0.0 to 100.0
    probability_rating: str                # "HIGH", "MODERATE", "LOW"
    key_entry_zone: str                    # e.g. "80,400 - 80,900"
    suggested_entry: float
    suggested_stop_loss: float
    stop_distance_pct: float
    take_profit_1: float
    take_profit_2: Optional[float]
    gross_reward_to_risk: float
    net_reward_to_risk: float
    key_catalysts: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    invalidation_level: float = 0.0
    invalidation_condition: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EntryLadderLevel:
    level_name: str                        # "Limit 1 (Primary)", "Limit 2 (Deep Discount)"
    price: float
    weight_pct: float                      # e.g. 60%, 40%
    allocation_usdt: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionPlanDetails:
    """Quantitative trade sizing and risk constraints (Advisory only)."""
    symbol: str
    direction: str
    primary_entry: float
    entry_ladder: List[EntryLadderLevel]
    stop_loss: float
    stop_distance_pct: float
    take_profit_1: float
    take_profit_2: Optional[float]
    break_even_price: float
    risk_budget_usdt: float
    suggested_quantity: float
    position_notional_usdt: float
    required_margin_usdt: float
    leverage: float
    estimated_maker_fee_usdt: float
    estimated_taker_fee_usdt: float
    estimated_slippage_usdt: float
    estimated_funding_usdt: float
    total_estimated_costs_usdt: float
    net_reward_to_risk: float
    is_notional_compliant: bool           # AsterDEX >= $5.00 min notional
    is_margin_compliant: bool             # <= $15.00 margin limit

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entry_ladder"] = [l.to_dict() if hasattr(l, "to_dict") else l for l in self.entry_ladder]
        return d


@dataclass
class IndonesianExplanation:
    """Institutional market analysis written in Bahasa Indonesia."""
    ringkasan_struktur: str
    analisis_skenario_long: str
    analisis_skenario_short: str
    rekomendasi_arah: str
    panduan_entry_dan_risiko: str
    kondisi_pembatalan: str
    catatan_penting_kepatuhan: str = "Peringatan: Analisis ini bersifat riset advisory murni. Tidak ada eksekusi order otomatis yang dikirim ke exchange."

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AIAnalystResult:
    """Master structured analysis result returned by Groq AI Analyst."""
    analysis_id: str
    symbol: str
    timeframe: str
    timestamp_ms: int
    datetime_utc: str
    data_age_sec: float
    is_stale: bool
    uncertainty_score: float               # 0.0 to 100.0 (higher = more volatile/conflicting)
    confidence_score: float                # 0.0 to 100.0
    primary_bias: str                      # "BULLISH", "BEARISH", "NEUTRAL"
    recommendation: str                    # "LONG", "SHORT", "NEUTRAL_STAND_ASIDE"
    executive_summary: str
    market_snapshot: QuantitativeMarketSnapshot
    long_scenario: TradeScenario
    short_scenario: TradeScenario
    recommended_plan: Optional[ExecutionPlanDetails]
    invalidation_conditions: List[str]
    risk_factors: List[str]
    penjelasan_indonesia: IndonesianExplanation
    model_id: str
    is_cached: bool = False
    raw_response: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp_ms": self.timestamp_ms,
            "datetime_utc": self.datetime_utc,
            "data_age_sec": round(self.data_age_sec, 1),
            "is_stale": self.is_stale,
            "uncertainty_score": round(self.uncertainty_score, 1),
            "confidence_score": round(self.confidence_score, 1),
            "primary_bias": self.primary_bias,
            "recommendation": self.recommendation,
            "executive_summary": self.executive_summary,
            "market_snapshot": self.market_snapshot.to_dict(),
            "long_scenario": self.long_scenario.to_dict(),
            "short_scenario": self.short_scenario.to_dict(),
            "recommended_plan": self.recommended_plan.to_dict() if self.recommended_plan else None,
            "invalidation_conditions": self.invalidation_conditions,
            "risk_factors": self.risk_factors,
            "penjelasan_indonesia": self.penjelasan_indonesia.to_dict(),
            "model_id": self.model_id,
            "is_cached": self.is_cached,
        }


@dataclass
class AIAnalystConfig:
    """Runtime configuration for Groq AI Trading Analyst."""
    groq_api_key: Optional[str] = None
    groq_model_id: str = "qwen/qwen3.8-27b"
    cache_ttl_sec: float = 60.0            # Cache deduplication window
    default_risk_budget_usdt: float = 1.00 # Standard advisory trade risk
    min_notional_usdt: float = 5.00        # AsterDEX minimum order size
    max_margin_usdt: float = 15.00         # Max allocated margin
    default_leverage: float = 10.0
    stale_threshold_sec: float = 3600.0    # 1 hour stale limit for lower TFs

    def __post_init__(self):
        if not self.groq_api_key:
            self.groq_api_key = os.getenv("GROQ_API_KEY") or os.getenv("groq")
        if not self.groq_model_id or self.groq_model_id == "qwen/qwen3.8-27b":
            self.groq_model_id = os.getenv("GROQ_MODEL") or "qwen/qwen3.8-27b"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "groq_model_id": self.groq_model_id,
            "has_api_key": bool(self.groq_api_key),
            "cache_ttl_sec": self.cache_ttl_sec,
            "default_risk_budget_usdt": self.default_risk_budget_usdt,
            "min_notional_usdt": self.min_notional_usdt,
            "max_margin_usdt": self.max_margin_usdt,
            "default_leverage": self.default_leverage,
        }


# Request Models for FastAPI
class AnalysisRequest(BaseModel):
    symbol: str = Field(..., description="Target perpetual trading pair, e.g. BTCUSDT, ETHUSDT, NEARUSDT")
    timeframe: str = Field(default="1h", description="Timeframe: 15m, 1h, 4h, 1d")
    risk_budget_usdt: Optional[float] = Field(default=None, description="Optional custom risk budget (default: $1.00)")
    force_refresh: bool = Field(default=False, description="Bypass cache and force fresh analysis")
    model_id: Optional[str] = Field(default=None, description="Optional Groq model ID override")


class AIConfigUpdateRequest(BaseModel):
    groq_api_key: Optional[str] = Field(default=None, description="Groq API key")
    groq_model_id: Optional[str] = Field(default=None, description="Groq model ID")
    default_risk_budget_usdt: Optional[float] = Field(default=None, description="Default risk budget in USDT")
    default_leverage: Optional[float] = Field(default=None, description="Default leverage (5x - 10x)")
