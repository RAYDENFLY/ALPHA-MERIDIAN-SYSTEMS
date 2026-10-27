"""Unit test suite for Dynamic Structural Resistance & L2 Wall Take-Profit (TP) Targeting,
Room-to-Resistance Gate, and Auto Break-Even Protection.
"""

import pytest
from unittest.mock import MagicMock
from src.core.types import Candle, Direction
from src.strategy.sweep_reclaim_15m import (
    SweepReclaim15MStrategy,
    compute_dynamic_structural_tp,
    find_prominent_swings,
    DynamicTPResult,
)
from src.scheduler.live_micro_runner import LiveMicroCoordinator


def make_candle(ts: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="ETHUSDT",
        timeframe="15m",
        timestamp_ms=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
        close_time_ms=ts + 899999,
    )


def test_01_long_snaps_tp_just_below_l2_ask_wall_and_swing_high():
    """Verify LONG setup snaps TP just below an L2 Ask Wall / Swing High at ~1.65R
    instead of blindly shooting for a rigid/unreachable fixed 2.5R.
    """
    entry_price = 100.0
    stop_loss = 98.0  # Risk = 2.0 USDT
    candles = [make_candle(1000 + i * 900000, 99.0, 100.0, 98.0, 99.0) for i in range(20)]

    # An L2 Ask Wall sits at 103.30 (which is 3.30 above entry = 1.65R)
    nearest_ask_wall = {"price": 103.30, "size_usdt": 25000.0}

    res: DynamicTPResult = compute_dynamic_structural_tp(
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        direction=Direction.LONG,
        candles_15m=candles,
        nearest_ask_wall=nearest_ask_wall,
        tick_size=0.01,
        front_run_buffer_pct=0.0008,  # 0.08% front-run
    )

    assert res.is_eligible is True
    assert res.tp_target_type == "L2_ASK_WALL_FRONTRUN"
    # Target should front-run 103.30 by 0.08% -> 103.30 * 0.9992 = 103.2173 -> 103.21
    assert res.take_profit_price < 103.30
    assert 103.20 <= res.take_profit_price <= 103.25
    # Gross R:R should be ~1.61R (close to 1.65R), not 2.25R or 2.5R
    assert 1.55 <= res.gross_rr <= 1.65
    assert res.take_profit_price < 105.0  # Far below rigid 2.5R (105.0)


def test_02_long_snaps_tp_just_below_15m_swing_high():
    """Verify LONG setup snaps TP below a confirmed 15m Swing High in the sweet spot (1.5R - 2.2R)."""
    entry_price = 2000.0
    stop_loss = 1975.0  # Risk = 25.0
    base_ts = 1792900000000

    candles = [make_candle(base_ts + i * 900000, 1990.0, 1995.0, 1985.0, 1990.0) for i in range(25)]
    # Create prominent swing high at bar 15: Peak at 2042.0 (42.0 above entry = 1.68R)
    candles[14] = make_candle(base_ts + 14 * 900000, 1995.0, 2010.0, 1990.0, 2005.0)
    candles[15] = make_candle(base_ts + 15 * 900000, 2005.0, 2042.0, 2000.0, 2035.0)  # Peak
    candles[16] = make_candle(base_ts + 16 * 900000, 2035.0, 2038.0, 2015.0, 2020.0)
    candles[17] = make_candle(base_ts + 17 * 900000, 2020.0, 2025.0, 2005.0, 2010.0)

    res: DynamicTPResult = compute_dynamic_structural_tp(
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        direction=Direction.LONG,
        candles_15m=candles,
        tick_size=0.10,
        front_run_buffer_pct=0.0008,
    )

    assert res.is_eligible is True
    assert res.tp_target_type == "15M_SWING_HIGH"
    # Front-runs 2042.0 by 0.08% -> 2042 * 0.9992 = 2040.36 -> floor to tick 0.10 = 2040.3
    assert res.take_profit_price < 2042.0
    assert 2039.5 <= res.take_profit_price <= 2041.5
    assert 1.55 <= res.gross_rr <= 1.70


def test_03_room_to_resistance_veto_when_obstacle_is_0_5r():
    """Verify setup is vetoed with INSUFFICIENT_ROOM_TO_RESISTANCE when a heavy wall or swing high sits at 0.5R."""
    entry_price = 100.0
    stop_loss = 98.0  # Risk = 2.0
    candles = [make_candle(1000 + i * 900000, 99.0, 100.0, 98.0, 99.0) for i in range(20)]

    # Heavy L2 ask wall sitting at 101.0 (Distance = 1.0 = 0.5R < 1.25R)
    nearest_ask_wall = {"price": 101.0, "size_usdt": 50000.0}

    res: DynamicTPResult = compute_dynamic_structural_tp(
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        direction=Direction.LONG,
        candles_15m=candles,
        nearest_ask_wall=nearest_ask_wall,
        tick_size=0.01,
    )

    assert res.is_eligible is False
    assert "INSUFFICIENT_ROOM_TO_RESISTANCE" in res.rejection_reason
    assert "0.50R" in res.rejection_reason or "blocks path" in res.rejection_reason


def test_04_tick_size_quantization_exactness():
    """Verify all dynamic TP prices are strictly quantized to exact symbol tick_size."""
    entry_price = 100.0
    stop_loss = 98.0  # Risk = 2.0
    candles = [make_candle(1000 + i * 900000, 99.0, 100.0, 98.0, 99.0) for i in range(20)]

    # Test tick sizes: 0.05, 0.25, 1.0, 0.001
    test_ticks = [0.05, 0.25, 1.0, 0.001]
    for ts in test_ticks:
        res = compute_dynamic_structural_tp(
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            direction=Direction.LONG,
            candles_15m=candles,
            nearest_ask_wall={"price": 103.333, "size_usdt": 15000.0},
            tick_size=ts,
        )
        assert res.is_eligible is True
        # Check integer multiple of tick_size
        quotient = round(res.take_profit_price / ts, 6)
        assert abs(quotient - round(quotient)) < 1e-5, f"TP {res.take_profit_price} not quantized to tick {ts}"


def test_05_strategy_evaluate_integrates_dynamic_tp_and_veto():
    """Verify SweepReclaim15MStrategy.evaluate integrates dynamic TP and room-to-resistance veto."""
    strat = SweepReclaim15MStrategy()
    base_ts = 1792900000000

    candles = [make_candle(base_ts + i * 900000, 2320.0, 2325.0, 2315.0, 2320.0) for i in range(25)]
    # Swing low at bar 20
    candles[20] = make_candle(base_ts + 20 * 900000, 2315.0, 2318.0, 2305.0, 2310.0)
    candles[21] = make_candle(base_ts + 21 * 900000, 2310.0, 2315.0, 2308.0, 2312.0)
    candles[22] = make_candle(base_ts + 22 * 900000, 2312.0, 2316.0, 2310.0, 2314.0)
    candles[23] = make_candle(base_ts + 23 * 900000, 2306.0, 2309.0, 2303.0, 2308.0)
    candles[24] = make_candle(base_ts + 24 * 900000, 2308.0, 2310.0, 2305.0, 2307.0)

    # Case A: An opposing Ask Wall sits at 0.5R from entry (~2315.0 where entry is ~2306 and SL is ~2278, risk=28)
    setup, reason = strat.evaluate(
        symbol="ETHUSDT",
        candles_15m=candles,
        dealing_range_position_pct=25.0,
        nearest_ask_wall={"price": 2314.0, "size_usdt": 50000.0},
    )
    assert setup is None
    assert "INSUFFICIENT_ROOM_TO_RESISTANCE" in reason

    # Case B: An L2 Ask Wall sits at 1.85R (2306 + 1.85 * 28 = ~2357.80)
    setup_valid, reason_valid = strat.evaluate(
        symbol="ETHUSDT",
        candles_15m=candles,
        dealing_range_position_pct=25.0,
        nearest_ask_wall={"price": 2357.80, "size_usdt": 50000.0},
    )
    assert setup_valid is not None, f"Expected valid setup, got {reason_valid}"
    assert setup_valid.tp_target_type == "L2_ASK_WALL_FRONTRUN"
    assert setup_valid.take_profit < 2357.80
    assert 1.70 <= setup_valid.gross_rr <= 1.90


def test_06_short_setup_dynamic_tp_and_veto():
    """Verify SHORT setup support targeting and room-to-resistance veto on L2 bid wall."""
    entry_price = 100.0
    stop_loss = 102.0  # Risk = 2.0
    candles = [make_candle(1000 + i * 900000, 100.0, 101.0, 99.0, 100.0) for i in range(20)]

    # Case A: Bid wall at 99.0 (Distance = 1.0 = 0.5R < 1.25R)
    res_veto = compute_dynamic_structural_tp(
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        direction=Direction.SHORT,
        candles_15m=candles,
        nearest_bid_wall={"price": 99.0, "size_usdt": 20000.0},
        tick_size=0.01,
    )
    assert res_veto.is_eligible is False
    assert "INSUFFICIENT_ROOM_TO_RESISTANCE" in res_veto.rejection_reason

    # Case B: Bid wall at 96.70 (Distance = 3.30 = 1.65R)
    res_valid = compute_dynamic_structural_tp(
        entry_price=entry_price,
        stop_loss_price=stop_loss,
        direction=Direction.SHORT,
        candles_15m=candles,
        nearest_bid_wall={"price": 96.70, "size_usdt": 20000.0},
        tick_size=0.01,
    )
    assert res_valid.is_eligible is True
    assert res_valid.tp_target_type == "L2_BID_WALL_FRONTRUN"
    # Front-runs above 96.70 -> 96.70 * 1.0008 = 96.777 -> Ceil to 96.78
    assert res_valid.take_profit_price > 96.70
    assert 96.75 <= res_valid.take_profit_price <= 96.85
    assert 1.55 <= res_valid.gross_rr <= 1.65


def test_07_auto_breakeven_protection_at_1r_profit():
    """Verify position monitor moves STOP_MARKET to average_entry_price (+0.08% fee buffer)
    when open position reaches >= +1.0R unrealized profit.
    """
    mock_exchange = MagicMock()

    # Active LONG position: Entry 2000.0, Mark 2035.0 (Gain = 35.0)
    mock_exchange.get_positions.return_value = [{
        "symbol": "ETHUSDT",
        "positionAmt": "0.10",
        "entryPrice": "2000.0",
        "markPrice": "2035.0",
    }]

    # Open SL order at 1970.0 (Risk = 30.0; Mark gain 35.0 >= 30.0 = +1.16R >= 1.0R)
    mock_exchange.get_open_orders.return_value = [{
        "orderId": "SL_OLD_123",
        "symbol": "ETHUSDT",
        "type": "STOP_MARKET",
        "stopPrice": "1970.0",
        "reduceOnly": True,
    }]

    mock_exchange.create_order.return_value = {"orderId": "SL_BE_999"}

    coordinator = LiveMicroCoordinator(exchange=mock_exchange)
    updates = coordinator.monitor_and_update_breakeven_protection(symbol="ETHUSDT", tick_size=0.10)

    assert len(updates) == 1
    up = updates[0]
    assert up["status"] == "MOVED_TO_BREAKEVEN"
    assert up["symbol"] == "ETHUSDT"
    # Entry 2000 * 1.0008 = 2001.6
    assert up["new_sl_price"] == 2001.6
    assert "+1.0R" in up["trigger_reason"]

    # Verify atomic replacement: new order placed and old order cancelled
    mock_exchange.create_order.assert_called_once_with(
        symbol="ETHUSDT",
        side="SELL",
        order_type="STOP_MARKET",
        quantity=0.10,
        stop_price=2001.6,
        reduce_only=True,
    )
    mock_exchange.cancel_order.assert_called_once_with(
        symbol="ETHUSDT",
        order_id="SL_OLD_123",
    )
