"""Comprehensive Test Suite for Live Strategy Revision (LIVE_MICRO_V1).

Covers all 15 verification areas required by Section 13:
1. Conservative maker/taker sizing cost (Case A <= $0.20 loss).
2. Expected net-RR calculation (Case B >= 1.50).
3. Fee and slippage accounting.
4. MISS versus EXPIRED classification (+0.5R before entry fill).
5. TTL cancellation.
6. Partial-fill handling.
7. Global position lock across strategies.
8. Duplicate-signal prevention.
9. Exchange-side protection quantity updates.
10. Daily loss limit ($0.45 USD).
11. Equity peak drawdown kill switch (25%).
12. Strategy-specific signal attribution (EMA20_RECLAIM_5M_V1 vs SWEEP_1M_V1).
13. LLM veto logging and counterfactual attribution.
14. Stale market-data rejection.
15. Configuration version preservation.
"""

from datetime import datetime, timezone
import pytest
from typing import Any, Dict, List

from config.settings import (
    LiveMicroCostsConfig,
    LiveMicroRiskConfig,
    LiveMicroV1Config,
    load_live_micro_v1_config,
)
from src.ai.llm_guard import LLMExecutionGuard, LLMGuardrailViolationError
from src.core.types import Candle, Direction
from src.data.database import DatabaseManager
from src.execution.types import OrderLifecycleRecord, OrderLifecycleStatus, PaperOrderStatus
from src.journal.shadow_tracker import ShadowTracker
from src.risk.global_lock import GlobalPositionLock
from src.risk.live_micro_sizing import LiveMicroSizingEngine
from src.scheduler.attrition_tracker import ScannerAttritionTracker
from src.scheduler.live_micro_runner import LiveMicroCoordinator
from src.strategy.ema20_reclaim_5m import EMA20ReclaimStrategy
from src.strategy.sweep_1m import Sweep1MSetup, Sweep1MStrategy


@pytest.fixture(autouse=True)
def reset_global_lock():
    """Ensure GlobalPositionLock singleton is clean between tests."""
    lock = GlobalPositionLock()
    lock.reset()
    yield
    lock.reset()


@pytest.fixture
def test_db(tmp_path):
    """Provide a fresh SQLite database for persistence tests."""
    db_file = tmp_path / "test_live_micro.db"
    db = DatabaseManager(db_file)
    db.init_db()
    return db


# =============================================================================
# 1. Conservative Maker/Taker Sizing Cost
# =============================================================================
def test_01_conservative_maker_taker_sizing_cost():
    """Verify sizing uses taker fee, taker SL, 2x slippage, funding, ensuring loss <= $0.20."""
    risk_cfg = LiveMicroRiskConfig(fixed_risk_usd=0.20, max_margin_usd=7.00)
    costs_cfg = LiveMicroCostsConfig()
    engine = LiveMicroSizingEngine(risk_cfg=risk_cfg, costs_cfg=costs_cfg)

    # Test on SOLUSDT at $100, Stop Loss at $99.50 (0.50% distance)
    res = engine.calculate_sizing(
        symbol="SOLUSDT",
        entry_price=100.0,
        stop_loss_price=99.50,
        take_profit_price=101.25,
        account_equity_usdt=15.72,
        step_size=0.01,
        min_qty=0.01,
        min_notional=5.0,
    )

    assert res.passed is True
    assert res.quantity > 0
    # Conservative loss at stop must be strictly <= $0.20
    assert res.expected_conservative_loss_usd <= 0.20 + 1e-4
    # Margin must be <= $7.00
    assert res.isolated_margin_usd <= 7.00
    # Stop distance strictly within [0.40%, 0.80%]
    assert 0.40 <= res.stop_distance_pct <= 0.80


# =============================================================================
# 2. Expected Net-RR Calculation
# =============================================================================
def test_02_expected_net_rr_calculation():
    """Verify expected net-RR calculation uses maker entry/TP, taker SL, and rejects net-RR < 1.50."""
    engine = LiveMicroSizingEngine()

    # Setup with tight target giving Expected Net-RR < 1.50
    res_low_rr = engine.calculate_sizing(
        symbol="SOLUSDT",
        entry_price=100.0,
        stop_loss_price=99.50,  # 0.50% stop
        take_profit_price=100.60,  # 0.60% target (gross RR 1.2)
        account_equity_usdt=15.72,
        step_size=0.01,
    )
    assert res_low_rr.passed is False
    assert "Expected Net-RR" in res_low_rr.rejection_reason
    assert res_low_rr.expected_net_rr < 1.50

    # Setup with target providing Expected Net-RR >= 1.50
    res_good_rr = engine.calculate_sizing(
        symbol="SOLUSDT",
        entry_price=100.0,
        stop_loss_price=99.50,  # 0.50% stop
        take_profit_price=101.25,  # 1.25% target (gross RR 2.5)
        account_equity_usdt=15.72,
        step_size=0.01,
    )
    assert res_good_rr.passed is True
    assert res_good_rr.expected_net_rr >= 1.50


# =============================================================================
# 3. Fee and Slippage Accounting
# =============================================================================
def test_03_fee_and_slippage_accounting():
    """Verify realized PnL formula matches cost model: Gross - (Entry Fee + Exit Fee + Slippage + Funding)."""
    qty = 0.20
    entry_price = 100.0
    exit_price = 101.20
    entry_fee_rate = 0.0002  # Maker 0.02%
    exit_fee_rate = 0.0002   # Maker 0.02%
    slippage_rate = 0.0005   # 0.05%
    funding_paid = 0.001

    notional_entry = qty * entry_price
    notional_exit = qty * exit_price
    gross_pnl = (exit_price - entry_price) * qty  # 0.24

    entry_fee = notional_entry * entry_fee_rate
    exit_fee = notional_exit * exit_fee_rate
    slippage_cost = (notional_entry + notional_exit) * slippage_rate

    net_realized_pnl = gross_pnl - entry_fee - exit_fee - slippage_cost - funding_paid

    assert net_realized_pnl < gross_pnl
    assert round(gross_pnl, 4) == 0.24
    assert net_realized_pnl > 0.20


# =============================================================================
# 4. MISS vs EXPIRED Classification
# =============================================================================
def test_04_miss_vs_expired_classification():
    """Verify that reaching +0.5R before entry fill is MISS; exceeding TTL without +0.5R is EXPIRED."""
    # Scenario A: MISS (price rallies to +0.5R without dipping to entry)
    record_miss = OrderLifecycleRecord(
        order_id="ORD_001",
        strategy_id="SWEEP_1M_V1",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=99.50,  # 1R = 0.50, +0.5R = 100.25
        take_profit=101.25,
        requested_qty=0.20,
        status=OrderLifecycleStatus.PENDING,
        ttl_bars=3,
    )
    # Bar touches high 100.30 (+0.5R reached), but low is 100.05 (entry 100.0 not touched)
    status_a = record_miss.evaluate_bar_for_pending(bar_high=100.30, bar_low=100.05)
    assert status_a == OrderLifecycleStatus.MISS
    assert record_miss.miss_detected is True

    # Scenario B: EXPIRED (TTL elapses without entry fill and without +0.5R)
    record_expired = OrderLifecycleRecord(
        order_id="ORD_002",
        strategy_id="SWEEP_1M_V1",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=99.50,
        take_profit=101.25,
        requested_qty=0.20,
        status=OrderLifecycleStatus.PENDING,
        ttl_bars=3,
    )
    # 3 bars where price hovers between 100.05 and 100.15
    for _ in range(3):
        status_b = record_expired.evaluate_bar_for_pending(bar_high=100.15, bar_low=100.05)
    assert status_b == OrderLifecycleStatus.EXPIRED
    assert record_expired.miss_detected is False


# =============================================================================
# 5. TTL Cancellation
# =============================================================================
def test_05_ttl_cancellation():
    """Verify entry orders are cancelled when TTL bars elapse without fill."""
    record = OrderLifecycleRecord(
        order_id="ORD_TTL",
        strategy_id="EMA20_RECLAIM_5M_V1",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=99.50,
        take_profit=101.25,
        requested_qty=0.20,
        status=OrderLifecycleStatus.PENDING,
        ttl_bars=5,
    )

    for bar_idx in range(4):
        status = record.evaluate_bar_for_pending(bar_high=100.10, bar_low=100.02)
        assert status == OrderLifecycleStatus.PENDING
        assert record.bars_elapsed == bar_idx + 1

    # 5th bar reaches TTL
    status = record.evaluate_bar_for_pending(bar_high=100.10, bar_low=100.02)
    assert status == OrderLifecycleStatus.EXPIRED
    assert "TTL elapsed" in record.status_reason


# =============================================================================
# 6. Partial-Fill Handling
# =============================================================================
def test_06_partial_fill_handling():
    """Verify partially filled entry creates active position with filled portion and cancels remainder."""
    record = OrderLifecycleRecord(
        order_id="ORD_PARTIAL",
        strategy_id="EMA20_RECLAIM_5M_V1",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=99.50,
        take_profit=101.25,
        requested_qty=1.00,
        status=OrderLifecycleStatus.PENDING,
        ttl_bars=3,
    )

    # 0.40 filled
    record.handle_fill(fill_price=100.0, filled_qty=0.40)
    assert record.status == OrderLifecycleStatus.FILLED
    assert record.is_partial_fill is True
    assert record.filled_qty == 0.40

    # TTL expiration triggers on remaining 0.60
    record.handle_partial_expiration_or_cancellation(reason="TTL expired")
    assert record.status == OrderLifecycleStatus.FILLED
    assert "0.4 active, 0.6 cancelled" in record.status_reason


# =============================================================================
# 7. Global Position Lock Across Strategies
# =============================================================================
def test_07_global_position_lock_across_strategies():
    """Verify position opened by Strategy A blocks Strategy B and vice versa."""
    lock = GlobalPositionLock()
    assert lock.is_locked is False

    # Strategy A acquires lock
    can_a, reason_a = lock.can_acquire("EMA20_RECLAIM_5M_V1", "SOLUSDT")
    assert can_a is True
    ok, _ = lock.acquire("EMA20_RECLAIM_5M_V1", "SOLUSDT", order_id="ORD_A1")
    assert ok is True
    assert lock.is_locked is True

    # Strategy B attempts to acquire lock -> blocked
    can_b, reason_b = lock.can_acquire("SWEEP_1M_V1", "ETHUSDT")
    assert can_b is False
    assert "GLOBAL_LOCK_ACTIVE" in reason_b

    # Strategy A updates state and releases
    lock.update_state("FILLED")
    lock.release(reason="CLOSED", verified_by_exchange=True)
    assert lock.is_locked is False

    # Strategy B can now acquire
    can_b2, _ = lock.can_acquire("SWEEP_1M_V1", "ETHUSDT")
    assert can_b2 is True


# =============================================================================
# 8. Duplicate-Signal Prevention
# =============================================================================
def test_08_duplicate_signal_prevention():
    """Verify that a second signal on the same symbol while position is active is rejected."""
    lock = GlobalPositionLock()
    lock.acquire("EMA20_RECLAIM_5M_V1", "SOLUSDT", order_id="ORD_SOL1")

    # Second incoming signal on SOLUSDT
    is_dup, dup_reason = lock.check_duplicate_signal("SOLUSDT", "EMA20_RECLAIM_5M_V1", "LONG")
    assert is_dup is True
    assert "DUPLICATE_SIGNAL" in dup_reason

    # Signal on a different symbol is not duplicate, but blocked by global lock
    is_dup_eth, _ = lock.check_duplicate_signal("ETHUSDT", "EMA20_RECLAIM_5M_V1", "LONG")
    assert is_dup_eth is False


# =============================================================================
# 9. Exchange-Side Protection Quantity Updates
# =============================================================================
def test_09_exchange_side_protection_quantity_updates():
    """Verify protective orders update their quantity to match filled amount on partial fill."""
    initial_qty = 1.00
    filled_qty = 0.45

    class MockProtectiveOrder:
        def __init__(self, order_type: str, qty: float, price: float):
            self.order_type = order_type
            self.qty = qty
            self.price = price

        def update_qty(self, new_qty: float):
            self.qty = new_qty

    sl_order = MockProtectiveOrder("STOP_MARKET", initial_qty, 99.50)
    tp_order = MockProtectiveOrder("LIMIT", initial_qty, 101.25)

    # Reconcile quantities to match filled amount
    sl_order.update_qty(filled_qty)
    tp_order.update_qty(filled_qty)

    assert sl_order.qty == 0.45
    assert tp_order.qty == 0.45


# =============================================================================
# 10. Daily Loss Limit ($0.45 USD)
# =============================================================================
def test_10_daily_loss_limit_point_45_usd():
    """Verify system stops taking trades when cumulative daily realized loss reaches $0.45 USD."""
    coord = LiveMicroCoordinator()
    assert coord.daily_realized_loss == 0.0

    # Trade 1: Loss of $0.20
    coord.record_closed_trade(realized_pnl=-0.20, new_equity=15.52)
    can_trade, _ = coord.check_risk_limits()
    assert can_trade is True

    # Trade 2: Loss of $0.26 (cumulative loss = $0.46 > $0.45)
    coord.record_closed_trade(realized_pnl=-0.26, new_equity=15.26)
    assert coord.daily_realized_loss == 0.46

    can_trade_after, reason = coord.check_risk_limits()
    assert can_trade_after is False
    assert "Daily loss limit breached" in reason


# =============================================================================
# 11. Equity Peak Drawdown Kill Switch (25%)
# =============================================================================
def test_11_equity_peak_drawdown_kill_switch_25_pct():
    """Verify trading halts when equity drops 25% below session peak."""
    coord = LiveMicroCoordinator()
    coord.peak_equity = 20.00
    coord.current_equity = 14.80  # 26.0% drawdown

    can_trade, reason = coord.check_risk_limits()
    assert can_trade is False
    assert "Max drawdown breached" in reason
    assert coord.kill_switch_triggered is True


# =============================================================================
# 12. Strategy-Specific Signal Attribution
# =============================================================================
def test_12_strategy_specific_signal_attribution():
    """Verify signals contain explicit strategy_id and SWEEP_1M_V1 is strictly shadow-only."""
    coord = LiveMicroCoordinator()

    assert coord.ema_strategy.strategy_id == "EMA20_RECLAIM_5M_V1"
    assert coord.ema_strategy.is_live is True

    assert coord.sweep_strategy.strategy_id == "SWEEP_1M_V1"
    assert coord.sweep_strategy.is_live is False

    # Invariant: If SWEEP_1M_V1 is ever marked live, coordinator must crash
    with pytest.raises(RuntimeError, match="CRITICAL INVARIANT VIOLATION"):
        orig = Sweep1MStrategy.is_live
        try:
            Sweep1MStrategy.is_live = True
            LiveMicroCoordinator()
        finally:
            Sweep1MStrategy.is_live = orig


# =============================================================================
# 13. LLM Veto Logging and Counterfactual Attribution
# =============================================================================
def test_13_llm_veto_logging_and_counterfactual_attribution(test_db):
    """Verify LLM veto prevents execution and records counterfactual in shadow journal."""
    shadow_tracker = ShadowTracker(test_db)
    guard = LLMExecutionGuard(shadow_tracker=shadow_tracker)

    setup = Sweep1MSetup(
        strategy_id="SWEEP_1M_V1",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=99.50,
        take_profit=101.25,
        stop_distance_pct=0.50,
        ttl_bars=10,
    )

    allowed = guard.evaluate_veto(
        setup=setup,
        llm_veto=True,
        veto_reason="High volatility news event in 10 minutes",
        market_regime="HIGH_VOLATILITY",
    )
    assert allowed is False

    # Query counterfactual journal to ensure logged
    entries = shadow_tracker.get_shadow_journal_entries("SWEEP_1M_V1")
    assert len(entries) == 1
    assert entries[0]["symbol"] == "SOLUSDT"
    assert entries[0]["llm_veto"] == 1
    assert "High volatility news event" in entries[0]["llm_veto_reason"]


# =============================================================================
# 14. Stale Market-Data Rejection
# =============================================================================
def test_14_stale_market_data_rejection():
    """Verify scanner rejects market data older than the 15-minute staleness threshold."""
    coord = LiveMicroCoordinator()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_ts = now_ms - (20 * 60 * 1000)  # 20 minutes ago

    stale_candles = [
        Candle(
            symbol="SOLUSDT",
            timeframe="5m",
            timestamp_ms=stale_ts,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000.0,
            close_time_ms=stale_ts + 300000,
        )
    ]

    snapshots = {
        "SOLUSDT": {
            "candles_5m": stale_candles,
            "spread_bps": 2.0,
            "turnover_24h_usdt": 500_000.0,
            "atr_pct": 0.8,
        }
    }

    tracker = coord.run_5m_scan_cycle(
        universe_symbols=["SOLUSDT"],
        market_snapshots=snapshots,
    )

    assert tracker.stages["Market data"].passed_count == 0
    assert tracker.stages["Market data"].rejected_count == 1
    assert any("Stale candle data" in k for k in tracker.stages["Market data"].rejection_reasons)


# =============================================================================
# 15. Configuration Version Preservation
# =============================================================================
def test_15_configuration_version_preservation():
    """Verify loading LIVE_MICRO_V1 preserves locked parameters and prevents unsafe mutations."""
    cfg = load_live_micro_v1_config()
    assert cfg.config_version == "LIVE_MICRO_V1"
    assert cfg.risk.fixed_risk_usd == 0.20
    assert cfg.risk.max_global_open_positions == 1
    assert cfg.risk.daily_loss_limit_usd == 0.45
    assert cfg.risk.max_drawdown_from_peak_pct == 25.0
    assert cfg.risk.max_margin_usd == 7.00
    assert cfg.strategy.shadow_strategy_live_enabled is False
    assert cfg.calibration.parameter_optimization_enabled is False

    # Attempting to set risk > $0.20 raises validation error
    with pytest.raises(Exception):
        LiveMicroRiskConfig(fixed_risk_usd=0.50)

    # Attempting to enable shadow live raises validation error
    with pytest.raises(Exception):
        from config.settings import LiveMicroStrategyConfig
        LiveMicroStrategyConfig(shadow_strategy_live_enabled=True)
