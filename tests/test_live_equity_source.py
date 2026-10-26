import threading
from types import SimpleNamespace

from src.execution.live_session import ExecutionMode, LiveAutoSessionManager, LiveSessionState


class _Account:
    def __init__(self, value=None, connected=True):
        self.snapshot = SimpleNamespace(
            total_equity_usdt=value,
            status="CONNECTED" if connected else "UNAVAILABLE",
        )

    def get_snapshot(self):
        return self.snapshot


def _manager(mode, account):
    m = LiveAutoSessionManager.__new__(LiveAutoSessionManager)
    m._mutex = threading.RLock()
    m.mode = mode
    m.state = LiveSessionState.ACTIVE
    m.session_id = "TEST"
    m.started_at_utc = None
    m.paused_at_utc = None
    m.stopped_at_utc = None
    m.authorized_by = "TEST"
    m.orders_placed_count = 0
    m.orders_filled_count = 0
    m.last_candidate_symbol = None
    m.last_status_message = ""
    m.recovery_reason = None
    m._scanner_running = False
    m._last_scan_utc = None
    m.coordinator = SimpleNamespace(
        current_equity=15.72,
        daily_filled_entries=0,
        daily_realized_loss=0.0,
        get_daily_loss_snapshot=lambda: {"used_usdt": 0.0, "status": "VERIFIED"},
        global_lock=SimpleNamespace(is_locked=False, active_symbol=None, state="IDLE"),
        kill_switch_triggered=False,
    )
    m.account_service = account
    m._last_reconciliation = {"reconciliation_status": "UNVERIFIED"}
    return m


def test_live_session_equity_uses_exchange_snapshot_not_local_baseline():
    info = _manager(ExecutionMode.LIVE_AUTO, _Account(15.01)).get_session_info()
    assert info["current_equity_usd"] == 15.01
    assert info["current_equity_source"] == "ASTERDEX_ACCOUNT_SNAPSHOT"


def test_live_session_equity_is_unverified_when_exchange_snapshot_unavailable():
    info = _manager(ExecutionMode.LIVE_AUTO, _Account(None, connected=False)).get_session_info()
    assert info["current_equity_usd"] is None
    assert info["current_equity_source"] == "UNVERIFIED"


def test_paper_session_keeps_local_equity_behavior():
    info = _manager(ExecutionMode.PAPER, _Account(15.01)).get_session_info()
    assert info["current_equity_usd"] == 15.72
    assert info["current_equity_source"] == "LIVE_MICRO_LOCAL_PAPER"
