"""Market Structure Analysis: Trend, BOS (Break of Structure), and CHoCH (Change of Character).

Operates strictly on confirmed swings without lookahead bias.
"""

from typing import List, Optional, Tuple
from src.core.types import Candle, Direction, SMCEvent, SMCEventType, StructureState, SwingPoint, SwingType


class MarketStructureEngine:
    """Evaluates market structure, BOS, and CHoCH over chronological candles."""

    @staticmethod
    def evaluate_structure(
        candles: List[Candle],
        swings: List[SwingPoint],
    ) -> Tuple[StructureState, List[SMCEvent]]:
        """Compute chronological market structure progression and structural breaks.

        Parameters:
        -----------
        candles : List[Candle]
            Chronological candles.
        swings : List[SwingPoint]
            Pre-detected swings with confirmed_index.

        Returns:
        --------
        Tuple[StructureState, List[SMCEvent]]
            Current structure state and list of historical SMC events.
        """
        if not candles or not swings:
            return StructureState.SIDEWAYS, []

        events: List[SMCEvent] = []
        current_state = StructureState.SIDEWAYS

        # Active confirmed swings
        last_high: Optional[SwingPoint] = None
        last_low: Optional[SwingPoint] = None
        prev_high: Optional[SwingPoint] = None
        prev_low: Optional[SwingPoint] = None

        swing_idx_map = {s.confirmed_index: s for s in swings}

        for i, c in enumerate(candles):
            # Check if any swing was confirmed at candle i
            if i in swing_idx_map:
                s = swing_idx_map[i]
                if s.swing_type == SwingType.HIGH:
                    prev_high = last_high
                    last_high = s
                    # Initialize or verify trend from swings
                    if prev_high is not None and prev_low is not None:
                        if last_high.price > prev_high.price and (last_low and prev_low and last_low.price >= prev_low.price):
                            if current_state == StructureState.SIDEWAYS:
                                current_state = StructureState.BULLISH
                elif s.swing_type == SwingType.LOW:
                    prev_low = last_low
                    last_low = s
                    if prev_high is not None and prev_low is not None:
                        if last_low.price < prev_low.price and (last_high and prev_high and last_high.price <= prev_high.price):
                            if current_state == StructureState.SIDEWAYS:
                                current_state = StructureState.BEARISH

            # Evaluate structural breaks by candle close
            if last_high is not None and not last_high.broken:
                if c.close > last_high.price and c.timestamp_ms > last_high.confirmed_timestamp_ms:
                    last_high.broken = True
                    last_high.broken_timestamp_ms = c.timestamp_ms
                    if current_state == StructureState.BEARISH:
                        # Reversal break: CHoCH
                        current_state = StructureState.BULLISH
                        events.append(
                            SMCEvent(
                                event_type=SMCEventType.CHOCH,
                                direction=Direction.LONG,
                                timestamp_ms=c.timestamp_ms,
                                price_level=last_high.price,
                                zone_high=c.high,
                                zone_low=last_high.price,
                                reference_timestamp_ms=last_high.timestamp_ms,
                                description=f"Bullish CHoCH: Candle close ({c.close:.4f}) broke prior confirmed high ({last_high.price:.4f})",
                            )
                        )
                    else:
                        # Continuation break: BOS
                        current_state = StructureState.BULLISH
                        events.append(
                            SMCEvent(
                                event_type=SMCEventType.BOS,
                                direction=Direction.LONG,
                                timestamp_ms=c.timestamp_ms,
                                price_level=last_high.price,
                                zone_high=c.high,
                                zone_low=last_high.price,
                                reference_timestamp_ms=last_high.timestamp_ms,
                                description=f"Bullish BOS: Candle close ({c.close:.4f}) broke swing high ({last_high.price:.4f})",
                            )
                        )

            if last_low is not None and not last_low.broken:
                if c.close < last_low.price and c.timestamp_ms > last_low.confirmed_timestamp_ms:
                    last_low.broken = True
                    last_low.broken_timestamp_ms = c.timestamp_ms
                    if current_state == StructureState.BULLISH:
                        # Reversal break: CHoCH
                        current_state = StructureState.BEARISH
                        events.append(
                            SMCEvent(
                                event_type=SMCEventType.CHOCH,
                                direction=Direction.SHORT,
                                timestamp_ms=c.timestamp_ms,
                                price_level=last_low.price,
                                zone_high=last_low.price,
                                zone_low=c.low,
                                reference_timestamp_ms=last_low.timestamp_ms,
                                description=f"Bearish CHoCH: Candle close ({c.close:.4f}) broke prior confirmed low ({last_low.price:.4f})",
                            )
                        )
                    else:
                        # Continuation break: BOS
                        current_state = StructureState.BEARISH
                        events.append(
                            SMCEvent(
                                event_type=SMCEventType.BOS,
                                direction=Direction.SHORT,
                                timestamp_ms=c.timestamp_ms,
                                price_level=last_low.price,
                                zone_high=last_low.price,
                                zone_low=c.low,
                                reference_timestamp_ms=last_low.timestamp_ms,
                                description=f"Bearish BOS: Candle close ({c.close:.4f}) broke swing low ({last_low.price:.4f})",
                            )
                        )

        return current_state, events
