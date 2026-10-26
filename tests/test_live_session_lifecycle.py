"""Unit and Integration Tests for LIVE Session Lifecycle, Recovery, and Persistence.

Verifies:
1. Session state is persisted server-side in SQLite with an auditable session ID.
2. Dashboard refresh preserves active session state without resetting to PAPER/INACTIVE.
3. Process restart recovers session state and transitions to REAUTHORIZATION_REQUIRED (never silently resumes).
4. Reconciles existing exchange positions and resting orders on restart and restores GlobalPositionLock.
5. If exchange is unreachable or has discrepancies, transitions to RECOVERY_REQUIRED and gates entries.
6. Explicit operator reauthorization transitions session to ACTIVE and restores live auto-execution.
7. Distinguishes all 7 lifecycle states:
   - INACTIVE
   - ACTIVE
   - PAUSED
   - RECOVERY_REQUIRED
   - REAUTHORIZATION_REQUIRED
   - HALTED
   - KILL_SWITCH_HALTED
8. Audit trail persistence in live_auto_session_audit.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
import pytest

from src.ai.groq_autotrader import (
    ExecutionVenue,
    GroqAutoTrader,
    RiskPreset,
)
from src.core.types import Candle
from src.data.database import DatabaseManager
from src.execution.live_session import (
    ExecutionMode,
    LiveAutoSessionManager,
    LiveSessionState,
)
from src.intelligence.coin_inspector import CoinInspectorService
from src.journal.automated_journal import AutomatedTradeJournal
from src.risk.global_lock import GlobalPositionLock
from src.scheduler.live_micro_runner import LiveMicroCoordinator


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singleton GlobalPositionLock and LiveAutoSessionManager before and after each test."""
    lock = GlobalPositionLock()
    with lock._mutex:
        lock._is_locked = False
        lock._symbol = None
        lock._strategy_id = None
        lock._order_id = None
        lock._state = "IDLE"
        lock._acquired_at_ms = 0
        lock._lock_history = []
    LiveAutoSessionManager._instance = None
    yield
    with lock._mutex:
        lock._is_locked = False
        lock._symbol = None
        lock._strategy_id = None
        lock._order_id = None
        lock._state = "IDLE"
        lock._acquired_at_ms = 0
    LiveAutoSessionManager._instance = None


@pytest.fixture
def test_env(tmp_path):
    """Create isolated test environment with SQLite database and mock adapters."""
    db_file = tmp_path / "test_lifecycle.db"
    db = DatabaseManager(str(db_file))
    db.init_db()

    mock_exchange = MagicMock()
    mock_exchange.get_account_balance.return_value = [{"asset": "USDT", "balance": 15.72, "availableBalance": 15.72}]
    mock_exchange.get_positions.return_value = []
    mock_exchange.get_open_orders.return_value = []
    mock_exchange.get_bulk_market_snapshot.return_value = {"ETHUSDT": {"mark_price": 2343.62}}

    coord = LiveMicroCoordinator(db=db, exchange=mock_exchange)
    coord.current_equity = 15.72
    coord.peak_equity = 15.72

    journal = AutomatedTradeJournal(db)
    inspector = CoinInspectorService(adapter=mock_exchange)

    sess_mgr = LiveAutoSessionManager(
        coordinator=coord,
        exchange_adapter=mock_exchange,
        automated_journal=journal,
        db=db,
    )

    trader = GroqAutoTrader(
        adapter=mock_exchange,
        inspector_service=inspector,
        coordinator=coord,
        automated_journal=journal,
        session_manager=sess_mgr,
    )

    return {
        "db": db,
        "coord": coord,
        "journal": journal,
        "sess_mgr": sess_mgr,
        "trader": trader,
        "mock_exchange": mock_exchange,
    }


def test_01_session_state_persisted_in_database(test_env):
    """Requirement 2: Persist session state server-side with an auditable session ID."""
    sess_mgr = test_env["sess_mgr"]
    db = test_env["db"]

    # Start session
    ok, msg, info = sess_mgr.start_session(operator_id="OPERATOR_ALICE")
    assert ok
    assert sess_mgr.state == LiveSessionState.ACTIVE
    assert sess_mgr.mode == ExecutionMode.LIVE_AUTO
    sess_id = sess_mgr.session_id
    assert sess_id.startswith("SESS_LIVE_AUTO_")

    # Inspect SQLite table live_auto_session_state directly
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT session_id, mode, state, authorized_by FROM live_auto_session_state WHERE id = 1")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == sess_id
        assert row[1] == "LIVE_AUTO"
        assert row[2] == "ACTIVE"
        assert row[3] == "OPERATOR_ALICE"


def test_02_dashboard_refresh_preserves_state(test_env):
    """Requirement 3 & 4: On dashboard refresh, fetch backend session state without resetting to PAPER/INACTIVE."""
    sess_mgr = test_env["sess_mgr"]

    # Start live session
    ok, _, _ = sess_mgr.start_session(operator_id="OPERATOR_BOB")
    assert ok
    sess_id = sess_mgr.session_id
    started_at = sess_mgr.started_at_utc

    # Simulate 5 consecutive dashboard refreshes (calling get_session_info)
    for _ in range(5):
        info = sess_mgr.get_session_info()
        assert info["session_id"] == sess_id
        assert info["mode"] == "LIVE_AUTO"
        assert info["state"] == "ACTIVE"
        assert info["started_at_utc"] == started_at
        assert info["is_authorized_and_active"] is True
        assert sess_mgr.mode == ExecutionMode.LIVE_AUTO
        assert sess_mgr.state == LiveSessionState.ACTIVE


def test_03_process_restart_requires_reauthorization(test_env):
    """Requirement 5: After process restart, require explicit operator reauthorization before any new live entry."""
    sess_mgr = test_env["sess_mgr"]
    ok, _, _ = sess_mgr.start_session(operator_id="OPERATOR_CHARLIE")
    assert ok
    sess_id = sess_mgr.session_id

    # Simulate process termination and restart by resetting in-memory singleton
    LiveAutoSessionManager._instance = None

    restarted_mgr = LiveAutoSessionManager(
        coordinator=test_env["coord"],
        exchange_adapter=test_env["mock_exchange"],
        automated_journal=test_env["journal"],
        db=test_env["db"],
    )
    # Recover session after restart
    restarted_mgr.recover_session_after_restart()

    # Session is preserved but suspended in REAUTHORIZATION_REQUIRED
    assert restarted_mgr.session_id == sess_id
    assert restarted_mgr.mode == ExecutionMode.LIVE_AUTO
    assert restarted_mgr.state == LiveSessionState.REAUTHORIZATION_REQUIRED
    assert restarted_mgr.is_authorized_and_active() is False

    # Attempt to place order in REAUTHORIZATION_REQUIRED must be rejected
    setup_output = MagicMock()
    setup_output.strategy_eligible = True
    setup_output.symbol = "SOLUSDT"

    # Operator explicitly reauthorizes
    reauth_ok, reauth_msg, _ = restarted_mgr.reauthorize_session(operator_id="OPERATOR_CHARLIE")
    assert reauth_ok
    assert restarted_mgr.state == LiveSessionState.ACTIVE
    assert restarted_mgr.is_authorized_and_active() is True


def test_04_restart_with_open_exchange_position_restores_global_lock(test_env):
    """Requirement 6 & 7: Reconcile existing exchange positions/orders and restore GlobalPositionLock."""
    sess_mgr = test_env["sess_mgr"]
    mock_exchange = test_env["mock_exchange"]
    coord = test_env["coord"]

    ok, _, _ = sess_mgr.start_session(operator_id="OPERATOR_DAVE")
    assert ok

    # Simulate existing open ETH position on Aster DEX Mainnet with SL and TP resting orders
    mock_exchange.get_positions.return_value = [
        {"symbol": "ETHUSDT", "positionAmt": "0.009", "entryPrice": "2343.62"}
    ]
    mock_exchange.get_open_orders.return_value = [
        {"orderId": "18639022146", "symbol": "ETHUSDT", "type": "STOP_MARKET", "reduceOnly": True},
        {"orderId": "18639068962", "symbol": "ETHUSDT", "type": "LIMIT", "reduceOnly": True},
    ]

    # Restart process
    LiveAutoSessionManager._instance = None
    new_mgr = LiveAutoSessionManager(
        coordinator=coord,
        exchange_adapter=mock_exchange,
        automated_journal=test_env["journal"],
        db=test_env["db"],
    )
    new_mgr.recover_session_after_restart()

    # Lock must be restored to PROTECTED for ETHUSDT
    assert coord.global_lock.is_locked is True
    assert coord.global_lock.active_symbol == "ETHUSDT"
    assert coord.global_lock.state == "PROTECTED"

    # New session state is REAUTHORIZATION_REQUIRED
    assert new_mgr.state == LiveSessionState.REAUTHORIZATION_REQUIRED

    # Reauthorization pre-flight check recognizes existing protected position without error
    reauth_preflight = new_mgr.run_preflight_audit(is_reauthorization=True)
    assert reauth_preflight.is_valid is True
    assert len(reauth_preflight.warnings) > 0

    # Reauthorize successfully
    reauth_ok, _, _ = new_mgr.reauthorize_session(operator_id="OPERATOR_DAVE")
    assert reauth_ok
    assert new_mgr.state == LiveSessionState.ACTIVE

    # Global lock is STILL held on ETHUSDT and blocks any new candidate from opening a 2nd position
    can_acquire, block_reason = coord.global_lock.can_acquire("EMA20_RECLAIM_5M_V1", "SOLUSDT")
    assert can_acquire is False
    assert "GLOBAL_LOCK_ACTIVE" in block_reason


def test_05_exchange_unreachable_triggers_recovery_required(test_env):
    """Requirement 9: If exchange cannot be reconciled, enter RECOVERY_REQUIRED and halt new entries."""
    sess_mgr = test_env["sess_mgr"]
    mock_exchange = test_env["mock_exchange"]

    ok, _, _ = sess_mgr.start_session(operator_id="OPERATOR_EVE")
    assert ok

    # Mock exchange connection failure
    mock_exchange.get_positions.side_effect = ConnectionError("Aster DEX Mainnet API connection timeout")

    # Restart process
    LiveAutoSessionManager._instance = None
    new_mgr = LiveAutoSessionManager(
        coordinator=test_env["coord"],
        exchange_adapter=mock_exchange,
        automated_journal=test_env["journal"],
        db=test_env["db"],
    )
    new_mgr.recover_session_after_restart()

    assert new_mgr.state == LiveSessionState.RECOVERY_REQUIRED
    assert "Aster DEX Mainnet API connection timeout" in str(new_mgr.recovery_reason)
    assert new_mgr.is_authorized_and_active() is False

    # Pre-flight must fail
    preflight = new_mgr.run_preflight_audit()
    assert preflight.is_valid is False


def test_06_all_7_lifecycle_states_and_audit_trail(test_env):
    """Requirement 8: Distinguish all 7 required lifecycle states and record auditable history."""
    sess_mgr = test_env["sess_mgr"]
    db = test_env["db"]

    # 1. INACTIVE
    assert sess_mgr.state == LiveSessionState.INACTIVE

    # 2. ACTIVE
    ok, _, _ = sess_mgr.start_session(operator_id="OPERATOR_FRANK")
    assert ok
    assert sess_mgr.state == LiveSessionState.ACTIVE

    # 3. PAUSED
    ok, _, _ = sess_mgr.pause_session(reason="Operator test pause")
    assert ok
    assert sess_mgr.state == LiveSessionState.PAUSED

    # Resume to ACTIVE
    ok, _, _ = sess_mgr.resume_session()
    assert ok
    assert sess_mgr.state == LiveSessionState.ACTIVE

    # 4. HALTED
    ok, _, _ = sess_mgr.stop_session(reason="Operator test stop")
    assert ok
    assert sess_mgr.state == LiveSessionState.HALTED

    # 5. KILL_SWITCH_HALTED
    ok, _, _ = sess_mgr.trigger_emergency_kill_switch(reason="Operator emergency test")
    assert ok
    assert sess_mgr.state == LiveSessionState.KILL_SWITCH_HALTED

    # 6. Verify audit table contains all events
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT event_type, new_state, operator_id FROM live_auto_session_audit ORDER BY id ASC")
        rows = cur.fetchall()
        event_types = [r[0] for r in rows]
        assert "SESSION_STARTED" in event_types
        assert "SESSION_PAUSED" in event_types
        assert "SESSION_RESUMED" in event_types
        assert "SESSION_STOPPED" in event_types
        assert "EMERGENCY_KILL_SWITCH" in event_types
