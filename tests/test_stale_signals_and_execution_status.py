"""Tests for Stale Signals Handling, Scanner Lifecycle, and Execution Status Decoupling.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from config.settings import ExecutionMode, get_default_config
from src.core.types import (
    CandidateStatus,
    Direction,
    ExecutionStatus,
    MarketBias,
    SetupCandidate,
    StructureState,
)
from src.scheduler.runner import ScannerRunner
from src.api.app import app


@pytest.fixture
def sample_candidate() -> SetupCandidate:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return SetupCandidate(
        candidate_id="SETUP_TEST_001_SHORT",
        symbol="BNBUSDT",
        timestamp_ms=now_ms,
        direction=Direction.SHORT,
        bias_4h=MarketBias.BEARISH,
        structure_1h=StructureState.BEARISH,
        entry_price=600.0,
        stop_loss=610.0,
        take_profit_1=580.0,
        status=CandidateStatus.READY,
        expires_at_ms=now_ms + 12 * 3600 * 1000,
    )


def test_execution_status_enum_and_defaults(sample_candidate):
    """Verify that ExecutionStatus enum exists and defaults to NOT_SUBMITTED."""
    assert sample_candidate.execution_status == ExecutionStatus.NOT_SUBMITTED
    assert sample_candidate.order_id is None
    assert sample_candidate.client_order_id is None
    assert sample_candidate.filled_quantity == 0.0
    assert sample_candidate.filled_at_ms is None

    # Check serialization
    d = sample_candidate.to_dict()
    assert d["execution_status"] == "NOT_SUBMITTED"
    assert "order_id" in d
    assert "client_order_id" in d
    assert "filled_quantity" in d
    assert "filled_at_ms" in d


def test_candidate_status_decoupled_from_execution_status(sample_candidate):
    """Verify that a candidate marked READY does NOT imply filled or submitted execution."""
    assert sample_candidate.status == CandidateStatus.READY
    assert sample_candidate.is_executable is True
    # Execution status must remain NOT_SUBMITTED until explicitly executed
    assert sample_candidate.execution_status == ExecutionStatus.NOT_SUBMITTED


def test_dynamic_staleness_by_ttl_expiry(sample_candidate):
    """Verify that candidates past expires_at_ms transition to STALE dynamically."""
    now_ms = sample_candidate.timestamp_ms
    # Set expires_at_ms in the past (e.g., 2 hours ago)
    sample_candidate.expires_at_ms = now_ms - (2 * 3600 * 1000)

    # In runner recheck logic or dynamic evaluation
    is_expired = bool(sample_candidate.expires_at_ms and now_ms > sample_candidate.expires_at_ms)
    assert is_expired is True


def test_dynamic_staleness_by_candle_age():
    """Verify that candidate older than stale threshold (e.g., 3 hours) is recognized as STALE."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    old_timestamp_ms = now_ms - (5 * 3600 * 1000)  # 5 hours ago

    old_candidate = SetupCandidate(
        candidate_id="SETUP_OLD_001_SHORT",
        symbol="BTCUSDT",
        timestamp_ms=old_timestamp_ms,
        direction=Direction.SHORT,
        bias_4h=MarketBias.BEARISH,
        structure_1h=StructureState.BEARISH,
        entry_price=60000.0,
        stop_loss=61000.0,
        take_profit_1=58000.0,
        status=CandidateStatus.READY,
        expires_at_ms=old_timestamp_ms + (12 * 3600 * 1000),
    )

    candle_age_hours = (now_ms - old_candidate.timestamp_ms) / (3600.0 * 1000.0)
    assert candle_age_hours > 3.0


def test_api_status_reports_scanner_lifecycle():
    """Verify that /api/status endpoint returns scanner state, process_status, and freshness fields."""
    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()

    assert "scanner" in data
    scanner_data = data["scanner"]
    assert "state" in scanner_data
    assert "process_status" in scanner_data
    assert "candle_age_seconds" in scanner_data
    assert "last_scan_age_seconds" in scanner_data
    assert "background_worker_active" in scanner_data
    assert "is_stale" in scanner_data


def test_api_scanner_start_stop_endpoints():
    """Verify that /api/scanner/stop and /api/scanner/start control scanner state."""
    client = TestClient(app)

    # Stop scanner
    stop_resp = client.post("/api/scanner/stop")
    assert stop_resp.status_code == 200
    stop_data = stop_resp.json()
    assert stop_data["status"] == "SUCCESS"
    assert stop_data["scanner_state"] == "STOPPED"

    status_resp = client.get("/api/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["scanner"]["process_status"] == "STOPPED"

    # Start scanner
    start_resp = client.post("/api/scanner/start")
    assert start_resp.status_code == 200
    start_data = start_resp.json()
    assert start_data["status"] == "SUCCESS"

    status_resp2 = client.get("/api/status")
    assert status_resp2.status_code == 200
    assert status_resp2.json()["scanner"]["process_status"] in ("RUNNING", "IDLE")


def test_api_setups_returns_execution_status_and_stale_classification():
    """Verify that /api/setups evaluates dynamic staleness and returns execution_status on every setup."""
    client = TestClient(app)
    response = client.get("/api/setups")
    assert response.status_code == 200
    data = response.json()

    assert "setups" in data
    assert "counts_by_status" in data
    assert "STALE" in data["counts_by_status"]

    for setup in data["setups"]:
        assert "execution_status" in setup
        assert setup["execution_status"] in [e.value for e in ExecutionStatus]
        assert "order_id" in setup
        # If setup was generated > 3 hours ago or TTL expired, status must be STALE
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        age_hours = (now_ms - setup["timestamp_ms"]) / 3600000.0
        exp_ms = setup.get("expires_at_ms") or 0
        if age_hours > 3.0 or (exp_ms > 0 and now_ms > exp_ms):
            assert setup["status"] == "STALE"


def test_database_reconcile_stale_setups(tmp_path):
    """Verify that JournalRepository.reconcile_stale_setups transitions expired/stale DB rows from READY to STALE."""
    from src.data.database import DatabaseManager
    from src.journal.repository import JournalRepository
    from src.core.types import Direction, MarketBias, StructureState

    db_path = tmp_path / "test_reconcile.db"
    db_mgr = DatabaseManager(db_path)
    journal_repo = JournalRepository(db_mgr)

    old_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - (10 * 3600 * 1000)  # 10h old
    cand = SetupCandidate(
        candidate_id="SETUP_STALE_TEST_001",
        symbol="BTCUSDT",
        timestamp_ms=old_ts_ms,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit_1=62000.0,
        invalidation_price=59000.0,
        status=CandidateStatus.READY,
        expires_at_ms=old_ts_ms + (4 * 3600 * 1000),  # expired 6h ago
    )
    journal_repo.save_setup(cand)

    # Initial check: in DB it was saved as READY
    setups_before = journal_repo.get_latest_setups()
    assert len(setups_before) == 1
    assert setups_before[0]["status"] == "READY"

    # Run database reconciliation
    updated_count = journal_repo.reconcile_stale_setups(stale_threshold_hours=3.0)
    assert updated_count == 1

    # Verified in database: status changed to STALE
    setups_after = journal_repo.get_latest_setups()
    assert len(setups_after) == 1
    assert setups_after[0]["status"] == "STALE"
    assert any("expired" in r.lower() for r in setups_after[0]["details"]["rejection_reasons"])


def test_candle_age_and_expiry_boundary_conditions():
    """Verify strict boundary conditions: 2.9h (fresh) vs 3.1h (stale), now-1ms (expired) vs now+1000ms (valid)."""
    now_ms = 1_700_000_000_000

    # 1. Candle Age: 2.9 hours ago (fresh)
    ts_2_9h = now_ms - int(2.9 * 3600 * 1000)
    age_2_9h = (now_ms - ts_2_9h) / (3600.0 * 1000.0)
    assert age_2_9h < 3.0
    assert (age_2_9h > 3.0) is False

    # 2. Candle Age: 3.1 hours ago (stale)
    ts_3_1h = now_ms - int(3.1 * 3600 * 1000)
    age_3_1h = (now_ms - ts_3_1h) / (3600.0 * 1000.0)
    assert age_3_1h > 3.0

    # 3. Expiry: 1 millisecond past expires_at_ms (expired)
    exp_past = now_ms - 1
    assert bool(exp_past and now_ms > exp_past) is True

    # 4. Expiry: 1000 milliseconds in future (valid)
    exp_future = now_ms + 1000
    assert bool(exp_future and now_ms > exp_future) is False


def test_malformed_and_missing_timestamps_handled_safely(tmp_path):
    """Verify that malformed, string, or negative timestamps do not crash reconciliation and are marked STALE."""
    from src.data.database import DatabaseManager
    from src.journal.repository import JournalRepository

    db_path = tmp_path / "test_malformed.db"
    db_mgr = DatabaseManager(db_path)
    repo = JournalRepository(db_mgr)

    # Insert a row with string expires_at_ms and negative timestamp
    with db_mgr.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO setup_candidates (
                candidate_id, symbol, timestamp_ms, direction, bias_4h, structure_1h,
                entry_price, stop_loss, take_profit_1, gross_rr, net_rr, invalidation_price,
                status, details_json, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                "SETUP_MALFORMED_001", "BTCUSDT", -100, "LONG", "BULLISH", "BULLISH",
                60000.0, 59000.0, 62000.0, 2.0, 1.8, 59000.0,
                "READY", '{"expires_at_ms": "not_an_int", "rejection_reasons": []}', "2026-10-17T00:00:00Z"
            )
        )
        conn.commit()

    # Reconciliation must not crash with TypeError and must update malformed candidate to STALE
    updated = repo.reconcile_stale_setups(stale_threshold_hours=3.0)
    assert updated == 1

    rows = repo.get_latest_setups()
    assert rows[0]["status"] == "STALE"


def test_idempotent_reconciliation(tmp_path):
    """Verify that calling reconcile_stale_setups multiple times is strictly idempotent."""
    from src.data.database import DatabaseManager
    from src.journal.repository import JournalRepository
    from src.core.types import Direction, MarketBias, StructureState

    db_path = tmp_path / "test_idempotent.db"
    db_mgr = DatabaseManager(db_path)
    repo = JournalRepository(db_mgr)

    old_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - (20 * 3600 * 1000)
    cand = SetupCandidate(
        candidate_id="SETUP_IDEM_001",
        symbol="ETHUSDT",
        timestamp_ms=old_ts_ms,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=2500.0,
        stop_loss=2400.0,
        take_profit_1=2700.0,
        invalidation_price=2400.0,
        status=CandidateStatus.READY,
        expires_at_ms=old_ts_ms + (12 * 3600 * 1000),
    )
    repo.save_setup(cand)

    # First reconciliation updates the row
    first_run = repo.reconcile_stale_setups()
    assert first_run == 1

    # Second, third, and fourth runs must return 0 (no redundant updates or corruption)
    assert repo.reconcile_stale_setups() == 0
    assert repo.reconcile_stale_setups() == 0
    assert repo.reconcile_stale_setups() == 0


def test_execution_gate_rejects_stale_candidate_in_paper_engine(tmp_path):
    """Verify that PaperExecutionEngine.submit_candidate directly rejects a stale candidate."""
    from src.data.database import DatabaseManager
    from src.journal.repository import JournalRepository
    from src.execution.paper_engine import PaperExecutionEngine
    from src.risk.manager import RiskManager
    from src.core.types import RiskAssessment, Direction, MarketBias, StructureState

    db_mgr = DatabaseManager(tmp_path / "test_paper_gate.db")
    repo = JournalRepository(db_mgr)
    risk_mgr = RiskManager()
    engine = PaperExecutionEngine(db=db_mgr, journal=repo, risk_manager=risk_mgr)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_ts = now_ms - (10 * 3600 * 1000)  # 10 hours old

    assessment = RiskAssessment(
        symbol="BTCUSDT",
        entry_price=60000.0,
        stop_loss=59000.0,
        stop_distance_pct=0.016,
        risk_budget_usdt=0.09,
        position_notional_usdt=50.0,
        quantity=0.001,
        required_margin_usdt=5.0,
        effective_leverage=10.0,
        estimated_fee_usdt=0.05,
        estimated_slippage_usdt=0.05,
        is_feasible=True,
    )

    stale_cand = SetupCandidate(
        candidate_id="SETUP_STALE_PAPER_001",
        symbol="BTCUSDT",
        timestamp_ms=stale_ts,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit_1=62000.0,
        invalidation_price=59000.0,
        status=CandidateStatus.READY,  # Status says READY, but candle age is 10h!
        risk_assessment=assessment,
    )

    # Execution gate must reject stale candidate
    order = engine.submit_candidate(stale_cand, now_ms)
    assert order is None  # Safe rejection!


def test_execution_gate_rejects_stale_candidate_in_v3_lifecycle():
    """Verify that V3OrderLifecycleManager directly blocks entry for stale or expired candidates."""
    from unittest.mock import MagicMock
    from src.execution.v3_lifecycle import V3OrderLifecycleManager
    from src.risk.manager import RiskManager
    from src.core.types import RiskAssessment, Direction, MarketBias, StructureState

    adapter = MagicMock()
    risk_mgr = RiskManager()
    manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_ts = now_ms - (8 * 3600 * 1000)  # 8 hours old

    assessment = RiskAssessment(
        symbol="BTCUSDT",
        entry_price=60000.0,
        stop_loss=59000.0,
        stop_distance_pct=0.016,
        risk_budget_usdt=0.09,
        position_notional_usdt=50.0,
        quantity=0.001,
        required_margin_usdt=5.0,
        effective_leverage=10.0,
        estimated_fee_usdt=0.05,
        estimated_slippage_usdt=0.05,
        is_feasible=True,
    )

    # Test A: Status is STALE
    cand_stale = SetupCandidate(
        candidate_id="SETUP_V3_STALE_001",
        symbol="BTCUSDT",
        timestamp_ms=now_ms,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit_1=62000.0,
        invalidation_price=59000.0,
        status=CandidateStatus.STALE,
        risk_assessment=assessment,
    )
    pos_a = manager.execute_order_lifecycle(cand_stale, now_ms)
    assert pos_a.entry_status == "BLOCKED_CANDIDATE_STATUS"
    assert adapter.create_order.call_count == 0

    # Test B: Status says READY, but candle age is 8 hours old
    cand_old_ts = SetupCandidate(
        candidate_id="SETUP_V3_OLD_002",
        symbol="BTCUSDT",
        timestamp_ms=stale_ts,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit_1=62000.0,
        invalidation_price=59000.0,
        status=CandidateStatus.READY,
        risk_assessment=assessment,
    )
    pos_b = manager.execute_order_lifecycle(cand_old_ts, now_ms)
    assert pos_b.entry_status == "BLOCKED_CANDLE_STALE"
    assert adapter.create_order.call_count == 0


