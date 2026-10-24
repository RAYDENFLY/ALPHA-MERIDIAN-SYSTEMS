"""Independent Risk Management and Circuit Breakers for Model B.

Enforces:
- Target trade risk: $1.00 USDT.
- Daily realized loss cap: $3.00 USDT (3R stop-out breaker).
- Weekly realized loss cap: $7.50 USDT (7.5R stop-out breaker).
- Maximum simultaneous open positions: 3.
- Capital baseline: $100.00 USDT research equity.
- Complete ledger isolation from Model A.
- Hard safety invariant: live_trading_locked = True.

Temporary paper-only diagnostics (default OFF, Model B scope only):
- diagnostic_breaker_override: the daily/weekly loss breakers (and the remaining-risk-budget check
  derived from them) stop blocking entries. Baseline limits are never modified; breaches are still
  computed and reported as "breached but NOT enforced".
- entry_kill_switch: blocks every new Model B paper entry immediately, regardless of override.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.types import TradeRecord
from config.settings import ModelBConfig


class ModelBRiskManager:
    """Oversees independent portfolio-level risk limits and circuit breakers for Model B."""

    def __init__(
        self,
        config: Optional[ModelBConfig] = None,
        initial_equity_usdt: float = 100.0,
        trade_risk_usdt: float = 1.0,
        daily_loss_limit_usdt: float = 3.0,
        weekly_loss_limit_usdt: float = 7.5,
        max_open_positions: int = 10,
        diagnostic_override_active: bool = False,
        entry_kill_switch: bool = False,
        live_trading_locked: bool = True,
    ) -> None:
        if config:
            self.initial_equity = config.initial_equity_usdt
            self.trade_risk_usdt = config.target_trade_risk_usdt
            self.daily_loss_limit = config.daily_loss_limit_usdt
            self.weekly_loss_limit = config.weekly_loss_limit_usdt
            self.max_open_positions = config.max_open_positions
            self.diagnostic_override_active = bool(getattr(config, "diagnostic_breaker_override", diagnostic_override_active))
            self.entry_kill_switch = bool(getattr(config, "entry_kill_switch", entry_kill_switch))
        else:
            self.initial_equity = initial_equity_usdt
            self.trade_risk_usdt = trade_risk_usdt
            self.daily_loss_limit = daily_loss_limit_usdt
            self.weekly_loss_limit = weekly_loss_limit_usdt
            self.max_open_positions = max_open_positions
            self.diagnostic_override_active = diagnostic_override_active
            self.entry_kill_switch = entry_kill_switch

        self.diagnostic_state: Dict[str, Any] = {}
        self.equity_usdt = self.initial_equity
        self.model_id = "MODEL_B"
        self.live_trading_locked = live_trading_locked

        self.current_day_str: str = ""
        self.current_week_str: str = ""
        self.daily_loss_usdt: float = 0.0
        self.weekly_loss_usdt: float = 0.0
        self.weekly_entries_count: int = 0

        self.open_trades: List[TradeRecord] = []
        self.closed_trades: List[TradeRecord] = []

    def _sync_calendar_buckets(self, current_ts_ms: int) -> None:
        """Reset loss counters at 00:00 UTC daily and Mondays 00:00 UTC weekly."""
        dt = datetime.fromtimestamp(current_ts_ms / 1000.0, tz=timezone.utc)
        day_str = dt.strftime("%Y-%m-%d")
        week_str = dt.strftime("%Y-W%W")

        if day_str != self.current_day_str:
            self.current_day_str = day_str
            self.daily_loss_usdt = 0.0

        if week_str != self.current_week_str:
            self.current_week_str = week_str
            self.weekly_loss_usdt = 0.0
            self.weekly_entries_count = 0

    def sync_state_from_journal(
        self,
        journal: Any,
        now_ms: Optional[int] = None,
        model_id: Optional[str] = "MODEL_B",
    ) -> None:
        """Authoritatively restore realized loss counters and equity from journal for Model B."""
        if journal is None:
            return

        current_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
        self._sync_calendar_buckets(current_ms)

        # Query closed trades specifically for Model B
        trades = journal.get_trades(open_only=False, limit=5000, model_id="MODEL_B")
        closed_trades = [t for t in trades if not t.get("is_open", False) and t.get("exit_timestamp_ms") is not None]

        daily_losses = 0.0
        weekly_losses = 0.0
        weekly_entries = 0
        cumulative_realized_pnl = 0.0

        for t in closed_trades:
            net_pnl = float(t.get("pnl_net", 0.0))
            cumulative_realized_pnl += net_pnl

            exit_ts = t.get("exit_timestamp_ms")
            if exit_ts:
                exit_dt = datetime.fromtimestamp(exit_ts / 1000.0, tz=timezone.utc)
                if exit_dt.strftime("%Y-%m-%d") == self.current_day_str:
                    if net_pnl < 0:
                        daily_losses += abs(net_pnl)

                if exit_dt.strftime("%Y-W%W") == self.current_week_str:
                    if net_pnl < 0:
                        weekly_losses += abs(net_pnl)

            entry_ts = t.get("entry_timestamp_ms")
            if entry_ts:
                entry_dt = datetime.fromtimestamp(entry_ts / 1000.0, tz=timezone.utc)
                if entry_dt.strftime("%Y-W%W") == self.current_week_str:
                    weekly_entries += 1

        self.daily_loss_usdt = round(daily_losses, 4)
        self.weekly_loss_usdt = round(weekly_losses, 4)
        self.weekly_entries_count = weekly_entries
        self.equity_usdt = round(self.initial_equity + cumulative_realized_pnl, 4)

        # Sync open trades
        raw_open = journal.get_open_trades(model_id="MODEL_B")
        self.open_trades = [
            TradeRecord(
                trade_id=r["trade_id"],
                symbol=r["symbol"],
                direction=r["direction"],
                entry_timestamp_ms=int(r["entry_timestamp_ms"]),
                entry_price=float(r["entry_price"]),
                stop_loss=float(r["stop_loss"]),
                take_profit=float(r["take_profit"]),
                quantity=float(r["quantity"]),
                notional_usdt=float(r["notional_usdt"]),
                is_open=True,
                setup_id=r.get("setup_id", ""),
                model_id="MODEL_B",
            )
            for r in raw_open
        ]

    def can_open_new_position(self, current_ts_ms: int) -> Tuple[bool, Optional[str]]:
        """Validate whether a new Model B position is permitted under risk governance."""
        self._sync_calendar_buckets(current_ts_ms)

        # 0. Manual kill switch: always enforced, independent of the diagnostic override
        if self.entry_kill_switch:
            return False, "Model B entry kill switch is ON: new paper entries are blocked."

        # 1. Concurrent open positions cap
        if len(self.open_trades) >= self.max_open_positions:
            return (
                False,
                f"Model B concurrent position cap reached ({len(self.open_trades)}/{self.max_open_positions}).",
            )

        # Diagnostic override (paper-only): the PnL breakers below are reported but not enforced.
        # The position cap above and every other safeguard stay in force.
        if self.diagnostic_override_active and self.live_trading_locked:
            return True, None

        # 2. Daily circuit breaker
        if self.daily_loss_usdt >= self.daily_loss_limit:
            return (
                False,
                f"Model B daily loss circuit breaker triggered (${self.daily_loss_usdt:.2f} >= ${self.daily_loss_limit:.2f}).",
            )

        # 3. Weekly circuit breaker
        if self.weekly_loss_usdt >= self.weekly_loss_limit:
            return (
                False,
                f"Model B weekly loss circuit breaker triggered (${self.weekly_loss_usdt:.2f} >= ${self.weekly_loss_limit:.2f}).",
            )

        # 4. Remaining risk budget check
        daily_remaining = max(0.0, self.daily_loss_limit - self.daily_loss_usdt)
        weekly_remaining = max(0.0, self.weekly_loss_limit - self.weekly_loss_usdt)
        min_remaining = min(daily_remaining, weekly_remaining)

        if min_remaining < self.trade_risk_usdt * 0.5:
            return (
                False,
                f"Model B remaining risk budget (${min_remaining:.2f}) insufficient for target risk (${self.trade_risk_usdt:.2f}).",
            )

        return True, None

    def record_entry(self, trade: TradeRecord) -> None:
        """Track entry of a new simulated paper position."""
        if not any(t.trade_id == trade.trade_id for t in self.open_trades):
            self.open_trades.append(trade)
        self.weekly_entries_count += 1

    def record_exit(self, trade: TradeRecord) -> None:
        """Update realized PnL, loss meters, and equity upon simulated position close."""
        self.open_trades = [t for t in self.open_trades if t.trade_id != trade.trade_id]
        self.closed_trades.append(trade)

        exit_ts = trade.exit_timestamp_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        self._sync_calendar_buckets(exit_ts)

        net_pnl = float(trade.pnl_net)
        self.equity_usdt = round(self.equity_usdt + net_pnl, 4)

        if net_pnl < 0:
            loss = abs(net_pnl)
            self.daily_loss_usdt = round(self.daily_loss_usdt + loss, 4)
            self.weekly_loss_usdt = round(self.weekly_loss_usdt + loss, 4)

    def get_risk_summary(self) -> Dict[str, Any]:
        """Telemetry snapshot for dashboard and API exposure."""
        return {
            "model_id": self.model_id,
            "equity_usdt": round(self.equity_usdt, 2),
            "initial_equity_usdt": round(self.initial_equity, 2),
            "daily_loss_usdt": round(self.daily_loss_usdt, 2),
            "daily_loss_limit_usdt": round(self.daily_loss_limit, 2),
            "weekly_loss_usdt": round(self.weekly_loss_usdt, 2),
            "weekly_loss_limit_usdt": round(self.weekly_loss_limit, 2),
            "open_positions_count": len(self.open_trades),
            "max_open_positions": self.max_open_positions,
            "weekly_entries_count": self.weekly_entries_count,
            "live_trading_locked": self.live_trading_locked,
            # *_active = raw breach of the unchanged baseline limit; *_enforced = actually blocking entries.
            "daily_circuit_breaker_active": self.daily_loss_usdt >= self.daily_loss_limit,
            "weekly_circuit_breaker_active": self.weekly_loss_usdt >= self.weekly_loss_limit,
            "daily_circuit_breaker_enforced": (self.daily_loss_usdt >= self.daily_loss_limit) and not self.diagnostic_override_active,
            "weekly_circuit_breaker_enforced": (self.weekly_loss_usdt >= self.weekly_loss_limit) and not self.diagnostic_override_active,
            "diagnostic_override_active": self.diagnostic_override_active,
            "entry_kill_switch": self.entry_kill_switch,
        }
