"""Integration tests for AsterDEX Position Intelligence & Market Liquidity API endpoints."""

import pytest
from fastapi.testclient import TestClient
from src.api.app import app


@pytest.fixture
def client():
    return TestClient(app)


class TestIntelligenceAPI:
    """Test suite for /api/intelligence endpoints."""

    def test_analyze_position_endpoint_with_payload(self, client):
        payload = {
            "symbol": "BTCUSDT",
            "position": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "position_amt": 0.1,
                "entry_price": 80000.0,
                "mark_price": 80500.0,
                "stop_loss_price": 78000.0,
                "take_profit_price": 84000.0,
                "liquidation_price": 70000.0,
                "margin_mode": "ISOLATED",
                "allocated_margin_usdt": 800.0,
            },
        }
        resp = client.post("/api/intelligence/analyze_position", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "BTCUSDT"
        assert data["side"] == "LONG"
        assert "thesis_status" in data
        assert "action" in data
        assert "risk" in data
        assert data["risk"]["has_stop_loss"] is True
        assert data["risk"]["has_take_profit"] is True

    def test_analyze_portfolio_endpoint(self, client):
        resp = client.get("/api/intelligence/analyze_portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert "open_positions_count" in data
        assert "total_notional_usdt" in data
        assert "portfolio_health_status" in data
        assert isinstance(data["positions_analyses"], list)

    def test_liquidation_heatmap_endpoint(self, client):
        resp = client.get("/api/intelligence/liquidation_heatmap?symbol=BTCUSDT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "BTCUSDT"
        assert data["is_exchange_reported"] is False
        assert "methodology" in data
        assert isinstance(data["long_clusters"], list)
        assert isinstance(data["short_clusters"], list)

    def test_whale_activity_endpoint(self, client):
        resp = client.get("/api/intelligence/whale_activity?symbol=BTCUSDT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "BTCUSDT"
        assert "net_whale_bias" in data
        assert "active_walls_count" in data
        assert isinstance(data["recent_events"], list)

    def test_market_context_endpoint(self, client):
        resp = client.get("/api/intelligence/market_context?symbol=BTCUSDT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "BTCUSDT"
        assert "structural_indicators" in data
        assert "order_book_depth" in data
        assert "trade_flow" in data
        assert "liquidation_heatmap" in data
        assert "whale_activity" in data
