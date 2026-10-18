"""Unit tests for Unified Market Context Service."""

import pytest
from unittest.mock import MagicMock
from src.core.types import Candle
from src.exchange.normalized import NormalizedOrderBook, NormalizedTrade, OrderBookLevel
from src.intelligence.market_context import MarketContextService, UnifiedMarketContext


def make_candle(idx: int, price: float, tf: str = "1h") -> Candle:
    ts = 1700000000000 + idx * 3600000
    return Candle(
        symbol="BTCUSDT",
        timeframe=tf,
        timestamp_ms=ts,
        open=price - 10,
        high=price + 50,
        low=price - 50,
        close=price,
        volume=100.0,
        close_time_ms=ts + 3599999,
        quote_volume=100.0 * price,
    )


class TestMarketContextService:
    """Test suite for unified market context synthesis."""

    def test_build_context_from_data_complete(self):
        service = MarketContextService()
        mark_price = 80000.0

        candles_4h = [make_candle(i, 80000 + i * 20, "4h") for i in range(25)]
        candles_1h = [make_candle(i, 80000 + i * 20, "1h") for i in range(25)]
        candles_15m = [make_candle(i, 80000 + i * 10, "15m") for i in range(25)]

        book = NormalizedOrderBook(
            symbol="BTCUSDT",
            exchange_name="asterdex",
            timestamp_ms=1700000000000,
            bids=[OrderBookLevel(price=79950.0, size=1.0)],
            asks=[OrderBookLevel(price=80050.0, size=0.5)],
        )

        trades = [
            NormalizedTrade(
                trade_id="1",
                symbol="BTCUSDT",
                exchange_name="asterdex",
                timestamp_ms=1700000000000,
                price=80000.0,
                size=0.5,
                notional_usdt=40000.0,
                side="BUY",
            )
        ]

        ctx = service.build_context_from_data(
            symbol="BTCUSDT",
            mark_price=mark_price,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_15m=candles_15m,
            book=book,
            trades=trades,
            open_interest_contracts=50.0,
            funding_rate=0.0001,
        )

        assert isinstance(ctx, UnifiedMarketContext)
        assert ctx.symbol == "BTCUSDT"
        assert ctx.mark_price == mark_price
        assert "macro_4h_trend" in ctx.structural_indicators
        assert "local_1h_trend" in ctx.structural_indicators
        assert "order_book_bias" in ctx.structural_indicators
        assert "taker_flow_bias" in ctx.structural_indicators
        assert ctx.order_book_depth["depth_bias"] in ("BID_HEAVY", "ASK_HEAVY", "BALANCED")
        assert ctx.trade_flow["flow_bias"] in ("BUY_DOMINANT", "SELL_DOMINANT", "BALANCED")
        assert len(ctx.liquidation_heatmap.long_clusters) > 0
        assert len(ctx.liquidation_heatmap.short_clusters) > 0

        # Verify serialization
        d = ctx.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert isinstance(d["key_levels_to_watch"], list)
        assert isinstance(d["structural_indicators"], dict)
        assert "market_posture_summary" in d

    def test_caching_behavior(self):
        mock_adapter = MagicMock()
        mock_adapter.get_klines.return_value = [make_candle(i, 80000) for i in range(10)]
        mock_adapter.get_order_book.return_value = None
        mock_adapter.get_recent_trades.return_value = []
        mock_adapter.get_open_interest.return_value = {"open_interest": 10.0}
        mock_adapter.get_premium_index.return_value = {"mark_price": 80000.0, "last_funding_rate": 0.0001}

        service = MarketContextService(aster_adapter=mock_adapter, cache_ttl_sec=30.0)
        ctx1 = service.get_market_context("BTCUSDT")
        ctx2 = service.get_market_context("BTCUSDT")

        # Second call should hit cache and NOT invoke adapter again
        assert ctx1 is ctx2
        assert mock_adapter.get_premium_index.call_count == 1
