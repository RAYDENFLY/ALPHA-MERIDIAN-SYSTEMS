"""Model B paper-testing limits: no daily trade-count cap, 10 concurrent positions, other controls intact."""
import os
from datetime import datetime, timezone

import pytest

from config.settings import ModelBConfig, ModelDConfig, RiskConfig, get_default_config, ExecutionMode
from src.core.types import Direction, TradeRecord
from src.risk.manager import RiskManager
from src.risk.model_b_risk import ModelBRiskManager

NOW_MS = int(datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _open(i, ts=NOW_MS):
    return TradeRecord(trade_id=f"T{i}", symbol=f"SYM{i}", direction=Direction.LONG, entry_timestamp_ms=ts,
                       entry_price=100.0, stop_loss=95.0, take_profit=110.0, quantity=1.0,
                       notional_usdt=100.0, is_open=True, model_id="MODEL_B")


def _closed(i, pnl, ts=NOW_MS):
    t = _open(i, ts)
    t.is_open = False
    t.exit_timestamp_ms = ts
    t.pnl_net = pnl
    return t


def test_config_and_effective_runtime_value():
    assert ModelBConfig().max_open_positions == 10
    cfg = get_default_config()  # includes .env overrides
    assert cfg.model_b.max_open_positions == 10
    assert ModelBRiskManager(config=cfg.model_b).max_open_positions == 10
    assert cfg.mode == ExecutionMode.PAPER
    assert cfg.live_trading_locked is True


def test_no_daily_trade_count_limit_many_entries_same_day():
    risk = ModelBRiskManager(config=ModelBConfig())
    for i in range(10):
        ok, reason = risk.can_open_new_position(NOW_MS)
        assert ok, reason
        risk.record_entry(_open(i))
        risk.record_exit(_closed(i, 0.5))  # 10 trades already taken today (wins), none block the next
    assert risk.weekly_entries_count == 10
    ok, _ = risk.can_open_new_position(NOW_MS)
    assert ok


def test_ten_concurrent_allowed_eleventh_rejected():
    risk = ModelBRiskManager(config=ModelBConfig())
    for i in range(10):
        ok, reason = risk.can_open_new_position(NOW_MS)
        assert ok, reason
        risk.record_entry(_open(i))
    ok, reason = risk.can_open_new_position(NOW_MS)
    assert ok is False and "10/10" in reason


def test_config_cannot_exceed_ten():
    with pytest.raises(Exception):
        ModelBConfig(max_open_positions=11)


def test_daily_loss_limit_still_enforced():
    risk = ModelBRiskManager(config=ModelBConfig())
    risk._sync_calendar_buckets(NOW_MS)
    risk.daily_loss_usdt = 3.0
    ok, reason = risk.can_open_new_position(NOW_MS)
    assert ok is False and "daily loss" in reason


def test_weekly_loss_and_kill_switch_still_enforced():
    risk = ModelBRiskManager(config=ModelBConfig())
    risk._sync_calendar_buckets(NOW_MS)
    risk.weekly_loss_usdt = 7.5
    assert risk.can_open_new_position(NOW_MS)[0] is False
    risk2 = ModelBRiskManager(config=ModelBConfig(entry_kill_switch=True))
    assert risk2.can_open_new_position(NOW_MS)[0] is False


def test_risk_budget_and_sizing_defaults_unchanged():
    c = ModelBConfig()
    assert (c.target_trade_risk_usdt, c.max_leverage, c.max_margin_usdt) == (1.0, 10, 15.0)
    assert (c.daily_loss_limit_usdt, c.weekly_loss_limit_usdt) == (3.0, 7.5)
    # remaining-budget check still blocks when risk budget is exhausted
    risk = ModelBRiskManager(config=c)
    risk._sync_calendar_buckets(NOW_MS)
    risk.daily_loss_usdt = 2.6
    assert risk.can_open_new_position(NOW_MS)[0] is False


def test_model_a_and_d_limits_unchanged():
    assert RiskConfig().max_open_positions == 1
    a = RiskManager()
    assert a.max_open_positions == 1
    assert ModelDConfig().enabled in (True, False)


def test_live_trading_stays_locked():
    assert ModelBRiskManager(config=ModelBConfig()).live_trading_locked is True
    assert get_default_config().live_trading_locked is True
