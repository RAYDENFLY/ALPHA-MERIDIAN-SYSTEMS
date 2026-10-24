"""Model C — Data Types and Research Schema Specifications.

Phase C0: Pump.fun Research Scope & Data Feasibility.
Strictly non-executable research schema. No trading logic, order execution, or wallet operations.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ModelCLifecycleState(str, Enum):
    """Verified lifecycle states of a Pump.fun token."""
    DISCOVERED = "DISCOVERED"          # Token creation event detected; bonding curve initialized
    BONDING_CURVE = "BONDING_CURVE"    # Active trading on the Pump.fun bonding curve PDA (0% < progress < 100%)
    MIGRATING = "MIGRATING"            # Bonding curve reached 100% (complete=True); migration tx pending
    MIGRATED = "MIGRATED"              # Liquidity seeded to Raydium AMM / PumpSwap; LP tokens burned
    ACTIVE = "ACTIVE"                  # Post-migration or sustained bonding curve with regular trade volume
    ILLIQUID = "ILLIQUID"              # Trading velocity stalled (<0.1 SOL/hr) or real reserves severely drained
    FAILED = "FAILED"                  # Developer dumped 100%, bonding curve abandoned >24h, or LP drained
    UNKNOWN = "UNKNOWN"                # Data feed disconnect, missing RPC slot, or unclassifiable on-chain state


class DataFeasibilityStatus(str, Enum):
    """Classification for data source and research feasibility."""
    FEASIBLE = "FEASIBLE"
    FEASIBLE_WITH_LIMITATIONS = "FEASIBLE_WITH_LIMITATIONS"
    NOT_FEASIBLE = "NOT_FEASIBLE"


class DataSourceType(str, Enum):
    """Categorized data sources available for Pump.fun research."""
    SOLANA_PUBLIC_RPC = "SOLANA_PUBLIC_RPC"
    DEDICATED_FREE_RPC = "DEDICATED_FREE_RPC"
    DEXSCREENER_PUBLIC = "DEXSCREENER_PUBLIC"
    GECKOTERMINAL_PUBLIC = "GECKOTERMINAL_PUBLIC"
    PUMPFUN_INTERNAL = "PUMPFUN_INTERNAL"
    BIRDEYE_FREE = "BIRDEYE_FREE"
    RAYDIUM_PUBLIC = "RAYDIUM_PUBLIC"


@dataclass(frozen=True)
class PumpFunProgramConstants:
    """Canonical constants for the Pump.fun smart contract on Solana Mainnet."""
    PROGRAM_ID: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    BONDING_CURVE_SEED: str = "bonding-curve"
    TOTAL_SUPPLY: int = 1_000_000_000_000_000  # 1B tokens with 6 decimals (10^15 raw units)
    BONDING_CURVE_ALLOCATION: int = 800_000_000_000_000  # 800M tokens sold on curve (80%)
    MIGRATION_DEX_RESERVE: int = 200_000_000_000_000     # 200M tokens seeded to Raydium (20%)
    INITIAL_VIRTUAL_SOL_RESERVES: int = 30_000_000_000   # ~30 SOL in lamports
    INITIAL_VIRTUAL_TOKEN_RESERVES: int = 1_073_000_000_000_000
    GRADUATION_REAL_SOL_TARGET: int = 85_000_000_000     # ~85 SOL in lamports (~$12,750 @ $150/SOL)
    DECIMALS: int = 6
    FEE_BASIS_POINTS: int = 100                          # 1.0% protocol transaction fee


@dataclass
class PumpFunTokenMetadata:
    """Static metadata captured at token creation."""
    mint: str
    bonding_curve_pda: str
    creator_address: str
    name: str
    symbol: str
    uri: str
    creation_timestamp_ms: int
    creation_slot: int
    creation_tx_signature: str
    initial_buy_sol: float = 0.0
    initial_buy_tokens: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mint": self.mint,
            "bonding_curve_pda": self.bonding_curve_pda,
            "creator_address": self.creator_address,
            "name": self.name,
            "symbol": self.symbol,
            "uri": self.uri,
            "creation_timestamp_ms": self.creation_timestamp_ms,
            "creation_slot": self.creation_slot,
            "creation_tx_signature": self.creation_tx_signature,
            "initial_buy_sol": self.initial_buy_sol,
            "initial_buy_tokens": self.initial_buy_tokens,
        }


@dataclass
class BondingCurveState:
    """Deterministic state of a Pump.fun bonding curve account."""
    mint: str
    observed_timestamp_ms: int
    slot: int
    virtual_sol_reserves_lamports: int
    virtual_token_reserves_units: int
    real_sol_reserves_lamports: int
    real_token_reserves_units: int
    complete: bool
    progress_pct: float
    marginal_price_sol: float
    market_cap_sol: float

    @classmethod
    def from_reserves(
        cls,
        mint: str,
        observed_timestamp_ms: int,
        slot: int,
        virtual_sol: int,
        virtual_token: int,
        real_sol: int,
        real_token: int,
        complete: bool,
    ) -> "BondingCurveState":
        constants = PumpFunProgramConstants()
        # Progress is the proportion of real SOL accumulated towards the ~85 SOL graduation cap
        progress = min(100.0, max(0.0, (real_sol / constants.GRADUATION_REAL_SOL_TARGET) * 100.0))
        # Marginal price in SOL per token unit (accounting for 10^6 token decimals and 10^9 SOL lamports)
        if virtual_token > 0:
            price_sol = (virtual_sol / 1e9) / (virtual_token / 1e6)
        else:
            price_sol = 0.0
        mcap_sol = price_sol * 1_000_000_000.0

        return cls(
            mint=mint,
            observed_timestamp_ms=observed_timestamp_ms,
            slot=slot,
            virtual_sol_reserves_lamports=virtual_sol,
            virtual_token_reserves_units=virtual_token,
            real_sol_reserves_lamports=real_sol,
            real_token_reserves_units=real_token,
            complete=complete,
            progress_pct=round(progress, 2),
            marginal_price_sol=price_sol,
            market_cap_sol=round(mcap_sol, 4),
        )


@dataclass
class HolderDistribution:
    """Concentration and ownership dispersion at a specific observation epoch."""
    mint: str
    observed_timestamp_ms: int
    total_holders_count: int
    top_10_share_pct: float
    top_20_share_pct: float
    creator_balance_pct: float
    bonding_curve_balance_pct: float
    gini_coefficient: Optional[float] = None
    is_clustered_suspect: bool = False


@dataclass
class TokenObservationSnapshot:
    """Discrete time-series observation for empirical survival/momentum analysis."""
    mint: str
    observation_epoch_ms: int
    elapsed_seconds_from_creation: int
    lifecycle_state: ModelCLifecycleState
    price_sol: float
    market_cap_usd: Optional[float]
    real_liquidity_sol: float
    bonding_curve_progress_pct: float
    cumulative_buy_count: int
    cumulative_sell_count: int
    cumulative_buy_volume_sol: float
    cumulative_sell_volume_sol: float
    unique_buyers_count: int
    unique_sellers_count: int
    holder_top10_pct: Optional[float]
    creator_balance_pct: Optional[float]


@dataclass
class ResearchLabelSpec:
    """Specification of an objective empirical research label without look-ahead bias."""
    label_id: str
    description: str
    observation_window_seconds: int     # e.g., features computed strictly during first 300s (5m)
    prediction_horizon_seconds: int     # e.g., outcome evaluated at 3600s (1h)
    label_type: str                     # "BINARY", "CATEGORICAL", "CONTINUOUS"
    evaluation_formula: str
    leakage_prevention_rule: str


@dataclass
class DataSourceAuditRecord:
    """Audit report for an evaluated public data source."""
    source_name: str
    source_type: DataSourceType
    official_doc_url: str
    available_data_fields: List[str]
    historical_depth: str
    update_frequency: str
    auth_requirement: str
    free_tier_limits: str
    rate_limit_policy: str
    data_quality_limitations: List[str]
    research_suitability: DataFeasibilityStatus
    recommendation: str


@dataclass
class DataFeasibilityDomainAssessment:
    """Feasibility outcome for each of the 6 core research domains."""
    domain_name: str
    status: DataFeasibilityStatus
    primary_source: str
    evidence_and_rationale: str
    mitigation_or_requirement: str
