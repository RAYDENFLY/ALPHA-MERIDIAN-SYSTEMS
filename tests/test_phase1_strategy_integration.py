import threading
from types import SimpleNamespace

import pytest

from src.ai.groq_autotrader import ExecutionVenue, GroqAutoTrader
from src.execution.live_session import LiveAutoSessionManager


class _Strategy:
    def __init__(self, strategy_id, is_live=True):
        self.strategy_id = strategy_id
        self.is_live = is_live


def _coordinator(strategy_id="15M_SWEEP_RECLAIM_SMC", include_impl=True):
    cfg = SimpleNamespace(strategy=SimpleNamespace(live_strategy_id=strategy_id))
    obj = SimpleNamespace(config=cfg)
    if include_impl:
        obj.sweep_reclaim_strategy = _Strategy("15M_SWEEP_RECLAIM_SMC")
        obj.ema_strategy = _Strategy("EMA20_RECLAIM_5M_V1")
    return obj


def test_configured_strategy_authority_resolves_sweep_implementation():
    manager = object.__new__(LiveAutoSessionManager)
    manager.coordinator = _coordinator()
    selected, reason = manager.get_configured_live_strategy_id()
    assert selected == "15M_SWEEP_RECLAIM_SMC"
    assert reason == "OK"


def test_unavailable_configured_strategy_fails_closed():
    manager = object.__new__(LiveAutoSessionManager)
    manager.coordinator = _coordinator(include_impl=False)
    selected, reason = manager.get_configured_live_strategy_id()
    assert selected is None
    assert "unavailable" in reason


def test_unknown_configured_strategy_fails_closed():
    manager = object.__new__(LiveAutoSessionManager)
    manager.coordinator = _coordinator("UNSUPPORTED_STRATEGY")
    selected, reason = manager.get_configured_live_strategy_id()
    assert selected is None
    assert "unavailable" in reason


def test_live_pipeline_rejects_legacy_strategy_mismatch_without_dispatch():
    trader = object.__new__(GroqAutoTrader)
    trader._telemetry_lock = threading.RLock()
    trader._active_cycle_telemetry = None
    trader._last_cycle_telemetry = None
    coord = _coordinator()
    trader.session_manager = None
    trader.coordinator = coord
    with pytest.raises(PermissionError, match="does not match configured strategy"):
        trader.run_full_pipeline(
            execution_venue=ExecutionVenue.LIVE_AUTO,
            execution_mode=ExecutionVenue.LIVE_AUTO,
            strategy_id="EMA20_RECLAIM_5M_V1",
            coordinator=coord,
        )
    telemetry = trader.get_cycle_telemetry()
    assert telemetry["status"] == "ERROR"
    assert telemetry["strategy_id"] == "EMA20_RECLAIM_5M_V1"


def test_live_strategy_helper_rejects_non_live_implementation():
    coord = _coordinator()
    coord.sweep_reclaim_strategy.is_live = False
    selected, reason = GroqAutoTrader._configured_live_strategy_id(coord)
    assert selected is None
    assert "not live" in reason
