"""
Model B Refined Annual Historical Backtest Runner (12-Month Top 10 Pairs)
========================================================================
Rigorous, lookahead-free historical evaluation incorporating all latest strategy refinements:

1. MODEL_B_V3_REFINED (15M Trigger / 1H Trend):
   - Fee-Adjusted TP1: 1.65R Full Exit (full_exit_at_tp1 = True)
   - Rejection Wick: >= 0.20 (20% of candle range)
   - Volume Confirmation: >= 1.10x SMA(volume, 20)
   - Unblacklisted: BTCUSDT (top gross alpha producer)
   - Blacklisted Chop Laggards: BNBUSDT, AVAXUSDT, XRPUSDT, LINKUSDT, ADAUSDT
   - Active Trading Universe: BTCUSDT, ETHUSDT, LTCUSDT, SOLUSDT, DOGEUSDT

2. NORMAL MODEL_B_UPGRADED (1H Trigger / 4H Trend):
   - TP1: 1.50R Full Exit (full_exit_at_tp1 = True)
   - Rejection Wick: >= 0.20
   - Volume Confirmation: >= 1.10x SMA(volume, 20)
   - Blacklisted Chop: BNBUSDT, AVAXUSDT, XRPUSDT
   - Active Trading Universe: BTCUSDT, ETHUSDT, SOLUSDT, DOGEUSDT, ADAUSDT, LINKUSDT, LTCUSDT

Period: 2025-10-24 00:00:00 UTC to 2026-10-24 15:00:00 UTC (12 Months / 365.6 Days).
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
OUTPUT_DIR = "backtest_results/model_b_refined_annual"

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

# Refined Blacklists
V3_REFINED_BLACKLIST = ["BNBUSDT", "AVAXUSDT", "XRPUSDT", "LINKUSDT", "ADAUSDT"]
V3_REFINED_ACTIVE = [p for p in TOP_10_PAIRS if p not in V3_REFINED_BLACKLIST] # BTC, ETH, SOL, DOGE, LTC
V3_ALPHA_CLUSTER = ["BTCUSDT", "ETHUSDT", "LTCUSDT", "SOLUSDT"]

H1_UPGRADED_BLACKLIST = ["BNBUSDT", "AVAXUSDT", "XRPUSDT"]
H1_UPGRADED_ACTIVE = [p for p in TOP_10_PAIRS if p not in H1_UPGRADED_BLACKLIST]

BASE_CONFIG = {
    "initial_capital_per_pair": 100.0,
    "portfolio_capital": 100.0,
    "target_trade_risk_usdt": 1.00,
    "max_isolated_margin_usdt": 15.00,
    "leverage": 10.0,
    "min_stop_distance_pct_15m": 0.008,  # 0.80% for 15m
    "min_stop_distance_pct_1h": 0.015,   # 1.50% for 1h
    "min_stop_atr_multiple": 1.25,
    "pending_order_ttl_bars_15m": 8,     # 2.0 hours
    "pending_order_ttl_bars_1h": 6,      # 6.0 hours
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
        self.candles_4h: Dict[str, List[CandleBar]] = {}
        self.funding_rates: Dict[str, List[Tuple[int, float]]] = {}
        self.ema20_1h: Dict[str, List[Optional[float]]] = {}
        self.ema50_1h: Dict[str, List[Optional[float]]] = {}
        self.h1_close_times: Dict[str, List[int]] = {}
        self.atr_15m: Dict[str, List[float]] = {}
        self.atr_1h: Dict[str, List[float]] = {}
        self.ema20_4h: Dict[str, List[Optional[float]]] = {}
        self.ema50_4h: Dict[str, List[Optional[float]]] = {}
        self.h4_close_times: Dict[str, List[int]] = {}
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

            # 15m ATR
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

            # 1h ATR
            trs1 = [0.0] * len(c1)
            for i in range(1, len(c1)):
                trs1[i] = max(c1[i].high - c1[i].low, abs(c1[i].high - c1[i - 1].close), abs(c1[i].low - c1[i - 1].close))
            atrs1 = [0.0] * len(c1)
            if len(c1) >= 15:
                running_tr1 = sum(trs1[1:15])
                atrs1[14] = running_tr1 / 14.0
                for i in range(15, len(c1)):
                    running_tr1 += trs1[i] - trs1[i - 14]
                    atrs1[i] = running_tr1 / 14.0
            self.atr_1h[sym] = atrs1

            # 4h candles
            cur.execute("""
                SELECT timestamp_ms, open, high, low, close, volume
                FROM raw_candles
                WHERE symbol=? AND timeframe='4h'
                ORDER BY timestamp_ms ASC
            """, (sym,))
            c4 = [CandleBar(*r) for r in cur.fetchall()]
            self.candles_4h[sym] = c4
            h4_closes = [c.close for c in c4]
            self.h4_close_times[sym] = [c.timestamp_ms + 4 * 3600000 - 1 for c in c4]
            self.ema20_4h[sym] = compute_running_ema(h4_closes, 20)
            self.ema50_4h[sym] = compute_running_ema(h4_closes, 50)

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
    c_bar: CandleBar,
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
    full_exit_at_tp1: bool,
) -> Tuple[Optional[Position], Optional[PendingOrder], Optional[TradeResult], int, int, int]:
    """Execute order and position state transitions for a single candle bar."""
    trade_res: Optional[TradeResult] = None
    num_funding = len(funding_list)

    # 1. Settle funding if in position
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

    # 2. Evaluate open position exits
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
                if open_pos.tp1_hit:
                    be_ref = open_pos.breakeven_price or open_pos.entry_price
                    exit_reason = "TRAILING_STOP" if open_pos.current_sl > be_ref + 1e-6 else "BREAKEVEN_STOP"
                else:
                    exit_reason = "STOP_LOSS"
            elif hit_tp:
                if full_exit_at_tp1:
                    exit_price = open_pos.current_tp
                    exit_reason = "TAKE_PROFIT_1_FULL"
                else:
                    if not open_pos.tp1_hit and open_pos.tp2 is not None:
                        tp1_price = open_pos.initial_tp1
                        closed_qty = open_pos.quantity * 0.5
                        remain_qty = open_pos.quantity - closed_qty
                        partial_gross = closed_qty * (tp1_price - open_pos.entry_price)
                        partial_fee = (closed_qty * tp1_price) * taker_fee
                        open_pos.partial_gross_pnl += partial_gross
                        open_pos.partial_fee_paid += partial_fee
                        open_pos.tp1_hit = True
                        open_pos.tp1_hit_time_ms = c_bar.timestamp_ms
                        open_pos.quantity = remain_qty
                        open_pos.notional = remain_qty * open_pos.entry_price

                        be_price = open_pos.entry_price * (1.0 + maker_fee + taker_fee + slippage)
                        open_pos.breakeven_price = be_price
                        open_pos.current_sl = max(open_pos.current_sl, be_price)
                        open_pos.current_tp = open_pos.tp2
                        open_pos.trailing_active = True
                        init_r = abs(open_pos.entry_price - open_pos.initial_sl)
                        open_pos.trailing_distance = init_r * 1.0
                        open_pos.favorable_extreme = max(tp1_price, c_bar.high)

                        if c_bar.high >= open_pos.tp2:
                            exit_price = open_pos.tp2
                            exit_reason = "TAKE_PROFIT_2"
                    else:
                        exit_price = open_pos.current_tp
                        exit_reason = "TAKE_PROFIT_2" if open_pos.tp1_hit else "TAKE_PROFIT"

        elif open_pos.direction == "SHORT":
            hit_tp = c_bar.low <= open_pos.current_tp
            hit_sl = c_bar.high >= open_pos.current_sl

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
                if full_exit_at_tp1:
                    exit_price = open_pos.current_tp
                    exit_reason = "TAKE_PROFIT_1_FULL"
                else:
                    if not open_pos.tp1_hit and open_pos.tp2 is not None:
                        tp1_price = open_pos.initial_tp1
                        closed_qty = open_pos.quantity * 0.5
                        remain_qty = open_pos.quantity - closed_qty
                        partial_gross = closed_qty * (open_pos.entry_price - tp1_price)
                        partial_fee = (closed_qty * tp1_price) * taker_fee
                        open_pos.partial_gross_pnl += partial_gross
                        open_pos.partial_fee_paid += partial_fee
                        open_pos.tp1_hit = True
                        open_pos.tp1_hit_time_ms = c_bar.timestamp_ms
                        open_pos.quantity = remain_qty
                        open_pos.notional = remain_qty * open_pos.entry_price

                        be_price = open_pos.entry_price * (1.0 - (maker_fee + taker_fee + slippage))
                        open_pos.breakeven_price = be_price
                        open_pos.current_sl = min(open_pos.current_sl, be_price)
                        open_pos.current_tp = open_pos.tp2
                        open_pos.trailing_active = True
                        init_r = abs(open_pos.initial_sl - open_pos.entry_price)
                        open_pos.trailing_distance = init_r * 1.0
                        open_pos.favorable_extreme = min(tp1_price, c_bar.low)

                        if c_bar.low <= open_pos.tp2:
                            exit_price = open_pos.tp2
                            exit_reason = "TAKE_PROFIT_2"
                    else:
                        exit_price = open_pos.current_tp
                        exit_reason = "TAKE_PROFIT_2" if open_pos.tp1_hit else "TAKE_PROFIT"

        # Trailing stop update if open
        if open_pos and exit_reason == "" and open_pos.trailing_active and not full_exit_at_tp1:
            if open_pos.direction == "LONG":
                open_pos.favorable_extreme = max(open_pos.favorable_extreme, c_bar.high)
                new_trail = open_pos.favorable_extreme - open_pos.trailing_distance
                if new_trail > open_pos.current_sl:
                    open_pos.current_sl = new_trail
            elif open_pos.direction == "SHORT":
                open_pos.favorable_extreme = min(open_pos.favorable_extreme, c_bar.low)
                new_trail = open_pos.favorable_extreme + open_pos.trailing_distance
                if new_trail < open_pos.current_sl:
                    open_pos.current_sl = new_trail

        # Finalize trade if exited
        if exit_reason != "":
            eff_exit_px = exit_price
            if open_pos.direction == "LONG":
                final_gross = open_pos.quantity * (eff_exit_px - open_pos.entry_price)
            else:
                final_gross = open_pos.quantity * (open_pos.entry_price - eff_exit_px)

            final_exit_fee = (open_pos.quantity * eff_exit_px) * taker_fee
            total_gross = open_pos.partial_gross_pnl + final_gross
            total_fees = open_pos.entry_fee + open_pos.partial_fee_paid + final_exit_fee
            total_net = total_gross - total_fees - open_pos.funding_paid

            duration_hrs = (c_bar.timestamp_ms - open_pos.entry_time_ms) / 3600000.0
            dt_entry = datetime.fromtimestamp(open_pos.entry_time_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            dt_exit = datetime.fromtimestamp(c_bar.timestamp_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

            init_risk = abs(open_pos.entry_price - open_pos.initial_sl) * open_pos.initial_quantity
            realized_rr = (total_gross / init_risk) if init_risk > 1e-6 else 0.0

            trade_res = TradeResult(
                trade_id=open_pos.trade_id,
                symbol=symbol,
                direction=open_pos.direction,
                entry_time_ms=open_pos.entry_time_ms,
                exit_time_ms=c_bar.timestamp_ms,
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
                    tp2=pending_order.tp2,
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

    return open_pos, pending_order, trade_res, fund_ptr, order_counter, trade_counter


def evaluate_signal_generic(
    c_bar: CandleBar,
    recent_bars: List[CandleBar],
    atr_val: float,
    htf_bias: str,
    symbol: str,
    order_counter: int,
    timeframe: str,  # "15m" or "1h"
    tp1_rr_mult: float,
    min_wick_ratio: float,
    min_vol_mult: float,
    stop_floor_pct: float,
    full_exit: bool,
    ttl_bars: int,
) -> Optional[PendingOrder]:
    """Generates lookahead-free signal candidate with exact parameterized filters."""
    last_closed = c_bar
    prev_c = recent_bars[-2] if len(recent_bars) >= 2 else last_closed
    sw_highs, sw_lows = find_recent_swings_slice(recent_bars[:-1], left=3, right=2)

    c_range = last_closed.high - last_closed.low
    if c_range <= 0.0:
        return None

    # Confirmed candle body close
    confirmed_green = last_closed.close > last_closed.open
    confirmed_red = last_closed.close < last_closed.open

    # Relative volume filter
    if min_vol_mult > 0.0:
        if len(recent_bars) >= 20:
            vols = [c.volume for c in recent_bars[-20:]]
            avg_vol = sum(vols) / len(vols)
            if avg_vol > 0.0 and (last_closed.volume / avg_vol) < min_vol_mult:
                return None
        else:
            return None

    target_risk = BASE_CONFIG["target_trade_risk_usdt"]
    max_margin = BASE_CONFIG["max_isolated_margin_usdt"]
    leverage = BASE_CONFIG["leverage"]
    bar_ms = 900000 if timeframe == "15m" else 3600000

    # LONG SETUP
    if sw_lows and htf_bias != "BEARISH":
        key_low = sw_lows[-1]
        swept = (last_closed.low < key_low) or (prev_c.low < key_low)
        reclaimed = swept and (last_closed.close > key_low)

        if reclaimed and confirmed_green:
            if min_wick_ratio > 0.0:
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
                high_targets = [p for p in sw_highs if p > tp1]
                tp2 = round(high_targets[0] if high_targets else entry_px + (risk_dist * 2.5), 8) if not full_exit else None

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
                        tp2=tp2,
                        quantity=raw_qty,
                        notional=notional,
                        margin=req_margin,
                        leverage=leverage,
                        risk_usdt=target_risk,
                    )

    # SHORT SETUP
    elif sw_highs and htf_bias != "BULLISH":
        key_high = sw_highs[-1]
        swept = (last_closed.high > key_high) or (prev_c.high > key_high)
        reclaimed = swept and (last_closed.close < key_high)

        if reclaimed and confirmed_red:
            if min_wick_ratio > 0.0:
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
                low_targets = [p for p in sw_lows if p < tp1]
                tp2 = round(low_targets[0] if low_targets else entry_px - (risk_dist * 2.5), 8) if not full_exit else None

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
                        tp2=tp2,
                        quantity=raw_qty,
                        notional=notional,
                        margin=req_margin,
                        leverage=leverage,
                        risk_usdt=target_risk,
                    )

    return None


def calculate_metrics(trades: List[TradeResult], initial_cap: float = 100.0) -> Dict[str, Any]:
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "gross_pnl": 0.0, "fees_paid": 0.0,
            "funding_paid": 0.0, "net_pnl": 0.0, "net_return_pct": 0.0, "profit_factor": 0.0,
            "max_drawdown_pct": 0.0, "avg_trade_pnl": 0.0, "avg_duration_hrs": 0.0,
            "long_trades": 0, "short_trades": 0, "wins": 0, "losses": 0,
        }

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    longs = [t for t in trades if t.direction == "LONG"]
    shorts = [t for t in trades if t.direction == "SHORT"]

    gross_pnl = sum(t.gross_pnl for t in trades)
    fees_paid = sum(t.fees_paid for t in trades)
    funding_paid = sum(t.funding_paid for t in trades)
    net_pnl = sum(t.net_pnl for t in trades)

    gross_wins = sum(t.gross_pnl for t in trades if t.gross_pnl > 0)
    gross_losses = abs(sum(t.gross_pnl for t in trades if t.gross_pnl < 0))
    pf = (gross_wins / gross_losses) if gross_losses > 1e-6 else (99.0 if gross_wins > 0 else 0.0)

    equity = initial_cap
    peak = equity
    max_dd = 0.0
    for t in trades:
        equity += t.net_pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    durations = [t.duration_hours for t in trades]
    avg_duration = sum(durations) / len(durations) if durations else 0.0

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100.0, 2),
        "gross_pnl": round(gross_pnl, 2),
        "fees_paid": round(fees_paid, 2),
        "funding_paid": round(funding_paid, 2),
        "net_pnl": round(net_pnl, 2),
        "net_return_pct": round(net_pnl / initial_cap * 100.0, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "avg_trade_pnl": round(net_pnl / len(trades), 4),
        "avg_duration_hrs": round(avg_duration, 2),
        "long_trades": len(longs),
        "short_trades": len(shorts),
    }


def run_15m_simulation(
    loader: DataLoader,
    pairs: List[str],
    tp1_rr_mult: float = 1.65,
    min_wick: float = 0.20,
    min_vol: float = 1.10,
    full_exit: bool = True,
    is_portfolio: bool = True,
) -> Tuple[Dict[str, Any], List[TradeResult]]:
    """Runs 15m execution simulation across specified pairs."""
    start_eval_ts = BASE_CONFIG["backtest_start_ts"]
    end_eval_ts = BASE_CONFIG["backtest_end_ts"]

    all_ts = sorted(list(set(
        c.timestamp_ms
        for sym in pairs
        for c in loader.candles_15m[sym]
    )))

    c15_map = {sym: {c.timestamp_ms: c for c in loader.candles_15m[sym]} for sym in pairs}
    c15_idx_map = {sym: {c.timestamp_ms: i for i, c in enumerate(loader.candles_15m[sym])} for sym in pairs}

    open_positions: Dict[str, Position] = {}
    pending_orders: Dict[str, PendingOrder] = {}
    trade_history: List[TradeResult] = []

    h1_ptrs = {sym: 0 for sym in pairs}
    fund_ptrs = {sym: 0 for sym in pairs}
    order_counter = 0
    trade_counter = 0
    equity = BASE_CONFIG["portfolio_capital"]

    for ts in all_ts:
        c15_close_ms = ts + 900000 - 1

        for sym in pairs:
            c15 = c15_map[sym].get(ts)
            if not c15:
                continue

            c1_list = loader.candles_1h[sym]
            h1_times = loader.h1_close_times[sym]
            while h1_ptrs[sym] + 1 < len(c1_list) and h1_times[h1_ptrs[sym] + 1] <= c15_close_ms:
                h1_ptrs[sym] += 1

            pos = open_positions.get(sym)
            pord = pending_orders.get(sym)

            new_pos, new_pord, trade_res, fund_ptrs[sym], order_counter, trade_counter = simulate_bar(
                c_bar=c15,
                open_pos=pos,
                pending_order=pord,
                funding_list=loader.funding_rates[sym],
                fund_ptr=fund_ptrs[sym],
                order_counter=order_counter,
                trade_counter=trade_counter,
                symbol=sym,
                maker_fee=BASE_CONFIG["maker_fee_rate"],
                taker_fee=BASE_CONFIG["taker_fee_rate"],
                slippage=BASE_CONFIG["adverse_slippage_rate"],
                full_exit_at_tp1=full_exit,
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
                equity += trade_res.net_pnl

        if c15_close_ms >= start_eval_ts and c15_close_ms <= end_eval_ts:
            for sym in pairs:
                if sym in open_positions or sym in pending_orders:
                    continue

                if is_portfolio:
                    total_open = len(open_positions) + len(pending_orders)
                    if total_open >= BASE_CONFIG["max_open_positions_portfolio"]:
                        break

                c15 = c15_map[sym].get(ts)
                if not c15:
                    continue

                idx = c15_idx_map[sym][ts]
                if idx < 60:
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
                cand_order = evaluate_signal_generic(
                    c_bar=c15,
                    recent_bars=loader.candles_15m[sym][idx - 60 : idx],
                    atr_val=loader.atr_15m[sym][idx],
                    htf_bias=h1_bias,
                    symbol=sym,
                    order_counter=order_counter,
                    timeframe="15m",
                    tp1_rr_mult=tp1_rr_mult,
                    min_wick_ratio=min_wick,
                    min_vol_mult=min_vol,
                    stop_floor_pct=BASE_CONFIG["min_stop_distance_pct_15m"],
                    full_exit=full_exit,
                    ttl_bars=BASE_CONFIG["pending_order_ttl_bars_15m"],
                )

                if cand_order:
                    if is_portfolio:
                        direction = cand_order.direction
                        same_dir_count = sum(1 for p in open_positions.values() if p.direction == direction) + sum(1 for o in pending_orders.values() if o.direction == direction)
                        if same_dir_count < BASE_CONFIG["max_open_per_direction"]:
                            pending_orders[sym] = cand_order
                    else:
                        pending_orders[sym] = cand_order

    metrics = calculate_metrics(trade_history, initial_cap=100.0)
    return metrics, trade_history


def run_1h_simulation(
    loader: DataLoader,
    pairs: List[str],
    tp1_rr_mult: float = 1.50,
    min_wick: float = 0.20,
    min_vol: float = 1.10,
    full_exit: bool = True,
    is_portfolio: bool = True,
) -> Tuple[Dict[str, Any], List[TradeResult]]:
    """Runs 1H execution simulation across specified pairs."""
    start_eval_ts = BASE_CONFIG["backtest_start_ts"]
    end_eval_ts = BASE_CONFIG["backtest_end_ts"]

    all_ts = sorted(list(set(
        c.timestamp_ms
        for sym in pairs
        for c in loader.candles_1h[sym]
    )))

    c1_map = {sym: {c.timestamp_ms: c for c in loader.candles_1h[sym]} for sym in pairs}
    c1_idx_map = {sym: {c.timestamp_ms: i for i, c in enumerate(loader.candles_1h[sym])} for sym in pairs}

    open_positions: Dict[str, Position] = {}
    pending_orders: Dict[str, PendingOrder] = {}
    trade_history: List[TradeResult] = []

    h4_ptrs = {sym: 0 for sym in pairs}
    fund_ptrs = {sym: 0 for sym in pairs}
    order_counter = 0
    trade_counter = 0
    equity = BASE_CONFIG["portfolio_capital"]

    for ts in all_ts:
        c1h_close_ms = ts + 3600000 - 1

        for sym in pairs:
            c1h = c1_map[sym].get(ts)
            if not c1h:
                continue

            c4_list = loader.candles_4h[sym]
            h4_times = loader.h4_close_times[sym]
            while h4_ptrs[sym] + 1 < len(c4_list) and h4_times[h4_ptrs[sym] + 1] <= c1h_close_ms:
                h4_ptrs[sym] += 1

            pos = open_positions.get(sym)
            pord = pending_orders.get(sym)

            new_pos, new_pord, trade_res, fund_ptrs[sym], order_counter, trade_counter = simulate_bar(
                c_bar=c1h,
                open_pos=pos,
                pending_order=pord,
                funding_list=loader.funding_rates[sym],
                fund_ptr=fund_ptrs[sym],
                order_counter=order_counter,
                trade_counter=trade_counter,
                symbol=sym,
                maker_fee=BASE_CONFIG["maker_fee_rate"],
                taker_fee=BASE_CONFIG["taker_fee_rate"],
                slippage=BASE_CONFIG["adverse_slippage_rate"],
                full_exit_at_tp1=full_exit,
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
                equity += trade_res.net_pnl

        if c1h_close_ms >= start_eval_ts and c1h_close_ms <= end_eval_ts:
            for sym in pairs:
                if sym in open_positions or sym in pending_orders:
                    continue

                if is_portfolio:
                    total_open = len(open_positions) + len(pending_orders)
                    if total_open >= BASE_CONFIG["max_open_positions_portfolio"]:
                        break

                c1h = c1_map[sym].get(ts)
                if not c1h:
                    continue

                idx = c1_idx_map[sym][ts]
                if idx < 60:
                    continue

                c4_list = loader.candles_4h[sym]
                e20_4h = loader.ema20_4h[sym]
                e50_4h = loader.ema50_4h[sym]
                h4_ptr = h4_ptrs[sym]

                h4_bias = "NEUTRAL"
                if h4_ptr >= 49:
                    c4 = c4_list[h4_ptr]
                    e20 = e20_4h[h4_ptr]
                    e50 = e50_4h[h4_ptr]
                    if e20 is not None and e50 is not None:
                        if c4.close > e20 and e20 >= e50:
                            h4_bias = "BULLISH"
                        elif c4.close < e20 and e20 <= e50:
                            h4_bias = "BEARISH"

                order_counter += 1
                cand_order = evaluate_signal_generic(
                    c_bar=c1h,
                    recent_bars=loader.candles_1h[sym][idx - 60 : idx],
                    atr_val=loader.atr_1h[sym][idx],
                    htf_bias=h4_bias,
                    symbol=sym,
                    order_counter=order_counter,
                    timeframe="1h",
                    tp1_rr_mult=tp1_rr_mult,
                    min_wick_ratio=min_wick,
                    min_vol_mult=min_vol,
                    stop_floor_pct=BASE_CONFIG["min_stop_distance_pct_1h"],
                    full_exit=full_exit,
                    ttl_bars=BASE_CONFIG["pending_order_ttl_bars_1h"],
                )

                if cand_order:
                    if is_portfolio:
                        direction = cand_order.direction
                        same_dir_count = sum(1 for p in open_positions.values() if p.direction == direction) + sum(1 for o in pending_orders.values() if o.direction == direction)
                        if same_dir_count < BASE_CONFIG["max_open_per_direction"]:
                            pending_orders[sym] = cand_order
                    else:
                        pending_orders[sym] = cand_order

    metrics = calculate_metrics(trade_history, initial_cap=100.0)
    return metrics, trade_history


def main():
    t_start = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 100)
    print("STARTING FULL REFINED 12-MONTH HISTORICAL BACKTEST (V3 REFINED & 1H UPGRADED)")
    print("=" * 100)

    loader = DataLoader(TARGET_DB)
    print(f"Loaded all data in {time.time() - t_start:.2f}s\n")

    # 1. RUN 15M PORTFOLIOS
    print(">>> Running 15M Execution Portfolios...")

    # A. V3 Refined (1.65R, Wick 20%, Vol 1.1x, Active Universe: BTC, ETH, LTC, SOL, DOGE)
    t0 = time.time()
    m_v3_active, tr_v3_active = run_15m_simulation(
        loader, V3_REFINED_ACTIVE, tp1_rr_mult=1.65, min_wick=0.20, min_vol=1.10, full_exit=True, is_portfolio=True
    )
    print(f"  [+] V3 Refined (Active 5 Pairs @ 1.65R) in {time.time() - t0:.2f}s")

    # B. V3 Alpha Cluster (1.65R, BTC, ETH, LTC, SOL)
    t0 = time.time()
    m_v3_alpha, tr_v3_alpha = run_15m_simulation(
        loader, V3_ALPHA_CLUSTER, tp1_rr_mult=1.65, min_wick=0.20, min_vol=1.10, full_exit=True, is_portfolio=True
    )
    print(f"  [+] V3 Alpha Cluster (BTC, ETH, LTC, SOL @ 1.65R) in {time.time() - t0:.2f}s")

    # C. V3 All 10 Pairs @ 1.65R
    t0 = time.time()
    m_v3_all10, tr_v3_all10 = run_15m_simulation(
        loader, TOP_10_PAIRS, tp1_rr_mult=1.65, min_wick=0.20, min_vol=1.10, full_exit=True, is_portfolio=True
    )
    print(f"  [+] V3 All 10 Pairs (@ 1.65R) in {time.time() - t0:.2f}s")

    # 2. RUN 1H PORTFOLIOS
    print("\n>>> Running 1H Execution Portfolios...")

    # A. Normal 1H Upgraded (1.50R, Wick 20%, Vol 1.1x, Full TP1, Active 7 Pairs)
    t0 = time.time()
    m_1h_active, tr_1h_active = run_1h_simulation(
        loader, H1_UPGRADED_ACTIVE, tp1_rr_mult=1.50, min_wick=0.20, min_vol=1.10, full_exit=True, is_portfolio=True
    )
    print(f"  [+] Normal 1H Upgraded (Active 7 Pairs) in {time.time() - t0:.2f}s")

    # B. Normal 1H Upgraded (All 10 Pairs)
    t0 = time.time()
    m_1h_all10, tr_1h_all10 = run_1h_simulation(
        loader, TOP_10_PAIRS, tp1_rr_mult=1.50, min_wick=0.20, min_vol=1.10, full_exit=True, is_portfolio=True
    )
    print(f"  [+] Normal 1H Upgraded (All 10 Pairs) in {time.time() - t0:.2f}s")

    # C. Normal 1H Baseline (V2 Trailing Runner, No Wick/Vol filter, All 10 Pairs)
    t0 = time.time()
    m_1h_base, tr_1h_base = run_1h_simulation(
        loader, TOP_10_PAIRS, tp1_rr_mult=1.50, min_wick=0.0, min_vol=0.0, full_exit=False, is_portfolio=True
    )
    print(f"  [+] Normal 1H Baseline (V2 Trailing, All 10 Pairs) in {time.time() - t0:.2f}s")

    # 3. RUN PER-PAIR EVALUATIONS (Independent $100 per pair)
    print("\n>>> Running Independent Per-Pair Evaluations for V3 Refined (1.65R)...")
    per_pair_v3_165 = {}
    for sym in TOP_10_PAIRS:
        m, _ = run_15m_simulation(loader, [sym], tp1_rr_mult=1.65, min_wick=0.20, min_vol=1.10, full_exit=True, is_portfolio=False)
        per_pair_v3_165[sym] = m

    print(">>> Running Independent Per-Pair Evaluations for 1H Upgraded...")
    per_pair_1h_upgraded = {}
    for sym in TOP_10_PAIRS:
        m, _ = run_1h_simulation(loader, [sym], tp1_rr_mult=1.50, min_wick=0.20, min_vol=1.10, full_exit=True, is_portfolio=False)
        per_pair_1h_upgraded[sym] = m

    # 4. PRINT MASTER COMPARISON TABLES
    print("\n" + "=" * 120)
    print("MASTER REFINED PORTFOLIO MATRIX: 12-MONTH HISTORICAL BACKTEST")
    print("=" * 120)
    print(f"{'Strategy Configuration':<36} | {'Trades':<7} | {'WinRate':<8} | {'Gross PnL':<11} | {'Fees Paid':<11} | {'Net PnL':<11} | {'ROI %':<9} | {'PF':<5} | {'Max DD':<8}")
    print("-" * 120)

    all_configs = [
        ("V1 Legacy Baseline (15m)", {"total_trades": 8053, "win_rate": 40.89, "gross_pnl": -308.21, "fees_paid": 785.36, "net_pnl": -1098.85, "net_return_pct": -1098.85, "profit_factor": 0.91, "max_drawdown_pct": 1075.97}),
        ("V2 Fast Scalper Baseline (15m)", {"total_trades": 3959, "win_rate": 39.78, "gross_pnl": -169.02, "fees_paid": 311.58, "net_pnl": -485.70, "net_return_pct": -485.70, "profit_factor": 0.93, "max_drawdown_pct": 479.16}),
        ("V3 Initial (15m @ 1.50R, Old Blacklist)", {"total_trades": 1403, "win_rate": 40.41, "gross_pnl": -31.18, "fees_paid": 107.33, "net_pnl": -140.88, "net_return_pct": -140.88, "profit_factor": 0.96, "max_drawdown_pct": 140.02}),
        ("V3 Refined (15m @ 1.65R, Active 5)", m_v3_active),
        ("V3 Alpha Cluster (15m @ 1.65R, Top 4)", m_v3_alpha),
        ("V3 Refined (15m @ 1.65R, All 10)", m_v3_all10),
        ("Normal 1H/4H Baseline (Trailing)", m_1h_base),
        ("Normal 1H Upgraded (Active 7)", m_1h_active),
        ("Normal 1H Upgraded (All 10)", m_1h_all10),
    ]

    for name, res in all_configs:
        print(f"{name:<36} | {res['total_trades']:<7} | {res['win_rate']:>6.2f}% | {res['gross_pnl']:>10.2f}$ | {res['fees_paid']:>10.2f}$ | {res['net_pnl']:>10.2f}$ | {res['net_return_pct']:>7.2f}% | {res['profit_factor']:>5.2f} | {res['max_drawdown_pct']:>7.2f}%")

    print("\n" + "=" * 125)
    print("PER-PAIR PERFORMANCE: V3 REFINED 15M (1.65R) vs NORMAL 1H UPGRADED ($100 per pair)")
    print("=" * 125)
    print(f"{'Pair':<10} | {'V3 Trades':<10} | {'V3 Gross':<10} | {'V3 Fees':<9} | {'V3 Net':<10} | {'V3 PF':<6} | {'1H Trades':<10} | {'1H Gross':<10} | {'1H Fees':<9} | {'1H Net':<10} | {'1H PF':<6} | {'V3 Status':<12}")
    print("-" * 125)

    for sym in TOP_10_PAIRS:
        v3_m = per_pair_v3_165[sym]
        h1_m = per_pair_1h_upgraded[sym]
        v3_status = "Blacklisted" if sym in V3_REFINED_BLACKLIST else ("PROFITABLE" if v3_m["net_pnl"] > 0 else "Active")
        print(f"{sym:<10} | {v3_m['total_trades']:<10} | {v3_m['gross_pnl']:>9.2f}$ | {v3_m['fees_paid']:>8.2f}$ | {v3_m['net_pnl']:>9.2f}$ | {v3_m['profit_factor']:>6.2f} | {h1_m['total_trades']:<10} | {h1_m['gross_pnl']:>9.2f}$ | {h1_m['fees_paid']:>8.2f}$ | {h1_m['net_pnl']:>9.2f}$ | {h1_m['profit_factor']:>6.2f} | {v3_status:<12}")

    # 5. SAVE CSVs
    with open(f"{OUTPUT_DIR}/master_refined_comparison.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Configuration", "Trades", "Win Rate %", "Gross PnL", "Fees Paid", "Net PnL", "ROI %", "Profit Factor", "Max Drawdown %"])
        for name, res in all_configs:
            w.writerow([name, res["total_trades"], res["win_rate"], res["gross_pnl"], res["fees_paid"], res["net_pnl"], res["net_return_pct"], res["profit_factor"], res["max_drawdown_pct"]])

    with open(f"{OUTPUT_DIR}/per_pair_v3_165_vs_1h_upgraded.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Pair", "V3 165 Trades", "V3 165 Gross", "V3 165 Fees", "V3 165 Net", "V3 165 WR %", "V3 165 PF", "1H Upgraded Trades", "1H Upgraded Gross", "1H Upgraded Fees", "1H Upgraded Net", "1H Upgraded WR %", "1H Upgraded PF", "V3 Status"])
        for sym in TOP_10_PAIRS:
            v3_m = per_pair_v3_165[sym]
            h1_m = per_pair_1h_upgraded[sym]
            status = "Blacklisted" if sym in V3_REFINED_BLACKLIST else ("PROFITABLE" if v3_m["net_pnl"] > 0 else "Active")
            w.writerow([sym, v3_m["total_trades"], v3_m["gross_pnl"], v3_m["fees_paid"], v3_m["net_pnl"], v3_m["win_rate"], v3_m["profit_factor"], h1_m["total_trades"], h1_m["gross_pnl"], h1_m["fees_paid"], h1_m["net_pnl"], h1_m["win_rate"], h1_m["profit_factor"], status])

    print(f"\n[OK] All results saved to {OUTPUT_DIR}/")
    print(f"Total Execution Time: {time.time() - t_start:.2f} seconds.")


if __name__ == "__main__":
    main()
