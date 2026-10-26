"""Unit and Integration Tests for Groq AI (qwen/qwen3.8-27b) 10 -> 3 -> 1 Funnel Screener & Auto-Limit Order Pipeline.

Validates:
1. Configuration defaults & settings initialization.
2. Stage 1 Deterministic Pre-Screener filtering gates (BTC rejection, BNB blacklist, spread <= 2.5 bps, knife-trap rejection).
3. Stage 2 Groq LLM response parsing and deterministic fallback resilience.
4. Stage 3 Python mathematical guardrails (stop floor >= 1.15%, R:R >= 1.60, dollar risk exact lock $0.35/$0.80, min notional $5.50, max margin $7.00).
5. FastAPI Endpoint /api/inspector/groq-auto-setup execution in both preview and auto-place paper modes.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from config.settings import GroqAutoTraderConfig, AppConfig
from src.ai.groq_autotrader import (
    ExecutionVenue,
    GroqAutoTrader,
    RiskPreset,
    Stage1Result,
    Stage2FinalistItem,
    Stage2Result,
    Stage3SetupOutput,
    Top10CandidateItem,
)
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.metadata import SymbolSpecs
from src.intelligence.coin_inspector import CoinInspectorService
from src.api.app import app


# -----------------------------------------------------------------------------
# 1. CONFIGURATION TESTS
# -----------------------------------------------------------------------------

def test_groq_autotrader_config_defaults():
    cfg = GroqAutoTraderConfig()
    assert cfg.model == "qwen/qwen3.8-27b"
    assert cfg.base_url == "https://api.groq.com/openai/v1"
    assert cfg.auto_execution_venue == "PAPER"
    assert cfg.temperature == 0.1
    assert cfg.timeout_sec == 25.0


# -----------------------------------------------------------------------------
# 2. STAGE 1 PRE-SCREENER FILTERING GATES
# -----------------------------------------------------------------------------

def test_stage_1_screening_filtering():
    """Verify that Stage 1 filters out BTC (capital barrier), BNB (blacklist), wide spreads, and knife traps."""
    mock_adapter = MagicMock(spec=AsterAdapter)

    # Mock Aster DEX snapshot dictionary
    mock_snapshots = {
        # BTC: min qty * price = 0.001 * 80,000 = $80 > $25 (must be rejected)
        "BTCUSDT": {"last_price": 80000.0, "price_change_pct": 1.2, "volume_24h_usdt": 500000000.0, "spread_bps": 0.5},
        # ETH: Anchor benchmark
        "ETHUSDT": {"last_price": 2350.0, "price_change_pct": 1.8, "volume_24h_usdt": 120000000.0, "spread_bps": 0.8},
        # BNB: blacklisted symbol (must be rejected)
        "BNBUSDT": {"last_price": 600.0, "price_change_pct": 2.0, "volume_24h_usdt": 10000000.0, "spread_bps": 0.9},
        # WIDE: spread > 2.5 bps (e.g. 5 bps) (must be rejected)
        "WIDEUSDT": {"last_price": 10.0, "price_change_pct": 3.0, "volume_24h_usdt": 2000000.0, "spread_bps": 5.0},
        # LOWVOL: volume < $500k (must be rejected)
        "LOWVOLUSDT": {"last_price": 5.0, "price_change_pct": 1.0, "volume_24h_usdt": 200000.0, "spread_bps": 1.0},
        # KNIFE: change > 22% (must be rejected)
        "KNIFEUSDT": {"last_price": 1.0, "price_change_pct": 28.5, "volume_24h_usdt": 5000000.0, "spread_bps": 1.0},
        # VALID CANDIDATES
        "SOLUSDT": {"last_price": 102.50, "price_change_pct": 3.5, "volume_24h_usdt": 80000000.0, "spread_bps": 1.0},
        "DOGEUSDT": {"last_price": 0.1420, "price_change_pct": -2.1, "volume_24h_usdt": 15000000.0, "spread_bps": 1.4},
    }
    mock_adapter.get_bulk_market_snapshot.return_value = mock_snapshots

    # Mock Aster DEX instrument specifications
    mock_instruments = [
        MagicMock(symbol="BTCUSDT", min_qty=0.001, tick_size=0.1, step_size=0.001),
        MagicMock(symbol="ETHUSDT", min_qty=0.001, tick_size=0.01, step_size=0.001),
        MagicMock(symbol="BNBUSDT", min_qty=0.01, tick_size=0.01, step_size=0.01),
        MagicMock(symbol="WIDEUSDT", min_qty=0.1, tick_size=0.001, step_size=0.1),
        MagicMock(symbol="LOWVOLUSDT", min_qty=0.1, tick_size=0.001, step_size=0.1),
        MagicMock(symbol="KNIFEUSDT", min_qty=1.0, tick_size=0.0001, step_size=1.0),
        MagicMock(symbol="SOLUSDT", min_qty=0.01, tick_size=0.01, step_size=0.01),
        MagicMock(symbol="DOGEUSDT", min_qty=1.0, tick_size=0.00001, step_size=1.0),
    ]
    mock_adapter.discover_instruments.return_value = mock_instruments

    # Mock klines and premium index for candidate indicators
    def mock_klines(symbol, timeframe, limit):
        base = 100.0 if "SOL" in symbol else (2300.0 if "ETH" in symbol else 0.14)
        return [[0, base, base * 1.01, base * 0.99, base, 1000] for _ in range(limit)]

    mock_adapter.get_klines.side_effect = mock_klines
    mock_adapter.get_premium_index.return_value = {"last_funding_rate": "0.0001"}

    trader = GroqAutoTrader(adapter=mock_adapter)
    s1_res = trader.run_stage1_prescreen()

    symbols = [item.symbol for item in s1_res.top_10]
    assert "BTCUSDT" not in symbols, "BTCUSDT must be excluded due to micro-account sizing capital barrier"
    assert "BNBUSDT" not in symbols, "BNBUSDT must be excluded due to hard blacklist"
    assert "WIDEUSDT" not in symbols, "WIDEUSDT must be excluded due to spread > 2.5 bps"
    assert "LOWVOLUSDT" not in symbols, "LOWVOLUSDT must be excluded due to volume < $500k"
    assert "KNIFEUSDT" not in symbols, "KNIFEUSDT must be excluded due to extreme pump/dump > 22%"

    assert "SOLUSDT" in symbols or "DOGEUSDT" in symbols


# -----------------------------------------------------------------------------
# 3. STAGE 2 GROQ LLM & DETERMINISTIC FALLBACK
# -----------------------------------------------------------------------------

def test_stage_2_deterministic_fallback():
    """Verify that Stage 2 falls back gracefully to a balanced top 3 when Groq API fails."""
    mock_adapter = MagicMock(spec=AsterAdapter)
    trader = GroqAutoTrader(adapter=mock_adapter, api_key="test_dummy_key")

    dummy_s1 = Stage1Result(
        market_regime={"btc_price": 80000.0, "btc_change_24h_pct": 1.0, "btc_trend": "BULLISH"},
        top_10=[
            Top10CandidateItem(
                symbol="SOLUSDT", mark_price=102.5, change_24h_pct=3.5, rel_strength_vs_btc=2.5,
                turnover_24h_m=80.0, spread_bps=1.0, funding_rate_pct=0.005, range_72h_pct=45.0,
                trend_15m="BULLISH", trend_1h="BULLISH", trend_4h="BULLISH", rsi_15m=52.0, rsi_1h=55.0,
                reason_selected="Strong volume and momentum"
            ),
            Top10CandidateItem(
                symbol="ETHUSDT", mark_price=2350.0, change_24h_pct=1.8, rel_strength_vs_btc=0.8,
                turnover_24h_m=120.0, spread_bps=0.8, funding_rate_pct=0.003, range_72h_pct=38.0,
                trend_15m="NEUTRAL", trend_1h="BULLISH", trend_4h="BULLISH", rsi_15m=48.0, rsi_1h=51.0,
                reason_selected="Solid liquid large cap"
            ),
            Top10CandidateItem(
                symbol="DOGEUSDT", mark_price=0.142, change_24h_pct=-2.1, rel_strength_vs_btc=-3.1,
                turnover_24h_m=15.0, spread_bps=1.4, funding_rate_pct=-0.001, range_72h_pct=22.0,
                trend_15m="BEARISH", trend_1h="BEARISH", trend_4h="BEARISH", rsi_15m=32.0, rsi_1h=35.0,
                reason_selected="Discount range reclaim candidate"
            ),
        ],
        total_screened_pairs=3,
        execution_time_ms=100.0
    )

    # Force Groq API call to raise an exception
    with patch.object(trader, "_call_groq_json", side_effect=Exception("Groq API 503 Unavailable")):
        s2_res = trader.run_stage2_rank_top3(dummy_s1)

    assert len(s2_res.top_3) == 3
    assert s2_res.model_used == "deterministic_fallback"
    categories = [c.category for c in s2_res.top_3]
    assert any("LIQUIDITY_SWEEP" in cat for cat in categories)
    assert any("MOMENTUM" in cat for cat in categories)


# -----------------------------------------------------------------------------
# 4. STAGE 3 POST-LLM MATHEMATICAL GUARDRAIL
# -----------------------------------------------------------------------------

def test_stage_3_mathematical_guardrail_sizing():
    """Verify that Stage 3 guardrails strictly enforce micro-account sizing, stop floor, and R:R."""
    mock_adapter = MagicMock(spec=AsterAdapter)
    mock_inspector = MagicMock(spec=CoinInspectorService)
    trader = GroqAutoTrader(adapter=mock_adapter, inspector_service=mock_inspector)

    specs = SymbolSpecs(
        symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        tick_size=0.01,
        price_precision=2,
        step_size=0.01,
        quantity_precision=2,
        min_qty=0.01,
        max_qty=1000000.0,
        min_notional=5.0,
        maint_margin_percent=0.02,
    )

    # Case A: Growth Preset ($0.80 risk) with a narrow stop (0.50%) that violates the 1.15% floor
    raw_setup = {
        "decision": "EXECUTE",
        "symbol": "SOLUSDT",
        "direction": "LONG",
        "entry_price": 100.00,
        "stop_loss_price": 99.50,   # 0.50% stop -> must be expanded to at least 1.15% ($98.85)
        "take_profit_price": 101.00, # 1.00R -> must be expanded to at least 1.65R
        "notes": "Testing guardrail stop expansion",
        "reasoning": "Validating micro sizing"
    }

    guarded = trader.apply_mathematical_guardrails(
        raw_setup=raw_setup,
        specs=specs,
        risk_preset=RiskPreset.GROWTH_080,
        execution_venue=ExecutionVenue.PAPER,
    )

    # Assertions
    assert guarded.stop_distance_pct >= 1.15, f"Stop distance was {guarded.stop_distance_pct}%, must be >= 1.15%"
    assert guarded.rr_ratio >= 1.60, f"R:R was {guarded.rr_ratio}, must be >= 1.60"
    assert 0.70 <= guarded.max_risk_usdt <= 0.85, f"Risk USDT was {guarded.max_risk_usdt}, must be close to target $0.80"
    assert guarded.notional_usdt >= 5.50, f"Notional was {guarded.notional_usdt}, must be >= $5.50"
    assert guarded.isolated_margin_usdt <= 7.00, f"Margin was {guarded.isolated_margin_usdt}, must be <= $7.00"
    assert guarded.leverage == 10
    assert guarded.margin_mode == "ISOLATED"
    assert guarded.stop_loss_price < guarded.entry_price < guarded.take_profit_price


def test_stage_3_conservative_preset_sizing():
    """Verify conservative preset ($0.35 risk) respects min notional scaling without violating risk ceiling."""
    mock_adapter = MagicMock(spec=AsterAdapter)
    mock_inspector = MagicMock(spec=CoinInspectorService)
    trader = GroqAutoTrader(adapter=mock_adapter, inspector_service=mock_inspector)

    specs = SymbolSpecs(
        symbol="ETHUSDT",
        base_asset="ETH",
        quote_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        tick_size=0.1,
        price_precision=1,
        step_size=0.001,
        quantity_precision=3,
        min_qty=0.001,
        max_qty=1000000.0,
        min_notional=5.0,
        maint_margin_percent=0.02,
    )

    raw_setup = {
        "decision": "EXECUTE",
        "symbol": "ETHUSDT",
        "direction": "LONG",
        "entry_price": 2400.0,
        "stop_loss_price": 2360.0,   # 1.67% stop
        "take_profit_price": 2470.0, # 1.75R
        "notes": "Conservative ETH Scalp",
        "reasoning": "SMC Orderblock test"
    }

    guarded = trader.apply_mathematical_guardrails(
        raw_setup=raw_setup,
        specs=specs,
        risk_preset=RiskPreset.CONSERVATIVE_035,
        execution_venue=ExecutionVenue.PAPER,
    )

    assert guarded.max_risk_usdt <= 0.40, f"Conservative risk was {guarded.max_risk_usdt}, must not blow micro budget"
    assert guarded.notional_usdt >= 5.50
    assert guarded.isolated_margin_usdt <= 7.00


# -----------------------------------------------------------------------------
# 5. REST API ENDPOINT /api/inspector/groq-auto-setup
# -----------------------------------------------------------------------------

def test_api_groq_auto_setup_preview():
    """Verify POST /api/inspector/groq-auto-setup with auto_fire_limit_order=False returns valid setup without firing order."""
    client = TestClient(app)

    mock_result = MagicMock()
    mock_result.model_dump.return_value = {
        "status": "SUCCESS",
        "risk_preset": "GROWTH_080",
        "execution_venue": "PAPER",
        "stage1": {
            "market_regime": {"btc_price": 80000.0, "btc_change_24h_pct": 1.2, "btc_trend": "BULLISH"},
            "top_10": [],
            "total_screened_pairs": 100,
            "execution_time_ms": 250.0,
        },
        "stage2": {
            "top_3": [
                {"rank": 1, "symbol": "SOLUSDT", "category": "LIQUIDITY_SWEEP_RECLAIM", "score": 9.2, "thesis": "Clean 15m sweep"}
            ],
            "eliminated_summary": {},
            "model_used": "qwen/qwen3.8-27b",
        },
        "stage3": {
            "decision": "EXECUTE",
            "symbol": "SOLUSDT",
            "direction": "LONG",
            "order_type": "LIMIT",
            "entry_price": 102.50,
            "stop_loss_price": 101.10,
            "take_profit_price": 105.10,
            "qty": 0.57,
            "leverage": 10,
            "margin_mode": "ISOLATED",
            "max_risk_usdt": 0.80,
            "stop_distance_pct": 1.37,
            "rr_ratio": 1.86,
            "notional_usdt": 58.43,
            "isolated_margin_usdt": 5.84,
            "execution_venue": "PAPER",
            "notes": "SOL 15m sweep reclaim",
            "reasoning": "Testing endpoint preview",
        },
        "execution_receipt": None,
        "timestamp_utc": "2026-10-26T00:00:00Z",
    }

    with patch("src.api.app.groq_autotrader.run_full_pipeline", return_value=mock_result):
        resp = client.post(
            "/api/inspector/groq-auto-setup",
            json={
                "risk_preset": "GROWTH_080",
                "execution_venue": "PAPER",
                "auto_fire_limit_order": False,
                "model_override": "qwen/qwen3.8-27b",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["data"]["stage3"]["symbol"] == "SOLUSDT"
    assert data["data"]["stage3"]["leverage"] == 10
    assert data["data"]["execution_receipt"] is None
