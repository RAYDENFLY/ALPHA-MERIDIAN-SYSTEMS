"""Deterministic Integration and Audit Tests for Forward Paper Activation.

Covers:
1. Step 3: Risk-Guard Interactions (Hard SL $1.50, Daily Cap $0.18, Weekly Cap $0.54, Max 1 Pos, Max 3 Entries/Wk).
2. Step 4: Paper Execution End-to-End Lifecycle & Idempotent State Recovery on Restart.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest

from unittest.mock import MagicMock, patch

from config.settings import ExecutionMode, PaperExecutionConfig
from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    EntryCategory,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
    TradeRecord,
)
from src.data.database import DatabaseManager
from src.exchange.metadata import SymbolSpecs
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperOrderStatus
from src.journal.repository import JournalRepository
from src.risk.manager import RiskManager
from src.scheduler.runner import ScannerRunner


def _make_candle(symbol: str, ts_ms: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe="1h",
        timestamp_ms=ts_ms,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
        close_time_ms=ts_ms + 3600_000 - 1,
    )


def _make_candidate(
    symbol: str = "SOLUSDT",
    ts_ms: int = 1760000000000,
    direction: Direction = Direction.LONG,
    entry_price: float = 100.0,
    stop_loss: float = 98.0,
    tp1: float = 104.0,
    tp2: float = 108.0,
) -> SetupCandidate:
    assessment = RiskAssessment(
        symbol=symbol,
        entry_price=entry_price,
        stop_loss=stop_loss,
        stop_distance_pct=abs(entry_price - stop_loss) / entry_price,
        risk_budget_usdt=0.09,
        position_notional_usdt=60.0,
        quantity=0.60,
        required_margin_usdt=6.00,
        effective_leverage=3.75,
        estimated_fee_usdt=0.06,
        estimated_slippage_usdt=0.06,
        is_feasible=True,
        infeasible_reason=None,
        target_isolated_margin_usdt=6.00,
        estimated_loss_usdt=1.32,
        estimated_loss_pct=8.25,
        position_leverage=10.0,
        equity_exposure_ratio=3.75,
    )
    return SetupCandidate(
        candidate_id=f"CAND_{symbol}_{ts_ms}",
        symbol=symbol,
        timestamp_ms=ts_ms,
        direction=direction,
        bias_4h=MarketBias.BULLISH if direction == Direction.LONG else MarketBias.BEARISH,
        structure_1h=StructureState.BULLISH if direction == Direction.LONG else StructureState.BEARISH,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit_1=tp1,
        take_profit_2=tp2,
        gross_reward_to_risk=2.0,
        net_reward_to_risk=1.8,
        invalidation_price=stop_loss,
        invalidation_reason="Structural swing",
        smc_evidence=["1H FVG"],
        snr_evidence=["Support cluster"],
        supporting_reasons=["4H trend aligned"],
        opposing_reasons=[],
        ml_probability=0.55,
        calibration_status="VALIDATED",
        eval_sample_count=60,
        status=CandidateStatus.READY,
        entry_category=EntryCategory.PULLBACK,
        rejection_reasons=[],
        expires_at_ms=ts_ms + (12 * 3600 * 1000),
        risk_assessment=assessment,
    )


# ==============================================================================
# STEP 3: Deterministic Risk-Guard Interaction Scenarios
# ==============================================================================

def test_risk_guard_interaction_scenario_1_loss_0_18():
    """Scenario 1: Single trade loss of exactly $0.18 USDT.
    Verifies that daily loss cap ($0.18) halts further trading for the day,
    while leaving $0.36 weekly loss budget remaining.
    """
    rm = RiskManager(
        initial_equity_usdt=16.0,
        trade_risk_usdt=0.09,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
        max_open_positions=1,
        max_entries_per_week=3,
    )
    t0 = 1760000000000  # Monday 08:53 UTC
    can_open_0, _ = rm.can_open_new_position(t0)
    assert can_open_0 is True

    # Record trade entry and exit with -$0.18 loss
    tr = TradeRecord(
        trade_id="TR_SCEN_1",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=t0,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        quantity=0.6,
        notional_usdt=60.0,
        exit_timestamp_ms=t0 + 3600_000,
        exit_price=99.7,
        pnl_net=-0.18,
        fee_paid=0.02,
        funding_paid=0.0,
        exit_reason="STOP_LOSS",
        is_open=False,
    )
    rm.record_entry(tr)
    rm.record_exit(tr)

    assert rm.daily_loss_usdt == 0.18
    assert rm.weekly_loss_usdt == 0.18
    assert rm.weekly_entries_count == 1
    assert rm.equity_usdt == 15.82

    # Subsequent entry on the same day must be blocked
    can_open_same_day, reason_same_day = rm.can_open_new_position(t0 + 7200_000)
    assert can_open_same_day is False
    assert "Daily loss limit" in (reason_same_day or "")
    assert rm.get_effective_trade_risk(t0 + 7200_000) == 0.0

    # Next day (00:00 UTC boundary, t0 + 24h): daily meter resets to 0.0, weekly remains 0.18
    t_next_day = t0 + (24 * 3600 * 1000)
    can_open_next_day, _ = rm.can_open_new_position(t_next_day)
    assert can_open_next_day is True
    assert rm.daily_loss_usdt == 0.0
    assert rm.weekly_loss_usdt == 0.18
    # Remaining weekly budget: 0.54 - 0.18 = 0.36; effective risk min(0.09, 0.18, 0.36) = 0.09
    assert rm.get_effective_trade_risk(t_next_day) == 0.09


def test_risk_guard_interaction_scenario_2_loss_0_50():
    """Scenario 2: Single trade loss of $0.50 USDT.
    Verifies that daily loss limit is breached, weekly budget drops to $0.04,
    and effective trade risk collapses to 0.04 on day 2.
    """
    rm = RiskManager(
        initial_equity_usdt=16.0,
        trade_risk_usdt=0.09,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
    )
    t0 = 1760000000000
    tr = TradeRecord(
        trade_id="TR_SCEN_2",
        symbol="ADAUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=t0,
        entry_price=0.50,
        stop_loss=0.48,
        take_profit=0.54,
        quantity=100.0,
        notional_usdt=50.0,
        exit_timestamp_ms=t0 + 3600_000,
        exit_price=0.495,
        pnl_net=-0.50,
        fee_paid=0.02,
        funding_paid=0.0,
        exit_reason="STOP_LOSS",
        is_open=False,
    )
    rm.record_entry(tr)
    rm.record_exit(tr)

    assert rm.daily_loss_usdt == 0.50
    assert rm.weekly_loss_usdt == 0.50

    can_open, reason = rm.can_open_new_position(t0 + 7200_000)
    assert can_open is False
    assert "Daily loss limit" in (reason or "")

    # Next day: daily meter resets to 0.0, but remaining weekly is only 0.54 - 0.50 = 0.04
    t_next_day = t0 + (24 * 3600 * 1000)
    eff_risk = rm.get_effective_trade_risk(t_next_day)
    assert eff_risk == 0.04  # min(0.09, 0.18, 0.04)


def test_risk_guard_interaction_scenario_3_sl_loss_1_50():
    """Scenario 3: Single trade reaches hard SL with loss of $1.50 USDT.
    Because $1.50 > daily cap ($0.18) and $1.50 > weekly cap ($0.54),
    this single trade halts the account for the ENTIRE calendar week.
    """
    rm = RiskManager(
        initial_equity_usdt=16.0,
        trade_risk_usdt=0.09,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
        max_open_positions=1,
        max_entries_per_week=3,
    )
    t0 = 1760313600000  # Monday 2025-10-13 00:00 UTC (Week 2025-W41)
    tr = TradeRecord(
        trade_id="TR_SCEN_3",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=t0,
        entry_price=100.0,
        stop_loss=97.5,
        take_profit=104.0,
        quantity=0.6,
        notional_usdt=60.0,
        exit_timestamp_ms=t0 + 3600_000,
        exit_price=97.5,
        pnl_net=-1.50,
        fee_paid=0.06,
        funding_paid=0.0,
        exit_reason="STOP_LOSS",
        is_open=False,
    )
    rm.record_entry(tr)
    rm.record_exit(tr)

    assert rm.daily_loss_usdt == 1.50
    assert rm.weekly_loss_usdt == 1.50

    # Blocked on Day 1 (Daily + Weekly)
    can_open_d1, r1 = rm.can_open_new_position(t0 + 7200_000)
    assert can_open_d1 is False

    # Day 2: Daily meter resets, but Weekly limit ($0.54) remains breached ($1.50 >= $0.54)
    t_day2 = t0 + (24 * 3600 * 1000)
    can_open_d2, r2 = rm.can_open_new_position(t_day2)
    assert can_open_d2 is False
    assert "Weekly loss limit" in (r2 or "")
    assert rm.get_effective_trade_risk(t_day2) == 0.0

    # Day 5 (Friday): Still blocked by weekly limit
    t_day5 = t0 + (4 * 24 * 3600 * 1000)
    can_open_d5, r5 = rm.can_open_new_position(t_day5)
    assert can_open_d5 is False
    assert "Weekly loss limit" in (r5 or "")

    # Next Monday (t0 + 7 days): Calendar week bucket changes -> Weekly meter resets!
    t_next_mon = t0 + (7 * 24 * 3600 * 1000)
    can_open_mon, _ = rm.can_open_new_position(t_next_mon)
    assert can_open_mon is True
    assert rm.weekly_loss_usdt == 0.0
    assert rm.daily_loss_usdt == 0.0
    assert rm.weekly_entries_count == 0


def test_risk_guard_interaction_scenario_4_restart_behavior(tmp_path: Path):
    """Scenario 4: Restart after losing trade and before next day.
    Audits whether process restart preserves or resets realized loss counters.
    """
    db = DatabaseManager(tmp_path / "test_restart.db")
    journal = JournalRepository(db)

    # Session 1: Run trade that loses -$0.18
    rm1 = RiskManager(initial_equity_usdt=16.0, daily_loss_limit_usdt=0.18, weekly_loss_limit_usdt=0.54)
    paper_cfg = PaperExecutionConfig(enabled=True)
    engine1 = PaperExecutionEngine(db=db, journal=journal, risk_manager=rm1, config=paper_cfg)

    t0 = 1760000000000
    cand = _make_candidate(ts_ms=t0)
    journal.save_setup(cand)
    order = engine1.submit_candidate(cand, now_ms=t0 + 1000)
    assert order is not None

    # Fill
    c1 = _make_candle("SOLUSDT", t0 + 3600_000, 100.5, 101.0, 99.8, 100.1)
    engine1.evaluate_pending_orders({"SOLUSDT": [c1]}, now_ms=t0 + 3660_000)
    assert "SOLUSDT" in engine1.open_positions

    # Exit at SL with net loss
    c2 = _make_candle("SOLUSDT", t0 + 7200_000, 100.1, 100.2, 97.5, 97.6)
    closed = engine1.evaluate_open_positions({"SOLUSDT": [c1, c2]}, now_ms=t0 + 7260_000)
    assert len(closed) == 1
    assert closed[0].pnl_net < 0
    trade_pnl = closed[0].pnl_net
    assert rm1.daily_loss_usdt == abs(trade_pnl)
    assert len(engine1.open_positions) == 0

    # Session 2: Process Restart! Create fresh RiskManager and PaperExecutionEngine
    rm2 = RiskManager(initial_equity_usdt=16.0, daily_loss_limit_usdt=0.18, weekly_loss_limit_usdt=0.54)
    engine2 = PaperExecutionEngine(db=db, journal=journal, risk_manager=rm2, config=paper_cfg)
    engine2.recover_state()

    # AUDIT FINDING:
    # Notice: In the default RiskManager implementation, rm2 is instantiated with daily_loss_usdt=0.0
    # unless historical closed trades for today are loaded from SQLite into rm2.closed_trades!
    # SQLite has the completed trade in trade_journal:
    closed_in_db = journal.get_trades(open_only=False)
    assert len(closed_in_db) == 1
    assert closed_in_db[0]["trade_id"] == closed[0].trade_id
    assert closed_in_db[0]["pnl_net"] == pytest.approx(trade_pnl, rel=1e-5)


# ==============================================================================
# STEP 4: Paper Execution End-to-End Integration & Persistence
# ==============================================================================

def test_paper_execution_end_to_end_deterministic(tmp_path: Path):
    """Prove the complete deterministic end-to-end paper lifecycle:
    1. Scanner creates eligible setup candidate.
    2. Risk assessment succeeds.
    3. Paper limit order is created.
    4. Fill condition: price must penetrate past entry with buffer (0.05%).
    5. Order fills -> open position stored in trade_journal with is_open=1.
    6. Take-profit closes position with maker fee, taker exit fee, and slippage.
    7. Trade journal finalized with is_open=0.
    8. Engine restart recover_state() does NOT duplicate orders or trades.
    """
    db = DatabaseManager(tmp_path / "test_e2e.db")
    journal = JournalRepository(db)
    rm = RiskManager(initial_equity_usdt=16.0)
    paper_cfg = PaperExecutionConfig(
        enabled=True,
        conservative_fill_buffer_pct=0.0005,
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
        slippage_rate=0.0005,
        enable_tp1_trailing_stop=False,  # Single-leg TP1 for exact test closure
    )
    engine = PaperExecutionEngine(db=db, journal=journal, risk_manager=rm, config=paper_cfg)

    t0 = 1760000000000
    cand = _make_candidate(symbol="AVAXUSDT", ts_ms=t0, entry_price=20.0, stop_loss=19.5, tp1=21.0, tp2=None)
    journal.save_setup(cand)

    # 1 & 2. Submit candidate -> Order Created
    order = engine.submit_candidate(cand, now_ms=t0 + 1000)
    assert order is not None
    assert order.status == PaperOrderStatus.PENDING
    assert order.symbol == "AVAXUSDT"

    # Verify pending order persisted in database
    pending_orders = journal.get_paper_orders(status="PENDING")
    assert len(pending_orders) == 1
    assert pending_orders[0]["order_id"] == order.order_id

    # 3. Bar 1: Price does NOT penetrate entry price (Low = 20.01 > 20.00 * (1 - 0.0005) = 19.99)
    c1 = _make_candle("AVAXUSDT", t0 + 3600_000, 20.20, 20.50, 20.01, 20.30)
    fills_1 = engine.evaluate_pending_orders({"AVAXUSDT": [c1]}, now_ms=t0 + 3660_000)
    assert len(fills_1) == 0
    assert order.order_id in engine.pending_orders
    assert len(engine.open_positions) == 0

    # 4. Bar 2: Price penetrates entry price (Low = 19.95 <= 19.99) -> FILLS at limit price 20.0
    c2 = _make_candle("AVAXUSDT", t0 + 7200_000, 20.30, 20.40, 19.95, 20.10)
    fills_2 = engine.evaluate_pending_orders({"AVAXUSDT": [c1, c2]}, now_ms=t0 + 7260_000)
    assert len(fills_2) == 1
    assert fills_2[0].order_id == order.order_id
    assert fills_2[0].status == PaperOrderStatus.FILLED
    assert "AVAXUSDT" in engine.open_positions

    # 5. Verify position open state in database
    open_trades_db = journal.get_open_trades()
    assert len(open_trades_db) == 1
    assert open_trades_db[0]["symbol"] == "AVAXUSDT"
    assert open_trades_db[0]["is_open"] == 1
    assert open_trades_db[0]["entry_price"] == 20.0

    # 6. Bar 3: Price hits Take-Profit (High = 21.20 >= 21.0)
    c3 = _make_candle("AVAXUSDT", t0 + 10800_000, 20.10, 21.20, 20.05, 21.10)
    closed = engine.evaluate_open_positions({"AVAXUSDT": [c1, c2, c3]}, now_ms=t0 + 10860_000)
    assert len(closed) == 1
    assert closed[0].exit_reason == "TAKE_PROFIT"
    assert closed[0].is_open is False
    assert closed[0].pnl_net > 0  # Profitable net after maker entry + taker exit fees

    # 7. Verify finalized journal record in database
    final_trades_db = journal.get_trades(open_only=False)
    assert len(final_trades_db) == 1
    assert final_trades_db[0]["is_open"] == 0
    assert final_trades_db[0]["exit_reason"] == "TAKE_PROFIT"
    assert final_trades_db[0]["exit_price"] == 21.0

    # 8. Idempotent Restart: Re-instantiate engine on same database
    engine_recovered = PaperExecutionEngine(db=db, journal=journal, risk_manager=rm, config=paper_cfg)
    engine_recovered.recover_state()
    assert len(engine_recovered.open_positions) == 0
    assert len(engine_recovered.pending_orders) == 0
    # No duplicate trade records created
    assert len(journal.get_trades(open_only=False)) == 1


# ==============================================================================
# TASK A INTEGRATION TESTS: Authoritative Risk Counter Recovery from Journal
# ==============================================================================

def test_risk_recovery_restart_same_day_loss_0_18(tmp_path: Path):
    """Scenario: Loss $0.18, restart, then entry attempt on the same UTC day.
    Proves that RiskManager restores daily and weekly loss meters from SQLite trade_journal,
    and blocks new position entries on the same UTC day because daily loss limit ($0.18) is exhausted.
    """
    db = DatabaseManager(tmp_path / "test_rec_018.db")
    journal = JournalRepository(db)

    # Monday 10:00 UTC
    dt_entry = datetime(2026, 10, 19, 10, 0, 0, tzinfo=timezone.utc)
    ts_entry = int(dt_entry.timestamp() * 1000)
    dt_exit = datetime(2026, 10, 19, 11, 0, 0, tzinfo=timezone.utc)
    ts_exit = int(dt_exit.timestamp() * 1000)

    # Initial session: trade loses $0.18 net
    trade = TradeRecord(
        trade_id="TR_REC_018",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=ts_entry,
        entry_price=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        quantity=0.6,
        notional_usdt=60.0,
        exit_timestamp_ms=ts_exit,
        exit_price=99.7,
        pnl_gross=-0.16,
        pnl_net=-0.18,  # Net includes fee and slippage
        fee_paid=0.015,
        slippage_paid=0.005,
        exit_reason="STOP_LOSS",
        is_open=False,
    )
    journal.save_trade(trade)

    # Simulate restart: fresh RiskManager instance with no prior in-memory state
    rm_recovered = RiskManager(
        initial_equity_usdt=16.0,
        trade_risk_usdt=0.09,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
    )
    # Synchronize state from journal at Monday 12:00 UTC (same day)
    ts_now = int(datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    rm_recovered.sync_state_from_journal(journal, now_ms=ts_now)

    assert rm_recovered.daily_loss_usdt == 0.18
    assert rm_recovered.weekly_loss_usdt == 0.18
    assert rm_recovered.weekly_entries_count == 1
    assert rm_recovered.equity_usdt == pytest.approx(16.0 - 0.18, rel=1e-5)

    # Attempt new position on same day
    can_open, reason = rm_recovered.can_open_new_position(ts_now)
    assert can_open is False
    assert "Daily loss limit" in (reason or "") and "reached" in (reason or "")


def test_risk_recovery_restart_next_day_same_week_loss_0_50(tmp_path: Path):
    """Scenario: Loss $0.50 on Monday, restart on Tuesday (next day, same week).
    Proves that daily loss meter resets to $0.00 on the new UTC day,
    while weekly loss meter ($0.50) is retained from SQLite.
    Dynamic risk budget adjusts to min(0.09, 0.18, 0.54 - 0.50 = 0.04) = $0.04 USDT.
    """
    db = DatabaseManager(tmp_path / "test_rec_050.db")
    journal = JournalRepository(db)

    # Monday 14:00 UTC
    dt_entry = datetime(2026, 10, 19, 14, 0, 0, tzinfo=timezone.utc)
    ts_entry = int(dt_entry.timestamp() * 1000)
    dt_exit = datetime(2026, 10, 19, 15, 0, 0, tzinfo=timezone.utc)
    ts_exit = int(dt_exit.timestamp() * 1000)

    trade = TradeRecord(
        trade_id="TR_REC_050",
        symbol="BTCUSDT",
        direction=Direction.SHORT,
        entry_timestamp_ms=ts_entry,
        entry_price=68000.0,
        stop_loss=69000.0,
        take_profit=66000.0,
        quantity=0.001,
        notional_usdt=68.0,
        exit_timestamp_ms=ts_exit,
        exit_price=68500.0,
        pnl_gross=-0.45,
        pnl_net=-0.50,
        fee_paid=0.03,
        slippage_paid=0.02,
        exit_reason="STOP_LOSS",
        is_open=False,
    )
    journal.save_trade(trade)

    # Restart on Tuesday 09:00 UTC
    dt_tuesday = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)
    ts_tuesday = int(dt_tuesday.timestamp() * 1000)

    rm_recovered = RiskManager(
        initial_equity_usdt=16.0,
        trade_risk_usdt=0.09,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
    )
    rm_recovered.sync_state_from_journal(journal, now_ms=ts_tuesday)

    assert rm_recovered.daily_loss_usdt == 0.0    # Reset at 00:00 UTC
    assert rm_recovered.weekly_loss_usdt == 0.50  # Retained across week
    assert rm_recovered.weekly_entries_count == 1

    # Can initially open position on Tuesday because daily loss is 0.0 and weekly loss < 0.54
    can_open, _ = rm_recovered.can_open_new_position(ts_tuesday)
    assert can_open is True

    # But remaining weekly loss budget is 0.54 - 0.50 = 0.04
    eff_risk = rm_recovered.get_effective_trade_risk(ts_tuesday)
    assert eff_risk == pytest.approx(0.04, rel=1e-5)


def test_risk_recovery_restart_loss_1_50_breaches_weekly(tmp_path: Path):
    """Scenario: Hard SL hit of $1.50 on Monday, restart on Wednesday (same week).
    Proves that catastrophic loss ($1.50) halts trading for the remainder of the week
    even after process restart, because weekly loss ($1.50 >= $0.54) persists.
    """
    db = DatabaseManager(tmp_path / "test_rec_150.db")
    journal = JournalRepository(db)

    # Monday 09:00 UTC
    dt_entry = datetime(2026, 10, 19, 9, 0, 0, tzinfo=timezone.utc)
    ts_entry = int(dt_entry.timestamp() * 1000)
    dt_exit = datetime(2026, 10, 19, 10, 0, 0, tzinfo=timezone.utc)
    ts_exit = int(dt_exit.timestamp() * 1000)

    trade = TradeRecord(
        trade_id="TR_REC_150",
        symbol="ETHUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=ts_entry,
        entry_price=3500.0,
        stop_loss=3412.5,
        take_profit=3675.0,
        quantity=0.017,
        notional_usdt=59.5,
        exit_timestamp_ms=ts_exit,
        exit_price=3412.5,
        pnl_gross=-1.4875,
        pnl_net=-1.50,
        fee_paid=0.0075,
        slippage_paid=0.005,
        exit_reason="STOP_LOSS",
        is_open=False,
    )
    journal.save_trade(trade)

    # Restart on Wednesday 15:00 UTC
    dt_wednesday = datetime(2026, 10, 21, 15, 0, 0, tzinfo=timezone.utc)
    ts_wednesday = int(dt_wednesday.timestamp() * 1000)

    rm_recovered = RiskManager(
        initial_equity_usdt=16.0,
        trade_risk_usdt=0.09,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
    )
    rm_recovered.sync_state_from_journal(journal, now_ms=ts_wednesday)

    assert rm_recovered.daily_loss_usdt == 0.0
    assert rm_recovered.weekly_loss_usdt == 1.50
    assert rm_recovered.weekly_entries_count == 1

    can_open, reason = rm_recovered.can_open_new_position(ts_wednesday)
    assert can_open is False
    assert "Weekly loss limit" in (reason or "") and "reached" in (reason or "")
    assert rm_recovered.get_effective_trade_risk(ts_wednesday) == 0.0


def test_risk_recovery_calendar_boundaries_utc():
    """Scenario: Verification of 00:00 UTC daily reset and Monday 00:00 UTC weekly reset.
    Proves that:
    1. Daily loss resets cleanly across UTC midnight.
    2. Weekly loss resets cleanly across Monday 00:00 UTC boundary.
    """
    rm = RiskManager(
        initial_equity_usdt=16.0,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
    )
    # Sunday 2026-10-25 23:59:59 UTC
    dt_sun = datetime(2026, 10, 25, 23, 59, 59, tzinfo=timezone.utc)
    ts_sun = int(dt_sun.timestamp() * 1000)
    rm._sync_calendar_buckets(ts_sun)
    rm.daily_loss_usdt = 0.15
    rm.weekly_loss_usdt = 0.45
    rm.weekly_entries_count = 2
    assert rm.daily_loss_usdt == 0.15
    assert rm.weekly_loss_usdt == 0.45

    # Monday 2026-10-26 00:00:01 UTC (New Day and New Week)
    dt_mon = datetime(2026, 10, 26, 0, 0, 1, tzinfo=timezone.utc)
    ts_mon = int(dt_mon.timestamp() * 1000)
    rm._sync_calendar_buckets(ts_mon)
    assert rm.daily_loss_usdt == 0.0
    assert rm.weekly_loss_usdt == 0.0
    assert rm.weekly_entries_count == 0


def test_risk_recovery_restart_with_open_position(tmp_path: Path):
    """Scenario: Restart while a position is still open.
    Proves that PaperExecutionEngine and RiskManager correctly restore the open position,
    preventing any new entry from breaching the max 1 open position limit.
    """
    db = DatabaseManager(tmp_path / "test_rec_open.db")
    journal = JournalRepository(db)

    dt_entry = datetime(2026, 10, 19, 10, 0, 0, tzinfo=timezone.utc)
    ts_entry = int(dt_entry.timestamp() * 1000)

    open_trade = TradeRecord(
        trade_id="TR_OPEN_01",
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=ts_entry,
        entry_price=65000.0,
        stop_loss=64000.0,
        take_profit=67000.0,
        quantity=0.001,
        notional_usdt=65.0,
        exit_timestamp_ms=None,
        is_open=True,
    )
    journal.save_trade(open_trade)

    rm_recovered = RiskManager(initial_equity_usdt=16.0, max_open_positions=1)
    engine = PaperExecutionEngine(
        db=db,
        journal=journal,
        risk_manager=rm_recovered,
        config=PaperExecutionConfig(enabled=True),
    )
    engine.recover_state()

    assert "BTCUSDT" in engine.open_positions
    assert len(rm_recovered.open_trades) == 1
    assert rm_recovered.open_trades[0].trade_id == "TR_OPEN_01"

    # Verify opening a second position is strictly blocked
    can_open, reason = rm_recovered.can_open_new_position(ts_entry + 60_000)
    assert can_open is False
    assert "Maximum open position" in (reason or "")


def test_risk_recovery_idempotency_and_fee_slippage(tmp_path: Path):
    """Scenario: Repeated sync_state_from_journal calls and fee/slippage inclusion.
    Proves:
    1. RiskManager sync is fully idempotent (no double counting).
    2. Realized losses accurately include maker/taker fees and slippage from pnl_net.
    """
    db = DatabaseManager(tmp_path / "test_rec_idemp.db")
    journal = JournalRepository(db)

    dt_exit = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
    ts_exit = int(dt_exit.timestamp() * 1000)

    # Gross PnL was -$0.10, but fee ($0.04) and slippage ($0.04) pushed net loss to -$0.18
    trade = TradeRecord(
        trade_id="TR_FEE_SLIP",
        symbol="AVAXUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=ts_exit - 3600_000,
        entry_price=20.0,
        stop_loss=19.5,
        take_profit=21.0,
        quantity=3.0,
        notional_usdt=60.0,
        exit_timestamp_ms=ts_exit,
        exit_price=19.967,
        pnl_gross=-0.10,
        pnl_net=-0.18,
        fee_paid=0.04,
        slippage_paid=0.04,
        exit_reason="STOP_LOSS",
        is_open=False,
    )
    journal.save_trade(trade)

    rm = RiskManager(initial_equity_usdt=16.0)
    # Sync 3 times sequentially
    rm.sync_state_from_journal(journal, now_ms=ts_exit)
    rm.sync_state_from_journal(journal, now_ms=ts_exit)
    rm.sync_state_from_journal(journal, now_ms=ts_exit)

    # Loss is exactly $0.18 (not 3 x $0.18 = $0.54)
    assert rm.daily_loss_usdt == 0.18
    assert rm.weekly_loss_usdt == 0.18
    assert rm.weekly_entries_count == 1
    assert rm.equity_usdt == pytest.approx(15.82, rel=1e-5)


# ==============================================================================
# TASK B INTEGRATION TESTS: Empty Scan Observability & Rejection Logging
# ==============================================================================

def test_scan_observability_empty_cycle_no_trade(tmp_path: Path):
    """Scenario: Complete empty scan cycle across symbols.
    Proves that when market conditions produce 0 candidates (NO_TRADE):
    1. SCAN_STARTED is explicitly recorded.
    2. SETUP_REJECTED is recorded per symbol with NO_SMC_CONFLUENCE.
    3. SCAN_COMPLETED is explicitly recorded with reason_code="NO_TRADE" and status="SUCCESS".
    4. Scanner state is healthy IDLE (not ERROR or STALE).
    """
    db = DatabaseManager(tmp_path / "test_scan_obs.db")
    journal = JournalRepository(db)
    rm = RiskManager(initial_equity_usdt=16.0)

    now_ms = 1760000000000
    # Save 45 fresh 1H candles for BTCUSDT and ETHUSDT
    for sym in ["BTCUSDT", "ETHUSDT"]:
        candles = [
            _make_candle(sym, now_ms - (45 - i) * 3600_000, 100.0, 101.0, 99.0, 100.5)
            for i in range(45)
        ]
        db.insert_candles(candles)

    mock_exchange = MagicMock()
    mock_sync = MagicMock()
    mock_gen = MagicMock()
    mock_gen.generate_candidate.return_value = None  # No setup detected

    runner = ScannerRunner(
        exchange=mock_exchange,
        db=db,
        journal=journal,
        risk_manager=rm,
        generator=mock_gen,
        synchronizer=mock_sync,
        specs_map={},
        mode=ExecutionMode.PAPER,
        stale_threshold_hours=12,
    )

    with patch("time.time", return_value=now_ms / 1000.0):
        results = runner.run_scan(symbols=["BTCUSDT", "ETHUSDT"], sync_live=False)

    assert len(results) == 0
    assert runner.scanner_state == "IDLE"
    assert runner.last_scan_status == "SUCCESS"

    events = journal.get_lifecycle_events(limit=100)
    stages = [e["stage"] for e in events]

    assert "SCAN_STARTED" in stages
    assert "SCAN_COMPLETED" in stages
    assert "SETUP_REJECTED" in stages

    # Verify SCAN_COMPLETED details
    completed_events = [e for e in events if e["stage"] == "SCAN_COMPLETED"]
    assert len(completed_events) == 1
    assert completed_events[0]["reason_code"] == "NO_TRADE"
    assert completed_events[0]["metadata"]["candidate_count"] == 0
    assert completed_events[0]["metadata"]["ready_count"] == 0
    assert completed_events[0]["metadata"]["status"] == "SUCCESS"

    # Verify SETUP_REJECTED details
    rej_events = [e for e in events if e["stage"] == "SETUP_REJECTED"]
    assert len(rej_events) == 2
    for rev in rej_events:
        assert rev["reason_code"] == "NO_SMC_CONFLUENCE"


def test_scan_observability_stale_data_rejection(tmp_path: Path):
    """Scenario: Candle data exceeds stale threshold (12 hours).
    Proves that scanner detects stale market data, marks candidate STALE,
    logs SETUP_REJECTED with reason_code="REJECTED_STALE", and flags scanner_state="STALE".
    """
    db = DatabaseManager(tmp_path / "test_scan_stale.db")
    journal = JournalRepository(db)
    rm = RiskManager(initial_equity_usdt=16.0)

    now_ms = 1760000000000
    # Last candle closed 15 hours ago (> 12h threshold)
    stale_ts = now_ms - (15 * 3600_000)
    candles = [
        _make_candle("BTCUSDT", stale_ts - (45 - i) * 3600_000, 100.0, 101.0, 99.0, 100.5)
        for i in range(45)
    ]
    db.insert_candles(candles)

    specs = SymbolSpecs(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        contract_type="PERPETUAL",
        status="TRADING",
        tick_size=0.1,
        price_precision=1,
        step_size=0.001,
        quantity_precision=3,
        min_qty=0.001,
        max_qty=1000.0,
        min_notional=5.0,
        maint_margin_percent=0.01,
    )
    mock_exchange = MagicMock()
    mock_exchange.get_symbol_specs.return_value = specs
    mock_sync = MagicMock()
    cand = _make_candidate(symbol="BTCUSDT", ts_ms=stale_ts)
    mock_gen = MagicMock()
    mock_gen.generate_candidate.return_value = cand

    runner = ScannerRunner(
        exchange=mock_exchange,
        db=db,
        journal=journal,
        risk_manager=rm,
        generator=mock_gen,
        synchronizer=mock_sync,
        specs_map={"BTCUSDT": specs},
        mode=ExecutionMode.PAPER,
        stale_threshold_hours=12,
    )

    with patch("time.time", return_value=now_ms / 1000.0):
        results = runner.run_scan(symbols=["BTCUSDT"], sync_live=False)

    assert len(results) == 1
    assert results[0].status == CandidateStatus.STALE
    assert runner.scanner_state == "STALE"

    events = journal.get_lifecycle_events(symbol="BTCUSDT")
    rej_events = [e for e in events if e["stage"] == "SETUP_REJECTED"]
    assert len(rej_events) >= 1
    assert rej_events[-1]["reason_code"] == "REJECTED_STALE"


def test_scan_observability_fatal_scan_failure(tmp_path: Path):
    """Scenario: Top-level unexpected exception in scanner cycle.
    Proves that SCAN_FAILED lifecycle event is recorded with reason_code="SCAN_EXCEPTION",
    scanner_state is set to "ERROR", and exception is re-raised safely.
    """
    db = DatabaseManager(tmp_path / "test_scan_fail.db")
    journal = JournalRepository(db)
    rm = RiskManager(initial_equity_usdt=16.0)

    runner = ScannerRunner(
        exchange=MagicMock(),
        db=db,
        journal=journal,
        risk_manager=rm,
        specs_map={},
        mode=ExecutionMode.PAPER,
    )

    with patch("src.scheduler.runner.rank_candidates", side_effect=RuntimeError("Database corruption crash")):
        with pytest.raises(RuntimeError):
            runner.run_scan(symbols=["BTCUSDT"], sync_live=False)

    assert runner.scanner_state == "ERROR"
    assert runner.last_scan_status == "FAILED"

    events = journal.get_lifecycle_events()
    failed_events = [e for e in events if e["stage"] == "SCAN_FAILED"]
    assert len(failed_events) == 1
    assert failed_events[0]["reason_code"] == "SCAN_EXCEPTION"
    assert "Database corruption crash" in failed_events[0]["reason_detail"]
