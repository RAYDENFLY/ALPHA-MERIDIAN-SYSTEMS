"""Comprehensive test suite for Adaptive TP Refresh and Structural Invalidation Monitoring.

Tests:
1. Stale fixed-R TP replaced by valid structural TP (atomic replacement).
2. No replacement when target unchanged or difference below threshold (anti-churn).
3. Invalid targets at or behind mark price rejected.
4. Replacement failure preserves old TP.
5. SL and breakeven orders untouched.
6. Sweep-and-reclaim vs confirmed structural breakdown (closed-candle confirmation).
7. Partial fills, closed positions, duplicate reconciliation, and API failure resilience.
8. No live exchange order changes in diagnostic / dry-run mode.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from src.core.types import Candle, Direction
from src.strategy.sweep_reclaim_15m import (
    compute_dynamic_structural_tp,
    find_prominent_swings,
    DynamicTPResult,
)
from src.scheduler.live_micro_runner import LiveMicroCoordinator
from src.data.database import DatabaseManager


def make_candle(ts: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="BZUSDT",
        timeframe="15m",
        timestamp_ms=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
        close_time_ms=ts + 899999,
        is_closed=True,
    )


def build_test_candles(base_price: float = 110.0, count: int = 40, swing_high: float = 111.95) -> list[Candle]:
    base_ts = 1793000000000
    candles = [
        make_candle(base_ts + i * 900000, base_price, base_price + 0.3, base_price - 0.3, base_price)
        for i in range(count)
    ]
    # Create prominent swing high at bar 25: peak at swing_high
    candles[24] = make_candle(base_ts + 24 * 900000, base_price, swing_high - 0.5, base_price - 0.2, swing_high - 0.3)
    candles[25] = make_candle(base_ts + 25 * 900000, swing_high - 0.3, swing_high, base_price, swing_high - 0.2)
    candles[26] = make_candle(base_ts + 26 * 900000, swing_high - 0.2, swing_high - 0.4, base_price - 0.3, swing_high - 0.5)
    return candles


# =============================================================================
# Test 1: Stale fixed-R TP replaced by valid structural TP (atomic replacement)
# =============================================================================
def test_01_stale_fixed_r_tp_replaced_by_valid_structural_tp():
    mock_exchange = MagicMock()

    # Active LONG position: Entry 109.870, Mark 111.020, Qty 0.13
    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    # Active orders: Stale fixed-R TP at 112.820, SL at 109.960
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "118566868",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "112.820",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "109.960",
            "origQty": "0.13",
            "reduceOnly": True,
        },
    ]

    # Provide 15m and 1h candles with swing high at 111.950
    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m
    mock_exchange.create_order.return_value = {"orderId": "119999001"}

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(results) == 1
    res = results[0]
    assert res["status"] == "UPDATED"
    assert res["symbol"] == "BZUSDT"
    assert res["old_tp_price"] == 112.820
    # 111.950 * 0.9992 = 111.860 (15M_SWING_HIGH)
    assert res["new_tp_price"] == 111.860
    assert res["tp_target_type"] == "15M_SWING_HIGH"
    assert res["new_order_id"] == "119999001"

    # Verify atomic replacement sequence:
    # Step 1: Create new order
    mock_exchange.create_order.assert_called_once_with(
        symbol="BZUSDT",
        side="SELL",
        order_type="LIMIT",
        quantity=0.13,
        price=111.860,
        reduce_only=True,
        time_in_force="GTC",
    )
    # Step 2: Cancel old TP order
    mock_exchange.cancel_order.assert_called_once_with(
        symbol="BZUSDT",
        order_id="118566868",
    )

    # Verify provenance recorded
    latest = coord.get_latest_adaptive_tp_provenance("BZUSDT")
    assert latest["status"] == "UPDATED"
    assert latest["old_tp_price"] == 112.820
    assert latest["proposed_tp_price"] == 111.860
    assert latest["structural_source"] == "15M_SWING_HIGH"


# =============================================================================
# Test 2: No replacement when target unchanged or difference below threshold
# =============================================================================
def test_02_no_replacement_when_target_unchanged_or_below_threshold():
    mock_exchange = MagicMock()

    # Active LONG position: Entry 109.870, Mark 111.020
    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    # Active TP is ALREADY at 111.860
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "119999001",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "111.860",
            "origQty": "0.13",
            "reduceOnly": True,
        }
    ]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT", min_price_change_pct=0.003)

    assert len(results) == 1
    res = results[0]
    assert res["status"] == "UNCHANGED"
    assert res["old_tp_price"] == 111.860
    assert res["proposed_tp_price"] == 111.860

    # Ensure zero exchange mutations occurred!
    mock_exchange.create_order.assert_not_called()
    mock_exchange.cancel_order.assert_not_called()


# =============================================================================
# Test 3: Invalid targets at or behind mark price rejected
# =============================================================================
def test_03_invalid_targets_at_or_behind_mark_price_rejected():
    mock_exchange = MagicMock()

    # Case A: LONG position where mark price has moved past all targets and 2.0R fallback
    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "113.000",
    }]

    mock_exchange.get_open_orders.return_value = [{
        "orderId": "118566868",
        "symbol": "BZUSDT",
        "type": "LIMIT",
        "side": "SELL",
        "price": "113.500",
        "origQty": "0.13",
        "reduceOnly": True,
    }]

    # Swings are all below 112.00
    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(results) == 1
    res = results[0]
    assert res["status"] in ("REJECTED", "REJECTED_BEHIND_MARK")
    assert "at or behind mark price" in res["reason"]
    # Verify no exchange orders placed or cancelled
    mock_exchange.create_order.assert_not_called()
    mock_exchange.cancel_order.assert_not_called()

    # Case B: Direct function call for SHORT where mark price is below target
    short_res = compute_dynamic_structural_tp(
        entry_price=100.0,
        stop_loss_price=102.0,  # Risk = 2.0, 2.0R TP = 96.0
        direction=Direction.SHORT,
        candles_15m=candles_15m,
        current_mark_price=95.0,  # Mark price dropped to 95.0 (below 96.0 target)
        for_open_position=True,
    )
    assert short_res.is_eligible is False
    assert short_res.tp_target_type == "REJECTED_BEHIND_MARK"
    assert "at or behind mark price" in short_res.rejection_reason


# =============================================================================
# Test 4: Replacement failure preserves old TP
# =============================================================================
def test_04_replacement_failure_preserves_old_tp():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    mock_exchange.get_open_orders.return_value = [{
        "orderId": "118566868",
        "symbol": "BZUSDT",
        "type": "LIMIT",
        "side": "SELL",
        "price": "112.820",
        "origQty": "0.13",
        "reduceOnly": True,
    }]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m

    # Simulate exchange error on new order creation
    mock_exchange.create_order.side_effect = RuntimeError("Aster DEX 503 Service Unavailable / Rate Limit")

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(results) == 1
    res = results[0]
    assert res["status"] == "FAILED"
    assert "Aster DEX 503" in res["error"]
    assert res["preserved_tp_order_id"] == "118566868"

    # CRITICAL INVARIANT: Old TP order must NEVER be cancelled when replacement fails!
    mock_exchange.cancel_order.assert_not_called()


# =============================================================================
# Test 5: SL and breakeven orders untouched
# =============================================================================
def test_05_sl_and_breakeven_orders_untouched():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    # Orders include STOP_MARKET breakeven order at 109.960 and stale TP at 112.820
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "118566868",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "112.820",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "109.960",
            "origQty": "0.13",
            "reduceOnly": True,
        },
    ]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m
    mock_exchange.create_order.return_value = {"orderId": "119999002"}

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(results) == 1
    assert results[0]["status"] == "UPDATED"

    # Verify that cancel_order was called ONLY for the TP order (118566868)
    # and NEVER for the SL order (118936872)
    mock_exchange.cancel_order.assert_called_once_with(
        symbol="BZUSDT",
        order_id="118566868",
    )


# =============================================================================
# Test 6: Sweep-and-reclaim vs confirmed structural breakdown
# =============================================================================
def test_06_sweep_and_reclaim_vs_confirmed_structural_breakdown():
    coord = LiveMicroCoordinator(exchange=None)
    invalidation_level = 108.600  # Sweep low / initial SL level
    base_ts = 1793000000000

    # Case A: Wick sweep and reclaim (low pierced 108.40 < 108.60, but bar closed at 108.90 >= 108.60)
    candles_wick_sweep = [
        make_candle(base_ts, 109.50, 109.80, 109.20, 109.40),
        make_candle(base_ts + 900000, 109.40, 109.50, 108.40, 108.90),  # Closed bar [-2]
        make_candle(base_ts + 1800000, 108.90, 109.20, 108.80, 109.10), # Forming bar [-1]
    ]

    res_wick = coord.check_structural_invalidation(
        symbol="BZUSDT",
        direction="LONG",
        entry_price=109.870,
        stop_loss_price=invalidation_level,
        candles_15m=candles_wick_sweep,
    )
    assert res_wick["status"] == "WICK_SWEEP_RECLAIM"
    assert "pierced low" in res_wick["reason"]

    # Case B: Confirmed structural breakdown (bar closed at 108.30 < 108.60)
    candles_breakdown = [
        make_candle(base_ts, 109.50, 109.80, 109.20, 109.40),
        make_candle(base_ts + 900000, 109.40, 109.40, 108.10, 108.30),  # Closed bar [-2] closed below!
        make_candle(base_ts + 1800000, 108.30, 108.40, 107.90, 108.00), # Forming bar [-1]
    ]

    res_breakdown = coord.check_structural_invalidation(
        symbol="BZUSDT",
        direction="LONG",
        entry_price=109.870,
        stop_loss_price=invalidation_level,
        candles_15m=candles_breakdown,
    )
    assert res_breakdown["status"] == "CONFIRMED_STRUCTURAL_BREAKDOWN"
    assert "closed below invalidation level" in res_breakdown["reason"]

    # Case C: Structure intact (low is 109.00 >= 108.60)
    candles_intact = [
        make_candle(base_ts, 109.50, 109.80, 109.20, 109.40),
        make_candle(base_ts + 900000, 109.40, 109.60, 109.00, 109.30),  # Closed bar [-2]
        make_candle(base_ts + 1800000, 109.30, 109.50, 109.10, 109.40), # Forming bar [-1]
    ]

    res_intact = coord.check_structural_invalidation(
        symbol="BZUSDT",
        direction="LONG",
        entry_price=109.870,
        stop_loss_price=invalidation_level,
        candles_15m=candles_intact,
    )
    assert res_intact["status"] == "STRUCTURE_INTACT"


# =============================================================================
# Test 7: Partial fills, closure, duplicate reconciliation, API failure
# =============================================================================
def test_07_partial_fills_and_position_closure_handling():
    mock_exchange = MagicMock()

    # Case A: Partial fill - position quantity reduced from 0.13 to 0.08
    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.08",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    mock_exchange.get_open_orders.return_value = [{
        "orderId": "118566868",
        "symbol": "BZUSDT",
        "type": "LIMIT",
        "side": "SELL",
        "price": "112.820",
        "origQty": "0.13",
        "reduceOnly": True,
    }]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m
    mock_exchange.create_order.return_value = {"orderId": "119999003"}

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(results) == 1
    # Verify new order uses the updated partial fill quantity of 0.08!
    mock_exchange.create_order.assert_called_once_with(
        symbol="BZUSDT",
        side="SELL",
        order_type="LIMIT",
        quantity=0.08,
        price=111.860,
        reduce_only=True,
        time_in_force="GTC",
    )

    # Case B: Closed position (positionAmt = 0.0)
    mock_exchange.reset_mock()
    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.00",
        "entryPrice": "109.870",
    }]
    results_closed = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")
    assert results_closed == []
    mock_exchange.create_order.assert_not_called()

    # Case C: API failure during kline fetch
    mock_exchange.reset_mock()
    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.08",
        "entryPrice": "109.870",
    }]
    mock_exchange.get_open_orders.return_value = []
    mock_exchange.get_klines.side_effect = RuntimeError("Timeout fetching klines")

    results_err = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")
    assert len(results_err) == 1
    assert results_err[0]["status"] == "SKIPPED_INSUFFICIENT_DATA"


# =============================================================================
# Test 8: No live exchange order changes in diagnostic/dry-run mode
# =============================================================================
def test_08_no_live_exchange_order_changes_in_dry_run_mode():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    mock_exchange.get_open_orders.return_value = [{
        "orderId": "118566868",
        "symbol": "BZUSDT",
        "type": "LIMIT",
        "side": "SELL",
        "price": "112.820",
        "origQty": "0.13",
        "reduceOnly": True,
    }]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    # Explicit dry_run = True
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT", dry_run=True)

    assert len(results) == 1
    res = results[0]
    assert res["status"] == "DRY_RUN_ELIGIBLE"
    assert res["old_tp_price"] == 112.820
    assert res["proposed_tp_price"] == 111.860
    assert res["tp_target_type"] == "15M_SWING_HIGH"

    # CRITICAL: dry_run must NEVER send create_order or cancel_order to exchange!
    mock_exchange.create_order.assert_not_called()
    mock_exchange.cancel_order.assert_not_called()


# =============================================================================
# Test 9: LiveAutoSessionManager reconciliation wires adaptive TP and alerts
# =============================================================================
def test_09_reconciliation_loop_wires_adaptive_tp_and_invalidation(tmp_path):
    from src.execution.live_session import LiveAutoSessionManager, ExecutionMode, LiveSessionState
    from src.journal.automated_journal import AutomatedTradeJournal
    from src.risk.global_lock import GlobalPositionLock

    # Reset singletons
    lock = GlobalPositionLock()
    with lock._mutex:
        lock._is_locked = False
        lock._symbol = None
        lock._state = "IDLE"
    LiveAutoSessionManager._instance = None

    db_file = tmp_path / "test_recon.db"
    db = DatabaseManager(str(db_file))
    db.init_db()
    journal = AutomatedTradeJournal(db)

    mock_exchange = MagicMock()
    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]
    mock_exchange.get_open_orders.return_value = [{
        "orderId": "118566868",
        "symbol": "BZUSDT",
        "type": "LIMIT",
        "side": "SELL",
        "price": "112.820",
        "origQty": "0.13",
        "reduceOnly": True,
    }]
    mock_exchange.get_account_balance.return_value = [{"asset": "USDT", "balance": 15.72, "availableBalance": 15.72}]
    mock_exchange.get_user_trades.return_value = []

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m
    mock_exchange.create_order.return_value = {"orderId": "119999009"}

    coord = LiveMicroCoordinator(db=db, exchange=mock_exchange)

    session_mgr = LiveAutoSessionManager(
        coordinator=coord,
        exchange_adapter=mock_exchange,
        automated_journal=journal,
        db=db,
    )
    session_mgr.mode = ExecutionMode.LIVE_AUTO
    session_mgr.state = LiveSessionState.ACTIVE
    session_mgr._reconciliation_started_event.set()

    # Run reconciliation once
    summary = session_mgr._run_reconciliation_once()

    assert "adaptive_tp_updates" in summary
    assert len(summary["adaptive_tp_updates"]) == 1
    assert summary["adaptive_tp_updates"][0]["status"] == "UPDATED"
    assert summary["adaptive_tp_updates"][0]["new_tp_price"] == 111.860

    assert "structural_invalidation_alerts" in summary
    assert len(summary["structural_invalidation_alerts"]) == 1
    assert summary["structural_invalidation_alerts"][0]["status"] == "STRUCTURE_INTACT"

    # Verify _last_reconciliation updated
    assert len(session_mgr._last_reconciliation["adaptive_tp_updates"]) == 1
    assert len(session_mgr._last_reconciliation["structural_invalidation_alerts"]) == 1

    # Clean up singletons
    with lock._mutex:
        lock._is_locked = False
    LiveAutoSessionManager._instance = None


# =============================================================================
# Test 10: Persistent ask wall front-running and disappearance fallback
# =============================================================================
def test_10_persistent_ask_wall_frontrunning_and_disappearance_fallback():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "118566868",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "112.820",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "108.690",
            "origQty": "0.13",
            "reduceOnly": True,
        },
    ]
    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=112.100)
    mock_exchange.get_klines.return_value = candles_15m

    # Cycle 1: Heavy ask wall of $50,000 at 111.900 (in sweet spot ~1.72R)
    ob_mock = MagicMock()
    ask_level = MagicMock()
    ask_level.price = 111.900
    ask_level.size = 450.0  # 111.900 * 450 = ~$50,355
    ob_mock.asks = [ask_level]
    ob_mock.bids = []
    mock_exchange.get_order_book.return_value = ob_mock
    mock_exchange.create_order.return_value = {"orderId": "119999010"}

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    res_cycle1 = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(res_cycle1) == 1
    # 111.900 * 0.9992 = 111.810 (0.08% front-run below ask wall)
    assert res_cycle1[0]["status"] == "UPDATED"
    assert res_cycle1[0]["tp_target_type"] == "L2_ASK_WALL_FRONTRUN"
    assert res_cycle1[0]["new_tp_price"] == 111.810

    # Cycle 2A: Wall disappears! Orderbook has no large wall.
    # Swing high fallback is 112.010. Distance from 111.810 is 0.179% (< 0.3% anti-churn threshold).
    # System correctly suppresses order churn!
    ob_empty = MagicMock()
    ob_empty.asks = []
    ob_empty.bids = []
    mock_exchange.get_order_book.return_value = ob_empty
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "119999010",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "111.810",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "108.690",
            "origQty": "0.13",
            "reduceOnly": True,
        },
    ]
    res_cycle2a = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT", min_price_change_pct=0.003)
    assert len(res_cycle2a) == 1
    assert res_cycle2a[0]["status"] == "UNCHANGED"
    assert res_cycle2a[0]["price_diff_pct"] < 0.3
    mock_exchange.create_order.assert_called_once()  # not called again

    # Cycle 2B: When structural fallback shifts beyond 0.3% (e.g. higher swing at 112.350 -> 112.260),
    # replacement executes cleanly
    candles_higher = build_test_candles(base_price=110.0, count=40, swing_high=112.350)
    mock_exchange.get_klines.return_value = candles_higher
    mock_exchange.create_order.return_value = {"orderId": "119999011"}

    res_cycle2b = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT", min_price_change_pct=0.003)
    assert len(res_cycle2b) == 1
    assert res_cycle2b[0]["status"] == "UPDATED"
    assert res_cycle2b[0]["tp_target_type"] == "15M_SWING_HIGH"
    assert res_cycle2b[0]["new_tp_price"] == 112.260


# =============================================================================
# Test 11: Stale or missing L2 data falls back gracefully to structure
# =============================================================================
def test_11_stale_or_missing_l2_data_falls_back_to_structure():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]
    mock_exchange.get_open_orders.return_value = [{
        "orderId": "118566868",
        "symbol": "BZUSDT",
        "type": "LIMIT",
        "side": "SELL",
        "price": "112.820",
        "origQty": "0.13",
        "reduceOnly": True,
    }]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m

    # Simulate get_order_book raising an exception or returning None (stale/broken L2 feed)
    mock_exchange.get_order_book.side_effect = TimeoutError("L2 depth endpoint timed out")
    mock_exchange.create_order.return_value = {"orderId": "119999012"}

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    # Verify coordinator gracefully continues with 15m/1h candle structure
    assert len(results) == 1
    assert results[0]["status"] == "UPDATED"
    assert results[0]["tp_target_type"] == "15M_SWING_HIGH"
    assert results[0]["new_tp_price"] == 111.860


# =============================================================================
# Test 12: Ambiguous create-order timeout preserves old TP and prevents duplicates
# =============================================================================
def test_12_ambiguous_create_order_timeout_preserves_old_tp_and_prevents_duplicates():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]
    mock_exchange.get_open_orders.return_value = [{
        "orderId": "118566868",
        "symbol": "BZUSDT",
        "type": "LIMIT",
        "side": "SELL",
        "price": "112.820",
        "origQty": "0.13",
        "reduceOnly": True,
    }]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m

    # Cycle 1: Exchange create_order throws timeout exception
    mock_exchange.create_order.side_effect = TimeoutError("Network timeout during create_order")

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    res_timeout = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(res_timeout) == 1
    assert res_timeout[0]["status"] == "FAILED"
    assert "timeout" in res_timeout[0]["error"].lower()
    # Old TP must NOT be cancelled!
    mock_exchange.cancel_order.assert_not_called()

    # Cycle 2: Suppose the timeout was ambiguous and the order actually landed on exchange
    # (exchange open orders now reflects the new order at 111.860)
    mock_exchange.reset_mock()
    mock_exchange.create_order.side_effect = None
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "119999015",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "111.860",
            "origQty": "0.13",
            "reduceOnly": True,
        }
    ]

    res_recovery = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT", min_price_change_pct=0.003)
    assert len(res_recovery) == 1
    # Target is already at 111.860, so price diff is 0.0% -> UNCHANGED
    assert res_recovery[0]["status"] == "UNCHANGED"
    # No duplicate order created!
    mock_exchange.create_order.assert_not_called()


# =============================================================================
# Test 13: Multiple TP orders at disparate prices reconciled to single adaptive TP
# =============================================================================
def test_13_multiple_tp_orders_disparate_prices_reconciled_to_single_adaptive_tp():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    # Discrepancy scenario: 2 active TP orders at disparate prices
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "118566868",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "112.820",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118566869",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "112.400",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "108.690",
            "origQty": "0.13",
            "reduceOnly": True,
        },
    ]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m
    mock_exchange.create_order.return_value = {"orderId": "119999020"}

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(results) == 1
    res = results[0]
    assert res["status"] == "UPDATED"
    assert res["new_tp_price"] == 111.860
    assert res["new_order_id"] == "119999020"
    assert "118566868" in res["superseded_order_ids"]
    assert "118566869" in res["superseded_order_ids"]

    # Verify BOTH superseded TP orders were cancelled
    assert mock_exchange.cancel_order.call_count == 2
    mock_exchange.cancel_order.assert_any_call(symbol="BZUSDT", order_id="118566868")
    mock_exchange.cancel_order.assert_any_call(symbol="BZUSDT", order_id="118566869")


# =============================================================================
# Test 14: Multiple TP orders where one matches target consolidated without churn
# =============================================================================
def test_14_multiple_tp_orders_one_matching_consolidated_without_order_churn():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    # One order already matches 111.860, but another duplicate exists at 112.820
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "118566870",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "111.860",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118566868",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "112.820",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "108.690",
            "origQty": "0.13",
            "reduceOnly": True,
        },
    ]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(results) == 1
    res = results[0]
    assert res["status"] == "CONSOLIDATED_DUPLICATES"
    assert res["retained_order_id"] == "118566870"
    assert "118566868" in res["cancelled_order_ids"]

    # Invariant: NO new order created (prevents unnecessary churn)
    mock_exchange.create_order.assert_not_called()

    # Invariant: ONLY duplicate 118566868 was cancelled, 118566870 was retained
    mock_exchange.cancel_order.assert_called_once_with(symbol="BZUSDT", order_id="118566868")


# =============================================================================
# Test 15: Unrelated LIMIT orders and orders on other symbols never cancelled
# =============================================================================
def test_15_unrelated_limit_orders_never_cancelled():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    mock_exchange.get_open_orders.return_value = [
        # Valid stale TP order for BZUSDT
        {
            "orderId": "118566868",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "112.820",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        # Unrelated: Non-reduce-only BUY limit entry order on BZUSDT
        {
            "orderId": "118566890",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "BUY",
            "price": "108.000",
            "origQty": "0.13",
            "reduceOnly": False,
        },
        # Unrelated: Order on a different symbol (SOLUSDT)
        {
            "orderId": "118566891",
            "symbol": "SOLUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "150.000",
            "origQty": "1.0",
            "reduceOnly": True,
        },
        # Unrelated: Grid / DCA non-reduce-only SELL limit order on BZUSDT
        {
            "orderId": "118566892",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "115.000",
            "origQty": "0.10",
            "reduceOnly": False,
        },
        # Unrelated: STOP_MARKET SL order on BZUSDT
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "108.690",
            "origQty": "0.13",
            "reduceOnly": True,
        },
    ]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m
    mock_exchange.create_order.return_value = {"orderId": "119999021"}

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(results) == 1
    assert results[0]["status"] == "UPDATED"

    # CRITICAL INVARIANT: Only the genuine reduce-only LIMIT TP (118566868) is cancelled
    assert mock_exchange.cancel_order.call_count == 1
    mock_exchange.cancel_order.assert_called_once_with(symbol="BZUSDT", order_id="118566868")


# =============================================================================
# Test 16: Partial fill resizes TP quantity even when price is unchanged
# =============================================================================
def test_16_partial_fill_resizes_tp_quantity_even_when_price_unchanged():
    mock_exchange = MagicMock()

    # Position quantity reduced from 0.13 to 0.08 due to partial fill
    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.08",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    # TP order exists at target price 111.860, but with OLD quantity 0.13
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "118566868",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "111.860",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "108.690",
            "origQty": "0.08",
            "reduceOnly": True,
        },
    ]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m
    mock_exchange.create_order.return_value = {"orderId": "119999022"}

    coord = LiveMicroCoordinator(exchange=mock_exchange)
    results = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")

    assert len(results) == 1
    assert results[0]["status"] == "UPDATED"

    # Verify new TP order created for the REDUCED quantity 0.08!
    mock_exchange.create_order.assert_called_once_with(
        symbol="BZUSDT",
        side="SELL",
        order_type="LIMIT",
        quantity=0.08,
        price=111.860,
        reduce_only=True,
        time_in_force="GTC",
    )
    mock_exchange.cancel_order.assert_called_once_with(symbol="BZUSDT", order_id="118566868")


# =============================================================================
# Test 17: Idempotency across repeated reconciliation cycles
# =============================================================================
def test_17_idempotency_across_repeated_reconciliation_cycles():
    mock_exchange = MagicMock()

    mock_exchange.get_positions.return_value = [{
        "symbol": "BZUSDT",
        "positionAmt": "0.13",
        "entryPrice": "109.870",
        "markPrice": "111.020",
    }]

    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "118566868",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "112.820",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "108.690",
            "origQty": "0.13",
            "reduceOnly": True,
        },
    ]

    candles_15m = build_test_candles(base_price=110.0, count=40, swing_high=111.950)
    mock_exchange.get_klines.return_value = candles_15m
    mock_exchange.create_order.return_value = {"orderId": "119999025"}

    coord = LiveMicroCoordinator(exchange=mock_exchange)

    # Cycle 1: Stale 112.820 -> 111.860 (UPDATED)
    res1 = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")
    assert len(res1) == 1
    assert res1[0]["status"] == "UPDATED"
    assert mock_exchange.create_order.call_count == 1
    assert mock_exchange.cancel_order.call_count == 1

    # Simulate exchange state after Cycle 1 (open orders now has order 119999025 @ 111.860)
    mock_exchange.get_open_orders.return_value = [
        {
            "orderId": "119999025",
            "symbol": "BZUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "111.860",
            "origQty": "0.13",
            "reduceOnly": True,
        },
        {
            "orderId": "118936872",
            "symbol": "BZUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "108.690",
            "origQty": "0.13",
            "reduceOnly": True,
        },
    ]
    mock_exchange.create_order.reset_mock()
    mock_exchange.cancel_order.reset_mock()

    # Cycles 2 to 5: Must all be UNCHANGED with zero exchange mutations!
    for cycle in range(2, 6):
        res = coord.monitor_and_update_adaptive_tp(symbol="BZUSDT")
        assert len(res) == 1
        assert res[0]["status"] == "UNCHANGED", f"Failed at cycle {cycle}"
        mock_exchange.create_order.assert_not_called()
        mock_exchange.cancel_order.assert_not_called()
