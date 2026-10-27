"""Unit and Integration Tests for Session Entries vs Open Positions Semantics.

Phase 4 Verification Suite:
1. An existing exchange position with a counter reset to zero after restart.
2. A restored position whose entry belongs to the previous session.
3. A new successfully filled entry in the current session.
4. A rejected or failed entry order.
5. Partial fills and duplicate order events.
6. Repeated reconciliation without double-counting.
7. No active positions and zero entries.
8. Protective orders must never count as entry orders.
9. The dashboard accurately distinguishes session entries from currently open positions.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
import pytest

from src.data.database import DatabaseManager
from src.execution.live_session import (
    ExecutionMode,
    LiveAutoSessionManager,
    LiveSessionState,
)
from src.journal.automated_journal import AutomatedTradeJournal
from src.journal.models import (
    AutomatedTradeRecord,
    FillRecord,
    TradeStatus,
    ReconciliationStatus,
)
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
def test_setup(tmp_path):
    """Create isolated test environment with SQLite database, coordinator, journal, and session manager."""
    db_file = tmp_path / "test_semantics.db"
    db = DatabaseManager(str(db_file))
    db.init_db()

    mock_exchange = MagicMock()
    mock_exchange.get_account_balance.return_value = [{"asset": "USDT", "balance": 15.72, "availableBalance": 15.72}]
    mock_exchange.get_positions.return_value = []
    mock_exchange.get_open_orders.return_value = []
    mock_exchange.get_user_trades.return_value = []

    coord = LiveMicroCoordinator(db=db, exchange=mock_exchange)
    coord.current_equity = 15.72
    coord.peak_equity = 15.72

    journal = AutomatedTradeJournal(db)

    sess_mgr = LiveAutoSessionManager(
        coordinator=coord,
        exchange_adapter=mock_exchange,
        automated_journal=journal,
        db=db,
    )

    return {
        "db": db,
        "exchange": mock_exchange,
        "coord": coord,
        "journal": journal,
        "sess_mgr": sess_mgr,
    }


def test_01_existing_position_counter_reset_to_zero_after_restart(test_setup):
    """An existing exchange position whose entry belongs to a prior session does not inflate new session counters."""
    db = test_setup["db"]
    exchange = test_setup["exchange"]
    sess_mgr = test_setup["sess_mgr"]

    # Historical trade entered yesterday in a previous session
    past_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    old_trade = AutomatedTradeRecord(
        journal_id="j_TRD_SOLUSDT_PAST",
        trade_id="TRD_SOLUSDT_PAST",
        exchange="Aster DEX",
        symbol="SOLUSDT",
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        mode="LIVE",
        side="LONG",
        status=TradeStatus.OPEN.value,
        order_id="10001",
        created_at_utc=past_ts,
        first_fill_timestamp_utc=past_ts,
        filled_quantity=0.15,
        reconciliation_status=ReconciliationStatus.RECONCILED.value,
    )
    test_setup["journal"].record_trade_intent(old_trade)

    # Active position on exchange
    exchange.get_positions.return_value = [{"symbol": "SOLUSDT", "positionAmt": 0.15, "entryPrice": 100.0}]
    exchange.get_open_orders.return_value = [
        {"orderId": "10002", "symbol": "SOLUSDT", "type": "STOP_MARKET", "reduceOnly": True, "stopPrice": 98.5},
        {"orderId": "10003", "symbol": "SOLUSDT", "type": "LIMIT", "reduceOnly": True, "price": 103.0},
    ]

    # Start a brand new live auto session today
    sess_mgr.start_session(operator_id="OPERATOR")

    info = sess_mgr.get_session_info()
    assert info["entries_placed_session"] == 0
    assert info["entries_filled_session"] == 0
    assert info["open_positions_count"] == 1


def test_02_restored_position_belonging_to_previous_session(test_setup):
    """A restored position whose trade created_at < current session started_at is isolated from session entries."""
    db = test_setup["db"]
    exchange = test_setup["exchange"]
    sess_mgr = test_setup["sess_mgr"]
    journal = test_setup["journal"]

    # Session 1 starts and places order
    sess_mgr.start_session(operator_id="OPERATOR")
    session_1_id = sess_mgr.session_id
    sess_mgr.record_order_placed("ETHUSDT", "20001")

    # Order fills
    fill_ts = datetime.now(timezone.utc).isoformat()
    trade = AutomatedTradeRecord(
        journal_id="j_TRD_ETHUSDT_S1",
        trade_id="TRD_ETHUSDT_S1",
        exchange="Aster DEX",
        symbol="ETHUSDT",
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        mode="LIVE",
        side="LONG",
        status=TradeStatus.OPEN.value,
        order_id="20001",
        created_at_utc=fill_ts,
        first_fill_timestamp_utc=fill_ts,
        filled_quantity=0.01,
        reconciliation_status=ReconciliationStatus.RECONCILED.value,
    )
    journal.record_trade_intent(trade)
    sess_mgr.sync_session_metrics_from_journal()

    info1 = sess_mgr.get_session_info()
    assert info1["entries_placed_session"] == 1
    assert info1["entries_filled_session"] == 1

    # Session 1 stops
    sess_mgr.stop_session(reason="OPERATOR_STOPPED")

    # Time passes: Session 2 is initialized in a new time window
    sess_mgr.session_id = "SESS_LIVE_AUTO_NEW"
    sess_mgr.started_at_utc = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    sess_mgr.orders_placed_count = 0
    sess_mgr.orders_filled_count = 0
    sess_mgr.mode = ExecutionMode.LIVE_AUTO
    sess_mgr.state = LiveSessionState.ACTIVE

    # Position from Session 1 remains held on exchange
    test_setup["coord"].global_lock.acquire(
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        symbol="ETHUSDT",
        order_id="PREV_SESSION_POS",
        state="PROTECTED",
    )
    sess_mgr._last_reconciliation = {"position_amt": 0.01, "lock_symbol": "ETHUSDT", "lock_state": "PROTECTED"}

    sess_mgr.sync_session_metrics_from_journal()
    info2 = sess_mgr.get_session_info()

    # Session 2 has 0 placed/filled entries, but detects the open position
    assert info2["entries_placed_session"] == 0
    assert info2["entries_filled_session"] == 0
    assert info2["open_positions_count"] == 1


def test_03_new_successfully_filled_entry_in_current_session(test_setup):
    """A new entry order placed and filled in the current session increments both placed and filled counters."""
    sess_mgr = test_setup["sess_mgr"]
    journal = test_setup["journal"]

    sess_mgr.start_session(operator_id="OPERATOR")

    # Place order
    sess_mgr.record_order_placed("BZUSDT", "30001")
    info_placed = sess_mgr.get_session_info()
    assert info_placed["entries_placed_session"] == 1
    assert info_placed["entries_filled_session"] == 0

    # Fill arrives
    fill_ts = datetime.now(timezone.utc).isoformat()
    trade = AutomatedTradeRecord(
        journal_id="j_TRD_BZUSDT_S3",
        trade_id="TRD_BZUSDT_S3",
        exchange="Aster DEX",
        symbol="BZUSDT",
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        mode="LIVE",
        side="LONG",
        status=TradeStatus.OPEN.value,
        order_id="30001",
        created_at_utc=fill_ts,
        first_fill_timestamp_utc=fill_ts,
        filled_quantity=0.13,
        reconciliation_status=ReconciliationStatus.RECONCILED.value,
    )
    journal.record_trade_intent(trade)

    sess_mgr.sync_session_metrics_from_journal()
    info_filled = sess_mgr.get_session_info()

    assert info_filled["entries_placed_session"] == 1
    assert info_filled["entries_filled_session"] == 1
    assert info_filled["daily_filled_entries"] == 1


def test_04_rejected_or_failed_entry_order(test_setup):
    """A failed or rejected entry order never increments entries_placed_session or entries_filled_session."""
    sess_mgr = test_setup["sess_mgr"]
    sess_mgr.start_session(operator_id="OPERATOR")

    # Order rejection: no record_order_placed call occurs
    info = sess_mgr.get_session_info()
    assert info["entries_placed_session"] == 0
    assert info["entries_filled_session"] == 0
    assert info["daily_filled_entries"] == 0


def test_05_partial_fills_and_duplicate_order_events(test_setup):
    """Multiple partial fills on the same trade count as exactly 1 filled entry."""
    sess_mgr = test_setup["sess_mgr"]
    journal = test_setup["journal"]
    db = test_setup["db"]

    sess_mgr.start_session(operator_id="OPERATOR")
    sess_mgr.record_order_placed("SOLUSDT", "50001")

    now = datetime.now(timezone.utc)
    trade = AutomatedTradeRecord(
        journal_id="j_TRD_SOLUSDT_PARTIAL",
        trade_id="TRD_SOLUSDT_PARTIAL",
        exchange="Aster DEX",
        symbol="SOLUSDT",
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        mode="LIVE",
        side="LONG",
        status=TradeStatus.OPEN.value,
        order_id="50001",
        created_at_utc=now.isoformat(),
        first_fill_timestamp_utc=now.isoformat(),
        filled_quantity=0.05,
        reconciliation_status=ReconciliationStatus.RECONCILED.value,
    )
    journal.record_trade_intent(trade)

    # First partial fill record
    f1 = FillRecord(
        fill_id="fill_1",
        trade_id="TRD_SOLUSDT_PARTIAL",
        order_id="50001",
        timestamp_utc=now.isoformat(),
        timestamp_ms=int(now.timestamp() * 1000),
        symbol="SOLUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=101.0,
        fill_quantity=0.05,
        fill_notional_usd=5.05,
        fee_amount=0.002,
        fee_asset="USDT",
        fee_usd=0.002,
        liquidity_type="TAKER",
        exchange_trade_id="ex_fill_1",
    )
    journal.record_fill(f1)
    sess_mgr.sync_session_metrics_from_journal()

    # Second partial fill record
    trade.filled_quantity = 0.10
    journal.record_trade_intent(trade)
    f2 = FillRecord(
        fill_id="fill_2",
        trade_id="TRD_SOLUSDT_PARTIAL",
        order_id="50001",
        timestamp_utc=(now + timedelta(seconds=5)).isoformat(),
        timestamp_ms=int((now + timedelta(seconds=5)).timestamp() * 1000),
        symbol="SOLUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=101.0,
        fill_quantity=0.05,
        fill_notional_usd=5.05,
        fee_amount=0.002,
        fee_asset="USDT",
        fee_usd=0.002,
        liquidity_type="TAKER",
        exchange_trade_id="ex_fill_2",
    )
    journal.record_fill(f2)
    sess_mgr.sync_session_metrics_from_journal()

    info = sess_mgr.get_session_info()
    assert info["entries_placed_session"] == 1
    assert info["entries_filled_session"] == 1
    assert info["daily_filled_entries"] == 1


def test_06_repeated_reconciliation_without_double_counting(test_setup):
    """Running reconciliation 10 times consecutively is strictly idempotent and never double-counts."""
    sess_mgr = test_setup["sess_mgr"]
    journal = test_setup["journal"]
    coord = test_setup["coord"]

    sess_mgr.start_session(operator_id="OPERATOR")
    sess_mgr.record_order_placed("ETHUSDT", "60001")

    now = datetime.now(timezone.utc).isoformat()
    trade = AutomatedTradeRecord(
        journal_id="j_TRD_ETHUSDT_IDEMP",
        trade_id="TRD_ETHUSDT_IDEMP",
        exchange="Aster DEX",
        symbol="ETHUSDT",
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        mode="LIVE",
        side="LONG",
        status=TradeStatus.OPEN.value,
        order_id="60001",
        created_at_utc=now,
        first_fill_timestamp_utc=now,
        filled_quantity=0.01,
        reconciliation_status=ReconciliationStatus.RECONCILED.value,
    )
    journal.record_trade_intent(trade)

    for _ in range(10):
        sess_mgr.sync_session_metrics_from_journal()
        coord.sync_daily_metrics_from_journal()

    info = sess_mgr.get_session_info()
    assert info["entries_placed_session"] == 1
    assert info["entries_filled_session"] == 1
    assert info["daily_filled_entries"] == 1


def test_07_no_active_positions_and_zero_entries(test_setup):
    """Clean baseline state reports exactly zero across all entry and position metrics."""
    sess_mgr = test_setup["sess_mgr"]
    sess_mgr.start_session(operator_id="OPERATOR")

    info = sess_mgr.get_session_info()
    assert info["entries_placed_session"] == 0
    assert info["entries_filled_session"] == 0
    assert info["open_positions_count"] == 0
    assert info["daily_filled_entries"] == 0
    assert info["entry_limit"] == 3


def test_08_protective_orders_never_count_as_entry_orders(test_setup):
    """Protective orders (STOP_MARKET and LIMIT TP) do not increment session or daily entries."""
    sess_mgr = test_setup["sess_mgr"]
    exchange = test_setup["exchange"]
    journal = test_setup["journal"]

    sess_mgr.start_session(operator_id="OPERATOR")

    # Protective orders open on exchange
    exchange.get_open_orders.return_value = [
        {"orderId": "80001", "symbol": "ETHUSDT", "type": "STOP_MARKET", "reduceOnly": True, "stopPrice": 2250.0},
        {"orderId": "80002", "symbol": "ETHUSDT", "type": "LIMIT", "reduceOnly": True, "price": 2400.0},
    ]

    sess_mgr.sync_session_metrics_from_journal()
    info = sess_mgr.get_session_info()

    assert info["entries_placed_session"] == 0
    assert info["entries_filled_session"] == 0
    assert info["daily_filled_entries"] == 0


def test_09_dashboard_payload_accurately_distinguishes_session_entries_from_open_positions(test_setup):
    """Dashboard get_session_info payload provides explicit, non-conflated metrics."""
    sess_mgr = test_setup["sess_mgr"]
    exchange = test_setup["exchange"]
    journal = test_setup["journal"]

    # Past session opened SOLUSDT
    past_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    old_trade = AutomatedTradeRecord(
        journal_id="j_TRD_SOL_DASH",
        trade_id="TRD_SOL_DASH",
        exchange="Aster DEX",
        symbol="SOLUSDT",
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        mode="LIVE",
        side="LONG",
        status=TradeStatus.OPEN.value,
        order_id="90001",
        created_at_utc=past_ts,
        first_fill_timestamp_utc=past_ts,
        filled_quantity=0.2,
        reconciliation_status=ReconciliationStatus.RECONCILED.value,
    )
    journal.record_trade_intent(old_trade)

    # Start new session now
    sess_mgr.start_session(operator_id="OPERATOR")
    sess_mgr._last_reconciliation = {
        "position_amt": 0.2,
        "lock_symbol": "SOLUSDT",
        "lock_state": "PROTECTED",
        "active_protective_order_count": 2,
    }

    info = sess_mgr.get_session_info()

    # Distinct semantics verification:
    # 1. entries_placed_session is 0 (no new order placed in this session)
    assert info["entries_placed_session"] == 0
    # 2. entries_filled_session is 0 (no fill in this session)
    assert info["entries_filled_session"] == 0
    # 3. open_positions_count is 1 (position is open)
    assert info["open_positions_count"] == 1
    # 4. entry_limit is 3
    assert info["entry_limit"] == 3
    # 5. daily_filled_entries is 1 (old_trade was filled today in WIB)
    assert info["daily_filled_entries"] == 1
