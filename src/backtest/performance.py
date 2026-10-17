"""Performance Metrics and Statistics Calculator for Backtesting and Journaling.
"""

from dataclasses import dataclass
from typing import Dict, List
import numpy as np
import pandas as pd
from src.core.types import Direction, TradeRecord


@dataclass
class BacktestReport:
    """Comprehensive backtest performance report."""
    initial_equity: float
    final_equity: float
    total_net_pnl: float
    return_on_capital_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    expectancy_per_trade: float
    max_drawdown_usdt: float
    max_drawdown_pct: float
    avg_win_usdt: float
    avg_loss_usdt: float
    total_fees_paid: float
    total_funding_paid: float
    long_trades_count: int
    long_net_pnl: float
    short_trades_count: int
    short_net_pnl: float
    per_symbol_summary: Dict[str, Dict[str, float]]
    equity_curve: List[Dict[str, float]]


class PerformanceCalculator:
    """Calculates quantitative risk-adjusted performance metrics."""

    @staticmethod
    def calculate_report(
        trades: List[TradeRecord],
        initial_equity: float = 16.0,
    ) -> BacktestReport:
        if not trades:
            return BacktestReport(
                initial_equity=initial_equity,
                final_equity=initial_equity,
                total_net_pnl=0.0,
                return_on_capital_pct=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                expectancy_per_trade=0.0,
                max_drawdown_usdt=0.0,
                max_drawdown_pct=0.0,
                avg_win_usdt=0.0,
                avg_loss_usdt=0.0,
                total_fees_paid=0.0,
                total_funding_paid=0.0,
                long_trades_count=0,
                long_net_pnl=0.0,
                short_trades_count=0,
                short_net_pnl=0.0,
                per_symbol_summary={},
                equity_curve=[{"timestamp_ms": 0, "equity": initial_equity}],
            )

        running_equity = initial_equity
        peak_equity = initial_equity
        max_dd_usdt = 0.0
        max_dd_pct = 0.0

        equity_curve: List[Dict[str, float]] = [{"timestamp_ms": trades[0].entry_timestamp_ms, "equity": initial_equity}]

        wins = []
        losses = []
        total_fees = 0.0
        total_funding = 0.0

        long_trades = []
        short_trades = []

        per_symbol_trades: Dict[str, List[TradeRecord]] = {}

        for t in trades:
            running_equity += t.pnl_net
            peak_equity = max(peak_equity, running_equity)
            dd_usdt = peak_equity - running_equity
            dd_pct = (dd_usdt / peak_equity * 100.0) if peak_equity > 0 else 0.0

            max_dd_usdt = max(max_dd_usdt, dd_usdt)
            max_dd_pct = max(max_dd_pct, dd_pct)

            equity_curve.append({
                "timestamp_ms": t.exit_timestamp_ms or t.entry_timestamp_ms,
                "equity": round(running_equity, 4),
            })

            total_fees += t.fee_paid
            total_funding += t.funding_paid

            if t.pnl_net > 0:
                wins.append(t.pnl_net)
            else:
                losses.append(abs(t.pnl_net))

            if t.direction == Direction.LONG:
                long_trades.append(t)
            elif t.direction == Direction.SHORT:
                short_trades.append(t)

            per_symbol_trades.setdefault(t.symbol, []).append(t)

        total_trades = len(trades)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

        sum_wins = sum(wins)
        sum_losses = sum(losses)
        profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else (99.0 if sum_wins > 0 else 0.0)

        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0

        win_frac = win_count / total_trades if total_trades > 0 else 0.0
        loss_frac = loss_count / total_trades if total_trades > 0 else 0.0
        expectancy = (win_frac * avg_win) - (loss_frac * avg_loss)

        total_net = running_equity - initial_equity
        roc_pct = (total_net / initial_equity * 100.0) if initial_equity > 0 else 0.0

        long_pnl = sum(t.pnl_net for t in long_trades)
        short_pnl = sum(t.pnl_net for t in short_trades)

        # Per symbol summary
        sym_summary = {}
        for sym, sym_list in per_symbol_trades.items():
            sym_wins = [t.pnl_net for t in sym_list if t.pnl_net > 0]
            sym_pnl = sum(t.pnl_net for t in sym_list)
            sym_summary[sym] = {
                "trades": len(sym_list),
                "win_rate": round(len(sym_wins) / len(sym_list) * 100.0, 1),
                "net_pnl": round(sym_pnl, 4),
            }

        return BacktestReport(
            initial_equity=initial_equity,
            final_equity=round(running_equity, 4),
            total_net_pnl=round(total_net, 4),
            return_on_capital_pct=round(roc_pct, 2),
            total_trades=total_trades,
            winning_trades=win_count,
            losing_trades=loss_count,
            win_rate_pct=round(win_rate, 2),
            profit_factor=round(profit_factor, 2),
            expectancy_per_trade=round(expectancy, 4),
            max_drawdown_usdt=round(max_dd_usdt, 4),
            max_drawdown_pct=round(max_dd_pct, 2),
            avg_win_usdt=round(avg_win, 4),
            avg_loss_usdt=round(avg_loss, 4),
            total_fees_paid=round(total_fees, 4),
            total_funding_paid=round(total_funding, 4),
            long_trades_count=len(long_trades),
            long_net_pnl=round(long_pnl, 4),
            short_trades_count=len(short_trades),
            short_net_pnl=round(short_pnl, 4),
            per_symbol_summary=sym_summary,
            equity_curve=equity_curve,
        )
