import threading

from src.execution.live_session import ExecutionMode, LiveAutoSessionManager


class _Lock:
    state = "PARTIALLY_PROTECTED"
    active_symbol = "ZECUSDT"


class _Coordinator:
    global_lock = _Lock()


class _Adapter:
    def __init__(self):
        self.calls = []

    def get_positions(self):
        self.calls.append("positions")
        return [{"symbol": "ZECUSDT", "positionAmt": "0.018"}]

    def get_open_orders(self):
        self.calls.append("orders")
        return [{"symbol": "ZECUSDT", "type": "STOP_MARKET", "reduceOnly": True}]


class _Journal:
    def __init__(self):
        self.kwargs = None

    def reconcile_with_exchange(self, **kwargs):
        self.kwargs = kwargs
        return {"status": "OK", "details": []}


def _manager(adapter=None, journal=None):
    m = object.__new__(LiveAutoSessionManager)
    m._reconciliation_mutex = threading.Lock()
    m.mode = ExecutionMode.LIVE_AUTO
    m.coordinator = _Coordinator()
    m.exchange_adapter = adapter or _Adapter()
    m.automated_journal = journal or _Journal()
    m._last_reconciliation = {}
    m.is_authorized_and_active = lambda: True
    return m


def test_reconciliation_worker_uses_exchange_quantity_and_authorizes_deferred_tp():
    journal = _Journal()
    m = _manager(journal=journal)
    result = m._run_reconciliation_once()
    assert result["status"] == "OK"
    assert journal.kwargs["allow_tp_submission"] is True
    assert journal.kwargs["exchange_positions"][0]["positionAmt"] == "0.018"


def test_reconciliation_failure_is_unverified_and_does_not_call_journal():
    class Failing:
        def get_positions(self):
            raise RuntimeError("read-only query failed")

    journal = _Journal()
    m = _manager(adapter=Failing(), journal=journal)
    result = m._run_reconciliation_once()
    assert result["status"] == "UNVERIFIED"
    assert m._last_reconciliation["reconciliation_status"] == "UNVERIFIED"
    assert journal.kwargs is None


def test_overlapping_reconciliation_is_skipped():
    m = _manager()
    m._reconciliation_mutex.acquire()
    try:
        assert m._run_reconciliation_once()["status"] == "SKIPPED_OVERLAP"
    finally:
        m._reconciliation_mutex.release()
