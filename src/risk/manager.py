"""Account Risk Management and Loss Limit Governance.

Enforces:
- Maximum 1 concurrent position across account.
- Daily loss limit ($0.18 USDT).
- Weekly loss limit ($0.54 USDT).
- Dynamic risk budget calculation based on remaining loss budgets.
- Weekly entry count tracked as telemetry (no hard entry quota cap).
"""

from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple
from src.core.types import Direction, EntryCategory, TradeRecord


class RiskManager:
    """Oversees account-level risk limits and position gating."""

    def __init__(
        self,
        initial_equity_usdt: float = 16.0,
        trade_risk_usdt: float = 0.09,
        daily_loss_limit_usdt: float = 0.18,
        weekly_loss_limit_usdt: float = 0.54,
        max_open_positions: int = 1,
        max_entries_per_week: Optional[int] = None,
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

    def sync_state_from_journal(self, journal: Any, now_ms: Optional[int] = None) -> None:
        """Authoritatively restore realized loss counters, equity, and weekly entry counts from trade_journal.

        Calculates:
        - daily_loss_usdt: sum of abs(pnl_net) for all closed trades with exit_timestamp_ms within current UTC day.
        - weekly_loss_usdt: sum of abs(pnl_net) for all closed trades with exit_timestamp_ms within current UTC week (Monday-Sunday).
        - weekly_entries_count: count of all unique trades with entry_timestamp_ms within current UTC week.
        - equity_usdt: initial_equity + sum(pnl_net of all closed trades).
        - open_trades: synchronized with open trades in journal.
        - Deduplicated by trade_id: idempotent against replay or repeated calls.
        """
        if journal is None:
            return

        current_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
        self._sync_calendar_buckets(current_ms)

        try:
            all_trades = journal.get_trades(limit=5000)
        except Exception:
            all_trades = []

        seen_trade_ids = set()
        closed_trades_list: List[TradeRecord] = []
        open_trades_list: List[TradeRecord] = []

        daily_loss = 0.0
        weekly_loss = 0.0
        weekly_entries = set()
        total_realized_pnl = 0.0

        for r in all_trades:
            trade_id = r.get("trade_id")
            if not trade_id or trade_id in seen_trade_ids:
                continue
            seen_trade_ids.add(trade_id)

            is_open = bool(r.get("is_open", 0))
            entry_ts = int(r.get("entry_timestamp_ms") or 0)
            exit_ts = int(r.get("exit_timestamp_ms") or 0) if r.get("exit_timestamp_ms") is not None else None
            pnl_net = float(r.get("pnl_net", 0.0))

            # Check weekly entry bucket using entry_ts
            if entry_ts > 0:
                entry_dt = datetime.fromtimestamp(entry_ts / 1000.0, tz=timezone.utc)
                entry_week_str = entry_dt.strftime("%Y-W%W")
                if entry_week_str == self.current_week_str:
                    weekly_entries.add(trade_id)

            if is_open or exit_ts is None:
                # Open trade
                direction_val = Direction.LONG if str(r.get("direction")).upper() == "LONG" else Direction.SHORT
                open_rec = TradeRecord(
                    trade_id=trade_id,
                    symbol=str(r.get("symbol", "")),
                    direction=direction_val,
                    entry_timestamp_ms=entry_ts,
                    entry_price=float(r.get("entry_price", 0.0)),
                    stop_loss=float(r.get("stop_loss", 0.0)),
                    take_profit=float(r.get("take_profit", 0.0)),
                    quantity=float(r.get("quantity", 0.0)),
                    notional_usdt=float(r.get("notional_usdt", 0.0)),
                    pnl_gross=float(r.get("pnl_gross", 0.0)),
                    pnl_net=pnl_net,
                    fee_paid=float(r.get("fee_paid", 0.0)),
                    funding_paid=float(r.get("funding_paid", 0.0)),
                    exit_reason=str(r.get("exit_reason", "")),
                    is_open=True,
                    setup_id=str(r.get("setup_id", "")),
                    timeframe=str(r.get("timeframe", "1h")),
                    order_status=str(r.get("order_status", "FILLED")),
                    leverage=float(r.get("leverage", 10.0)),
                    margin_usdt=float(r.get("margin_usdt", 5.0)),
                    slippage_paid=float(r.get("slippage_paid", 0.0)),
                    entry_category=EntryCategory(r.get("entry_category", "Unclassified")),
                )
                open_trades_list.append(open_rec)
            else:
                # Closed trade
                total_realized_pnl += pnl_net
                direction_val = Direction.LONG if str(r.get("direction")).upper() == "LONG" else Direction.SHORT
                closed_rec = TradeRecord(
                    trade_id=trade_id,
                    symbol=str(r.get("symbol", "")),
                    direction=direction_val,
                    entry_timestamp_ms=entry_ts,
                    entry_price=float(r.get("entry_price", 0.0)),
                    stop_loss=float(r.get("stop_loss", 0.0)),
                    take_profit=float(r.get("take_profit", 0.0)),
                    quantity=float(r.get("quantity", 0.0)),
                    notional_usdt=float(r.get("notional_usdt", 0.0)),
                    exit_timestamp_ms=exit_ts,
                    exit_price=float(r.get("exit_price", 0.0)),
                    pnl_gross=float(r.get("pnl_gross", 0.0)),
                    pnl_net=pnl_net,
                    fee_paid=float(r.get("fee_paid", 0.0)),
                    funding_paid=float(r.get("funding_paid", 0.0)),
                    exit_reason=str(r.get("exit_reason", "")),
                    is_open=False,
                    setup_id=str(r.get("setup_id", "")),
                    timeframe=str(r.get("timeframe", "1h")),
                    order_status=str(r.get("order_status", "FILLED")),
                    leverage=float(r.get("leverage", 10.0)),
                    margin_usdt=float(r.get("margin_usdt", 5.0)),
                    slippage_paid=float(r.get("slippage_paid", 0.0)),
                    entry_category=EntryCategory(r.get("entry_category", "Unclassified")),
                )
                closed_trades_list.append(closed_rec)

                # Check loss in current calendar buckets (using exit_ts)
                if pnl_net < 0:
                    loss_amount = abs(pnl_net)
                    exit_dt = datetime.fromtimestamp(exit_ts / 1000.0, tz=timezone.utc)
                    exit_day_str = exit_dt.strftime("%Y-%m-%d")
                    exit_week_str = exit_dt.strftime("%Y-W%W")

                    if exit_day_str == self.current_day_str:
                        daily_loss += loss_amount
                    if exit_week_str == self.current_week_str:
                        weekly_loss += loss_amount

        self.daily_loss_usdt = round(daily_loss, 4)
        self.weekly_loss_usdt = round(weekly_loss, 4)
        self.weekly_entries_count = len(weekly_entries)
        self.equity_usdt = round(self.initial_equity + total_realized_pnl, 4)
        self.open_trades = open_trades_list
        self.closed_trades = closed_trades_list

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
