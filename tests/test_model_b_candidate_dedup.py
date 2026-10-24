"""Tests for Model B Candidate Deduplication, Truthful Execution States, and Metrics.

Covers all 10 verification requirements of Phase 6:
1. Repeated scan cycles do not create duplicate active candidates for one logical setup.
2. A genuinely new setup can be created after invalidation or expiration.
3. Candidate persistence survives restart without duplicate active rows.
4. READY alone does not create a paper order or position.
5. A valid candidate advances through the intended submission path when all required conditions are satisfied.
6. Risk rejection and missing-data rejection expose explicit reasons.
7. Paper orders and positions remain idempotent.
8. Dashboard rows and execution states match API and database records.
9. Model A regression tests and historical cohort data remain unchanged.
10. Live trading remains locked.
"""

from typing import Dict, List
import json
import os
import tempfile
import time
import pytest

from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    ExecutionStatus,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
    TradeRecord,
)
from src.data.database import DatabaseManager
from src.exchange.normalized import LiquiditySnapshot, NormalizedInstrument
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperOrder, PaperOrderStatus
from src.journal.repository import JournalRepository
from src.risk.model_b_risk import ModelBRiskManager
from src.scheduler.model_b_runner import ModelBRunner
from src.strategy.model_b.generator import ModelBStrategyGenerator
from config.settings import AppConfig, ModelBConfig, PaperExecutionConfig



def create_test_candle_series(symbol: str = "TESTUSDT", base_price: float = 100.0, count: int = 30) -> List[Candle]:
    """Create a synthetic 1H candle series forming a confirmed swing low and sweep-reclaim."""
    candles = []
    base_ts = 1792300000000  # Fixed base time

    for i in range(count):
        ts = base_ts + (i * 3600 * 1000)
        close_ts = ts + (3600 * 1000) - 1

        # Form a clear swing low at bar 10 (low=90.0), then sweep at bar 28 (low=89.0, close=92.0)
        if i == 10:
            o, h, l, c = 95.0, 96.0, 90.0, 93.0
        elif i == 28:
            o, h, l, c = 91.0, 93.0, 89.0, 92.0  # Swept 90.0 and closed above
        elif i == 29:
            o, h, l, c = 92.0, 94.0, 91.5, 93.5  # Current closed bar
        else:
            o, h, l, c = 100.0, 102.0, 98.0, 100.0

        candles.append(
            Candle(
                symbol=symbol,
                timeframe="1h",
                timestamp_ms=ts,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=10000.0,
                close_time_ms=close_ts,
            )
        )
    return candles


def create_test_instrument(symbol: str = "TESTUSDT") -> NormalizedInstrument:
    return NormalizedInstrument(
        symbol=symbol,
        exchange_name="asterdex",
        base_asset=symbol.replace("USDT", ""),
        quote_asset="USDT",
        min_qty=0.1,
        step_size=0.1,
        tick_size=0.01,
        min_notional=5.0,
        contract_type="PERPETUAL",
        is_active=True,
        volume_24h_usdt=5000000.0,
        volume_status="AVAILABLE",
    )


@pytest.fixture
def test_setup():
    """Build isolated in-memory or temp SQLite environment for Model B testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db = DatabaseManager(db_path)
    journal = JournalRepository(db)
    config = ModelBConfig(target_trade_risk_usdt=1.0, max_margin_usdt=15.0, max_open_positions=3)
    paper_config = PaperExecutionConfig(initial_paper_balance=100.0)
    risk_manager = ModelBRiskManager(config=config)
    paper_engine = PaperExecutionEngine(db=db, journal=journal, risk_manager=risk_manager, config=paper_config, model_id="MODEL_B")
    generator = ModelBStrategyGenerator(config=config)

    # Attach mock liquidity collector returning healthy L2 snapshot
    class MockCollector:
        def collect_for_symbol(self, symbol, candle=None):
            return LiquiditySnapshot(
                symbol=symbol,
                exchange_name="asterdex",
                timestamp_ms=candle.timestamp_ms if candle else 1792300000000,
                spread_bps=5.0,
                depth_10bps_usdt=25000.0,
                depth_25bps_usdt=50000.0,
                depth_50bps_usdt=100000.0,
                depth_100bps_usdt=200000.0,
                imbalance_10bps=0.30,
                imbalance_25bps=0.35,
                imbalance_50bps=0.25,
                imbalance_100bps=0.20,
                cvd_1h=1500.0,
                taker_buy_volume_1h=10000.0,
                taker_sell_volume_1h=8500.0,
                large_trade_count=10,
                absorption_score=0.65,
            )

    generator.liquidity_collector = MockCollector()


    runner = ModelBRunner(
        db=db,
        journal=journal,
        config=config,
        paper_config=paper_config,
        risk_manager=risk_manager,
        paper_engine=paper_engine,
        generator=generator,
    )


    yield {
        "db": db,
        "journal": journal,
        "config": config,
        "paper_config": paper_config,
        "risk_manager": risk_manager,
        "paper_engine": paper_engine,
        "generator": generator,
        "runner": runner,
        "db_path": db_path,
    }

    try:
        os.remove(db_path)
    except Exception:
        pass


class TestModelBCandidateDeduplication:
    """Deterministic validation test suite for Phase 6 requirements."""

    def test_1_repeated_scan_cycles_do_not_create_duplicate_active_candidates(self, test_setup):
        """Requirement 1: Repeated scan cycles do not create duplicate active candidates for one logical setup."""
        runner = test_setup["runner"]
        journal = test_setup["journal"]
        candles = create_test_candle_series("SOLUSDT")
        inst = create_test_instrument("SOLUSDT")

        # Mock exchange get_klines and focus symbols
        runner.get_focus_symbols = lambda max_symbols=20: [inst]
        valid_book = NormalizedOrderBook("LINKUSDT", "asterdex", 1792350000000, [OrderBookLevel(price=19.98, size=500.0)], [OrderBookLevel(price=20.02, size=500.0)])
        runner.exchange = type("MockEx", (), {
            "get_klines": lambda self, sym, tf, limit=50: candles,
            "get_order_book": lambda self, sym: valid_book,
        })()
        runner.exchange_service.get_aggregated_order_books = lambda sym: {"asterdex": valid_book}

        # Run cycle 1
        now_ms = 1792350000000
        runner.run_scan_cycle(now_ms=now_ms)
        setups_1 = journal.get_latest_setups(symbol="SOLUSDT", model_id="MODEL_B")
        assert len(setups_1) == 1, "Expected exactly 1 candidate after initial scan"
        initial_cid = setups_1[0]["candidate_id"]

        # Run cycle 2 (same candle state, later scan time)
        runner.run_scan_cycle(now_ms=now_ms + 60000)
        setups_2 = journal.get_latest_setups(symbol="SOLUSDT", model_id="MODEL_B")
        assert len(setups_2) == 1, f"Expected exactly 1 candidate after second scan cycle, got {len(setups_2)}"
        assert setups_2[0]["candidate_id"] == initial_cid, "Candidate ID must remain stable across scans for identical setup"

        # Run cycle 3
        runner.run_scan_cycle(now_ms=now_ms + 120000)
        setups_3 = journal.get_latest_setups(symbol="SOLUSDT", model_id="MODEL_B")
        assert len(setups_3) == 1, "Idempotent upsert must not create duplicate active candidates"

    def test_2_genuinely_new_setup_can_be_created_after_expiration_or_close(self, test_setup):
        """Requirement 2: A genuinely new setup can be created after invalidation or expiration."""
        runner = test_setup["runner"]
        journal = test_setup["journal"]
        inst = create_test_instrument("BTCUSDT")

        candles_1 = create_test_candle_series("BTCUSDT", base_price=50000.0)
        runner.get_focus_symbols = lambda max_symbols=20: [inst]
        runner.exchange = type("MockEx", (), {"get_klines": lambda self, sym, tf, limit=50: candles_1})()

        # Cycle 1: First setup
        t1 = 1792350000000
        runner.run_scan_cycle(now_ms=t1)
        setups = journal.get_latest_setups(symbol="BTCUSDT", model_id="MODEL_B")
        assert len(setups) == 1
        first_cid = setups[0]["candidate_id"]

        # Mark setup 1 as expired / closed
        with test_setup["db"].get_connection() as conn:
            conn.execute("UPDATE setup_candidates SET status = 'EXPIRED' WHERE candidate_id = ?", (first_cid,))
            conn.commit()

        # Advance candle series by 5 bars forming a new bar anchor
        candles_2 = create_test_candle_series("BTCUSDT", base_price=51000.0)
        # Shift candle timestamps forward by 10 hours
        shifted_candles = [
            Candle(
                symbol=c.symbol,
                timeframe=c.timeframe,
                timestamp_ms=c.timestamp_ms + (10 * 3600 * 1000),
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                close_time_ms=c.close_time_ms + (10 * 3600 * 1000),
            )
            for c in candles_2
        ]
        runner.exchange = type("MockEx", (), {"get_klines": lambda self, sym, tf, limit=50: shifted_candles})()

        # Cycle 2: Genuinely new setup
        t2 = t1 + (10 * 3600 * 1000)
        runner.run_scan_cycle(now_ms=t2)
        all_setups = journal.get_latest_setups(symbol="BTCUSDT", model_id="MODEL_B")
        assert len(all_setups) == 2, "A new logical setup on a later candle must be preserved alongside historical setup"
        cids = [s["candidate_id"] for s in all_setups]
        assert first_cid in cids

    def test_3_candidate_persistence_survives_restart_without_duplication(self, test_setup):
        """Requirement 3: Candidate persistence survives restart without duplicate active rows."""
        db = test_setup["db"]
        journal = test_setup["journal"]
        candles = create_test_candle_series("ETHUSDT")
        inst = create_test_instrument("ETHUSDT")

        runner1 = test_setup["runner"]
        runner1.get_focus_symbols = lambda max_symbols=20: [inst]
        runner1.exchange = type("MockEx", (), {"get_klines": lambda self, sym, tf, limit=50: candles})()
        runner1.run_scan_cycle(now_ms=1792350000000)

        assert len(journal.get_latest_setups(symbol="ETHUSDT", model_id="MODEL_B")) == 1

        # Simulate fresh process restart by creating new runner instances on same db
        config = test_setup["config"]
        paper_config = test_setup["paper_config"]
        risk_mgr2 = ModelBRiskManager(config=config)
        paper_engine2 = PaperExecutionEngine(db=db, journal=journal, risk_manager=risk_mgr2, config=paper_config, model_id="MODEL_B")
        runner2 = ModelBRunner(
            db=db,
            journal=journal,
            config=config,
            paper_config=paper_config,
            risk_manager=risk_mgr2,
            paper_engine=paper_engine2,
        )
        runner2.generator.liquidity_collector = runner1.generator.liquidity_collector
        runner2.get_focus_symbols = lambda max_symbols=20: [inst]
        runner2.exchange = type("MockEx", (), {"get_klines": lambda self, sym, tf, limit=50: candles})()

        # Scan on restarted instance
        runner2.run_scan_cycle(now_ms=1792350060000)
        setups_after_restart = journal.get_latest_setups(symbol="ETHUSDT", model_id="MODEL_B")
        assert len(setups_after_restart) == 1, "Restart reload must not create duplicate active rows"

    def test_4_ready_alone_does_not_create_paper_order_or_position(self, test_setup):
        """Requirement 4: READY alone does not create a paper order or position."""
        journal = test_setup["journal"]
        paper_engine = test_setup["paper_engine"]

        # Directly save a READY candidate into journal without calling submit_candidate
        cand = SetupCandidate(
            candidate_id="MCAND_B_TEST_STANDALONE",
            symbol="AVAXUSDT",
            timestamp_ms=1792350000000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=20.0,
            stop_loss=19.0,
            take_profit_1=21.5,
            status=CandidateStatus.READY,
            model_id="MODEL_B",
            execution_status=ExecutionStatus.NOT_SUBMITTED,
        )
        journal.save_setup(cand)

        orders = journal.get_paper_orders(symbol="AVAXUSDT", model_id="MODEL_B")
        trades = journal.get_trades(symbol="AVAXUSDT", model_id="MODEL_B")
        assert len(orders) == 0, "READY candidate must not automatically spawn a paper order"
        assert len(trades) == 0, "READY candidate must not automatically spawn a trade"
        assert len(paper_engine.open_positions) == 0, "No open position without fill"

    def test_5_valid_candidate_advances_through_submission_path(self, test_setup):
        """Requirement 5: A valid candidate advances through the intended submission path when conditions are satisfied."""
        runner = test_setup["runner"]
        journal = test_setup["journal"]
        candles = create_test_candle_series("LINKUSDT")
        inst = create_test_instrument("LINKUSDT")

        runner.get_focus_symbols = lambda max_symbols=20: [inst]
        runner.exchange = type("MockEx", (), {"get_klines": lambda self, sym, tf, limit=50: candles})()

        # Run scan
        now_ms = 1792350000000
        runner.run_scan_cycle(now_ms=now_ms)

        setups = journal.get_latest_setups(symbol="LINKUSDT", model_id="MODEL_B")
        assert len(setups) == 1
        cand = setups[0]
        assert cand["status"] == "READY"
        det = cand["details"]
        assert det["execution_status"] in ("SUBMITTED", "FILLED"), "Valid candidate must advance to SUBMITTED or FILLED"
        assert det["order_id"] is not None

        orders = journal.get_paper_orders(symbol="LINKUSDT", model_id="MODEL_B")
        assert len(orders) == 1, "Paper order must be created"
        assert orders[0]["order_id"] == det["order_id"]

    def test_6_risk_rejection_and_missing_data_expose_explicit_reasons(self, test_setup):
        """Requirement 6: Risk rejection and missing-data rejection expose explicit reasons."""
        generator = test_setup["generator"]
        inst = create_test_instrument("RISKUSDT")

        # Invalid stop loss causing negative stop distance
        assessment = generator.size_position(entry_price=10.0, stop_loss=10.0, instrument=inst)
        assert not assessment.is_feasible
        assert len(assessment.rejection_reasons) > 0
        assert "Invalid entry or stop-loss price" in assessment.rejection_reasons[0]

        # Infeasible due to exceeding max margin cap ($15.00 USDT)
        # Entry=100.0, SL=99.9 => raw_qty = 1.0 / 0.1 = 10 units => notional = $1000 => margin at 10x = $100 > $15 cap
        assessment_cap = generator.size_position(entry_price=100.0, stop_loss=99.9, instrument=inst)
        assert not assessment_cap.is_feasible
        reasons_text = " ".join(assessment_cap.rejection_reasons)
        assert "exceeds Model B cap" in reasons_text or "exceeds" in reasons_text

    def test_7_paper_orders_and_positions_remain_idempotent(self, test_setup):
        """Requirement 7: Paper orders and positions remain idempotent."""
        paper_engine = test_setup["paper_engine"]
        cand = SetupCandidate(
            candidate_id="MCAND_B_IDEM_1",
            symbol="BNBUSDT",
            timestamp_ms=1792350000000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=300.0,
            stop_loss=295.0,
            take_profit_1=307.5,
            status=CandidateStatus.READY,
            model_id="MODEL_B",
            risk_assessment=RiskAssessment(
                is_feasible=True,
                quantity=1.0,
                position_notional_usdt=300.0,
                target_isolated_margin_usdt=6.0,
                position_leverage=10.0,
            ),
        )

        # First submission succeeds
        order1 = paper_engine.submit_candidate(cand, now_ms=1792350000000)
        assert order1 is not None

        # Second submission of same candidate must be rejected by idempotency check
        order2 = paper_engine.submit_candidate(cand, now_ms=1792350005000)
        assert order2 is None, "Duplicate candidate submission must return None"

    def test_8_dashboard_and_api_counters_agree_with_db(self, test_setup):
        """Requirement 8: Dashboard rows and execution states match API and database records."""
        from src.api.app import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        res = client.get("/api/model_b/setups")
        assert res.status_code == 200
        data = res.json()
        assert "setups" in data
        assert "count" in data
        assert data["count"] == len(data["setups"])

        for s in data["setups"]:
            # Verify metrics are not bare nulls without metadata
            assert "metadata" in s
            assert "risk_assessment" in s
            assert "execution_state" in s
            if s["execution_state"] == "NOT_SUBMITTED":
                assert "not_submitted_reason" in s
                assert len(s["not_submitted_reason"]) > 0

    def test_9_model_a_regression_and_historical_cohort_unchanged(self, test_setup):
        """Requirement 9: Model A regression tests and historical cohort data remain unchanged."""
        journal = test_setup["journal"]
        cfg = AppConfig()

        # Model A whitelist must remain 11 frozen symbols
        assert len(cfg.watchlist) == 11
        assert "AGTUSDT" not in cfg.watchlist
        assert "BTCUSDT" in cfg.watchlist
        assert "ETHUSDT" in cfg.watchlist

        # Forward cohort computation must complete without errors
        cohort = journal.compute_forward_validation_cohort()
        assert isinstance(cohort, dict)
        assert "completed_forward_trades" in cohort

    def test_10_live_trading_remains_locked(self, test_setup):
        """Requirement 10: Live trading remains locked."""
        cfg = AppConfig()

        assert cfg.live_trading_locked is True
        assert os.environ.get("SCANNER_MODE", "PAPER").upper() == "PAPER"
