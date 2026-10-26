from types import SimpleNamespace

from src.execution.live_session import LiveAutoSessionManager
from src.risk.global_lock import GlobalPositionLock


class _Adapter:
    def __init__(self, positions=None, orders=None, error=None):
        self.positions = positions or []
        self.orders = orders or []
        self.error = error

    def get_positions(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.positions

    def get_open_orders(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.orders


def _manager(adapter, locked=True):
    GlobalPositionLock._instance = None
    lock = GlobalPositionLock()
    if locked:
        lock.acquire("TEST", "SOLUSDT", "old", state="PROTECTED")
    mgr = LiveAutoSessionManager.__new__(LiveAutoSessionManager)
    mgr.coordinator = SimpleNamespace(global_lock=lock, entries_paused=True)
    mgr.exchange_adapter = adapter
    mgr.account_service = None
    return mgr


def test_zero_position_and_no_orders_releases_stale_lock():
    mgr = _manager(_Adapter())
    ok, msg = mgr._refresh_lock_from_exchange()
    assert ok is True
    assert mgr.coordinator.global_lock.is_locked is False
    assert mgr.coordinator.entries_paused is False


def test_nonzero_position_retains_lock():
    mgr = _manager(_Adapter(positions=[{"symbol": "SOLUSDT", "positionAmt": "0.19"}]))
    ok, msg = mgr._refresh_lock_from_exchange()
    assert ok is True
    assert mgr.coordinator.global_lock.is_locked is True
    assert mgr.coordinator.global_lock.state == "FILLED"


def test_exchange_failure_retains_lock_and_is_unresolved():
    mgr = _manager(_Adapter(error=RuntimeError("signed request failed")))
    ok, msg = mgr._refresh_lock_from_exchange()
    assert ok is False
    assert mgr.coordinator.global_lock.is_locked is True


def test_closed_position_with_orphan_protective_order_does_not_silently_unlock():
    mgr = _manager(_Adapter(orders=[{"symbol": "SOLUSDT", "type": "STOP_MARKET", "reduceOnly": True}]))
    # A closed position with an orphan order is unresolved, not a clean close.
    ok, msg = mgr._refresh_lock_from_exchange()
    assert ok is False
    assert mgr.coordinator.global_lock.is_locked is True
    assert mgr.coordinator.global_lock.state == "RECONCILIATION_REQUIRED"
    assert "orphan protective order" in msg
