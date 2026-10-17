"""Unit tests for SQLite Database, Candle Validation, and Resumable Downloader.
"""

from pathlib import Path
import tempfile
import time
from unittest.mock import MagicMock
import pytest

from src.core.types import Candle
from src.data.database import DatabaseManager
from src.data.validator import CandleValidator, ValidationReport
from src.data.downloader import HistoricalDownloader


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        manager = DatabaseManager(db_path)
        yield manager


def make_candle(
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    ts_ms: int = 1700000000000,
    o: float = 60000.0,
    h: float = 60500.0,
    l: float = 59800.0,
    c: float = 60200.0,
    v: float = 120.5,
    is_closed: bool = True,
) -> Candle:
    step_ms = 3600 * 1000
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp_ms=ts_ms,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        close_time_ms=ts_ms + step_ms - 1,
        quote_volume=v * c,
        trades_count=500,
        is_closed=is_closed,
    )


class TestDatabaseManager:
    def test_insert_and_deduplicate(self, temp_db: DatabaseManager):
        c1 = make_candle(ts_ms=1700000000000)
        c2 = make_candle(ts_ms=1700003600000)

        # First insert
        count = temp_db.insert_candles([c1, c2])
        assert count == 2
        assert temp_db.get_candle_count("BTCUSDT", "1h") == 2

        # Duplicate insert
        count_dup = temp_db.insert_candles([c1, c2])
        assert count_dup == 0  # No duplicate rows inserted
        assert temp_db.get_candle_count("BTCUSDT", "1h") == 2

    def test_get_candles_range(self, temp_db: DatabaseManager):
        base_ts = 1700000000000
        candles = [make_candle(ts_ms=base_ts + i * 3600000) for i in range(5)]
        temp_db.insert_candles(candles)

        fetched = temp_db.get_candles(
            "BTCUSDT",
            "1h",
            start_time_ms=base_ts + 3600000,
            end_time_ms=base_ts + 3 * 3600000,
        )
        assert len(fetched) == 3
        assert fetched[0].timestamp_ms == base_ts + 3600000
        assert fetched[-1].timestamp_ms == base_ts + 3 * 3600000

    def test_sync_state_tracking(self, temp_db: DatabaseManager):
        c1 = make_candle(ts_ms=1700000000000)
        c2 = make_candle(ts_ms=1700007200000)
        temp_db.insert_candles([c1, c2])

        state = temp_db.get_sync_state("BTCUSDT", "1h")
        assert state is not None
        assert state["earliest_timestamp_ms"] == 1700000000000
        assert state["latest_timestamp_ms"] == 1700007200000
        assert state["total_candles"] == 2

    def test_funding_rates_storage(self, temp_db: DatabaseManager):
        rates = [
            {"fundingTime": 1700000000000, "fundingRate": 0.0001},
            {"fundingTime": 1700028800000, "fundingRate": 0.00015},
        ]
        inserted = temp_db.insert_funding_rates("BTCUSDT", rates)
        assert inserted == 2

        # Duplicate insert
        inserted_dup = temp_db.insert_funding_rates("BTCUSDT", rates)
        assert inserted_dup == 0

        fetched = temp_db.get_funding_rates("BTCUSDT")
        assert len(fetched) == 2
        assert fetched[0]["funding_rate"] == 0.0001


class TestCandleValidator:
    def test_valid_candle(self):
        c = make_candle()
        valid, err = CandleValidator.validate_single_candle(c)
        assert valid is True
        assert err is None

    def test_high_less_than_low(self):
        c = make_candle(h=59000.0, l=60000.0)
        valid, err = CandleValidator.validate_single_candle(c)
        assert valid is False
        assert "High" in err and "Low" in err

    def test_high_less_than_open(self):
        c = make_candle(o=61000.0, h=60500.0, l=59000.0, c=60000.0)
        valid, err = CandleValidator.validate_single_candle(c)
        assert valid is False
        assert "less than max(open, close)" in err

    def test_negative_volume(self):
        c = make_candle(v=-5.0)
        valid, err = CandleValidator.validate_single_candle(c)
        assert valid is False
        assert "volume" in err.lower()

    def test_series_gap_detection(self):
        base_ts = 1700000000000
        # Candle 0, 1, and skip to 4 (a gap of 2 missing candles)
        c0 = make_candle(ts_ms=base_ts)
        c1 = make_candle(ts_ms=base_ts + 3600000)
        c4 = make_candle(ts_ms=base_ts + 4 * 3600000)

        report = CandleValidator.validate_series([c0, c1, c4], expected_timeframe="1h")
        assert len(report.missing_intervals) == 1
        gap_start, gap_end, missing_count = report.missing_intervals[0]
        assert gap_start == base_ts + 3600000
        assert gap_end == base_ts + 4 * 3600000
        assert missing_count == 2

    def test_filter_incomplete_candles(self):
        now_ms = 1700005000000
        # Finished candle
        c_closed = make_candle(ts_ms=1700000000000)
        assert c_closed.close_time_ms < now_ms

        # Incomplete candle (close time in the future)
        c_open = make_candle(ts_ms=1700004000000)
        c_open.close_time_ms = 1700007600000  # after now_ms

        filtered = CandleValidator.filter_closed_candles([c_closed, c_open], now_ms=now_ms)
        assert len(filtered) == 1
        assert filtered[0].timestamp_ms == 1700000000000


class TestHistoricalDownloader:
    def test_downloader_pagination_and_idempotency(self, temp_db: DatabaseManager):
        mock_exchange = MagicMock()
        base_ts = 1700000000000

        # Simulate 2 pages of 2 candles each
        page1 = [
            make_candle(ts_ms=base_ts),
            make_candle(ts_ms=base_ts + 3600000),
        ]
        page2 = [
            make_candle(ts_ms=base_ts + 2 * 3600000),
            make_candle(ts_ms=base_ts + 3 * 3600000),
        ]

        # Call sequence returns page1, page2, then empty list
        mock_exchange.get_klines.side_effect = [page1, page2, []]

        downloader = HistoricalDownloader(
            exchange=mock_exchange,
            db=temp_db,
            batch_size=2,
            request_delay_sec=0.0,
        )

        inserted = downloader._fetch_range(
            symbol="BTCUSDT",
            timeframe="1h",
            start_ms=base_ts,
            end_ms=base_ts + 4 * 3600000,
            step_ms=3600000,
        )
        assert inserted == 4
        assert temp_db.get_candle_count("BTCUSDT", "1h") == 4
