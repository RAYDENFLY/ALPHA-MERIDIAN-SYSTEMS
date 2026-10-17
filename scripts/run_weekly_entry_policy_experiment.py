"""Empirical Evaluation of Weekly Entry Policies on SignalGenerator Baseline Strategy.

Compares:
1. Policy A: Legacy Hard Cap (max_entries_per_week = 3)
2. Policy B: Opportunity-Driven / No Hard Cap (max_entries_per_week = None)
3. Policy Sensitivity Sweep: Cap = 1, 2, 3, 4, 5, Unlimited (None)

Evaluates:
- 1-Year historical dataset (11 symbols, 93,170 1H bars)
- Exact portfolio execution under real risk controls:
  - max_open_positions = 1
  - daily_loss_limit = $0.18 USDT
  - weekly_loss_limit = $0.54 USDT
  - hard_risk_limit = $1.50 USDT
  - target_margin = $6.00
  - leverage = 10x
  - TP1 partial close (50%) + break-even trailing stop + TP2
  - maker 0.02% / taker 0.05% / slippage 0.05% / funding rates
- Walk-forward chronological splits (60% Dev / 20% Val / 20% OOS)
- Cost sensitivity stress test (1.5x fees, 2.0x slippage)
- Blocked trade attribution analysis (did the 3-cap block winning or losing trades?)

Exports:
- weekly_policy_comparison.csv
- weekly_policy_trade_attribution.csv
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_default_config
from src.core.types import Candle, Direction, EntryCategory, SetupCandidate
from src.data.database import DatabaseManager
from src.exchange.aster import AsterDexAdapter
from src.exchange.metadata import SymbolSpecs
from src.risk.sizing import PositionSizer
from src.strategy.generator import SetupGenerator


SYMBOLS = [
    "ADAUSDT", "XRPUSDT", "BTCUSDT", "BNBUSDT", "ETHUSDT",
    "SOLUSDT", "AVAXUSDT", "LINKUSDT", "XMRUSDT", "DOGEUSDT", "LTCUSDT"
]

# Chronological Validation Split (Calendar 60/20/20 with 48h embargo)
DEV_START_MS = 1728903600000  # 2024-10-14 11:00 UTC
DEV_END_MS = 1747872000000    # 2025-05-22 00:00 UTC
VAL_START_MS = 1748044800000  # 2025-05-24 00:00 UTC
VAL_END_MS = 1754352000000    # 2025-08-05 00:00 UTC
OOS_START_MS = 1754524800000  # 2025-08-07 00:00 UTC
OOS_END_MS = 1760713200000    # 2025-10-17 15:00 UTC


@dataclass
class SimulatedPaperTrade:
    trade_id: str
    candidate_id: str
    symbol: str
    direction: Direction
    entry_category: str
    created_ts_ms: int
    fill_ts_ms: int
    exit_ts_ms: int
    entry_price: float
    stop_loss: float
    take_profit: float
    take_profit_2: Optional[float]
    quantity: float
    notional_usdt: float
    margin_usdt: float
    exit_price: float
    gross_pnl_usdt: float
    net_pnl_usdt: float
    fees_paid_usdt: float
    funding_paid_usdt: float
    exit_reason: str
    holding_bars: int
    is_win: bool


def simulate_paper_trade(
    cand: SetupCandidate,
    c1_list: List[Candle],
    start_idx: int,
    funding_map: Dict[int, float],
    maker_fee_rate: float = 0.0002,
    taker_fee_rate: float = 0.0005,
    slippage_rate: float = 0.0005,
    fill_buffer_pct: float = 0.0005,
    max_pending_bars: int = 12,
    max_holding_bars: int = 48,
) -> Tuple[str, Optional[SimulatedPaperTrade]]:
    """Simulate paper lifecycle for candidate against forward 1H candles."""
    total_bars = len(c1_list)
    pending_end = min(total_bars, start_idx + 1 + max_pending_bars)

    fill_idx = None
    actual_entry_price = cand.entry_price

    # 1. Pending Limit Order Evaluation
    for idx in range(start_idx + 1, pending_end):
        bar = c1_list[idx]

        if cand.direction == Direction.LONG and bar.low <= cand.stop_loss:
            return "INVALIDATED_PRE_FILL", None
        if cand.direction == Direction.SHORT and bar.high >= cand.stop_loss:
            return "INVALIDATED_PRE_FILL", None

        if cand.direction == Direction.LONG:
            fill_thresh = cand.entry_price * (1.0 - fill_buffer_pct)
            if bar.low <= fill_thresh:
                fill_idx = idx
                actual_entry_price = cand.entry_price * (1.0 + slippage_rate)
                break
        else:
            fill_thresh = cand.entry_price * (1.0 + fill_buffer_pct)
            if bar.high >= fill_thresh:
                fill_idx = idx
                actual_entry_price = cand.entry_price * (1.0 - slippage_rate)
                break

    if fill_idx is None:
        return "EXPIRED_NON_FILL", None

    # 2. Position Lifecycle Evaluation
    assessment = cand.risk_assessment
    quantity = assessment.quantity if assessment else 0.01
    notional = actual_entry_price * quantity
    margin = assessment.target_isolated_margin_usdt if assessment else 6.0

    entry_fee = notional * maker_fee_rate
    current_sl = cand.stop_loss
    tp1 = cand.take_profit_1
    tp2 = cand.take_profit_2 or (tp1 + abs(tp1 - actual_entry_price))

    is_partial_closed = False
    closed_at_tp1_qty = 0.0
    closed_at_tp1_notional = 0.0
    tp1_gross_pnl = 0.0
    tp1_fee = 0.0
    funding_total = 0.0

    exit_idx = None
    exit_price = actual_entry_price
    exit_reason = "UNKNOWN"

    holding_end = min(total_bars, fill_idx + max_holding_bars)
    for idx in range(fill_idx, holding_end):
        bar = c1_list[idx]

        fr = funding_map.get(bar.timestamp_ms, 0.0)
        if fr != 0.0:
            f_amount = notional * fr if cand.direction == Direction.LONG else notional * (-fr)
            funding_total += f_amount

        if cand.direction == Direction.LONG:
            hit_sl = bar.low <= current_sl
            hit_tp = bar.high >= tp1 if not is_partial_closed else (bar.high >= tp2)

            if hit_sl:
                exit_idx = idx
                exit_price = current_sl * (1.0 - slippage_rate)
                exit_reason = "STOP_LOSS" if not is_partial_closed else "TRAILING_STOP_BE"
                break
            elif hit_tp and not is_partial_closed:
                is_partial_closed = True
                closed_at_tp1_qty = quantity * 0.5
                closed_at_tp1_notional = tp1 * closed_at_tp1_qty
                tp1_gross_pnl = (tp1 - actual_entry_price) * closed_at_tp1_qty
                tp1_fee = closed_at_tp1_notional * taker_fee_rate
                current_sl = actual_entry_price * 1.0005
                if bar.high >= tp2:
                    exit_idx = idx
                    exit_price = tp2 * (1.0 - slippage_rate)
                    exit_reason = "TAKE_PROFIT_2"
                    break
            elif hit_tp and is_partial_closed:
                exit_idx = idx
                exit_price = tp2 * (1.0 - slippage_rate)
                exit_reason = "TAKE_PROFIT_2"
                break
        else:
            hit_sl = bar.high >= current_sl
            hit_tp = bar.low <= tp1 if not is_partial_closed else (bar.low <= tp2)

            if hit_sl:
                exit_idx = idx
                exit_price = current_sl * (1.0 + slippage_rate)
                exit_reason = "STOP_LOSS" if not is_partial_closed else "TRAILING_STOP_BE"
                break
            elif hit_tp and not is_partial_closed:
                is_partial_closed = True
                closed_at_tp1_qty = quantity * 0.5
                closed_at_tp1_notional = tp1 * closed_at_tp1_qty
                tp1_gross_pnl = (actual_entry_price - tp1) * closed_at_tp1_qty
                tp1_fee = closed_at_tp1_notional * taker_fee_rate
                current_sl = actual_entry_price * 0.9995
                if bar.low <= tp2:
                    exit_idx = idx
                    exit_price = tp2 * (1.0 + slippage_rate)
                    exit_reason = "TAKE_PROFIT_2"
                    break
            elif hit_tp and is_partial_closed:
                exit_idx = idx
                exit_price = tp2 * (1.0 + slippage_rate)
                exit_reason = "TAKE_PROFIT_2"
                break

    if exit_idx is None:
        exit_idx = holding_end - 1
        bar_exit = c1_list[exit_idx]
        exit_price = bar_exit.close
        exit_reason = "EXPIRED_HOLDING"

    remaining_qty = quantity - closed_at_tp1_qty
    remaining_notional = exit_price * remaining_qty
    final_gross_pnl = (
        (exit_price - actual_entry_price) * remaining_qty
        if cand.direction == Direction.LONG
        else (actual_entry_price - exit_price) * remaining_qty
    )
    final_fee = remaining_notional * taker_fee_rate

    gross_pnl_total = tp1_gross_pnl + final_gross_pnl
    fees_total = entry_fee + tp1_fee + final_fee
    net_pnl_total = gross_pnl_total - fees_total - funding_total
    holding_bars = exit_idx - fill_idx + 1

    entry_cat_str = cand.entry_category.value if hasattr(cand.entry_category, "value") else str(cand.entry_category)

    sim_trade = SimulatedPaperTrade(
        trade_id=f"sim_{cand.candidate_id[:8]}",
        candidate_id=cand.candidate_id,
        symbol=cand.symbol,
        direction=cand.direction,
        entry_category=entry_cat_str,
        created_ts_ms=cand.timestamp_ms,
        fill_ts_ms=c1_list[fill_idx].timestamp_ms,
        exit_ts_ms=c1_list[exit_idx].timestamp_ms,
        entry_price=actual_entry_price,
        stop_loss=cand.stop_loss,
        take_profit=tp1,
        take_profit_2=tp2,
        quantity=quantity,
        notional_usdt=notional,
        margin_usdt=margin,
        exit_price=exit_price,
        gross_pnl_usdt=round(gross_pnl_total, 4),
        net_pnl_usdt=round(net_pnl_total, 4),
        fees_paid_usdt=round(fees_total, 4),
        funding_paid_usdt=round(funding_total, 4),
        exit_reason=exit_reason,
        holding_bars=holding_bars,
        is_win=net_pnl_total > 0,
    )
    return "FILLED", sim_trade


def calc_stats(trades: List[SimulatedPaperTrade]) -> Dict[str, Any]:
    """Calculate comprehensive performance statistics."""
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "net_pnl": 0.0, "expectancy": 0.0, "profit_factor": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "max_dd": 0.0,
            "ci_low": 0.0, "ci_high": 0.0, "avg_holding_bars": 0.0,
            "long_trades": 0, "long_exp": 0.0, "short_trades": 0, "short_exp": 0.0
        }
    pnl_arr = np.array([t.net_pnl_usdt for t in trades])
    wins = [p for p in pnl_arr if p > 0]
    losses = [p for p in pnl_arr if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 999.0
    exp = float(np.mean(pnl_arr))
    wr = (len(wins) / len(pnl_arr)) * 100.0
    avg_w = float(np.mean(wins)) if wins else 0.0
    avg_l = float(np.mean(losses)) if losses else 0.0

    # Equity curve and max drawdown
    equity_curve = [16.0]
    for p in pnl_arr:
        equity_curve.append(equity_curve[-1] + p)
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    # Bootstrap 95% Confidence Interval (2,000 iterations)
    np.random.seed(42)
    boot_means = []
    n = len(pnl_arr)
    for _ in range(2000):
        sample = np.random.choice(pnl_arr, size=n, replace=True)
        boot_means.append(np.mean(sample))
    ci_low = float(np.percentile(boot_means, 2.5))
    ci_high = float(np.percentile(boot_means, 97.5))

    longs = [t.net_pnl_usdt for t in trades if t.direction == Direction.LONG]
    shorts = [t.net_pnl_usdt for t in trades if t.direction == Direction.SHORT]

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(wr, 2),
        "net_pnl": round(float(np.sum(pnl_arr)), 4),
        "expectancy": round(exp, 4),
        "profit_factor": round(pf, 2),
        "avg_win": round(avg_w, 4),
        "avg_loss": round(avg_l, 4),
        "max_dd": round(max_dd, 4),
        "ci_low": round(ci_low, 4),
        "ci_high": round(ci_high, 4),
        "avg_holding_bars": round(float(np.mean([t.holding_bars for t in trades])), 1),
        "long_trades": len(longs),
        "long_exp": round(float(np.mean(longs)), 4) if longs else 0.0,
        "short_trades": len(shorts),
        "short_exp": round(float(np.mean(shorts)), 4) if shorts else 0.0,
    }


def run_portfolio_simulation(
    eligible_candidates: List[Tuple[int, str, int, SetupCandidate]],
    candles_1h: Dict[str, List[Candle]],
    funding_cache: Dict[str, Dict[int, float]],
    max_entries_per_week: Optional[int],
    daily_loss_limit: float = 0.18,
    weekly_loss_limit: float = 0.54,
    maker_fee: float = 0.0002,
    taker_fee: float = 0.0005,
    slippage: float = 0.0005,
) -> Dict[str, Any]:
    """Simulate realistic portfolio execution bar-by-bar with risk guards."""
    portfolio_trades: List[SimulatedPaperTrade] = []
    active_pos_exit_ts = 0
    weekly_entries_count: Dict[int, int] = defaultdict(int)

    # Real-world realized loss meters
    daily_realized_loss: Dict[str, float] = defaultdict(float)   # "YYYY-MM-DD" -> loss
    weekly_realized_loss: Dict[int, float] = defaultdict(float)  # week_key -> loss

    rejection_reasons = {
        "CONCURRENCY_BLOCKED": 0,
        "WEEKLY_COUNT_BLOCKED": 0,
        "DAILY_LOSS_GUARD_BLOCKED": 0,
        "WEEKLY_LOSS_GUARD_BLOCKED": 0,
    }

    submitted_count = 0
    blocked_trade_records: List[Dict[str, Any]] = []

    for ts, sym, idx, cand in eligible_candidates:
        dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        day_str = dt.strftime("%Y-%m-%d")
        week_key = dt.year * 100 + dt.isocalendar()[1]

        # 1. Concurrency Check (Max 1 Open Position)
        if ts < active_pos_exit_ts:
            rejection_reasons["CONCURRENCY_BLOCKED"] += 1
            continue

        # 2. Daily Loss Limit Check ($0.18)
        if daily_realized_loss[day_str] >= daily_loss_limit:
            rejection_reasons["DAILY_LOSS_GUARD_BLOCKED"] += 1
            continue

        # 3. Weekly Loss Limit Check ($0.54)
        if weekly_realized_loss[week_key] >= weekly_loss_limit:
            rejection_reasons["WEEKLY_LOSS_GUARD_BLOCKED"] += 1
            continue

        # 4. Weekly Entry Count Policy Gate
        if max_entries_per_week is not None and weekly_entries_count[week_key] >= max_entries_per_week:
            rejection_reasons["WEEKLY_COUNT_BLOCKED"] += 1
            # Simulate what would have happened to this candidate
            _, phantom_trade = simulate_paper_trade(
                cand=cand,
                c1_list=candles_1h[sym],
                start_idx=idx,
                funding_map=funding_cache[sym],
                maker_fee_rate=maker_fee,
                taker_fee_rate=taker_fee,
                slippage_rate=slippage,
            )
            if phantom_trade is not None:
                blocked_trade_records.append({
                    "ts": ts,
                    "date_utc": dt.strftime("%Y-%m-%d %H:%M"),
                    "symbol": sym,
                    "direction": cand.direction.value,
                    "net_pnl": phantom_trade.net_pnl_usdt,
                    "is_win": phantom_trade.is_win,
                    "exit_reason": phantom_trade.exit_reason,
                    "week_key": week_key,
                })
            continue

        # Candidate is authorized for entry
        submitted_count += 1
        weekly_entries_count[week_key] += 1

        status, trade = simulate_paper_trade(
            cand=cand,
            c1_list=candles_1h[sym],
            start_idx=idx,
            funding_map=funding_cache[sym],
            maker_fee_rate=maker_fee,
            taker_fee_rate=taker_fee,
            slippage_rate=slippage,
        )

        if status == "FILLED" and trade is not None:
            portfolio_trades.append(trade)
            active_pos_exit_ts = trade.exit_ts_ms

            # If trade lost, record realized loss to calendar meters
            if trade.net_pnl_usdt < 0:
                loss_val = abs(trade.net_pnl_usdt)
                exit_dt = datetime.fromtimestamp(trade.exit_ts_ms / 1000.0, tz=timezone.utc)
                exit_day = exit_dt.strftime("%Y-%m-%d")
                exit_week = exit_dt.year * 100 + exit_dt.isocalendar()[1]
                daily_realized_loss[exit_day] += loss_val
                weekly_realized_loss[exit_week] += loss_val

    # Histogram of weekly trade counts
    weeks_distribution = defaultdict(int)
    for w, count in weekly_entries_count.items():
        weeks_distribution[count] += 1

    stats = calc_stats(portfolio_trades)

    # Chronological Splits
    dev_trades = [t for t in portfolio_trades if t.created_ts_ms <= DEV_END_MS]
    val_trades = [t for t in portfolio_trades if VAL_START_MS <= t.created_ts_ms <= VAL_END_MS]
    oos_trades = [t for t in portfolio_trades if t.created_ts_ms >= OOS_START_MS]

    return {
        "policy_name": f"Cap={max_entries_per_week}" if max_entries_per_week else "Unlimited (No Hard Cap)",
        "max_entries_per_week": max_entries_per_week,
        "submitted_count": submitted_count,
        "completed_trades": len(portfolio_trades),
        "rejections": rejection_reasons,
        "stats": stats,
        "dev_stats": calc_stats(dev_trades),
        "val_stats": calc_stats(val_trades),
        "oos_stats": calc_stats(oos_trades),
        "weekly_distribution": dict(weeks_distribution),
        "blocked_trade_records": blocked_trade_records,
        "trades": portfolio_trades,
    }


def main():
    print("================================================================================")
    print("SignalGenerator — Quantitative Weekly Entry Policy Research Experiment")
    print("Evaluating Baseline SMC/SnR across 1-Year Dataset (93,170 1H bars, 11 symbols)")
    print("================================================================================\n")

    cfg = get_default_config()
    db = DatabaseManager(cfg.data_dir / cfg.db_filename)

    # 1. Load Symbol Specs
    specs_map: Dict[str, SymbolSpecs] = {}
    adapter = AsterDexAdapter()
    for s in SYMBOLS:
        sp = adapter.get_symbol_specs(s)
        if sp:
            specs_map[s] = sp

    # 2. Load Candles and Funding Rates
    candles_1h: Dict[str, List[Candle]] = {}
    candles_4h: Dict[str, List[Candle]] = {}
    funding_cache: Dict[str, Dict[int, float]] = {}

    for sym in SYMBOLS:
        candles_1h[sym] = db.get_candles(sym, "1h")
        candles_4h[sym] = db.get_candles(sym, "4h")
        rates = db.get_funding_rates(sym)
        funding_cache[sym] = {r["funding_time_ms"]: float(r["funding_rate"]) for r in rates}

    print(f"Loaded 11 symbols. Total 1H candles: {sum(len(c) for c in candles_1h.values()):,}")

    # 3. Generate Baseline Setups (Immutable Strategy Baseline: [0.12, 0.88], Breakout 0.92)
    gen = SetupGenerator(discount_threshold=0.12, premium_threshold=0.88, bull_breakout_pos_72h=0.92)
    eligible_candidates: List[Tuple[int, str, int, SetupCandidate]] = []

    for sym in SYMBOLS:
        c1 = candles_1h[sym]
        c4 = candles_4h[sym]
        specs = specs_map.get(sym)

        for i in range(170, len(c1)):
            cand = gen.generate_candidate(sym, c1, c4, as_of_index_1h=i)
            if cand is None:
                continue

            assessment = PositionSizer.calculate_sizing(
                symbol_specs=specs,
                entry_price=cand.entry_price,
                stop_loss=cand.stop_loss,
                target_isolated_margin=6.0,
                target_isolated_margin_max=6.5,
                available_equity_usdt=16.0,
                max_leverage=10,
                hard_risk_limit_usdt=1.50,
                risk_budget_usdt=0.09,
                direction=cand.direction,
            )
            if assessment.is_feasible:
                cand.risk_assessment = assessment
                eligible_candidates.append((cand.timestamp_ms, sym, i, cand))

    eligible_candidates.sort(key=lambda x: x[0])
    print(f"Total eligible baseline setups: {len(eligible_candidates)}")

    # 4. Standalone Simulation (Zero Portfolio Constraints)
    standalone_trades: List[SimulatedPaperTrade] = []
    for ts, sym, idx, cand in eligible_candidates:
        status, trade = simulate_paper_trade(
            cand=cand,
            c1_list=candles_1h[sym],
            start_idx=idx,
            funding_map=funding_cache[sym],
        )
        if status == "FILLED" and trade is not None:
            standalone_trades.append(trade)

    st_stats = calc_stats(standalone_trades)
    print(f"Standalone Baseline: {len(standalone_trades)} fills, Net PnL: +${st_stats['net_pnl']:.2f}, WR: {st_stats['win_rate']}%, Exp: +${st_stats['expectancy']:.4f}\n")

    # 5. Evaluate Policies: Cap=1, Cap=2, Cap=3 (Legacy), Cap=4, Cap=5, and Unlimited (None)
    policies = [1, 2, 3, 4, 5, None]
    results = []

    for p in policies:
        res = run_portfolio_simulation(
            eligible_candidates=eligible_candidates,
            candles_1h=candles_1h,
            funding_cache=funding_cache,
            max_entries_per_week=p,
        )
        results.append(res)

    # 6. Cost Sensitivity for Legacy Cap=3 vs Unlimited (Stressed Costs: 1.5x Fees, 2.0x Slippage)
    res_cap3_stressed = run_portfolio_simulation(
        eligible_candidates=eligible_candidates,
        candles_1h=candles_1h,
        funding_cache=funding_cache,
        max_entries_per_week=3,
        maker_fee=0.0003,
        taker_fee=0.00075,
        slippage=0.0010,
    )
    res_unlimited_stressed = run_portfolio_simulation(
        eligible_candidates=eligible_candidates,
        candles_1h=candles_1h,
        funding_cache=funding_cache,
        max_entries_per_week=None,
        maker_fee=0.0003,
        taker_fee=0.00075,
        slippage=0.0010,
    )

    # Print Comparative Matrix
    print("==========================================================================================================")
    print("POLICY COMPARISON MATRIX (1-YEAR SAMPLE, $16 CAPITAL, MAX 1 OPEN POS, $0.18 DAILY / $0.54 WEEKLY LOSS CAP)")
    print("==========================================================================================================")
    print(f"{'Policy':<25} | {'Trades':<6} | {'WR (%)':<6} | {'Net PnL':<10} | {'Expectancy':<10} | {'PF':<5} | {'Max DD':<7} | {'95% CI':<22}")
    print("----------------------------------------------------------------------------------------------------------")
    for r in results:
        s = r["stats"]
        ci_str = f"[{s['ci_low']:+.4f}, {s['ci_high']:+.4f}]"
        print(f"{r['policy_name']:<25} | {r['completed_trades']:<6} | {s['win_rate']:<6.1f} | {s['net_pnl']:<+10.2f} | {s['expectancy']:<+10.4f} | {s['profit_factor']:<5.2f} | ${s['max_dd']:<6.2f} | {ci_str:<22}")

    print("----------------------------------------------------------------------------------------------------------")
    s_c3_st = res_cap3_stressed["stats"]
    s_un_st = res_unlimited_stressed["stats"]
    print(f"{'Cap=3 (Stressed Costs)':<25} | {res_cap3_stressed['completed_trades']:<6} | {s_c3_st['win_rate']:<6.1f} | {s_c3_st['net_pnl']:<+10.2f} | {s_c3_st['expectancy']:<+10.4f} | {s_c3_st['profit_factor']:<5.2f} | ${s_c3_st['max_dd']:<6.2f} | [{s_c3_st['ci_low']:+.4f}, {s_c3_st['ci_high']:+.4f}]")
    print(f"{'Unlimited (Stressed Costs)':<25} | {res_unlimited_stressed['completed_trades']:<6} | {s_un_st['win_rate']:<6.1f} | {s_un_st['net_pnl']:<+10.2f} | {s_un_st['expectancy']:<+10.4f} | {s_un_st['profit_factor']:<5.2f} | ${s_un_st['max_dd']:<6.2f} | [{s_un_st['ci_low']:+.4f}, {s_un_st['ci_high']:+.4f}]")
    print("==========================================================================================================\n")

    # 7. Blocked Trade Attribution Analysis
    cap3_res = next(r for r in results if r["max_entries_per_week"] == 3)
    unlimited_res = next(r for r in results if r["max_entries_per_week"] is None)

    blocked_trades = cap3_res["blocked_trade_records"]
    print(f"--- BLOCKED TRADES ATTRIBUTION ANALYSIS (TRADES BLOCKED BY 3-CAP) ---")
    print(f"Total Trades Blocked by Cap=3: {len(blocked_trades)}")
    if blocked_trades:
        b_pnl = sum(t["net_pnl"] for t in blocked_trades)
        b_wins = sum(1 for t in blocked_trades if t["is_win"])
        b_wr = (b_wins / len(blocked_trades)) * 100.0
        print(f"Net PnL of Blocked Trades: {b_pnl:+.4f} USDT")
        print(f"Win Rate of Blocked Trades: {b_wr:.1f}% ({b_wins} wins, {len(blocked_trades) - b_wins} losses)")
        print("\nIndividual Blocked Trades:")
        for t in blocked_trades:
            print(f"  [{t['date_utc']}] {t['symbol']} {t['direction']:<5} -> PnL: {t['net_pnl']:+.4f} USDT ({t['exit_reason']})")
    print()

    # 8. Export weekly_policy_comparison.csv
    csv_rows = [
        "Policy,MaxWeeklyCap,CompletedTrades,WinRatePct,NetPnLUSDT,ExpectancyPerTrade,ProfitFactor,MaxDrawdownUSDT,CILowUSDT,CIHighUSDT,DevExpectancy,ValExpectancy,OOSExpectancy,ConcurrencyBlocked,WeeklyCapBlocked,DailyLossBlocked,WeeklyLossBlocked"
    ]
    for r in results:
        s = r["stats"]
        rej = r["rejections"]
        dev_exp = r["dev_stats"]["expectancy"]
        val_exp = r["val_stats"]["expectancy"]
        oos_exp = r["oos_stats"]["expectancy"]
        row = f'"{r["policy_name"]}",{r["max_entries_per_week"] if r["max_entries_per_week"] else "None"},{r["completed_trades"]},{s["win_rate"]},{s["net_pnl"]},{s["expectancy"]},{s["profit_factor"]},{s["max_dd"]},{s["ci_low"]},{s["ci_high"]},{dev_exp},{val_exp},{oos_exp},{rej["CONCURRENCY_BLOCKED"]},{rej["WEEKLY_COUNT_BLOCKED"]},{rej["DAILY_LOSS_GUARD_BLOCKED"]},{rej["WEEKLY_LOSS_GUARD_BLOCKED"]}'
        csv_rows.append(row)

    csv_path = Path("weekly_policy_comparison.csv")
    csv_path.write_text("\n".join(csv_rows), encoding="utf-8")
    print(f"Saved: {csv_path.resolve()}")

    # 9. Export weekly_policy_trade_attribution.csv
    attr_rows = ["DateUTC,TimestampMs,Symbol,Direction,NetPnLUSDT,IsWin,ExitReason,WeekKey"]
    for t in blocked_trades:
        attr_rows.append(f'{t["date_utc"]},{t["ts"]},{t["symbol"]},{t["direction"]},{t["net_pnl"]},{t["is_win"]},{t["exit_reason"]},{t["week_key"]}')
    attr_path = Path("weekly_policy_trade_attribution.csv")
    attr_path.write_text("\n".join(attr_rows), encoding="utf-8")
    print(f"Saved: {attr_path.resolve()}")

    # 10. Export individual trade logs for Unlimited and Cap=3
    def export_trade_list(trades: List[SimulatedPaperTrade], filename: str):
        rows = ["TradeNum,FillDateUTC,ExitDateUTC,Symbol,Direction,EntryPrice,ExitPrice,NetPnLUSDT,ExitReason,HoldingBars,IsWin,WeekKey"]
        for idx, t in enumerate(trades):
            fill_dt = datetime.fromtimestamp(t.fill_ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            exit_dt = datetime.fromtimestamp(t.exit_ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            week_key = datetime.fromtimestamp(t.fill_ts_ms / 1000.0, tz=timezone.utc).year * 100 + datetime.fromtimestamp(t.fill_ts_ms / 1000.0, tz=timezone.utc).isocalendar()[1]
            rows.append(f'{idx+1},{fill_dt},{exit_dt},{t.symbol},{t.direction.value},{t.entry_price:.4f},{t.exit_price:.4f},{t.net_pnl_usdt:+.4f},{t.exit_reason},{t.holding_bars},{t.is_win},{week_key}')
        p = Path(filename)
        p.write_text("\n".join(rows), encoding="utf-8")
        print(f"Saved: {p.resolve()}")

    export_trade_list(cap3_res["trades"], "weekly_policy_trades_cap3.csv")
    export_trade_list(unlimited_res["trades"], "weekly_policy_trades_unlimited.csv")


if __name__ == "__main__":
    main()
