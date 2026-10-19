"""Cross-Market Whale Activity Ranking Engine for Model B.

Scans eligible perpetual contracts across AsterDEX, Binance USD(S)-M, and Hyperliquid,
distinguishes resting depth from executed tape flow, tracks persistence and replenishment,
and generates a transparent, evidence-backed ranked watchlist.

CRITICAL GOVERNANCE GUARDRAIL:
Whale flow alerts and ranking scores are decision-support and research telemetry ONLY.
Under NO circumstances does an alert or score trigger automatic live or paper orders.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.core.logger import logger
from src.data.database import DatabaseManager
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook, NormalizedTrade
from src.exchange.service import MultiExchangeService
from src.intelligence.whale_detector import (
    WhaleActivityEvent,
    WhaleActivitySummary,
    WhaleDetector,
    WhaleEventType,
)


@dataclass
class RankedWhaleSymbol:
    """Per-symbol whale activity ranking record."""
    symbol: str
    mark_price: float
    whale_buy_score: float             # 0.0 to 100.0 transparent anomaly score
    whale_sell_score: float            # 0.0 to 100.0 transparent anomaly score
    net_whale_score: float             # -100.0 to +100.0
    net_whale_bias: str                # BULLISH_FLOW, BEARISH_FLOW, BALANCED
    interpretation: str                # e.g. BULLISH_CONTINUATION_SUPPORT, BEARISH_PRESSURE_CAP, ABSORPTION_ZONE, INCONCLUSIVE
    baseline_depth_usdt: float
    resting_bid_notional_usdt: float
    resting_ask_notional_usdt: float
    bid_depth_multiple: float
    ask_depth_multiple: float
    max_bid_persistence_sec: float
    max_ask_persistence_sec: float
    replenishment_count: int
    spoof_cancellation_count: int
    absorption_detected: bool
    active_walls_count: int
    cvd_ratio: float                   # (Buy Vol - Sell Vol) / Total Vol (-1.0 to +1.0)
    aggressive_buy_notional_usdt: float
    aggressive_sell_notional_usdt: float
    venues_covered: List[str]          # e.g. ["asterdex", "binance", "hyperliquid"]
    cross_venue_walls_agreement: bool  # True if walls present on >= 2 venues
    feed_health: str                   # "HIGH", "PARTIAL", "DEGRADED"
    recent_events_count: int
    last_event_time_utc: Optional[str]
    is_trade_signal: bool = False      # Explicit guardrail: never an automatic trade trigger

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mark_price": round(self.mark_price, 4),
            "whale_buy_score": round(self.whale_buy_score, 1),
            "whale_sell_score": round(self.whale_sell_score, 1),
            "net_whale_score": round(self.net_whale_score, 1),
            "net_whale_bias": self.net_whale_bias,
            "interpretation": self.interpretation,
            "baseline_depth_usdt": round(self.baseline_depth_usdt, 2),
            "resting_bid_notional_usdt": round(self.resting_bid_notional_usdt, 2),
            "resting_ask_notional_usdt": round(self.resting_ask_notional_usdt, 2),
            "bid_depth_multiple": round(self.bid_depth_multiple, 2),
            "ask_depth_multiple": round(self.ask_depth_multiple, 2),
            "max_bid_persistence_sec": round(self.max_bid_persistence_sec, 1),
            "max_ask_persistence_sec": round(self.max_ask_persistence_sec, 1),
            "replenishment_count": self.replenishment_count,
            "spoof_cancellation_count": self.spoof_cancellation_count,
            "absorption_detected": self.absorption_detected,
            "active_walls_count": self.active_walls_count,
            "cvd_ratio": round(self.cvd_ratio, 3),
            "aggressive_buy_notional_usdt": round(self.aggressive_buy_notional_usdt, 2),
            "aggressive_sell_notional_usdt": round(self.aggressive_sell_notional_usdt, 2),
            "venues_covered": self.venues_covered,
            "cross_venue_walls_agreement": self.cross_venue_walls_agreement,
            "feed_health": self.feed_health,
            "recent_events_count": self.recent_events_count,
            "last_event_time_utc": self.last_event_time_utc,
            "is_trade_signal": self.is_trade_signal,
        }


class WhaleRankingEngine:
    """Coordinates cross-venue scanning, whale scoring, and ranked watchlist generation."""

    def __init__(
        self,
        exchange_service: Optional[MultiExchangeService] = None,
        whale_detector: Optional[WhaleDetector] = None,
        db: Optional[DatabaseManager] = None,
        cache_ttl_sec: float = 30.0,
    ) -> None:
        self.exchange_service = exchange_service or MultiExchangeService()
        self.whale_detector = whale_detector or WhaleDetector()
        self.db = db
        self.cache_ttl_sec = cache_ttl_sec

        self._cached_watchlist: Optional[Dict[str, Any]] = None
        self._last_scan_time: float = 0.0

    def evaluate_symbol(self, symbol: str) -> Optional[RankedWhaleSymbol]:
        """Collect market data across venues for a single symbol and produce ranked profile."""
        sym = symbol.strip().upper().replace(".P", "")
        now_ms = int(time.time() * 1000)

        # 1. Fetch concurrent order books and trades across venues
        books_dict = self.exchange_service.get_aggregated_order_books(sym)
        trades_dict = self.exchange_service.get_recent_trades(sym, limit=100)

        # Determine feed health
        active_venues = [v for v, b in books_dict.items() if b is not None]
        if not active_venues:
            return None

        feed_health = "HIGH" if len(active_venues) >= 2 else "PARTIAL"

        # Prioritize AsterDEX book, fallback to Binance or Hyperliquid
        primary_venue = "asterdex" if books_dict.get("asterdex") else active_venues[0]
        primary_book = books_dict.get(primary_venue)
        if primary_book is None:
            return None

        # Flatten trades across all venues for flow analysis
        combined_trades: List[NormalizedTrade] = []
        for v_trades in trades_dict.values():
            if v_trades:
                combined_trades.extend(v_trades)
        combined_trades.sort(key=lambda t: t.timestamp_ms)

        # 2. Analyze primary book & tape through WhaleDetector
        summary: WhaleActivitySummary = self.whale_detector.analyze_market_data(
            symbol=sym,
            book=primary_book,
            trades=combined_trades,
            now_ms=now_ms,
        )

        # 3. Inspect other venues for cross-exchange agreement
        cross_venue_bids = 0
        cross_venue_asks = 0
        for v in active_venues:
            b = books_dict.get(v)
            if b and b.bids and b.asks:
                mid = b.mid_price
                if mid > 0:
                    top_b = sum(lvl.price * lvl.size for lvl in b.bids[:5])
                    top_a = sum(lvl.price * lvl.size for lvl in b.asks[:5])
                    if top_b >= summary.baseline_depth_usdt * 1.5:
                        cross_venue_bids += 1
                    if top_a >= summary.baseline_depth_usdt * 1.5:
                        cross_venue_asks += 1

        cross_agreement = cross_venue_bids >= 2 or cross_venue_asks >= 2

        # 4. Compute transparent scoring components
        baseline = max(1.0, summary.baseline_depth_usdt)
        bid_mult = summary.large_bid_notional_usdt / baseline
        ask_mult = summary.large_ask_notional_usdt / baseline

        active_walls = self.whale_detector._tracked_walls.get(sym, {})
        max_bid_persist = max([w.persistence_seconds for w in active_walls.values() if w.side == "BID" and w.is_active], default=0.0)
        max_ask_persist = max([w.persistence_seconds for w in active_walls.values() if w.side == "ASK" and w.is_active], default=0.0)

        replen_count = sum(
            1 for e in summary.recent_events
            if e.event_type in (WhaleEventType.LARGE_BID_REPLENISHED, WhaleEventType.LARGE_ASK_REPLENISHED, WhaleEventType.WALL_REPLENISHMENT)
        )

        buy_trade_vol = sum(t.notional_usdt for t in combined_trades if t.side == "BUY")
        sell_trade_vol = sum(t.notional_usdt for t in combined_trades if t.side == "SELL")
        tot_trade_vol = buy_trade_vol + sell_trade_vol
        cvd_ratio = (buy_trade_vol - sell_trade_vol) / tot_trade_vol if tot_trade_vol > 0 else 0.0

        agg_buy_notional = sum(t.notional_usdt for t in combined_trades if t.side == "BUY" and t.notional_usdt >= baseline * 2.0)
        agg_sell_notional = sum(t.notional_usdt for t in combined_trades if t.side == "SELL" and t.notional_usdt >= baseline * 2.0)

        # Cross-venue bonus: +10 pts if confirmed on >= 2 venues
        cross_bonus_buy = 10.0 if cross_venue_bids >= 2 else 0.0
        cross_bonus_sell = 10.0 if cross_venue_asks >= 2 else 0.0

        whale_buy_score = round(min(100.0, summary.whale_buy_score + cross_bonus_buy), 1)
        whale_sell_score = round(min(100.0, summary.whale_sell_score + cross_bonus_sell), 1)
        net_whale_score = round(whale_buy_score - whale_sell_score, 1)

        # 5. Formulate directional hypothesis / interpretation
        if summary.absorption_detected:
            interpretation = "ABSORPTION_ZONE"
        elif whale_buy_score >= 50.0 and net_whale_score >= 25.0:
            interpretation = "BULLISH_CONTINUATION_SUPPORT"
        elif whale_sell_score >= 50.0 and net_whale_score <= -25.0:
            interpretation = "BEARISH_PRESSURE_CAP"
        elif abs(net_whale_score) < 20.0 and (whale_buy_score > 30.0 or whale_sell_score > 30.0):
            interpretation = "CONTESTED_FLOW"
        else:
            interpretation = "BALANCED_FLOW"

        last_ev_time = (
            datetime.fromtimestamp(summary.recent_events[0].timestamp_ms / 1000, tz=timezone.utc).isoformat()
            if summary.recent_events
            else None
        )

        # 6. Persist detected events to SQLite if db available
        if self.db and summary.recent_events:
            for ev in summary.recent_events:
                self.db.save_whale_event(ev.to_dict())

        return RankedWhaleSymbol(
            symbol=sym,
            mark_price=summary.mark_price,
            whale_buy_score=whale_buy_score,
            whale_sell_score=whale_sell_score,
            net_whale_score=net_whale_score,
            net_whale_bias=summary.net_whale_bias,
            interpretation=interpretation,
            baseline_depth_usdt=summary.baseline_depth_usdt,
            resting_bid_notional_usdt=summary.large_bid_notional_usdt,
            resting_ask_notional_usdt=summary.large_ask_notional_usdt,
            bid_depth_multiple=bid_mult,
            ask_depth_multiple=ask_mult,
            max_bid_persistence_sec=max_bid_persist,
            max_ask_persistence_sec=max_ask_persist,
            replenishment_count=replen_count,
            spoof_cancellation_count=summary.spoof_cancellation_count,
            absorption_detected=summary.absorption_detected,
            active_walls_count=summary.active_walls_count,
            cvd_ratio=cvd_ratio,
            aggressive_buy_notional_usdt=agg_buy_notional,
            aggressive_sell_notional_usdt=agg_sell_notional,
            venues_covered=active_venues,
            cross_venue_walls_agreement=cross_agreement,
            feed_health=feed_health,
            recent_events_count=len(summary.recent_events),
            last_event_time_utc=last_ev_time,
            is_trade_signal=False,
        )

    def scan_and_rank_universe(
        self,
        symbols: Optional[List[str]] = None,
        max_symbols: int = 25,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """Scan candidate perpetuals across venues and produce ranked whale watchlists."""
        now = time.time()
        if not force_refresh and self._cached_watchlist and (now - self._last_scan_time < self.cache_ttl_sec):
            return self._cached_watchlist

        target_symbols = symbols or []
        if not target_symbols:
            # Discover top volume perps across venues
            discovered = self.exchange_service.discover_all_instruments()
            insts = discovered.get("asterdex") or discovered.get("binance") or []
            valid_insts = [i for i in insts if i.volume_status == "AVAILABLE" and i.volume_24h_usdt]
            valid_insts.sort(key=lambda x: x.volume_24h_usdt or 0.0, reverse=True)
            target_symbols = [i.symbol for i in valid_insts[:max_symbols]]

        # Default fallback symbols if discovery empty
        if not target_symbols:
            target_symbols = [
                "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
            ]

        ranked_list: List[RankedWhaleSymbol] = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(self.evaluate_symbol, sym): sym for sym in target_symbols}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    res = fut.result()
                    if res:
                        ranked_list.append(res)
                except Exception as err:
                    logger.debug(f"WhaleRankingEngine: Failed to evaluate {sym}: {err}")

        # Sort watchlists
        top_buy = sorted(ranked_list, key=lambda x: x.whale_buy_score, reverse=True)
        top_sell = sorted(ranked_list, key=lambda x: x.whale_sell_score, reverse=True)

        # Collect active resting walls across all evaluated symbols
        active_walls_list: List[Dict[str, Any]] = []
        for r in ranked_list:
            sym_walls = self.whale_detector._tracked_walls.get(r.symbol, {})
            for w in sym_walls.values():
                if w.is_active:
                    active_walls_list.append({
                        "symbol": w.symbol,
                        "venue": w.venue,
                        "side": w.side,
                        "price": round(w.price, 4),
                        "notional_usdt": round(w.notional_usdt, 2),
                        "distance_bps": round(w.min_distance_bps, 1),
                        "persistence_seconds": round(w.persistence_seconds, 1),
                        "is_spoof_candidate": w.is_spoof_candidate,
                        "first_seen_utc": datetime.fromtimestamp(w.first_seen_ms / 1000, tz=timezone.utc).isoformat(),
                    })

        active_walls_list.sort(key=lambda x: x["notional_usdt"], reverse=True)

        result = {
            "scanned_count": len(ranked_list),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "top_buy_activity": [r.to_dict() for r in top_buy[:15]],
            "top_sell_activity": [r.to_dict() for r in top_sell[:15]],
            "active_resting_walls": active_walls_list[:30],
            "guardrail_notice": "Whale flow alerts and ranking scores are research and decision-support telemetry only. They NEVER automatically submit or modify live or paper orders.",
        }

        self._cached_watchlist = result
        self._last_scan_time = now
        return result
