"""Unit and Integration tests for FastAPI Application Endpoints.
"""

from fastapi.testclient import TestClient
import pytest
from src.api.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestAPIEndpoints:
    def test_get_dashboard_html(self, client: TestClient):
        response = client.get("/")
        assert response.status_code == 200
        assert "SMC-SNR ML Perpetual Setup Scanner" in response.text
        assert "text/html" in response.headers["content-type"]

    def test_get_status(self, client: TestClient):
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["capital"]["initial_equity_usdt"] == 16.0
        assert data["capital"]["margin_mode"] == "ISOLATED"
        assert data["capital"]["max_leverage"] == 10
        assert data["risk_limits"]["daily_loss_limit_usdt"] == 0.18
        assert data["risk_limits"]["weekly_loss_limit_usdt"] == 0.54
        assert data["risk_limits"]["max_open_positions"] == 1
        assert data["risk_limits"]["max_weekly_entries"] is None
        assert data["live_trading_locked"] is True
        assert "paper" in data
        assert "enabled" in data["paper"]
        assert "open_positions" in data["paper"]
        assert "pending_orders" in data["paper"]

    def test_get_symbols_and_toggle(self, client: TestClient):
        response = client.get("/api/symbols")
        assert response.status_code == 200
        symbols = response.json()
        assert len(symbols) >= 11
        btc = next(s for s in symbols if s["symbol"] == "BTCUSDT")
        assert btc["enabled"] is True

        # Toggle BTC to disabled
        res_toggle = client.post("/api/symbols/BTCUSDT/toggle", json={"enabled": False})
        assert res_toggle.status_code == 200
        assert res_toggle.json()["enabled"] is False

        # Toggle back to enabled
        res_re_enable = client.post("/api/symbols/BTCUSDT/toggle", json={"enabled": True})
        assert res_re_enable.status_code == 200
        assert res_re_enable.json()["enabled"] is True

    def test_get_setups(self, client: TestClient):
        response = client.get("/api/setups")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ("VALID", "NO_TRADE")

    def test_manual_scan(self, client: TestClient):
        response = client.post("/api/scan")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "setups" in data

    def test_get_journal(self, client: TestClient):
        response = client.get("/api/journal")
        assert response.status_code == 200
        data = response.json()
        assert "setups" in data
        assert "trades" in data

    def test_get_trades(self, client: TestClient):
        response = client.get("/api/trades")
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "trades" in data
        assert "paper_orders_count" in data
        assert "paper_orders" in data

    def test_backtest_endpoint(self, client: TestClient):
        response = client.get("/api/backtest?symbols=ADAUSDT")
        assert response.status_code == 200
        data = response.json()
        assert "deterministic" in data
        assert "ml_filtered" in data
        assert data["deterministic"]["initial_equity"] == 16.0
        assert data["ml_filtered"]["initial_equity"] == 16.0

    def test_get_ai_status(self, client: TestClient):
        response = client.get("/api/ai/status")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "mode" in data
        assert "model_id" in data
        assert data["model_id"] == "Qwen/Qwen2.5-7B-Instruct"
