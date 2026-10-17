"""Unit tests for Exchange Adapter, Symbol Specifications, and Metadata Filters.
"""

from unittest.mock import MagicMock, patch
import pytest

from src.core.exceptions import ExchangeAPIError, SymbolNotFoundError
from src.core.types import Candle
from src.exchange.aster import AsterDexAdapter
from src.exchange.metadata import SymbolSpecs


SAMPLE_BTC_EXCHANGE_INFO = {
    "symbol": "BTCUSDT",
    "baseAsset": "BTC",
    "quoteAsset": "USDT",
    "contractType": "PERPETUAL",
    "status": "TRADING",
    "pricePrecision": 1,
    "quantityPrecision": 3,
    "maintMarginPercent": "2.5000",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.1", "minPrice": "1", "maxPrice": "1000000"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ],
}

SAMPLE_ADA_EXCHANGE_INFO = {
    "symbol": "ADAUSDT",
    "baseAsset": "ADA",
    "quoteAsset": "USDT",
    "contractType": "PERPETUAL",
    "status": "TRADING",
    "pricePrecision": 4,
    "quantityPrecision": 0,
    "maintMarginPercent": "5.0000",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.0001", "minPrice": "0.0001", "maxPrice": "1000"},
        {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1", "maxQty": "1000000"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ],
}


class TestSymbolSpecs:
    def test_parse_btc_specs(self):
        specs = SymbolSpecs.from_aster_dict(SAMPLE_BTC_EXCHANGE_INFO)
        assert specs.symbol == "BTCUSDT"
        assert specs.tick_size == 0.1
        assert specs.step_size == 0.001
        assert specs.min_notional == 5.0
        assert specs.price_precision == 1
        assert specs.quantity_precision == 3

    def test_price_rounding(self):
        specs = SymbolSpecs.from_aster_dict(SAMPLE_BTC_EXCHANGE_INFO)
        assert specs.round_price(64123.456) == 64123.5
        assert specs.round_price(64123.411) == 64123.4

    def test_quantity_rounding_floors(self):
        # Quantity rounding must floor (ROUND_DOWN) to prevent exceeding allocated risk budget
        specs = SymbolSpecs.from_aster_dict(SAMPLE_BTC_EXCHANGE_INFO)
        assert specs.round_quantity(0.00289) == 0.002
        assert specs.round_quantity(0.00101) == 0.001

        ada_specs = SymbolSpecs.from_aster_dict(SAMPLE_ADA_EXCHANGE_INFO)
        assert ada_specs.round_quantity(15.9) == 15.0

    def test_min_notional_validation(self):
        specs = SymbolSpecs.from_aster_dict(SAMPLE_BTC_EXCHANGE_INFO)
        assert specs.validate_notional(60000.0, 0.001) is True   # $60 >= $5
        assert specs.validate_notional(60000.0, 0.00005) is False # $3 < $5


class TestAsterDexAdapter:
    def test_live_ping(self):
        adapter = AsterDexAdapter()
        assert adapter.ping() is True

    def test_live_exchange_info_watchlist(self):
        adapter = AsterDexAdapter()
        specs_dict = adapter.get_exchange_info()
        watchlist = [
            "ADAUSDT", "XRPUSDT", "BTCUSDT", "BNBUSDT", "ETHUSDT",
            "SOLUSDT", "AVAXUSDT", "LINKUSDT", "XMRUSDT", "DOGEUSDT", "LTCUSDT"
        ]
        for sym in watchlist:
            assert sym in specs_dict, f"Symbol {sym} missing from Aster DEX"
            assert specs_dict[sym].min_notional > 0
            assert specs_dict[sym].step_size > 0

    def test_live_klines_fetch(self):
        adapter = AsterDexAdapter()
        candles = adapter.get_klines("BTCUSDT", "1h", limit=5)
        assert len(candles) == 5
        for c in candles:
            assert isinstance(c, Candle)
            assert c.symbol == "BTCUSDT"
            assert c.timeframe == "1h"
            assert c.low <= c.open <= c.high
            assert c.low <= c.close <= c.high
            assert c.volume >= 0

    def test_symbol_not_found(self):
        adapter = AsterDexAdapter()
        with pytest.raises(SymbolNotFoundError):
            adapter.get_symbol_specs("NONEXISTENT_COIN_USDT")

    def test_mock_rate_limit_retry(self):
        adapter = AsterDexAdapter(max_retries=2, retry_delay_sec=0.01)
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {"Retry-After": "0.01"}

        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        mock_response_200.json.return_value = {}

        with patch.object(adapter.session, "get", side_effect=[mock_response_429, mock_response_200]):
            res = adapter.ping()
            assert res is True

    def test_live_server_time(self):
        adapter = AsterDexAdapter()
        server_time = adapter.get_server_time()
        assert isinstance(server_time, int)
        assert server_time > 1700000000000  # valid epoch ms in 2024+

    def test_sync_server_time(self):
        adapter = AsterDexAdapter()
        offset = adapter.sync_server_time()
        assert isinstance(offset, int)
        assert adapter._time_synced is True

    def test_auth_missing_credentials_raises_error(self):
        adapter = AsterDexAdapter(api_key=None, api_secret=None)
        # Clear any env var overrides for isolation
        adapter.api_key = None
        adapter.api_secret = None

        with pytest.raises(ExchangeAPIError) as exc_info:
            adapter.get_account_balance()
        assert "Aster DEX API key and secret are required" in str(exc_info.value)

        with pytest.raises(ExchangeAPIError):
            adapter.create_order("BTCUSDT", "BUY", "LIMIT", quantity=0.001, price=60000.0)

    def test_hmac_signature_generation(self):
        adapter = AsterDexAdapter(api_key="mock_key", api_secret="mock_secret_key_123")
        query_string = "symbol=BTCUSDT&timestamp=1700000000000&recvWindow=5000"
        sig = adapter._generate_signature(query_string)
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA256 hex length
        # Deterministic check
        import hashlib, hmac
        expected = hmac.new(b"mock_secret_key_123", query_string.encode("utf-8"), hashlib.sha256).hexdigest()
        assert sig == expected

    def test_mock_get_account_balance(self):
        adapter = AsterDexAdapter(api_key="test_key", api_secret="test_secret")
        adapter._time_synced = True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"asset": "USDT", "balance": "16.00000000", "availableBalance": "16.00000000"},
            {"asset": "BNB", "balance": "0.00000000", "availableBalance": "0.00000000"},
        ]

        with patch.object(adapter.session, "get", return_value=mock_resp) as mock_get:
            balances = adapter.get_account_balance()
            assert len(balances) == 2
            assert balances[0]["asset"] == "USDT"
            assert balances[0]["availableBalance"] == "16.00000000"
            mock_get.assert_called_once()
            call_url = mock_get.call_args[0][0]
            assert "/fapi/v2/balance?" in call_url
            assert "signature=" in call_url
            assert mock_get.call_args[1]["headers"]["X-MBX-APIKEY"] == "test_key"

    def test_mock_get_positions(self):
        adapter = AsterDexAdapter(api_key="test_key", api_secret="test_secret")
        adapter._time_synced = True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.000",
                "entryPrice": "0.0",
                "unRealizedProfit": "0.0",
                "leverage": "10",
                "marginType": "isolated",
            }
        ]

        with patch.object(adapter.session, "get", return_value=mock_resp):
            positions = adapter.get_positions(symbol="BTCUSDT")
            assert len(positions) == 1
            assert positions[0]["symbol"] == "BTCUSDT"
            assert positions[0]["marginType"] == "isolated"

    def test_mock_set_leverage(self):
        adapter = AsterDexAdapter(api_key="test_key", api_secret="test_secret")
        adapter._time_synced = True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"symbol": "BTCUSDT", "leverage": 10}

        with patch.object(adapter.session, "post", return_value=mock_resp) as mock_post:
            res = adapter.set_leverage("BTCUSDT", 10)
            assert res["leverage"] == 10
            mock_post.assert_called_once()
            call_url = mock_post.call_args[0][0]
            assert "symbol=BTCUSDT" in call_url
            assert "leverage=10" in call_url

    def test_mock_set_margin_type(self):
        adapter = AsterDexAdapter(api_key="test_key", api_secret="test_secret")
        adapter._time_synced = True

        # Case 1: Success
        mock_resp_ok = MagicMock()
        mock_resp_ok.status_code = 200
        mock_resp_ok.json.return_value = {"code": 200, "msg": "success"}

        with patch.object(adapter.session, "post", return_value=mock_resp_ok):
            res = adapter.set_margin_type("BTCUSDT", "ISOLATED")
            assert res["code"] == 200

        # Case 2: Code -4046 (already isolated) handled gracefully
        mock_resp_already = MagicMock()
        mock_resp_already.status_code = 400
        mock_resp_already.json.return_value = {"code": -4046, "msg": "No need to change margin type."}

        with patch.object(adapter.session, "post", return_value=mock_resp_already):
            res_already = adapter.set_margin_type("BTCUSDT", "ISOLATED")
            assert res_already["code"] == -4046

    def test_mock_create_order_limit(self):
        adapter = AsterDexAdapter(api_key="test_key", api_secret="test_secret")
        adapter._time_synced = True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "orderId": 987654321,
            "symbol": "BTCUSDT",
            "status": "NEW",
            "clientOrderId": "SMC_TEST_001",
            "price": "60000.0",
            "origQty": "0.001",
            "type": "LIMIT",
            "side": "BUY",
        }

        with patch.object(adapter.session, "post", return_value=mock_resp) as mock_post:
            res = adapter.create_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity=0.001,
                price=60000.0,
                time_in_force="GTC",
                client_order_id="SMC_TEST_001",
            )
            assert res["orderId"] == 987654321
            assert res["status"] == "NEW"
            call_url = mock_post.call_args[0][0]
            assert "symbol=BTCUSDT" in call_url
            assert "side=BUY" in call_url
            assert "type=LIMIT" in call_url
            assert "quantity=0.001" in call_url
            assert "price=60000.0" in call_url
            assert "newClientOrderId=SMC_TEST_001" in call_url

    def test_mock_cancel_order(self):
        adapter = AsterDexAdapter(api_key="test_key", api_secret="test_secret")
        adapter._time_synced = True

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "orderId": 987654321,
            "symbol": "BTCUSDT",
            "status": "CANCELED",
        }

        with patch.object(adapter.session, "delete", return_value=mock_resp) as mock_del:
            res = adapter.cancel_order("BTCUSDT", order_id=987654321)
            assert res["status"] == "CANCELED"
            call_url = mock_del.call_args[0][0]
            assert "orderId=987654321" in call_url

