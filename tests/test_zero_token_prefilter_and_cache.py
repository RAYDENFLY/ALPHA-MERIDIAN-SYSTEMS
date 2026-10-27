"""Unit tests for Zero-Token Python Pre-Filtering, Single-Pass LLM Call, and 15m Candle Bar Caching."""

import time
from unittest.mock import MagicMock, patch
import pytest

from src.ai.groq_autotrader import (
    ExecutionVenue,
    GroqAutoTrader,
    Stage1Result,
    Top10CandidateItem,
)
from src.core.types import Candle, Direction
from src.risk.live_micro_sizing import LiveMicroSizingResult
from src.strategy.sweep_reclaim_15m import SweepReclaim15MSetup


@pytest.fixture
def empty_market_trader():
    """Trader where no coins have SMC sweep reclaim triggers (typical 95%+ cycles)."""
    trader = GroqAutoTrader(api_key="gsk-mock-key")
    # Mock stage 1 prescreen with 10 coins in equilibrium or without triggers
    mock_top10 = []
    for i in range(1, 11):
        mock_top10.append(
            Top10CandidateItem(
                symbol=f"COIN{i}USDT",
                mark_price=10.0 * i,
                change_24h_pct=1.0,
                rel_strength_vs_btc=0.5,
                turnover_24h_m=20.0,
                spread_bps=2.0,
                funding_rate_pct=0.01,
                range_72h_pct=50.0,  # Equilibrium (38% - 65%) -> restricted by Hard Laws
                trend_15m="CHOP",
                trend_1h="CHOP",
                trend_4h="CHOP",
                rsi_15m=50.0,
                rsi_1h=50.0,
                reason_selected="Volume rank",
            )
        )
    trader.run_stage1_prescreen = MagicMock(return_value=Stage1Result(
        market_regime={"regime_label": "CHOP_RANGE", "btc_change_24h_pct": 0.5},
        top_10=mock_top10,
        total_screened_pairs=45,
        execution_time_ms=120.0,
    ))

    # Mock adapter klines returning flat candles without sweep
    def mock_get_klines(symbol, timeframe="15m", limit=40):
        now_ms = int(time.time() * 1000)
        return [
            Candle(symbol=symbol, timeframe=timeframe, timestamp_ms=now_ms - (limit - j) * 900000,
                   open=10.0, high=10.1, low=9.9, close=10.0, volume=100.0,
                   close_time_ms=now_ms - (limit - j) * 900000 + 899999)
            for j in range(limit)
        ]
    trader.adapter.get_klines = MagicMock(side_effect=mock_get_klines)
    return trader


def test_zero_groq_tokens_consumed_on_empty_cycle(empty_market_trader):
    """When 0 candidates pass Python gates, Groq API MUST NOT be called (0 tokens)."""
    with patch.object(empty_market_trader, "_call_groq_json") as mock_groq_call:
        result = empty_market_trader.run_full_pipeline(
            execution_venue=ExecutionVenue.PAPER,
            strategy_id="15M_SWEEP_RECLAIM_SMC",
        )

        # 1. Zero Groq calls made!
        assert mock_groq_call.call_count == 0

        # 2. Status cleanly recorded as REJECTED with WAIT decision
        assert result.status == "REJECTED"
        assert result.stage3.decision == "WAIT"
        assert result.stage3.strategy_eligible is False
        assert result.stage3.veto_code == "NO_VALID_SWEEP_CANDIDATES_ON_BAR"
        assert result.stage3.veto_source == "PYTHON_PREFILTER"
        assert result.stage3.provider_failure is False

        # 3. Telemetry and latest screening history recorded
        latest = empty_market_trader.get_latest_screening()
        assert latest is not None
        assert latest["status"] == "REJECTED"
        assert len(latest["stage1"]["top_10"]) == 10
        assert len(latest["stage2"]["top_3"]) == 3
        # Eliminated summary details reasons for all 10 coins
        assert len(latest["stage2"]["eliminated_summary"]) >= 10


def test_15m_candle_bar_caching_deduplication():
    """Verify that multiple scans within the same 15m candle bar reuse cached LLM results."""
    trader = GroqAutoTrader(api_key="gsk-mock-key")
    now_ms = 1770000000000  # fixed epoch

    # Mock a qualified candidate
    qualified_cand = {
        "candidate": Top10CandidateItem(
            symbol="SOLUSDT", mark_price=100.0, change_24h_pct=2.0, rel_strength_vs_btc=1.0,
            turnover_24h_m=50.0, spread_bps=1.0, funding_rate_pct=0.01, range_72h_pct=28.0,
            trend_15m="BULLISH", trend_1h="BULLISH", trend_4h="NEUTRAL", rsi_15m=40.0, rsi_1h=45.0,
            reason_selected="Discount momentum",
        ),
        "setup": SweepReclaim15MSetup(
            symbol="SOLUSDT", direction=Direction.LONG, entry_price=99.5, stop_loss=98.2, take_profit=102.5,
            stop_distance_pct=0.013, sweep_level=99.0, sweep_wick_extreme=98.0, wick_ratio=0.45,
            rsi_15m=40.0, dealing_range_position_pct=28.0, trigger_candle_time_ms=now_ms,
        ),
        "direction": "LONG",
        "web_intelligence": {},
        "dealing_range_position_pct": 28.0,
        "trigger_candle_time_ms": now_ms,
    }

    trader.prefilter_candidates_with_python_gates = MagicMock(return_value=([qualified_cand], {"SOLUSDT": "PASSED_ALL_PYTHON_GATES"}))
    trader.run_stage1_prescreen = MagicMock(return_value=Stage1Result(
        market_regime={"regime_label": "RISK_ON_BULLISH"},
        top_10=[qualified_cand["candidate"]],
        total_screened_pairs=10,
        execution_time_ms=50.0,
    ))

    with patch.object(trader, "_call_groq_json") as mock_groq_call:
        mock_groq_call.return_value = {
            "decision": "EXECUTE",
            "symbol": "SOLUSDT",
            "direction": "LONG",
            "order_type": "LIMIT",
            "entry_price": 99.5,
            "stop_loss_price": 98.2,
            "take_profit_price": 102.5,
            "notes": "15m sweep reclaim setup",
            "reasoning": "Clear swing low sweep",
            "top_3": [{"rank": 1, "symbol": "SOLUSDT", "score": 92.0}],
        }

        # Cycle 1: First scan on candle bar -> invokes Groq (1 call)
        res1 = trader.run_full_pipeline(execution_venue=ExecutionVenue.PAPER)
        assert mock_groq_call.call_count == 1
        assert res1.stage3.symbol == "SOLUSDT"

        # Cycle 2: Second scan on same candle bar -> cache hit (0 Groq calls!)
        res2 = trader.run_full_pipeline(execution_venue=ExecutionVenue.PAPER)
        assert mock_groq_call.call_count == 1  # Still 1! Groq was NOT called again!
        assert res2.stage3.symbol == "SOLUSDT"


def test_quant_verified_fallback_on_tpd_exhaustion():
    """Verify that when Groq fails, quant-verified deterministic engine populates setup without provider failure."""
    trader = GroqAutoTrader(api_key="gsk-mock-key")
    trader.allow_quant_verified_fallback_on_tpd_exhaustion = True
    now_ms = 1770000000000

    sizing_sol = LiveMicroSizingResult(
        is_feasible=True,
        quantity=0.61,
        notional_usd=60.7,
        isolated_margin_usd=6.07,
        expected_conservative_loss_usd=0.79,
        gross_rr=2.31,
        expected_net_rr=2.08,
    )

    setup_sol = SweepReclaim15MSetup(
        symbol="SOLUSDT", direction=Direction.LONG, entry_price=99.5, stop_loss=98.2, take_profit=102.5,
        stop_distance_pct=0.013, sweep_level=99.0, sweep_wick_extreme=98.0, wick_ratio=0.45,
        rsi_15m=40.0, dealing_range_position_pct=28.0, trigger_candle_time_ms=now_ms,
        sizing=sizing_sol,
    )

    qualified_cand = {
        "candidate": Top10CandidateItem(
            symbol="SOLUSDT", mark_price=100.0, change_24h_pct=2.0, rel_strength_vs_btc=1.0,
            turnover_24h_m=50.0, spread_bps=1.0, funding_rate_pct=0.01, range_72h_pct=28.0,
            trend_15m="BULLISH", trend_1h="BULLISH", trend_4h="NEUTRAL", rsi_15m=40.0, rsi_1h=45.0,
            reason_selected="Discount momentum",
        ),
        "setup": setup_sol,
        "direction": "LONG",
        "web_intelligence": {},
        "dealing_range_position_pct": 28.0,
        "trigger_candle_time_ms": now_ms,
    }

    trader.prefilter_candidates_with_python_gates = MagicMock(return_value=([qualified_cand], {"SOLUSDT": "PASSED_ALL_PYTHON_GATES"}))
    trader.run_stage1_prescreen = MagicMock(return_value=Stage1Result(
        market_regime={"regime_label": "RISK_ON_BULLISH"},
        top_10=[qualified_cand["candidate"]],
        total_screened_pairs=10,
        execution_time_ms=50.0,
    ))
    trader.evaluate_candidate_with_strategy = MagicMock(return_value=(setup_sol, "SETUP_VALID"))

    with patch.object(trader, "_call_groq_json", side_effect=RuntimeError("Groq 429 TPD exhausted")):
        res = trader.run_full_pipeline(execution_venue=ExecutionVenue.PAPER)

        # Provider failure is False because quant-verified engine handled it
        assert res.stage3.provider_failure is False
        assert res.stage3.model_used == "quant_verified_engine"
        assert res.stage3.strategy_eligible is True
        assert res.stage3.symbol == "SOLUSDT"
        assert "[Quant-Verified Engine]" in res.stage3.notes
