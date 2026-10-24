"""Liquidity Wall Hunter for Model B.

Tracks resting order book depth anomalies, persistence, spoofing cancellations,
and replenishment dynamics across normalized L2 order books.

CRITICAL GUARDRAIL:
Visible resting limit orders are NEVER assumed to be guaranteed executable
liquidity or definitive evidence of institutional trading intent. Resting orders
can be pulled (canceled) instantaneously before incoming market orders arrive.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import statistics
from typing import Any, Dict, List, Optional

from src.exchange.normalized import NormalizedOrderBook

logger = logging.getLogger(__name__)


@dataclass
class LiquidityWall:
    """Represents an identified resting liquidity wall in an L2 order book."""
    symbol: str
    venue: str
    side: str                          # "BID" or "ASK"
    price_level: float
    depth_notional_usdt: float
    multiple_of_baseline: float        # e.g. 3.5x normal tier depth
    distance_bps: float                # distance from mid price
    first_seen_ms: int
    last_seen_ms: int
    persistence_seconds: float = 0.0
    is_canceled_pre_touch: bool = False # Spoofing flag: pulled as price approached
    is_filled_or_swept: bool = False
    is_replenished: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_persistent(self) -> bool:
        """Wall has persisted long enough to rule out fleeting millisecond noise."""
        return self.persistence_seconds >= 15.0 and not self.is_canceled_pre_touch


class LiquidityWallHunter:
    """Monitors order book depth against historical baseline and tracks wall lifecycle."""

    def __init__(
        self,
        wall_multiple_threshold: float = 3.0,
        min_persistence_sec: float = 15.0,
        spoof_distance_threshold_bps: float = 5.0,
    ):
        self.wall_multiple_threshold = wall_multiple_threshold
        self.min_persistence_sec = min_persistence_sec
        self.spoof_distance_threshold_bps = spoof_distance_threshold_bps
        
        # Track active walls: symbol -> list of LiquidityWall
        self._active_walls: Dict[str, List[LiquidityWall]] = {}
        # Historical baseline depth (symbol -> average depth notional per 10 bps)
        self._baseline_depth: Dict[str, float] = {}

    def update_baseline(self, symbol: str, avg_depth_usdt: float) -> None:
        """Update historical moving average baseline depth for an instrument."""
        if avg_depth_usdt > 0:
            self._baseline_depth[symbol.upper()] = avg_depth_usdt

    def analyze_order_book(
        self,
        book: NormalizedOrderBook,
        now_ms: Optional[int] = None,
    ) -> List[LiquidityWall]:
        """Scan order book levels for outsized depth walls and update persistence state."""
        if now_ms is None:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        sym = book.symbol.upper()
        mid = getattr(book, "mid_price", 0.0)
        if not isinstance(mid, (int, float)) or mid <= 0:
            return []

        if sym in self._baseline_depth and self._baseline_depth[sym] > 0:
            baseline = self._baseline_depth[sym]
        else:
            top_notionals = [level.price * level.size for level in (book.bids[:20] + book.asks[:20]) if level.price * level.size > 0]
            if top_notionals:
                baseline = max(1000.0, float(statistics.median(top_notionals)))
            else:
                baseline = 10000.0
        current_walls: List[LiquidityWall] = []

        # 1. Scan Bid Levels
        for level in book.bids[:20]:
            level_notional = level.price * level.size
            multiple = level_notional / baseline
            if multiple >= self.wall_multiple_threshold:
                dist_bps = (mid - level.price) / mid * 10000.0
                current_walls.append(
                    LiquidityWall(
                        symbol=sym,
                        venue=book.exchange_name,
                        side="BID",
                        price_level=level.price,
                        depth_notional_usdt=round(level_notional, 2),
                        multiple_of_baseline=round(multiple, 2),
                        distance_bps=round(dist_bps, 2),
                        first_seen_ms=now_ms,
                        last_seen_ms=now_ms,
                    )
                )

        # 2. Scan Ask Levels
        for level in book.asks[:20]:
            level_notional = level.price * level.size
            multiple = level_notional / baseline
            if multiple >= self.wall_multiple_threshold:
                dist_bps = (level.price - mid) / mid * 10000.0
                current_walls.append(
                    LiquidityWall(
                        symbol=sym,
                        venue=book.exchange_name,
                        side="ASK",
                        price_level=level.price,
                        depth_notional_usdt=round(level_notional, 2),
                        multiple_of_baseline=round(multiple, 2),
                        distance_bps=round(dist_bps, 2),
                        first_seen_ms=now_ms,
                        last_seen_ms=now_ms,
                    )
                )

        # 3. Match against prior active walls to track persistence and spoofing
        prior_walls = self._active_walls.get(sym, [])
        updated_walls: List[LiquidityWall] = []

        for curr in current_walls:
            matched_prior = None
            for p in prior_walls:
                # Match price within 0.05%
                if p.side == curr.side and abs(p.price_level - curr.price_level) / curr.price_level < 0.0005:
                    matched_prior = p
                    break

            if matched_prior:
                curr.first_seen_ms = matched_prior.first_seen_ms
                curr.persistence_seconds = (now_ms - curr.first_seen_ms) / 1000.0
                if curr.depth_notional_usdt > matched_prior.depth_notional_usdt * 1.2:
                    curr.is_replenished = True
            updated_walls.append(curr)

        # Detect disappeared walls (cancellation / spoofing)
        for p in prior_walls:
            still_exists = any(
                p.side == u.side and abs(p.price_level - u.price_level) / p.price_level < 0.0005
                for u in updated_walls
            )
            if not still_exists:
                # If mid price was close when it disappeared, flag as pre-touch spoofing
                dist_when_vanished = abs(mid - p.price_level) / mid * 10000.0
                if dist_when_vanished <= self.spoof_distance_threshold_bps:
                    p.is_canceled_pre_touch = True
                    logger.info(
                        "Spoof wall detected on %s %s at %.4f (vanished %0.1f bps from mid)",
                        sym,
                        p.side,
                        p.price_level,
                        dist_when_vanished,
                    )

        self._active_walls[sym] = updated_walls
        return updated_walls

    def get_persistent_walls(self, symbol: str) -> List[LiquidityWall]:
        """Return currently resting walls that meet persistence criteria."""
        return [w for w in self._active_walls.get(symbol.upper(), []) if w.is_persistent]

    detect_walls = analyze_order_book
