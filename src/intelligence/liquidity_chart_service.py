"""Liquidity Chart Service.

Orchestrates multi-timeframe OHLC candlestick data, structural liquidity levels,
sweep/reclaim markers, order-book depth/walls, and estimated liquidation clusters
for the interactive chart dashboard.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from src.data.downloader import HistoricalDownloader
from src.data.sync import CandleSynchronizer
from src.data.validator import CandleValidator, TIMEFRAME_MS

from src.core.types import Candle
from src.data.database import DatabaseManager
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.cross_exchange_liquidity import CrossExchangeLiquidityAggregator
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

    def __init__(
        self,
        adapter: Optional[AsterAdapter] = None,
        cross_aggregator: Optional[CrossExchangeLiquidityAggregator] = None,
        binance_adapter: Optional[Any] = None,
        hyperliquid_adapter: Optional[Any] = None,
        db: Optional[DatabaseManager] = None,
    ):
        self.adapter = adapter or AsterAdapter()
        from src.exchange.binance_adapter import BinanceFuturesAdapter
        from src.exchange.hyperliquid_adapter import HyperliquidAdapter
        self.binance_adapter = binance_adapter or BinanceFuturesAdapter()
        self.hyperliquid_adapter = hyperliquid_adapter or HyperliquidAdapter()
        self.tracker = SweepReclaimTracker()
        self.wall_hunter = LiquidityWallHunter(wall_multiple_threshold=2.5, min_persistence_sec=10.0)
        self.cross_aggregator = cross_aggregator or CrossExchangeLiquidityAggregator(aster_adapter=self.adapter)
        self._d1_cache: Dict[str, Dict[str, Any]] = {}
        self.db = db
        self._sync_locks_guard = threading.Lock()
        self._sync_locks: Dict[str, threading.Lock] = {}
        self._last_sync_check_ts: Dict[str, float] = {}
        self._exhausted_earliest_ts: Dict[str, int] = {}

    DEFAULT_TARGET_HISTORY_DAYS: Dict[str, int] = {
        "1d": 1465,   # ~4.01 years (1,465 daily bars)
        "4h": 730,    # 2 years
        "1h": 365,    # 1 year
        "15m": 60,    # 60 days
    }

    def _get_symbol_tf_lock(self, key: str) -> threading.Lock:
        with self._sync_locks_guard:
            lock = self._sync_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._sync_locks[key] = lock
            return lock

    def ensure_local_history(
        self,
        symbol: str,
        timeframe: str,
        target_start_ms: Optional[int] = None,
        now_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Ensure SQLite has backfilled historical candles back to target_start_ms and synced recent closed bars."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        tf = timeframe.strip().lower()
        current_ms = now_ms or int(time.time() * 1000)
        step_ms = TIMEFRAME_MS.get(tf, 3600 * 1000)
        default_days = self.DEFAULT_TARGET_HISTORY_DAYS.get(tf, 365)
        desired_start_ms = target_start_ms if target_start_ms is not None else (current_ms - default_days * 86400 * 1000)

        if not self.db:
            return {"synced": False, "reason": "no_db"}

        sync_initial = self.db.get_sync_state(clean_symbol, tf)
        if hasattr(self.adapter, "_mock_name"):
            return {
                "synced": False,
                "reason": "mock_adapter",
                "earliest_timestamp_ms": sync_initial["earliest_timestamp_ms"] if sync_initial else None,
                "latest_timestamp_ms": sync_initial["latest_timestamp_ms"] if sync_initial else None,
                "total_candles": int(sync_initial["total_candles"]) if sync_initial and sync_initial.get("total_candles") else 0,
            }

        key = f"{clean_symbol}|{tf}"
        lock = self._get_symbol_tf_lock(key)
        with lock:
            sync = self.db.get_sync_state(clean_symbol, tf)
            earliest_db = sync["earliest_timestamp_ms"] if sync else None
            latest_db = sync["latest_timestamp_ms"] if sync else None
            total_db = int(sync["total_candles"]) if sync and sync.get("total_candles") else 0

            # Avoid hammering exchange if we already checked within the last 45s and no deeper cursor is requested
            now_sec = time.time()
            last_check = self._last_sync_check_ts.get(key, 0.0)
            exhausted_ts = self._exhausted_earliest_ts.get(key)

            need_initial = (earliest_db is None or total_db == 0)
            need_backward = (
                not need_initial
                and earliest_db is not None
                and earliest_db > (desired_start_ms + step_ms * 2)
                and (exhausted_ts is None or desired_start_ms < exhausted_ts and earliest_db > exhausted_ts)
            )
            need_forward = (
                not need_initial
                and latest_db is not None
                and (current_ms - latest_db) >= (step_ms * 2)
                and (now_sec - last_check >= 45.0)
            )

            inserted_total = 0
            if need_initial or need_backward:
                downloader = HistoricalDownloader(
                    exchange=self.adapter,
                    db=self.db,
                    batch_size=1000,
                    request_delay_sec=0.02,
                )
                days_needed = max(1, int((current_ms - desired_start_ms) // (86400 * 1000)) + 2)
                prev_earliest = earliest_db
                try:
                    inserted_total += downloader.download_symbol_timeframe(
                        symbol=clean_symbol,
                        timeframe=tf,
                        days_back=days_needed,
                        end_time_ms=current_ms,
                    )
                except Exception as dl_err:
                    logger.warning(f"LiquidityChartService: Historical backfill warning for {clean_symbol} {tf}: {dl_err}")

                sync_after = self.db.get_sync_state(clean_symbol, tf)
                new_earliest = sync_after["earliest_timestamp_ms"] if sync_after else None
                if new_earliest is not None and (prev_earliest is not None and new_earliest >= prev_earliest):
                    # Exchange has no older candles prior to new_earliest
                    self._exhausted_earliest_ts[key] = new_earliest
                elif new_earliest is not None and new_earliest > (desired_start_ms + step_ms * 5):
                    # Reached exchange inception boundary
                    self._exhausted_earliest_ts[key] = new_earliest
                self._last_sync_check_ts[key] = now_sec

            elif need_forward:
                synchronizer = CandleSynchronizer(exchange=self.adapter, db=self.db)
                try:
                    synced_bars = synchronizer.sync_symbol(clean_symbol, tf, now_ms=current_ms, max_pages=5)
                    inserted_total += len(synced_bars)
                except Exception as sync_err:
                    logger.warning(f"LiquidityChartService: Incremental sync warning for {clean_symbol} {tf}: {sync_err}")
                self._last_sync_check_ts[key] = now_sec

            final_sync = self.db.get_sync_state(clean_symbol, tf)
            return {
                "synced": True,
                "inserted": inserted_total,
                "earliest_timestamp_ms": final_sync["earliest_timestamp_ms"] if final_sync else None,
                "latest_timestamp_ms": final_sync["latest_timestamp_ms"] if final_sync else None,
                "total_candles": int(final_sync["total_candles"]) if final_sync and final_sync.get("total_candles") else 0,
            }

    def _get_local_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: Optional[int] = None,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        before_time_ms: Optional[int] = None,
    ) -> List[Candle]:
        if not self.db:
            return []
        capped_limit = min(max(int(limit), 1), 5000) if limit is not None else None
        return self.db.get_candles(
            symbol,
            timeframe,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            before_time_ms=before_time_ms,
            limit=capped_limit,
        )

    def _get_cached_1d_candles(self, symbol: str, limit: int = 500) -> List[Candle]:
        """Fetch and cache 1D daily candles (preferring SQLite local store) to minimize repeated upstream requests."""
        now = time.time()
        cached = self._d1_cache.get(symbol)
        if cached and (now - cached.get("ts", 0.0) < 60.0):
            return cached["candles"]
        candles = self._get_local_candles(symbol, "1d", limit=limit)
        if not candles:
            candles = self.adapter.get_klines(symbol=symbol, timeframe="1d", limit=min(limit, 1000))
            if candles and self.db and not hasattr(self.adapter, "_mock_name"):
                closed = CandleValidator.filter_closed_candles(candles, now_ms=int(now * 1000))
                if closed:
                    self.db.insert_candles(closed)
        self._d1_cache[symbol] = {"ts": now, "candles": candles}
        return candles

    def get_chart_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 500,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        before_time_ms: Optional[int] = None,
        venue: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch and format OHLCV candles suitable for TradingView Lightweight Charts across AsterDEX, Binance, and Hyperliquid."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        tf = timeframe.strip().lower()
        target_venue = (venue or "asterdex").strip().lower()
        raw_candles: List[Candle] = []

        if target_venue in ("asterdex", "local", "combined", ""):
            step_ms = TIMEFRAME_MS.get(tf, 3600 * 1000)
            target_start = start_time_ms
            if before_time_ms is not None and target_start is None:
                target_start = before_time_ms - (max(limit, 500) * step_ms)
            self.ensure_local_history(clean_symbol, tf, target_start_ms=target_start)
            raw_candles = self._get_local_candles(
                clean_symbol,
                tf,
                limit=limit,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                before_time_ms=before_time_ms,
            )

        if target_venue in ("hyperliquid", "hl"):
            if hasattr(self, "hyperliquid_adapter") and self.hyperliquid_adapter:
                raw_candles = self.hyperliquid_adapter.get_klines(
                    symbol=clean_symbol,
                    timeframe=tf,
                    limit=min(limit, 1000),
                    start_time_ms=start_time_ms,
                    end_time_ms=before_time_ms - 1 if before_time_ms is not None else end_time_ms,
                )
        elif target_venue in ("binance", "binance_futures"):
            if hasattr(self, "binance_adapter") and self.binance_adapter:
                raw_candles = self.binance_adapter.get_klines(
                    symbol=clean_symbol,
                    timeframe=tf,
                    limit=min(limit, 1000),
                    start_time_ms=start_time_ms,
                    end_time_ms=before_time_ms - 1 if before_time_ms is not None else end_time_ms,
                )

        # Fallback to primary AsterAdapter if venue yielded no candles and not paginating past earliest boundary
        if not raw_candles and before_time_ms is None:
            raw_candles = self.adapter.get_klines(
                symbol=clean_symbol,
                timeframe=tf,
                limit=min(limit, 1000),
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if raw_candles and self.db and not hasattr(self.adapter, "_mock_name"):
                closed = CandleValidator.filter_closed_candles(raw_candles, now_ms=int(time.time() * 1000))
                if closed:
                    self.db.insert_candles(closed)

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
        limit: int = 500,
        before_time_ms: Optional[int] = None,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        as_of_index: Optional[int] = None,
        as_of_timestamp_ms: Optional[int] = None,
        include_1d_overlay: bool = False,
        venue_source: str = "combined",
    ) -> Dict[str, Any]:
        """Generate complete chart data with independent overlays, event markers, cursor pagination, and replay support."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        tf = timeframe.strip().lower()
        now_ms = int(time.time() * 1000)
        step_ms = TIMEFRAME_MS.get(tf, 3600 * 1000)

        # 1. Ensure local SQLite history is synchronized (4+ years for 1D, 2Y for 4H, 1Y for 1H)
        desired_start = start_time_ms
        if before_time_ms is not None and desired_start is None:
            desired_start = before_time_ms - (max(limit, 500) * step_ms)
        sync_info = self.ensure_local_history(clean_symbol, tf, target_start_ms=desired_start, now_ms=now_ms)

        candles = self._get_local_candles(
            clean_symbol,
            tf,
            limit=limit,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            before_time_ms=before_time_ms,
        )
        if not candles and before_time_ms is None:
            candles = self.adapter.get_klines(symbol=clean_symbol, timeframe=tf, limit=min(limit, 1000))
            if candles and self.db and not hasattr(self.adapter, "_mock_name"):
                closed = CandleValidator.filter_closed_candles(candles, now_ms=now_ms)
                if closed:
                    self.db.insert_candles(closed)
                    sync_info = self.ensure_local_history(clean_symbol, tf, target_start_ms=desired_start, now_ms=now_ms)
        if not candles:
            return {
                "symbol": clean_symbol,
                "timeframe": timeframe,
                "venue_source": venue_source,
                "feed_status": {
                    "source": f"AsterDEX Klines & {venue_source.upper()} L2 Depth",
                    "status": "UNAVAILABLE",
                    "is_stale": True,
                    "candle_count": 0,
                    "candle_feed": {
                        "source": "AsterDEX V3 REST",
                        "candle_count": 0,
                        "is_stale": True,
                    },
                    "order_book_feed": {
                        "venue": venue_source,
                        "venue_name": venue_source.upper(),
                        "status": "UNAVAILABLE",
                        "age_seconds": 0.0,
                        "contributing_venues": [],
                        "error_message": f"No kline or order book data returned for {clean_symbol}",
                    },
                    "error_message": f"No kline data returned for {clean_symbol}",
                },
                "candles": [],
                "structural_levels": [],
                "sweep_events": [],
                "chart_markers": [],
                "order_book_overlay": {
                    "bids": [],
                    "asks": [],
                    "walls": [],
                    "venue_filter": venue_source,
                    "status": "UNAVAILABLE",
                    "error_message": f"No data returned for {clean_symbol}",
                },
                "liquidation_overlay": {"clusters": []},
                "replay_metadata": {"is_replay": False, "as_of_index": None, "total_bars": 0},
                "history_metadata": {
                    "symbol": clean_symbol,
                    "timeframe": tf,
                    "loaded_count": 0,
                    "oldest_loaded_timestamp_ms": None,
                    "newest_loaded_timestamp_ms": None,
                    "db_earliest_timestamp_ms": sync_info.get("earliest_timestamp_ms"),
                    "db_latest_timestamp_ms": sync_info.get("latest_timestamp_ms"),
                    "db_total_candles": sync_info.get("total_candles", 0),
                    "has_more_before": False,
                    "next_before_time_ms": None,
                },
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
        cutoff_ts = chart_candles[-1].timestamp_ms if chart_candles else now_ms

        # 2b. Overlay Confirmed 1D Higher Timeframe (HTF) Structural Levels
        if include_1d_overlay and timeframe.lower() != "1d":
            d1_candles = self._get_cached_1d_candles(clean_symbol)
            if d1_candles:
                d1_levels = self.tracker.identify_structural_levels(d1_candles, clean_symbol, "1d")
                for d1_lvl in d1_levels:
                    # Strict non-lookahead temporal invariant: level confirmed on or before chart cutoff
                    if d1_lvl.confirmed_timestamp_ms <= cutoff_ts:
                        htf_lvl = LiquidityLevel(
                            level_id=f"HTF_{d1_lvl.level_id}",
                            symbol=d1_lvl.symbol,
                            timeframe="1d",
                            level_type=d1_lvl.level_type,
                            price=d1_lvl.price,
                            zone_top=d1_lvl.zone_top,
                            zone_bottom=d1_lvl.zone_bottom,
                            created_index=d1_lvl.created_index,
                            created_timestamp_ms=d1_lvl.created_timestamp_ms,
                            confirmed_index=d1_lvl.confirmed_index,
                            confirmed_timestamp_ms=d1_lvl.confirmed_timestamp_ms,
                            source_label=f"1D Macro {d1_lvl.level_type.value}",
                            status=LiquidityLevelStatus.ACTIVE,
                            test_count=0,
                            is_htf=True,
                        )
                        visible_levels.append(htf_lvl)

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

        # 4. Live Order-Book Liquidity Overlay (Cross-Exchange Depth Bands & Resting Walls)
        order_book_overlay = self._build_order_book_overlay(clean_symbol, venue_source=venue_source)

        # 5. Estimated Liquidation Clusters Overlay
        liquidation_overlay = self._build_liquidation_overlay(clean_symbol, candles)

        # 6. Feed Freshness and Health Assessment
        latest_candle = chart_candles[-1] if chart_candles else None
        candle_age_sec = (now_ms - latest_candle.timestamp_ms) / 1000.0 if latest_candle else 999999.0
        
        # Max expected candle ages before declaring stale
        tf_secs = {"15m": 900.0, "1h": 3600.0, "4h": 14400.0, "1d": 86400.0}.get(timeframe.lower(), 3600.0)
        tf_buffer = {"15m": 600.0, "1h": 1200.0, "4h": 3600.0, "1d": 14400.0}.get(timeframe.lower(), 1200.0)
        candle_stale_limit = tf_secs + tf_buffer
        candle_is_stale = (candle_age_sec > candle_stale_limit) and (effective_as_of_index is None)

        ob_status = order_book_overlay.get("status", "AVAILABLE")
        ob_age_sec = order_book_overlay.get("age_seconds", 0.0)
        venue_label = order_book_overlay.get("venue_name", venue_source.upper())

        # Determine composite status
        if effective_as_of_index is not None:
            composite_status = "REPLAY"
        elif ob_status in ("UNAVAILABLE", "ERROR"):
            composite_status = ob_status
        elif candle_is_stale or ob_status == "STALE":
            composite_status = "STALE"
        else:
            composite_status = "LIVE"

        feed_status = {
            "source": f"AsterDEX Klines & {venue_label} L2 Depth",
            "status": composite_status,
            "is_stale": (composite_status == "STALE"),
            "candle_feed": {
                "source": "AsterDEX V3 REST",
                "last_candle_timestamp_ms": latest_candle.timestamp_ms if latest_candle else None,
                "last_candle_utc": latest_candle.datetime_utc.isoformat() if latest_candle else None,
                "candle_age_seconds": round(candle_age_sec, 1),
                "candle_count": len(chart_candles),
                "is_stale": candle_is_stale,
            },
            "order_book_feed": {
                "venue": venue_source,
                "venue_name": venue_label,
                "status": ob_status,
                "age_seconds": ob_age_sec,
                "timestamp_ms": order_book_overlay.get("timestamp_ms"),
                "contributing_venues": order_book_overlay.get("contributing_venues", []),
                "error_message": order_book_overlay.get("error_message"),
            },
            "last_candle_timestamp_ms": latest_candle.timestamp_ms if latest_candle else None,
            "last_candle_utc": latest_candle.datetime_utc.isoformat() if latest_candle else None,
            "candle_age_seconds": round(candle_age_sec, 1),
            "candle_count": len(chart_candles),
            "total_available_bars": total_bars,
            "order_book_status": ob_status,
            "order_book_age_seconds": ob_age_sec,
        }

        db_earliest_ms = sync_info.get("earliest_timestamp_ms")
        db_latest_ms = sync_info.get("latest_timestamp_ms")
        db_total_bars = int(sync_info.get("total_candles") or total_bars)
        oldest_loaded_ms = chart_candles[0].timestamp_ms if chart_candles else None
        newest_loaded_ms = chart_candles[-1].timestamp_ms if chart_candles else None
        has_more_before = bool(
            oldest_loaded_ms is not None
            and db_earliest_ms is not None
            and oldest_loaded_ms > db_earliest_ms
        )

        replay_metadata = {
            "is_replay": effective_as_of_index is not None,
            "as_of_index": effective_as_of_index,
            "total_bars": total_bars,
            "db_total_bars": db_total_bars,
            "earliest_timestamp_ms": oldest_loaded_ms,
            "latest_timestamp_ms": newest_loaded_ms,
            "db_earliest_timestamp_ms": db_earliest_ms,
            "db_latest_timestamp_ms": db_latest_ms,
            "current_bar_timestamp_ms": latest_candle.timestamp_ms if latest_candle else None,
        }

        history_metadata = {
            "symbol": clean_symbol,
            "timeframe": tf,
            "loaded_count": len(chart_candles),
            "oldest_loaded_timestamp_ms": oldest_loaded_ms,
            "oldest_loaded_utc": chart_candles[0].datetime_utc.isoformat() if chart_candles else None,
            "newest_loaded_timestamp_ms": newest_loaded_ms,
            "newest_loaded_utc": chart_candles[-1].datetime_utc.isoformat() if chart_candles else None,
            "db_earliest_timestamp_ms": db_earliest_ms,
            "db_earliest_utc": datetime.fromtimestamp(db_earliest_ms / 1000.0, tz=timezone.utc).isoformat() if db_earliest_ms else None,
            "db_latest_timestamp_ms": db_latest_ms,
            "db_latest_utc": datetime.fromtimestamp(db_latest_ms / 1000.0, tz=timezone.utc).isoformat() if db_latest_ms else None,
            "db_total_candles": db_total_bars,
            "has_more_before": has_more_before,
            "next_before_time_ms": oldest_loaded_ms if has_more_before else None,
        }

        return {
            "symbol": clean_symbol,
            "timeframe": timeframe,
            "venue_source": venue_source,
            "feed_status": feed_status,
            "candles": formatted_candles,
            "structural_levels": [lvl.to_dict() for lvl in visible_levels],
            "sweep_events": [evt.to_dict() for evt in detected_events],
            "chart_markers": chart_markers,
            "order_book_overlay": order_book_overlay,
            "liquidation_overlay": liquidation_overlay,
            "replay_metadata": replay_metadata,
            "history_metadata": history_metadata,
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

    def _build_order_book_overlay(self, symbol: str, venue_source: str = "combined") -> Dict[str, Any]:
        """Calculate order-book depth bands and resting walls strictly partitioned across venues."""
        venue = (venue_source or "combined").lower().strip()
        clean_symbol = symbol.strip().upper().replace(".P", "")
        now_ms = int(time.time() * 1000)

        # Retrieve cross-exchange heatmap aggregation for background reference
        heatmap_data = self.cross_aggregator.aggregate_liquidity_heatmap(
            symbol=clean_symbol,
            bucket_bps=5.0,
            venue_filter=venue,
            limit=50,
        )

        venue_summaries = heatmap_data.get("venue_summaries", {})
        walls_list: List[Dict[str, Any]] = []
        target_book: Optional[NormalizedOrderBook] = None
        healthy_books: Dict[str, NormalizedOrderBook] = {}
        contributing_venues: List[str] = []
        venue_display_names = {
            "combined": "Combined (Cross-Exchange)",
            "asterdex": "AsterDEX V3",
            "binance": "Binance Futures",
            "bybit": "Bybit Linear",
        }
        venue_name = venue_display_names.get(venue, venue.upper())
        status = "AVAILABLE"
        error_message = None
        error_code = None
        venue_diagnostics: Dict[str, Any] = {}
        reused_books = getattr(self.cross_aggregator, "last_raw_books", {}).get(clean_symbol, {})

        if venue == "combined":
            # Combined: reuse books already fetched by aggregate_liquidity_heatmap (or fetch if not in cache)
            for vname, adapter in [
                ("asterdex", self.adapter),
                ("binance", self.cross_aggregator.binance_adapter),
                ("bybit", self.cross_aggregator.bybit_adapter),
            ]:
                try:
                    bk = reused_books.get(vname) if vname in reused_books else adapter.get_order_book(clean_symbol, limit=50)
                    if hasattr(adapter, "get_symbol_depth_status"):
                        venue_diagnostics[vname] = adapter.get_symbol_depth_status(clean_symbol)
                    elif hasattr(adapter, "last_depth_status") and adapter.last_depth_status:
                        venue_diagnostics[vname] = dict(adapter.last_depth_status)
                    if bk and bk.bids and bk.asks:
                        healthy_books[vname] = bk
                        contributing_venues.append(vname)
                except Exception as e:
                    logger.warning(f"Error fetching {vname} book for {clean_symbol}: {e}")

            if not healthy_books:
                status = "UNAVAILABLE"
                error_message = f"Order book unavailable across all venues for {clean_symbol}"
                mid = 0.0
                spread = 0.0
                total_bid_notional = 0.0
                total_ask_notional = 0.0
                ts_ms = now_ms
            else:
                valid_mids = [bk.mid_price for bk in healthy_books.values() if bk.mid_price > 0]
                mid = heatmap_data.get("composite_mid") or (sum(valid_mids) / len(valid_mids) if valid_mids else 0.0)
                spread = sum(bk.spread_bps for bk in healthy_books.values()) / len(healthy_books)
                total_bid_notional = sum(sum(b.price * b.size for b in bk.bids) for bk in healthy_books.values())
                total_ask_notional = sum(sum(a.price * a.size for a in bk.asks) for bk in healthy_books.values())
                ts_ms = max(bk.timestamp_ms for bk in healthy_books.values())

                raw_walls: List[Tuple[str, LiquidityWall]] = []
                for vname, bk in healthy_books.items():
                    detected = self.wall_hunter.analyze_order_book(bk)
                    for dw in detected:
                        raw_walls.append((vname, dw))

                merged_walls: List[Dict[str, Any]] = []
                used_indices: Set[int] = set()

                for i, (v1, w1) in enumerate(raw_walls):
                    if i in used_indices:
                        continue
                    cluster = [(v1, w1)]
                    used_indices.add(i)
                    for j, (v2, w2) in enumerate(raw_walls):
                        if j in used_indices:
                            continue
                        if w1.side == w2.side and mid > 0:
                            dist_bps = abs(w1.price_level - w2.price_level) / mid * 10000.0
                            if dist_bps <= 5.0:
                                cluster.append((v2, w2))
                                used_indices.add(j)

                    c_venues = sorted(list({cv for cv, _ in cluster}))
                    total_w_depth = sum(cw.depth_notional_usdt for _, cw in cluster)
                    c_price = (sum(cw.price_level * cw.depth_notional_usdt for _, cw in cluster) / total_w_depth) if total_w_depth > 0 else cluster[0][1].price_level
                    c_depth = total_w_depth
                    c_multiple = max(cw.multiple_of_baseline for _, cw in cluster)
                    c_persistence = max(cw.persistence_seconds for _, cw in cluster)
                    c_dist = abs(mid - c_price) / mid * 10000.0 if mid > 0 else 0.0

                    v_count = len(c_venues)
                    is_cross = v_count >= 2
                    tier = "TRIPLE_CONFIRMED" if v_count >= 3 else ("DUAL_CONFIRMED" if v_count == 2 else "SINGLE_VENUE")

                    merged_walls.append({
                        "side": cluster[0][1].side,
                        "price": round(c_price, 4),
                        "depth_usdt": round(c_depth, 2),
                        "multiple": round(c_multiple, 1),
                        "distance_bps": round(c_dist, 1),
                        "persistence_sec": round(c_persistence, 1),
                        "is_persistent": c_persistence >= 5.0,
                        "venues": c_venues,
                        "venue_count": v_count,
                        "is_cross_confirmed": is_cross,
                        "confirmation_tier": tier,
                    })

                merged_walls.sort(key=lambda x: x["depth_usdt"], reverse=True)
                walls_list = merged_walls

        else:
            # Single venue requested: asterdex, binance, or bybit
            adapter_map = {
                "asterdex": self.adapter,
                "binance": self.cross_aggregator.binance_adapter,
                "bybit": self.cross_aggregator.bybit_adapter,
            }
            ad = adapter_map.get(venue)
            if not ad:
                status = "ERROR"
                error_message = f"Unknown venue source: {venue}"
                mid = 0.0
                spread = 0.0
                total_bid_notional = 0.0
                total_ask_notional = 0.0
                ts_ms = now_ms
            else:
                try:
                    target_book = reused_books.get(venue) if venue in reused_books else ad.get_order_book(clean_symbol, limit=50)
                except Exception as e:
                    target_book = None
                    error_message = f"{venue_name} connection error: {e}"
                    error_code = "NETWORK_ERROR"

                diag = (
                    ad.get_symbol_depth_status(clean_symbol)
                    if hasattr(ad, "get_symbol_depth_status")
                    else (dict(ad.last_depth_status) if hasattr(ad, "last_depth_status") and ad.last_depth_status else {})
                )
                venue_diagnostics[venue] = diag

                if not target_book or not target_book.bids or not target_book.asks:
                    diag_status = str(diag.get("status") or "").upper()
                    error_code = diag.get("error_code") or diag_status or "UNAVAILABLE"
                    if diag_status in ("IP_BANNED", "RATE_LIMITED", "TIMEOUT", "INVALID_SYMBOL", "MALFORMED_RESPONSE", "EMPTY_ORDER_BOOK", "HTTP_ERROR", "CIRCUIT_OPEN"):
                        status = diag_status
                    else:
                        status = "UNAVAILABLE"
                    if not error_message:
                        if diag.get("message"):
                            error_message = str(diag["message"])
                        else:
                            error_message = f"{venue_name} order book unavailable for {clean_symbol} ({error_code})"
                    mid = 0.0
                    spread = 0.0
                    total_bid_notional = 0.0
                    total_ask_notional = 0.0
                    ts_ms = now_ms
                else:
                    if diag.get("is_stale") or str(diag.get("status") or "").upper() == "STALE":
                        status = "STALE"
                        error_code = diag.get("error_code")
                        error_message = diag.get("message")
                    contributing_venues = [venue]
                    mid = target_book.mid_price
                    spread = target_book.spread_bps
                    total_bid_notional = sum(b.price * b.size for b in target_book.bids)
                    total_ask_notional = sum(a.price * a.size for a in target_book.asks)
                    ts_ms = target_book.timestamp_ms
                    detected = self.wall_hunter.analyze_order_book(target_book)
                    for w in detected:
                        walls_list.append({
                            "side": w.side,
                            "price": round(w.price_level, 4),
                            "depth_usdt": round(w.depth_notional_usdt, 2),
                            "multiple": round(w.multiple_of_baseline, 1),
                            "distance_bps": round(w.distance_bps, 1),
                            "persistence_sec": round(w.persistence_seconds, 1),
                            "is_persistent": w.is_persistent,
                            "venues": [venue],
                            "venue_count": 1,
                            "is_cross_confirmed": False,
                            "confirmation_tier": "SINGLE_VENUE",
                        })

        # Calculate depth bands at 10, 25, 50, 100 bps
        band_distances_bps = [10.0, 25.0, 50.0, 100.0]
        bid_bands = []
        ask_bands = []
        active_book = target_book if target_book else (healthy_books.get("asterdex") or next(iter(healthy_books.values()), None) if healthy_books else None)

        if active_book and active_book.bids and active_book.asks and mid > 0:
            for bps in band_distances_bps:
                target_px = mid * (1.0 - bps / 10000.0)
                cum_vol = sum((b.price * b.size) for b in active_book.bids if b.price >= target_px)
                bid_bands.append({"bps": bps, "price": round(target_px, 4), "notional_usdt": round(cum_vol, 2)})

            for bps in band_distances_bps:
                target_px = mid * (1.0 + bps / 10000.0)
                cum_vol = sum((a.price * a.size) for a in active_book.asks if a.price <= target_px)
                ask_bands.append({"bps": bps, "price": round(target_px, 4), "notional_usdt": round(cum_vol, 2)})

        age_sec = max(0.0, (now_ms - ts_ms) / 1000.0)
        if status == "AVAILABLE" and age_sec > 30.0:
            status = "STALE"

        return {
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
            "venue_diagnostics": venue_diagnostics,
            "venue_filter": venue,
            "venue_name": venue_name,
            "mid_price": round(mid, 4) if mid > 0 else None,
            "spread_bps": round(spread, 2),
            "timestamp_ms": ts_ms,
            "age_seconds": round(age_sec, 2),
            "total_bid_depth_usdt": round(total_bid_notional, 2),
            "total_ask_depth_usdt": round(total_ask_notional, 2),
            "bids_depth_bands": bid_bands,
            "asks_depth_bands": ask_bands,
            "walls": walls_list,
            "contributing_venues": contributing_venues,
            "venue_summaries": venue_summaries,
            "composite_mid": round(mid, 4) if mid > 0 else None,
            "bids_buckets": heatmap_data.get("bids_buckets", []),
            "asks_buckets": heatmap_data.get("asks_buckets", []),
            "persistent_walls": heatmap_data.get("persistent_walls", []),
            "distinction_disclosure": heatmap_data.get(
                "distinction_disclosure",
                "ORDER_BOOK_DEPTH: Visible resting maker orders on AsterDEX V3, Binance USD(S)-M Futures, and Bybit Linear. DISTINCT from historical structural liquidity (BSL/SSL) and estimated liquidation clusters."
            ),
        }

    def get_cross_exchange_book(
        self,
        symbol: str,
        bucket_bps: float = 5.0,
        venue_filter: str = "combined",
    ) -> Dict[str, Any]:
        """Direct accessor for the cross-exchange order-book heatmap."""
        return self.cross_aggregator.aggregate_liquidity_heatmap(
            symbol=symbol,
            bucket_bps=bucket_bps,
            venue_filter=venue_filter,
        )

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
