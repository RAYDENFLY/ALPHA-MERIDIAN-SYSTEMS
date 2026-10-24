"""Bounded Model D scan worker with explicit lifecycle and auditable observations."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import threading, time, uuid
from typing import Any, Dict
from src.model_d.features import build_features, AMDState, classify_amd
from src.model_d.decision import decide
from src.model_d.targets import derive_targets


class ModelDScanWorker:
    def __init__(self, db: Any, universe: Any, collector: Any, config: Any, max_workers: int = 4, bots: Any = None, bot_store: Any = None):
        self.db, self.universe, self.collector, self.config, self.bots, self.bot_store = db, universe, collector, config, bots or {}, bot_store
        self.max_workers = max(1, min(max_workers, 8)); self._lock = threading.Lock(); self._stop = threading.Event(); self._thread = None
        self.state = {"enabled": bool(config.enabled and config.collector_enabled), "disable_reason": None if (config.enabled and config.collector_enabled) else "MODEL_D_ENABLED and MODEL_D_COLLECTOR_ENABLED are required", "health": "STOPPED", "last_scan_utc": None, "next_scan_utc": None, "cycle_id": None, "scanned": 0, "failed": 0, "unavailable": 0, "total": 0}
        with db.get_connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS model_d_scan_observations(
                observation_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, symbol TEXT NOT NULL,
                venue TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL,
                exchange_timestamp_ms INTEGER, received_timestamp_ms INTEGER NOT NULL,
                freshness_status TEXT NOT NULL, feature_version TEXT NOT NULL,
                error TEXT, created_at TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_d_scan_cycle ON model_d_scan_observations(cycle_id, symbol)"); conn.commit()
            conn.execute("""CREATE TABLE IF NOT EXISTS model_d_signal_decisions(
                signal_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL,
                reason TEXT NOT NULL, entry_price REAL, features_json TEXT NOT NULL, created_at TEXT NOT NULL)"""); conn.commit()

    def start(self) -> bool:
        if not self.state["enabled"] or (self._thread and self._thread.is_alive()): return False
        self._stop.clear(); self._thread = threading.Thread(target=self._loop, name="model-d-scan", daemon=True); self._thread.start(); return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=5)
        self.state["health"] = "STOPPED"

    def _loop(self):
        self.state["health"] = "RUNNING"
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(max(30, int(getattr(self.config, "scan_interval_sec", 300))))

    def run_once(self) -> Dict[str, Any]:
        if not self.state["enabled"]: return {"status": "DISABLED", **self.state}
        if not self._lock.acquire(blocking=False): return {"status": "OVERLAPPING_SCAN_REJECTED"}
        cycle_id = f"MD-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"; started = int(time.time()*1000)
        try:
            instruments = self.universe.refresh(); self.state["total"] = len(instruments); self.state["scanned"] = self.state["failed"] = self.state["unavailable"] = 0
            def scan(inst):
                try:
                    data = self.collector.collect_symbol(inst.symbol)
                    sources = data.get("sources", {}); trade = sources.get("trades", {})
                    freshness = trade.get("status", "UNAVAILABLE")
                    features = build_features(data.get("order_book"), data.get("trades", []), getattr(self.config, "trade_reference_window", 200))
                    amd = classify_amd(data.get("candles", []))
                    signal = decide(inst.symbol, int(time.time()*1000), features, amd, f"{cycle_id}:{inst.symbol}")
                    candle_status = sources.get("candles", {}).get("status", "UNAVAILABLE")
                    if freshness != "LIVE" or candle_status != "LIVE": signal.direction = "WAIT"; signal.explanation = "primary Binance source or closed candle context unavailable"
                    feasibility = {bot_id: bot.assess(inst.symbol, signal.direction, features.mid_price, getattr(inst, "min_notional", 5.0)) for bot_id, bot in self.bots.items()} if self.bots else {}
                    entries = {}
                    stop_loss, take_profit, target_inputs = derive_targets(signal.direction, features.mid_price, amd)
                    if signal.direction != "WAIT" and stop_loss is None:
                        signal.direction = "WAIT"; signal.explanation = "risk-compatible structure target unavailable"
                    if signal.direction in ("LONG", "SHORT") and self.bot_store:
                        for bot_id, bot in self.bots.items():
                            if feasibility.get(bot_id, {}).get("status") == "ENTRY_ELIGIBLE":
                                try:
                                    position = bot.open(f"{cycle_id}:{inst.symbol}", inst.symbol, signal.direction, features.mid_price, getattr(inst, "min_notional", 5.0), stop_loss=stop_loss, take_profit=take_profit)
                                    position.target_policy_version = target_inputs.get("policy_version")
                                    position.target_inputs = __import__('json').dumps(target_inputs, sort_keys=True)
                                    self.bot_store.persist_open(position); entries[bot_id] = "ENTRY_COMMITTED"
                                except ValueError as exc:
                                    entries[bot_id] = str(exc)
                    feasibility["entries"] = entries
                    if self.bot_store:
                        for bot in self.bots.values():
                            for position in list(bot.positions.values()):
                                if position.symbol != inst.symbol or position.status != "OPEN": continue
                                if freshness != "LIVE" or candle_status != "LIVE":
                                    bot.mark(position.position_id, None); continue
                                bot.mark(position.position_id, features.mid_price)
                                last = data.get("candles", [])[-1] if data.get("candles") else None
                                trigger = bot.exit_trigger(position.position_id, last.high, last.low) if last else None
                                if trigger:
                                    closed = bot.close(position.position_id, trigger[0], reason=trigger[1]); self.bot_store.persist_close(bot, closed)
                    return inst.symbol, signal.direction, signal.explanation, trade.get("exchange_timestamp_ms"), freshness, None, features.to_dict(), features.mid_price, feasibility
                except Exception:
                    return inst.symbol, "WAIT", "symbol_scan_error", None, "ERROR", "symbol scan failed", {}, None, {}
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                results = [future.result() for future in as_completed([pool.submit(scan, i) for i in instruments])]
            now = int(time.time()*1000); created = datetime.now(timezone.utc).isoformat()
            with self.db.get_connection() as conn:
                for symbol, decision, reason, source_ts, freshness, error, features, entry_price, feasibility in results:
                    conn.execute("INSERT INTO model_d_scan_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (f"{cycle_id}:{symbol}", cycle_id, symbol, "binance", decision, reason, source_ts, now, freshness, "0.1.0-research", error, created))
                    conn.execute("INSERT OR REPLACE INTO model_d_signal_decisions VALUES(?,?,?,?,?,?,?,?)", (f"{cycle_id}:{symbol}", cycle_id, symbol, decision, reason, entry_price, __import__('json').dumps({"features": features, "bot_feasibility": feasibility}), created))
                conn.commit()
            self.state.update({"health": "HEALTHY", "last_scan_utc": created, "cycle_id": cycle_id, "scanned": len(results), "failed": sum(r[5] is not None for r in results), "unavailable": sum(r[4] == "UNAVAILABLE" for r in results)})
            return {"status": "OK", "cycle_id": cycle_id, **self.state}
        finally: self._lock.release()

    def recent(self, limit: int = 50):
        limit = max(1, min(limit, 200))
        with self.db.get_connection() as conn: return [dict(r) for r in conn.execute("SELECT * FROM model_d_scan_observations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()]


class ModelDExitMonitor:
    """Independent paper-only monitor so exits do not depend on entry scans."""
    def __init__(self, collector, bots, bot_store, interval_sec=30):
        self.collector, self.bots, self.bot_store = collector, bots, bot_store
        self.interval_sec = max(10, int(interval_sec)); self._lock = threading.Lock(); self._stop = threading.Event(); self._thread = None
        self.state = {"health": "STOPPED", "last_success_utc": None, "last_cycle_status": "NEVER_RUN", "stale": 0, "unavailable": 0, "exits": 0}

    def start(self):
        if self._thread and self._thread.is_alive(): return False
        self._stop.clear(); self._thread = threading.Thread(target=self._loop, name="model-d-exit-monitor", daemon=True); self._thread.start(); return True

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=5)
        self.state["health"] = "STOPPED"

    def _loop(self):
        self.state["health"] = "RUNNING"
        while not self._stop.is_set():
            self.run_once(); self._stop.wait(self.interval_sec)

    def run_once(self):
        if not self._lock.acquire(blocking=False): return {"status": "OVERLAPPING_MONITOR_REJECTED"}
        try:
            positions = [p for bot in self.bots.values() for p in bot.positions.values() if p.status == "OPEN"]
            exits = 0; stale = 0; unavailable = 0
            for position in positions:
                try:
                    data = self.collector.collect_symbol(position.symbol); sources = data.get("sources", {})
                    book_status = sources.get("order_book", {}).get("status")
                    if book_status != "LIVE" or not data.get("order_book"):
                        stale += book_status == "STALE"; unavailable += book_status in (None, "UNAVAILABLE", "ERROR"); self.bots[position.bot_id].mark(position.position_id, None); continue
                    bot = self.bots[position.bot_id]; mid = data["order_book"].mid_price; bot.mark(position.position_id, mid); self.bot_store.persist_mark(position)
                    candles = data.get("candles", []); candle_status = sources.get("candles", {}).get("status")
                    if candle_status != "LIVE" or not candles: continue
                    trigger = bot.exit_trigger(position.position_id, candles[-1].high, candles[-1].low)
                    if trigger:
                        self.bot_store.persist_close(bot, bot.close(position.position_id, trigger[0], reason=trigger[1])); exits += 1
                except Exception:
                    unavailable += 1
            self.state.update({"health": "HEALTHY", "last_success_utc": datetime.now(timezone.utc).isoformat(), "last_cycle_status": "OK", "stale": stale, "unavailable": unavailable, "exits": self.state["exits"] + exits})
            return {"status": "OK", **self.state}
        finally: self._lock.release()
