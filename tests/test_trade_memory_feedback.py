"""Unit tests for Closed-Loop Trade Memory and Self-Learning Feedback Engine."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from src.ai.trade_memory_feedback import (
    TradeMemoryFeedbackEngine,
    generate_post_loss_autopsy_tags,
    parse_utc_timestamp,
)
from src.journal.models import AutomatedTradeRecord, TradeStatus


class TestTradeMemoryFeedbackEngine:
    """Test suite for deterministic scar-tissue pre-trade guardrails."""

    def test_revenge_lock_blocks_immediate_long_reentry_unless_1_20_pct_deeper(self):
        """If a symbol hit SL within 120m, long re-entry is blocked unless price is >= 1.20% lower."""
        now = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)
        sl_exit_time = (now - timedelta(minutes=15)).isoformat()

        # Previous losing trade on SOLUSDT LONG stopped out at 100.73
        mock_trades = [
            {
                "trade_id": "TRD_SOL_PREV",
                "symbol": "SOLUSDT",
                "side": "LONG",
                "status": "CLOSED",
                "exit_reason": "STOP_LOSS",
                "average_entry_price": 101.45,
                "average_exit_price": 100.73,
                "planned_stop_price": 100.74,
                "planned_stop_pct": 0.74,
                "realized_r": -0.83,
                "net_pnl_usd": -0.16,
                "exit_timestamp_utc": sl_exit_time,
                "created_at_utc": (now - timedelta(minutes=25)).isoformat(),
            }
        ]

        engine = TradeMemoryFeedbackEngine()

        # Case 1: Re-entering at 100.50 (only 0.23% below SL exit 100.73) -> BLOCKED
        res1 = engine.check_pre_trade_memory_gates(
            symbol="SOLUSDT",
            side="LONG",
            entry_price=100.50,
            planned_stop_pct=1.30,
            current_time=now,
            trades=mock_trades,
        )
        assert not res1.passed
        assert res1.rule_triggered == "REVENGE_LOCK"
        assert "REVENGE_LOCK_ACTIVE" in res1.rejection_reason
        assert "not >= 1.20% deeper" in res1.rejection_reason

        # Case 2: Re-entering at 101.00 (above previous SL exit!) -> BLOCKED
        res2 = engine.check_pre_trade_memory_gates(
            symbol="SOLUSDT",
            side="LONG",
            entry_price=101.00,
            planned_stop_pct=1.30,
            current_time=now,
            trades=mock_trades,
        )
        assert not res2.passed
        assert res2.rule_triggered == "REVENGE_LOCK"

        # Case 3: Re-entering at 99.40 (1.32% below SL exit 100.73, satisfies >= 1.20% deeper) -> PASSES
        max_allowed = 100.73 * (1.0 - 0.012)  # 99.5212
        res3 = engine.check_pre_trade_memory_gates(
            symbol="SOLUSDT",
            side="LONG",
            entry_price=99.40,
            planned_stop_pct=1.30,
            current_time=now,
            trades=mock_trades,
        )
        assert res3.passed
        assert res3.rejection_reason == "PASSED"

    def test_revenge_lock_blocks_immediate_short_reentry_unless_1_20_pct_higher(self):
        """If a symbol hit SL on SHORT within 120m, short re-entry is blocked unless price is >= 1.20% higher."""
        now = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)
        sl_exit_time = (now - timedelta(minutes=20)).isoformat()

        mock_trades = [
            {
                "trade_id": "TRD_ETH_PREV",
                "symbol": "ETHUSDT",
                "side": "SHORT",
                "status": "CLOSED",
                "exit_reason": "STOP_LOSS",
                "average_entry_price": 2308.76,
                "average_exit_price": 2322.04,
                "planned_stop_price": 2322.00,
                "planned_stop_pct": 0.57,
                "realized_r": -0.79,
                "net_pnl_usd": -0.156,
                "exit_timestamp_utc": sl_exit_time,
                "created_at_utc": (now - timedelta(minutes=45)).isoformat(),
            }
        ]

        engine = TradeMemoryFeedbackEngine()

        # Case 1: Re-entering SHORT at 2325.0 (only +0.13% higher) -> BLOCKED
        res1 = engine.check_pre_trade_memory_gates(
            symbol="ETHUSDT",
            side="SHORT",
            entry_price=2325.0,
            planned_stop_pct=1.30,
            current_time=now,
            trades=mock_trades,
        )
        assert not res1.passed
        assert res1.rule_triggered == "REVENGE_LOCK"
        assert "not >= 1.20% higher" in res1.rejection_reason

        # Case 2: Re-entering SHORT at 2355.0 (+1.42% higher than 2322.04) -> PASSES
        min_allowed = 2322.04 * (1.0 + 0.012)  # 2349.90
        res2 = engine.check_pre_trade_memory_gates(
            symbol="ETHUSDT",
            side="SHORT",
            entry_price=2355.0,
            planned_stop_pct=1.30,
            current_time=now,
            trades=mock_trades,
        )
        assert res2.passed
        assert res2.rejection_reason == "PASSED"

    def test_revenge_lock_expires_after_120_minutes(self):
        """After 120 minutes, the revenge lock for that symbol/side is expired."""
        now = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)
        sl_exit_time = (now - timedelta(minutes=125)).isoformat()  # 125m ago

        mock_trades = [
            {
                "trade_id": "TRD_SOL_OLD",
                "symbol": "SOLUSDT",
                "side": "LONG",
                "status": "CLOSED",
                "exit_reason": "STOP_LOSS",
                "average_entry_price": 101.45,
                "average_exit_price": 100.73,
                "planned_stop_pct": 1.20,
                "net_pnl_usd": -0.16,
                "exit_timestamp_utc": sl_exit_time,
            }
        ]

        engine = TradeMemoryFeedbackEngine()
        res = engine.check_pre_trade_memory_gates(
            symbol="SOLUSDT",
            side="LONG",
            entry_price=100.70,
            planned_stop_pct=1.20,
            current_time=now,
            trades=mock_trades,
        )
        assert res.passed

    def test_stop_loss_hard_floor_rejection_under_1_15_pct(self):
        """Any planned stop distance < 1.15% is strictly rejected."""
        engine = TradeMemoryFeedbackEngine()
        res = engine.check_pre_trade_memory_gates(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=67000.0,
            planned_stop_pct=0.70,  # Below 1.15%
            trades=[],
        )
        assert not res.passed
        assert res.rule_triggered == "SCAR_TISSUE_STOP_TOO_TIGHT"
        assert "below the minimum safety threshold of 1.15%" in res.rejection_reason

    def test_consecutive_losses_escalates_stop_floor_and_llm_score(self):
        """When account has >= 2 consecutive losses, stop floor widens to 1.25% and score threshold to 90."""
        now = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)
        mock_trades = [
            {
                "trade_id": "TRD_1",
                "symbol": "ETHUSDT",
                "side": "LONG",
                "status": "CLOSED",
                "exit_reason": "STOP_LOSS",
                "net_pnl_usd": -0.15,
                "exit_timestamp_utc": (now - timedelta(hours=3)).isoformat(),
            },
            {
                "trade_id": "TRD_2",
                "symbol": "SOLUSDT",
                "side": "SHORT",
                "status": "CLOSED",
                "exit_reason": "STOP_LOSS",
                "net_pnl_usd": -0.16,
                "exit_timestamp_utc": (now - timedelta(hours=5)).isoformat(),
            },
        ]

        engine = TradeMemoryFeedbackEngine()

        # Stop is 1.18% (passes 1.15% base floor, but fails escalated 1.25% floor)
        res1 = engine.check_pre_trade_memory_gates(
            symbol="AVAXUSDT",
            side="LONG",
            entry_price=25.0,
            planned_stop_pct=1.18,
            current_time=now,
            trades=mock_trades,
        )
        assert not res1.passed
        assert res1.rule_triggered == "CONSECUTIVE_LOSS_ESCALATION"
        assert "escalated to 1.25%" in res1.rejection_reason

        # Stop is 1.30% (passes floor), but LLM score is 87.0 (below escalated 90.0)
        res2 = engine.check_pre_trade_memory_gates(
            symbol="AVAXUSDT",
            side="LONG",
            entry_price=25.0,
            planned_stop_pct=1.30,
            llm_score=87.0,
            current_time=now,
            trades=mock_trades,
        )
        assert not res2.passed
        assert res2.rule_triggered == "LLM_SCORE_TOO_LOW"
        assert "escalated to 90.0/100" in res2.rejection_reason

        # Stop is 1.30% and LLM score is 92.0 -> PASSES
        res3 = engine.check_pre_trade_memory_gates(
            symbol="AVAXUSDT",
            side="LONG",
            entry_price=25.0,
            planned_stop_pct=1.30,
            llm_score=92.0,
            current_time=now,
            trades=mock_trades,
        )
        assert res3.passed

    def test_streak_circuit_breaker_triggers_60m_cooldown(self):
        """2 consecutive losses within 90 minutes trigger a 60-minute cooling off period."""
        now = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)
        loss1_time = now - timedelta(minutes=10)  # Most recent loss 10m ago
        loss2_time = now - timedelta(minutes=40)  # Previous loss 40m ago (30m between losses)

        mock_trades = [
            {
                "trade_id": "TRD_LOSS_1",
                "symbol": "SOLUSDT",
                "side": "LONG",
                "status": "CLOSED",
                "exit_reason": "STOP_LOSS",
                "net_pnl_usd": -0.16,
                "exit_timestamp_utc": loss1_time.isoformat(),
            },
            {
                "trade_id": "TRD_LOSS_2",
                "symbol": "ETHUSDT",
                "side": "SHORT",
                "status": "CLOSED",
                "exit_reason": "STOP_LOSS",
                "net_pnl_usd": -0.15,
                "exit_timestamp_utc": loss2_time.isoformat(),
            },
        ]

        engine = TradeMemoryFeedbackEngine()
        summary = engine.analyze_recent_performance(trades=mock_trades, current_time=now)
        assert summary.cooling_off_active
        expected_cooldown_end = loss1_time + timedelta(minutes=60)
        assert summary.cooling_off_until == expected_cooldown_end.isoformat()

        # Attempting trade during cooldown is blocked
        res = engine.check_pre_trade_memory_gates(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=67000.0,
            planned_stop_pct=1.30,
            current_time=now,
            trades=mock_trades,
        )
        assert not res.passed
        assert res.rule_triggered == "STREAK_CIRCUIT_BREAKER"
        assert "STREAK_CIRCUIT_BREAKER_ACTIVE" in res.rejection_reason

        # 65 minutes after loss1, cooldown is over
        future_time = loss1_time + timedelta(minutes=65)
        res_future = engine.check_pre_trade_memory_gates(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=67000.0,
            planned_stop_pct=1.30,
            current_time=future_time,
            trades=mock_trades,
        )
        assert res_future.passed

    def test_episodic_memory_prompt_block_formatting(self):
        """Verify prompt block formats win rate, streak, failure modes, and recent trade logs."""
        now = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)
        mock_trades = [
            {
                "trade_id": "TRD_1",
                "symbol": "SOLUSDT",
                "side": "LONG",
                "strategy_id": "15M_SWEEP_RECLAIM_SMC",
                "average_entry_price": 101.45,
                "average_exit_price": 100.73,
                "planned_stop_pct": 0.74,
                "realized_r": -0.83,
                "net_pnl_usd": -0.16,
                "exit_reason": "STOP_LOSS",
                "exit_timestamp_utc": (now - timedelta(minutes=15)).isoformat(),
                "created_at_utc": (now - timedelta(minutes=25)).isoformat(),
            }
        ]

        engine = TradeMemoryFeedbackEngine()
        block = engine.build_episodic_memory_prompt_block(current_time=now, trades=mock_trades)

        assert "RECENT_LIVE_PERFORMANCE_AND_MISTAKES" in block
        assert "Rolling Last-10 Live Record: Win Rate 0.0% (0W / 1L)" in block
        assert "SOLUSDT LONG (15M_SWEEP_RECLAIM_SMC)" in block
        assert "Entry: 101.4500" in block
        assert "Exit: 100.7300" in block
        assert "TIGHT_STOP_<0.80PCT" in block

    def test_post_loss_autopsy_tagging(self):
        """Verify post-loss autopsy tags are accurately generated from trade attributes."""
        trade = AutomatedTradeRecord(
            journal_id="JRN_1",
            trade_id="TRD_1",
            symbol="SOLUSDT",
            strategy_id="EMA20_RECLAIM_5M_V1",
            planned_stop_pct=0.739,
            entry_fill_type="TAKER",
            holding_duration_seconds=535.0,  # < 10m
            realized_r=-0.83,
            exit_reason="STOP_LOSS",
            status="CLOSED",
        )

        tags = generate_post_loss_autopsy_tags(trade)
        assert "TIGHT_STOP_<0.80PCT" in tags
        assert "TAKER_ENTRY_LOSS" in tags
        assert "PREMATURE_STOP_OUT (<10m)" in tags
        assert "CHOP_CHASING_5M_EMA" in tags
        assert "FULL_R_LOSS" in tags

    def test_qwen_prompt_contains_feedback_and_self_correction_directive(self):
        """GroqAutoTrader system prompt incorporates episodic memory and self-correction directive."""
        from src.ai.groq_autotrader import GroqAutoTrader

        mock_adapter = MagicMock()
        mock_coord = MagicMock()
        mock_coord.current_equity = 15.0

        trader = GroqAutoTrader(adapter=mock_adapter, coordinator=mock_coord)

        # Mock feedback block
        test_block = "=== RECENT_LIVE_PERFORMANCE_AND_MISTAKES (EPISODIC TRADE MEMORY) ===\nDominant Failure Modes: TIGHT_STOP_<0.80PCT"
        trader.feedback_engine.build_episodic_memory_prompt_block = MagicMock(return_value=test_block)

        # Verify Stage 2 payload includes memory
        stage1_mock = MagicMock()
        stage1_mock.market_regime = {"regime": "CHOP"}
        cand_mock = MagicMock()
        cand_mock.symbol = "SOLUSDT"
        cand_mock.range_72h_pct = 30.0
        cand_mock.turnover_24h_m = 50.0
        cand_mock.model_dump.return_value = {"symbol": "SOLUSDT", "range_72h_pct": 30.0}
        stage1_mock.top_10 = [cand_mock]

        captured_messages = []
        def mock_call_json(messages, **kwargs):
            captured_messages.extend(messages)
            return {"top_3": [{"rank": 1, "symbol": "SOLUSDT", "category": "LIQUIDITY_SWEEP_RECLAIM", "score": 92}]}

        trader._call_groq_json = mock_call_json
        trader.run_stage2_rank_top3(stage1_mock)

        assert len(captured_messages) > 0
        user_msg = captured_messages[1]["content"]
        assert "recent_live_performance_and_mistakes" in user_msg
        assert "TIGHT_STOP_<0.80PCT" in user_msg
