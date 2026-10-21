"""Evidence-based whale-following context and order-flow fusion for Model D.

Wallet evidence remains contextual. Direction is derived from observed AsterDEX
executions/liquidity and is withheld when critical inputs are stale or missing.
"""
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
from src.model_d.features import AMDState, LiquidityFeatures
from src.model_d.wallets import WalletCandidate


ACTOR_SCORE_VERSION = "actor-context-v1"
FUSION_VERSION = "orderflow-wallet-context-v1"


@dataclass
class ActorEvidence:
    label: str
    source: str
    verification_status: str
    confidence: Optional[float]
    evidence_reference: Optional[str]
    timestamp_ms: Optional[int]

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


def classify_wallet(candidate: WalletCandidate) -> ActorEvidence:
    """Classify observed wallet evidence without claiming trader identity."""
    if candidate.contract_classification in {"EXCHANGE", "BRIDGE", "PROTOCOL", "TREASURY"}:
        label = "EXCHANGE_INFRASTRUCTURE" if candidate.contract_classification == "EXCHANGE" else "PROTOCOL_OR_BRIDGE"
    elif candidate.verification_status == "VERIFIED":
        label = "VERIFIED_ENTITY"
    elif candidate.enrichment_status == "ENRICHED" and (candidate.transaction_count_observed or 0) >= 10:
        label = "ACTIVE_WALLET_CANDIDATE"
    else:
        label = "UNKNOWN"
    return ActorEvidence(label, "on_chain_registry", candidate.verification_status, None,
                         candidate.wallet_id, None)


@dataclass
class FusionDecision:
    direction: str
    timestamp_ms: int
    heuristic_score: Optional[float]
    confidence_definition: str
    wallet_context_score: Optional[float]
    orderflow_score: Optional[float]
    supporting_evidence: List[str]
    contradicting_evidence: List[str]
    invalidation: Optional[str]
    data_quality: str
    version: str = FUSION_VERSION

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


def fuse_orderflow_context(timestamp_ms: int, features: LiquidityFeatures, amd: AMDState,
                           wallet_evidence: Optional[List[ActorEvidence]] = None,
                           source_fresh: bool = True) -> FusionDecision:
    support: List[str] = []; contradict: List[str] = []
    if not source_fresh or features.data_quality != "OK" or amd.state == "UNAVAILABLE":
        return FusionDecision("WAIT", timestamp_ms, None, "not applicable", None, None, [], ["critical_data_unavailable_or_stale"], amd.invalidation, "UNAVAILABLE")
    buy = features.aggressive_buy_notional; sell = features.aggressive_sell_notional
    orderflow = 1.0 if buy > sell * 1.5 else -1.0 if sell > buy * 1.5 else 0.0
    if orderflow > 0: support.append("observed_aggressive_buy_pressure")
    if orderflow < 0: support.append("observed_aggressive_sell_pressure")
    if amd.reclaim_confirmed: support.append(f"{amd.sweep_direction}_sweep_reclaim")
    wallet_score = None
    if wallet_evidence:
        wallet_score = sum(0.2 for e in wallet_evidence if e.label in {"ACTIVE_WALLET_CANDIDATE", "VERIFIED_ENTITY"})
        support.append("wallet_context_recorded_not_directional")
    if amd.state == "MANIPULATION" and amd.sweep_direction == "BELOW" and orderflow > 0:
        direction = "LONG"
    elif amd.state == "MANIPULATION" and amd.sweep_direction == "ABOVE" and orderflow < 0:
        direction = "SHORT"
    else:
        direction = "WAIT"
        contradict.append("market_structure_confirmation_insufficient")
    return FusionDecision(direction, timestamp_ms, abs(orderflow), "heuristic score, not calibrated probability", wallet_score, orderflow, support, contradict, amd.invalidation, "OK")
