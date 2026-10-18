"""Interactive Liquidity Sweep & Reclaim Tracking Engine.

Deterministic, non-lookahead detection of:
1. Structural liquidity levels (Swing High BSL, Swing Low SSL, Equal Highs EQH, Equal Lows EQL).
2. Level breaches (wick sweep past level).
3. Reclaim confirmation (candle close returning across breached level).
4. Subsequent structure confirmation.
5. Level lifecycle (Active, Swept, Reclaimed, Expired, Broken/Invalidated).
6. Temporal replay integrity ensuring no future data leaks at replay time T.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

from src.core.types import Candle, Direction, SwingPoint, SwingType
from src.smc.swings import detect_swings


class LiquidityLevelType(str, Enum):
    SWING_HIGH_BSL = "SWING_HIGH_BSL"
    SWING_LOW_SSL = "SWING_LOW_SSL"
    EQUAL_HIGHS = "EQUAL_HIGHS"
    EQUAL_LOWS = "EQUAL_LOWS"


class LiquidityLevelStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SWEPT_PENDING = "SWEPT_PENDING"
    RECLAIMED = "RECLAIMED"
    INVALIDATED_BROKEN = "INVALIDATED_BROKEN"
    EXPIRED = "EXPIRED"


class SweepEventType(str, Enum):
    SWEEP_UNCONFIRMED = "SWEEP_UNCONFIRMED"
    SWEEP_RECLAIMED = "SWEEP_RECLAIMED"
    SWEEP_CONFIRMED = "SWEEP_CONFIRMED"
    SWEEP_INVALIDATED = "SWEEP_INVALIDATED"


@dataclass
class LiquidityLevel:
    """Represents a confirmed structural liquidity level."""
    level_id: str
    symbol: str
    timeframe: str
    level_type: LiquidityLevelType
    price: float
    zone_top: float
    zone_bottom: float
    created_index: int
    created_timestamp_ms: int
    confirmed_index: int
    confirmed_timestamp_ms: int
    source_label: str
    status: LiquidityLevelStatus = LiquidityLevelStatus.ACTIVE
    test_count: int = 0
    swept_index: Optional[int] = None
    swept_timestamp_ms: Optional[int] = None
    reclaimed_index: Optional[int] = None
    reclaimed_timestamp_ms: Optional[int] = None
    broken_index: Optional[int] = None
    broken_timestamp_ms: Optional[int] = None
    last_event_description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level_id": self.level_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "level_type": self.level_type.value,
            "price": round(self.price, 4),
            "zone_top": round(self.zone_top, 4),
            "zone_bottom": round(self.zone_bottom, 4),
            "created_index": self.created_index,
            "created_timestamp_ms": self.created_timestamp_ms,
            "confirmed_index": self.confirmed_index,
            "confirmed_timestamp_ms": self.confirmed_timestamp_ms,
            "source_label": self.source_label,
            "status": self.status.value,
            "test_count": self.test_count,
            "swept_index": self.swept_index,
            "swept_timestamp_ms": self.swept_timestamp_ms,
            "reclaimed_index": self.reclaimed_index,
            "reclaimed_timestamp_ms": self.reclaimed_timestamp_ms,
            "broken_index": self.broken_index,
            "broken_timestamp_ms": self.broken_timestamp_ms,
            "last_event_description": self.last_event_description,
        }


@dataclass
class SweepReclaimEvent:
    """Represents a deterministic sweep and reclaim detection event."""
    event_id: str
    symbol: str
    timeframe: str
    level_id: str
    level_price: float
    level_type: str
    direction: str                          # "LONG" (swept SSL) or "SHORT" (swept BSL)
    event_type: SweepEventType
    sweep_index: int
    sweep_timestamp_ms: int
    sweep_price: float                      # Wick extreme price
    sweep_depth_usdt: float
    sweep_depth_pct: float
    sweep_depth_bps: float
    reclaim_index: Optional[int] = None
    reclaim_timestamp_ms: Optional[int] = None
    reclaim_price: Optional[float] = None   # Candle close price that confirmed reclaim
    confirmation_index: Optional[int] = None
    confirmation_timestamp_ms: Optional[int] = None
    invalidation_price: float = 0.0         # Placed beyond sweep wick
    target_tp1: Optional[float] = None
    target_tp2: Optional[float] = None
    target_sl: Optional[float] = None
    is_valid_entry_candidate: bool = False
    evidence_text: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "level_id": self.level_id,
            "level_price": round(self.level_price, 4),
            "level_type": self.level_type,
            "direction": self.direction,
            "event_type": self.event_type.value,
            "sweep_index": self.sweep_index,
            "sweep_timestamp_ms": self.sweep_timestamp_ms,
            "sweep_price": round(self.sweep_price, 4),
            "sweep_depth_usdt": round(self.sweep_depth_usdt, 4),
            "sweep_depth_pct": round(self.sweep_depth_pct, 2),
            "sweep_depth_bps": round(self.sweep_depth_bps, 1),
            "reclaim_index": self.reclaim_index,
            "reclaim_timestamp_ms": self.reclaim_timestamp_ms,
            "reclaim_price": round(self.reclaim_price, 4) if self.reclaim_price is not None else None,
            "confirmation_index": self.confirmation_index,
            "confirmation_timestamp_ms": self.confirmation_timestamp_ms,
            "invalidation_price": round(self.invalidation_price, 4),
            "target_tp1": round(self.target_tp1, 4) if self.target_tp1 is not None else None,
            "target_tp2": round(self.target_tp2, 4) if self.target_tp2 is not None else None,
            "target_sl": round(self.target_sl, 4) if self.target_sl is not None else None,
            "is_valid_entry_candidate": self.is_valid_entry_candidate,
            "evidence_text": self.evidence_text,
            "provenance": self.provenance,
        }


class SweepReclaimTracker:
    """Core tracking engine for liquidity levels and sweep/reclaim events."""

    def __init__(
        self,
        left_bars: int = 3,
        right_bars: int = 3,
        max_age_bars: int = 120,
        eq_tolerance_bps: float = 12.0,
        max_reclaim_bars: int = 4,
        invalidation_threshold_pct: float = 0.8,
        min_sweep_bps: float = 2.0,
        max_sweep_bps: float = 400.0,
    ):
        self.left_bars = left_bars
        self.right_bars = right_bars
        self.max_age_bars = max_age_bars
        self.eq_tolerance_bps = eq_tolerance_bps
        self.max_reclaim_bars = max_reclaim_bars
        self.invalidation_threshold_pct = invalidation_threshold_pct
        self.min_sweep_bps = min_sweep_bps
        self.max_sweep_bps = max_sweep_bps

    def identify_structural_levels(self, candles: List[Candle], symbol: str, timeframe: str) -> List[LiquidityLevel]:
        """Detect confirmed structural swing levels and Equal Highs/Lows with non-lookahead confirmation."""
        if len(candles) < (self.left_bars + self.right_bars + 1):
            return []

        # Detect non-lookahead swing points
        swings = detect_swings(candles, left_bars=self.left_bars, right_bars=self.right_bars)
        if not swings:
            return []

        levels: List[LiquidityLevel] = []
        high_swings: List[SwingPoint] = [s for s in swings if s.swing_type == SwingType.HIGH]
        low_swings: List[SwingPoint] = [s for s in swings if s.swing_type == SwingType.LOW]

        # 1. Cluster Equal Highs (EQH)
        processed_high_indices = set()
        for i, s1 in enumerate(high_swings):
            if s1.index in processed_high_indices:
                continue
            cluster = [s1]
            for j in range(i + 1, len(high_swings)):
                s2 = high_swings[j]
                if s2.index in processed_high_indices:
                    continue
                diff_bps = abs(s2.price - s1.price) / s1.price * 10000.0
                if diff_bps <= self.eq_tolerance_bps and (s2.index - s1.index) <= self.max_age_bars:
                    cluster.append(s2)

            if len(cluster) >= 2:
                for c_item in cluster:
                    processed_high_indices.add(c_item.index)
                avg_price = sum(c.price for c in cluster) / len(cluster)
                latest_confirmed_idx = max(c.confirmed_index for c in cluster)
                latest_confirmed_ts = max(c.confirmed_timestamp_ms for c in cluster)
                earliest_created_idx = min(c.index for c in cluster)
                earliest_created_ts = min(c.timestamp_ms for c in cluster)
                buffer_pct = 0.001
                levels.append(
                    LiquidityLevel(
                        level_id=f"EQH_{symbol}_{timeframe}_{earliest_created_idx}_{latest_confirmed_idx}",
                        symbol=symbol,
                        timeframe=timeframe,
                        level_type=LiquidityLevelType.EQUAL_HIGHS,
                        price=avg_price,
                        zone_top=avg_price * (1.0 + buffer_pct),
                        zone_bottom=avg_price,
                        created_index=earliest_created_idx,
                        created_timestamp_ms=earliest_created_ts,
                        confirmed_index=latest_confirmed_idx,
                        confirmed_timestamp_ms=latest_confirmed_ts,
                        source_label=f"{timeframe.upper()} Equal Highs ({len(cluster)} pivots)",
                    )
                )

        # 2. Remaining individual Swing Highs (BSL)
        for s in high_swings:
            if s.index in processed_high_indices:
                continue
            buffer_pct = 0.001
            levels.append(
                LiquidityLevel(
                    level_id=f"BSL_{symbol}_{timeframe}_{s.index}_{s.confirmed_index}",
                    symbol=symbol,
                    timeframe=timeframe,
                    level_type=LiquidityLevelType.SWING_HIGH_BSL,
                    price=s.price,
                    zone_top=s.price * (1.0 + buffer_pct),
                    zone_bottom=s.price,
                    created_index=s.index,
                    created_timestamp_ms=s.timestamp_ms,
                    confirmed_index=s.confirmed_index,
                    confirmed_timestamp_ms=s.confirmed_timestamp_ms,
                    source_label=f"{timeframe.upper()} Swing High BSL (Pivot bar {s.index})",
                )
            )

        # 3. Cluster Equal Lows (EQL)
        processed_low_indices = set()
        for i, s1 in enumerate(low_swings):
            if s1.index in processed_low_indices:
                continue
            cluster = [s1]
            for j in range(i + 1, len(low_swings)):
                s2 = low_swings[j]
                if s2.index in processed_low_indices:
                    continue
                diff_bps = abs(s2.price - s1.price) / s1.price * 10000.0
                if diff_bps <= self.eq_tolerance_bps and (s2.index - s1.index) <= self.max_age_bars:
                    cluster.append(s2)

            if len(cluster) >= 2:
                for c_item in cluster:
                    processed_low_indices.add(c_item.index)
                avg_price = sum(c.price for c in cluster) / len(cluster)
                latest_confirmed_idx = max(c.confirmed_index for c in cluster)
                latest_confirmed_ts = max(c.confirmed_timestamp_ms for c in cluster)
                earliest_created_idx = min(c.index for c in cluster)
                earliest_created_ts = min(c.timestamp_ms for c in cluster)
                buffer_pct = 0.001
                levels.append(
                    LiquidityLevel(
                        level_id=f"EQL_{symbol}_{timeframe}_{earliest_created_idx}_{latest_confirmed_idx}",
                        symbol=symbol,
                        timeframe=timeframe,
                        level_type=LiquidityLevelType.EQUAL_LOWS,
                        price=avg_price,
                        zone_top=avg_price,
                        zone_bottom=avg_price * (1.0 - buffer_pct),
                        created_index=earliest_created_idx,
                        created_timestamp_ms=earliest_created_ts,
                        confirmed_index=latest_confirmed_idx,
                        confirmed_timestamp_ms=latest_confirmed_ts,
                        source_label=f"{timeframe.upper()} Equal Lows ({len(cluster)} pivots)",
                    )
                )

        # 4. Remaining individual Swing Lows (SSL)
        for s in low_swings:
            if s.index in processed_low_indices:
                continue
            buffer_pct = 0.001
            levels.append(
                LiquidityLevel(
                    level_id=f"SSL_{symbol}_{timeframe}_{s.index}_{s.confirmed_index}",
                    symbol=symbol,
                    timeframe=timeframe,
                    level_type=LiquidityLevelType.SWING_LOW_SSL,
                    price=s.price,
                    zone_top=s.price,
                    zone_bottom=s.price * (1.0 - buffer_pct),
                    created_index=s.index,
                    created_timestamp_ms=s.timestamp_ms,
                    confirmed_index=s.confirmed_index,
                    confirmed_timestamp_ms=s.confirmed_timestamp_ms,
                    source_label=f"{timeframe.upper()} Swing Low SSL (Pivot bar {s.index})",
                )
            )

        # Sort by confirmed_index ascending
        levels.sort(key=lambda x: (x.confirmed_index, x.created_index))
        return levels

    def track_events(
        self,
        candles: List[Candle],
        levels: List[LiquidityLevel],
        as_of_index: Optional[int] = None,
    ) -> Tuple[List[LiquidityLevel], List[SweepReclaimEvent]]:
        """Track level lifecycle and detect sweeps, reclaims, and confirmations up to as_of_index.

        Strict Temporal Invariant:
        Candles after as_of_index are completely invisible and cannot influence level status or events.
        """
        if not candles or not levels:
            return [], []

        eval_limit = len(candles) if as_of_index is None else min(len(candles), as_of_index + 1)
        
        # Deep copy levels to avoid mutating caller's objects across different as_of queries
        active_levels: List[LiquidityLevel] = [
            LiquidityLevel(
                level_id=l.level_id,
                symbol=l.symbol,
                timeframe=l.timeframe,
                level_type=l.level_type,
                price=l.price,
                zone_top=l.zone_top,
                zone_bottom=l.zone_bottom,
                created_index=l.created_index,
                created_timestamp_ms=l.created_timestamp_ms,
                confirmed_index=l.confirmed_index,
                confirmed_timestamp_ms=l.confirmed_timestamp_ms,
                source_label=l.source_label,
                status=LiquidityLevelStatus.ACTIVE,
                test_count=0,
            )
            for l in levels
        ]

        events: List[SweepReclaimEvent] = []
        event_by_level_sweep: Dict[str, SweepReclaimEvent] = {}

        # Iterate sequentially through candle bars up to eval_limit
        for k in range(eval_limit):
            current_candle = candles[k]

            for level in active_levels:
                # Level is not yet confirmed/known at candle k
                if level.confirmed_index > k:
                    continue

                # Level has already expired or broken in a previous bar
                if level.status in (LiquidityLevelStatus.EXPIRED, LiquidityLevelStatus.INVALIDATED_BROKEN):
                    continue

                # Check Expiry due to age
                if (k - level.created_index) > self.max_age_bars and level.status == LiquidityLevelStatus.ACTIVE:
                    level.status = LiquidityLevelStatus.EXPIRED
                    continue

                is_bearish_target = level.level_type in (LiquidityLevelType.SWING_HIGH_BSL, LiquidityLevelType.EQUAL_HIGHS)
                is_bullish_target = level.level_type in (LiquidityLevelType.SWING_LOW_SSL, LiquidityLevelType.EQUAL_LOWS)

                # ==============================================================
                # A. BEARISH SETUP: Sweep of High / BSL / EQH -> SHORT
                # ==============================================================
                if is_bearish_target:
                    # Check if currently in pending sweep mode from an earlier bar
                    if level.status == LiquidityLevelStatus.SWEPT_PENDING:
                        pending_evt = event_by_level_sweep.get(level.level_id)
                        if pending_evt and pending_evt.sweep_index < k:
                            bars_since_sweep = k - pending_evt.sweep_index
                            # Check for reclaim close inside
                            if current_candle.close < level.price:
                                level.status = LiquidityLevelStatus.RECLAIMED
                                level.reclaimed_index = k
                                level.reclaimed_timestamp_ms = current_candle.timestamp_ms
                                level.last_event_description = (
                                    f"Delayed Reclaim: Pierced to {pending_evt.sweep_price:.4f}, "
                                    f"closed back inside at {current_candle.close:.4f}"
                                )
                                pending_evt.event_type = SweepEventType.SWEEP_RECLAIMED
                                pending_evt.reclaim_index = k
                                pending_evt.reclaim_timestamp_ms = current_candle.timestamp_ms
                                pending_evt.reclaim_price = current_candle.close
                                pending_evt.evidence_text = (
                                    f"Delayed Reclaim confirmed {bars_since_sweep} bar(s) after sweep. "
                                    f"Level: {level.price:.4f}, Reclaim Close: {current_candle.close:.4f}"
                                )
                                self._populate_event_targets(pending_evt, level, candles, k)
                            elif (
                                current_candle.close > level.price * (1.0 + self.invalidation_threshold_pct / 100.0)
                                or bars_since_sweep >= self.max_reclaim_bars
                            ):
                                level.status = LiquidityLevelStatus.INVALIDATED_BROKEN
                                level.broken_index = k
                                level.broken_timestamp_ms = current_candle.timestamp_ms
                                level.last_event_description = f"Invalidated: Trend continued above level ({current_candle.close:.4f})"
                                pending_evt.event_type = SweepEventType.SWEEP_INVALIDATED
                                pending_evt.evidence_text = f"Breakout invalidation: price sustained above {level.price:.4f}"
                            continue

                    # Fresh breach check on current candle
                    # Must occur strictly after the pivot confirmation candle
                    if current_candle.high > level.price and k > level.confirmed_index:
                        level.test_count += 1
                        depth_usdt = current_candle.high - level.price
                        depth_pct = (depth_usdt / level.price) * 100.0
                        depth_bps = (depth_usdt / level.price) * 10000.0

                        # Case 1: Immediate Reclaim on same candle (wick pierced, closed below)
                        if current_candle.close < level.price:
                            level.status = LiquidityLevelStatus.RECLAIMED
                            level.swept_index = k
                            level.swept_timestamp_ms = current_candle.timestamp_ms
                            level.reclaimed_index = k
                            level.reclaimed_timestamp_ms = current_candle.timestamp_ms
                            level.last_event_description = (
                                f"Immediate Reclaim: Wick {current_candle.high:.4f} (+{depth_bps:.1f} bps) "
                                f"pierced {level.price:.4f}, closed at {current_candle.close:.4f}"
                            )

                            evt = SweepReclaimEvent(
                                event_id=f"EVT_{level.level_id}_SWP_{k}",
                                symbol=level.symbol,
                                timeframe=level.timeframe,
                                level_id=level.level_id,
                                level_price=level.price,
                                level_type=level.level_type.value,
                                direction="SHORT",
                                event_type=SweepEventType.SWEEP_RECLAIMED,
                                sweep_index=k,
                                sweep_timestamp_ms=current_candle.timestamp_ms,
                                sweep_price=current_candle.high,
                                sweep_depth_usdt=depth_usdt,
                                sweep_depth_pct=depth_pct,
                                sweep_depth_bps=depth_bps,
                                reclaim_index=k,
                                reclaim_timestamp_ms=current_candle.timestamp_ms,
                                reclaim_price=current_candle.close,
                                invalidation_price=current_candle.high * (1.0 + 0.001),
                                evidence_text=f"Immediate Wick Sweep & Reclaim across {level.source_label}",
                                provenance={
                                    "pivot_created_idx": level.created_index,
                                    "pivot_confirmed_idx": level.confirmed_index,
                                    "sweep_bar_idx": k,
                                    "reclaim_bar_idx": k,
                                    "candle_timestamp_utc": current_candle.datetime_utc.isoformat(),
                                },
                            )
                            self._populate_event_targets(evt, level, candles, k)
                            events.append(evt)
                            event_by_level_sweep[level.level_id] = evt

                        # Case 2: Candle closed above level (Pending Sweep or Breakout)
                        else:
                            level.status = LiquidityLevelStatus.SWEPT_PENDING
                            level.swept_index = k
                            level.swept_timestamp_ms = current_candle.timestamp_ms
                            level.last_event_description = (
                                f"Sweep Pending: Candle closed above {level.price:.4f} at {current_candle.close:.4f}. "
                                "Awaiting reclaim close inside."
                            )

                            evt = SweepReclaimEvent(
                                event_id=f"EVT_{level.level_id}_SWP_{k}",
                                symbol=level.symbol,
                                timeframe=level.timeframe,
                                level_id=level.level_id,
                                level_price=level.price,
                                level_type=level.level_type.value,
                                direction="SHORT",
                                event_type=SweepEventType.SWEEP_UNCONFIRMED,
                                sweep_index=k,
                                sweep_timestamp_ms=current_candle.timestamp_ms,
                                sweep_price=current_candle.high,
                                sweep_depth_usdt=depth_usdt,
                                sweep_depth_pct=depth_pct,
                                sweep_depth_bps=depth_bps,
                                reclaim_index=None,
                                reclaim_timestamp_ms=None,
                                reclaim_price=None,
                                invalidation_price=current_candle.high * (1.0 + 0.001),
                                evidence_text=f"Unconfirmed Sweep: candle closed above {level.source_label}",
                                provenance={
                                    "pivot_created_idx": level.created_index,
                                    "pivot_confirmed_idx": level.confirmed_index,
                                    "sweep_bar_idx": k,
                                    "candle_timestamp_utc": current_candle.datetime_utc.isoformat(),
                                },
                            )
                            events.append(evt)
                            event_by_level_sweep[level.level_id] = evt

                # ==============================================================
                # B. BULLISH SETUP: Sweep of Low / SSL / EQL -> LONG
                # ==============================================================
                elif is_bullish_target:
                    # Check if currently in pending sweep mode from an earlier bar
                    if level.status == LiquidityLevelStatus.SWEPT_PENDING:
                        pending_evt = event_by_level_sweep.get(level.level_id)
                        if pending_evt and pending_evt.sweep_index < k:
                            bars_since_sweep = k - pending_evt.sweep_index
                            # Check for reclaim close inside
                            if current_candle.close > level.price:
                                level.status = LiquidityLevelStatus.RECLAIMED
                                level.reclaimed_index = k
                                level.reclaimed_timestamp_ms = current_candle.timestamp_ms
                                level.last_event_description = (
                                    f"Delayed Reclaim: Pierced down to {pending_evt.sweep_price:.4f}, "
                                    f"closed back inside at {current_candle.close:.4f}"
                                )
                                pending_evt.event_type = SweepEventType.SWEEP_RECLAIMED
                                pending_evt.reclaim_index = k
                                pending_evt.reclaim_timestamp_ms = current_candle.timestamp_ms
                                pending_evt.reclaim_price = current_candle.close
                                pending_evt.evidence_text = (
                                    f"Delayed Reclaim confirmed {bars_since_sweep} bar(s) after sweep. "
                                    f"Level: {level.price:.4f}, Reclaim Close: {current_candle.close:.4f}"
                                )
                                self._populate_event_targets(pending_evt, level, candles, k)
                            elif (
                                current_candle.close < level.price * (1.0 - self.invalidation_threshold_pct / 100.0)
                                or bars_since_sweep >= self.max_reclaim_bars
                            ):
                                level.status = LiquidityLevelStatus.INVALIDATED_BROKEN
                                level.broken_index = k
                                level.broken_timestamp_ms = current_candle.timestamp_ms
                                level.last_event_description = f"Invalidated: Trend continued below level ({current_candle.close:.4f})"
                                pending_evt.event_type = SweepEventType.SWEEP_INVALIDATED
                                pending_evt.evidence_text = f"Breakdown invalidation: price sustained below {level.price:.4f}"
                            continue

                    # Fresh breach check on current candle
                    if current_candle.low < level.price and k > level.confirmed_index:
                        level.test_count += 1
                        depth_usdt = level.price - current_candle.low
                        depth_pct = (depth_usdt / level.price) * 100.0
                        depth_bps = (depth_usdt / level.price) * 10000.0

                        # Case 1: Immediate Reclaim on same candle (wick pierced, closed above)
                        if current_candle.close > level.price:
                            level.status = LiquidityLevelStatus.RECLAIMED
                            level.swept_index = k
                            level.swept_timestamp_ms = current_candle.timestamp_ms
                            level.reclaimed_index = k
                            level.reclaimed_timestamp_ms = current_candle.timestamp_ms
                            level.last_event_description = (
                                f"Immediate Reclaim: Wick {current_candle.low:.4f} (-{depth_bps:.1f} bps) "
                                f"pierced {level.price:.4f}, closed at {current_candle.close:.4f}"
                            )

                            evt = SweepReclaimEvent(
                                event_id=f"EVT_{level.level_id}_SWP_{k}",
                                symbol=level.symbol,
                                timeframe=level.timeframe,
                                level_id=level.level_id,
                                level_price=level.price,
                                level_type=level.level_type.value,
                                direction="LONG",
                                event_type=SweepEventType.SWEEP_RECLAIMED,
                                sweep_index=k,
                                sweep_timestamp_ms=current_candle.timestamp_ms,
                                sweep_price=current_candle.low,
                                sweep_depth_usdt=depth_usdt,
                                sweep_depth_pct=depth_pct,
                                sweep_depth_bps=depth_bps,
                                reclaim_index=k,
                                reclaim_timestamp_ms=current_candle.timestamp_ms,
                                reclaim_price=current_candle.close,
                                invalidation_price=current_candle.low * (1.0 - 0.001),
                                evidence_text=f"Immediate Wick Sweep & Reclaim across {level.source_label}",
                                provenance={
                                    "pivot_created_idx": level.created_index,
                                    "pivot_confirmed_idx": level.confirmed_index,
                                    "sweep_bar_idx": k,
                                    "reclaim_bar_idx": k,
                                    "candle_timestamp_utc": current_candle.datetime_utc.isoformat(),
                                },
                            )
                            self._populate_event_targets(evt, level, candles, k)
                            events.append(evt)
                            event_by_level_sweep[level.level_id] = evt

                        # Case 2: Candle closed below level (Pending Sweep or Breakdown)
                        else:
                            level.status = LiquidityLevelStatus.SWEPT_PENDING
                            level.swept_index = k
                            level.swept_timestamp_ms = current_candle.timestamp_ms
                            level.last_event_description = (
                                f"Sweep Pending: Candle closed below {level.price:.4f} at {current_candle.close:.4f}. "
                                "Awaiting reclaim close inside."
                            )

                            evt = SweepReclaimEvent(
                                event_id=f"EVT_{level.level_id}_SWP_{k}",
                                symbol=level.symbol,
                                timeframe=level.timeframe,
                                level_id=level.level_id,
                                level_price=level.price,
                                level_type=level.level_type.value,
                                direction="LONG",
                                event_type=SweepEventType.SWEEP_UNCONFIRMED,
                                sweep_index=k,
                                sweep_timestamp_ms=current_candle.timestamp_ms,
                                sweep_price=current_candle.low,
                                sweep_depth_usdt=depth_usdt,
                                sweep_depth_pct=depth_pct,
                                sweep_depth_bps=depth_bps,
                                reclaim_index=None,
                                reclaim_timestamp_ms=None,
                                reclaim_price=None,
                                invalidation_price=current_candle.low * (1.0 - 0.001),
                                evidence_text=f"Unconfirmed Sweep: candle closed below {level.source_label}",
                                provenance={
                                    "pivot_created_idx": level.created_index,
                                    "pivot_confirmed_idx": level.confirmed_index,
                                    "sweep_bar_idx": k,
                                    "candle_timestamp_utc": current_candle.datetime_utc.isoformat(),
                                },
                            )
                            events.append(evt)
                            event_by_level_sweep[level.level_id] = evt

            # ==============================================================
            # C. Check Subsequent Confirmation for Reclaimed Events
            # ==============================================================
            for evt in events:
                if evt.event_type == SweepEventType.SWEEP_RECLAIMED and evt.reclaim_index is not None:
                    if k == evt.reclaim_index + 1:
                        # Bar immediately following reclaim
                        if evt.direction == "LONG":
                            # Hold above sweep low, positive continuation
                            if current_candle.low >= evt.sweep_price and current_candle.close > evt.level_price:
                                evt.event_type = SweepEventType.SWEEP_CONFIRMED
                                evt.confirmation_index = k
                                evt.confirmation_timestamp_ms = current_candle.timestamp_ms
                                evt.evidence_text += f" | Follow-through confirmed at bar {k}"
                        elif evt.direction == "SHORT":
                            # Hold below sweep high, negative continuation
                            if current_candle.high <= evt.sweep_price and current_candle.close < evt.level_price:
                                evt.event_type = SweepEventType.SWEEP_CONFIRMED
                                evt.confirmation_index = k
                                evt.confirmation_timestamp_ms = current_candle.timestamp_ms
                                evt.evidence_text += f" | Follow-through confirmed at bar {k}"

        # Filter levels to only those confirmed on or before eval_limit - 1
        visible_levels = [l for l in active_levels if l.confirmed_index < eval_limit]
        return visible_levels, events

    def _populate_event_targets(
        self,
        evt: SweepReclaimEvent,
        level: LiquidityLevel,
        candles: List[Candle],
        reclaim_idx: int,
    ) -> None:
        """Calculate invalidation stop, take profits, and entry qualification."""
        reclaim_px = evt.reclaim_price or level.price
        sweep_px = evt.sweep_price

        # Determine if sweep depth is in the valid institutional sweet-spot
        valid_depth = self.min_sweep_bps <= evt.sweep_depth_bps <= self.max_sweep_bps

        if evt.direction == "LONG":
            risk = max(abs(reclaim_px - sweep_px), reclaim_px * 0.002)
            evt.target_sl = sweep_px * (1.0 - 0.001)
            evt.invalidation_price = evt.target_sl
            evt.target_tp1 = reclaim_px + (1.5 * risk)
            evt.target_tp2 = reclaim_px + (3.0 * risk)
            evt.is_valid_entry_candidate = valid_depth and (reclaim_px > evt.target_sl)
        else:  # SHORT
            risk = max(abs(sweep_px - reclaim_px), reclaim_px * 0.002)
            evt.target_sl = sweep_px * (1.0 + 0.001)
            evt.invalidation_price = evt.target_sl
            evt.target_tp1 = reclaim_px - (1.5 * risk)
            evt.target_tp2 = reclaim_px - (3.0 * risk)
            evt.is_valid_entry_candidate = valid_depth and (reclaim_px < evt.target_sl)
