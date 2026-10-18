"""Comprehensive tests for Trading Journal & Performance Analytics Model Isolation.

Validates:
1. Model A journal queries return only Model A records.
2. Model B journal queries return only Model B records.
3. All Models query returns explicit per-model metrics without silent blending.
4. Model-specific KPI calculations use the correct trade subset.
5. Open-position counts are strictly separate from realized closed-trade metrics.
6. Floating PnL on open positions is never counted as realized PnL.
7. Fees and funding are attributed to the correct model.
8. Legacy records lacking model_id are classified as UNKNOWN without unsupported inference.
9. Linked orders, positions, trades, and lifecycle events retain consistent model_id.
10. API endpoint /api/trades supports model_id filtering and per_model_metrics.
11. Model A frozen cohort parameters remain strictly preserved.
12. SCANNER_MODE=PAPER and live_trading_locked=True remain enforced.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi.testclient import TestClient
import pytest

from src.core.types import (
    CandidateStatus,
    Direction,
    EntryCategory,
    MarketBias,
    SetupCandidate,
    StructureState,
    TradeRecord,
)
from src.data.database import DatabaseManager
from src.execution.paper_engine import (
    PaperExecutionConfig,
    PaperExecutionEngine,
    PaperOrder,
    PaperOrderStatus,
    PaperPosition,
)
from src.journal.repository import JournalRepository
from src.risk.manager import RiskManager
from src.api.app import app


@pytest.fixture
def temp_db():
    """Create an isolated, temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    db = DatabaseManager(db_path)
    db.init_db()
    yield db
    
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def test_journal(temp_db):
    """Create a JournalRepository backed by temporary DB."""
    return JournalRepository(temp_db)


def _create_mock_trade(
    trade_id: str,
    symbol: str,
    direction: Direction,
    entry_price: float,
    exit_price: float = None,
    is_open: bool = False,
    pnl_gross: float = 0.0,
    pnl_net: float = 0.0,
    fee_paid: float = 0.01,
    funding_paid: float = 0.005,
    slippage_paid: float = 0.002,
    model_id: str = "MODEL_A",
    setup_id: str = "",
) -> TradeRecord:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return TradeRecord(
        trade_id=trade_id,
        symbol=symbol,
        direction=direction,
        entry_timestamp_ms=now_ms - 3600000,
        entry_price=entry_price,
        stop_loss=entry_price * 0.95 if direction == Direction.LONG else entry_price * 1.05,
        take_profit=entry_price * 1.10 if direction == Direction.LONG else entry_price * 0.90,
        quantity=100.0,
        notional_usdt=entry_price * 100.0,
        exit_timestamp_ms=None if is_open else now_ms,
        exit_price=exit_price,
        pnl_gross=pnl_gross,
        pnl_net=pnl_net,
        fee_paid=fee_paid,
        funding_paid=funding_paid,
        exit_reason="" if is_open else "TAKE_PROFIT",
        is_open=is_open,
        setup_id=setup_id,
        leverage=10.0,
        margin_usdt=entry_price * 10.0,
        slippage_paid=slippage_paid,
        model_id=model_id,
        entry_category=EntryCategory.BREAKOUT,
    )


class TestJournalModelIsolation:

    def test_1_model_a_journal_queries_return_only_model_a(self, test_journal: JournalRepository):
        """Verify that querying for Model A records returns only Model A."""
        t_a = _create_mock_trade("TRD_A_1", "BTCUSDT", Direction.LONG, 80000.0, 81000.0, pnl_gross=10.0, pnl_net=9.5, model_id="MODEL_A")
        t_b = _create_mock_trade("TRD_B_1", "AGTUSDT", Direction.LONG, 0.067, 0.075, pnl_gross=1.5, pnl_net=1.4, model_id="MODEL_B")
        test_journal.save_trade(t_a)
        test_journal.save_trade(t_b)

        trades_a = test_journal.get_trades(model_id="MODEL_A")
        assert len(trades_a) == 1
        assert trades_a[0]["trade_id"] == "TRD_A_1"
        assert trades_a[0]["model_id"] == "MODEL_A"

    def test_2_model_b_journal_queries_return_only_model_b(self, test_journal: JournalRepository):
        """Verify that querying for Model B records returns only Model B."""
        t_a = _create_mock_trade("TRD_A_2", "ETHUSDT", Direction.SHORT, 2400.0, 2350.0, pnl_gross=5.0, pnl_net=4.8, model_id="MODEL_A")
        t_b = _create_mock_trade("TRD_B_2", "AGTUSDT", Direction.LONG, 0.067, is_open=True, model_id="MODEL_B")
        test_journal.save_trade(t_a)
        test_journal.save_trade(t_b)

        trades_b = test_journal.get_trades(model_id="MODEL_B")
        assert len(trades_b) == 1
        assert trades_b[0]["trade_id"] == "TRD_B_2"
        assert trades_b[0]["model_id"] == "MODEL_B"

    def test_3_all_models_query_does_not_silently_mix_and_labels_each(self, test_journal: JournalRepository):
        """Verify that querying ALL models labels each record with its authoritative model_id."""
        t_a = _create_mock_trade("TRD_A_3", "SOLUSDT", Direction.LONG, 100.0, 105.0, pnl_gross=5.0, pnl_net=4.8, model_id="MODEL_A")
        t_b = _create_mock_trade("TRD_B_3", "AGTUSDT", Direction.LONG, 0.067, is_open=True, model_id="MODEL_B")
        test_journal.save_trade(t_a)
        test_journal.save_trade(t_b)

        trades_all = test_journal.get_trades(model_id="ALL")
        assert len(trades_all) == 2
        models = {t["trade_id"]: t["model_id"] for t in trades_all}
        assert models["TRD_A_3"] == "MODEL_A"
        assert models["TRD_B_3"] == "MODEL_B"

    def test_4_model_specific_kpis_use_correct_subset(self, test_journal: JournalRepository):
        """Verify that KPI calculation on Model A subset does not include Model B numbers."""
        t_a1 = _create_mock_trade("TRD_A_WIN", "BTCUSDT", Direction.LONG, 80000.0, 82000.0, pnl_gross=20.0, pnl_net=19.0, model_id="MODEL_A")
        t_a2 = _create_mock_trade("TRD_A_LOSS", "ETHUSDT", Direction.LONG, 2400.0, 2350.0, pnl_gross=-5.0, pnl_net=-5.5, model_id="MODEL_A")
        t_b1 = _create_mock_trade("TRD_B_WIN", "AGTUSDT", Direction.LONG, 0.060, 0.070, pnl_gross=1.0, pnl_net=0.95, model_id="MODEL_B")
        
        test_journal.save_trade(t_a1)
        test_journal.save_trade(t_a2)
        test_journal.save_trade(t_b1)

        trades_a = test_journal.get_trades(model_id="MODEL_A")
        kpi_a = test_journal.compute_trade_metrics(trades_a)
        assert kpi_a["closed_trades_count"] == 2
        assert kpi_a["wins_count"] == 1
        assert kpi_a["losses_count"] == 1
        assert kpi_a["gross_pnl"] == 15.0
        assert kpi_a["net_pnl"] == 13.5

        trades_b = test_journal.get_trades(model_id="MODEL_B")
        kpi_b = test_journal.compute_trade_metrics(trades_b)
        assert kpi_b["closed_trades_count"] == 1
        assert kpi_b["wins_count"] == 1
        assert kpi_b["losses_count"] == 0
        assert kpi_b["gross_pnl"] == 1.0
        assert kpi_b["net_pnl"] == 0.95

    def test_5_open_positions_strictly_separate_from_closed_metrics(self, test_journal: JournalRepository):
        """Verify that open positions are counted in open_trades_count and never in closed_trades_count."""
        t_open = _create_mock_trade("TRD_B_OPEN", "AGTUSDT", Direction.LONG, 0.067, is_open=True, model_id="MODEL_B")
        test_journal.save_trade(t_open)

        trades = test_journal.get_trades(model_id="MODEL_B")
        metrics = test_journal.compute_trade_metrics(trades)
        assert metrics["closed_trades_count"] == 0
        assert metrics["open_trades_count"] == 1
        assert metrics["wins_count"] == 0
        assert metrics["losses_count"] == 0
        assert metrics["win_rate_pct"] == 0.0
        assert metrics["gross_pnl"] == 0.0
        assert metrics["net_pnl"] == 0.0

    def test_6_floating_pnl_not_counted_as_realized_pnl(self, test_journal: JournalRepository):
        """Verify that floating unrealized PnL is excluded from realized gross and net PnL."""
        t_open = _create_mock_trade(
            "TRD_B_FLOAT",
            "AGTUSDT",
            Direction.LONG,
            0.066935,
            is_open=True,
            pnl_gross=0.50,
            pnl_net=0.45,  # Floating PnL
            model_id="MODEL_B",
        )
        test_journal.save_trade(t_open)

        trades = test_journal.get_trades(model_id="MODEL_B")
        metrics = test_journal.compute_trade_metrics(trades)
        # Realized metrics MUST remain 0.00
        assert metrics["gross_pnl"] == 0.0
        assert metrics["net_pnl"] == 0.0
        assert metrics["closed_trades_count"] == 0
        assert metrics["open_trades_count"] == 1

    def test_7_fees_and_funding_attributed_to_correct_model(self, test_journal: JournalRepository):
        """Verify that fees, funding, and slippage are attributed strictly to the owning model."""
        t_a = _create_mock_trade("TRD_A_FEE", "BTCUSDT", Direction.LONG, 80000.0, 81000.0, fee_paid=0.50, funding_paid=0.20, slippage_paid=0.10, model_id="MODEL_A")
        t_b = _create_mock_trade("TRD_B_FEE", "AGTUSDT", Direction.LONG, 0.067, 0.075, fee_paid=0.02, funding_paid=0.01, slippage_paid=0.005, model_id="MODEL_B")
        test_journal.save_trade(t_a)
        test_journal.save_trade(t_b)

        kpi_a = test_journal.compute_trade_metrics(test_journal.get_trades(model_id="MODEL_A"))
        assert kpi_a["total_fees"] == 0.80

        kpi_b = test_journal.compute_trade_metrics(test_journal.get_trades(model_id="MODEL_B"))
        assert kpi_b["total_fees"] == 0.035

    def test_8_legacy_records_classified_as_unknown(self, temp_db: DatabaseManager):
        """Verify that legacy records without model_id are returned as UNKNOWN and never guessed."""
        repo = JournalRepository(temp_db)
        # Insert a raw trade without model_id directly into database
        with temp_db.get_connection() as conn:
            conn.execute("""
            INSERT INTO trade_journal (
                trade_id, symbol, direction, entry_timestamp_ms, entry_price,
                stop_loss, take_profit, quantity, notional_usdt, created_at_utc,
                model_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, ("TRD_LEGACY_1", "BTCUSDT", "LONG", 1700000000000, 50000.0, 48000.0, 55000.0, 0.1, 5000.0, "2026-01-01T00:00:00Z", ""))
            conn.commit()

        trades = repo.get_trades()
        legacy_trade = next(t for t in trades if t["trade_id"] == "TRD_LEGACY_1")
        assert legacy_trade["model_id"] == "UNKNOWN"

        # Query specifically for UNKNOWN model
        unknown_trades = repo.get_trades(model_id="UNKNOWN")
        assert any(t["trade_id"] == "TRD_LEGACY_1" for t in unknown_trades)

    def test_9_linked_orders_positions_and_lifecycle_retain_consistent_model_id(self, test_journal: JournalRepository):
        """Verify that candidate, order, position, trade, and lifecycle events have consistent model_id."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        cand = SetupCandidate(
            candidate_id="MCAND_B_TEST_1",
            symbol="AGTUSDT",
            timestamp_ms=now_ms,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=0.067,
            stop_loss=0.063,
            take_profit_1=0.075,
            gross_reward_to_risk=2.0,
            net_reward_to_risk=1.8,
            invalidation_price=0.063,
            entry_category=EntryCategory.REVERSAL,
            model_id="MODEL_B",
        )
        test_journal.save_setup(cand)

        order_dict = {
            "order_id": "PORD_B_TEST_1",
            "candidate_id": cand.candidate_id,
            "symbol": cand.symbol,
            "direction": cand.direction,
            "order_type": "LIMIT",
            "price": cand.entry_price,
            "stop_price": cand.stop_loss,
            "take_profit": cand.take_profit_1,
            "quantity": 100.0,
            "notional_usdt": 6.7,
            "margin_usdt": 0.67,
            "leverage": 10.0,
            "status": "FILLED",
            "created_bar_time_ms": now_ms,
            "model_id": "MODEL_B",
            "entry_category": "Reversal",
        }
        test_journal.save_paper_order(order_dict)

        test_journal.save_lifecycle_event(
            candidate_id=cand.candidate_id,
            symbol=cand.symbol,
            stage="FILL_SIMULATED",
            timestamp_ms=now_ms,
            order_id="PORD_B_TEST_1",
            trade_id="PTRD_B_TEST_1",
            model_id="MODEL_B",
        )

        trade = _create_mock_trade("PTRD_B_TEST_1", "AGTUSDT", Direction.LONG, 0.067, is_open=True, model_id="MODEL_B", setup_id=cand.candidate_id)
        test_journal.save_trade(trade)

        # Verify all linked entities have MODEL_B
        setups = test_journal.get_latest_setups(model_id="MODEL_B")
        assert any(s["candidate_id"] == "MCAND_B_TEST_1" and s["model_id"] == "MODEL_B" for s in setups)

        orders = test_journal.get_paper_orders(model_id="MODEL_B")
        assert any(o["order_id"] == "PORD_B_TEST_1" and o["model_id"] == "MODEL_B" for o in orders)

        events = test_journal.get_lifecycle_events(model_id="MODEL_B")
        assert any(e["candidate_id"] == "MCAND_B_TEST_1" and e["model_id"] == "MODEL_B" for e in events)

        trades = test_journal.get_trades(model_id="MODEL_B")
        assert any(t["trade_id"] == "PTRD_B_TEST_1" and t["model_id"] == "MODEL_B" for t in trades)

    def test_10_api_trades_endpoint_supports_model_id_and_per_model_metrics(self):
        """Verify that /api/trades supports model_id filtering and returns per_model_metrics."""
        client = TestClient(app)
        
        # 1. Query All Models
        res_all = client.get("/api/trades")
        assert res_all.status_code == 200
        data_all = res_all.json()
        assert data_all["model_id"] == "ALL"
        assert "per_model_metrics" in data_all
        assert "MODEL_A" in data_all["per_model_metrics"]
        assert "MODEL_B" in data_all["per_model_metrics"]

        # 2. Query Model A
        res_a = client.get("/api/trades?model_id=MODEL_A")
        assert res_a.status_code == 200
        data_a = res_a.json()
        assert data_a["model_id"] == "MODEL_A"
        # All returned trades must have model_id == MODEL_A
        for t in data_a["trades"]:
            assert t["model_id"] == "MODEL_A"

        # 3. Query Model B
        res_b = client.get("/api/trades?model_id=MODEL_B")
        assert res_b.status_code == 200
        data_b = res_b.json()
        assert data_b["model_id"] == "MODEL_B"
        for t in data_b["trades"]:
            assert t["model_id"] == "MODEL_B"

    def test_11_model_a_frozen_cohort_records_unchanged(self):
        """Verify that Model A risk policy and forward validation cohort metrics remain intact."""
        client = TestClient(app)
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()
        # Model A risk parameters must remain frozen
        assert data["capital"]["initial_equity_usdt"] == 16.0
        assert data["risk_limits"]["daily_loss_limit_usdt"] == 0.50
        assert data["risk_limits"]["weekly_loss_limit_usdt"] == 1.25
        assert data["risk_limits"]["max_open_positions"] == 1

    def test_12_scanner_mode_paper_and_live_trading_locked_enforced(self):
        """Verify that live_trading_locked=True and mode=PAPER remain strictly enforced."""
        client = TestClient(app)
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.json()
        assert data["live_trading_locked"] is True
        assert data["mode"] == "PAPER"
        assert os.environ.get("SCANNER_MODE", "PAPER").upper() == "PAPER"
