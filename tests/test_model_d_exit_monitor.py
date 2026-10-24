from pathlib import Path
import uuid
from types import SimpleNamespace

from src.data.database import DatabaseManager
from src.model_d.bots import BotConfig, PersistentBotStore
from src.model_d.scanner import ModelDExitMonitor


class FakeCollector:
    def __init__(self, status="LIVE", high=110.0, low=99.0, mid=105.0, fail_symbol=None):
        self.status, self.high, self.low, self.mid, self.fail_symbol = status, high, low, mid, fail_symbol

    def collect_symbol(self, symbol):
        if symbol == self.fail_symbol:
            raise RuntimeError("fixture failure")
        return {"order_book": SimpleNamespace(mid_price=self.mid), "candles": [SimpleNamespace(high=self.high, low=self.low)], "sources": {"order_book": {"status": self.status}, "candles": {"status": self.status}}}


def make_monitor(status="LIVE", high=110.0, low=99.0, fail_symbol=None):
    db = DatabaseManager(Path("data") / f"model_d_monitor_{uuid.uuid4().hex}.db")
    store = PersistentBotStore(db); store.initialize([BotConfig("MODEL_D_BOT_100", 100.0)])
    bots = store.load(); bot = bots["MODEL_D_BOT_100"]
    position = bot.open("signal-1", "BTCUSDT", "LONG", 100.0, min_notional=.1, stop_loss=95.0, take_profit=110.0)
    store.persist_open(position)
    return ModelDExitMonitor(FakeCollector(status, high, low, fail_symbol=fail_symbol), bots, store), store, position


def test_fresh_data_closes_and_stale_data_does_not():
    monitor, store, position = make_monitor(high=111.0)
    result = monitor.run_once()
    assert result["exits"] == 1
    assert store.load()["MODEL_D_BOT_100"].positions[position.position_id].status == "CLOSED"

    monitor, store, position = make_monitor(status="STALE", high=111.0)
    result = monitor.run_once()
    assert result["exits"] == 0 and result["stale"] == 1
    assert store.load()["MODEL_D_BOT_100"].positions[position.position_id].status == "OPEN"


def test_failed_symbol_isolated_and_overlap_is_rejected():
    monitor, store, position = make_monitor(fail_symbol="BTCUSDT")
    monitor._lock.acquire()
    try:
        assert monitor.run_once()["status"] == "OVERLAPPING_MONITOR_REJECTED"
    finally:
        monitor._lock.release()
    result = monitor.run_once()
    assert result["unavailable"] == 1
    assert store.load()["MODEL_D_BOT_100"].positions[position.position_id].status == "OPEN"
