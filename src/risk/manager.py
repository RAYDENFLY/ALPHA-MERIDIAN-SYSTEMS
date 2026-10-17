"""Account Risk Management and Loss Limit Governance.

Enforces:
- Maximum 1 concurrent position across account.
- Maximum 3 entries per calendar week.
- Daily loss limit ($0.18 USDT).
- Weekly loss limit ($0.54 USDT).
- Dynamic risk budget calculation based on remaining loss budgets.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple
from src.core.types import TradeRecord


class RiskManager:
    """Oversees account-level risk limits and position gating."""

    def __init__(
        self,
        initial_equity_usdt: float = 16.0,
        trade_risk_usdt: float = 0.09,
        daily_loss_limit_usdt: float = 0.18,
        weekly_loss_limit_usdt: float = 0.54,
        max_open_positions: int = 1,
        max_entries_per_week: int = 3,
    ) -> None:
        self.equity_usdt = initial_equity_usdt
        self.initial_equity = initial_equity_usdt
        self.trade_risk_usdt = trade_risk_usdt
        self.daily_loss_limit = daily_loss_limit_usdt
        self.weekly_loss_limit = weekly_loss_limit_usdt
        self.max_open_positions = max_open_positions
        self.max_entries_per_week = max_entries_per_week

        self.current_day_str: str = ""
        self.current_week_str: str = ""
        self.daily_loss_usdt: float = 0.0
        self.weekly_loss_usdt: float = 0.0
        self.weekly_entries_count: int = 0

        self.open_trades: List[TradeRecord] = []
        self.closed_trades: List[TradeRecord] = []

    def _sync_calendar_buckets(self, current_ts_ms: int) -> None:
        """Reset loss counters at 00:00 UTC and weekly counters on Mondays."""
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

    def get_effective_trade_risk(self, current_ts_ms: int) -> float:
        """Calculate dynamic trade risk budget:
        risk_budget = min(configured_trade_risk, remaining_daily_loss, remaining_weekly_loss)
        """
        self._sync_calendar_buckets(current_ts_ms)
        rem_daily = max(0.0, self.daily_loss_limit - self.daily_loss_usdt)
        rem_weekly = max(0.0, self.weekly_loss_limit - self.weekly_loss_usdt)
        effective = min(self.trade_risk_usdt, rem_daily, rem_weekly)
        return float(round(effective, 4))

    def can_open_new_position(self, current_ts_ms: int) -> Tuple[bool, Optional[str]]:
        """Validate whether account conditions allow entering a new position."""
        self._sync_calendar_buckets(current_ts_ms)

        if len(self.open_trades) >= self.max_open_positions:
            return False, f"Maximum open position limit ({self.max_open_positions}) reached."

        if self.weekly_entries_count >= self.max_entries_per_week:
            return False, f"Weekly entry limit ({self.max_entries_per_week}) reached for week {self.current_week_str}."

        if self.daily_loss_usdt >= self.daily_loss_limit:
            return False, f"Daily loss limit (${self.daily_loss_limit:.2f}) reached (${self.daily_loss_usdt:.2f} loss)."

        if self.weekly_loss_usdt >= self.weekly_loss_limit:
            return False, f"Weekly loss limit (${self.weekly_loss_limit:.2f}) reached (${self.weekly_loss_usdt:.2f} loss)."

        effective_risk = self.get_effective_trade_risk(current_ts_ms)
        if effective_risk <= 0.01:
            return False, "Insufficient remaining daily or weekly loss budget."

        return True, None

    def record_entry(self, trade: TradeRecord) -> None:
        """Record trade opening and update weekly entry count."""
        self._sync_calendar_buckets(trade.entry_timestamp_ms)
        self.open_trades.append(trade)
        self.weekly_entries_count += 1

    def record_exit(self, trade: TradeRecord) -> None:
        """Record trade closure and update equity and loss budgets."""
        self._sync_calendar_buckets(trade.exit_timestamp_ms or trade.entry_timestamp_ms)
        self.open_trades = [t for t in self.open_trades if t.trade_id != trade.trade_id]
        self.closed_trades.append(trade)

        self.equity_usdt += trade.pnl_net

        # If net loss, add to daily and weekly loss meters
        if trade.pnl_net < 0:
            loss = abs(trade.pnl_net)
            self.daily_loss_usdt += loss
            self.weekly_loss_usdt += loss
