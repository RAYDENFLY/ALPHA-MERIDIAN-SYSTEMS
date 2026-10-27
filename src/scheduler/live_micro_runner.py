"""Live Micro-Account Strategy Revision Coordinator (LIVE_MICRO_V1).

Coordinates:
- Configured LIVE_MICRO_V1 strategy as the live candidate; EMA20 remains available only when explicitly configured.
- 1m Sweep & Reclaim (SWEEP_1M_V1) as SHADOW-only (asserts NEVER live).
- Global Position Lock (max 1 concurrent position across all strategies).
- Risk limits: $0.20 fixed dollar risk, $0.45 daily loss limit, 3 filled entries/day, 25% peak DD.
- 11-stage Scanner Attrition Tracker with zero-signal bottleneck diagnosis.
- Shadow & Counterfactual journal tracking for SWEEP_1M_V1 and LLM vetoes.
"""

from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Optional, Tuple

from config.settings import LiveMicroV1Config, load_live_micro_v1_config
from src.ai.llm_guard import LLMExecutionGuard
from src.core.types import Direction
from src.data.database import DatabaseManager
from src.execution.types import OrderLifecycleRecord, OrderLifecycleStatus
from src.journal.shadow_tracker import ShadowTracker
from src.risk.global_lock import GlobalPositionLock
from src.risk.live_micro_sizing import LiveMicroSizingEngine, LiveMicroSizingResult, normalize_price_to_tick
from src.scheduler.attrition_tracker import ScannerAttritionTracker
from src.strategy.ema20_reclaim_5m import EMA20ReclaimSetup, EMA20ReclaimStrategy
from src.strategy.sweep_1m import Sweep1MSetup, Sweep1MStrategy
from src.core.types import Candle, Direction
from src.strategy.sweep_reclaim_15m import SweepReclaim15MSetup, SweepReclaim15MStrategy, compute_dynamic_structural_tp, DynamicTPResult, compute_dynamic_structural_sl, DynamicSLResult, calc_atr, find_swings_15m

logger = logging.getLogger("src.scheduler.live_micro_runner")

WIB_TIMEZONE = timezone(timedelta(hours=7))

def get_daily_reset_timezone(tz_name: str = "WIB") -> timezone:
    tz_clean = str(tz_name or "WIB").strip().upper()
    if tz_clean in ("UTC", "GMT", "+00:00", "Z"):
        return timezone.utc
    return WIB_TIMEZONE



def is_valid_position_tp_order(
    order: Dict[str, Any],
    symbol: str,
    close_side: str,
    direction: str = "LONG",
) -> bool:
    """Validate whether an exchange order is strictly an active TP order for the specific position.

    Invariants enforced:
    1. Symbol matches target symbol (case-insensitive).
    2. Order type is strictly LIMIT (never STOP, STOP_MARKET, or MARKET).
    3. Side matches position close_side (SELL for LONG, BUY for SHORT).
    4. reduce_only is strictly True (never an entry order or unhedged order).
    5. positionSide (if present and not empty/BOTH) must match direction (e.g. 'LONG'/'SHORT').
    6. Status (if present) is not a closed/cancelled state ('FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED').
    """
    if not isinstance(order, dict):
        return False

    o_sym = str(order.get("symbol", "")).strip().upper()
    if o_sym and o_sym != str(symbol).strip().upper():
        return False

    o_type = str(order.get("type", order.get("orderType", ""))).strip().upper()
    if o_type != "LIMIT":
        return False

    o_side = str(order.get("side", "")).strip().upper()
    if o_side != str(close_side).strip().upper():
        return False

    is_ro = bool(order.get("reduceOnly", order.get("reduce_only", False)))
    if not is_ro:
        return False

    o_pside = str(order.get("positionSide", order.get("position_side", ""))).strip().upper()
    if o_pside and o_pside not in ("BOTH", ""):
        if o_pside != str(direction).strip().upper():
            return False

    o_status = str(order.get("status", "")).strip().upper()
    if o_status in ("FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"):
        return False

    return True


class LiveMicroCoordinator:
    """Master coordinator for LIVE_MICRO_V1 execution and shadow tracking."""

    is_valid_position_tp_order = staticmethod(is_valid_position_tp_order)

    def __init__(
        self,
        config: Optional[LiveMicroV1Config] = None,
        db: Optional[DatabaseManager] = None,
        exchange: Optional[Any] = None,
    ) -> None:
        self.config = config or load_live_micro_v1_config()
        self.db = db
        self.exchange = exchange

        # Risk and Lock components
        self.global_lock = GlobalPositionLock()
        self.sizing_engine = LiveMicroSizingEngine(
            risk_cfg=self.config.risk,
            costs_cfg=self.config.costs,
        )

        # Strategies
        self.ema_strategy = EMA20ReclaimStrategy(
            risk_cfg=self.config.risk,
            costs_cfg=self.config.costs,
            sizing_engine=self.sizing_engine,
        )
        self.sweep_reclaim_strategy = SweepReclaim15MStrategy(
            risk_cfg=self.config.risk,
            costs_cfg=self.config.costs,
            sizing_engine=self.sizing_engine,
        )
        self.sweep_strategy = Sweep1MStrategy(
            risk_cfg=self.config.risk,
            costs_cfg=self.config.costs,
            sizing_engine=self.sizing_engine,
        )

        # Strict invariant verification: SWEEP_1M_V1 must NEVER be authorized for live
        if self.sweep_strategy.is_live:
            raise RuntimeError("CRITICAL INVARIANT VIOLATION: SWEEP_1M_V1 must be SHADOW-only and never live!")

        # Shadow & Counterfactual Journal
        self.shadow_tracker = ShadowTracker(self.db) if self.db else None
        self.llm_guard = LLMExecutionGuard(shadow_tracker=self.shadow_tracker)

        # Dynamic risk tracking state
        self.current_equity = 15.72  # Micro account baseline
        self.peak_equity = 15.72
        tz = self.reset_timezone
        self.current_day_str = datetime.now(tz).strftime("%Y-%m-%d")
        self.daily_filled_entries = 0
        self.daily_realized_loss = 0.0  # Positive number representing accumulated loss
        self.kill_switch_triggered = False
        self.kill_switch_reason = ""
        # Fail closed after a protection/reconciliation incident. This flag only
        # blocks new entries; it never cancels or changes an existing position.
        self.entries_paused = False
        # Adaptive TP & Invalidation Tracking
        self.latest_adaptive_tp_provenance: Dict[str, Dict[str, Any]] = {}
        self.adaptive_tp_history: List[Dict[str, Any]] = []
        self.last_structural_invalidation_status: Dict[str, Dict[str, Any]] = {}
        self.latest_dynamic_sl_provenance: Dict[str, Dict[str, Any]] = {}
        self.dynamic_sl_history: List[Dict[str, Any]] = []
        if self.db:
            self.sync_daily_metrics_from_journal()

    @property
    def reset_timezone(self) -> timezone:
        cfg_risk = getattr(self.config, "risk", None) if hasattr(self, "config") else None
        tz_name = getattr(cfg_risk, "daily_reset_timezone", "WIB") if cfg_risk else "WIB"
        return get_daily_reset_timezone(tz_name)

    def _reset_daily_metrics_if_new_day(self) -> None:
        """Reset daily counters at configured timezone (WIB/UTC) midnight rollover."""
        tz = self.reset_timezone
        now_day = datetime.now(tz).strftime("%Y-%m-%d")
        if now_day != self.current_day_str:
            tz_label = "WIB" if tz == WIB_TIMEZONE else "UTC"
            logger.info("New %s day %s detected. Resetting daily counters.", tz_label, now_day)
            self.current_day_str = now_day
            self.daily_filled_entries = 0
            self.daily_realized_loss = 0.0

    def sync_daily_metrics_from_journal(self) -> int:
        """Synchronize daily_filled_entries and daily_realized_loss from persistent journal."""
        self._reset_daily_metrics_if_new_day()
        if not self.db:
            return self.daily_filled_entries
        try:
            tz = self.reset_timezone
            day_str = datetime.now(tz).date().isoformat()
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    """SELECT first_fill_timestamp_utc, created_at_utc, status, net_pnl_usd, reconciliation_status
                       FROM automated_trade_journal
                       WHERE mode = 'LIVE' AND status IN ('OPEN', 'CLOSED')"""
                ).fetchall()

            filled_today_count = 0
            loss_today = 0.0
            for r in rows:
                first_fill_ts = r[0]
                created_ts = r[1]
                st = r[2]
                net_pnl = float(r[3] or 0.0)
                ts_to_check = first_fill_ts or created_ts
                if not ts_to_check:
                    continue
                dt_str = str(ts_to_check).strip()
                try:
                    if dt_str.endswith("Z"):
                        dt_str = dt_str[:-1] + "+00:00"
                    dt = datetime.fromisoformat(dt_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt.astimezone(tz).date().isoformat() == day_str:
                        filled_today_count += 1
                        if st == "CLOSED" and net_pnl < 0:
                            loss_today += abs(net_pnl)
                except Exception:
                    if dt_str.startswith(day_str):
                        filled_today_count += 1
                        if st == "CLOSED" and net_pnl < 0:
                            loss_today += abs(net_pnl)

            self.daily_filled_entries = max(self.daily_filled_entries, filled_today_count)
            if loss_today > 0:
                self.daily_realized_loss = max(self.daily_realized_loss, loss_today)
            return self.daily_filled_entries
        except Exception as exc:
            logger.warning("Failed to sync daily metrics from journal: %s", exc)
            return self.daily_filled_entries

    def check_risk_limits(self) -> Tuple[bool, str]:
        """Check all overarching risk limits before considering trade execution."""
        self._reset_daily_metrics_if_new_day()
        if self.db and self.daily_filled_entries == 0:
            self.sync_daily_metrics_from_journal()

        if self.kill_switch_triggered:
            return False, f"Kill switch active: {self.kill_switch_reason}"

        # 1. Max entries per day (3)
        if self.daily_filled_entries >= self.config.risk.max_filled_entries_per_day:
            return False, f"Daily max filled entries reached ({self.daily_filled_entries}/{self.config.risk.max_filled_entries_per_day})"

        # 2. Daily loss limit ($0.45 USD)
        if self.daily_realized_loss >= self.config.risk.daily_loss_limit_usd:
            return False, f"Daily loss limit breached (${self.daily_realized_loss:.3f} >= ${self.config.risk.daily_loss_limit_usd:.3f})"

        # 3. Peak equity drawdown kill switch (25.0%)
        if self.peak_equity > 0:
            drawdown_pct = ((self.peak_equity - self.current_equity) / self.peak_equity) * 100.0
            if drawdown_pct >= self.config.risk.max_drawdown_from_peak_pct:
                return self.trigger_kill_switch(
                    f"Max drawdown breached: {drawdown_pct:.1f}% >= {self.config.risk.max_drawdown_from_peak_pct}%",
                    emergency_close=True,
                )

        return True, "OK"

    def trigger_kill_switch(self, reason: str, emergency_close: bool = True) -> Tuple[bool, str]:
        """Trigger kill switch and execute formal procedure for any currently open position.
        
        Procedure:
        1. Halt all new incoming entries immediately.
        2. Cancel all resting entry and protective orders on the exchange to prevent unintended fills.
        3. If an open position exists on the exchange:
           - Immediately send a reduce-only MARKET order to close the remaining quantity.
           - Record in journal with exit_reason='KILL_SWITCH_EMERGENCY_CLOSE'.
        4. Release GlobalPositionLock with status 'KILL_SWITCH_HALTED'.
        """
        self.kill_switch_triggered = True
        self.kill_switch_reason = reason
        logger.critical("KILL SWITCH TRIGGERED: %s. Executing open position handling procedure.", reason)

        # Check if GlobalPositionLock is held or position is open
        if self.global_lock.is_locked:
            locked_symbol = getattr(self.global_lock, "active_symbol", None)
            if locked_symbol and self.exchange:
                # Step 1: Cancel all resting orders on symbol to eliminate pending exposure
                try:
                    if hasattr(self.exchange, "cancel_all_open_orders"):
                        self.exchange.cancel_all_open_orders(locked_symbol)
                except Exception as e:
                    logger.error("Failed to cancel resting orders on %s during kill switch: %s", locked_symbol, e)

                # Step 2: Query actual exchange position and close if open
                try:
                    positions = self.exchange.get_positions(locked_symbol) if hasattr(self.exchange, "get_positions") else []
                    active_pos = [p for p in positions if abs(float(p.get("positionAmt", p.get("size", 0.0)) or 0.0)) > 1e-6]

                    if active_pos and emergency_close:
                        p0 = active_pos[0]
                        pos_amt = float(p0.get("positionAmt", p0.get("size", 0.0)))
                        close_side = "SELL" if pos_amt > 0 else "BUY"
                        close_qty = abs(pos_amt)

                        logger.warning("Emergency closing open position on %s: %s %s...", locked_symbol, close_side, close_qty)
                        if hasattr(self.exchange, "create_order"):
                            self.exchange.create_order(
                                symbol=locked_symbol,
                                side=close_side,
                                order_type="MARKET",
                                quantity=close_qty,
                                reduce_only=True,
                            )
                except Exception as e:
                    logger.error("Emergency market close failed for %s during kill switch: %s", locked_symbol, e)

            self.global_lock.update_state("KILL_SWITCH_HALTED")
            self.global_lock.release(reason=f"KILL_SWITCH: {reason}")

        return False, f"Kill switch active: {reason}"

    def record_filled_trade(self, notional: float = 0.0) -> None:
        """Increment daily filled entries counter."""
        self._reset_daily_metrics_if_new_day()
        self.daily_filled_entries += 1

    def record_closed_trade(self, realized_pnl: float, new_equity: float) -> None:
        """Record trade realization, update equity, peak, and daily loss."""
        self._reset_daily_metrics_if_new_day()
        self.current_equity = new_equity
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

        if realized_pnl < 0:
            self.daily_realized_loss += abs(realized_pnl)

        # Clean up any resting open orders on the closed symbol
        if self.exchange and hasattr(self.exchange, "cancel_all_open_orders"):
            active_sym = getattr(self.global_lock, "active_symbol", None)
            if active_sym:
                try:
                    self.exchange.cancel_all_open_orders(active_sym)
                    logger.info("Purged remaining open orders on %s upon position close.", active_sym)
                except Exception as clean_err:
                    logger.warning("Failed to cancel open orders on %s after close: %s", active_sym, clean_err)

        # Re-evaluate kill switch
        self.check_risk_limits()

    def get_daily_loss_snapshot(self) -> Dict[str, Any]:
        """Return the persistent LIVE ledger loss for the current day in configured timezone (WIB/UTC).

        The in-memory counter is intentionally not authoritative: it disappears
        on restart and can diverge from exchange-reconciled fills.
        """
        tz = self.reset_timezone
        tz_name = "WIB" if tz == WIB_TIMEZONE else "UTC"
        day_str = datetime.now(tz).date().isoformat()
        daily_limit = getattr(self.config.risk, "daily_loss_limit_usdt", getattr(self.config.risk, "daily_loss_limit_usd", 0.45))
        result: Dict[str, Any] = {
            "used_usdt": None,
            "limit_usdt": float(daily_limit),
            "status": "UNVERIFIED",
            "timezone": tz_name,
            "trades_count": 0,
            "reason": "LIVE journal unavailable",
        }
        if not self.db:
            return result
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    """SELECT net_pnl_usd, status, reconciliation_status, exit_timestamp_utc
                       FROM automated_trade_journal
                       WHERE mode = 'LIVE' AND status = 'CLOSED'
                         AND reconciliation_status = 'RECONCILED'
                         AND exit_timestamp_utc IS NOT NULL"""
                ).fetchall()
                open_unreconciled = conn.execute(
                    """SELECT COUNT(*) FROM automated_trade_journal
                       WHERE mode = 'LIVE'
                         AND status IN ('OPEN','PENDING')
                         AND reconciliation_status != 'RECONCILED'"""
                ).fetchone()[0]
                closed_unreconciled_rows = conn.execute(
                    """SELECT exit_timestamp_utc FROM automated_trade_journal
                       WHERE mode = 'LIVE' AND status = 'CLOSED'
                         AND exit_timestamp_utc IS NOT NULL
                         AND reconciliation_status != 'RECONCILED'"""
                ).fetchall()

            today_rows = []
            for row in rows:
                dt_str = str(row[3] or "").strip()
                if not dt_str:
                    continue
                try:
                    if dt_str.endswith("Z"):
                        dt_str = dt_str[:-1] + "+00:00"
                    dt = datetime.fromisoformat(dt_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt.astimezone(tz).date().isoformat() == day_str:
                        today_rows.append(row)
                except Exception:
                    if dt_str.startswith(day_str):
                        today_rows.append(row)

            unresolved_today = 0
            for row in closed_unreconciled_rows:
                dt_str = str(row[0] or "").strip()
                if not dt_str:
                    continue
                try:
                    if dt_str.endswith("Z"):
                        dt_str = dt_str[:-1] + "+00:00"
                    dt = datetime.fromisoformat(dt_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt.astimezone(tz).date().isoformat() == day_str:
                        unresolved_today += 1
                except Exception:
                    if dt_str.startswith(day_str):
                        unresolved_today += 1

            used = sum(max(0.0, -float(row[0] or 0.0)) for row in today_rows)
            unresolved = bool(open_unreconciled or unresolved_today)
            result.update({
                "used_usdt": round(used, 6),
                "status": "UNVERIFIED" if unresolved else "VERIFIED",
                "trades_count": len(today_rows),
                "reason": "LIVE trade reconciliation incomplete" if unresolved else "Exchange-reconciled closed LIVE journal",
            })
            return result
        except Exception as exc:
            result["reason"] = f"LIVE journal query failed: {exc}"
            return result

    def monitor_and_update_dynamic_sl(
        self,
        symbol: Optional[str] = None,
        dealing_range_midline: Optional[float] = None,
        fee_buffer_pct: float = 0.0008,
        tick_size: Optional[float] = None,
        dry_run: bool = False,
    ) -> List[Dict[str, Any]]:
        """3-Tier Live Dynamic SL Ratchet & Structural Trailing.

        Upgrades active position SL manager into a strict 3-stage ratchet:
        - Stage A: Initial Structural Protection (exchange STOP_MARKET rests at planned_stop_price).
        - Stage B: Break-Even Protection (at >= +1.0R or 50% Dealing Range midline reached, move to entry +/- 0.08%).
        - Stage C: Structural Swing Trail (at >= +1.5R and confirmed 15m higher-low / lower-high forms past entry,
                   trail behind new swing: swing_level -/+ 0.15 * atr_15m).

        Strict Invariants:
        1. One-way ratchet: NEVER loosen an active exchange SL (LONG SL must only increase; SHORT SL must only decrease).
        2. Operator override protection: NEVER overwrite an operator's manually widened SL with a tighter initial SL.
        3. Atomic replacement: create new STOP_MARKET order first, verify success, then cancel old SL orders.
        4. Duplicate SL hygiene: consolidate multiple active SL orders to the single active ratcheted order.
        """
        results: List[Dict[str, Any]] = []
        if not self.exchange:
            return results

        try:
            positions: List[Dict[str, Any]] = []
            if hasattr(self.exchange, "get_positions"):
                all_pos = self.exchange.get_positions(symbol) if symbol else self.exchange.get_positions()
                if isinstance(all_pos, list):
                    positions = [p for p in all_pos if abs(float(p.get("positionAmt", p.get("size", 0.0)) or 0.0)) > 1e-6]

            for pos in positions:
                sym = str(pos.get("symbol", "")).upper()
                pos_amt = float(pos.get("positionAmt", pos.get("size", 0.0)) or 0.0)
                is_long = pos_amt > 0
                direction = "LONG" if is_long else "SHORT"
                close_side = "SELL" if is_long else "BUY"
                entry_price = float(pos.get("entryPrice", pos.get("avgEntryPrice", 0.0)) or 0.0)
                mark_price = float(pos.get("markPrice", 0.0) or entry_price)

                if entry_price <= 0:
                    continue

                eff_tick = tick_size
                if eff_tick is None:
                    eff_tick = 0.01
                    if hasattr(self.exchange, "get_symbol_info"):
                        try:
                            s_info = self.exchange.get_symbol_info(sym)
                            if s_info and "tickSize" in s_info:
                                eff_tick = float(s_info["tickSize"])
                        except Exception:
                            pass

                # Query open orders for protective SL orders
                open_orders = []
                if hasattr(self.exchange, "get_open_orders"):
                    try:
                        open_orders = self.exchange.get_open_orders(sym) or []
                    except Exception as err:
                        logger.warning("Could not fetch open orders for %s: %s", sym, err)

                sl_orders = [
                    o for o in open_orders
                    if str(o.get("type", "")).upper() in ("STOP_MARKET", "STOP")
                    and bool(o.get("reduceOnly", False))
                ]

                # Identify current SL order & price (if multiple, find the most protective)
                curr_sl_order = None
                curr_sl_price = None
                sl_order_id = None
                if sl_orders:
                    if is_long:
                        curr_sl_order = max(sl_orders, key=lambda o: float(o.get("stopPrice", 0.0)))
                    else:
                        curr_sl_order = min(sl_orders, key=lambda o: float(o.get("stopPrice", float("inf"))))
                    curr_sl_price = float(curr_sl_order.get("stopPrice", 0.0))
                    sl_order_id = str(curr_sl_order.get("orderId", ""))

                # Lookup journal trade for planned initial stop price & risk
                journal_trade = None
                planned_stop = 0.0
                if self.db:
                    try:
                        with self.db.get_connection() as conn:
                            row = conn.execute(
                                """SELECT journal_id, trade_id, planned_stop_price, planned_entry_price, sl_order_id, sl_price
                                   FROM automated_trade_journal
                                   WHERE symbol = ? AND mode = 'LIVE' AND status = 'OPEN'
                                   ORDER BY created_at_utc DESC LIMIT 1""",
                                (sym,),
                            ).fetchone()
                            if row:
                                journal_trade = {
                                    "journal_id": row[0],
                                    "trade_id": row[1],
                                    "planned_stop_price": float(row[2] or 0.0),
                                    "planned_entry_price": float(row[3] or 0.0),
                                    "sl_order_id": row[4],
                                    "sl_price": float(row[5] or 0.0),
                                }
                                planned_stop = journal_trade["planned_stop_price"]
                    except Exception as db_err:
                        logger.warning("Error querying journal in dynamic SL for %s: %s", sym, db_err)

                # Determine reference initial risk (1.0R baseline)
                if planned_stop > 0 and abs(entry_price - planned_stop) > 0:
                    initial_risk_dist = abs(entry_price - planned_stop)
                elif curr_sl_price is not None and abs(entry_price - curr_sl_price) > 0:
                    initial_risk_dist = abs(entry_price - curr_sl_price)
                else:
                    initial_risk_dist = entry_price * 0.0120  # fallback 1.20%

                # Calculate unrealized R multiple
                if is_long:
                    unrealized_r = (mark_price - entry_price) / initial_risk_dist
                else:
                    unrealized_r = (entry_price - mark_price) / initial_risk_dist

                # Fetch 15m and 1h candles for structural analysis
                candles_15m = []
                candles_1h = []
                if hasattr(self.exchange, "get_klines"):
                    try:
                        candles_15m = self.exchange.get_klines(sym, timeframe="15m", limit=60) or []
                        candles_1h = self.exchange.get_klines(sym, timeframe="1h", limit=48) or []
                    except Exception as kerr:
                        logger.warning("Could not fetch klines for dynamic SL on %s: %s", sym, kerr)

                # Dealing range midline (50%)
                dr_midline = dealing_range_midline
                if dr_midline is None:
                    if candles_1h and len(candles_1h) >= 24:
                        window = candles_1h[-72:] if len(candles_1h) >= 72 else candles_1h
                        dr_midline = (min(c.low for c in window) + max(c.high for c in window)) / 2.0
                    elif len(candles_15m) >= 40:
                        window = candles_15m[-96:] if len(candles_15m) >= 96 else candles_15m
                        dr_midline = (min(c.low for c in window) + max(c.high for c in window)) / 2.0

                atr_15m = calc_atr(candles_15m, period=14) if candles_15m else (entry_price * 0.005)

                target_sl = None
                ratchet_stage = None
                trigger_reason = None

                # Compute Break-Even price level
                if is_long:
                    be_stop_raw = entry_price * (1.0 + fee_buffer_pct)
                    be_stop_norm = normalize_price_to_tick(be_stop_raw, eff_tick, rounding="CEIL")
                else:
                    be_stop_raw = entry_price * (1.0 - fee_buffer_pct)
                    be_stop_norm = normalize_price_to_tick(be_stop_raw, eff_tick, rounding="FLOOR")

                # =========================================================================
                # Stage C Evaluation: >= +1.5R Structural Swing Trail
                # =========================================================================
                stage_c_eligible = False
                if unrealized_r >= 1.50 and len(candles_15m) >= 10:
                    swing_highs, swing_lows = find_swings_15m(candles_15m[:-1], left_bars=3, right_bars=2)
                    if is_long:
                        past_entry_lows = [s[1] for s in swing_lows if s[1] > entry_price]
                        if past_entry_lows:
                            best_swing_low = max(past_entry_lows)
                            trail_raw = best_swing_low - (0.15 * atr_15m)
                            trail_norm = normalize_price_to_tick(trail_raw, eff_tick, rounding="FLOOR")
                            if trail_norm > be_stop_norm and (curr_sl_price is None or trail_norm > curr_sl_price):
                                target_sl = trail_norm
                                ratchet_stage = "STAGE_C_STRUCTURAL_SWING_TRAIL"
                                trigger_reason = (
                                    f"+{unrealized_r:.2f}R reached (>= +1.5R): Trailing behind 15m higher-low "
                                    f"${best_swing_low:.4f} (-0.15*ATR)"
                                )
                                stage_c_eligible = True
                    else:  # SHORT
                        past_entry_highs = [s[1] for s in swing_highs if s[1] < entry_price]
                        if past_entry_highs:
                            best_swing_high = min(past_entry_highs)
                            trail_raw = best_swing_high + (0.15 * atr_15m)
                            trail_norm = normalize_price_to_tick(trail_raw, eff_tick, rounding="CEIL")
                            if trail_norm < be_stop_norm and (curr_sl_price is None or trail_norm < curr_sl_price):
                                target_sl = trail_norm
                                ratchet_stage = "STAGE_C_STRUCTURAL_SWING_TRAIL"
                                trigger_reason = (
                                    f"+{unrealized_r:.2f}R reached (>= +1.5R): Trailing behind 15m lower-high "
                                    f"${best_swing_high:.4f} (+0.15*ATR)"
                                )
                                stage_c_eligible = True

                # =========================================================================
                # Stage B Evaluation: >= +1.0R or 50% Dealing Range Midline Reached
                # =========================================================================
                if not stage_c_eligible:
                    hit_1r = unrealized_r >= 1.00
                    hit_eq = (dr_midline is not None and ((is_long and mark_price >= dr_midline) or (not is_long and mark_price <= dr_midline)))
                    if hit_1r or hit_eq:
                        if is_long and (curr_sl_price is None or be_stop_norm > curr_sl_price):
                            target_sl = be_stop_norm
                            ratchet_stage = "STAGE_B_BREAK_EVEN"
                            trigger_reason = "+1.0R unrealized profit reached" if hit_1r else "50% Dealing Range midline reached"
                        elif not is_long and (curr_sl_price is None or be_stop_norm < curr_sl_price):
                            target_sl = be_stop_norm
                            ratchet_stage = "STAGE_B_BREAK_EVEN"
                            trigger_reason = "+1.0R unrealized profit reached" if hit_1r else "50% Dealing Range midline reached"

                # =========================================================================
                # Stage A Evaluation: Initial Structural Protection (Missing SL Recovery)
                # =========================================================================
                if target_sl is None and curr_sl_order is None and planned_stop > 0:
                    target_sl = planned_stop
                    ratchet_stage = "STAGE_A_INITIAL_STRUCTURAL"
                    trigger_reason = "Missing exchange stop-loss recovered from planned structural stop"

                # Check if an update is needed
                if target_sl is None:
                    continue

                # =========================================================================
                # STRICT ONE-WAY RATCHET INVARIANT ENFORCEMENT
                # =========================================================================
                if curr_sl_price is not None:
                    if is_long and target_sl <= curr_sl_price:
                        logger.debug(
                            "Dynamic SL ratchet invariant for %s LONG: proposed %s <= curr %s (no move)",
                            sym, target_sl, curr_sl_price,
                        )
                        continue
                    if not is_long and target_sl >= curr_sl_price:
                        logger.debug(
                            "Dynamic SL ratchet invariant for %s SHORT: proposed %s >= curr %s (no move)",
                            sym, target_sl, curr_sl_price,
                        )
                        continue

                provenance = {
                    "symbol": sym,
                    "direction": direction,
                    "ratchet_stage": ratchet_stage,
                    "entry_price": entry_price,
                    "mark_price": mark_price,
                    "unrealized_r": round(unrealized_r, 2),
                    "old_sl_price": curr_sl_price,
                    "new_sl_price": target_sl,
                    "trigger_reason": trigger_reason,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
                self._record_dynamic_sl_provenance(sym, provenance)

                if dry_run:
                    logger.info("[DRY_RUN] Dynamic SL ratchet for %s: %s -> %s (%s, Stage: %s)", sym, curr_sl_price, target_sl, trigger_reason, ratchet_stage)
                    results.append({
                        "status": "DRY_RUN_RATCHET_PLANNED",
                        "symbol": sym,
                        "direction": direction,
                        "entry_price": entry_price,
                        "old_sl_price": curr_sl_price,
                        "new_sl_price": target_sl,
                        "trigger_reason": trigger_reason,
                        "ratchet_stage": ratchet_stage,
                    })
                    continue

                # =========================================================================
                # ATOMIC REPLACEMENT & DUPLICATE SL HYGIENE
                # =========================================================================
                logger.info(
                    "Executing Dynamic SL ratchet for %s %s: Entry %s, Mark %s, Old SL %s -> New SL %s (%s, Stage: %s)",
                    sym, direction, entry_price, mark_price, curr_sl_price, target_sl, trigger_reason, ratchet_stage,
                )
                try:
                    new_order_id = ""
                    if hasattr(self.exchange, "create_order"):
                        new_sl_resp = self.exchange.create_order(
                            symbol=sym,
                            side=close_side,
                            order_type="STOP_MARKET",
                            quantity=abs(pos_amt),
                            stop_price=target_sl,
                            reduce_only=True,
                        )
                        new_order_id = str(new_sl_resp.get("orderId", ""))

                    # Clean up all superseded SL orders on symbol
                    for old_sl in sl_orders:
                        old_oid = str(old_sl.get("orderId", ""))
                        if old_oid and old_oid != new_order_id and hasattr(self.exchange, "cancel_order"):
                            try:
                                self.exchange.cancel_order(symbol=sym, order_id=old_oid)
                                logger.info("Cancelled superseded SL order %s for %s", old_oid, sym)
                            except Exception as c_err:
                                logger.warning("Failed to cancel superseded SL %s: %s", old_oid, c_err)

                    # Update persistent trade journal
                    if self.db and journal_trade:
                        try:
                            with self.db.get_connection() as conn:
                                note_tag = f"[DYNAMIC_SL: {curr_sl_price} -> {target_sl} ({ratchet_stage})]"
                                conn.execute(
                                    """UPDATE automated_trade_journal
                                       SET sl_order_id = ?, sl_price = ?, sl_status = 'PLACED',
                                           notes = CASE WHEN notes = '' OR notes IS NULL THEN ? ELSE notes || '; ' || ? END,
                                           updated_at_utc = ?
                                       WHERE journal_id = ?""",
                                    (new_order_id, target_sl, note_tag, note_tag, datetime.now(timezone.utc).isoformat(), journal_trade["journal_id"]),
                                )
                                conn.commit()
                        except Exception as db_up_err:
                            logger.warning("Failed to update journal DB for dynamic SL: %s", db_up_err)

                    results.append({
                        "status": "MOVED_TO_BREAKEVEN" if ratchet_stage == "STAGE_B_BREAK_EVEN" else f"MOVED_TO_{ratchet_stage}",
                        "symbol": sym,
                        "direction": direction,
                        "entry_price": entry_price,
                        "old_sl_price": curr_sl_price,
                        "new_sl_price": target_sl,
                        "trigger_reason": trigger_reason,
                        "new_order_id": new_order_id,
                        "ratchet_stage": ratchet_stage,
                    })

                except Exception as order_err:
                    logger.error("Failed to place dynamic STOP_MARKET for %s: %s", sym, order_err)
                    results.append({
                        "status": "FAILED",
                        "symbol": sym,
                        "error": str(order_err),
                    })

        except Exception as e:
            logger.error("Error in monitor_and_update_dynamic_sl: %s", e, exc_info=True)

        return results

    def monitor_and_update_breakeven_protection(
        self,
        symbol: Optional[str] = None,
        dealing_range_midline: Optional[float] = None,
        fee_buffer_pct: float = 0.0008,
        tick_size: float = 0.01,
    ) -> List[Dict[str, Any]]:
        """Auto Break-Even Protection at >= +1.0R Minor Resistance or 50% Dealing Range Midline.

        Backwards-compatible wrapper delegating to monitor_and_update_dynamic_sl.
        """
        return self.monitor_and_update_dynamic_sl(
            symbol=symbol,
            dealing_range_midline=dealing_range_midline,
            fee_buffer_pct=fee_buffer_pct,
            tick_size=tick_size,
        )


    def _record_dynamic_sl_provenance(self, symbol: str, provenance: Dict[str, Any]) -> None:
        """Store in-memory latest dynamic SL provenance and append to audit history."""
        sym_clean = symbol.upper()
        self.latest_dynamic_sl_provenance[sym_clean] = dict(provenance)
        self.dynamic_sl_history.append(dict(provenance))
        if len(self.dynamic_sl_history) > 500:
            self.dynamic_sl_history = self.dynamic_sl_history[-500:]

    def get_latest_dynamic_sl_provenance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Return the latest dynamic SL provenance dictionary for symbol or all symbols."""
        if symbol:
            return dict(self.latest_dynamic_sl_provenance.get(symbol.upper(), {}))
        return dict(self.latest_dynamic_sl_provenance)

    def _record_adaptive_tp_provenance(self, symbol: str, provenance: Dict[str, Any]) -> None:
        """Store in-memory latest adaptive TP provenance and append to audit history."""
        sym_clean = symbol.upper()
        self.latest_adaptive_tp_provenance[sym_clean] = dict(provenance)
        self.adaptive_tp_history.append(dict(provenance))
        if len(self.adaptive_tp_history) > 500:
            self.adaptive_tp_history = self.adaptive_tp_history[-500:]

    def get_latest_adaptive_tp_provenance(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Return the latest adaptive TP provenance dictionary for symbol or all symbols."""
        if symbol:
            return dict(self.latest_adaptive_tp_provenance.get(symbol.upper(), {}))
        return dict(self.latest_adaptive_tp_provenance)

    def monitor_and_update_adaptive_tp(
        self,
        symbol: Optional[str] = None,
        min_price_change_pct: float = 0.003,
        tick_size: Optional[float] = None,
        dry_run: bool = False,
    ) -> List[Dict[str, Any]]:
        """Adaptive Take-Profit for Existing Open Positions.

        Periodically recalculates structural TP using fresh 15m/1H market data and L2 orderbook
        depth. Atomically replaces existing exchange LIMIT TP orders if the newly computed target
        differs by >= min_price_change_pct (default 0.3%). Enforces strict profitable-side
        directional bounds (LONG: TP > mark; SHORT: TP < mark). Never cancels old TP if replacement
        order submission fails. Never touches active Stop-Loss or Auto Break-Even orders.
        """
        results: List[Dict[str, Any]] = []
        if not self.exchange:
            return results

        try:
            positions: List[Dict[str, Any]] = []
            if hasattr(self.exchange, "get_positions"):
                all_pos = self.exchange.get_positions(symbol) if symbol else self.exchange.get_positions()
                if isinstance(all_pos, list):
                    positions = [p for p in all_pos if abs(float(p.get("positionAmt", p.get("size", 0.0)) or 0.0)) > 1e-6]

            for pos in positions:
                sym = str(pos.get("symbol", "")).upper()
                pos_amt = float(pos.get("positionAmt", pos.get("size", 0.0)) or 0.0)
                is_long = pos_amt > 0
                direction = "LONG" if is_long else "SHORT"
                close_side = "SELL" if is_long else "BUY"
                entry_price = float(pos.get("entryPrice", pos.get("avgEntryPrice", 0.0)) or 0.0)
                mark_price = float(pos.get("markPrice", 0.0) or entry_price)

                if entry_price <= 0:
                    continue

                # Query open orders for existing TP and SL
                open_orders = []
                if hasattr(self.exchange, "get_open_orders"):
                    try:
                        open_orders = self.exchange.get_open_orders(sym) or []
                    except Exception as err:
                        logger.warning("Could not fetch open orders for %s in adaptive TP: %s", sym, err)

                tp_orders = [
                    o for o in open_orders
                    if is_valid_position_tp_order(o, sym, close_side, direction)
                ]

                sl_orders = [
                    o for o in open_orders
                    if str(o.get("type", o.get("orderType", ""))).upper() in ("STOP_MARKET", "STOP")
                    and bool(o.get("reduceOnly", o.get("reduce_only", False)))
                    and str(o.get("side", "")).upper() == close_side
                ]

                curr_tp_order = tp_orders[0] if tp_orders else None
                curr_tp_price = float(curr_tp_order.get("price", 0.0)) if curr_tp_order else 0.0
                curr_tp_order_id = str(curr_tp_order.get("orderId", curr_tp_order.get("order_id", ""))) if curr_tp_order else ""

                if len(tp_orders) > 1:
                    existing_tps_summary = [f"#{o.get('orderId', o.get('order_id', ''))}@{float(o.get('price', 0.0)):.4f}" for o in tp_orders]
                    logger.warning(
                        "Multiple active TP orders detected for %s: %s. Applying duplicate-order reconciliation.",
                        sym, existing_tps_summary,
                    )

                # Instrument tick_size & step_size
                eff_tick = tick_size if tick_size is not None else 0.01
                eff_step = 0.01
                if tick_size is None and hasattr(self.exchange, "_catalog_cache") and isinstance(self.exchange._catalog_cache, dict):
                    inst = self.exchange._catalog_cache.get(sym)
                    if inst and hasattr(inst, "tick_size") and isinstance(getattr(inst, "tick_size", None), (int, float, str)):
                        try:
                            eff_tick = float(inst.tick_size)
                        except (TypeError, ValueError):
                            pass
                    if inst and hasattr(inst, "step_size") and isinstance(getattr(inst, "step_size", None), (int, float, str)):
                        try:
                            eff_step = float(inst.step_size)
                        except (TypeError, ValueError):
                            pass
                elif tick_size is None and hasattr(self.exchange, "get_symbol_specs"):
                    try:
                        specs = self.exchange.get_symbol_specs(sym)
                        if specs and hasattr(specs, "tick_size") and isinstance(getattr(specs, "tick_size", None), (int, float, str)):
                            eff_tick = float(specs.tick_size)
                        if specs and hasattr(specs, "step_size") and isinstance(getattr(specs, "step_size", None), (int, float, str)):
                            eff_step = float(specs.step_size)
                    except Exception:
                        pass

                close_qty = normalize_price_to_tick(abs(pos_amt), eff_step, rounding="FLOOR")

                # Journal lookup for initial planned risk
                journal_trade = None
                if self.db:
                    try:
                        with self.db.get_connection() as conn:
                            row = conn.execute(
                                """SELECT journal_id, trade_id, planned_stop_price, planned_entry_price, planned_take_profit_price, tp_order_id, tp_price, sl_order_id
                                   FROM automated_trade_journal
                                   WHERE symbol = ? AND mode = 'LIVE' AND status = 'OPEN'
                                   ORDER BY created_at_utc DESC LIMIT 1""",
                                (sym,),
                            ).fetchone()
                            if row:
                                journal_trade = {
                                    "journal_id": row[0],
                                    "trade_id": row[1],
                                    "planned_stop_price": float(row[2] or 0.0),
                                    "planned_entry_price": float(row[3] or 0.0),
                                    "planned_take_profit_price": float(row[4] or 0.0),
                                    "tp_order_id": row[5],
                                    "tp_price": float(row[6] or 0.0),
                                    "sl_order_id": row[7],
                                }
                    except Exception as db_err:
                        logger.warning("Error querying journal in adaptive TP for %s: %s", sym, db_err)

                # Planned stop loss for risk determination
                planned_stop = journal_trade.get("planned_stop_price", 0.0) if journal_trade else 0.0
                if planned_stop > 0:
                    stop_loss_price = planned_stop
                elif sl_orders:
                    stop_loss_price = float(sl_orders[0].get("stopPrice", 0.0))
                else:
                    stop_loss_price = entry_price * (0.9875 if is_long else 1.0125)

                # Ensure stop loss price is strictly on risk side for calculating trade risk
                if is_long and stop_loss_price >= entry_price:
                    stop_loss_price = entry_price * 0.9875
                elif not is_long and stop_loss_price <= entry_price:
                    stop_loss_price = entry_price * 1.0125

                # Fresh candlesticks
                candles_15m = []
                candles_1h = []
                if hasattr(self.exchange, "get_klines"):
                    try:
                        candles_15m = self.exchange.get_klines(sym, timeframe="15m", limit=100) or []
                        candles_1h = self.exchange.get_klines(sym, timeframe="1h", limit=72) or []
                    except Exception as kline_err:
                        logger.warning("Could not fetch klines for %s: %s", sym, kline_err)

                if len(candles_15m) < 10:
                    logger.warning("Insufficient 15m candles for %s (%d bars), skipping adaptive TP", sym, len(candles_15m))
                    res_item = {
                        "symbol": sym,
                        "status": "SKIPPED_INSUFFICIENT_DATA",
                        "reason": f"Insufficient 15m candles ({len(candles_15m)})",
                    }
                    results.append(res_item)
                    continue

                # Dealing range bounds (72h)
                dr_bounds = None
                pos_pct = None
                if candles_1h and len(candles_1h) >= 24:
                    window = candles_1h[-72:] if len(candles_1h) >= 72 else candles_1h
                    dr_l = min(c.low for c in window)
                    dr_h = max(c.high for c in window)
                    dr_bounds = (dr_l, dr_h)
                    span = dr_h - dr_l
                    pos_pct = ((mark_price - dr_l) / (span + 1e-12)) * 100.0 if span > 0 else 50.0
                elif len(candles_15m) >= 40:
                    window = candles_15m[-96:] if len(candles_15m) >= 96 else candles_15m
                    dr_l = min(c.low for c in window)
                    dr_h = max(c.high for c in window)
                    dr_bounds = (dr_l, dr_h)
                    span = dr_h - dr_l
                    pos_pct = ((mark_price - dr_l) / (span + 1e-12)) * 100.0 if span > 0 else 50.0

                # L2 walls
                nearest_ask_wall = None
                nearest_bid_wall = None
                if hasattr(self.exchange, "get_order_book"):
                    try:
                        ob = self.exchange.get_order_book(sym, limit=50)
                        if ob and hasattr(ob, "asks") and hasattr(ob, "bids"):
                            for ask in getattr(ob, "asks", []):
                                p = float(ask.price if hasattr(ask, "price") else ask[0])
                                s = float(ask.size if hasattr(ask, "size") else ask[1])
                                if p * s >= 5000.0 and p > mark_price:
                                    nearest_ask_wall = {"price": p, "size_usdt": p * s}
                                    break
                            for bid in getattr(ob, "bids", []):
                                p = float(bid.price if hasattr(bid, "price") else bid[0])
                                s = float(bid.size if hasattr(bid, "size") else bid[1])
                                if p * s >= 5000.0 and p < mark_price:
                                    nearest_bid_wall = {"price": p, "size_usdt": p * s}
                                    break
                    except Exception as ob_err:
                        logger.debug("Orderbook fetch skipped for %s: %s", sym, ob_err)

                # Compute dynamic structural TP
                tp_res = compute_dynamic_structural_tp(
                    entry_price=entry_price,
                    stop_loss_price=stop_loss_price,
                    direction=direction,
                    candles_15m=candles_15m,
                    candles_1h=candles_1h,
                    dealing_range_bounds=dr_bounds,
                    dealing_range_position_pct=pos_pct,
                    nearest_ask_wall=nearest_ask_wall,
                    nearest_bid_wall=nearest_bid_wall,
                    tick_size=eff_tick,
                    current_mark_price=mark_price,
                    for_open_position=True,
                )

                if not tp_res.is_eligible:
                    res_item = {
                        "symbol": sym,
                        "direction": direction,
                        "status": "REJECTED",
                        "reason": tp_res.rejection_reason,
                        "tp_target_type": tp_res.tp_target_type,
                        "current_tp": curr_tp_price,
                    }
                    results.append(res_item)
                    continue

                target_tp = tp_res.take_profit_price

                # Validate profitable side of mark price
                if is_long and target_tp <= mark_price:
                    res_item = {
                        "symbol": sym,
                        "direction": direction,
                        "status": "REJECTED_BEHIND_MARK",
                        "reason": f"Target TP {target_tp} is at or behind current mark price {mark_price}",
                        "current_tp": curr_tp_price,
                        "proposed_tp": target_tp,
                    }
                    results.append(res_item)
                    continue
                if not is_long and target_tp >= mark_price:
                    res_item = {
                        "symbol": sym,
                        "direction": direction,
                        "status": "REJECTED_BEHIND_MARK",
                        "reason": f"Target TP {target_tp} is at or behind current mark price {mark_price}",
                        "current_tp": curr_tp_price,
                        "proposed_tp": target_tp,
                    }
                    results.append(res_item)
                    continue

                # Duplicate-Order Hygiene & Anti-Churn Checks
                price_diff_pct = abs(target_tp - curr_tp_price) / curr_tp_price if curr_tp_price > 0 else 1.0
                curr_tp_qty = float(curr_tp_order.get("origQty", curr_tp_order.get("orig_qty", 0.0)) or 0.0) if curr_tp_order else 0.0
                qty_matched = abs(curr_tp_qty - close_qty) < 1e-6

                # Subcase A: Multiple TP orders exist, and one already matches target_tp and close_qty
                matching_tp = next(
                    (
                        o for o in tp_orders
                        if abs(float(o.get("price", 0.0)) - target_tp) / (target_tp if target_tp > 0 else 1.0) < min_price_change_pct
                        and abs(float(o.get("origQty", o.get("orig_qty", 0.0)) or 0.0) - close_qty) < 1e-6
                    ),
                    None,
                )
                if matching_tp and len(tp_orders) > 1:
                    retained_oid = str(matching_tp.get("orderId", matching_tp.get("order_id", "")))
                    superseded_ids = [
                        str(o.get("orderId", o.get("order_id", "")))
                        for o in tp_orders
                        if str(o.get("orderId", o.get("order_id", ""))) and str(o.get("orderId", o.get("order_id", ""))) != retained_oid
                    ]
                    cancelled_ids = []
                    if not dry_run and hasattr(self.exchange, "cancel_order"):
                        for old_oid in superseded_ids:
                            try:
                                self.exchange.cancel_order(symbol=sym, order_id=old_oid)
                                cancelled_ids.append(old_oid)
                                logger.info("Cancelled duplicate superseded TP order %s for %s", old_oid, sym)
                            except Exception as cancel_err:
                                logger.warning("Failed to cancel duplicate TP %s: %s", old_oid, cancel_err)

                    if not dry_run and self.db and journal_trade and str(journal_trade.get("tp_order_id", "")) != retained_oid:
                        try:
                            with self.db.get_connection() as conn:
                                note_tag = f"[ADAPTIVE_TP: duplicate consolidated to {retained_oid} @ {target_tp}]"
                                conn.execute(
                                    """UPDATE automated_trade_journal
                                       SET tp_order_id = ?, tp_price = ?, tp_status = 'PLACED',
                                           notes = CASE WHEN notes = '' OR notes IS NULL THEN ? ELSE notes || '; ' || ? END,
                                           updated_at_utc = ?
                                       WHERE journal_id = ?""",
                                    (retained_oid, target_tp, note_tag, note_tag, datetime.now(timezone.utc).isoformat(), journal_trade["journal_id"]),
                                )
                                conn.commit()
                        except Exception as db_up_err:
                            logger.warning("Failed to update journal DB for consolidated TP: %s", db_up_err)

                    provenance = {
                        "symbol": sym,
                        "direction": direction,
                        "status": "CONSOLIDATED_DUPLICATES" if not dry_run else "DRY_RUN_ELIGIBLE",
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "old_tp_price": float(matching_tp.get("price", 0.0)),
                        "proposed_tp_price": target_tp,
                        "tp_target_type": tp_res.tp_target_type,
                        "target_level_price": tp_res.target_level_price,
                        "gross_rr": tp_res.gross_rr,
                        "structural_source": tp_res.tp_target_type,
                        "computation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "decision_reason": f"Retained valid matching TP {retained_oid} at {target_tp} and cancelled {len(superseded_ids)} duplicate order(s)" if not dry_run else f"DRY_RUN: Would retain TP {retained_oid} and cancel duplicates {superseded_ids}",
                        "retained_order_id": retained_oid,
                        "cancelled_order_ids": cancelled_ids if not dry_run else [],
                        "superseded_order_ids": superseded_ids,
                        "price_diff_pct": 0.0,
                    }
                    self._record_adaptive_tp_provenance(sym, provenance)
                    results.append(provenance)
                    continue

                # Subcase B: Exactly 1 valid TP exists and target price difference is below threshold with matched qty
                if curr_tp_price > 0 and price_diff_pct < min_price_change_pct and qty_matched and len(tp_orders) == 1:
                    provenance = {
                        "symbol": sym,
                        "direction": direction,
                        "status": "UNCHANGED",
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "old_tp_price": curr_tp_price,
                        "proposed_tp_price": target_tp,
                        "tp_target_type": tp_res.tp_target_type,
                        "target_level_price": tp_res.target_level_price,
                        "gross_rr": tp_res.gross_rr,
                        "structural_source": tp_res.tp_target_type,
                        "computation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "decision_reason": f"Price difference {price_diff_pct * 100:.2f}% below threshold {min_price_change_pct * 100:.2f}% with matched quantity",
                        "price_diff_pct": round(price_diff_pct * 100, 3),
                        "active_order_id": curr_tp_order_id,
                    }
                    self._record_adaptive_tp_provenance(sym, provenance)
                    results.append(provenance)
                    continue

                # Dry-run check
                if dry_run:
                    superseded_ids = [str(o.get("orderId", o.get("order_id", ""))) for o in tp_orders if str(o.get("orderId", o.get("order_id", "")))]
                    provenance = {
                        "symbol": sym,
                        "direction": direction,
                        "status": "DRY_RUN_ELIGIBLE",
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "old_tp_price": curr_tp_price,
                        "proposed_tp_price": target_tp,
                        "tp_target_type": tp_res.tp_target_type,
                        "target_level_price": tp_res.target_level_price,
                        "gross_rr": tp_res.gross_rr,
                        "structural_source": tp_res.tp_target_type,
                        "computation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "decision_reason": (
                            f"DRY_RUN: Would update TP from {curr_tp_price} to {target_tp} ({tp_res.tp_target_type})"
                            if len(tp_orders) <= 1 else
                            f"DRY_RUN: Would reconcile {len(tp_orders)} TP orders to single TP at {target_tp} and cancel superseded orders {superseded_ids}"
                        ),
                        "price_diff_pct": round(price_diff_pct * 100, 3),
                        "superseded_order_ids": superseded_ids,
                    }
                    self._record_adaptive_tp_provenance(sym, provenance)
                    results.append(provenance)
                    continue

                # Atomic replacement: create new LIMIT TP order first
                logger.info(
                    "Updating adaptive TP for %s %s: Entry %s, Mark %s, Old TP %s -> New Adaptive TP %s (%s, gross_rr=%.2fR, existing_tp_count=%d)",
                    sym, direction, entry_price, mark_price, curr_tp_price, target_tp, tp_res.tp_target_type, tp_res.gross_rr, len(tp_orders),
                )
                try:
                    new_order_id = ""
                    if hasattr(self.exchange, "create_order"):
                        try:
                            new_tp_resp = self.exchange.create_order(
                                symbol=sym,
                                side=close_side,
                                order_type="LIMIT",
                                quantity=close_qty,
                                price=target_tp,
                                reduce_only=True,
                                time_in_force="GTC",
                            )
                            if isinstance(new_tp_resp, dict):
                                new_order_id = str(new_tp_resp.get("orderId", new_tp_resp.get("order_id", "")))
                        except Exception as create_exc:
                            # Ambiguous timeout recovery: verify if order actually landed on exchange
                            logger.warning(
                                "create_order encountered error/timeout for %s (%s). Querying open orders to verify state...",
                                sym, create_exc,
                            )
                            if hasattr(self.exchange, "get_open_orders"):
                                try:
                                    recheck_orders = self.exchange.get_open_orders(sym) or []
                                    recheck_tps = [
                                        o for o in recheck_orders
                                        if is_valid_position_tp_order(o, sym, close_side, direction)
                                        and abs(float(o.get("price", 0.0)) - target_tp) < (eff_tick * 1.5)
                                        and abs(float(o.get("origQty", o.get("orig_qty", 0.0)) or 0.0) - close_qty) < 1e-6
                                    ]
                                    if recheck_tps:
                                        new_order_id = str(recheck_tps[0].get("orderId", recheck_tps[0].get("order_id", "")))
                                        logger.info(
                                            "Ambiguous create_order timeout resolved: order %s found active on exchange at %s.",
                                            new_order_id, target_tp,
                                        )
                                except Exception as verify_err:
                                    logger.error("Failed to recheck open orders after create_order exception: %s", verify_err)

                            if not new_order_id:
                                raise create_exc

                    if not new_order_id:
                        raise RuntimeError("Exchange accepted order call but returned no orderId")

                    # Cancel ALL superseded TP orders only after new order is accepted and verified
                    superseded_order_ids = [
                        str(o.get("orderId", o.get("order_id", "")))
                        for o in tp_orders
                        if str(o.get("orderId", o.get("order_id", ""))) and str(o.get("orderId", o.get("order_id", ""))) != new_order_id
                    ]
                    cancelled_order_ids = []
                    if superseded_order_ids and hasattr(self.exchange, "cancel_order"):
                        for old_oid in superseded_order_ids:
                            try:
                                self.exchange.cancel_order(symbol=sym, order_id=old_oid)
                                cancelled_order_ids.append(old_oid)
                                logger.info("Cancelled superseded TP order %s for %s", old_oid, sym)
                            except Exception as cancel_err:
                                logger.warning("Failed to cancel superseded TP %s after new TP placement: %s", old_oid, cancel_err)

                    # Update database journal
                    if self.db and journal_trade:
                        try:
                            with self.db.get_connection() as conn:
                                note_tag = f"[ADAPTIVE_TP: {curr_tp_price} -> {target_tp} ({tp_res.tp_target_type}, {tp_res.gross_rr}R)]"
                                conn.execute(
                                    """UPDATE automated_trade_journal
                                       SET tp_order_id = ?, tp_price = ?, tp_status = 'PLACED',
                                           notes = CASE WHEN notes = '' OR notes IS NULL THEN ? ELSE notes || '; ' || ? END,
                                           updated_at_utc = ?
                                       WHERE journal_id = ?""",
                                    (new_order_id, target_tp, note_tag, note_tag, datetime.now(timezone.utc).isoformat(), journal_trade["journal_id"]),
                                )
                                conn.commit()
                        except Exception as db_up_err:
                            logger.warning("Failed to update journal DB for adaptive TP: %s", db_up_err)

                    provenance = {
                        "symbol": sym,
                        "direction": direction,
                        "status": "UPDATED",
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "old_tp_price": curr_tp_price,
                        "new_tp_price": target_tp,
                        "proposed_tp_price": target_tp,
                        "tp_target_type": tp_res.tp_target_type,
                        "target_level_price": tp_res.target_level_price,
                        "gross_rr": tp_res.gross_rr,
                        "structural_source": tp_res.tp_target_type,
                        "computation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "decision_reason": f"Updated TP from {curr_tp_price} to {target_tp} ({tp_res.tp_target_type}) and cancelled {len(cancelled_order_ids)} superseded order(s)",
                        "old_order_id": curr_tp_order_id,
                        "new_order_id": new_order_id,
                        "superseded_order_ids": superseded_order_ids,
                        "cancelled_order_ids": cancelled_order_ids,
                        "price_diff_pct": round(price_diff_pct * 100, 3),
                    }
                    self._record_adaptive_tp_provenance(sym, provenance)
                    results.append(provenance)

                except Exception as order_err:
                    preserved_ids = [str(o.get("orderId", o.get("order_id", ""))) for o in tp_orders if str(o.get("orderId", o.get("order_id", "")))]
                    logger.error("Failed to place adaptive LIMIT TP for %s: %s. Preserving existing TP orders %s", sym, order_err, preserved_ids)
                    provenance = {
                        "symbol": sym,
                        "direction": direction,
                        "status": "FAILED",
                        "error": str(order_err),
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "old_tp_price": curr_tp_price,
                        "proposed_tp_price": target_tp,
                        "preserved_tp_order_ids": preserved_ids,
                        "preserved_tp_order_id": curr_tp_order_id,
                        "tp_target_type": tp_res.tp_target_type,
                        "computation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "decision_reason": f"Replacement order failed: {order_err}. Preserved existing TP orders {preserved_ids}.",
                    }
                    self._record_adaptive_tp_provenance(sym, provenance)
                    results.append(provenance)

        except Exception as e:
            logger.error("Error in monitor_and_update_adaptive_tp: %s", e)

        return results

    def check_structural_invalidation(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss_price: float,
        candles_15m: List[Candle],
        sweep_wick_extreme: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Diagnostic evaluation of structural invalidation vs wick sweep/reclaim.
        
        Distinguishes between a temporary wick test (liquidity grab) and a confirmed structural
        breakdown using closed 15m candle confirmation.
        Diagnostic only: does NOT close or alter live positions.
        """
        dir_clean = str(direction).strip().upper()
        is_long = dir_clean == "LONG"
        invalidation_level = sweep_wick_extreme if (sweep_wick_extreme is not None and sweep_wick_extreme > 0) else stop_loss_price

        if not candles_15m or len(candles_15m) < 2:
            return {
                "symbol": symbol,
                "status": "UNVERIFIED",
                "reason": "Insufficient 15m candles",
                "invalidation_level": invalidation_level,
            }

        # Latest closed bar is candles_15m[-2] if len >= 2 else candles_15m[-1]
        closed_candle = candles_15m[-2] if len(candles_15m) >= 2 else candles_15m[-1]

        if is_long:
            is_confirmed_breakdown = closed_candle.close < invalidation_level
            is_wick_sweep = (closed_candle.low < invalidation_level) and (closed_candle.close >= invalidation_level)
            if is_confirmed_breakdown:
                status = "CONFIRMED_STRUCTURAL_BREAKDOWN"
                reason = f"15m closed candle at {closed_candle.close:.4f} closed below invalidation level {invalidation_level:.4f}"
            elif is_wick_sweep:
                status = "WICK_SWEEP_RECLAIM"
                reason = f"15m candle pierced low {closed_candle.low:.4f} < {invalidation_level:.4f} but closed above at {closed_candle.close:.4f}"
            else:
                status = "STRUCTURE_INTACT"
                reason = f"Structure intact (15m low {closed_candle.low:.4f} >= invalidation level {invalidation_level:.4f})"
        else:
            is_confirmed_breakdown = closed_candle.close > invalidation_level
            is_wick_sweep = (closed_candle.high > invalidation_level) and (closed_candle.close <= invalidation_level)
            if is_confirmed_breakdown:
                status = "CONFIRMED_STRUCTURAL_BREAKDOWN"
                reason = f"15m closed candle at {closed_candle.close:.4f} closed above invalidation level {invalidation_level:.4f}"
            elif is_wick_sweep:
                status = "WICK_SWEEP_RECLAIM"
                reason = f"15m candle pierced high {closed_candle.high:.4f} > {invalidation_level:.4f} but closed below at {closed_candle.close:.4f}"
            else:
                status = "STRUCTURE_INTACT"
                reason = f"Structure intact (15m high {closed_candle.high:.4f} <= invalidation level {invalidation_level:.4f})"

        return {
            "symbol": symbol,
            "direction": dir_clean,
            "status": status,
            "reason": reason,
            "invalidation_level": invalidation_level,
            "closed_bar_timestamp_utc": closed_candle.datetime_utc.isoformat(),
            "closed_bar_close": closed_candle.close,
            "closed_bar_high": closed_candle.high,
            "closed_bar_low": closed_candle.low,
            "evaluation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

    def monitor_structural_invalidation(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Phase 2 diagnostic loop: inspect active positions for structural breakdown vs wick sweeps.
        
        Diagnostic only. Does NOT submit exchange orders, cancel orders, or close live positions.
        """
        alerts: List[Dict[str, Any]] = []
        if not self.exchange:
            return alerts

        try:
            positions: List[Dict[str, Any]] = []
            if hasattr(self.exchange, "get_positions"):
                all_pos = self.exchange.get_positions(symbol) if symbol else self.exchange.get_positions()
                if isinstance(all_pos, list):
                    positions = [p for p in all_pos if abs(float(p.get("positionAmt", p.get("size", 0.0)) or 0.0)) > 1e-6]

            for pos in positions:
                sym = str(pos.get("symbol", "")).upper()
                pos_amt = float(pos.get("positionAmt", pos.get("size", 0.0)) or 0.0)
                is_long = pos_amt > 0
                direction = "LONG" if is_long else "SHORT"
                entry_price = float(pos.get("entryPrice", pos.get("avgEntryPrice", 0.0)) or 0.0)

                candles_15m = []
                if hasattr(self.exchange, "get_klines"):
                    try:
                        candles_15m = self.exchange.get_klines(sym, timeframe="15m", limit=30) or []
                    except Exception as err:
                        logger.warning("Could not fetch 15m klines for invalidation check on %s: %s", sym, err)

                # Look up stop loss / sweep level in journal
                stop_loss = entry_price * (0.9875 if is_long else 1.0125)
                sweep_wick = None
                if self.db:
                    try:
                        with self.db.get_connection() as conn:
                            row = conn.execute(
                                """SELECT planned_stop_price FROM automated_trade_journal
                                   WHERE symbol = ? AND mode = 'LIVE' AND status = 'OPEN'
                                   ORDER BY created_at_utc DESC LIMIT 1""",
                                (sym,),
                            ).fetchone()
                            if row and row[0]:
                                stop_loss = float(row[0])
                    except Exception:
                        pass

                alert_info = self.check_structural_invalidation(
                    symbol=sym,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss_price=stop_loss,
                    candles_15m=candles_15m,
                    sweep_wick_extreme=sweep_wick,
                )
                self.last_structural_invalidation_status[sym] = alert_info
                alerts.append(alert_info)
                if alert_info["status"] == "CONFIRMED_STRUCTURAL_BREAKDOWN":
                    logger.warning("STRUCTURAL BREAKDOWN DETECTED for %s %s: %s (Diagnostic only - live position untouched)", sym, direction, alert_info["reason"])

        except Exception as e:
            logger.error("Error in monitor_structural_invalidation: %s", e)

        return alerts

    def run_5m_scan_cycle(
        self,
        universe_symbols: List[str],
        market_snapshots: Dict[str, Dict[str, Any]],
        cycle_id: Optional[str] = None,
    ) -> ScannerAttritionTracker:
        """Execute the configured live strategy through the 11-stage attrition pipeline."""
        tracker = ScannerAttritionTracker(cycle_id=cycle_id)

        # Stage 1: Universe
        total_universe = len(universe_symbols)
        tracker.record_stage("Universe", passed_count=total_universe)

        # Stage 2: Market data (staleness check)
        passed_market_data = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        for sym in universe_symbols:
            snap = market_snapshots.get(sym)
            if not snap or "candles_5m" not in snap or not snap["candles_5m"]:
                tracker.log_symbol_rejection(sym, "Market data", "Missing 5m candle history")
                continue
            last_ts = snap["candles_5m"][-1].timestamp_ms if hasattr(snap["candles_5m"][-1], "timestamp_ms") else snap["candles_5m"][-1].get("timestamp_ms", 0)
            # Rejection if candle is older than 15 minutes (900,000 ms)
            if last_ts > 0 and (now_ms - last_ts > 900000):
                tracker.log_symbol_rejection(sym, "Market data", "Stale candle data (>15m)")
                continue
            passed_market_data.append(sym)
        tracker.record_stage("Market data", passed_count=len(passed_market_data))

        # Stage 3: Spread filter (max 10 bps default)
        passed_spread = []
        for sym in passed_market_data:
            snap = market_snapshots[sym]
            spread_bps = snap.get("spread_bps", 0.0)
            if spread_bps > 10.0:
                tracker.log_symbol_rejection(sym, "Spread", f"Spread {spread_bps:.1f} bps > 10 bps limit")
                continue
            passed_spread.append(sym)
        tracker.record_stage("Spread", passed_count=len(passed_spread))

        # Stage 4: Liquidity / Turnover filter
        passed_liquidity = []
        for sym in passed_spread:
            snap = market_snapshots[sym]
            turnover_24h = snap.get("turnover_24h_usdt", 1_000_000.0)
            if turnover_24h < 100_000.0:
                tracker.log_symbol_rejection(sym, "Liquidity", f"24h turnover ${turnover_24h:,.0f} < $100k")
                continue
            passed_liquidity.append(sym)
        tracker.record_stage("Liquidity", passed_count=len(passed_liquidity))

        # Stage 5: Volatility bounds
        passed_volatility = []
        for sym in passed_liquidity:
            snap = market_snapshots[sym]
            atr_pct = snap.get("atr_pct", 1.0)
            if atr_pct < 0.10:
                tracker.log_symbol_rejection(sym, "Volatility", f"ATR {atr_pct:.2f}% too compressed (<0.10%)")
                continue
            if atr_pct > 10.0:
                tracker.log_symbol_rejection(sym, "Volatility", f"ATR {atr_pct:.2f}% too extreme (>10.0%)")
                continue
            passed_volatility.append(sym)
        tracker.record_stage("Volatility", passed_count=len(passed_volatility))

        # Stage 6: Candidate ranking
        passed_ranking = list(passed_volatility)
        tracker.record_stage("Candidate ranking", passed_count=len(passed_ranking))

        # Stage 7: Strategy trigger. Strictly requires 15M_SWEEP_RECLAIM_SMC.
        active_strat_id = getattr(getattr(self.config, "strategy", None), "live_strategy_id", None)
        if active_strat_id == "EMA20_RECLAIM_5M_V1":
            raise PermissionError("CRITICAL SECURITY INVARIANT: EMA20_RECLAIM_5M_V1 is strictly prohibited from live execution. Only 15M_SWEEP_RECLAIM_SMC is authorized.")
        if active_strat_id != "15M_SWEEP_RECLAIM_SMC":
            raise ValueError(f"Unsupported configured live strategy: {active_strat_id}")
        triggered_candidates: List[Tuple[str, Any]] = []
        for sym in passed_ranking:
            snap = market_snapshots[sym]
            step_size = snap.get("step_size", 0.01)
            min_notional = snap.get("min_notional", 5.0)

            if active_strat_id == "15M_SWEEP_RECLAIM_SMC":
                candles_15m = snap.get("candles_15m") or snap.get("candles_5m") or []
                setup, reason = self.sweep_15m_strategy.evaluate(
                    symbol=sym,
                    candles_15m=candles_15m,
                    tick_size=snap.get("tick_size", 0.01),
                    step_size=step_size,
                    min_notional=min_notional,
                    account_equity=self.current_equity,
                    dealing_range_position_pct=snap.get("dealing_range_position_pct", 50.0),
                )
            else:
                setup, reason = self.ema_strategy.evaluate(
                    symbol=sym,
                    candles_5m=snap.get("candles_5m", []),
                    step_size=step_size,
                    min_notional=min_notional,
                    account_equity=self.current_equity,
                )
            if not setup:
                if "STOP_DISTANCE" in reason:
                    tracker.log_symbol_rejection(sym, "Cost gate", reason)
                elif "NET_RR" in reason:
                    tracker.log_symbol_rejection(sym, "Cost gate", reason)
                elif "MARGIN" in reason or "SIZING" in reason:
                    tracker.log_symbol_rejection(sym, "Risk gate", reason)
                else:
                    tracker.log_symbol_rejection(sym, "Strategy trigger", reason)
                continue
            triggered_candidates.append((sym, setup))
        tracker.record_stage("Strategy trigger", passed_count=len(triggered_candidates))

        # Stage 8: Risk gate
        passed_risk_gate: List[Tuple[str, Any]] = []
        risk_ok, risk_reason = self.check_risk_limits()
        if not risk_ok:
            for sym, _ in triggered_candidates:
                tracker.log_symbol_rejection(sym, "Risk gate", risk_reason)
            tracker.record_stage("Risk gate", passed_count=0)
        else:
            for sym, setup in triggered_candidates:
                passed_risk_gate.append((sym, setup))
            tracker.record_stage("Risk gate", passed_count=len(passed_risk_gate))

        # Stage 9: Cost gate (already verified inside evaluate, but verified here for stage count)
        passed_cost_gate: List[Tuple[str, EMA20ReclaimSetup]] = []
        for sym, setup in passed_risk_gate:
            if setup.sizing and setup.sizing.expected_net_rr >= self.config.costs.net_reward_to_risk_min:
                passed_cost_gate.append((sym, setup))
            else:
                tracker.log_symbol_rejection(sym, "Cost gate", f"Expected Net-RR {setup.sizing.expected_net_rr if setup.sizing else 0.0:.2f} < 1.50")
        tracker.record_stage("Cost gate", passed_count=len(passed_cost_gate))

        # Stage 10: Global lock (Max 1 concurrent position)
        passed_global_lock: List[Tuple[str, EMA20ReclaimSetup]] = []
        for sym, setup in passed_cost_gate:
            can_open, lock_reason = self.global_lock.can_open_position(sym, setup.strategy_id)
            if not can_open:
                tracker.log_symbol_rejection(sym, "Global lock", lock_reason)
                continue
            passed_global_lock.append((sym, setup))
        tracker.record_stage("Global lock", passed_count=len(passed_global_lock))

        # Stage 11: Executor
        executable_signals: List[Dict[str, Any]] = []
        for sym, setup in passed_global_lock:
            # Check Scar-Tissue Feedback Gates (Revenge Lock, Stop Escalation, Streak Cooldown)
            try:
                from src.ai.trade_memory_feedback import TradeMemoryFeedbackEngine
                feedback_eng = TradeMemoryFeedbackEngine(journal=getattr(self, "journal", None), db=getattr(self, "db", None))
                mem_gate = feedback_eng.check_pre_trade_memory_gates(
                    symbol=sym,
                    side=setup.direction.value if hasattr(setup.direction, "value") else str(setup.direction),
                    entry_price=setup.entry_price,
                    planned_stop_pct=setup.stop_distance_pct,
                )
                if not mem_gate.passed:
                    tracker.log_symbol_rejection(sym, "Executor", f"Scar-Tissue Gate: {mem_gate.rejection_reason}")
                    continue
            except Exception as gate_err:
                logger.warning(f"Note checking memory feedback gate: {gate_err}")

            # Check Hard Dashboard Confluence Gates (Sweep + Order Flow + Anti-Repeat Memory + Breathing Room)
            try:
                from src.intelligence.dashboard_confluence import evaluate_dashboard_confluence_gates, build_web_intelligence_payload
                from src.intelligence.coin_inspector import CoinInspectorService
                inspector = CoinInspectorService(adapter=self.exchange)
                insp_snap = inspector.inspect_coin(sym)
                c_intel = build_web_intelligence_payload(
                    symbol=sym,
                    inspector_snapshot=insp_snap,
                    recent_closed_trades=feedback_eng.get_recent_live_trades(limit=10, mode="LIVE", closed_only=True),
                )
                conf_res = evaluate_dashboard_confluence_gates(
                    symbol=sym,
                    direction=setup.direction.value if hasattr(setup.direction, "value") else str(setup.direction),
                    entry_price=setup.entry_price,
                    stop_loss_price=setup.stop_loss,
                    intelligence=c_intel,
                )
                if not conf_res.passed:
                    tracker.log_symbol_rejection(sym, "Executor", f"Dashboard Confluence Gate: {conf_res.rejection_reason}")
                    continue
            except Exception as conf_err:
                logger.warning(f"Note checking dashboard confluence gate: {conf_err}")

            # Check duplicate signals
            is_dup, dup_reason = self.global_lock.check_duplicate_signal(sym, setup.strategy_id, setup.direction.value)
            if is_dup:
                tracker.log_symbol_rejection(sym, "Executor", dup_reason)
                continue

            order_id = f"LIVE_{setup.strategy_id}_{sym}_{int(datetime.now(timezone.utc).timestamp()*1000)}"
            lock_success, lock_msg = self.global_lock.acquire(
                strategy_id=setup.strategy_id,
                symbol=sym,
                order_id=order_id,
                state="PENDING_ENTRY",
            )
            if not lock_success:
                tracker.log_symbol_rejection(sym, "Executor", f"Failed to acquire global lock: {lock_msg}")
                continue

            executable_signals.append({
                "setup": setup,
                "order_id": order_id,
                "status": "PENDING",
            })

        tracker.record_stage("Executor", passed_count=len(executable_signals))
        tracker.finalize()

        # Persist report if database available
        if self.db:
            try:
                tracker.persist_to_db(self.db)
            except Exception as e:
                logger.error("Failed to persist scan attrition report: %s", e)

        return tracker

    def run_1m_shadow_cycle(
        self,
        universe_symbols: List[str],
        market_snapshots: Dict[str, Dict[str, Any]],
    ) -> List[Sweep1MSetup]:
        """Execute 1m Sweep & Reclaim (SWEEP_1M_V1) for SHADOW counterfactual evidence.
        
        CRITICAL: Never submits live orders; always records counterfactual outcomes.
        """
        assert self.sweep_strategy.is_live is False, "SWEEP_1M_V1 must NEVER submit live orders"

        detected_setups: List[Sweep1MSetup] = []
        for sym in universe_symbols:
            snap = market_snapshots.get(sym)
            if not snap or "candles_1m" not in snap or not snap["candles_1m"]:
                continue

            step_size = snap.get("step_size", 0.01)
            min_notional = snap.get("min_notional", 5.0)

            setup, reason = self.sweep_strategy.evaluate(
                symbol=sym,
                candles_1m=snap["candles_1m"],
                step_size=step_size,
                min_notional=min_notional,
                account_equity=self.current_equity,
            )
            if not setup:
                continue

            # Check if it would have been blocked by the global position lock
            would_block = self.global_lock.would_block_counterfactual(setup.strategy_id, setup.symbol)
            setup.blocked_by_global_lock = would_block

            # Evaluate forward path if future bars are available
            future_bars = snap.get("future_candles_1m", [])
            if future_bars:
                self.sweep_strategy.evaluate_counterfactual_outcome(setup, future_bars)

            # Persist in shadow tracker
            if self.shadow_tracker:
                self.shadow_tracker.record_shadow_setup(
                    setup=setup,
                    market_regime=snap.get("regime", "UNKNOWN"),
                )

            detected_setups.append(setup)

        return detected_setups
