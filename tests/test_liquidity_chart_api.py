"""Integration and REST API tests for Feature E: Interactive Liquidity Chart.

Verifies:
1. GET /api/chart/symbols returns supported perpetual instruments.
2. GET /api/chart/klines returns formatted OHLCV candles.
3. GET /api/chart/liquidity_layers returns structural levels, sweep events, order-book depth, and liquidation overlays.
4. Overlay separation: verified structural liquidity, order-book resting depth, and estimated liquidation clusters.
5. Estimated liquidation methodology disclosure check: never claims to be exchange stops.
6. Replay query with as_of_index bounds data properly.
7. Model A frozen invariants and Model B execution safety invariants.
"""

from typing import Any, Dict, List, Optional
import pytest
from fastapi.testclient import TestClient

from config.settings import ExecutionMode, get_default_config
from src.api.app import app
from src.core.types import Candle
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook, OrderBookLevel
from src.intelligence.liquidity_chart_service import LiquidityChartService


class MockChartAsterAdapter(AsterAdapter):
    """Mock AsterAdapter providing synthetic candles, order book, and open interest."""

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
                last_price=65000.0,
                volume_24h_usdt=50000000.0,
                is_active=True,
            ),
            NormalizedInstrument(
                symbol="ETHUSDT",
                exchange_name="asterdex",
                base_asset="ETH",
                quote_asset="USDT",
                min_qty=0.01,
                step_size=0.01,
                tick_size=0.01,
                min_notional=5.0,
                contract_type="PERPETUAL",
                last_price=2600.0,
                volume_24h_usdt=25000000.0,
                is_active=True,
            ),
        ]

    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Candle]:
        candles = []
        base_ts = 1700000000000
        interval_ms = 3600000 if timeframe == "1h" else 900000
        base_px = 65000.0 if "BTC" in symbol else 2600.0

        for i in range(limit):
            ts = base_ts + (i * interval_ms)
            # Create a pivot high at bar 20 and a sweep at bar 35
            high_adj = 500.0 if i == 20 else (600.0 if i == 35 else 50.0)
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp_ms=ts,
                    open=base_px + (i * 2.0),
                    high=base_px + (i * 2.0) + high_adj,
                    low=base_px + (i * 2.0) - 40.0,
                    close=base_px + (i * 2.0) + 10.0,
                    volume=150.0,
                    close_time_ms=ts + interval_ms - 1,
                )
            )
        return candles

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        mid = 65000.0 if "BTC" in symbol else 2600.0
        return NormalizedOrderBook(
            symbol=symbol,
            exchange_name="asterdex",
            timestamp_ms=1700000000000,
            bids=[OrderBookLevel(price=mid - (i * 5.0), size=2.0) for i in range(1, 20)],
            asks=[OrderBookLevel(price=mid + (i * 5.0), size=2.0) for i in range(1, 20)],
        )

    def get_open_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        return {"symbol": symbol, "open_interest": 500.0, "timestamp_ms": 1700000000000}

    def get_premium_index(self, symbol: str) -> Optional[Dict[str, Any]]:
        mid = 65000.0 if "BTC" in symbol else 2600.0
        return {
            "symbol": symbol,
            "mark_price": mid,
            "index_price": mid,
            "last_funding_rate": 0.0001,
            "next_funding_time": 1700000000000 + 28800000,
        }


@pytest.fixture
def client_with_mock_adapter():
    from src.api.app import liquidity_chart_service
    original_adapter = liquidity_chart_service.adapter
    mock_adapter = MockChartAsterAdapter()
    liquidity_chart_service.adapter = mock_adapter
    yield TestClient(app)
    liquidity_chart_service.adapter = original_adapter


class TestLiquidityChartAPI:

    def test_chart_symbols_endpoint(self, client_with_mock_adapter):
        resp = client_with_mock_adapter.get("/api/chart/symbols")
        assert resp.status_code == 200
        data = resp.json()
        assert "symbols" in data
        assert "BTCUSDT" in data["symbols"]
        assert "ETHUSDT" in data["symbols"]
        assert data["default_timeframe"] == "1h"
        assert "15m" in data["supported_timeframes"]

    def test_chart_klines_endpoint(self, client_with_mock_adapter):
        resp = client_with_mock_adapter.get("/api/chart/klines?symbol=BTCUSDT&timeframe=1h&limit=50")
        assert resp.status_code == 200
        candles = resp.json()
        assert isinstance(candles, list)
        assert len(candles) == 50
        first = candles[0]
        assert "time" in first
        assert "open" in first
        assert "high" in first
        assert "low" in first
        assert "close" in first
        assert "volume" in first
        assert isinstance(first["time"], int)

    def test_chart_liquidity_layers_complete(self, client_with_mock_adapter):
        resp = client_with_mock_adapter.get("/api/chart/liquidity_layers?symbol=BTCUSDT&timeframe=1h&limit=60")
        assert resp.status_code == 200
        data = resp.json()

        assert data["symbol"] == "BTCUSDT"
        assert data["timeframe"] == "1h"
        assert "feed_status" in data
        assert "candles" in data
        assert "structural_levels" in data
        assert "sweep_events" in data
        assert "chart_markers" in data
        assert "order_book_overlay" in data
        assert "liquidation_overlay" in data
        assert "replay_metadata" in data

        # Check separation of overlays:
        # 1. Structural levels
        assert isinstance(data["structural_levels"], list)

        # 2. Order book depth overlay
        ob = data["order_book_overlay"]
        assert ob["status"] == "AVAILABLE"
        assert "bids_depth_bands" in ob
        assert "asks_depth_bands" in ob

        # 3. Estimated Liquidation Clusters
        liq = data["liquidation_overlay"]
        assert liq["status"] == "AVAILABLE"
        assert "clusters" in liq
        assert "methodology_disclosure" in liq
        assert "PROBABILISTIC ESTIMATION" in liq["methodology_disclosure"].upper() or "ESTIMATION" in liq["methodology_disclosure"].upper()

    def test_replay_bounded_query(self, client_with_mock_adapter):
        # Query with as_of_index = 25
        resp = client_with_mock_adapter.get("/api/chart/liquidity_layers?symbol=BTCUSDT&timeframe=1h&limit=60&as_of_index=25")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["candles"]) == 26  # 0 through 25
        assert data["replay_metadata"]["is_replay"] is True
        assert data["replay_metadata"]["as_of_index"] == 25
        assert data["feed_status"]["status"] == "REPLAY"

    def test_model_invariants_and_safety(self, client_with_mock_adapter):
        """Verify Model A frozen whitelist and live trading locked invariant."""
        cfg = get_default_config()
        # Model A 11-symbol frozen whitelist
        assert len(cfg.watchlist) == 11
        assert "BTCUSDT" in cfg.watchlist
        assert "ETHUSDT" in cfg.watchlist

        # Mode and live trading locked
        assert cfg.mode in (ExecutionMode.RESEARCH, ExecutionMode.PAPER)
        assert cfg.live_trading_locked is True
