import time

from src.ai.groq_autotrader import GroqAutoTrader


def test_cycle_telemetry_records_monotonic_stage_duration_without_running_pipeline():
    trader = object.__new__(GroqAutoTrader)
    import threading
    trader._telemetry_lock = threading.RLock()
    trader._active_cycle_telemetry = None
    trader._last_cycle_telemetry = None

    cycle = trader._cycle_start()
    started = trader._stage_start("market_snapshot")
    time.sleep(0.001)
    trader._stage_end("market_snapshot", started)
    trader._finish_cycle(cycle, "SUCCESS")
    data = trader.get_cycle_telemetry()

    assert data["cycle_id"]
    assert data["last_completed_cycle_id"] == data["cycle_id"]
    assert data["last_cycle_status"] == "SUCCESS"
    assert data["last_cycle_duration_ms"] >= data["stages"]["market_snapshot"]["duration_ms"]
    assert data["stages"]["market_snapshot"]["duration_ms"] >= 0


def test_cycle_telemetry_exposes_idle_status_before_first_cycle():
    trader = object.__new__(GroqAutoTrader)
    import threading
    trader._telemetry_lock = threading.RLock()
    trader._active_cycle_telemetry = None
    trader._last_cycle_telemetry = None
    assert trader.get_cycle_telemetry() == {"last_cycle_status": "IDLE", "current_stage": "IDLE"}
