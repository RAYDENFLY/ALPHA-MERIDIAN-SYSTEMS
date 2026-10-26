import threading

from src.execution.live_session import ExecutionMode, LiveAutoSessionManager, LiveSessionState


def _manager():
    m = object.__new__(LiveAutoSessionManager)
    m._reconciliation_thread = None
    m._reconciliation_worker_enabled = True
    m._reconciliation_worker_started_utc = None
    m._reconciliation_last_cycle_started_utc = None
    m._reconciliation_last_cycle_completed_utc = None
    m._reconciliation_cycle_count = 0
    m._reconciliation_last_cycle_status = "IDLE"
    m._reconciliation_last_result = {}
    m._reconciliation_interval_sec = 0.01
    m._reconciliation_stop_event = threading.Event()
    m._reconciliation_started_event = threading.Event()
    m._scanner_stop_event = threading.Event()
    m._scanner_running = False
    m._mutex = threading.RLock()
    m.mode = ExecutionMode.LIVE_AUTO
    m.state = LiveSessionState.ACTIVE
    m.is_authorized_and_active = lambda: True
    return m


def test_worker_status_is_explicit_and_starts_once(monkeypatch):
    m = _manager()
    created = []

    class FakeThread:
        def __init__(self, **kwargs):
            created.append(kwargs["name"])
        def start(self):
            pass
        def is_alive(self):
            return True

    monkeypatch.setattr("src.execution.live_session.threading.Thread", FakeThread)
    m.start_scanner()
    first = m.get_reconciliation_worker_status()
    m.start_scanner()
    assert created.count("LiveProtectionReconciliationThread") == 1
    assert first["worker_enabled"] is True
    assert first["worker_start_requested"] is True
    assert first["worker_started"] is False


def test_worker_does_not_start_without_authorization(monkeypatch):
    m = _manager()
    m.is_authorized_and_active = lambda: False
    monkeypatch.setattr("src.execution.live_session.threading.Thread", lambda **kwargs: (_ for _ in ()).throw(AssertionError("worker must not start")))
    m.start_scanner()
    assert m.get_reconciliation_worker_status()["worker_started"] is False


def test_worker_stop_is_deterministic():
    m = _manager()
    class FakeThread:
        def is_alive(self): return False
    m._reconciliation_thread = FakeThread()
    m._scanner_running = True
    m.stop_scanner()
    assert m._scanner_stop_event.is_set()


def test_stop_reports_timeout_for_worker_that_does_not_terminate():
    m = _manager()
    class BlockedThread:
        def is_alive(self): return True
        def join(self, timeout=None): pass
    m._reconciliation_thread = BlockedThread()
    m._scanner_running = False
    result = m.stop_scanner()
    assert result["shutdown_complete"] is False
    assert result["lifecycle_state"] == "STOP_TIMEOUT"


def test_restart_is_blocked_while_old_worker_is_alive(monkeypatch):
    m = _manager()
    class OldThread:
        def is_alive(self): return True
    m._reconciliation_thread = OldThread()
    m._scanner_running = False
    m.start_scanner()
    assert m.get_reconciliation_worker_status()["worker_lifecycle_state"] == "START_BLOCKED_OLD_WORKER_ALIVE"


def test_worker_run_loop_acknowledges_start():
    m = _manager()
    m.is_authorized_and_active = lambda: False
    m._reconciliation_loop()
    assert m._reconciliation_started_event.is_set()
    assert m.get_reconciliation_worker_status()["worker_started"] is True


def test_unverified_worker_cycle_is_exposed_without_claiming_exchange_success():
    m = _manager()
    m._reconciliation_last_cycle_status = "UNVERIFIED"
    m._reconciliation_last_result = {"status": "UNVERIFIED"}
    m._reconciliation_cycle_count = 1
    status = m.get_reconciliation_worker_status()
    assert status["cycle_count"] == 1
    assert status["last_cycle_status"] == "UNVERIFIED"
    assert status["last_cycle_result"]["status"] == "UNVERIFIED"
