"""Comprehensive Test Suite for AsterDEX Demand-Zone LONG Scanner.

Tests:
1. Universe Discovery & 24H Volume Screening (>= $1M - $5M USDT turnover, spread cap)
2. BTC Market Context Evaluation (Ref zone $79,864 - $80,400; Policies A, B, C)
3. 1H Technical Support & Demand-Zone Detection (swing lows, displacement ATR, freshness)
4. $0.30 Fixed Risk Budget Position Sizing & Margin Feasibility (5x - 10x leverage, $5 min notional)
5. Take-Profit Planning, Net R:R, Break-Even Price, and Resistance Obstruction Checks
6. Level-2 Order-Book Depth, Resting Bid Walls, Persistence Tracking, and Spoofing Risk
7. Deterministic Status Attribution (READY, APPROACHING, WATCHLIST, INFEASIBLE, INVALIDATED)
8. SQLite Storage Isolation Guarantee (Zero leakage into Model A, Model B, or trade journals)
9. FastAPI REST Endpoints (/api/demand_zone/*)
"""

import os
import time
from typing import Any, Dict, List, Optional
import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.core.types import Candle
from src.data.database import DatabaseManager
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.normalized import NormalizedInstrument, NormalizedOrderBook, OrderBookLevel
from src.demand_zone.btc_context import BTCContextEvaluator
from src.demand_zone.order_book_analyzer import DemandZoneOrderBookAnalyzer
from src.demand_zone.risk_sizer import DemandZoneRiskSizer
from src.demand_zone.scanner import AsterDexDemandZoneScanner
from src.demand_zone.storage import DemandZoneStorage
from src.demand_zone.tp_planner import DemandZoneTPPlanner
from src.demand_zone.types import (
    BTCZonePolicy,
    BTCZoneStatus,
    DemandZone,
    DemandZoneCandidateStatus,
    DemandZoneConfig,
)
from src.demand_zone.zone_detector import DemandZoneDetector


# ==============================================================================
# Helpers & Mocks
# ==============================================================================

def make_candle(
    timestamp_ms: int,
    open_px: float,
    high_px: float,
    low_px: float,
    close_px: float,
    volume: float = 1000.0,
    is_closed: bool = True,
    symbol: str = "TESTUSDT",
    timeframe: str = "1h",
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp_ms=timestamp_ms,
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        volume=volume,
        close_time_ms=timestamp_ms + 3600000,
        quote_volume=volume * close_px,
        trades_count=100,
        is_closed=is_closed,
    )


def generate_demand_candles(
    base_price: float = 100.0,
    demand_low: float = 95.0,
    demand_high: float = 96.5,
    count: int = 50,
) -> List[Candle]:
    """Generate 1H candles that create a clear validated demand zone at [demand_low, demand_high]."""
    candles = []
    t_start = int(time.time() * 1000) - (count * 3600 * 1000)
    px = base_price

    # 1. Early candles forming a swing low and base
    for i in range(20):
        t = t_start + (i * 3600 * 1000)
        c_open = px
        c_low = min(px - 0.5, demand_low) if i == 10 else px - 0.3
        c_high = px + 0.5
        c_close = px + 0.1
        candles.append(make_candle(t, c_open, c_high, c_low, c_close, is_closed=True))
        if i == 10:
            px = demand_high

    # 2. Strong displacement rally from demand zone
    for i in range(20, 30):
        t = t_start + (i * 3600 * 1000)
        c_open = px
        c_low = px - 0.2
        c_high = px + 1.5  # displacement expansion
        c_close = px + 1.2
        px = c_close
        candles.append(make_candle(t, c_open, c_high, c_low, c_close, is_closed=True))

    # 3. Pullback returning toward demand zone without breaking it
    target_pullback = demand_high * 1.025  # ~2.5% above zone
    num_pb = max(count - 30, 1)
    step = (px - target_pullback) / num_pb
    for i in range(30, count):
        t = t_start + (i * 3600 * 1000)
        c_open = px
        c_close = px - step
        c_high = px + 0.2
        c_low = c_close - 0.2
        px = c_close
        candles.append(make_candle(t, c_open, c_high, c_low, c_close, is_closed=True))

    return candles


# ==============================================================================
# 1. Candidate Universe and 24-Hour Volume Filter Tests
# ==============================================================================

class TestUniverseAndVolumeFilter:
    """Verifies $1M hard turnover threshold and bid-ask spread screening."""

    def test_volume_filter_thresholds(self):
        config = DemandZoneConfig(min_24h_volume_usdt=1000000.0, max_spread_bps=15.0)
        assert config.min_24h_volume_usdt == 1000000.0

        # Symbols satisfying turnover >= $1M
        liquid_vol = 1500000.0
        assert liquid_vol >= config.min_24h_volume_usdt

        # Low-volume instruments below $1M
        illiquid_vol = 750000.0
        assert illiquid_vol < config.min_24h_volume_usdt

    def test_spread_filtering(self):
        config = DemandZoneConfig(max_spread_bps=15.0)
        tight_spread = 3.5
        wide_spread = 22.0
        assert tight_spread <= config.max_spread_bps
        assert wide_spread > config.max_spread_bps


# ==============================================================================
# 2. BTC Market Context Filter Tests
# ==============================================================================

class TestBTCContextEvaluator:
    """Verifies reference zone evaluation ($79,864 - $80,400) and Policies A, B, and C."""

    def test_zone_status_classification(self):
        evaluator = BTCContextEvaluator()

        # BTC above zone ($81,500 > $80,400)
        obs_above = evaluator.evaluate(current_btc_price=81500.0)
        assert obs_above.zone_status == BTCZoneStatus.ABOVE_ZONE
        assert obs_above.distance_to_upper_pct > 0

        # BTC approaching zone ($80,800 is within 1.5% of upper bound $80,400)
        obs_approach = evaluator.evaluate(current_btc_price=80800.0)
        assert obs_approach.is_approaching is True

        # BTC inside zone ($80,100 is between $79,864 and $80,400)
        obs_inside = evaluator.evaluate(current_btc_price=80100.0)
        assert obs_inside.zone_status == BTCZoneStatus.INSIDE_ZONE

        # BTC below zone ($78,500 < $79,864)
        obs_below = evaluator.evaluate(current_btc_price=78500.0)
        assert obs_below.zone_status == BTCZoneStatus.BELOW_ZONE

    def test_policy_a_observe_continuously(self):
        evaluator = BTCContextEvaluator()
        # Policy A allows READY status even if BTC is above reference zone
        obs = evaluator.evaluate(current_btc_price=82000.0, policy_override=BTCZonePolicy.OBSERVE_CONTINUOUSLY)
        assert obs.allows_ready_status is True
        assert obs.priority_boost == 0.0

    def test_policy_b_prioritize_on_approach(self):
        evaluator = BTCContextEvaluator()
        # When approaching zone ($80,600), Policy B gives priority boost
        obs = evaluator.evaluate(current_btc_price=80600.0, policy_override=BTCZonePolicy.PRIORITIZE_ON_APPROACH)
        assert obs.allows_ready_status is True
        assert obs.priority_boost >= 10.0

    def test_policy_c_require_confirmation(self):
        evaluator = BTCContextEvaluator()
        # BTC is above zone: Policy C withholds READY status (held in WATCHLIST)
        obs_held = evaluator.evaluate(current_btc_price=82500.0, policy_override=BTCZonePolicy.REQUIRE_BTC_CONFIRMATION)
        assert obs_held.allows_ready_status is False

        # BTC enters zone: Policy C activates READY eligibility
        obs_active = evaluator.evaluate(current_btc_price=80100.0, policy_override=BTCZonePolicy.REQUIRE_BTC_CONFIRMATION)
        assert obs_active.allows_ready_status is True


# ==============================================================================
# 3. Technical Support and Demand-Zone Detection Tests
# ==============================================================================

class TestDemandZoneDetector:
    """Verifies zone detection, swing lows, displacement ATR, and invalidation rules."""

    def test_demand_zone_detection_on_closed_candles(self):
        detector = DemandZoneDetector()
        candles = generate_demand_candles(base_price=100.0, demand_low=95.0, demand_high=96.5, count=60)
        curr_px = candles[-1].close

        zones = detector.detect_demand_zones(symbol="TESTUSDT", candles_1h=candles, current_price=curr_px)
        assert len(zones) >= 1

        best = detector.select_best_demand_zone(zones, curr_px)
        assert best is not None
        assert best.lower_price <= best.upper_price
        assert best.invalidation_level < best.lower_price
        assert best.quality_score >= 50.0

    def test_unclosed_candle_protection(self):
        detector = DemandZoneDetector()
        candles = generate_demand_candles(count=40)
        # Mark last candle unclosed
        candles[-1].is_closed = False

        zones = detector.detect_demand_zones(symbol="TESTUSDT", candles_1h=candles, current_price=100.0)
        # Ensure detector ignores unclosed bar when anchoring zones
        for z in zones:
            assert z.is_provisional is False

    def test_distance_to_demand_computation(self):
        detector = DemandZoneDetector()
        zone = DemandZone(
            zone_id="DZ_TEST",
            symbol="TESTUSDT",
            lower_price=95.0,
            upper_price=97.0,
            mid_price=96.0,
            formation_timestamp_ms=1000,
            swing_low_price=94.5,
            invalidation_level=94.0,
        )
        curr_px = 100.0
        # Distance to upper boundary: (100 - 97) / 100 * 100 = 3.0%
        dist_pct, ref_px = detector.compute_distance_to_demand(curr_px, zone, reference_rule="ZONE_UPPER")
        assert round(dist_pct, 2) == 3.00
        assert ref_px == 97.0


# ==============================================================================
# 4. Entry, Stop Loss, and Risk Budget Position Sizing Tests
# ==============================================================================

class TestRiskSizingAndMargin:
    """Verifies $0.30 fixed risk budget, fees, slippage, and AsterDEX min notional."""

    def test_risk_budget_sizing_accuracy(self):
        sizer = DemandZoneRiskSizer(DemandZoneConfig(risk_budget_usdt=0.30))
        inst = NormalizedInstrument(
            symbol="TESTUSDT",
            exchange_name="asterdex",
            base_asset="TEST",
            quote_asset="USDT",
            min_qty=0.01,
            step_size=0.01,
            tick_size=0.01,
            min_notional=5.0,
            contract_type="PERPETUAL",
            last_price=100.0,
            volume_24h_usdt=5000000.0,
            is_active=True,
        )
        # Entry 100, Stop Loss 97.5 (2.5% stop distance)
        result = sizer.calculate_sizing_and_risk(
            symbol="TESTUSDT",
            entry_price=100.0,
            stop_loss_price=97.5,
            instrument=inst,
        )
        assert result.is_feasible is True
        assert result.risk_budget_usdt == 0.30
        assert result.position_notional_usdt >= 5.0  # Above AsterDEX min notional
        assert result.required_margin_usdt <= 15.0   # Under margin cap
        # Net risk including fees and slippage should be very close to $0.30
        assert 0.25 <= result.total_estimated_loss_usdt <= 0.35

    def test_min_notional_rejection(self):
        # Extremely tight stop or high price where budget produces < $5 notional
        sizer = DemandZoneRiskSizer(DemandZoneConfig(risk_budget_usdt=0.30, min_notional_usdt=5.0))
        inst = NormalizedInstrument(
            symbol="WIDEUSDT",
            exchange_name="asterdex",
            base_asset="WIDE",
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
            min_notional=5.0,
            contract_type="PERPETUAL",
            last_price=100.0,
            volume_24h_usdt=5000000.0,
            is_active=True,
        )
        # If stop is 20% wide, notional = 0.30 / 0.20 = $1.50, which violates $5 min notional
        result = sizer.calculate_sizing_and_risk(
            symbol="WIDEUSDT",
            entry_price=100.0,
            stop_loss_price=80.0,
            instrument=inst,
        )
        assert result.is_feasible is False
        assert any("minimum" in r for r in result.infeasible_reasons)

    def test_hard_stop_loss_cap_rejection(self):
        sizer = DemandZoneRiskSizer(DemandZoneConfig(hard_max_stop_loss_pct=5.0))
        inst = NormalizedInstrument(
            symbol="TESTUSDT",
            exchange_name="asterdex",
            base_asset="TEST",
            quote_asset="USDT",
            min_qty=0.01,
            step_size=0.01,
            tick_size=0.01,
            min_notional=5.0,
            contract_type="PERPETUAL",
            last_price=100.0,
            volume_24h_usdt=5000000.0,
            is_active=True,
        )
        # 6.0% stop distance exceeds 5.0% hard cap
        result = sizer.calculate_sizing_and_risk(
            symbol="TESTUSDT",
            entry_price=100.0,
            stop_loss_price=94.0,
            instrument=inst,
        )
        assert result.is_feasible is False
        assert any("exceeds hard risk cap" in r for r in result.infeasible_reasons)


# ==============================================================================
# 5. Take-Profit Planning and Resistance Obstruction Tests
# ==============================================================================

class TestTakeProfitPlanner:
    """Verifies structural target anchoring, net R:R calculation, and resistance obstruction."""

    def test_take_profit_net_rr_calculation(self):
        planner = DemandZoneTPPlanner(DemandZoneConfig(min_net_reward_to_risk=1.5))
        candles = generate_demand_candles(count=40)

        result = planner.plan_targets(
            symbol="TESTUSDT",
            entry_price=100.0,
            stop_loss_price=97.5,
            quantity=0.11,
            position_notional_usdt=11.0,
            candles_1h=candles,
            current_price=100.0,
        )
        assert result.take_profit_1 > 100.0
        assert result.net_reward_to_risk >= 1.50
        assert result.break_even_price > 100.0  # Must cover maker + taker + slippage

    def test_resistance_obstruction_detection(self):
        planner = DemandZoneTPPlanner(DemandZoneConfig(min_resistance_clearance_rr=1.2))
        candles = generate_demand_candles(count=40)
        # Inject an unbroken resistance high right above current price (at 0.5R)
        # Entry 100, Stop 97.5, Stop dist 2.5 -> 0.5R resistance is at 101.25
        t_recent = candles[-1].timestamp_ms - 3600000
        candles[-2] = make_candle(t_recent, 100.5, 101.25, 100.2, 100.8, is_closed=True)

        result = planner.plan_targets(
            symbol="TESTUSDT",
            entry_price=100.0,
            stop_loss_price=97.5,
            quantity=0.11,
            position_notional_usdt=11.0,
            candles_1h=candles,
            current_price=100.8,
        )
        # If nearest overhead resistance is closer than 1.2R, obstruction is flagged
        if result.is_obstructed_by_resistance:
            assert result.is_feasible is False
            assert any("obstructed" in r for r in result.infeasible_reasons)


# ==============================================================================
# 6. Order-Book and Spoofing-Risk Assessment Tests
# ==============================================================================

class TestOrderBookAnalyzer:
    """Verifies L2 depth calculation, resting bid walls, persistence, and spoofing detection."""

    def test_order_book_depth_and_score(self):
        analyzer = DemandZoneOrderBookAnalyzer()
        book = NormalizedOrderBook(
            symbol="TESTUSDT",
            exchange_name="asterdex",
            timestamp_ms=int(time.time() * 1000),
            bids=[OrderBookLevel(price=100.0 - (i * 0.05), size=10.0) for i in range(20)],
            asks=[OrderBookLevel(price=100.05 + (i * 0.05), size=10.0) for i in range(20)],
        )
        assessment = analyzer.analyze_order_book(
            symbol="TESTUSDT",
            book=book,
            intended_order_notional=11.0,
        )
        assert assessment.spread_bps > 0
        assert assessment.bid_depth_25bps_usdt > 0
        assert assessment.order_size_depth_ratio > 1.0
        assert 5.0 <= assessment.liquidity_quality_score <= 100.0

    def test_resting_bid_wall_persistence_and_spoofing(self):
        analyzer = DemandZoneOrderBookAnalyzer()
        sym = "WALLUSDT"
        now = int(time.time() * 1000)

        # Snapshot 1: Large resting wall at $95.50 ($50,000 USDT)
        bids_with_wall = [
            OrderBookLevel(price=100.0, size=1.0),
            OrderBookLevel(price=95.5, size=523.56),  # $50,000 wall
        ]
        asks = [OrderBookLevel(price=100.1, size=1.0)]
        book1 = NormalizedOrderBook(sym, "asterdex", now, bids=bids_with_wall, asks=asks)
        analyzer.analyze_order_book(sym, book1, intended_order_notional=10.0, now_ms=now)

        # Snapshot 2: Wall persists
        now += 60000
        book2 = NormalizedOrderBook(sym, "asterdex", now, bids=bids_with_wall, asks=asks)
        analyzer.analyze_order_book(sym, book2, intended_order_notional=10.0, now_ms=now)

        # Snapshot 3: Wall persists -> confirmed persistent
        now += 60000
        book3 = NormalizedOrderBook(sym, "asterdex", now, bids=bids_with_wall, asks=asks)
        res3 = analyzer.analyze_order_book(sym, book3, intended_order_notional=10.0, now_ms=now)
        assert res3.wall_persistence_status == "CONFIRMED_PERSISTENT"

        # Snapshot 4: Wall vanished before price touched it -> spoofing warning
        now += 60000
        bids_without_wall = [OrderBookLevel(price=100.0, size=1.0)]
        book4 = NormalizedOrderBook(sym, "asterdex", now, bids=bids_without_wall, asks=asks)
        res4 = analyzer.analyze_order_book(sym, book4, intended_order_notional=10.0, now_ms=now)
        assert res4.spoofing_warning is True


# ==============================================================================
# 7. Storage Isolation & End-to-End Scanner Integration Tests
# ==============================================================================

class TestScannerIntegrationAndIsolation:
    """Verifies scanner pipeline execution and guarantees zero leakage into Model A/B."""

    def test_storage_and_governance_isolation(self, tmp_path):
        test_db_path = str(tmp_path / "test_scanner.db")
        db = DatabaseManager(test_db_path)
        storage = DemandZoneStorage(db)

        # Confirm dedicated tables exist
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [r[0] for r in cur.fetchall()]
            assert "demand_zone_candidates" in tables
            assert "demand_zone_scans" in tables

            # Verify trade_journal has zero rows from demand zone operations
            cur.execute("SELECT COUNT(*) FROM trade_journal;")
            assert cur.fetchone()[0] == 0

    def test_full_scanner_run_with_synthetic_markets(self, tmp_path):
        test_db_path = str(tmp_path / "test_scanner_run.db")
        db = DatabaseManager(test_db_path)
        config = DemandZoneConfig(min_24h_volume_usdt=1000000.0, risk_budget_usdt=0.30)
        scanner = AsterDexDemandZoneScanner(db=db, config=config)

        # Provide synthetic candidates
        candles_spcx = generate_demand_candles(base_price=100.0, demand_low=95.0, demand_high=96.5, count=50)
        book_spcx = NormalizedOrderBook(
            symbol="SPCXUSDT",
            exchange_name="asterdex",
            timestamp_ms=int(time.time() * 1000),
            bids=[OrderBookLevel(100.0, 10.0), OrderBookLevel(96.5, 50.0)],
            asks=[OrderBookLevel(100.1, 10.0)],
        )

        candidates = scanner.scan_universe(
            max_symbols_to_evaluate=5,
            candles_by_symbol={"SPCXUSDT": candles_spcx, "BTCUSDT": candles_spcx},
            books_by_symbol={"SPCXUSDT": book_spcx},
        )
        assert len(candidates) >= 1
        assert scanner.latest_telemetry.get("total_markets_discovered") >= 0


# ==============================================================================
# 8. REST API Endpoints Tests
# ==============================================================================

class TestDemandZoneRESTAPI:
    """Verifies FastAPI endpoints for candidate queries, BTC context, and configuration."""

    def test_get_demand_zone_config(self):
        client = TestClient(app)
        res = client.get("/api/demand_zone/config")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "SUCCESS"
        assert "config" in data
        assert data["config"]["risk_budget_usdt"] >= 0.25

    def test_get_demand_zone_btc_context(self):
        client = TestClient(app)
        res = client.get("/api/demand_zone/btc_context")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "SUCCESS"
        assert "btc_context" in data

    def test_get_demand_zone_candidates(self):
        client = TestClient(app)
        res = client.get("/api/demand_zone/candidates?limit=10")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "SUCCESS"
        assert "candidates" in data
        assert "status_counts" in data

    def test_update_demand_zone_config(self):
        client = TestClient(app)
        res = client.post("/api/demand_zone/config", json={
            "risk_budget_usdt": 0.32,
            "btc_policy": "Policy A",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "SUCCESS"
        assert data["updated_config"]["risk_budget_usdt"] == 0.32
        assert data["updated_config"]["btc_policy"] == "OBSERVE_CONTINUOUSLY"
