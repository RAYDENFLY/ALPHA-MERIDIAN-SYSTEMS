import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from src.scheduler.live_micro_runner import LiveMicroCoordinator


class _Conn:
    def __init__(self):
        import sqlite3
        self.raw = sqlite3.connect(":memory:")
        self.raw.execute("""
            CREATE TABLE automated_trade_journal (
                net_pnl_usd REAL,
                status TEXT,
                reconciliation_status TEXT,
                exit_timestamp_utc TEXT,
                mode TEXT
            )
        """)

    def execute(self, *args):
        return self.raw.execute(*args)

    def commit(self):
        self.raw.commit()


class _Db:
    def __init__(self):
        self.conn = _Conn()

    def get_connection(self):
        class _Ctx:
            def __init__(self, c): self.c = c
            def __enter__(self): return self.c
            def __exit__(self, *a): pass
        return _Ctx(self.conn)


def _coordinator(db, tz="WIB"):
    obj = LiveMicroCoordinator.__new__(LiveMicroCoordinator)
    obj.db = db
    obj.config = SimpleNamespace(risk=SimpleNamespace(daily_loss_limit_usdt=0.45, daily_reset_timezone=tz))
    return obj


def test_daily_loss_uses_closed_live_net_pnl_and_fees_are_already_in_net():
    db = _Db()
    coord = _coordinator(db, tz="WIB")
    today_wib = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d")
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (-0.14905076, "CLOSED", "RECONCILED", f"{today_wib}T08:00:00+07:00", "LIVE"))
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (0.05, "CLOSED", "RECONCILED", f"{today_wib}T09:00:00+07:00", "LIVE"))
    db.conn.commit()
    snapshot = coord.get_daily_loss_snapshot()
    assert snapshot["limit_usdt"] == 0.45
    assert snapshot["used_usdt"] == 0.149051
    assert snapshot["status"] == "VERIFIED"
    assert snapshot["timezone"] == "WIB"


def test_daily_loss_is_unverified_when_live_reconciliation_is_incomplete():
    db = _Db()
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (0.0, "OPEN", "REQUIRES_RECONCILIATION", None, "LIVE"))
    db.conn.commit()
    snapshot = _coordinator(db).get_daily_loss_snapshot()
    assert snapshot["used_usdt"] == 0.0
    assert snapshot["status"] == "UNVERIFIED"
    assert "incomplete" in snapshot["reason"]


def test_daily_loss_aggregates_multiple_reconciled_trades_on_same_utc_day(monkeypatch):
    db = _Db()
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (-0.16544972, "CLOSED", "RECONCILED", "2026-10-26T01:44:27.450000+00:00", "LIVE"))
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (-0.14905100, "CLOSED", "RECONCILED", "2026-10-26T23:26:40.100000+00:00", "LIVE"))
    db.conn.commit()

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 26, 12, tzinfo=timezone.utc)

    monkeypatch.setattr("src.scheduler.live_micro_runner.datetime", _Now)
    snapshot = _coordinator(db, tz="UTC").get_daily_loss_snapshot()
    assert snapshot["used_usdt"] == 0.314501
    assert snapshot["trades_count"] == 2
    assert snapshot["status"] == "VERIFIED"


def test_daily_loss_excludes_prior_utc_day_trade(monkeypatch):
    db = _Db()
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (-0.16544972, "CLOSED", "RECONCILED", "2026-10-26T01:44:27.450000+00:00", "LIVE"))
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (-0.14905100, "CLOSED", "RECONCILED", "2026-10-25T23:26:40.100000+00:00", "LIVE"))
    db.conn.commit()

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 26, 12, tzinfo=timezone.utc)

    monkeypatch.setattr("src.scheduler.live_micro_runner.datetime", _Now)
    snapshot = _coordinator(db, tz="UTC").get_daily_loss_snapshot()
    assert snapshot["used_usdt"] == 0.16545
    assert snapshot["trades_count"] == 1


def test_closed_unreconciled_trade_is_not_counted_and_marks_unverified(monkeypatch):
    db = _Db()
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (-0.15, "CLOSED", "PENDING_RECONCILIATION", "2026-10-26T01:00:00+00:00", "LIVE"))
    db.conn.commit()

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 26, 12, tzinfo=timezone.utc)

    monkeypatch.setattr("src.scheduler.live_micro_runner.datetime", _Now)
    snapshot = _coordinator(db, tz="UTC").get_daily_loss_snapshot()
    assert snapshot["used_usdt"] == 0.0
    assert snapshot["status"] == "UNVERIFIED"


def test_daily_loss_resets_at_wib_midnight_while_still_prior_utc_day(monkeypatch):
    """Test that at 00:15 WIB (17:15 UTC of prior UTC day), trades before 00:00 WIB are reset."""
    db = _Db()
    # Trade 1: Closed at 23:50 WIB (16:50 UTC Oct 26) -> Yesterday in WIB
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (-0.20, "CLOSED", "RECONCILED", "2026-10-26T16:50:00+00:00", "LIVE"))
    # Trade 2: Closed at 00:10 WIB (17:10 UTC Oct 26) -> Today in WIB (Oct 27 WIB)
    db.conn.execute("INSERT INTO automated_trade_journal VALUES (?, ?, ?, ?, ?)", (-0.05, "CLOSED", "RECONCILED", "2026-10-26T17:10:00+00:00", "LIVE"))
    db.conn.commit()

    wib_tz = timezone(timedelta(hours=7))
    class _NowWib(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 27, 0, 15, tzinfo=wib_tz)

    monkeypatch.setattr("src.scheduler.live_micro_runner.datetime", _NowWib)
    snapshot = _coordinator(db, tz="WIB").get_daily_loss_snapshot()
    # Only Trade 2 ($0.05) should be counted for today (Oct 27 WIB)! Trade 1 was yesterday (Oct 26 WIB).
    assert snapshot["used_usdt"] == 0.05
    assert snapshot["trades_count"] == 1
    assert snapshot["timezone"] == "WIB"
    assert snapshot["status"] == "VERIFIED"
