"""Unit and integration tests for Groq AI Auto-Hunter 10 -> 3 -> 1 screening log and telemetry."""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

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
from src.core.types import Direction
from src.execution.live_session import LiveAutoSessionManager, ExecutionMode


@pytest.fixture(autouse=True)
def mock_external_calls(monkeypatch):
    """Bypass external network calls and sleeps."""
    monkeypatch.setattr("time.sleep", lambda s: None)

    def mock_groq_json(self, messages, model_override=None, max_tokens=400):
        content = " ".join([m.get("content", "") for m in messages])
        if "Analyze candidates and output Top 3" in content:
            return {
                "top_3": [
                    {
                        "rank": 1,
                        "symbol": "SOLUSDT",
                        "category": "LIQUIDITY_SWEEP_RECLAIM",
                        "score": 9.2,
                        "thesis": "15m sweep reclaim in 25% discount with bid wall support",
                    },
                    {
                        "rank": 2,
                        "symbol": "ETHUSDT",
                        "category": "SMC_DISCOUNT_REVERSAL",
                        "score": 8.5,
                        "thesis": "Reclaiming swing low",
                    },
                    {
                        "rank": 3,
                        "symbol": "BTCUSDT",
                        "category": "MOMENTUM_CONTINUATION",
                        "score": 7.8,
                        "thesis": "Range retest",
                    },
                ],
                "eliminated_summary": {
                    "ZECUSDT": "Reached 70% Premium zone",
                    "AVAXUSDT": "Ask wall blocking 0.3% above entry",
                },
            }
        return {
            "decision": "EXECUTE",
            "symbol": "SOLUSDT",
            "direction": "LONG",
            "order_type": "LIMIT",
            "entry_price": 99.40,
            "stop_loss_price": 98.15,
            "take_profit_price": 102.20,
            "notes": "15m sweep reclaim setup",
            "reasoning": "Discount sweep with orderbook support",
            "sweep_reclaim_evidence": "Swept 15m swing low with 42% wick",
            "orderflow_wall_evidence": "54% bid depth with wall at 99.20",
            "why_not_repeating_past_sl": "Priced 1.3% deeper than previous SL",
        }

    monkeypatch.setattr(GroqAutoTrader, "_call_groq_json", mock_groq_json)


def test_autotrader_screening_history_recorded():
    """Verify that running a pipeline records latest screening and historical cycle."""
    trader = GroqAutoTrader(api_key="mock_key")

    # Initially empty
    assert trader.get_latest_screening() is None
    assert trader.get_screening_history() == []

    # Mock stage1 prescreen
    mock_top10 = [
        Top10CandidateItem(
            symbol="SOLUSDT",
            mark_price=100.0,
            change_24h_pct=2.5,
            rel_strength_vs_btc=1.2,
            turnover_24h_m=45.0,
            spread_bps=1.5,
            funding_rate_pct=0.01,
            range_72h_pct=28.0,
            trend_15m="BULL",
            trend_1h="BULL",
            trend_4h="NEUTRAL",
            rsi_15m=45.0,
            rsi_1h=48.0,
            reason_selected="Top momentum in discount",
        )
    ]
    for i in range(2, 11):
        mock_top10.append(
            Top10CandidateItem(
                symbol=f"COIN{i}USDT",
                mark_price=10.0 * i,
                change_24h_pct=1.0,
                rel_strength_vs_btc=0.5,
                turnover_24h_m=20.0,
                spread_bps=2.0,
                funding_rate_pct=0.01,
                range_72h_pct=50.0,
                trend_15m="CHOP",
                trend_1h="CHOP",
                trend_4h="CHOP",
                rsi_15m=50.0,
                rsi_1h=50.0,
                reason_selected="Volume rank",
            )
        )

    trader.run_stage1_prescreen = MagicMock(return_value=Stage1Result(
        market_regime={"regime": "CHOP_RANGE", "btc_change_24h": 0.5},
        top_10=mock_top10,
        total_screened_pairs=45,
        execution_time_ms=150.0,
    ))

    # Mock coordinator & strategy
    mock_coord = MagicMock()
    mock_coord.config.strategy.live_strategy_id = "15M_SWEEP_RECLAIM_SMC"
    mock_coord.sweep_reclaim_strategy.strategy_id = "15M_SWEEP_RECLAIM_SMC"
    mock_coord.sweep_reclaim_strategy.is_live = True
    mock_coord.sweep_reclaim_strategy.evaluate.return_value = (None, "NO_SETUP")
    mock_coord.check_risk_limits.return_value = (True, "OK")
    mock_coord.global_lock.can_open_position.return_value = (True, "OK")
    mock_coord.current_equity = 100.0
    mock_coord.daily_filled_entries = 0
    mock_coord.daily_realized_loss = 0.0

    # Run preview pipeline
    result = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.PAPER,
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        coordinator=mock_coord,
    )

    assert result is not None
    latest = trader.get_latest_screening()
    assert latest is not None
    assert "stage1" in latest
    assert len(latest["stage1"]["top_10"]) == 10
    assert "stage2" in latest
    assert len(latest["stage2"]["top_3"]) == 3
    assert "eliminated_summary" in latest["stage2"]
    assert "stage3" in latest
    assert latest["stage3"]["symbol"] == "SOLUSDT"

    # History contains the cycle
    history = trader.get_screening_history(limit=5)
    assert len(history) == 1
    assert history[0]["cycle_id"] == latest["cycle_id"]


def test_screening_log_api_endpoint():
    """Verify GET /api/autotrader/screening-log returns properly formatted telemetry."""
    from src.api.app import app
    client = TestClient(app)

    resp = client.get("/api/autotrader/screening-log?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "history" in data
    assert "active_cycle" in data
    assert "timestamp_utc" in data


def test_live_session_info_includes_screening_data():
    """Verify LiveAutoSessionManager.get_session_info includes latest_screening."""
    mock_trader = MagicMock()
    mock_trader.get_latest_screening.return_value = {"cycle_id": "cycle-abc", "stage1": {}}
    mock_trader.get_screening_history.return_value = [{"cycle_id": "cycle-abc"}]

    mgr = LiveAutoSessionManager()
    mgr.autotrader = mock_trader

    info = mgr.get_session_info()
    assert info["latest_screening"] == {"cycle_id": "cycle-abc", "stage1": {}}
    assert info["screening_history_count"] == 1
