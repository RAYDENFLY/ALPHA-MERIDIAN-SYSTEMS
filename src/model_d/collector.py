"""Model D public-data collector.

This module is deliberately read-only. It reuses the existing AsterDEX adapter,
tracks freshness per source, and exposes unavailable sources explicitly.
"""

import time
from typing import Any, Dict, Optional

from config.settings import ModelDConfig
from src.exchange.aster_adapter import AsterAdapter
from src.model_d.types import DataAvailability, SourceHealth


class ModelDCollector:
    def __init__(self, adapter: Optional[AsterAdapter] = None, config: Optional[ModelDConfig] = None) -> None:
        self.adapter = adapter or AsterAdapter()
        self.config = config or ModelDConfig()
        self.health: Dict[str, SourceHealth] = {}

    def _health(self, source: str, timestamp_ms: Optional[int] = None, error: Optional[str] = None, venue: str = "asterdex", symbol: Optional[str] = None) -> SourceHealth:
        observed = int(time.time() * 1000)
        status = DataAvailability.ERROR if error else DataAvailability.UNAVAILABLE
        if timestamp_ms is not None and not error:
            age = max(0, observed - timestamp_ms) / 1000
            limit = self.config.max_book_age_sec if source == "order_book" else self.config.max_event_age_sec
            status = DataAvailability.LIVE if age <= limit else DataAvailability.STALE
        value = SourceHealth(source, venue, symbol, status, timestamp_ms, observed, error)
        self.health[source] = value
        return value

    def collect_symbol(self, symbol: str, book_limit: int = 50) -> Dict[str, Any]:
        symbol = symbol.strip().upper().replace(".P", "")
        result: Dict[str, Any] = {"symbol": symbol, "exchange": "asterdex", "sources": {}}
        try:
            book = self.adapter.get_order_book(symbol, limit=book_limit)
            result["order_book"] = book
            result["sources"]["order_book"] = self._health("order_book", book.timestamp_ms if book else None, venue=result["exchange"], symbol=symbol).to_dict()
        except Exception as exc:
            result["order_book"] = None
            result["sources"]["order_book"] = self._health("order_book", None, str(exc), result["exchange"], symbol).to_dict()
        try:
            trades = self.adapter.get_recent_trades(symbol, limit=self.config.trade_reference_window)
            result["trades"] = trades
            latest = max((t.timestamp_ms for t in trades), default=None)
            result["sources"]["trades"] = self._health("trades", latest, venue=result["exchange"], symbol=symbol).to_dict()
        except Exception as exc:
            result["trades"] = []
            result["sources"]["trades"] = self._health("trades", None, str(exc), result["exchange"], symbol).to_dict()
        for name, getter in (("premium_index", self.adapter.get_premium_index), ("open_interest", self.adapter.get_open_interest)):
            try:
                value = getter(symbol)
                result[name] = value
                result["sources"][name] = self._health(name, value.get("timestamp_ms") if value else None, venue=result["exchange"], symbol=symbol).to_dict()
            except Exception as exc:
                result[name] = None
                result["sources"][name] = self._health(name, None, str(exc), result["exchange"], symbol).to_dict()
        # No chain endpoint or attribution source is configured in this repository.
        result["wallet_activity"] = None
        result["sources"]["wallet_activity"] = self._health("wallet_activity", venue="on_chain", symbol=symbol).to_dict()
        return result
