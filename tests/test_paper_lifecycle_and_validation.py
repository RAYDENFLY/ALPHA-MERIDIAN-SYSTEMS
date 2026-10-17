"""Tests for Phase 5–7 Profitability Research, Paper Lifecycle State Machine, and Forward Validation Cohort."""

from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

from src.backtest.engine import BacktestEngine
from src.data.database import DatabaseManager
from src.core.types import (
    CandidateStatus,
    Candle,
    Direction,
    EntryCategory,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
)
from src.data.sync import CandleSynchronizer
from src.execution.paper_engine import PaperExecutionEngine
from config.settings import PaperExecutionConfig as PaperConfig
from src.execution.types import PaperLifecycleStage
from src.journal.repository import JournalRepository
from src.risk.manager import RiskManager


def _make_candle(symbol: str, ts_ms: int, open_p: float, high_p: float, low_p: float, close_p: float) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe="1h",
        timestamp_ms=ts_ms,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=1000.0,
        close_time_ms=ts_ms + 3600_000 - 1,
    )


def _make_ready_candidate(
    symbol: str = "SOLUSDT",
    ts_ms: int = 1760000000000,
    direction: Direction = Direction.LONG,
    entry_price: float = 100.0,
    stop_loss: float = 98.0,
    tp1: float = 103.5,
    tp2: float = 106.0,
) -> SetupCandidate:
    assessment = RiskAssessment(
        symbol=symbol,
        entry_price=entry_price,
        stop_loss=stop_loss,
        stop_distance_pct=abs(entry_price - stop_loss) / entry_price,
        risk_budget_usdt=1.50,
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
        gross_reward_to_risk=1.75,
        net_reward_to_risk=1.60,
        invalidation_price=stop_loss,
        invalidation_reason="1H structural swing low",
        smc_evidence=["1H FVG", "1H Liquidity Sweep"],
        snr_evidence=["Support Retest"],
        supporting_reasons=["4H trend aligned"],
        opposing_reasons=[],
        ml_probability=0.52,
        calibration_status="VALIDATED",
        eval_sample_count=50,
        status=CandidateStatus.READY,
        entry_category=EntryCategory.PULLBACK,
        rejection_reasons=[],
        expires_at_ms=ts_ms + (12 * 3600 * 1000),
        risk_assessment=assessment,
    )


def test_full_10_stage_paper_lifecycle_persisted_and_finalized(tmp_path: Path):
    """Verify the explicit 10-stage lifecycle state machine from SCAN through JOURNAL_FINALIZED."""
    db = DatabaseManager(tmp_path / "test_lifecycle.db")
    journal = JournalRepository(db)
    risk_mgr = RiskManager(
        initial_equity_usdt=16.0,
        trade_risk_usdt=0.08,
        max_open_positions=1,
        max_entries_per_week=3,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
    )
    paper_cfg = PaperConfig(
        enabled=True,
        conservative_fill_buffer_pct=0.0005,
        pending_expiry_bars=12,
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
        slippage_rate=0.0005,
        enable_tp1_trailing_stop=False,
    )
    engine = PaperExecutionEngine(
        db=db,
        journal=journal,
        risk_manager=risk_mgr,
        config=paper_cfg,
    )

    t0 = 1760000000000
    cand = _make_ready_candidate(symbol="SOLUSDT", ts_ms=t0, entry_price=100.0, stop_loss=98.0, tp1=104.0, tp2=None)
    journal.save_setup(cand)

    # Stage 1–4: Record scanner stages (SCAN -> SETUP_DETECTED -> RISK_VALIDATION -> ELIGIBLE)
    journal.save_lifecycle_event(cand.candidate_id, "SOLUSDT", PaperLifecycleStage.SCAN.value, t0, "SCAN_COMPLETED")
    journal.save_lifecycle_event(cand.candidate_id, "SOLUSDT", PaperLifecycleStage.SETUP_DETECTED.value, t0, "SMC_SNR_GENERATED")
    journal.save_lifecycle_event(cand.candidate_id, "SOLUSDT", PaperLifecycleStage.RISK_VALIDATION.value, t0, "FEASIBLE")
    journal.save_lifecycle_event(cand.candidate_id, "SOLUSDT", PaperLifecycleStage.ELIGIBLE.value, t0, "ELIGIBLE_READY")

    # Stage 5: PAPER_ORDER_CREATED
    order = engine.submit_candidate(cand, now_ms=t0 + 60_000)
    assert order is not None

    # Stage 6 & 7: ENTRY_FILLED -> POSITION_OPEN on next closed bar (t0 + 1h)
    c1 = _make_candle("SOLUSDT", t0 + 3600_000, 100.5, 101.0, 99.80, 100.2)
    filled = engine.evaluate_pending_orders({"SOLUSDT": [c1]}, now_ms=t0 + 3660_000)
    assert len(filled) == 1
    assert "SOLUSDT" in engine.open_positions

    # Stage 8, 9, 10: EXIT_TRIGGERED -> POSITION_CLOSED -> JOURNAL_FINALIZED on bar t0 + 2h hitting TP1
    c2 = _make_candle("SOLUSDT", t0 + 7200_000, 100.2, 104.5, 100.1, 104.2)
    closed = engine.evaluate_open_positions({"SOLUSDT": [c1, c2]}, now_ms=t0 + 7260_000)
    assert len(closed) == 1
    assert closed[0].exit_reason == "TAKE_PROFIT"
    assert closed[0].is_open is False

    events = journal.get_lifecycle_events(candidate_id=cand.candidate_id)
    stages = [e["stage"] for e in events]
    assert stages == [
        "SCAN",
        "SETUP_DETECTED",
        "RISK_VALIDATION",
        "ELIGIBLE",
        "PAPER_ORDER_CREATED",
        "ENTRY_FILLED",
        "POSITION_OPEN",
        "EXIT_TRIGGERED",
        "POSITION_CLOSED",
        "JOURNAL_FINALIZED",
    ]


def test_forward_validation_cohort_classification_states(tmp_path: Path):
    """Verify truthful forward paper validation cohort status transitions."""
    db = DatabaseManager(tmp_path / "test_cohort.db")
    journal = JournalRepository(db)

    # 1. Zero completed trades -> UNPROVEN_NO_COMPLETED_FORWARD_TRADES
    cohort0 = journal.compute_forward_validation_cohort(min_sample_size=30)
    assert cohort0["validation_status"] == "UNPROVEN_NO_COMPLETED_FORWARD_TRADES"
    assert cohort0["closed_trades_count"] == 0
    assert cohort0["is_sample_sufficient"] is False

    # 2. Fewer than 30 completed trades -> INSUFFICIENT_FORWARD_EVIDENCE
    risk_mgr = RiskManager(initial_equity_usdt=16.0)
    engine = PaperExecutionEngine(
        db=db,
        journal=journal,
        risk_manager=risk_mgr,
        config=PaperConfig(enabled=True, enable_tp1_trailing_stop=False),
    )
    t0 = 1760000000000
    cand = _make_ready_candidate(symbol="SOLUSDT", ts_ms=t0, entry_price=100.0, stop_loss=98.0, tp1=104.0, tp2=None)
    engine.submit_candidate(cand, now_ms=t0 + 1000)
    c1 = _make_candle("SOLUSDT", t0 + 3600_000, 100.5, 101.0, 99.8, 100.2)
    c2 = _make_candle("SOLUSDT", t0 + 7200_000, 100.2, 104.5, 100.1, 104.2)
    engine.evaluate_pending_orders({"SOLUSDT": [c1]}, now_ms=t0 + 3660_000)
    engine.evaluate_open_positions({"SOLUSDT": [c1, c2]}, now_ms=t0 + 7260_000)

    cohort1 = journal.compute_forward_validation_cohort(min_sample_size=30)
    assert cohort1["closed_trades_count"] == 1
    assert cohort1["validation_status"] == "INSUFFICIENT_FORWARD_EVIDENCE"
    assert cohort1["net_expectancy_per_trade"] > 0

    # 3. Sufficient sample with positive expectancy -> PRELIMINARY_POSITIVE_NOT_VALIDATED
    cohort_pos = journal.compute_forward_validation_cohort(min_sample_size=1)
    assert cohort_pos["is_sample_sufficient"] is True
    assert cohort_pos["validation_status"] == "PRELIMINARY_POSITIVE_NOT_VALIDATED"


def test_short_tp_slippage_adversity_in_backtest_engine(tmp_path: Path):
    """Verify BT-01 fix: SHORT take-profit fill applies adverse upward slippage (1.0 + slippage), never favorable."""
    from src.core.types import TradeRecord
    db = DatabaseManager(tmp_path / "test_bt_slip.db")
    bt = BacktestEngine(db=db, symbol_specs_map={}, slippage=0.0005)

    # Simulate a SHORT position entry at 100.0 with TP at 95.0
    t0 = 1760000000000
    trade = TradeRecord(
        trade_id="BT_SHORT_1",
        symbol="ETHUSDT",
        direction=Direction.SHORT,
        entry_timestamp_ms=t0,
        entry_price=100.0,
        stop_loss=102.0,
        take_profit=95.0,
        quantity=0.60,
        notional_usdt=60.0,
        fee_paid=0.03,
        funding_paid=0.0,
        is_open=True,
        setup_id="CAND_ETH_1",
        entry_category=EntryCategory.PULLBACK,
    )
    c_tp = _make_candle("ETHUSDT", t0 + 3600_000, 99.0, 99.5, 94.5, 95.0)
    closed = bt._process_open_trade_bar(trade, c_tp)
    assert closed is True
    assert trade.exit_reason == "TP"
    # Adverse slippage on SHORT exit (buying back) means exit_price >= tp1 (95.0 * 1.0005 = 95.0475)
    assert trade.exit_price == pytest.approx(95.0 * 1.0005, rel=1e-6)
    assert trade.entry_category == EntryCategory.PULLBACK
    assert trade.setup_id == "CAND_ETH_1"


def test_candle_synchronizer_paginates_multi_day_gap(tmp_path: Path):
    """Verify CandleSynchronizer fetches multiple pages forward when local DB has a >50 bar gap."""
    db = DatabaseManager(tmp_path / "test_sync.db")
    base_ts = 1760000000000
    hour_ms = 3600 * 1000
    # Seed 1 initial candle 120 hours ago
    db.insert_candles([_make_candle("SOLUSDT", base_ts, 100.0, 101.0, 99.0, 100.0)])

    call_log: List[int] = []

    def fake_get_klines(symbol: str, timeframe: str, start_time_ms=None, end_time_ms=None, limit: int = 500):
        call_log.append(start_time_ms)
        # Return up to 50 bars starting from start_time_ms
        bars = []
        start_idx = max(0, int((start_time_ms - base_ts) // hour_ms))
        for i in range(start_idx, min(start_idx + 50, 121)):
            ts = base_ts + i * hour_ms
            bars.append(_make_candle(symbol, ts, 100.0, 101.0, 99.0, 100.0))
        return bars

    mock_exchange = MagicMock()
    mock_exchange.get_klines.side_effect = fake_get_klines

    sync = CandleSynchronizer(exchange=mock_exchange, db=db)
    sync.sync_symbol("SOLUSDT", "1h", now_ms=base_ts + 120 * hour_ms)

    latest = db.get_latest_candle("SOLUSDT", "1h")
    assert latest is not None
    # Should have paginated across all 120 hours up to base_ts + 119*hour_ms (last closed bar)
    assert latest.timestamp_ms >= base_ts + 119 * hour_ms
    assert len(call_log) >= 3

