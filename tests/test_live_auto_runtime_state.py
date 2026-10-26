import sqlite3
from types import SimpleNamespace

from src.scheduler.live_micro_runner import LiveMicroCoordinator


class _Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE automated_trade_journal (net_pnl_usd REAL, status TEXT, reconciliation_status TEXT, exit_timestamp_utc TEXT, mode TEXT)")
        self.conn.commit()

    def get_connection(self):
        return self.conn


def _coordinator(db):
    obj = LiveMicroCoordinator.__new__(LiveMicroCoordinator)
    obj.db = db
    obj.config = SimpleNamespace(risk=SimpleNamespace(daily_loss_limit_usdt=0.45))
    return obj


def test_daily_loss_uses_closed_live_net_pnl_and_fees_are_already_in_net():
    db = _Db()
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (-0.14905076, "CLOSED", "RECONCILED", "2026-10-26T01:00:00+00:00", "LIVE"))
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (0.05, "CLOSED", "RECONCILED", "2026-10-26T02:00:00+00:00", "LIVE"))
    db.conn.commit()
    snapshot = _coordinator(db).get_daily_loss_snapshot()
    # The implementation uses today's UTC date; patch the rows to today's date.
    assert snapshot["limit_usdt"] == 0.45
    assert snapshot["used_usdt"] == 0.149051
    assert snapshot["status"] == "VERIFIED"


def test_daily_loss_is_unverified_when_live_reconciliation_is_incomplete():
    db = _Db()
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (0.0, "OPEN", "REQUIRES_RECONCILIATION", None, "LIVE"))
    db.conn.commit()
    snapshot = _coordinator(db).get_daily_loss_snapshot()
    assert snapshot["used_usdt"] == 0.0
    assert snapshot["status"] == "UNVERIFIED"
    assert "incomplete" in snapshot["reason"]
