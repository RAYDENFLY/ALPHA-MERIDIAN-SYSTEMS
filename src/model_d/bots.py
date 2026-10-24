"""Independent Model D virtual paper bots. No exchange execution methods exist here."""
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
import json
import threading
from datetime import datetime


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
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    funding: float = 0.0
    slippage: float = 0.0
    mark_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_timestamp: Optional[str] = None
    target_policy_version: Optional[str] = None
    target_inputs: Optional[str] = None


class ModelDBot:
    def __init__(self, config: BotConfig):
        self.config = config; self.equity = config.initial_equity_usdt
        self.positions: Dict[str, BotPosition] = {}; self.consumed_signals = set(); self._lock = threading.Lock()

    def open(self, signal_id: str, symbol: str, direction: str, entry_price: float, min_notional: float = 5.0, stop_loss: Optional[float] = None, take_profit: Optional[float] = None, slippage: float = 0.0) -> BotPosition:
        with self._lock:
            if not self.config.enabled: raise ValueError("bot disabled")
            key = (signal_id, symbol, direction)
            if key in self.consumed_signals: raise ValueError("duplicate signal")
            margin = self.equity * self.config.risk_fraction
            notional = margin * self.config.leverage
            if notional < min_notional: raise ValueError("INFEASIBLE: minimum notional exceeds bot capacity")
            quantity = notional / entry_price
            position = BotPosition(f"{self.config.bot_id}:{signal_id}", self.config.bot_id, signal_id, symbol, direction, quantity, entry_price, margin, stop_loss=stop_loss, take_profit=take_profit, slippage=slippage)
            self.positions[position.position_id] = position; self.consumed_signals.add(key)
            return position

    def assess(self, symbol: str, direction: str, entry_price: Optional[float], min_notional: float = 5.0) -> Dict[str, str]:
        if not self.config.enabled: return {"status": "REJECTED", "reason": "bot_disabled"}
        if entry_price is None or entry_price <= 0: return {"status": "WAIT", "reason": "entry_price_unavailable"}
        if any(p.status == "OPEN" and p.symbol == symbol for p in self.positions.values()): return {"status": "REJECTED", "reason": "position_already_open"}
        notional = self.equity * self.config.risk_fraction * self.config.leverage
        if notional < min_notional: return {"status": "INFEASIBLE", "reason": "minimum_notional_exceeds_available_equity"}
        return {"status": "ENTRY_ELIGIBLE", "reason": "risk_and_minimum_notional_passed"}

    def close(self, position_id: str, exit_price: float, fee_rate: float = 0.0005, funding: float = 0.0, reason: str = "MANUAL") -> BotPosition:
        with self._lock:
            position = self.positions[position_id]
            if position.status != "OPEN": return position
            move = exit_price - position.entry_price if position.direction == "LONG" else position.entry_price - exit_price
            gross = move * position.quantity; fees = (position.entry_price + exit_price) * position.quantity * fee_rate
            position.exit_price = exit_price; position.realized_pnl = gross - fees - funding; position.fees = fees; position.status = "CLOSED"
            position.funding = funding; position.exit_reason = reason; position.exit_timestamp = datetime.utcnow().isoformat()
            self.equity += position.realized_pnl
            return position

    def mark(self, position_id: str, mark_price: Optional[float]) -> Optional[float]:
        with self._lock:
            position = self.positions[position_id]
            if mark_price is None or mark_price <= 0 or position.status != "OPEN":
                position.mark_price = None; position.unrealized_pnl = None; return None
            position.mark_price = mark_price
            move = mark_price - position.entry_price if position.direction == "LONG" else position.entry_price - mark_price
            position.unrealized_pnl = move * position.quantity - position.entry_price * position.quantity * position.slippage
            return position.unrealized_pnl

    def exit_trigger(self, position_id: str, high: float, low: float) -> Optional[tuple]:
        position = self.positions[position_id]
        if position.status != "OPEN": return None
        # Conservative collision rule: if both levels trade in one candle, stop wins.
        if position.direction == "LONG":
            if position.stop_loss is not None and low <= position.stop_loss: return position.stop_loss, "STOP_LOSS"
            if position.take_profit is not None and high >= position.take_profit: return position.take_profit, "TAKE_PROFIT"
        else:
            if position.stop_loss is not None and high >= position.stop_loss: return position.stop_loss, "STOP_LOSS"
            if position.take_profit is not None and low <= position.take_profit: return position.take_profit, "TAKE_PROFIT"
        return None

    def summary(self):
        closed = [p for p in self.positions.values() if p.status == "CLOSED"]
        return {"bot_id": self.config.bot_id, "initial_equity_usdt": self.config.initial_equity_usdt, "equity_usdt": self.equity,
                "available_equity_usdt": self.equity - sum(p.margin_usdt for p in self.positions.values() if p.status == "OPEN"),
                "open_positions": sum(p.status == "OPEN" for p in self.positions.values()), "closed_trades": len(closed),
                "realized_pnl": sum(p.realized_pnl for p in closed), "fees": sum(p.fees for p in closed)}


class PersistentBotStore:
    """SQLite-backed Model D bot state; never touches Model A/B journal rows."""
    def __init__(self, db: Any):
        self.db = db
        with db.get_connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS model_d_bots (
                bot_id TEXT PRIMARY KEY, initial_equity REAL NOT NULL, equity REAL NOT NULL,
                enabled INTEGER NOT NULL, risk_fraction REAL NOT NULL, leverage INTEGER NOT NULL,
                updated_at TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS model_d_bot_positions (
                position_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, signal_id TEXT NOT NULL,
                symbol TEXT NOT NULL, direction TEXT NOT NULL, quantity REAL NOT NULL,
                entry_price REAL NOT NULL, margin_usdt REAL NOT NULL, status TEXT NOT NULL,
                exit_price REAL, realized_pnl REAL NOT NULL DEFAULT 0, fees REAL NOT NULL DEFAULT 0,
                UNIQUE(bot_id, signal_id, symbol, direction))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS model_d_bot_signals (
                bot_id TEXT NOT NULL, signal_id TEXT NOT NULL, symbol TEXT NOT NULL,
                direction TEXT NOT NULL, status TEXT NOT NULL, reason TEXT DEFAULT '',
                PRIMARY KEY(bot_id, signal_id, symbol, direction))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS model_d_bot_journal (
                lifecycle_id TEXT PRIMARY KEY, position_id TEXT NOT NULL, bot_id TEXT NOT NULL,
                signal_id TEXT NOT NULL, event_type TEXT NOT NULL, event_timestamp TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}')""")
            existing = {row[1] for row in conn.execute("PRAGMA table_info(model_d_bot_positions)").fetchall()}
            for name, definition in (("stop_loss", "REAL"), ("take_profit", "REAL"), ("funding", "REAL NOT NULL DEFAULT 0"), ("slippage", "REAL NOT NULL DEFAULT 0"), ("mark_price", "REAL"), ("unrealized_pnl", "REAL"), ("exit_reason", "TEXT"), ("exit_timestamp", "TEXT")):
                if name not in existing:
                    conn.execute(f"ALTER TABLE model_d_bot_positions ADD COLUMN {name} {definition}")
            conn.commit()

    def initialize(self, configs) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            for config in configs:
                conn.execute("""INSERT INTO model_d_bots(bot_id,initial_equity,equity,enabled,risk_fraction,leverage,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(bot_id) DO UPDATE SET enabled=excluded.enabled,
                    risk_fraction=excluded.risk_fraction,leverage=excluded.leverage,updated_at=excluded.updated_at""",
                    (config.bot_id, config.initial_equity_usdt, config.initial_equity_usdt, int(config.enabled), config.risk_fraction, config.leverage, now))
            conn.commit()

    def load(self) -> Dict[str, ModelDBot]:
        bots: Dict[str, ModelDBot] = {}
        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM model_d_bots").fetchall()
            position_rows = conn.execute("SELECT * FROM model_d_bot_positions").fetchall()
            signal_rows = conn.execute("SELECT * FROM model_d_bot_signals").fetchall()
        for row in rows:
            config = BotConfig(row["bot_id"], float(row["initial_equity"]), bool(row["enabled"]), float(row["risk_fraction"]), int(row["leverage"]))
            bot = ModelDBot(config); bot.equity = float(row["equity"]); bots[config.bot_id] = bot
        for row in position_rows:
            if row["bot_id"] in bots:
                fields = {name: row[name] for name in ("position_id", "bot_id", "signal_id", "symbol", "direction", "quantity", "entry_price", "margin_usdt", "status", "exit_price", "realized_pnl", "fees", "stop_loss", "take_profit", "funding", "slippage", "mark_price", "unrealized_pnl", "exit_reason", "exit_timestamp") if name in row.keys()}
                bots[row["bot_id"]].positions[row["position_id"]] = BotPosition(**fields)
        for row in signal_rows:
            if row["bot_id"] in bots: bots[row["bot_id"]].consumed_signals.add((row["signal_id"], row["symbol"], row["direction"]))
        return bots

    def persist_open(self, position: BotPosition) -> None:
        with self.db.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO model_d_bot_signals VALUES(?,?,?,?,?,?)", (position.bot_id, position.signal_id, position.symbol, position.direction, "ACCEPTED", ""))
            conn.execute("""INSERT INTO model_d_bot_positions
                (position_id,bot_id,signal_id,symbol,direction,quantity,entry_price,margin_usdt,status,exit_price,realized_pnl,fees)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(position_id) DO NOTHING""", (position.position_id, position.bot_id, position.signal_id, position.symbol, position.direction, position.quantity, position.entry_price, position.margin_usdt, position.status, position.exit_price, position.realized_pnl, position.fees))
            conn.execute("UPDATE model_d_bot_positions SET stop_loss=?,take_profit=?,funding=?,slippage=? WHERE position_id=?", (position.stop_loss, position.take_profit, position.funding, position.slippage, position.position_id))
            conn.execute("INSERT OR IGNORE INTO model_d_bot_journal VALUES(?,?,?,?,?,?,?)", (f"{position.position_id}:ENTRY", position.position_id, position.bot_id, position.signal_id, "ENTRY_COMMITTED", datetime.utcnow().isoformat(), json.dumps({"target_policy_version": position.target_policy_version, "target_inputs": position.target_inputs})))
            conn.commit()

    def persist_close(self, bot: ModelDBot, position: BotPosition) -> None:
        with self.db.get_connection() as conn:
            changed = conn.execute("UPDATE model_d_bot_positions SET status=?,exit_price=?,realized_pnl=?,fees=?,funding=?,exit_reason=?,exit_timestamp=? WHERE position_id=? AND status='OPEN'", (position.status, position.exit_price, position.realized_pnl, position.fees, position.funding, position.exit_reason, position.exit_timestamp, position.position_id)).rowcount
            if not changed:
                conn.commit(); return
            conn.execute("UPDATE model_d_bots SET equity=?,updated_at=datetime('now') WHERE bot_id=?", (bot.equity, bot.config.bot_id))
            conn.execute("INSERT OR IGNORE INTO model_d_bot_journal VALUES(?,?,?,?,?,?,?)", (f"{position.position_id}:EXIT", position.position_id, position.bot_id, position.signal_id, "EXIT_COMMITTED", datetime.utcnow().isoformat(), json.dumps({"exit_price": position.exit_price, "realized_pnl": position.realized_pnl, "fees": position.fees})))
            conn.commit()

    def journal(self, limit: int = 100):
        limit = max(1, min(limit, 200))
        with self.db.get_connection() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM model_d_bot_journal ORDER BY event_timestamp DESC LIMIT ?", (limit,)).fetchall()]
            positions = {row["position_id"]: dict(row) for row in conn.execute("SELECT * FROM model_d_bot_positions").fetchall()}
        for row in rows:
            position = positions.get(row["position_id"], {})
            for key in ("symbol", "direction", "quantity", "entry_price", "margin_usdt", "exit_price", "realized_pnl", "fees", "funding", "slippage", "stop_loss", "take_profit", "status"):
                row[key] = position.get(key)
            row["notional"] = (position.get("quantity") or 0) * (position.get("entry_price") or 0) if position else None
        return rows

    def filtered_journal(self, limit=100, offset=0, bot_id=None, symbol=None, direction=None, lifecycle_status=None, entry_from=None, entry_to=None, exit_from=None, exit_to=None):
        limit = max(1, min(int(limit), 200)); offset = max(0, int(offset))
        clauses = []; params = []
        for column, value in (("bot_id", bot_id), ("symbol", symbol.upper() if symbol else None), ("direction", direction.upper() if direction else None)):
            if value: clauses.append(f"p.{column} = ?"); params.append(value)
        if lifecycle_status == "OPEN": clauses.append("p.status = 'OPEN'")
        elif lifecycle_status == "CLOSED": clauses.append("p.status = 'CLOSED'")
        if entry_from: clauses.append("j.event_timestamp >= ?"); params.append(entry_from)
        if entry_to: clauses.append("j.event_timestamp <= ?"); params.append(entry_to)
        if exit_from: clauses.append("(p.exit_timestamp IS NULL OR p.exit_timestamp >= ?)"); params.append(exit_from)
        if exit_to: clauses.append("(p.exit_timestamp IS NULL OR p.exit_timestamp <= ?)"); params.append(exit_to)
        where = " AND ".join(clauses) or "1=1"
        with self.db.get_connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM model_d_bot_journal j LEFT JOIN model_d_bot_positions p ON p.position_id=j.position_id WHERE {where}", params).fetchone()[0]
            rows = [dict(r) for r in conn.execute(f"SELECT j.*,p.symbol,p.direction,p.quantity,p.entry_price,p.margin_usdt,p.exit_price,p.realized_pnl,p.fees,p.funding,p.slippage,p.stop_loss,p.take_profit,p.status,p.mark_price,p.unrealized_pnl FROM model_d_bot_journal j LEFT JOIN model_d_bot_positions p ON p.position_id=j.position_id WHERE {where} ORDER BY j.event_timestamp DESC,j.lifecycle_id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()]
        return rows, total

    def persist_mark(self, position: BotPosition) -> None:
        with self.db.get_connection() as conn:
            conn.execute("UPDATE model_d_bot_positions SET mark_price=?,unrealized_pnl=? WHERE position_id=? AND status='OPEN'", (position.mark_price, position.unrealized_pnl, position.position_id)); conn.commit()


def default_bots() -> Dict[str, ModelDBot]:
    return {c.bot_id: ModelDBot(c) for c in (BotConfig("MODEL_D_BOT_15", 15.0), BotConfig("MODEL_D_BOT_25", 25.0), BotConfig("MODEL_D_BOT_100", 100.0))}
