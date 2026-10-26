"""
Model B V3 Fast Parameter Grid Sweep Runner
============================================
Programmatic grid sweep across trailing 12-month historical data to find the exact
net-profitable parameter combination that overcomes exchange fee friction.

Evaluates 4 structural levers:
  1. Stop Floor / Notional Compression: 0.80%, 1.10%, 1.40%
  2. Timeframe: 15m vs 30m (aggregated from 15m)
  3. TP1 Full Exit Multiplier: 1.65R, 1.85R, 2.10R
  4. Lookback & Wick Filter:
     - Standard: Lookback 20, Left=3/Right=2, Wick >= 0.20
     - Structural: Lookback 36, Left=5/Right=3, Wick >= 0.25
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
from dataclasses import dataclass
from datetime import datetime, timezone

TARGET_DB = "data/backtest_15m_top10.db"
OUTPUT_DIR = "backtest_results/model_b_v3_grid_sweep"

SWEEP_PAIRS_5 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT"]
SWEEP_PAIRS_4 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT"]

BASE_CONFIG = {
    "portfolio_capital": 100.0,
    "target_trade_risk_usdt": 1.00,
    "max_isolated_margin_usdt": 15.00,
    "leverage": 10.0,
    "maker_fee_rate": 0.0002,            # 0.02%
    "taker_fee_rate": 0.0005,            # 0.05%
    "adverse_slippage_rate": 0.0005,     # 0.05%
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
    direction: str
    entry_time_ms: int
    entry_price: float
    initial_sl: float
    current_sl: float
    initial_tp1: float
    current_tp: float
    quantity: float
    initial_quantity: float
    notional: float
    margin: float
    leverage: float
    entry_fee: float
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


def find_swings(candles: List[CandleBar], left: int, right: int):
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


class SweepDataLoader:
    def __init__(self, db_path: str = TARGET_DB):
        self.db_path = db_path
        self.candles_15m: Dict[str, List[CandleBar]] = {}
        self.candles_30m: Dict[str, List[CandleBar]] = {}
        self.candles_1h: Dict[str, List[CandleBar]] = {}
        self.funding_rates: Dict[str, List[Tuple[int, float]]] = {}
        self.ema20_1h: Dict[str, List[Optional[float]]] = {}
        self.ema50_1h: Dict[str, List[Optional[float]]] = {}
        self.h1_close_times: Dict[str, List[int]] = {}
        self.atr_15m: Dict[str, List[float]] = {}
        self.atr_30m: Dict[str, List[float]] = {}
        self.load_all()

    def load_all(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        for sym in SWEEP_PAIRS_5:
            # 1. 15M candles
            cur.execute("""
                SELECT timestamp_ms, open, high, low, close, volume
                FROM raw_candles
                WHERE symbol=? AND timeframe='15m'
                ORDER BY timestamp_ms ASC
            """, (sym,))
            c15 = [CandleBar(*r) for r in cur.fetchall()]
            self.candles_15m[sym] = c15

            # 15M ATR
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

            # 2. Resample 15M -> 30M cleanly
            c30 = []
            for i in range(0, len(c15) - 1, 2):
                b0 = c15[i]
                b1 = c15[i + 1]
                c30.append(CandleBar(
                    timestamp_ms=b0.timestamp_ms,
                    open=b0.open,
                    high=max(b0.high, b1.high),
                    low=min(b0.low, b1.low),
                    close=b1.close,
                    volume=b0.volume + b1.volume,
                ))
            self.candles_30m[sym] = c30

            # 30M ATR
            trs30 = [0.0] * len(c30)
            for i in range(1, len(c30)):
                trs30[i] = max(c30[i].high - c30[i].low, abs(c30[i].high - c30[i - 1].close), abs(c30[i].low - c30[i - 1].close))
            atrs30 = [0.0] * len(c30)
            if len(c30) >= 15:
                running_tr = sum(trs30[1:15])
                atrs30[14] = running_tr / 14.0
                for i in range(15, len(c30)):
                    running_tr += trs30[i] - trs30[i - 14]
                    atrs30[i] = running_tr / 14.0
            self.atr_30m[sym] = atrs30

            # 3. 1H trend candles
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

            # 4. Funding rates
            cur.execute("""
                SELECT funding_time_ms, funding_rate
                FROM funding_rates
                WHERE symbol=?
                ORDER BY funding_time_ms ASC
            """, (sym,))
            self.funding_rates[sym] = sorted(cur.fetchall(), key=lambda x: x[0])

        conn.close()


def simulate_bar_fast(
    c_bar: CandleBar,
    open_pos: Optional[Position],
    pending_order: Optional[PendingOrder],
    funding_list: List[Tuple[int, float]],
    fund_ptr: int,
    trade_counter: int,
    symbol: str,
    maker_fee: float,
    taker_fee: float,
    slippage: float,
) -> Tuple[Optional[Position], Optional[PendingOrder], Optional[TradeResult], int, int]:
    trade_res: Optional[TradeResult] = None
    num_funding = len(funding_list)

    # 1. Settle funding
    if open_pos:
        while fund_ptr < num_funding and funding_list[fund_ptr][0] <= c_bar.timestamp_ms:
            fts, frate = funding_list[fund_ptr]
            if fts > open_pos.last_funding_time_ms:
                payment = (open_pos.notional * frate) if open_pos.direction == "LONG" else (-open_pos.notional * frate)
                open_pos.funding_paid += payment
                open_pos.last_funding_time_ms = fts
            fund_ptr += 1
    else:
        while fund_ptr < num_funding and funding_list[fund_ptr][0] <= c_bar.timestamp_ms:
            fund_ptr += 1

    # 2. Position exits (100% Full Exit at TP1)
    if open_pos:
        hit_tp = False
        hit_sl = False
        exit_price = 0.0
        exit_reason = ""

        if open_pos.direction == "LONG":
            hit_tp = c_bar.high >= open_pos.current_tp
            hit_sl = c_bar.low <= open_pos.current_sl

            if hit_tp and hit_sl:
                exit_price = open_pos.current_sl * (1.0 - slippage)
                exit_reason = "SL_COLLISION"
            elif hit_sl:
                exit_price = open_pos.current_sl * (1.0 - slippage)
                exit_reason = "STOP_LOSS"
            elif hit_tp:
                exit_price = open_pos.current_tp
                exit_reason = "TAKE_PROFIT"

        elif open_pos.direction == "SHORT":
            hit_tp = c_bar.low <= open_pos.current_tp
            hit_sl = c_bar.high >= open_pos.current_sl

            if hit_tp and hit_sl:
                exit_price = open_pos.current_sl * (1.0 + slippage)
                exit_reason = "SL_COLLISION"
            elif hit_sl:
                exit_price = open_pos.current_sl * (1.0 + slippage)
                exit_reason = "STOP_LOSS"
            elif hit_tp:
                exit_price = open_pos.current_tp
                exit_reason = "TAKE_PROFIT"

        if exit_reason != "":
            eff_exit_px = exit_price
            if open_pos.direction == "LONG":
                gross = open_pos.quantity * (eff_exit_px - open_pos.entry_price)
            else:
                gross = open_pos.quantity * (open_pos.entry_price - eff_exit_px)

            exit_fee = (open_pos.quantity * eff_exit_px) * taker_fee
            total_fees = open_pos.entry_fee + exit_fee
            net = gross - total_fees - open_pos.funding_paid

            duration_hrs = (c_bar.timestamp_ms - open_pos.entry_time_ms) / 3600000.0

            trade_res = TradeResult(
                trade_id=open_pos.trade_id,
                symbol=symbol,
                direction=open_pos.direction,
                entry_time_ms=open_pos.entry_time_ms,
                exit_time_ms=c_bar.timestamp_ms,
                entry_price=open_pos.entry_price,
                exit_price=eff_exit_px,
                quantity=open_pos.quantity,
                notional=open_pos.notional,
                gross_pnl=round(gross, 4),
                net_pnl=round(net, 4),
                fees_paid=round(total_fees, 4),
                funding_paid=round(open_pos.funding_paid, 4),
                exit_reason=exit_reason,
                duration_hours=round(duration_hrs, 2),
            )
            open_pos = None

    # 3. Order fills
    if pending_order and not open_pos:
        if c_bar.timestamp_ms >= pending_order.expiry_time_ms:
            pending_order = None
        else:
            filled = False
            if pending_order.direction == "LONG" and c_bar.low <= pending_order.entry_price:
                filled = True
            elif pending_order.direction == "SHORT" and c_bar.high >= pending_order.entry_price:
                filled = True

            if filled:
                entry_fee = pending_order.notional * maker_fee
                open_pos = Position(
                    trade_id=f"PTRD_{symbol}_{trade_counter}",
                    symbol=symbol,
                    direction=pending_order.direction,
                    entry_time_ms=c_bar.timestamp_ms,
                    entry_price=pending_order.entry_price,
                    initial_sl=pending_order.stop_loss,
                    current_sl=pending_order.stop_loss,
                    initial_tp1=pending_order.tp1,
                    current_tp=pending_order.tp1,
                    quantity=pending_order.quantity,
                    initial_quantity=pending_order.quantity,
                    notional=pending_order.notional,
                    margin=pending_order.margin,
                    leverage=pending_order.leverage,
                    entry_fee=entry_fee,
                    last_funding_time_ms=c_bar.timestamp_ms,
                )
                trade_counter += 1
                pending_order = None

    return open_pos, pending_order, trade_res, fund_ptr, trade_counter


def eval_signal_grid(
    c_bar: CandleBar,
    recent_bars: List[CandleBar],
    atr_val: float,
    htf_bias: str,
    symbol: str,
    order_counter: int,
    bar_ms: int,
    tp1_rr_mult: float,
    min_wick_ratio: float,
    stop_floor_pct: float,
    lookback_bars: int,
    swing_left: int,
    swing_right: int,
    ttl_bars: int = 8,
) -> Optional[PendingOrder]:
    last_closed = c_bar
    prev_c = recent_bars[-2] if len(recent_bars) >= 2 else last_closed
    sw_slice = recent_bars[-lookback_bars:-1] if len(recent_bars) >= lookback_bars else recent_bars[:-1]
    sw_highs, sw_lows = find_swings(sw_slice, left=swing_left, right=swing_right)

    c_range = last_closed.high - last_closed.low
    if c_range <= 0.0:
        return None

    # Confirmed body close
    confirmed_green = last_closed.close > last_closed.open
    confirmed_red = last_closed.close < last_closed.open

    # Volume filter (1.10x SMA20)
    if len(recent_bars) >= 20:
        vols = [c.volume for c in recent_bars[-20:]]
        avg_vol = sum(vols) / len(vols)
        if avg_vol > 0.0 and (last_closed.volume / avg_vol) < 1.10:
            return None
    else:
        return None

    target_risk = BASE_CONFIG["target_trade_risk_usdt"]
    max_margin = BASE_CONFIG["max_isolated_margin_usdt"]
    leverage = BASE_CONFIG["leverage"]

    # LONG
    if sw_lows and htf_bias != "BEARISH":
        key_low = sw_lows[-1]
        swept = (last_closed.low < key_low) or (prev_c.low < key_low)
        reclaimed = swept and (last_closed.close > key_low)

        if reclaimed and confirmed_green:
            lower_wick = min(last_closed.open, last_closed.close) - last_closed.low
            if (lower_wick / c_range) < min_wick_ratio:
                return None

            entry_px = last_closed.close
            raw_sl = min(last_closed.low, prev_c.low) * 0.999
            min_stop_dist = max(entry_px * stop_floor_pct, atr_val * 1.25)
            sl_px = min(raw_sl, entry_px - min_stop_dist)
            risk_dist = entry_px - sl_px

            if risk_dist > 0:
                tp1 = round(entry_px + (risk_dist * tp1_rr_mult), 8)
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
                        expiry_time_ms=last_closed.timestamp_ms + (ttl_bars * bar_ms),
                        entry_price=entry_px,
                        stop_loss=sl_px,
                        tp1=tp1,
                        quantity=raw_qty,
                        notional=notional,
                        margin=req_margin,
                        leverage=leverage,
                        risk_usdt=target_risk,
                    )

    # SHORT
    elif sw_highs and htf_bias != "BULLISH":
        key_high = sw_highs[-1]
        swept = (last_closed.high > key_high) or (prev_c.high > key_high)
        reclaimed = swept and (last_closed.close < key_high)

        if reclaimed and confirmed_red:
            upper_wick = last_closed.high - max(last_closed.open, last_closed.close)
            if (upper_wick / c_range) < min_wick_ratio:
                return None

            entry_px = last_closed.close
            raw_sl = max(last_closed.high, prev_c.high) * 1.001
            min_stop_dist = max(entry_px * stop_floor_pct, atr_val * 1.25)
            sl_px = max(raw_sl, entry_px + min_stop_dist)
            risk_dist = sl_px - entry_px

            if risk_dist > 0:
                tp1 = round(entry_px - (risk_dist * tp1_rr_mult), 8)
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
                        expiry_time_ms=last_closed.timestamp_ms + (ttl_bars * bar_ms),
                        entry_price=entry_px,
                        stop_loss=sl_px,
                        tp1=tp1,
                        quantity=raw_qty,
                        notional=notional,
                        margin=req_margin,
                        leverage=leverage,
                        risk_usdt=target_risk,
                    )

    return None


def run_portfolio_simulation(
    loader: SweepDataLoader,
    pairs: List[str],
    timeframe: str,  # "15m" or "30m"
    stop_floor_pct: float,
    tp1_rr_mult: float,
    lookback_bars: int,
    swing_left: int,
    swing_right: int,
    min_wick_ratio: float,
) -> Dict[str, Any]:
    bar_ms = 900000 if timeframe == "15m" else 1800000
    candles_dict = loader.candles_15m if timeframe == "15m" else loader.candles_30m
    atr_dict = loader.atr_15m if timeframe == "15m" else loader.atr_30m

    start_eval_ts = BASE_CONFIG["backtest_start_ts"]
    end_eval_ts = BASE_CONFIG["backtest_end_ts"]

    all_ts = sorted(list(set(
        c.timestamp_ms
        for sym in pairs
        for c in candles_dict[sym]
    )))

    c_map = {sym: {c.timestamp_ms: c for c in candles_dict[sym]} for sym in pairs}
    c_idx_map = {sym: {c.timestamp_ms: i for i, c in enumerate(candles_dict[sym])} for sym in pairs}

    open_positions: Dict[str, Position] = {}
    pending_orders: Dict[str, PendingOrder] = {}
    trade_history: List[TradeResult] = []

    h1_ptrs = {sym: 0 for sym in pairs}
    fund_ptrs = {sym: 0 for sym in pairs}
    order_counter = 0
    trade_counter = 0

    for ts in all_ts:
        c_close_ms = ts + bar_ms - 1

        for sym in pairs:
            c_bar = c_map[sym].get(ts)
            if not c_bar:
                continue

            c1_list = loader.candles_1h[sym]
            h1_times = loader.h1_close_times[sym]
            while h1_ptrs[sym] + 1 < len(c1_list) and h1_times[h1_ptrs[sym] + 1] <= c_close_ms:
                h1_ptrs[sym] += 1

            pos = open_positions.get(sym)
            pord = pending_orders.get(sym)

            new_pos, new_pord, trade_res, fund_ptrs[sym], trade_counter = simulate_bar_fast(
                c_bar=c_bar,
                open_pos=pos,
                pending_order=pord,
                funding_list=loader.funding_rates[sym],
                fund_ptr=fund_ptrs[sym],
                trade_counter=trade_counter,
                symbol=sym,
                maker_fee=BASE_CONFIG["maker_fee_rate"],
                taker_fee=BASE_CONFIG["taker_fee_rate"],
                slippage=BASE_CONFIG["adverse_slippage_rate"],
            )

            if new_pos:
                open_positions[sym] = new_pos
            elif sym in open_positions:
                del open_positions[sym]

            if new_pord:
                pending_orders[sym] = new_pord
            elif sym in pending_orders:
                del pending_orders[sym]

            if trade_res:
                trade_history.append(trade_res)

        if c_close_ms >= start_eval_ts and c_close_ms <= end_eval_ts:
            for sym in pairs:
                if sym in open_positions or sym in pending_orders:
                    continue

                total_open = len(open_positions) + len(pending_orders)
                if total_open >= BASE_CONFIG["max_open_positions_portfolio"]:
                    break

                c_bar = c_map[sym].get(ts)
                if not c_bar:
                    continue

                idx = c_idx_map[sym][ts]
                if idx < lookback_bars + 10:
                    continue

                c1_list = loader.candles_1h[sym]
                e20_1h = loader.ema20_1h[sym]
                e50_1h = loader.ema50_1h[sym]
                h1_ptr = h1_ptrs[sym]

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
                cand_order = eval_signal_grid(
                    c_bar=c_bar,
                    recent_bars=candles_dict[sym][idx - lookback_bars : idx],
                    atr_val=atr_dict[sym][idx],
                    htf_bias=h1_bias,
                    symbol=sym,
                    order_counter=order_counter,
                    bar_ms=bar_ms,
                    tp1_rr_mult=tp1_rr_mult,
                    min_wick_ratio=min_wick_ratio,
                    stop_floor_pct=stop_floor_pct,
                    lookback_bars=lookback_bars,
                    swing_left=swing_left,
                    swing_right=swing_right,
                    ttl_bars=8,
                )

                if cand_order:
                    direction = cand_order.direction
                    same_dir_count = sum(1 for p in open_positions.values() if p.direction == direction) + sum(1 for o in pending_orders.values() if o.direction == direction)
                    if same_dir_count < BASE_CONFIG["max_open_per_direction"]:
                        pending_orders[sym] = cand_order

    # Calculate metrics
    if not trade_history:
        return {"total_trades": 0, "win_rate": 0.0, "gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0, "profit_factor": 0.0, "max_dd": 0.0}

    wins = [t for t in trade_history if t.net_pnl > 0]
    gross_wins = sum(t.gross_pnl for t in trade_history if t.gross_pnl > 0)
    gross_losses = abs(sum(t.gross_pnl for t in trade_history if t.gross_pnl < 0))
    pf = (gross_wins / gross_losses) if gross_losses > 1e-6 else 99.0

    gross_pnl = sum(t.gross_pnl for t in trade_history)
    fees_paid = sum(t.fees_paid for t in trade_history)
    funding_paid = sum(t.funding_paid for t in trade_history)
    net_pnl = sum(t.net_pnl for t in trade_history)

    equity = 100.0
    peak = equity
    max_dd = 0.0
    for t in trade_history:
        equity += t.net_pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades": len(trade_history),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(trade_history) * 100.0, 2),
        "gross_pnl": round(gross_pnl, 2),
        "fees": round(fees_paid, 2),
        "funding": round(funding_paid, 2),
        "net_pnl": round(net_pnl, 2),
        "profit_factor": round(pf, 2),
        "max_dd": round(max_dd * 100.0, 2),
    }


def main():
    t_start = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 105)
    print("STARTING FAST PARAMETER GRID SWEEP ON MODEL B V3 (OVERCOMING $73 FEE DRAG)")
    print("=" * 105)

    loader = SweepDataLoader(TARGET_DB)
    print(f"Loaded and resampled all candles in {time.time() - t_start:.2f}s\n")

    timeframes = ["15m", "30m"]
    stop_floors = [0.008, 0.011, 0.014]
    tp_multipliers = [1.65, 1.85, 2.10]
    filter_profiles = [
        {"name": "Standard (20b/Wick20)", "lookback": 20, "left": 3, "right": 2, "wick": 0.20},
        {"name": "Structural (36b/Wick25)", "lookback": 36, "left": 5, "right": 3, "wick": 0.25},
    ]

    total_combos = len(timeframes) * len(stop_floors) * len(tp_multipliers) * len(filter_profiles)
    print(f">>> Sweeping {total_combos} combinations across 5 Active Pairs ({SWEEP_PAIRS_5})...\n")

    grid_results = []
    combo_count = 0

    for tf in timeframes:
        for sf in stop_floors:
            for tp in tp_multipliers:
                for fp in filter_profiles:
                    combo_count += 1
                    t0 = time.time()

                    # Run on 5 pairs
                    res_5 = run_portfolio_simulation(
                        loader=loader,
                        pairs=SWEEP_PAIRS_5,
                        timeframe=tf,
                        stop_floor_pct=sf,
                        tp1_rr_mult=tp,
                        lookback_bars=fp["lookback"],
                        swing_left=fp["left"],
                        swing_right=fp["right"],
                        min_wick_ratio=fp["wick"],
                    )

                    # Run on Top 4 Alpha Cluster
                    res_4 = run_portfolio_simulation(
                        loader=loader,
                        pairs=SWEEP_PAIRS_4,
                        timeframe=tf,
                        stop_floor_pct=sf,
                        tp1_rr_mult=tp,
                        lookback_bars=fp["lookback"],
                        swing_left=fp["left"],
                        swing_right=fp["right"],
                        min_wick_ratio=fp["wick"],
                    )

                    elapsed = time.time() - t0
                    grid_results.append({
                        "tf": tf,
                        "stop_floor": sf,
                        "stop_floor_pct": f"{sf*100:.2f}%",
                        "tp1_rr": tp,
                        "profile": fp["name"],
                        "lookback": fp["lookback"],
                        "wick": fp["wick"],
                        "trades_5": res_5["total_trades"],
                        "wr_5": res_5["win_rate"],
                        "gross_5": res_5["gross_pnl"],
                        "fees_5": res_5["fees"],
                        "net_5": res_5["net_pnl"],
                        "pf_5": res_5["profit_factor"],
                        "max_dd_5": res_5["max_dd"],
                        "trades_4": res_4["total_trades"],
                        "wr_4": res_4["win_rate"],
                        "gross_4": res_4["gross_pnl"],
                        "fees_4": res_4["fees"],
                        "net_4": res_4["net_pnl"],
                        "pf_4": res_4["profit_factor"],
                        "max_dd_4": res_4["max_dd"],
                    })

                    net_tag = f"NET: {res_4['net_pnl']:+6.2f}$" if res_4['net_pnl'] > 0 else f"NET: {res_4['net_pnl']:6.2f}$"
                    pf_tag = f"PF: {res_4['profit_factor']:.2f}"
                    print(f"[{combo_count:02d}/{total_combos:02d}] {tf} | Stop: {sf*100:.2f}% | TP1: {tp:.2f}R | {fp['name']:<24} -> Top4: {net_tag} ({pf_tag}) | 5Pairs: {res_5['net_pnl']:+6.2f}$ ({elapsed:.2f}s)")

    # Sort results by Top 4 Cluster Net PnL descending
    grid_results.sort(key=lambda x: (x["net_4"], x["pf_4"]), reverse=True)

    print("\n" + "=" * 135)
    print("TOP 10 BEST PARAMETER COMBINATIONS RANKED BY FULL NET PNL (AFTER FEES, SLIPPAGE & FUNDING)")
    print("=" * 135)
    print(f"{'Rank':<5} | {'TF':<4} | {'Stop Floor':<10} | {'TP1 Target':<10} | {'Profile':<24} | {'Top4 Trades':<11} | {'Top4 Gross':<11} | {'Top4 Fees':<10} | {'Top4 Net PnL':<13} | {'Top4 PF':<7} | {'5P Net PnL':<11}")
    print("-" * 135)

    for rank, r in enumerate(grid_results[:10], 1):
        net_str = f"+{r['net_4']:.2f}$" if r['net_4'] > 0 else f"{r['net_4']:.2f}$"
        gross_str = f"+{r['gross_4']:.2f}$" if r['gross_4'] > 0 else f"{r['gross_4']:.2f}$"
        net5_str = f"+{r['net_5']:.2f}$" if r['net_5'] > 0 else f"{r['net_5']:.2f}$"
        print(f"#{rank:<4} | {r['tf']:<4} | {r['stop_floor_pct']:<10} | {r['tp1_rr']:>5.2f}R     | {r['profile']:<24} | {r['trades_4']:<11} | {gross_str:>10} | {r['fees_4']:>9.2f}$ | {net_str:>12} | {r['pf_4']:>6.2f} | {net5_str:>10}")

    # Export CSV
    csv_file = f"{OUTPUT_DIR}/grid_sweep_results.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Rank", "Timeframe", "Stop Floor %", "TP1 RR Multiple", "Filter Profile", "Lookback Bars", "Min Wick Ratio",
            "Top4 Trades", "Top4 Win Rate %", "Top4 Gross PnL", "Top4 Fees Paid", "Top4 Net PnL", "Top4 Profit Factor", "Top4 Max DD %",
            "5Pair Trades", "5Pair Win Rate %", "5Pair Gross PnL", "5Pair Fees Paid", "5Pair Net PnL", "5Pair Profit Factor", "5Pair Max DD %"
        ])
        for rank, r in enumerate(grid_results, 1):
            w.writerow([
                rank, r["tf"], r["stop_floor_pct"], r["tp1_rr"], r["profile"], r["lookback"], r["wick"],
                r["trades_4"], r["wr_4"], r["gross_4"], r["fees_4"], r["net_4"], r["pf_4"], r["max_dd_4"],
                r["trades_5"], r["wr_5"], r["gross_5"], r["fees_5"], r["net_5"], r["pf_5"], r["max_dd_5"],
            ])

    print(f"\n[OK] Full sweep results saved to {csv_file}")
    best = grid_results[0]
    print(f"\n>>> WINNING CONFIGURATION (#{1}):")
    print(f"    Timeframe: {best['tf']}")
    print(f"    Stop Floor: {best['stop_floor_pct']} (min_stop_distance_pct = {best['stop_floor']})")
    print(f"    TP1 Target: {best['tp1_rr']:.2f}R (tp1_rr_multiple = {best['tp1_rr']})")
    print(f"    Filter Profile: {best['profile']}")
    print(f"    Top 4 Cluster Net PnL: {best['net_4']:+.2f}$ (PF: {best['pf_4']:.2f}, Gross: {best['gross_4']:+.2f}$, Fees: {best['fees_4']:.2f}$)")
    print(f"    5 Active Pairs Net PnL: {best['net_5']:+.2f}$ (PF: {best['pf_5']:.2f}, Gross: {best['gross_5']:+.2f}$, Fees: {best['fees_5']:.2f}$)")
    print(f"    Total Sweep Time: {time.time() - t_start:.2f}s")


if __name__ == "__main__":
    main()
