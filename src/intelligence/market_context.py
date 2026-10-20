"""Unified Market Structure and Liquidity Context Module.

Synthesizes multi-timeframe SMC market structure, L2 order book depth & imbalance,
CVD trade flow proxy, funding rates, open interest, liquidation heatmap zones,
and institutional whale events into a coherent per-symbol decision-support view.

STRICT GOVERNANCE GUARDRAIL:
No unverified, unexplained composite scoring or black-box rankings.
All structural indicators are derived from transparent, verifiable market evidence.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import concurrent.futures
import time
from typing import Any, Dict, List, Optional, Tuple

from src.core.logger import logger
from src.core.types import Candle
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.normalized import NormalizedOrderBook, NormalizedTrade
from src.intelligence.liquidation_heatmap import (
    LiquidationHeatmap,
    LiquidationHeatmapEngine,
)
from src.intelligence.position_analyzer import (
    PositionAnalyzer,
    TimeframeStructure,
)
from src.intelligence.whale_detector import (
    WhaleActivitySummary,
    WhaleDetector,
)


@dataclass
class UnifiedMarketContext:
    """Coherent per-symbol technical and liquidity intelligence context."""
    symbol: str
    mark_price: float
    timeframe_4h: TimeframeStructure
    timeframe_1h: TimeframeStructure
    timeframe_15m: TimeframeStructure
    order_book_depth: Dict[str, Any]
    trade_flow: Dict[str, Any]
    derivatives_context: Dict[str, Any]
    liquidation_heatmap: LiquidationHeatmap
    whale_activity: WhaleActivitySummary
    structural_indicators: Dict[str, str]
    market_posture_summary: str
    key_levels_to_watch: List[Dict[str, Any]]
    data_freshness: Dict[str, Any] = field(default_factory=dict)
    generated_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mark_price": round(self.mark_price, 4),
            "timeframe_4h": self.timeframe_4h.to_dict(),
            "timeframe_1h": self.timeframe_1h.to_dict(),
            "timeframe_15m": self.timeframe_15m.to_dict(),
            "order_book_depth": self.order_book_depth,
            "trade_flow": self.trade_flow,
            "derivatives_context": self.derivatives_context,
            "liquidation_heatmap": self.liquidation_heatmap.to_dict(),
            "whale_activity": self.whale_activity.to_dict(),
            "structural_indicators": self.structural_indicators,
            "market_posture_summary": self.market_posture_summary,
            "key_levels_to_watch": self.key_levels_to_watch,
            "data_freshness": self.data_freshness,
            "generated_at_utc": self.generated_at_utc,
        }


class MarketContextService:
    """Coordinates public market data ingestion and synthesizes unified market context."""

    def __init__(
        self,
        aster_adapter: Optional[AsterAdapter] = None,
        whale_detector: Optional[WhaleDetector] = None,
        cache_ttl_sec: float = 15.0,
    ) -> None:
        self.adapter = aster_adapter or AsterAdapter()
        self.whale_detector = whale_detector or WhaleDetector()
        self.cache_ttl_sec = cache_ttl_sec
        self._cached_contexts: Dict[str, Tuple[float, UnifiedMarketContext]] = {}

    def get_market_context(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> UnifiedMarketContext:
        """Fetch live market data across public endpoints and synthesize unified context."""
        sym = symbol.upper().replace(".P", "")
        now_mono = time.monotonic()

        if not force_refresh and sym in self._cached_contexts:
            cached_time, cached_ctx = self._cached_contexts[sym]
            if (now_mono - cached_time) < self.cache_ttl_sec:
                return cached_ctx

        # Fetch market data concurrently with safe failure isolation
        def _safe_get_klines(s: str, tf: str, lim: int) -> List[Candle]:
            try:
                return self.adapter.get_klines(s, tf, limit=lim)
            except Exception as e:
                logger.warning(f"MarketContext: get_klines failed for {s} {tf}: {e}")
                return []

        def _safe_get_book(s: str, lim: int) -> Optional[NormalizedOrderBook]:
            try:
                return self.adapter.get_order_book(s, limit=lim)
            except Exception as e:
                logger.warning(f"MarketContext: get_order_book failed for {s}: {e}")
                return None

        def _safe_get_trades(s: str, lim: int) -> List[NormalizedTrade]:
            try:
                return self.adapter.get_recent_trades(s, limit=lim)
            except Exception as e:
                logger.warning(f"MarketContext: get_recent_trades failed for {s}: {e}")
                return []

        def _safe_get_oi(s: str) -> Optional[Dict[str, Any]]:
            try:
                return self.adapter.get_open_interest(s)
            except Exception as e:
                logger.warning(f"MarketContext: get_open_interest failed for {s}: {e}")
                return None

        def _safe_get_prem(s: str) -> Optional[Dict[str, Any]]:
            try:
                return self.adapter.get_premium_index(s)
            except Exception as e:
                logger.warning(f"MarketContext: get_premium_index failed for {s}: {e}")
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            fut_4h = executor.submit(_safe_get_klines, sym, "4h", 40)
            fut_1h = executor.submit(_safe_get_klines, sym, "1h", 50)
            fut_15m = executor.submit(_safe_get_klines, sym, "15m", 50)
            fut_book = executor.submit(_safe_get_book, sym, 50)
            fut_trades = executor.submit(_safe_get_trades, sym, 100)
            fut_oi = executor.submit(_safe_get_oi, sym)
            fut_prem = executor.submit(_safe_get_prem, sym)

            candles_4h = fut_4h.result()
            candles_1h = fut_1h.result()
            candles_15m = fut_15m.result()
            book = fut_book.result()
            trades = fut_trades.result()
            oi_data = fut_oi.result()
            prem_data = fut_prem.result()

        # Resolve current mark price
        mark_price = 0.0
        if prem_data and prem_data.get("mark_price"):
            mark_price = float(prem_data["mark_price"])
        elif book and book.mid_price > 0:
            mark_price = book.mid_price
        elif candles_15m:
            mark_price = candles_15m[-1].close
        elif candles_1h:
            mark_price = candles_1h[-1].close

        oi_contracts = oi_data.get("open_interest") if oi_data else None
        funding_rate = prem_data.get("last_funding_rate") if prem_data else 0.0001

        # Freshness metrics
        now_ms = int(time.time() * 1000)
        book_age_ms = max(0, now_ms - (book.timestamp_ms or now_ms)) if book else None
        book_freshness = "LIVE" if (book_age_ms is not None and book_age_ms < 15000) else ("STALE" if book else "UNAVAILABLE")

        freshness_info = {
            "source_exchange": "AsterDEX",
            "timestamp_ms": now_ms,
            "order_book_status": book_freshness,
            "order_book_age_ms": book_age_ms,
            "trades_count": len(trades or []),
            "klines_fresh": bool(candles_15m),
            "status": book_freshness,
        }

        # Synthesize context
        context = self.build_context_from_data(
            symbol=sym,
            mark_price=mark_price,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_15m=candles_15m,
            book=book,
            trades=trades,
            open_interest_contracts=oi_contracts,
            funding_rate=funding_rate,
            data_freshness=freshness_info,
        )

        self._cached_contexts[sym] = (now_mono, context)
        return context

    def build_context_from_data(
        self,
        symbol: str,
        mark_price: float,
        candles_4h: Optional[List[Candle]] = None,
        candles_1h: Optional[List[Candle]] = None,
        candles_15m: Optional[List[Candle]] = None,
        book: Optional[NormalizedOrderBook] = None,
        trades: Optional[List[NormalizedTrade]] = None,
        open_interest_contracts: Optional[float] = None,
        funding_rate: Optional[float] = None,
        data_freshness: Optional[Dict[str, Any]] = None,
    ) -> UnifiedMarketContext:
        """Construct UnifiedMarketContext given market components."""
        sym = symbol.upper()
        if mark_price <= 0:
            mark_price = 1.0

        # 1. Multi-Timeframe Structure
        tf_4h = PositionAnalyzer._evaluate_tf_structure(candles_4h or [], "4h", mark_price)
        tf_1h = PositionAnalyzer._evaluate_tf_structure(candles_1h or [], "1h", mark_price)
        tf_15m = PositionAnalyzer._evaluate_tf_structure(candles_15m or [], "15m", mark_price)

        # 2. Order Book Depth & Imbalance
        top_bid_notional = 0.0
        top_ask_notional = 0.0
        imbalance_ratio = 0.0
        spread_bps = 0.0

        if book:
            top_bid_notional = sum(b.price * b.size for b in book.bids[:10])
            top_ask_notional = sum(a.price * a.size for a in book.asks[:10])
            tot_depth = top_bid_notional + top_ask_notional
            if tot_depth > 0:
                imbalance_ratio = (top_bid_notional - top_ask_notional) / tot_depth
            if book.best_bid and book.best_ask and book.mid_price > 0:
                spread_bps = ((book.best_ask - book.best_bid) / book.mid_price) * 10000.0

        depth_bias = (
            "BID_HEAVY" if imbalance_ratio >= 0.20
            else ("ASK_HEAVY" if imbalance_ratio <= -0.20 else "BALANCED")
        )

        depth_dict = {
            "top10_bid_notional_usdt": round(top_bid_notional, 2),
            "top10_ask_notional_usdt": round(top_ask_notional, 2),
            "depth_imbalance_ratio": round(imbalance_ratio, 3),
            "depth_bias": depth_bias,
            "spread_bps": round(spread_bps, 2),
        }

        # 3. Trade Flow Proxy (Buy vs Sell Volume, CVD proxy)
        buy_vol_notional = 0.0
        sell_vol_notional = 0.0
        for t in (trades or []):
            if t.side == "BUY":
                buy_vol_notional += t.notional_usdt
            else:
                sell_vol_notional += t.notional_usdt

        tot_trade_vol = buy_vol_notional + sell_vol_notional
        cvd_net = buy_vol_notional - sell_vol_notional
        flow_bias = (
            "BUY_DOMINANT" if (buy_vol_notional > sell_vol_notional * 1.25)
            else ("SELL_DOMINANT" if (sell_vol_notional > buy_vol_notional * 1.25) else "BALANCED")
        )

        flow_dict = {
            "buy_volume_usdt": round(buy_vol_notional, 2),
            "sell_volume_usdt": round(sell_vol_notional, 2),
            "net_taker_delta_usdt": round(cvd_net, 2),
            "flow_bias": flow_bias,
            "trades_analyzed_count": len(trades or []),
        }

        # 4. Derivatives Context (OI & Funding)
        f_rate = funding_rate if funding_rate is not None else 0.0001
        annualized_f = f_rate * 3 * 365 * 100
        oi_notional = (open_interest_contracts * mark_price) if open_interest_contracts else None

        deriv_dict = {
            "open_interest_contracts": round(open_interest_contracts, 4) if open_interest_contracts else None,
            "open_interest_usdt": round(oi_notional, 2) if oi_notional else None,
            "funding_rate": f_rate,
            "funding_rate_annualized_pct": round(annualized_f, 2),
            "funding_rate_bias": "LONG_CROWDED" if f_rate > 0.0003 else ("SHORT_CROWDED" if f_rate < -0.0003 else "NEUTRAL"),
        }

        # 5. Liquidation Heatmap Engine
        heatmap = LiquidationHeatmapEngine.generate_heatmap(
            symbol=sym,
            mark_price=mark_price,
            open_interest_contracts=open_interest_contracts,
            funding_rate=f_rate,
            candles_1h=candles_1h,
        )

        # 6. Whale & Large-Order Activity
        whale_summary = self.whale_detector.analyze_market_data(
            symbol=sym,
            book=book,
            trades=trades,
            current_oi_contracts=open_interest_contracts,
            mark_price=mark_price,
        )

        # 7. Structural Indicators Synthesis (Explicit verifiable categories)
        indicators = {
            "macro_4h_trend": tf_4h.trend,
            "local_1h_trend": tf_1h.trend,
            "execution_15m_momentum": tf_15m.trend,
            "order_book_bias": depth_bias,
            "taker_flow_bias": flow_bias,
            "liquidation_risk_profile": heatmap.predominant_risk_side,
            "whale_flow_bias": whale_summary.net_whale_bias,
        }

        # Key Levels to Watch
        key_levels = []
        if tf_1h.last_swing_high:
            key_levels.append({
                "type": "RESISTANCE",
                "label": "1H Swing High",
                "price": round(tf_1h.last_swing_high, 4),
                "distance_pct": round(abs(tf_1h.last_swing_high - mark_price) / mark_price * 100, 2),
            })
        if tf_1h.last_swing_low:
            key_levels.append({
                "type": "SUPPORT",
                "label": "1H Swing Low",
                "price": round(tf_1h.last_swing_low, 4),
                "distance_pct": round(abs(mark_price - tf_1h.last_swing_low) / mark_price * 100, 2),
            })
        if heatmap.short_clusters:
            nearest_s = heatmap.short_clusters[0]
            key_levels.append({
                "type": "LIQUIDATION_MAGNET",
                "label": f"Short Liquidation Pool ({nearest_s.leverage_tier})",
                "price": round(nearest_s.price_level, 4),
                "distance_pct": round(nearest_s.distance_pct, 2),
            })
        if heatmap.long_clusters:
            nearest_l = heatmap.long_clusters[0]
            key_levels.append({
                "type": "LIQUIDATION_MAGNET",
                "label": f"Long Liquidation Pool ({nearest_l.leverage_tier})",
                "price": round(nearest_l.price_level, 4),
                "distance_pct": round(nearest_l.distance_pct, 2),
            })

        # Market Posture Summary Narrative
        narrative_parts = [
            f"4H Macro structure is {tf_4h.trend} with 1H local trend {tf_1h.trend}."
        ]
        if depth_bias != "BALANCED":
            narrative_parts.append(f"Order book depth displays {depth_bias} imbalance ({imbalance_ratio:+.2f}).")
        if whale_summary.active_walls_count > 0:
            narrative_parts.append(f"{whale_summary.active_walls_count} institutional resting depth wall(s) active.")
        if whale_summary.spoof_cancellation_count > 0:
            narrative_parts.append(f"{whale_summary.spoof_cancellation_count} pre-touch spoof cancellation(s) flagged.")
        if heatmap.predominant_risk_side == "LONG_CASCADE_RISK":
            narrative_parts.append(f"Heavier long liquidation concentration below mark price ({heatmap.nearest_long_cluster_pct:.1f}% away).")
        elif heatmap.predominant_risk_side == "SHORT_SQUEEZE_RISK":
            narrative_parts.append(f"Heavier short liquidation concentration overhead ({heatmap.nearest_short_cluster_pct:.1f}% away).")

        summary_narrative = " ".join(narrative_parts)

        return UnifiedMarketContext(
            symbol=sym,
            mark_price=mark_price,
            timeframe_4h=tf_4h,
            timeframe_1h=tf_1h,
            timeframe_15m=tf_15m,
            order_book_depth=depth_dict,
            trade_flow=flow_dict,
            derivatives_context=deriv_dict,
            liquidation_heatmap=heatmap,
            whale_activity=whale_summary,
            structural_indicators=indicators,
            market_posture_summary=summary_narrative,
            key_levels_to_watch=key_levels,
            data_freshness=data_freshness or {},
        )
