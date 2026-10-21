from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
from src.model_d.features import AMDState, LiquidityFeatures


@dataclass
class ModelDDecision:
    signal_id: str
    symbol: str
    timestamp_ms: int
    direction: str
    status: str
    entry_reference: Optional[float]
    invalidation: Optional[float]
    confidence: Optional[float]
    confidence_definition: str
    positive_evidence: List[str]
    contradictory_evidence: List[str]
    explanation: str
    strategy_version: str = "0.1.0-research"

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


def decide(symbol: str, timestamp_ms: int, features: LiquidityFeatures, amd: AMDState, signal_id: str) -> ModelDDecision:
    positive: List[str] = []; contradictory: List[str] = []
    if features.depth_imbalance is not None:
        if features.depth_imbalance > 0.2: positive.append("bid_depth_exceeds_ask_depth")
        elif features.depth_imbalance < -0.2: positive.append("ask_depth_exceeds_bid_depth")
    if features.aggressive_buy_notional > features.aggressive_sell_notional * 1.5: positive.append("aggressive_buy_pressure")
    if features.aggressive_sell_notional > features.aggressive_buy_notional * 1.5: positive.append("aggressive_sell_pressure")
    if amd.reclaim_confirmed: positive.append(f"{amd.sweep_direction or 'range'}_sweep_reclaimed")
    if features.data_quality != "OK" or amd.state == "UNAVAILABLE":
        contradictory.append("critical_data_incomplete")
    if amd.state == "MANIPULATION" and amd.sweep_direction == "BELOW" and features.aggressive_buy_notional >= features.aggressive_sell_notional:
        direction = "LONG"
    elif amd.state == "MANIPULATION" and amd.sweep_direction == "ABOVE" and features.aggressive_sell_notional >= features.aggressive_buy_notional:
        direction = "SHORT"
    else: direction = "WAIT"
    if contradictory: direction = "WAIT"
    return ModelDDecision(signal_id, symbol, timestamp_ms, direction, "CONFIRMED" if direction != "WAIT" else "WAITING",
        features.mid_price, None, 0.6 if direction != "WAIT" else None,
        "heuristic, uncalibrated score; not a probability" if direction != "WAIT" else "not applicable",
        positive, contradictory, "Model D uses executed-flow evidence separately from resting liquidity and requires sweep/reclaim confirmation.")
