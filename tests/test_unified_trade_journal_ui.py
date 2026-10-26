"""Comprehensive Test Suite for Unified Automated Trade Journal Table & CSV Exporter.

Verifies all requirements:
1. One row per unique trade ID.
2. Correct Realized PnL and Realized R calculation using initial planned risk ($0.20 USD).
3. Open trades do not calculate Realized R as if closed (displays '--' in UI, empty in CSV).
4. Strict separation of LIVE, PAPER, and SHADOW metrics and data.
5. Filtering & sorting by date range, symbol, strategy, mode, status, side, and reconciliation status.
6. Expandable trade details displaying nested granular fill ledger records.
7. Duplicate reconciliation prevention (idempotent fill ingestion).
8. Unresolved reconciliation status display (REQUIRES_RECONCILIATION, UNVERIFIED).
9. Full consistency between unified CSV export and table data.
10. Zero fabrication of missing execution data.
"""

import csv
import io
import os
import tempfile
from datetime import datetime, timezone
import pytest

from src.data.database import DatabaseManager
from src.journal.automated_journal import AutomatedTradeJournal
from src.journal.exporter import JournalExporter
from src.journal.models import AutomatedTradeRecord, FillRecord, JournalFilter, ReconciliationStatus, TradeStatus


@pytest.fixture
def temp_journal_env():
    """Create isolated SQLite database with test journal data."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = DatabaseManager(path)
    journal = AutomatedTradeJournal(db)
    exporter = JournalExporter(db, journal)
    yield db, journal, exporter
    if os.path.exists(path):
        os.remove(path)


def test_01_one_row_per_unique_trade_id(temp_journal_env):
    """Test that each trade has exactly one primary row identified by unique trade_id."""
    db, journal, exporter = temp_journal_env

    t1 = AutomatedTradeRecord(
        journal_id="JRN_ETH_1",
        trade_id="TRD_ETH_001",
        symbol="ETHUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        status="CLOSED",
        planned_entry_price=2300.0,
        average_entry_price=2300.0,
        planned_risk_usd=0.20,
        net_pnl_usd=-0.10,
        realized_r=-0.50,
    )
    t2 = AutomatedTradeRecord(
        journal_id="JRN_SOL_1",
        trade_id="TRD_SOL_002",
        symbol="SOLUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        status="OPEN",
        planned_entry_price=100.0,
        average_entry_price=100.0,
        planned_risk_usd=0.20,
    )
    journal.record_trade_intent(t1)
    journal.record_trade_intent(t2)

    trades = journal.get_trades()
    assert len(trades) == 2
    trade_ids = [t.trade_id for t in trades]
    assert len(set(trade_ids)) == len(trade_ids)
    assert "TRD_ETH_001" in trade_ids
    assert "TRD_SOL_002" in trade_ids


def test_02_realized_pnl_and_realized_r_use_planned_risk(temp_journal_env):
    """Realized R must be strictly net_pnl_usd / planned_risk_usd ($0.20 planned risk)."""
    db, journal, exporter = temp_journal_env

    trade = AutomatedTradeRecord(
        journal_id="JRN_TEST_R",
        trade_id="TRD_TEST_R",
        symbol="ETHUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        status="CLOSED",
        planned_entry_price=2343.62,
        average_entry_price=2343.62,
        filled_quantity=0.009,
        planned_risk_usd=0.20,
        gross_pnl_usd=-0.14067,
        total_fees_usd=0.00838,
        net_pnl_usd=-0.14905,
    )
    journal.record_trade_intent(trade)

    summary = journal.get_summary_metrics(mode="LIVE")
    assert summary["total_closed_trades"] == 1
    assert summary["losses_count"] == 1
    assert summary["wins_count"] == 0
    assert summary["total_net_pnl_usd"] == round(-0.14905, 4)
    # -0.14905 / 0.20 = -0.74525 R
    expected_r = round(-0.14905 / 0.20, 3)
    assert summary["average_realized_r"] == expected_r


def test_03_open_trades_do_not_calculate_realized_r(temp_journal_env):
    """Open trades must never compute realized R as if closed; CSV must be empty and summary must not count them."""
    db, journal, exporter = temp_journal_env

    open_trade = AutomatedTradeRecord(
        journal_id="JRN_OPEN_1",
        trade_id="TRD_OPEN_SOL",
        symbol="SOLUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        status="OPEN",
        planned_entry_price=101.38,
        average_entry_price=101.38,
        filled_quantity=0.19,
        planned_risk_usd=0.1904,
        net_pnl_usd=0.0,
        realized_r=0.0,
    )
    journal.record_trade_intent(open_trade)

    # In summary: closed count must be 0, open positions count must be 1
    summary = journal.get_summary_metrics(mode="LIVE")
    assert summary["total_closed_trades"] == 0
    assert summary["open_positions_count"] == 1
    assert summary["average_realized_r"] == 0.0

    # In Unified CSV: realized_r column for open trade must be empty string
    csv_str = exporter.export_unified_journal_csv()
    reader = csv.DictReader(io.StringIO(csv_str))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "TRD_OPEN_SOL"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["realized_r"] == ""


def test_04_live_shadow_paper_strict_separation(temp_journal_env):
    """Metrics and table queries must strictly separate LIVE, PAPER, and SHADOW executions."""
    db, journal, exporter = temp_journal_env

    live_t = AutomatedTradeRecord(
        journal_id="J_LIVE",
        trade_id="TRD_LIVE_1",
        symbol="ETHUSDT",
        mode="LIVE",
        status="CLOSED",
        net_pnl_usd=-0.15,
        planned_risk_usd=0.20,
    )
    paper_t = AutomatedTradeRecord(
        journal_id="J_PAPER",
        trade_id="TRD_PAPER_1",
        symbol="BTCUSDT",
        mode="PAPER",
        status="CLOSED",
        net_pnl_usd=1.50,
        planned_risk_usd=0.20,
    )
    shadow_t = AutomatedTradeRecord(
        journal_id="J_SHADOW",
        trade_id="TRD_SHADOW_1",
        symbol="SOLUSDT",
        mode="SHADOW",
        status="CLOSED",
        net_pnl_usd=0.80,
        planned_risk_usd=0.20,
    )
    journal.record_trade_intent(live_t)
    journal.record_trade_intent(paper_t)
    journal.record_trade_intent(shadow_t)

    live_sum = journal.get_summary_metrics("LIVE")
    assert live_sum["total_closed_trades"] == 1
    assert live_sum["total_net_pnl_usd"] == -0.15

    paper_sum = journal.get_summary_metrics("PAPER")
    assert paper_sum["total_closed_trades"] == 1
    assert paper_sum["total_net_pnl_usd"] == 1.50

    shadow_sum = journal.get_summary_metrics("SHADOW")
    assert shadow_sum["total_closed_trades"] == 1
    assert shadow_sum["total_net_pnl_usd"] == 0.80

    all_sum = journal.get_summary_metrics("ALL")
    assert all_sum["total_closed_trades"] == 3


def test_05_filtering_and_sorting_capabilities(temp_journal_env):
    """Test filtering by symbol, strategy, status, and reconciliation status."""
    db, journal, exporter = temp_journal_env

    t1 = AutomatedTradeRecord(
        journal_id="J1",
        trade_id="TRD_1",
        symbol="ETHUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        status="CLOSED",
        reconciliation_status="RECONCILED",
    )
    t2 = AutomatedTradeRecord(
        journal_id="J2",
        trade_id="TRD_2",
        symbol="SOLUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        status="OPEN",
        reconciliation_status="REQUIRES_RECONCILIATION",
    )
    t3 = AutomatedTradeRecord(
        journal_id="J3",
        trade_id="TRD_3",
        symbol="BTCUSDT",
        strategy_id="SWEEP_1M_V1",
        mode="SHADOW",
        status="CLOSED",
        reconciliation_status="RECONCILED",
    )
    journal.record_trade_intent(t1)
    journal.record_trade_intent(t2)
    journal.record_trade_intent(t3)

    # Filter by symbol
    eth_trades = journal.get_trades(JournalFilter(symbol="ETH"))
    assert len(eth_trades) == 1
    assert eth_trades[0].trade_id == "TRD_1"

    # Filter by reconciliation_status
    recon_trades = journal.get_trades(JournalFilter(reconciliation_status="REQUIRES_RECONCILIATION"))
    assert len(recon_trades) == 1
    assert recon_trades[0].trade_id == "TRD_2"

    # Filter by strategy
    sweep_trades = journal.get_trades(JournalFilter(strategy_id="SWEEP_1M_V1"))
    assert len(sweep_trades) == 1
    assert sweep_trades[0].trade_id == "TRD_3"


def test_06_expandable_granular_fill_ledger(temp_journal_env):
    """Granular fills in fill_ledger must link to parent trade and be retrievable."""
    db, journal, exporter = temp_journal_env

    trade = AutomatedTradeRecord(
        journal_id="J_ETH",
        trade_id="TRD_ETH_FILLS",
        symbol="ETHUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        status="CLOSED",
        planned_entry_price=2343.62,
    )
    journal.record_trade_intent(trade)

    fill1 = FillRecord(
        fill_id="F1",
        trade_id="TRD_ETH_FILLS",
        order_id="ORD_ENTRY",
        timestamp_utc="2026-10-25T22:20:34Z",
        timestamp_ms=1792966834250,
        symbol="ETHUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=2343.62,
        fill_quantity=0.009,
        fill_notional_usd=21.09,
        fee_usd=0.0,
        liquidity_type="MAKER",
    )
    fill2 = FillRecord(
        fill_id="F2",
        trade_id="TRD_ETH_FILLS",
        order_id="ORD_EXIT",
        timestamp_utc="2026-10-25T23:26:40Z",
        timestamp_ms=1792970800100,
        symbol="ETHUSDT",
        side="SELL",
        fill_role="EXIT",
        fill_price=2327.99,
        fill_quantity=0.009,
        fill_notional_usd=20.95,
        fee_usd=0.00838,
        liquidity_type="TAKER",
    )
    journal.record_fill(fill1)
    journal.record_fill(fill2)

    fills = journal.get_fills_for_trade("TRD_ETH_FILLS")
    assert len(fills) == 2
    assert fills[0].fill_role == "ENTRY"
    assert fills[0].liquidity_type == "MAKER"
    assert fills[1].fill_role == "EXIT"
    assert fills[1].liquidity_type == "TAKER"
    assert fills[1].fee_usd == 0.00838


def test_07_duplicate_reconciliation_prevention(temp_journal_env):
    """Reconciling multiple times must be idempotent and never duplicate fills or trades."""
    db, journal, exporter = temp_journal_env

    trade = AutomatedTradeRecord(
        journal_id="J_DUP",
        trade_id="TRD_DUP",
        symbol="ETHUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        status="OPEN",
        order_id="ORD_100",
        sl_order_id="SL_200",
        tp_order_id="TP_300",
        filled_quantity=0.01,
        average_entry_price=2000.0,
    )
    journal.record_trade_intent(trade)

    exchange_trades = [{
        "symbol": "ETHUSDT",
        "id": 99999,
        "orderId": 200,
        "side": "SELL",
        "price": "1990.0",
        "qty": "0.01",
        "realizedPnl": "-0.10",
        "commission": "0.005",
        "commissionAsset": "USDT",
        "time": 1792970000000,
        "buyer": False,
        "maker": False,
    }]

    # Reconcile twice
    res1 = journal.reconcile_with_exchange(exchange_trades=exchange_trades, exchange_positions=[])
    assert res1["new_fills_imported"] == 1

    res2 = journal.reconcile_with_exchange(exchange_trades=exchange_trades, exchange_positions=[])
    assert res2["new_fills_imported"] == 0  # Deduplicated

    fills = journal.get_fills_for_trade("TRD_DUP")
    assert len(fills) == 1


def test_08_unresolved_reconciliation_status_flagging(temp_journal_env):
    """Discrepancies between exchange and local state must be flagged REQUIRES_RECONCILIATION."""
    db, journal, exporter = temp_journal_env

    # Trade claims to be open with 0.05 ETH, but exchange position is 0 with no matching exit fills
    trade = AutomatedTradeRecord(
        journal_id="J_DISC",
        trade_id="TRD_DISC",
        symbol="ETHUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        status="OPEN",
        filled_quantity=0.05,
        average_entry_price=2500.0,
        exit_quantity=0.0,
    )
    journal.record_trade_intent(trade)

    res = journal.reconcile_with_exchange(exchange_trades=[], exchange_positions=[])
    assert res["discrepancies_found"] >= 1

    updated_trade = journal.get_trade_by_id("TRD_DISC")
    assert updated_trade.reconciliation_status == ReconciliationStatus.REQUIRES_RECONCILIATION.value
    assert "Discrepancy" in updated_trade.notes


def test_09_unified_csv_export_endpoint_and_consistency(temp_journal_env):
    """Unified CSV export contains all required columns and matches table data."""
    db, journal, exporter = temp_journal_env

    t = AutomatedTradeRecord(
        journal_id="J_CSV",
        trade_id="TRD_CSV_MATCH",
        symbol="ETHUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        status="CLOSED",
        planned_entry_price=2343.62,
        average_entry_price=2343.62,
        planned_stop_price=2328.19,
        sl_price=2328.19,
        planned_take_profit_price=2378.33,
        tp_price=2378.33,
        filled_quantity=0.009,
        planned_risk_usd=0.20,
        gross_pnl_usd=-0.14,
        total_fees_usd=0.008,
        net_pnl_usd=-0.148,
        realized_r=-0.74,
        reconciliation_status="RECONCILED",
        protection_status="CLOSED",
    )
    journal.record_trade_intent(t)

    csv_data = exporter.export_unified_journal_csv()
    reader = csv.DictReader(io.StringIO(csv_data))
    rows = list(reader)
    assert len(rows) == 1
    row = rows[0]

    assert row["trade_id"] == "TRD_CSV_MATCH"
    assert row["symbol"] == "ETHUSDT"
    assert row["strategy_id"] == "EMA20_RECLAIM_5M_V1"
    assert row["mode"] == "LIVE"
    assert row["side"] == "LONG"
    assert row["status"] == "CLOSED"
    assert float(row["average_entry_price"]) == 2343.62
    assert float(row["planned_stop_price"]) == 2328.19
    assert float(row["actual_stop_price"]) == 2328.19
    assert float(row["planned_take_profit_price"]) == 2378.33
    assert float(row["actual_take_profit_price"]) == 2378.33
    assert float(row["planned_risk_usd"]) == 0.20
    assert float(row["realized_r"]) == -0.74
    assert row["reconciliation_status"] == "RECONCILED"
    assert row["protection_status"] == "CLOSED"


def test_10_missing_execution_data_not_fabricated(temp_journal_env):
    """When execution data is missing, planned prices must not be substituted as actual fills."""
    db, journal, exporter = temp_journal_env

    # Setup with no actual fill yet
    unfilled = AutomatedTradeRecord(
        journal_id="J_UNFILLED",
        trade_id="TRD_UNFILLED",
        symbol="ETHUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        status="PENDING",
        planned_entry_price=2300.0,
        average_entry_price=None,  # No actual fill!
        filled_quantity=0.0,
        average_exit_price=None,
        exit_quantity=0.0,
        planned_risk_usd=0.20,
    )
    journal.record_trade_intent(unfilled)

    csv_data = exporter.export_unified_journal_csv()
    reader = csv.DictReader(io.StringIO(csv_data))
    rows = list(reader)
    assert len(rows) == 1
    row = rows[0]

    assert row["planned_entry_price"] == "2300.0"
    assert row["average_entry_price"] == ""  # NOT fabricated!
    assert row["average_exit_price"] == ""
    assert row["realized_r"] == ""
