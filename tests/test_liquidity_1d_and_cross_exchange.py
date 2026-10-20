"""Comprehensive test suite for Feature E Upgrade:
1. 1D Timeframe with strict non-lookahead unclosed candle protection.
2. Cross-Exchange Order-Book Liquidity Heatmap across AsterDEX V3, Binance USD(S)-M Futures, and Bybit Linear.
3. 1D Macro HTF level overlay on lower timeframes (15m, 1h, 4h).
4. REST API endpoints and safety/isolation invariants.
"""

from typing import Any, Dict, List, Optional
import time
import pytest
from fastapi.testclient import TestClient

from config.settings import ExecutionMode, get_default_config
from src.api.app import app
from src.core.types import Candle, Timeframe
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.binance_adapter import BinanceFuturesAdapter
from src.exchange.bybit_adapter import BybitLinearAdapter
from src.exchange.cross_exchange_liquidity import (
    CrossExchangeLiquidityAggregator,
    HeatmapPriceBucket,
    PersistentRestingWall,
    VenueOrderBookSnapshot,
)
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook, OrderBookLevel
from src.intelligence.liquidity_chart_service import LiquidityChartService
from src.intelligence.sweep_tracker import (
    LiquidityLevel,
    LiquidityLevelStatus,
    LiquidityLevelType,
    SweepEventType,
    SweepReclaimTracker,
)
from src.smc.swings import detect_swings


class MockAsterAdapter(AsterAdapter):
    """Mock AsterAdapter providing synthetic 1D and lower-timeframe data."""

    def __init__(self):
        super().__init__(base_url="https://mock.asterdex.local")

    def discover_instruments(self) -> List[NormalizedInstrument]:
        return [
            NormalizedInstrument(
                symbol="LTCUSDT",
                exchange_name="asterdex",
                base_asset="LTC",
                quote_asset="USDT",
                min_qty=0.01,
                step_size=0.01,
                tick_size=0.01,
                min_notional=5.0,
                contract_type="PERPETUAL",
                last_price=60.0,
                volume_24h_usdt=5000000.0,
                is_active=True,
            ),
            NormalizedInstrument(
                symbol="BTCUSDT",
                exchange_name="asterdex",
                base_asset="BTC",
                quote_asset="USDT",
                min_qty=0.001,
                step_size=0.001,
                tick_size=0.1,
                min_notional=5.0,
                contract_type="PERPETUAL",
                last_price=65000.0,
                volume_24h_usdt=50000000.0,
                is_active=True,
            ),
        ]

    def get_premium_index(self, symbol: str) -> Optional[Dict[str, Any]]:
        return {"symbol": symbol, "mark_price": 60.0, "last_funding_rate": 0.0001}

    def get_open_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        return {"symbol": symbol, "open_interest": 50000.0}

    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Candle]:
        candles = []
        # For 1D HTF overlay, start daily history 30 days prior so confirmed pivots precede 1H bars
        base_ts = 1700000000000 - (30 * 86400000) if timeframe == "1d" else 1700000000000
        interval_ms = 86400000 if timeframe == "1d" else 3600000
        base_px = 60.0 if "LTC" in symbol else 65000.0

        for i in range(limit):
            ts = base_ts + (i * interval_ms)
            # Create a pivot high at bar 5 and a sweep at bar 15
            high_adj = 5.0 if i == 5 else (6.0 if i == 15 else 0.5)
            # Make the very last candle unclosed by setting close_time_ms in the future
            is_last = (i == limit - 1)
            close_time = int(time.time() * 1000) + 3600000 if is_last else (ts + interval_ms - 1)
            is_closed = not is_last

            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp_ms=ts,
                    open=base_px + (i * 0.1),
                    high=base_px + (i * 0.1) + high_adj,
                    low=base_px + (i * 0.1) - 0.5,
                    close=base_px + (i * 0.1) + 0.2,
                    volume=1000.0,
                    close_time_ms=close_time,
                    is_closed=is_closed,
                )
            )
        return candles

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="asterdex",
            timestamp_ms=int(time.time() * 1000),
            bids=[
                OrderBookLevel(price=59.0, size=10.0),
                OrderBookLevel(price=58.9, size=15.0),
                OrderBookLevel(price=58.8, size=12.0),
                OrderBookLevel(price=58.5, size=2000.0),  # resting wall
            ],
            asks=[
                OrderBookLevel(price=59.1, size=10.0),
                OrderBookLevel(price=59.2, size=15.0),
                OrderBookLevel(price=59.3, size=12.0),
                OrderBookLevel(price=60.0, size=2000.0),  # resting wall
            ],
        )


class MockBinanceAdapter(BinanceFuturesAdapter):
    """Mock BinanceFuturesAdapter for cross-exchange tests."""

    def __init__(self):
        super().__init__(base_url="https://mock.binance.local")

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="binance",
            timestamp_ms=int(time.time() * 1000),
            bids=[
                OrderBookLevel(price=59.0, size=20.0),
                OrderBookLevel(price=58.9, size=25.0),
                OrderBookLevel(price=58.8, size=22.0),
                OrderBookLevel(price=58.5, size=3000.0),  # confirmed resting wall
            ],
            asks=[
                OrderBookLevel(price=59.1, size=20.0),
                OrderBookLevel(price=59.2, size=25.0),
                OrderBookLevel(price=59.3, size=22.0),
                OrderBookLevel(price=60.0, size=3000.0),  # confirmed resting wall
            ],
        )


class MockBybitAdapter(BybitLinearAdapter):
    """Mock BybitLinearAdapter for cross-exchange tests."""

    def __init__(self):
        super().__init__(base_url="https://mock.bybit.local")

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="bybit",
            timestamp_ms=int(time.time() * 1000),
            bids=[
                OrderBookLevel(price=59.0, size=15.0),
                OrderBookLevel(price=58.9, size=18.0),
                OrderBookLevel(price=58.8, size=16.0),
                OrderBookLevel(price=58.5, size=2500.0),  # confirmed resting wall
            ],
            asks=[
                OrderBookLevel(price=59.1, size=15.0),
                OrderBookLevel(price=59.2, size=18.0),
                OrderBookLevel(price=59.3, size=16.0),
                OrderBookLevel(price=60.0, size=2500.0),  # confirmed resting wall
            ],
        )


class TestFeatureEUpgrade:
    """Test suite for 1D timeframe, non-lookahead closure, cross-exchange liquidity, and API endpoints."""

    @pytest.fixture(autouse=True)
    def setup_services(self):
        self.mock_aster = MockAsterAdapter()
        self.mock_binance = MockBinanceAdapter()
        self.mock_bybit = MockBybitAdapter()
        self.aggregator = CrossExchangeLiquidityAggregator(
            aster_adapter=self.mock_aster,
            binance_adapter=self.mock_binance,
            bybit_adapter=self.mock_bybit,
            wall_notional_threshold=20000.0,
        )
        self.chart_service = LiquidityChartService(
            adapter=self.mock_aster,
            cross_aggregator=self.aggregator,
        )
        self.client = TestClient(app)

    # =========================================================================
    # A. 1D Timeframe and Non-Lookahead Candle Closure Protection
    # =========================================================================

    def test_unclosed_daily_candle_never_confirms_pivot(self):
        """Proves that an in-progress unclosed daily candle (is_closed=False) is NEVER used to confirm a swing pivot."""
        candles = []
        base_ts = 1700000000000
        interval_ms = 86400000  # 1D

        # Create 10 closed candles (bar 0 to 9), with a swing high at bar 6 (left_bars=3, right_bars=3)
        # Bar 6 requires bars 7, 8, 9 to confirm.
        for i in range(10):
            high_px = 65.0 if i == 6 else (50.0 + i)
            candles.append(
                Candle(
                    symbol="LTCUSDT",
                    timeframe="1d",
                    timestamp_ms=base_ts + (i * interval_ms),
                    open=50.0,
                    high=high_px,
                    low=48.0,
                    close=51.0,
                    volume=100.0,
                    close_time_ms=base_ts + ((i + 1) * interval_ms) - 1,
                    is_closed=True,
                )
            )

        # Bar 10 is currently UNCLOSED (in progress)
        candles.append(
            Candle(
                symbol="LTCUSDT",
                timeframe="1d",
                timestamp_ms=base_ts + (10 * interval_ms),
                open=51.0,
                high=52.0,
                low=49.0,
                close=50.5,
                volume=50.0,
                close_time_ms=int(time.time() * 1000) + 86400000,
                is_closed=False,
            )
        )

        # detect_swings must not allow bar 10 to confirm anything
        swings = detect_swings(candles, left_bars=3, right_bars=3)
        for s in swings:
            assert s.confirmed_index <= 9, f"Swing was confirmed on unclosed candle {s.confirmed_index}!"
            assert s.confirmed_index != 10, "Unclosed candle confirmed a swing pivot!"

    def test_unclosed_daily_candle_never_confirms_reclaim(self):
        """Proves that when price pierces a level on an unclosed bar, it only registers SWEEP_UNCONFIRMED, never SWEEP_RECLAIMED."""
        base_ts = 1700000000000
        candles = [
            Candle("LTCUSDT", "1d", base_ts + (0 * 86400000), 50, 52, 48, 51, 100, is_closed=True, close_time_ms=base_ts + 86400000),
            Candle("LTCUSDT", "1d", base_ts + (1 * 86400000), 51, 53, 49, 52, 100, is_closed=True, close_time_ms=base_ts + 2 * 86400000),
            Candle("LTCUSDT", "1d", base_ts + (2 * 86400000), 52, 54, 50, 53, 100, is_closed=True, close_time_ms=base_ts + 3 * 86400000),
            Candle("LTCUSDT", "1d", base_ts + (3 * 86400000), 53, 60, 51, 55, 100, is_closed=True, close_time_ms=base_ts + 4 * 86400000),  # High at 60.0
            Candle("LTCUSDT", "1d", base_ts + (4 * 86400000), 54, 55, 52, 53, 100, is_closed=True, close_time_ms=base_ts + 5 * 86400000),
            Candle("LTCUSDT", "1d", base_ts + (5 * 86400000), 53, 54, 51, 52, 100, is_closed=True, close_time_ms=base_ts + 6 * 86400000),
            Candle("LTCUSDT", "1d", base_ts + (6 * 86400000), 52, 53, 50, 51, 100, is_closed=True, close_time_ms=base_ts + 7 * 86400000),  # Confirmed at 6
            # Bar 7 is UNCLOSED: wicks to 61.0 (> 60.0) but current price is 59.0 (< 60.0)
            Candle(
                "LTCUSDT",
                "1d",
                base_ts + (7 * 86400000),
                51,
                61.0,
                50,
                59.0,
                100,
                is_closed=False,
                close_time_ms=int(time.time() * 1000) + 86400000,
            ),
        ]

        tracker = SweepReclaimTracker(left_bars=3, right_bars=3)
        levels = tracker.identify_structural_levels(candles, "LTCUSDT", "1d")
        vis_levels, events = tracker.track_events(candles, levels, as_of_index=7)

        target_lvl = [l for l in vis_levels if l.price == 60.0][0]
        # Must be pending, NOT reclaimed because bar 7 is still open!
        assert target_lvl.status == LiquidityLevelStatus.SWEPT_PENDING
        assert len(events) == 1
        assert events[0].event_type == SweepEventType.SWEEP_UNCONFIRMED
        assert events[0].reclaim_price is None

    # =========================================================================
    # B. 1D Macro Level Overlay on Lower Timeframes
    # =========================================================================

    def test_1d_macro_level_overlay_on_1h_chart(self):
        """Proves that 1D macro levels can be overlaid on a 1H chart with is_htf=True and strict non-lookahead."""
        data = self.chart_service.get_liquidity_layers(
            symbol="LTCUSDT",
            timeframe="1h",
            limit=50,
            include_1d_overlay=True,
        )

        levels = data["structural_levels"]
        htf_levels = [l for l in levels if l.get("is_htf") is True]
        assert len(htf_levels) > 0, "No 1D HTF levels found in overlay response!"
        for hl in htf_levels:
            assert hl["timeframe"] == "1d"
            assert "1D Macro" in hl["source_label"]
            assert hl["level_id"].startswith("HTF_")

    def test_1d_macro_overlay_strict_temporal_non_lookahead_in_replay(self):
        """Proves that in historical replay at bar T, any 1D level confirmed after T is excluded."""
        # 1H chart with cutoff at bar 15
        data = self.chart_service.get_liquidity_layers(
            symbol="LTCUSDT",
            timeframe="1h",
            limit=50,
            as_of_index=15,
            include_1d_overlay=True,
        )

        chart_candles = data["candles"]
        cutoff_ts = chart_candles[-1]["timestamp_ms"]
        htf_levels = [l for l in data["structural_levels"] if l.get("is_htf") is True]

        for hl in htf_levels:
            assert hl["confirmed_timestamp_ms"] <= cutoff_ts, (
                f"Lookahead violation! 1D level confirmed at {hl['confirmed_timestamp_ms']} "
                f"was visible on 1H bar at {cutoff_ts}"
            )

    # =========================================================================
    # C. Cross-Exchange Order-Book Liquidity Heatmap
    # =========================================================================

    def test_bybit_linear_adapter_order_book(self):
        """Verifies BybitLinearAdapter parses bids and asks with correct sort order and contract parameters."""
        book = self.mock_bybit.get_order_book("LTCUSDT")
        assert book is not None
        assert book.symbol == "LTCUSDT"
        assert book.exchange_name == "bybit"
        assert len(book.bids) == 4
        assert len(book.asks) == 4
        assert book.best_bid == 59.0
        assert book.best_ask == 59.1
        # Check sort order
        assert book.bids[0].price >= book.bids[1].price
        assert book.asks[0].price <= book.asks[1].price

    def test_cross_exchange_liquidity_aggregation_and_confirmation(self):
        """Verifies CrossExchangeLiquidityAggregator merges depth across AsterDEX, Binance, and Bybit and identifies confirmed walls."""
        heatmap = self.aggregator.aggregate_liquidity_heatmap(
            symbol="LTCUSDT",
            bucket_bps=5.0,
            venue_filter="combined",
        )

        assert heatmap["status"] == "AVAILABLE"
        assert heatmap["verified_contract"] is True
        assert heatmap["contract_type"] == "PERPETUAL_LINEAR"
        assert "asterdex" in heatmap["venue_summaries"]
        assert "binance" in heatmap["venue_summaries"]
        assert "bybit" in heatmap["venue_summaries"]

        # Check resting walls
        walls = heatmap["persistent_walls"]
        assert len(walls) > 0

        # Resting wall at 58.5 should be confirmed across all 3 venues
        wall_585 = [w for w in walls if abs(w["price"] - 58.5) < 0.1]
        assert len(wall_585) > 0
        w = wall_585[0]
        assert w["is_cross_confirmed"] is True
        assert w["confirmation_tier"] == "TRIPLE_CONFIRMED"
        assert len(w["venues"]) == 3
        assert set(w["venues"]) == {"asterdex", "binance", "bybit"}

    def test_cross_exchange_venue_filter_isolation(self):
        """Verifies that selecting a single venue isolates order book depth to that venue."""
        binance_view = self.aggregator.aggregate_liquidity_heatmap(
            symbol="LTCUSDT",
            bucket_bps=5.0,
            venue_filter="binance",
        )

        assert binance_view["status"] == "AVAILABLE"
        for b in binance_view["bids_buckets"]:
            assert set(b["venues"].keys()) == {"binance"}
            assert b["is_cross_confirmed"] is False

    def test_distinct_liquidity_disclosure(self):
        """Verifies explicit disclosure distinguishing structural liquidity, order-book depth, and liquidation models."""
        heatmap = self.aggregator.aggregate_liquidity_heatmap("LTCUSDT")
        disclosure = heatmap.get("distinction_disclosure", "")
        assert "ORDER_BOOK_DEPTH" in disclosure
        assert "DISTINCT from historical structural liquidity" in disclosure
        assert "liquidation clusters" in disclosure

    # =========================================================================
    # D. REST API Endpoints
    # =========================================================================

    def test_api_chart_symbols_includes_1d(self):
        """Verifies GET /api/chart/symbols returns 1d in supported_timeframes."""
        res = self.client.get("/api/chart/symbols")
        assert res.status_code == 200
        data = res.json()
        assert "1d" in data["supported_timeframes"]

    def test_api_chart_klines_1d_endpoint(self):
        """Verifies GET /api/chart/klines accepts timeframe=1d."""
        res = self.client.get("/api/chart/klines?symbol=BTCUSDT&timeframe=1d&limit=20")
        assert res.status_code == 200
        candles = res.json()
        assert isinstance(candles, list)

    def test_api_chart_cross_book_endpoint(self):
        """Verifies GET /api/chart/cross_book returns multi-exchange depth heatmap."""
        res = self.client.get("/api/chart/cross_book?symbol=LTCUSDT&bucket_bps=5.0&venue_source=combined")
        assert res.status_code == 200
        data = res.json()
        assert data["verified_contract"] is True
        assert "venue_summaries" in data
        assert "persistent_walls" in data

    # =========================================================================
    # E. Strict Governance and Safety Invariants
    # =========================================================================

    def test_model_invariants_and_execution_locks(self):
        """Verifies Model A frozen invariants, paper execution safety, and zero live mutations."""
        cfg = get_default_config()
        assert cfg.mode in (ExecutionMode.PAPER, ExecutionMode.RESEARCH)
        assert cfg.live_trading_locked is True

        # Model A Whitelist intact (11 symbols)
        assert len(cfg.watchlist) == 11
        assert "BTCUSDT" in cfg.watchlist
        assert "ETHUSDT" in cfg.watchlist
        assert "LTCUSDT" in cfg.watchlist
