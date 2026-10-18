"""Tests for Model B's Three Opportunity Engines:
1. Liquidity Wall Hunter (wall persistence, baseline multiple, spoofing detection)
2. New Market Opportunity Scanner (listing age, spread, liquidity feasibility vs risk budget)
3. News & Catalyst Engine (provenance, deduplication, conflict tracking, anti-hallucination gate)
"""

from datetime import datetime, timezone
import pytest

from src.catalyst.engine import NewsCatalystEngine
from src.catalyst.types import CatalystCategory, CatalystEvent, VerificationStatus
from src.exchange.new_market_scanner import NewMarketOpportunityScanner
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook, OrderBookLevel
from src.liquidity.wall_hunter import LiquidityWallHunter


class TestNewsCatalystEngine:
    def test_deduplication_and_anti_hallucination(self):
        engine = NewsCatalystEngine()

        # Unverified report (e.g. twitter rumor)
        rumor = CatalystEvent(
            event_id="rumor-001",
            title="Unconfirmed token listing leak",
            source_name="TwitterLeaker",
            source_url="https://x.com/leaks/123",
            published_at_utc=datetime.now(timezone.utc).isoformat(),
            ingested_at_utc=datetime.now(timezone.utc).isoformat(),
            category=CatalystCategory.EXCHANGE_LISTING,
            verification_status=VerificationStatus.UNVERIFIED_REPORT,
            affected_symbols=["HYPEUSDT"],
            confidence_score=0.95,
        )

        assert engine.ingest_event(rumor) is True
        # Duplicate should be rejected
        assert engine.ingest_event(rumor) is False

        # Invariant: Unverified report CANNOT trigger trade bias
        assert rumor.is_eligible_for_trade_bias is False
        assert engine.evaluate_catalyst_bias("HYPEUSDT") is None

    def test_confirmed_fact_and_conflict_neutralization(self):
        engine = NewsCatalystEngine()

        # Confirmed listing
        listing = CatalystEvent(
            event_id="ann-001",
            title="Official AsterDEX listing announcement",
            source_name="AsterDEX Official",
            source_url="https://asterdex.com/announcements/101",
            published_at_utc=datetime.now(timezone.utc).isoformat(),
            ingested_at_utc=datetime.now(timezone.utc).isoformat(),
            category=CatalystCategory.EXCHANGE_LISTING,
            verification_status=VerificationStatus.CONFIRMED_FACT,
            affected_symbols=["SOLUSDT"],
            confidence_score=0.98,
        )
        engine.ingest_event(listing)
        bias = engine.evaluate_catalyst_bias("SOLUSDT")
        assert bias is not None
        assert bias["bias"] == "BULLISH"

        # Conflicting event arrives (e.g. delisting report)
        delisting = CatalystEvent(
            event_id="ann-002",
            title="Emergency delisting announcement",
            source_name="ExchangeOps",
            source_url="https://asterdex.com/announcements/102",
            published_at_utc=datetime.now(timezone.utc).isoformat(),
            ingested_at_utc=datetime.now(timezone.utc).isoformat(),
            category=CatalystCategory.EXCHANGE_DELISTING,
            verification_status=VerificationStatus.CONFIRMED_FACT,
            affected_symbols=["SOLUSDT"],
            confidence_score=0.90,
        )
        engine.ingest_event(delisting)

        # Conflict should neutralize bias
        assert engine.evaluate_catalyst_bias("SOLUSDT") is None


class TestLiquidityWallHunter:
    def test_wall_detection_and_persistence(self):
        hunter = LiquidityWallHunter(wall_multiple_threshold=3.0, min_persistence_sec=15.0)
        hunter.update_baseline("BTCUSDT", avg_depth_usdt=10000.0)

        # Snapshot 1 at t=0
        bids = [
            OrderBookLevel(price=60000.0, size=0.1),
            OrderBookLevel(price=59500.0, size=1.0), # 1.0 * 59500 = $59,500 (~5.95x baseline)
        ]
        asks = [OrderBookLevel(price=60010.0, size=0.1)]
        book_t0 = NormalizedOrderBook(
            symbol="BTCUSDT",
            exchange_name="asterdex",
            timestamp_ms=1000000,
            bids=bids,
            asks=asks,
        )

        walls_t0 = hunter.analyze_order_book(book_t0, now_ms=1000000)
        assert len(walls_t0) == 1
        assert walls_t0[0].side == "BID"
        assert walls_t0[0].multiple_of_baseline >= 5.0
        assert walls_t0[0].is_persistent is False # t=0, persistence = 0

        # Snapshot 2 at t=20s (persisted wall)
        walls_t20 = hunter.analyze_order_book(book_t0, now_ms=1020000)
        assert len(walls_t20) == 1
        assert walls_t20[0].persistence_seconds == 20.0
        assert walls_t20[0].is_persistent is True

    def test_spoof_cancellation_detection(self):
        hunter = LiquidityWallHunter(
            wall_multiple_threshold=3.0,
            spoof_distance_threshold_bps=5.0,
        )
        hunter.update_baseline("BTCUSDT", avg_depth_usdt=10000.0)

        # Wall at 60000
        bids = [OrderBookLevel(price=60000.0, size=1.0)]
        asks = [OrderBookLevel(price=60005.0, size=0.1)]
        book_t0 = NormalizedOrderBook(
            symbol="BTCUSDT",
            exchange_name="asterdex",
            timestamp_ms=1000000,
            bids=bids,
            asks=asks,
        )
        hunter.analyze_order_book(book_t0, now_ms=1000000)

        # Price moves right up to 60002 (mid ~60002.5, < 5 bps away) and the bid wall vanishes
        bids_pulled = [OrderBookLevel(price=59800.0, size=0.1)]
        asks_close = [OrderBookLevel(price=60003.0, size=0.1)]
        book_t1 = NormalizedOrderBook(
            symbol="BTCUSDT",
            exchange_name="asterdex",
            timestamp_ms=1002000,
            bids=bids_pulled,
            asks=asks_close,
        )
        hunter.analyze_order_book(book_t1, now_ms=1002000)

        # The prior wall should have been flagged as canceled pre-touch
        prior = hunter._active_walls["BTCUSDT"]
        assert len(hunter.get_persistent_walls("BTCUSDT")) == 0


class TestNewMarketOpportunityScanner:
    def test_evaluate_healthy_vs_infeasible_instrument(self):
        scanner = NewMarketOpportunityScanner(
            max_listing_age_days=14.0,
            max_spread_bps=20.0,
            min_depth_10bps_usdt=5000.0,
            min_volume_24h_usdt=500000.0,
            target_risk_usdt=1.00,
            max_margin_usdt=15.00,
        )

        healthy_inst = NormalizedInstrument(
            symbol="NEWPERP",
            exchange_name="asterdex",
            base_asset="NEW",
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
            min_notional=5.0,
            volume_24h_usdt=1000000.0,
        )
        healthy_book = NormalizedOrderBook(
            symbol="NEWPERP",
            exchange_name="asterdex",
            timestamp_ms=1000000,
            bids=[OrderBookLevel(price=10.0, size=1000.0)], # $10,000 depth
            asks=[OrderBookLevel(price=10.01, size=1000.0)],
        )

        now_ms = 1700000000000
        res = scanner.evaluate_instrument(
            instrument=healthy_inst,
            order_book=healthy_book,
            listing_time_ms=now_ms - int(5 * 86400 * 1000), # 5 days old
            now_ms=now_ms,
        )
        assert res.is_feasible is True
        assert res.listing_age_days == 5.0
        assert len(res.rejection_reasons) == 0

    def test_evaluate_rejects_excess_spread_and_risk(self):
        scanner = NewMarketOpportunityScanner(
            target_risk_usdt=1.00,
            max_margin_usdt=15.00,
        )

        illiquid_inst = NormalizedInstrument(
            symbol="BADPERP",
            exchange_name="asterdex",
            base_asset="BAD",
            quote_asset="USDT",
            min_qty=1.0,
            step_size=1.0,
            tick_size=0.01,
            min_notional=200.0,      # Huge min notional ($200 * 2% stop = $4.00 > $1.00 risk cap)
            volume_24h_usdt=10000.0, # Too low
        )
        wide_book = NormalizedOrderBook(
            symbol="BADPERP",
            exchange_name="asterdex",
            timestamp_ms=1000000,
            bids=[OrderBookLevel(price=10.0, size=10.0)],
            asks=[OrderBookLevel(price=10.50, size=10.0)], # 500 bps spread!
        )

        res = scanner.evaluate_instrument(
            instrument=illiquid_inst,
            order_book=wide_book,
            listing_time_ms=None,
            now_ms=1000000,
        )
        assert res.is_feasible is False
        assert any("Spread" in r for r in res.rejection_reasons)
        assert any("24h volume" in r for r in res.rejection_reasons)
        assert any("risk" in r for r in res.rejection_reasons)
