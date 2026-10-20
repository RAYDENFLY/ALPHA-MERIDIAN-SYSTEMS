import pytest
import time
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from src.screener.types import (
    VenueMarketMetrics,
    CrossExchangeMarketAsset,
    VenueStatusTelemetry,
    ScreenerFilterParams,
    MultiExchangeScreenerResponse
)
from src.screener.service import MultiExchangeMarketScreener


def test_venue_market_metrics_normalization():
    """Verify L1/L2 metric normalization and funding standardization."""
    # 8h funding (Binance / AsterDEX / Bybit standard)
    m8 = VenueMarketMetrics(
        exchange="asterdex",
        symbol="BTCUSDT",
        last_price=80000.0,
        mark_price=80000.0,
        index_price=80005.0,
        volume_24h_usdt=5000000.0,
        open_interest_notional=10000000.0,
        funding_rate=0.0001, # +0.01% per 8h
        funding_interval_hours=8,
        best_bid=79998.0,
        best_ask=80002.0
    )
    assert m8.normalized_8h_funding_rate == pytest.approx(0.0001, abs=1e-7)
    # APR: 0.0001 * 3 * 365 * 100 = 10.95%
    assert m8.annualized_funding_pct == pytest.approx(10.95, abs=0.01)
    # Spread: (80002 - 79998) / 80000 * 10000 = 0.5 bps
    assert m8.spread_bps == pytest.approx(0.5, abs=0.01)

    # 1h funding (Hyperliquid)
    m1 = VenueMarketMetrics(
        exchange="hyperliquid",
        symbol="BTCUSDT",
        last_price=80010.0,
        mark_price=80010.0,
        volume_24h_usdt=2000000.0,
        funding_rate=0.00002, # 0.002% per 1h -> 0.016% per 8h
        funding_interval_hours=1,
        best_bid=80009.0,
        best_ask=80011.0
    )
    assert m1.normalized_8h_funding_rate == pytest.approx(0.00016, abs=1e-7)
    # APR: 0.00002 * 24 * 365 * 100 = 17.52%
    assert m1.annualized_funding_pct == pytest.approx(17.52, abs=0.01)


def test_cross_exchange_asset_aggregation_and_divergence():
    """Verify multi-venue asset aggregation, median benchmark, and price divergence."""
    v1 = VenueMarketMetrics(
        exchange="asterdex",
        symbol="ETHUSDT",
        mark_price=2500.0,
        volume_24h_usdt=1000000.0,
        open_interest_notional=2000000.0,
        funding_rate=0.0001,
        funding_interval_hours=8,
        best_bid=2499.5,
        best_ask=2500.5, # spread = 1.0 -> 4 bps
    )
    v2 = VenueMarketMetrics(
        exchange="binance",
        symbol="ETHUSDT",
        mark_price=2502.5,
        volume_24h_usdt=5000000.0,
        open_interest_notional=10000000.0,
        funding_rate=0.00015,
        funding_interval_hours=8,
        best_bid=2502.4,
        best_ask=2502.6, # spread = 0.2 -> 0.8 bps
    )
    v3 = VenueMarketMetrics(
        exchange="hyperliquid",
        symbol="ETHUSDT",
        mark_price=2505.0,
        volume_24h_usdt=1500000.0,
        open_interest_notional=3000000.0,
        funding_rate=0.00001,
        funding_interval_hours=1, # 0.00008 per 8h
        best_bid=2504.5,
        best_ask=2505.5,
    )

    asset = CrossExchangeMarketAsset(
        normalized_symbol="ETHUSDT",
        base_asset="ETH",
        quote_asset="USDT",
        venues={"asterdex": v1, "binance": v2, "hyperliquid": v3}
    )
    asset.recompute_cross_venue_metrics()

    assert asset.cross_listed is True
    assert asset.venue_count == 3
    # Median price of [2500.0, 2502.5, 2505.0] = 2502.5
    assert asset.benchmark_price == 2502.5
    # Total volume: 1M + 5M + 1.5M = 7.5M
    assert asset.total_volume_24h_usdt == 7500000.0
    assert asset.highest_volume_venue == "binance"
    # Total OI: 2M + 10M + 3M = 15M
    assert asset.total_open_interest_usdt == 15000000.0
    # Price divergence: max 2505 - min 2500 = 5.0
    assert asset.price_divergence_usdt == 5.0
    # Price divergence bps: 5.0 / 2502.5 * 10000 = 19.98 bps
    assert asset.price_divergence_bps == pytest.approx(19.98, abs=0.05)
    # Lowest spread venue should be binance (0.8 bps)
    assert asset.lowest_spread_venue == "binance"
    assert asset.best_spread_bps == pytest.approx(0.8, abs=0.01)


def test_screener_service_filtering_and_sorting():
    """Verify MultiExchangeMarketScreener filter and sorting capabilities."""
    screener = MultiExchangeMarketScreener()

    # Seed mock assets into screener
    a1 = CrossExchangeMarketAsset(
        normalized_symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        total_volume_24h_usdt=50000000.0,
        total_open_interest_usdt=20000000.0,
        benchmark_price=80000.0,
        venues={
            "asterdex": VenueMarketMetrics(exchange="asterdex", symbol="BTCUSDT", mark_price=80000.0, volume_24h_usdt=20000000.0, normalized_8h_funding_rate=0.0001, spread_bps=0.5),
            "binance": VenueMarketMetrics(exchange="binance", symbol="BTCUSDT", mark_price=80010.0, volume_24h_usdt=30000000.0, normalized_8h_funding_rate=0.00012, spread_bps=0.2)
        }
    )
    a1.recompute_cross_venue_metrics()

    a2 = CrossExchangeMarketAsset(
        normalized_symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        total_volume_24h_usdt=800000.0, # Below $1M
        total_open_interest_usdt=1500000.0,
        benchmark_price=160.0,
        venues={
            "binance": VenueMarketMetrics(exchange="binance", symbol="SOLUSDT", mark_price=160.0, volume_24h_usdt=800000.0, normalized_8h_funding_rate=-0.0002, spread_bps=2.0)
        }
    )
    a2.recompute_cross_venue_metrics()

    a3 = CrossExchangeMarketAsset(
        normalized_symbol="DOGEUSDT",
        base_asset="DOGE",
        quote_asset="USDT",
        total_volume_24h_usdt=15000000.0,
        total_open_interest_usdt=5000000.0,
        benchmark_price=0.18,
        venues={
            "asterdex": VenueMarketMetrics(exchange="asterdex", symbol="DOGEUSDT", mark_price=0.18, volume_24h_usdt=5000000.0, normalized_8h_funding_rate=-0.0003, spread_bps=1.5),
            "bybit": VenueMarketMetrics(exchange="bybit", symbol="DOGEUSDT", mark_price=0.1802, volume_24h_usdt=10000000.0, normalized_8h_funding_rate=-0.00025, spread_bps=1.0)
        }
    )
    a3.recompute_cross_venue_metrics()

    screener._cached_assets = {
        "BTCUSDT": a1,
        "SOLUSDT": a2,
        "DOGEUSDT": a3
    }
    screener._last_fetch_time = time.time()

    # 1. Default $1M volume filter: SOLUSDT ($800k) should be excluded
    p_def = ScreenerFilterParams(min_volume_usdt=1000000.0)
    res_def = screener.get_markets(p_def)
    symbols_def = [x["normalized_symbol"] for x in res_def["data"]]
    assert "BTCUSDT" in symbols_def
    assert "DOGEUSDT" in symbols_def
    assert "SOLUSDT" not in symbols_def
    assert res_def["total_matched"] == 2

    # 2. Search filter: search "doge"
    p_search = ScreenerFilterParams(search="doge", min_volume_usdt=0.0)
    res_search = screener.get_markets(p_search)
    assert res_search["total_matched"] == 1
    assert res_search["data"][0]["normalized_symbol"] == "DOGEUSDT"

    # 3. Cross-listed only: SOLUSDT (single venue) excluded
    p_cross = ScreenerFilterParams(cross_listed_only=True, min_volume_usdt=0.0)
    res_cross = screener.get_markets(p_cross)
    assert res_cross["total_matched"] == 2
    assert "SOLUSDT" not in [x["normalized_symbol"] for x in res_cross["data"]]

    # 4. Negative funding filter: SOL and DOGE have negative funding
    p_neg = ScreenerFilterParams(funding_direction="NEGATIVE", min_volume_usdt=0.0)
    res_neg = screener.get_markets(p_neg)
    neg_symbols = [x["normalized_symbol"] for x in res_neg["data"]]
    assert "DOGEUSDT" in neg_symbols
    assert "SOLUSDT" in neg_symbols
    assert "BTCUSDT" not in neg_symbols


def test_screener_resilience_to_exchange_outage():
    """Verify that screener does not crash if one exchange API fails."""
    screener = MultiExchangeMarketScreener()

    with patch.object(screener, "_fetch_bybit_data", side_effect=Exception("Bybit 503 Service Unavailable")), \
         patch.object(screener, "_fetch_hyperliquid_data", side_effect=Exception("Connection Timeout")):
        
        # Should gracefully log error and continue with AsterDEX and Binance
        assets = screener.refresh_all_venues(force=True)
        assert screener._venue_telemetry["bybit"].status == "ERROR"
        assert "503" in screener._venue_telemetry["bybit"].error_message
        assert screener._venue_telemetry["hyperliquid"].status == "ERROR"


def test_screener_safety_governance_invariants():
    """Verify non-execution invariant: zero order placement, strictly read-only."""
    screener = MultiExchangeMarketScreener()

    # Screener must not have any trade execution methods
    assert not hasattr(screener, "place_order")
    assert not hasattr(screener, "send_order")
    assert not hasattr(screener, "execute_trade")
    assert not hasattr(screener, "cancel_order")
    assert not hasattr(screener, "close_position")

    # Output models must not contain entry/SL/TP recommendations
    asset = CrossExchangeMarketAsset(normalized_symbol="BTCUSDT")
    assert not hasattr(asset, "target_entry")
    assert not hasattr(asset, "stop_loss")
    assert not hasattr(asset, "take_profit")
    assert not hasattr(asset, "recommended_action")


def test_screener_api_endpoints():
    """Test FastAPI screener endpoints via TestClient."""
    from src.api.app import app
    client = TestClient(app)

    # 1. GET /api/screener/venues
    r_venues = client.get("/api/screener/venues")
    assert r_venues.status_code == 200
    j_venues = r_venues.json()
    assert "venues" in j_venues
    assert set(j_venues["venues"].keys()) == {"asterdex", "binance", "bybit", "hyperliquid"}

    # 2. GET /api/screener/markets with params
    r_markets = client.get("/api/screener/markets?page=1&page_size=10&min_volume_usdt=1000000")
    assert r_markets.status_code == 200
    j_markets = r_markets.json()
    assert "total_universe_count" in j_markets
    assert "total_matched" in j_markets
    assert "data" in j_markets
    assert len(j_markets["data"]) <= 10
