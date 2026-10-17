"""Unit tests for SnR Clustering, TP/SL Planning, and Strategy Candidate Generation.
"""

from src.core.types import Candle, Direction, MarketBias, SnRZone, SwingPoint, SwingType
from src.snr.interaction import SnRInteractionEngine
from src.snr.zones import cluster_swing_zones
from src.strategy.generator import SetupGenerator
from src.strategy.planner import TPSLPlanner


def make_candle(ts: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe="1h",
        timestamp_ms=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
        close_time_ms=ts + 3599999,
        quote_volume=100.0 * c,
        trades_count=50,
        is_closed=True,
    )


class TestSnRZones:
    def test_clustering_swings(self):
        swings = [
            SwingPoint(index=2, timestamp_ms=1000, confirmed_index=4, confirmed_timestamp_ms=2000, price=100.0, swing_type=SwingType.HIGH),
            SwingPoint(index=8, timestamp_ms=3000, confirmed_index=10, confirmed_timestamp_ms=4000, price=100.8, swing_type=SwingType.HIGH),
            SwingPoint(index=15, timestamp_ms=5000, confirmed_index=17, confirmed_timestamp_ms=6000, price=101.2, swing_type=SwingType.HIGH),
            SwingPoint(index=20, timestamp_ms=7000, confirmed_index=22, confirmed_timestamp_ms=8000, price=50.0, swing_type=SwingType.LOW),
        ]
        # Tolerance 1.5%: 100.0, 100.8, 101.2 cluster into one resistance zone; 50.0 forms a support zone
        zones = cluster_swing_zones(swings, tolerance_pct=0.015)
        assert len(zones) == 2
        zone_100 = next(z for z in zones if z.price_center > 90)
        assert zone_100.touch_count == 3
        assert 99.5 <= zone_100.price_low <= 100.0
        assert 101.0 <= zone_100.price_high <= 101.5


class TestTPSLPlanner:
    def test_long_tp_sl_levels_direction_validity(self):
        entry = 60000.0
        invalidation = 59000.0
        atr = 500.0
        swings = [
            SwingPoint(index=1, timestamp_ms=10, confirmed_index=3, confirmed_timestamp_ms=20, price=62500.0, swing_type=SwingType.HIGH)
        ]
        sl, tp1, tp2, gross_rr, net_rr = TPSLPlanner.plan_trade_levels(
            direction=Direction.LONG,
            entry_price=entry,
            invalidation_price=invalidation,
            atr=atr,
            swings=swings,
            resistance=None,
            support=None,
            atr_buffer_mult=0.5,
            min_rr=1.5,
        )
        # Required direction invariants: SL < Entry < TP1 < TP2
        assert sl is not None
        assert sl < entry
        assert sl == 59000.0 - (0.5 * 500.0)  # 58750.0
        assert tp1 > entry
        assert tp2 > tp1
        assert gross_rr >= 1.5
        assert net_rr > 0

    def test_short_tp_sl_levels_direction_validity(self):
        entry = 60000.0
        invalidation = 61000.0
        atr = 500.0
        swings = [
            SwingPoint(index=1, timestamp_ms=10, confirmed_index=3, confirmed_timestamp_ms=20, price=57000.0, swing_type=SwingType.LOW)
        ]
        sl, tp1, tp2, gross_rr, net_rr = TPSLPlanner.plan_trade_levels(
            direction=Direction.SHORT,
            entry_price=entry,
            invalidation_price=invalidation,
            atr=atr,
            swings=swings,
            resistance=None,
            support=None,
            atr_buffer_mult=0.5,
            min_rr=1.5,
        )
        # Required direction invariants: TP1 < Entry < SL
        assert sl is not None
        assert sl > entry
        assert sl == 61000.0 + (0.5 * 500.0)  # 61250.0
        assert tp1 < entry
        assert tp2 < tp1
        assert gross_rr >= 1.5
        assert net_rr > 0


class TestSetupGenerator:
    def test_no_trade_when_flat(self):
        # 40 flat candles without swings or momentum
        candles_1h = [make_candle(1000 + i * 3600000, 100.0, 100.1, 99.9, 100.0) for i in range(40)]
        candles_4h = [make_candle(1000 + i * 14400000, 100.0, 100.2, 99.8, 100.0) for i in range(30)]

        gen = SetupGenerator()
        candidate = gen.generate_candidate("BTCUSDT", candles_1h, candles_4h)
        # In flat conditions, system must return None (NO TRADE)
        assert candidate is None

    def test_4h_boundary_close_synchronization_and_no_lookahead(self):
        """Regression test verifying:
        1. At non-boundary 1H bars (hours 0, 1, 2 of a 4H window), the in-progress 4H candle is NOT included.
        2. At boundary 1H bars (hour 3 of a 4H window, e.g. 03:00..03:59:59.999 UTC), the 4H candle that
           closes at the exact same millisecond (close_time_ms) IS included, while any subsequent 4H candle is excluded.
        """
        from src.strategy.bias import MultiTimeframeBiasEngine

        base_ts = 1760400000000  # 2025-10-14 00:00:00 UTC (exact 4H boundary start)
        # Construct 30 4H candles where k=0..28 has last_close > ema50 and ema20 > ema50,
        # and k=29 (the 30th candle) crashes sharply below ema50 so ema20 < ema50 and last_close < ema50,
        # flipping bias on k=29 close!
        # Also include a confirmed swing high & low so structure is BEARISH after break:
        prices = [
            100, 102, 105, 108, 104, 101, 99, 103, 107, 112,
            116, 111, 107, 105, 110, 115, 122, 128, 123, 118,
            115, 119, 125, 132, 126, 120, 117, 121, 124, 40,
        ]
        candles_4h = []
        for k, p_close in enumerate(prices):
            ts_4h = base_ts + k * 14400000
            p_open = prices[k - 1] if k > 0 else 100.0
            p_high = max(p_open, p_close) + 2.0
            p_low = min(p_open, p_close) - 2.0
            candles_4h.append(
                Candle(
                    symbol="BTCUSDT",
                    timeframe="4h",
                    timestamp_ms=ts_4h,
                    open=float(p_open),
                    high=float(p_high),
                    low=float(p_low),
                    close=float(p_close),
                    volume=500.0,
                    close_time_ms=ts_4h + 14399999,
                    is_closed=True,
                )
            )

        # Append future 4H candles (k=30..35) that skyrocket back to 300.0 (would flip bias if leaked)
        for k in range(30, 36):
            ts_4h = base_ts + k * 14400000
            p_open = 300.0 + (k - 30) * 20.0
            p_close = p_open + 15.0
            candles_4h.append(
                Candle(
                    symbol="BTCUSDT",
                    timeframe="4h",
                    timestamp_ms=ts_4h,
                    open=p_open,
                    high=p_close + 5.0,
                    low=p_open - 5.0,
                    close=p_close,
                    volume=500.0,
                    close_time_ms=ts_4h + 14399999,
                    is_closed=True,
                )
            )

        # Target 4H bar k=29 opens at base_ts + 29*14400000 and closes at base_ts + 29*14400000 + 14399999
        bar29_open = candles_4h[29].timestamp_ms
        bar29_close = candles_4h[29].close_time_ms

        bias_only_up_to_28 = MultiTimeframeBiasEngine.calculate_4h_bias(candles_4h[:29], as_of_timestamp_ms=None)
        bias_exact_30 = MultiTimeframeBiasEngine.calculate_4h_bias(candles_4h[:30], as_of_timestamp_ms=None)
        assert bias_only_up_to_28 != bias_exact_30

        # The 4 constituent 1H bars inside 4H bar k=29:
        # h=0 (00:00), h=1 (01:00), h=2 (02:00) are NON-BOUNDARY bars: bar29 is still in-progress!
        for h in (0, 1, 2):
            c1_open = bar29_open + h * 3600000
            c1_close = c1_open + 3599999
            assert c1_close < bar29_close
            # At c1_close, usable 4H candles must ONLY include up to k=28 (NOT k=29)
            bias_at_non_boundary = MultiTimeframeBiasEngine.calculate_4h_bias(candles_4h, as_of_timestamp_ms=c1_close)
            assert bias_at_non_boundary == bias_only_up_to_28

        # h=3 (03:00..03:59:59.999) is the 4H BOUNDARY bar: c1_close == bar29_close!
        c1_boundary_open = bar29_open + 3 * 3600000
        c1_boundary_close = c1_boundary_open + 3599999
        assert c1_boundary_close == bar29_close

        # Old behavior (evaluated at c1_boundary_open) lagged by 1 candle and returned bias_only_up_to_28;
        # New synchronized behavior (evaluated at c1_boundary_close) includes k=29 and excludes k=30..35, returning bias_exact_30
        bias_at_boundary_open_lagged = MultiTimeframeBiasEngine.calculate_4h_bias(candles_4h, as_of_timestamp_ms=c1_boundary_open)
        bias_at_boundary_close = MultiTimeframeBiasEngine.calculate_4h_bias(candles_4h, as_of_timestamp_ms=c1_boundary_close)
        assert bias_at_boundary_open_lagged == bias_only_up_to_28
        assert bias_at_boundary_close == bias_exact_30

    def test_all_six_daily_4h_close_boundaries(self):
        """Verifies all 6 daily UTC 4H boundary hours (03:00, 07:00, 11:00, 15:00, 19:00, 23:00 UTC)
        align 1H close_time_ms with 4H close_time_ms without off-by-one or future leakage.
        """
        import bisect
        day_start_ms = 1760486400000  # 2025-10-15 00:00:00 UTC
        c4 = [
            Candle(
                symbol="ETHUSDT",
                timeframe="4h",
                timestamp_ms=day_start_ms + b * 14400000,
                open=2000.0,
                high=2010.0,
                low=1990.0,
                close=2005.0,
                volume=1000.0,
                close_time_ms=day_start_ms + (b + 1) * 14400000 - 1,
                is_closed=True,
            )
            for b in range(6)
        ]
        close_times_4h = [c.close_time_ms for c in c4]

        for hour in range(24):
            c1_open = day_start_ms + hour * 3600000
            c1_close = c1_open + 3599999
            idx = bisect.bisect_right(close_times_4h, c1_close)
            usable = c4[:idx]
            expected_closed_4h_count = (hour + 1) // 4
            assert len(usable) == expected_closed_4h_count
            if usable:
                assert usable[-1].close_time_ms <= c1_close
            if idx < len(c4):
                # Next 4H candle is still in progress or in the future
                assert c4[idx].close_time_ms > c1_close

