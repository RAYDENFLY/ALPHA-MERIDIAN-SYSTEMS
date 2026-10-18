"""Unit tests for Model B multi-exchange adapters and normalized data structures."""

import pytest
from unittest.mock import MagicMock, patch

from src.exchange.normalized import (
    NormalizedInstrument,
    NormalizedOrderBook,
    NormalizedTrade,
    OrderBookLevel,
)
from src.exchange.binance_adapter import BinanceFuturesAdapter
from src.exchange.hyperliquid_adapter import HyperliquidAdapter
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.service import MultiExchangeService


class TestNormalizedDataStructures:
    def test_order_book_metrics(self):
        bids = [OrderBookLevel(price=100.0, size=2.0), OrderBookLevel(price=99.0, size=3.0)]
        asks = [OrderBookLevel(price=101.0, size=1.0), OrderBookLevel(price=102.0, size=4.0)]

        ob = NormalizedOrderBook(
            symbol="BTCUSDT",
            exchange_name="binance",
            timestamp_ms=1700000000000,
            bids=bids,
            asks=asks,
        )

        assert ob.best_bid == 100.0
        assert ob.best_ask == 101.0
        assert ob.mid_price == 100.5
        assert ob.spread == 1.0
        assert round(ob.spread_bps, 2) == round((1.0 / 100.5) * 10000.0, 2)

        # Depth at 100 bps: mid 100.5 -> bid cutoff 99.495 (includes 100.0), ask cutoff 101.505 (includes 101.0)
        b_notional, a_notional = ob.depth_at_bps(100.0)
        assert b_notional == 200.0  # 100.0 * 2.0
        assert a_notional == 101.0  # 101.0 * 1.0

        imb = ob.imbalance_ratio(100.0)
        expected_imb = (200.0 - 101.0) / (200.0 + 101.0)
        assert round(imb, 4) == round(expected_imb, 4)


class TestBinanceFuturesAdapter:
    def test_discover_instruments_mocked(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "baseAsset": "BTC",
                    "filters": [
                        {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
                    ],
                },
                {
                    "symbol": "BTCUSD_PERP",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USD",  # Not USDT -> filtered out
                    "status": "TRADING",
                },
                {
                    "symbol": "ETHUSDT",
                    "contractType": "CURRENT_QUARTER",  # Not PERPETUAL -> filtered out
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                },
            ]
        }
        mock_session.get.return_value = mock_resp

        adapter = BinanceFuturesAdapter(session=mock_session)
        insts = adapter.discover_instruments(force_refresh=True)

        assert len(insts) == 1
        inst = insts[0]
        assert inst.symbol == "BTCUSDT"
        assert inst.exchange_name == "binance"
        assert inst.min_qty == 0.001
        assert inst.min_notional == 5.0

    def test_get_order_book_mocked(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "E": 1700000001000,
            "bids": [["90000.0", "1.5"], ["89990.0", "2.0"]],
            "asks": [["90010.0", "1.0"], ["90020.0", "3.0"]],
        }
        mock_session.get.return_value = mock_resp

        adapter = BinanceFuturesAdapter(session=mock_session)
        book = adapter.get_order_book("BTCUSDT")

        assert book is not None
        assert book.symbol == "BTCUSDT"
        assert book.best_bid == 90000.0
        assert book.best_ask == 90010.0
        assert len(book.bids) == 2
        assert len(book.asks) == 2


class TestHyperliquidAdapter:
    def test_discover_instruments_mocked(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"universe": [{"name": "SOL", "szDecimals": 2, "maxLeverage": 20}]},
            [{"markPx": "150.0", "funding": "0.00001", "dayNtlVlm": "50000000.0"}],
        ]
        mock_session.post.return_value = mock_resp

        adapter = HyperliquidAdapter(session=mock_session)
        insts = adapter.discover_instruments(force_refresh=True)

        assert len(insts) == 1
        sol = insts[0]
        assert sol.symbol == "SOLUSDT"
        assert sol.base_asset == "SOL"
        assert sol.exchange_name == "hyperliquid"
        assert sol.mark_price == 150.0
        assert sol.volume_24h_usdt == 50000000.0

    def test_get_order_book_mocked(self):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "coin": "SOL",
            "time": 1700000002000,
            "levels": [
                [{"px": "150.0", "sz": "10.0", "n": 1}],
                [{"px": "150.1", "sz": "8.0", "n": 2}],
            ],
        }
        mock_session.post.return_value = mock_resp

        adapter = HyperliquidAdapter(session=mock_session)
        book = adapter.get_order_book("SOLUSDT")

        assert book is not None
        assert book.symbol == "SOLUSDT"
        assert book.best_bid == 150.0
        assert book.best_ask == 150.1
        assert book.spread == pytest.approx(0.1)


class TestMultiExchangeService:
    def test_discover_all_instruments_coordination(self):
        mock_aster = MagicMock()
        mock_binance = MagicMock()
        mock_hyperliquid = MagicMock()

        inst_a = NormalizedInstrument(symbol="BTCUSDT", exchange_name="asterdex", base_asset="BTC", quote_asset="USDT", min_qty=0.001, step_size=0.001, tick_size=0.1, min_notional=5.0)
        inst_b = NormalizedInstrument(symbol="BTCUSDT", exchange_name="binance", base_asset="BTC", quote_asset="USDT", min_qty=0.001, step_size=0.001, tick_size=0.1, min_notional=5.0)
        inst_h = NormalizedInstrument(symbol="BTCUSDT", exchange_name="hyperliquid", base_asset="BTC", quote_asset="USDT", min_qty=0.001, step_size=0.001, tick_size=0.1, min_notional=10.0)

        mock_aster.discover_instruments.return_value = [inst_a]
        mock_binance.discover_instruments.return_value = [inst_b]
        mock_hyperliquid.discover_instruments.return_value = [inst_h]

        svc = MultiExchangeService(
            aster_adapter=mock_aster,
            binance_adapter=mock_binance,
            hyperliquid_adapter=mock_hyperliquid,
        )

        res = svc.discover_all_instruments(force_refresh=True)
        assert len(res["asterdex"]) == 1
        assert len(res["binance"]) == 1
        assert len(res["hyperliquid"]) == 1
