"""Coin Inspector & AI Context Exporter Service.

Generates comprehensive, deterministic, token-efficient JSON snapshots for any
perpetual futures coin on Aster DEX for external AI quantitative reasoning.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import math
import time
from typing import Any, Dict, List, Optional, Tuple, Set
from concurrent.futures import ThreadPoolExecutor
import numpy as np

from src.core.types import Candle, Direction
from src.exchange.aster_adapter import AsterAdapter, NormalizedOrderBook
from src.exchange.metadata import SymbolSpecs
from src.execution.types import PaperOrder, PaperOrderStatus
from src.smc.fvg import FVGDetector
from src.strategy.bias import fast_ema
from src.strategy.model_b.evaluator import find_recent_swings


POPULAR_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "ZECUSDT",
    "XMRUSDT",
    "DOGEUSDT",
    "ASTERUSDT",
    "LITUSDT",
]

MODEL_A_WHITELIST: Set[str] = {
    "ADAUSDT",
    "XRPUSDT",
    "BTCUSDT",
    "BNBUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "XMRUSDT",
    "DOGEUSDT",
    "LTCUSDT",
}


def get_dynamic_precision(price: float, tick_size: float = 0.0) -> int:
    """Determine decimal precision ensuring at least 4-6 significant digits for sub-$1.00 coins."""
    if tick_size > 0:
        try:
            p = max(0, -int(math.floor(math.log10(tick_size) + 1e-9)))
            return min(max(p, 2), 8)
        except (ValueError, OverflowError):
            pass
    if price <= 0:
        return 4
    if price < 0.0001:
        return 8
    elif price < 0.001:
        return 7
    elif price < 0.01:
        return 6
    elif price < 0.1:
        return 5
    elif price < 1.0:
        return 4
    elif price < 10.0:
        return 3
    else:
        return 2


def round_price_dyn(price: float, precision: int, tick_size: float = 0.0) -> float:
    """Round price honoring tick_size and ensuring small coins preserve significant digits."""
    if tick_size > 0:
        try:
            d_price = Decimal(str(price))
            d_tick = Decimal(str(tick_size))
            rounded = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * d_tick
            return float(rounded)
        except Exception:
            pass
    return round(float(price), precision)


class CoinInspectorService:
    """Provides high-density market, structure, order-book, and sizing intelligence."""

    def __init__(self, adapter: Optional[AsterAdapter] = None):
        self.adapter = adapter or AsterAdapter()
        self._compass_cache: Optional[Dict[str, Any]] = None
        self._compass_cache_ts: float = 0.0
        self._compass_cache_ttl: float = 45.0  # 45 seconds cache for BTC/ETH reference
        self._specs_cache: Dict[str, SymbolSpecs] = {}

    def get_symbol_specs(self, symbol: str, mark_price: float = 0.0) -> SymbolSpecs:
        """Resolve contract specifications with true Aster DEX filters and dynamic sub-$1.00 precision."""
        clean = symbol.upper().replace(".P", "")
        if clean in self._specs_cache:
            specs = self._specs_cache[clean]
            # If specs had a coarse tick_size (e.g. 0.01) but price is < 1.0, refine precision
            if mark_price > 0 and mark_price < 1.0 and specs.tick_size >= 0.01:
                p_prec = get_dynamic_precision(mark_price, 0.0)
                specs.price_precision = p_prec
                specs.tick_size = 10 ** (-p_prec)
            return specs

        # 1. Check AsterAdapter's catalog cache
        if not getattr(self.adapter, "_catalog_cache", None):
            try:
                self.adapter.discover_instruments()
            except Exception:
                pass

        inst = getattr(self.adapter, "_catalog_cache", {}).get(clean)
        if inst:
            ref_px = float(inst.last_price or mark_price or 1.0)
            p_prec = get_dynamic_precision(ref_px, inst.tick_size)
            q_prec = max(0, -int(math.floor(math.log10(inst.step_size) + 1e-9))) if inst.step_size > 0 else 3
            specs = SymbolSpecs(
                symbol=inst.symbol,
                base_asset=inst.base_asset,
                quote_asset=inst.quote_asset,
                contract_type=inst.contract_type,
                status="TRADING",
                tick_size=inst.tick_size,
                price_precision=p_prec,
                step_size=inst.step_size,
                quantity_precision=q_prec,
                min_qty=inst.min_qty,
                max_qty=1000000.0,
                min_notional=inst.min_notional,
                maint_margin_percent=0.01,
            )
            self._specs_cache[clean] = specs
            return specs

        # 2. Query direct exchangeInfo if not in catalog cache
        try:
            url = f"{self.adapter.base_url}/fapi/v1/exchangeInfo"
            resp = self.adapter.session.get(url, params={"symbol": clean}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                symbols_data = data.get("symbols", [])
                if symbols_data:
                    s_info = symbols_data[0]
                    t_size = 0.01
                    s_size = 0.001
                    m_qty = 0.001
                    m_notional = 5.0
                    for f in s_info.get("filters", []):
                        ft = f.get("filterType")
                        if ft == "PRICE_FILTER":
                            t_size = float(f.get("tickSize", t_size))
                        elif ft == "LOT_SIZE":
                            s_size = float(f.get("stepSize", s_size))
                            m_qty = float(f.get("minQty", m_qty))
                        elif ft == "MIN_NOTIONAL":
                            m_notional = float(f.get("notional", m_notional))

                    p_prec = get_dynamic_precision(mark_price or 1.0, t_size)
                    q_prec = max(0, -int(math.floor(math.log10(s_size) + 1e-9))) if s_size > 0 else 3
                    specs = SymbolSpecs(
                        symbol=clean,
                        base_asset=s_info.get("baseAsset", clean.replace("USDT", "")),
                        quote_asset=s_info.get("quoteAsset", "USDT"),
                        contract_type="PERPETUAL",
                        status="TRADING",
                        tick_size=t_size,
                        price_precision=p_prec,
                        step_size=s_size,
                        quantity_precision=q_prec,
                        min_qty=m_qty,
                        max_qty=1000000.0,
                        min_notional=m_notional,
                        maint_margin_percent=0.01,
                    )
                    self._specs_cache[clean] = specs
                    return specs
        except Exception:
            pass

        # 3. Dynamic sub-$1.00 fallback preserving 4-8 significant digits
        p_prec = get_dynamic_precision(mark_price or 1.0, 0.0)
        t_size = 10 ** (-p_prec)
        specs = SymbolSpecs(
            symbol=clean,
            base_asset=clean.replace("USDT", ""),
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=t_size,
            price_precision=p_prec,
            step_size=1.0 if mark_price < 0.1 else 0.01,
            quantity_precision=0 if mark_price < 0.1 else 2,
            min_qty=1.0 if mark_price < 0.1 else 0.01,
            max_qty=1000000.0,
            min_notional=5.0,
            maint_margin_percent=0.01,
        )
        self._specs_cache[clean] = specs
        return specs

    def get_symbols_catalog(
        self,
        whitelisted_symbols: Optional[List[str]] = None,
        active_symbols: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return full list of eligible active perpetual symbols sorted by priority."""
        try:
            instruments = self.adapter.discover_instruments()
        except Exception:
            instruments = []

        active_set = set(active_symbols or [])
        whitelist_set = set(whitelisted_symbols or [])
        popular_set = set(POPULAR_SYMBOLS)

        # Get 24hr bulk snapshots for volume & price changes
        try:
            bulk_snaps = self.adapter.get_bulk_market_snapshot()
        except Exception:
            bulk_snaps = {}

        symbols_meta = {}
        for inst in instruments:
            snap = bulk_snaps.get(inst.symbol, {})
            vol_24h = snap.get("volume_24h_usdt", 0.0) or 0.0
            chg_pct = snap.get("price_change_pct", 0.0) or 0.0
            symbols_meta[inst.symbol] = {
                "symbol": inst.symbol,
                "volume_24h_usdt": vol_24h,
                "price_change_pct": chg_pct,
            }

        # Add popular symbols if missing
        for sym in POPULAR_SYMBOLS:
            if sym not in symbols_meta:
                symbols_meta[sym] = {
                    "symbol": sym,
                    "volume_24h_usdt": 1000000.0,
                    "price_change_pct": 0.0,
                }

        # Partition into priority groups
        priority_group = []
        normal_group = []

        for sym, data in symbols_meta.items():
            is_priority = (
                sym in popular_set
                or sym in active_set
                or sym in whitelist_set
                or data["volume_24h_usdt"] >= 10_000_000.0
            )
            item = {
                "symbol": sym,
                "volume_24h_usdt": round(data["volume_24h_usdt"], 2),
                "price_change_pct": round(data["price_change_pct"], 2),
                "is_popular": sym in popular_set,
                "is_active": sym in active_set,
            }
            if is_priority:
                priority_group.append(item)
            else:
                normal_group.append(item)

        # Sort priority group by volume descending, popular first
        priority_group.sort(
            key=lambda x: (
                1 if x["symbol"] in popular_set else 0,
                x["volume_24h_usdt"],
            ),
            reverse=True,
        )

        # Sort normal group alphabetically
        normal_group.sort(key=lambda x: x["symbol"])

        ordered_items = priority_group + normal_group
        ordered_symbols = [x["symbol"] for x in ordered_items]

        return {
            "symbols": ordered_symbols,
            "items": ordered_items,
            "popular_chips": [s for s in POPULAR_SYMBOLS if s in symbols_meta],
            "count": len(ordered_symbols),
        }

    def get_market_compass(self) -> Dict[str, Any]:
        """Fetch and analyze BTC and ETH market trend context with caching."""
        now = time.time()
        if (
            self._compass_cache is not None
            and (now - self._compass_cache_ts) < self._compass_cache_ttl
        ):
            return self._compass_cache

        def analyze_ref_coin(sym: str) -> Dict[str, Any]:
            try:
                c_1h = self.adapter.get_klines(sym, "1h", limit=50)
                c_4h = self.adapter.get_klines(sym, "4h", limit=50)
            except Exception:
                c_1h, c_4h = [], []

            if not c_1h:
                return {
                    "symbol": sym,
                    "price": 0.0,
                    "change_1h_pct": 0.0,
                    "change_24h_pct": 0.0,
                    "trend_1h": "CHOP",
                    "trend_4h": "CHOP",
                    "alignment": "CHOP",
                }

            last_px = c_1h[-1].close
            c_1h_ago = c_1h[-2].close if len(c_1h) >= 2 else last_px
            c_24h_ago = c_1h[-25].close if len(c_1h) >= 25 else c_1h[0].close

            chg_1h = ((last_px - c_1h_ago) / c_1h_ago * 100.0) if c_1h_ago > 0 else 0.0
            chg_24h = ((last_px - c_24h_ago) / c_24h_ago * 100.0) if c_24h_ago > 0 else 0.0

            # 1H trend
            closes_1h = np.array([c.close for c in c_1h], dtype=float)
            ema20_1h = fast_ema(closes_1h, 20)
            ema50_1h = fast_ema(closes_1h, 50)
            trend_1h = (
                "BULLISH"
                if (ema20_1h > ema50_1h and last_px >= ema20_1h)
                else ("BEARISH" if (ema20_1h < ema50_1h and last_px <= ema20_1h) else "CHOP")
            )

            # 4H trend
            if c_4h:
                closes_4h = np.array([c.close for c in c_4h], dtype=float)
                ema20_4h = fast_ema(closes_4h, 20)
                ema50_4h = fast_ema(closes_4h, 50)
                trend_4h = (
                    "BULLISH"
                    if (ema20_4h > ema50_4h and last_px >= ema20_4h)
                    else ("BEARISH" if (ema20_4h < ema50_4h and last_px <= ema20_4h) else "CHOP")
                )
            else:
                trend_4h = trend_1h

            alignment = (
                "BULLISH"
                if (trend_1h == "BULLISH" and trend_4h == "BULLISH")
                else ("BEARISH" if (trend_1h == "BEARISH" and trend_4h == "BEARISH") else "CHOP")
            )

            return {
                "symbol": sym,
                "price": round(last_px, 2),
                "change_1h_pct": round(chg_1h, 2),
                "change_24h_pct": round(chg_24h, 2),
                "trend_1h": trend_1h,
                "trend_4h": trend_4h,
                "alignment": alignment,
            }

        btc_data = analyze_ref_coin("BTCUSDT")
        eth_data = analyze_ref_coin("ETHUSDT")

        # Global market regime determination
        if btc_data["alignment"] == "BULLISH" and eth_data["alignment"] in ("BULLISH", "CHOP"):
            regime = "RISK_ON_BULLISH"
        elif btc_data["alignment"] == "BEARISH" or eth_data["alignment"] == "BEARISH":
            regime = "RISK_OFF_BEARISH"
        else:
            regime = "CHOP_RANGE"

        compass = {
            "btc": btc_data,
            "eth": eth_data,
            "market_regime": regime,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        self._compass_cache = compass
        self._compass_cache_ts = now
        return compass

    def compute_indicators(
        self,
        candles: List[Candle],
        price_prec: int = 4,
        tick_size: float = 0.0,
    ) -> Dict[str, Any]:
        """Compute standard technical indicators with dynamic price precision."""
        if not candles:
            return {
                "ema_20": None,
                "ema_50": None,
                "ema_200": None,
                "atr_14": None,
                "atr_pct": None,
                "rsi_14": None,
                "volume_sma_20": None,
                "current_vol_vs_sma_ratio": None,
                "trend_state": "NEUTRAL",
            }

        closes = np.array([c.close for c in candles], dtype=float)
        last_close = float(closes[-1])

        # EMAs
        raw_ema_20 = fast_ema(closes, 20) if len(closes) >= 5 else last_close
        raw_ema_50 = fast_ema(closes, 50) if len(closes) >= 10 else raw_ema_20
        raw_ema_200 = fast_ema(closes, 200) if len(closes) >= 20 else raw_ema_50

        ema_20 = round_price_dyn(raw_ema_20, price_prec, tick_size)
        ema_50 = round_price_dyn(raw_ema_50, price_prec, tick_size)
        ema_200 = round_price_dyn(raw_ema_200, price_prec, tick_size)

        # ATR 14
        tr_list = []
        for i in range(len(candles)):
            if i == 0:
                tr_list.append(candles[i].high - candles[i].low)
            else:
                prev_c = candles[i - 1]
                tr = max(
                    candles[i].high - candles[i].low,
                    abs(candles[i].high - prev_c.close),
                    abs(candles[i].low - prev_c.close),
                )
                tr_list.append(tr)

        atr_window = tr_list[-14:] if len(tr_list) >= 14 else tr_list
        raw_atr_14 = float(np.mean(atr_window)) if atr_window else 0.0
        atr_14 = round_price_dyn(raw_atr_14, price_prec, tick_size)
        atr_pct = round((raw_atr_14 / last_close * 100.0), 2) if last_close > 0 else 0.0

        # RSI 14
        if len(closes) >= 15:
            deltas = np.diff(closes[-15:])
            gains = np.where(deltas > 0, deltas, 0.0)
            losses = np.where(deltas < 0, -deltas, 0.0)
            avg_gain = float(np.mean(gains))
            avg_loss = float(np.mean(losses))
            if avg_loss == 0.0:
                rsi_14 = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_14 = round(100.0 - (100.0 / (1.0 + rs)), 1)
        else:
            rsi_14 = 50.0

        # Volume SMA 20
        vols = [float(c.volume or 0.0) for c in candles[-20:]]
        vol_sma = float(np.mean(vols)) if vols else 0.0
        cur_vol = float(candles[-1].volume or 0.0)
        vol_ratio = round(cur_vol / vol_sma, 2) if vol_sma > 0 else 1.0

        # Trend State
        if raw_ema_20 > raw_ema_50 and last_close >= raw_ema_20:
            trend_state = "BULL"
        elif raw_ema_20 < raw_ema_50 and last_close <= raw_ema_20:
            trend_state = "BEAR"
        else:
            trend_state = "NEUTRAL"

        return {
            "ema_20": ema_20,
            "ema_50": ema_50,
            "ema_200": ema_200,
            "atr_14": atr_14,
            "atr_pct": atr_pct,
            "rsi_14": rsi_14,
            "volume_sma_20": round(vol_sma, 2),
            "current_vol_vs_sma_ratio": vol_ratio,
            "trend_state": trend_state,
        }

    def format_recent_candles(
        self,
        candles: List[Candle],
        count: int = 13,
        price_prec: int = 4,
    ) -> List[str]:
        """Produce compact, token-efficient string representation of last candles."""
        slice_candles = candles[-count:] if len(candles) >= count else candles
        formatted = []

        for c in slice_candles:
            dt = datetime.fromtimestamp(c.timestamp_ms / 1000.0, tz=timezone.utc)
            t_str = dt.strftime("%Y-%m-%d %H:%M")
            c_range = c.high - c.low
            lower_wick = min(c.open, c.close) - c.low
            upper_wick = c.high - max(c.open, c.close)
            wick_ratio = (max(lower_wick, upper_wick) / c_range) if c_range > 0 else 0.0

            # Compact volume notation
            v = float(c.volume or 0.0)
            if v >= 1_000_000:
                v_str = f"{v/1_000_000:.1f}M"
            elif v >= 1_000:
                v_str = f"{v/1_000:.1f}K"
            else:
                v_str = f"{v:.0f}"

            o_str = f"{c.open:.{price_prec}f}"
            h_str = f"{c.high:.{price_prec}f}"
            l_str = f"{c.low:.{price_prec}f}"
            c_str = f"{c.close:.{price_prec}f}"

            formatted.append(
                f"{t_str} | O:{o_str} H:{h_str} L:{l_str} C:{c_str} | V:{v_str} | WickR:{wick_ratio:.2f}"
            )
        return formatted

    def compute_smc_structure(
        self,
        candles_1h: List[Candle],
        candles_15m: List[Candle],
        current_price: float,
        price_prec: int = 4,
        tick_size: float = 0.0,
    ) -> Dict[str, Any]:
        """Compute SMC Dealing Range (Model A) and Sweep-Reclaim Swings (Model B)."""
        # --- Model A: 72H Dealing Range ---
        if len(candles_1h) >= 5:
            window_72 = candles_1h[-72:] if len(candles_1h) >= 72 else candles_1h
            hi_72 = max(c.high for c in window_72)
            lo_72 = min(c.low for c in window_72)
            span_72 = hi_72 - lo_72
            pos_pct = round(((current_price - lo_72) / span_72 * 100.0), 1) if span_72 > 0 else 50.0

            if pos_pct <= 30.0:
                zone_status = "DISCOUNT"
            elif pos_pct >= 70.0:
                zone_status = "PREMIUM"
            else:
                zone_status = "EQUILIBRIUM"

            c_48_ago = candles_1h[-49].close if len(candles_1h) >= 49 else candles_1h[0].close
            c_12_ago = candles_1h[-13].close if len(candles_1h) >= 13 else candles_1h[0].close
            impulse_48h = round(((current_price - c_48_ago) / c_48_ago * 100.0), 2) if c_48_ago > 0 else 0.0
            impulse_12h = round(((current_price - c_12_ago) / c_12_ago * 100.0), 2) if c_12_ago > 0 else 0.0

            # FVGs on 1H
            fvgs = FVGDetector.detect_fvgs(candles_1h)
            bull_fvgs = [f for f in fvgs if f.direction == Direction.LONG and not f.mitigated and f.top <= current_price]
            bull_fvgs.sort(key=lambda x: x.top, reverse=True)
            nearest_bull_fvg = [
                round_price_dyn(bull_fvgs[0].bottom, price_prec, tick_size),
                round_price_dyn(bull_fvgs[0].top, price_prec, tick_size),
            ] if bull_fvgs else None

            bear_fvgs = [f for f in fvgs if f.direction == Direction.SHORT and not f.mitigated and f.bottom >= current_price]
            bear_fvgs.sort(key=lambda x: x.bottom, reverse=False)
            nearest_bear_fvg = [
                round_price_dyn(bear_fvgs[0].bottom, price_prec, tick_size),
                round_price_dyn(bear_fvgs[0].top, price_prec, tick_size),
            ] if bear_fvgs else None

            model_a_ctx = {
                "dealing_range_72h": {
                    "high": round_price_dyn(hi_72, price_prec, tick_size),
                    "low": round_price_dyn(lo_72, price_prec, tick_size),
                    "current_position_pct": pos_pct,
                    "zone_status": zone_status,
                },
                "impulse_48h_pct": impulse_48h,
                "impulse_12h_pct": impulse_12h,
                "nearest_unmitigated_bull_fvg": nearest_bull_fvg,
                "nearest_unmitigated_bear_fvg": nearest_bear_fvg,
            }
        else:
            model_a_ctx = {
                "dealing_range_72h": {
                    "high": round_price_dyn(current_price, price_prec, tick_size),
                    "low": round_price_dyn(current_price, price_prec, tick_size),
                    "current_position_pct": 50.0,
                    "zone_status": "EQUILIBRIUM",
                },
                "impulse_48h_pct": 0.0,
                "impulse_12h_pct": 0.0,
                "nearest_unmitigated_bull_fvg": None,
                "nearest_unmitigated_bear_fvg": None,
            }

        # --- Model B: Swings & Sweep Detection ---
        raw_sw_high_15m = max(c.high for c in candles_15m[-20:]) if len(candles_15m) >= 20 else current_price
        raw_sw_low_15m = min(c.low for c in candles_15m[-20:]) if len(candles_15m) >= 20 else current_price
        raw_sw_high_1h = max(c.high for c in candles_1h[-20:]) if len(candles_1h) >= 20 else current_price
        raw_sw_low_1h = min(c.low for c in candles_1h[-20:]) if len(candles_1h) >= 20 else current_price

        sw_high_15m = round_price_dyn(raw_sw_high_15m, price_prec, tick_size)
        sw_low_15m = round_price_dyn(raw_sw_low_15m, price_prec, tick_size)
        sw_high_1h = round_price_dyn(raw_sw_high_1h, price_prec, tick_size)
        sw_low_1h = round_price_dyn(raw_sw_low_1h, price_prec, tick_size)

        recent_sweep = False
        sweep_type = "NONE"
        swept_level = None
        reclaim_confirmed = False
        wick_ratio = 0.0

        if len(candles_15m) >= 15:
            last_closed = candles_15m[-1]
            prior_candles = candles_15m[:-1]
            swing_highs, swing_lows = find_recent_swings(prior_candles, left_bars=3, right_bars=2)

            c_range = last_closed.high - last_closed.low
            if swing_lows:
                _, key_low = swing_lows[-1]
                pierced_low = (last_closed.low < key_low) or (len(candles_15m) >= 2 and candles_15m[-2].low < key_low)
                if pierced_low and last_closed.close > key_low:
                    recent_sweep = True
                    sweep_type = "BULL_SWEEP_LOW"
                    swept_level = round_price_dyn(key_low, price_prec, tick_size)
                    reclaim_confirmed = True
                    lower_w = min(last_closed.open, last_closed.close) - last_closed.low
                    wick_ratio = round((lower_w / c_range), 2) if c_range > 0 else 0.0

            if not recent_sweep and swing_highs:
                _, key_high = swing_highs[-1]
                pierced_high = (last_closed.high > key_high) or (len(candles_15m) >= 2 and candles_15m[-2].high > key_high)
                if pierced_high and last_closed.close < key_high:
                    recent_sweep = True
                    sweep_type = "BEAR_SWEEP_HIGH"
                    swept_level = round_price_dyn(key_high, price_prec, tick_size)
                    reclaim_confirmed = True
                    upper_w = last_closed.high - max(last_closed.open, last_closed.close)
                    wick_ratio = round((upper_w / c_range), 2) if c_range > 0 else 0.0

        model_b_ctx = {
            "15m_swing_high_20b": sw_high_15m,
            "15m_swing_low_20b": sw_low_15m,
            "1h_swing_high_20b": sw_high_1h,
            "1h_swing_low_20b": sw_low_1h,
            "recent_sweep_detected": recent_sweep,
            "sweep_type": sweep_type,
            "swept_level": swept_level,
            "reclaim_confirmed": reclaim_confirmed,
            "rejection_wick_ratio": wick_ratio,
        }

        return {
            "model_a_smc_context": model_a_ctx,
            "model_b_liquidity_context": model_b_ctx,
        }

    def compute_orderbook_microstructure(
        self,
        book: Optional[NormalizedOrderBook],
        mid_price: float,
        price_prec: int = 4,
        tick_size: float = 0.0,
    ) -> Dict[str, Any]:
        """Compute L2 depth, walls, and opposing imbalance ratio."""
        if not book or not book.bids or not book.asks:
            return {
                "spread_bps": 0.0,
                "bid_depth_top20_usdt": 0.0,
                "ask_depth_top20_usdt": 0.0,
                "orderbook_imbalance_ratio": 0.5,
                "nearest_bid_wall": None,
                "nearest_ask_wall": None,
                "l2_gate_passed": False,
            }

        best_bid = book.bids[0].price
        best_ask = book.asks[0].price
        spread_bps = round(((best_ask - best_bid) / mid_price * 10000.0), 2) if mid_price > 0 else 0.0

        top20_bids = book.bids[:20]
        top20_asks = book.asks[:20]

        bid_depth = sum(b.price * b.size for b in top20_bids)
        ask_depth = sum(a.price * a.size for a in top20_asks)
        total_depth = bid_depth + ask_depth
        imbalance = round(bid_depth / total_depth, 3) if total_depth > 0 else 0.50

        # Nearest Walls (levels with size >= 2x average)
        avg_bid_val = (bid_depth / len(top20_bids)) if top20_bids else 1.0
        bid_wall = None
        for b in top20_bids:
            val = b.price * b.size
            if val >= avg_bid_val * 2.0 and val >= 1000.0:
                dist_pct = round(abs(mid_price - b.price) / mid_price * 100.0, 2)
                bid_wall = {
                    "price": round_price_dyn(b.price, price_prec, tick_size),
                    "size_usdt": round(val, 2),
                    "distance_pct": dist_pct,
                }
                break

        avg_ask_val = (ask_depth / len(top20_asks)) if top20_asks else 1.0
        ask_wall = None
        for a in top20_asks:
            val = a.price * a.size
            if val >= avg_ask_val * 2.0 and val >= 1000.0:
                dist_pct = round(abs(a.price - mid_price) / mid_price * 100.0, 2)
                ask_wall = {
                    "price": round_price_dyn(a.price, price_prec, tick_size),
                    "size_usdt": round(val, 2),
                    "distance_pct": dist_pct,
                }
                break

        l2_passed = (bid_depth >= 5000.0) and (ask_depth >= 5000.0)

        return {
            "spread_bps": spread_bps,
            "bid_depth_top20_usdt": round(bid_depth, 2),
            "ask_depth_top20_usdt": round(ask_depth, 2),
            "orderbook_imbalance_ratio": imbalance,
            "nearest_bid_wall": bid_wall,
            "nearest_ask_wall": ask_wall,
            "l2_gate_passed": l2_passed,
        }

    def compute_micro_sizing(
        self,
        entry_price: float,
        specs: SymbolSpecs,
        account_balance_usdt: float = 15.72,
        price_prec: int = 4,
    ) -> Dict[str, Any]:
        """Compute exact micro-live position sizing presets with dynamic sub-$1.00 tick precision."""
        presets = [
            ("fast_15m_standard_risk", 0.80, 0.012, 1.65, "15m Fast (1.20% Stop Floor, $0.80 Risk, 1.65R)"),
            ("fast_15m_conservative_risk", 0.35, 0.012, 1.65, "15m Fast (1.20% Stop Floor, $0.35 Risk, 1.65R)"),
            ("normal_1h_standard_risk", 0.80, 0.015, 2.10, "1H Swing (1.50% Stop Floor, $0.80 Risk, 2.10R)"),
            ("normal_1h_conservative_risk", 0.35, 0.015, 2.10, "1H Swing (1.50% Stop Floor, $0.35 Risk, 2.10R)"),
        ]

        sizing_results: Dict[str, Any] = {
            "account_balance_usdt": round(account_balance_usdt, 2),
            "leverage": 10,
            "margin_mode": "ISOLATED",
            "taker_fee_rate": 0.0004,
            "presets": {},
        }

        for key, risk_usdt, stop_floor_pct, tp1_rr, desc in presets:
            sl_dist = entry_price * stop_floor_pct
            raw_qty = (risk_usdt / sl_dist) if sl_dist > 0 else 0.0
            rounded_qty = specs.round_quantity(raw_qty)
            if rounded_qty < specs.min_qty:
                rounded_qty = specs.min_qty

            notional = round(rounded_qty * entry_price, 2)
            margin = round(notional / 10.0, 2)
            roundtrip_fee = round(notional * 0.0008, 4)
            min_notional_passed = notional >= specs.min_notional

            # LONG scenario: round via SymbolSpecs.round_price (which uses actual Aster tick_size)
            sl_long_raw = entry_price - sl_dist
            tp1_long_raw = entry_price + (sl_dist * tp1_rr)
            sl_long = specs.round_price(sl_long_raw)
            tp1_long = specs.round_price(tp1_long_raw)

            # Fallback guard: ensure sl_long < entry_price
            if sl_long >= entry_price:
                sl_long = round_price_dyn(sl_long_raw, price_prec, specs.tick_size)

            # SHORT scenario
            sl_short_raw = entry_price + sl_dist
            tp1_short_raw = entry_price - (sl_dist * tp1_rr)
            sl_short = specs.round_price(sl_short_raw)
            tp1_short = specs.round_price(tp1_short_raw)
            if sl_short <= entry_price:
                sl_short = round_price_dyn(sl_short_raw, price_prec, specs.tick_size)

            entry_display = round_price_dyn(entry_price, price_prec, specs.tick_size)

            sizing_results["presets"][key] = {
                "description": desc,
                "nominal_risk_usdt": risk_usdt,
                "stop_distance_pct": round(stop_floor_pct * 100.0, 2),
                "tp1_rr_multiple": tp1_rr,
                "rounded_qty": rounded_qty,
                "notional_usdt": notional,
                "isolated_margin_usdt": margin,
                "est_roundtrip_fee_usdt": roundtrip_fee,
                "min_notional_passed": min_notional_passed,
                "long_setup": {
                    "direction": "LONG",
                    "entry_price": entry_display,
                    "stop_loss_price": sl_long,
                    "take_profit_price": tp1_long,
                },
                "short_setup": {
                    "direction": "SHORT",
                    "entry_price": entry_display,
                    "stop_loss_price": sl_short,
                    "take_profit_price": tp1_short,
                },
            }

        return sizing_results

    def evaluate_cohort_statuses(
        self,
        symbol: str,
        runners: Dict[str, Any],
        smc_structure: Optional[Dict[str, Any]] = None,
        l2_microstructure: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Check live cohort status with authoritative SMC gates and liquidity sweep checks."""
        clean_sym = symbol.upper().replace(".P", "")
        smc_ctx = (smc_structure or {}).get("model_a_smc_context", {})
        model_b_ctx = (smc_structure or {}).get("model_b_liquidity_context", {})
        l2_ctx = l2_microstructure or {}

        dr = smc_ctx.get("dealing_range_72h", {})
        pos_pct = dr.get("current_position_pct", 50.0)
        zone_status = dr.get("zone_status", "EQUILIBRIUM")
        bull_fvg = smc_ctx.get("nearest_unmitigated_bull_fvg")
        bear_fvg = smc_ctx.get("nearest_unmitigated_bear_fvg")

        recent_sweep = model_b_ctx.get("recent_sweep_detected", False)
        sweep_type = model_b_ctx.get("sweep_type", "NONE")
        swept_level = model_b_ctx.get("swept_level")
        sw_low_15m = model_b_ctx.get("15m_swing_low_20b")
        sw_high_15m = model_b_ctx.get("15m_swing_high_20b")
        sw_low_1h = model_b_ctx.get("1h_swing_low_20b")
        sw_high_1h = model_b_ctx.get("1h_swing_high_20b")
        l2_gate_passed = l2_ctx.get("l2_gate_passed", False)

        cohorts = {}

        # -------------------------------------------------------------
        # 1. MODEL A: SMC & Dealing Range (4H Bias / 1H Execution)
        # -------------------------------------------------------------
        if clean_sym not in MODEL_A_WHITELIST:
            cohorts["MODEL_A"] = {
                "name": "Model A SMC Dealing Range",
                "status": "NOT_MONITORED",
                "reason": f"Not in 11-symbol core SMC whitelist ({', '.join(sorted(MODEL_A_WHITELIST))})",
            }
        else:
            runner_a = runners.get("MODEL_A")
            is_open = False
            is_pending = False
            if runner_a and hasattr(runner_a, "paper_engine"):
                is_open = clean_sym in runner_a.paper_engine.open_positions
                is_pending = clean_sym in runner_a.paper_engine.pending_orders

            if is_open:
                cohorts["MODEL_A"] = {
                    "name": "Model A SMC Dealing Range",
                    "status": "OPEN_POSITION",
                    "reason": "Actively held paper position in portfolio",
                }
            elif is_pending:
                cohorts["MODEL_A"] = {
                    "name": "Model A SMC Dealing Range",
                    "status": "PENDING_ORDER",
                    "reason": "Limit order pending execution",
                }
            else:
                cands = [c for c in getattr(runner_a, "latest_candidates", []) if getattr(c, "symbol", "") == clean_sym]
                if cands:
                    c = cands[0]
                    st = getattr(c, "status", None)
                    st_val = getattr(st, "value", str(st)) if st else "CANDIDATE"
                    direction = getattr(c, "direction", None)
                    dir_str = getattr(direction, "value", str(direction)) if direction else "LONG"
                    cohorts["MODEL_A"] = {
                        "name": "Model A SMC Dealing Range",
                        "status": st_val,
                        "reason": f"Active setup candidate found ({dir_str})",
                    }
                else:
                    # Authoritative real-time SMC gate status
                    if pos_pct <= 30.0:
                        if bull_fvg:
                            cohorts["MODEL_A"] = {
                                "name": "Model A SMC Dealing Range",
                                "status": "QUALIFIED_DISCOUNT",
                                "reason": f"Deep Discount ({pos_pct}%) with unmitigated Bull FVG [{bull_fvg[0]}, {bull_fvg[1]}], awaiting execution trigger",
                            }
                        else:
                            cohorts["MODEL_A"] = {
                                "name": "Model A SMC Dealing Range",
                                "status": "DISCOUNT_NO_FVG",
                                "reason": f"Deep Discount ({pos_pct}%) but no unmitigated Bull FVG found below current price",
                            }
                    elif pos_pct >= 70.0:
                        if bear_fvg:
                            cohorts["MODEL_A"] = {
                                "name": "Model A SMC Dealing Range",
                                "status": "QUALIFIED_PREMIUM",
                                "reason": f"Premium Exhaustion ({pos_pct}%) with unmitigated Bear FVG [{bear_fvg[0]}, {bear_fvg[1]}], awaiting execution trigger",
                            }
                        else:
                            cohorts["MODEL_A"] = {
                                "name": "Model A SMC Dealing Range",
                                "status": "PREMIUM_NO_FVG",
                                "reason": f"Premium Exhaustion ({pos_pct}%) but no unmitigated Bear FVG found above current price",
                            }
                    else:
                        cohorts["MODEL_A"] = {
                            "name": "Model A SMC Dealing Range",
                            "status": "EQUILIBRIUM_FILTERED",
                            "reason": f"Inside Equilibrium zone ({pos_pct}% of 72H range); requires Discount (<=30%) or Premium (>=70%)",
                        }

        # -------------------------------------------------------------
        # 2. MODEL B: 1H Normal Sweep-Reclaim
        # -------------------------------------------------------------
        runner_b = runners.get("MODEL_B")
        if runner_b and clean_sym in runner_b.paper_engine.open_positions:
            cohorts["MODEL_B"] = {"name": "Model B 1H Normal", "status": "OPEN_POSITION", "reason": "Actively held paper position"}
        elif runner_b and clean_sym in runner_b.paper_engine.pending_orders:
            cohorts["MODEL_B"] = {"name": "Model B 1H Normal", "status": "PENDING_ORDER", "reason": "Limit order pending execution"}
        else:
            cands_b = [c for c in getattr(runner_b, "latest_candidates", []) if getattr(c, "symbol", "") == clean_sym]
            if cands_b:
                c = cands_b[0]
                cohorts["MODEL_B"] = {"name": "Model B 1H Normal", "status": "CANDIDATE", "reason": f"Active setup found ({getattr(c, 'direction', 'UNKNOWN')})"}
            else:
                cohorts["MODEL_B"] = {
                    "name": "Model B 1H Normal",
                    "status": "FILTERED",
                    "reason": f"No 1H sweep-reclaim at swing high (${sw_high_1h}) or swing low (${sw_low_1h})",
                }

        # -------------------------------------------------------------
        # 3. MODEL B FAST: 15m Fast Scalper (V2)
        # -------------------------------------------------------------
        runner_fast = runners.get("MODEL_B_FAST")
        if runner_fast and clean_sym in runner_fast.paper_engine.open_positions:
            cohorts["MODEL_B_FAST"] = {"name": "Model B Fast 15m (V2)", "status": "OPEN_POSITION", "reason": "Actively held paper position"}
        elif runner_fast and clean_sym in runner_fast.paper_engine.pending_orders:
            cohorts["MODEL_B_FAST"] = {"name": "Model B Fast 15m (V2)", "status": "PENDING_ORDER", "reason": "Limit order pending execution"}
        else:
            cands_fast = [c for c in getattr(runner_fast, "latest_candidates", []) if getattr(c, "symbol", "") == clean_sym]
            if cands_fast:
                c = cands_fast[0]
                cohorts["MODEL_B_FAST"] = {"name": "Model B Fast 15m (V2)", "status": "CANDIDATE", "reason": f"Active setup found ({getattr(c, 'direction', 'UNKNOWN')})"}
            elif not recent_sweep:
                cohorts["MODEL_B_FAST"] = {
                    "name": "Model B Fast 15m (V2)",
                    "status": "FILTERED",
                    "reason": f"No 15m sweep-reclaim at swing high (${sw_high_15m}) or swing low (${sw_low_15m})",
                }
            elif not l2_gate_passed:
                cohorts["MODEL_B_FAST"] = {
                    "name": "Model B Fast 15m (V2)",
                    "status": "L2_DEPTH_VETO",
                    "reason": f"15m sweep ({sweep_type}) at ${swept_level} vetoed: L2 depth < $5,000",
                }
            else:
                cohorts["MODEL_B_FAST"] = {
                    "name": "Model B Fast 15m (V2)",
                    "status": "AWAITING_CONFIRMATION",
                    "reason": f"15m sweep ({sweep_type}) at ${swept_level} detected; awaiting green bar and CVD/imbalance confirmation",
                }

        # -------------------------------------------------------------
        # 4. MODEL B V1-PRO: 15m Responsive Long-Biased
        # -------------------------------------------------------------
        runner_v3 = runners.get("MODEL_B_V3")
        if runner_v3 and clean_sym in runner_v3.paper_engine.open_positions:
            cohorts["MODEL_B_V1_PRO"] = {"name": "Model B V1-Pro 15m (Long-Biased)", "status": "OPEN_POSITION", "reason": "Actively held paper position"}
        elif runner_v3 and clean_sym in runner_v3.paper_engine.pending_orders:
            cohorts["MODEL_B_V1_PRO"] = {"name": "Model B V1-Pro 15m (Long-Biased)", "status": "PENDING_ORDER", "reason": "Limit order pending execution"}
        else:
            cands_v3 = [c for c in getattr(runner_v3, "latest_candidates", []) if getattr(c, "symbol", "") == clean_sym]
            if cands_v3:
                c = cands_v3[0]
                cohorts["MODEL_B_V1_PRO"] = {"name": "Model B V1-Pro 15m (Long-Biased)", "status": "CANDIDATE", "reason": f"Active setup found ({getattr(c, 'direction', 'UNKNOWN')})"}
            elif sweep_type == "BEAR_SWEEP_HIGH":
                cohorts["MODEL_B_V1_PRO"] = {
                    "name": "Model B V1-Pro 15m (Long-Biased)",
                    "status": "SHORT_VETOED",
                    "reason": f"Bearish short sweep at ${swept_level} vetoed (V1-Pro is Long-Biased only)",
                }
            elif sweep_type == "BULL_SWEEP_LOW":
                if not l2_gate_passed:
                    cohorts["MODEL_B_V1_PRO"] = {
                        "name": "Model B V1-Pro 15m (Long-Biased)",
                        "status": "L2_DEPTH_VETO",
                        "reason": f"Bull sweep low at ${swept_level} vetoed: L2 order book depth < $5,000",
                    }
                else:
                    cohorts["MODEL_B_V1_PRO"] = {
                        "name": "Model B V1-Pro 15m (Long-Biased)",
                        "status": "CANDIDATE_READY",
                        "reason": f"Responsive bull sweep low reclaimed at ${swept_level} with valid L2 depth",
                    }
            else:
                cohorts["MODEL_B_V1_PRO"] = {
                    "name": "Model B V1-Pro 15m (Long-Biased)",
                    "status": "FILTERED",
                    "reason": f"No bull sweep reclaim at 15m swing low (${sw_low_15m})",
                }

        return cohorts

    def inspect_coin(
        self,
        symbol: str,
        runners: Optional[Dict[str, Any]] = None,
        account_balance_usdt: float = 15.72,
    ) -> Dict[str, Any]:
        """Generate complete, deterministic AI quantitative inspection snapshot."""
        clean_sym = symbol.upper().replace(".P", "")
        runners = runners or {}

        # 1. Concurrently fetch klines (15m, 1h, 4h), order book, and premium index
        with ThreadPoolExecutor(max_workers=5) as ex:
            f_15m = ex.submit(self.adapter.get_klines, clean_sym, "15m", limit=60)
            f_1h = ex.submit(self.adapter.get_klines, clean_sym, "1h", limit=80)
            f_4h = ex.submit(self.adapter.get_klines, clean_sym, "4h", limit=60)
            f_book = ex.submit(self.adapter.get_order_book, clean_sym, limit=50)
            f_pi = ex.submit(self.adapter.get_premium_index, clean_sym)

            candles_15m = f_15m.result()
            candles_1h = f_1h.result()
            candles_4h = f_4h.result()
            order_book = f_book.result()
            premium_index = f_pi.result() or {}

        # 2. Determine mark price and dynamic precision
        mark_price = float(premium_index.get("mark_price", 0.0) or 0.0)
        index_price = float(premium_index.get("index_price", 0.0) or 0.0)
        if mark_price <= 0 and candles_15m:
            mark_price = candles_15m[-1].close
        if index_price <= 0:
            index_price = mark_price

        # 3. Resolve true contract specifications honoring mark_price for sub-$1.00 coins
        specs = self.get_symbol_specs(clean_sym, mark_price=mark_price)
        price_prec = get_dynamic_precision(mark_price, specs.tick_size)

        # 4. 24h ticker info
        t24_map = self.adapter.get_bulk_market_snapshot() if hasattr(self.adapter, "get_bulk_market_snapshot") else {}
        t24_data = t24_map.get(clean_sym, {})
        vol_24h = t24_data.get("volume_24h_usdt", 0.0)
        chg_24h = t24_data.get("price_change_pct", 0.0)

        # If 24h ticker didn't have change, compute from 1h candles
        if chg_24h == 0.0 and len(candles_1h) >= 25:
            c24 = candles_1h[-25].close
            if c24 > 0:
                chg_24h = round((mark_price - c24) / c24 * 100.0, 2)

        funding_rate = float(premium_index.get("last_funding_rate", 0.0) or 0.0)
        next_funding_ms = int(premium_index.get("next_funding_time_ms", 0) or 0)
        next_funding_iso = (
            datetime.fromtimestamp(next_funding_ms / 1000.0, tz=timezone.utc).isoformat()
            if next_funding_ms > 0
            else None
        )

        # 5. Market compass (BTC & ETH reference context)
        compass = self.get_market_compass()

        # 6. Multi-timeframe indicators & recent candles
        summary_15m = {
            "latest_indicators": self.compute_indicators(candles_15m, price_prec=price_prec, tick_size=specs.tick_size),
            "recent_candles": self.format_recent_candles(candles_15m, count=13, price_prec=price_prec),
        }
        summary_1h = {
            "latest_indicators": self.compute_indicators(candles_1h, price_prec=price_prec, tick_size=specs.tick_size),
            "recent_candles": self.format_recent_candles(candles_1h, count=13, price_prec=price_prec),
        }
        summary_4h = {
            "latest_indicators": self.compute_indicators(candles_4h, price_prec=price_prec, tick_size=specs.tick_size),
            "recent_candles": self.format_recent_candles(candles_4h, count=13, price_prec=price_prec),
        }

        # 7. SMC & Liquidity structure
        smc_structure = self.compute_smc_structure(
            candles_1h, candles_15m, mark_price, price_prec=price_prec, tick_size=specs.tick_size
        )

        # 8. Orderbook L2 Microstructure
        l2_microstructure = self.compute_orderbook_microstructure(
            order_book, mark_price, price_prec=price_prec, tick_size=specs.tick_size
        )

        # 9. Bot signals and micro sizing
        cohort_evals = self.evaluate_cohort_statuses(
            clean_sym, runners, smc_structure=smc_structure, l2_microstructure=l2_microstructure
        )
        sizing = self.compute_micro_sizing(
            mark_price, specs, account_balance_usdt=account_balance_usdt, price_prec=price_prec
        )

        # Build full JSON payload
        snapshot = {
            "meta": {
                "symbol": clean_sym,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "mark_price": round_price_dyn(mark_price, price_prec, specs.tick_size),
                "index_price": round_price_dyn(index_price, price_prec, specs.tick_size),
                "change_24h_pct": round(chg_24h, 2),
                "volume_24h_usdt": round(vol_24h, 2),
                "funding_rate_pct": round(funding_rate * 100.0, 5),
                "next_funding_time": next_funding_iso,
                "exchange_filters": {
                    "tick_size": specs.tick_size,
                    "step_size": specs.step_size,
                    "min_qty": specs.min_qty,
                    "min_notional": specs.min_notional,
                },
            },
            "market_compass": compass,
            "multi_timeframe_summary": {
                "15m": summary_15m,
                "1h": summary_1h,
                "4h": summary_4h,
            },
            "smc_and_liquidity_structure": smc_structure,
            "orderbook_l2_microstructure": l2_microstructure,
            "bot_signals_and_micro_sizing": {
                "cohort_evaluations": cohort_evals,
                "sizing": sizing,
            },
        }

        return snapshot

    def execute_ai_trade_setup(
        self,
        payload: Dict[str, Any],
        journal: Any,
        live_trading_locked: bool = True,
        runners: Optional[Dict[str, Any]] = None,
        live_adapter: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Execute or simulate an AI-generated trade setup with ISOLATED 10x configuration and TP/SL bracket."""
        if isinstance(payload, str):
            import json, re
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", payload.strip())
            cleaned = re.sub(r"\n?```$", "", cleaned.strip())
            cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
            payload = json.loads(cleaned)

        raw_symbol = str(payload.get("symbol", "")).strip().upper().replace(".P", "")
        if not raw_symbol:
            raise ValueError("Field 'symbol' is required (e.g. 'SOLUSDT').")

        direction = str(payload.get("direction", "")).strip().upper()
        if direction not in ("LONG", "SHORT"):
            raise ValueError(f"Invalid direction '{direction}'. Must be 'LONG' or 'SHORT'.")

        order_type = str(payload.get("order_type", "LIMIT")).strip().upper()
        if order_type not in ("LIMIT", "MARKET"):
            raise ValueError(f"Invalid order_type '{order_type}'. Must be 'LIMIT' or 'MARKET'.")

        try:
            entry_price = float(payload.get("entry_price", 0.0))
            stop_loss_price = float(payload.get("stop_loss_price", 0.0))
            take_profit_price = float(payload.get("take_profit_price", 0.0))
        except (ValueError, TypeError) as err:
            raise ValueError(f"Price fields must be valid numeric values: {err}")

        if entry_price <= 0.0 or stop_loss_price <= 0.0 or take_profit_price <= 0.0:
            raise ValueError("entry_price, stop_loss_price, and take_profit_price must all be > 0.0.")

        # Geometric consistency check
        if direction == "LONG":
            if not (stop_loss_price < entry_price < take_profit_price):
                raise ValueError(
                    f"Invalid LONG setup geometry: Stop Loss (${stop_loss_price}) must be below Entry (${entry_price}) and Take Profit (${take_profit_price}) must be above Entry."
                )
        else:  # SHORT
            if not (stop_loss_price > entry_price > take_profit_price):
                raise ValueError(
                    f"Invalid SHORT setup geometry: Stop Loss (${stop_loss_price}) must be above Entry (${entry_price}) and Take Profit (${take_profit_price}) must be below Entry."
                )

        leverage = int(payload.get("leverage", 10) or 10)
        if leverage < 1 or leverage > 100:
            raise ValueError(f"Leverage must be between 1 and 100 (got {leverage}).")

        margin_mode = str(payload.get("margin_mode", "ISOLATED")).strip().upper()
        if margin_mode not in ("ISOLATED", "CROSS", "CROSSED"):
            margin_mode = "ISOLATED"
        if margin_mode == "CROSSED":
            margin_mode = "CROSS"

        venue = str(payload.get("execution_venue", "PAPER")).strip().upper()
        if venue not in ("PAPER", "LIVE"):
            venue = "PAPER"

        notes = str(payload.get("notes", "")).strip()

        # Specs and rounding
        specs = self.get_symbol_specs(raw_symbol, mark_price=entry_price)
        price_prec = get_dynamic_precision(entry_price, specs.tick_size)

        entry_price = round_price_dyn(entry_price, price_prec, specs.tick_size)
        stop_loss_price = round_price_dyn(stop_loss_price, price_prec, specs.tick_size)
        take_profit_price = round_price_dyn(take_profit_price, price_prec, specs.tick_size)

        stop_distance = abs(entry_price - stop_loss_price)
        target_distance = abs(take_profit_price - entry_price)
        rr_ratio = round(target_distance / stop_distance, 2) if stop_distance > 0 else 0.0

        # Quantity calculation / resolution
        qty_input = payload.get("qty")
        try:
            qty_val = float(qty_input) if qty_input is not None else 0.0
        except (ValueError, TypeError):
            qty_val = 0.0

        max_risk_input = payload.get("max_risk_usdt")
        try:
            max_risk_val = float(max_risk_input) if max_risk_input is not None else 0.0
        except (ValueError, TypeError):
            max_risk_val = 0.0

        if qty_val <= 0.0:
            if max_risk_val > 0.0 and stop_distance > 0.0:
                raw_qty = max_risk_val / stop_distance
            else:
                raw_qty = (specs.min_notional * 1.1) / entry_price
        else:
            raw_qty = qty_val

        final_qty = max(specs.min_qty, specs.round_quantity(raw_qty))
        notional_usdt = round(final_qty * entry_price, 4)
        margin_usdt = round(notional_usdt / leverage, 4)
        risk_usdt = round(final_qty * stop_distance, 4)

        if notional_usdt < specs.min_notional:
            min_safe_qty = specs.round_quantity((specs.min_notional * 1.05) / entry_price)
            raise ValueError(
                f"Position notional (${notional_usdt:.2f}) is below Aster DEX minimum notional (${specs.min_notional:.2f}). "
                f"Minimum quantity required for {raw_symbol} is {min_safe_qty}."
            )

        now_utc = datetime.now(timezone.utc)
        now_ms = int(now_utc.timestamp() * 1000)

        # LIVE Venue Routing (Aster DEX Mainnet)
        if venue == "LIVE":
            if live_trading_locked:
                raise PermissionError(
                    "Live trading execution blocked: system is operating under live_trading_locked=True invariant. "
                    "Set execution_venue to 'PAPER' for simulated execution."
                )
            if not live_adapter:
                raise RuntimeError("Live exchange adapter is not initialized or Aster credentials are not configured.")

            # 1. Enforce ISOLATED margin mode on Aster DEX
            try:
                live_adapter.set_margin_type(raw_symbol, margin_mode)
            except Exception as err:
                logger.warning(f"Aster DEX set_margin_type note: {err}")

            # 2. Enforce 10x leverage on Aster DEX
            try:
                live_adapter.set_leverage(raw_symbol, leverage)
            except Exception as err:
                logger.warning(f"Aster DEX set_leverage note: {err}")

            # 3. Submit Entry Order (LIMIT or MARKET)
            order_side = "BUY" if direction == "LONG" else "SELL"
            close_side = "SELL" if direction == "LONG" else "BUY"

            live_order_resp = live_adapter.create_order(
                symbol=raw_symbol,
                side=order_side,
                order_type=order_type,
                quantity=final_qty,
                price=entry_price if order_type == "LIMIT" else None,
                time_in_force="GTC" if order_type == "LIMIT" else None,
            )
            order_id = str(live_order_resp.get("orderId", f"LIV_{raw_symbol}_{now_ms}"))

            # 4. Submit Protective Stop Loss Order (STOP_MARKET, reduceOnly)
            sl_resp = None
            try:
                sl_resp = live_adapter.create_order(
                    symbol=raw_symbol,
                    side=close_side,
                    order_type="STOP_MARKET",
                    quantity=final_qty,
                    stop_price=stop_loss_price,
                    reduce_only=True,
                )
            except Exception as sl_err:
                logger.warning(f"Aster DEX protective SL submission note: {sl_err}")

            # 5. Submit Protective Take Profit Order (TAKE_PROFIT_MARKET, reduceOnly)
            tp_resp = None
            try:
                tp_resp = live_adapter.create_order(
                    symbol=raw_symbol,
                    side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=final_qty,
                    stop_price=take_profit_price,
                    reduce_only=True,
                )
            except Exception as tp_err:
                logger.warning(f"Aster DEX protective TP submission note: {tp_err}")

            sl_order_id = str(sl_resp.get("orderId")) if (sl_resp and sl_resp.get("orderId")) else None
            tp_order_id = str(tp_resp.get("orderId")) if (tp_resp and tp_resp.get("orderId")) else None

            # Persist live order in database journal for tracking
            if journal:
                try:
                    dir_enum = Direction.LONG if direction == "LONG" else Direction.SHORT
                    live_paper_order = PaperOrder(
                        order_id=order_id,
                        candidate_id=f"AI_LIVE_{raw_symbol}_{now_ms}",
                        symbol=raw_symbol,
                        direction=dir_enum,
                        order_type=order_type,
                        price=entry_price,
                        stop_loss=stop_loss_price,
                        take_profit=take_profit_price,
                        quantity=final_qty,
                        notional_usdt=notional_usdt,
                        margin_usdt=margin_usdt,
                        leverage=float(leverage),
                        status=PaperOrderStatus.CREATED,
                        created_bar_time_ms=now_ms,
                        entry_category="AI_LIVE_ASTERDEX",
                        model_id="AI_LIVE",
                        created_at_utc=now_utc.isoformat(),
                        updated_at_utc=now_utc.isoformat(),
                    )
                    journal.save_paper_order(live_paper_order.to_dict())
                    journal.save_lifecycle_event(
                        candidate_id=live_paper_order.candidate_id,
                        symbol=raw_symbol,
                        stage="PAPER_ORDER_CREATED",
                        timestamp_ms=now_ms,
                        reason_code="AI_LIVE_ORDER_PLACED",
                        reason_detail=f"Live order placed on Aster DEX (entry={order_id}, sl={sl_order_id}, tp={tp_order_id}). Notes: {notes}",
                        order_id=order_id,
                        model_id="AI_LIVE",
                        metadata={
                            "entry_price": entry_price,
                            "stop_loss": stop_loss_price,
                            "take_profit": take_profit_price,
                            "quantity": final_qty,
                            "notional_usdt": notional_usdt,
                            "margin_usdt": margin_usdt,
                            "leverage": leverage,
                            "margin_mode": margin_mode,
                            "sl_order_id": sl_order_id,
                            "tp_order_id": tp_order_id,
                            "raw_response": live_order_resp,
                        },
                    )
                except Exception as db_err:
                    logger.warning(f"Failed to record live order in journal: {db_err}")

            return {
                "status": "SUCCESS",
                "execution_venue": "LIVE",
                "order_id": order_id,
                "entry_order_id": order_id,
                "sl_order_id": sl_order_id,
                "tp_order_id": tp_order_id,
                "symbol": raw_symbol,
                "direction": direction,
                "order_type": order_type,
                "entry_price": entry_price,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
                "quantity": final_qty,
                "notional_usdt": notional_usdt,
                "margin_usdt": margin_usdt,
                "leverage": leverage,
                "margin_mode": margin_mode,
                "risk_usdt": risk_usdt,
                "rr_ratio": rr_ratio,
                "notes": notes,
                "raw_entry_response": live_order_resp,
                "raw_sl_response": sl_resp,
                "raw_tp_response": tp_resp,
                "created_at_utc": now_utc.isoformat(),
                "message": f"Successfully placed LIVE {direction} {order_type} bracket on Aster DEX for {final_qty} {raw_symbol} at ${entry_price} (ISOLATED {leverage}x).",
            }

        # PAPER Venue Routing
        order_id = f"PORD_AI_{raw_symbol}_{now_ms}"
        candidate_id = f"AI_{raw_symbol}_{now_ms}"

        dir_enum = Direction.LONG if direction == "LONG" else Direction.SHORT

        paper_order = PaperOrder(
            order_id=order_id,
            candidate_id=candidate_id,
            symbol=raw_symbol,
            direction=dir_enum,
            order_type=order_type,
            price=entry_price,
            stop_loss=stop_loss_price,
            take_profit=take_profit_price,
            quantity=final_qty,
            notional_usdt=notional_usdt,
            margin_usdt=margin_usdt,
            leverage=float(leverage),
            status=PaperOrderStatus.PENDING,
            created_bar_time_ms=now_ms,
            entry_category="AI_MANUAL_SETUP",
            model_id="AI_SETUP",
            created_at_utc=now_utc.isoformat(),
            updated_at_utc=now_utc.isoformat(),
        )

        # Persist paper order
        if journal:
            journal.save_paper_order(paper_order.to_dict())
            journal.save_lifecycle_event(
                candidate_id=candidate_id,
                symbol=raw_symbol,
                stage="PAPER_ORDER_CREATED",
                timestamp_ms=now_ms,
                reason_code="AI_SETUP_EXECUTED",
                reason_detail=f"AI Trade Setup placed via 1-Click Executor in PAPER mode (ISOLATED {leverage}x). Notes: {notes}",
                order_id=order_id,
                model_id="AI_SETUP",
                metadata={
                    "entry_price": entry_price,
                    "stop_loss": stop_loss_price,
                    "take_profit": take_profit_price,
                    "quantity": final_qty,
                    "notional_usdt": notional_usdt,
                    "margin_usdt": margin_usdt,
                    "leverage": leverage,
                    "margin_mode": margin_mode,
                    "rr_ratio": rr_ratio,
                    "notes": notes,
                },
            )

        # Register in fast runner pending orders if available
        if runners and "MODEL_B_FAST" in runners and runners["MODEL_B_FAST"] and hasattr(runners["MODEL_B_FAST"], "paper_engine"):
            runners["MODEL_B_FAST"].paper_engine.pending_orders[order_id] = paper_order

        return {
            "status": "SUCCESS",
            "execution_venue": "PAPER",
            "order_id": order_id,
            "candidate_id": candidate_id,
            "symbol": raw_symbol,
            "direction": direction,
            "order_type": order_type,
            "entry_price": entry_price,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "quantity": final_qty,
            "notional_usdt": notional_usdt,
            "margin_usdt": margin_usdt,
            "leverage": leverage,
            "margin_mode": margin_mode,
            "risk_usdt": risk_usdt,
            "rr_ratio": rr_ratio,
            "notes": notes,
            "created_at_utc": now_utc.isoformat(),
            "message": f"Successfully placed simulated {direction} Limit Order for {final_qty} {raw_symbol} at ${entry_price} in PAPER mode with ISOLATED {leverage}x and full TP/SL bracket.",
        }
