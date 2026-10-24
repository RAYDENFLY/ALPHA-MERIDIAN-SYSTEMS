"""Cross-Exchange Order-Book Liquidity Heatmap Engine.

Aggregates public Level-2 order books across:
1. AsterDEX V3 Perpetual
2. Binance USD(S)-M Futures
3. Bybit Linear Perpetual

Features:
- Strict contract verification: Linear USDT perpetuals only (no spot substitution).
- Normalized price bucketing without merging materially different prices.
- Retains venue, side, price, distance bps, quantity, USD notional, age, persistence, and cross-venue confirmation.
- Clear separation between visible resting order-book liquidity, structural liquidity (BSL/SSL), and liquidation models.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import concurrent.futures
import math
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.logger import logger
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.binance_adapter import BinanceFuturesAdapter
from src.exchange.bybit_adapter import BybitLinearAdapter
from src.exchange.normalized import NormalizedOrderBook, OrderBookLevel


@dataclass
class VenueOrderBookSnapshot:
    """Summary snapshot of a single venue's order book."""
    exchange_name: str
    status: str  # "AVAILABLE", "ERROR", "TIMEOUT", "UNAVAILABLE"
    timestamp_ms: int
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    mid_price: Optional[float] = None
    spread_bps: float = 0.0
    total_bid_notional_top50: float = 0.0
    total_ask_notional_top50: float = 0.0
    latency_ms: float = 0.0
    freshness_status: str = "LIVE"  # "LIVE", "STALE", "ERROR", "UNAVAILABLE"
    age_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exchange_name": self.exchange_name,
            "status": self.status,
            "freshness_status": self.freshness_status,
            "timestamp_ms": self.timestamp_ms,
            "age_ms": self.age_ms,
            "best_bid": round(self.best_bid, 4) if self.best_bid else None,
            "best_ask": round(self.best_ask, 4) if self.best_ask else None,
            "mid_price": round(self.mid_price, 4) if self.mid_price else None,
            "spread_bps": round(self.spread_bps, 2),
            "total_bid_notional_top50": round(self.total_bid_notional_top50, 2),
            "total_ask_notional_top50": round(self.total_ask_notional_top50, 2),
            "latency_ms": round(self.latency_ms, 1),
        }


@dataclass
class HeatmapPriceBucket:
    """Normalized price bucket aggregating resting depth across venues without collapsing price precision."""
    bucket_price: float
    side: str  # "BID" or "ASK"
    distance_bps: float
    total_notional_usdt: float
    total_quantity: float
    venues: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    venue_count: int = 0
    is_cross_confirmed: bool = False
    confirmation_tier: str = "SINGLE_VENUE"  # "SINGLE_VENUE", "DUAL_CONFIRMED", "TRIPLE_CONFIRMED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bucket_price": round(self.bucket_price, 4),
            "side": self.side,
            "distance_bps": round(self.distance_bps, 2),
            "total_notional_usdt": round(self.total_notional_usdt, 2),
            "total_quantity": round(self.total_quantity, 4),
            "venues": self.venues,
            "venue_count": self.venue_count,
            "is_cross_confirmed": self.is_cross_confirmed,
            "confirmation_tier": self.confirmation_tier,
        }


@dataclass
class PersistentRestingWall:
    """Resting liquidity wall tracked across observation cycles."""
    wall_id: str
    symbol: str
    side: str
    price: float
    notional_usdt: float
    distance_bps: float
    venues: List[str]
    venue_count: int
    is_cross_confirmed: bool
    confirmation_tier: str
    first_seen_timestamp_ms: int
    last_seen_timestamp_ms: int
    persistence_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wall_id": self.wall_id,
            "symbol": self.symbol,
            "side": self.side,
            "price": round(self.price, 4),
            "notional_usdt": round(self.notional_usdt, 2),
            "distance_bps": round(self.distance_bps, 2),
            "venues": self.venues,
            "venue_count": self.venue_count,
            "is_cross_confirmed": self.is_cross_confirmed,
            "confirmation_tier": self.confirmation_tier,
            "first_seen_timestamp_ms": self.first_seen_timestamp_ms,
            "last_seen_timestamp_ms": self.last_seen_timestamp_ms,
            "persistence_seconds": round(self.persistence_seconds, 1),
        }


class CrossExchangeLiquidityAggregator:
    """Coordinates multi-exchange order book retrieval, bucketing, and resting wall persistence."""

    def __init__(
        self,
        aster_adapter: Optional[AsterAdapter] = None,
        binance_adapter: Optional[BinanceFuturesAdapter] = None,
        bybit_adapter: Optional[BybitLinearAdapter] = None,
        wall_notional_threshold: float = 25000.0,
        wall_multiple_threshold: float = 2.0,
    ) -> None:
        self.aster_adapter = aster_adapter or AsterAdapter()
        self.binance_adapter = binance_adapter or BinanceFuturesAdapter()
        self.bybit_adapter = bybit_adapter or BybitLinearAdapter()
        self.wall_notional_threshold = wall_notional_threshold
        self.wall_multiple_threshold = wall_multiple_threshold

        # Persistent wall tracking keyed by (symbol, side, bucket_price_str)
        self._persistent_walls: Dict[str, PersistentRestingWall] = {}
        self._last_purge_ms: int = int(time.time() * 1000)
        # Retain latest valid order books per venue (from the most recent aggregate_liquidity_heatmap call)
        self.last_raw_books: Dict[str, Dict[str, Optional[NormalizedOrderBook]]] = {}
        self._heatmap_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def fetch_all_order_books(self, symbol: str, limit: int = 50) -> Dict[str, Tuple[Optional[NormalizedOrderBook], float]]:
        """Fetch Level-2 order books concurrently across AsterDEX, Binance, and Bybit."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        results: Dict[str, Tuple[Optional[NormalizedOrderBook], float]] = {}

        def _fetch_venue(vname: str) -> Tuple[str, Optional[NormalizedOrderBook], float]:
            t0 = time.time()
            try:
                if vname == "asterdex":
                    ob = self.aster_adapter.get_order_book(clean_symbol, limit=limit)
                elif vname == "binance":
                    ob = self.binance_adapter.get_order_book(clean_symbol, limit=limit)
                elif vname == "bybit":
                    ob = self.bybit_adapter.get_order_book(clean_symbol, limit=limit)
                else:
                    ob = None
                lat = (time.time() - t0) * 1000.0
                return vname, ob, lat
            except Exception as err:
                lat = (time.time() - t0) * 1000.0
                logger.warning(f"CrossExchange: {vname} fetch error for {clean_symbol}: {err}")
                return vname, None, lat

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_fetch_venue, v) for v in ["asterdex", "binance", "bybit"]]
            for fut in concurrent.futures.as_completed(futures):
                vname, book, latency = fut.result()
                results[vname] = (book, latency)

        return results

    def aggregate_liquidity_heatmap(
        self,
        symbol: str,
        bucket_bps: float = 5.0,
        venue_filter: str = "combined",
        limit: int = 50,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """Aggregate order-book depth across venues with normalized price bucketing."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        now_ms = int(time.time() * 1000)
        now_sec = time.time()

        # Shared short cache (3.0s TTL) to prevent API hammering during auto-refresh
        cache_key = f"{clean_symbol}_{bucket_bps}_{venue_filter}_{limit}"
        if not force_refresh and cache_key in self._heatmap_cache:
            cached_t, cached_data = self._heatmap_cache[cache_key]
            if (now_sec - cached_t) < 3.0:
                return cached_data

        requested = venue_filter.lower().strip()
        if requested in ("asterdex", "binance", "bybit"):
            # Do not contact unrelated exchanges for an explicitly selected venue.
            started = time.time()
            adapter = {"asterdex": self.aster_adapter, "binance": self.binance_adapter, "bybit": self.bybit_adapter}[requested]
            try:
                raw_books = {requested: (adapter.get_order_book(clean_symbol, limit=limit), (time.time() - started) * 1000.0)}
            except Exception as err:
                logger.warning(f"CrossExchange: {requested} fetch error for {clean_symbol}: {err}")
                raw_books = {requested: (None, (time.time() - started) * 1000.0)}
        else:
            raw_books = self.fetch_all_order_books(clean_symbol, limit=limit)

        self.last_raw_books[clean_symbol] = {k: v[0] for k, v in raw_books.items()}

        # Build Venue Summaries with freshness indicators
        venue_summaries: Dict[str, VenueOrderBookSnapshot] = {}
        valid_mids: List[float] = []

        for vname in ["asterdex", "binance", "bybit"]:
            book_entry = raw_books.get(vname)
            book, latency = book_entry if book_entry else (None, 0.0)
            if book and book.bids and book.asks:
                tot_bid = sum(b.notional for b in book.bids)
                tot_ask = sum(a.notional for a in book.asks)
                mid = book.mid_price or 0.0
                if mid > 0:
                    valid_mids.append(mid)
                age_ms = max(0, now_ms - (book.timestamp_ms or now_ms))
                freshness = "LIVE" if age_ms < 15000 else "STALE"
                snap = VenueOrderBookSnapshot(
                    exchange_name=vname,
                    status="AVAILABLE",
                    timestamp_ms=book.timestamp_ms,
                    best_bid=book.best_bid,
                    best_ask=book.best_ask,
                    mid_price=mid,
                    spread_bps=book.spread_bps,
                    total_bid_notional_top50=tot_bid,
                    total_ask_notional_top50=tot_ask,
                    latency_ms=latency,
                    freshness_status=freshness,
                    age_ms=age_ms,
                )
            else:
                snap = VenueOrderBookSnapshot(
                    exchange_name=vname,
                    status="UNAVAILABLE",
                    timestamp_ms=now_ms,
                    latency_ms=latency,
                    freshness_status="UNAVAILABLE",
                    age_ms=0,
                )
            venue_summaries[vname] = snap

        if not valid_mids:
            return {
                "symbol": clean_symbol,
                "contract_type": "PERPETUAL_LINEAR",
                "verified_contract": True,
                "status": "UNAVAILABLE",
                "message": f"No order book available across venues for {clean_symbol}",
                "composite_mid": 0.0,
                "venue_summaries": {k: v.to_dict() for k, v in venue_summaries.items()},
                "bids_buckets": [],
                "asks_buckets": [],
                "persistent_walls": [],
                "distinction_disclosure": (
                    "ORDER_BOOK_DEPTH: Visible resting maker orders on AsterDEX V3, Binance USD(S)-M Futures, "
                    "and Bybit Linear. DISTINCT from historical structural liquidity (BSL/SSL) and estimated liquidation clusters."
                ),
            }

        # Calculate composite mid price
        composite_mid = sum(valid_mids) / len(valid_mids)

        # Bucket width calculation in price units
        bucket_pct = max(0.0001, bucket_bps / 10000.0)
        bucket_price_step = composite_mid * bucket_pct

        # Bucketing containers: bucket_center_price -> HeatmapPriceBucket
        bids_map: Dict[float, HeatmapPriceBucket] = {}
        asks_map: Dict[float, HeatmapPriceBucket] = {}

        # Venues to process according to venue_filter
        target_venues = ["asterdex", "binance", "bybit"]
        if venue_filter.lower() in ("asterdex", "binance", "bybit"):
            target_venues = [venue_filter.lower()]

        for vname in target_venues:
            book_entry = raw_books.get(vname)
            if not book_entry:
                continue
            book, _ = book_entry
            if not book or not book.bids or not book.asks:
                continue

            # Process bids
            for b in book.bids:
                if b.price <= 0:
                    continue
                # Align to bucket center
                steps = round(b.price / bucket_price_step)
                bucket_px = round(steps * bucket_price_step, 4)
                dist_bps = ((composite_mid - bucket_px) / composite_mid) * 10000.0

                if bucket_px not in bids_map:
                    bids_map[bucket_px] = HeatmapPriceBucket(
                        bucket_price=bucket_px,
                        side="BID",
                        distance_bps=dist_bps,
                        total_notional_usdt=0.0,
                        total_quantity=0.0,
                        venues={},
                    )

                bucket = bids_map[bucket_px]
                bucket.total_notional_usdt += b.notional
                bucket.total_quantity += b.size
                if vname not in bucket.venues:
                    bucket.venues[vname] = {"notional_usdt": 0.0, "quantity": 0.0, "best_price": b.price}
                bucket.venues[vname]["notional_usdt"] += b.notional
                bucket.venues[vname]["quantity"] += b.size

            # Process asks
            for a in book.asks:
                if a.price <= 0:
                    continue
                steps = round(a.price / bucket_price_step)
                bucket_px = round(steps * bucket_price_step, 4)
                dist_bps = ((bucket_px - composite_mid) / composite_mid) * 10000.0

                if bucket_px not in asks_map:
                    asks_map[bucket_px] = HeatmapPriceBucket(
                        bucket_price=bucket_px,
                        side="ASK",
                        distance_bps=dist_bps,
                        total_notional_usdt=0.0,
                        total_quantity=0.0,
                        venues={},
                    )

                bucket = asks_map[bucket_px]
                bucket.total_notional_usdt += a.notional
                bucket.total_quantity += a.size
                if vname not in bucket.venues:
                    bucket.venues[vname] = {"notional_usdt": 0.0, "quantity": 0.0, "best_price": a.price}
                bucket.venues[vname]["notional_usdt"] += a.notional
                bucket.venues[vname]["quantity"] += a.size

        # Post-process confirmation tiers
        all_buckets: List[HeatmapPriceBucket] = list(bids_map.values()) + list(asks_map.values())
        for b in all_buckets:
            b.venue_count = len(b.venues)
            b.is_cross_confirmed = b.venue_count >= 2
            if b.venue_count >= 3:
                b.confirmation_tier = "TRIPLE_CONFIRMED"
            elif b.venue_count == 2:
                b.confirmation_tier = "DUAL_CONFIRMED"
            else:
                b.confirmation_tier = "SINGLE_VENUE"

        # Sort bids descending, asks ascending
        sorted_bids = sorted(bids_map.values(), key=lambda x: x.bucket_price, reverse=True)
        sorted_asks = sorted(asks_map.values(), key=lambda x: x.bucket_price, reverse=False)

        # Baseline depth calculation for wall threshold
        avg_bid_notional = (sum(b.total_notional_usdt for b in sorted_bids) / len(sorted_bids)) if sorted_bids else 10000.0
        avg_ask_notional = (sum(a.total_notional_usdt for a in sorted_asks) / len(sorted_asks)) if sorted_asks else 10000.0

        dynamic_bid_wall_thresh = max(self.wall_notional_threshold, avg_bid_notional * self.wall_multiple_threshold)
        dynamic_ask_wall_thresh = max(self.wall_notional_threshold, avg_ask_notional * self.wall_multiple_threshold)

        # Track persistent resting walls
        active_wall_keys: Set[str] = set()

        for b in sorted_bids:
            if b.total_notional_usdt >= dynamic_bid_wall_thresh:
                wall_key = f"{clean_symbol}_BID_{b.bucket_price:.4f}"
                active_wall_keys.add(wall_key)
                if wall_key in self._persistent_walls:
                    w = self._persistent_walls[wall_key]
                    w.last_seen_timestamp_ms = now_ms
                    w.notional_usdt = b.total_notional_usdt
                    w.distance_bps = b.distance_bps
                    w.venues = list(b.venues.keys())
                    w.venue_count = b.venue_count
                    w.is_cross_confirmed = b.is_cross_confirmed
                    w.confirmation_tier = b.confirmation_tier
                    w.persistence_seconds = (now_ms - w.first_seen_timestamp_ms) / 1000.0
                else:
                    self._persistent_walls[wall_key] = PersistentRestingWall(
                        wall_id=wall_key,
                        symbol=clean_symbol,
                        side="BID",
                        price=b.bucket_price,
                        notional_usdt=b.total_notional_usdt,
                        distance_bps=b.distance_bps,
                        venues=list(b.venues.keys()),
                        venue_count=b.venue_count,
                        is_cross_confirmed=b.is_cross_confirmed,
                        confirmation_tier=b.confirmation_tier,
                        first_seen_timestamp_ms=now_ms,
                        last_seen_timestamp_ms=now_ms,
                        persistence_seconds=0.0,
                    )

        for a in sorted_asks:
            if a.total_notional_usdt >= dynamic_ask_wall_thresh:
                wall_key = f"{clean_symbol}_ASK_{a.bucket_price:.4f}"
                active_wall_keys.add(wall_key)
                if wall_key in self._persistent_walls:
                    w = self._persistent_walls[wall_key]
                    w.last_seen_timestamp_ms = now_ms
                    w.notional_usdt = a.total_notional_usdt
                    w.distance_bps = a.distance_bps
                    w.venues = list(a.venues.keys())
                    w.venue_count = a.venue_count
                    w.is_cross_confirmed = a.is_cross_confirmed
                    w.confirmation_tier = a.confirmation_tier
                    w.persistence_seconds = (now_ms - w.first_seen_timestamp_ms) / 1000.0
                else:
                    self._persistent_walls[wall_key] = PersistentRestingWall(
                        wall_id=wall_key,
                        symbol=clean_symbol,
                        side="ASK",
                        price=a.bucket_price,
                        notional_usdt=a.total_notional_usdt,
                        distance_bps=a.distance_bps,
                        venues=list(a.venues.keys()),
                        venue_count=a.venue_count,
                        is_cross_confirmed=a.is_cross_confirmed,
                        confirmation_tier=a.confirmation_tier,
                        first_seen_timestamp_ms=now_ms,
                        last_seen_timestamp_ms=now_ms,
                        persistence_seconds=0.0,
                    )

        # Purge stale persistent walls not seen for > 120 seconds
        if now_ms - self._last_purge_ms > 30000:
            stale_keys = [
                k for k, w in self._persistent_walls.items()
                if (now_ms - w.last_seen_timestamp_ms) > 120000
            ]
            for sk in stale_keys:
                del self._persistent_walls[sk]
            self._last_purge_ms = now_ms

        active_walls = [
            w for w in self._persistent_walls.values()
            if w.symbol == clean_symbol and w.wall_id in active_wall_keys
        ]
        # Sort walls by notional descending
        active_walls.sort(key=lambda x: x.notional_usdt, reverse=True)

        result = {
            "symbol": clean_symbol,
            "contract_type": "PERPETUAL_LINEAR",
            "verified_contract": True,
            "status": "AVAILABLE",
            "venue_filter": venue_filter,
            "timestamp_ms": now_ms,
            "composite_mid": round(composite_mid, 4),
            "bucket_bps": bucket_bps,
            "venue_summaries": {k: v.to_dict() for k, v in venue_summaries.items()},
            "bids_buckets": [b.to_dict() for b in sorted_bids[:40]],
            "asks_buckets": [a.to_dict() for a in sorted_asks[:40]],
            "persistent_walls": [w.to_dict() for w in active_walls],
            "distinction_disclosure": (
                "ORDER_BOOK_DEPTH: Visible resting maker orders on AsterDEX V3, Binance USD(S)-M Futures, "
                "and Bybit Linear. DISTINCT from historical structural liquidity (BSL/SSL) and estimated liquidation clusters."
            ),
        }
        self._heatmap_cache[cache_key] = (now_sec, result)
        return result
