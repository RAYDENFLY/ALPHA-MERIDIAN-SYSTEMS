"""Model Registry for Multi-Model Architecture Isolation.

Defines model identities, versions, lifecycle statuses, and architectural boundaries.
Ensures strict separation between Model A (Defensive SMC/SnR, frozen) and
Model B (Aggressive Liquidity Research Engine, active research).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class ModelID(str, Enum):
    """Authoritative identifiers for trading research models."""
    MODEL_A = "MODEL_A"
    MODEL_B = "MODEL_B"
    MODEL_C = "MODEL_C"

    @classmethod
    def from_str(cls, val: Union[str, "ModelID"]) -> "ModelID":
        if isinstance(val, cls):
            return val
        clean = str(val).strip().upper().replace("-", "_").replace(" ", "_")
        for m in cls:
            if m.value == clean or m.name == clean:
                return m
        # Fallback default
        return cls.MODEL_A


class ModelStatus(str, Enum):
    """Operational and governance status of a model."""
    FROZEN_VALIDATION = "FROZEN_VALIDATION"  # Model A: immutable strategy, active forward cohort
    RESEARCH_PAPER = "RESEARCH_PAPER"        # Model B: aggressive paper testing and liquidity research
    DESIGN_SPEC = "DESIGN_SPEC"              # Model C: architecture specification & paper simulator design
    DISABLED = "DISABLED"


@dataclass
class ModelMetadata:
    """Detailed architectural specifications and telemetry for a registered model."""
    model_id: ModelID
    name: str
    version: str
    status: ModelStatus
    description: str
    universe_type: str  # "WHITELIST_11" or "UNIVERSAL_PERPETUAL"
    timeframe: str      # e.g. "1h"
    execution_mode: str # "PAPER"
    initial_equity_usdt: float
    target_trade_risk_usdt: float
    daily_loss_limit_usdt: float
    weekly_loss_limit_usdt: float
    max_open_positions: int
    max_leverage: int
    features: List[str] = field(default_factory=list)
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id.value,
            "name": self.name,
            "version": self.version,
            "status": self.status.value,
            "description": self.description,
            "universe_type": self.universe_type,
            "timeframe": self.timeframe,
            "execution_mode": self.execution_mode,
            "initial_equity_usdt": self.initial_equity_usdt,
            "target_trade_risk_usdt": self.target_trade_risk_usdt,
            "daily_loss_limit_usdt": self.daily_loss_limit_usdt,
            "weekly_loss_limit_usdt": self.weekly_loss_limit_usdt,
            "max_open_positions": self.max_open_positions,
            "max_leverage": self.max_leverage,
            "features": list(self.features),
            "created_at_utc": self.created_at_utc,
        }


class ModelRegistry:
    """Central registry maintaining isolation and metadata across models."""

    _models: Dict[ModelID, ModelMetadata] = {}

    @classmethod
    def register(cls, meta: ModelMetadata) -> None:
        """Register or update a model specification."""
        cls._models[meta.model_id] = meta

    @classmethod
    def get(cls, model_id: Union[str, ModelID]) -> Optional[ModelMetadata]:
        """Retrieve model metadata by ID."""
        mid = ModelID.from_str(model_id)
        return cls._models.get(mid)

    @classmethod
    def list_all(cls) -> List[ModelMetadata]:
        """Return all registered model specifications."""
        return list(cls._models.values())

    @classmethod
    def is_valid_model(cls, model_id: Union[str, ModelID]) -> bool:
        """Check whether a model ID is recognized."""
        mid = ModelID.from_str(model_id)
        return mid in cls._models


# ---------------------------------------------------------------------------
# Default System Registrations
# ---------------------------------------------------------------------------

ModelRegistry.register(
    ModelMetadata(
        model_id=ModelID.MODEL_A,
        name="Model A — Defensive SMC/SnR",
        version="1.0.0-frozen",
        status=ModelStatus.FROZEN_VALIDATION,
        description="Defensive Smart Money Concepts & Support/Resistance strategy on 11-symbol whitelist with LightGBM scoring and Tier 1 micro-margin risk controls.",
        universe_type="WHITELIST_11",
        timeframe="1h",
        execution_mode="PAPER",
        initial_equity_usdt=16.0,
        target_trade_risk_usdt=0.25,
        daily_loss_limit_usdt=0.50,
        weekly_loss_limit_usdt=1.25,
        max_open_positions=1,
        max_leverage=10,
        features=[
            "4H Market Bias Structure",
            "1H SMC/SnR Multi-touch Zones",
            "LightGBM Probability Gating",
            "Tier 1 Micro-Margin Sizing ($1.00-$1.50 target margin)",
            "Dynamic Structural Invalidation & Expiration",
        ],
    )
)

ModelRegistry.register(
    ModelMetadata(
        model_id=ModelID.MODEL_B,
        name="Model B — Aggressive Liquidity & Catalyst",
        version="1.0.0-alpha",
        status=ModelStatus.RESEARCH_PAPER,
        description="Aggressive multi-exchange perpetual market discovery with three independent opportunity engines: Liquidity Wall Hunter, New Market Opportunity Scanner, and News & Catalyst Engine.",
        universe_type="UNIVERSAL_PERPETUAL",
        timeframe="1h",
        execution_mode="PAPER",
        initial_equity_usdt=100.0,
        target_trade_risk_usdt=1.00,
        daily_loss_limit_usdt=3.00,
        weekly_loss_limit_usdt=7.50,
        max_open_positions=3,
        max_leverage=10,
        features=[
            "Universal Multi-Venue Perpetual Contract Discovery (AsterDEX, Binance, Hyperliquid)",
            "Engine 1: Liquidity Wall Hunter (L2 depth persistence, cancellation/withdrawal tracking, sweep/reclaim)",
            "Engine 2: New Market Opportunity Scanner (listing age, spread, liquidity feasibility vs risk budget)",
            "Engine 3: News & Catalyst Engine (structured verification, source URL, publication vs ingestion time, anti-hallucination)",
            "Cadence Protocol: 1H Candle Context + Sub-Hourly Live Order Book Reactivity",
            "Cumulative Volume Delta (CVD) & Aggressor Flow Absorption",
            "Independent Model Ledger & Dedicated $100.00 Paper Portfolio",
        ],
    )
)

ModelRegistry.register(
    ModelMetadata(
        model_id=ModelID.MODEL_C,
        name="Model C — Pump.fun Rapid-Execution Research",
        version="0.1.0-spec",
        status=ModelStatus.DESIGN_SPEC,
        description="Solana-specific rapid token discovery, bonding-curve lifecycle tracking, rug-pull/security screening, and execution-void paper simulation.",
        universe_type="SOLANA_BONDING_CURVES",
        timeframe="sub_minute",
        execution_mode="PAPER",
        initial_equity_usdt=50.0,
        target_trade_risk_usdt=0.50,
        daily_loss_limit_usdt=2.00,
        weekly_loss_limit_usdt=5.00,
        max_open_positions=2,
        max_leverage=1,
        features=[
            "Event-Driven Pump.fun Token Discovery & Metadata Ingestion",
            "Bonding Curve vs Raydium AMM Transition State Machine",
            "On-Chain Security Screening (Mint/Freeze Auth, Dev Wallet, Top-10 Concentration)",
            "Simulated Jito MEV Priority Fees & Cluster Latency Modeling",
            "Execution-Void & High Slippage Simulation (>10-25%)",
            "Time-Based Emergency Exit & Stop-Loss Void Awareness",
            "Zero-Leverage Spot Only Paper Ledger Isolation",
        ],
    )
)
