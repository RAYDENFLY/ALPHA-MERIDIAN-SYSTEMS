"""Liquidity data collector and persistent snapshot manager for Model B.

Orchestrates order book depth ingestion, trade aggregation, and persistence of
order book snapshots to SQLite for backtesting, auditing, and real-time inference.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import json
import time

from src.core.logger import logger
from src.core.types import Candle, Direction
from src.data.database import DatabaseManager
from src.exchange.normalized import (
    LiquiditySnapshot,
    NormalizedOrderBook,
    NormalizedTrade,
)
from src.exchange.service import MultiExchangeService
from src.liquidity.features import LiquidityFeatureExtractor, detect_liquidity_sweep_reclaim


class LiquidityCollector:
    """Collects multi-exchange liquidity snapshots for candidate screening."""

    def __init__(
        self,
        db: DatabaseManager,
        exchange_service: Optional[MultiExchangeService] = None,
    ) -> None:
        self.db = db
        self.exchange_service = exchange_service or MultiExchangeService()
        self._latest_snapshots: Dict[str, LiquiditySnapshot] = {}

    def collect_for_symbol(
        self,
        symbol: str,
        preferred_venue: str = "asterdex",
        candle: Optional[Candle] = None,
        prior_swing_level: Optional[float] = None,
        direction: Optional[Direction] = None,
    ) -> Optional[LiquiditySnapshot]:
        """Fetch depth and trade flow for a symbol and produce a normalized LiquiditySnapshot."""
        now_ms = int(time.time() * 1000)

        # 1. Fetch order book
        books = self.exchange_service.get_aggregated_order_books(symbol)
        # Select best available order book: preferred venue first, fallback to others
        book = books.get(preferred_venue)
        selected_venue = preferred_venue
        if book is None:
            for v, b in books.items():
                if b is not None:
                    book = b
                    selected_venue = v
                    break

        # 2. Fetch trades
        trades_dict = self.exchange_service.get_recent_trades(symbol, limit=100)
        trades = trades_dict.get(selected_venue) or trades_dict.get("asterdex") or []

        # 3. Liquidity sweep detection if structural context is provided
        sweep_detected = False
        sweep_reclaimed = False
        sweep_dir_str = None
        if candle and prior_swing_level and direction:
            sweep_detected, sweep_reclaimed = detect_liquidity_sweep_reclaim(
                candle=candle,
                prior_swing_level=prior_swing_level,
                direction=direction,
            )
            sweep_dir_str = direction.value

        # 4. Extract snapshot features
        snapshot = LiquidityFeatureExtractor.build_snapshot(
            symbol=symbol,
            exchange_name=selected_venue,
            timestamp_ms=book.timestamp_ms if book else now_ms,
            order_book=book,
            trades=trades,
            sweep_detected=sweep_detected,
            sweep_direction=sweep_dir_str,
            sweep_reclaimed=sweep_reclaimed,
        )

        self._latest_snapshots[symbol] = snapshot

        # 5. Persist book snapshot to SQLite if order book was present
        if book is not None:
            self._save_order_book_snapshot(book, snapshot)

        return snapshot

    def _save_order_book_snapshot(self, book: NormalizedOrderBook, snapshot: LiquiditySnapshot) -> None:
        """Persist snapshot to order_book_snapshots table."""
        now_utc = datetime.now(timezone.utc).isoformat()
        bids_json = json.dumps([{"p": b.price, "s": b.size} for b in book.bids[:10]])
        asks_json = json.dumps([{"p": a.price, "s": a.size} for a in book.asks[:10]])

        query = """
        INSERT INTO order_book_snapshots (
            venue, symbol, timestamp_ms, bids_json, asks_json,
            mid_price, spread_bps, imbalance_25bps, imbalance_50bps, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        try:
            with self.db.get_connection() as conn:
                conn.execute(
                    query,
                    (
                        book.exchange_name,
                        book.symbol,
                        book.timestamp_ms,
                        bids_json,
                        asks_json,
                        book.mid_price or 0.0,
                        snapshot.spread_bps,
                        snapshot.imbalance_25bps,
                        snapshot.imbalance_50bps,
                        now_utc,
                    ),
                )
                conn.commit()
        except Exception as err:
            logger.debug(f"LiquidityCollector: Failed to persist order book snapshot: {err}")

    def get_latest_snapshot(self, symbol: str) -> Optional[LiquiditySnapshot]:
        """Retrieve the most recent in-memory snapshot for a symbol."""
        return self._latest_snapshots.get(symbol)
