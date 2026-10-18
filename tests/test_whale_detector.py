"""Unit tests for AsterDEX WhaleDetector."""

import pytest
from src.exchange.normalized import NormalizedOrderBook, NormalizedTrade, OrderBookLevel
from src.intelligence.whale_detector import (
    WhaleActivityEvent,
    WhaleDetector,
    WhaleEventType,
)


def make_book(symbol: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> NormalizedOrderBook:
    return NormalizedOrderBook(
        symbol=symbol,
        exchange_name="asterdex",
        timestamp_ms=1700000000000,
        bids=[OrderBookLevel(price=p, size=s) for p, s in bids],
        asks=[OrderBookLevel(price=p, size=s) for p, s in asks],
    )


class TestWhaleDetector:
    """Test suite for whale detector and 10 event categories."""

    def test_adaptive_baseline_resolution(self):
        detector = WhaleDetector()
        # Mega cap: > 50M
        b_btc = detector.resolve_baseline_depth("BTCUSDT", volume_24h_usdt=80_000_000.0)
        assert b_btc == 100_000.0

        # Small cap: < 500k
        b_micro = detector.resolve_baseline_depth("MEMEUSDT", volume_24h_usdt=200_000.0)
        assert b_micro == 3_000.0

    def test_large_resting_bid_and_ask_detection(self):
        detector = WhaleDetector(wall_multiple=2.0)
        # Baseline = 10,000 USDT.
        detector._baseline_depths["BTCUSDT"] = 10_000.0

        # Create book with a $50k bid wall at 80,000 (0.625 BTC) and $40k ask wall at 81,000 (0.4938 BTC)
        book = make_book(
            "BTCUSDT",
            bids=[(80000.0, 0.625), (79900.0, 0.05)],
            asks=[(81000.0, 0.500), (81100.0, 0.05)],
        )

        summary = detector.analyze_market_data("BTCUSDT", book=book, mark_price=80500.0)
        ev_types = [e.event_type for e in summary.recent_events]
        assert WhaleEventType.LARGE_RESTING_BID in ev_types
        assert WhaleEventType.LARGE_RESTING_ASK in ev_types
        assert summary.active_walls_count == 2
        assert summary.large_bid_notional_usdt >= 50000.0
        assert summary.large_ask_notional_usdt >= 40000.0

    def test_pre_touch_spoof_cancellation_detection(self):
        detector = WhaleDetector(wall_multiple=2.0, spoof_proximity_bps=20.0)
        detector._baseline_depths["BTCUSDT"] = 10_000.0

        # Step 1: Place large bid wall at 80,000 (mid = 80,100 -> ~12.5 bps away)
        book_1 = make_book("BTCUSDT", bids=[(80000.0, 0.5)], asks=[(80200.0, 0.05)])
        detector.analyze_market_data("BTCUSDT", book=book_1, mark_price=80100.0)

        # Step 2: In the next frame, price approaches to 80,050 and the wall is pulled!
        book_2 = make_book("BTCUSDT", bids=[(79800.0, 0.02)], asks=[(80200.0, 0.05)])
        summary_2 = detector.analyze_market_data("BTCUSDT", book=book_2, mark_price=80050.0)

        ev_types_2 = [e.event_type for e in summary_2.recent_events]
        assert WhaleEventType.PRE_TOUCH_CANCELLATION in ev_types_2
        assert summary_2.spoof_cancellation_count >= 1

    def test_aggressive_trades_and_absorption(self):
        detector = WhaleDetector(trade_multiple=2.5)
        detector._baseline_depths["BTCUSDT"] = 10_000.0

        # Large trade: 30,000 USDT (> 2.5x baseline)
        trades = [
            NormalizedTrade(
                trade_id="101",
                symbol="BTCUSDT",
                exchange_name="asterdex",
                timestamp_ms=1700000001000,
                price=80000.0,
                size=0.5,
                notional_usdt=40000.0,
                side="BUY",
            ),
            NormalizedTrade(
                trade_id="102",
                symbol="BTCUSDT",
                exchange_name="asterdex",
                timestamp_ms=1700000002000,
                price=80000.0,
                size=0.5,
                notional_usdt=40000.0,
                side="BUY",
            ),
        ]

        summary = detector.analyze_market_data("BTCUSDT", trades=trades, mark_price=80000.0)
        ev_types = [e.event_type for e in summary.recent_events]
        assert WhaleEventType.LARGE_AGGRESSIVE_BUY in ev_types
        # High volume clustered at 80,000 -> triggers absorption
        assert summary.absorption_detected is True

    def test_open_interest_expansion_and_contraction(self):
        detector = WhaleDetector()
        sym = "SOLUSDT"

        # Snapshot 1: OI = 1000 contracts, mark = 150.0
        detector.analyze_market_data(sym, current_oi_contracts=1000.0, mark_price=150.0)

        # Snapshot 2: OI expands +10% to 1100 while mark price barely moves (150.2)
        summary_exp = detector.analyze_market_data(sym, current_oi_contracts=1100.0, mark_price=150.2)
        ev_types_exp = [e.event_type for e in summary_exp.recent_events]
        assert WhaleEventType.OI_EXPANSION_PRICE_FLAT in ev_types_exp

        # Snapshot 3: OI dumps -10% to 990 while mark price drops sharply to 146.0
        summary_con = detector.analyze_market_data(sym, current_oi_contracts=990.0, mark_price=146.0)
        ev_types_con = [e.event_type for e in summary_con.recent_events]
        assert WhaleEventType.OI_CONTRACTION_VOLUME_SPIKE in ev_types_con

    def test_serialization(self):
        detector = WhaleDetector()
        summary = detector.analyze_market_data("BTCUSDT", mark_price=80000.0)
        d = summary.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert d["net_whale_bias"] == "BALANCED"
        assert isinstance(d["recent_events"], list)
