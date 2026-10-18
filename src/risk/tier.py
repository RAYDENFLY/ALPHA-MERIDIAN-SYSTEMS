"""Configurable Equity-Based Isolated Margin Tier Model.

Replaces static $6.00-$6.50 default with equity-governed margin tiers:
- Sizing shrinks automatically on drawdown (anti-martingale invariant).
- Preserves 10x isolated leverage ceiling.
- Explicitly distinguishes Target Margin, Order Notional, and Stop-Loss Risk.
- Hard circuit breaker if equity drops below minimum viable threshold ($10.00 USDT).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field


class EquityTier(BaseModel):
    """Defines an isolated margin target range for a given equity band."""
    tier_id: str
    name: str
    min_equity_usdt: float = Field(ge=0.0)
    max_equity_usdt: float = Field(gt=0.0)
    target_margin_min_usdt: float = Field(gt=0.0)
    target_margin_max_usdt: float = Field(gt=0.0)
    description: str


class EquityTierConfig(BaseModel):
    """Configuration for equity-based isolated margin tier model."""
    enabled: bool = True
    min_trading_equity_usdt: float = Field(
        default=10.0,
        gt=0.0,
        description="Minimum account equity required to open new positions. Below this, sizing halts."
    )
    fallback_target_margin_min: float = Field(default=1.00, gt=0.0)
    fallback_target_margin_max: float = Field(default=1.50, gt=0.0)
    tiers: List[EquityTier] = Field(default_factory=lambda: [
        EquityTier(
            tier_id="TIER_1_MICRO",
            name="Tier 1 Micro-Margin Policy",
            min_equity_usdt=10.0,
            max_equity_usdt=25.0,
            target_margin_min_usdt=1.00,
            target_margin_max_usdt=1.50,
            description="Tier 1 Micro-Margin Policy ($1.00-$1.50 isolated margin, 10x max leverage, $0.50 daily loss cap).",
        ),
        EquityTier(
            tier_id="TIER_2_STANDARD",
            name="Tier 2 Standard Tier",
            min_equity_usdt=25.0,
            max_equity_usdt=50.0,
            target_margin_min_usdt=2.50,
            target_margin_max_usdt=3.50,
            description="Standard sizing for equity between $25 and $50. Target margin $2.50-$3.50.",
        ),
        EquityTier(
            tier_id="TIER_3_GROWTH",
            name="Tier 3 Growth Tier",
            min_equity_usdt=50.0,
            max_equity_usdt=100.0,
            target_margin_min_usdt=5.00,
            target_margin_max_usdt=6.50,
            description="Growth sizing for equity between $50 and $100. Target margin $5.00-$6.50.",
        ),
        EquityTier(
            tier_id="TIER_4_SCALE",
            name="Tier 4 Scaling Tier",
            min_equity_usdt=100.0,
            max_equity_usdt=1000000.0,
            target_margin_min_usdt=8.00,
            target_margin_max_usdt=10.00,
            description="Scaling sizing for equity above $100. Target margin $8.00-$10.00.",
        ),
    ])


@dataclass(frozen=True)
class TierResolutionResult:
    """Resolved sizing parameters for a verified equity value."""
    tier_id: str
    tier_name: str
    target_margin_min: float
    target_margin_max: float
    is_trading_allowed: bool
    rejection_reason: Optional[str] = None
    notional_min_10x: float = 0.0
    notional_max_10x: float = 0.0


def resolve_equity_tier(
    equity_usdt: float,
    config: Optional[EquityTierConfig] = None,
) -> TierResolutionResult:
    """Resolve target isolated margin bounds from verified account equity.

    Invariants:
    1. If equity < min_trading_equity_usdt: trading is blocked.
    2. Monotonicity: Higher equity >= lower equity margin bounds.
    3. Anti-martingale: Target margin shrinks when equity falls across tiers.
    """
    cfg = config or EquityTierConfig()

    if not cfg.enabled:
        return TierResolutionResult(
            tier_id="TIER_STATIC_DISABLED",
            tier_name="Static Margin Mode (Tiers Disabled)",
            target_margin_min=cfg.fallback_target_margin_min,
            target_margin_max=cfg.fallback_target_margin_max,
            is_trading_allowed=True,
            notional_min_10x=cfg.fallback_target_margin_min * 10.0,
            notional_max_10x=cfg.fallback_target_margin_max * 10.0,
        )

    # Check minimum trading equity floor
    if equity_usdt < cfg.min_trading_equity_usdt:
        return TierResolutionResult(
            tier_id="TIER_HALTED_LOW_EQUITY",
            tier_name="Capital Preservation Halt",
            target_margin_min=0.0,
            target_margin_max=0.0,
            is_trading_allowed=False,
            rejection_reason=(
                f"Account equity (${equity_usdt:.2f}) is below minimum trading threshold "
                f"(${cfg.min_trading_equity_usdt:.2f}). New entries halted to prevent account depletion."
            ),
            notional_min_10x=0.0,
            notional_max_10x=0.0,
        )

    # Match equity against sorted tiers
    for tier in cfg.tiers:
        if tier.min_equity_usdt <= equity_usdt < tier.max_equity_usdt:
            return TierResolutionResult(
                tier_id=tier.tier_id,
                tier_name=tier.name,
                target_margin_min=tier.target_margin_min_usdt,
                target_margin_max=tier.target_margin_max_usdt,
                is_trading_allowed=True,
                notional_min_10x=tier.target_margin_min_usdt * 10.0,
                notional_max_10x=tier.target_margin_max_usdt * 10.0,
            )

    # If equity exceeds all configured upper bounds, use the highest tier
    if cfg.tiers:
        highest = cfg.tiers[-1]
        return TierResolutionResult(
            tier_id=highest.tier_id,
            tier_name=highest.name,
            target_margin_min=highest.target_margin_min_usdt,
            target_margin_max=highest.target_margin_max_usdt,
            is_trading_allowed=True,
            notional_min_10x=highest.target_margin_min_usdt * 10.0,
            notional_max_10x=highest.target_margin_max_usdt * 10.0,
        )

    # Fallback to configured default
    return TierResolutionResult(
        tier_id="TIER_DEFAULT_FALLBACK",
        tier_name="Default Fallback Tier",
        target_margin_min=cfg.fallback_target_margin_min,
        target_margin_max=cfg.fallback_target_margin_max,
        is_trading_allowed=True,
        notional_min_10x=cfg.fallback_target_margin_min * 10.0,
        notional_max_10x=cfg.fallback_target_margin_max * 10.0,
    )
