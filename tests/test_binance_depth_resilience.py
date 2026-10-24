from unittest.mock import Mock

from src.exchange.binance_adapter import BinanceFuturesAdapter


def _response(status, payload=None, headers=None):
    r = Mock(status_code=status, headers=headers or {})
    r.json.return_value = payload or {"E": 1, "bids": [["100", "2"]], "asks": [["101", "2"]]}
    return r


def test_binance_418_opens_circuit_and_does_not_retry_immediately():
    session = Mock()
    session.get.return_value = _response(418, {"code": -1003, "msg": "banned"}, {"Retry-After": "30"})
    adapter = BinanceFuturesAdapter(base_url="https://test", session=session)

    assert adapter.get_order_book("BTCUSDT") is None
    assert adapter.last_depth_status["status"] == "RATE_LIMITED"
    assert session.get.call_count == 1
    assert adapter.get_order_book("BTCUSDT") is None
    assert adapter.last_depth_status["status"] == "CIRCUIT_OPEN"
    assert session.get.call_count == 1


def test_binance_depth_snapshot_is_cached():
    session = Mock()
    session.get.return_value = _response(200)
    adapter = BinanceFuturesAdapter(base_url="https://test", session=session)

    first = adapter.get_order_book("BTCUSDT")
    second = adapter.get_order_book("BTCUSDT")
    assert first is second
    assert session.get.call_count == 1
    assert adapter.last_depth_status["source"] == "cache"
