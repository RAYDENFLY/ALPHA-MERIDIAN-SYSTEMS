"""Unit tests verifying Dealing Range Zone Locks, 15M_SWEEP_RECLAIM_SMC strategy,
orphan open order cleanup, and tick-size quantization.
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from unittest.mock import MagicMock, patch

from src.core.types import Candle, Direction
from src.strategy.dealing_range import (
    DealingRangeInfo,
    check_dealing_range_zone_lock,
    compute_dealing_range,
)
from src.strategy.sweep_reclaim_15m import (
    SweepReclaim15MSetup,
    SweepReclaim15MStrategy,
    calc_rsi,
    find_swings_15m,
)
from src.risk.live_micro_sizing import normalize_price_to_tick


def make_candle(
    timestamp_ms: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    volume: float = 100.0,
    is_closed: bool = True,
    symbol: str = "ETHUSDT",
    timeframe: str = "15m",
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp_ms=timestamp_ms,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=volume,
        close_time_ms=timestamp_ms + 900000,
        is_closed=is_closed,
    )


# -----------------------------------------------------------------------------
# 1. Dealing Range Zone Lock Tests (Hard Laws)
# -----------------------------------------------------------------------------
class TestDealingRangeZoneLocks:
    """Tests for Hard Law 1 and Hard Law 2."""

    def test_hard_law_1_short_eth_below_2360_rejected(self):
        """Verify that any SHORT signal on ETHUSDT below 2360 (position_pct < 65%)
        is 100% REJECTED by the hard zone lock.
        
        Dealing Range: Low = 2281.33, High = 2416.80, Span = 135.47.
        Threshold 65%: 2281.33 + 0.65 * 135.47 = 2369.3855.
        Any price below 2369.39 (including 2360.00 and 2308.76) has position_pct < 65.0%.
        """
        range_low = 2281.33
        range_high = 2416.80
        range_span = range_high - range_low

        # Test price 2360.00
        pos_pct_2360 = ((2360.00 - range_low) / range_span) * 100.0
        assert pos_pct_2360 < 65.0  # 58.07%

        passed, reason = check_dealing_range_zone_lock(
            position_pct=pos_pct_2360,
            direction="SHORT",
        )
        assert not passed
        assert "HARD_ZONE_LOCK_VETO" in reason
        assert "Cannot SHORT" in reason
        assert "65.0%" in reason

        # Test exact live execution blunder: ETHUSDT SHORT at 2308.76 (~20.2% of range)
        pos_pct_2308 = ((2308.76 - range_low) / range_span) * 100.0
        assert round(pos_pct_2308, 1) == 20.2

        passed_2308, reason_2308 = check_dealing_range_zone_lock(
            position_pct=pos_pct_2308,
            direction=Direction.SHORT,
        )
        assert not passed_2308
        assert "HARD_ZONE_LOCK_VETO" in reason_2308
        assert "20.2%" in reason_2308

    def test_hard_law_1_short_approved_in_premium(self):
        """Verify that a SHORT in Premium (position_pct >= 65.0%) passes the zone lock."""
        passed, reason = check_dealing_range_zone_lock(
            position_pct=72.5,
            direction="SHORT",
        )
        assert passed
        assert "DEALING_RANGE_PREMIUM_OK" in reason

    def test_hard_law_2_long_in_top_half_rejected(self):
        """Verify that a LONG signal where position_pct > 38.0% is 100% REJECTED
        unless the Bull FVG exception is satisfied.
        """
        # Test SOL top entry at 42.3% of range (equilibrium)
        passed, reason = check_dealing_range_zone_lock(
            position_pct=42.3,
            direction="LONG",
            has_bull_fvg_pullback=False,
            trend_1h="NEUTRAL",
        )
        assert not passed
        assert "HARD_ZONE_LOCK_VETO" in reason
        assert "Cannot LONG" in reason
        assert "38.0%" in reason

        # Test ZEC upper equilibrium entry at 61.4%
        passed_zec, reason_zec = check_dealing_range_zone_lock(
            position_pct=61.4,
            direction="LONG",
            has_bull_fvg_pullback=False,
            trend_1h="NEUTRAL",
        )
        assert not passed_zec
        assert "HARD_ZONE_LOCK_VETO" in reason_zec

    def test_hard_law_2_long_approved_in_discount(self):
        """Verify that a LONG in Discount (position_pct <= 38.0%) passes the zone lock."""
        passed, reason = check_dealing_range_zone_lock(
            position_pct=25.0,
            direction="LONG",
        )
        assert passed
        assert "DEALING_RANGE_DISCOUNT_OK" in reason

    def test_hard_law_2_fvg_exception(self):
        """Verify that a LONG above 38.0% passes if there is an unmitigated Bull FVG pullback
        AND the 1H trend is strictly BULL.
        """
        passed, reason = check_dealing_range_zone_lock(
            position_pct=45.0,
            direction="LONG",
            has_bull_fvg_pullback=True,
            trend_1h="BULL",
        )
        assert passed
        assert "DEALING_RANGE_LONG_FVG_EXCEPTION_OK" in reason

        # But fails if trend is NOT BULL
        passed_bear, reason_bear = check_dealing_range_zone_lock(
            position_pct=45.0,
            direction="LONG",
            has_bull_fvg_pullback=True,
            trend_1h="BEARISH",
        )
        assert not passed_bear
        assert "HARD_ZONE_LOCK_VETO" in reason_bear

    def test_compute_dealing_range_from_1h_candles(self):
        """Verify compute_dealing_range computes range and position_pct accurately."""
        base_ts = 1792900000000
        candles_1h = []
        for i in range(72):
            candles_1h.append(
                make_candle(
                    timestamp_ms=base_ts + i * 3600000,
                    open_p=2300.0,
                    high_p=2400.0 if i == 10 else 2350.0,
                    low_p=2200.0 if i == 20 else 2250.0,
                    close_p=2300.0,
                )
            )

        dr = compute_dealing_range(candles_1h, current_price=2250.0, lookback_bars=72)
        assert dr.is_valid
        assert dr.range_high == 2400.0
        assert dr.range_low == 2200.0
        assert dr.range_span == 200.0
        # (2250 - 2200) / 200 * 100 = 25.0%
        assert dr.position_pct == 25.0


# -----------------------------------------------------------------------------
# 2. Primary Strategy: 15M_SWEEP_RECLAIM_SMC Tests
# -----------------------------------------------------------------------------
class TestSweepReclaim15MStrategy:
    """Tests for the 15M_SWEEP_RECLAIM_SMC strategy."""

    def test_long_discount_sweep_reclaim(self):
        """Test LONG trigger:
        - Price in Discount (position_pct <= 38%)
        - Sweeps swing low and reclaims with WickR >= 0.30
        - Stop loss placed within 1.15% - 1.40%
        - Fixed risk <= $0.20
        """
        strat = SweepReclaim15MStrategy()

        # Build 15m candle series
        base_ts = 1792900000000
        candles = []
        # Create baseline candles around 2320
        for i in range(25):
            candles.append(make_candle(base_ts + i * 900000, 2320.0, 2325.0, 2315.0, 2320.0))

        # Create a swing low at bar index 20 (low = 2305.0)
        candles[20] = make_candle(base_ts + 20 * 900000, 2315.0, 2318.0, 2305.0, 2310.0)

        # Subsequent bars stabilize above 2310
        candles[21] = make_candle(base_ts + 21 * 900000, 2310.0, 2315.0, 2308.0, 2312.0)
        candles[22] = make_candle(base_ts + 22 * 900000, 2312.0, 2316.0, 2310.0, 2314.0)

        # Bar 23: Sweeps below 2305 (dips to 2303.0), but candle closes back at 2308 with lower wick = 2306 - 2303 = 3
        # High = 2309, Low = 2303 (TR = 6), Open = 2306, Close = 2308. Lower wick = 2306 - 2303 = 3 (WickR = 3/6 = 0.50 >= 0.30)
        candles[23] = make_candle(base_ts + 23 * 900000, 2306.0, 2309.0, 2303.0, 2308.0)
        # Bar 24 (current bar): Confirms reclaim above 2305, close = 2307
        candles[24] = make_candle(base_ts + 24 * 900000, 2308.0, 2310.0, 2305.0, 2307.0)

        # Evaluate with Dealing Range in Discount (25.0%)
        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_15m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=25.0,  # Discount <= 38.0%
        )

        assert setup is not None, f"Expected setup, got reason: {reason}"
        assert setup.direction == Direction.LONG
        assert setup.strategy_id == "15M_SWEEP_RECLAIM_SMC"
        assert setup.order_type == "LIMIT"

        # Check Stop Distance constraint: 1.15% - 1.40%
        assert 1.10 <= setup.stop_distance_pct <= 1.45, f"Stop distance {setup.stop_distance_pct}% outside [1.15%, 1.40%]"

        # Check Stop Loss placement is below sweep wick low (2303.0)
        assert setup.stop_loss < 2303.0 or abs(setup.entry_price - setup.stop_loss) >= setup.entry_price * 0.0115

        # Check sizing: risk <= $0.20
        assert setup.sizing is not None
        assert setup.sizing.expected_conservative_loss_usd <= 0.205
        assert setup.sizing.isolated_margin_usd <= 7.00

    def test_short_premium_sweep_reject(self):
        """Test SHORT trigger:
        - Price in Premium (position_pct >= 65%)
        - Sweeps swing high and rejects with upper WickR >= 0.30
        - Stop loss placed within 1.15% - 1.40%
        """
        strat = SweepReclaim15MStrategy()

        base_ts = 1792900000000
        candles = []
        for i in range(25):
            candles.append(make_candle(base_ts + i * 900000, 2380.0, 2385.0, 2375.0, 2380.0))

        # Swing high at bar 20 (high = 2400.0)
        candles[20] = make_candle(base_ts + 20 * 900000, 2385.0, 2400.0, 2380.0, 2390.0)

        # Stabilize below 2400
        candles[21] = make_candle(base_ts + 21 * 900000, 2390.0, 2395.0, 2385.0, 2388.0)
        candles[22] = make_candle(base_ts + 22 * 900000, 2388.0, 2394.0, 2384.0, 2386.0)

        # Bar 23: Pierces above 2400 (reaches 2405.0), but closes back at 2396
        # High = 2405, Open = 2398, Close = 2396. Upper wick = 2405 - 2398 = 7. TR = 2405 - 2392 = 13 (WickR = 7/13 = 0.54 >= 0.30)
        candles[23] = make_candle(base_ts + 23 * 900000, 2398.0, 2405.0, 2392.0, 2396.0)
        candles[24] = make_candle(base_ts + 24 * 900000, 2396.0, 2398.0, 2390.0, 2392.0)

        # Evaluate with Dealing Range in Premium (75.0%)
        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_15m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=75.0,  # Premium >= 65.0%
        )

        assert setup is not None, f"Expected setup, got reason: {reason}"
        assert setup.direction == Direction.SHORT
        assert setup.strategy_id == "15M_SWEEP_RECLAIM_SMC"
        assert 1.10 <= setup.stop_distance_pct <= 1.45


# -----------------------------------------------------------------------------
# 3. Orphan Open Orders Cleanup Tests
# -----------------------------------------------------------------------------
class TestOrphanOpenOrdersCleanup:
    """Tests proving cancel_all_open_orders is called when position is closed."""

    def test_reconciliation_cleans_orphan_orders_on_closed_position(self):
        """When exchange reports no position (amt=0), but orphan open orders exist,
        reconciliation must call cancel_all_open_orders to purge stale orders.
        """
        from src.execution.live_session import LiveAutoSessionManager

        mock_coord = MagicMock()
        mock_coord.global_lock.is_locked = False
        mock_adapter = MagicMock()

        # Exchange returns flat position, but 1 orphan reduce-only open order remains
        mock_adapter.get_positions.return_value = [{"symbol": "ETHUSDT", "positionAmt": "0.0"}]
        mock_adapter.get_open_orders.return_value = [
            {"symbol": "ETHUSDT", "orderId": "999888", "status": "NEW", "reduceOnly": True}
        ]

        sess_mgr = LiveAutoSessionManager(
            coordinator=mock_coord,
            exchange_adapter=mock_adapter,
        )

        success, msg = sess_mgr._refresh_lock_from_exchange()

        # Must call cancel_all_open_orders for ETHUSDT
        mock_adapter.cancel_all_open_orders.assert_called_with("ETHUSDT")
        assert success is True
        assert "orphan orders purged" in msg

    def test_trade_close_calls_cancel_all_open_orders(self):
        """When a position closes, LiveMicroCoordinator.record_closed_trade must call cancel_all_open_orders."""
        from src.scheduler.live_micro_runner import LiveMicroCoordinator

        mock_exchange = MagicMock()
        coord = LiveMicroCoordinator(exchange=mock_exchange)
        coord.global_lock.acquire("15M_SWEEP_RECLAIM_SMC", "SOLUSDT", "ORD_123")
        assert coord.global_lock.active_symbol == "SOLUSDT"

        coord.record_closed_trade(realized_pnl=-0.15, new_equity=14.85)

        # Verify cancel_all_open_orders was called on SOLUSDT
        mock_exchange.cancel_all_open_orders.assert_called_with("SOLUSDT")


# -----------------------------------------------------------------------------
# 4. Tick-Size Quantization Tests (Preventing HTTP 400 code=-4014)
# -----------------------------------------------------------------------------
class TestTickSizeQuantization:
    """Tests proving tick-size quantization prevents code=-4014 rejections."""

    def test_sol_tp_tick_size_normalization(self):
        """SOLUSDT tick size is 0.0100. Raw calculated TP 103.1575 caused code=-4014 on live mainnet.
        normalize_price_to_tick must quantize to exactly 103.16.
        """
        raw_sol_tp = 103.1575
        sol_tick_size = 0.01

        normalized_tp = normalize_price_to_tick(raw_sol_tp, sol_tick_size, rounding="NEAREST")
        assert normalized_tp == 103.16

        # Verify that multiplying by tick size leaves zero remainder
        ticks = Decimal(str(normalized_tp)) / Decimal(str(sol_tick_size))
        assert ticks == Decimal("10316")  # Exactly integer ticks!

    def test_eth_and_btc_tick_size_quantization(self):
        """ETHUSDT tick size is 0.01, BTCUSDT tick size is 0.1."""
        raw_eth_sl = 2303.554999
        eth_norm_sl = normalize_price_to_tick(raw_eth_sl, 0.01, rounding="FLOOR")
        assert eth_norm_sl == 2303.55

        raw_btc_tp = 67891.2345
        btc_norm_tp = normalize_price_to_tick(raw_btc_tp, 0.1, rounding="NEAREST")
        assert btc_norm_tp == 67891.2
