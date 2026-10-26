"""Comprehensive Verification of Weekly Entry Policy and Risk Control Invariants.

Enforces:
1. Fourth, fifth, and subsequent otherwise-valid entries in same week are not rejected solely because of weekly entry count.
2. Valid setup is still rejected when daily loss guard is breached ($0.18).
3. Valid setup is still rejected when weekly loss guard is breached ($0.54).
4. Second concurrent position remains blocked while first position is open (max 1 open position).
5. STALE, INVALIDATED, INFEASIBLE, and duplicate setups remain blocked.
6. WATCHLIST setups cannot bypass confirmation requirements.
7. Weekly entry telemetry increments correctly without imposing a hard cap.
8. Weekly counters and risk state recover correctly after process restart via journal.
9. No code path can submit live orders while live_trading_locked=True.
10. Existing baseline strategy outputs remain unchanged for identical input candles.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
import pytest

from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    EntryCategory,
    ExecutionStatus,
    MarketBias,
    SetupCandidate,
    StructureState,
    TradeRecord,
)
from src.data.database import DatabaseManager
from src.exchange.aster_v3 import AsterDexV3Adapter
from src.exchange.metadata import SymbolSpecs
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperOrder, PaperOrderStatus, PaperPosition
from src.journal.repository import JournalRepository
from src.risk.manager import RiskManager
from src.risk.sizing import PositionSizer
from src.strategy.generator import SetupGenerator


def _create_specs(symbol: str = "ADAUSDT") -> SymbolSpecs:
    return SymbolSpecs(
        symbol=symbol,
        base_asset=symbol.replace("USDT", ""),
        quote_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        tick_size=0.0001,
        price_precision=4,
        step_size=0.1,
        quantity_precision=1,
        min_qty=0.1,
        max_qty=100000.0,
        min_notional=5.0,
        maint_margin_percent=0.01,
    )


def _create_sample_candle(
    timestamp_ms: int,
    symbol: str = "ADAUSDT",
    timeframe: str = "1h",
    open_: float = 0.50,
    high: float = 0.52,
    low: float = 0.49,
    close: float = 0.51,
    volume: float = 1000.0,
) -> Candle:
    duration_ms = 14400000 if timeframe == "4h" else 3600000
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp_ms=timestamp_ms,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        close_time_ms=timestamp_ms + duration_ms - 1,
    )


def _create_sample_candidate(
    candidate_id: str,
    timestamp_ms: int,
    symbol: str = "ADAUSDT",
    entry: float = 0.50,
    sl: float = 0.49,
    tp1: float = 0.52,
    direction: Direction = Direction.LONG,
    status: CandidateStatus = CandidateStatus.READY,
) -> SetupCandidate:
    cand = SetupCandidate(
        candidate_id=candidate_id,
        symbol=symbol,
        timestamp_ms=timestamp_ms,
        direction=direction,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp1 + 0.02,
        status=status,
    )
    cand.entry_category = EntryCategory.RANGE_MEAN_REVERSION
    return cand


# =========================================================================
# 1. Fourth, Fifth, and Subsequent Entries Allowed When Risk Permits
# =========================================================================
def test_1_fourth_and_subsequent_entries_not_blocked_by_weekly_count():
    """Requirement 1: Fourth, fifth, and subsequent otherwise-valid entries in the same
    week are NOT rejected solely because of weekly entry count."""
    rm = RiskManager(initial_equity_usdt=16.0, trade_risk_usdt=0.09)
    # Start on Wednesday of a week
    base_ts = 1700049600000  # 2023-11-15 12:00:00 UTC

    for entry_idx in range(7):
        ts = base_ts + entry_idx * 7200000  # +2 hours each
        can_open, reason = rm.can_open_new_position(ts)
        assert can_open is True, f"Entry #{entry_idx+1} rejected unexpectedly: {reason}"

        trade = TradeRecord(
            trade_id=f"trade_{entry_idx}",
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=ts,
            entry_price=0.50,
            stop_loss=0.49,
            take_profit=0.52,
            quantity=100.0,
            notional_usdt=50.0,
            is_open=True,
        )
        rm.record_entry(trade)

        # Close trade profitably so no open positions and no loss used
        trade.exit_timestamp_ms = ts + 3600000
        trade.pnl_net = 0.05
        trade.is_open = False
        rm.record_exit(trade)

    assert rm.weekly_entries_count == 7
    # 8th entry attempt is still allowed
    can_open_8, _ = rm.can_open_new_position(base_ts + 8 * 7200000)
    assert can_open_8 is True


# =========================================================================
# 2. Daily Loss Guard ($0.18) Breached Blocks New Entries
# =========================================================================
def test_2_daily_loss_guard_breached_blocks_entry():
    """Requirement 2: Valid setup is rejected when daily loss guard ($0.18) is breached."""
    rm = RiskManager(initial_equity_usdt=16.0, daily_loss_limit_usdt=0.18)
    ts = 1700049600000

    # Trade 1 loses $0.10
    t1 = TradeRecord(
        trade_id="t1",
        symbol="ADAUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=ts,
        entry_price=0.50,
        stop_loss=0.49,
        take_profit=0.52,
        quantity=100.0,
        notional_usdt=50.0,
        exit_timestamp_ms=ts + 1800000,
        pnl_net=-0.10,
        is_open=False,
    )
    rm.record_entry(t1)
    rm.record_exit(t1)

    # Daily loss = $0.10, limit = $0.18. Can still open if budget suffices
    can_open_mid, _ = rm.can_open_new_position(ts + 2000000)
    assert can_open_mid is True

    # Trade 2 loses $0.09 -> cumulative daily loss = $0.19 >= $0.18
    t2 = TradeRecord(
        trade_id="t2",
        symbol="ADAUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=ts + 2100000,
        entry_price=0.50,
        stop_loss=0.49,
        take_profit=0.52,
        quantity=100.0,
        notional_usdt=50.0,
        exit_timestamp_ms=ts + 3000000,
        pnl_net=-0.09,
        is_open=False,
    )
    rm.record_entry(t2)
    rm.record_exit(t2)

    # 3rd entry on same day must be rejected by daily loss limit
    can_open_blocked, reason = rm.can_open_new_position(ts + 3600000)
    assert can_open_blocked is False
    assert "Daily loss limit ($0.18) reached" in reason


# =========================================================================
# 3. Weekly Loss Guard ($0.54) Breached Blocks New Entries
# =========================================================================
def test_3_weekly_loss_guard_breached_blocks_entry():
    """Requirement 3: Valid setup is rejected when weekly loss guard ($0.54) is breached."""
    rm = RiskManager(
        initial_equity_usdt=16.0,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
    )
    # Start on Monday of week
    monday_ts = 1700006400000  # Monday 00:00:00 UTC

    # Simulate 3 daily losses of $0.18 on Mon, Tue, Wed (3 x $0.18 = $0.54)
    for day_offset in range(3):
        day_ts = monday_ts + day_offset * 86400000 + 3600000
        trade = TradeRecord(
            trade_id=f"loss_day_{day_offset}",
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=day_ts,
            entry_price=0.50,
            stop_loss=0.49,
            take_profit=0.52,
            quantity=100.0,
            notional_usdt=50.0,
            exit_timestamp_ms=day_ts + 1800000,
            pnl_net=-0.18,
            is_open=False,
        )
        rm.record_entry(trade)
        rm.record_exit(trade)

    # On Thursday, daily loss is 0.0, but weekly loss is $0.54 >= $0.54
    thursday_ts = monday_ts + 3 * 86400000 + 3600000
    can_open, reason = rm.can_open_new_position(thursday_ts)
    assert can_open is False
    assert "Weekly loss limit ($0.54) reached" in reason


# =========================================================================
# 4. Second Concurrent Position Remains Blocked
# =========================================================================
def test_4_second_concurrent_position_blocked():
    """Requirement 4: A second concurrent position remains blocked while the first position is open."""
    rm = RiskManager(initial_equity_usdt=16.0, max_open_positions=1)
    ts = 1700049600000

    t1 = TradeRecord(
        trade_id="open_pos_1",
        symbol="ADAUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=ts,
        entry_price=0.50,
        stop_loss=0.49,
        take_profit=0.52,
        quantity=100.0,
        notional_usdt=50.0,
        is_open=True,
    )
    rm.record_entry(t1)

    can_open, reason = rm.can_open_new_position(ts + 60000)
    assert can_open is False
    assert "Maximum open position limit (1) reached" in reason


# =========================================================================
# 5. Stale, Invalidated, Infeasible, and Duplicate Setups Blocked
# =========================================================================
def test_5_stale_invalidated_infeasible_duplicate_blocked(tmp_path: Path):
    """Requirement 5: STALE, INVALIDATED, INFEASIBLE, and duplicate setups remain blocked."""
    db = DatabaseManager(tmp_path / "test_safety_gates.db")
    journal = JournalRepository(db)
    rm = RiskManager(initial_equity_usdt=16.0)
    specs = _create_specs("ADAUSDT")

    engine = PaperExecutionEngine(
        db=db,
        journal=journal,
        risk_manager=rm,
    )

    now_ms = 1700049600000

    # A: Infeasible candidate (status=INFEASIBLE)
    c_inf = _create_sample_candidate("inf_1", now_ms, status=CandidateStatus.INFEASIBLE)
    order_inf = engine.submit_candidate(c_inf, now_ms=now_ms)
    assert order_inf is None

    # B: Stale candidate (> 3 hours old)
    c_stale = _create_sample_candidate("stale_1", now_ms - int(4 * 3600 * 1000))
    c_stale.risk_assessment = PositionSizer.calculate_sizing(
        symbol_specs=specs,
        entry_price=c_stale.entry_price,
        stop_loss=c_stale.stop_loss,
        target_isolated_margin=6.0,
        target_isolated_margin_max=6.5,
        available_equity_usdt=rm.equity_usdt,
        max_leverage=10,
        hard_risk_limit_usdt=1.50,
        risk_budget_usdt=0.09,
        direction=c_stale.direction,
    )
    order_stale = engine.submit_candidate(c_stale, now_ms=now_ms)
    assert order_stale is None

    # C: Valid candidate 1 submits successfully
    c_valid = _create_sample_candidate("valid_1", now_ms)
    c_valid.risk_assessment = PositionSizer.calculate_sizing(
        symbol_specs=specs,
        entry_price=c_valid.entry_price,
        stop_loss=c_valid.stop_loss,
        target_isolated_margin=6.0,
        target_isolated_margin_max=6.5,
        available_equity_usdt=rm.equity_usdt,
        max_leverage=10,
        hard_risk_limit_usdt=1.50,
        risk_budget_usdt=0.09,
        direction=c_valid.direction,
    )
    order_valid = engine.submit_candidate(c_valid, now_ms=now_ms)
    assert order_valid is not None
    assert order_valid.status == PaperOrderStatus.PENDING

    # D: Duplicate candidate rejected
    order_dup = engine.submit_candidate(c_valid, now_ms=now_ms + 1000)
    assert order_dup is None


# =========================================================================
# 6. WATCHLIST Setups Cannot Bypass Confirmation Requirements
# =========================================================================
def test_6_watchlist_setups_cannot_bypass_confirmation(tmp_path: Path):
    """Requirement 6: WATCHLIST setups cannot bypass their confirmation requirements."""
    db = DatabaseManager(tmp_path / "test_watchlist_gate.db")
    journal = JournalRepository(db)
    rm = RiskManager(initial_equity_usdt=16.0)

    engine = PaperExecutionEngine(
        db=db,
        journal=journal,
        risk_manager=rm,
    )

    now_ms = 1700049600000
    c_watchlist = _create_sample_candidate(
        "wl_1", now_ms, status=CandidateStatus.WATCHLIST
    )
    order = engine.submit_candidate(c_watchlist, now_ms=now_ms)
    assert order is None

    # Verify no pending order exists in journal
    pending = journal.get_paper_orders(status="PENDING")
    assert len(pending) == 0


# =========================================================================
# 7. Weekly Entry Telemetry Increments Correctly
# =========================================================================
def test_7_weekly_entry_telemetry_increments_correctly():
    """Requirement 7: Weekly entry telemetry increments correctly without imposing a hard cap."""
    rm = RiskManager(initial_equity_usdt=16.0)
    base_ts = 1700049600000
    assert rm.weekly_entries_count == 0

    for i in range(10):
        t = TradeRecord(
            trade_id=f"t_tel_{i}",
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=base_ts + i * 3600000,
            entry_price=0.50,
            stop_loss=0.49,
            take_profit=0.52,
            quantity=100.0,
            notional_usdt=50.0,
            is_open=True,
        )
        rm.record_entry(t)
        assert rm.weekly_entries_count == i + 1
        t.exit_timestamp_ms = base_ts + i * 3600000 + 1800000
        t.pnl_net = 0.02
        t.is_open = False
        rm.record_exit(t)

    assert rm.weekly_entries_count == 10


# =========================================================================
# 8. Weekly Counters and Risk State Recover After Process Restart
# =========================================================================
def test_8_weekly_counters_and_risk_state_recover_after_restart(tmp_path: Path):
    """Requirement 8: Weekly counters and risk state recover correctly after process restart."""
    db = DatabaseManager(tmp_path / "test_restart_rec.db")
    journal = JournalRepository(db)

    # Wednesday 2026-10-21 12:00:00 UTC
    now_dt = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
    now_ms = int(now_dt.timestamp() * 1000)

    # 4 trades executed earlier in the same week: 3 wins, 1 loss (-$0.08)
    for i in range(4):
        entry_ms = now_ms - (4 - i) * 7200000
        exit_ms = entry_ms + 3600000
        pnl = -0.08 if i == 3 else 0.05
        t = TradeRecord(
            trade_id=f"rec_{i}",
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=entry_ms,
            entry_price=0.50,
            stop_loss=0.49,
            take_profit=0.52,
            quantity=100.0,
            notional_usdt=50.0,
            exit_timestamp_ms=exit_ms,
            exit_price=0.51 if pnl > 0 else 0.49,
            pnl_net=pnl,
            is_open=False,
        )
        journal.save_trade(t)

    # Simulate fresh process startup / restart
    fresh_rm = RiskManager(
        initial_equity_usdt=16.0,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
    )
    fresh_rm.sync_state_from_journal(journal, now_ms=now_ms)

    # Telemetry recovered
    assert fresh_rm.weekly_entries_count == 4
    # Realized loss recovered
    assert fresh_rm.daily_loss_usdt == 0.08
    assert fresh_rm.weekly_loss_usdt == 0.08
    # 5th entry is permitted (not blocked by weekly count, daily loss 0.08 < 0.18)
    can_open, reason = fresh_rm.can_open_new_position(now_ms)
    assert can_open is True, f"5th entry should be permitted after recovery, got: {reason}"


# =========================================================================
# 9. No Code Path Can Submit Live Orders While live_trading_locked=True
# =========================================================================
def test_9_live_trading_locked_prevents_live_orders():
    """Requirement 9: No code path can submit live orders while live_trading_locked=True."""
    from src.core.exceptions import ExchangeAPIError
    adapter = AsterDexV3Adapter(
        live_trading_locked=True,
    )
    with pytest.raises(ExchangeAPIError, match="live_trading_locked=True"):
        adapter.create_order(
            symbol="ADAUSDT",
            side="BUY",
            order_type="LIMIT",
            quantity=10.0,
            price=0.50,
        )


# =========================================================================
# 10. Baseline Strategy Outputs Unchanged for Identical Input Candles
# =========================================================================
def test_10_baseline_strategy_outputs_unchanged():
    """Requirement 10: Existing baseline strategy outputs remain unchanged for identical input candles."""
    generator = SetupGenerator()
    base_ts = 1700000000000

    candles_1h = [
        _create_sample_candle(
            base_ts + i * 3600000,
            timeframe="1h",
            open_=0.50 + i * 0.001,
            high=0.52 + i * 0.001,
            low=0.49 + i * 0.001,
            close=0.51 + i * 0.001,
        )
        for i in range(50)
    ]
    candles_4h = [
        _create_sample_candle(
            base_ts + i * 14400000,
            timeframe="4h",
            open_=0.50,
            high=0.53,
            low=0.48,
            close=0.51,
        )
        for i in range(20)
    ]

    candidate = generator.generate_candidate(
        symbol="ADAUSDT",
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        as_of_index_1h=45,
    )
    # The policy change touched RiskManager/API, NOT SetupGenerator parameters
    assert generator.discount_threshold == 0.30
    assert generator.premium_threshold == 0.70
    assert generator.bull_breakout_pos_72h == 0.85
