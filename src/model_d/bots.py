"""Independent Model D virtual paper bots. No exchange execution methods exist here."""
from dataclasses import dataclass, asdict
from typing import Dict, Optional
import threading


@dataclass(frozen=True)
class BotConfig:
    bot_id: str
    initial_equity_usdt: float
    enabled: bool = True
    risk_fraction: float = 0.02
    leverage: int = 1


@dataclass
class BotPosition:
    position_id: str
    bot_id: str
    signal_id: str
    symbol: str
    direction: str
    quantity: float
    entry_price: float
    margin_usdt: float
    status: str = "OPEN"
    exit_price: Optional[float] = None
    realized_pnl: float = 0.0
    fees: float = 0.0


class ModelDBot:
    def __init__(self, config: BotConfig):
        self.config = config; self.equity = config.initial_equity_usdt
        self.positions: Dict[str, BotPosition] = {}; self.consumed_signals = set(); self._lock = threading.Lock()

    def open(self, signal_id: str, symbol: str, direction: str, entry_price: float, min_notional: float = 5.0) -> BotPosition:
        with self._lock:
            if not self.config.enabled: raise ValueError("bot disabled")
            key = (signal_id, symbol, direction)
            if key in self.consumed_signals: raise ValueError("duplicate signal")
            margin = self.equity * self.config.risk_fraction
            notional = margin * self.config.leverage
            if notional < min_notional: raise ValueError("INFEASIBLE: minimum notional exceeds bot capacity")
            quantity = notional / entry_price
            position = BotPosition(f"{self.config.bot_id}:{signal_id}", self.config.bot_id, signal_id, symbol, direction, quantity, entry_price, margin)
            self.positions[position.position_id] = position; self.consumed_signals.add(key)
            return position

    def close(self, position_id: str, exit_price: float, fee_rate: float = 0.0005, funding: float = 0.0) -> BotPosition:
        with self._lock:
            position = self.positions[position_id]
            if position.status != "OPEN": return position
            move = exit_price - position.entry_price if position.direction == "LONG" else position.entry_price - exit_price
            gross = move * position.quantity; fees = (position.entry_price + exit_price) * position.quantity * fee_rate
            position.exit_price = exit_price; position.realized_pnl = gross - fees - funding; position.fees = fees; position.status = "CLOSED"
            self.equity += position.realized_pnl
            return position

    def summary(self):
        closed = [p for p in self.positions.values() if p.status == "CLOSED"]
        return {"bot_id": self.config.bot_id, "initial_equity_usdt": self.config.initial_equity_usdt, "equity_usdt": self.equity,
                "available_equity_usdt": self.equity - sum(p.margin_usdt for p in self.positions.values() if p.status == "OPEN"),
                "open_positions": sum(p.status == "OPEN" for p in self.positions.values()), "closed_trades": len(closed),
                "realized_pnl": sum(p.realized_pnl for p in closed), "fees": sum(p.fees for p in closed)}


def default_bots() -> Dict[str, ModelDBot]:
    return {c.bot_id: ModelDBot(c) for c in (BotConfig("MODEL_D_BOT_15", 15.0), BotConfig("MODEL_D_BOT_25", 25.0), BotConfig("MODEL_D_BOT_100", 100.0))}
