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
from src.risk.live_micro_sizing import LiveMicroSizingEngine, LiveMicroSizingResult
from src.scheduler.attrition_tracker import ScannerAttritionTracker
from src.strategy.ema20_reclaim_5m import EMA20ReclaimSetup, EMA20ReclaimStrategy
from src.strategy.sweep_1m import Sweep1MSetup, Sweep1MStrategy
from src.strategy.sweep_reclaim_15m import SweepReclaim15MSetup, SweepReclaim15MStrategy

logger = logging.getLogger("src.scheduler.live_micro_runner")

WIB_TIMEZONE = timezone(timedelta(hours=7))

def get_daily_reset_timezone(tz_name: str = "WIB") -> timezone:
    tz_clean = str(tz_name or "WIB").strip().upper()
    if tz_clean in ("UTC", "GMT", "+00:00", "Z"):
        return timezone.utc
    return WIB_TIMEZONE



class LiveMicroCoordinator:
    """Master coordinator for LIVE_MICRO_V1 execution and shadow tracking."""

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

    def check_risk_limits(self) -> Tuple[bool, str]:
        """Check all overarching risk limits before considering trade execution."""
        self._reset_daily_metrics_if_new_day()

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
