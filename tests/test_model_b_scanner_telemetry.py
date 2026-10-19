"""Unit and Acceptance Tests for Model B Scanner Performance Telemetry.

Verifies Phase 1 instrumentation, metric aggregations, coverage accounting,
and API endpoint consistency.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.exchange.normalized import NormalizedInstrument
from src.scheduler.scanner_telemetry import ScanCycleTelemetry, ScannerTelemetryTracker
from src.scheduler.model_b_runner import ModelBRunner
from src.data.database import DatabaseManager
from src.journal.repository import JournalRepository
from config.settings import ModelBConfig, get_default_config


def test_scanner_telemetry_aggregations():
    """Verify statistics computation (coverage %, median, p95 latencies)."""
    tel = ScanCycleTelemetry(
        cycle_id="TEST_01",
        start_time_utc="2026-10-19T00:00:00Z",
        symbols_discovered_asterdex=578,
        symbols_evaluated=20,
        request_latencies_ms=[100.0, 105.0, 110.0, 120.0, 500.0],
        cache_hits=5,
        cache_misses=15,
    )
    tel.compute_aggregates()

    assert tel.coverage_pct == pytest.approx(3.46, abs=0.01)
    assert tel.cache_hit_rate_pct == 25.0
    assert tel.latency_stats["count"] == 5
    assert tel.latency_stats["min_ms"] == 100.0
    assert tel.latency_stats["max_ms"] == 500.0
    assert tel.latency_stats["median_ms"] == 110.0
    assert tel.latency_stats["p95_ms"] >= 120.0


def test_scanner_telemetry_tracker_lifecycle():
    """Verify cycle start, request recording, finish, and history tracking."""
    tracker = ScannerTelemetryTracker(max_history=5)
    cycle = tracker.start_cycle("CYCLE_TEST")

    tracker.record_request("asterdex:/fapi/v1/klines", latency_ms=120.0)
    tracker.record_request("asterdex:/fapi/v1/klines", latency_ms=130.0)
    tracker.record_request("asterdex:/fapi/v1/depth", latency_ms=80.0, is_cached=True)

    finished = tracker.finish_cycle()
    assert finished.cycle_id == "CYCLE_TEST"
    assert finished.request_count_total == 2
    assert finished.cache_hits == 1
    assert finished.cache_misses == 2

    latest = tracker.get_latest()
    assert latest is not None
    assert latest["cycle_id"] == "CYCLE_TEST"

    hist = tracker.get_history(limit=5)
    assert len(hist) == 1
    assert hist[0]["cycle_id"] == "CYCLE_TEST"


def test_model_b_scanner_telemetry_api(tmp_path):
    """Verify /api/model_b/scanner_telemetry endpoint consistency."""
    client = TestClient(app)
    resp = client.get("/api/model_b/scanner_telemetry")
    assert resp.status_code == 200
    data = resp.json()
    assert "latest" in data
    assert "history" in data


def test_model_a_invariants_preserved():
    """Verify Model A remains 100% frozen during scanner telemetry inspection."""
    cfg = get_default_config()
    assert len(cfg.enabled_symbols) == 11
    assert cfg.risk.margin_mode == "ISOLATED"
    assert cfg.live_trading_locked is True


def test_bulk_screening_and_fair_rotation_mechanics(tmp_path):
    """Phase 3 & 4: Verify Stage 1 bulk screening, anchor retention, and fair rotation."""
    db = DatabaseManager(tmp_path / "test.db")
    journal = JournalRepository(db)
    config = ModelBConfig(
        stage1_min_24h_volume_usdt=50000.0,
        stage1_max_spread_bps=35.0,
        anchor_symbol_count=5,
        rotation_slice_size=10,
    )
    runner = ModelBRunner(db=db, journal=journal, config=config)

    # 1. Create 40 synthetic instruments
    instruments = []
    bulk_snapshot = {}
    for i in range(40):
        sym = f"SYM{i:02d}USDT"
        vol = 100000.0 * (40 - i)  # SYM00 has $4M volume, SYM39 has $100k
        spread = 5.0 + (i * 0.5)   # spread 5.0 bps to 24.5 bps (all <= 35)
        inst = NormalizedInstrument(
            symbol=sym,
            exchange_name="asterdex",
            base_asset=f"SYM{i:02d}",
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
            min_notional=5.0,
            contract_type="PERPETUAL",
            volume_24h_usdt=vol,
            volume_status="AVAILABLE",
        )
        instruments.append(inst)
        bulk_snapshot[sym] = {
            "symbol": sym,
            "volume_24h_usdt": vol,
            "spread_bps": spread,
            "bid_price": 10.0,
            "ask_price": 10.005,
            "last_price": 10.0,
        }

    # Add 1 illiquid instrument that should be filtered out by Stage 1
    bad_sym = "DEADCOINUSDT"
    instruments.append(
        NormalizedInstrument(
            symbol=bad_sym,
            exchange_name="asterdex",
            base_asset="DEADCOIN",
            quote_asset="USDT",
            min_qty=1.0,
            step_size=1.0,
            tick_size=0.001,
            min_notional=5.0,
            contract_type="PERPETUAL",
            volume_24h_usdt=100.0,
            volume_status="AVAILABLE",
        )
    )
    bulk_snapshot[bad_sym] = {
        "symbol": bad_sym,
        "volume_24h_usdt": 100.0, # Below $50k
        "spread_bps": 150.0,      # Above 35 bps
        "bid_price": 0.01,
        "ask_price": 0.02,
        "last_price": 0.015,
    }

    # Mock exchange bulk snapshot
    mock_ex = type("MockEx", (), {"get_bulk_market_snapshot": lambda self=None: bulk_snapshot})()
    runner.exchange = mock_ex

    # --- Cycle 1 ---
    cycle1 = runner.telemetry_tracker.start_cycle("CYCLE_1")
    targets_1, bulk_count_1, qual_count_1 = runner.screen_and_rank_symbols(instruments)
    runner.telemetry_tracker.finish_cycle()

    assert bulk_count_1 == 41
    assert qual_count_1 == 40
    assert bad_sym not in [t.symbol for t in targets_1]

    # Anchors: Top 5 symbols (SYM00 to SYM04)
    anchors_1 = [t.symbol for t in targets_1[:5]]
    assert anchors_1 == ["SYM00USDT", "SYM01USDT", "SYM02USDT", "SYM03USDT", "SYM04USDT"]

    # Rotating slice: Next 10 symbols (SYM05 to SYM14)
    slice_1 = [t.symbol for t in targets_1[5:15]]
    assert slice_1 == [f"SYM{i:02d}USDT" for i in range(5, 15)]
    assert runner._rotation_offset == 10
    assert runner._rotation_pool_size == 35  # 40 - 5 anchors = 35

    # --- Cycle 2 ---
    cycle2 = runner.telemetry_tracker.start_cycle("CYCLE_2")
    targets_2, bulk_count_2, qual_count_2 = runner.screen_and_rank_symbols(instruments)
    runner.telemetry_tracker.finish_cycle()

    # Anchors in Cycle 2 must still be top 5
    anchors_2 = [t.symbol for t in targets_2[:5]]
    assert anchors_2 == anchors_1

    # Rotating slice in Cycle 2 must advance to (SYM15 to SYM24)
    slice_2 = [t.symbol for t in targets_2[5:15]]
    assert slice_2 == [f"SYM{i:02d}USDT" for i in range(15, 25)]
    assert runner._rotation_offset == 20
    assert slice_1 != slice_2


def test_active_monitoring_symbol_is_prioritized(tmp_path):
    """Verify active positions and pending orders are given top evaluation priority."""
    db = DatabaseManager(tmp_path / "test.db")
    journal = JournalRepository(db)
    config = ModelBConfig(anchor_symbol_count=3, rotation_slice_size=3)
    runner = ModelBRunner(db=db, journal=journal, config=config)

    # 10 instruments
    instruments = [
        NormalizedInstrument(
            symbol=f"SYM{i:02d}USDT",
            exchange_name="asterdex",
            base_asset=f"SYM{i:02d}",
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
            min_notional=5.0,
            contract_type="PERPETUAL",
            volume_24h_usdt=1000000.0 - (i * 50000.0),
            volume_status="AVAILABLE",
        )
        for i in range(10)
    ]

    # Suppose SYM09 has an open position (it is low volume, rank 10)
    open_pos = type("MockPos", (), {"symbol": "SYM09USDT", "order_id": "ORD_1", "setup_id": "SET_1"})()
    runner.paper_engine.open_positions["SYM09USDT"] = open_pos

    targets, _, _ = runner.screen_and_rank_symbols(instruments)

    # SYM09USDT MUST be included in targets, and placed at the very top (priority)
    target_syms = [t.symbol for t in targets]
    assert "SYM09USDT" in target_syms
    assert target_syms[0] == "SYM09USDT"

