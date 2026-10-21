"""Comprehensive tests for Model B Circuit Breaker Override & Diagnostic Framework.

Verifies:
1. Baseline Breaker Behavior: loss limit breaches block new entries when override is False.
2. Override Enabled: loss breakers bypassed for paper-trading while position caps remain strictly enforced.
3. Live-Trading Safety: override is refused/neutralized if live-trading lock is released or mode is LIVE.
4. Entry Kill Switch: immediately halts new paper entries and cancels resting orders.
5. Model A Isolation: Model A risk governance and execution pipeline remain 100% frozen and unaffected.
6. Read-Only Ledger Diagnostics: LONG vs SHORT metric segregation, trade deduplication, and mathematical reconciliation.
7. Diagnostic API Endpoints: report retrieval across cohorts and mutating kill switch endpoint.
"""

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from config.settings import AppConfig, ModelBConfig, ExecutionMode
from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    EntryCategory,
    ExecutionStatus,
    SetupCandidate,
    TradeRecord,
)
from src.execution.types import (
    PaperOrder,
    PaperOrderStatus,
    PaperPosition,
)
from src.execution.paper_engine import PaperExecutionEngine
from src.journal.repository import JournalRepository
from src.research.model_b_diagnostics import (
    breaker_reconciliation,
    build_report,
    dedupe_trades,
    direction_metrics,
    signal_stats,
)
from src.risk.manager import RiskManager as ModelARiskManager
from src.risk.model_b_diagnostic import (
    DEFAULT_STATE_PATH,
    baseline_snapshot,
    override_permitted,
    resolve_override_state,
)
from src.risk.model_b_risk import ModelBRiskManager


@pytest.fixture
def temp_db_and_journal(tmp_path: Path):
    db_path = tmp_path / "test_scanner.db"
    from src.data.database import DatabaseManager

    db = DatabaseManager(db_path=db_path)
    db.init_db()
    journal = JournalRepository(db=db)
    return db, journal


class TestBaselineBreakerBehavior:
    """Verifies baseline risk manager behavior with override disabled."""

    def test_weekly_breaker_blocks_entries_at_threshold(self):
        risk = ModelBRiskManager(weekly_loss_limit_usdt=7.50, diagnostic_override_active=False)
        now_ms = int(datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # Baseline: 0 losses -> allowed
        can_open, reason = risk.can_open_new_position(now_ms)
        assert can_open is True
        assert reason is None

        # Simulate losing trades reaching $9.00 (the exact W42 state)
        risk.weekly_loss_usdt = 9.0008
        can_open, reason = risk.can_open_new_position(now_ms)
        assert can_open is False
        assert "weekly loss circuit breaker triggered" in str(reason)
        assert risk.get_risk_summary()["weekly_circuit_breaker_active"] is True
        assert risk.get_risk_summary()["weekly_circuit_breaker_enforced"] is True

    def test_daily_breaker_blocks_entries_at_threshold(self):
        risk = ModelBRiskManager(daily_loss_limit_usdt=3.00, diagnostic_override_active=False)
        now_ms = int(datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        risk._sync_calendar_buckets(now_ms)
        risk.daily_loss_usdt = 3.10
        can_open, reason = risk.can_open_new_position(now_ms)
        assert can_open is False
        assert "daily loss circuit breaker triggered" in str(reason)
        assert risk.get_risk_summary()["daily_circuit_breaker_active"] is True
        assert risk.get_risk_summary()["daily_circuit_breaker_enforced"] is True


class TestDiagnosticOverrideBehavior:
    """Verifies controlled breaker bypass during the research experiment."""

    def test_override_bypasses_loss_breaker_while_reporting_breach(self):
        risk = ModelBRiskManager(
            daily_loss_limit_usdt=3.00,
            weekly_loss_limit_usdt=7.50,
            diagnostic_override_active=True,
            live_trading_locked=True,
        )
        now_ms = int(datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # Simulate breached weekly loss ($9.00 / $7.50)
        risk._sync_calendar_buckets(now_ms)
        risk.weekly_loss_usdt = 9.0008
        can_open, reason = risk.can_open_new_position(now_ms)

        # Entries must be permitted under the diagnostic override
        assert can_open is True
        assert reason is None

        # Telemetry must accurately distinguish active breach vs non-enforcement
        summary = risk.get_risk_summary()
        assert summary["weekly_circuit_breaker_active"] is True
        assert summary["weekly_circuit_breaker_enforced"] is False
        assert summary["diagnostic_override_active"] is True

    def test_override_strictly_preserves_position_caps(self):
        risk = ModelBRiskManager(
            max_open_positions=3,
            diagnostic_override_active=True,
            live_trading_locked=True,
        )
        now_ms = int(datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        # Fill 3 positions
        for i in range(3):
            trd = TradeRecord(
                trade_id=f"T_DIAG_{i}",
                symbol=f"SYM{i}",
                direction=Direction.LONG,
                entry_timestamp_ms=now_ms,
                entry_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
                quantity=1.0,
                notional_usdt=100.0,
                is_open=True,
                model_id="MODEL_B",
            )
            risk.record_entry(trd)

        # 4th position MUST still be rejected by position cap
        can_open, reason = risk.can_open_new_position(now_ms)
        assert can_open is False
        assert "Model B concurrent position cap reached (3/3)" in str(reason)


class TestLiveTradingSafetyLock:
    """Verifies that the diagnostic override is impossible in live trading."""

    def test_override_resolution_refuses_live_mode(self, tmp_path):
        cfg = AppConfig()
        cfg.mode = ExecutionMode.LIVE
        cfg.live_trading_locked = False
        cfg.model_b.diagnostic_breaker_override = True

        state = resolve_override_state(cfg, path=tmp_path / "diag.json")
        assert state["active"] is False

    def test_risk_manager_ignores_override_if_live_trading_unlocked(self):
        risk = ModelBRiskManager(
            weekly_loss_limit_usdt=7.50,
            diagnostic_override_active=True,
            live_trading_locked=False,  # Safety lock breached or live mode
        )
        now_ms = int(datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        risk._sync_calendar_buckets(now_ms)
        risk.weekly_loss_usdt = 9.00

        # Must reject entry because live_trading_locked is False!
        can_open, reason = risk.can_open_new_position(now_ms)
        assert can_open is False
        assert "weekly loss circuit breaker triggered" in str(reason)


class TestEntryKillSwitch:
    """Verifies the manual entry kill switch for immediate cessation of entries."""

    def test_kill_switch_blocks_new_positions_immediately(self):
        risk = ModelBRiskManager(
            diagnostic_override_active=True,
            entry_kill_switch=True,
            live_trading_locked=True,
        )
        now_ms = int(datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

        can_open, reason = risk.can_open_new_position(now_ms)
        assert can_open is False
        assert "Model B entry kill switch is ON" in str(reason)

    def test_kill_switch_cancels_resting_orders_in_paper_engine(self, temp_db_and_journal):
        db, journal = temp_db_and_journal
        risk = ModelBRiskManager(entry_kill_switch=True)
        engine = PaperExecutionEngine(db=db, journal=journal, risk_manager=risk, model_id="MODEL_B")

        # Create a pending limit order
        now_ms = 1700000000000
        order = PaperOrder(
            order_id="PORD_RESTING_1",
            candidate_id="CAND_1",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            order_type="LIMIT",
            price=60000.0,
            stop_loss=59000.0,
            take_profit=62000.0,
            quantity=0.001,
            notional_usdt=60.0,
            margin_usdt=6.0,
            leverage=10.0,
            created_bar_time_ms=now_ms,
            status=PaperOrderStatus.PENDING,
            model_id="MODEL_B",
        )
        engine.pending_orders[order.order_id] = order
        journal.save_paper_order(order.to_dict())

        candle = Candle(
            symbol="BTCUSDT",
            timeframe="1h",
            timestamp_ms=now_ms + 60000,
            open=60500.0,
            high=61000.0,
            low=59500.0,  # Penetrates limit price 60000.0!
            close=60200.0,
            volume=100.0,
            close_time_ms=now_ms + 120000,
        )

        terminal_orders = engine.evaluate_pending_orders({"BTCUSDT": [candle]}, now_ms=now_ms + 60000)

        # Because kill switch is ON, the resting order must be cancelled rather than filled!
        assert len(terminal_orders) == 1
        assert terminal_orders[0].status == PaperOrderStatus.CANCELLED
        assert "entry kill switch ON" in terminal_orders[0].rejection_reason
        assert len(engine.pending_orders) == 0
        assert len(engine.open_positions) == 0


class TestModelACompleteIsolation:
    """Verifies that Model A is completely untouched and isolated from Model B override."""

    def test_model_a_risk_manager_unaffected(self):
        # Model A uses RiskManager with conservative capital and limits
        model_a_risk = ModelARiskManager()
        model_b_risk = ModelBRiskManager(diagnostic_override_active=True, live_trading_locked=True)

        assert not hasattr(model_a_risk, "diagnostic_override_active")
        assert model_a_risk.max_open_positions == 1
        assert model_a_risk.daily_loss_limit == 0.18
        assert model_a_risk.weekly_loss_limit == 0.54

        # Bypassing Model B has zero effect on Model A
        now_ms = 1700000000000
        model_b_risk._sync_calendar_buckets(now_ms)
        model_b_risk.weekly_loss_usdt = 10.0
        assert model_b_risk.can_open_new_position(now_ms)[0] is True

        model_a_risk._sync_calendar_buckets(now_ms)
        model_a_risk.weekly_loss_usdt = 0.60
        assert model_a_risk.can_open_new_position(now_ms)[0] is False


class TestReadOnlyDiagnosticsModule:
    """Verifies trade deduplication, directional metric separation, and ledger reconciliation."""

    def test_trade_deduplication(self):
        trades = [
            {"trade_id": "T1", "symbol": "BTCUSDT", "direction": "LONG", "entry_timestamp_ms": 1000, "pnl_net": 1.0},
            {"trade_id": "T1", "symbol": "BTCUSDT", "direction": "LONG", "entry_timestamp_ms": 1000, "pnl_net": 1.0}, # dup by ID
            {"trade_id": "T2", "symbol": "BTCUSDT", "direction": "LONG", "entry_timestamp_ms": 1000, "pnl_net": 1.0}, # dup by key
            {"trade_id": "T3", "symbol": "ETHUSDT", "direction": "SHORT", "entry_timestamp_ms": 2000, "pnl_net": -1.0},
        ]
        unique, dups = dedupe_trades(trades)
        assert len(unique) == 2
        assert dups == 2
        assert [t["trade_id"] for t in unique] == ["T1", "T3"]

    def test_direction_metrics_separation(self):
        trades = [
            # LONG trade: Win
            {"trade_id": "TL1", "direction": "LONG", "is_open": False, "exit_timestamp_ms": 2000, "pnl_gross": 2.0, "fee_paid": 0.1, "funding_paid": 0.0, "pnl_net": 1.9, "exit_reason": "TAKE_PROFIT_1"},
            # SHORT trade: Loss 1
            {"trade_id": "TS1", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": 3000, "pnl_gross": -1.0, "fee_paid": 0.1, "funding_paid": 0.0, "pnl_net": -1.1, "exit_reason": "STOP_LOSS"},
            # SHORT trade: Loss 2
            {"trade_id": "TS2", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": 4000, "pnl_gross": -0.8, "fee_paid": 0.1, "funding_paid": 0.0, "pnl_net": -0.9, "exit_reason": "STOP_LOSS"},
        ]
        long_m = direction_metrics(trades, "LONG")
        short_m = direction_metrics(trades, "SHORT")

        assert long_m["closed_trades"] == 1
        assert long_m["win_rate_pct"] == 100.0
        assert long_m["net_realized_pnl"] == 1.9
        assert long_m["exit_reason_distribution"] == {"TAKE_PROFIT_1": 1}

        assert short_m["closed_trades"] == 2
        assert short_m["win_rate_pct"] == 0.0
        assert short_m["net_realized_pnl"] == -2.0
        assert short_m["avg_loss"] == -1.0
        assert short_m["exit_reason_distribution"] == {"STOP_LOSS": 2}

    def test_breaker_mathematical_reconciliation(self):
        now = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
        now_ms = int(now.timestamp() * 1000)

        # Recreate the exact W42 trades: 8 losing trades totaling -$9.00 and 1 winning trade +$1.4161
        trades = [
            {"trade_id": "TW1", "direction": "LONG", "is_open": False, "exit_timestamp_ms": now_ms - 10000, "pnl_net": 1.4161},
            {"trade_id": "TL1", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": now_ms - 9000, "pnl_net": -1.1544},
            {"trade_id": "TL2", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": now_ms - 8000, "pnl_net": -1.1200},
            {"trade_id": "TL3", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": now_ms - 7000, "pnl_net": -1.1300},
            {"trade_id": "TL4", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": now_ms - 6000, "pnl_net": -1.1100},
            {"trade_id": "TL5", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": now_ms - 5000, "pnl_net": -1.1200},
            {"trade_id": "TL6", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": now_ms - 4000, "pnl_net": -1.1100},
            {"trade_id": "TL7", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": now_ms - 3000, "pnl_net": -1.1300},
            {"trade_id": "TL8", "direction": "LONG", "is_open": False, "exit_timestamp_ms": now_ms - 2000, "pnl_net": -1.1264},
        ]
        recon = breaker_reconciliation(trades, now=now)
        weekly = recon["weekly"]

        assert weekly["closed_trades"] == 9
        # Sum of losing trades must be exactly $9.0008
        assert weekly["sum_of_losing_trades_used_by_breaker"] == 9.0008
        # Sum of winning trades ignored by breaker must be $1.4161
        assert weekly["sum_of_winning_trades_ignored_by_breaker"] == 1.4161
        # Net realized PnL must be -$7.5847
        assert weekly["net_realized_pnl"] == -7.5847


class TestDiagnosticApiEndpoints:
    """Verifies FastAPI endpoints for diagnostic report and kill switch."""

    @pytest.fixture
    def test_client(self):
        from fastapi.testclient import TestClient
        from src.api.app import app

        return TestClient(app)

    def test_get_diagnostic_report_all_cohorts(self, test_client):
        for cohort in ("auto", "experiment", "baseline", "all"):
            res = test_client.get(f"/api/model_b/diagnostic/report?cohort={cohort}")
            assert res.status_code == 200
            data = res.json()
            assert data["cohort"] in ("auto", "experiment", "baseline", "all")
            assert "per_direction" in data
            assert "LONG" in data["per_direction"]
            assert "SHORT" in data["per_direction"]
            assert "breaker_reconciliation" in data
            assert "risk_status" in data

    def test_kill_switch_api_toggle(self, test_client):
        # Activate kill switch
        res_on = test_client.post(
            "/api/model_b/diagnostic/kill_switch",
            json={"enabled": True},
            headers={"X-API-Key": "dev-key"},
        )
        assert res_on.status_code == 200
        assert res_on.json()["entry_kill_switch"] is True

        # Check status endpoint reflects it
        res_stat = test_client.get("/api/model_b/status")
        assert res_stat.status_code == 200
        assert res_stat.json()["risk_limits"]["entry_kill_switch"] is True

        # Deactivate kill switch
        res_off = test_client.post(
            "/api/model_b/diagnostic/kill_switch",
            json={"enabled": False},
            headers={"X-API-Key": "dev-key"},
        )
        assert res_off.status_code == 200
        assert res_off.json()["entry_kill_switch"] is False
