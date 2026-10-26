"""Closed-Loop Trade Memory and Self-Learning Feedback Engine.

Connects exchange-reconciled history from `automated_trade_journal` directly into:
1. Deterministic Python pre-trade scar-tissue guardrails (revenge locks, stop escalation, streak circuit breaker).
2. Qwen/Groq LLM prompt context as structured episodic memory with mandatory self-correction directives.
3. Automated quantitative autopsy tagging on trade closure.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("src.ai.trade_memory_feedback")


def parse_utc_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp strings into UTC-aware datetime."""
    if not ts:
        return None
    try:
        cleaned = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


@dataclass
class PreTradeMemoryGateResult:
    """Evaluation result for deterministic scar-tissue pre-trade guardrails."""
    passed: bool
    rejection_reason: str
    rule_triggered: Optional[str] = None  # REVENGE_LOCK, SCAR_TISSUE_STOP_TOO_TIGHT, CONSECUTIVE_LOSS_ESCALATION, STREAK_CIRCUIT_BREAKER, LLM_SCORE_TOO_LOW
    cooling_off_until: Optional[str] = None
    min_stop_floor_pct: float = 1.15
    required_score_threshold: float = 85.0
    consecutive_losses: int = 0


@dataclass
class PerformanceMemorySummary:
    """Summary of recent live trading performance and common failure modes."""
    total_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    net_pnl_usd: float
    consecutive_losses: int
    loss_streak_active: bool
    recent_trades_log: List[Dict[str, Any]] = field(default_factory=list)
    top_failure_modes: List[str] = field(default_factory=list)
    cooling_off_active: bool = False
    cooling_off_until: Optional[str] = None
    min_stop_floor_pct: float = 1.15
    required_score_threshold: float = 85.0


def generate_post_loss_autopsy_tags(trade: Any, recent_trades: Optional[List[Any]] = None) -> List[str]:
    """Generate concise quantitative autopsy tags for a losing trade."""
    tags = []

    planned_stop_pct = getattr(trade, "planned_stop_pct", None)
    if planned_stop_pct is None and isinstance(trade, dict):
        planned_stop_pct = trade.get("planned_stop_pct")
    if planned_stop_pct is not None:
        try:
            p_stop = float(planned_stop_pct)
            if p_stop < 0.80:
                tags.append("TIGHT_STOP_<0.80PCT")
            elif p_stop < 1.00:
                tags.append("TIGHT_STOP_<1PCT")
            elif p_stop < 1.15:
                tags.append("TIGHT_STOP_<1.15PCT")
        except (ValueError, TypeError):
            pass

    entry_fill_type = getattr(trade, "entry_fill_type", "")
    if not entry_fill_type and isinstance(trade, dict):
        entry_fill_type = trade.get("entry_fill_type", "")
    if str(entry_fill_type).upper() == "TAKER":
        tags.append("TAKER_ENTRY_LOSS")

    holding_dur = getattr(trade, "holding_duration_seconds", None)
    if holding_dur is None and isinstance(trade, dict):
        holding_dur = trade.get("holding_duration_seconds")
    if holding_dur is not None:
        try:
            if 0 < float(holding_dur) < 600:
                tags.append("PREMATURE_STOP_OUT (<10m)")
        except (ValueError, TypeError):
            pass

    strategy_id = getattr(trade, "strategy_id", "")
    if not strategy_id and isinstance(trade, dict):
        strategy_id = trade.get("strategy_id", "")
    if str(strategy_id).upper() in ("EMA20_RECLAIM_5M_V1", "SWEEP_1M_V1"):
        tags.append("CHOP_CHASING_5M_EMA")

    # Check for repeat symbol loss within recent trades
    sym = getattr(trade, "symbol", "") or (trade.get("symbol", "") if isinstance(trade, dict) else "")
    trade_id = getattr(trade, "trade_id", "") or (trade.get("trade_id", "") if isinstance(trade, dict) else "")
    if sym and recent_trades:
        prev_trades_on_sym = [
            t for t in recent_trades
            if (getattr(t, "symbol", "") or (t.get("symbol", "") if isinstance(t, dict) else "")) == sym
            and (getattr(t, "trade_id", "") or (t.get("trade_id", "") if isinstance(t, dict) else "")) != trade_id
        ]
        if prev_trades_on_sym:
            tags.append("REPEAT_SYMBOL_LOSS")

    realized_r = getattr(trade, "realized_r", None)
    if realized_r is None and isinstance(trade, dict):
        realized_r = trade.get("realized_r")
    if realized_r is not None:
        try:
            if float(realized_r) <= -0.75:
                tags.append("FULL_R_LOSS")
        except (ValueError, TypeError):
            pass

    if not tags:
        tags.append("STRUCTURAL_STOP_HIT")

    return tags


class TradeMemoryFeedbackEngine:
    """Queries automated_trade_journal, enforces scar-tissue guardrails, and provides LLM memory."""

    def __init__(self, journal: Optional[Any] = None, db: Optional[Any] = None, db_path: Optional[str] = None) -> None:
        self.journal = journal
        self.db = db
        self.db_path = db_path
        if not self.journal and not self.db and not self.db_path:
            try:
                from config.settings import get_default_config
                from src.data.database import DatabaseManager
                cfg = get_default_config()
                self.db = DatabaseManager(cfg.db_path)
            except Exception:
                pass

    def get_recent_live_trades(
        self,
        limit: int = 10,
        mode: str = "LIVE",
        closed_only: bool = True,
    ) -> List[Any]:
        """Fetch the most recent closed trades from automated_trade_journal."""
        # 1. Use journal instance if available
        if self.journal and hasattr(self.journal, "get_trades"):
            try:
                from src.journal.models import JournalFilter
                filt = JournalFilter(mode=mode, closed_only=closed_only, limit=limit)
                return self.journal.get_trades(filt)
            except Exception as e:
                logger.warning(f"Error querying journal via get_trades: {e}")

        # 2. Direct database query
        try:
            if self.db and hasattr(self.db, "get_connection"):
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    conds = []
                    params: List[Any] = []
                    if mode and mode.upper() != "ALL":
                        conds.append("mode = ?")
                        params.append(mode.upper())
                    if closed_only:
                        conds.append("status = 'CLOSED'")
                    where = f"WHERE {' AND '.join(conds)}" if conds else ""
                    sql = f"SELECT * FROM automated_trade_journal {where} ORDER BY created_at_utc DESC LIMIT ?;"
                    params.append(limit)
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    return [dict(r) for r in rows]
            elif self.journal and hasattr(self.journal, "db") and hasattr(self.journal.db, "get_connection"):
                with self.journal.db.get_connection() as conn:
                    cur = conn.cursor()
                    conds = []
                    params: List[Any] = []
                    if mode and mode.upper() != "ALL":
                        conds.append("mode = ?")
                        params.append(mode.upper())
                    if closed_only:
                        conds.append("status = 'CLOSED'")
                    where = f"WHERE {' AND '.join(conds)}" if conds else ""
                    sql = f"SELECT * FROM automated_trade_journal {where} ORDER BY created_at_utc DESC LIMIT ?;"
                    params.append(limit)
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    return [dict(r) for r in rows]
            elif self.db_path:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    conds = []
                    params: List[Any] = []
                    if mode and mode.upper() != "ALL":
                        conds.append("mode = ?")
                        params.append(mode.upper())
                    if closed_only:
                        conds.append("status = 'CLOSED'")
                    where = f"WHERE {' AND '.join(conds)}" if conds else ""
                    sql = f"SELECT * FROM automated_trade_journal {where} ORDER BY created_at_utc DESC LIMIT ?;"
                    params.append(limit)
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"Error querying automated_trade_journal: {e}")

        return []

    def analyze_recent_performance(
        self,
        trades: Optional[List[Any]] = None,
        current_time: Optional[datetime] = None,
    ) -> PerformanceMemorySummary:
        """Calculate rolling win rate, loss streak, failure modes, and cooldown status."""
        now = current_time or datetime.now(timezone.utc)
        if trades is None:
            trades = self.get_recent_live_trades(limit=10, mode="LIVE", closed_only=True)

        if not trades:
            return PerformanceMemorySummary(
                total_trades=0,
                wins=0,
                losses=0,
                win_rate_pct=0.0,
                net_pnl_usd=0.0,
                consecutive_losses=0,
                loss_streak_active=False,
                recent_trades_log=[],
                top_failure_modes=[],
                cooling_off_active=False,
                cooling_off_until=None,
                min_stop_floor_pct=1.15,
                required_score_threshold=85.0,
            )

        wins = 0
        losses = 0
        net_pnl = 0.0
        failure_mode_counts: Dict[str, int] = {}
        recent_log = []

        consecutive_losses = 0
        streak_broken = False

        for t in trades:
            pnl = float(getattr(t, "net_pnl_usd", 0.0) or (t.get("net_pnl_usd", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
            exit_reason = str(getattr(t, "exit_reason", "") or (t.get("exit_reason", "") if isinstance(t, dict) else "")).upper()
            sym = str(getattr(t, "symbol", "") or (t.get("symbol", "") if isinstance(t, dict) else ""))
            side = str(getattr(t, "side", "") or (t.get("side", "") if isinstance(t, dict) else ""))
            strat = str(getattr(t, "strategy_id", "") or (t.get("strategy_id", "") if isinstance(t, dict) else ""))
            entry_p = float(getattr(t, "average_entry_price", 0.0) or (t.get("average_entry_price", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
            exit_p = float(getattr(t, "average_exit_price", 0.0) or (t.get("average_exit_price", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
            stop_pct = float(getattr(t, "planned_stop_pct", 0.0) or (t.get("planned_stop_pct", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
            real_r = float(getattr(t, "realized_r", 0.0) or (t.get("realized_r", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
            created_utc = str(getattr(t, "created_at_utc", "") or (t.get("created_at_utc", "") if isinstance(t, dict) else ""))
            exit_utc = str(getattr(t, "exit_timestamp_utc", "") or (t.get("exit_timestamp_utc", "") if isinstance(t, dict) else "")) or created_utc
            llm_reason = str(getattr(t, "llm_reason", "") or (t.get("llm_reason", "") if isinstance(t, dict) else ""))

            net_pnl += pnl
            is_loss = (pnl < -1e-6) or ("STOP_LOSS" in exit_reason)
            is_win = (pnl > 1e-6) or ("TAKE_PROFIT" in exit_reason)

            if is_win:
                wins += 1
                streak_broken = True
            elif is_loss:
                losses += 1
                if not streak_broken:
                    consecutive_losses += 1

                # Gather autopsy tags
                tags = generate_post_loss_autopsy_tags(t, recent_trades=trades)
                for tag in tags:
                    failure_mode_counts[tag] = failure_mode_counts.get(tag, 0) + 1
            else:
                streak_broken = True

            recent_log.append({
                "timestamp": exit_utc[:19].replace("T", " ") if exit_utc else "RECENT",
                "symbol": sym,
                "side": side,
                "strategy_id": strat,
                "entry_price": entry_p,
                "exit_price": exit_p,
                "planned_stop_pct": stop_pct,
                "realized_r": real_r,
                "exit_reason": exit_reason or ("STOP_LOSS" if is_loss else "WIN"),
                "llm_reason": llm_reason,
                "autopsy_tags": generate_post_loss_autopsy_tags(t, recent_trades=trades) if is_loss else [],
            })

        total = len(trades)
        win_rate = (wins / total * 100.0) if total > 0 else 0.0

        # Sort failure modes by frequency
        sorted_modes = sorted(failure_mode_counts.items(), key=lambda x: x[1], reverse=True)
        top_modes = [f"{mode} ({count}/{losses})" for mode, count in sorted_modes[:4]]

        # Streak Circuit Breaker Check (Cooldown):
        # If the last 2 live trades within a 90-minute window ended in STOP_LOSS -> 60m cooling off
        cooling_off = False
        cooling_off_until = None
        if len(trades) >= 2:
            t0 = trades[0]
            t1 = trades[1]
            t0_reason = str(getattr(t0, "exit_reason", "") or (t0.get("exit_reason", "") if isinstance(t0, dict) else "")).upper()
            t1_reason = str(getattr(t1, "exit_reason", "") or (t1.get("exit_reason", "") if isinstance(t1, dict) else "")).upper()
            t0_pnl = float(getattr(t0, "net_pnl_usd", 0.0) or (t0.get("net_pnl_usd", 0.0) if isinstance(t0, dict) else 0.0) or 0.0)
            t1_pnl = float(getattr(t1, "net_pnl_usd", 0.0) or (t1.get("net_pnl_usd", 0.0) if isinstance(t1, dict) else 0.0) or 0.0)

            t0_is_loss = (t0_pnl < -1e-6) or ("STOP_LOSS" in t0_reason)
            t1_is_loss = (t1_pnl < -1e-6) or ("STOP_LOSS" in t1_reason)

            if t0_is_loss and t1_is_loss:
                t0_dt = parse_utc_timestamp(getattr(t0, "exit_timestamp_utc", None) or (t0.get("exit_timestamp_utc") if isinstance(t0, dict) else None))
                t1_dt = parse_utc_timestamp(getattr(t1, "exit_timestamp_utc", None) or (t1.get("exit_timestamp_utc") if isinstance(t1, dict) else None))
                if t0_dt and t1_dt:
                    # Check if the two losses occurred within 90 minutes of each other
                    diff_seconds = abs((t0_dt - t1_dt).total_seconds())
                    if diff_seconds <= 90 * 60:
                        cooldown_end = t0_dt + timedelta(minutes=60)
                        if now < cooldown_end:
                            cooling_off = True
                            cooling_off_until = cooldown_end.isoformat()

        # Adaptive Escalation Floors
        min_stop_floor = 1.25 if consecutive_losses >= 2 else 1.15
        score_thresh = 90.0 if consecutive_losses >= 2 else 85.0

        return PerformanceMemorySummary(
            total_trades=total,
            wins=wins,
            losses=losses,
            win_rate_pct=round(win_rate, 1),
            net_pnl_usd=round(net_pnl, 4),
            consecutive_losses=consecutive_losses,
            loss_streak_active=(consecutive_losses >= 2),
            recent_trades_log=recent_log[:6],
            top_failure_modes=top_modes,
            cooling_off_active=cooling_off,
            cooling_off_until=cooling_off_until,
            min_stop_floor_pct=min_stop_floor,
            required_score_threshold=score_thresh,
        )

    def check_pre_trade_memory_gates(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        planned_stop_pct: float,
        llm_score: Optional[float] = None,
        current_time: Optional[datetime] = None,
        trades: Optional[List[Any]] = None,
    ) -> PreTradeMemoryGateResult:
        """Deterministic Python scar-tissue guardrail checking revenge lock, stop floor, and streak cooldown."""
        now = current_time or datetime.now(timezone.utc)
        clean_sym = symbol.strip().upper().replace(".P", "")
        clean_side = side.strip().upper()

        if trades is None:
            trades = self.get_recent_live_trades(limit=10, mode="LIVE", closed_only=True)

        summary = self.analyze_recent_performance(trades=trades, current_time=now)

        # 1. Streak Circuit Breaker Check (Cooldown)
        if summary.cooling_off_active and summary.cooling_off_until:
            return PreTradeMemoryGateResult(
                passed=False,
                rule_triggered="STREAK_CIRCUIT_BREAKER",
                rejection_reason=(
                    f"STREAK_CIRCUIT_BREAKER_ACTIVE: 2 consecutive losses within 90m. "
                    f"Cooling off until {summary.cooling_off_until}. Live orders paused; route to SHADOW."
                ),
                cooling_off_until=summary.cooling_off_until,
                consecutive_losses=summary.consecutive_losses,
            )

        # 2. Same-Symbol & Same-Direction Revenge Lock (120 Minutes + Deeper Price Requirement)
        # Find the most recent trade matching (clean_sym, clean_side)
        for t in trades:
            t_sym = str(getattr(t, "symbol", "") or (t.get("symbol", "") if isinstance(t, dict) else "")).strip().upper().replace(".P", "")
            t_side = str(getattr(t, "side", "") or (t.get("side", "") if isinstance(t, dict) else "")).strip().upper()

            if t_sym == clean_sym and t_side == clean_side:
                t_reason = str(getattr(t, "exit_reason", "") or (t.get("exit_reason", "") if isinstance(t, dict) else "")).upper()
                t_pnl = float(getattr(t, "net_pnl_usd", 0.0) or (t.get("net_pnl_usd", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
                is_loss = (t_pnl < -1e-6) or ("STOP_LOSS" in t_reason)

                if is_loss:
                    t_exit_dt = parse_utc_timestamp(getattr(t, "exit_timestamp_utc", None) or (t.get("exit_timestamp_utc") if isinstance(t, dict) else None))
                    if t_exit_dt:
                        elapsed_seconds = (now - t_exit_dt).total_seconds()
                        if 0 <= elapsed_seconds <= 120 * 60:
                            elapsed_min = elapsed_seconds / 60.0
                            prev_exit_p = float(getattr(t, "average_exit_price", 0.0) or (t.get("average_exit_price", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
                            if prev_exit_p <= 0:
                                prev_exit_p = float(getattr(t, "planned_stop_price", 0.0) or (t.get("planned_stop_price", 0.0) if isinstance(t, dict) else 0.0) or 0.0)

                            if prev_exit_p > 0:
                                if clean_side == "LONG":
                                    # For LONG: Current entry must be at least 1.20% lower (cheaper) than previous SL exit
                                    max_allowed_entry = prev_exit_p * (1.0 - 0.012)
                                    if entry_price > max_allowed_entry:
                                        pct_diff = ((entry_price - prev_exit_p) / prev_exit_p) * 100.0
                                        return PreTradeMemoryGateResult(
                                            passed=False,
                                            rule_triggered="REVENGE_LOCK",
                                            rejection_reason=(
                                                f"REVENGE_LOCK_ACTIVE: {clean_sym} {clean_side} stopped out {elapsed_min:.1f}m ago at {prev_exit_p:.4f}. "
                                                f"Entry {entry_price:.4f} is not >= 1.20% deeper than previous SL exit price "
                                                f"(max allowed entry: {max_allowed_entry:.4f}, diff: {pct_diff:+.2f}%)."
                                            ),
                                            min_stop_floor_pct=summary.min_stop_floor_pct,
                                            required_score_threshold=summary.required_score_threshold,
                                            consecutive_losses=summary.consecutive_losses,
                                        )
                                else:  # SHORT
                                    # For SHORT: Current entry must be at least 1.20% higher than previous SL exit
                                    min_allowed_entry = prev_exit_p * (1.0 + 0.012)
                                    if entry_price < min_allowed_entry:
                                        pct_diff = ((entry_price - prev_exit_p) / prev_exit_p) * 100.0
                                        return PreTradeMemoryGateResult(
                                            passed=False,
                                            rule_triggered="REVENGE_LOCK",
                                            rejection_reason=(
                                                f"REVENGE_LOCK_ACTIVE: {clean_sym} {clean_side} stopped out {elapsed_min:.1f}m ago at {prev_exit_p:.4f}. "
                                                f"Entry {entry_price:.4f} is not >= 1.20% higher than previous SL exit price "
                                                f"(min allowed entry: {min_allowed_entry:.4f}, diff: {pct_diff:+.2f}%)."
                                            ),
                                            min_stop_floor_pct=summary.min_stop_floor_pct,
                                            required_score_threshold=summary.required_score_threshold,
                                            consecutive_losses=summary.consecutive_losses,
                                        )
                break  # Only inspect the most recent trade on this (symbol, side)

        # 3. Adaptive Stop-Loss Floor Escalation
        # Absolute hard floor
        if planned_stop_pct < 1.15:
            return PreTradeMemoryGateResult(
                passed=False,
                rule_triggered="SCAR_TISSUE_STOP_TOO_TIGHT",
                rejection_reason=(
                    f"SCAR_TISSUE_STOP_TOO_TIGHT: Planned stop distance {planned_stop_pct:.2f}% is strictly "
                    f"below the minimum safety threshold of 1.15%."
                ),
                min_stop_floor_pct=summary.min_stop_floor_pct,
                required_score_threshold=summary.required_score_threshold,
                consecutive_losses=summary.consecutive_losses,
            )

        # Consecutive loss escalation
        if summary.consecutive_losses >= 2:
            if planned_stop_pct < 1.25:
                return PreTradeMemoryGateResult(
                    passed=False,
                    rule_triggered="CONSECUTIVE_LOSS_ESCALATION",
                    rejection_reason=(
                        f"SCAR_TISSUE_CONSECUTIVE_LOSS_ESCALATION: Account has {summary.consecutive_losses} consecutive losses; "
                        f"required structural stop floor escalated to 1.25% (got {planned_stop_pct:.2f}%)."
                    ),
                    min_stop_floor_pct=1.25,
                    required_score_threshold=summary.required_score_threshold,
                    consecutive_losses=summary.consecutive_losses,
                )

            if llm_score is not None and llm_score < summary.required_score_threshold:
                return PreTradeMemoryGateResult(
                    passed=False,
                    rule_triggered="LLM_SCORE_TOO_LOW",
                    rejection_reason=(
                        f"SCAR_TISSUE_CONFIDENCE_ESCALATION: Account has {summary.consecutive_losses} consecutive losses; "
                        f"required LLM selection score escalated to {summary.required_score_threshold:.1f}/100 (got {llm_score:.1f})."
                    ),
                    min_stop_floor_pct=summary.min_stop_floor_pct,
                    required_score_threshold=summary.required_score_threshold,
                    consecutive_losses=summary.consecutive_losses,
                )

        return PreTradeMemoryGateResult(
            passed=True,
            rejection_reason="PASSED",
            rule_triggered=None,
            min_stop_floor_pct=summary.min_stop_floor_pct,
            required_score_threshold=summary.required_score_threshold,
            consecutive_losses=summary.consecutive_losses,
        )

    def build_episodic_memory_prompt_block(
        self,
        limit: int = 8,
        current_time: Optional[datetime] = None,
        trades: Optional[List[Any]] = None,
    ) -> str:
        """Build structured episodic trade memory context to inject directly into Qwen's prompt."""
        if trades is None:
            trades = self.get_recent_live_trades(limit=limit, mode="LIVE", closed_only=True)

        summary = self.analyze_recent_performance(trades=trades, current_time=current_time)

        lines = [
            "=== RECENT_LIVE_PERFORMANCE_AND_MISTAKES (EPISODIC TRADE MEMORY) ===",
            f"Rolling Last-10 Live Record: Win Rate {summary.win_rate_pct:.1f}% ({summary.wins}W / {summary.losses}L) | Net Realized PnL: ${summary.net_pnl_usd:+.2f} USD | Current Streak: {summary.consecutive_losses} CONSECUTIVE LOSSES",
        ]

        if summary.top_failure_modes:
            lines.append(f"Dominant Failure Modes: {', '.join(summary.top_failure_modes)}")
        else:
            lines.append("Dominant Failure Modes: TIGHT_STOPS (<0.80%), CHOP_CHASING_5M_EMA")

        if summary.cooling_off_active and summary.cooling_off_until:
            lines.append(f"CIRCUIT BREAKER: Cooling off until {summary.cooling_off_until} due to 2 losses in 90m window.")

        lines.append("\nRecent Closed Trade Log (Last 6):")
        if not summary.recent_trades_log:
            lines.append("- (No recent closed live trades recorded)")
        else:
            for idx, tr in enumerate(summary.recent_trades_log, start=1):
                sym = tr.get("symbol", "COIN")
                side = tr.get("side", "LONG")
                strat = tr.get("strategy_id", "EMA20")
                entry = tr.get("entry_price", 0.0)
                exit_p = tr.get("exit_price", 0.0)
                stop_pct = tr.get("planned_stop_pct", 0.0)
                real_r = tr.get("realized_r", 0.0)
                reason = tr.get("exit_reason", "STOP_LOSS")
                tags = ", ".join(tr.get("autopsy_tags", [])) or "NONE"
                ts = tr.get("timestamp", "")
                lines.append(
                    f"{idx}. [{ts}] {sym} {side} ({strat}) | Entry: {entry:.4f} | Exit: {exit_p:.4f} | Stop: {stop_pct:.2f}% | R: {real_r:+.2f}R | Result: {reason} | Autopsy: {tags}"
                )

        lines.append("==================================================================")
        return "\n".join(lines)
