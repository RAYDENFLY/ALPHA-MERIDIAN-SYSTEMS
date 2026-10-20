"""Quantitative Market Structure, Order Book, and Multi-Timeframe Analyzer for Groq AI Analyst.

Extracts:
- Swings (BSL / SSL) from closed candles
- Support / Demand zones & Supply zones
- Liquidity sweeps & reclaims
- Level-2 Order-Book depth and bid/ask imbalance
- BTC Market Context ($79,864 - $80,400 reference zone)
- Relative Strength vs BTC
- Data freshness, candle close verification, and staleness detection
"""

from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from src.ai_analyst.types import QuantitativeMarketSnapshot
from src.core.types import Candle
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook
from src.demand_zone.btc_context import BTCContextEvaluator
from src.demand_zone.types import DemandZoneConfig
from src.demand_zone.zone_detector import DemandZoneDetector
from src.smc.swings import detect_swings


class AIMarketAnalyzer:
    """Gathers read-only exchange market telemetry and extracts quantitative SMC/SNR features."""

    TIMEFRAME_SEC = {
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }

    def __init__(self, adapter: Optional[AsterAdapter] = None) -> None:
        self.adapter = adapter or AsterAdapter()
        self.zone_detector = DemandZoneDetector()
        self.btc_evaluator = BTCContextEvaluator(DemandZoneConfig())

    def analyze_market(
        self,
        symbol: str,
        timeframe: str = "1h",
        candles_override: Optional[List[Candle]] = None,
        orderbook_override: Optional[NormalizedOrderBook] = None,
        now_ms: Optional[int] = None,
    ) -> QuantitativeMarketSnapshot:
        """Extract multi-layered structural, liquidity, and risk metrics for a symbol."""
        now = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        norm_sym = symbol.upper()

        # 1. Fetch live candles (or use override)
        if candles_override:
            candles = candles_override
        else:
            try:
                candles = self.adapter.get_klines(norm_sym, timeframe, limit=100)
            except Exception:
                candles = []

        # 2. Fetch live order book (or use override)
        if orderbook_override:
            book = orderbook_override
        else:
            try:
                book = self.adapter.get_order_book(norm_sym, limit=50)
            except Exception:
                book = None

        # 3. Fetch bulk tickers for 24h volume and BTC benchmark
        try:
            bulk_snaps = self.adapter.get_bulk_market_snapshot()
        except Exception:
            bulk_snaps = {}

        sym_snap = bulk_snaps.get(norm_sym, {})
        btc_snap = bulk_snaps.get("BTCUSDT", {})

        current_px = float(sym_snap.get("last_price") or (candles[-1].close if candles else 100.0))
        vol_24h = float(sym_snap.get("volume_24h_usdt") or 0.0)
        spread_bps = float(sym_snap.get("spread_bps") or 0.0)

        btc_px = float(btc_snap.get("last_price") or 81000.0)
        btc_vol_change_24h = float(btc_snap.get("price_change_pct") or 0.0)
        sym_change_24h = float(sym_snap.get("price_change_pct") or 0.0)
        rs_vs_btc = sym_change_24h - btc_vol_change_24h

        # BTC Context
        btc_ctx = self.btc_evaluator.evaluate(btc_px, now_ms=now)
        btc_status_str = btc_ctx.zone_status.value

        # 4. Candles analysis: closed only
        usable = [c for c in candles if getattr(c, "is_closed", True)]
        if not usable and candles:
            usable = candles

        data_time_ms = usable[-1].timestamp_ms if usable else now
        close_time_ms = getattr(usable[-1], "close_time_ms", data_time_ms + 3600000) if usable else now
        data_age_sec = max(0.0, (now - close_time_ms) / 1000.0) if close_time_ms > 0 else 0.0

        tf_seconds = self.TIMEFRAME_SEC.get(timeframe.lower(), 3600)
        is_stale = data_age_sec > (tf_seconds * 2.0)

        # 5. Volatility (ATR 14)
        if len(usable) >= 15:
            highs = [c.high for c in usable]
            lows = [c.low for c in usable]
            closes = [c.close for c in usable]
            tr = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(usable))]
            atr_14 = float(np.mean(tr[-14:]))
            atr_pct = (atr_14 / current_px) * 100.0 if current_px > 0 else 1.0
        else:
            atr_14 = current_px * 0.02
            atr_pct = 2.0

        # 6. Swings & Liquidity
        swings = detect_swings(usable, left_bars=3, right_bars=2) if len(usable) >= 10 else []
        swing_highs = [s.price for s in swings if s.swing_type.value == "HIGH"]
        swing_lows = [s.price for s in swings if s.swing_type.value == "LOW"]

        recent_swing_high = swing_highs[-1] if swing_highs else (current_px * 1.03)
        recent_swing_low = swing_lows[-1] if swing_lows else (current_px * 0.97)

        # 7. Demand & Supply zones
        demand_zones = self.zone_detector.detect_demand_zones(norm_sym, usable, current_px) if len(usable) >= 15 else []
        best_dz = self.zone_detector.select_best_demand_zone(demand_zones, current_px)

        dz_lower = best_dz.lower_price if best_dz else (current_px * 0.97)
        dz_upper = best_dz.upper_price if best_dz else (current_px * 0.985)

        # Symmetrical supply zone estimate around recent swing highs
        sz_lower = recent_swing_high * 0.995
        sz_upper = recent_swing_high * 1.01

        # Sweep check: did any recent candle low dip below a prior swing low and close back above?
        recent_sweep = False
        if len(usable) >= 5 and swing_lows:
            latest_bar = usable[-1]
            prior_low = swing_lows[-1]
            if latest_bar.low < prior_low and latest_bar.close > prior_low:
                recent_sweep = True

        # 8. Order-book depth & imbalance
        bid_depth_25 = 0.0
        ask_depth_25 = 0.0
        imbalance_25 = 0.0

        if book and book.bids and book.asks:
            best_bid = float(book.bids[0].price if hasattr(book.bids[0], "price") else book.bids[0][0])
            best_ask = float(book.asks[0].price if hasattr(book.asks[0], "price") else book.asks[0][0])
            mid_px = (best_bid + best_ask) / 2.0
            band_25 = mid_px * 0.0025

            def _get_notional(level):
                p = float(level.price if hasattr(level, "price") else level[0])
                s = float(level.size if hasattr(level, "size") else level[1])
                return p, p * s

            for item in book.bids:
                p, notional = _get_notional(item)
                if p >= mid_px - band_25:
                    bid_depth_25 += notional

            for item in book.asks:
                p, notional = _get_notional(item)
                if p <= mid_px + band_25:
                    ask_depth_25 += notional

            tot = bid_depth_25 + ask_depth_25
            if tot > 0:
                imbalance_25 = (bid_depth_25 - ask_depth_25) / tot

        return QuantitativeMarketSnapshot(
            symbol=norm_sym,
            timeframe=timeframe,
            current_price=round(current_px, 6),
            volume_24h_usdt=round(vol_24h, 2),
            spread_bps=round(spread_bps, 2),
            atr_14=round(atr_14, 6),
            atr_pct=round(atr_pct, 2),
            swing_high=round(recent_swing_high, 6),
            swing_low=round(recent_swing_low, 6),
            demand_zone_lower=round(dz_lower, 6),
            demand_zone_upper=round(dz_upper, 6),
            supply_zone_lower=round(sz_lower, 6),
            supply_zone_upper=round(sz_upper, 6),
            recent_liquidity_sweep=recent_sweep,
            orderbook_bid_depth_25bps=round(bid_depth_25, 2),
            orderbook_ask_depth_25bps=round(ask_depth_25, 2),
            orderbook_imbalance_25bps=round(imbalance_25, 4),
            btc_price=round(btc_px, 2),
            btc_zone_status=btc_status_str,
            relative_strength_vs_btc_24h=round(rs_vs_btc, 2),
            data_timestamp_ms=data_time_ms,
            candle_close_time_ms=close_time_ms,
            data_age_sec=round(data_age_sec, 1),
            is_stale=is_stale,
        )
