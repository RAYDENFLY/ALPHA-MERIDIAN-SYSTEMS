"""End-to-End Integration Tests for Scanner, ML Filter, Risk Sizing, and Backtest Engine.
"""

from pathlib import Path
import pytest
from config.settings import get_default_config
from src.backtest.engine import BacktestEngine
from src.core.types import CandidateStatus, Direction
from src.data.database import DatabaseManager
from src.exchange.aster import AsterDexAdapter
from src.journal.repository import JournalRepository
from src.ml.models import ModelRegistry
from src.risk.manager import RiskManager
from src.scheduler.runner import ScannerRunner


class TestFullIntegration:
    @pytest.fixture(autouse=True)
    def setup_system(self):
        self.cfg = get_default_config()
        self.db = DatabaseManager(self.cfg.db_path)
        self.exchange = AsterDexAdapter()
        self.journal = JournalRepository(self.db)
        self.risk_mgr = RiskManager(
            initial_equity_usdt=16.0,
            trade_risk_usdt=0.09,
            daily_loss_limit_usdt=0.18,
            weekly_loss_limit_usdt=0.54,
            max_open_positions=1,
            max_entries_per_week=3,
        )
        self.specs_map = self.exchange.get_exchange_info()
        self.runner = ScannerRunner(
            exchange=self.exchange,
            db=self.db,
            journal=self.journal,
            risk_manager=self.risk_mgr,
            specs_map=self.specs_map,
            models_dir=self.cfg.data_dir / "models",
        )

    def test_scanner_run_end_to_end(self):
        # Run scan across ADAUSDT and BTCUSDT
        symbols = ["ADAUSDT", "BTCUSDT"]
        candidates = self.runner.run_scan(symbols=symbols, sync_live=False)

        assert self.runner.last_scan_utc is not None

        for cand in candidates:
            # Check price level consistency
            if cand.direction == Direction.LONG:
                assert cand.stop_loss < cand.entry_price < cand.take_profit_1
                assert cand.invalidation_price < cand.entry_price
            elif cand.direction == Direction.SHORT:
                assert cand.take_profit_1 < cand.entry_price < cand.stop_loss
                assert cand.invalidation_price > cand.entry_price

            assert cand.gross_reward_to_risk >= 1.5
            assert cand.invalidation_reason != ""
            assert len(cand.smc_evidence) > 0 or len(cand.snr_evidence) > 0

            # Sizing and Risk verification
            if cand.risk_assessment is not None:
                assert cand.risk_assessment.risk_budget_usdt <= 0.091
                if cand.status == CandidateStatus.VALID:
                    assert cand.risk_assessment.is_feasible is True
                    assert cand.risk_assessment.position_notional_usdt >= 5.0
                    assert cand.risk_assessment.required_margin_usdt <= 16.0
                elif cand.status == CandidateStatus.INFEASIBLE:
                    assert cand.risk_assessment.is_feasible is False

    def test_backtest_engine_comparison(self):
        symbols = ["ADAUSDT"]
        reg = ModelRegistry(self.cfg.data_dir / "models")
        model_bundle = reg.load_model()
        ml_model = model_bundle if model_bundle else None

        engine = BacktestEngine(
            db=self.db,
            symbol_specs_map=self.specs_map,
            initial_equity=16.0,
            trade_risk=0.09,
            daily_loss_limit=0.18,
            weekly_loss_limit=0.54,
        )

        # 1. Deterministic run
        report_det = engine.run_backtest(symbols=symbols)
        assert report_det.initial_equity == 16.0
        assert isinstance(report_det.total_trades, int)

        # 2. ML-filtered run
        report_ml = engine.run_backtest(symbols=symbols, ml_model=ml_model, min_ml_confidence=0.50)
        assert report_ml.initial_equity == 16.0
        assert report_ml.total_trades <= report_det.total_trades  # ML filters out candidates
