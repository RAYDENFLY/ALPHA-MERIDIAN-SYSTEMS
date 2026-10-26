"""Authoritative 48H/72H Dealing Range and Zone Lock Engine.

Implements the Non-Negotiable Structural Laws:
- HARD LAW 1 (NO SHORTING IN THE BOTTOM HALF):
  NEVER generate or approve a SHORT signal if position_pct < 65.0%!
  (e.g., Shorting ETHUSDT at 2308.76 when 72h low is 2281.33 is ~20% of range -> automatic hard veto).

- HARD LAW 2 (NO LONGING IN THE TOP HALF):
  NEVER generate or approve a LONG signal if position_pct > 38.0%
  (unless there is an unmitigated 15m/1H Bull FVG pullback AND 1H trend is strictly BULL).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.core.types import Candle, Direction


@dataclass
class DealingRangeInfo:
    """Quantitative snapshot of the dealing range."""
    range_high: float
    range_low: float
    range_span: float
    current_price: float
    position_pct: float
    bars_count: int
    is_valid: bool
    rejection_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "range_high": self.range_high,
            "range_low": self.range_low,
            "range_span": self.range_span,
            "current_price": self.current_price,
            "position_pct": round(self.position_pct, 2),
            "bars_count": self.bars_count,
            "is_valid": self.is_valid,
            "rejection_reason": self.rejection_reason,
        }


def compute_dealing_range(
    candles_1h: List[Candle],
    current_price: float,
    lookback_bars: int = 72,
) -> DealingRangeInfo:
    """Compute rolling 48H/72H Dealing Range from 1H closed candles.
    
    Formula:
        position_pct = (current_price - range_low) / (range_high - range_low) * 100.0
    """
    if not candles_1h or len(candles_1h) < 10:
        return DealingRangeInfo(
            range_high=current_price,
            range_low=current_price,
            range_span=0.0,
            current_price=current_price,
            position_pct=50.0,
            bars_count=len(candles_1h) if candles_1h else 0,
            is_valid=False,
            rejection_reason="INSUFFICIENT_1H_CANDLES: Need at least 10 1H candles to compute dealing range",
        )

    # Use up to lookback_bars
    window = candles_1h[-lookback_bars:] if len(candles_1h) >= lookback_bars else candles_1h
    highs = [float(c.high) for c in window]
    lows = [float(c.low) for c in window]

    range_high = max(highs)
    range_low = min(lows)
    range_span = range_high - range_low

    if range_span <= 1e-9:
        return DealingRangeInfo(
            range_high=range_high,
            range_low=range_low,
            range_span=0.0,
            current_price=current_price,
            position_pct=50.0,
            bars_count=len(window),
            is_valid=False,
            rejection_reason="FLAT_RANGE: Range span is virtually zero",
        )

    position_pct = ((current_price - range_low) / range_span) * 100.0

    return DealingRangeInfo(
        range_high=range_high,
        range_low=range_low,
        range_span=range_span,
        current_price=current_price,
        position_pct=position_pct,
        bars_count=len(window),
        is_valid=True,
    )


def check_dealing_range_zone_lock(
    position_pct: float,
    direction: str | Direction,
    has_bull_fvg_pullback: bool = False,
    trend_1h: str = "NEUTRAL",
    short_min_premium_pct: float = 65.0,
    long_max_discount_pct: float = 38.0,
) -> Tuple[bool, str]:
    """Enforce Non-Negotiable Dealing Range Structural Laws.
    
    HARD LAW 1 (NO SHORTING IN THE BOTTOM HALF):
        NEVER generate or approve a SHORT signal if position_pct < 65.0%!
    
    HARD LAW 2 (NO LONGING IN THE TOP HALF):
        NEVER generate or approve a LONG signal if position_pct > 38.0%
        (unless there is an unmitigated 15m/1H Bull FVG pullback AND 1H trend is strictly BULL).
    
    Returns:
        (passed: bool, reason: str)
    """
    dir_str = direction.value if hasattr(direction, "value") else str(direction).strip().upper()

    if dir_str == "SHORT":
        # HARD LAW 1
        if position_pct < short_min_premium_pct:
            return (
                False,
                f"HARD_ZONE_LOCK_VETO: Cannot SHORT at {position_pct:.1f}% of Dealing Range "
                f"(Strictly requires Premium >= {short_min_premium_pct:.1f}% to prevent shorting bottoms)",
            )
        return True, f"DEALING_RANGE_PREMIUM_OK: Position {position_pct:.1f}% >= {short_min_premium_pct:.1f}%"

    elif dir_str == "LONG":
        # HARD LAW 2
        if position_pct > long_max_discount_pct:
            # Check Exception: unmitigated 15m/1H Bull FVG pullback AND 1H trend strictly BULL
            is_bull_trend = trend_1h.upper() in ("BULL", "BULLISH")
            if has_bull_fvg_pullback and is_bull_trend:
                return (
                    True,
                    f"DEALING_RANGE_LONG_FVG_EXCEPTION_OK: Position {position_pct:.1f}% > {long_max_discount_pct:.1f}%, "
                    f"but allowed due to verified unmitigated Bull FVG pullback & 1H BULL trend",
                )
            return (
                False,
                f"HARD_ZONE_LOCK_VETO: Cannot LONG at {position_pct:.1f}% of Dealing Range "
                f"(Strictly requires Discount <= {long_max_discount_pct:.1f}% unless Bull FVG + 1H Bull trend)",
            )
        return True, f"DEALING_RANGE_DISCOUNT_OK: Position {position_pct:.1f}% <= {long_max_discount_pct:.1f}%"

    return False, f"INVALID_DIRECTION: Unknown trade direction '{dir_str}'"
