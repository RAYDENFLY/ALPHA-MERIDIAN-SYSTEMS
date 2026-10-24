import pytest
from starlette.testclient import TestClient

from src.api.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestModelBAPIEndpoints:
    def test_get_model_b_status(self, client: TestClient):
        res = client.get("/api/model_b/status")
        assert res.status_code == 200
        data = res.json()
        assert data["model_id"] == "MODEL_B"
        assert "capital" in data
        assert data["capital"]["initial_equity_usdt"] == 100.0
        assert data["capital"]["max_leverage"] == 10
        assert data["risk_limits"]["daily_loss_limit_usdt"] == 3.0
        assert data["risk_limits"]["weekly_loss_limit_usdt"] == 7.5
        assert data["risk_limits"]["max_open_positions"] == 6
        assert data["live_trading_locked"] is True
        assert "paper" in data

    def test_get_model_b_coverage(self, client: TestClient):
        res = client.get("/api/model_b/coverage")
        assert res.status_code == 200
        data = res.json()
        assert data["model_id"] == "MODEL_B"
        assert "venues" in data
        assert "asterdex" in data["venues"]
        assert "binance" in data["venues"]
        assert "hyperliquid" in data["venues"]
        assert "asterdex" in data["verified_venues"]

    def test_get_model_b_setups(self, client: TestClient):
        res = client.get("/api/model_b/setups")
        assert res.status_code == 200
        data = res.json()
        assert data["model_id"] == "MODEL_B"
        assert "setups" in data
        assert isinstance(data["setups"], list)

    def test_get_model_b_journal_and_cohort_tagging(self, client: TestClient):
        res = client.get("/api/model_b/journal")
        assert res.status_code == 200
        data = res.json()
        assert data["model_id"] == "MODEL_B"
        assert "trades" in data
        assert "metrics" in data
        assert "cohort_counts" in data
        assert data["cohort_counts"]["all"] >= 0
        assert data["cohort_counts"]["v1"] >= 0
        assert data["cohort_counts"]["v2"] >= 0

        # Verify every trade has cohort, cohort_version, and cohort_label
        for t in data["trades"]:
            assert "cohort" in t
            assert t["cohort"] in ("V1", "V2")
            assert "cohort_version" in t
            assert "cohort_label" in t

        # Verify cohort filtering endpoint behavior
        res_v1 = client.get("/api/model_b/journal?cohort=V1")
        assert res_v1.status_code == 200
        for t in res_v1.json()["trades"]:
            assert t["cohort"] == "V1"

        res_v2 = client.get("/api/model_b/journal?cohort=V2")
        assert res_v2.status_code == 200
        for t in res_v2.json()["trades"]:
            assert t["cohort"] == "V2"

    def test_get_model_b_orderbook(self, client: TestClient):
        res = client.get("/api/model_b/orderbook/BTCUSDT")
        assert res.status_code == 200
        data = res.json()
        assert data["symbol"] == "BTCUSDT"
        assert "books" in data

    def test_trigger_model_b_scan(self, client: TestClient):
        res = client.post("/api/model_b/scan")
        assert res.status_code == 200
        data = res.json()
        assert "status" in data
        assert "scan_count" in data
        assert "total_evaluated" in data
