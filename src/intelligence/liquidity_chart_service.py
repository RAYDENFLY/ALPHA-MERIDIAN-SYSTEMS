"""Liquidity Chart Service.

Orchestrates multi-timeframe OHLC candlestick data, structural liquidity levels,
sweep/reclaim markers, order-book depth/walls, and estimated liquidation clusters
for the interactive chart dashboard.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from src.core.types import Candle
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.normalized import NormalizedOrderBook
from src.intelligence.liquidation_heatmap import LiquidationHeatmapEngine
from src.intelligence.sweep_tracker import (
    LiquidityLevel,
    LiquidityLevelStatus,
    LiquidityLevelType,
    SweepEventType,
    SweepReclaimEvent,
    SweepReclaimTracker,
)
from src.liquidity.wall_hunter import LiquidityWall, LiquidityWallHunter

logger = logging.getLogger(__name__)


class LiquidityChartService:
    """Service providing chart-ready data, overlays, markers, and replay snapshots."""

    def __init__(self, adapter: Optional[AsterAdapter] = None):
        self.adapter = adapter or AsterAdapter()
        self.tracker = SweepReclaimTracker()
        self.wall_hunter = LiquidityWallHunter(wall_multiple_threshold=2.5, min_persistence_sec=10.0)

    def get_chart_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 200,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch and format OHLCV candles suitable for TradingView Lightweight Charts."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        raw_candles = self.adapter.get_klines(
            symbol=clean_symbol,
            timeframe=timeframe,
            limit=min(limit, 500),
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

        formatted: List[Dict[str, Any]] = []
        for c in raw_candles:
            formatted.append(
                {
                    "time": int(c.timestamp_ms / 1000),  # Lightweight charts requires unix timestamp in seconds
                    "open": round(c.open, 4),
                    "high": round(c.high, 4),
                    "low": round(c.low, 4),
                    "close": round(c.close, 4),
                    "volume": round(c.volume, 2),
                    "quote_volume": round(c.quote_volume, 2),
                    "timestamp_ms": c.timestamp_ms,
                    "datetime_utc": c.datetime_utc.isoformat(),
                }
            )
        return formatted

    def get_liquidity_layers(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 150,
        as_of_index: Optional[int] = None,
        as_of_timestamp_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate complete chart data with independent overlays, event markers, and replay support."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        now_ms = int(time.time() * 1000)

        # 1. Retrieve Historical Candlesticks
        candles = self.adapter.get_klines(symbol=clean_symbol, timeframe=timeframe, limit=min(limit, 400))
        if not candles:
            return {
                "symbol": clean_symbol,
                "timeframe": timeframe,
                "feed_status": {
                    "source": "AsterDEX V3 REST",
                    "status": "UNAVAILABLE",
                    "is_stale": True,
                    "candle_count": 0,
                    "error_message": f"No kline data returned for {clean_symbol}",
                },
                "candles": [],
                "structural_levels": [],
                "sweep_events": [],
                "chart_markers": [],
                "order_book_overlay": {"bids": [], "asks": [], "walls": []},
                "liquidation_overlay": {"clusters": []},
                "replay_metadata": {"is_replay": False, "as_of_index": None, "total_bars": 0},
            }

        # Resolve replay index if timestamp provided
        total_bars = len(candles)
        effective_as_of_index = as_of_index
        if as_of_timestamp_ms is not None:
            effective_as_of_index = total_bars - 1
            for idx, c in enumerate(candles):
                if c.timestamp_ms <= as_of_timestamp_ms:
                    effective_as_of_index = idx
                else:
                    break

        if effective_as_of_index is not None:
            effective_as_of_index = max(0, min(total_bars - 1, effective_as_of_index))

        # 2. Structural Liquidity Levels & Sweep/Reclaim Events
        all_levels = self.tracker.identify_structural_levels(candles, clean_symbol, timeframe)
        visible_levels, detected_events = self.tracker.track_events(
            candles=candles,
            levels=all_levels,
            as_of_index=effective_as_of_index,
        )

        # Slice candles if in historical replay mode
        chart_candles = candles if effective_as_of_index is None else candles[: effective_as_of_index + 1]
        formatted_candles = [
            {
                "time": int(c.timestamp_ms / 1000),
                "open": round(c.open, 4),
                "high": round(c.high, 4),
                "low": round(c.low, 4),
                "close": round(c.close, 4),
                "volume": round(c.volume, 2),
                "quote_volume": round(c.quote_volume, 2),
                "timestamp_ms": c.timestamp_ms,
                "datetime_utc": c.datetime_utc.isoformat(),
            }
            for c in chart_candles
        ]

        # 3. Build Chart Markers for Sweeps, Reclaims, and Confirmations
        chart_markers = self._build_chart_markers(detected_events)

        # 4. Live Order-Book Liquidity Overlay (Depth Bands & Resting Walls)
        order_book_overlay = self._build_order_book_overlay(clean_symbol)

        # 5. Estimated Liquidation Clusters Overlay
        liquidation_overlay = self._build_liquidation_overlay(clean_symbol, candles)

        # 6. Feed Freshness and Health Assessment
        latest_candle = chart_candles[-1] if chart_candles else None
        candle_age_sec = (now_ms - latest_candle.timestamp_ms) / 1000.0 if latest_candle else 999999.0
        
        # Max expected candle ages before declaring stale
        tf_stale_limits = {"15m": 1200.0, "1h": 4500.0, "4h": 18000.0}
        stale_limit = tf_stale_limits.get(timeframe.lower(), 4500.0)
        is_stale = candle_age_sec > stale_limit and effective_as_of_index is None

        feed_status = {
            "source": "AsterDEX V3 REST",
            "status": "REPLAY" if effective_as_of_index is not None else ("STALE" if is_stale else "LIVE"),
            "is_stale": is_stale,
            "last_candle_timestamp_ms": latest_candle.timestamp_ms if latest_candle else None,
            "last_candle_utc": latest_candle.datetime_utc.isoformat() if latest_candle else None,
            "candle_age_seconds": round(candle_age_sec, 1),
            "candle_count": len(chart_candles),
            "total_available_bars": total_bars,
        }

        replay_metadata = {
            "is_replay": effective_as_of_index is not None,
            "as_of_index": effective_as_of_index,
            "total_bars": total_bars,
            "earliest_timestamp_ms": candles[0].timestamp_ms if candles else None,
            "latest_timestamp_ms": candles[-1].timestamp_ms if candles else None,
            "current_bar_timestamp_ms": latest_candle.timestamp_ms if latest_candle else None,
        }

        return {
            "symbol": clean_symbol,
            "timeframe": timeframe,
            "feed_status": feed_status,
            "candles": formatted_candles,
            "structural_levels": [lvl.to_dict() for lvl in visible_levels],
            "sweep_events": [evt.to_dict() for evt in detected_events],
            "chart_markers": chart_markers,
            "order_book_overlay": order_book_overlay,
            "liquidation_overlay": liquidation_overlay,
            "replay_metadata": replay_metadata,
        }

    def _build_chart_markers(self, events: List[SweepReclaimEvent]) -> List[Dict[str, Any]]:
        """Generate TradingView Lightweight Chart markers for sweep, reclaim, and confirmation candles."""
        markers: List[Dict[str, Any]] = []

        for evt in events:
            time_sec = int(evt.sweep_timestamp_ms / 1000)

            # 1. Sweep Candle Marker
            if evt.direction == "SHORT":
                markers.append(
                    {
                        "time": time_sec,
                        "position": "aboveBar",
                        "color": "#ef4444",  # Red
                        "shape": "arrowDown",
                        "text": f"SWEEP {evt.sweep_depth_bps:.0f}bps",
                        "id": f"marker_swp_{evt.event_id}",
                    }
                )
            else:  # LONG
                markers.append(
                    {
                        "time": time_sec,
                        "position": "belowBar",
                        "color": "#10b981",  # Green
                        "shape": "arrowUp",
                        "text": f"SWEEP {evt.sweep_depth_bps:.0f}bps",
                        "id": f"marker_swp_{evt.event_id}",
                    }
                )

            # 2. Reclaim Candle Marker
            if evt.reclaim_timestamp_ms is not None:
                rec_time_sec = int(evt.reclaim_timestamp_ms / 1000)
                markers.append(
                    {
                        "time": rec_time_sec,
                        "position": "aboveBar" if evt.direction == "SHORT" else "belowBar",
                        "color": "#f59e0b",  # Amber
                        "shape": "circle",
                        "text": "RECLAIM",
                        "id": f"marker_rec_{evt.event_id}",
                    }
                )

            # 3. Confirmation Candle Marker
            if evt.confirmation_timestamp_ms is not None:
                conf_time_sec = int(evt.confirmation_timestamp_ms / 1000)
                markers.append(
                    {
                        "time": conf_time_sec,
                        "position": "aboveBar" if evt.direction == "SHORT" else "belowBar",
                        "color": "#8b5cf6",  # Purple
                        "shape": "square",
                        "text": "CONFIRMED",
                        "id": f"marker_cnf_{evt.event_id}",
                    }
                )

        # Sort markers chronologically (Lightweight Charts requirement!)
        markers.sort(key=lambda m: m["time"])
        return markers

    def _build_order_book_overlay(self, symbol: str) -> Dict[str, Any]:
        """Calculate order-book depth bands and persistent resting walls."""
        book: Optional[NormalizedOrderBook] = self.adapter.get_order_book(symbol, limit=50)
        if not book or not book.bids or not book.asks:
            return {"status": "UNAVAILABLE", "bids": [], "asks": [], "walls": []}

        mid = book.mid_price
        if mid <= 0.0:
            return {"status": "UNAVAILABLE", "bids": [], "asks": [], "walls": []}

        # Calculate depth bands at 10, 25, 50, 100 bps
        band_distances_bps = [10.0, 25.0, 50.0, 100.0]
        bid_bands = []
        for bps in band_distances_bps:
            target_px = mid * (1.0 - bps / 10000.0)
            cum_vol = sum((b.price * b.size) for b in book.bids if b.price >= target_px)
            bid_bands.append({"bps": bps, "price": round(target_px, 4), "notional_usdt": round(cum_vol, 2)})

        ask_bands = []
        for bps in band_distances_bps:
            target_px = mid * (1.0 + bps / 10000.0)
            cum_vol = sum((a.price * a.size) for a in book.asks if a.price <= target_px)
            ask_bands.append({"bps": bps, "price": round(target_px, 4), "notional_usdt": round(cum_vol, 2)})

        # Detect resting walls
        walls = self.wall_hunter.analyze_order_book(book)
        formatted_walls = [
            {
                "side": w.side,
                "price": round(w.price_level, 4),
                "depth_usdt": round(w.depth_notional_usdt, 2),
                "multiple": round(w.multiple_of_baseline, 1),
                "distance_bps": round(w.distance_bps, 1),
                "persistence_sec": round(w.persistence_seconds, 1),
                "is_persistent": w.is_persistent,
            }
            for w in walls
        ]

        return {
            "status": "AVAILABLE",
            "mid_price": round(mid, 4),
            "spread_bps": round(book.spread_bps, 2),
            "timestamp_ms": book.timestamp_ms,
            "bids_depth_bands": bid_bands,
            "asks_depth_bands": ask_bands,
            "walls": formatted_walls,
        }

    def _build_liquidation_overlay(self, symbol: str, candles_1h: List[Candle]) -> Dict[str, Any]:
        """Generate estimated liquidation clusters with explicit mathematical disclosure."""
        prem = self.adapter.get_premium_index(symbol)
        mark_px = float(prem.get("mark_price", 0.0)) if prem else 0.0
        funding = float(prem.get("last_funding_rate", 0.0001)) if prem else 0.0001
        oi_data = self.adapter.get_open_interest(symbol)
        oi_contracts = float(oi_data.get("open_interest", 0.0)) if oi_data else None

        if mark_px <= 0.0 and candles_1h:
            mark_px = candles_1h[-1].close

        if mark_px <= 0.0:
            return {"status": "UNAVAILABLE", "clusters": [], "disclosure": "Mark price unavailable"}

        heatmap = LiquidationHeatmapEngine.generate_heatmap(
            symbol=symbol,
            mark_price=mark_px,
            open_interest_contracts=oi_contracts,
            funding_rate=funding,
            candles_1h=candles_1h[-50:] if len(candles_1h) > 50 else candles_1h,
        )

        return {
            "status": "AVAILABLE",
            "mark_price": round(mark_px, 4),
            "total_oi_usdt": heatmap.open_interest_usdt,
            "long_short_ratio": round(heatmap.long_short_liquidation_ratio, 2),
            "clusters": [c.to_dict() for c in (heatmap.long_clusters + heatmap.short_clusters)],
            "methodology_disclosure": heatmap.methodology,
        }
