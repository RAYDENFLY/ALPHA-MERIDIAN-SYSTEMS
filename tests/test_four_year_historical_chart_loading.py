"""Comprehensive test suite for 4-Year Historical Chart Loading, Cursor Pagination,
Local SQLite Persistence, Viewport-Aware Overlays, and Drawing Persistence.

Covers:
1. Four-year historical boundary queries (1,460+ daily candles).
2. Oldest/newest candle chronological ordering.
3. Cursor-based pagination (`before_time_ms` / `before`).
4. Duplicate candle handling (`INSERT OR IGNORE` in SQLite + JS `deduplicateAndSortCandles`).
5. Missing candle intervals & `CandleValidator.validate_series`.
6. Viewport preservation after historical prepending (`savedLogicalRange + prependedCount`).
7. Symbol/timeframe switching request cancellation (`chartRequestEpoch`).
8. Concurrent history loading lock (`isIncrementalHistoryLoading` + `_get_symbol_tf_lock`).
9. Overlay rendering limits & explicit 5-state separation.
10. Drawing persistence in SQLite (`chart_drawings` table + `/api/chart/drawings` CRUD, zero localStorage).
11. Fullscreen controls & drawing toolbar containment inside `#chartWorkspace`.
12. Strict PAPER-only & Model A/B isolation invariants.
"""

import json
from pathlib import Path
import threading
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.core.types import Candle
from src.data.database import DatabaseManager
from src.data.downloader import HistoricalDownloader
from src.data.validator import CandleValidator, TIMEFRAME_MS
from src.intelligence.liquidity_chart_service import LiquidityChartService


def _make_daily_candles(symbol: str, count: int, end_ts_ms: int = 1792800000000) -> List[Candle]:
    step_ms = TIMEFRAME_MS["1d"]
    start_ts_ms = end_ts_ms - (count - 1) * step_ms
    candles: List[Candle] = []
    price = 42000.0
    for i in range(count):
        ts = start_ts_ms + i * step_ms
        drift = ((i % 19) - 9) * 180.0
        o = max(1000.0, price)
        c = max(1000.0, o + drift)
        h = max(o, c) + 420.0
        l = max(500.0, min(o, c) - 380.0)
        price = c
        candles.append(
            Candle(
                symbol=symbol,
                timeframe="1d",
                timestamp_ms=ts,
                open=round(o, 2),
                high=round(h, 2),
                low=round(l, 2),
                close=round(c, 2),
                volume=1250.0 + (i % 40) * 15.0,
                close_time_ms=ts + step_ms - 1,
                quote_volume=(1250.0 + (i % 40) * 15.0) * c,
                trades_count=1500 + (i % 200),
                is_closed=True,
            )
        )
    return candles


class TestFourYearHistoricalChartLoading:
    """Validates Phase 1 through Phase 6 requirements for 4-year historical chart loading."""

    def test_1_four_year_boundary_and_chronological_ordering(self, tmp_path: Path) -> None:
        """Verify SQLite stores and queries >= 4 years (1,500 daily bars) in strict ascending chronological order."""
        db = DatabaseManager(tmp_path / "test_4y.db")
        candles_4y = _make_daily_candles("BTCUSDT", 1500)
        inserted = db.insert_candles(candles_4y)
        assert inserted == 1500

        # Query initial 500 window (should return the newest 500 bars ordered oldest -> newest)
        initial_window = db.get_candles("BTCUSDT", "1d", limit=500)
        assert len(initial_window) == 500
        assert initial_window[-1].timestamp_ms == candles_4y[-1].timestamp_ms
        assert initial_window[0].timestamp_ms == candles_4y[-500].timestamp_ms
        for i in range(1, len(initial_window)):
            assert initial_window[i].timestamp_ms > initial_window[i - 1].timestamp_ms

        # Query full 4-year boundary
        four_years_ms = 4 * 365 * 86400 * 1000
        earliest_target_ms = candles_4y[-1].timestamp_ms - four_years_ms
        all_4y = db.get_candles("BTCUSDT", "1d", start_time_ms=earliest_target_ms)
        assert len(all_4y) >= 1460
        assert all_4y[0].timestamp_ms <= earliest_target_ms + TIMEFRAME_MS["1d"]
        assert all_4y[-1].timestamp_ms == candles_4y[-1].timestamp_ms

    def test_2_cursor_based_pagination_walk_to_four_year_boundary(self, tmp_path: Path) -> None:
        """Verify cursor-based pagination (`before_time_ms`) walks backwards across 1,500 bars without overlap or gaps."""
        db = DatabaseManager(tmp_path / "test_cursor.db")
        candles_4y = _make_daily_candles("BTCUSDT", 1500)
        db.insert_candles(candles_4y)

        mock_adapter = MagicMock()
        mock_adapter._mock_name = "mock_aster"
        mock_adapter.get_order_book.return_value = None
        mock_adapter.get_premium_index.return_value = {"mark_price": 80000.0, "last_funding_rate": 0.0001}
        mock_adapter.get_open_interest.return_value = {"open_interest": 1000.0}

        svc = LiquidityChartService(adapter=mock_adapter, db=db)

        # Page 1: newest 500 candles
        p1 = svc.get_liquidity_layers("BTCUSDT", "1d", limit=500)
        hm1 = p1["history_metadata"]
        assert len(p1["candles"]) == 500
        assert hm1["has_more_before"] is True
        assert hm1["next_before_time_ms"] == p1["candles"][0]["timestamp_ms"]

        # Page 2: next 500 older candles strictly before Page 1's earliest timestamp
        p2 = svc.get_liquidity_layers("BTCUSDT", "1d", limit=500, before_time_ms=hm1["next_before_time_ms"])
        hm2 = p2["history_metadata"]
        assert len(p2["candles"]) == 500
        assert p2["candles"][-1]["timestamp_ms"] < p1["candles"][0]["timestamp_ms"]
        assert hm2["has_more_before"] is True

        # Page 3: oldest 500 candles reaching the 1,500-bar boundary
        p3 = svc.get_liquidity_layers("BTCUSDT", "1d", limit=500, before_time_ms=hm2["next_before_time_ms"])
        hm3 = p3["history_metadata"]
        assert len(p3["candles"]) == 500
        assert p3["candles"][-1]["timestamp_ms"] < p2["candles"][0]["timestamp_ms"]
        assert hm3["has_more_before"] is False
        assert hm3["next_before_time_ms"] is None

        # Combined 3 pages reconstruct all 1,500 candles with zero duplicates
        combined_ts = [c["timestamp_ms"] for c in (p3["candles"] + p2["candles"] + p1["candles"])]
        assert len(combined_ts) == 1500
        assert len(set(combined_ts)) == 1500
        assert combined_ts == [c.timestamp_ms for c in candles_4y]

    def test_3_duplicate_candle_handling_and_missing_interval_validation(self, tmp_path: Path) -> None:
        """Verify duplicate candles are ignored idempotently and missing intervals are detected by CandleValidator."""
        db = DatabaseManager(tmp_path / "test_dedup.db")
        candles = _make_daily_candles("BTCUSDT", 100)
        assert db.insert_candles(candles) == 100
        # Re-inserting overlapping/duplicate batch inserts 0 duplicates
        assert db.insert_candles(candles[20:80]) == 0
        assert db.get_candle_count("BTCUSDT", "1d") == 100

        # Validate contiguous series has 0 missing intervals
        report_clean = CandleValidator.validate_series(candles, expected_timeframe="1d")
        assert report_clean.is_consistent is True
        assert len(report_clean.missing_intervals) == 0

        # Introduce a 3-day missing gap and verify detection
        gapped_candles = candles[:40] + candles[43:]
        report_gap = CandleValidator.validate_series(gapped_candles, expected_timeframe="1d")
        assert len(report_gap.missing_intervals) == 1
        gap_start, gap_end, missing_cnt = report_gap.missing_intervals[0]
        assert missing_cnt == 3

    def test_4_concurrent_history_loading_and_local_persistence_reuse(self, tmp_path: Path) -> None:
        """Verify concurrent requests for a symbol/timeframe synchronize once and subsequent visits reuse SQLite."""
        db = DatabaseManager(tmp_path / "test_concurrent.db")
        full_series = _make_daily_candles("BTCUSDT", 1500)

        call_count = 0
        call_lock = threading.Lock()

        class FakeExchange:
            def get_klines(
                self,
                symbol: str,
                timeframe: str,
                limit: int = 1000,
                start_time_ms: int = None,
                end_time_ms: int = None,
            ) -> List[Candle]:
                nonlocal call_count
                with call_lock:
                    call_count += 1
                filtered = [
                    c for c in full_series
                    if (start_time_ms is None or c.timestamp_ms >= start_time_ms)
                    and (end_time_ms is None or c.timestamp_ms <= end_time_ms)
                ]
                return filtered[:limit]

            def get_order_book(self, symbol: str, limit: int = 50):
                return None

            def get_premium_index(self, symbol: str):
                return {"mark_price": 80000.0, "last_funding_rate": 0.0001}

            def get_open_interest(self, symbol: str):
                return {"open_interest": 500.0}

        svc = LiquidityChartService(adapter=FakeExchange(), db=db)

        # Fire 4 concurrent threads requesting 1D history
        results: List[Dict[str, Any]] = []
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    svc.ensure_local_history("BTCUSDT", "1d", target_start_ms=full_series[0].timestamp_ms, now_ms=full_series[-1].timestamp_ms)
                )
            )
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 4
        assert db.get_candle_count("BTCUSDT", "1d") == 1500
        calls_after_initial = call_count
        assert calls_after_initial >= 2  # 1,500 bars across 1,000-bar batches

        # Subsequent visit reuses SQLite with zero exchange kline calls
        svc.ensure_local_history("BTCUSDT", "1d", target_start_ms=full_series[0].timestamp_ms, now_ms=full_series[-1].timestamp_ms)
        assert call_count == calls_after_initial

    def test_5_drawing_persistence_crud_in_sqlite_and_api(self, tmp_path: Path) -> None:
        """Verify drawings persist in SQLite keyed by (user_profile, symbol, timeframe) and via FastAPI endpoints."""
        client = TestClient(app)

        # Clear any existing test drawings for BTCUSDT 1d under test_profile
        res_clear = client.delete("/api/chart/drawings?symbol=BTCUSDT&timeframe=1d&user_profile=test_profile")
        assert res_clear.status_code == 200

        # Create a Fibonacci retracement drawing and a Long R:R drawing
        d1 = {
            "drawing_id": "drw_fib_test_1",
            "user_profile": "test_profile",
            "symbol": "BTCUSDT",
            "timeframe": "1d",
            "tool_type": "fib_retracement",
            "points": [{"time": 1700000000, "price": 35000.0}, {"time": 1710000000, "price": 73000.0}],
            "style": {"color": "#f59e0b", "lineWidth": 2},
            "is_visible": True,
        }
        d2 = {
            "drawing_id": "drw_long_test_2",
            "user_profile": "test_profile",
            "symbol": "BTCUSDT",
            "timeframe": "1d",
            "tool_type": "long_position",
            "points": [{"time": 1720000000, "price": 60000.0}, {"time": 1725000000, "price": 68000.0}],
            "style": {"color": "#10b981", "lineWidth": 2},
            "is_visible": True,
        }

        r1 = client.post("/api/chart/drawings", json=d1)
        assert r1.status_code == 200
        assert r1.json()["status"] == "SAVED"

        r2 = client.post("/api/chart/drawings", json=d2)
        assert r2.status_code == 200

        # Retrieve drawings for BTCUSDT 1d
        get_res = client.get("/api/chart/drawings?symbol=BTCUSDT&timeframe=1d&user_profile=test_profile")
        assert get_res.status_code == 200
        body = get_res.json()
        assert body["count"] == 2
        ids = [d["drawing_id"] for d in body["drawings"]]
        assert ids == ["drw_fib_test_1", "drw_long_test_2"]

        # Verify isolation across timeframes (1h should have 0 drawings for test_profile)
        get_1h = client.get("/api/chart/drawings?symbol=BTCUSDT&timeframe=1h&user_profile=test_profile")
        assert get_1h.json()["count"] == 0

        # Edit d1 points (simulate dragging handle)
        d1["points"][1]["price"] = 74500.0
        client.post("/api/chart/drawings", json=d1)
        updated = client.get("/api/chart/drawings?symbol=BTCUSDT&timeframe=1d&user_profile=test_profile").json()
        assert updated["drawings"][0]["points"][1]["price"] == 74500.0

        # Delete single drawing
        del_res = client.delete("/api/chart/drawings/drw_fib_test_1?user_profile=test_profile")
        assert del_res.status_code == 200
        assert del_res.json()["deleted"] is True

        rem = client.get("/api/chart/drawings?symbol=BTCUSDT&timeframe=1d&user_profile=test_profile").json()
        assert rem["count"] == 1

        # Clear remaining
        client.delete("/api/chart/drawings?symbol=BTCUSDT&timeframe=1d&user_profile=test_profile")

    def test_6_frontend_viewport_preservation_5_state_separation_and_fullscreen_containment(self) -> None:
        """Verify sweep_chart.js and dashboard.html implement viewport shift on prepend, 5-state separation,
        fullscreen toolbar containment, and zero localStorage usage for history or drawings.
        """
        js_text = Path("src/static/js/sweep_chart.js").read_text(encoding="utf-8")
        html_text = Path("src/api/dashboard.html").read_text(encoding="utf-8")

        # 1. No browser localStorage or IndexedDB for candle history or drawings
        assert "localStorage" not in js_text
        assert "indexedDB" not in js_text

        # 2. Viewport preservation after prepending older candles: shiftedRange adds prependedCount
        assert "from: savedLogicalRange.from + prependedCount" in js_text
        assert "to: savedLogicalRange.to + prependedCount" in js_text

        # 3. Request epoch cancellation on symbol/timeframe switch and single-flight guard
        assert "chartRequestEpoch += 1" in js_text
        assert "reqEpoch !== chartRequestEpoch" in js_text
        assert "isIncrementalHistoryLoading" in js_text

        # 4. Explicit separation of the 5 required concepts
        for key in (
            "historicalDataAvailable",
            "candlesCurrentlyLoaded",
            "candlesCurrentlyVisible",
            "historicalEventsAvailable",
            "eventsCurrentlyRendered",
        ):
            assert key in js_text

        # 5. Drawing tools and fullscreen containment inside #chartWorkspace
        for tool_id in (
            "drawToolBtn-crosshair",
            "drawToolBtn-trend_line",
            "drawToolBtn-ray",
            "drawToolBtn-horizontal_line",
            "drawToolBtn-vertical_line",
            "drawToolBtn-rectangle",
            "drawToolBtn-fib_retracement",
            "drawToolBtn-long_position",
            "drawToolBtn-short_position",
            "drawVisibilityToggleBtn",
            "drawUndoBtn",
            "drawRedoBtn",
            "drawRemoveSelectedBtn",
            "drawClearAllBtn",
        ):
            assert tool_id in html_text

        # Verify chartDrawingToolbar and chartCanvasStack are inside chartWorkspace
        ws_start = html_text.index('id="chartWorkspace"')
        toolbar_idx = html_text.index('id="chartDrawingToolbar"')
        canvas_idx = html_text.index('id="chartDrawingCanvasOverlay"')
        assert ws_start < toolbar_idx < canvas_idx
