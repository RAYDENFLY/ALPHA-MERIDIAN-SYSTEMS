import threading
import time
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest
import requests

from src.exchange.binance_adapter import BinanceFuturesAdapter
from src.exchange.cross_exchange_liquidity import CrossExchangeLiquidityAggregator
from src.exchange.normalized import NormalizedOrderBook, OrderBookLevel
from src.intelligence.liquidity_chart_service import LiquidityChartService


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        json_data: Optional[Dict[str, Any]] = None,
        text: str = "",
        headers: Optional[Dict[str, str]] = None,
    ):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError("Invalid JSON body")
        return self._json_data


@pytest.fixture(autouse=True)
def reset_binance_shared_state():
    """Ensure clean shared circuit-breaker and cache state before and after each test."""
    BinanceFuturesAdapter.reset_shared_state()
    yield
    BinanceFuturesAdapter.reset_shared_state()


class TestBinanceDepthErrorClassificationAndResilience:
    """Comprehensive test suite for Binance USD(S)-M Futures /fapi/v1/depth handling."""

    def test_1_valid_depth_response_parses_and_caches_across_instances(self):
        """1. Valid HTTP 200 depth response uses GET /fapi/v1/depth, sorts bids/asks, and caches across instances."""
        session = MagicMock()
        session.get.return_value = FakeResponse(
            status_code=200,
            json_data={
                "lastUpdateId": 99887766,
                "E": 1792831000000,
                "T": 1792830999995,
                "bids": [["81200.00", "1.250"], ["81205.50", "2.500"]],
                "asks": [["81210.00", "0.900"], ["81206.00", "1.800"]],
            },
        )

        adapter1 = BinanceFuturesAdapter(session=session, depth_ttl_sec=10.0)
        adapter2 = BinanceFuturesAdapter(session=session, depth_ttl_sec=10.0)

        book = adapter1.get_order_book("BTCUSDT", limit=50)
        assert book is not None
        assert book.symbol == "BTCUSDT"
        assert book.exchange_name == "binance"
        # Bids sorted descending, asks ascending
        assert book.best_bid == 81205.50
        assert book.best_ask == 81206.00

        # Confirm exact endpoint GET https://fapi.binance.com/fapi/v1/depth
        session.get.assert_called_once_with(
            "https://fapi.binance.com/fapi/v1/depth",
            params={"symbol": "BTCUSDT", "limit": 50},
            timeout=8.0,
        )

        st1 = adapter1.get_symbol_depth_status("BTCUSDT")
        assert st1["status"] == "LIVE"
        assert st1["source"] == "network"
        assert st1["http_status"] == 200

        # Second adapter instance hits shared cache without making a duplicate network call
        book2 = adapter2.get_order_book("BTCUSDT", limit=50)
        assert book2 is book
        assert session.get.call_count == 1
        st2 = adapter2.get_symbol_depth_status("BTCUSDT")
        assert st2["status"] == "LIVE"
        assert st2["source"] == "cache"

    def test_2_http_418_ip_ban_parses_banned_until_and_never_retries_aggressively(self):
        """2. HTTP 418 IP auto-ban is classified as IP_BANNED, honors banned-until timestamp, and blocks all instances."""
        future_ban_ms = int((time.time() + 1259.0) * 1000)
        session = MagicMock()
        session.get.return_value = FakeResponse(
            status_code=418,
            headers={"Retry-After": "1259", "x-mbx-used-weight-1m": "198"},
            json_data={
                "code": -1003,
                "msg": f"Way too many requests; IP(104.28.215.132) banned until {future_ban_ms}. Please use the websocket for live updates to avoid bans.",
            },
        )

        adapter1 = BinanceFuturesAdapter(session=session)
        adapter2 = BinanceFuturesAdapter(session=session)

        book = adapter1.get_order_book("BTCUSDT", limit=50)
        assert book is None
        # Must NOT retry 418
        assert session.get.call_count == 1

        st = adapter1.get_symbol_depth_status("BTCUSDT")
        assert st["status"] == "IP_BANNED"
        assert st["error_code"] == "IP_BANNED"
        assert st["http_status"] == 418
        assert st["binance_error_code"] == -1003
        assert st["banned_until_ms"] == future_ban_ms
        assert st["retry_after_seconds"] >= 1200.0
        assert "HTTP 418 IP Auto-Ban" in st["message"]
        assert "contract not listed or feed offline" not in st["message"]

        # Subsequent call on another symbol or another adapter instance is immediately blocked by circuit breaker (zero HTTP calls)
        book_eth = adapter2.get_order_book("ETHUSDT", limit=50)
        assert book_eth is None
        assert session.get.call_count == 1
        st_eth = adapter2.get_symbol_depth_status("ETHUSDT")
        assert st_eth["error_code"] == "IP_BANNED"

    def test_3_http_429_rate_limit_classified_and_backed_off(self):
        """3. HTTP 429 rate limit is classified as RATE_LIMITED with Retry-After backoff."""
        session = MagicMock()
        session.get.return_value = FakeResponse(
            status_code=429,
            headers={"Retry-After": "45"},
            json_data={"code": -1003, "msg": "Too many requests; current limit is 2400 requests per minute."},
        )

        adapter = BinanceFuturesAdapter(session=session)
        book = adapter.get_order_book("BTCUSDT", limit=50)
        assert book is None
        assert session.get.call_count == 1

        st = adapter.get_symbol_depth_status("BTCUSDT")
        assert st["status"] == "RATE_LIMITED"
        assert st["error_code"] == "RATE_LIMITED"
        assert st["http_status"] == 429
        assert st["retry_after_seconds"] >= 45.0
        assert "HTTP 429 Rate Limit" in st["message"]
        assert "contract not listed or feed offline" not in st["message"]

    def test_4_invalid_symbol_http_400_classified_without_tripping_global_breaker(self):
        """4. Invalid symbol (HTTP 400 code -1121) is classified as INVALID_SYMBOL and does not block valid symbols."""
        session = MagicMock()
        session.get.side_effect = [
            FakeResponse(
                status_code=400,
                json_data={"code": -1121, "msg": "Invalid symbol."},
            ),
            FakeResponse(
                status_code=200,
                json_data={
                    "lastUpdateId": 100,
                    "E": int(time.time() * 1000),
                    "bids": [["81000.0", "1.0"]],
                    "asks": [["81001.0", "1.0"]],
                },
            ),
        ]

        adapter = BinanceFuturesAdapter(session=session)
        bad_book = adapter.get_order_book("NOTAREALCOINUSDT", limit=50)
        assert bad_book is None

        st_bad = adapter.get_symbol_depth_status("NOTAREALCOINUSDT")
        assert st_bad["status"] == "INVALID_SYMBOL"
        assert st_bad["error_code"] == "INVALID_SYMBOL"
        assert st_bad["binance_error_code"] == -1121
        assert "invalid symbol" in st_bad["message"].lower()

        # Global breaker must NOT be tripped: fetching BTCUSDT immediately succeeds
        good_book = adapter.get_order_book("BTCUSDT", limit=50)
        assert good_book is not None
        assert good_book.best_bid == 81000.0

    def test_5_network_timeout_and_malformed_response_distinguished(self):
        """5. Network timeout and malformed/empty responses are distinctly classified."""
        session = MagicMock()
        session.get.side_effect = [
            requests.Timeout("Read timed out (read timeout=8.0)"),
            FakeResponse(status_code=200, text="<html>502 Bad Gateway</html>", json_data=None),
            FakeResponse(status_code=200, json_data={"bids": [], "asks": []}),
        ]

        adapter = BinanceFuturesAdapter(session=session)

        # Timeout
        assert adapter.get_order_book("BTCUSDT") is None
        st_to = adapter.get_symbol_depth_status("BTCUSDT")
        assert st_to["status"] == "TIMEOUT"
        assert st_to["error_code"] == "TIMEOUT"
        assert "timeout" in st_to["message"].lower()

        # Malformed JSON
        assert adapter.get_order_book("ETHUSDT") is None
        st_mal = adapter.get_symbol_depth_status("ETHUSDT")
        assert st_mal["status"] == "MALFORMED_RESPONSE"
        assert st_mal["error_code"] == "MALFORMED_RESPONSE"

        # Empty book
        assert adapter.get_order_book("SOLUSDT") is None
        st_emp = adapter.get_symbol_depth_status("SOLUSDT")
        assert st_emp["status"] == "EMPTY_ORDER_BOOK"
        assert st_emp["error_code"] == "EMPTY_ORDER_BOOK"

    def test_6_stale_cached_data_served_when_http_418_or_429_hits_after_prior_success(self):
        """6. When a valid order book is cached and later expired, an HTTP 418/429 returns stale cached book with STALE status."""
        now_ms = int(time.time() * 1000)
        session = MagicMock()
        session.get.side_effect = [
            FakeResponse(
                status_code=200,
                json_data={
                    "lastUpdateId": 555,
                    "E": now_ms - 10000,
                    "bids": [["81500.0", "3.0"]],
                    "asks": [["81502.0", "2.0"]],
                },
            ),
            FakeResponse(
                status_code=418,
                headers={"Retry-After": "600"},
                json_data={"code": -1003, "msg": "Way too many requests; IP banned."},
            ),
        ]

        adapter = BinanceFuturesAdapter(session=session, depth_ttl_sec=0.01, stale_fallback_max_age_sec=900.0)
        first_book = adapter.get_order_book("BTCUSDT")
        assert first_book is not None
        assert first_book.best_bid == 81500.0

        # Expire fresh TTL
        time.sleep(0.03)

        # Next call receives HTTP 418 -> falls back to stale cached book and marks status STALE + IP_BANNED
        fallback_book = adapter.get_order_book("BTCUSDT")
        assert fallback_book is not None
        assert fallback_book.best_bid == 81500.0

        st = adapter.get_symbol_depth_status("BTCUSDT")
        assert st["status"] == "STALE"
        assert st["error_code"] == "IP_BANNED"
        assert st["is_stale"] is True
        assert st["source"] == "stale_cache"
        assert st["http_status"] == 418

    def test_7_liquidity_chart_service_propagates_exact_binance_error_instead_of_generic_message(self):
        """7. LiquidityChartService surfaces exact HTTP 418 / 429 / INVALID_SYMBOL messages and deduplicates heatmap + overlay calls."""
        session = MagicMock()
        session.get.return_value = FakeResponse(
            status_code=418,
            headers={"Retry-After": "900"},
            json_data={
                "code": -1003,
                "msg": "Way too many requests; IP(104.28.215.132) banned until 1792832038322.",
            },
        )
        binance_ad = BinanceFuturesAdapter(session=session)
        cross_agg = CrossExchangeLiquidityAggregator(binance_adapter=binance_ad)
        chart_svc = LiquidityChartService(cross_aggregator=cross_agg, binance_adapter=binance_ad)

        overlay = chart_svc._build_order_book_overlay("BTCUSDT", venue_source="binance")
        assert overlay["status"] == "IP_BANNED"
        assert overlay["error_code"] == "IP_BANNED"
        assert "HTTP 418 IP Auto-Ban" in overlay["error_message"]
        assert "contract not listed or feed offline" not in overlay["error_message"]
        # Heatmap + overlay builder must not make duplicate calls
        assert session.get.call_count == 1

    def test_8_single_flight_deduplication_under_concurrent_threads(self):
        """8. Concurrent threads requesting BTCUSDT depth simultaneously execute only 1 upstream HTTP request."""
        call_count = 0
        lock = threading.Lock()

        def slow_get(*args, **kwargs):
            nonlocal call_count
            with lock:
                call_count += 1
            time.sleep(0.08)
            return FakeResponse(
                status_code=200,
                json_data={
                    "lastUpdateId": 777,
                    "E": int(time.time() * 1000),
                    "bids": [["82000.0", "1.5"]],
                    "asks": [["82001.0", "1.5"]],
                },
            )

        session = MagicMock()
        session.get.side_effect = slow_get
        adapter = BinanceFuturesAdapter(session=session, depth_ttl_sec=10.0)

        results = [None] * 6
        threads = [
            threading.Thread(target=lambda idx=i: results.__setitem__(idx, adapter.get_order_book("BTCUSDT")))
            for i in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count == 1
        for r in results:
            assert r is not None
            assert r.best_bid == 82000.0
