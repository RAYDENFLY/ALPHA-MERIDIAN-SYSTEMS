"""Order Block (OB) Identification and Mitigation Tracking.

Identifies the opposing candle preceding a displacement impulse that created a BOS or CHoCH.
Tracks unmitigated zones chronologically.
"""

from typing import List, Optional
from src.core.types import Candle, Direction, OrderBlock, SMCEvent, SMCEventType


class OrderBlockDetector:
    """Detects and tracks bullish and bearish order blocks."""

    @staticmethod
    def identify_order_blocks(
        candles: List[Candle],
        structural_events: List[SMCEvent],
        max_search_bars: int = 10,
    ) -> List[OrderBlock]:
        """Detect Order Blocks linked to verified BOS/CHoCH structural breaks.

        Parameters:
        -----------
        candles : List[Candle]
            Chronological candles.
        structural_events : List[SMCEvent]
            Verified BOS and CHoCH events.
        max_search_bars : int
            Number of candles preceding the break to search for the origin order block.

        Returns:
        --------
        List[OrderBlock]
            Identified Order Blocks with historical mitigation tracking.
        """
        if not candles or not structural_events:
            return []

        ts_to_idx = {c.timestamp_ms: i for i, c in enumerate(candles)}
        order_blocks: List[OrderBlock] = []

        for event in structural_events:
            if event.event_type not in (SMCEventType.BOS, SMCEventType.CHOCH):
                continue

            event_idx = ts_to_idx.get(event.timestamp_ms)
            if event_idx is None or event_idx < 1:
                continue

            start_search = max(0, event_idx - max_search_bars)

            if event.direction == Direction.LONG:
                # Bullish OB: Find the last bearish candle before the upward displacement
                ob_candle: Optional[Candle] = None
                for k in range(event_idx - 1, start_search - 1, -1):
                    if candles[k].close < candles[k].open:
                        ob_candle = candles[k]
                        break

                if ob_candle is not None:
                    ob = OrderBlock(
                        direction=Direction.LONG,
                        timestamp_ms=ob_candle.timestamp_ms,
                        high=ob_candle.high,
                        low=ob_candle.low,
                        volume=ob_candle.volume,
                        mitigated=False,
                    )
                    order_blocks.append(ob)

            elif event.direction == Direction.SHORT:
                # Bearish OB: Find the last bullish candle before the downward displacement
                ob_candle = None
                for k in range(event_idx - 1, start_search - 1, -1):
                    if candles[k].close > candles[k].open:
                        ob_candle = candles[k]
                        break

                if ob_candle is not None:
                    ob = OrderBlock(
                        direction=Direction.SHORT,
                        timestamp_ms=ob_candle.timestamp_ms,
                        high=ob_candle.high,
                        low=ob_candle.low,
                        volume=ob_candle.volume,
                        mitigated=False,
                    )
                    order_blocks.append(ob)

        # Update mitigation status sequentially up to the latest candle
        for ob in order_blocks:
            ob_idx = ts_to_idx.get(ob.timestamp_ms)
            if ob_idx is None:
                continue
            for post_c in candles[ob_idx + 1:]:
                if ob.direction == Direction.LONG:
                    # Bullish OB mitigated if subsequent low re-enters the OB zone
                    if post_c.low <= ob.high:
                        ob.mitigated = True
                        ob.mitigated_timestamp_ms = post_c.timestamp_ms
                        break
                elif ob.direction == Direction.SHORT:
                    # Bearish OB mitigated if subsequent high re-enters the OB zone
                    if post_c.high >= ob.low:
                        ob.mitigated = True
                        ob.mitigated_timestamp_ms = post_c.timestamp_ms
                        break

        return order_blocks
