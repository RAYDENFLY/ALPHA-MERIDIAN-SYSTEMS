"""Tests for Screener AI Top 3 Pair Selection (Gemini Argon Autonomous Market Screener).

Validates:
1. Multi-factor quantitative scoring and ranking rubric (0-100 score, 5 factors).
2. Category, bias, and entry readiness schema validation.
3. Missing metric resilience (None/null handling without fabrication).
4. Deterministic fallback when LLM API is unavailable.
5. REST API endpoints (/api/screener/ai-top3/latest and /api/screener/ai-top3/select).
6. UI integration & safety invariants preservation (read-only discovery, zero live orders).
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from src.screener.ai_selector import (
    AITop3SelectionResponse,
    EntryReadiness,
    GeminiArgonPairSelector,
    MarketRegimeSummary,
    PairCandidateOutput,
    SelectionBias,
    SelectionCategory,
)
from src.screener.types import CrossExchangeMarketAsset, VenueMarketMetrics
from src.screener.service import MultiExchangeMarketScreener
from src.api.app import app


@pytest.fixture
def mock_screener():
    screener = MagicMock(spec=MultiExchangeMarketScreener)
    
    # Mock BTC asset
    btc = CrossExchangeMarketAsset(
        normalized_symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        benchmark_price=80500.0,
        price_change_24h_pct=0.75,
        total_volume_24h_usdt=200000000.0,
        total_open_interest_usdt=500000000.0,
        best_spread_bps=0.01,
        venue_count=4,
        cross_listed=True,
    )
    # Mock ETH asset
    eth = CrossExchangeMarketAsset(
        normalized_symbol="ETHUSDT",
        base_asset="ETH",
        quote_asset="USDT",
        benchmark_price=2350.0,
        price_change_24h_pct=1.65,
        total_volume_24h_usdt=220000000.0,
        total_open_interest_usdt=200000000.0,
        best_spread_bps=0.04,
        venue_count=4,
        cross_listed=True,
    )
    # Mock Altcoin 1 (SOL)
    sol = CrossExchangeMarketAsset(
        normalized_symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        benchmark_price=101.50,
        price_change_24h_pct=1.80,
        total_volume_24h_usdt=35000000.0,
        total_open_interest_usdt=150000000.0,
        best_spread_bps=0.95,
        venue_count=4,
        cross_listed=True,
    )
    # Mock Altcoin 2 (DOGE)
    doge = CrossExchangeMarketAsset(
        normalized_symbol="DOGEUSDT",
        base_asset="DOGE",
        quote_asset="USDT",
        benchmark_price=0.0772,
        price_change_24h_pct=1.20,
        total_volume_24h_usdt=6000000.0,
        total_open_interest_usdt=5800000.0,
        best_spread_bps=1.25,
        venue_count=3,
        cross_listed=True,
    )
    # Mock Altcoin 3 (ZEC)
    zec = CrossExchangeMarketAsset(
        normalized_symbol="ZECUSDT",
        base_asset="ZEC",
        quote_asset="USDT",
        benchmark_price=1230.0,
        price_change_24h_pct=5.50,
        total_volume_24h_usdt=20000000.0,
        total_open_interest_usdt=8500000.0,
        best_spread_bps=1.50,
        venue_count=4,
        cross_listed=True,
    )
    
    cached_dict = {
        "BTCUSDT": btc,
        "ETHUSDT": eth,
        "SOLUSDT": sol,
        "DOGEUSDT": doge,
        "ZECUSDT": zec,
    }
    screener._cached_assets = cached_dict
    screener.filter_and_paginate.return_value = {
        "total_matched": len(cached_dict),
        "total_universe_count": len(cached_dict),
        "last_fetch_utc": "2026-10-26T01:00:00Z",
        "data": list(cached_dict.values()),
    }
    return screener


def test_ai_selector_deterministic_fallback_rubric(mock_screener):
    """Verify deterministic multi-factor confluence scoring when LLM is unavailable."""
    selector = GeminiArgonPairSelector(screener=mock_screener)
    
    # Force deterministic fallback by passing an invalid API key / environment override
    with patch.object(selector, "_get_api_key", return_value=None):
        result = selector.select_top3(force_refresh=True)

    assert result["status"] == "SUCCESS"
    assert result["model_used"] == "deterministic_confluence_engine"
    assert result["total_universe_screened"] == 5
    assert len(result["candidates"]) == 3

    # Check Candidate 1
    c1 = result["candidates"][0]
    assert c1["rank"] == 1
    assert 0.0 <= c1["selection_score"] <= 100.0
    assert c1["category"] in [cat.value for cat in SelectionCategory]
    assert c1["bias"] in [b.value for b in SelectionBias]
    assert c1["entry_readiness"] in [r.value for r in EntryReadiness]
    assert len(c1["why_selected"]) > 20
    assert len(c1["what_to_verify_in_inspector"]) > 20
    assert len(c1["invalidation_and_risk"]) > 20

    # Ensure rankings are sequential 1, 2, 3
    ranks = [c["rank"] for c in result["candidates"]]
    assert ranks == [1, 2, 3]


def test_ai_selector_missing_metrics_resilience():
    """Verify that assets with missing/None metrics are handled gracefully without crashing."""
    screener = MagicMock(spec=MultiExchangeMarketScreener)
    # Asset with None price, None spread, None funding, None OI
    sparse_asset = CrossExchangeMarketAsset(
        normalized_symbol="SPARSEUSDT",
        base_asset="SPARSE",
        quote_asset="USDT",
        benchmark_price=None,
        price_change_24h_pct=None,
        total_volume_24h_usdt=800000.0,
        total_open_interest_usdt=None,
        best_spread_bps=None,
        venue_count=1,
    )
    screener._cached_assets = {"SPARSEUSDT": sparse_asset}
    screener.filter_and_paginate.return_value = {
        "total_matched": 1,
        "total_universe_count": 1,
        "last_fetch_utc": "2026-10-26T01:00:00Z",
        "data": [sparse_asset],
    }

    selector = GeminiArgonPairSelector(screener=screener)
    with patch.object(selector, "_get_api_key", return_value=None):
        result = selector.select_top3(force_refresh=True)

    assert result["status"] == "SUCCESS"
    assert len(result["candidates"]) == 1
    c = result["candidates"][0]
    assert c["symbol"] == "SPARSEUSDT"
    assert c["price"] is None
    assert c["open_interest_usdt"] is None
    assert c["spread_bps"] is None
    assert 0.0 <= c["selection_score"] <= 100.0


def test_ai_selector_caching_behavior(mock_screener):
    """Verify that multiple calls without force_refresh reuse cached result."""
    selector = GeminiArgonPairSelector(screener=mock_screener)
    with patch.object(selector, "_get_api_key", return_value=None):
        res1 = selector.select_top3(force_refresh=True)
        call_count_after_first = mock_screener.filter_and_paginate.call_count

        res2 = selector.select_top3(force_refresh=False)
        # Should not call screener filter_and_paginate again due to in-memory TTL
        assert mock_screener.filter_and_paginate.call_count == call_count_after_first
        assert res1["market_snapshot_id"] == res2["market_snapshot_id"]


def test_api_screener_ai_top3_endpoints():
    """Verify FastAPI GET /api/screener/ai-top3/latest and POST /api/screener/ai-top3/select."""
    client = TestClient(app)

    # 1. Test GET latest endpoint
    resp_get = client.get("/api/screener/ai-top3/latest")
    assert resp_get.status_code == 200
    data_get = resp_get.json()
    assert data_get["status"] == "SUCCESS"
    assert "data" in data_get
    assert "candidates" in data_get["data"]
    assert len(data_get["data"]["candidates"]) <= 3

    # 2. Test POST select endpoint
    resp_post = client.post("/api/screener/ai-top3/select", json={"force_refresh": False})
    assert resp_post.status_code == 200
    data_post = resp_post.json()
    assert data_post["status"] == "SUCCESS"
    assert "data" in data_post
    assert "candidates" in data_post["data"]
    assert len(data_post["data"]["candidates"]) <= 3


def test_dashboard_html_and_js_ui_elements():
    """Verify that dashboard.html and market_screener.js contain all required AI Top 3 components."""
    # 1. Check dashboard.html
    with open("src/api/dashboard.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert "screenerAITop3Section" in html
    assert "AI Top 3 Pair Selection" in html
    assert "aiSelectTop3Btn" in html
    assert "triggerAISelectTop3(true)" in html
    assert "aiTop3EmptyState" in html
    assert "aiTop3LoadingState" in html
    assert "aiTop3ErrorState" in html
    assert "aiTop3CandidatesGrid" in html
    assert "Disclaimer:" in html

    # 2. Check market_screener.js
    with open("src/static/js/market_screener.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "triggerAISelectTop3" in js
    assert "loadLatestAITop3" in js
    assert "renderAITop3Selection" in js
    assert "renderCandidateCard" in js
    assert "openCoinInInspector" in js
    assert "/api/screener/ai-top3/select" in js
    assert "/api/screener/ai-top3/latest" in js


def test_safety_invariants_preserved():
    """Verify that paper-only and read-only invariants are maintained."""
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    status_data = resp.json()
    
    # SignalGenerator safety invariants
    assert status_data.get("mode") == "PAPER"
    assert status_data.get("live_trading_locked") is True
    assert status_data.get("paper", {}).get("enabled") is True
