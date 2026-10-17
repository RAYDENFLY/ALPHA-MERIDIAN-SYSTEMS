"""Unit tests for SMC Market Structure, Swings, BOS, CHoCH, Liquidity Sweeps, OB, and FVG.
"""

import pytest
from src.core.types import Candle, Direction, SMCEventType, StructureState, SwingType
from src.smc.fvg import FVGDetector
from src.smc.liquidity import LiquiditySweepDetector
from src.smc.order_blocks import OrderBlockDetector
from src.smc.structure import MarketStructureEngine
from src.smc.swings import detect_swings


def make_candle_list(prices: list[tuple[float, float, float, float]]) -> list[Candle]:
    """Helper to create chronological candles from (O, H, L, C) tuples."""
    base_ts = 1700000000000
    candles = []
    for i, (o, h, l, c) in enumerate(prices):
        ts = base_ts + i * 3600000
        candles.append(
            Candle(
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
                trades_count=100,
                is_closed=True,
            )
        )
    return candles


class TestSMCSwings:
    def test_swing_confirmation_no_lookahead(self):
        # Create a peak at index 3: prices = [10, 12, 14, 20, 15, 13, 11]
        # left=2, right=2. Peak at idx 3 (price 20).
        # It must be confirmed ONLY at index 3 + 2 = 5!
        prices = [
            (10, 11, 9, 10),  # 0
            (10, 13, 9, 12),  # 1
            (12, 15, 11, 14), # 2
            (14, 20, 13, 19), # 3: Peak High = 20
            (19, 16, 14, 15), # 4
            (15, 14, 12, 13), # 5: Confirmation candle for index 3
            (13, 12, 10, 11), # 6
        ]
        candles = make_candle_list(prices)

        # Evaluate swings as of index 4 (before confirmation)
        swings_at_4 = detect_swings(candles, left_bars=2, right_bars=2, max_index=4)
        assert len(swings_at_4) == 0  # Index 3 is not yet confirmed at index 4!

        # Evaluate swings as of index 5 (confirmation bar)
        swings_at_5 = detect_swings(candles, left_bars=2, right_bars=2, max_index=5)
        assert len(swings_at_5) == 1
        assert swings_at_5[0].index == 3
        assert swings_at_5[0].confirmed_index == 5
        assert swings_at_5[0].price == 20.0
        assert swings_at_5[0].swing_type == SwingType.HIGH


class TestSMCStructure:
    def test_bos_and_choch(self):
        # Construct a sequence:
        # Bearish phase -> Lower High (50), Lower Low (20)
        # Then strong reversal breaking 50 -> Bullish CHoCH!
        prices = [
            (50, 52, 48, 49), # 0
            (49, 51, 47, 48), # 1
            (48, 55, 47, 54), # 2: High 55
            (54, 52, 40, 42), # 3
            (42, 44, 38, 40), # 4
            (40, 41, 30, 32), # 5: Low 30
            (32, 35, 29, 31), # 6
            (31, 34, 28, 30), # 7
            (30, 45, 29, 44), # 8: Bullish displacement
            (44, 60, 43, 58), # 9: Closes at 58 (breaks prior high 55) -> CHoCH
        ]
        candles = make_candle_list(prices)
        swings = detect_swings(candles, left_bars=1, right_bars=1)
        state, events = MarketStructureEngine.evaluate_structure(candles, swings)

        # There should be structural events detected
        assert len(events) > 0
        assert any(e.event_type in (SMCEventType.CHOCH, SMCEventType.BOS) for e in events)


class TestLiquiditySweep:
    def test_liquidity_sweep_detection(self):
        # Swing High at index 2 (price 100), confirmed at index 4.
        # Candle at index 6 wicks to 105, but closes at 97 -> Bearish Liquidity Sweep!
        prices = [
            (90, 92, 88, 91),   # 0
            (91, 95, 90, 94),   # 1
            (94, 100, 93, 98),  # 2: Swing High = 100
            (98, 96, 92, 94),   # 3
            (94, 95, 91, 93),   # 4: Confirms swing high
            (93, 95, 90, 92),   # 5
            (92, 105, 91, 97),  # 6: Wick = 105 (>100), Close = 97 (<100) -> SWEEP!
        ]
        candles = make_candle_list(prices)
        swings = detect_swings(candles, left_bars=1, right_bars=1)
        sweeps = LiquiditySweepDetector.detect_sweeps(candles, swings)

        assert len(sweeps) == 1
        assert sweeps[0].event_type == SMCEventType.LIQUIDITY_SWEEP
        assert sweeps[0].direction == Direction.SHORT
        assert sweeps[0].price_level == 100.0


class TestFVG:
    def test_bullish_fvg_and_mitigation(self):
        # Candle 0: High = 50
        # Candle 1: Big impulse up (Open=50, High=75, Low=49, Close=74)
        # Candle 2: Low = 58 (58 > 50 -> Bullish FVG [50, 58])
        # Candle 3: Low = 52 (mitigates FVG)
        prices = [
            (45, 50, 42, 48), # 0: C1 High = 50
            (49, 75, 48, 74), # 1: C2 Big impulse
            (74, 80, 58, 78), # 2: C3 Low = 58 (>50)
            (78, 79, 52, 70), # 3: Dips to 52 (enters [50, 58]) -> Mitigated
        ]
        candles = make_candle_list(prices)
        fvgs = FVGDetector.detect_fvgs(candles, min_gap_size_atr_ratio=0.01)

        assert len(fvgs) == 1
        assert fvgs[0].direction == Direction.LONG
        assert fvgs[0].bottom == 50.0
        assert fvgs[0].top == 58.0
        assert fvgs[0].mitigated is True
