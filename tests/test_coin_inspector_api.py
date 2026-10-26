"""Unit tests for Coin Inspector & AI Context Exporter API (/api/inspector/*).

Verifies:
1. /api/inspector/symbols returns prioritized, schema-complete active perpetual futures symbols.
2. /api/inspector/coin/{symbol} returns deterministic, schema-complete quantitative snapshot.
3. Multi-timeframe summary, SMC structure, L2 microstructure, and micro-sizing presets are complete.
4. Dynamic decimal precision for sub-$1.00 coins (e.g. JCTUSDT, DOGEUSDT) preserves 4-6 digits and ensures SL < entry.
5. MODEL_A whitelist check properly identifies 11 whitelisted symbols (e.g. SOLUSDT) and reports true SMC gate status.
6. Custom account balance query parameter applies to sizing calculations.
7. GET /inspector route serves the interactive HTML interface.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_get_inspector_symbols(client):
    """Prove /api/inspector/symbols returns active symbols catalog with popular chips at top."""
    resp = client.get("/api/inspector/symbols")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    data = resp.json()
    assert "symbols" in data, "Missing 'symbols' list in response"
    assert "items" in data, "Missing 'items' list in response"
    assert "popular_chips" in data, "Missing 'popular_chips' in response"
    assert "count" in data, "Missing 'count' in response"

    assert data["count"] > 0, "Expected at least 1 symbol"
    assert len(data["symbols"]) == data["count"]

    # Verify popular coins exist
    popular_set = set(data["popular_chips"])
    assert "SOLUSDT" in popular_set or "BTCUSDT" in popular_set or "ETHUSDT" in popular_set

    # Verify item structure
    first_item = data["items"][0]
    assert "symbol" in first_item
    assert "volume_24h_usdt" in first_item
    assert "is_popular" in first_item


def test_get_coin_inspection_solusdt(client):
    """Prove /api/inspector/coin/SOLUSDT returns schema-complete quantitative context."""
    resp = client.get("/api/inspector/coin/SOLUSDT")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    data = resp.json()

    # 1. Meta
    assert "meta" in data, "Missing 'meta' section"
    meta = data["meta"]
    assert meta["symbol"] == "SOLUSDT"
    assert meta["mark_price"] > 0
    assert "exchange_filters" in meta
    filters = meta["exchange_filters"]
    assert "tick_size" in filters
    assert "step_size" in filters
    assert "min_qty" in filters
    assert "min_notional" in filters

    # 2. Market Compass
    assert "market_compass" in data, "Missing 'market_compass' section"
    compass = data["market_compass"]
    assert "btc" in compass
    assert "eth" in compass
    assert "market_regime" in compass
    assert compass["market_regime"] in ("RISK_ON_BULLISH", "RISK_OFF_BEARISH", "CHOP_RANGE")

    # 3. Multi-timeframe Summary
    assert "multi_timeframe_summary" in data, "Missing 'multi_timeframe_summary' section"
    mtf = data["multi_timeframe_summary"]
    for tf in ["15m", "1h", "4h"]:
        assert tf in mtf, f"Missing timeframe '{tf}'"
        tf_data = mtf[tf]
        assert "latest_indicators" in tf_data
        ind = tf_data["latest_indicators"]
        assert "ema_20" in ind
        assert "ema_50" in ind
        assert "atr_14" in ind
        assert "rsi_14" in ind
        assert "trend_state" in ind
        assert ind["trend_state"] in ("BULL", "BEAR", "NEUTRAL")

        assert "recent_candles" in tf_data
        assert isinstance(tf_data["recent_candles"], list)
        if tf_data["recent_candles"]:
            first_candle_str = tf_data["recent_candles"][0]
            assert "O:" in first_candle_str and "C:" in first_candle_str and "WickR:" in first_candle_str

    # 4. SMC and Liquidity Structure
    assert "smc_and_liquidity_structure" in data, "Missing 'smc_and_liquidity_structure' section"
    smc = data["smc_and_liquidity_structure"]
    assert "model_a_smc_context" in smc
    model_a = smc["model_a_smc_context"]
    assert "dealing_range_72h" in model_a
    dr = model_a["dealing_range_72h"]
    assert "high" in dr and "low" in dr and "current_position_pct" in dr and "zone_status" in dr
    assert dr["zone_status"] in ("DISCOUNT", "EQUILIBRIUM", "PREMIUM")
    assert "impulse_48h_pct" in model_a
    assert "impulse_12h_pct" in model_a

    assert "model_b_liquidity_context" in smc
    model_b = smc["model_b_liquidity_context"]
    assert "15m_swing_high_20b" in model_b
    assert "15m_swing_low_20b" in model_b
    assert "recent_sweep_detected" in model_b
    assert "sweep_type" in model_b
    assert model_b["sweep_type"] in ("BULL_SWEEP_LOW", "BEAR_SWEEP_HIGH", "NONE")

    # 5. Orderbook L2 Microstructure
    assert "orderbook_l2_microstructure" in data, "Missing 'orderbook_l2_microstructure' section"
    l2 = data["orderbook_l2_microstructure"]
    assert "spread_bps" in l2
    assert "bid_depth_top20_usdt" in l2
    assert "ask_depth_top20_usdt" in l2
    assert "orderbook_imbalance_ratio" in l2
    assert "l2_gate_passed" in l2

    # 6. Bot Signals and Micro Sizing
    assert "bot_signals_and_micro_sizing" in data, "Missing 'bot_signals_and_micro_sizing' section"
    bot_sizing = data["bot_signals_and_micro_sizing"]
    assert "cohort_evaluations" in bot_sizing
    cohorts = bot_sizing["cohort_evaluations"]
    assert "MODEL_A" in cohorts
    assert "MODEL_B" in cohorts
    assert "MODEL_B_FAST" in cohorts
    assert "MODEL_B_V1_PRO" in cohorts

    # Crucial Model A check: SOLUSDT is in the whitelist and must NOT be marked NOT_MONITORED!
    assert cohorts["MODEL_A"]["status"] != "NOT_MONITORED", "SOLUSDT is in 11-symbol core whitelist"

    assert "sizing" in bot_sizing
    sizing = bot_sizing["sizing"]
    assert sizing["account_balance_usdt"] == 15.72
    assert "presets" in sizing
    presets = sizing["presets"]
    for preset_key in [
        "fast_15m_standard_risk",
        "fast_15m_conservative_risk",
        "normal_1h_standard_risk",
        "normal_1h_conservative_risk",
    ]:
        assert preset_key in presets, f"Missing preset '{preset_key}'"
        p = presets[preset_key]
        assert "long_setup" in p
        assert "short_setup" in p
        assert p["rounded_qty"] > 0
        assert p["notional_usdt"] > 0
        assert p["isolated_margin_usdt"] > 0
        assert p["est_roundtrip_fee_usdt"] > 0


def test_sub_dollar_coin_decimal_precision(client):
    """Prove sub-$1.00 coins (JCTUSDT) retain dynamic 5-6 digit precision and valid SL."""
    resp = client.get("/api/inspector/coin/JCTUSDT")
    assert resp.status_code == 200

    data = resp.json()
    meta = data["meta"]
    assert meta["mark_price"] < 1.0, f"Expected sub-$1 mark price, got {meta['mark_price']}"
    assert meta["exchange_filters"]["tick_size"] < 0.01, f"Expected fine tick size for JCTUSDT, got {meta['exchange_filters']['tick_size']}"

    # Verify micro-sizing SL and TP1 prices
    presets = data["bot_signals_and_micro_sizing"]["sizing"]["presets"]
    long_setup = presets["fast_15m_standard_risk"]["long_setup"]
    entry_px = long_setup["entry_price"]
    sl_px = long_setup["stop_loss_price"]
    tp1_px = long_setup["take_profit_price"]

    assert sl_px < entry_px, f"Stop loss ({sl_px}) must be strictly less than entry ({entry_px})"
    assert tp1_px > entry_px, f"Take profit ({tp1_px}) must be strictly greater than entry ({entry_px})"
    assert sl_px > 0, "Stop loss must be positive"


def test_model_a_whitelist_and_non_whitelist_evaluations(client):
    """Prove Model A correctly identifies whitelisted vs non-whitelisted coins."""
    # Whitelisted coin: DOGEUSDT
    resp_doge = client.get("/api/inspector/coin/DOGEUSDT")
    assert resp_doge.status_code == 200
    cohorts_doge = resp_doge.json()["bot_signals_and_micro_sizing"]["cohort_evaluations"]
    assert cohorts_doge["MODEL_A"]["status"] != "NOT_MONITORED", "DOGEUSDT must be evaluated in MODEL_A"

    # Non-whitelisted coin: ASTERUSDT
    resp_aster = client.get("/api/inspector/coin/ASTERUSDT")
    assert resp_aster.status_code == 200
    cohorts_aster = resp_aster.json()["bot_signals_and_micro_sizing"]["cohort_evaluations"]
    assert cohorts_aster["MODEL_A"]["status"] == "NOT_MONITORED", "ASTERUSDT is not in 11-symbol Model A whitelist"
    assert "whitelist" in cohorts_aster["MODEL_A"]["reason"].lower()


def test_custom_account_balance_sizing(client):
    """Prove account_balance query parameter updates sizing calculations."""
    resp = client.get("/api/inspector/coin/ETHUSDT?account_balance=50.0")
    assert resp.status_code == 200

    data = resp.json()
    sizing = data["bot_signals_and_micro_sizing"]["sizing"]
    assert sizing["account_balance_usdt"] == 50.0


def test_inspector_html_page_route(client):
    """Prove /inspector serves valid HTML containing the Coin Inspector workspace."""
    resp = client.get("/inspector")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "tab-inspector" in html
    assert "Coin Inspector &amp; AI Context Exporter" in html or "Coin Inspector" in html
    assert "coin_inspector.js" in html
    assert "inspectorSearchInput" in html
    assert "copyJsonBtn" in html


def test_execute_ai_setup_paper_mode(client):
    """Prove AI Trade Setup Executor creates simulated paper order with ISOLATED 10x bracket."""
    payload = {
        "symbol": "SOLUSDT",
        "direction": "LONG",
        "order_type": "LIMIT",
        "entry_price": 100.55,
        "stop_loss_price": 99.34,
        "take_profit_price": 102.30,
        "qty": 0.66,
        "leverage": 10,
        "margin_mode": "ISOLATED",
        "max_risk_usdt": 0.85,
        "execution_venue": "PAPER",
        "notes": "SOL 15m/1h oversold sweep-reclaim into 102.30 Bear FVG",
    }
    resp = client.post("/api/inspector/execute-setup", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["status"] == "SUCCESS"
    assert data["execution_venue"] == "PAPER"
    assert data["symbol"] == "SOLUSDT"
    assert data["direction"] == "LONG"
    assert data["order_type"] == "LIMIT"
    assert data["leverage"] == 10
    assert data["margin_mode"] == "ISOLATED"
    assert data["order_id"].startswith("PORD_AI_SOLUSDT_")
    assert data["entry_price"] == 100.55
    assert data["stop_loss_price"] == 99.34
    assert data["take_profit_price"] == 102.30
    assert data["quantity"] == 0.66
    assert data["notional_usdt"] > 5.0
    assert data["margin_usdt"] == round(data["notional_usdt"] / 10, 4)
    assert data["rr_ratio"] > 1.0


def test_execute_ai_setup_live_mode_unlocked(client, monkeypatch):
    """Prove execution_venue='LIVE' submits to Aster DEX when live execution is unlocked."""
    from unittest.mock import MagicMock
    mock_adapter = MagicMock()
    mock_adapter.create_order.return_value = {"orderId": "5304691234", "status": "NEW"}
    mock_adapter.set_margin_type.return_value = {"code": 200}
    mock_adapter.set_leverage.return_value = {"symbol": "SOLUSDT", "leverage": 10}

    import src.api.app as app_mod
    monkeypatch.setattr(app_mod.account_service, "adapter", mock_adapter)

    payload = {
        "symbol": "SOLUSDT",
        "direction": "LONG",
        "order_type": "LIMIT",
        "entry_price": 100.55,
        "stop_loss_price": 99.34,
        "take_profit_price": 102.30,
        "qty": 0.66,
        "leverage": 10,
        "margin_mode": "ISOLATED",
        "execution_venue": "LIVE",
        "notes": "Testing live unlocked execution route",
    }
    resp = client.post("/api/inspector/execute-setup", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["execution_venue"] == "LIVE"
    assert data["order_id"] == "5304691234"
    assert mock_adapter.create_order.called
    assert mock_adapter.set_margin_type.called
    assert mock_adapter.set_leverage.called


def test_execute_ai_setup_geometry_validation(client):
    """Prove geometric sanity checks reject invalid TP/SL configurations."""
    # Invalid LONG: SL higher than Entry
    payload = {
        "symbol": "SOLUSDT",
        "direction": "LONG",
        "order_type": "LIMIT",
        "entry_price": 100.0,
        "stop_loss_price": 105.0,
        "take_profit_price": 110.0,
        "qty": 0.5,
    }
    resp = client.post("/api/inspector/execute-setup", json=payload)
    assert resp.status_code == 400
    assert "geometry" in resp.json()["detail"].lower()


def test_execute_ai_setup_sub_dollar_precision(client):
    """Prove sub-$1.00 coin (JCTUSDT) executes with high dynamic precision."""
    payload = {
        "symbol": "JCTUSDT",
        "direction": "LONG",
        "order_type": "LIMIT",
        "entry_price": 0.006950,
        "stop_loss_price": 0.006850,
        "take_profit_price": 0.007150,
        "qty": 1000.0,
        "leverage": 10,
        "margin_mode": "ISOLATED",
        "execution_venue": "PAPER",
    }
    resp = client.post("/api/inspector/execute-setup", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["entry_price"] == 0.006950
    assert data["stop_loss_price"] == 0.006850
    assert data["take_profit_price"] == 0.007150

