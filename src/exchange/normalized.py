"""Normalized multi-exchange data structures for Model B liquidity intelligence.

Provides standard abstractions across supported perpetual exchanges (AsterDEX, Binance, Hyperliquid).
Strictly separates data-plane models from order execution (read-only telemetry).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import time


@dataclass
class OrderBookLevel:
    """Single price-quantity level in an order book."""
    price: float
    size: float

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass
class NormalizedOrderBook:
    """Standardized Level-2 Order Book snapshot across venues."""
    symbol: str
    exchange_name: str
    timestamp_ms: int
    bids: List[OrderBookLevel] = field(default_factory=list)  # Descending: best bid first
    asks: List[OrderBookLevel] = field(default_factory=list)  # Ascending: best ask first

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        bb = self.best_bid
        ba = self.best_ask
        if bb is not None and ba is not None:
            return (bb + ba) / 2.0
        return bb or ba

    @property
    def spread(self) -> float:
        bb = self.best_bid
        ba = self.best_ask
        if bb is not None and ba is not None:
            return max(0.0, ba - bb)
        return 0.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid and mid > 0:
            return (self.spread / mid) * 10000.0
        return 0.0

    def depth_at_bps(self, bps: float) -> Tuple[float, float]:
        """Compute cumulative notional depth (USDT) on bids and asks within +/- bps from mid."""
        mid = self.mid_price
        if not mid or mid <= 0:
            return 0.0, 0.0

        bid_cutoff = mid * (1.0 - bps / 10000.0)
        ask_cutoff = mid * (1.0 + bps / 10000.0)

        bid_notional = sum(lvl.notional for lvl in self.bids if lvl.price >= bid_cutoff)
        ask_notional = sum(lvl.notional for lvl in self.asks if lvl.price <= ask_cutoff)
        return bid_notional, ask_notional

    def imbalance_ratio(self, bps: float = 25.0) -> float:
        """Compute order book depth imbalance ratio in [-1.0, 1.0].
        
        Formula: (bid_depth - ask_depth) / (bid_depth + ask_depth)
        Positive: buy pressure / bid support.
        Negative: sell pressure / ask resistance.
        """
        bid_depth, ask_depth = self.depth_at_bps(bps)
        tot = bid_depth + ask_depth
        if tot <= 0:
            return 0.0
        return (bid_depth - ask_depth) / tot

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange_name,
            "timestamp_ms": self.timestamp_ms,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": self.mid_price,
            "spread_bps": round(self.spread_bps, 2),
            "bids_top5": [{"price": b.price, "size": b.size} for b in self.bids[:5]],
            "asks_top5": [{"price": a.price, "size": a.size} for a in self.asks[:5]],
        }


@dataclass
class NormalizedTrade:
    """Standardized public trade print."""
    trade_id: str
    symbol: str
    exchange_name: str
    timestamp_ms: int
    price: float
    size: float
    notional_usdt: float
    side: str  # "BUY" (buyer taker) or "SELL" (seller taker)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "exchange": self.exchange_name,
            "timestamp_ms": self.timestamp_ms,
            "price": self.price,
            "size": self.size,
            "notional_usdt": self.notional_usdt,
            "side": self.side,
        }


@dataclass
class NormalizedInstrument:
    """Standardized perpetual contract specifications across exchanges."""
    symbol: str
    exchange_name: str
    base_asset: str
    quote_asset: str
    min_qty: float
    step_size: float
    tick_size: float
    min_notional: float
    contract_type: str = "PERPETUAL"
    funding_rate: Optional[float] = None
    funding_interval_hours: int = 8
    next_funding_time_ms: Optional[int] = None
    mark_price: Optional[float] = None
    index_price: Optional[float] = None
    last_price: Optional[float] = None
    volume_24h_usdt: Optional[float] = None
    is_active: bool = True
    discovered_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    volume_status: str = "AVAILABLE"
    eligibility_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange_name,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "min_qty": self.min_qty,
            "step_size": self.step_size,
            "tick_size": self.tick_size,
            "min_notional": self.min_notional,
            "contract_type": self.contract_type,
            "funding_rate": self.funding_rate,
            "funding_interval_hours": self.funding_interval_hours,
            "next_funding_time_ms": self.next_funding_time_ms,
            "mark_price": self.mark_price,
            "last_price": self.last_price,
            "volume_24h_usdt": self.volume_24h_usdt,
            "volume_status": self.volume_status,
            "eligibility_reason": self.eligibility_reason,
            "is_active": self.is_active,
            "discovered_at_ms": self.discovered_at_ms,
        }


@dataclass
class LiquiditySnapshot:
    """Aggregated liquidity features computed from order book depth and trade flow."""
    symbol: str
    exchange_name: str
    timestamp_ms: int
    spread_bps: float
    depth_10bps_usdt: float
    depth_25bps_usdt: float
    depth_50bps_usdt: float
    depth_100bps_usdt: float
    imbalance_10bps: float
    imbalance_25bps: float
    imbalance_50bps: float
    imbalance_100bps: float
    cvd_1h: float
    taker_buy_volume_1h: float
    taker_sell_volume_1h: float
    large_trade_count: int
    absorption_score: float
    sweep_detected: bool = False
    sweep_direction: Optional[str] = None
    sweep_reclaimed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange_name,
            "timestamp_ms": self.timestamp_ms,
            "spread_bps": round(self.spread_bps, 2),
            "depth_10bps_usdt": round(self.depth_10bps_usdt, 2),
            "depth_25bps_usdt": round(self.depth_25bps_usdt, 2),
            "depth_50bps_usdt": round(self.depth_50bps_usdt, 2),
            "depth_100bps_usdt": round(self.depth_100bps_usdt, 2),
            "imbalance_10bps": round(self.imbalance_10bps, 4),
            "imbalance_25bps": round(self.imbalance_25bps, 4),
            "imbalance_50bps": round(self.imbalance_50bps, 4),
            "imbalance_100bps": round(self.imbalance_100bps, 4),
            "cvd_1h": round(self.cvd_1h, 2),
            "taker_buy_volume_1h": round(self.taker_buy_volume_1h, 2),
            "taker_sell_volume_1h": round(self.taker_sell_volume_1h, 2),
            "large_trade_count": self.large_trade_count,
            "absorption_score": round(self.absorption_score, 4),
            "sweep_detected": self.sweep_detected,
            "sweep_direction": self.sweep_direction,
            "sweep_reclaimed": self.sweep_reclaimed,
        }
