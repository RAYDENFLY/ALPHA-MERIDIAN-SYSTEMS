"""Unit tests for Model B liquidity features, CVD, absorption, and collector."""

import pytest
from pathlib import Path
import tempfile

from src.core.types import Candle, Direction
from src.data.database import DatabaseManager
from src.exchange.normalized import (
    NormalizedOrderBook,
    NormalizedTrade,
    OrderBookLevel,
)
from src.liquidity.features import (
    compute_order_book_features,
    compute_trade_flow_features,
    detect_liquidity_sweep_reclaim,
    LiquidityFeatureExtractor,
)
from src.liquidity.collector import LiquidityCollector


class TestLiquidityFeatures:
    def test_compute_order_book_imbalances(self):
        # Heavy bid side: bids $300 vs asks $100
        bids = [OrderBookLevel(price=100.0, size=3.0)]
        asks = [OrderBookLevel(price=100.1, size=1.0)]

        ob = NormalizedOrderBook(
            symbol="ETHUSDT",
            exchange_name="asterdex",
            timestamp_ms=1700000000000,
            bids=bids,
            asks=asks,
        )

        feats = compute_order_book_features(ob)
        assert feats["imbalance_25bps"] > 0.40  # Positive bid skew
        assert feats["spread_bps"] > 0.0

    def test_compute_trade_flow_cvd_and_absorption(self):
        trades = [
            NormalizedTrade(trade_id="1", symbol="ETHUSDT", exchange_name="asterdex", timestamp_ms=1000, price=2000.0, size=10.0, notional_usdt=20000.0, side="BUY"),
            NormalizedTrade(trade_id="2", symbol="ETHUSDT", exchange_name="asterdex", timestamp_ms=2000, price=2000.5, size=5.0, notional_usdt=10000.0, side="SELL"),
            NormalizedTrade(trade_id="3", symbol="ETHUSDT", exchange_name="asterdex", timestamp_ms=3000, price=2000.2, size=15.0, notional_usdt=30000.0, side="BUY"),
        ]

        flow = compute_trade_flow_features(trades, large_trade_threshold_usdt=15000.0)
        assert flow["taker_buy_volume_1h"] == 50000.0
        assert flow["taker_sell_volume_1h"] == 10000.0
        assert flow["cvd_1h"] == 40000.0
        assert flow["large_trade_count"] == 2  # trade 1 and 3 > 15,000 USDT
        assert flow["absorption_score"] > 0.0

    def test_detect_liquidity_sweep_reclaim_long(self):
        swing_low = 60000.0

        # Case 1: Decisive sweep and reclaim: low = 59800, close = 60100
        c1 = Candle(symbol="BTCUSDT", timeframe="1h", timestamp_ms=1000, close_time_ms=2000, open=60050.0, high=60200.0, low=59800.0, close=60100.0, volume=10.0)
        swept, reclaimed = detect_liquidity_sweep_reclaim(c1, swing_low, Direction.LONG)
        assert swept is True
        assert reclaimed is True

        # Case 2: Sweep without reclaim: low = 59800, close = 59900 (closed below key level)
        c2 = Candle(symbol="BTCUSDT", timeframe="1h", timestamp_ms=2000, close_time_ms=3000, open=60050.0, high=60100.0, low=59800.0, close=59900.0, volume=10.0)
        swept2, reclaimed2 = detect_liquidity_sweep_reclaim(c2, swing_low, Direction.LONG)
        assert swept2 is True
        assert reclaimed2 is False

        # Case 3: No sweep at all: low = 60050 > 60000
        c3 = Candle(symbol="BTCUSDT", timeframe="1h", timestamp_ms=3000, close_time_ms=4000, open=60100.0, high=60300.0, low=60050.0, close=60200.0, volume=10.0)
        swept3, reclaimed3 = detect_liquidity_sweep_reclaim(c3, swing_low, Direction.LONG)
        assert swept3 is False
        assert reclaimed3 is False

    def test_detect_liquidity_sweep_reclaim_short(self):
        swing_high = 70000.0

        # Sweep high: high = 70200, close = 69900 (closed back below)
        c_short = Candle(symbol="BTCUSDT", timeframe="1h", timestamp_ms=1000, close_time_ms=2000, open=69800.0, high=70200.0, low=69700.0, close=69900.0, volume=10.0)
        swept, reclaimed = detect_liquidity_sweep_reclaim(c_short, swing_high, Direction.SHORT)
        assert swept is True
        assert reclaimed is True


class TestLiquidityCollectorPersistence:
    def test_save_and_retrieve_order_book_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_liq.db"
            db = DatabaseManager(db_path)

            collector = LiquidityCollector(db=db)

            bids = [OrderBookLevel(price=50000.0, size=2.0)]
            asks = [OrderBookLevel(price=50010.0, size=1.5)]
            book = NormalizedOrderBook(
                symbol="BTCUSDT",
                exchange_name="asterdex",
                timestamp_ms=1700000000000,
                bids=bids,
                asks=asks,
            )

            snapshot = LiquidityFeatureExtractor.build_snapshot(
                symbol="BTCUSDT",
                exchange_name="asterdex",
                timestamp_ms=1700000000000,
                order_book=book,
                trades=[],
                sweep_detected=True,
                sweep_direction="LONG",
                sweep_reclaimed=True,
            )

            collector._save_order_book_snapshot(book, snapshot)

            with db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM order_book_snapshots WHERE symbol = 'BTCUSDT';")
                rows = cur.fetchall()
                assert len(rows) == 1
                row = dict(rows[0])
                assert row["symbol"] == "BTCUSDT"
                assert row["venue"] == "asterdex"
                assert float(row["mid_price"]) == pytest.approx(50005.0)
                assert float(row["spread_bps"]) > 0.0
