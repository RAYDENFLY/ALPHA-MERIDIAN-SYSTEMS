"""
Model B V1 vs V2 vs V3 Annual Historical Backtest Runner (Top 10 Perpetual Pairs)
================================================================================
Comprehensive, lookahead-free historical evaluation across Top 10 perpetual pairs:
BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, BNBUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, LTCUSDT.

Period: 2025-10-24 00:00:00 UTC to 2026-10-24 15:00:00 UTC (12 Months / 365.6 Days).
Candles: 354,850 15M candles + 90,040 1H candles + 10,950 funding intervals.

Compares:
  - V1 (Legacy 15M / 1H): Raw sweep reclaim, no body confirmation, no stop floor, 50% TP1 + trailing runner.
  - V2 (Remediated Fast 15M / 1H): Confirmed candle body close, 0.80% stop floor, dynamic >= 1.5R TP, 50% TP1 + trailing runner.
  - V3 (Tuned Hybrid 15M / 1H): Confirmed candle body close, 0.80% stop floor, dynamic >= 1.5R TP,
                               Rejection Wick >= 20%, Volume >= 1.10x SMA20, 100% Full Exit at TP1.
  - V3_BLACKLISTED: V3 portfolio with structural chop pairs (BTC, BNB, XRP, AVAX) excluded.
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
OUTPUT_DIR = "backtest_results/model_b_v1_v2_v3_comparison"

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

V3_CHOP_BLACKLIST = ["BTCUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT"]

BASE_CONFIG = {
    "initial_capital_per_pair": 100.0,
    "portfolio_capital": 100.0,
    "portfolio_capital_alt": 1000.0,
    "target_trade_risk_usdt": 1.00,
    "max_isolated_margin_usdt": 15.00,
    "leverage": 10.0,
    "min_stop_distance_pct": 0.008,  # 0.80% stop floor for V2 & V3
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
    full_exit_at_tp1: bool,
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
                if full_exit_at_tp1:
                    # MODEL_B_V3: 100% full exit immediately at TP1
                    exit_price = open_pos.current_tp
                    exit_reason = "TAKE_PROFIT_1_FULL"
                else:
                    # V1 / V2: 50% partial exit + trailing runner
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
                if full_exit_at_tp1:
                    # MODEL_B_V3: 100% full exit immediately at TP1
                    exit_price = open_pos.current_tp
                    exit_reason = "TAKE_PROFIT_1_FULL"
                else:
                    # V1 / V2: 50% partial exit + trailing runner
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
        if open_pos and exit_reason == "" and open_pos.trailing_active and not full_exit_at_tp1:
            if open_pos.direction == "LONG":
                open_pos.favorable_extreme = max(open_pos.favorable_extreme, c15.high)
                new_trail = open_pos.favorable_extreme - open_pos.trailing_distance
                if new_trail > open_pos.current_sl:
                    open_pos.current_sl = new_trail
            elif open_pos.direction == "SHORT":
                open_pos.favorable_extreme = min(open_pos.favorable_extreme, c15.low)
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

            duration_hrs = (c15.timestamp_ms - open_pos.entry_time_ms) / 3600000.0
            dt_entry = datetime.fromtimestamp(open_pos.entry_time_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            dt_exit = datetime.fromtimestamp(c15.timestamp_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

            init_risk = abs(open_pos.entry_price - open_pos.initial_sl) * open_pos.initial_quantity
            realized_rr = (total_gross / init_risk) if init_risk > 1e-6 else 0.0

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


def evaluate_signal_variant(
    version: str,  # "V1", "V2", "V3"
    c15: CandleBar,
    recent_15m: List[CandleBar],
    atr_val: float,
    h1_bias: str,
    symbol: str,
    order_counter: int,
    cfg: Dict[str, Any],
) -> Optional[PendingOrder]:
    """Generate signal candidate parameterized by version."""
    last_closed = c15
    prev_c = recent_15m[-2] if len(recent_15m) >= 2 else last_closed
    sw_highs, sw_lows = find_recent_swings_slice(recent_15m[:-1], left=3, right=2)

    target_risk = cfg["target_trade_risk_usdt"]
    pending_ttl_bars = cfg["pending_order_ttl_bars"]
    max_margin = cfg["max_isolated_margin_usdt"]
    leverage = cfg["leverage"]

    c_range = last_closed.high - last_closed.low

    # Check Candle Body Direction Confirmation (V2 and V3 require confirmed body close; V1 ignores)
    require_confirmed_body = (version in ("V2", "V3"))
    confirmed_green = (last_closed.close > last_closed.open) if require_confirmed_body else True
    confirmed_red = (last_closed.close < last_closed.open) if require_confirmed_body else True

    # Check V3 Rejection Wick & Relative Volume
    if version == "V3":
        if c_range <= 0.0:
            return None
        # Volume SMA check
        if len(recent_15m) >= 20:
            vols = [c.volume for c in recent_15m[-20:]]
            avg_vol = sum(vols) / len(vols)
            if avg_vol > 0.0 and (last_closed.volume / avg_vol) < 1.10:
                return None
        else:
            return None

    # LONG SETUP
    if sw_lows and h1_bias != "BEARISH":
        key_low = sw_lows[-1]
        swept = (last_closed.low < key_low) or (prev_c.low < key_low)
        reclaimed = swept and (last_closed.close > key_low)

        if reclaimed and confirmed_green:
            # V3 lower rejection wick check
            if version == "V3":
                lower_wick = min(last_closed.open, last_closed.close) - last_closed.low
                if (lower_wick / c_range) < 0.20:
                    return None

            entry_px = last_closed.close
            raw_sl = min(last_closed.low, prev_c.low) * 0.999

            if version == "V1":
                # V1: raw stop loss without stop floor
                sl_px = raw_sl
            else:
                # V2 & V3: 0.80% stop floor & 1.25x ATR floor
                stop_floor_pct = cfg["min_stop_distance_pct"]
                stop_atr_mult = cfg["min_stop_atr_multiple"]
                min_stop_dist = max(entry_px * stop_floor_pct, atr_val * stop_atr_mult)
                sl_px = min(raw_sl, entry_px - min_stop_dist)

            risk_dist = entry_px - sl_px
            if risk_dist > 0:
                tp1 = round(entry_px + (risk_dist * 1.5), 8)
                high_targets = [p for p in sw_highs if p > tp1]
                tp2 = round(high_targets[0] if high_targets else entry_px + (risk_dist * 2.5), 8) if version != "V3" else None

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

    # SHORT SETUP
    elif sw_highs and h1_bias != "BULLISH":
        key_high = sw_highs[-1]
        swept = (last_closed.high > key_high) or (prev_c.high > key_high)
        reclaimed = swept and (last_closed.close < key_high)

        if reclaimed and confirmed_red:
            # V3 upper rejection wick check
            if version == "V3":
                upper_wick = last_closed.high - max(last_closed.open, last_closed.close)
                if (upper_wick / c_range) < 0.20:
                    return None

            entry_px = last_closed.close
            raw_sl = max(last_closed.high, prev_c.high) * 1.001

            if version == "V1":
                # V1: raw stop loss without stop floor
                sl_px = raw_sl
            else:
                # V2 & V3: 0.80% stop floor & 1.25x ATR floor
                stop_floor_pct = cfg["min_stop_distance_pct"]
                stop_atr_mult = cfg["min_stop_atr_multiple"]
                min_stop_dist = max(entry_px * stop_floor_pct, atr_val * stop_atr_mult)
                sl_px = max(raw_sl, entry_px + min_stop_dist)

            risk_dist = sl_px - entry_px
            if risk_dist > 0:
                tp1 = round(entry_px - (risk_dist * 1.5), 8)
                low_targets = [p for p in sw_lows if p < tp1]
                tp2 = round(low_targets[0] if low_targets else entry_px - (risk_dist * 2.5), 8) if version != "V3" else None

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


def calculate_performance_metrics(trades: List[TradeResult], initial_cap: float = 100.0) -> Dict[str, Any]:
    """Calculates comprehensive performance metrics."""
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "gross_pnl": 0.0,
            "fees_paid": 0.0,
            "funding_paid": 0.0,
            "net_pnl": 0.0,
            "net_return_pct": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_trade_pnl": 0.0,
            "avg_duration_hrs": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "wins": 0,
            "losses": 0,
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

    # Max Drawdown
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


def run_per_pair_backtests(loader: DataLoader, version: str) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[TradeResult]]]:
    """Runs independent $100 per-pair backtest for the specified version."""
    results: Dict[str, Dict[str, Any]] = {}
    trades_by_sym: Dict[str, List[TradeResult]] = {}
    full_exit = (version == "V3")

    start_eval_ts = BASE_CONFIG["backtest_start_ts"]
    end_eval_ts = BASE_CONFIG["backtest_end_ts"]

    for sym in TOP_10_PAIRS:
        c15_list = loader.candles_15m[sym]
        c1_list = loader.candles_1h[sym]
        fund_list = loader.funding_rates[sym]
        e20_1h = loader.ema20_1h[sym]
        e50_1h = loader.ema50_1h[sym]
        h1_times = loader.h1_close_times[sym]
        atr_vals = loader.atr_15m[sym]

        equity = BASE_CONFIG["initial_capital_per_pair"]
        open_pos: Optional[Position] = None
        pending_order: Optional[PendingOrder] = None
        trade_history: List[TradeResult] = []

        h1_ptr = 0
        fund_ptr = 0
        order_counter = 0
        trade_counter = 0

        for idx, c15 in enumerate(c15_list):
            c15_close_ms = c15.timestamp_ms + 900000 - 1

            while h1_ptr + 1 < len(c1_list) and h1_times[h1_ptr + 1] <= c15_close_ms:
                h1_ptr += 1

            open_pos, pending_order, trade_res, fund_ptr, order_counter, trade_counter = simulate_bar(
                c15=c15,
                open_pos=open_pos,
                pending_order=pending_order,
                funding_list=fund_list,
                fund_ptr=fund_ptr,
                order_counter=order_counter,
                trade_counter=trade_counter,
                symbol=sym,
                maker_fee=BASE_CONFIG["maker_fee_rate"],
                taker_fee=BASE_CONFIG["taker_fee_rate"],
                slippage=BASE_CONFIG["adverse_slippage_rate"],
                full_exit_at_tp1=full_exit,
            )

            if trade_res:
                trade_history.append(trade_res)
                equity += trade_res.net_pnl

            # Signal generation
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
                    pending_order = evaluate_signal_variant(
                        version=version,
                        c15=c15,
                        recent_15m=c15_list[idx - 60 : idx],
                        atr_val=atr_vals[idx],
                        h1_bias=h1_bias,
                        symbol=sym,
                        order_counter=order_counter,
                        cfg=BASE_CONFIG,
                    )

        metrics = calculate_performance_metrics(trade_history, initial_cap=100.0)
        results[sym] = metrics
        trades_by_sym[sym] = trade_history

    return results, trades_by_sym


def run_portfolio_backtest(
    loader: DataLoader,
    version: str,
    pair_subset: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], List[TradeResult]]:
    """Runs combined portfolio simulation under unified $100 equity & risk limits."""
    active_pairs = pair_subset if pair_subset is not None else TOP_10_PAIRS
    full_exit = (version == "V3")

    start_eval_ts = BASE_CONFIG["backtest_start_ts"]
    end_eval_ts = BASE_CONFIG["backtest_end_ts"]

    # Build synchronous timeline across active pairs
    all_ts = sorted(list(set(
        c.timestamp_ms
        for sym in active_pairs
        for c in loader.candles_15m[sym]
    )))

    c15_map = {sym: {c.timestamp_ms: c for c in loader.candles_15m[sym]} for sym in active_pairs}
    c15_idx_map = {sym: {c.timestamp_ms: i for i, c in enumerate(loader.candles_15m[sym])} for sym in active_pairs}

    open_positions: Dict[str, Position] = {}
    pending_orders: Dict[str, PendingOrder] = {}
    trade_history: List[TradeResult] = []

    h1_ptrs = {sym: 0 for sym in active_pairs}
    fund_ptrs = {sym: 0 for sym in active_pairs}
    order_counter = 0
    trade_counter = 0
    equity = BASE_CONFIG["portfolio_capital"]

    for ts in all_ts:
        c15_close_ms = ts + 900000 - 1

        # 1. Update H1 ptrs and simulate bar for each active symbol
        for sym in active_pairs:
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
                c15=c15,
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

        # 2. Portfolio risk manager checks & signal evaluations
        if c15_close_ms >= start_eval_ts and c15_close_ms <= end_eval_ts:
            for sym in active_pairs:
                if sym in open_positions or sym in pending_orders:
                    continue

                # Check portfolio capacity limits
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
                cand_order = evaluate_signal_variant(
                    version=version,
                    c15=c15,
                    recent_15m=loader.candles_15m[sym][idx - 60 : idx],
                    atr_val=loader.atr_15m[sym][idx],
                    h1_bias=h1_bias,
                    symbol=sym,
                    order_counter=order_counter,
                    cfg=BASE_CONFIG,
                )

                if cand_order:
                    # Directional cap check
                    direction = cand_order.direction
                    same_dir_count = sum(
                        1 for p in open_positions.values() if p.direction == direction
                    ) + sum(
                        1 for o in pending_orders.values() if o.direction == direction
                    )
                    if same_dir_count < BASE_CONFIG["max_open_per_direction"]:
                        pending_orders[sym] = cand_order

    metrics = calculate_performance_metrics(trade_history, initial_cap=BASE_CONFIG["portfolio_capital"])
    return metrics, trade_history


def main():
    t_start = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 80)
    print("STARTING MODEL B V1 vs V2 vs V3 12-MONTH HISTORICAL BACKTEST")
    print(f"Target DB: {TARGET_DB}")
    print(f"Output Dir: {OUTPUT_DIR}")
    print("=" * 80)

    loader = DataLoader(TARGET_DB)
    print(f"Successfully loaded all 10 pairs candle & funding data in {time.time() - t_start:.2f}s\n")

    # 1. RUN PER-PAIR EVALUATIONS FOR V1, V2, V3
    print(">>> Running Independent Per-Pair Annual Backtests ($100 Capital Each)...")
    t0 = time.time()
    v1_pair_res, v1_pair_trades = run_per_pair_backtests(loader, "V1")
    print(f"  [+] V1 (Legacy) finished in {time.time() - t0:.2f}s")

    t0 = time.time()
    v2_pair_res, v2_pair_trades = run_per_pair_backtests(loader, "V2")
    print(f"  [+] V2 (Remediated) finished in {time.time() - t0:.2f}s")

    t0 = time.time()
    v3_pair_res, v3_pair_trades = run_per_pair_backtests(loader, "V3")
    print(f"  [+] V3 (Tuned Hybrid) finished in {time.time() - t0:.2f}s")

    # 2. RUN PORTFOLIO EVALUATIONS
    print("\n>>> Running Combined Portfolio Annual Backtests ($100 Multi-Pair Account)...")
    t0 = time.time()
    v1_port_res, v1_port_trades = run_portfolio_backtest(loader, "V1")
    print(f"  [+] Portfolio V1 finished in {time.time() - t0:.2f}s")

    t0 = time.time()
    v2_port_res, v2_port_trades = run_portfolio_backtest(loader, "V2")
    print(f"  [+] Portfolio V2 finished in {time.time() - t0:.2f}s")

    t0 = time.time()
    v3_port_res, v3_port_trades = run_portfolio_backtest(loader, "V3")
    print(f"  [+] Portfolio V3 (All 10 Pairs) finished in {time.time() - t0:.2f}s")

    # Production configured V3 portfolio (excluding chop pairs: BTC, BNB, XRP, AVAX)
    v3_clean_pairs = [p for p in TOP_10_PAIRS if p not in V3_CHOP_BLACKLIST]
    t0 = time.time()
    v3_clean_res, v3_clean_trades = run_portfolio_backtest(loader, "V3", pair_subset=v3_clean_pairs)
    print(f"  [+] Portfolio V3 (Chop Blacklist: {v3_clean_pairs}) finished in {time.time() - t0:.2f}s")

    # 3. PRINT COMPARATIVE SUMMARY TABLES
    print("\n" + "=" * 110)
    print("MASTER PORTFOLIO COMPARISON MATRIX: V1 vs V2 vs V3 (12-MONTH HISTORICAL)")
    print("=" * 110)
    print(f"{'Configuration':<32} | {'Trades':<7} | {'WinRate':<8} | {'Gross PnL':<11} | {'Fees Paid':<11} | {'Net PnL':<11} | {'ROI %':<9} | {'PF':<5} | {'Max DD':<8}")
    print("-" * 110)

    configs = [
        ("V1 Legacy (Unremediated)", v1_port_res),
        ("V2 Remediated (Fast Scalper)", v2_port_res),
        ("V3 Tuned (All 10 Pairs)", v3_port_res),
        ("V3 Tuned (Chop Blacklist)", v3_clean_res),
    ]

    for name, res in configs:
        print(f"{name:<32} | {res['total_trades']:<7} | {res['win_rate']:>6.2f}% | {res['gross_pnl']:>10.2f}$ | {res['fees_paid']:>10.2f}$ | {res['net_pnl']:>10.2f}$ | {res['net_return_pct']:>7.2f}% | {res['profit_factor']:>5.2f} | {res['max_drawdown_pct']:>7.2f}%")

    print("\n" + "=" * 125)
    print("TOP 10 PAIRS COMPARISON: NET PNL (USDT) & WIN RATE ACROSS V1, V2, AND V3")
    print("=" * 125)
    print(f"{'Pair':<10} | {'V1 Trades':<10} | {'V1 Net PnL':<12} | {'V2 Trades':<10} | {'V2 Net PnL':<12} | {'V3 Trades':<10} | {'V3 Net PnL':<12} | {'V3 WinRate':<10} | {'V3 PF':<6} | {'V3 Status':<12}")
    print("-" * 125)

    for sym in TOP_10_PAIRS:
        m1 = v1_pair_res[sym]
        m2 = v2_pair_res[sym]
        m3 = v3_pair_res[sym]
        status = "Blacklisted" if sym in V3_CHOP_BLACKLIST else ("PROFITABLE" if m3["net_pnl"] > 0 else "Reduced Loss")
        print(f"{sym:<10} | {m1['total_trades']:<10} | {m1['net_pnl']:>11.2f}$ | {m2['total_trades']:<10} | {m2['net_pnl']:>11.2f}$ | {m3['total_trades']:<10} | {m3['net_pnl']:>11.2f}$ | {m3['win_rate']:>9.2f}% | {m3['profit_factor']:>6.2f} | {status:<12}")

    # 4. SAVE EXPORT CSVs
    # Save Master Portfolio Comparison CSV
    with open(f"{OUTPUT_DIR}/master_portfolio_comparison.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Configuration", "Total Trades", "Win Rate %", "Gross PnL", "Fees Paid", "Funding Paid", "Net PnL", "ROI % ($100)", "Profit Factor", "Max Drawdown %", "Avg Trade PnL", "Avg Duration Hrs", "Long Trades", "Short Trades"])
        for name, res in configs:
            w.writerow([
                name,
                res["total_trades"],
                res["win_rate"],
                res["gross_pnl"],
                res["fees_paid"],
                res["funding_paid"],
                res["net_pnl"],
                res["net_return_pct"],
                res["profit_factor"],
                res["max_drawdown_pct"],
                res["avg_trade_pnl"],
                res["avg_duration_hrs"],
                res["long_trades"],
                res["short_trades"],
            ])

    # Save Per-Pair Comparison CSV
    with open(f"{OUTPUT_DIR}/top10_pairs_v1_v2_v3_comparison.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Pair",
            "V1 Trades", "V1 Gross PnL", "V1 Fees", "V1 Net PnL", "V1 Win Rate %", "V1 PF",
            "V2 Trades", "V2 Gross PnL", "V2 Fees", "V2 Net PnL", "V2 Win Rate %", "V2 PF",
            "V3 Trades", "V3 Gross PnL", "V3 Fees", "V3 Net PnL", "V3 Win Rate %", "V3 PF",
            "V3 Status"
        ])
        for sym in TOP_10_PAIRS:
            m1 = v1_pair_res[sym]
            m2 = v2_pair_res[sym]
            m3 = v3_pair_res[sym]
            status = "Blacklisted Chop" if sym in V3_CHOP_BLACKLIST else ("PROFITABLE" if m3["net_pnl"] > 0 else "Loss")
            w.writerow([
                sym,
                m1["total_trades"], m1["gross_pnl"], m1["fees_paid"], m1["net_pnl"], m1["win_rate"], m1["profit_factor"],
                m2["total_trades"], m2["gross_pnl"], m2["fees_paid"], m2["net_pnl"], m2["win_rate"], m2["profit_factor"],
                m3["total_trades"], m3["gross_pnl"], m3["fees_paid"], m3["net_pnl"], m3["win_rate"], m3["profit_factor"],
                status
            ])

    # Save V3 Trades
    with open(f"{OUTPUT_DIR}/v3_portfolio_trades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Trade ID", "Symbol", "Direction", "Entry UTC", "Exit UTC", "Entry Px", "Exit Px", "Quantity", "Notional", "Gross PnL", "Fees Paid", "Funding Paid", "Net PnL", "Exit Reason", "Duration Hrs", "Return %", "Realized RR"])
        for t in v3_clean_trades:
            w.writerow([
                t.trade_id, t.symbol, t.direction, t.entry_time_utc, t.exit_time_utc,
                t.entry_price, t.exit_price, t.quantity, t.notional, t.gross_pnl,
                t.fees_paid, t.funding_paid, t.net_pnl, t.exit_reason, t.duration_hours,
                t.return_pct, t.realized_rr
            ])

    print(f"\n[OK] All results, comparisons, and trades exported to {OUTPUT_DIR}/")
    print(f"Total Execution Time: {time.time() - t_start:.2f} seconds.")


if __name__ == "__main__":
    main()
