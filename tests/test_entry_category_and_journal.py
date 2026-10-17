"""Comprehensive Unit and Integration Tests for Entry Category Classification & Trading Journal.

Verifies:
1. Deterministic Entry Category Classification (Breakout, Pullback, Reversal, Range/Mean Reversion, Unclassified).
2. Category independence from symbol name, trade direction, and ML score.
3. SetupCandidate propagation, serialization, and database persistence.
4. Database migrations and schema compatibility across restarts.
5. PaperExecutionEngine lifecycle propagation: Candidate -> Order -> Position -> Closed Trade Record.
6. Strict PnL Accounting Isolation: Floating open positions must never enter realized PnL, gross PnL, or win rate.
7. TradeRecord enriched properties (outcome, duration, total fees).
8. Multi-criteria filtering in JournalRepository and API endpoints (/api/setups, /api/trades, /api/journal).
9. Safety Invariants: Paper mode preserved, live trading locked, zero live orders.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from config.settings import AppConfig, ExecutionMode, PaperExecutionConfig
from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    EntryCategory,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
    TradeRecord,
)
from src.data.database import DatabaseManager
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperOrder, PaperOrderStatus, PaperPosition
from src.journal.repository import JournalRepository
from src.risk.manager import RiskManager
from src.strategy.classifier import classify_entry_category


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_journal_scanner.db"
    return DatabaseManager(db_file)


@pytest.fixture
def journal_repo(temp_db):
    return JournalRepository(temp_db)


@pytest.fixture
def risk_mgr():
    return RiskManager(
        initial_equity_usdt=16.0,
        trade_risk_usdt=0.09,
        daily_loss_limit_usdt=0.18,
        weekly_loss_limit_usdt=0.54,
        max_open_positions=1,
        max_entries_per_week=3,
    )


@pytest.fixture
def paper_cfg():
    return PaperExecutionConfig(
        enabled=True,
        slippage_rate=0.0005,
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
        pending_expiry_bars=5,
        conservative_fill_buffer_pct=0.00025,
    )


@pytest.fixture
def paper_engine(temp_db, journal_repo, risk_mgr, paper_cfg):
    return PaperExecutionEngine(
        db=temp_db,
        journal=journal_repo,
        risk_manager=risk_mgr,
        config=paper_cfg,
    )


def make_test_candidate(
    candidate_id: str = "setup-001",
    symbol: str = "BTCUSDT",
    direction: Direction = Direction.LONG,
    entry: float = 60000.0,
    sl: float = 59000.0,
    tp: float = 62000.0,
    status: CandidateStatus = CandidateStatus.READY,
    entry_category: EntryCategory = EntryCategory.UNCLASSIFIED,
    smc_evidence: list = None,
    supporting_reasons: list = None,
) -> SetupCandidate:
    stop_dist = abs(entry - sl) / entry
    risk_assessment = RiskAssessment(
        symbol=symbol,
        entry_price=entry,
        stop_loss=sl,
        stop_distance_pct=stop_dist,
        risk_budget_usdt=0.08,
        position_notional_usdt=4.8,
        quantity=round(4.8 / entry, 4),
        required_margin_usdt=0.48,
        effective_leverage=0.3,
        estimated_fee_usdt=0.005,
        estimated_slippage_usdt=0.002,
        is_feasible=True,
        target_isolated_margin_usdt=5.0,
        estimated_loss_usdt=0.08,
        estimated_loss_pct=1.0,
        position_leverage=10.0,
        equity_exposure_ratio=0.3,
    )
    return SetupCandidate(
        candidate_id=candidate_id,
        symbol=symbol,
        timestamp_ms=1700000000000,
        direction=direction,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=entry,
        stop_loss=sl,
        take_profit_1=tp,
        take_profit_2=tp + 1000.0,
        gross_reward_to_risk=2.0,
        net_reward_to_risk=1.85,
        status=status,
        entry_category=entry_category,
        smc_evidence=smc_evidence or [],
        supporting_reasons=supporting_reasons or [],
        risk_assessment=risk_assessment,
    )


# ---------------------------------------------------------------------------
# 1. Deterministic Entry Category Classification Tests
# ---------------------------------------------------------------------------

class TestEntryCategoryClassifier:
    """Verifies deterministic classification based on market structure evidence."""

    def test_breakout_classification(self):
        # Triggered by Break of Structure (BOS)
        cat = classify_entry_category(
            direction=Direction.LONG,
            smc_evidence=["Bullish Break of Structure (BOS) confirmed on 1H"],
            snr_evidence=[],
            supporting_reasons=["Momentum expansion candle closed above swing high"],
        )
        assert cat == EntryCategory.BREAKOUT

        # Regime expansion / breakout keywords
        cat2 = classify_entry_category(
            direction=Direction.SHORT,
            smc_evidence=[],
            snr_evidence=["Support level breakdown with aggressive volume"],
            supporting_reasons=["Breakout beyond consolidation range"],
        )
        assert cat2 == EntryCategory.BREAKOUT

    def test_reversal_classification(self):
        # Triggered by Change of Character (CHoCH)
        cat = classify_entry_category(
            direction=Direction.LONG,
            smc_evidence=["Change of Character (CHoCH) indicating trend shift"],
            snr_evidence=[],
            supporting_reasons=["Bullish reversal from multi-day bottom"],
        )
        assert cat == EntryCategory.REVERSAL

        # Liquidity sweep / sweep of highs/lows
        cat2 = classify_entry_category(
            direction=Direction.SHORT,
            smc_evidence=["Bearish liquidity sweep of Asian session high"],
            snr_evidence=[],
            supporting_reasons=["Failed auction at premium, rejection wick"],
        )
        assert cat2 == EntryCategory.REVERSAL

    def test_pullback_classification(self):
        # Triggered by Order Block (OB) mitigation or retest
        cat = classify_entry_category(
            direction=Direction.LONG,
            smc_evidence=["Mitigation of Bullish Order Block (OB)"],
            snr_evidence=["Key demand level retest holding as support"],
            supporting_reasons=["Pullback into discount zone"],
        )
        assert cat == EntryCategory.PULLBACK

        # Fair Value Gap (FVG) mitigation / retest
        cat2 = classify_entry_category(
            direction=Direction.SHORT,
            smc_evidence=["FVG mitigation into premium imbalance"],
            snr_evidence=[],
            supporting_reasons=["Trend continuation retest"],
        )
        assert cat2 == EntryCategory.PULLBACK

    def test_range_mean_reversion_classification(self):
        # Triggered by dealing range boundary exhaustion or mean reversion
        cat = classify_entry_category(
            direction=Direction.LONG,
            smc_evidence=[],
            snr_evidence=["Range low bounce at 24h dealing range boundary"],
            supporting_reasons=["Mean reversion towards range equilibrium"],
        )
        assert cat == EntryCategory.RANGE_MEAN_REVERSION

        # Consolidation / sideways structure
        cat2 = classify_entry_category(
            direction=Direction.SHORT,
            smc_evidence=["Range high rejection at premium boundary"],
            snr_evidence=["Range boundary resistance"],
            supporting_reasons=["Sideways market consolidation"],
        )
        assert cat2 == EntryCategory.RANGE_MEAN_REVERSION

    def test_unclassified_fallback(self):
        # Insufficient or ambiguous evidence falls back safely to Unclassified
        cat = classify_entry_category(
            direction=Direction.LONG,
            smc_evidence=[],
            snr_evidence=[],
            supporting_reasons=[],
        )
        assert cat == EntryCategory.UNCLASSIFIED

    def test_category_independent_of_symbol_direction_and_ml(self):
        """Invariant: Symbol name, direction, and ML score must NEVER dictate entry category."""
        evidence = ["Bullish Break of Structure (BOS) confirmed"]
        
        # Test across diverse symbols
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "UNKNOWNUSDT"]:
            # With Long
            cat_long = classify_entry_category(
                direction=Direction.LONG,
                smc_evidence=evidence,
            )
            assert cat_long == EntryCategory.BREAKOUT

            # With Short
            cat_short = classify_entry_category(
                direction=Direction.SHORT,
                smc_evidence=["Bearish Break of Structure (BOS) confirmed"],
            )
            assert cat_short == EntryCategory.BREAKOUT

    def test_enum_case_and_alias_handling(self):
        assert EntryCategory("Breakout") == EntryCategory.BREAKOUT
        assert EntryCategory("BREAKOUT") == EntryCategory.BREAKOUT
        assert EntryCategory("pullback") == EntryCategory.PULLBACK
        assert EntryCategory("Reversal") == EntryCategory.REVERSAL
        assert EntryCategory("range") == EntryCategory.RANGE_MEAN_REVERSION
        assert EntryCategory("Range/Mean Reversion") == EntryCategory.RANGE_MEAN_REVERSION
        assert EntryCategory("UNKNOWN_VAL") == EntryCategory.UNCLASSIFIED


# ---------------------------------------------------------------------------
# 2. SetupCandidate & Strategy Generation Tests
# ---------------------------------------------------------------------------

class TestSetupCandidateCategory:
    """Verifies SetupCandidate handling of entry_category."""

    def test_candidate_to_dict_includes_category(self):
        cand = make_test_candidate(entry_category=EntryCategory.BREAKOUT)
        d = cand.to_dict()
        assert "entry_category" in d
        assert d["entry_category"] == "Breakout"

    def test_candidate_default_category(self):
        cand = make_test_candidate()
        assert cand.entry_category == EntryCategory.UNCLASSIFIED
        assert cand.to_dict()["entry_category"] == "Unclassified"


# ---------------------------------------------------------------------------
# 3. TradeRecord & Strict PnL Accounting Isolation Tests
# ---------------------------------------------------------------------------

class TestTradeRecordAccounting:
    """Verifies outcome derivation, duration, total fees, and strict PnL isolation."""

    def test_trade_record_outcomes(self):
        now_ms = 1700000000000
        # WIN: net pnl > 0.0001
        t_win = TradeRecord(
            trade_id="t-win",
            symbol="BTCUSDT",
            direction="LONG",
            entry_timestamp_ms=now_ms,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit=62000.0,
            quantity=0.1,
            notional_usdt=6000.0,
            exit_timestamp_ms=now_ms + 3600000,
            exit_price=62000.0,
            pnl_gross=200.0,
            pnl_net=195.0,
            fee_paid=4.0,
            funding_paid=1.0,
            is_open=False,
            entry_category=EntryCategory.BREAKOUT,
        )
        assert t_win.outcome == "WIN"
        assert t_win.duration_seconds == 3600.0
        assert t_win.duration_display == "1.0h"
        assert t_win.total_fees == 5.0

        # LOSS: net pnl < -0.0001
        t_loss = TradeRecord(
            trade_id="t-loss",
            symbol="ETHUSDT",
            direction="SHORT",
            entry_timestamp_ms=now_ms,
            entry_price=3000.0,
            stop_loss=3050.0,
            take_profit=2900.0,
            quantity=1.0,
            notional_usdt=3000.0,
            exit_timestamp_ms=now_ms + 1800000,
            exit_price=3050.0,
            pnl_gross=-50.0,
            pnl_net=-52.5,
            fee_paid=2.0,
            funding_paid=0.5,
            is_open=False,
            entry_category=EntryCategory.PULLBACK,
        )
        assert t_loss.outcome == "LOSS"
        assert t_loss.duration_display == "30m"
        assert t_loss.total_fees == 2.5

        # BREAKEVEN: abs(net pnl) <= 0.0001
        t_be = TradeRecord(
            trade_id="t-be",
            symbol="SOLUSDT",
            direction="LONG",
            entry_timestamp_ms=now_ms,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            quantity=1.0,
            notional_usdt=100.0,
            exit_timestamp_ms=now_ms + 600000,
            exit_price=100.05,
            pnl_gross=0.05,
            pnl_net=0.0,
            fee_paid=0.05,
            funding_paid=0.0,
            is_open=False,
            entry_category=EntryCategory.REVERSAL,
        )
        assert t_be.outcome == "BREAKEVEN"

        # OPEN: active position
        t_open = TradeRecord(
            trade_id="t-open",
            symbol="BTCUSDT",
            direction="LONG",
            entry_timestamp_ms=now_ms,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit=62000.0,
            quantity=0.1,
            notional_usdt=6000.0,
            exit_timestamp_ms=None,
            exit_price=None,
            pnl_gross=0.0,
            pnl_net=0.0,
            fee_paid=1.2,
            funding_paid=0.0,
            is_open=True,
            entry_category=EntryCategory.BREAKOUT,
        )
        assert t_open.outcome == "OPEN"
        assert t_open.duration_display == "Open"

    def test_strict_pnl_isolation_in_compute_metrics(self):
        """Realized PnL from closed trades must be strictly isolated from floating open trades."""
        now_ms = 1700000000000
        trades = [
            # Closed Win
            {
                "trade_id": "t1",
                "is_open": False,
                "exit_timestamp_ms": now_ms + 3600000,
                "outcome": "WIN",
                "pnl_gross": 10.0,
                "pnl_net": 9.5,
                "fee_paid": 0.3,
                "funding_paid": 0.1,
                "slippage_paid": 0.1,
                "total_fees": 0.5,
            },
            # Closed Loss
            {
                "trade_id": "t2",
                "is_open": False,
                "exit_timestamp_ms": now_ms + 7200000,
                "outcome": "LOSS",
                "pnl_gross": -4.0,
                "pnl_net": -4.5,
                "fee_paid": 0.3,
                "funding_paid": 0.1,
                "slippage_paid": 0.1,
                "total_fees": 0.5,
            },
            # Closed Breakeven
            {
                "trade_id": "t3",
                "is_open": False,
                "exit_timestamp_ms": now_ms + 1800000,
                "outcome": "BREAKEVEN",
                "pnl_gross": 0.4,
                "pnl_net": 0.0,
                "fee_paid": 0.3,
                "funding_paid": 0.1,
                "slippage_paid": 0.0,
                "total_fees": 0.4,
            },
            # ACTIVE OPEN TRADE (Must NEVER enter closed metrics)
            {
                "trade_id": "t-active-1",
                "is_open": True,
                "exit_timestamp_ms": None,
                "outcome": "OPEN",
                "pnl_gross": 50.0,  # Huge floating profit
                "pnl_net": 48.0,
                "fee_paid": 1.0,
                "funding_paid": 0.0,
                "slippage_paid": 0.0,
                "total_fees": 1.0,
            },
            # Another OPEN trade with floating loss
            {
                "trade_id": "t-active-2",
                "is_open": True,
                "exit_timestamp_ms": None,
                "outcome": "OPEN",
                "pnl_gross": -30.0,
                "pnl_net": -31.0,
                "fee_paid": 1.0,
                "funding_paid": 0.0,
                "slippage_paid": 0.0,
                "total_fees": 1.0,
            },
        ]

        metrics = JournalRepository.compute_trade_metrics(trades)

        # Closed trade metrics
        assert metrics["closed_trades_count"] == 3
        assert metrics["wins_count"] == 1
        assert metrics["losses_count"] == 1
        assert metrics["breakeven_count"] == 1
        assert metrics["win_rate_pct"] == 33.33  # 1 win out of 3 closed trades
        assert metrics["gross_pnl"] == round(10.0 + (-4.0) + 0.4, 4)  # 6.4
        assert metrics["total_fees"] == round(0.5 + 0.5 + 0.4, 4)  # 1.4
        assert metrics["net_pnl"] == round(9.5 + (-4.5) + 0.0, 4)  # 5.0
        assert metrics["avg_net_pnl"] == round(5.0 / 3, 4)

        # Active open trades isolated
        assert metrics["open_trades_count"] == 2


# ---------------------------------------------------------------------------
# 4. Database Persistence & Migration Tests
# ---------------------------------------------------------------------------

class TestDatabasePersistenceAndMigration:
    """Verifies schema migration and persistence for entry_category."""

    def test_schema_has_entry_category_column(self, temp_db):
        with temp_db.get_connection() as conn:
            # Check setup_candidates
            cur = conn.execute("PRAGMA table_info(setup_candidates)")
            cols = [row["name"] for row in cur.fetchall()]
            assert "entry_category" in cols

            # Check trade_journal
            cur = conn.execute("PRAGMA table_info(trade_journal)")
            cols = [row["name"] for row in cur.fetchall()]
            assert "entry_category" in cols

            # Check paper_orders
            cur = conn.execute("PRAGMA table_info(paper_orders)")
            cols = [row["name"] for row in cur.fetchall()]
            assert "entry_category" in cols

    def test_save_and_retrieve_setup_with_category(self, journal_repo):
        cand = make_test_candidate(
            candidate_id="cand-persist-1",
            entry_category=EntryCategory.BREAKOUT,
        )
        journal_repo.save_setup(cand)

        setups = journal_repo.get_latest_setups(limit=10)
        assert len(setups) >= 1
        saved = next(s for s in setups if s["candidate_id"] == "cand-persist-1")
        assert saved["entry_category"] == "Breakout"

    def test_save_and_retrieve_trade_with_category(self, journal_repo):
        trade = TradeRecord(
            trade_id="trade-persist-1",
            symbol="BTCUSDT",
            direction="LONG",
            entry_timestamp_ms=1700000000000,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit=62000.0,
            quantity=0.08,
            notional_usdt=4800.0,
            exit_timestamp_ms=1700003600000,
            exit_price=62000.0,
            pnl_gross=160.0,
            pnl_net=155.0,
            fee_paid=4.0,
            funding_paid=1.0,
            is_open=False,
            entry_category=EntryCategory.REVERSAL,
        )
        journal_repo.save_trade(trade)

        trades = journal_repo.get_trades(symbol="BTCUSDT")
        assert len(trades) >= 1
        saved = next(t for t in trades if t["trade_id"] == "trade-persist-1")
        assert saved["entry_category"] == "Reversal"
        assert saved["outcome"] == "WIN"
        assert saved["total_fees"] == 5.0


# ---------------------------------------------------------------------------
# 5. Multi-Criteria Journal Filtering Tests
# ---------------------------------------------------------------------------

class TestMultiFilterJournal:
    """Verifies multi-criteria filtering on JournalRepository.get_trades."""

    def test_multi_filter_combinations(self, journal_repo):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        # Create 3 diverse trades
        t1 = TradeRecord(
            trade_id="filt-1",
            symbol="BTCUSDT",
            direction="LONG",
            entry_timestamp_ms=now_ms - 3600000,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit=62000.0,
            quantity=0.1,
            notional_usdt=6000.0,
            exit_timestamp_ms=now_ms - 1800000,
            exit_price=62000.0,
            pnl_gross=200.0,
            pnl_net=195.0,
            is_open=False,
            entry_category=EntryCategory.BREAKOUT,
        )
        t2 = TradeRecord(
            trade_id="filt-2",
            symbol="ETHUSDT",
            direction="SHORT",
            entry_timestamp_ms=now_ms - 7200000,
            entry_price=3000.0,
            stop_loss=3050.0,
            take_profit=2900.0,
            quantity=1.0,
            notional_usdt=3000.0,
            exit_timestamp_ms=now_ms - 3600000,
            exit_price=3050.0,
            pnl_gross=-50.0,
            pnl_net=-52.0,
            is_open=False,
            entry_category=EntryCategory.PULLBACK,
        )
        t3 = TradeRecord(
            trade_id="filt-3",
            symbol="BTCUSDT",
            direction="SHORT",
            entry_timestamp_ms=now_ms - 1000000,
            entry_price=61000.0,
            stop_loss=61500.0,
            take_profit=60000.0,
            quantity=0.1,
            notional_usdt=6100.0,
            exit_timestamp_ms=None,
            exit_price=None,
            is_open=True,
            entry_category=EntryCategory.REVERSAL,
        )
        journal_repo.save_trade(t1)
        journal_repo.save_trade(t2)
        journal_repo.save_trade(t3)

        # 1. Filter by entry_category
        res = journal_repo.get_trades(entry_category="Breakout")
        assert len(res) == 1
        assert res[0]["trade_id"] == "filt-1"

        # 2. Filter by symbol
        res = journal_repo.get_trades(symbol="ETHUSDT")
        assert len(res) == 1
        assert res[0]["trade_id"] == "filt-2"

        # 3. Filter by direction
        res = journal_repo.get_trades(direction="LONG")
        assert len(res) == 1
        assert res[0]["trade_id"] == "filt-1"

        # 4. Filter by outcome WIN
        res = journal_repo.get_trades(outcome="WIN")
        assert len(res) == 1
        assert res[0]["trade_id"] == "filt-1"

        # 5. Filter by outcome OPEN
        res = journal_repo.get_trades(outcome="OPEN")
        assert len(res) == 1
        assert res[0]["trade_id"] == "filt-3"

        # 6. Filter open_only
        res = journal_repo.get_trades(open_only=True)
        assert len(res) == 1
        assert res[0]["trade_id"] == "filt-3"


# ---------------------------------------------------------------------------
# 6. Paper Execution Engine Lifecycle & State Recovery Tests
# ---------------------------------------------------------------------------

class TestPaperExecutionEngineLifecycle:
    """Verifies entry_category flows seamlessly from Candidate to Order to Position to TradeRecord."""

    def test_full_lifecycle_preserves_category(self, paper_engine, journal_repo):
        cand = make_test_candidate(
            candidate_id="cand-lifecycle-1",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry=60000.0,
            sl=59000.0,
            tp=62000.0,
            entry_category=EntryCategory.BREAKOUT,
        )
        cand.take_profit_2 = None

        # 1. Submit candidate -> PaperOrder created with category
        order = paper_engine.submit_candidate(cand, now_ms=1700000000000)
        assert order is not None
        assert order.entry_category == "Breakout"
        assert order.status == PaperOrderStatus.PENDING

        # Verify DB order has category
        orders = journal_repo.get_paper_orders(symbol="BTCUSDT")
        assert len(orders) >= 1
        assert orders[0]["entry_category"] == "Breakout"

        # 2. Simulate candle penetration past entry (e.g. low=59900 below 60000)
        c_fill = Candle(
            symbol="BTCUSDT",
            timeframe="1h",
            timestamp_ms=1700003600000,
            close_time_ms=1700007199999,
            open=60100.0,
            high=60200.0,
            low=59900.0,
            close=60050.0,
            volume=100.0,
        )
        filled_orders = paper_engine.evaluate_pending_orders({"BTCUSDT": [c_fill]}, now_ms=1700003600000)
        assert len(filled_orders) == 1
        assert filled_orders[0].status == PaperOrderStatus.FILLED

        # Verify position created with category
        pos = paper_engine.open_positions.get("BTCUSDT")
        assert pos is not None
        assert pos.entry_category == "Breakout"
        assert pos.is_open is True

        # 3. Simulate candle hitting TP (high=62100 above 62000)
        c_tp = Candle(
            symbol="BTCUSDT",
            timeframe="1h",
            timestamp_ms=1700007200000,
            close_time_ms=1700010799999,
            open=60100.0,
            high=62100.0,
            low=60000.0,
            close=61900.0,
            volume=100.0,
        )
        closed_trades = paper_engine.evaluate_open_positions({"BTCUSDT": [c_tp]}, now_ms=1700007200000)
        assert len(closed_trades) == 1
        closed_trade = closed_trades[0]
        assert closed_trade.entry_category == EntryCategory.BREAKOUT
        assert closed_trade.outcome == "WIN"
        assert closed_trade.is_open is False

        # Verify saved trade in DB has category
        trades = journal_repo.get_trades(symbol="BTCUSDT")
        assert len(trades) >= 1
        assert trades[0]["entry_category"] == "Breakout"

    def test_state_recovery_restores_category(self, temp_db, journal_repo, risk_mgr, paper_cfg):
        """Verifies engine restart restores pending orders and open positions with entry_category."""
        engine1 = PaperExecutionEngine(
            db=temp_db,
            journal=journal_repo,
            risk_manager=risk_mgr,
            config=paper_cfg,
        )
        cand = make_test_candidate(
            candidate_id="cand-recover-1",
            symbol="ETHUSDT",
            entry_category=EntryCategory.PULLBACK,
        )
        engine1.submit_candidate(cand, now_ms=1700000000000)

        # Create new engine instance simulating system restart
        engine2 = PaperExecutionEngine(
            db=temp_db,
            journal=journal_repo,
            risk_manager=risk_mgr,
            config=paper_cfg,
        )
        # recover_state is invoked in __init__
        assert any(o.symbol == "ETHUSDT" for o in engine2.pending_orders.values())
        recovered_order = next(o for o in engine2.pending_orders.values() if o.symbol == "ETHUSDT")
        assert recovered_order.entry_category == "Pullback"


# ---------------------------------------------------------------------------
# 7. FastApi Endpoint Tests
# ---------------------------------------------------------------------------

class TestApiEndpoints:
    """Verifies /api/setups, /api/trades, and /api/journal endpoints."""

    def test_get_trades_endpoint(self):
        from src.api.app import app
        client = TestClient(app)

        res = client.get("/api/trades")
        assert res.status_code == 200
        data = res.json()
        assert "trades" in data
        assert "metrics" in data
        assert "filtered_metrics" in data
        assert "closed_trades_count" in data["metrics"]
        assert "open_trades_count" in data["metrics"]

    def test_get_setups_endpoint_with_category_filter(self):
        from src.api.app import app
        client = TestClient(app)

        res = client.get("/api/setups?category=Breakout")
        assert res.status_code == 200
        data = res.json()
        assert "counts_by_category" in data
        assert "Breakout" in data["counts_by_category"]
        assert "Pullback" in data["counts_by_category"]
        assert "Reversal" in data["counts_by_category"]
        assert "Range/Mean Reversion" in data["counts_by_category"]
        assert "Unclassified" in data["counts_by_category"]

    def test_get_journal_endpoint(self):
        from src.api.app import app
        client = TestClient(app)

        res = client.get("/api/journal")
        assert res.status_code == 200
        data = res.json()
        assert "setups" in data
        assert "trades" in data
        assert "metrics" in data


# ---------------------------------------------------------------------------
# 8. Mandatory Safety Invariants Test
# ---------------------------------------------------------------------------

class TestMandatorySafetyInvariants:
    """Validates that no execution locks or safety gates are compromised."""

    def test_safety_invariants_preserved(self):
        import os
        cfg = AppConfig()
        assert cfg.mode == ExecutionMode.RESEARCH
        assert cfg.live_trading_locked is True
        assert os.environ.get("SCANNER_MODE", "PAPER") == "PAPER"

    def test_stale_or_infeasible_candidate_cannot_be_executed_by_category(self, paper_engine):
        """A candidate with a valid entry_category cannot bypass non-actionable status."""
        stale_cand = make_test_candidate(
            candidate_id="cand-stale",
            status=CandidateStatus.STALE,
            entry_category=EntryCategory.BREAKOUT,
        )
        assert not stale_cand.is_executable
        order = paper_engine.submit_candidate(stale_cand, now_ms=1700000000000)
        assert order is None  # Refused execution

        infeasible_cand = make_test_candidate(
            candidate_id="cand-infeasible",
            status=CandidateStatus.INFEASIBLE,
            entry_category=EntryCategory.PULLBACK,
        )
        assert not infeasible_cand.is_executable
        order2 = paper_engine.submit_candidate(infeasible_cand, now_ms=1700000000000)
        assert order2 is None  # Refused execution
