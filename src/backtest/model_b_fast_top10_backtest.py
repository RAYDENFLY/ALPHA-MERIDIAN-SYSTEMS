"""
Model B Fast Scalper (15M / 1H) — Top 10 Pair Annual Historical Backtest Runner
================================================================================
Comprehensive, lookahead-free historical evaluation across Top 10 perpetual pairs:
BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, BNBUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, LTCUSDT.

Period: 2025-10-24 00:00:00 UTC to 2026-10-24 15:00:00 UTC (12 Months).
"""

import sys
import os
sys.path.insert(0, os.getcwd())
import math
import json
import csv
import sqlite3
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

TARGET_DB = "data/backtest_15m_top10.db"
OUTPUT_DIR = "backtest_results/model_b_fast_annual_top10"

TOP_10_PAIRS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "BNBUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "LTCUSDT",
]

# Baseline Strategy Parameters (from ModelBConfig & create_model_b_fast_config)
CONFIG = {
    "model_id": "MODEL_B_FAST",
    "cohort_version": "MODEL_B_FAST_V2",
    "trigger_timeframe": "15m",
    "trend_timeframe": "1h",
    "initial_capital_per_pair": 100.0,
    "portfolio_capital": 100.0,
    "portfolio_capital_alt": 1000.0,
    "target_trade_risk_usdt": 1.00,
    "max_isolated_margin_usdt": 15.00,
    "leverage": 10.0,
    "min_stop_distance_pct": 0.008,  # 0.80%
    "min_stop_atr_multiple": 1.25,
    "pending_order_ttl_bars": 8,     # 2.0 hours
    "maker_fee_rate": 0.0002,        # 0.02%
    "taker_fee_rate": 0.0005,        # 0.05%
    "adverse_slippage_rate": 0.0005, # 0.05%
    "max_open_positions_portfolio": 6,
    "max_open_per_direction": 3,
    "backtest_start_ts": 1761264000000,  # 2025-10-24 00:00:00 UTC
    "backtest_end_ts": 1792854000000,    # 2026-10-24 15:00:00 UTC
}


@dataclass
class CandleBar:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Position:
    trade_id: str
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_time_ms: int
    entry_price: float
    initial_sl: float
    current_sl: float
    initial_tp1: float
    current_tp: float
    tp2: Optional[float]
    quantity: float
    initial_quantity: float
    notional: float
    margin: float
    leverage: float
    entry_fee: float
    tp1_hit: bool = False
    tp1_hit_time_ms: Optional[int] = None
    partial_gross_pnl: float = 0.0
    partial_fee_paid: float = 0.0
    trailing_active: bool = False
    favorable_extreme: Optional[float] = None
    trailing_distance: Optional[float] = None
    breakeven_price: Optional[float] = None
    funding_paid: float = 0.0
    last_funding_time_ms: int = 0


@dataclass
class PendingOrder:
    order_id: str
    symbol: str
    direction: str
    created_time_ms: int
    expiry_time_ms: int
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: Optional[float]
    quantity: float
    notional: float
    margin: float
    leverage: float
    risk_usdt: float


@dataclass
class TradeResult:
    trade_id: str
    symbol: str
    direction: str
    entry_time_ms: int
    exit_time_ms: int
    entry_time_utc: str
    exit_time_utc: str
    entry_price: float
    exit_price: float
    quantity: float
    notional: float
    gross_pnl: float
    net_pnl: float
    fees_paid: float
    funding_paid: float
    exit_reason: str
    duration_hours: float
    return_pct: float
    realized_rr: float


def compute_running_ema(values: List[float], period: int) -> List[Optional[float]]:
    n = len(values)
    result: List[Optional[float]] = [None] * n
    if n < period:
        return result
    k = 2.0 / (period + 1)
    running = sum(values[:period]) / period
    result[period - 1] = running
    for i in range(period, n):
        running = values[i] * k + running * (1.0 - k)
        result[i] = running
    return result


def find_recent_swings_slice(candles: List[CandleBar], left: int = 3, right: int = 2):
    n = len(candles)
    highs, lows = [], []
    if n < left + right + 1:
        return highs, lows
    for i in range(left, n - right):
        c = candles[i]
        is_h = True
        for j in range(i - left, i + right + 1):
            if j != i and candles[j].high >= c.high:
                is_h = False
                break
        if is_h:
            highs.append(c.high)

        is_l = True
        for j in range(i - left, i + right + 1):
            if j != i and candles[j].low <= c.low:
                is_l = False
                break
        if is_l:
            lows.append(c.low)
    return highs, lows


class DataLoader:
    """Preloads and indexes all candles and funding rates."""

    def __init__(self, db_path: str = TARGET_DB):
        self.db_path = db_path
        self.candles_15m: Dict[str, List[CandleBar]] = {}
        self.candles_1h: Dict[str, List[CandleBar]] = {}
        self.funding_rates: Dict[str, List[Tuple[int, float]]] = {}
        self.ema20_1h: Dict[str, List[Optional[float]]] = {}
        self.ema50_1h: Dict[str, List[Optional[float]]] = {}
        self.h1_close_times: Dict[str, List[int]] = {}
        self.atr_15m: Dict[str, List[float]] = {}
        self.load_all()

    def load_all(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        for sym in TOP_10_PAIRS:
            # 15m candles
            cur.execute("""
                SELECT timestamp_ms, open, high, low, close, volume
                FROM raw_candles
                WHERE symbol=? AND timeframe='15m'
                ORDER BY timestamp_ms ASC
            """, (sym,))
            c15 = [CandleBar(*r) for r in cur.fetchall()]
            self.candles_15m[sym] = c15

            # Pre-compute 15m ATR
            trs = [0.0] * len(c15)
            for i in range(1, len(c15)):
                trs[i] = max(c15[i].high - c15[i].low, abs(c15[i].high - c15[i - 1].close), abs(c15[i].low - c15[i - 1].close))
            atrs = [0.0] * len(c15)
            if len(c15) >= 15:
                running_tr = sum(trs[1:15])
                atrs[14] = running_tr / 14.0
                for i in range(15, len(c15)):
                    running_tr += trs[i] - trs[i - 14]
                    atrs[i] = running_tr / 14.0
            self.atr_15m[sym] = atrs

            # 1h candles
            cur.execute("""
                SELECT timestamp_ms, open, high, low, close, volume
                FROM raw_candles
                WHERE symbol=? AND timeframe='1h'
                ORDER BY timestamp_ms ASC
            """, (sym,))
            c1 = [CandleBar(*r) for r in cur.fetchall()]
            self.candles_1h[sym] = c1

            h1_closes = [c.close for c in c1]
            self.h1_close_times[sym] = [c.timestamp_ms + 3600000 - 1 for c in c1]
            self.ema20_1h[sym] = compute_running_ema(h1_closes, 20)
            self.ema50_1h[sym] = compute_running_ema(h1_closes, 50)

            # Funding rates
            cur.execute("""
                SELECT funding_time_ms, funding_rate
                FROM funding_rates
                WHERE symbol=?
                ORDER BY funding_time_ms ASC
            """, (sym,))
            self.funding_rates[sym] = sorted(cur.fetchall(), key=lambda x: x[0])

        conn.close()


def simulate_bar(
    c15: CandleBar,
    open_pos: Optional[Position],
    pending_order: Optional[PendingOrder],
    funding_list: List[Tuple[int, float]],
    fund_ptr: int,
    order_counter: int,
    trade_counter: int,
    symbol: str,
    maker_fee: float,
    taker_fee: float,
    slippage: float,
) -> Tuple[Optional[Position], Optional[PendingOrder], Optional[TradeResult], int, int, int]:
    """Execute order and position state transitions for a single bar."""
    trade_res: Optional[TradeResult] = None
    num_funding = len(funding_list)

    # 1. Settle funding if in position
    if open_pos:
        while fund_ptr < num_funding and funding_list[fund_ptr][0] <= c15.timestamp_ms:
            fts, frate = funding_list[fund_ptr]
            if fts > open_pos.last_funding_time_ms:
                payment = (open_pos.notional * frate) if open_pos.direction == "LONG" else (-open_pos.notional * frate)
                open_pos.funding_paid += payment
                open_pos.last_funding_time_ms = fts
            fund_ptr += 1
    else:
        while fund_ptr < num_funding and funding_list[fund_ptr][0] <= c15.timestamp_ms:
            fund_ptr += 1

    # 2. Evaluate open position exits
    if open_pos:
        hit_tp = False
        hit_sl = False
        exit_price = 0.0
        exit_reason = ""

        if open_pos.direction == "LONG":
            hit_tp = c15.high >= open_pos.current_tp
            hit_sl = c15.low <= open_pos.current_sl

            if hit_tp and hit_sl:
                exit_price = open_pos.current_sl * (1.0 - slippage)
                exit_reason = "SL_COLLISION"
            elif hit_sl:
                exit_price = open_pos.current_sl * (1.0 - slippage)
                if open_pos.tp1_hit:
                    be_ref = open_pos.breakeven_price or open_pos.entry_price
                    exit_reason = "TRAILING_STOP" if open_pos.current_sl > be_ref + 1e-6 else "BREAKEVEN_STOP"
                else:
                    exit_reason = "STOP_LOSS"
            elif hit_tp:
                if not open_pos.tp1_hit and open_pos.tp2 is not None:
                    tp1_price = open_pos.initial_tp1
                    closed_qty = open_pos.quantity * 0.5
                    remain_qty = open_pos.quantity - closed_qty
                    partial_gross = closed_qty * (tp1_price - open_pos.entry_price)
                    partial_fee = (closed_qty * tp1_price) * taker_fee
                    open_pos.partial_gross_pnl += partial_gross
                    open_pos.partial_fee_paid += partial_fee
                    open_pos.tp1_hit = True
                    open_pos.tp1_hit_time_ms = c15.timestamp_ms
                    open_pos.quantity = remain_qty
                    open_pos.notional = remain_qty * open_pos.entry_price

                    be_price = open_pos.entry_price * (1.0 + maker_fee + taker_fee + slippage)
                    open_pos.breakeven_price = be_price
                    open_pos.current_sl = max(open_pos.current_sl, be_price)
                    open_pos.current_tp = open_pos.tp2
                    open_pos.trailing_active = True
                    init_r = abs(open_pos.entry_price - open_pos.initial_sl)
                    open_pos.trailing_distance = init_r * 1.0
                    open_pos.favorable_extreme = max(tp1_price, c15.high)

                    if c15.high >= open_pos.tp2:
                        exit_price = open_pos.tp2
                        exit_reason = "TAKE_PROFIT_2"
                else:
                    exit_price = open_pos.current_tp
                    exit_reason = "TAKE_PROFIT_2" if open_pos.tp1_hit else "TAKE_PROFIT"

        elif open_pos.direction == "SHORT":
            hit_tp = c15.low <= open_pos.current_tp
            hit_sl = c15.high >= open_pos.current_sl

            if hit_tp and hit_sl:
                exit_price = open_pos.current_sl * (1.0 + slippage)
                exit_reason = "SL_COLLISION"
            elif hit_sl:
                exit_price = open_pos.current_sl * (1.0 + slippage)
                if open_pos.tp1_hit:
                    be_ref = open_pos.breakeven_price or open_pos.entry_price
                    exit_reason = "TRAILING_STOP" if open_pos.current_sl < be_ref - 1e-6 else "BREAKEVEN_STOP"
                else:
                    exit_reason = "STOP_LOSS"
            elif hit_tp:
                if not open_pos.tp1_hit and open_pos.tp2 is not None:
                    tp1_price = open_pos.initial_tp1
                    closed_qty = open_pos.quantity * 0.5
                    remain_qty = open_pos.quantity - closed_qty
                    partial_gross = closed_qty * (open_pos.entry_price - tp1_price)
                    partial_fee = (closed_qty * tp1_price) * taker_fee
                    open_pos.partial_gross_pnl += partial_gross
                    open_pos.partial_fee_paid += partial_fee
                    open_pos.tp1_hit = True
                    open_pos.tp1_hit_time_ms = c15.timestamp_ms
                    open_pos.quantity = remain_qty
                    open_pos.notional = remain_qty * open_pos.entry_price

                    be_price = open_pos.entry_price * (1.0 - (maker_fee + taker_fee + slippage))
                    open_pos.breakeven_price = be_price
                    open_pos.current_sl = min(open_pos.current_sl, be_price)
                    open_pos.current_tp = open_pos.tp2
                    open_pos.trailing_active = True
                    init_r = abs(open_pos.initial_sl - open_pos.entry_price)
                    open_pos.trailing_distance = init_r * 1.0
                    open_pos.favorable_extreme = min(tp1_price, c15.low)

                    if c15.low <= open_pos.tp2:
                        exit_price = open_pos.tp2
                        exit_reason = "TAKE_PROFIT_2"
                else:
                    exit_price = open_pos.current_tp
                    exit_reason = "TAKE_PROFIT_2" if open_pos.tp1_hit else "TAKE_PROFIT"

        # Trailing stop update if open
        if open_pos and exit_reason == "" and open_pos.trailing_active:
            if open_pos.direction == "LONG":
                open_pos.favorable_extreme = max(open_pos.favorable_extreme, c15.high)
                trail_cand = open_pos.favorable_extreme - open_pos.trailing_distance
                if trail_cand > open_pos.current_sl:
                    open_pos.current_sl = max(open_pos.current_sl, open_pos.breakeven_price, trail_cand)
            else:
                open_pos.favorable_extreme = min(open_pos.favorable_extreme, c15.low)
                trail_cand = open_pos.favorable_extreme + open_pos.trailing_distance
                if trail_cand < open_pos.current_sl:
                    open_pos.current_sl = min(open_pos.current_sl, open_pos.breakeven_price, trail_cand)

        # Final exit settlement
        if exit_reason != "":
            final_exit_fee = (open_pos.quantity * exit_price) * taker_fee
            total_fees = open_pos.entry_fee + open_pos.partial_fee_paid + final_exit_fee

            if open_pos.direction == "LONG":
                final_gross = open_pos.quantity * (exit_price - open_pos.entry_price)
            else:
                final_gross = open_pos.quantity * (open_pos.entry_price - exit_price)

            total_gross = open_pos.partial_gross_pnl + final_gross
            total_net = total_gross - total_fees - open_pos.funding_paid

            duration_hrs = (c15.timestamp_ms - open_pos.entry_time_ms) / 3600000.0
            initial_r = abs(open_pos.entry_price - open_pos.initial_sl)
            eff_exit_px = (
                ((open_pos.initial_quantity - open_pos.quantity) * open_pos.initial_tp1 + open_pos.quantity * exit_price) / open_pos.initial_quantity
                if open_pos.tp1_hit else exit_price
            )
            gain = (eff_exit_px - open_pos.entry_price) if open_pos.direction == "LONG" else (open_pos.entry_price - eff_exit_px)
            realized_rr = gain / initial_r if initial_r > 0 else 0.0

            dt_entry = datetime.fromtimestamp(open_pos.entry_time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            dt_exit = datetime.fromtimestamp(c15.timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

            trade_res = TradeResult(
                trade_id=open_pos.trade_id,
                symbol=symbol,
                direction=open_pos.direction,
                entry_time_ms=open_pos.entry_time_ms,
                exit_time_ms=c15.timestamp_ms,
                entry_time_utc=dt_entry,
                exit_time_utc=dt_exit,
                entry_price=open_pos.entry_price,
                exit_price=round(eff_exit_px, 6),
                quantity=open_pos.initial_quantity,
                notional=round(open_pos.initial_quantity * open_pos.entry_price, 2),
                gross_pnl=round(total_gross, 4),
                net_pnl=round(total_net, 4),
                fees_paid=round(total_fees, 4),
                funding_paid=round(open_pos.funding_paid, 4),
                exit_reason=exit_reason,
                duration_hours=round(duration_hrs, 2),
                return_pct=round(total_net / 100.0 * 100.0, 4),
                realized_rr=round(realized_rr, 2),
            )
            open_pos = None

    # 3. Evaluate pending order fill or expiration
    if pending_order and not open_pos:
        if c15.timestamp_ms >= pending_order.expiry_time_ms:
            pending_order = None
        else:
            filled = False
            if pending_order.direction == "LONG" and c15.low <= pending_order.entry_price:
                filled = True
            elif pending_order.direction == "SHORT" and c15.high >= pending_order.entry_price:
                filled = True

            if filled:
                entry_fee = pending_order.notional * maker_fee
                open_pos = Position(
                    trade_id=f"PTRD_{symbol}_{trade_counter}",
                    symbol=symbol,
                    direction=pending_order.direction,
                    entry_time_ms=c15.timestamp_ms,
                    entry_price=pending_order.entry_price,
                    initial_sl=pending_order.stop_loss,
                    current_sl=pending_order.stop_loss,
                    initial_tp1=pending_order.tp1,
                    current_tp=pending_order.tp1,
                    tp2=pending_order.tp2,
                    quantity=pending_order.quantity,
                    initial_quantity=pending_order.quantity,
                    notional=pending_order.notional,
                    margin=pending_order.margin,
                    leverage=pending_order.leverage,
                    entry_fee=entry_fee,
                    last_funding_time_ms=c15.timestamp_ms,
                )
                trade_counter += 1
                pending_order = None

    return open_pos, pending_order, trade_res, fund_ptr, order_counter, trade_counter


def evaluate_signal(
    c15: CandleBar,
    recent_15m: List[CandleBar],
    atr_val: float,
    h1_bias: str,
    symbol: str,
    order_counter: int,
    cfg: Dict[str, Any],
) -> Optional[PendingOrder]:
    """Evaluate confirmed candle body close and swing sweep-reclaim."""
    last_closed = c15
    prev_c = recent_15m[-2] if len(recent_15m) >= 2 else last_closed
    sw_highs, sw_lows = find_recent_swings_slice(recent_15m[:-1], left=3, right=2)

    target_risk = cfg["target_trade_risk_usdt"]
    stop_floor_pct = cfg["min_stop_distance_pct"]
    stop_atr_mult = cfg["min_stop_atr_multiple"]
    pending_ttl_bars = cfg["pending_order_ttl_bars"]
    max_margin = cfg["max_isolated_margin_usdt"]
    leverage = cfg["leverage"]

    # Long setup
    if sw_lows and h1_bias != "BEARISH":
        key_low = sw_lows[-1]
        swept = (last_closed.low < key_low) or (prev_c.low < key_low)
        reclaimed = swept and (last_closed.close > key_low)
        confirmed_green = last_closed.close > last_closed.open

        if reclaimed and confirmed_green:
            entry_px = last_closed.close
            raw_sl = min(last_closed.low, prev_c.low) * 0.999
            min_stop_dist = max(entry_px * stop_floor_pct, atr_val * stop_atr_mult)
            sl_px = min(raw_sl, entry_px - min_stop_dist)
            risk_dist = entry_px - sl_px

            if risk_dist > 0:
                tp1 = round(entry_px + (risk_dist * 1.5), 8)
                high_targets = [p for p in sw_highs if p > tp1]
                tp2 = round(high_targets[0] if high_targets else entry_px + (risk_dist * 2.5), 8)

                raw_qty = target_risk / risk_dist
                notional = raw_qty * entry_px
                if notional > max_margin * leverage:
                    notional = max_margin * leverage
                    raw_qty = notional / entry_px
                req_margin = notional / leverage

                if notional >= 5.0 and req_margin <= max_margin:
                    return PendingOrder(
                        order_id=f"PORD_{symbol}_{order_counter}",
                        symbol=symbol,
                        direction="LONG",
                        created_time_ms=last_closed.timestamp_ms,
                        expiry_time_ms=last_closed.timestamp_ms + (pending_ttl_bars * 15 * 60 * 1000),
                        entry_price=entry_px,
                        stop_loss=sl_px,
                        tp1=tp1,
                        tp2=tp2,
                        quantity=raw_qty,
                        notional=notional,
                        margin=req_margin,
                        leverage=leverage,
                        risk_usdt=target_risk,
                    )

    # Short setup
    elif sw_highs and h1_bias != "BULLISH":
        key_high = sw_highs[-1]
        swept = (last_closed.high > key_high) or (prev_c.high > key_high)
        reclaimed = swept and (last_closed.close < key_high)
        confirmed_red = last_closed.close < last_closed.open

        if reclaimed and confirmed_red:
            entry_px = last_closed.close
            raw_sl = max(last_closed.high, prev_c.high) * 1.001
            min_stop_dist = max(entry_px * stop_floor_pct, atr_val * stop_atr_mult)
            sl_px = max(raw_sl, entry_px + min_stop_dist)
            risk_dist = sl_px - entry_px

            if risk_dist > 0:
                tp1 = round(entry_px - (risk_dist * 1.5), 8)
                low_targets = [p for p in sw_lows if p < tp1]
                tp2 = round(low_targets[0] if low_targets else entry_px - (risk_dist * 2.5), 8)

                raw_qty = target_risk / risk_dist
                notional = raw_qty * entry_px
                if notional > max_margin * leverage:
                    notional = max_margin * leverage
                    raw_qty = notional / entry_px
                req_margin = notional / leverage

                if notional >= 5.0 and req_margin <= max_margin:
                    return PendingOrder(
                        order_id=f"PORD_{symbol}_{order_counter}",
                        symbol=symbol,
                        direction="SHORT",
                        created_time_ms=last_closed.timestamp_ms,
                        expiry_time_ms=last_closed.timestamp_ms + (pending_ttl_bars * 15 * 60 * 1000),
                        entry_price=entry_px,
                        stop_loss=sl_px,
                        tp1=tp1,
                        tp2=tp2,
                        quantity=raw_qty,
                        notional=notional,
                        margin=req_margin,
                        leverage=leverage,
                        risk_usdt=target_risk,
                    )
    return None


def calculate_metrics(
    trades: List[TradeResult],
    equity_curve: List[Tuple[int, float]],
    initial_equity: float,
) -> Dict[str, Any]:
    """Compute standard quantitative backtest metrics."""
    total_trades = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0

    gross_profit = sum(t.gross_pnl for t in wins)
    gross_loss = abs(sum(t.gross_pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

    total_gross = sum(t.gross_pnl for t in trades)
    total_net = sum(t.net_pnl for t in trades)
    total_fees = sum(t.fees_paid for t in trades)
    total_funding = sum(t.funding_paid for t in trades)

    final_equity = initial_equity + total_net
    net_pnl_pct = (total_net / initial_equity) * 100.0

    avg_trade_pnl = (total_net / total_trades) if total_trades > 0 else 0.0

    long_trades = [t for t in trades if t.direction == "LONG"]
    short_trades = [t for t in trades if t.direction == "SHORT"]

    long_net = sum(t.net_pnl for t in long_trades)
    short_net = sum(t.net_pnl for t in short_trades)
    long_wr = (len([t for t in long_trades if t.net_pnl > 0]) / len(long_trades) * 100.0) if long_trades else 0.0
    short_wr = (len([t for t in short_trades if t.net_pnl > 0]) / len(short_trades) * 100.0) if short_trades else 0.0

    # Max Drawdown
    peak = initial_equity
    max_dd = 0.0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio approximation on daily returns
    daily_pnl = {}
    for t in trades:
        day_str = datetime.fromtimestamp(t.exit_time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        daily_pnl[day_str] = daily_pnl.get(day_str, 0.0) + t.net_pnl
    daily_returns = [pnl / initial_equity for pnl in daily_pnl.values()]
    if len(daily_returns) > 1:
        mean_ret = sum(daily_returns) / len(daily_returns)
        std_ret = math.sqrt(sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1))
        sharpe = (mean_ret / std_ret * math.sqrt(365)) if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    # Exit reason breakdown
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    return {
        "initial_capital": initial_equity,
        "final_equity": round(final_equity, 2),
        "gross_pnl": round(total_gross, 2),
        "net_pnl": round(total_net, 2),
        "net_pnl_pct": round(net_pnl_pct, 2),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "avg_trade_pnl": round(avg_trade_pnl, 4),
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_net_pnl": round(long_net, 2),
        "short_net_pnl": round(short_net, 2),
        "long_win_rate": round(long_wr, 2),
        "short_win_rate": round(short_wr, 2),
        "total_fees": round(total_fees, 2),
        "total_funding": round(total_funding, 2),
        "sharpe_ratio": round(sharpe, 2),
        "exit_reasons": reasons,
    }


def run_full_annual_backtest():
    """Run both individual pair backtests and combined portfolio backtest."""
    t0 = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    loader = DataLoader(TARGET_DB)
    print(f"Loaded all data in {time.time() - t0:.2f}s")

    # 1. RUN INDEPENDENT PER-PAIR BACKTESTS ($100 per pair)
    per_pair_results = {}
    per_pair_trades: Dict[str, List[TradeResult]] = {}
    per_pair_equity_curves: Dict[str, List[Tuple[int, float]]] = {}

    print("\n" + "=" * 80)
    print("PHASE 1: INDEPENDENT TOP 10 PER-PAIR ANNUAL BACKTESTS ($100 CAPITAL EACH)")
    print("=" * 80)

    start_eval_ts = CONFIG["backtest_start_ts"]
    end_eval_ts = CONFIG["backtest_end_ts"]

    for sym in TOP_10_PAIRS:
        c15_list = loader.candles_15m[sym]
        c1_list = loader.candles_1h[sym]
        fund_list = loader.funding_rates[sym]
        e20_1h = loader.ema20_1h[sym]
        e50_1h = loader.ema50_1h[sym]
        h1_times = loader.h1_close_times[sym]
        atr_vals = loader.atr_15m[sym]

        equity = CONFIG["initial_capital_per_pair"]
        open_pos: Optional[Position] = None
        pending_order: Optional[PendingOrder] = None
        trade_history: List[TradeResult] = []
        equity_curve: List[Tuple[int, float]] = []

        h1_ptr = 0
        fund_ptr = 0
        order_counter = 0
        trade_counter = 0

        for idx, c15 in enumerate(c15_list):
            c15_close_ms = c15.timestamp_ms + 900000 - 1

            while h1_ptr + 1 < len(c1_list) and h1_times[h1_ptr + 1] <= c15_close_ms:
                h1_ptr += 1

            # Simulate bar
            open_pos, pending_order, trade_res, fund_ptr, order_counter, trade_counter = simulate_bar(
                c15=c15,
                open_pos=open_pos,
                pending_order=pending_order,
                funding_list=fund_list,
                fund_ptr=fund_ptr,
                order_counter=order_counter,
                trade_counter=trade_counter,
                symbol=sym,
                maker_fee=CONFIG["maker_fee_rate"],
                taker_fee=CONFIG["taker_fee_rate"],
                slippage=CONFIG["adverse_slippage_rate"],
            )

            if trade_res:
                trade_history.append(trade_res)
                equity += trade_res.net_pnl

            # Signal evaluation on close
            if c15_close_ms >= start_eval_ts and c15_close_ms <= end_eval_ts and not open_pos and not pending_order:
                if idx >= 60:
                    h1_bias = "NEUTRAL"
                    if h1_ptr >= 49:
                        c1h = c1_list[h1_ptr]
                        e20 = e20_1h[h1_ptr]
                        e50 = e50_1h[h1_ptr]
                        if e20 is not None and e50 is not None:
                            if c1h.close > e20 and e20 >= e50:
                                h1_bias = "BULLISH"
                            elif c1h.close < e20 and e20 <= e50:
                                h1_bias = "BEARISH"

                    order_counter += 1
                    pending_order = evaluate_signal(
                        c15=c15,
                        recent_15m=c15_list[idx - 60 : idx],
                        atr_val=atr_vals[idx],
                        h1_bias=h1_bias,
                        symbol=sym,
                        order_counter=order_counter,
                        cfg=CONFIG,
                    )

            equity_curve.append((c15.timestamp_ms, equity))

        metrics = calculate_metrics(trade_history, equity_curve, CONFIG["initial_capital_per_pair"])
        per_pair_results[sym] = metrics
        per_pair_trades[sym] = trade_history
        per_pair_equity_curves[sym] = equity_curve

        print(f"[{sym:8s}] Net PnL: ${metrics['net_pnl']:+7.2f} ({metrics['net_pnl_pct']:+6.2f}%) | "
              f"Gross: ${metrics['gross_pnl']:+7.2f} | Fees: ${metrics['total_fees']:5.2f} | "
              f"Trades: {metrics['total_trades']:4d} | WinRate: {metrics['win_rate']:5.2f}% | "
              f"PF: {metrics['profit_factor']:4.2f} | MaxDD: {metrics['max_drawdown_pct']:5.2f}%")

    # 2. RUN COMBINED PORTFOLIO SIMULATION (Synchronized 15m bar simulation across all 10 pairs)
    print("\n" + "=" * 80)
    print("PHASE 2: COMBINED PORTFOLIO SIMULATION (MAX 6 POSITIONS, SHARED CAPITAL)")
    print("=" * 80)

    # All pairs share the exact same 35,485 15m timestamps
    sample_sym = TOP_10_PAIRS[0]
    all_timestamps = [c.timestamp_ms for c in loader.candles_15m[sample_sym]]

    # Map timestamps to candle indices per symbol
    c15_by_sym_map = {sym: {c.timestamp_ms: c for c in loader.candles_15m[sym]} for sym in TOP_10_PAIRS}
    c15_idx_map = {sym: {c.timestamp_ms: i for i, c in enumerate(loader.candles_15m[sym])} for sym in TOP_10_PAIRS}

    portfolio_capital = CONFIG["portfolio_capital"]
    port_equity = portfolio_capital
    open_positions: Dict[str, Position] = {}
    pending_orders: Dict[str, PendingOrder] = {}
    port_trades: List[TradeResult] = []
    port_equity_curve: List[Tuple[int, float]] = []
    simultaneous_positions_history: List[int] = []

    h1_ptrs = {sym: 0 for sym in TOP_10_PAIRS}
    fund_ptrs = {sym: 0 for sym in TOP_10_PAIRS}
    order_counters = {sym: 0 for sym in TOP_10_PAIRS}
    trade_counters = {sym: 0 for sym in TOP_10_PAIRS}

    for ts in all_timestamps:
        c15_close_ms = ts + 900000 - 1

        # A. Evaluate open position exits and pending fills across all 10 symbols
        for sym in TOP_10_PAIRS:
            c15 = c15_by_sym_map[sym].get(ts)
            if not c15:
                continue

            c1_list = loader.candles_1h[sym]
            h1_times = loader.h1_close_times[sym]
            while h1_ptrs[sym] + 1 < len(c1_list) and h1_times[h1_ptrs[sym] + 1] <= c15_close_ms:
                h1_ptrs[sym] += 1

            cur_pos = open_positions.get(sym)
            cur_order = pending_orders.get(sym)

            new_pos, new_order, trade_res, fund_ptrs[sym], order_counters[sym], trade_counters[sym] = simulate_bar(
                c15=c15,
                open_pos=cur_pos,
                pending_order=cur_order,
                funding_list=loader.funding_rates[sym],
                fund_ptr=fund_ptrs[sym],
                order_counter=order_counters[sym],
                trade_counter=trade_counters[sym],
                symbol=sym,
                maker_fee=CONFIG["maker_fee_rate"],
                taker_fee=CONFIG["taker_fee_rate"],
                slippage=CONFIG["adverse_slippage_rate"],
            )

            if new_pos:
                open_positions[sym] = new_pos
            elif sym in open_positions:
                del open_positions[sym]

            if new_order:
                pending_orders[sym] = new_order
            elif sym in pending_orders:
                del pending_orders[sym]

            if trade_res:
                port_trades.append(trade_res)
                port_equity += trade_res.net_pnl

        # B. Generate new candidate orders if portfolio constraints allow
        if c15_close_ms >= start_eval_ts and c15_close_ms <= end_eval_ts:
            total_open = len(open_positions) + len(pending_orders)
            long_count = sum(1 for p in open_positions.values() if p.direction == "LONG") + sum(1 for o in pending_orders.values() if o.direction == "LONG")
            short_count = sum(1 for p in open_positions.values() if p.direction == "SHORT") + sum(1 for o in pending_orders.values() if o.direction == "SHORT")

            if total_open < CONFIG["max_open_positions_portfolio"]:
                for sym in TOP_10_PAIRS:
                    if sym in open_positions or sym in pending_orders:
                        continue
                    if len(open_positions) + len(pending_orders) >= CONFIG["max_open_positions_portfolio"]:
                        break

                    c15 = c15_by_sym_map[sym].get(ts)
                    if not c15:
                        continue
                    idx = c15_idx_map[sym][ts]
                    if idx < 60:
                        continue

                    h1_bias = "NEUTRAL"
                    h1_ptr = h1_ptrs[sym]
                    if h1_ptr >= 49:
                        c1h = loader.candles_1h[sym][h1_ptr]
                        e20 = loader.ema20_1h[sym][h1_ptr]
                        e50 = loader.ema50_1h[sym][h1_ptr]
                        if e20 is not None and e50 is not None:
                            if c1h.close > e20 and e20 >= e50:
                                h1_bias = "BULLISH"
                            elif c1h.close < e20 and e20 <= e50:
                                h1_bias = "BEARISH"

                    order_counters[sym] += 1
                    cand_order = evaluate_signal(
                        c15=c15,
                        recent_15m=loader.candles_15m[sym][idx - 60 : idx],
                        atr_val=loader.atr_15m[sym][idx],
                        h1_bias=h1_bias,
                        symbol=sym,
                        order_counter=order_counters[sym],
                        cfg=CONFIG,
                    )

                    if cand_order:
                        # Enforce directional cap (max 3 per direction)
                        if cand_order.direction == "LONG" and long_count >= CONFIG["max_open_per_direction"]:
                            continue
                        if cand_order.direction == "SHORT" and short_count >= CONFIG["max_open_per_direction"]:
                            continue

                        pending_orders[sym] = cand_order
                        if cand_order.direction == "LONG":
                            long_count += 1
                        else:
                            short_count += 1

        port_equity_curve.append((ts, port_equity))
        simultaneous_positions_history.append(len(open_positions))

    port_metrics = calculate_metrics(port_trades, port_equity_curve, CONFIG["portfolio_capital"])
    port_metrics["avg_simultaneous_positions"] = round(sum(simultaneous_positions_history) / len(simultaneous_positions_history), 2)
    port_metrics["max_simultaneous_positions"] = max(simultaneous_positions_history)

    print(f"\n[COMBINED PORTFOLIO ($100.00 Base)]")
    print(f"Total Completed Trades: {port_metrics['total_trades']}")
    print(f"Win Rate: {port_metrics['win_rate']:.2f}%")
    print(f"Gross PnL: ${port_metrics['gross_pnl']:+.2f}")
    print(f"Total Fees: ${port_metrics['total_fees']:.2f}")
    print(f"Total Funding: ${port_metrics['total_funding']:.2f}")
    print(f"Net PnL: ${port_metrics['net_pnl']:+.2f} ({port_metrics['net_pnl_pct']:+.2f}%)")
    print(f"Final Equity: ${port_metrics['final_equity']:.2f}")
    print(f"Profit Factor: {port_metrics['profit_factor']:.2f}")
    print(f"Max Drawdown: {port_metrics['max_drawdown_pct']:.2f}%")
    print(f"Max Simultaneous Positions: {port_metrics['max_simultaneous_positions']} (Avg: {port_metrics['avg_simultaneous_positions']})")

    # 3. MONTHLY & QUARTERLY BREAKDOWN
    print("\n" + "=" * 80)
    print("PHASE 3: MONTHLY & QUARTERLY BREAKDOWNS")
    print("=" * 80)

    monthly_summary = {}
    quarterly_summary = {}

    for t in port_trades:
        dt = datetime.fromtimestamp(t.exit_time_ms / 1000, tz=timezone.utc)
        m_key = dt.strftime("%Y-%m")
        q_key = f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"

        if m_key not in monthly_summary:
            monthly_summary[m_key] = {"trades": 0, "wins": 0, "gross_pnl": 0.0, "net_pnl": 0.0, "fees": 0.0}
        monthly_summary[m_key]["trades"] += 1
        if t.net_pnl > 0:
            monthly_summary[m_key]["wins"] += 1
        monthly_summary[m_key]["gross_pnl"] += t.gross_pnl
        monthly_summary[m_key]["net_pnl"] += t.net_pnl
        monthly_summary[m_key]["fees"] += t.fees_paid

        if q_key not in quarterly_summary:
            quarterly_summary[q_key] = {"trades": 0, "wins": 0, "gross_pnl": 0.0, "net_pnl": 0.0, "fees": 0.0}
        quarterly_summary[q_key]["trades"] += 1
        if t.net_pnl > 0:
            quarterly_summary[q_key]["wins"] += 1
        quarterly_summary[q_key]["gross_pnl"] += t.gross_pnl
        quarterly_summary[q_key]["net_pnl"] += t.net_pnl
        quarterly_summary[q_key]["fees"] += t.fees_paid

    for m in sorted(monthly_summary.keys()):
        d = monthly_summary[m]
        wr = (d["wins"] / d["trades"] * 100.0) if d["trades"] > 0 else 0.0
        print(f"Month {m}: Trades: {d['trades']:3d} | WinRate: {wr:5.2f}% | Gross: ${d['gross_pnl']:+6.2f} | Fees: ${d['fees']:5.2f} | Net: ${d['net_pnl']:+6.2f}")

    # 4. ROBUSTNESS & SENSITIVITY TESTING
    print("\n" + "=" * 80)
    print("PHASE 4: ROBUSTNESS & COST FRICTION SENSITIVITY ANALYSIS")
    print("=" * 80)

    # Sensitivity variations on combined portfolio trades:
    # 1. Zero Costs (Gross Alpha)
    gross_total = sum(t.gross_pnl for t in port_trades)
    # 2. Baseline
    base_net = sum(t.net_pnl for t in port_trades)
    # 3. 2x Fees (Maker 0.04%, Taker 0.10%)
    double_fees_net = sum(t.gross_pnl - (t.fees_paid * 2.0) - t.funding_paid for t in port_trades)
    # 4. 2x Slippage (0.10%) -> add 0.0005 * exit notional
    double_slip_net = sum(t.net_pnl - (t.notional * 0.0005) for t in port_trades)

    sensitivity_report = {
        "gross_pnl_zero_costs": round(gross_total, 2),
        "baseline_net_pnl": round(base_net, 2),
        "double_fees_net_pnl": round(double_fees_net, 2),
        "double_slippage_net_pnl": round(double_slip_net, 2),
        "fee_impact_pct_of_gross": round((sum(t.fees_paid for t in port_trades) / abs(gross_total) * 100.0), 2) if gross_total != 0 else 0.0,
    }

    print(f"Gross PnL (Zero Friction):        ${gross_total:+7.2f}")
    print(f"Baseline Net PnL (Realistic):     ${base_net:+7.2f}")
    print(f"2x Fee Sensitivity (0.10% Taker): ${double_fees_net:+7.2f}")
    print(f"2x Slippage Sensitivity (0.10%):  ${double_slip_net:+7.2f}")

    # 5. EXPORT DELIVERABLES
    print("\nSaving deliverables in " + OUTPUT_DIR + "...")

    # Export trade journal CSV
    csv_path = os.path.join(OUTPUT_DIR, "combined_portfolio_trades.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "trade_id", "symbol", "direction", "entry_time_utc", "exit_time_utc",
            "entry_price", "exit_price", "quantity", "notional",
            "gross_pnl", "net_pnl", "fees_paid", "funding_paid",
            "exit_reason", "duration_hours", "return_pct", "realized_rr"
        ])
        writer.writeheader()
        for t in port_trades:
            d = asdict(t)
            del d["entry_time_ms"]
            del d["exit_time_ms"]
            writer.writerow(d)

    # Export summary JSON
    summary_data = {
        "metadata": {
            "strategy": "Model B Fast Scalper (15M Trigger / 1H Trend)",
            "model_id": "MODEL_B_FAST",
            "cohort_version": "MODEL_B_FAST_V2",
            "backtest_window": "2025-10-24 00:00:00 UTC to 2026-10-24 15:00:00 UTC",
            "exchange": "Aster DEX / Binance Futures Perpetuals",
            "symbols": TOP_10_PAIRS,
            "config": CONFIG,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "per_pair_results": per_pair_results,
        "portfolio_results": port_metrics,
        "monthly_breakdown": monthly_summary,
        "quarterly_breakdown": quarterly_summary,
        "sensitivity_analysis": sensitivity_report,
    }

    json_path = os.path.join(OUTPUT_DIR, "annual_backtest_summary.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    print(f"Exported CSV: {csv_path}")
    print(f"Exported JSON: {json_path}")
    print(f"Total Backtest Elapsed Time: {time.time() - t0:.2f}s")

    return summary_data


if __name__ == "__main__":
    run_full_annual_backtest()
