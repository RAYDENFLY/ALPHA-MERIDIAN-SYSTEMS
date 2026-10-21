"""Independent Model D shadow ledger; no exchange execution calls are present."""
from dataclasses import dataclass, asdict
from typing import Dict, List


@dataclass
class ShadowRecord:
    signal_id: str
    symbol: str
    direction: str
    entry_price: float
    status: str = "OPEN"
    exit_price: float = 0.0
    gross_pnl: float = 0.0
    costs: float = 0.0
    net_pnl: float = 0.0
    exit_reason: str = ""


class ModelDShadowLedger:
    def __init__(self, fee_rate: float = 0.0005, slippage_rate: float = 0.0005) -> None:
        self.fee_rate = fee_rate; self.slippage_rate = slippage_rate
        self.records: Dict[str, ShadowRecord] = {}

    def record_signal(self, signal_id: str, symbol: str, direction: str, entry_price: float) -> ShadowRecord:
        if signal_id in self.records: return self.records[signal_id]
        record = ShadowRecord(signal_id, symbol, direction, entry_price)
        self.records[signal_id] = record
        return record

    def close(self, signal_id: str, exit_price: float, reason: str) -> ShadowRecord:
        record = self.records[signal_id]
        if record.status != "OPEN": return record
        signed_move = (exit_price - record.entry_price) if record.direction == "LONG" else (record.entry_price - exit_price)
        record.exit_price = exit_price; record.gross_pnl = signed_move
        record.costs = (record.entry_price + exit_price) * (self.fee_rate + self.slippage_rate)
        record.net_pnl = record.gross_pnl - record.costs; record.status = "CLOSED"; record.exit_reason = reason
        return record

    def summary(self) -> Dict[str, float]:
        closed = [r for r in self.records.values() if r.status == "CLOSED"]
        longs = [r for r in closed if r.direction == "LONG"]; shorts = [r for r in closed if r.direction == "SHORT"]
        return {"signals": float(len(self.records)), "closed": float(len(closed)), "long_closed": float(len(longs)), "short_closed": float(len(shorts)), "net_pnl": sum(r.net_pnl for r in closed)}
