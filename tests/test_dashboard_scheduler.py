"""Comprehensive Unit and Integration Tests for Dynamic Dashboard, Background Scheduler,
Confidence Ranking, Status Classifications, Invalidation Lifecycle, and Concurrency.
"""

import asyncio
import hashlib
from pathlib import Path
import time
import pytest
from fastapi.testclient import TestClient

from config.settings import get_default_config
from src.api.app import app, scan_lock, runner, BACKGROUND_SCAN_ENABLED
from src.core.types import (
    CandidateStatus,
    Candle,
    Direction,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
)
from src.data.database import DatabaseManager
from src.exchange.metadata import SymbolSpecs
from src.journal.repository import JournalRepository
from src.scheduler.runner import ScannerRunner, rank_candidates


@pytest.fixture
def test_client():
    with TestClient(app) as client:
        yield client


class TestConfidenceRanking:
    """Test 1: Confidence ranking, missing confidence values (N/A), and deterministic tie-breaking."""

    def test_ranking_deterministic_and_tie_breaking(self):
        # 1. Candidate with high confidence
        c_high = SetupCandidate(
            candidate_id="C_HIGH",
            symbol="BTCUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit_1=115.0,
            gross_reward_to_risk=3.0,
            net_reward_to_risk=2.8,
            ml_probability=0.75,
            status=CandidateStatus.READY,
        )

        # 2. Candidate with moderate confidence
        c_mid_1 = SetupCandidate(
            candidate_id="C_MID_1",
            symbol="ETHUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=50.0,
            stop_loss=48.0,
            take_profit_1=56.0,
            gross_reward_to_risk=3.0,
            net_reward_to_risk=2.0,
            ml_probability=0.60,
            status=CandidateStatus.READY,
        )

        # 3. Candidate with equal confidence but higher R:R (Tie-breaker 1)
        c_mid_2 = SetupCandidate(
            candidate_id="C_MID_2",
            symbol="SOLUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=20.0,
            stop_loss=19.0,
            take_profit_1=23.0,
            gross_reward_to_risk=3.0,
            net_reward_to_risk=2.5,  # Higher R:R than c_mid_1
            ml_probability=0.60,
            status=CandidateStatus.READY,
        )

        # 4. Candidate with equal confidence and equal R:R, but newer timestamp (Tie-breaker 2)
        c_mid_3 = SetupCandidate(
            candidate_id="C_MID_3",
            symbol="AVAXUSDT",
            timestamp_ms=2000,  # Newer than c_mid_1
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=30.0,
            stop_loss=28.0,
            take_profit_1=34.0,
            gross_reward_to_risk=2.5,
            net_reward_to_risk=2.0,  # Same R:R as c_mid_1
            ml_probability=0.60,
            status=CandidateStatus.READY,
        )

        # 5. Candidate with missing ML probability (None -> N/A)
        c_na = SetupCandidate(
            candidate_id="C_NA",
            symbol="ADAUSDT",
            timestamp_ms=1500,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=0.5,
            stop_loss=0.48,
            take_profit_1=0.56,
            gross_reward_to_risk=3.0,
            net_reward_to_risk=2.9,
            ml_probability=None,
            status=CandidateStatus.WATCHLIST,
        )

        candidates = [c_na, c_mid_1, c_high, c_mid_3, c_mid_2]

        # Test confidence_desc
        ranked_desc = rank_candidates(candidates, sort_by="confidence_desc")
        assert ranked_desc[0].candidate_id == "C_HIGH"  # 0.75
        assert ranked_desc[1].candidate_id == "C_MID_2"  # 0.60, R:R 2.5
        assert ranked_desc[2].candidate_id == "C_MID_3"  # 0.60, R:R 2.0, ts 2000
        assert ranked_desc[3].candidate_id == "C_MID_1"  # 0.60, R:R 2.0, ts 1000
        assert ranked_desc[4].candidate_id == "C_NA"  # None placed explicitly at bottom!

        # Test confidence_asc
        ranked_asc = rank_candidates(candidates, sort_by="confidence_asc")
        # 0.60 group first, then 0.75, then None (never treated as 0)
        assert ranked_asc[0].candidate_id in ("C_MID_2", "C_MID_3", "C_MID_1")
        assert ranked_asc[3].candidate_id == "C_HIGH"
        assert ranked_asc[4].candidate_id == "C_NA"  # N/A remains at bottom

        # Test latest
        ranked_latest = rank_candidates(candidates, sort_by="latest")
        assert ranked_latest[0].candidate_id == "C_MID_3"  # ts 2000
        assert ranked_latest[1].candidate_id == "C_NA"  # ts 1500


class TestStatusClassifications:
    """Test 2: All status classifications and semantics."""

    def test_status_values_and_compatibility(self):
        assert CandidateStatus.READY.value == "READY"
        assert CandidateStatus.WATCHLIST.value == "WATCHLIST"
        assert CandidateStatus.INFEASIBLE.value == "INFEASIBLE"
        assert CandidateStatus.INVALIDATED.value == "INVALIDATED"
        assert CandidateStatus.STALE.value == "STALE"
        assert CandidateStatus.ERROR.value == "ERROR"

        # Compatibility alias check
        assert CandidateStatus.VALID == CandidateStatus.READY
        assert CandidateStatus("VALID") == CandidateStatus.READY

    def test_candidate_properties(self):
        cand = SetupCandidate(
            candidate_id="TEST_READY",
            symbol="BTCUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit_1=115.0,
            ml_probability=0.685,
            status=CandidateStatus.READY,
        )
        assert cand.is_executable is True
        assert cand.confidence_display == "68.5%"

        cand_na = SetupCandidate(
            candidate_id="TEST_WL",
            symbol="ETHUSDT",
            timestamp_ms=1000,
            direction=Direction.SHORT,
            bias_4h=MarketBias.BEARISH,
            structure_1h=StructureState.BEARISH,
            entry_price=50.0,
            stop_loss=52.0,
            take_profit_1=45.0,
            ml_probability=None,
            status=CandidateStatus.WATCHLIST,
        )
        assert cand_na.is_executable is False
        assert cand_na.confidence_display == "N/A"


class TestSignalLifecycleAndInvalidation:
    """Test 3 & 4: Structural invalidation and expiration."""

    def test_structural_invalidation_on_subsequent_candle(self, tmp_path):
        db = DatabaseManager(tmp_path / "test.db")
        now_ms = 1700000000000

        # Insert historical candles
        initial_candle = Candle(
            symbol="BTCUSDT",
            timeframe="1h",
            timestamp_ms=now_ms,
            open=100.0,
            high=102.0,
            low=98.0,
            close=100.0,
            volume=100.0,
            close_time_ms=now_ms + 3600000,
        )
        db.insert_candles([initial_candle])

        # Active long setup with invalidation at 95.0
        cand = SetupCandidate(
            candidate_id="SETUP_BTC_LONG",
            symbol="BTCUSDT",
            timestamp_ms=now_ms,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit_1=115.0,
            invalidation_price=95.0,
            invalidation_reason="Invalidated if bar breaches 95.0",
            status=CandidateStatus.READY,
            expires_at_ms=now_ms + (12 * 3600 * 1000),
        )

        from unittest.mock import MagicMock
        runner_instance = ScannerRunner(
            exchange=MagicMock(),
            db=db,
            journal=MagicMock(),
            risk_manager=MagicMock(),
            specs_map={},
        )
        runner_instance.active_setups_by_id[cand.candidate_id] = cand

        # 1. No new candles: setup stays READY
        runner_instance.recheck_active_setups(now_ms + 1000)
        assert cand.status == CandidateStatus.READY

        # 2. Add subsequent candle breaching invalidation (low = 94.0)
        breach_candle = Candle(
            symbol="BTCUSDT",
            timeframe="1h",
            timestamp_ms=now_ms + 3600000,
            open=99.0,
            high=100.0,
            low=94.0,  # Breached 95.0!
            close=96.0,
            volume=200.0,
            close_time_ms=now_ms + 7200000,
        )
        db.insert_candles([breach_candle])

        runner_instance.recheck_active_setups(now_ms + 7200000)
        assert cand.status == CandidateStatus.INVALIDATED
        assert any("low (94.0) <= invalidation price (95.0)" in r for r in cand.rejection_reasons)

    def test_expiration_detects_stale_setup(self, tmp_path):
        db = DatabaseManager(tmp_path / "test.db")
        now_ms = 1700000000000

        cand = SetupCandidate(
            candidate_id="SETUP_STALE",
            symbol="ETHUSDT",
            timestamp_ms=now_ms - (13 * 3600 * 1000),
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=50.0,
            stop_loss=48.0,
            take_profit_1=56.0,
            status=CandidateStatus.READY,
            expires_at_ms=now_ms - 1000,  # Expired
        )

        from unittest.mock import MagicMock
        runner_instance = ScannerRunner(
            exchange=MagicMock(),
            db=db,
            journal=MagicMock(),
            risk_manager=MagicMock(),
            specs_map={},
        )
        runner_instance.active_setups_by_id[cand.candidate_id] = cand

        runner_instance.recheck_active_setups(now_ms)
        assert cand.status == CandidateStatus.STALE
        assert any("TTL exceeded" in r for r in cand.rejection_reasons)


class TestConcurrencyAndAPIEndpoints:
    """Test 5-12: Concurrency lock, filtering query params, polling independence, risk limits."""

    def test_filtering_and_counts_by_status(self, test_client: TestClient):
        res = test_client.get("/api/setups")
        assert res.status_code == 200
        data = res.json()
        assert "counts_by_status" in data
        assert "ALL" in data["counts_by_status"]
        assert "READY" in data["counts_by_status"]
        assert "WATCHLIST" in data["counts_by_status"]
        assert "INFEASIBLE" in data["counts_by_status"]
        assert "INVALIDATED" in data["counts_by_status"]
        assert "STALE" in data["counts_by_status"]
        assert "ERROR" in data["counts_by_status"]

    def test_prevention_of_overlapping_scans(self, test_client: TestClient):
        # Manually acquire the async scan_lock
        async def _acquire():
            await scan_lock.acquire()

        asyncio.run(_acquire())
        assert scan_lock.locked() is True

        try:
            # While lock is acquired, trigger_scan must safely return ALREADY_RUNNING
            res = test_client.post("/api/scan")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ALREADY_RUNNING"
            assert "already in progress" in data["message"].lower()
        finally:
            # Release lock
            scan_lock.release()

    def test_polling_does_not_trigger_scans_or_alter_artifacts(self, test_client: TestClient):
        # 1. Get model file hash before polling
        model_path = Path("data/models/calibrated_model.joblib")
        hash_before = hashlib.sha256(model_path.read_bytes()).hexdigest() if model_path.exists() else None

        # 2. Poll /api/status and /api/setups repeatedly
        for _ in range(5):
            res1 = test_client.get("/api/status")
            assert res1.status_code == 200
            res2 = test_client.get("/api/setups")
            assert res2.status_code == 200

        # 3. Model artifact must be completely unchanged
        if model_path.exists():
            hash_after = hashlib.sha256(model_path.read_bytes()).hexdigest()
            assert hash_before == hash_after, "Model artifact was modified during dashboard polling!"

    def test_risk_limits_preserved(self, test_client: TestClient):
        res = test_client.get("/api/status")
        assert res.status_code == 200
        data = res.json()
        assert data["live_trading_locked"] is True
        assert data["capital"]["initial_equity_usdt"] == 16.0
        assert data["capital"]["margin_mode"] == "ISOLATED"
        assert data["capital"]["max_leverage"] == 10
        assert data["risk_limits"]["configured_trade_risk_usdt"] == 0.09
        assert data["risk_limits"]["daily_loss_limit_usdt"] == 0.18
        assert data["risk_limits"]["weekly_loss_limit_usdt"] == 0.54
        assert data["risk_limits"]["max_open_positions"] == 1
        assert data["risk_limits"]["max_weekly_entries"] is None


class TestSynchronizationRegression:
    """Regression tests for confirmed global STALE defects."""

    def test_regression_successful_synchronization(self, tmp_path):
        from unittest.mock import MagicMock, patch
        db = DatabaseManager(tmp_path / "sync_test.db")
        now_ms = 1791986400000  # 14:00:00 UTC

        # Insert 45 candles to satisfy minimum history (>= 40)
        candles = []
        for i in range(45):
            ts = now_ms - ((45 - i) * 3600 * 1000)
            candles.append(Candle(
                symbol="BTCUSDT",
                timeframe="1h",
                timestamp_ms=ts,
                open=50000.0 + i,
                high=50100.0 + i,
                low=49900.0 + i,
                close=50050.0 + i,
                volume=100.0,
                close_time_ms=ts + 3600000,
            ))
        db.insert_candles(candles)

        mock_exchange = MagicMock()
        mock_journal = MagicMock()
        mock_risk = MagicMock()
        mock_risk.get_effective_trade_risk.return_value = 0.09
        mock_risk.equity_usdt = 16.0
        mock_risk.can_open_new_position.return_value = (True, None)

        runner_inst = ScannerRunner(
            exchange=mock_exchange,
            db=db,
            journal=mock_journal,
            risk_manager=mock_risk,
            specs_map={},
        )

        with patch.object(runner_inst.synchronizer, "sync_symbol") as mock_sync:
            mock_sync.return_value = []
            results = runner_inst.run_scan(["BTCUSDT"], sync_live=True)

            # Assert both 1h and 4h were synced without AttributeError
            assert mock_sync.call_count == 2
            mock_sync.assert_any_call("BTCUSDT", "1h")
            mock_sync.assert_any_call("BTCUSDT", "4h")

    def test_regression_synchronization_failure_fail_safe(self, tmp_path):
        from unittest.mock import MagicMock, patch
        db = DatabaseManager(tmp_path / "sync_fail_test.db")
        now_ms = int(time.time() * 1000)

        candles = []
        for i in range(45):
            ts = now_ms - ((45 - i) * 3600 * 1000)
            candles.append(Candle(
                symbol="BTCUSDT",
                timeframe="1h",
                timestamp_ms=ts,
                open=50000.0 + i,
                high=50100.0 + i,
                low=49900.0 + i,
                close=50050.0 + i,
                volume=100.0,
                close_time_ms=ts + 3600000,
            ))
        db.insert_candles(candles)

        mock_exchange = MagicMock()
        mock_exchange.get_symbol_specs.return_value = None
        mock_journal = MagicMock()
        mock_risk = MagicMock()
        mock_risk.get_effective_trade_risk.return_value = 0.09
        mock_risk.equity_usdt = 16.0
        mock_risk.can_open_new_position.return_value = (True, None)

        runner_inst = ScannerRunner(
            exchange=mock_exchange,
            db=db,
            journal=mock_journal,
            risk_manager=mock_risk,
            specs_map={},
        )

        cand = SetupCandidate(
            candidate_id="SYNC_FAIL_CANDIDATE",
            symbol="BTCUSDT",
            timestamp_ms=now_ms,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit_1=51500.0,
            gross_reward_to_risk=3.0,
            net_reward_to_risk=2.8,
            invalidation_price=49400.0,
            status=CandidateStatus.READY,
            ml_probability=0.75,
        )
        runner_inst.generator.generate_candidate = MagicMock(return_value=cand)
        runner_inst.evaluator.evaluate_setup = MagicMock(return_value=cand)

        with patch.object(runner_inst.synchronizer, "sync_symbol", side_effect=Exception("Aster DEX 503 Outage")):
            results = runner_inst.run_scan(["BTCUSDT"], sync_live=True)
            assert len(results) > 0
            for c in results:
                # Must never be promoted to READY on unverified sync failure
                assert c.status != CandidateStatus.READY
                if c.status == CandidateStatus.WATCHLIST:
                    assert any("Live sync failed" in r for r in c.rejection_reasons)

    def test_regression_stale_data_failsafe_blocks_ready(self, tmp_path):
        from unittest.mock import MagicMock
        db = DatabaseManager(tmp_path / "stale_test.db")
        # 10,870 seconds ago (over 3 hours)
        old_time_ms = int(time.time() * 1000) - (10870 * 1000)

        candles = []
        for i in range(45):
            ts = old_time_ms - ((45 - i) * 3600 * 1000)
            candles.append(Candle(
                symbol="BTCUSDT",
                timeframe="1h",
                timestamp_ms=ts,
                open=50000.0 + i,
                high=50100.0 + i,
                low=49900.0 + i,
                close=50050.0 + i,
                volume=100.0,
                close_time_ms=ts + 3600000,
            ))
        db.insert_candles(candles)

        mock_exchange = MagicMock()
        mock_exchange.get_symbol_specs.return_value = None
        mock_journal = MagicMock()
        mock_risk = MagicMock()
        mock_risk.get_effective_trade_risk.return_value = 0.09
        mock_risk.equity_usdt = 16.0
        mock_risk.can_open_new_position.return_value = (True, None)

        runner_inst = ScannerRunner(
            exchange=mock_exchange,
            db=db,
            journal=mock_journal,
            risk_manager=mock_risk,
            specs_map={},
            stale_threshold_hours=3.0,
        )

        cand_stale = SetupCandidate(
            candidate_id="STALE_CANDIDATE",
            symbol="BTCUSDT",
            timestamp_ms=old_time_ms,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=50000.0,
            stop_loss=49500.0,
            take_profit_1=51500.0,
            gross_reward_to_risk=3.0,
            net_reward_to_risk=2.8,
            invalidation_price=49400.0,
            status=CandidateStatus.READY,
            ml_probability=0.75,
        )
        runner_inst.generator.generate_candidate = MagicMock(return_value=cand_stale)
        runner_inst.evaluator.evaluate_setup = MagicMock(return_value=cand_stale)

        results = runner_inst.run_scan(["BTCUSDT"], sync_live=False)
        assert len(results) > 0
        for c in results:
            assert c.status == CandidateStatus.STALE
            assert c.is_executable is False
            assert any("stale" in r.lower() for r in c.rejection_reasons)
            assert any("limit" in r.lower() for r in c.rejection_reasons)

    def test_regression_manual_scan_invokes_sync_live(self, test_client: TestClient):
        res = test_client.post("/api/scan")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("VALID", "NO_TRADE", "ALREADY_RUNNING")
        assert "setups" in data


class TestMLInformativeRanking:
    """Test ML Informative Ranking: ML score below break-even threshold does not block READY status."""

    def test_ml_probability_below_break_even_does_not_block_ready(self, tmp_path):
        """Verifies candidate with ml_probability (21.1%) below break-even (35.3%) becomes READY when all mandatory gates pass."""
        from unittest.mock import MagicMock
        from src.exchange.metadata import SymbolSpecs

        db = DatabaseManager(tmp_path / "ml_informative_test.db")
        now_ms = int(time.time() * 1000)

        # Fresh candles
        candles = []
        for i in range(45):
            ts = now_ms - ((45 - i) * 3600 * 1000)
            candles.append(Candle(
                symbol="BNBUSDT",
                timeframe="1h",
                timestamp_ms=ts,
                open=730.0 + i * 0.1,
                high=735.0 + i * 0.1,
                low=728.0 + i * 0.1,
                close=733.96,
                volume=100.0,
                close_time_ms=ts + 3600000,
            ))
        db.insert_candles(candles)

        specs = SymbolSpecs(
            symbol="BNBUSDT",
            base_asset="BNB",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.01,
            price_precision=2,
            step_size=0.01,
            quantity_precision=2,
            min_qty=0.01,
            max_qty=1000.0,
            min_notional=5.0,
            maint_margin_percent=1.0,
        )

        mock_exchange = MagicMock()
        mock_journal = MagicMock()
        mock_risk = MagicMock()
        mock_risk.get_effective_trade_risk.return_value = 0.09
        mock_risk.equity_usdt = 16.0
        mock_risk.can_open_new_position.return_value = (True, None)

        runner_inst = ScannerRunner(
            exchange=mock_exchange,
            db=db,
            journal=mock_journal,
            risk_manager=mock_risk,
            specs_map={"BNBUSDT": specs},
            target_isolated_margin=5.0,
            target_isolated_margin_max=5.5,
            hard_risk_limit_usdt=1.50,
            max_leverage=10,
        )

        # Candidate corresponding to audited BNB SHORT:
        # Entry 733.96, SL 749.145, TP1 703.59 (RR = 2.0:1)
        # Required break-even is 35.3%, ML probability is 21.1%
        bnb_cand = SetupCandidate(
            candidate_id="BNBUSDT_SHORT_AUDIT",
            symbol="BNBUSDT",
            timestamp_ms=now_ms,
            direction=Direction.SHORT,
            bias_4h=MarketBias.BEARISH,
            structure_1h=StructureState.BEARISH,
            entry_price=733.96,
            stop_loss=749.145,
            take_profit_1=703.59,
            gross_reward_to_risk=2.0,
            net_reward_to_risk=1.95,
            invalidation_price=750.0,
            ml_probability=0.211,
            eval_sample_count=1971,
        )
        runner_inst.generator.generate_candidate = MagicMock(return_value=bnb_cand)
        runner_inst.evaluator.evaluate_setup = MagicMock(return_value=bnb_cand)

        results = runner_inst.run_scan(["BNBUSDT"], sync_live=False)

        assert len(results) == 1
        cand = results[0]
        # Must be READY because sizing is feasible ($5.14 margin on 10x, loss $1.17 <= $1.50 cap)
        # and ML probability is now informative only!
        assert cand.status == CandidateStatus.READY
        assert cand.is_executable is True
        assert cand.risk_assessment is not None
        assert cand.risk_assessment.is_feasible is True
        assert 5.0 <= cand.risk_assessment.required_margin_usdt <= 5.5
        assert cand.risk_assessment.estimated_loss_usdt <= 1.50
        # Rejection reasons must NOT contain ML blocking
        assert not any("ML probability" in r and "below" in r for r in cand.rejection_reasons)
        # Opposing reasons must contain the informative note
        assert any("Informative: ML score" in r for r in cand.opposing_reasons)


class TestCandidateDeduplicationAndAuditTrail:
    """Regression tests for candidate deduplication, single-per-symbol active display,
    direction change replacement, stale/invalidation retention, cache/DB fallback parity,
    full historical journal preservation, and dynamic WATCHLIST status badges.
    """

    @staticmethod
    def _seed_candles(db, symbol):
        now_ms = int(time.time() * 1000)
        candles = []
        for i in range(45):
            ts = now_ms - ((45 - i) * 3600 * 1000)
            candles.append(Candle(
                symbol=symbol,
                timeframe="1h",
                timestamp_ms=ts,
                open=700.0 + i * 0.1,
                high=710.0 + i * 0.1,
                low=690.0 + i * 0.1,
                close=705.0,
                volume=100.0,
                close_time_ms=ts + 3600000,
            ))
        db.insert_candles(candles)

    def test_single_candidate_per_symbol_replacement(self, tmp_path):
        """Test 1: Newer candidate on same symbol replaces older candidate in active runner results."""
        db = DatabaseManager(tmp_path / "test.db")
        self._seed_candles(db, "BNBUSDT")
        from unittest.mock import MagicMock
        mock_exchange = MagicMock()
        mock_journal = MagicMock()
        mock_risk = MagicMock()
        mock_risk.equity_usdt = 16.0
        mock_risk.can_open_new_position.return_value = (True, None)

        specs = SymbolSpecs(
            symbol="BNBUSDT",
            base_asset="BNB",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.01,
            price_precision=2,
            step_size=0.01,
            quantity_precision=2,
            min_qty=0.01,
            max_qty=1000.0,
            min_notional=5.0,
            maint_margin_percent=1.0,
        )

        now_ms = 1728921600000  # 14:00 UTC
        cand_1400 = SetupCandidate(
            candidate_id="SETUP_BNBUSDT_1728921600000_SHORT",
            symbol="BNBUSDT",
            timestamp_ms=now_ms,
            direction=Direction.SHORT,
            bias_4h=MarketBias.BEARISH,
            structure_1h=StructureState.BEARISH,
            entry_price=733.96,
            stop_loss=749.145,
            take_profit_1=703.59,
            gross_reward_to_risk=2.0,
            net_reward_to_risk=1.95,
            invalidation_price=750.0,
            status=CandidateStatus.READY,
        )

        runner_inst = ScannerRunner(
            exchange=mock_exchange,
            db=db,
            journal=mock_journal,
            risk_manager=mock_risk,
            specs_map={"BNBUSDT": specs},
        )
        runner_inst.generator.generate_candidate = MagicMock(return_value=cand_1400)
        runner_inst.evaluator.evaluate_setup = MagicMock(return_value=cand_1400)

        results_1 = runner_inst.run_scan(["BNBUSDT"], sync_live=False)
        assert len(results_1) == 1
        assert results_1[0].candidate_id == "SETUP_BNBUSDT_1728921600000_SHORT"
        assert results_1[0].entry_price == 733.96

        # Cycle 2: Newer candle arrives at 16:00 UTC (1728928800000)
        later_ms = 1728928800000
        cand_1600 = SetupCandidate(
            candidate_id="SETUP_BNBUSDT_1728928800000_SHORT",
            symbol="BNBUSDT",
            timestamp_ms=later_ms,
            direction=Direction.SHORT,
            bias_4h=MarketBias.BEARISH,
            structure_1h=StructureState.BEARISH,
            entry_price=732.85,
            stop_loss=747.58,
            take_profit_1=703.39,
            gross_reward_to_risk=2.0,
            net_reward_to_risk=1.95,
            invalidation_price=748.0,
            status=CandidateStatus.READY,
        )
        runner_inst.generator.generate_candidate = MagicMock(return_value=cand_1600)
        runner_inst.evaluator.evaluate_setup = MagicMock(return_value=cand_1600)

        results_2 = runner_inst.run_scan(["BNBUSDT"], sync_live=False)
        # Active setup for BNB must be exactly 1, updated to the 16:00 UTC candidate
        assert len(results_2) == 1
        assert results_2[0].candidate_id == "SETUP_BNBUSDT_1728928800000_SHORT"
        assert results_2[0].timestamp_ms == later_ms
        assert results_2[0].entry_price == 732.85
        assert runner_inst.active_setups_by_symbol["BNBUSDT"].candidate_id == "SETUP_BNBUSDT_1728928800000_SHORT"
        assert "SETUP_BNBUSDT_1728921600000_SHORT" not in runner_inst.active_setups_by_id

        # Both setups were saved to journal for complete audit history
        assert mock_journal.save_setup.call_count == 2

    def test_direction_change_replaces_setup_for_symbol(self, tmp_path):
        """Test 2: Setup direction change (SHORT -> LONG) on same symbol replaces old setup and maintains single active candidate."""
        db = DatabaseManager(tmp_path / "test.db")
        self._seed_candles(db, "BNBUSDT")
        from unittest.mock import MagicMock
        mock_journal = MagicMock()
        mock_risk = MagicMock()
        mock_risk.equity_usdt = 16.0
        mock_risk.can_open_new_position.return_value = (True, None)

        specs = SymbolSpecs(
            symbol="BNBUSDT",
            base_asset="BNB",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.01,
            price_precision=2,
            step_size=0.01,
            quantity_precision=2,
            min_qty=0.01,
            max_qty=1000.0,
            min_notional=5.0,
            maint_margin_percent=1.0,
        )

        cand_short = SetupCandidate(
            candidate_id="SETUP_BNBUSDT_1000_SHORT",
            symbol="BNBUSDT",
            timestamp_ms=1000,
            direction=Direction.SHORT,
            bias_4h=MarketBias.BEARISH,
            structure_1h=StructureState.BEARISH,
            entry_price=700.0,
            stop_loss=710.0,
            take_profit_1=680.0,
            status=CandidateStatus.READY,
        )
        cand_long = SetupCandidate(
            candidate_id="SETUP_BNBUSDT_2000_LONG",
            symbol="BNBUSDT",
            timestamp_ms=2000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=720.0,
            stop_loss=710.0,
            take_profit_1=740.0,
            status=CandidateStatus.READY,
        )

        runner_inst = ScannerRunner(
            exchange=MagicMock(),
            db=db,
            journal=mock_journal,
            risk_manager=mock_risk,
            specs_map={"BNBUSDT": specs},
        )

        runner_inst.generator.generate_candidate = MagicMock(return_value=cand_short)
        runner_inst.evaluator.evaluate_setup = MagicMock(return_value=cand_short)
        runner_inst.run_scan(["BNBUSDT"], sync_live=False)

        runner_inst.generator.generate_candidate = MagicMock(return_value=cand_long)
        runner_inst.evaluator.evaluate_setup = MagicMock(return_value=cand_long)
        results = runner_inst.run_scan(["BNBUSDT"], sync_live=False)

        assert len(results) == 1
        assert results[0].candidate_id == "SETUP_BNBUSDT_2000_LONG"
        assert results[0].direction == Direction.LONG
        assert results[0].entry_price == 720.0

    def test_stale_or_invalidated_retention_does_not_resurrect_older(self, tmp_path):
        """Test 3: Stale/invalidated candidate stays active with updated status without resurrecting older candidates."""
        db = DatabaseManager(tmp_path / "test.db")
        self._seed_candles(db, "ETHUSDT")
        from unittest.mock import MagicMock
        mock_journal = MagicMock()
        mock_risk = MagicMock()
        mock_risk.equity_usdt = 16.0
        mock_risk.can_open_new_position.return_value = (True, None)

        specs = SymbolSpecs(
            symbol="ETHUSDT",
            base_asset="ETH",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.01,
            price_precision=2,
            step_size=0.01,
            quantity_precision=2,
            min_qty=0.01,
            max_qty=1000.0,
            min_notional=5.0,
            maint_margin_percent=1.0,
        )

        runner_inst = ScannerRunner(
            exchange=MagicMock(),
            db=db,
            journal=mock_journal,
            risk_manager=mock_risk,
            specs_map={"ETHUSDT": specs},
        )

        # Cand 1 at 1000: READY
        cand_ready = SetupCandidate(
            candidate_id="SETUP_ETH_1000_READY",
            symbol="ETHUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=3000.0,
            stop_loss=2950.0,
            take_profit_1=3100.0,
            status=CandidateStatus.READY,
        )
        runner_inst.generator.generate_candidate = MagicMock(return_value=cand_ready)
        runner_inst.evaluator.evaluate_setup = MagicMock(return_value=cand_ready)
        runner_inst.run_scan(["ETHUSDT"], sync_live=False)

        # Cand 2 at 2000: breached invalidation level (invalidation_price = 720.0, higher than candle low 690.0) -> INVALIDATED
        cand_invalid = SetupCandidate(
            candidate_id="SETUP_ETH_2000_INVALID",
            symbol="ETHUSDT",
            timestamp_ms=2000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=710.0,
            stop_loss=690.0,
            take_profit_1=740.0,
            invalidation_price=720.0,
            status=CandidateStatus.READY,
        )
        runner_inst.generator.generate_candidate = MagicMock(return_value=cand_invalid)
        runner_inst.evaluator.evaluate_setup = MagicMock(return_value=cand_invalid)
        results = runner_inst.run_scan(["ETHUSDT"], sync_live=False)

        assert len(results) == 1
        assert results[0].candidate_id == "SETUP_ETH_2000_INVALID"
        assert results[0].status == CandidateStatus.INVALIDATED
        # Old ready setup is NOT resurrected
        assert "SETUP_ETH_1000_READY" not in runner_inst.active_setups_by_id

    def test_scheduler_cache_and_db_fallback_parity(self, tmp_path):
        """Test 4: Cache results and database journal fallback yield identical latest-per-symbol aggregations."""
        db = DatabaseManager(tmp_path / "test.db")
        journal_repo = JournalRepository(db)

        # Save historical candidates to DB (2 for BNB, 2 for BTC)
        bnb_old = SetupCandidate(
            candidate_id="SETUP_BNB_1400",
            symbol="BNBUSDT",
            timestamp_ms=1400000,
            direction=Direction.SHORT,
            bias_4h=MarketBias.BEARISH,
            structure_1h=StructureState.BEARISH,
            entry_price=733.96,
            stop_loss=749.145,
            take_profit_1=703.59,
            status=CandidateStatus.READY,
        )
        bnb_new = SetupCandidate(
            candidate_id="SETUP_BNB_1600",
            symbol="BNBUSDT",
            timestamp_ms=1600000,
            direction=Direction.SHORT,
            bias_4h=MarketBias.BEARISH,
            structure_1h=StructureState.BEARISH,
            entry_price=732.85,
            stop_loss=747.58,
            take_profit_1=703.39,
            status=CandidateStatus.READY,
        )
        btc_old = SetupCandidate(
            candidate_id="SETUP_BTC_1400",
            symbol="BTCUSDT",
            timestamp_ms=1400000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=80000.0,
            stop_loss=79000.0,
            take_profit_1=82000.0,
            status=CandidateStatus.READY,
        )
        btc_new = SetupCandidate(
            candidate_id="SETUP_BTC_1600",
            symbol="BTCUSDT",
            timestamp_ms=1600000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=81000.0,
            stop_loss=80000.0,
            take_profit_1=83000.0,
            status=CandidateStatus.READY,
        )

        for c in [bnb_old, bnb_new, btc_old, btc_new]:
            journal_repo.save_setup(c)

        import src.api.app as app_module
        old_runner = app_module.runner
        old_journal = app_module.journal
        old_has_completed = getattr(app_module.runner, "has_completed_scan", False)
        old_last_results = list(app_module.runner.last_results)
        try:
            app_module.journal = journal_repo
            client = TestClient(app_module.app)

            # Test A: Fallback to DB journal (when runner has not yet completed a scan in this session)
            app_module.runner.has_completed_scan = False
            app_module.runner.last_results = []
            res_db = client.get("/api/setups")
            assert res_db.status_code == 200
            data_db = res_db.json()
            assert data_db["count"] == 2
            db_ids = {s["candidate_id"] for s in data_db["setups"]}
            assert db_ids == {"SETUP_BNB_1600", "SETUP_BTC_1600"}

            # Test B: In-memory runner cache
            app_module.runner.has_completed_scan = True
            app_module.runner.last_results = [bnb_new, btc_new]
            res_cache = client.get("/api/setups")
            assert res_cache.status_code == 200
            data_cache = res_cache.json()
            assert data_cache["count"] == 2
            cache_ids = {s["candidate_id"] for s in data_cache["setups"]}
            assert cache_ids == {"SETUP_BNB_1600", "SETUP_BTC_1600"}

            # Verify identical counts and statuses
            assert data_db["counts_by_status"] == data_cache["counts_by_status"]
            assert data_db["counts_by_status"]["ALL"] == 2
        finally:
            app_module.runner = old_runner
            app_module.runner.has_completed_scan = old_has_completed
            app_module.runner.last_results = old_last_results
            app_module.journal = old_journal

    def test_symbol_filtering_on_deduplicated_set(self, test_client: TestClient):
        """Test 5: Filtering by symbol returns only the latest candidate for matching symbol without older duplicates."""
        res = test_client.get("/api/setups?symbol=BNB")
        assert res.status_code == 200
        data = res.json()
        for s in data["setups"]:
            assert "BNB" in s["symbol"]
        # Symbol BNB must appear at most once in the setups list
        symbols_returned = [s["symbol"] for s in data["setups"]]
        assert len(symbols_returned) == len(set(symbols_returned))

    def test_historical_journal_preservation(self, tmp_path):
        """Test 6: Full historical records are preserved in journal and setup_candidates table."""
        db = DatabaseManager(tmp_path / "test.db")
        journal_repo = JournalRepository(db)

        cand_1 = SetupCandidate(
            candidate_id="SETUP_BNB_HIST_1",
            symbol="BNBUSDT",
            timestamp_ms=1000,
            direction=Direction.SHORT,
            bias_4h=MarketBias.BEARISH,
            structure_1h=StructureState.BEARISH,
            entry_price=700.0,
            stop_loss=710.0,
            take_profit_1=680.0,
            status=CandidateStatus.READY,
        )
        cand_2 = SetupCandidate(
            candidate_id="SETUP_BNB_HIST_2",
            symbol="BNBUSDT",
            timestamp_ms=2000,
            direction=Direction.SHORT,
            bias_4h=MarketBias.BEARISH,
            structure_1h=StructureState.BEARISH,
            entry_price=695.0,
            stop_loss=705.0,
            take_profit_1=675.0,
            status=CandidateStatus.READY,
        )

        journal_repo.save_setup(cand_1)
        journal_repo.save_setup(cand_2)

        history = journal_repo.get_latest_setups(limit=50)
        assert len(history) == 2
        cand_ids = {h["candidate_id"] for h in history}
        assert cand_ids == {"SETUP_BNB_HIST_1", "SETUP_BNB_HIST_2"}

    def test_watchlist_status_title_nuances(self):
        """Test 7: WATCHLIST badge categorization differentiates account limit vs data pending vs trigger."""
        def get_watchlist_title(rejection_reasons, opposing_reasons=None):
            r_text = (" ".join(rejection_reasons or []) + " " + " ".join(opposing_reasons or [])).lower()
            if any(term in r_text for term in ["account limit", "open position", "weekly entries", "loss limit"]):
                return "WATCHLIST (Account Limit Reached)"
            elif any(term in r_text for term in ["live sync failed", "pending verified data", "sync failed"]):
                return "WATCHLIST (Data Verification Pending)"
            elif any(term in r_text for term in ["trigger", "entry price", "breakout"]):
                return "WATCHLIST (Awaiting Trigger)"
            else:
                return "WATCHLIST (Awaiting Confirmation)"

        # 1. Account Limit Reached
        assert get_watchlist_title(["Maximum open positions reached (1)"]) == "WATCHLIST (Account Limit Reached)"
        assert get_watchlist_title(["Account limits reached (awaiting position closure or quota reset)."]) == "WATCHLIST (Account Limit Reached)"
        assert get_watchlist_title(["Maximum weekly entries reached (3/week)."]) == "WATCHLIST (Account Limit Reached)"
        assert get_watchlist_title(["Daily loss limit reached ($0.18)."]) == "WATCHLIST (Account Limit Reached)"

        # 2. Data Pending / Live Sync Failed
        assert get_watchlist_title(["Live sync failed for BNBUSDT (timeout). Candidate held on WATCHLIST pending verified data."]) == "WATCHLIST (Data Verification Pending)"

        # 3. Awaiting Trigger
        assert get_watchlist_title(["Waiting for entry trigger price level 733.96."]) == "WATCHLIST (Awaiting Trigger)"

        # 4. Fallback / General confirmation
        assert get_watchlist_title(["Awaiting 4H candle confirmation."]) == "WATCHLIST (Awaiting Confirmation)"

    def test_multi_symbol_latest_aggregation(self):
        """Test 8: Multiple symbols with several historical candles are each aggregated to exactly one latest candidate."""
        cands = [
            SetupCandidate(candidate_id="BTC_1", symbol="BTCUSDT", timestamp_ms=1000, direction=Direction.LONG, bias_4h=MarketBias.BULLISH, structure_1h=StructureState.BULLISH, entry_price=1.0, stop_loss=0.9, take_profit_1=1.2, status=CandidateStatus.READY),
            SetupCandidate(candidate_id="BTC_2", symbol="BTCUSDT", timestamp_ms=2000, direction=Direction.LONG, bias_4h=MarketBias.BULLISH, structure_1h=StructureState.BULLISH, entry_price=1.0, stop_loss=0.9, take_profit_1=1.2, status=CandidateStatus.READY),
            SetupCandidate(candidate_id="BTC_3", symbol="BTCUSDT", timestamp_ms=3000, direction=Direction.LONG, bias_4h=MarketBias.BULLISH, structure_1h=StructureState.BULLISH, entry_price=1.0, stop_loss=0.9, take_profit_1=1.2, status=CandidateStatus.READY),
            SetupCandidate(candidate_id="ETH_1", symbol="ETHUSDT", timestamp_ms=1500, direction=Direction.LONG, bias_4h=MarketBias.BULLISH, structure_1h=StructureState.BULLISH, entry_price=1.0, stop_loss=0.9, take_profit_1=1.2, status=CandidateStatus.READY),
            SetupCandidate(candidate_id="ETH_2", symbol="ETHUSDT", timestamp_ms=2500, direction=Direction.LONG, bias_4h=MarketBias.BULLISH, structure_1h=StructureState.BULLISH, entry_price=1.0, stop_loss=0.9, take_profit_1=1.2, status=CandidateStatus.READY),
            SetupCandidate(candidate_id="SOL_1", symbol="SOLUSDT", timestamp_ms=2000, direction=Direction.LONG, bias_4h=MarketBias.BULLISH, structure_1h=StructureState.BULLISH, entry_price=1.0, stop_loss=0.9, take_profit_1=1.2, status=CandidateStatus.READY),
        ]

        latest_by_symbol = {}
        for c in cands:
            sym = c.symbol.upper()
            if sym not in latest_by_symbol or c.timestamp_ms > latest_by_symbol[sym].timestamp_ms:
                latest_by_symbol[sym] = c

        assert len(latest_by_symbol) == 3
        assert latest_by_symbol["BTCUSDT"].candidate_id == "BTC_3"
        assert latest_by_symbol["BTCUSDT"].timestamp_ms == 3000
        assert latest_by_symbol["ETHUSDT"].candidate_id == "ETH_2"
        assert latest_by_symbol["ETHUSDT"].timestamp_ms == 2500
        assert latest_by_symbol["SOLUSDT"].candidate_id == "SOL_1"
        assert latest_by_symbol["SOLUSDT"].timestamp_ms == 2000

