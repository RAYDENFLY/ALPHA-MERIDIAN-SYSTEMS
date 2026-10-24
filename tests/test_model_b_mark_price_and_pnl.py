"""Comprehensive regression test suite for Model B mark price, linear perpetual UPnL/ROE calculations,
missing/stale price handling, quantity unit handling, API serialization, and scanner telemetry lifecycle.
"""

from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock, patch

from src.core.types import Candle, CandidateStatus, Direction, ExecutionStatus, SetupCandidate
from src.data.database import DatabaseManager
from src.execution.types import PaperOrder, PaperOrderStatus, PaperPosition
from src.journal.repository import JournalRepository
from src.scheduler.model_b_runner import ModelBRunner
from src.scheduler.scanner_telemetry import ScannerTelemetryTracker


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_signals.db"
    db = DatabaseManager(str(db_file))
    db.init_db()
    return db


@pytest.fixture
def test_journal(test_db):
    return JournalRepository(test_db)


@pytest.fixture
def model_b_runner(test_db, test_journal):
    runner = ModelBRunner(db=test_db, journal=test_journal)
    return runner


class TestModelBMarkPriceAndPnL:
    """Test suite covering 10 distinct regression requirements."""

    # 1. LONG PnL when mark price increases
    def test_long_pnl_when_mark_price_increases(self, model_b_runner):
        pos = PaperPosition(
            trade_id="TRD_LONG_1",
            order_id="ORD_LONG_1",
            setup_id="SET_LONG_1",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1000000,
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=120.0,
            quantity=2.0,
            notional_usdt=200.0,
            margin_usdt=20.0,
            leverage=10.0,
            entry_fee=0.10,
        )
        model_b_runner.paper_engine.open_positions["BTCUSDT"] = pos

        # Mark price increases to 110.0
        # Expected:
        # gross_upnl = 2.0 * (110.0 - 100.0) = +20.0 USDT
        # est_exit_fee = (2.0 * 110.0) * 0.0005 = 0.11 USDT
        # net_upnl = 20.0 - 0.10 (entry) - 0.11 (exit) = 19.79 USDT
        # roe_pct = 19.79 / 20.0 * 100 = 98.95%
        with patch.object(model_b_runner, "get_mark_price", return_value=(110.0, 1005000, "FRESH")):
            enriched = model_b_runner.get_enriched_open_positions()

        assert len(enriched) == 1
        p = enriched[0]
        assert p["side"] == "LONG"
        assert p["mark_price"] == 110.0
        assert p["unrealized_pnl_gross"] == 20.0
        assert p["unrealized_pnl_usdt"] == 19.79
        assert p["roe_pct"] == 98.95
        assert p["mark_price_status"] == "FRESH"

    # 2. LONG PnL when mark price decreases
    def test_long_pnl_when_mark_price_decreases(self, model_b_runner):
        pos = PaperPosition(
            trade_id="TRD_LONG_2",
            order_id="ORD_LONG_2",
            setup_id="SET_LONG_2",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1000000,
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=120.0,
            quantity=2.0,
            notional_usdt=200.0,
            margin_usdt=20.0,
            leverage=10.0,
            entry_fee=0.10,
        )
        model_b_runner.paper_engine.open_positions["BTCUSDT"] = pos

        # Mark price decreases to 95.0
        # gross_upnl = 2.0 * (95.0 - 100.0) = -10.0 USDT
        # est_exit_fee = (2.0 * 95.0) * 0.0005 = 0.095 USDT
        # net_upnl = -10.0 - 0.10 - 0.095 = -10.195 USDT -> round 4: -10.195
        # roe_pct = -10.195 / 20.0 * 100 = -50.98%
        with patch.object(model_b_runner, "get_mark_price", return_value=(95.0, 1005000, "FRESH")):
            enriched = model_b_runner.get_enriched_open_positions()

        p = enriched[0]
        assert p["side"] == "LONG"
        assert p["mark_price"] == 95.0
        assert p["unrealized_pnl_gross"] == -10.0
        assert p["unrealized_pnl_usdt"] == -10.195
        assert p["roe_pct"] == -50.98

    # 3. SHORT PnL when mark price increases (loss for short)
    def test_short_pnl_when_mark_price_increases(self, model_b_runner):
        pos = PaperPosition(
            trade_id="TRD_SHORT_1",
            order_id="ORD_SHORT_1",
            setup_id="SET_SHORT_1",
            symbol="IRYSUSDT",
            direction=Direction.SHORT,
            entry_timestamp_ms=1000000,
            entry_price=0.0200,
            stop_loss=0.0250,
            take_profit=0.0150,
            quantity=1000.0,
            notional_usdt=20.0,
            margin_usdt=2.0,
            leverage=10.0,
            entry_fee=0.01,
        )
        model_b_runner.paper_engine.open_positions["IRYSUSDT"] = pos

        # Price rises to 0.0300 (adverse move for SHORT)
        # gross_upnl = 1000.0 * (0.0200 - 0.0300) = -10.0 USDT
        # est_exit_fee = (1000.0 * 0.0300) * 0.0005 = 0.015 USDT
        # net_upnl = -10.0 - 0.01 - 0.015 = -10.025 USDT
        # roe_pct = -10.025 / 2.0 * 100 = -501.25%
        with patch.object(model_b_runner, "get_mark_price", return_value=(0.0300, 1005000, "FRESH")):
            enriched = model_b_runner.get_enriched_open_positions()

        p = enriched[0]
        assert p["side"] == "SHORT"
        assert p["mark_price"] == 0.0300
        assert p["unrealized_pnl_gross"] == -10.0
        assert p["unrealized_pnl_usdt"] == -10.025
        assert p["roe_pct"] == -501.25

    # 4. SHORT PnL when mark price decreases (gain for short)
    def test_short_pnl_when_mark_price_decreases(self, model_b_runner):
        pos = PaperPosition(
            trade_id="TRD_SHORT_2",
            order_id="ORD_SHORT_2",
            setup_id="SET_SHORT_2",
            symbol="IRYSUSDT",
            direction=Direction.SHORT,
            entry_timestamp_ms=1000000,
            entry_price=0.0200,
            stop_loss=0.0250,
            take_profit=0.0150,
            quantity=1000.0,
            notional_usdt=20.0,
            margin_usdt=2.0,
            leverage=10.0,
            entry_fee=0.01,
        )
        model_b_runner.paper_engine.open_positions["IRYSUSDT"] = pos

        # Price drops to 0.0150 (favorable move for SHORT)
        # gross_upnl = 1000.0 * (0.0200 - 0.0150) = +5.0 USDT
        # est_exit_fee = (1000.0 * 0.0150) * 0.0005 = 0.0075 USDT
        # net_upnl = 5.0 - 0.01 - 0.0075 = 4.9825 USDT
        # roe_pct = 4.9825 / 2.0 * 100 = 249.125% -> rounded to 2: 249.12
        with patch.object(model_b_runner, "get_mark_price", return_value=(0.0150, 1005000, "FRESH")):
            enriched = model_b_runner.get_enriched_open_positions()

        p = enriched[0]
        assert p["side"] == "SHORT"
        assert p["mark_price"] == 0.0150
        assert p["unrealized_pnl_gross"] == 5.0
        assert p["unrealized_pnl_usdt"] == 4.9825
        assert p["roe_pct"] == 249.13

    # 5. Missing mark price handling
    def test_missing_mark_price_handling(self, model_b_runner):
        pos = PaperPosition(
            trade_id="TRD_MISSING",
            order_id="ORD_MISSING",
            setup_id="SET_MISSING",
            symbol="UNKNOWNUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1000000,
            entry_price=50.0,
            stop_loss=45.0,
            take_profit=60.0,
            quantity=1.0,
            notional_usdt=50.0,
            margin_usdt=5.0,
            leverage=10.0,
            entry_fee=0.025,
        )
        model_b_runner.paper_engine.open_positions["UNKNOWNUSDT"] = pos

        with patch.object(model_b_runner, "get_mark_price", return_value=(None, None, "MISSING")):
            enriched = model_b_runner.get_enriched_open_positions()

        p = enriched[0]
        # Never silently substitute entry price!
        assert p["mark_price"] is None
        assert p["mark_price_status"] == "MISSING"
        assert p["mark_price_timestamp_ms"] is None
        assert p["unrealized_pnl_gross"] is None
        assert p["unrealized_pnl_usdt"] is None
        assert p["roe_pct"] is None

    # 6. Stale mark price handling
    def test_stale_mark_price_handling(self, model_b_runner):
        pos = PaperPosition(
            trade_id="TRD_STALE",
            order_id="ORD_STALE",
            setup_id="SET_STALE",
            symbol="STALEUSDT",
            direction=Direction.SHORT,
            entry_timestamp_ms=1000000,
            entry_price=10.0,
            stop_loss=12.0,
            take_profit=8.0,
            quantity=5.0,
            notional_usdt=50.0,
            margin_usdt=5.0,
            leverage=10.0,
            entry_fee=0.025,
        )
        model_b_runner.paper_engine.open_positions["STALEUSDT"] = pos

        stale_ts = 1000000
        with patch.object(model_b_runner, "get_mark_price", return_value=(9.0, stale_ts, "STALE")):
            enriched = model_b_runner.get_enriched_open_positions()

        p = enriched[0]
        assert p["mark_price"] == 9.0
        assert p["mark_price_status"] == "STALE"
        assert p["mark_price_timestamp_ms"] == stale_ts
        assert p["unrealized_pnl_usdt"] is not None

    # 7. Quantity and contract unit handling
    def test_quantity_and_contract_unit_handling(self, model_b_runner):
        # Fractional coin units (e.g. 0.05 ETH)
        pos = PaperPosition(
            trade_id="TRD_ETH",
            order_id="ORD_ETH",
            setup_id="SET_ETH",
            symbol="ETHUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1000000,
            entry_price=2000.0,
            stop_loss=1900.0,
            take_profit=2200.0,
            quantity=0.05,
            notional_usdt=100.0,
            margin_usdt=10.0,
            leverage=10.0,
            entry_fee=0.05,
        )
        model_b_runner.paper_engine.open_positions["ETHUSDT"] = pos

        with patch.object(model_b_runner, "get_mark_price", return_value=(2100.0, 1005000, "FRESH")):
            enriched = model_b_runner.get_enriched_open_positions()

        p = enriched[0]
        # gross_upnl = 0.05 * (2100 - 2000) = +5.0 USDT
        assert p["unrealized_pnl_gross"] == 5.0
        assert p["position_amt"] == 0.05

    # 8. ROE based on allocated margin
    def test_roe_based_on_allocated_margin(self, model_b_runner):
        pos = PaperPosition(
            trade_id="TRD_ROE",
            order_id="ORD_ROE",
            setup_id="SET_ROE",
            symbol="SOLUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1000000,
            entry_price=150.0,
            stop_loss=140.0,
            take_profit=170.0,
            quantity=1.0,
            notional_usdt=150.0,
            margin_usdt=15.0,  # 10x margin
            leverage=10.0,
            entry_fee=0.0,
        )
        model_b_runner.paper_engine.open_positions["SOLUSDT"] = pos

        with patch.object(model_b_runner, "get_mark_price", return_value=(165.0, 1005000, "FRESH")):
            enriched = model_b_runner.get_enriched_open_positions()

        p = enriched[0]
        # gross_upnl = 15.0
        # est_exit_fee = 165.0 * 0.0005 = 0.0825
        # net_upnl = 14.9175
        # roe_pct = 14.9175 / 15.0 * 100 = 99.45%
        assert p["allocated_margin_usdt"] == 15.0
        assert p["roe_pct"] == 99.45

    # 9. API serialization and dashboard field mapping
    def test_api_serialization_and_field_mapping(self, model_b_runner):
        pos = PaperPosition(
            trade_id="PTRD_IRYSUSDT_1792645200000",
            order_id="PORD_IRYSUSDT_1792645200000",
            setup_id="MCAND_B_IRYSUSDT_SHORT_1792645200000",
            symbol="IRYSUSDT",
            direction=Direction.SHORT,
            entry_timestamp_ms=1792648800000,
            entry_price=0.0212,
            stop_loss=0.0227,
            take_profit=0.0189,
            quantity=665.0,
            notional_usdt=14.098,
            margin_usdt=1.4098,
            leverage=10.0,
            entry_fee=0.0028,
        )
        model_b_runner.paper_engine.open_positions["IRYSUSDT"] = pos

        with patch.object(model_b_runner, "get_mark_price", return_value=(0.04382, 1792764000000, "FRESH")):
            enriched = model_b_runner.get_enriched_open_positions()

        p = enriched[0]
        # Check all exchange-style fields required by frontend model-b.js
        required_keys = [
            "trade_id", "order_id", "symbol", "side", "direction",
            "position_amt", "notional_usdt", "entry_price", "mark_price",
            "mark_price_timestamp_ms", "mark_price_time_utc", "mark_price_status",
            "allocated_margin_usdt", "allocated_margin_label",
            "take_profit_price", "take_profit_label",
            "stop_loss_price", "stop_loss_label",
            "unrealized_pnl_gross", "unrealized_pnl_usdt", "unrealized_pnl_net_usdt",
            "roe_pct", "roi_pct", "entry_time_utc"
        ]
        for key in required_keys:
            assert key in p, f"Missing key: {key}"

        # Real price movement must yield negative UPnL for SHORT
        assert p["mark_price"] == 0.04382
        assert p["unrealized_pnl_usdt"] < -10.0
        assert p["roe_pct"] < -500.0

    # 10. Scanner telemetry and rotation lifecycle
    def test_scanner_telemetry_and_rotation_lifecycle(self, model_b_runner):
        tracker = ScannerTelemetryTracker()
        cycle = tracker.start_cycle("CYCLE_TEST_01")
        cycle.symbols_discovered_total = 100
        cycle.symbols_screened = 80
        cycle.symbols_deep_evaluated = 25
        cycle.rotation_offset = 25
        cycle.rotation_pool_size = 125
        cycle.rotation_progress_pct = 20.0
        cycle.request_latencies_ms = [40.0, 50.0, 60.0]

        snapshot = tracker.finish_cycle()
        data = tracker.get_latest()

        assert data is not None
        assert data["cycle_id"] == "CYCLE_TEST_01"
        assert data["coverage"]["screened"] == 80
        assert data["coverage"]["deep_evaluated"] == 25
        assert data["rotation"]["offset"] == 25
        assert data["rotation"]["pool_size"] == 125
        assert data["rotation"]["progress_pct"] == 20.0
        assert data["requests"]["latency_stats"]["p95_ms"] >= 50.0
