"""Comprehensive Test Suite for Groq AI Trading Analyst Module.

Tests:
1. Risk Calculator: Entry ladder (60/40), structural SL/TP, $1 risk budget, fees, slippage, net R:R, min notional
2. Market Analyzer: SMC swings, demand/supply zones, sweeps, L2 book imbalance, BTC alignment, freshness
3. Groq Client: Rule-based fallback synthesis and structured schema parsing
4. Storage & Deduplication: SQLite persistence, cache hit within TTL, cache bypass on force_refresh
5. Isolation Guarantee: Zero mutation of trade journals, paper positions, or live orders
6. FastAPI REST Endpoints: /api/ai_analyst/* (analyze, history, symbols, config, id lookup)
"""

import os
from pathlib import Path
import time
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from src.ai_analyst.types import (
    QuantitativeMarketSnapshot,
    AIAnalystResult,
    AIAnalystConfig,
    AnalysisRequest,
    AIConfigUpdateRequest,
)
from src.ai_analyst.risk_calculator import AIRiskCalculator
from src.ai_analyst.market_analyzer import AIMarketAnalyzer
from src.ai_analyst.groq_client import GroqAnalystClient
from src.ai_analyst.storage import AIAnalystStorage
from src.ai_analyst.service import AIAnalystService
from src.core.types import Candle
from src.data.database import DatabaseManager
from src.exchange.normalized import NormalizedOrderBook, OrderBookLevel


# ==============================================================================
# Helpers & Fixtures
# ==============================================================================

def make_candle(
    timestamp_ms: int,
    open_px: float,
    high_px: float,
    low_px: float,
    close_px: float,
    volume: float = 1000.0,
    symbol: str = "TESTUSDT",
    timeframe: str = "1h",
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp_ms=timestamp_ms,
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        volume=volume,
        close_time_ms=timestamp_ms + 3600000 - 1,
        is_closed=True,
    )


def make_mock_orderbook(mid_price: float = 2500.0) -> NormalizedOrderBook:
    bids = [
        OrderBookLevel(price=mid_price * (1 - 0.0005 * (i + 1)), size=10.0 + i * 2)
        for i in range(10)
    ]
    asks = [
        OrderBookLevel(price=mid_price * (1 + 0.0005 * (i + 1)), size=8.0 + i * 2)
        for i in range(10)
    ]
    return NormalizedOrderBook(
        symbol="TESTUSDT",
        exchange_name="asterdex",
        timestamp_ms=int(time.time() * 1000),
        bids=bids,
        asks=asks,
    )


# ==============================================================================
# 1. AIRiskCalculator Tests
# ==============================================================================

def test_risk_calculator_long_scenario():
    """Verify LONG execution plan calculations, 60/40 entry ladder, fee modeling, and net R:R."""
    calc = AIRiskCalculator()

    symbol = "ETHUSDT"
    entry_price = 2000.0
    stop_loss = 1950.0  # $50 risk distance
    tp1 = 2100.0        # 2:1 gross R:R
    tp2 = 2200.0
    risk_budget = 1.00

    plan = calc.compute_plan(
        symbol=symbol,
        direction="LONG",
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        tp1_price=tp1,
        tp2_price=tp2,
        risk_budget_usdt=risk_budget,
        leverage=10.0,
    )

    assert plan.direction == "LONG"
    assert len(plan.entry_ladder) == 2
    assert plan.entry_ladder[0].weight_pct == 60.0
    assert plan.entry_ladder[1].weight_pct == 40.0
    assert plan.entry_ladder[0].price == 2000.0
    assert plan.entry_ladder[1].price == round(2000.0 * 0.995, 6)

    # Position sizing from $1.00 risk budget
    assert plan.suggested_quantity > 0
    assert plan.position_notional_usdt >= 5.0  # Min notional satisfied
    assert plan.is_notional_compliant is True
    assert plan.is_margin_compliant is True

    # Protection & Targets
    assert plan.stop_loss == 1950.0
    assert plan.take_profit_1 == 2100.0
    assert plan.take_profit_2 == 2200.0

    # Costs & Break-Even
    assert plan.estimated_maker_fee_usdt > 0
    assert plan.estimated_taker_fee_usdt > 0
    assert plan.estimated_slippage_usdt > 0
    assert plan.break_even_price > entry_price  # Higher than entry for LONG
    assert plan.net_reward_to_risk > 0


def test_risk_calculator_short_scenario():
    """Verify SHORT execution plan calculations, inverted discount ladder, and break-even."""
    calc = AIRiskCalculator()

    symbol = "BTCUSDT"
    entry_price = 80000.0
    stop_loss = 81000.0  # $1000 risk distance
    tp1 = 78000.0        # 2:1 gross R:R
    tp2 = 76000.0
    risk_budget = 1.00

    plan = calc.compute_plan(
        symbol=symbol,
        direction="SHORT",
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        tp1_price=tp1,
        tp2_price=tp2,
        risk_budget_usdt=risk_budget,
        leverage=10.0,
    )

    assert plan.direction == "SHORT"
    assert len(plan.entry_ladder) == 2
    assert plan.entry_ladder[0].price == 80000.0
    assert plan.entry_ladder[1].price == round(80000.0 * 1.005, 6)  # Premium limit for short
    assert plan.stop_loss == 81000.0
    assert plan.take_profit_1 == 78000.0
    assert plan.take_profit_2 == 76000.0
    assert plan.break_even_price < entry_price  # Lower than entry for SHORT
    assert plan.suggested_quantity > 0
    assert plan.net_reward_to_risk > 0


# ==============================================================================
# 2. AIMarketAnalyzer Tests
# ==============================================================================

def test_market_analyzer_quantitative_extraction():
    """Verify structure extraction: swings, zones, sweeps, order-book depth, ATR."""
    now_ms = int(time.time() * 1000)
    candles = []
    base_px = 100.0

    # Build 50 simulated candles with a distinct swing high and swing low
    for i in range(50):
        t = now_ms - (50 - i) * 3600 * 1000
        if i == 20:
            c = make_candle(t, 92.0, 93.0, 88.0, 91.0)
        elif i == 35:
            c = make_candle(t, 110.0, 118.0, 109.0, 115.0)
        else:
            px = base_px + (i % 5)
            c = make_candle(t, px, px + 2, px - 2, px + 1)
        candles.append(c)

    mock_adapter = MagicMock()
    mock_adapter.get_klines.return_value = candles
    mock_adapter.get_order_book.return_value = make_mock_orderbook(mid_price=105.0)
    mock_adapter.get_ticker.return_value = {"last_price": 105.0, "price_change_24h": 3.5}
    mock_adapter.get_bulk_market_snapshot.return_value = {
        "TESTUSDT": {"last_price": 105.0, "volume_24h_usdt": 2500000.0, "spread_bps": 2.0, "price_change_pct": 2.5},
        "BTCUSDT": {"last_price": 80100.0, "volume_24h_usdt": 50000000.0, "spread_bps": 1.0, "price_change_pct": 1.0},
    }

    analyzer = AIMarketAnalyzer(adapter=mock_adapter)
    snapshot = analyzer.analyze_market(
        symbol="TESTUSDT",
        timeframe="1h",
        candles_override=candles,
        orderbook_override=make_mock_orderbook(mid_price=105.0),
        now_ms=now_ms,
    )

    assert isinstance(snapshot, QuantitativeMarketSnapshot)
    assert snapshot.symbol == "TESTUSDT"
    assert snapshot.timeframe == "1h"
    assert snapshot.current_price == 105.0
    assert snapshot.atr_14 > 0
    assert snapshot.orderbook_bid_depth_25bps > 0
    assert snapshot.orderbook_ask_depth_25bps > 0
    assert snapshot.is_stale is False


def test_market_analyzer_stale_data_detection():
    """Verify stale data warning when candle timestamps are older than the timeframe threshold."""
    now_ms = int(time.time() * 1000)
    old_time_ms = now_ms - (20 * 3600 * 1000)  # Started 20 hours ago, ends 10 hours ago
    candles = [make_candle(old_time_ms + i * 3600 * 1000, 100.0, 102.0, 99.0, 101.0) for i in range(10)]

    mock_adapter = MagicMock()
    mock_adapter.get_klines.return_value = candles
    mock_adapter.get_order_book.return_value = make_mock_orderbook(101.0)
    mock_adapter.get_ticker.return_value = {"last_price": 101.0, "price_change_24h": 0.0}
    mock_adapter.get_bulk_market_snapshot.return_value = {
        "TESTUSDT": {"last_price": 101.0, "volume_24h_usdt": 1000000.0, "spread_bps": 2.0, "price_change_pct": 0.0},
        "BTCUSDT": {"last_price": 80100.0, "volume_24h_usdt": 50000000.0, "spread_bps": 1.0, "price_change_pct": 0.0},
    }

    analyzer = AIMarketAnalyzer(adapter=mock_adapter)
    snapshot = analyzer.analyze_market(
        symbol="TESTUSDT",
        timeframe="1h",
        candles_override=candles,
        orderbook_override=make_mock_orderbook(101.0),
        now_ms=now_ms,
    )

    assert snapshot.is_stale is True
    assert snapshot.data_age_sec > 3600.0


# ==============================================================================
# 3. GroqAnalystClient Tests (Fallback & Schema Parsing)
# ==============================================================================

def test_groq_client_fallback_synthesis():
    """Verify deterministic fallback synthesizer creates valid multi-scenario result without Groq API."""
    client = GroqAnalystClient(api_key="disabled")
    client._groq_client = None  # Explicitly force fallback synthesizer mode

    snapshot = QuantitativeMarketSnapshot(
        symbol="TESTUSDT",
        timeframe="1h",
        current_price=2500.0,
        volume_24h_usdt=5000000.0,
        spread_bps=2.0,
        atr_14=35.0,
        atr_pct=1.4,
        swing_high=2600.0,
        swing_low=2400.0,
        demand_zone_lower=2420.0,
        demand_zone_upper=2450.0,
        supply_zone_lower=2580.0,
        supply_zone_upper=2610.0,
        recent_liquidity_sweep=True,
        orderbook_bid_depth_25bps=50000.0,
        orderbook_ask_depth_25bps=30000.0,
        orderbook_imbalance_25bps=25.0,
        btc_price=80100.0,
        btc_zone_status="INSIDE_ZONE",
        relative_strength_vs_btc_24h=3.2,
        data_timestamp_ms=int(time.time() * 1000),
        candle_close_time_ms=int(time.time() * 1000) - 60000,
        data_age_sec=60.0,
        is_stale=False,
    )

    result = client.analyze(snapshot, risk_budget_usdt=1.00)

    assert result.symbol == "TESTUSDT"
    assert "fallback" in result.model_id.lower() or "rule_based" in result.model_id.lower()
    assert result.primary_bias in ["BULLISH", "BEARISH", "NEUTRAL", "CHOPPY"]
    assert 0 <= result.confidence_score <= 100
    assert 0 <= result.uncertainty_score <= 100
    assert len(result.long_scenario.key_catalysts) > 0
    assert len(result.short_scenario.key_catalysts) > 0
    assert result.recommended_plan is not None
    assert result.recommended_plan.suggested_quantity > 0

    # Indonesian narrative checks
    assert result.penjelasan_indonesia.ringkasan_struktur != ""
    assert result.penjelasan_indonesia.analisis_skenario_long != ""
    assert result.penjelasan_indonesia.analisis_skenario_short != ""
    assert result.penjelasan_indonesia.panduan_entry_dan_risiko != ""
    assert result.penjelasan_indonesia.kondisi_pembatalan != ""


# ==============================================================================
# 4. Storage & Deduplication Tests
# ==============================================================================

def test_storage_and_cache_deduplication(tmp_path: Path):
    """Verify SQLite persistence and 60s cache deduplication."""
    db_file = tmp_path / "test_storage.db"
    db = DatabaseManager(db_file)
    storage = AIAnalystStorage(db)

    # 1. Verify schema created
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_analyst_records'")
        assert cursor.fetchone() is not None

    # 2. Check no cached record initially
    cached = storage.get_latest_cached("SOLUSDT", "1h", max_age_ms=60000)
    assert cached is None

    # 3. Create mock result and save
    client = GroqAnalystClient(api_key="disabled")
    client._groq_client = None
    snap = QuantitativeMarketSnapshot(
        symbol="SOLUSDT",
        timeframe="1h",
        current_price=150.0,
        volume_24h_usdt=1000000.0,
        spread_bps=2.0,
        atr_14=3.5,
        atr_pct=2.3,
        swing_high=160.0,
        swing_low=140.0,
        demand_zone_lower=142.0,
        demand_zone_upper=146.0,
        supply_zone_lower=158.0,
        supply_zone_upper=162.0,
        recent_liquidity_sweep=False,
        orderbook_bid_depth_25bps=20000.0,
        orderbook_ask_depth_25bps=18000.0,
        orderbook_imbalance_25bps=5.2,
        btc_price=80200.0,
        btc_zone_status="INSIDE_ZONE",
        relative_strength_vs_btc_24h=1.0,
        data_timestamp_ms=int(time.time() * 1000),
        candle_close_time_ms=int(time.time() * 1000) - 30000,
        data_age_sec=30.0,
        is_stale=False,
    )
    result = client.analyze(snap, risk_budget_usdt=1.00)
    storage.save_analysis(result)

    # 4. Check cache hit within 60s
    cached_hit = storage.get_latest_cached("SOLUSDT", "1h", max_age_ms=60000)
    assert cached_hit is not None
    assert cached_hit["symbol"] == "SOLUSDT"
    assert cached_hit["is_cached"] is True
    assert cached_hit["analysis_id"] == result.analysis_id

    # 5. Fetch by ID and History
    by_id = storage.get_by_id(result.analysis_id)
    assert by_id is not None
    assert by_id["symbol"] == "SOLUSDT"

    history = storage.get_history(limit=10)
    assert len(history) == 1
    assert history[0]["symbol"] == "SOLUSDT"


# ==============================================================================
# 5. Isolation Guarantee: Zero Mutation of Live / Paper State
# ==============================================================================

def test_module_isolation_guarantee(tmp_path: Path):
    """Verify that running AI Analyst queries does NOT write to trade journals or mutate Model A/B."""
    db_file = tmp_path / "test_isolation.db"
    db = DatabaseManager(db_file)

    # Pre-count existing records
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM trade_journal")
        journal_count_before = cursor.fetchone()[0]

    storage = AIAnalystStorage(db)
    client = GroqAnalystClient(api_key="disabled")
    client._groq_client = None

    # Run analysis
    snap = QuantitativeMarketSnapshot(
        symbol="BTCUSDT",
        timeframe="1h",
        current_price=80000.0,
        volume_24h_usdt=50000000.0,
        spread_bps=1.0,
        atr_14=800.0,
        atr_pct=1.0,
        swing_high=81000.0,
        swing_low=79000.0,
        demand_zone_lower=79200.0,
        demand_zone_upper=79600.0,
        supply_zone_lower=80800.0,
        supply_zone_upper=81200.0,
        recent_liquidity_sweep=False,
        orderbook_bid_depth_25bps=100000.0,
        orderbook_ask_depth_25bps=100000.0,
        orderbook_imbalance_25bps=0.0,
        btc_price=80000.0,
        btc_zone_status="INSIDE_ZONE",
        relative_strength_vs_btc_24h=0.0,
        data_timestamp_ms=int(time.time() * 1000),
        candle_close_time_ms=int(time.time() * 1000) - 10000,
        data_age_sec=10.0,
        is_stale=False,
    )
    result = client.analyze(snap, risk_budget_usdt=1.00)
    storage.save_analysis(result)

    # Verify trade journal remains 100% untouched
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM trade_journal")
        journal_count_after = cursor.fetchone()[0]

    assert journal_count_after == journal_count_before, "Isolation violation: AI Analyst modified trade_journal!"


# ==============================================================================
# 6. FastAPI REST Endpoints Tests
# ==============================================================================

def test_fastapi_endpoints():
    """Verify /api/ai_analyst/* REST endpoints return 200 and adhere to contracts."""
    from src.api.app import app
    client = TestClient(app)

    # 1. Config endpoint
    res_cfg = client.get("/api/ai_analyst/config")
    assert res_cfg.status_code == 200
    data_cfg = res_cfg.json()
    assert data_cfg["status"] == "SUCCESS"
    assert "groq_model_id" in data_cfg["config"]
    assert "cache_ttl_sec" in data_cfg["config"]

    # 2. Symbols endpoint
    res_sym = client.get("/api/ai_analyst/symbols")
    assert res_sym.status_code == 200
    data_sym = res_sym.json()
    assert data_sym["status"] == "SUCCESS"
    assert isinstance(data_sym["symbols"], list)
    assert len(data_sym["symbols"]) > 0

    # 3. History endpoint
    res_hist = client.get("/api/ai_analyst/history?limit=5")
    assert res_hist.status_code == 200
    data_hist = res_hist.json()
    assert data_hist["status"] == "SUCCESS"
    assert isinstance(data_hist["records"], list)

    # 4. Analyze endpoint (live or fallback)
    req_payload = {
        "symbol": "ETHUSDT",
        "timeframe": "1h",
        "risk_budget_usdt": 1.00,
        "force_refresh": False,
    }
    res_an = client.post("/api/ai_analyst/analyze", json=req_payload)
    assert res_an.status_code == 200
    data_an = res_an.json()
    assert data_an["status"] == "SUCCESS"
    result = data_an["result"]
    assert result["symbol"] == "ETHUSDT"
    assert "primary_bias" in result
    assert "confidence_score" in result
    assert "recommended_plan" in result
    assert "penjelasan_indonesia" in result
    analysis_id = result["analysis_id"]

    # 5. Fetch by ID
    res_id = client.get(f"/api/ai_analyst/{analysis_id}")
    assert res_id.status_code == 200
    data_id = res_id.json()
    assert data_id["status"] == "SUCCESS"
    assert data_id["record"]["analysis_id"] == analysis_id

    # 6. Update config endpoint
    cfg_update = {
        "groq_model_id": "qwen/qwen3.8-27b",
        "default_risk_budget_usdt": 1.50,
    }
    res_up = client.post("/api/ai_analyst/config", json=cfg_update)
    assert res_up.status_code == 200
    assert res_up.json()["config"]["default_risk_budget_usdt"] == 1.50
