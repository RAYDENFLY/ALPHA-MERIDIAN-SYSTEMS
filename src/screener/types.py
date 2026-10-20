"""Data models and type definitions for Multi-Exchange Perpetual Market Screener."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import time


@dataclass
class VenueMarketMetrics:
    """Standardized Level-1 and Level-2 market metrics for a single exchange venue."""
    symbol: str
    exchange: str
    contract_type: str = "PERPETUAL"
    last_price: Optional[float] = None
    mark_price: Optional[float] = None
    index_price: Optional[float] = None
    price_change_24h_pct: Optional[float] = None
    volume_24h_usdt: Optional[float] = None
    open_interest_notional: Optional[float] = None
    open_interest_contracts: Optional[float] = None
    funding_rate: Optional[float] = None
    funding_interval_hours: int = 8
    normalized_8h_funding_rate: Optional[float] = None
    annualized_funding_pct: Optional[float] = None
    next_funding_time_ms: Optional[int] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    bid_qty: Optional[float] = None
    ask_qty: Optional[float] = None
    spread_pct: Optional[float] = None
    spread_bps: Optional[float] = None
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    latency_ms: float = 0.0
    status: str = "AVAILABLE"  # "AVAILABLE", "STALE", "UNAVAILABLE", "ERROR"
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.funding_rate is not None and self.normalized_8h_funding_rate is None:
            interval = self.funding_interval_hours or 8
            self.normalized_8h_funding_rate = self.funding_rate * (8.0 / interval)
            self.annualized_funding_pct = self.funding_rate * (24.0 / interval) * 365.0 * 100.0
        if self.best_bid is not None and self.best_ask is not None and self.best_ask >= self.best_bid and self.spread_bps is None:
            mid = (self.best_bid + self.best_ask) / 2.0
            if mid > 0:
                self.spread_pct = ((self.best_ask - self.best_bid) / mid) * 100.0
                self.spread_bps = self.spread_pct * 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "contract_type": self.contract_type,
            "last_price": self.last_price,
            "mark_price": self.mark_price,
            "index_price": self.index_price,
            "price_change_24h_pct": self.price_change_24h_pct,
            "volume_24h_usdt": self.volume_24h_usdt,
            "open_interest_notional": self.open_interest_notional,
            "open_interest_contracts": self.open_interest_contracts,
            "funding_rate": self.funding_rate,
            "funding_interval_hours": self.funding_interval_hours,
            "normalized_8h_funding_rate": self.normalized_8h_funding_rate,
            "annualized_funding_pct": self.annualized_funding_pct,
            "next_funding_time_ms": self.next_funding_time_ms,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "bid_qty": self.bid_qty,
            "ask_qty": self.ask_qty,
            "spread_pct": self.spread_pct,
            "spread_bps": self.spread_bps,
            "timestamp_ms": self.timestamp_ms,
            "latency_ms": round(self.latency_ms, 1),
            "status": self.status,
            "error_message": self.error_message,
        }


@dataclass
class CrossExchangeMarketAsset:
    """Aggregated market data for an underlying asset across all reporting venues."""
    normalized_symbol: str
    base_asset: str = ""
    quote_asset: str = "USDT"
    total_volume_24h_usdt: float = 0.0
    total_open_interest_usdt: float = 0.0
    oi_change_1h_pct: Optional[float] = None
    oi_change_24h_pct: Optional[float] = None
    benchmark_price: Optional[float] = None
    price_change_24h_pct: Optional[float] = None
    venues: Dict[str, VenueMarketMetrics] = field(default_factory=dict)
    venue_count: int = 0
    cross_listed: bool = False
    
    # Cross-venue price divergence
    price_divergence_usdt: Optional[float] = None
    price_divergence_bps: Optional[float] = None
    price_divergence_pct: Optional[float] = None
    min_price_venue: Optional[str] = None
    max_price_venue: Optional[str] = None
    
    # Funding divergence
    min_funding_venue: Optional[str] = None
    max_funding_venue: Optional[str] = None
    funding_divergence_bps: Optional[float] = None
    
    # Liquidity comparison
    highest_volume_venue: Optional[str] = None
    lowest_spread_venue: Optional[str] = None
    best_spread_bps: Optional[float] = None
    data_freshness_seconds: float = 0.0

    def recompute_cross_venue_metrics(self) -> None:
        """Derive cross-venue aggregated metrics, benchmark price, divergence, and liquidity leader."""
        self.venue_count = len(self.venues)
        self.cross_listed = self.venue_count >= 2

        vols = [v.volume_24h_usdt for v in self.venues.values() if v.volume_24h_usdt is not None]
        if vols:
            self.total_volume_24h_usdt = sum(vols)

        ois = [v.open_interest_notional for v in self.venues.values() if v.open_interest_notional is not None]
        if ois:
            self.total_open_interest_usdt = sum(ois)

        prices = [v.mark_price or v.last_price for v in self.venues.values() if (v.mark_price or v.last_price)]
        if prices:
            prices.sort()
            mid_idx = len(prices) // 2
            self.benchmark_price = prices[mid_idx]

        valid_venues = {k: v for k, v in self.venues.items() if (v.mark_price or v.last_price)}
        if len(valid_venues) >= 2:
            sorted_venues = sorted(valid_venues.items(), key=lambda x: (x[1].mark_price or x[1].last_price or 0.0))
            min_ex, min_m = sorted_venues[0]
            max_ex, max_m = sorted_venues[-1]
            min_p = min_m.mark_price or min_m.last_price or 0.0
            max_p = max_m.mark_price or max_m.last_price or 0.0

            div_usdt = max(0.0, max_p - min_p)
            self.price_divergence_usdt = div_usdt
            self.min_price_venue = min_ex
            self.max_price_venue = max_ex

            if self.benchmark_price and self.benchmark_price > 0:
                self.price_divergence_pct = (div_usdt / self.benchmark_price) * 100.0
                self.price_divergence_bps = self.price_divergence_pct * 100.0

        valid_fr = {k: v for k, v in self.venues.items() if v.normalized_8h_funding_rate is not None}
        if len(valid_fr) >= 2:
            sorted_fr = sorted(valid_fr.items(), key=lambda x: x[1].normalized_8h_funding_rate or 0.0)
            self.min_funding_venue = sorted_fr[0][0]
            self.max_funding_venue = sorted_fr[-1][0]
            diff = (sorted_fr[-1][1].normalized_8h_funding_rate or 0) - (sorted_fr[0][1].normalized_8h_funding_rate or 0)
            self.funding_divergence_bps = diff * 10000.0

        vol_sorted = sorted(
            [(k, v.volume_24h_usdt or 0.0) for k, v in self.venues.items()],
            key=lambda x: x[1],
            reverse=True,
        )
        if vol_sorted and vol_sorted[0][1] > 0:
            self.highest_volume_venue = vol_sorted[0][0]

        spread_sorted = sorted(
            [(k, v.spread_bps) for k, v in self.venues.items() if v.spread_bps is not None and v.spread_bps > 0],
            key=lambda x: x[1],
        )
        if spread_sorted:
            self.lowest_spread_venue = spread_sorted[0][0]
            self.best_spread_bps = spread_sorted[0][1]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "normalized_symbol": self.normalized_symbol,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "total_volume_24h_usdt": round(self.total_volume_24h_usdt, 2),
            "total_open_interest_usdt": round(self.total_open_interest_usdt, 2),
            "oi_change_1h_pct": round(self.oi_change_1h_pct, 2) if self.oi_change_1h_pct is not None else None,
            "oi_change_24h_pct": round(self.oi_change_24h_pct, 2) if self.oi_change_24h_pct is not None else None,
            "benchmark_price": self.benchmark_price,
            "price_change_24h_pct": round(self.price_change_24h_pct, 2) if self.price_change_24h_pct is not None else None,
            "venue_count": self.venue_count,
            "cross_listed": self.cross_listed,
            "price_divergence_usdt": round(self.price_divergence_usdt, 4) if self.price_divergence_usdt is not None else None,
            "price_divergence_bps": round(self.price_divergence_bps, 2) if self.price_divergence_bps is not None else None,
            "price_divergence_pct": round(self.price_divergence_pct, 4) if self.price_divergence_pct is not None else None,
            "min_price_venue": self.min_price_venue,
            "max_price_venue": self.max_price_venue,
            "min_funding_venue": self.min_funding_venue,
            "max_funding_venue": self.max_funding_venue,
            "funding_divergence_bps": round(self.funding_divergence_bps, 2) if self.funding_divergence_bps is not None else None,
            "highest_volume_venue": self.highest_volume_venue,
            "lowest_spread_venue": self.lowest_spread_venue,
            "best_spread_bps": round(self.best_spread_bps, 2) if self.best_spread_bps is not None else None,
            "data_freshness_seconds": round(self.data_freshness_seconds, 1),
            "venues": {k: v.to_dict() for k, v in self.venues.items()},
        }


@dataclass
class VenueStatusTelemetry:
    """Status, latency, and instrument discovery count for each exchange."""
    exchange_name: str
    status: str = "AVAILABLE"  # "AVAILABLE", "DEGRADED", "OFFLINE"
    latency_ms: float = 0.0
    last_successful_fetch_utc: Optional[str] = None
    total_contracts: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exchange_name": self.exchange_name,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "last_successful_fetch_utc": self.last_successful_fetch_utc,
            "total_contracts": self.total_contracts,
            "error_message": self.error_message,
        }


@dataclass
class ScreenerFilterParams:
    """User-configurable filtering parameters for the market screener."""
    search: Optional[str] = None
    min_volume_usdt: float = 1_000_000.0  # Default $1M USD turnover
    max_volume_usdt: Optional[float] = None
    min_oi_usdt: Optional[float] = None
    max_oi_usdt: Optional[float] = None
    funding_direction: Optional[str] = None  # "ALL", "POSITIVE", "NEGATIVE"
    min_funding_abs_pct: Optional[float] = None
    max_spread_bps: Optional[float] = None
    exchanges: Optional[List[str]] = None
    cross_listed_only: bool = False
    sort_by: str = "volume_desc"
    page: int = 1
    page_size: int = 25


@dataclass
class MultiExchangeScreenerResponse:
    """Paginated response payload for the market screener."""
    total_universe_count: int
    total_matched: int
    page: int
    page_size: int
    total_pages: int
    combined_volume_all_usdt: float
    combined_oi_all_usdt: float
    last_fetch_utc: Optional[str]
    venues_telemetry: Dict[str, Any]
    data: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_universe_count": self.total_universe_count,
            "total_matched": self.total_matched,
            "page": self.page,
            "page_size": self.page_size,
            "total_pages": self.total_pages,
            "combined_volume_all_usdt": round(self.combined_volume_all_usdt, 2),
            "combined_oi_all_usdt": round(self.combined_oi_all_usdt, 2),
            "last_fetch_utc": self.last_fetch_utc,
            "venues_telemetry": self.venues_telemetry,
            "data": self.data,
        }

