"""Binance-first Model D market collection and local-book sequence guard."""
from typing import Any, Dict, Optional
from src.exchange.binance_adapter import BinanceFuturesAdapter
from src.model_d.collector import ModelDCollector
from src.model_d.types import DataAvailability


class BinanceOrderBookSync:
    def __init__(self) -> None:
        self.last_update_id: Optional[int] = None
        self.bids: Dict[str, str] = {}; self.asks: Dict[str, str] = {}
        self.needs_snapshot = True

    def apply_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self.last_update_id = int(snapshot["lastUpdateId"])
        self.bids = {str(p): str(q) for p, q in snapshot.get("bids", [])}; self.asks = {str(p): str(q) for p, q in snapshot.get("asks", [])}
        self.needs_snapshot = False

    def apply_update(self, event: Dict[str, Any]) -> bool:
        first = int(event["U"]); final = int(event["u"])
        if self.last_update_id is None or first > self.last_update_id + 1:
            self.needs_snapshot = True
            return False
        if final <= self.last_update_id: return True
        for side, target in (("b", self.bids), ("a", self.asks)):
            for price, qty in event.get(side, []):
                if float(qty) == 0: target.pop(str(price), None)
                else: target[str(price)] = str(qty)
        self.last_update_id = final
        return True


class BinanceModelDCollector(ModelDCollector):
    """Explicit Binance collector; it never falls back to AsterDEX."""
    def __init__(self, adapter: Optional[BinanceFuturesAdapter] = None, config=None, reference_adapter=None):
        super().__init__(adapter=adapter or BinanceFuturesAdapter(), config=config)
        self.adapter: BinanceFuturesAdapter
        self.reference_adapter = reference_adapter

    def collect_symbol(self, symbol: str, book_limit: int = 50) -> Dict[str, Any]:
        result = super().collect_symbol(symbol, book_limit)
        result["exchange"] = "binance"
        result["reference_venue"] = "asterdex" if self.reference_adapter else None
        return result
