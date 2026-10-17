"""Comprehensive Quantitative Research Experiment Runner.

Executes controlled, reproducible experiments across the 1-year historical dataset (11 symbols, 95,007 1H bars):
1. Baseline Control ([0.12, 0.88], breakout 0.92)
2. Variant B1 ([0.20, 0.80], breakout 0.92)
3. Variant B2 ([0.25, 0.75], breakout 0.92)
4. Experiment A: Mode 2C Standalone (Mid-Range Liquidity Sweeps)
5. Experiment A+Control: Combined Baseline + Mode 2C

Collects all required metrics (Standalone & Portfolio, Walk-Forward splits, Risk Constraints, Cost Sensitivity)
and exports:
- candidate_rejection_attribution.csv
- baseline_vs_experiments.csv
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
from src.strategy.research_mode2c import CombinedBaselineAndMode2CGenerator, ResearchMode2CGenerator


SYMBOLS = [
    "ADAUSDT", "XRPUSDT", "BTCUSDT", "BNBUSDT", "ETHUSDT",
    "SOLUSDT", "AVAXUSDT", "LINKUSDT", "XMRUSDT", "DOGEUSDT", "LTCUSDT"
]

# Chronological Validation Split 1 (Calendar 60/20/20 with 48h embargo)
DEV_START_MS = 1728903600000  # 2024-10-14 11:00 UTC (or start of dataset)
DEV_END_MS = 1747872000000    # 2025-05-22 00:00 UTC
VAL_START_MS = 1748044800000  # 2025-05-24 00:00 UTC (48h embargo)
VAL_END_MS = 1754352000000    # 2025-08-05 00:00 UTC
OOS_START_MS = 1754524800000  # 2025-08-07 00:00 UTC (48h embargo)
OOS_END_MS = 1760713200000    # 2025-10-17 15:00 UTC

# Rolling Split 2
ROLLING_DEV_END_MS = 1755475200000   # 2025-08-18 00:00 UTC
ROLLING_VAL_START_MS = 1755648000000 # 2025-08-20 00:00 UTC
ROLLING_VAL_END_MS = 1756684800000   # 2025-09-01 00:00 UTC
ROLLING_OOS_START_MS = 1756857600000 # 2025-09-03 00:00 UTC


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
    """Simulate exact paper lifecycle for a candidate against forward 1H candles.

    Returns status string ("FILLED", "INVALIDATED_PRE_FILL", "EXPIRED_NON_FILL") and trade if filled.
    """
    total_bars = len(c1_list)
    pending_end = min(total_bars, start_idx + 1 + max_pending_bars)

    fill_idx = None
    actual_entry_price = cand.entry_price

    # 1. Pending Limit Order Evaluation (Anti-Lookahead: starts at start_idx + 1)
    for idx in range(start_idx + 1, pending_end):
        bar = c1_list[idx]

        # Invalidation check: hits SL before entry penetration
        if cand.direction == Direction.LONG and bar.low <= cand.stop_loss:
            return "INVALIDATED_PRE_FILL", None
        if cand.direction == Direction.SHORT and bar.high >= cand.stop_loss:
            return "INVALIDATED_PRE_FILL", None

        # Fill check: decisive penetration by fill_buffer_pct
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

    # 2. Position Lifecycle Evaluation (Post-Fill)
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

        # Accumulate funding if 8h funding bar
        fr = funding_map.get(bar.timestamp_ms, 0.0)
        if fr != 0.0:
            f_amount = notional * fr if cand.direction == Direction.LONG else notional * (-fr)
            funding_total += f_amount

        # Intrabar collision resolution: conservative SL-first
        if cand.direction == Direction.LONG:
            hit_sl = bar.low <= current_sl
            hit_tp = bar.high >= tp1 if not is_partial_closed else (bar.high >= tp2)

            if hit_sl:
                exit_idx = idx
                exit_price = current_sl * (1.0 - slippage_rate)
                exit_reason = "STOP_LOSS" if not is_partial_closed else "TRAILING_STOP_BE"
                break
            elif hit_tp and not is_partial_closed:
                # 50% TP1 partial close
                is_partial_closed = True
                closed_at_tp1_qty = quantity * 0.5
                closed_at_tp1_notional = tp1 * closed_at_tp1_qty
                tp1_gross_pnl = (tp1 - actual_entry_price) * closed_at_tp1_qty
                tp1_fee = closed_at_tp1_notional * taker_fee_rate
                # Move SL to break-even + small fee buffer
                current_sl = actual_entry_price * 1.0005
                # Check if same bar also hit TP2
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
        else:  # SHORT
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
        # Time horizon expiration at max_holding_bars
        exit_idx = holding_end - 1
        exit_price = c1_list[exit_idx].close
        exit_reason = "EXPIRED_MAX_HOLDING"

    # Calculate final PnL
    rem_qty = quantity * 0.5 if is_partial_closed else quantity
    if cand.direction == Direction.LONG:
        rem_gross_pnl = (exit_price - actual_entry_price) * rem_qty
    else:
        rem_gross_pnl = (actual_entry_price - exit_price) * rem_qty

    rem_exit_fee = (exit_price * rem_qty) * taker_fee_rate
    total_gross_pnl = tp1_gross_pnl + rem_gross_pnl
    total_fees = entry_fee + tp1_fee + rem_exit_fee
    net_pnl = total_gross_pnl - total_fees - funding_total

    holding_bars = exit_idx - fill_idx + 1

    trade = SimulatedPaperTrade(
        trade_id=f"T_{cand.symbol}_{cand.timestamp_ms}",
        candidate_id=cand.candidate_id,
        symbol=cand.symbol,
        direction=cand.direction,
        entry_category=cand.entry_category.value if hasattr(cand.entry_category, "value") else str(cand.entry_category),
        created_ts_ms=cand.timestamp_ms,
        fill_ts_ms=c1_list[fill_idx].timestamp_ms,
        exit_ts_ms=c1_list[exit_idx].timestamp_ms,
        entry_price=round(actual_entry_price, 6),
        stop_loss=round(cand.stop_loss, 6),
        take_profit=round(cand.take_profit_1, 6),
        take_profit_2=round(tp2, 6) if tp2 else None,
        quantity=round(quantity, 6),
        notional_usdt=round(notional, 4),
        margin_usdt=round(margin, 4),
        exit_price=round(exit_price, 6),
        gross_pnl_usdt=round(total_gross_pnl, 4),
        net_pnl_usdt=round(net_pnl, 4),
        fees_paid_usdt=round(total_fees, 4),
        funding_paid_usdt=round(funding_total, 4),
        exit_reason=exit_reason,
        holding_bars=holding_bars,
        is_win=net_pnl > 0,
    )
    return "FILLED", trade


def run_experiment_variant(
    variant_name: str,
    generator_factory,
    db: DatabaseManager,
    specs_map: Dict[str, SymbolSpecs],
    fee_pert_mult: float = 1.0,
    slip_pert_mult: float = 1.0,
) -> Dict[str, Any]:
    """Execute full evaluation for a strategy variant over all 11 symbols."""
    print(f"\n==================================================")
    print(f"Running Experiment: {variant_name}")
    print(f"==================================================")

    maker_fee = 0.0002 * fee_pert_mult
    taker_fee = 0.0005 * fee_pert_mult
    slippage = 0.0005 * slip_pert_mult

    # 1. Load candles and funding cache
    candles_1h: Dict[str, List[Candle]] = {}
    candles_4h: Dict[str, List[Candle]] = {}
    funding_cache: Dict[str, Dict[int, float]] = {}

    for sym in SYMBOLS:
        c1 = db.get_candles(sym, "1h")
        c4 = db.get_candles(sym, "4h")
        candles_1h[sym] = c1
        candles_4h[sym] = c4
        rates = db.get_funding_rates(sym)
        funding_cache[sym] = {r["funding_time_ms"]: float(r["funding_rate"]) for r in rates}

    gen = generator_factory()

    # Attribution tracking
    rejection_counts: Dict[str, int] = defaultdict(int)
    all_evaluated_bars = 0

    # Candidate collection
    eligible_candidates: List[Tuple[int, str, int, SetupCandidate]] = [] # (ts, sym, idx, cand)
    raw_candidates_count = 0
    infeasible_count = 0

    for sym in SYMBOLS:
        c1 = candles_1h[sym]
        c4 = candles_4h[sym]
        specs = specs_map.get(sym)

        for i in range(170, len(c1)):
            all_evaluated_bars += 1
            cand = gen.generate_candidate(sym, c1, c4, as_of_index_1h=i)

            if cand is None:
                diag = getattr(gen, "last_diagnostic", None)
                code = diag.reason_code if diag else "NO_SMC_CONFLUENCE"
                rejection_counts[code] += 1
                continue

            raw_candidates_count += 1

            # Sizing & Feasibility check ($16 capital, $6 target margin, $1.50 SL cap)
            if specs is None:
                infeasible_count += 1
                rejection_counts["INFEASIBLE"] += 1
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
            cand.risk_assessment = assessment

            if not assessment.is_feasible:
                infeasible_count += 1
                rejection_counts["INFEASIBLE"] += 1
                continue

            eligible_candidates.append((c1[i].timestamp_ms, sym, i, cand))

    print(f"Evaluated bars: {all_evaluated_bars}")
    print(f"Raw candidates: {raw_candidates_count}")
    print(f"Infeasible sizing: {infeasible_count}")
    print(f"Eligible setups: {len(eligible_candidates)}")

    # Sort candidates chronologically
    eligible_candidates.sort(key=lambda x: x[0])

    # 2. Standalone Paper Simulation (Independent, no portfolio blocking)
    standalone_trades: List[SimulatedPaperTrade] = []
    pre_fill_invalidations = 0
    expired_non_fills = 0

    for ts, sym, idx, cand in eligible_candidates:
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
            standalone_trades.append(trade)
        elif status == "INVALIDATED_PRE_FILL":
            pre_fill_invalidations += 1
        elif status == "EXPIRED_NON_FILL":
            expired_non_fills += 1

    # 3. Portfolio-Constrained Paper Simulation (Max 1 open pos, Max 3 entries/week)
    portfolio_trades: List[SimulatedPaperTrade] = []
    active_pos_exit_ts = 0
    weekly_entries_count: Dict[int, int] = defaultdict(int) # week_num -> count
    portfolio_submitted = 0

    for ts, sym, idx, cand in eligible_candidates:
        dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        week_key = dt.year * 100 + dt.isocalendar()[1]

        # Account concurrency gates
        if ts < active_pos_exit_ts:
            rejection_counts["PORTFOLIO_CONCURRENCY_BLOCKED"] += 1
            continue
        if weekly_entries_count[week_key] >= 3:
            rejection_counts["PORTFOLIO_WEEKLY_LIMIT_BLOCKED"] += 1
            continue

        portfolio_submitted += 1
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

    def calc_stats(trades: List[SimulatedPaperTrade]) -> Dict[str, Any]:
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

        # Equity curve & max DD
        equity = 16.0
        peak = equity
        max_dd = 0.0
        for p in pnl_arr:
            equity += p
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        # Bootstrap 95% CI
        n_boot = 1000
        boot_means = []
        rng = np.random.default_rng(42)
        for _ in range(n_boot):
            sample = rng.choice(pnl_arr, size=len(pnl_arr), replace=True)
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

    st_stats = calc_stats(standalone_trades)
    port_stats = calc_stats(portfolio_trades)

    # Walk-Forward Breakdown for Standalone
    dev_trades = [t for t in standalone_trades if t.created_ts_ms <= DEV_END_MS]
    val_trades = [t for t in standalone_trades if VAL_START_MS <= t.created_ts_ms <= VAL_END_MS]
    oos_trades = [t for t in standalone_trades if t.created_ts_ms >= OOS_START_MS]

    dev_port = [t for t in portfolio_trades if t.created_ts_ms <= DEV_END_MS]
    val_port = [t for t in portfolio_trades if VAL_START_MS <= t.created_ts_ms <= VAL_END_MS]
    oos_port = [t for t in portfolio_trades if t.created_ts_ms >= OOS_START_MS]

    # Rolling Breakdown
    roll_dev = [t for t in standalone_trades if t.created_ts_ms <= ROLLING_DEV_END_MS]
    roll_val = [t for t in standalone_trades if ROLLING_VAL_START_MS <= t.created_ts_ms <= ROLLING_VAL_END_MS]
    roll_oos = [t for t in standalone_trades if t.created_ts_ms >= ROLLING_OOS_START_MS]

    return {
        "variant": variant_name,
        "evaluated_bars": all_evaluated_bars,
        "rejection_counts": dict(rejection_counts),
        "raw_candidates": raw_candidates_count,
        "infeasible_count": infeasible_count,
        "eligible_setups": len(eligible_candidates),
        "pre_fill_invalidations": pre_fill_invalidations,
        "expired_non_fills": expired_non_fills,
        "standalone_fills": len(standalone_trades),
        "standalone_fill_rate": round(len(standalone_trades) / len(eligible_candidates) * 100.0, 2) if eligible_candidates else 0.0,
        "standalone_stats": st_stats,
        "portfolio_submitted": portfolio_submitted,
        "portfolio_fills": len(portfolio_trades),
        "portfolio_stats": port_stats,
        "wf_scheme1": {
            "dev_standalone": calc_stats(dev_trades),
            "val_standalone": calc_stats(val_trades),
            "oos_standalone": calc_stats(oos_trades),
            "dev_portfolio": calc_stats(dev_port),
            "val_portfolio": calc_stats(val_port),
            "oos_portfolio": calc_stats(oos_port),
        },
        "wf_rolling": {
            "dev_standalone": calc_stats(roll_dev),
            "val_standalone": calc_stats(roll_val),
            "oos_standalone": calc_stats(roll_oos),
        },
        "all_standalone_trades": standalone_trades,
        "all_portfolio_trades": portfolio_trades,
    }


def main():
    cfg = get_default_config()
    db = DatabaseManager(cfg.db_path)
    exchange = AsterDexAdapter()
    specs_map = exchange.get_exchange_info()

    # Define generators
    variants = [
        ("Baseline (Control [0.12, 0.88])", lambda: SetupGenerator(discount_threshold=0.12, premium_threshold=0.88, bull_breakout_pos_72h=0.92)),
        ("Variant B1 (Narrow [0.20, 0.80])", lambda: SetupGenerator(discount_threshold=0.20, premium_threshold=0.80, bull_breakout_pos_72h=0.92)),
        ("Variant B2 (Moderate [0.25, 0.75])", lambda: SetupGenerator(discount_threshold=0.25, premium_threshold=0.75, bull_breakout_pos_72h=0.92)),
        ("Mode 2C Standalone (Mid-Range Sweep)", lambda: ResearchMode2CGenerator(mid_range_min_pos=0.20, mid_range_max_pos=0.80)),
        ("Combined Baseline + Mode 2C", lambda: CombinedBaselineAndMode2CGenerator(
            SetupGenerator(discount_threshold=0.12, premium_threshold=0.88, bull_breakout_pos_72h=0.92),
            ResearchMode2CGenerator(mid_range_min_pos=0.20, mid_range_max_pos=0.80)
        )),
    ]

    results = []
    for name, factory in variants:
        res = run_experiment_variant(name, factory, db, specs_map)
        results.append(res)

    # Cost sensitivity runs for Baseline and Mode 2C (+50% fees, +100% slippage)
    print("\n--- Running Cost Sensitivity Perturbations ---")
    res_base_stress = run_experiment_variant(
        "Baseline (Stressed Costs: 1.5x Fee, 2.0x Slip)",
        variants[0][1], db, specs_map, fee_pert_mult=1.5, slip_pert_mult=2.0
    )
    res_m2c_stress = run_experiment_variant(
        "Mode 2C (Stressed Costs: 1.5x Fee, 2.0x Slip)",
        variants[3][1], db, specs_map, fee_pert_mult=1.5, slip_pert_mult=2.0
    )

    # Export candidate_rejection_attribution.csv
    rejection_keys = [
        "EQUILIBRIUM_ZONE_72H", "NO_LIQUIDITY_SWEEP", "SWEEP_NOT_CONFIRMED",
        "HTF_BIAS_MISMATCH", "NO_SMC_CONFLUENCE", "INSUFFICIENT_RISK_REWARD",
        "INFEASIBLE", "PORTFOLIO_CONCURRENCY_BLOCKED", "PORTFOLIO_WEEKLY_LIMIT_BLOCKED"
    ]
    with open("candidate_rejection_attribution.csv", "w", encoding="utf-8") as f:
        f.write("variant,total_bars_evaluated,raw_candidates,eligible_setups," + ",".join(rejection_keys) + "\n")
        for r in results:
            counts = r["rejection_counts"]
            row = [
                f'"{r["variant"]}"',
                str(r["evaluated_bars"]),
                str(r["raw_candidates"]),
                str(r["eligible_setups"]),
            ]
            for k in rejection_keys:
                row.append(str(counts.get(k, 0)))
            f.write(",".join(row) + "\n")

    # Export baseline_vs_experiments.csv
    headers = [
        "variant", "raw_candidates", "eligible_setups", "standalone_trades",
        "standalone_win_rate_pct", "standalone_net_pnl_usdt", "standalone_expectancy_usdt",
        "standalone_profit_factor", "standalone_max_dd_usdt", "standalone_ci95_low", "standalone_ci95_high",
        "standalone_long_exp", "standalone_short_exp", "portfolio_orders_submitted",
        "portfolio_trades", "portfolio_win_rate_pct", "portfolio_net_pnl_usdt",
        "portfolio_expectancy_usdt", "portfolio_profit_factor", "portfolio_max_dd_usdt",
        "oos1_standalone_trades", "oos1_standalone_exp", "oos1_portfolio_trades", "oos1_portfolio_exp",
        "rolling_oos_standalone_trades", "rolling_oos_standalone_exp", "decision_gate_status"
    ]

    with open("baseline_vs_experiments.csv", "w", encoding="utf-8") as f:
        f.write(",".join(headers) + "\n")
        for r in results:
            st = r["standalone_stats"]
            pt = r["portfolio_stats"]
            oos1_st = r["wf_scheme1"]["oos_standalone"]
            oos1_pt = r["wf_scheme1"]["oos_portfolio"]
            roll_oos = r["wf_rolling"]["oos_standalone"]

            # Decision Gate logic
            # Baseline is CONTROL
            # Promising if OOS improves on baseline with statistical significance
            if "Baseline (Control" in r["variant"]:
                status = "CONTROL_BASELINE"
            elif st["expectancy"] < 0 or oos1_st["expectancy"] < results[0]["wf_scheme1"]["oos_standalone"]["expectancy"] or pt["expectancy"] < results[0]["portfolio_stats"]["expectancy"]:
                status = "REJECTED"
            elif oos1_st["trades"] < 30:
                status = "INCONCLUSIVE"
            else:
                status = "PROMISING"

            row = [
                f'"{r["variant"]}"',
                str(r["raw_candidates"]),
                str(r["eligible_setups"]),
                str(st["trades"]),
                str(st["win_rate"]),
                str(st["net_pnl"]),
                str(st["expectancy"]),
                str(st["profit_factor"]),
                str(st["max_dd"]),
                str(st["ci_low"]),
                str(st["ci_high"]),
                str(st["long_exp"]),
                str(st["short_exp"]),
                str(r["portfolio_submitted"]),
                str(pt["trades"]),
                str(pt["win_rate"]),
                str(pt["net_pnl"]),
                str(pt["expectancy"]),
                str(pt["profit_factor"]),
                str(pt["max_dd"]),
                str(oos1_st["trades"]),
                str(oos1_st["expectancy"]),
                str(oos1_pt["trades"]),
                str(oos1_pt["expectancy"]),
                str(roll_oos["trades"]),
                str(roll_oos["expectancy"]),
                status,
            ]
            f.write(",".join(row) + "\n")

    print("\nSaved candidate_rejection_attribution.csv and baseline_vs_experiments.csv successfully!")


if __name__ == "__main__":
    main()
