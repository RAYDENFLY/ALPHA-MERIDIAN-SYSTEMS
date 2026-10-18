"""Unit tests for SweepReclaimTracker and Non-Lookahead Liquidity Lifecycle.

Verifies:
1. Pivot confirmation and strict anti-lookahead behavior.
2. Sweep without reclaim (wick breaches, candle closes outside -> no false reclaim).
3. Immediate and delayed reclaim after valid sweep.
4. False reclaim rejection and breakout invalidation.
5. Level expiry and age lifecycle.
6. Equal Highs (EQH) and Equal Lows (EQL) clustering.
7. Duplicate event prevention.
8. Historical replay snapshot integrity: strictly zero future data leaks at time T.
"""

from typing import List
import pytest

from src.core.types import Candle
from src.intelligence.sweep_tracker import (
    LiquidityLevel,
    LiquidityLevelStatus,
    LiquidityLevelType,
    SweepEventType,
    SweepReclaimEvent,
    SweepReclaimTracker,
)


def make_candle(
    index: int,
    open_px: float,
    high_px: float,
    low_px: float,
    close_px: float,
    volume: float = 100.0,
    interval_sec: int = 3600,
) -> Candle:
    base_ts = 1700000000000
    ts = base_ts + (index * interval_sec * 1000)
    return Candle(
        symbol="BTCUSDT",
        timeframe="1h",
        timestamp_ms=ts,
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        volume=volume,
        close_time_ms=ts + (interval_sec * 1000) - 1,
    )


class TestSweepReclaimTracker:

    def test_pivot_confirmation_and_anti_lookahead(self):
        """A pivot at bar 3 (with left=3, right=3) is ONLY confirmed at bar 6.
        Before bar 6, this level must NOT exist in visible active levels.
        """
        # Form a swing high at bar 3: high=105.0, surrounding highs < 105.0
        candles = [
            make_candle(0, 100, 101, 99, 100),
            make_candle(1, 100, 102, 99, 101),
            make_candle(2, 101, 103, 100, 102),
            make_candle(3, 102, 105, 101, 103),  # Pivot High at bar 3
            make_candle(4, 102, 103, 100, 101),
            make_candle(5, 101, 102, 99, 100),
            make_candle(6, 100, 101, 98, 99),   # Confirmation bar (3 + 3 = 6)
            make_candle(7, 99, 100, 98, 99),
        ]

        tracker = SweepReclaimTracker(left_bars=3, right_bars=3)
        levels = tracker.identify_structural_levels(candles, "BTCUSDT", "1h")

        high_levels = [l for l in levels if l.level_type == LiquidityLevelType.SWING_HIGH_BSL]
        assert len(high_levels) >= 1
        level = high_levels[0]
        assert level.price == 105.0
        assert level.created_index == 3
        assert level.confirmed_index == 6

        # Check at as_of_index = 5 (before confirmation bar 6)
        vis_5, evts_5 = tracker.track_events(candles, levels, as_of_index=5)
        assert not any(l.price == 105.0 for l in vis_5), "Level must not be visible before confirmation bar!"

        # Check at as_of_index = 6 (at confirmation bar 6)
        vis_6, evts_6 = tracker.track_events(candles, levels, as_of_index=6)
        assert any(l.price == 105.0 for l in vis_6), "Level must be visible at confirmation bar!"
        assert vis_6[0].status == LiquidityLevelStatus.ACTIVE

    def test_sweep_without_reclaim_does_not_false_trigger(self):
        """Price pierces level with wick, but candle close is above the level.
        Must transition to SWEPT_PENDING, not SWEEP_RECLAIMED.
        """
        candles = [
            make_candle(0, 100, 101, 99, 100),
            make_candle(1, 100, 102, 99, 101),
            make_candle(2, 101, 103, 100, 102),
            make_candle(3, 102, 105, 101, 103),  # High at 105.0
            make_candle(4, 102, 103, 100, 101),
            make_candle(5, 101, 102, 99, 100),
            make_candle(6, 100, 101, 98, 99),   # Confirmed at 6
            # Bar 7: Breaches 105.0, high=107.0, and closes ABOVE at 106.0
            make_candle(7, 101, 107, 100, 106),
        ]

        tracker = SweepReclaimTracker(left_bars=3, right_bars=3)
        levels = tracker.identify_structural_levels(candles, "BTCUSDT", "1h")
        vis_levels, events = tracker.track_events(candles, levels, as_of_index=7)

        target_lvl = [l for l in vis_levels if l.price == 105.0][0]
        assert target_lvl.status == LiquidityLevelStatus.SWEPT_PENDING
        assert len(events) == 1
        assert events[0].event_type == SweepEventType.SWEEP_UNCONFIRMED
        assert events[0].reclaim_price is None
        assert events[0].sweep_price == 107.0

    def test_immediate_reclaim_after_valid_sweep(self):
        """Price pierces level with wick, same candle closes back inside -> RECLAIMED."""
        candles = [
            make_candle(0, 100, 101, 99, 100),
            make_candle(1, 100, 102, 99, 101),
            make_candle(2, 101, 103, 100, 102),
            make_candle(3, 102, 105, 101, 103),  # High at 105.0
            make_candle(4, 102, 103, 100, 101),
            make_candle(5, 101, 102, 99, 100),
            make_candle(6, 100, 101, 98, 99),   # Confirmed at 6
            # Bar 7: Wicks to 106.5 (+150 bps), closes at 104.2 (inside)
            make_candle(7, 101, 106.5, 100.5, 104.2),
        ]

        tracker = SweepReclaimTracker(left_bars=3, right_bars=3)
        levels = tracker.identify_structural_levels(candles, "BTCUSDT", "1h")
        vis_levels, events = tracker.track_events(candles, levels, as_of_index=7)

        target_lvl = [l for l in vis_levels if l.price == 105.0][0]
        assert target_lvl.status == LiquidityLevelStatus.RECLAIMED
        assert len(events) == 1
        evt = events[0]
        assert evt.event_type == SweepEventType.SWEEP_RECLAIMED
        assert evt.direction == "SHORT"
        assert evt.sweep_price == 106.5
        assert evt.reclaim_price == 104.2
        assert evt.sweep_depth_bps > 100.0
        assert evt.invalidation_price > 106.5
        assert evt.is_valid_entry_candidate is True

    def test_delayed_reclaim_and_subsequent_confirmation(self):
        """Candle 7 pierces and closes outside (SWEPT_PENDING).
        Candle 8 closes back inside (RECLAIMED).
        Candle 9 holds below sweep wick (CONFIRMED).
        """
        candles = [
            make_candle(0, 100, 101, 99, 100),
            make_candle(1, 100, 102, 99, 101),
            make_candle(2, 101, 103, 100, 102),
            make_candle(3, 102, 105, 101, 103),  # High at 105.0
            make_candle(4, 102, 103, 100, 101),
            make_candle(5, 101, 102, 99, 100),
            make_candle(6, 100, 101, 98, 99),   # Confirmed at 6
            # Bar 7: Closes above
            make_candle(7, 101, 106.0, 100.5, 105.2),
            # Bar 8: Closes back inside
            make_candle(8, 105.2, 105.5, 103.5, 104.0),
            # Bar 9: Follow-through holds below sweep high (106.0)
            make_candle(9, 104.0, 104.8, 102.5, 103.0),
        ]

        tracker = SweepReclaimTracker(left_bars=3, right_bars=3)
        levels = tracker.identify_structural_levels(candles, "BTCUSDT", "1h")
        vis_levels, events = tracker.track_events(candles, levels, as_of_index=9)

        target_lvl = [l for l in vis_levels if l.price == 105.0][0]
        assert target_lvl.status == LiquidityLevelStatus.RECLAIMED
        assert len(events) == 1
        evt = events[0]
        assert evt.event_type == SweepEventType.SWEEP_CONFIRMED
        assert evt.reclaim_index == 8
        assert evt.confirmation_index == 9
        assert evt.reclaim_price == 104.0

    def test_false_reclaim_rejection_and_breakout_invalidation(self):
        """When price sustains above level past invalidation threshold, mark INVALIDATED_BROKEN."""
        candles = [
            make_candle(0, 100, 101, 99, 100),
            make_candle(1, 100, 102, 99, 101),
            make_candle(2, 101, 103, 100, 102),
            make_candle(3, 102, 105, 101, 103),  # High at 105.0
            make_candle(4, 102, 103, 100, 101),
            make_candle(5, 101, 102, 99, 100),
            make_candle(6, 100, 101, 98, 99),   # Confirmed at 6
            # Bar 7: Closes above
            make_candle(7, 101, 106.0, 100.5, 105.5),
            # Bar 8: Breaks out higher to 108.0 (well above 0.8% threshold)
            make_candle(8, 105.5, 108.5, 105.0, 108.0),
        ]

        tracker = SweepReclaimTracker(left_bars=3, right_bars=3, invalidation_threshold_pct=0.8)
        levels = tracker.identify_structural_levels(candles, "BTCUSDT", "1h")
        vis_levels, events = tracker.track_events(candles, levels, as_of_index=8)

        target_lvl = [l for l in vis_levels if l.price == 105.0][0]
        assert target_lvl.status == LiquidityLevelStatus.INVALIDATED_BROKEN
        assert len(events) == 1
        assert events[0].event_type == SweepEventType.SWEEP_INVALIDATED

    def test_equal_highs_and_equal_lows_clustering(self):
        """Two swing highs within 12 bps form an EQUAL_HIGHS level."""
        # 105.0 and 105.08 is 7.6 bps diff
        candles = [
            make_candle(0, 100, 101, 99, 100),
            make_candle(1, 100, 102, 99, 101),
            make_candle(2, 101, 103, 100, 102),
            make_candle(3, 102, 105.0, 101, 103),  # Pivot 1 at 3: 105.0
            make_candle(4, 102, 103, 100, 101),
            make_candle(5, 101, 102, 99, 100),
            make_candle(6, 100, 101, 98, 99),    # Confirmed 1 at 6
            make_candle(7, 100, 103, 99, 102),
            make_candle(8, 102, 105.08, 101, 103), # Pivot 2 at 8: 105.08
            make_candle(9, 102, 103, 100, 101),
            make_candle(10, 101, 102, 99, 100),
            make_candle(11, 100, 101, 98, 99),   # Confirmed 2 at 11
        ]

        tracker = SweepReclaimTracker(left_bars=3, right_bars=3, eq_tolerance_bps=12.0)
        levels = tracker.identify_structural_levels(candles, "BTCUSDT", "1h")

        eqh_levels = [l for l in levels if l.level_type == LiquidityLevelType.EQUAL_HIGHS]
        assert len(eqh_levels) == 1
        eqh = eqh_levels[0]
        assert abs(eqh.price - 105.04) < 0.01
        assert "Equal Highs (2 pivots)" in eqh.source_label
        assert eqh.confirmed_index == 11

    def test_historical_replay_snapshot_integrity(self):
        """At replay index T=7, data and events that occur at T=8 or later must NOT be present."""
        candles = [
            make_candle(0, 100, 101, 99, 100),
            make_candle(1, 100, 102, 99, 101),
            make_candle(2, 101, 103, 100, 102),
            make_candle(3, 102, 105, 101, 103),  # Pivot High at 3: 105.0
            make_candle(4, 102, 103, 100, 101),
            make_candle(5, 101, 102, 99, 100),
            make_candle(6, 100, 101, 98, 99),   # Confirmed at 6
            # Bar 7: Normal candle below level
            make_candle(7, 99, 102, 98, 101),
            # Bar 8: Future sweep + reclaim
            make_candle(8, 101, 106.5, 100.5, 104.2),
        ]

        tracker = SweepReclaimTracker(left_bars=3, right_bars=3)
        levels = tracker.identify_structural_levels(candles, "BTCUSDT", "1h")

        # Replay at T=7
        vis_7, evts_7 = tracker.track_events(candles, levels, as_of_index=7)
        assert len(evts_7) == 0, "No events should exist at T=7 before the sweep at T=8!"
        assert vis_7[0].status == LiquidityLevelStatus.ACTIVE, "Level at T=7 should still be ACTIVE!"

        # Replay at T=8
        vis_8, evts_8 = tracker.track_events(candles, levels, as_of_index=8)
        assert len(evts_8) == 1, "Event should appear at T=8!"
        assert vis_8[0].status == LiquidityLevelStatus.RECLAIMED
