"""Unit and integration tests for Model B margin sizing audit and exchange-style paper positions.

Verifies:
1. Mathematical root cause of INFEASIBLE margin rejections (ASTERUSDT $22.44, SKHYNIXUSDT $28.63).
2. Mathematical feasibility of setups within margin cap (ZECUSDT $12.00).
3. Explicit clamping of Model B leverage to 10.0x max.
4. Enriched exchange-style paper positions table telemetry (mark price, net UPnL, ROE, simulated margin, liquidation NOT MODELED).
5. Dedicated /api/model_b/positions endpoint contract.
6. Absolute separation of metrics: pending orders vs open positions vs closed trades.
7. Model A freeze and live trading lock invariants.
"""

import pytest
from pathlib import Path
import tempfile
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from src.core.types import Candle, CandidateStatus, Direction, ExecutionStatus, MarketBias, SetupCandidate, StructureState, TradeRecord
from src.data.database import DatabaseManager
from src.exchange.normalized import NormalizedInstrument
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperOrder, PaperOrderStatus, PaperPosition
from src.journal.repository import JournalRepository
from src.risk.model_b_risk import ModelBRiskManager
from src.strategy.model_b.generator import ModelBStrategyGenerator
from src.scheduler.model_b_runner import ModelBRunner
from config.settings import AppConfig, ModelBConfig, PaperExecutionConfig, RiskConfig


@pytest.fixture
def temp_db_and_journal():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_model_b_margin.db"
        db = DatabaseManager(str(db_path))
        journal = JournalRepository(db)
        yield db, journal


class TestModelBMarginSizingAudit:
    def test_1_asterusdt_margin_rejection_mathematical_proof(self):
        """Verify why ASTERUSDT requires $22.44 margin (> $15 cap) and is legitimately rejected."""
        config = ModelBConfig(target_trade_risk_usdt=1.0, max_leverage=10.0, max_margin_usdt=15.0)
        generator = ModelBStrategyGenerator(config=config)

        inst = NormalizedInstrument(
            symbol="ASTERUSDT",
            exchange_name="asterdex",
            base_asset="ASTER",
            quote_asset="USDT",
            min_qty=0.01,
            step_size=0.01,
            tick_size=0.0001,
            min_notional=5.0,
        )

        # Real recorded values for ASTERUSDT:
        entry = 0.6938
        stop = 0.6907086
        stop_dist = abs(entry - stop)  # ~0.0030914 (0.445% stop)

        assessment = generator.size_position(entry_price=entry, stop_loss=stop, instrument=inst)

        # Proof:
        # raw_qty = 1.00 / 0.0030914 = 323.477
        # rounded_qty = 323.47
        # notional = 323.47 * 0.6938 = 224.42
        # margin at 10x = 22.44 USDT
        assert assessment.is_feasible is False
        assert assessment.required_margin_usdt > 15.0
        assert round(assessment.required_margin_usdt, 2) == 22.44
        assert any("exceeds Model B cap $15.00" in r for r in assessment.rejection_reasons)
        assert any("exceeds risk budget cap $1.25" in r for r in assessment.rejection_reasons)

    def test_2_skhynixusdt_margin_rejection_mathematical_proof(self):
        """Verify why SKHYNIXUSDT requires $28.63 margin (> $15 cap) and is legitimately rejected."""
        config = ModelBConfig(target_trade_risk_usdt=1.0, max_leverage=10.0, max_margin_usdt=15.0)
        generator = ModelBStrategyGenerator(config=config)

        inst = NormalizedInstrument(
            symbol="SKHYNIXUSDT",
            exchange_name="asterdex",
            base_asset="SKHYNIX",
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
            min_notional=5.0,
        )

        # Real recorded values for SKHYNIXUSDT:
        entry = 891.99
        stop = 888.88023
        stop_dist = abs(entry - stop)  # ~3.10977 (0.348% stop)

        assessment = generator.size_position(entry_price=entry, stop_loss=stop, instrument=inst)

        # Proof:
        # raw_qty = 1.00 / 3.10977 = 0.32156
        # rounded_qty = 0.321
        # notional = 0.321 * 891.99 = 286.33
        # margin at 10x = 28.63 USDT
        assert assessment.is_feasible is False
        assert assessment.required_margin_usdt > 15.0
        assert round(assessment.required_margin_usdt, 2) == 28.63
        assert any("exceeds Model B cap $15.00" in r for r in assessment.rejection_reasons)
        assert any("exceeds risk budget cap $1.25" in r for r in assessment.rejection_reasons)

    def test_3_zecusdt_margin_feasibility_proof(self):
        """Verify why ZECUSDT requires $12.00 margin (<= $15 cap) and is marked FEASIBLE / READY."""
        config = ModelBConfig(target_trade_risk_usdt=1.0, max_leverage=10.0, max_margin_usdt=15.0)
        generator = ModelBStrategyGenerator(config=config)

        inst = NormalizedInstrument(
            symbol="ZECUSDT",
            exchange_name="asterdex",
            base_asset="ZEC",
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
            min_notional=5.0,
        )

        # Real recorded values for ZECUSDT:
        entry = 1081.31
        stop = 1072.31661
        stop_dist = abs(entry - stop)  # ~8.99339 (0.832% stop)

        assessment = generator.size_position(entry_price=entry, stop_loss=stop, instrument=inst)

        # Proof:
        # raw_qty = 1.00 / 8.99339 = 0.11119
        # rounded_qty = 0.111
        # notional = 0.111 * 1081.31 = 120.025
        # margin at 10x = 12.00 USDT (<= $15 cap)
        # est loss = 0.998 + 0.060 + 0.060 + 0.060 + 0.012 = 1.19 <= $1.25 cap
        assert assessment.is_feasible is True
        assert assessment.required_margin_usdt <= 15.0
        assert round(assessment.required_margin_usdt, 2) == 12.00
        assert assessment.quantity == 0.111
        assert len(assessment.rejection_reasons) == 0

    def test_4_leverage_is_clamped_to_10x_max(self):
        """Verify that Model B strictly clamps leverage to 10.0x max even if higher is passed in config."""
        overleveraged_config = ModelBConfig(target_trade_risk_usdt=1.0, max_leverage=15.0, max_margin_usdt=15.0)
        generator = ModelBStrategyGenerator(config=overleveraged_config)

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

        assessment = generator.size_position(entry_price=60000.0, stop_loss=59500.0, instrument=inst)
        # Leverage must be clamped to 10.0x
        assert assessment.position_leverage == 10.0
        # Notional = 0.002 * 60000 = 120.0 -> required margin at 10x is 12.00, NOT 120 / 15 = 8.00
        assert assessment.required_margin_usdt == 12.0


class TestModelBExchangeStylePaperPositions:
    def test_1_enriched_open_positions_telemetry(self, temp_db_and_journal):
        """Verify that get_enriched_open_positions provides all 8 required columns and exchange-style metrics."""
        db, journal = temp_db_and_journal
        risk_manager = ModelBRiskManager()
        paper_engine = PaperExecutionEngine(
            db=db,
            journal=journal,
            risk_manager=risk_manager,
            model_id="MODEL_B",
        )

        # Mock exchange adapter returning live mark price
        mock_exchange = MagicMock()
        mock_exchange.get_premium_index.return_value = {
            "symbol": "AGTUSDT",
            "mark_price": 0.07000,
        }

        runner = ModelBRunner(
            db=db,
            journal=journal,
            exchange=mock_exchange,
            risk_manager=risk_manager,
            paper_engine=paper_engine,
        )
        runner.get_mark_price = MagicMock(return_value=(0.07000, 1792342800000, 'FRESH'))

        # Create an open position matching AGTUSDT filled order
        t_now = 1792342800000
        open_pos = PaperPosition(
            trade_id="PTRD_AGTUSDT_1792341746379",
            order_id="PORD_AGTUSDT_1792341746379",
            setup_id="MCAND_B_AGTUSDT_LONG_1792341600000",
            symbol="AGTUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=t_now,
            entry_price=0.066935,
            stop_loss=0.06500,
            take_profit=0.07500,
            quantity=190.0,
            notional_usdt=12.71765,
            margin_usdt=1.2718,
            leverage=10.0,
            entry_fee=0.00254353,
            model_id="MODEL_B",
            take_profit_1=0.07500,
        )
        paper_engine.open_positions["AGTUSDT"] = open_pos

        enriched = runner.get_enriched_open_positions()
        assert len(enriched) == 1
        pos = enriched[0]

        # Column 1: Symbol & Mode
        assert pos["symbol"] == "AGTUSDT"
        assert pos["side"] == "LONG"
        assert pos["margin_mode_display"] == "SIMULATED (10x)"
        assert pos["margin_mode"] == "SIMULATED_ISOLATED"
        assert pos["is_simulated"] is True

        # Column 2: Size & Notional
        assert pos["position_amt"] == 190.0
        assert pos["notional_usdt"] == 12.7177

        # Column 3: Entry / Mark
        assert pos["entry_price"] == 0.066935
        assert pos["mark_price"] == 0.07000

        # Column 4: Allocated Margin
        assert pos["allocated_margin_usdt"] == 1.2718
        assert pos["allocated_margin_label"] == "$1.27"

        # Column 5: TP / SL Targets
        assert pos["protection_status"] == "PROTECTED"
        assert "$0.0750" in pos["take_profit_label"]
        assert "$0.0650" in pos["stop_loss_label"]

        # Column 6: Estimated Liquidation
        assert pos["liquidation_price"] is None
        assert pos["liquidation_label"] == "NOT MODELED"

        # Column 7: Unrealized PnL & ROE
        # Gross UPnL: (0.07000 - 0.066935) * 190 = 0.58235
        assert round(pos["unrealized_pnl_gross"], 4) == 0.5824
        # Exit fee est: 190 * 0.07000 * 0.0005 = 0.00665
        assert round(pos["estimated_exit_fee"], 4) == 0.0067
        # Net UPnL: 0.58235 - 0.00254353 - 0.00665 = 0.573156
        assert round(pos["unrealized_pnl_net_usdt"], 4) == 0.5732
        # ROE: 0.5732 / 1.2718 * 100 = 45.07%
        assert pos["roe_pct"] > 40.0

    def test_2_api_model_b_positions_endpoint(self, monkeypatch, temp_db_and_journal):
        """Verify GET /api/model_b/positions returns truthful Model B positions."""
        import src.api.app as app_module
        from src.api.app import app

        db, journal = temp_db_and_journal
        risk_manager = ModelBRiskManager()
        paper_engine = PaperExecutionEngine(
            db=db,
            journal=journal,
            risk_manager=risk_manager,
            model_id="MODEL_B",
        )

        runner = ModelBRunner(
            db=db,
            journal=journal,
            risk_manager=risk_manager,
            paper_engine=paper_engine,
        )

        pos = PaperPosition(
            trade_id="PTRD_TEST_1",
            order_id="PORD_TEST_1",
            setup_id="MCAND_B_TEST_1",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1700000000000,
            entry_price=60000.0,
            stop_loss=59500.0,
            take_profit=61500.0,
            quantity=0.002,
            notional_usdt=120.0,
            margin_usdt=12.0,
            leverage=10.0,
            entry_fee=0.024,
            model_id="MODEL_B",
        )
        paper_engine.open_positions["BTCUSDT"] = pos

        monkeypatch.setattr(app_module, "model_b_runner", runner)
        client = TestClient(app)

        res = client.get("/api/model_b/positions")
        assert res.status_code == 200
        data = res.json()

        assert data["model_id"] == "MODEL_B"
        assert data["count"] == 1
        assert data["is_simulated"] is True
        assert data["mode"] == "SIMULATED_ISOLATED"
        pos_res = data["positions"][0]
        assert pos_res["symbol"] == "BTCUSDT"
        assert pos_res["margin_mode_display"] == "SIMULATED (10x)"
        assert pos_res["liquidation_label"] == "NOT MODELED"

    def test_3_statistics_separation_between_open_pending_closed(self, temp_db_and_journal):
        """Verify open positions are strictly excluded from closed trade metrics, and pending orders are isolated."""
        db, journal = temp_db_and_journal
        risk_manager = ModelBRiskManager()
        paper_engine = PaperExecutionEngine(
            db=db,
            journal=journal,
            risk_manager=risk_manager,
            model_id="MODEL_B",
        )
        runner = ModelBRunner(
            db=db,
            journal=journal,
            risk_manager=risk_manager,
            paper_engine=paper_engine,
        )

        # 1. Add 1 pending order
        pending_order = PaperOrder(
            order_id="PORD_PENDING_1",
            candidate_id="MCAND_PENDING_1",
            symbol="ZECUSDT",
            direction=Direction.LONG,
            order_type="LIMIT",
            price=1081.31,
            stop_loss=1072.31,
            take_profit=1100.0,
            quantity=0.111,
            notional_usdt=120.0,
            margin_usdt=12.0,
            leverage=10.0,
            created_bar_time_ms=1700000000000,
            status=PaperOrderStatus.PENDING,
            model_id="MODEL_B",
        )
        paper_engine.pending_orders["PORD_PENDING_1"] = pending_order

        # 2. Add 1 open position
        open_pos = PaperPosition(
            trade_id="PTRD_OPEN_1",
            order_id="PORD_FILLED_1",
            setup_id="MCAND_OPEN_1",
            symbol="AGTUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1700000000000,
            entry_price=0.066935,
            stop_loss=0.0650,
            take_profit=0.0750,
            quantity=190.0,
            notional_usdt=12.71,
            margin_usdt=1.27,
            leverage=10.0,
            entry_fee=0.0025,
            model_id="MODEL_B",
        )
        paper_engine.open_positions["AGTUSDT"] = open_pos

        trade = TradeRecord(
            trade_id="PTRD_OPEN_1",
            setup_id="MCAND_OPEN_1",
            symbol="AGTUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1700000000000,
            entry_price=0.066935,
            stop_loss=0.0650,
            take_profit=0.0750,
            quantity=190.0,
            notional_usdt=12.71,
            margin_usdt=1.27,
            leverage=10.0,
            fee_paid=0.0025,
            is_open=True,
            model_id="MODEL_B",
        )
        journal.save_trade(trade)
        risk_manager.sync_state_from_journal(journal, now_ms=1700000000000, model_id="MODEL_B")

        # Check status
        status = runner.get_status()
        exec_metrics = status["execution_metrics"]

        # Assert clean separation:
        assert exec_metrics["orders_pending"] == 1
        assert exec_metrics["positions_open"] == 1
        assert exec_metrics["trades_closed"] == 0

        # Risk limits check:
        assert status["risk_limits"]["open_positions_count"] == 1
        assert status["risk_limits"]["pending_orders_count"] == 1

        # Realized PnL is 0.0 because open position is NOT closed
        assert status["risk_limits"]["daily_loss_realized_usdt"] == 0.0

    def test_4_model_a_freeze_and_safety_locks_preserved(self):
        """Verify Model A 11-symbol whitelist, Tier 1 parameters, and safety invariants remain frozen."""
        cfg = AppConfig()

        # Model A whitelist check
        assert len(cfg.watchlist) == 11
        assert "BTCUSDT" in cfg.watchlist
        assert "AGTUSDT" not in cfg.watchlist  # AGTUSDT is Tier 2 / Model B only

        # Tier 1 Risk Policy check
        tier1_risk = RiskConfig()
        assert tier1_risk.initial_equity_usdt == 16.0
        assert tier1_risk.daily_loss_limit_usdt == 0.50
        assert tier1_risk.weekly_loss_limit_usdt == 1.25
        assert tier1_risk.max_open_positions == 1
        assert tier1_risk.max_leverage == 10

        # Model B config check
        model_b_config = ModelBConfig()
        assert model_b_config.target_account_equity_usdt == 100.0
        assert model_b_config.target_trade_risk_usdt == 1.0
        assert model_b_config.max_margin_usdt == 15.0
        assert model_b_config.max_open_positions == 6
        assert model_b_config.max_leverage == 10.0
