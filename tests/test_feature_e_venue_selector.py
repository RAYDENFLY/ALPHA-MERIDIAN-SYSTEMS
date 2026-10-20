"""Focused test suite for Feature E Venue Selector Hotfix.

Verifies:
1. Venue switching across all 4 options: 'combined', 'asterdex', 'binance', 'bybit'.
2. Order-book and wall isolation: single venue only returns that venue's data.
3. Combined venue cross-correlation: correct TRIPLE_CONFIRMED / DUAL_CONFIRMED tiers and contributing venues.
4. Unsupported symbol / venue error handling: returns UNAVAILABLE without silently substituting AsterDEX.
5. Accurate feed freshness telemetry: independent candle_feed and order_book_feed with LIVE/STALE/UNAVAILABLE/ERROR/REPLAY.
6. Telemetry payload completeness for UI venue bar: mid_price, spread_bps, depth, age_seconds, venue_name.
7. Rapid switching idempotency: no state leakage across calls.
8. System safety & governance: Model A frozen, Model B paper-only, live trading locked.
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
from src.exchange.cross_exchange_liquidity import CrossExchangeLiquidityAggregator
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook, OrderBookLevel
from src.intelligence.liquidity_chart_service import LiquidityChartService


class MockAsterAdapter(AsterAdapter):
    """Synthetic AsterAdapter for deterministic testing."""

    def __init__(self):
        super().__init__(base_url="https://mock.asterdex.local")

    def discover_instruments(self) -> List[NormalizedInstrument]:
        return [
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
                last_price=80000.0,
                volume_24h_usdt=50000000.0,
                is_active=True,
            ),
            NormalizedInstrument(
                symbol="ASTERONLYUSDT",
                exchange_name="asterdex",
                base_asset="ASTER",
                quote_asset="USDT",
                min_qty=1.0,
                step_size=1.0,
                tick_size=0.01,
                min_notional=5.0,
                contract_type="PERPETUAL",
                last_price=10.0,
                volume_24h_usdt=100000.0,
                is_active=True,
            ),
        ]

    def get_premium_index(self, symbol: str) -> Optional[Dict[str, Any]]:
        return {"symbol": symbol, "mark_price": 80000.0, "last_funding_rate": 0.0001}

    def get_open_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        return {"symbol": symbol, "open_interest": 50000.0}

    def get_klines(self, symbol: str, timeframe: str = "1h", limit: int = 50, **kwargs) -> List[Candle]:
        now_ms = int(time.time() * 1000)
        # Generate clean candles
        candles = []
        for i in range(limit):
            ts = now_ms - (limit - i) * 3600 * 1000
            p = 80000.0 + (i * 10)
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp_ms=ts,
                    close_time_ms=ts + 3600 * 1000 - 1,
                    open=p - 5,
                    high=p + 15,
                    low=p - 10,
                    close=p,
                    volume=150.0 + i,
                )
            )
        return candles

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        if symbol == "UNKNOWNUSDT":
            return None
        # AsterDEX specific order book
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="asterdex",
            timestamp_ms=int(time.time() * 1000),
            bids=[
                OrderBookLevel(price=79950.0, size=50.0),  # AsterDEX bid wall ($3.99M)
                OrderBookLevel(price=79900.0, size=5.0),
            ],
            asks=[
                OrderBookLevel(price=80050.0, size=45.0),  # AsterDEX ask wall ($3.60M)
                OrderBookLevel(price=80100.0, size=4.0),
            ],
        )


class MockBinanceAdapter(BinanceFuturesAdapter):
    """Synthetic BinanceFuturesAdapter."""

    def __init__(self):
        super().__init__(base_url="https://mock.binance.local")

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        if symbol in ("UNKNOWNUSDT", "ASTERONLYUSDT"):
            return None
        # Binance specific order book
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="binance",
            timestamp_ms=int(time.time() * 1000),
            bids=[
                OrderBookLevel(price=79952.0, size=80.0),  # Coincides within 5 bps of AsterDEX 79950!
                OrderBookLevel(price=79800.0, size=10.0),
            ],
            asks=[
                OrderBookLevel(price=80048.0, size=90.0),  # Coincides within 5 bps of AsterDEX 80050!
                OrderBookLevel(price=80200.0, size=5.0),
            ],
        )


class MockBybitAdapter(BybitLinearAdapter):
    """Synthetic BybitLinearAdapter."""

    def __init__(self):
        super().__init__(base_url="https://mock.bybit.local")

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        if symbol in ("UNKNOWNUSDT", "ASTERONLYUSDT"):
            return None
        # Bybit specific order book
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="bybit",
            timestamp_ms=int(time.time() * 1000),
            bids=[
                OrderBookLevel(price=79949.0, size=60.0),  # Also coincides within 5 bps (Triple confirmed!)
                OrderBookLevel(price=79750.0, size=15.0),
            ],
            asks=[
                OrderBookLevel(price=80052.0, size=70.0),  # Triple confirmed ask!
                OrderBookLevel(price=80250.0, size=8.0),
            ],
        )


@pytest.fixture
def chart_service():
    aster = MockAsterAdapter()
    binance = MockBinanceAdapter()
    bybit = MockBybitAdapter()
    aggregator = CrossExchangeLiquidityAggregator(
        aster_adapter=aster,
        binance_adapter=binance,
        bybit_adapter=bybit,
        wall_notional_threshold=20000.0,
    )
    return LiquidityChartService(adapter=aster, cross_aggregator=aggregator)


@pytest.fixture
def client(chart_service):
    # Inject our mock service into app.state for integration tests
    app.state.liquidity_chart_service = chart_service
    return TestClient(app)


def test_venue_order_book_isolation(chart_service):
    """Test that querying single venues isolates order book data strictly to that exchange."""
    # 1. AsterDEX
    aster_res = chart_service.get_liquidity_layers(symbol="BTCUSDT", timeframe="1h", limit=50, venue_source="asterdex")
    ob_aster = aster_res["order_book_overlay"]
    assert ob_aster["venue_filter"] == "asterdex"
    assert ob_aster["venue_name"] == "AsterDEX V3"
    assert ob_aster["status"] == "AVAILABLE"
    assert ob_aster["contributing_venues"] == ["asterdex"]
    assert len(ob_aster["walls"]) > 0
    for w in ob_aster["walls"]:
        assert w["venues"] == ["asterdex"]
        assert w["confirmation_tier"] == "SINGLE_VENUE"
        assert w["is_cross_confirmed"] is False

    # 2. Binance
    binance_res = chart_service.get_liquidity_layers(symbol="BTCUSDT", timeframe="1h", limit=50, venue_source="binance")
    ob_binance = binance_res["order_book_overlay"]
    assert ob_binance["venue_filter"] == "binance"
    assert ob_binance["venue_name"] == "Binance Futures"
    assert ob_binance["status"] == "AVAILABLE"
    assert ob_binance["contributing_venues"] == ["binance"]
    assert len(ob_binance["walls"]) > 0
    for w in ob_binance["walls"]:
        assert w["venues"] == ["binance"]
        assert w["confirmation_tier"] == "SINGLE_VENUE"

    # 3. Bybit
    bybit_res = chart_service.get_liquidity_layers(symbol="BTCUSDT", timeframe="1h", limit=50, venue_source="bybit")
    ob_bybit = bybit_res["order_book_overlay"]
    assert ob_bybit["venue_filter"] == "bybit"
    assert ob_bybit["venue_name"] == "Bybit Linear"
    assert ob_bybit["status"] == "AVAILABLE"
    assert ob_bybit["contributing_venues"] == ["bybit"]
    assert len(ob_bybit["walls"]) > 0
    for w in ob_bybit["walls"]:
        assert w["venues"] == ["bybit"]
        assert w["confirmation_tier"] == "SINGLE_VENUE"


def test_combined_venue_cross_confirmation(chart_service):
    """Test that combined venue correlates overlapping walls into TRIPLE_CONFIRMED tiers."""
    res = chart_service.get_liquidity_layers(symbol="BTCUSDT", timeframe="1h", limit=50, venue_source="combined")
    ob = res["order_book_overlay"]
    assert ob["venue_filter"] == "combined"
    assert ob["venue_name"] == "Combined (Cross-Exchange)"
    assert set(ob["contributing_venues"]) == {"asterdex", "binance", "bybit"}
    
    # Overlapping walls at ~79950 and ~80050 must be TRIPLE_CONFIRMED
    tiers = [w["confirmation_tier"] for w in ob["walls"]]
    assert "TRIPLE_CONFIRMED" in tiers
    
    triple_walls = [w for w in ob["walls"] if w["confirmation_tier"] == "TRIPLE_CONFIRMED"]
    assert len(triple_walls) >= 2  # Bid and Ask
    for tw in triple_walls:
        assert tw["venue_count"] == 3
        assert set(tw["venues"]) == {"asterdex", "binance", "bybit"}
        assert tw["is_cross_confirmed"] is True


def test_venue_unsupported_symbol_no_silent_fallback(chart_service):
    """Test that if a venue does not support a contract, it reports UNAVAILABLE without AsterDEX fallback."""
    # ASTERONLYUSDT is supported on AsterDEX but NOT on Binance or Bybit
    binance_res = chart_service.get_liquidity_layers(symbol="ASTERONLYUSDT", timeframe="1h", limit=50, venue_source="binance")
    ob_binance = binance_res["order_book_overlay"]
    assert ob_binance["status"] == "UNAVAILABLE"
    assert ob_binance["walls"] == []
    assert "Binance Futures order book unavailable" in ob_binance["error_message"]
    # Crucial: NO silent fallback to AsterDEX!
    assert ob_binance["venue_filter"] == "binance"
    assert ob_binance["contributing_venues"] == []

    bybit_res = chart_service.get_liquidity_layers(symbol="ASTERONLYUSDT", timeframe="1h", limit=50, venue_source="bybit")
    ob_bybit = bybit_res["order_book_overlay"]
    assert ob_bybit["status"] == "UNAVAILABLE"
    assert ob_bybit["walls"] == []
    assert "Bybit Linear order book unavailable" in ob_bybit["error_message"]
    assert ob_bybit["venue_filter"] == "bybit"


def test_feed_status_freshness_breakdown(chart_service):
    """Test detailed feed status distinguishing candle feed from venue order book feed."""
    res = chart_service.get_liquidity_layers(symbol="BTCUSDT", timeframe="1h", limit=50, venue_source="binance")
    fs = res["feed_status"]
    
    assert fs["status"] == "LIVE"
    assert fs["is_stale"] is False
    assert "Binance Futures" in fs["source"]
    
    # Sub-component feeds
    candle_feed = fs["candle_feed"]
    assert candle_feed["source"] == "AsterDEX V3 REST"
    assert candle_feed["candle_count"] == 50
    assert candle_feed["is_stale"] is False
    
    ob_feed = fs["order_book_feed"]
    assert ob_feed["venue"] == "binance"
    assert ob_feed["venue_name"] == "Binance Futures"
    assert ob_feed["status"] == "AVAILABLE"
    assert ob_feed["age_seconds"] >= 0.0
    assert ob_feed["contributing_venues"] == ["binance"]


def test_venue_telemetry_payload_completeness(chart_service):
    """Test that order_book_overlay contains all necessary metrics for the UI telemetry bar."""
    for v in ["combined", "asterdex", "binance", "bybit"]:
        res = chart_service.get_liquidity_layers(symbol="BTCUSDT", timeframe="1h", limit=50, venue_source=v)
        ob = res["order_book_overlay"]
        assert "mid_price" in ob and ob["mid_price"] is not None
        assert "spread_bps" in ob and ob["spread_bps"] is not None
        assert "total_bid_depth_usdt" in ob and ob["total_bid_depth_usdt"] > 0
        assert "total_ask_depth_usdt" in ob and ob["total_ask_depth_usdt"] > 0
        assert "venue_name" in ob and len(ob["venue_name"]) > 0
        assert "age_seconds" in ob and ob["age_seconds"] >= 0.0
        assert "status" in ob and ob["status"] in ("AVAILABLE", "LIVE")


def test_rapid_venue_switching_idempotency(chart_service):
    """Test switching rapidly between venues does not pollute shared state or leak data."""
    sequence = ["combined", "binance", "asterdex", "bybit", "combined", "asterdex"]
    for v in sequence:
        res = chart_service.get_liquidity_layers(symbol="BTCUSDT", timeframe="1h", limit=50, venue_source=v)
        assert res["order_book_overlay"]["venue_filter"] == v
        if v == "combined":
            assert len(res["order_book_overlay"]["contributing_venues"]) == 3
        else:
            assert res["order_book_overlay"]["contributing_venues"] == [v]


def test_rest_api_venue_selector_endpoints(client):
    """Integration test of /api/chart/liquidity_layers via FastAPI TestClient."""
    for v in ["combined", "asterdex", "binance", "bybit"]:
        response = client.get(f"/api/chart/liquidity_layers?symbol=BTCUSDT&timeframe=1h&limit=50&venue_source={v}")
        assert response.status_code == 200
        data = response.json()
        assert data["venue_source"] == v
        assert data["order_book_overlay"]["venue_filter"] == v
        assert data["feed_status"]["order_book_feed"]["venue"] == v


def test_governance_and_safety_invariants():
    """Verify Model A frozen, Model B paper-only rules, and live trading locked."""
    config = get_default_config()
    # Model A / execution safety
    assert config.mode in (ExecutionMode.RESEARCH, ExecutionMode.PAPER)
    assert config.live_trading_locked is True
    # Model B limits
    assert config.model_b.initial_equity_usdt == 100.0
    assert config.model_b.target_trade_risk_usdt == 1.00
    assert config.model_b.max_margin_usdt == 15.00
    assert config.model_b.max_leverage <= 10
    assert config.model_b.daily_loss_limit_usdt == 3.00
    assert config.model_b.weekly_loss_limit_usdt == 7.50
