"""Deterministic Model D features; resting liquidity and executions stay separate."""
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
from src.exchange.normalized import NormalizedOrderBook, NormalizedTrade


@dataclass
class LiquidityFeatures:
    mid_price: Optional[float]
    spread_bps: Optional[float]
    depth_imbalance: Optional[float]
    aggressive_buy_notional: float
    aggressive_sell_notional: float
    large_trade_threshold_usdt: Optional[float]
    data_quality: str

    def to_dict(self) -> Dict[str, Any]: return asdict(self)


def build_features(book: Optional[NormalizedOrderBook], trades: List[NormalizedTrade], reference_window: int = 200) -> LiquidityFeatures:
    if book is None or not book.bids or not book.asks:
        return LiquidityFeatures(None, None, None, 0.0, 0.0, None, "INCOMPLETE_ORDER_BOOK")
    mid = book.mid_price
    bid_depth = sum(max(0.0, x.price * x.size) for x in book.bids[:10])
    ask_depth = sum(max(0.0, x.price * x.size) for x in book.asks[:10])
    total = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total if total else None
    notionals = sorted(max(0.0, t.notional_usdt) for t in trades[-reference_window:])
    threshold = notionals[int(len(notionals) * 0.95)] if notionals else None
    return LiquidityFeatures(mid, book.spread_bps, imbalance,
        sum(t.notional_usdt for t in trades if t.side == "BUY"),
        sum(t.notional_usdt for t in trades if t.side == "SELL"), threshold, "OK")


@dataclass
class AMDState:
    state: str
    confidence: float
    range_high: Optional[float]
    range_low: Optional[float]
    sweep_direction: Optional[str]
    reclaim_confirmed: bool
    provisional: bool = False
    invalidation: Optional[str] = None


def classify_amd(candles: List[Any]) -> AMDState:
    """Classify only from candles supplied; callers must pass closed candles."""
    if len(candles) < 8:
        return AMDState("UNAVAILABLE", 0.0, None, None, None, False, invalidation="insufficient_closed_candles")
    window = candles[-8:-2]
    high = max(c.high for c in window); low = min(c.low for c in window)
    last = candles[-1]
    if last.high > high and last.close < high:
        return AMDState("MANIPULATION", 0.7, high, low, "ABOVE", True, invalidation="close_above_range_high")
    if last.low < low and last.close > low:
        return AMDState("MANIPULATION", 0.7, high, low, "BELOW", True, invalidation="close_below_range_low")
    span = high - low
    if span > 0 and max(c.high for c in candles[-4:]) - min(c.low for c in candles[-4:]) < span * 0.65:
        return AMDState("ACCUMULATION", 0.6, high, low, None, False, invalidation="range_expansion")
    return AMDState("DISTRIBUTION", 0.45, high, low, None, False, invalidation="new_range_required")
