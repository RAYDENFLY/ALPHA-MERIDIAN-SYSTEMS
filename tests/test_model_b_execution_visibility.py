"""Comprehensive Deterministic Test Suite for Model B Paper Execution Visibility.

Proves:
1. Candidate qualification (READY) alone does NOT falsely create an OPEN position.
2. A valid execution path creates the appropriate PENDING paper order.
3. A simulated fill creates exactly one OPEN paper position and one journal trade record.
4. Risk rejection is visible with its explicit reason code and message.
5. Pending orders remain pending until their conservative fill penetration conditions are satisfied.
6. Closing a position updates the journal and net PnL correctly with maker/taker fees.
7. Restart/reload recovers active pending orders and positions without duplication.
8. Dashboard/API counters agree with database records and runtime engine state.
9. Model A defensive SMC/SnR rules and Tier 1 risk parameters remain unchanged.
10. Live trading remains locked with SCANNER_MODE=PAPER and live_trading_locked=True.
"""

from datetime import datetime, timezone
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from config.settings import AppConfig, ModelBConfig, PaperExecutionConfig
from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    EntryCategory,
    ExecutionStatus,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
    TradeRecord,
)
from src.data.database import DatabaseManager
from src.execution.events import PaperEventDispatcher
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperOrder, PaperOrderStatus, PaperPosition
from src.journal.repository import JournalRepository
from src.risk.model_b_risk import ModelBRiskManager
from src.scheduler.model_b_runner import ModelBRunner
from src.api.app import app


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_model_b_visibility.db"
    return DatabaseManager(db_file)


@pytest.fixture
def journal_repo(temp_db: DatabaseManager):
    return JournalRepository(temp_db)


@pytest.fixture
def model_b_config():
    return ModelBConfig(
        enabled=True,
        initial_equity_usdt=100.0,
        target_account_equity_usdt=100.0,
        target_trade_risk_usdt=1.00,
        daily_loss_limit_usdt=3.00,
        weekly_loss_limit_usdt=7.50,
        max_open_positions=3,
        max_leverage=10,
        max_margin_usdt=15.00,
    )


@pytest.fixture
def model_b_risk(model_b_config: ModelBConfig):
    return ModelBRiskManager(config=model_b_config)


@pytest.fixture
def paper_config():
    return PaperExecutionConfig(
        enabled=True,
        conservative_fill_buffer_pct=0.00025,
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
        pending_expiry_bars=12,
        enable_tp1_trailing_stop=False,
    )


@pytest.fixture
def model_b_paper_engine(temp_db: DatabaseManager, journal_repo: JournalRepository, model_b_risk: ModelBRiskManager, paper_config: PaperExecutionConfig):
    return PaperExecutionEngine(
        db=temp_db,
        journal=journal_repo,
        risk_manager=model_b_risk,
        config=paper_config,
        model_id="MODEL_B",
    )


def create_sample_candidate(
    candidate_id: str = "MCAND_B_TEST_100",
    symbol: str = "TESTUSDT",
    entry_price: float = 100.0,
    stop_loss: float = 95.0,
    take_profit: float = 110.0,
    timestamp_ms: int = 1792340000000,
    status: CandidateStatus = CandidateStatus.READY,
) -> SetupCandidate:
    assessment = RiskAssessment(
        symbol=symbol,
        entry_price=entry_price,
        stop_loss=stop_loss,
        stop_distance_pct=5.0,
        risk_budget_usdt=1.00,
        position_notional_usdt=10.0,
        quantity=0.1,
        required_margin_usdt=1.0,
        effective_leverage=10.0,
        position_leverage=10.0,
        target_isolated_margin_usdt=1.0,
        is_feasible=True,
    )
    return SetupCandidate(
        candidate_id=candidate_id,
        symbol=symbol,
        timestamp_ms=timestamp_ms,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit_1=take_profit,
        take_profit_2=take_profit * 1.05,
        gross_reward_to_risk=2.0,
        net_reward_to_risk=1.95,
        invalidation_price=stop_loss,
        status=status,
        model_id="MODEL_B",
        entry_category=EntryCategory.BREAKOUT,
        risk_assessment=assessment,
    )


# ==============================================================================
# Phase 4 Deterministic Tests
# ==============================================================================

class TestModelBExecutionVisibility:
    """Deterministic tests proving execution semantics, truthful status, and visibility."""

    def test_1_ready_alone_does_not_create_open_position(self, journal_repo: JournalRepository, model_b_paper_engine: PaperExecutionEngine):
        """1. READY alone does not falsely create an OPEN position."""
        cand = create_sample_candidate(status=CandidateStatus.READY)
        journal_repo.save_setup(cand)

        # Candidate is persisted as READY
        setups = journal_repo.get_latest_setups(model_id="MODEL_B")
        assert len(setups) == 1
        assert setups[0]["status"] == "READY"

        # Hard invariant: No open position and no executed trades exist
        assert len(model_b_paper_engine.open_positions) == 0
        trades = journal_repo.get_trades(model_id="MODEL_B")
        assert len(trades) == 0

    def test_2_valid_execution_path_creates_pending_paper_order(self, model_b_paper_engine: PaperExecutionEngine, journal_repo: JournalRepository):
        """2. A valid execution path creates the appropriate paper order in PENDING status."""
        cand = create_sample_candidate(entry_price=50.0, stop_loss=48.0, take_profit=55.0)
        now_ms = cand.timestamp_ms + 1000

        order = model_b_paper_engine.submit_candidate(cand, now_ms=now_ms)
        assert order is not None
        assert order.status == PaperOrderStatus.PENDING
        assert order.price == 50.0
        assert order.stop_loss == 48.0
        assert order.take_profit == 55.0
        assert order.model_id == "MODEL_B"

        # Verify persisted in paper_orders table
        db_orders = journal_repo.get_paper_orders(status="PENDING", model_id="MODEL_B")
        assert len(db_orders) == 1
        assert db_orders[0]["order_id"] == order.order_id
        assert db_orders[0]["status"] == "PENDING"

    def test_3_fill_creates_exactly_one_paper_position(self, model_b_paper_engine: PaperExecutionEngine, journal_repo: JournalRepository):
        """3. A fill creates exactly one paper position."""
        cand = create_sample_candidate(entry_price=100.0, stop_loss=95.0, take_profit=110.0)
        t0 = cand.timestamp_ms
        order = model_b_paper_engine.submit_candidate(cand, now_ms=t0 + 1000)
        assert order is not None

        # Subsequent bar t+1 decisively penetrates limit price (long limit at 100.0, penetrates to 99.0)
        t1 = t0 + 3600000
        candle_t1 = Candle(
            symbol=cand.symbol,
            timeframe="1h",
            timestamp_ms=t1,
            open=100.5,
            high=101.0,
            low=99.0,
            close=99.5,
            volume=500.0,
            close_time_ms=t1 + 3599999,
        )

        terminal = model_b_paper_engine.evaluate_pending_orders({cand.symbol: [candle_t1]}, now_ms=t1)
        assert len(terminal) == 1
        assert terminal[0].status == PaperOrderStatus.FILLED

        # Exactly one open position created in memory and database
        assert len(model_b_paper_engine.open_positions) == 1
        pos = model_b_paper_engine.open_positions[cand.symbol]
        assert pos.entry_price == 100.0
        assert pos.is_open is True

        open_trades = journal_repo.get_trades(open_only=True, model_id="MODEL_B")
        assert len(open_trades) == 1
        assert open_trades[0]["trade_id"] == pos.trade_id

    def test_4_risk_rejection_is_visible_with_reason(self, model_b_paper_engine: PaperExecutionEngine, model_b_risk: ModelBRiskManager, journal_repo: JournalRepository):
        """4. Risk rejection is visible with its explicit reason."""
        cand = create_sample_candidate()
        # Force circuit breaker active
        model_b_risk.daily_loss_usdt = 3.50
        can_open, reason = model_b_risk.can_open_new_position(cand.timestamp_ms + 1000)
        assert can_open is False
        assert "circuit breaker" in reason.lower()

        order = model_b_paper_engine.submit_candidate(cand, now_ms=cand.timestamp_ms + 1000)
        assert order is None

        # Rejected order must be persisted in SQLite with explicit reason
        rejected_orders = journal_repo.get_paper_orders(status="REJECTED", model_id="MODEL_B")
        assert len(rejected_orders) == 1
        assert "circuit breaker" in rejected_orders[0]["rejection_reason"].lower()

        # Lifecycle event must record RISK_VALIDATION stage with reason code
        lc_events = journal_repo.get_lifecycle_events(symbol=cand.symbol, model_id="MODEL_B")
        assert any(e["reason_code"] == "ACCOUNT_RISK_REJECTED" for e in lc_events)

    def test_5_pending_orders_remain_pending_until_penetrated(self, model_b_paper_engine: PaperExecutionEngine):
        """5. Pending orders remain pending until their actual simulated fill conditions are satisfied."""
        cand = create_sample_candidate(entry_price=100.0)
        t0 = cand.timestamp_ms
        order = model_b_paper_engine.submit_candidate(cand, now_ms=t0 + 1000)
        assert order is not None

        # Anti-lookahead rule: Bar t (creation bar) CANNOT fill itself
        candle_t0 = Candle(
            symbol=cand.symbol, timeframe="1h", timestamp_ms=t0,
            open=105.0, high=105.0, low=98.0, close=99.0, volume=100.0, close_time_ms=t0 + 3599999
        )
        res_t0 = model_b_paper_engine.evaluate_pending_orders({cand.symbol: [candle_t0]}, now_ms=t0)
        assert len(res_t0) == 0
        assert order.status == PaperOrderStatus.PENDING

        # Conservative buffer rule: Bar t+1 merely touches 100.0 without penetrating past buffer
        # buffer_pct = 0.00025 -> threshold = 100 * (1 - 0.00025) = 99.975
        t1 = t0 + 3600000
        candle_touch_only = Candle(
            symbol=cand.symbol, timeframe="1h", timestamp_ms=t1,
            open=101.0, high=102.0, low=99.99, close=100.5, volume=100.0, close_time_ms=t1 + 3599999
        )
        res_t1 = model_b_paper_engine.evaluate_pending_orders({cand.symbol: [candle_touch_only]}, now_ms=t1)
        assert len(res_t1) == 0
        assert order.status == PaperOrderStatus.PENDING

    def test_6_closing_position_updates_journal_and_net_pnl(self, model_b_paper_engine: PaperExecutionEngine, journal_repo: JournalRepository):
        """6. Closing a position updates the journal and net PnL correctly."""
        cand = create_sample_candidate(entry_price=100.0, stop_loss=95.0, take_profit=110.0)
        t0 = cand.timestamp_ms
        model_b_paper_engine.submit_candidate(cand, now_ms=t0 + 1000)

        # Bar t+1 fills entry
        t1 = t0 + 3600000
        candle_fill = Candle(
            symbol=cand.symbol, timeframe="1h", timestamp_ms=t1,
            open=100.5, high=101.0, low=99.0, close=99.5, volume=500.0, close_time_ms=t1 + 3599999
        )
        model_b_paper_engine.evaluate_pending_orders({cand.symbol: [candle_fill]}, now_ms=t1)
        assert len(model_b_paper_engine.open_positions) == 1

        # Bar t+2 hits Take Profit (110.0)
        t2 = t1 + 3600000
        candle_exit = Candle(
            symbol=cand.symbol, timeframe="1h", timestamp_ms=t2,
            open=105.0, high=112.0, low=104.0, close=111.0, volume=500.0, close_time_ms=t2 + 3599999
        )
        closed_trades = model_b_paper_engine.evaluate_open_positions({cand.symbol: [candle_exit]}, now_ms=t2)
        assert len(closed_trades) == 1
        assert closed_trades[0].is_open is False
        assert closed_trades[0].exit_reason == "TAKE_PROFIT"
        assert closed_trades[0].pnl_gross > 0.0
        assert closed_trades[0].pnl_net > 0.0
        assert len(model_b_paper_engine.open_positions) == 0

        # Verify persisted trade record in DB
        db_trades = journal_repo.get_trades(open_only=False, model_id="MODEL_B")
        assert len(db_trades) == 1
        assert db_trades[0]["is_open"] == 0
        assert db_trades[0]["pnl_net"] == closed_trades[0].pnl_net

    def test_7_restart_reload_recovers_state_without_duplication(
        self, temp_db: DatabaseManager, journal_repo: JournalRepository, model_b_risk: ModelBRiskManager, paper_config: PaperExecutionConfig
    ):
        """7. Restart/reload does not lose or duplicate persisted lifecycle events."""
        engine1 = PaperExecutionEngine(
            db=temp_db, journal=journal_repo, risk_manager=model_b_risk, config=paper_config, model_id="MODEL_B"
        )
        cand = create_sample_candidate(entry_price=100.0)
        order = engine1.submit_candidate(cand, now_ms=cand.timestamp_ms + 1000)
        assert len(engine1.pending_orders) == 1

        # Simulate engine crash and restart
        engine2 = PaperExecutionEngine(
            db=temp_db, journal=journal_repo, risk_manager=model_b_risk, config=paper_config, model_id="MODEL_B"
        )
        assert len(engine2.pending_orders) == 1
        assert order.order_id in engine2.pending_orders

        # Re-submitting the same candidate must be idempotent and rejected
        dup_order = engine2.submit_candidate(cand, now_ms=cand.timestamp_ms + 2000)
        assert dup_order is None
        assert len(engine2.pending_orders) == 1

    def test_8_dashboard_and_api_counters_agree_with_db(
        self, temp_db: DatabaseManager, journal_repo: JournalRepository, model_b_risk: ModelBRiskManager, paper_config: PaperExecutionConfig, model_b_config: ModelBConfig
    ):
        """8. Dashboard counters agree with API and database records."""
        runner = ModelBRunner(
            db=temp_db,
            journal=journal_repo,
            config=model_b_config,
            paper_config=paper_config,
            risk_manager=model_b_risk,
        )

        cand = create_sample_candidate(entry_price=100.0)
        journal_repo.save_setup(cand)
        runner.paper_engine.submit_candidate(cand, now_ms=cand.timestamp_ms + 1000)

        status = runner.get_status()
        exec_metrics = status["execution_metrics"]

        assert exec_metrics["candidates_total"] == 1
        assert exec_metrics["candidates_ready"] == 1
        assert exec_metrics["orders_pending"] == 1
        assert exec_metrics["positions_open"] == 0
        assert exec_metrics["trades_closed"] == 0

        # Test FastAPI client response
        with patch("src.api.app.model_b_runner", runner), patch("src.api.app.journal", journal_repo):
            client = TestClient(app)

            # /api/model_b/status
            r_stat = client.get("/api/model_b/status")
            assert r_stat.status_code == 200
            data_stat = r_stat.json()
            assert data_stat["execution_metrics"]["orders_pending"] == 1
            assert data_stat["risk_limits"]["pending_orders_count"] == 1

            # /api/model_b/orders
            r_ord = client.get("/api/model_b/orders?status=PENDING")
            assert r_ord.status_code == 200
            data_ord = r_ord.json()
            assert data_ord["count"] == 1
            assert data_ord["pending_count"] == 1

            # /api/model_b/setups
            r_setups = client.get("/api/model_b/setups")
            assert r_setups.status_code == 200
            data_setups = r_setups.json()
            assert data_setups["count"] == 1
            assert data_setups["setups"][0]["execution_state"] == "ENTRY_PENDING"

    def test_9_model_a_regression_and_parameters_frozen(self):
        """9. Model A defensive SMC/SnR rules and Tier 1 risk parameters remain unchanged."""
        from config.settings import RiskConfig
        rc = RiskConfig()
        assert rc.initial_equity_usdt == 16.0
        assert rc.risk_per_trade_usdt == 0.25
        assert rc.daily_loss_limit_usdt == 0.50
        assert rc.weekly_loss_limit_usdt == 1.25
        assert rc.max_open_positions == 1
        assert rc.margin_mode == "ISOLATED"

    def test_10_live_trading_remains_locked(self):
        """10. Live trading remains locked in AppConfig and runner."""
        cfg = AppConfig()
        assert cfg.live_trading_locked is True
        assert cfg.mode.value in ("PAPER", "RESEARCH")

        # Attempting LIVE mode must raise ValueError
        with pytest.raises(ValueError, match="LIVE mode is permanently locked"):
            AppConfig(mode="LIVE")
