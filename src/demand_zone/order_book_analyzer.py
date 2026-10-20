"""Order-Book and Liquidity Quality Analyzer for AsterDEX Demand-Zone LONG Scanner.

Measures:
- Live bid-ask spread in basis points
- Depth in 25 bps, 50 bps, and 100 bps price bands
- Depth ratio relative to the intended limit order size
- Large resting bid walls located inside or near the demand zone
- Wall persistence tracking across consecutive scan observations
- Liquidity quality score (0.0 to 100.0)
- Spoofing risk warning for unstable or flickering walls
"""

from typing import Any, Dict, List, Optional, Tuple
import time

from src.exchange.normalized import NormalizedOrderBook
from src.demand_zone.types import (
    DemandZone,
    DemandZoneConfig,
    OrderBookLiquidityAssessment,
)


def _extract_level(level: Any) -> Tuple[float, float]:
    """Extract (price, size) tuple from OrderBookLevel or tuple/list."""
    if hasattr(level, "price") and hasattr(level, "size"):
        return float(level.price), float(level.size)
    if isinstance(level, (list, tuple)) and len(level) >= 2:
        return float(level[0]), float(level[1])
    raise ValueError(f"Unsupported order book level type: {type(level)}")


class DemandZoneOrderBookAnalyzer:
    """Analyzes Level-2 order book depth, spread, resting walls, and spoofing indicators."""

    def __init__(self, config: Optional[DemandZoneConfig] = None) -> None:
        self.config = config or DemandZoneConfig()
        # History of observed walls for persistence tracking: symbol -> List of snapshots
        self._wall_history: Dict[str, List[Dict[str, Any]]] = {}

    def analyze_order_book(
        self,
        symbol: str,
        book: Optional[NormalizedOrderBook],
        intended_order_notional: float,
        demand_zone: Optional[DemandZone] = None,
        now_ms: Optional[int] = None,
    ) -> OrderBookLiquidityAssessment:
        """Evaluate L2 depth, resting bid walls near demand, and liquidity quality."""
        current_time = now_ms or int(time.time() * 1000)

        if book is None or not book.bids or not book.asks:
            return OrderBookLiquidityAssessment(
                symbol=symbol,
                best_bid=0.0,
                best_ask=0.0,
                spread_bps=999.0,
                bid_depth_25bps_usdt=0.0,
                ask_depth_25bps_usdt=0.0,
                bid_depth_50bps_usdt=0.0,
                ask_depth_50bps_usdt=0.0,
                depth_imbalance_25bps=0.0,
                order_size_depth_ratio=0.0,
                supporting_bid_walls=[],
                wall_persistence_status="UNAVAILABLE",
                spoofing_warning=False,
                spoofing_warning_reason=None,
                liquidity_quality_score=0.0,
                assessment_notes=["Order book unavailable or empty."],
            )

        parsed_bids: List[Tuple[float, float]] = [_extract_level(item) for item in book.bids]
        parsed_asks: List[Tuple[float, float]] = [_extract_level(item) for item in book.asks]

        best_bid = parsed_bids[0][0]
        best_ask = parsed_asks[0][0]
        mid_px = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else best_bid
        spread_bps = ((best_ask - best_bid) / mid_px * 10000.0) if (mid_px > 0 and best_ask >= best_bid) else 0.0

        # 1. Depth accumulation within 25 bps, 50 bps, 100 bps
        band_25 = mid_px * 0.0025
        band_50 = mid_px * 0.0050

        bid_25_notional = sum(px * qty for px, qty in parsed_bids if px >= mid_px - band_25)
        ask_25_notional = sum(px * qty for px, qty in parsed_asks if px <= mid_px + band_25)

        bid_50_notional = sum(px * qty for px, qty in parsed_bids if px >= mid_px - band_50)
        ask_50_notional = sum(px * qty for px, qty in parsed_asks if px <= mid_px + band_50)

        total_25 = bid_25_notional + ask_25_notional
        imbalance_25 = (bid_25_notional - ask_25_notional) / total_25 if total_25 > 0 else 0.0

        depth_ratio = (bid_25_notional / intended_order_notional) if intended_order_notional > 0 else 100.0

        # 2. Identify resting bid walls near or inside demand zone
        supporting_walls: List[Dict[str, Any]] = []
        avg_bid_size = sum(px * qty for px, qty in parsed_bids[:20]) / max(len(parsed_bids[:20]), 1)

        z_upper = demand_zone.upper_price if demand_zone else (mid_px * 0.985)
        z_lower = demand_zone.lower_price if demand_zone else (mid_px * 0.965)

        for px, qty in parsed_bids:
            notional = px * qty
            # Wall threshold: >= 3x average bid level or >= $10,000 USDT
            if notional >= 10000.0 or (avg_bid_size > 0 and notional >= avg_bid_size * 2.5):
                # Check proximity to demand zone (within 1% of zone)
                is_near_demand = (z_lower * 0.99 <= px <= z_upper * 1.01)
                supporting_walls.append({
                    "price": round(px, 6),
                    "quantity": qty,
                    "notional_usdt": round(notional, 2),
                    "multiple_of_avg": round(notional / avg_bid_size, 1) if avg_bid_size > 0 else 1.0,
                    "is_in_demand_zone": is_near_demand,
                    "timestamp_ms": current_time,
                })

        # 3. Track persistence and spoofing warning across observations
        sym_key = symbol.upper()
        history = self._wall_history.setdefault(sym_key, [])
        history.append({
            "timestamp_ms": current_time,
            "walls": supporting_walls,
            "best_bid": best_bid,
        })
        # Keep last 10 snapshots (approx 5-10 minutes)
        if len(history) > 10:
            history.pop(0)

        wall_persistence = "NEUTRAL"
        spoofing_warning = False
        spoof_reason = None

        if len(history) >= 3:
            current_wall_prices = [w["price"] for w in supporting_walls]
            persistent_count = 0

            if supporting_walls:
                # Check how many prior observations confirmed these wall levels
                for snap in history[:-1]:
                    prev_prices = [w["price"] for w in snap.get("walls", [])]
                    if any(any(abs(cp - pp) / cp < 0.001 for pp in prev_prices) for cp in current_wall_prices):
                        persistent_count += 1

                if persistent_count >= 2:
                    wall_persistence = "CONFIRMED_PERSISTENT"
                else:
                    wall_persistence = "NEWLY_APPEARED"

            # Check for sudden cancellation before price arrived (flickering / spoofing warning)
            disappeared_large_walls = []
            for snap in history[-4:-1]:
                for pw in snap.get("walls", []):
                    # If prior large wall is no longer present and price never reached it
                    if pw["notional_usdt"] >= 25000.0:
                        if not any(abs(pw["price"] - cw) / pw["price"] < 0.001 for cw in current_wall_prices):
                            if best_bid > pw["price"] * 1.002:  # Price never reached wall before it vanished
                                disappeared_large_walls.append(pw)

            if disappeared_large_walls:
                spoofing_warning = True
                spoof_reason = f"Large resting bid (${disappeared_large_walls[0]['notional_usdt']:,.0f} at {disappeared_large_walls[0]['price']}) vanished prior to price touch."

        # 4. Liquidity Quality Score (0 - 100)
        quality_score = 50.0

        # Spread score (up to +25 pts)
        if spread_bps <= 2.0:
            quality_score += 25.0
        elif spread_bps <= 5.0:
            quality_score += 20.0
        elif spread_bps <= 10.0:
            quality_score += 10.0
        elif spread_bps > 15.0:
            quality_score -= 20.0

        # Depth ratio score (up to +25 pts)
        if depth_ratio >= 10.0:
            quality_score += 20.0
        elif depth_ratio >= 5.0:
            quality_score += 10.0
        elif depth_ratio < 2.0:
            quality_score -= 15.0

        # Order book imbalance score (+15 pts for net bid-heavy)
        if imbalance_25 >= 0.10:
            quality_score += 15.0
        elif imbalance_25 <= -0.10:
            quality_score -= 10.0

        # Wall support (+10 pts if persistent bid wall guards demand zone)
        if wall_persistence == "CONFIRMED_PERSISTENT" and any(w["is_in_demand_zone"] for w in supporting_walls):
            quality_score += 15.0

        # Spoofing penalty
        if spoofing_warning:
            quality_score -= 20.0

        final_score = max(5.0, min(100.0, round(quality_score, 1)))

        notes = []
        if spread_bps > self.config.max_spread_bps:
            notes.append(f"Excessive spread: {spread_bps:.1f} bps > {self.config.max_spread_bps:.1f} bps cap.")
        if depth_ratio < 3.0:
            notes.append(f"Thin order book: Top 25 bps bid depth provides only {depth_ratio:.1f}x order notional.")
        if wall_persistence == "CONFIRMED_PERSISTENT":
            notes.append("Resting bid wall confirmed persistent across multiple observation cycles.")
        if spoofing_warning:
            notes.append(f"Spoofing Risk: {spoof_reason}")

        return OrderBookLiquidityAssessment(
            symbol=symbol,
            best_bid=round(best_bid, 6),
            best_ask=round(best_ask, 6),
            spread_bps=round(spread_bps, 2),
            bid_depth_25bps_usdt=round(bid_25_notional, 2),
            ask_depth_25bps_usdt=round(ask_25_notional, 2),
            bid_depth_50bps_usdt=round(bid_50_notional, 2),
            ask_depth_50bps_usdt=round(ask_50_notional, 2),
            depth_imbalance_25bps=round(imbalance_25, 4),
            order_size_depth_ratio=round(depth_ratio, 2),
            supporting_bid_walls=supporting_walls,
            wall_persistence_status=wall_persistence,
            spoofing_warning=spoofing_warning,
            spoofing_warning_reason=spoof_reason,
            liquidity_quality_score=final_score,
            assessment_notes=notes,
        )
