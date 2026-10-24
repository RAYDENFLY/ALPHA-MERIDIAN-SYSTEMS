"""Model C — Data Types and Research Schema Specifications.

Phase C0 & C1: Pump.fun Research Scope, Data Feasibility & Prospective Collection Pipeline.
Strictly non-executable research schema. No trading logic, order execution, or wallet operations.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 string format with millisecond precision."""
    return datetime.now(timezone.utc).isoformat()


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


class PumpFunEventType(str, Enum):
    """Parsed Pump.fun event classifications."""
    CREATE = "CREATE"
    TRADE = "TRADE"
    COMPLETE = "COMPLETE"
    SET_PARAMS = "SET_PARAMS"
    UNKNOWN = "UNKNOWN"
    MALFORMED = "MALFORMED"


class EventParseStatus(str, Enum):
    """Parsing fidelity status for ingested on-chain events."""
    PARSED = "PARSED"                      # Full event binary decoded successfully
    PARTIALLY_PARSED = "PARTIALLY_PARSED"  # Instruction detected in log text, but binary payload absent
    UNSUPPORTED = "UNSUPPORTED"            # Unrecognized discriminator or unknown program instruction
    MALFORMED = "MALFORMED"                # Payload corrupted, truncated, or failed Borsh decoding


class TokenQualityStatus(str, Enum):
    """Data quality classification for normalized token records."""
    VALID = "VALID"
    PARTIAL = "PARTIAL"
    CORRUPTED = "CORRUPTED"
    UNCONFIRMED = "UNCONFIRMED"


class CollectorHealthStatus(str, Enum):
    """Operational health status of the prospective event collector."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"


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
class RawEventRecord:
    """Raw, immutable on-chain event as received from Solana RPC/WebSocket."""
    schema_version: str = "1.0"
    event_id: str = ""
    signature: str = ""
    slot: int = 0
    block_time: Optional[int] = None       # On-chain block Unix timestamp (seconds)
    received_at: str = field(default_factory=utc_now_iso)  # Local ingestion time
    program_id: str = PumpFunProgramConstants.PROGRAM_ID
    event_type: str = PumpFunEventType.UNKNOWN.value
    raw_payload: str = ""                  # Full raw log / message payload
    parse_status: str = EventParseStatus.PARSED.value
    parser_version: str = "1.0"

    def compute_event_id(self) -> str:
        """Deterministic event ID based on signature, slot, event_type, and raw payload hash."""
        h = hashlib.sha256(f"{self.signature}:{self.slot}:{self.event_type}:{self.raw_payload}".encode()).hexdigest()
        self.event_id = f"evt_{h[:24]}"
        return self.event_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id or self.compute_event_id(),
            "signature": self.signature,
            "slot": self.slot,
            "block_time": self.block_time,
            "received_at": self.received_at,
            "program_id": self.program_id,
            "event_type": self.event_type,
            "raw_payload": self.raw_payload,
            "parse_status": self.parse_status,
            "parser_version": self.parser_version,
        }


@dataclass
class NormalizedTokenRecord:
    """Normalized prospective record for a Pump.fun token discovered on-chain."""
    schema_version: str = "1.0"
    mint_address: str = ""
    creation_signature: str = ""
    creation_slot: int = 0
    creation_timestamp: Optional[int] = None  # On-chain block time (seconds)
    creator_address: str = ""
    bonding_curve_pda: Optional[str] = None
    name: Optional[str] = None
    symbol: Optional[str] = None
    metadata_uri: Optional[str] = None
    first_seen_at: str = field(default_factory=utc_now_iso)
    last_seen_at: str = field(default_factory=utc_now_iso)
    lifecycle_state: str = ModelCLifecycleState.DISCOVERED.value
    migration_status: bool = False
    migration_timestamp: Optional[int] = None
    migration_pool_address: Optional[str] = None
    migration_destination: Optional[str] = None  # "RAYDIUM", "PUMPSWAP", "UNKNOWN"
    data_quality_status: str = TokenQualityStatus.VALID.value
    tracked_for_sampling: bool = True
    sampling_reason: str = "UNIVERSAL_DISCOVERY"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mint_address": self.mint_address,
            "creation_signature": self.creation_signature,
            "creation_slot": self.creation_slot,
            "creation_timestamp": self.creation_timestamp,
            "creator_address": self.creator_address,
            "bonding_curve_pda": self.bonding_curve_pda,
            "name": self.name,
            "symbol": self.symbol,
            "metadata_uri": self.metadata_uri,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "lifecycle_state": self.lifecycle_state,
            "migration_status": int(self.migration_status),
            "migration_timestamp": self.migration_timestamp,
            "migration_pool_address": self.migration_pool_address,
            "migration_destination": self.migration_destination,
            "data_quality_status": self.data_quality_status,
            "tracked_for_sampling": int(self.tracked_for_sampling),
            "sampling_reason": self.sampling_reason,
        }


@dataclass
class NormalizedTradeRecord:
    """Normalized on-chain trade event executed on a Pump.fun bonding curve."""
    schema_version: str = "1.0"
    event_id: str = ""
    signature: str = ""
    slot: int = 0
    block_time: Optional[int] = None
    mint_address: str = ""
    trader_address: str = ""
    is_buy: bool = True
    sol_amount_lamports: int = 0
    token_amount_units: int = 0
    virtual_sol_reserves_lamports: int = 0
    virtual_token_reserves_units: int = 0
    real_sol_reserves_lamports: int = 0
    real_token_reserves_units: int = 0
    received_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "signature": self.signature,
            "slot": self.slot,
            "block_time": self.block_time,
            "mint_address": self.mint_address,
            "trader_address": self.trader_address,
            "is_buy": int(self.is_buy),
            "sol_amount_lamports": self.sol_amount_lamports,
            "token_amount_units": self.token_amount_units,
            "virtual_sol_reserves_lamports": self.virtual_sol_reserves_lamports,
            "virtual_token_reserves_units": self.virtual_token_reserves_units,
            "real_sol_reserves_lamports": self.real_sol_reserves_lamports,
            "real_token_reserves_units": self.real_token_reserves_units,
            "received_at": self.received_at,
        }


@dataclass
class DataGapRecord:
    """Explicitly tracked discontinuity or gap in collection."""
    gap_id: str
    start_time: str
    end_time: Optional[str] = None
    reason: str = "WEBSOCKET_DISCONNECT"
    last_known_slot: Optional[int] = None
    reconnected_slot: Optional[int] = None
    estimated_missed_slots: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "reason": self.reason,
            "last_known_slot": self.last_known_slot,
            "reconnected_slot": self.reconnected_slot,
            "estimated_missed_slots": self.estimated_missed_slots,
        }


@dataclass
class CollectorCheckpoint:
    """Durable state marker persisted across collector restarts."""
    checkpoint_key: str
    checkpoint_value: str
    updated_at: str = field(default_factory=utc_now_iso)


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
        progress = min(100.0, max(0.0, (real_sol / constants.GRADUATION_REAL_SOL_TARGET) * 100.0))
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
    observation_window_seconds: int
    prediction_horizon_seconds: int
    label_type: str
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
