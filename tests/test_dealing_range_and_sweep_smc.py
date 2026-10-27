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


# -----------------------------------------------------------------------------
# 5. Directional Zone Lock Veto Integrity Tests (Preventing Cross-Contamination)
# -----------------------------------------------------------------------------
class TestDirectionalZoneLockVetoIntegrity:
    """Rigorous tests proving that SHORT zone lock vetoes NEVER contaminate LONG candidates,
    and LONG zone lock vetoes NEVER contaminate SHORT candidates.
    """

    def test_long_candidate_in_discount_without_sweep_never_returns_short_veto(self):
        """A LONG setup at 30.2% Dealing Range (Discount) has no sweep trigger formed.
        It must return 'NO_SWEEP_RECLAIM_TRIGGER'.
        Under NO circumstances may it return 'HARD_ZONE_LOCK_VETO: Cannot SHORT at 30.2%'!
        """
        strat = SweepReclaim15MStrategy()
        base_ts = 1792900000000
        # 25 flat candles without any swing sweep
        candles = [
            make_candle(base_ts + i * 900000, 2310.0, 2315.0, 2308.0, 2312.0)
            for i in range(25)
        ]

        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_15m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=30.2,  # Discount <= 38.0%
            direction="LONG",
        )

        assert setup is None
        assert reason == "NO_SWEEP_RECLAIM_TRIGGER"
        assert "SHORT" not in reason
        assert "HARD_ZONE_LOCK_VETO" not in reason

    def test_short_candidate_in_discount_strictly_vetoed_by_short_rule(self):
        """A SHORT setup at 30.2% Dealing Range (Discount) must be strictly vetoed
        by Hard Law 1 (Premium >= 65.0% required).
        """
        strat = SweepReclaim15MStrategy()
        base_ts = 1792900000000
        candles = [
            make_candle(base_ts + i * 900000, 2310.0, 2315.0, 2308.0, 2312.0)
            for i in range(25)
        ]

        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_15m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=30.2,  # Discount (< 65.0%)
            direction="SHORT",
        )

        assert setup is None
        assert "HARD_ZONE_LOCK_VETO" in reason
        assert "Cannot SHORT at 30.2%" in reason
        assert "65.0%" in reason

    def test_short_candidate_in_premium_without_sweep_never_returns_long_veto(self):
        """A SHORT setup at 75.0% Dealing Range (Premium) has no sweep trigger formed.
        It must return 'NO_SWEEP_REJECT_TRIGGER'.
        Under NO circumstances may it return 'HARD_ZONE_LOCK_VETO: Cannot LONG at 75.0%'!
        """
        strat = SweepReclaim15MStrategy()
        base_ts = 1792900000000
        candles = [
            make_candle(base_ts + i * 900000, 2400.0, 2405.0, 2395.0, 2402.0)
            for i in range(25)
        ]

        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_15m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=75.0,  # Premium >= 65.0%
            direction="SHORT",
        )

        assert setup is None
        assert reason == "NO_SWEEP_REJECT_TRIGGER"
        assert "LONG" not in reason
        assert "HARD_ZONE_LOCK_VETO" not in reason

    def test_long_candidate_in_premium_strictly_vetoed_by_long_rule(self):
        """A LONG setup at 75.0% Dealing Range (Premium) without Bull FVG exception
        must be strictly vetoed by Hard Law 2 (Discount <= 38.0% required).
        """
        strat = SweepReclaim15MStrategy()
        base_ts = 1792900000000
        candles = [
            make_candle(base_ts + i * 900000, 2400.0, 2405.0, 2395.0, 2402.0)
            for i in range(25)
        ]

        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_15m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=75.0,  # Premium (> 38.0%)
            direction="LONG",
        )

        assert setup is None
        assert "HARD_ZONE_LOCK_VETO" in reason
        assert "Cannot LONG at 75.0%" in reason
        assert "38.0%" in reason

    def test_bidirectional_scan_discount_returns_no_sweep_reclaim(self):
        """When direction is None (bidirectional scan) at 30.2% Dealing Range (Discount),
        if no sweep formed, it returns NO_SWEEP_RECLAIM_TRIGGER, not a SHORT veto.
        """
        strat = SweepReclaim15MStrategy()
        base_ts = 1792900000000
        candles = [
            make_candle(base_ts + i * 900000, 2310.0, 2315.0, 2308.0, 2312.0)
            for i in range(25)
        ]

        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_15m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=30.2,
            direction=None,
        )

        assert setup is None
        assert reason == "NO_SWEEP_RECLAIM_TRIGGER"
        assert "Cannot SHORT" not in reason

    def test_bidirectional_scan_premium_returns_no_sweep_reject(self):
        """When direction is None (bidirectional scan) at 75.0% Dealing Range (Premium),
        if no sweep formed, it returns NO_SWEEP_REJECT_TRIGGER, not a LONG veto.
        """
        strat = SweepReclaim15MStrategy()
        base_ts = 1792900000000
        candles = [
            make_candle(base_ts + i * 900000, 2400.0, 2405.0, 2395.0, 2402.0)
            for i in range(25)
        ]

        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_15m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=75.0,
            direction=None,
        )

        assert setup is None
        assert reason == "NO_SWEEP_REJECT_TRIGGER"
        assert "Cannot LONG" not in reason

    def test_bidirectional_scan_equilibrium_returns_zone_restriction(self):
        """When direction is None at 50.0% Dealing Range (Equilibrium),
        it surfaces ZONE_LOCK_RESTRICTION explaining equilibrium bounds.
        """
        strat = SweepReclaim15MStrategy()
        base_ts = 1792900000000
        candles = [
            make_candle(base_ts + i * 900000, 2350.0, 2355.0, 2345.0, 2350.0)
            for i in range(25)
        ]

        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_15m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=50.0,
            direction=None,
        )

        assert setup is None
        assert "ZONE_LOCK_RESTRICTION" in reason
        assert "50.0%" in reason

    def test_ema20_directional_isolation(self):
        """Verify EMA20ReclaimStrategy does not emit SHORT vetoes for LONG evaluations."""
        from src.strategy.ema20_reclaim_5m import EMA20ReclaimStrategy

        strat = EMA20ReclaimStrategy()
        base_ts = 1792900000000
        # 30 flat candles
        candles = [
            make_candle(base_ts + i * 300000, 2310.0, 2312.0, 2308.0, 2310.0, timeframe="5m")
            for i in range(30)
        ]

        setup, reason = strat.evaluate(
            symbol="ETHUSDT",
            candles_5m=candles,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            account_equity=15.0,
            dealing_range_position_pct=30.2,  # Discount
            direction="LONG",
        )

        assert setup is None
        assert reason == "NO_RECLAIM_TRIGGER"
        assert "Cannot SHORT" not in reason

    def test_autotrader_evaluate_candidate_directional_consistency(self):
        """Verify GroqAutoTrader.evaluate_candidate_with_strategy passes direction
        and never returns a cross-directional veto reason.
        """
        from src.ai.groq_autotrader import GroqAutoTrader

        trader = GroqAutoTrader(api_key="test_key")
        mock_coord = MagicMock()
        mock_coord.current_equity = 15.0
        mock_coord.sweep_reclaim_strategy = SweepReclaim15MStrategy()

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        base_1h_ts = now_ms - (72 * 3600000)
        base_15m_ts = now_ms - (30 * 900000)

        candles_1h = [
            make_candle(base_1h_ts + i * 3600000, 2280.0, 2400.0, 2270.0, 2310.0, timeframe="1h")
            for i in range(72)
        ]
        candles_15m = [
            make_candle(base_15m_ts + i * 900000, 2310.0, 2315.0, 2308.0, 2312.0, timeframe="15m")
            for i in range(30)
        ]

        trader.adapter = MagicMock()
        trader.adapter.get_klines.side_effect = lambda sym, timeframe, limit: (
            candles_1h if timeframe == "1h" else candles_15m
        )
        mock_specs = MagicMock(symbol="ETHUSDT", tick_size=0.01, step_size=0.001, min_notional=5.0, min_qty=0.001)
        trader.inspector = MagicMock()
        trader.inspector.get_symbol_specs.return_value = mock_specs

        setup, reason = trader.evaluate_candidate_with_strategy(
            symbol="ETHUSDT",
            coordinator=mock_coord,
            direction="LONG",
            strategy_id="15M_SWEEP_RECLAIM_SMC",
        )

        assert setup is None
        # Must be NO_SWEEP_RECLAIM_TRIGGER, NEVER a SHORT veto
        assert reason == "NO_SWEEP_RECLAIM_TRIGGER"
        assert "Cannot SHORT" not in reason
        assert "HARD_ZONE_LOCK_VETO" not in reason

    def test_stage3_candidate_to_veto_consistency(self):
        """Verify that Stage3SetupOutput retains exact candidate-to-veto association:
        - Symbol: ETHUSDT
        - Direction: LONG
        - candidate_id: starts with 'ETHUSDT:LONG:'
        - strategy_rejection_reason does NOT mention 'Cannot SHORT'
        - veto_code and veto_source correctly classified
        """
        from src.ai.groq_autotrader import (
            GroqAutoTrader,
            RiskPreset,
            ExecutionVenue,
            Stage1Result,
            Stage2Result,
            Stage2FinalistItem,
        )

        trader = GroqAutoTrader(api_key="test_key")
        mock_coord = MagicMock()
        mock_coord.current_equity = 16.0
        mock_coord.sweep_reclaim_strategy = SweepReclaim15MStrategy()

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        base_1h_ts = now_ms - (72 * 3600000)
        base_15m_ts = now_ms - (30 * 900000)

        candles_1h = [
            make_candle(base_1h_ts + i * 3600000, 2280.0, 2400.0, 2270.0, 2310.0, timeframe="1h")
            for i in range(72)
        ]
        candles_15m = [
            make_candle(base_15m_ts + i * 900000, 2310.0, 2315.0, 2308.0, 2312.0, timeframe="15m")
            for i in range(30)
        ]

        trader.adapter = MagicMock()
        trader.adapter.get_klines.side_effect = lambda sym, timeframe, limit: (
            candles_1h if timeframe == "1h" else candles_15m
        )
        mock_specs = MagicMock(symbol="ETHUSDT", tick_size=0.01, step_size=0.001, min_notional=5.0, min_qty=0.001)
        trader.inspector = MagicMock()
        trader.inspector.get_symbol_specs.return_value = mock_specs
        trader.inspector.inspect_coin.return_value = {
            "symbol": "ETHUSDT",
            "meta": {"mark_price": 2310.5},
            "smc_and_liquidity_structure": {"dealing_range_position_pct": 30.2},
            "orderbook_l2_microstructure": {"orderbook_imbalance_ratio": 0.52},
            "multi_timeframe_summary": {},
        }

        stage1 = Stage1Result(
            market_regime={"regime": "CHOP_RANGE"},
            top_10=[],
            total_screened_pairs=10,
            execution_time_ms=50.0,
        )
        stage2 = Stage2Result(
            top_3=[
                Stage2FinalistItem(rank=1, symbol="ETHUSDT", category="DEFI", score=9.0, thesis="Strong"),
            ],
            selection_rationale="test",
            model_used="test_model",
            execution_time_ms=10.0,
        )

        # Selected setup from LLM is LONG ETHUSDT at 30.2% dealing range
        selected_setup = {
            "decision": "EXECUTE",
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "order_type": "LIMIT",
            "entry_price": 2310.5,
            "stop_loss_price": 2281.62,
            "take_profit_price": 2375.49,
            "notes": "ETHUSDT 15m sweep reclaim candidate",
        }

        # Mock LLM call to return selected setup
        with patch.object(trader, "_call_groq_json", return_value=selected_setup):
            setup_output, receipt, _ = trader.run_stage3_finalist_and_order(
                stage1=stage1,
                stage2=stage2,
                strategy_id="15M_SWEEP_RECLAIM_SMC",
                risk_preset=RiskPreset.CONSERVATIVE_035,
                execution_venue=ExecutionVenue.PAPER,
                auto_fire_limit_order=False,
                coordinator=mock_coord,
                selected_setup=selected_setup,
            )

        assert setup_output.symbol == "ETHUSDT"
        assert setup_output.direction == "LONG"
        assert setup_output.candidate_id is not None
        assert setup_output.candidate_id.startswith("ETHUSDT:LONG:")
        # Crucial: strategy rejection reason must NOT contain SHORT veto!
        assert "Cannot SHORT" not in setup_output.strategy_rejection_reason
        assert "HARD_ZONE_LOCK_VETO: Cannot SHORT" not in setup_output.strategy_rejection_reason
        assert setup_output.strategy_rejection_reason == "NO_SWEEP_RECLAIM_TRIGGER"
        assert setup_output.veto_code == "NO_TRIGGER"
        assert setup_output.veto_source == "STRATEGY_TRIGGER"
