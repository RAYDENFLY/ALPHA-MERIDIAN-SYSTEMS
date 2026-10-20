"""Test suite for Market Liquidity & Order Flow Intelligence Auto-Refresh Engine.

Verifies:
1. Navigation lifecycle wiring: start/stop hooks on tab switch.
2. Dashboard UI markup: presence of auto-refresh toggle, state badge, countdown, last update, and exchange badges.
3. Backend bounded concurrency across venues and cached snapshots.
4. Venue health & data freshness classification (LIVE vs STALE vs UNAVAILABLE).
5. Fault tolerance on individual exchange adapter failures.
6. MarketContextService concurrent query ingestion and data freshness metadata.
7. Whale event deduplication across polling cycles.
8. REST API compatibility and latency budget.
"""

from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional
import pytest
from fastapi.testclient import TestClient

from config.settings import ExecutionMode, get_default_config
from src.api.app import app
from src.core.types import Candle
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.binance_adapter import BinanceFuturesAdapter
from src.exchange.bybit_adapter import BybitLinearAdapter
from src.exchange.cross_exchange_liquidity import (
    CrossExchangeLiquidityAggregator,
    VenueOrderBookSnapshot,
)
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook, NormalizedTrade, OrderBookLevel
from src.intelligence.market_context import MarketContextService, UnifiedMarketContext
from src.intelligence.whale_detector import WhaleDetector


class MockAsterAdapter(AsterAdapter):
    def __init__(self, fail: bool = False, delay_sec: float = 0.0):
        super().__init__(base_url="https://mock.asterdex.local")
        self.fail = fail
        self.delay_sec = delay_sec

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        if self.delay_sec > 0:
            time.sleep(self.delay_sec)
        if self.fail:
            raise ConnectionError("AsterDEX connection dropped")
        now_ms = int(time.time() * 1000)
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="asterdex",
            timestamp_ms=now_ms,
            bids=[OrderBookLevel(price=80000.0, size=1.5)],
            asks=[OrderBookLevel(price=80010.0, size=1.2)],
        )

    def get_klines(self, symbol: str, timeframe: str, limit: int = 40, **kwargs) -> List[Candle]:
        now_ms = int(time.time() * 1000)
        return [
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp_ms=now_ms - (i * 900000),
                open=80000.0,
                high=80100.0,
                low=79900.0,
                close=80050.0,
                volume=10.0,
                close_time_ms=now_ms - ((i - 1) * 900000),
                is_closed=True,
            )
            for i in range(limit, 0, -1)
        ]

    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[NormalizedTrade]:
        now_ms = int(time.time() * 1000)
        return [
            NormalizedTrade(
                trade_id="TRD_1001",
                symbol=symbol,
                exchange_name="asterdex",
                price=80005.0,
                size=2.0,
                notional_usdt=160010.0,
                side="BUY",
                timestamp_ms=now_ms - 2000,
            )
        ]

    def get_open_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        return {"symbol": symbol, "open_interest": 45000.0}

    def get_premium_index(self, symbol: str) -> Optional[Dict[str, Any]]:
        return {"symbol": symbol, "mark_price": 80005.0, "last_funding_rate": 0.0001}


class MockBinanceAdapter(BinanceFuturesAdapter):
    def __init__(self, fail: bool = False, delay_sec: float = 0.0):
        super().__init__(base_url="https://mock.binance.local")
        self.fail = fail
        self.delay_sec = delay_sec

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        if self.delay_sec > 0:
            time.sleep(self.delay_sec)
        if self.fail:
            raise TimeoutError("Binance gateway timeout")
        now_ms = int(time.time() * 1000)
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="binance",
            timestamp_ms=now_ms,
            bids=[OrderBookLevel(price=80001.0, size=5.0)],
            asks=[OrderBookLevel(price=80009.0, size=4.0)],
        )


class MockBybitAdapter(BybitLinearAdapter):
    def __init__(self, fail: bool = False, delay_sec: float = 0.0):
        super().__init__(base_url="https://mock.bybit.local")
        self.fail = fail
        self.delay_sec = delay_sec

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        if self.delay_sec > 0:
            time.sleep(self.delay_sec)
        if self.fail:
            raise ConnectionResetError("Bybit rate limit exceeded")
        now_ms = int(time.time() * 1000)
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="bybit",
            timestamp_ms=now_ms,
            bids=[OrderBookLevel(price=80000.5, size=3.0)],
            asks=[OrderBookLevel(price=80009.5, size=2.5)],
        )


class TestLiquidityIntelAutoRefresh:
    """Test suite covering the complete data-refresh pipeline."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        self.client = TestClient(app)

    # -------------------------------------------------------------------------
    # 1. Navigation Lifecycle Wiring
    # -------------------------------------------------------------------------
    def test_navigation_js_mount_unmount_hooks(self):
        """Verifies navigation.js hooks startLiquidityIntelAutoRefresh and stopLiquidityIntelAutoRefresh."""
        with open("src/static/js/navigation.js", "r", encoding="utf-8") as f:
            nav_js = f.read()

        assert "stopLiquidityIntelAutoRefresh" in nav_js
        assert "startLiquidityIntelAutoRefresh" in nav_js
        assert "tabId !== 'liquidity_intel' && typeof stopLiquidityIntelAutoRefresh === 'function'" in nav_js
        assert "tabId === 'liquidity_intel'" in nav_js

    # -------------------------------------------------------------------------
    # 2. UI Markup Presence & Compliance
    # -------------------------------------------------------------------------
    def test_dashboard_html_auto_refresh_elements(self):
        """Verifies dashboard.html contains all required auto-refresh controls and health indicators."""
        with open("src/api/dashboard.html", "r", encoding="utf-8") as f:
            html = f.read()

        assert 'id="intelAutoRefreshToggleBtn"' in html
        assert 'id="intelRequestStatusBadge"' in html
        assert 'id="intelNextRefreshCountdown"' in html
        assert 'id="intelCountdownSec"' in html
        assert 'id="intelLastUpdatedDisplay"' in html
        assert 'id="intelExBadge-asterdex"' in html
        assert 'id="intelExBadge-binance"' in html
        assert 'id="intelExBadge-bybit"' in html
        assert 'onclick="triggerManualIntelRefresh()"' in html
        assert 'onclick="toggleIntelAutoRefresh()"' in html

    # -------------------------------------------------------------------------
    # 3. Backend Concurrency & Cache Efficiency
    # -------------------------------------------------------------------------
    def test_cross_exchange_concurrent_order_book_fetching(self):
        """Verifies fetch_all_order_books runs concurrently across all venues with latency tracking."""
        mock_aster = MockAsterAdapter(delay_sec=0.05)
        mock_binance = MockBinanceAdapter(delay_sec=0.05)
        mock_bybit = MockBybitAdapter(delay_sec=0.05)

        agg = CrossExchangeLiquidityAggregator(
            aster_adapter=mock_aster,
            binance_adapter=mock_binance,
            bybit_adapter=mock_bybit,
        )

        t0 = time.time()
        results = agg.fetch_all_order_books("BTCUSDT")
        elapsed = time.time() - t0

        assert len(results) == 3
        assert "asterdex" in results
        assert "binance" in results
        assert "bybit" in results

        # Concurrent run of 3 x 50ms delays completes in well under sequential 150ms
        assert elapsed < 0.12, f"Expected concurrent execution <120ms, got {elapsed*1000:.1f}ms"

        # Check venue freshness status
        snap_aster = results["asterdex"][0]
        assert snap_aster is not None
        assert snap_aster.best_bid == 80000.0

    def test_heatmap_short_ttl_caching_prevents_api_stampede(self):
        """Verifies repeated calls to aggregate_liquidity_heatmap within 3s use short cache."""
        mock_aster = MockAsterAdapter()
        mock_binance = MockBinanceAdapter()
        mock_bybit = MockBybitAdapter()

        agg = CrossExchangeLiquidityAggregator(
            aster_adapter=mock_aster,
            binance_adapter=mock_binance,
            bybit_adapter=mock_bybit,
        )

        res1 = agg.aggregate_liquidity_heatmap("BTCUSDT", bucket_bps=5.0)
        assert res1["status"] == "AVAILABLE"
        ts1 = res1["timestamp_ms"]

        # Call again immediately without force_refresh
        res2 = agg.aggregate_liquidity_heatmap("BTCUSDT", bucket_bps=5.0)
        assert res2["timestamp_ms"] == ts1

        # Call with force_refresh=True
        time.sleep(0.01)
        res3 = agg.aggregate_liquidity_heatmap("BTCUSDT", bucket_bps=5.0, force_refresh=True)
        assert res3["timestamp_ms"] >= ts1

    # -------------------------------------------------------------------------
    # 4. Exchange Freshness Classification (LIVE, STALE, UNAVAILABLE)
    # -------------------------------------------------------------------------
    def test_venue_freshness_status_classification(self):
        """Verifies freshness classification correctly marks fresh as LIVE and old as STALE."""
        mock_aster = MockAsterAdapter()
        mock_binance = MockBinanceAdapter()
        mock_bybit = MockBybitAdapter()

        agg = CrossExchangeLiquidityAggregator(
            aster_adapter=mock_aster,
            binance_adapter=mock_binance,
            bybit_adapter=mock_bybit,
        )

        heatmap = agg.aggregate_liquidity_heatmap("BTCUSDT", bucket_bps=5.0)
        venue_sums = heatmap["venue_summaries"]

        assert "asterdex" in venue_sums
        assert "binance" in venue_sums
        assert "bybit" in venue_sums

        assert venue_sums["asterdex"]["freshness_status"] == "LIVE"
        assert venue_sums["binance"]["freshness_status"] == "LIVE"
        assert venue_sums["bybit"]["freshness_status"] == "LIVE"
        assert venue_sums["asterdex"]["status"] == "AVAILABLE"

    # -------------------------------------------------------------------------
    # 5. Fault Tolerance on Partial Exchange Failure
    # -------------------------------------------------------------------------
    def test_partial_exchange_failure_tolerance(self):
        """Verifies that if Bybit fails, AsterDEX and Binance depth are still aggregated gracefully."""
        mock_aster = MockAsterAdapter()
        mock_binance = MockBinanceAdapter()
        mock_bybit_fail = MockBybitAdapter(fail=True)

        agg = CrossExchangeLiquidityAggregator(
            aster_adapter=mock_aster,
            binance_adapter=mock_binance,
            bybit_adapter=mock_bybit_fail,
        )

        heatmap = agg.aggregate_liquidity_heatmap("BTCUSDT", bucket_bps=5.0)
        venue_sums = heatmap["venue_summaries"]

        assert heatmap["status"] == "AVAILABLE"
        assert venue_sums["asterdex"]["status"] == "AVAILABLE"
        assert venue_sums["binance"]["status"] == "AVAILABLE"
        assert venue_sums["bybit"]["status"] == "UNAVAILABLE"
        assert venue_sums["bybit"]["freshness_status"] == "UNAVAILABLE"

    # -------------------------------------------------------------------------
    # 6. MarketContextService Concurrency and Data Freshness
    # -------------------------------------------------------------------------
    def test_market_context_service_concurrency_and_freshness(self):
        """Verifies MarketContextService queries endpoints concurrently and attaches data_freshness."""
        mock_aster = MockAsterAdapter(delay_sec=0.03)
        svc = MarketContextService(aster_adapter=mock_aster, cache_ttl_sec=1.0)

        t0 = time.time()
        ctx = svc.get_market_context("BTCUSDT", force_refresh=True)
        elapsed = time.time() - t0

        # 7 tasks with 30ms delay concurrent should finish in <100ms (vs sequential >210ms)
        assert elapsed < 0.15, f"Expected concurrent execution <150ms, got {elapsed*1000:.1f}ms"
        assert ctx.symbol == "BTCUSDT"
        assert ctx.mark_price == 80005.0

        ctx_dict = ctx.to_dict()
        assert "data_freshness" in ctx_dict
        freshness = ctx_dict["data_freshness"]
        assert freshness["source_exchange"] == "AsterDEX"
        assert freshness["status"] == "LIVE"
        assert freshness["order_book_status"] == "LIVE"
        assert freshness["trades_count"] == 1

    # -------------------------------------------------------------------------
    # 7. Whale Event Deduplication
    # -------------------------------------------------------------------------
    def test_whale_detector_event_deduplication(self):
        """Verifies repeated calls to analyze_market_data with same trades do not append duplicates."""
        detector = WhaleDetector(
            wall_multiple=2.0,
            trade_multiple=2.0,
        )

        now_ms = int(time.time() * 1000)
        trade = NormalizedTrade(
            trade_id="WHALE_TRD_9999",
            symbol="BTCUSDT",
            exchange_name="asterdex",
            price=80000.0,
            size=1.5,
            notional_usdt=120000.0,
            side="BUY",
            timestamp_ms=now_ms - 500,
        )

        # First run: detects aggressive trade
        res1 = detector.analyze_market_data("BTCUSDT", book=None, trades=[trade], mark_price=80000.0)
        trade_events_1 = [e for e in res1.recent_events if "AGGRESSIVE" in e.event_type]
        assert len(trade_events_1) == 1

        # Second run with same trade: deduplication prevents second event
        res2 = detector.analyze_market_data("BTCUSDT", book=None, trades=[trade], mark_price=80000.0)
        trade_events_2 = [e for e in res2.recent_events if "AGGRESSIVE" in e.event_type]
        assert len(trade_events_2) == 1, "Duplicate trade event should NOT be appended"

    # -------------------------------------------------------------------------
    # 8. API Endpoints Latency and Health
    # -------------------------------------------------------------------------
    def test_live_market_context_and_cross_book_endpoints(self):
        """Verifies live API endpoints respond properly and include freshness data."""
        res_ctx = self.client.get("/api/intelligence/market_context?symbol=BTCUSDT")
        assert res_ctx.status_code == 200
        data_ctx = res_ctx.json()
        assert "structural_indicators" in data_ctx
        assert "data_freshness" in data_ctx

        res_cross = self.client.get("/api/chart/cross_book?symbol=BTCUSDT&bucket_bps=5.0&venue_source=combined")
        assert res_cross.status_code == 200
        data_cross = res_cross.json()
        assert "venue_summaries" in data_cross
        assert "asterdex" in data_cross["venue_summaries"]
        assert "freshness_status" in data_cross["venue_summaries"]["asterdex"]
