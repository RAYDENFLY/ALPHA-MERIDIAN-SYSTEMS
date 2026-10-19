"""Whale & Large-Order Activity Detector for AsterDEX Perpetuals.

Detects institutional resting depth, aggressive executions, wall replenishment,
pre-touch spoof cancellations, absorption zones, and open interest anomalies
across 10 distinct event categories with adaptive instrument baselines.

Strictly READ-ONLY telemetry and market data analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from src.core.logger import logger
from src.exchange.normalized import NormalizedOrderBook, NormalizedTrade


class WhaleEventType(str, Enum):
    # Phase 2 Resting-order events
    LARGE_BID_DETECTED = "LARGE_BID_DETECTED"
    LARGE_BID_PERSISTING = "LARGE_BID_PERSISTING"
    LARGE_BID_REPLENISHED = "LARGE_BID_REPLENISHED"
    LARGE_BID_REDUCED = "LARGE_BID_REDUCED"
    LARGE_BID_CANCELLED = "LARGE_BID_CANCELLED"
    LARGE_ASK_DETECTED = "LARGE_ASK_DETECTED"
    LARGE_ASK_PERSISTING = "LARGE_ASK_PERSISTING"
    LARGE_ASK_REPLENISHED = "LARGE_ASK_REPLENISHED"
    LARGE_ASK_REDUCED = "LARGE_ASK_REDUCED"
    LARGE_ASK_CANCELLED = "LARGE_ASK_CANCELLED"

    # Phase 2 Executed-flow events
    LARGE_AGGRESSIVE_BUY = "LARGE_AGGRESSIVE_BUY"
    LARGE_AGGRESSIVE_SELL = "LARGE_AGGRESSIVE_SELL"
    BUY_ABSORPTION_CANDIDATE = "BUY_ABSORPTION_CANDIDATE"
    SELL_ABSORPTION_CANDIDATE = "SELL_ABSORPTION_CANDIDATE"
    CVD_BUY_PRESSURE = "CVD_BUY_PRESSURE"
    CVD_SELL_PRESSURE = "CVD_SELL_PRESSURE"

    # Backward-compatible existing names
    LARGE_RESTING_BID = "LARGE_RESTING_BID"
    LARGE_RESTING_ASK = "LARGE_RESTING_ASK"
    WALL_REPLENISHMENT = "WALL_REPLENISHMENT"
    PRE_TOUCH_CANCELLATION = "PRE_TOUCH_CANCELLATION"
    ABSORPTION_CANDIDATE = "ABSORPTION_CANDIDATE"
    DEPTH_IMBALANCE_SHIFT = "DEPTH_IMBALANCE_SHIFT"
    OI_EXPANSION_PRICE_FLAT = "OI_EXPANSION_PRICE_FLAT"
    OI_CONTRACTION_VOLUME_SPIKE = "OI_CONTRACTION_VOLUME_SPIKE"


@dataclass
class WhaleActivityEvent:
    """Individual detected large-order or institutional tape event."""
    event_id: str
    timestamp_ms: int
    symbol: str
    event_type: WhaleEventType
    side: str                          # "BUY", "SELL", "BID", "ASK", "NEUTRAL"
    price: float
    size: float
    notional_usdt: float
    distance_bps: float                # Basis points from current mark/mid price
    baseline_multiple: float           # Multiplier above typical instrument depth
    classification_tags: List[str]     # e.g. ["INSTITUTIONAL_WALL", "SPOOF_RISK", "ABSORPTION"]
    details: str
    venue: str = "asterdex"
    original_symbol: str = ""
    event_class: str = "RESTING"       # "RESTING", "EXECUTED", "DERIVATIVES"
    persistence_seconds: float = 0.0
    lifecycle_state: str = "ACTIVE"    # "ACTIVE", "PERSISTING", "REPLENISHED", "REDUCED", "CANCELLED", "CONSUMED", "EXPIRED"
    is_spoof_candidate: bool = False
    mark_price_at_event: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp_ms": self.timestamp_ms,
            "timestamp_utc": datetime.fromtimestamp(self.timestamp_ms / 1000, tz=timezone.utc).isoformat(),
            "venue": self.venue,
            "symbol": self.symbol,
            "original_symbol": self.original_symbol or self.symbol,
            "event_type": self.event_type.value,
            "event_class": self.event_class,
            "side": self.side,
            "price": round(self.price, 4),
            "size": round(self.size, 4),
            "notional_usdt": round(self.notional_usdt, 2),
            "distance_bps": round(self.distance_bps, 1),
            "baseline_multiple": round(self.baseline_multiple, 2),
            "persistence_seconds": round(self.persistence_seconds, 1),
            "lifecycle_state": self.lifecycle_state,
            "is_spoof_candidate": self.is_spoof_candidate,
            "mark_price_at_event": round(self.mark_price_at_event or self.price, 4),
            "classification_tags": self.classification_tags,
            "details": self.details,
        }


@dataclass
class ActiveTrackedWall:
    """Internal tracker for resting depth wall lifecycle."""
    wall_id: str
    symbol: str
    side: str
    price: float
    notional_usdt: float
    first_seen_ms: int
    last_seen_ms: int
    min_distance_bps: float
    is_active: bool = True
    cancellation_detected: bool = False
    is_spoof_candidate: bool = False
    venue: str = "asterdex"
    has_emitted_persisting: bool = False

    @property
    def persistence_seconds(self) -> float:
        return (self.last_seen_ms - self.first_seen_ms) / 1000.0 if self.last_seen_ms >= self.first_seen_ms else 0.0



@dataclass
class WhaleActivitySummary:
    """Aggregated large-order profile and directional flow bias for a symbol."""
    symbol: str
    mark_price: float
    baseline_depth_usdt: float
    recent_events: List[WhaleActivityEvent]
    large_bid_notional_usdt: float
    large_ask_notional_usdt: float
    net_whale_bias: str                # "BULLISH_FLOW", "BEARISH_FLOW", "BALANCED"
    spoof_cancellation_count: int
    absorption_detected: bool
    active_walls_count: int
    whale_buy_score: float = 0.0
    whale_sell_score: float = 0.0
    net_whale_score: float = 0.0
    analyzed_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mark_price": round(self.mark_price, 4),
            "baseline_depth_usdt": round(self.baseline_depth_usdt, 2),
            "recent_events": [e.to_dict() for e in self.recent_events],
            "large_bid_notional_usdt": round(self.large_bid_notional_usdt, 2),
            "large_ask_notional_usdt": round(self.large_ask_notional_usdt, 2),
            "net_whale_bias": self.net_whale_bias,
            "spoof_cancellation_count": self.spoof_cancellation_count,
            "absorption_detected": self.absorption_detected,
            "active_walls_count": self.active_walls_count,
            "whale_buy_score": round(self.whale_buy_score, 1),
            "whale_sell_score": round(self.whale_sell_score, 1),
            "net_whale_score": round(self.net_whale_score, 1),
            "analyzed_at_utc": self.analyzed_at_utc,
        }



class WhaleDetector:
    """Monitors L2 order books, trades, and OI dynamics for institutional activity."""

    DEFAULT_WALL_MULTIPLE = 2.5       # Multiplier of baseline for large resting order
    DEFAULT_TRADE_MULTIPLE = 3.0      # Multiplier of baseline for large aggressive trade
    SPOOF_PROXIMITY_BPS = 15.0        # Pulled within 15 bps of price -> spoof flag

    def __init__(
        self,
        wall_multiple: float = DEFAULT_WALL_MULTIPLE,
        trade_multiple: float = DEFAULT_TRADE_MULTIPLE,
        spoof_proximity_bps: float = SPOOF_PROXIMITY_BPS,
    ):
        self.wall_multiple = wall_multiple
        self.trade_multiple = trade_multiple
        self.spoof_proximity_bps = spoof_proximity_bps

        # Internal state tracking: symbol -> state
        self._tracked_walls: Dict[str, Dict[str, ActiveTrackedWall]] = {}
        self._events_history: Dict[str, List[WhaleActivityEvent]] = {}
        self._baseline_depths: Dict[str, float] = {}
        self._last_imbalances: Dict[str, float] = {}
        self._last_oi_snapshots: Dict[str, Tuple[float, float, int]] = {} # (oi_contracts, mark_price, ts_ms)

    def resolve_baseline_depth(
        self,
        symbol: str,
        volume_24h_usdt: Optional[float] = None,
        book: Optional[NormalizedOrderBook] = None,
    ) -> float:
        """Resolve adaptive baseline depth (typical 10 bps resting notional) for instrument."""
        sym = symbol.upper()
        if sym in self._baseline_depths:
            return self._baseline_depths[sym]

        # Adaptive heuristic: 10 bps depth is approximately 0.015% to 0.03% of 24h turnover
        if volume_24h_usdt and volume_24h_usdt > 0:
            if volume_24h_usdt >= 50_000_000.0:
                base = 100_000.0  # Mega-cap
            elif volume_24h_usdt >= 5_000_000.0:
                base = 30_000.0   # Mid-cap
            elif volume_24h_usdt >= 500_000.0:
                base = 10_000.0   # Small-cap
            else:
                base = 3_000.0    # Micro-cap / new listing
        elif book and (book.bids or book.asks):
            # Calculate from actual top 5 book levels
            bid_notional = sum(b.price * b.size for b in book.bids[:5])
            ask_notional = sum(a.price * a.size for a in book.asks[:5])
            avg_top5 = (bid_notional + ask_notional) / 2.0
            base = max(2_000.0, avg_top5 / 3.0)
        else:
            base = 25_000.0

        self._baseline_depths[sym] = base
        return base

    def analyze_market_data(
        self,
        symbol: str,
        book: Optional[NormalizedOrderBook] = None,
        trades: Optional[List[NormalizedTrade]] = None,
        current_oi_contracts: Optional[float] = None,
        mark_price: Optional[float] = None,
        volume_24h_usdt: Optional[float] = None,
        now_ms: Optional[int] = None,
    ) -> WhaleActivitySummary:
        """Analyze order book, trade prints, and open interest for institutional signatures."""
        sym = symbol.upper()
        now_ts = now_ms if now_ms is not None else int(time.time() * 1000)
        baseline = self.resolve_baseline_depth(sym, volume_24h_usdt=volume_24h_usdt, book=book)

        active_walls = self._tracked_walls.setdefault(sym, {})
        events_list = self._events_history.setdefault(sym, [])
        new_events: List[WhaleActivityEvent] = []

        venue = book.exchange_name if book else (trades[0].exchange_name if trades else "asterdex")
        mid_price = mark_price or (book.mid_price if book else 0.0)
        if mid_price <= 0 and book and (book.bids or book.asks):
            best_bid = book.bids[0].price if book.bids else 1.0
            best_ask = book.asks[0].price if book.asks else best_bid
            mid_price = (best_bid + best_ask) / 2.0

        current_bid_walls_notional = 0.0
        current_ask_walls_notional = 0.0
        active_walls_count = 0
        spoof_count = 0
        absorption_found = False

        # ---------------------------------------------------------------------
        # 1. Inspect L2 Order Book Depth (Resting Bids & Asks, Walls)
        # ---------------------------------------------------------------------
        seen_wall_keys = set()
        if book and mid_price > 0:
            # Check Bids
            for level in book.bids[:20]:
                notional = level.price * level.size
                multiple = notional / baseline
                if multiple >= self.wall_multiple:
                    dist_bps = ((mid_price - level.price) / mid_price) * 10000.0
                    wall_key = f"BID_{int(level.price * 1000)}"
                    seen_wall_keys.add(wall_key)
                    current_bid_walls_notional += notional

                    if wall_key not in active_walls:
                        # New Large Resting Bid
                        active_walls[wall_key] = ActiveTrackedWall(
                            wall_id=wall_key,
                            symbol=sym,
                            side="BID",
                            price=level.price,
                            notional_usdt=notional,
                            first_seen_ms=now_ts,
                            last_seen_ms=now_ts,
                            min_distance_bps=dist_bps,
                            venue=venue,
                        )
                        # Legacy event for existing tests
                        new_events.append(
                            WhaleActivityEvent(
                                event_id=f"EV_BID_{sym}_{now_ts}_{int(level.price)}",
                                timestamp_ms=now_ts,
                                symbol=sym,
                                event_type=WhaleEventType.LARGE_RESTING_BID,
                                side="BID",
                                price=level.price,
                                size=level.size,
                                notional_usdt=notional,
                                distance_bps=dist_bps,
                                baseline_multiple=multiple,
                                classification_tags=["INSTITUTIONAL_WALL", "SUPPORT_DEFENSE", "LARGE_BID_DETECTED"],
                                details=f"Large resting bid wall of ${notional:,.2f} ({multiple:.1f}x baseline) placed at ${level.price:,.4f} ({dist_bps:.1f} bps below mid).",
                                venue=venue,
                                event_class="RESTING",
                                mark_price_at_event=mid_price,
                            )
                        )
                        # Phase 2 explicit resting event
                        new_events.append(
                            WhaleActivityEvent(
                                event_id=f"EV_BID_DET_{sym}_{now_ts}_{int(level.price)}",
                                timestamp_ms=now_ts,
                                symbol=sym,
                                event_type=WhaleEventType.LARGE_BID_DETECTED,
                                side="BID",
                                price=level.price,
                                size=level.size,
                                notional_usdt=notional,
                                distance_bps=dist_bps,
                                baseline_multiple=multiple,
                                classification_tags=["INSTITUTIONAL_WALL", "LARGE_BID_DETECTED"],
                                details=f"Large resting bid detected: ${notional:,.2f} ({multiple:.1f}x baseline) at ${level.price:,.4f}.",
                                venue=venue,
                                event_class="RESTING",
                                mark_price_at_event=mid_price,
                            )
                        )
                    else:
                        tracked = active_walls[wall_key]
                        tracked.last_seen_ms = now_ts
                        tracked.min_distance_bps = min(tracked.min_distance_bps, dist_bps)
                        persist_sec = (now_ts - tracked.first_seen_ms) / 1000.0

                        # Check Wall Persistence (>= 15 seconds)
                        if persist_sec >= 15.0 and not tracked.has_emitted_persisting:
                            tracked.has_emitted_persisting = True
                            new_events.append(
                                WhaleActivityEvent(
                                    event_id=f"EV_BID_PERSIST_{sym}_{now_ts}_{int(level.price)}",
                                    timestamp_ms=now_ts,
                                    symbol=sym,
                                    event_type=WhaleEventType.LARGE_BID_PERSISTING,
                                    side="BID",
                                    price=level.price,
                                    size=level.size,
                                    notional_usdt=notional,
                                    distance_bps=dist_bps,
                                    baseline_multiple=multiple,
                                    persistence_seconds=persist_sec,
                                    classification_tags=["PERSISTENT_WALL", "BID_SUPPORT"],
                                    details=f"Resting bid wall at ${level.price:,.4f} persisting for {persist_sec:.1f}s ({multiple:.1f}x baseline).",
                                    venue=venue,
                                    event_class="RESTING",
                                    mark_price_at_event=mid_price,
                                )
                            )

                        # Check Wall Replenishment (+20% size increase)
                        if notional > tracked.notional_usdt * 1.2:
                            new_events.append(
                                WhaleActivityEvent(
                                    event_id=f"EV_REPL_{sym}_{now_ts}_{int(level.price)}",
                                    timestamp_ms=now_ts,
                                    symbol=sym,
                                    event_type=WhaleEventType.WALL_REPLENISHMENT,
                                    side="BID",
                                    price=level.price,
                                    size=level.size,
                                    notional_usdt=notional,
                                    distance_bps=dist_bps,
                                    baseline_multiple=multiple,
                                    classification_tags=["WALL_REPLENISHED", "BID_SUPPORT"],
                                    details=f"Bid wall at ${level.price:,.4f} replenished by ${(notional - tracked.notional_usdt):,.2f}.",
                                    venue=venue,
                                    event_class="RESTING",
                                    mark_price_at_event=mid_price,
                                )
                            )
                            new_events.append(
                                WhaleActivityEvent(
                                    event_id=f"EV_BID_REPL_{sym}_{now_ts}_{int(level.price)}",
                                    timestamp_ms=now_ts,
                                    symbol=sym,
                                    event_type=WhaleEventType.LARGE_BID_REPLENISHED,
                                    side="BID",
                                    price=level.price,
                                    size=level.size,
                                    notional_usdt=notional,
                                    distance_bps=dist_bps,
                                    baseline_multiple=multiple,
                                    classification_tags=["WALL_REPLENISHED", "BID_SUPPORT"],
                                    details=f"Large bid replenished: ${(notional - tracked.notional_usdt):,.2f} added to ${level.price:,.4f}.",
                                    venue=venue,
                                    event_class="RESTING",
                                    mark_price_at_event=mid_price,
                                )
                            )
                        # Check Wall Reduction (-30% size decrease without disappearing)
                        elif notional < tracked.notional_usdt * 0.7:
                            new_events.append(
                                WhaleActivityEvent(
                                    event_id=f"EV_BID_RED_{sym}_{now_ts}_{int(level.price)}",
                                    timestamp_ms=now_ts,
                                    symbol=sym,
                                    event_type=WhaleEventType.LARGE_BID_REDUCED,
                                    side="BID",
                                    price=level.price,
                                    size=level.size,
                                    notional_usdt=notional,
                                    distance_bps=dist_bps,
                                    baseline_multiple=multiple,
                                    classification_tags=["WALL_REDUCED"],
                                    details=f"Large bid at ${level.price:,.4f} reduced from ${tracked.notional_usdt:,.2f} to ${notional:,.2f}.",
                                    venue=venue,
                                    event_class="RESTING",
                                    mark_price_at_event=mid_price,
                                )
                            )
                        tracked.notional_usdt = notional

            # Check Asks
            for level in book.asks[:20]:
                notional = level.price * level.size
                multiple = notional / baseline
                if multiple >= self.wall_multiple:
                    dist_bps = ((level.price - mid_price) / mid_price) * 10000.0
                    wall_key = f"ASK_{int(level.price * 1000)}"
                    seen_wall_keys.add(wall_key)
                    current_ask_walls_notional += notional

                    if wall_key not in active_walls:
                        active_walls[wall_key] = ActiveTrackedWall(
                            wall_id=wall_key,
                            symbol=sym,
                            side="ASK",
                            price=level.price,
                            notional_usdt=notional,
                            first_seen_ms=now_ts,
                            last_seen_ms=now_ts,
                            min_distance_bps=dist_bps,
                            venue=venue,
                        )
                        # Legacy ask event
                        new_events.append(
                            WhaleActivityEvent(
                                event_id=f"EV_ASK_{sym}_{now_ts}_{int(level.price)}",
                                timestamp_ms=now_ts,
                                symbol=sym,
                                event_type=WhaleEventType.LARGE_RESTING_ASK,
                                side="ASK",
                                price=level.price,
                                size=level.size,
                                notional_usdt=notional,
                                distance_bps=dist_bps,
                                baseline_multiple=multiple,
                                classification_tags=["INSTITUTIONAL_WALL", "RESISTANCE_CAP", "LARGE_ASK_DETECTED"],
                                details=f"Large resting ask wall of ${notional:,.2f} ({multiple:.1f}x baseline) placed at ${level.price:,.4f} ({dist_bps:.1f} bps above mid).",
                                venue=venue,
                                event_class="RESTING",
                                mark_price_at_event=mid_price,
                            )
                        )
                        # Phase 2 explicit ask event
                        new_events.append(
                            WhaleActivityEvent(
                                event_id=f"EV_ASK_DET_{sym}_{now_ts}_{int(level.price)}",
                                timestamp_ms=now_ts,
                                symbol=sym,
                                event_type=WhaleEventType.LARGE_ASK_DETECTED,
                                side="ASK",
                                price=level.price,
                                size=level.size,
                                notional_usdt=notional,
                                distance_bps=dist_bps,
                                baseline_multiple=multiple,
                                classification_tags=["INSTITUTIONAL_WALL", "LARGE_ASK_DETECTED"],
                                details=f"Large resting ask detected: ${notional:,.2f} ({multiple:.1f}x baseline) at ${level.price:,.4f}.",
                                venue=venue,
                                event_class="RESTING",
                                mark_price_at_event=mid_price,
                            )
                        )
                    else:
                        tracked = active_walls[wall_key]
                        tracked.last_seen_ms = now_ts
                        tracked.min_distance_bps = min(tracked.min_distance_bps, dist_bps)
                        persist_sec = (now_ts - tracked.first_seen_ms) / 1000.0

                        # Check Ask Persistence (>= 15 seconds)
                        if persist_sec >= 15.0 and not tracked.has_emitted_persisting:
                            tracked.has_emitted_persisting = True
                            new_events.append(
                                WhaleActivityEvent(
                                    event_id=f"EV_ASK_PERSIST_{sym}_{now_ts}_{int(level.price)}",
                                    timestamp_ms=now_ts,
                                    symbol=sym,
                                    event_type=WhaleEventType.LARGE_ASK_PERSISTING,
                                    side="ASK",
                                    price=level.price,
                                    size=level.size,
                                    notional_usdt=notional,
                                    distance_bps=dist_bps,
                                    baseline_multiple=multiple,
                                    persistence_seconds=persist_sec,
                                    classification_tags=["PERSISTENT_WALL", "ASK_CAP"],
                                    details=f"Resting ask wall at ${level.price:,.4f} persisting for {persist_sec:.1f}s ({multiple:.1f}x baseline).",
                                    venue=venue,
                                    event_class="RESTING",
                                    mark_price_at_event=mid_price,
                                )
                            )

                        if notional > tracked.notional_usdt * 1.2:
                            new_events.append(
                                WhaleActivityEvent(
                                    event_id=f"EV_REPL_{sym}_{now_ts}_{int(level.price)}",
                                    timestamp_ms=now_ts,
                                    symbol=sym,
                                    event_type=WhaleEventType.WALL_REPLENISHMENT,
                                    side="ASK",
                                    price=level.price,
                                    size=level.size,
                                    notional_usdt=notional,
                                    distance_bps=dist_bps,
                                    baseline_multiple=multiple,
                                    classification_tags=["WALL_REPLENISHED", "ASK_CAP"],
                                    details=f"Ask wall at ${level.price:,.4f} replenished by ${(notional - tracked.notional_usdt):,.2f}.",
                                    venue=venue,
                                    event_class="RESTING",
                                    mark_price_at_event=mid_price,
                                )
                            )
                            new_events.append(
                                WhaleActivityEvent(
                                    event_id=f"EV_ASK_REPL_{sym}_{now_ts}_{int(level.price)}",
                                    timestamp_ms=now_ts,
                                    symbol=sym,
                                    event_type=WhaleEventType.LARGE_ASK_REPLENISHED,
                                    side="ASK",
                                    price=level.price,
                                    size=level.size,
                                    notional_usdt=notional,
                                    distance_bps=dist_bps,
                                    baseline_multiple=multiple,
                                    classification_tags=["WALL_REPLENISHED", "ASK_CAP"],
                                    details=f"Large ask replenished: ${(notional - tracked.notional_usdt):,.2f} added to ${level.price:,.4f}.",
                                    venue=venue,
                                    event_class="RESTING",
                                    mark_price_at_event=mid_price,
                                )
                            )
                        elif notional < tracked.notional_usdt * 0.7:
                            new_events.append(
                                WhaleActivityEvent(
                                    event_id=f"EV_ASK_RED_{sym}_{now_ts}_{int(level.price)}",
                                    timestamp_ms=now_ts,
                                    symbol=sym,
                                    event_type=WhaleEventType.LARGE_ASK_REDUCED,
                                    side="ASK",
                                    price=level.price,
                                    size=level.size,
                                    notional_usdt=notional,
                                    distance_bps=dist_bps,
                                    baseline_multiple=multiple,
                                    classification_tags=["WALL_REDUCED"],
                                    details=f"Large ask at ${level.price:,.4f} reduced from ${tracked.notional_usdt:,.2f} to ${notional:,.2f}.",
                                    venue=venue,
                                    event_class="RESTING",
                                    mark_price_at_event=mid_price,
                                )
                            )
                        tracked.notional_usdt = notional

            # Check for Cancellations on walls that vanished
            missing_walls = [k for k in active_walls if k not in seen_wall_keys and active_walls[k].is_active]
            for m_key in missing_walls:
                wall = active_walls[m_key]
                wall.is_active = False
                wall.cancellation_detected = True
                wall_persist_sec = (now_ts - wall.first_seen_ms) / 1000.0

                cancel_ev_type = (
                    WhaleEventType.LARGE_BID_CANCELLED
                    if wall.side == "BID"
                    else WhaleEventType.LARGE_ASK_CANCELLED
                )

                # Neutral pre-touch cancellation flag if price was close (<= spoof_proximity_bps)
                if wall.min_distance_bps <= self.spoof_proximity_bps:
                    wall.is_spoof_candidate = True
                    spoof_count += 1
                    mult = wall.notional_usdt / baseline
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_SPOOF_{sym}_{now_ts}_{int(wall.price)}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=WhaleEventType.PRE_TOUCH_CANCELLATION,
                            side=wall.side,
                            price=wall.price,
                            size=wall.notional_usdt / (wall.price or 1.0),
                            notional_usdt=wall.notional_usdt,
                            distance_bps=wall.min_distance_bps,
                            baseline_multiple=mult,
                            persistence_seconds=wall_persist_sec,
                            lifecycle_state="CANCELLED",
                            is_spoof_candidate=True,
                            classification_tags=["PRE_TOUCH_CANCELLATION", "SPOOF_RISK", "ORDER_PULLED"],
                            details=f"Resting {wall.side} wall of ${wall.notional_usdt:,.2f} pulled when mark approached within {wall.min_distance_bps:.1f} bps (pre-touch cancellation).",
                            venue=wall.venue,
                            event_class="RESTING",
                            mark_price_at_event=mid_price,
                        )
                    )
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_CAN_{sym}_{now_ts}_{int(wall.price)}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=cancel_ev_type,
                            side=wall.side,
                            price=wall.price,
                            size=wall.notional_usdt / (wall.price or 1.0),
                            notional_usdt=wall.notional_usdt,
                            distance_bps=wall.min_distance_bps,
                            baseline_multiple=wall.notional_usdt / baseline,
                            persistence_seconds=wall_persist_sec,
                            lifecycle_state="CANCELLED",
                            is_spoof_candidate=True,
                            classification_tags=["ORDER_CANCELLED", "PRE_TOUCH"],
                            details=f"Resting {wall.side} of ${wall.notional_usdt:,.2f} cancelled as price approached within {wall.min_distance_bps:.1f} bps.",
                            venue=wall.venue,
                            event_class="RESTING",
                            mark_price_at_event=mid_price,
                        )
                    )
                else:
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_CAN_{sym}_{now_ts}_{int(wall.price)}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=cancel_ev_type,
                            side=wall.side,
                            price=wall.price,
                            size=wall.notional_usdt / (wall.price or 1.0),
                            notional_usdt=wall.notional_usdt,
                            distance_bps=wall.min_distance_bps,
                            baseline_multiple=wall.notional_usdt / baseline,
                            persistence_seconds=wall_persist_sec,
                            lifecycle_state="CANCELLED",
                            is_spoof_candidate=False,
                            classification_tags=["ORDER_CANCELLED"],
                            details=f"Resting {wall.side} of ${wall.notional_usdt:,.2f} cancelled at {wall.min_distance_bps:.1f} bps away.",
                            venue=wall.venue,
                            event_class="RESTING",
                            mark_price_at_event=mid_price,
                        )
                    )

            # Check Depth Imbalance Shift in Top 10 levels
            top_bids_notional = sum(b.price * b.size for b in book.bids[:10])
            top_asks_notional = sum(a.price * a.size for a in book.asks[:10])
            tot_depth = top_bids_notional + top_asks_notional
            if tot_depth > 0:
                current_imbalance = (top_bids_notional - top_asks_notional) / tot_depth  # -1.0 to +1.0
                prev_imbalance = self._last_imbalances.get(sym)
                if prev_imbalance is not None and abs(current_imbalance - prev_imbalance) >= 0.45:
                    shift_dir = "BULLISH" if current_imbalance > prev_imbalance else "BEARISH"
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_IMB_{sym}_{now_ts}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=WhaleEventType.DEPTH_IMBALANCE_SHIFT,
                            side="BUY" if shift_dir == "BULLISH" else "SELL",
                            price=mid_price,
                            size=0.0,
                            notional_usdt=abs(top_bids_notional - top_asks_notional),
                            distance_bps=0.0,
                            baseline_multiple=1.0,
                            classification_tags=["IMBALANCE_SHIFT", f"{shift_dir}_LIQUIDITY"],
                            details=f"Sharp order book depth ratio shift from {prev_imbalance:+.2f} to {current_imbalance:+.2f} ({shift_dir}).",
                            venue=venue,
                            event_class="RESTING",
                            mark_price_at_event=mid_price,
                        )
                    )
                self._last_imbalances[sym] = current_imbalance

        active_walls_count = sum(1 for w in active_walls.values() if w.is_active)

        # ---------------------------------------------------------------------
        # 2. Inspect Aggressive Trades, CVD Pressure & Absorption
        # ---------------------------------------------------------------------
        buy_trades_notional = 0.0
        sell_trades_notional = 0.0
        large_taker_buy_notional = 0.0
        large_taker_sell_notional = 0.0

        if trades and mid_price > 0:
            trade_venue = trades[0].exchange_name if trades else venue
            price_volume_clusters: Dict[int, float] = {}

            for t in trades[-100:]:
                if t.side == "BUY":
                    buy_trades_notional += t.notional_usdt
                elif t.side == "SELL":
                    sell_trades_notional += t.notional_usdt

                multiple = t.notional_usdt / baseline
                if multiple >= self.trade_multiple:
                    if t.side == "BUY":
                        large_taker_buy_notional += t.notional_usdt
                        ev_type = WhaleEventType.LARGE_AGGRESSIVE_BUY
                    else:
                        large_taker_sell_notional += t.notional_usdt
                        ev_type = WhaleEventType.LARGE_AGGRESSIVE_SELL

                    dist_bps = abs(t.price - mid_price) / mid_price * 10000.0
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_TRD_{sym}_{t.timestamp_ms}_{t.trade_id}",
                            timestamp_ms=t.timestamp_ms,
                            symbol=sym,
                            event_type=ev_type,
                            side=t.side,
                            price=t.price,
                            size=t.size,
                            notional_usdt=t.notional_usdt,
                            distance_bps=dist_bps,
                            baseline_multiple=multiple,
                            classification_tags=["AGGRESSIVE_TAKER", f"LARGE_{t.side}"],
                            details=f"Large aggressive market {t.side} print of ${t.notional_usdt:,.2f} ({multiple:.1f}x baseline) executed at ${t.price:,.4f}.",
                            venue=trade_venue,
                            event_class="EXECUTED",
                            mark_price_at_event=mid_price,
                        )
                    )

                # Cluster volume at nearest 0.1% price bucket
                bucket = int(t.price * 1000)
                price_volume_clusters[bucket] = price_volume_clusters.get(bucket, 0.0) + t.notional_usdt

            # Rolling CVD Momentum Check across recent trades
            tot_flow = buy_trades_notional + sell_trades_notional
            if tot_flow >= baseline * 2.0:
                if buy_trades_notional >= sell_trades_notional * 2.5:
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_CVD_BUY_{sym}_{now_ts}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=WhaleEventType.CVD_BUY_PRESSURE,
                            side="BUY",
                            price=mid_price,
                            size=0.0,
                            notional_usdt=buy_trades_notional - sell_trades_notional,
                            distance_bps=0.0,
                            baseline_multiple=(buy_trades_notional - sell_trades_notional) / baseline,
                            classification_tags=["CVD_MOMENTUM", "AGGRESSIVE_BUY_PRESSURE"],
                            details=f"Decisive CVD buy skew: ${buy_trades_notional:,.2f} buy vs ${sell_trades_notional:,.2f} sell across last trades.",
                            venue=trade_venue,
                            event_class="EXECUTED",
                            mark_price_at_event=mid_price,
                        )
                    )
                elif sell_trades_notional >= buy_trades_notional * 2.5:
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_CVD_SELL_{sym}_{now_ts}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=WhaleEventType.CVD_SELL_PRESSURE,
                            side="SELL",
                            price=mid_price,
                            size=0.0,
                            notional_usdt=sell_trades_notional - buy_trades_notional,
                            distance_bps=0.0,
                            baseline_multiple=(sell_trades_notional - buy_trades_notional) / baseline,
                            classification_tags=["CVD_MOMENTUM", "AGGRESSIVE_SELL_PRESSURE"],
                            details=f"Decisive CVD sell skew: ${sell_trades_notional:,.2f} sell vs ${buy_trades_notional:,.2f} buy across last trades.",
                            venue=trade_venue,
                            event_class="EXECUTED",
                            mark_price_at_event=mid_price,
                        )
                    )

            # Absorption detection: high volume cluster without price displacement
            for bucket, vol in price_volume_clusters.items():
                if vol >= baseline * 3.5:
                    cluster_price = bucket / 1000.0
                    absorption_found = True
                    is_buy_absorb = cluster_price <= mid_price
                    specific_absorb = (
                        WhaleEventType.BUY_ABSORPTION_CANDIDATE
                        if is_buy_absorb
                        else WhaleEventType.SELL_ABSORPTION_CANDIDATE
                    )
                    absorb_side = "BUY" if is_buy_absorb else "SELL"
                    # Legacy absorption event
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_ABS_{sym}_{now_ts}_{bucket}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=WhaleEventType.ABSORPTION_CANDIDATE,
                            side=absorb_side,
                            price=cluster_price,
                            size=vol / cluster_price,
                            notional_usdt=vol,
                            distance_bps=abs(cluster_price - mid_price) / mid_price * 10000.0,
                            baseline_multiple=vol / baseline,
                            classification_tags=["PASSIVE_ABSORPTION", "HIGH_VOLUME_NODE", specific_absorb.value],
                            details=f"Passive liquidity absorption detected: ${vol:,.2f} traded tightly at ~${cluster_price:,.4f} with minimal displacement.",
                            venue=trade_venue,
                            event_class="EXECUTED",
                            mark_price_at_event=mid_price,
                        )
                    )
                    # Phase 2 directional absorption event
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_ABS_DIR_{sym}_{now_ts}_{bucket}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=specific_absorb,
                            side=absorb_side,
                            price=cluster_price,
                            size=vol / cluster_price,
                            notional_usdt=vol,
                            distance_bps=abs(cluster_price - mid_price) / mid_price * 10000.0,
                            baseline_multiple=vol / baseline,
                            classification_tags=["PASSIVE_ABSORPTION", specific_absorb.value],
                            details=f"Passive {absorb_side} absorption: ${vol:,.2f} absorbed at ~${cluster_price:,.4f}.",
                            venue=trade_venue,
                            event_class="EXECUTED",
                            mark_price_at_event=mid_price,
                        )
                    )
                    break

        # ---------------------------------------------------------------------
        # 3. Inspect Open Interest & Price Divergences
        # ---------------------------------------------------------------------
        if current_oi_contracts is not None and mid_price > 0:
            prev_snapshot = self._last_oi_snapshots.get(sym)
            if prev_snapshot is not None:
                prev_oi, prev_px, prev_ts = prev_snapshot
                oi_delta_pct = ((current_oi_contracts - prev_oi) / prev_oi) * 100.0 if prev_oi > 0 else 0.0
                px_delta_pct = ((mid_price - prev_px) / prev_px) * 100.0 if prev_px > 0 else 0.0

                # 9. OI Expansion with Price Flat: Accumulation / Buildup
                if oi_delta_pct >= 4.0 and abs(px_delta_pct) <= 0.6:
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_OI_EXP_{sym}_{now_ts}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=WhaleEventType.OI_EXPANSION_PRICE_FLAT,
                            side="NEUTRAL",
                            price=mid_price,
                            size=abs(current_oi_contracts - prev_oi),
                            notional_usdt=abs(current_oi_contracts - prev_oi) * mid_price,
                            distance_bps=0.0,
                            baseline_multiple=1.0,
                            classification_tags=["POSITION_BUILDUP", "VOLATILITY_COIL"],
                            details=f"Open interest expanded by +{oi_delta_pct:.1f}% while price remained flat ({px_delta_pct:+.2f}%). Large position accumulation underway.",
                            venue=venue,
                            event_class="DERIVATIVES",
                            mark_price_at_event=mid_price,
                        )
                    )

                # 10. OI Contraction with Volume Spike: Liquidation Cascade or Forced Exits
                elif oi_delta_pct <= -4.0 and abs(px_delta_pct) >= 1.5:
                    new_events.append(
                        WhaleActivityEvent(
                            event_id=f"EV_OI_CON_{sym}_{now_ts}",
                            timestamp_ms=now_ts,
                            symbol=sym,
                            event_type=WhaleEventType.OI_CONTRACTION_VOLUME_SPIKE,
                            side="SELL" if px_delta_pct < 0 else "BUY",
                            price=mid_price,
                            size=abs(current_oi_contracts - prev_oi),
                            notional_usdt=abs(current_oi_contracts - prev_oi) * mid_price,
                            distance_bps=0.0,
                            baseline_multiple=1.0,
                            classification_tags=["LIQUIDATION_FLUSH", "UNWINDING"],
                            details=f"Open interest contracted by {oi_delta_pct:.1f}% with rapid price movement ({px_delta_pct:+.2f}%). Forced stop or liquidation cascade.",
                            venue=venue,
                            event_class="DERIVATIVES",
                            mark_price_at_event=mid_price,
                        )
                    )

            self._last_oi_snapshots[sym] = (current_oi_contracts, mid_price, now_ts)

        # Append new events and keep last 60 events
        events_list.extend(new_events)
        if len(events_list) > 60:
            self._events_history[sym] = events_list[-60:]

        # ---------------------------------------------------------------------
        # 4. Multi-Factor Whale Scoring & Directional Bias Calculation
        # ---------------------------------------------------------------------
        # 1. Depth Multiples (0 to 25 pts)
        bid_depth_pts = min(25.0, (current_bid_walls_notional / baseline) * 5.0)
        ask_depth_pts = min(25.0, (current_ask_walls_notional / baseline) * 5.0)

        # 2. Persistence (0 to 20 pts)
        max_bid_p = max([w.persistence_seconds for w in active_walls.values() if w.side == "BID" and w.is_active], default=0.0)
        max_ask_p = max([w.persistence_seconds for w in active_walls.values() if w.side == "ASK" and w.is_active], default=0.0)
        bid_persist_pts = min(20.0, (max_bid_p / 60.0) * 20.0)
        ask_persist_pts = min(20.0, (max_ask_p / 60.0) * 20.0)

        # 3. Replenishment (0 to 15 pts)
        bid_replen_count = sum(1 for e in new_events if e.event_type in (WhaleEventType.LARGE_BID_REPLENISHED, WhaleEventType.WALL_REPLENISHMENT) and e.side == "BID")
        ask_replen_count = sum(1 for e in new_events if e.event_type in (WhaleEventType.LARGE_ASK_REPLENISHED, WhaleEventType.WALL_REPLENISHMENT) and e.side == "ASK")
        bid_replen_pts = min(15.0, bid_replen_count * 7.5)
        ask_replen_pts = min(15.0, ask_replen_count * 7.5)

        # 4. CVD Trade Skew (0 to 20 pts)
        tot_trades = buy_trades_notional + sell_trades_notional
        cvd_bid_pts = (
            min(20.0, ((buy_trades_notional - sell_trades_notional) / tot_trades) * 20.0)
            if tot_trades > 0 and buy_trades_notional > sell_trades_notional
            else 0.0
        )
        cvd_ask_pts = (
            min(20.0, ((sell_trades_notional - buy_trades_notional) / tot_trades) * 20.0)
            if tot_trades > 0 and sell_trades_notional > buy_trades_notional
            else 0.0
        )

        # 5. Large Taker Executions (0 to 20 pts)
        taker_buy_pts = min(20.0, (large_taker_buy_notional / baseline) * 5.0)
        taker_sell_pts = min(20.0, (large_taker_sell_notional / baseline) * 5.0)

        whale_buy_score = round(min(100.0, bid_depth_pts + bid_persist_pts + bid_replen_pts + cvd_bid_pts + taker_buy_pts), 1)
        whale_sell_score = round(min(100.0, ask_depth_pts + ask_persist_pts + ask_replen_pts + cvd_ask_pts + taker_sell_pts), 1)
        net_whale_score = round(whale_buy_score - whale_sell_score, 1)

        # Determine Net Whale Bias
        if current_bid_walls_notional > current_ask_walls_notional * 1.5 or net_whale_score >= 25.0:
            net_bias = "BULLISH_FLOW"
        elif current_ask_walls_notional > current_bid_walls_notional * 1.5 or net_whale_score <= -25.0:
            net_bias = "BEARISH_FLOW"
        else:
            net_bias = "BALANCED"

        return WhaleActivitySummary(
            symbol=sym,
            mark_price=mid_price,
            baseline_depth_usdt=baseline,
            recent_events=list(reversed(self._events_history[sym][-20:])),
            large_bid_notional_usdt=current_bid_walls_notional,
            large_ask_notional_usdt=current_ask_walls_notional,
            net_whale_bias=net_bias,
            spoof_cancellation_count=spoof_count,
            absorption_detected=absorption_found,
            active_walls_count=active_walls_count,
            whale_buy_score=whale_buy_score,
            whale_sell_score=whale_sell_score,
            net_whale_score=net_whale_score,
        )

