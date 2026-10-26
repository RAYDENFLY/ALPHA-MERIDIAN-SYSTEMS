"""
Model B Normal Runner (1H Trigger / 4H Trend) — Top 10 Pair Annual Historical Backtest Runner
=============================================================================================
Comprehensive, lookahead-free historical evaluation across Top 10 perpetual pairs:
BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, BNBUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, LTCUSDT.

Period: 2025-10-24 00:00:00 UTC to 2026-10-24 15:00:00 UTC (12 Months).

Evaluates:
  1. Baseline (1H/4H Normal V2 Remediated: 1.50% min stop floor, 1.25x ATR, 50% TP1 @ 1.5R + Trailing Runner)
  2. Variant 1 (1H_VOLUME_CLIMAX_SWEEP: Vol >= 1.5x SMA20 AND Rejection Wick >= 35%)
  3. Variant 2A (1H_FULL_TP1_1.5R: 100% Full Exit at 1.5R, No Partial/Trailing)
  4. Variant 2B (1H_FULL_TP_2.0R: 100% Full Exit at 2.0R, No Partial/Trailing)
  5. Variant 3 (1H_REGIME_ADAPTIVE: ATR% >= 1.00% AND 4H EMA Spread >= 0.50%)
  6. Variant Combined Best (Synthesis of highest-expectancy structural filters)
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
OUTPUT_DIR = "backtest_results/model_b_1h_annual_top10"

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

BASE_CONFIG = {
    "model_id": "MODEL_B_1H",
    "cohort_version": "MODEL_B_V2_REMEDIATED",
    "trigger_timeframe": "1h",
    "trend_timeframe": "4h",
    "initial_capital_per_pair": 100.0,
    "portfolio_capital": 100.0,
    "target_trade_risk_usdt": 1.00,
    "max_isolated_margin_usdt": 15.00,
    "leverage": 10.0,
    "min_stop_distance_pct": 0.015,  # 1.50% stop floor for 1H
    "min_stop_atr_multiple": 1.25,
    "pending_order_ttl_bars": 6,     # 6 hours
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


class DataLoader1H:
    """Preloads and indexes all 1H candles, 4H candles, and funding rates."""

    def __init__(self, db_path: str = TARGET_DB):
        self.db_path = db_path
        self.candles_1h: Dict[str, List[CandleBar]] = {}
        self.candles_4h: Dict[str, List[CandleBar]] = {}
        self.funding_rates: Dict[str, List[Tuple[int, float]]] = {}
        self.ema20_4h: Dict[str, List[Optional[float]]] = {}
        self.ema50_4h: Dict[str, List[Optional[float]]] = {}
        self.h4_close_times: Dict[str, List[int]] = {}
        self.atr_1h: Dict[str, List[float]] = {}
        self.vol_sma20_1h: Dict[str, List[float]] = {}
        self.load_all()

    def load_all(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        for sym in TOP_10_PAIRS:
            # 1H candles
            cur.execute("""
                SELECT timestamp_ms, open, high, low, close, volume
                FROM raw_candles
                WHERE symbol=? AND timeframe='1h'
                ORDER BY timestamp_ms ASC
            """, (sym,))
            c1h = [CandleBar(*r) for r in cur.fetchall()]
            self.candles_1h[sym] = c1h

            # Pre-compute 1H ATR (14 period)
            trs = [0.0] * len(c1h)
            for i in range(1, len(c1h)):
                trs[i] = max(c1h[i].high - c1h[i].low, abs(c1h[i].high - c1h[i - 1].close), abs(c1h[i].low - c1h[i - 1].close))
            atrs = [0.0] * len(c1h)
            if len(c1h) >= 15:
                running_tr = sum(trs[1:15])
                atrs[14] = running_tr / 14.0
                for i in range(15, len(c1h)):
                    running_tr += trs[i] - trs[i - 14]
                    atrs[i] = running_tr / 14.0
            self.atr_1h[sym] = atrs

            # Pre-compute 1H Volume SMA 20
            vols = [c.volume for c in c1h]
            vol_sma = [0.0] * len(c1h)
            if len(c1h) >= 20:
                running_vol = sum(vols[:20])
                vol_sma[19] = running_vol / 20.0
                for i in range(20, len(c1h)):
                    running_vol += vols[i] - vols[i - 20]
                    vol_sma[i] = running_vol / 20.0
            self.vol_sma20_1h[sym] = vol_sma

            # 4H candles
            cur.execute("""
                SELECT timestamp_ms, open, high, low, close, volume
                FROM raw_candles
                WHERE symbol=? AND timeframe='4h'
                ORDER BY timestamp_ms ASC
            """, (sym,))
            c4h = [CandleBar(*r) for r in cur.fetchall()]
            self.candles_4h[sym] = c4h

            h4_closes = [c.close for c in c4h]
            # 4H candle opens at timestamp_ms, closes at timestamp_ms + 4*3600000 - 1
            self.h4_close_times[sym] = [c.timestamp_ms + 4 * 3600000 - 1 for c in c4h]
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


def simulate_bar_1h(
    c1h: CandleBar,
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
    exit_mode: str = "PARTIAL_TRAILING",  # "PARTIAL_TRAILING", "FULL_TP1", "FULL_TP_2R"
) -> Tuple[Optional[Position], Optional[PendingOrder], Optional[TradeResult], int, int, int]:
    """Execute order and position state transitions for a single 1H bar."""
    trade_res: Optional[TradeResult] = None
    num_funding = len(funding_list)

    # 1. Settle funding if in position
    if open_pos:
        while fund_ptr < num_funding and funding_list[fund_ptr][0] <= c1h.timestamp_ms:
            fts, frate = funding_list[fund_ptr]
            if fts > open_pos.last_funding_time_ms:
                payment = (open_pos.notional * frate) if open_pos.direction == "LONG" else (-open_pos.notional * frate)
                open_pos.funding_paid += payment
                open_pos.last_funding_time_ms = fts
            fund_ptr += 1
    else:
        while fund_ptr < num_funding and funding_list[fund_ptr][0] <= c1h.timestamp_ms:
            fund_ptr += 1

    # 2. Evaluate open position exits
    if open_pos:
        hit_tp = False
        hit_sl = False
        exit_price = 0.0
        exit_reason = ""

        if open_pos.direction == "LONG":
            hit_tp = c1h.high >= open_pos.current_tp
            hit_sl = c1h.low <= open_pos.current_sl

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
                if exit_mode in ("FULL_TP1", "FULL_TP_2R"):
                    # 100% exit at target
                    exit_price = open_pos.current_tp
                    exit_reason = "TAKE_PROFIT_FULL"
                else:
                    # PARTIAL_TRAILING baseline
                    if not open_pos.tp1_hit and open_pos.tp2 is not None:
                        tp1_price = open_pos.initial_tp1
                        closed_qty = open_pos.quantity * 0.5
                        remain_qty = open_pos.quantity - closed_qty
                        partial_gross = closed_qty * (tp1_price - open_pos.entry_price)
                        partial_fee = (closed_qty * tp1_price) * taker_fee
                        open_pos.partial_gross_pnl += partial_gross
                        open_pos.partial_fee_paid += partial_fee
                        open_pos.tp1_hit = True
                        open_pos.tp1_hit_time_ms = c1h.timestamp_ms
                        open_pos.quantity = remain_qty
                        open_pos.notional = remain_qty * open_pos.entry_price

                        be_price = open_pos.entry_price * (1.0 + maker_fee + taker_fee + slippage)
                        open_pos.breakeven_price = be_price
                        open_pos.current_sl = max(open_pos.current_sl, be_price)
                        open_pos.current_tp = open_pos.tp2
                        open_pos.trailing_active = True
                        init_r = abs(open_pos.entry_price - open_pos.initial_sl)
                        open_pos.trailing_distance = init_r * 1.0
                        open_pos.favorable_extreme = max(tp1_price, c1h.high)

                        if c1h.high >= open_pos.tp2:
                            exit_price = open_pos.tp2
                            exit_reason = "TAKE_PROFIT_2"
                    else:
                        exit_price = open_pos.current_tp
                        exit_reason = "TAKE_PROFIT_2" if open_pos.tp1_hit else "TAKE_PROFIT"

        elif open_pos.direction == "SHORT":
            hit_tp = c1h.low <= open_pos.current_tp
            hit_sl = c1h.high >= open_pos.current_sl

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
                if exit_mode in ("FULL_TP1", "FULL_TP_2R"):
                    exit_price = open_pos.current_tp
                    exit_reason = "TAKE_PROFIT_FULL"
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
                        open_pos.tp1_hit_time_ms = c1h.timestamp_ms
                        open_pos.quantity = remain_qty
                        open_pos.notional = remain_qty * open_pos.entry_price

                        be_price = open_pos.entry_price * (1.0 - (maker_fee + taker_fee + slippage))
                        open_pos.breakeven_price = be_price
                        open_pos.current_sl = min(open_pos.current_sl, be_price)
                        open_pos.current_tp = open_pos.tp2
                        open_pos.trailing_active = True
                        init_r = abs(open_pos.initial_sl - open_pos.entry_price)
                        open_pos.trailing_distance = init_r * 1.0
                        open_pos.favorable_extreme = min(tp1_price, c1h.low)

                        if c1h.low <= open_pos.tp2:
                            exit_price = open_pos.tp2
                            exit_reason = "TAKE_PROFIT_2"
                    else:
                        exit_price = open_pos.current_tp
                        exit_reason = "TAKE_PROFIT_2" if open_pos.tp1_hit else "TAKE_PROFIT"

        # Trailing stop update if open
        if open_pos and exit_reason == "" and open_pos.trailing_active:
            if open_pos.direction == "LONG":
                open_pos.favorable_extreme = max(open_pos.favorable_extreme, c1h.high)
                trail_cand = open_pos.favorable_extreme - open_pos.trailing_distance
                if trail_cand > open_pos.current_sl:
                    open_pos.current_sl = max(open_pos.current_sl, open_pos.breakeven_price, trail_cand)
            else:
                open_pos.favorable_extreme = min(open_pos.favorable_extreme, c1h.low)
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

            duration_hrs = (c1h.timestamp_ms - open_pos.entry_time_ms) / 3600000.0
            initial_r = abs(open_pos.entry_price - open_pos.initial_sl)
            eff_exit_px = (
                ((open_pos.initial_quantity - open_pos.quantity) * open_pos.initial_tp1 + open_pos.quantity * exit_price) / open_pos.initial_quantity
                if open_pos.tp1_hit else exit_price
            )
            gain = (eff_exit_px - open_pos.entry_price) if open_pos.direction == "LONG" else (open_pos.entry_price - eff_exit_px)
            realized_rr = gain / initial_r if initial_r > 0 else 0.0

            dt_entry = datetime.fromtimestamp(open_pos.entry_time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            dt_exit = datetime.fromtimestamp(c1h.timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

            trade_res = TradeResult(
                trade_id=open_pos.trade_id,
                symbol=symbol,
                direction=open_pos.direction,
                entry_time_ms=open_pos.entry_time_ms,
                exit_time_ms=c1h.timestamp_ms,
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
        if c1h.timestamp_ms >= pending_order.expiry_time_ms:
            pending_order = None
        else:
            filled = False
            if pending_order.direction == "LONG" and c1h.low <= pending_order.entry_price:
                filled = True
            elif pending_order.direction == "SHORT" and c1h.high >= pending_order.entry_price:
                filled = True

            if filled:
                entry_fee = pending_order.notional * maker_fee
                open_pos = Position(
                    trade_id=f"PTRD_{symbol}_{trade_counter}",
                    symbol=symbol,
                    direction=pending_order.direction,
                    entry_time_ms=c1h.timestamp_ms,
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
                    last_funding_time_ms=c1h.timestamp_ms,
                )
                trade_counter += 1
                pending_order = None

    return open_pos, pending_order, trade_res, fund_ptr, order_counter, trade_counter


def evaluate_signal_1h(
    c1h: CandleBar,
    recent_1h: List[CandleBar],
    atr_val: float,
    vol_sma_val: float,
    h4_bias: str,
    h4_ema20: Optional[float],
    h4_ema50: Optional[float],
    h4_close: Optional[float],
    symbol: str,
    order_counter: int,
    cfg: Dict[str, Any],
    variant_name: str = "BASELINE",
) -> Optional[PendingOrder]:
    """Evaluate confirmed 1H candle body close and sweep-reclaim with optional variant filters."""
    last_closed = c1h
    prev_c = recent_1h[-2] if len(recent_1h) >= 2 else last_closed
    sw_highs, sw_lows = find_recent_swings_slice(recent_1h[:-1], left=3, right=2)

    target_risk = cfg["target_trade_risk_usdt"]
    stop_floor_pct = cfg["min_stop_distance_pct"]
    stop_atr_mult = cfg["min_stop_atr_multiple"]
    pending_ttl_bars = cfg["pending_order_ttl_bars"]
    max_margin = cfg["max_isolated_margin_usdt"]
    leverage = cfg["leverage"]

    # Filter 1: Volume Climax & Rejection Wick (Variant 1 & Combined)
    use_volume_climax = variant_name in ("1H_VOLUME_CLIMAX_SWEEP", "COMBINED_BEST")
    # Filter 3: Regime Adaptive Volatility & EMA Slope (Variant 3 & Combined)
    use_regime_adaptive = variant_name in ("1H_REGIME_ADAPTIVE", "COMBINED_BEST")

    # Exit Mode for TP Target calculation
    exit_mode = "PARTIAL_TRAILING"
    if variant_name == "1H_FULL_TP1_1.5R":
        exit_mode = "FULL_TP1"
    elif variant_name == "1H_FULL_TP_2.0R":
        exit_mode = "FULL_TP_2R"

    # Regime Adaptive Check
    if use_regime_adaptive:
        if last_closed.close <= 0 or atr_val <= 0:
            return None
        atr_pct = atr_val / last_closed.close
        if atr_pct < 0.010:  # < 1.00%
            return None
        if h4_ema20 is None or h4_ema50 is None or h4_close is None or h4_close <= 0:
            return None
        ema_spread = abs(h4_ema20 - h4_ema50) / h4_close
        if ema_spread < 0.0050:  # < 0.50%
            return None

    candle_range = last_closed.high - last_closed.low

    # Long setup
    if sw_lows and h4_bias != "BEARISH":
        key_low = sw_lows[-1]
        swept = (last_closed.low < key_low) or (prev_c.low < key_low)
        reclaimed = swept and (last_closed.close > key_low)
        confirmed_green = last_closed.close > last_closed.open

        if reclaimed and confirmed_green:
            # Volume & Rejection Wick filter for Long
            if use_volume_climax:
                if vol_sma_val <= 0 or (last_closed.volume / vol_sma_val) < 1.50:
                    return None
                if candle_range <= 0:
                    return None
                lower_wick = min(last_closed.open, last_closed.close) - last_closed.low
                if (lower_wick / candle_range) < 0.35:
                    return None

            entry_px = last_closed.close
            raw_sl = min(last_closed.low, prev_c.low) * 0.999
            min_stop_dist = max(entry_px * stop_floor_pct, atr_val * stop_atr_mult)
            sl_px = min(raw_sl, entry_px - min_stop_dist)
            risk_dist = entry_px - sl_px

            if risk_dist > 0:
                if exit_mode == "FULL_TP1":
                    tp1 = round(entry_px + (risk_dist * 1.5), 8)
                    tp2 = None
                elif exit_mode == "FULL_TP_2R":
                    tp1 = round(entry_px + (risk_dist * 2.0), 8)
                    tp2 = None
                else:
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
                        expiry_time_ms=last_closed.timestamp_ms + (pending_ttl_bars * 3600 * 1000),
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
    elif sw_highs and h4_bias != "BULLISH":
        key_high = sw_highs[-1]
        swept = (last_closed.high > key_high) or (prev_c.high > key_high)
        reclaimed = swept and (last_closed.close < key_high)
        confirmed_red = last_closed.close < last_closed.open

        if reclaimed and confirmed_red:
            # Volume & Rejection Wick filter for Short
            if use_volume_climax:
                if vol_sma_val <= 0 or (last_closed.volume / vol_sma_val) < 1.50:
                    return None
                if candle_range <= 0:
                    return None
                upper_wick = last_closed.high - max(last_closed.open, last_closed.close)
                if (upper_wick / candle_range) < 0.35:
                    return None

            entry_px = last_closed.close
            raw_sl = max(last_closed.high, prev_c.high) * 1.001
            min_stop_dist = max(entry_px * stop_floor_pct, atr_val * stop_atr_mult)
            sl_px = max(raw_sl, entry_px + min_stop_dist)
            risk_dist = sl_px - entry_px

            if risk_dist > 0:
                if exit_mode == "FULL_TP1":
                    tp1 = round(entry_px - (risk_dist * 1.5), 8)
                    tp2 = None
                elif exit_mode == "FULL_TP_2R":
                    tp1 = round(entry_px - (risk_dist * 2.0), 8)
                    tp2 = None
                else:
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
                        expiry_time_ms=last_closed.timestamp_ms + (pending_ttl_bars * 3600 * 1000),
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


def run_portfolio_backtest_1h(
    loader: DataLoader1H,
    cfg: Dict[str, Any],
    variant_name: str = "BASELINE",
) -> Tuple[List[TradeResult], Dict[str, Any]]:
    """Runs simultaneous portfolio-level backtest across all Top 10 pairs on 1H timeframe."""
    all_trades: List[TradeResult] = []

    # Map timestamps across all symbols
    start_ts = cfg["backtest_start_ts"]
    end_ts = cfg["backtest_end_ts"]

    # Align timestamps: get sorted union of all 1h timestamps
    ts_set = set()
    for sym in TOP_10_PAIRS:
        for c in loader.candles_1h[sym]:
            if start_ts <= c.timestamp_ms <= end_ts:
                ts_set.add(c.timestamp_ms)
    sorted_ts = sorted(list(ts_set))

    # Fast indices per symbol
    candle_indices = {sym: 0 for sym in TOP_10_PAIRS}
    open_positions: Dict[str, Optional[Position]] = {sym: None for sym in TOP_10_PAIRS}
    pending_orders: Dict[str, Optional[PendingOrder]] = {sym: None for sym in TOP_10_PAIRS}
    funding_pointers = {sym: 0 for sym in TOP_10_PAIRS}

    order_counter = 1000
    trade_counter = 1000

    max_portfolio_pos = cfg["max_open_positions_portfolio"]
    max_dir_pos = cfg["max_open_per_direction"]
    maker_fee = cfg["maker_fee_rate"]
    taker_fee = cfg["taker_fee_rate"]
    slippage = cfg["adverse_slippage_rate"]

    exit_mode = "PARTIAL_TRAILING"
    if variant_name == "1H_FULL_TP1_1.5R":
        exit_mode = "FULL_TP1"
    elif variant_name == "1H_FULL_TP_2.0R":
        exit_mode = "FULL_TP_2R"

    # Pre-index 1H candle map for O(1) retrieval
    candles_by_ts: Dict[str, Dict[int, int]] = {}
    for sym in TOP_10_PAIRS:
        candles_by_ts[sym] = {c.timestamp_ms: idx for idx, c in enumerate(loader.candles_1h[sym])}

    # Step through time
    for current_ms in sorted_ts:
        # A. First pass: Update open positions and pending orders on current bar
        for sym in TOP_10_PAIRS:
            idx = candles_by_ts[sym].get(current_ms)
            if idx is None:
                continue
            c1h = loader.candles_1h[sym][idx]

            open_pos, pending_ord, tr_res, fptr, order_counter, trade_counter = simulate_bar_1h(
                c1h=c1h,
                open_pos=open_positions[sym],
                pending_order=pending_orders[sym],
                funding_list=loader.funding_rates[sym],
                fund_ptr=funding_pointers[sym],
                order_counter=order_counter,
                trade_counter=trade_counter,
                symbol=sym,
                maker_fee=maker_fee,
                taker_fee=taker_fee,
                slippage=slippage,
                exit_mode=exit_mode,
            )
            open_positions[sym] = open_pos
            pending_orders[sym] = pending_ord
            funding_pointers[sym] = fptr
            if tr_res:
                all_trades.append(tr_res)

        # Count active open positions and pending orders
        active_pos_count = sum(1 for p in open_positions.values() if p is not None)
        active_long_count = sum(1 for p in open_positions.values() if p and p.direction == "LONG")
        active_short_count = sum(1 for p in open_positions.values() if p and p.direction == "SHORT")

        # B. Second pass: Evaluate signals on closed bar for symbols without open position or pending order
        for sym in TOP_10_PAIRS:
            idx = candles_by_ts[sym].get(current_ms)
            if idx is None or idx < 30:
                continue
            if open_positions[sym] is not None or pending_orders[sym] is not None:
                continue
            if active_pos_count >= max_portfolio_pos:
                continue

            c1h = loader.candles_1h[sym][idx]
            c1h_close_time = c1h.timestamp_ms + 3600000 - 1

            # Determine 4H bias with strict zero-lookahead
            h4_close_times = loader.h4_close_times[sym]
            h4_idx = -1
            for j in range(len(h4_close_times) - 1, -1, -1):
                if h4_close_times[j] <= c1h_close_time:
                    h4_idx = j
                    break

            h4_bias = "NEUTRAL"
            h4_e20 = None
            h4_e50 = None
            h4_close_val = None
            if h4_idx >= 50:
                h4_c = loader.candles_4h[sym][h4_idx]
                h4_close_val = h4_c.close
                h4_e20 = loader.ema20_4h[sym][h4_idx]
                h4_e50 = loader.ema50_4h[sym][h4_idx]
                if h4_e20 is not None and h4_e50 is not None:
                    if h4_c.close > h4_e20 and h4_e20 >= h4_e50:
                        h4_bias = "BULLISH"
                    elif h4_c.close < h4_e20 and h4_e20 <= h4_e50:
                        h4_bias = "BEARISH"

            atr_val = loader.atr_1h[sym][idx]
            vol_sma_val = loader.vol_sma20_1h[sym][idx]
            recent_slice = loader.candles_1h[sym][max(0, idx - 29): idx + 1]

            new_order = evaluate_signal_1h(
                c1h=c1h,
                recent_1h=recent_slice,
                atr_val=atr_val,
                vol_sma_val=vol_sma_val,
                h4_bias=h4_bias,
                h4_ema20=h4_e20,
                h4_ema50=h4_e50,
                h4_close=h4_close_val,
                symbol=sym,
                order_counter=order_counter,
                cfg=cfg,
                variant_name=variant_name,
            )

            if new_order:
                # Check direction limit
                if new_order.direction == "LONG" and active_long_count >= max_dir_pos:
                    continue
                if new_order.direction == "SHORT" and active_short_count >= max_dir_pos:
                    continue

                pending_orders[sym] = new_order
                order_counter += 1

    # Compile portfolio metrics
    metrics = calculate_portfolio_metrics(all_trades, cfg["portfolio_capital"])
    return all_trades, metrics


def calculate_portfolio_metrics(trades: List[TradeResult], initial_capital: float = 100.0) -> Dict[str, Any]:
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "gross_pnl": 0.0,
            "total_fees": 0.0,
            "total_funding": 0.0,
            "net_pnl": 0.0,
            "roi_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_usdt": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_trade_pnl": 0.0,
            "avg_duration_hrs": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "win_count": 0,
            "loss_count": 0,
        }

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    gross_win = sum(t.gross_pnl for t in trades if t.gross_pnl > 0)
    gross_loss = abs(sum(t.gross_pnl for t in trades if t.gross_pnl < 0))

    gross_pnl = sum(t.gross_pnl for t in trades)
    total_fees = sum(t.fees_paid for t in trades)
    total_funding = sum(t.funding_paid for t in trades)
    net_pnl = sum(t.net_pnl for t in trades)

    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    win_rate = (len(wins) / total_trades) * 100.0

    # Drawdown calculation
    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0
    max_dd_pct = 0.0

    # Sort trades by exit time
    sorted_trades = sorted(trades, key=lambda x: x.exit_time_ms)
    for t in sorted_trades:
        equity += t.net_pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        dd_pct = (dd / peak * 100.0) if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    avg_dur = sum(t.duration_hours for t in trades) / total_trades
    long_count = sum(1 for t in trades if t.direction == "LONG")
    short_count = sum(1 for t in trades if t.direction == "SHORT")

    return {
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate, 2),
        "gross_pnl": round(gross_pnl, 2),
        "total_fees": round(total_fees, 2),
        "total_funding": round(total_funding, 2),
        "net_pnl": round(net_pnl, 2),
        "roi_pct": round((net_pnl / initial_capital) * 100.0, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_usdt": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "avg_trade_pnl": round(net_pnl / total_trades, 4),
        "avg_duration_hrs": round(avg_dur, 2),
        "long_trades": long_count,
        "short_trades": short_count,
        "win_count": len(wins),
        "loss_count": len(losses),
    }


def calculate_per_pair_metrics(trades: List[TradeResult]) -> Dict[str, Dict[str, Any]]:
    pair_res: Dict[str, Dict[str, Any]] = {}
    for sym in TOP_10_PAIRS:
        p_trades = [t for t in trades if t.symbol == sym]
        m = calculate_portfolio_metrics(p_trades, 100.0)
        pair_res[sym] = m
    return pair_res


def main():
    print("=" * 80)
    print("STARTING MODEL B (1H / 4H) 12-MONTH TOP 10 PAIRS HISTORICAL BACKTEST")
    print("Zero-Lookahead Continuous Execution | Maker 0.02% / Taker 0.05% / Slippage 0.05%")
    print("=" * 80)

    t0 = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Loading 1H candles, 4H candles, and historical funding rates from DB...")
    loader = DataLoader1H(TARGET_DB)
    print(f"Data preloaded in {time.time() - t0:.2f}s.")

    variants = [
        ("BASELINE", "1H/4H Baseline V2 Remediated (50% TP1 @ 1.5R + Trailing Runner)"),
        ("1H_VOLUME_CLIMAX_SWEEP", "Variant 1: Volume Climax (>=1.5x SMA20) & Rejection Wick (>=35%)"),
        ("1H_FULL_TP1_1.5R", "Variant 2A: 100% Full Exit at 1.5R (No Partial/Trailing)"),
        ("1H_FULL_TP_2.0R", "Variant 2B: 100% Full Exit at 2.0R (No Partial/Trailing)"),
        ("1H_REGIME_ADAPTIVE", "Variant 3: Regime Adaptive (ATR% >= 1.0% AND 4H EMA Spread >= 0.5%)"),
        ("COMBINED_BEST", "Variant Combined: Vol Climax + Regime Adaptive + Full TP1.5R"),
    ]

    summary_results: Dict[str, Any] = {}
    all_variant_trades: Dict[str, List[TradeResult]] = {}

    for var_code, var_desc in variants:
        t_var = time.time()
        print(f"\n---> Running Simulation: [{var_code}] - {var_desc} ...")
        trades, metrics = run_portfolio_backtest_1h(loader, BASE_CONFIG, variant_name=var_code)
        per_pair = calculate_per_pair_metrics(trades)

        all_variant_trades[var_code] = trades
        summary_results[var_code] = {
            "name": var_code,
            "description": var_desc,
            "portfolio_metrics": metrics,
            "per_pair_metrics": per_pair,
        }

        print(f"     Trades: {metrics['total_trades']} | Win Rate: {metrics['win_rate_pct']}% | Net PnL: ${metrics['net_pnl']} | PF: {metrics['profit_factor']} | MaxDD: {metrics['max_drawdown_pct']}% | Fees: ${metrics['total_fees']} | Duration: {time.time() - t_var:.2f}s")

    # Export variants summary JSON
    summary_path = os.path.join(OUTPUT_DIR, "variants_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary_results, f, indent=2)
    print(f"\n[OK] Saved variants summary JSON to: {summary_path}")

    # Determine best variant by Net PnL and Profit Factor
    best_variant = max(summary_results.keys(), key=lambda k: summary_results[k]["portfolio_metrics"]["net_pnl"])
    print(f"\n[*] Best Variant by Net PnL: {best_variant} (${summary_results[best_variant]['portfolio_metrics']['net_pnl']} Net PnL)")

    # Export Trades for all variants to CSV
    for var_code, v_trades in all_variant_trades.items():
        v_csv_path = os.path.join(OUTPUT_DIR, f"{var_code.lower()}_trades.csv")
        with open(v_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "trade_id", "symbol", "direction", "entry_time_utc", "exit_time_utc",
                "entry_price", "exit_price", "quantity", "notional",
                "gross_pnl", "net_pnl", "fees_paid", "funding_paid",
                "exit_reason", "duration_hours", "realized_rr"
            ])
            for t in v_trades:
                writer.writerow([
                    t.trade_id, t.symbol, t.direction, t.entry_time_utc, t.exit_time_utc,
                    t.entry_price, t.exit_price, t.quantity, t.notional,
                    t.gross_pnl, t.net_pnl, t.fees_paid, t.funding_paid,
                    t.exit_reason, t.duration_hours, t.realized_rr
                ])
        print(f"[OK] Exported {len(v_trades)} trades to: {v_csv_path}")

    print("\n" + "=" * 80)
    print("BACKTEST SUITE COMPLETED SUCCESSFULLY IN {:.2f} SECONDS".format(time.time() - t0))
    print("=" * 80)


if __name__ == "__main__":
    main()
