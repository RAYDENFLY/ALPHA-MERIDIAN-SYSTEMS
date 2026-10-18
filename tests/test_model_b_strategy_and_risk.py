"""Unit tests for Model B strategy generation, position sizing, and risk management."""

import pytest
from pathlib import Path
import tempfile
from unittest.mock import MagicMock

from src.core.types import Candle, CandidateStatus, Direction, EntryCategory, TradeRecord
from src.data.database import DatabaseManager
from src.exchange.normalized import NormalizedInstrument, LiquiditySnapshot
from src.journal.repository import JournalRepository
from src.risk.model_b_risk import ModelBRiskManager
from src.strategy.model_b.generator import ModelBStrategyGenerator
from config.settings import ModelBConfig


class TestModelBStrategyAndSizing:
    def test_position_sizing_within_risk_budget(self):
        config = ModelBConfig(target_trade_risk_usdt=1.0, max_leverage=10.0, max_margin_usdt=15.0)
        generator = ModelBStrategyGenerator(config=config)

        inst = NormalizedInstrument(
            symbol="BTCUSDT",
            exchange_name="asterdex",
            base_asset="BTC",
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.1,
            min_notional=5.0,
        )

        # Entry = 60000, SL = 59500 -> stop_distance = 500
        # raw_qty = 1.00 / 500 = 0.002 BTC
        # Notional = 0.002 * 60000 = $120.00 USDT
        # Margin at 10x = $12.00 USDT (<= $15.00 cap)
        assessment = generator.size_position(entry_price=60000.0, stop_loss=59500.0, instrument=inst)

        assert assessment.is_feasible is True
        assert assessment.quantity == 0.002
        assert assessment.position_notional_usdt == 120.0
        assert assessment.target_isolated_margin_usdt == 12.0
        assert assessment.position_leverage == 10.0

    def test_position_sizing_rejects_excess_margin(self):
        config = ModelBConfig(target_trade_risk_usdt=1.0, max_leverage=10.0, max_margin_usdt=15.0)
        generator = ModelBStrategyGenerator(config=config)

        inst = NormalizedInstrument(
            symbol="BTCUSDT",
            exchange_name="asterdex",
            base_asset="BTC",
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.1,
            min_notional=5.0,
        )

        # Very tight stop: 60000 to 59980 -> stop distance = 20
        # raw_qty = 1.00 / 20 = 0.05 BTC -> notional $3000 -> required margin $300 (> $15 cap)
        assessment = generator.size_position(entry_price=60000.0, stop_loss=59980.0, instrument=inst)

        assert assessment.is_feasible is False
        assert any("exceeds Model B cap" in r for r in assessment.rejection_reasons)


class TestModelBRiskManager:
    def test_initial_state_and_live_lock(self):
        risk = ModelBRiskManager()
        assert risk.initial_equity == 100.0
        assert risk.equity_usdt == 100.0
        assert risk.trade_risk_usdt == 1.0
        assert risk.daily_loss_limit == 3.0
        assert risk.weekly_loss_limit == 7.5
        assert risk.max_open_positions == 3
        assert risk.live_trading_locked is True

    def test_concurrent_position_gating(self):
        risk = ModelBRiskManager(max_open_positions=3)
        now_ms = 1700000000000

        # Open 3 positions
        for i in range(3):
            can_open, _ = risk.can_open_new_position(now_ms)
            assert can_open is True
            trd = TradeRecord(trade_id=f"T{i}", symbol=f"SYM{i}", direction=Direction.LONG, entry_timestamp_ms=now_ms, entry_price=100.0, stop_loss=90.0, take_profit=120.0, quantity=1.0, notional_usdt=100.0, is_open=True, model_id="MODEL_B")
            risk.record_entry(trd)

        # 4th position must be rejected
        can_open_4th, reason = risk.can_open_new_position(now_ms)
        assert can_open_4th is False
        assert "concurrent position cap reached" in str(reason)

    def test_daily_circuit_breaker_tripping(self):
        risk = ModelBRiskManager(daily_loss_limit_usdt=3.0)
        now_ms = 1700000000000

        # Close losing trade with -$3.10 net PnL
        trd = TradeRecord(trade_id="TL1", symbol="BTCUSDT", direction=Direction.LONG, entry_timestamp_ms=now_ms - 3600000, entry_price=60000.0, stop_loss=59000.0, take_profit=62000.0, quantity=0.003, notional_usdt=180.0, exit_timestamp_ms=now_ms, exit_price=59000.0, pnl_net=-3.10, is_open=False, model_id="MODEL_B")
        risk.record_exit(trd)

        assert risk.daily_loss_usdt == 3.10
        can_open, reason = risk.can_open_new_position(now_ms)
        assert can_open is False
        assert "daily loss circuit breaker triggered" in str(reason)

    def test_journal_sync_model_isolation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_risk_sync.db"
            db = DatabaseManager(db_path)
            journal = JournalRepository(db)

            # Insert a Model A trade (-$0.50) and a Model B trade (-$1.50)
            now_ms = 1700000000000
            trd_a = TradeRecord(trade_id="MA1", symbol="BTCUSDT", direction=Direction.LONG, entry_timestamp_ms=now_ms - 3600000, entry_price=60000.0, stop_loss=59000.0, take_profit=62000.0, quantity=0.001, notional_usdt=60.0, exit_timestamp_ms=now_ms, exit_price=59500.0, pnl_net=-0.50, is_open=False, model_id="MODEL_A")
            trd_b = TradeRecord(trade_id="MB1", symbol="SOLUSDT", direction=Direction.LONG, entry_timestamp_ms=now_ms - 3600000, entry_price=150.0, stop_loss=140.0, take_profit=170.0, quantity=0.15, notional_usdt=22.5, exit_timestamp_ms=now_ms, exit_price=140.0, pnl_net=-1.50, is_open=False, model_id="MODEL_B")

            journal.save_trade(trd_a)
            journal.save_trade(trd_b)

            risk_b = ModelBRiskManager()
            risk_b.sync_state_from_journal(journal, now_ms=now_ms)

            # Model B should only see -$1.50 loss, NOT Model A's -$0.50!
            assert risk_b.daily_loss_usdt == 1.50
            assert risk_b.equity_usdt == 100.0 - 1.50
