from src.core.types import Candle
from src.model_d.candles import validate_closed_candles


BASE = 1_700_000_000_000


def make_candle(i, **kwargs):
    values = dict(symbol="BTCUSDT", timeframe="1h", timestamp_ms=BASE + i * 3_600_000,
                  open=100.0, high=102.0, low=99.0, close=101.0, volume=10.0,
                  close_time_ms=BASE + i * 3_600_000 + 3_599_999, is_closed=True)
    values.update(kwargs)
    return Candle(**values)


def test_closed_candle_history_is_accepted():
    candles, status = validate_closed_candles([make_candle(i) for i in range(8)], now_ms=BASE + 100_000_000)
    assert status == "OK"
    assert len(candles) == 8


def test_incomplete_candle_is_not_used():
    candles, status = validate_closed_candles([make_candle(i, is_closed=(i < 7)) for i in range(8)], now_ms=BASE + 100_000_000)
    assert candles == []
    assert status == "INSUFFICIENT_CLOSED_HISTORY"


def test_gap_duplicate_and_order_are_rejected():
    assert validate_closed_candles([make_candle(i) for i in range(7)] + [make_candle(9)], now_ms=BASE + 100_000_000)[1] == "CANDLE_GAP"
    assert validate_closed_candles([make_candle(i) for i in range(7)] + [make_candle(6)], now_ms=BASE + 100_000_000)[1] == "DUPLICATE_TIMESTAMP"
    assert validate_closed_candles([make_candle(i) for i in [0, 1, 3, 2, 4, 5, 6, 7]], now_ms=BASE + 100_000_000)[1] == "OUT_OF_ORDER"


def test_invalid_ohlcv_and_insufficient_history_are_explicit():
    assert validate_closed_candles([make_candle(i) for i in range(7)], now_ms=BASE + 100_000_000)[1] == "INSUFFICIENT_HISTORY"
    invalid = [make_candle(i) for i in range(8)]
    invalid[3].low = 103.0
    assert validate_closed_candles(invalid, now_ms=BASE + 100_000_000)[1] == "INVALID_OHLCV"
