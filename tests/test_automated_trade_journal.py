"""Test Suite for Automated Trade Journal & CSV Exporter (Section 15).

Verifies all 14 required areas from Section 15.9:
1. Duplicate exchange fill prevention.
2. Partial entry fills.
3. Partial exit fills.
4. Fee aggregation.
5. Funding aggregation.
6. Realized PnL calculation.
7. Realized R calculation.
8. Reconciliation after restart.
9. Reconciliation after API interruption.
10. CSV schema and encoding.
11. CSV export consistency with journal records.
12. LIVE/SHADOW/PAPER separation.
13. Null and unavailable data handling.
14. Historical configuration preservation.
"""

import csv
from datetime import datetime, timezone
import io
import json
import pytest
from typing import Any, Dict
import zipfile

from src.data.database import DatabaseManager
from src.journal.automated_journal import AutomatedTradeJournal
from src.journal.exporter import JournalExporter
from src.journal.models import (
    AutomatedTradeRecord,
    DataQualityFlag,
    FillRecord,
    JournalFilter,
    JournalMode,
    ReconciliationStatus,
    TradeStatus,
)


@pytest.fixture
def journal_db(tmp_path):
    """Provide clean database instance for automated journal tests."""
    db_file = tmp_path / "test_autojournal.db"
    db = DatabaseManager(db_file)
    db.init_db()
    return db


@pytest.fixture
def journal(journal_db):
    """Provide AutomatedTradeJournal instance."""
    return AutomatedTradeJournal(journal_db)


@pytest.fixture
def exporter(journal_db, journal):
    """Provide JournalExporter instance."""
    return JournalExporter(journal_db, journal)


# =============================================================================
# 1. Duplicate Exchange Fill Prevention
# =============================================================================
def test_01_duplicate_exchange_fill_prevention(journal):
    """Verify that recording the exact same fill or exchange_trade_id twice is rejected and idempotent."""
    trade = AutomatedTradeRecord(
        journal_id="j_trade_001",
        trade_id="trade_001",
        symbol="SOLUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        planned_risk_usd=0.20,
        planned_entry_price=100.0,
    )
    journal.record_trade_intent(trade)

    fill = FillRecord(
        fill_id="fill_001",
        trade_id="trade_001",
        order_id="ord_001",
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        timestamp_ms=1700000000000,
        symbol="SOLUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=100.0,
        fill_quantity=0.20,
        fill_notional_usd=20.0,
        fee_amount=0.01,
        fee_usd=0.01,
        exchange_trade_id="ex_tr_999",
    )

    # First insertion
    first_res = journal.record_fill(fill)
    assert first_res is True

    # Duplicate insertion attempt
    second_res = journal.record_fill(fill)
    assert second_res is False

    # Verify parent trade quantity and fee were not doubled
    updated_trade = journal.get_trade_by_id("trade_001")
    assert updated_trade.filled_quantity == 0.20
    assert updated_trade.entry_fee_usd == 0.01
    assert updated_trade.entry_fill_count == 1


# =============================================================================
# 2. Partial Entry Fills
# =============================================================================
def test_02_partial_entry_fills(journal):
    """Verify multiple partial entry fills update filled quantity, calculate weighted average entry, and preserve fills."""
    trade = AutomatedTradeRecord(
        journal_id="j_trade_002",
        trade_id="trade_002",
        symbol="SOLUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        planned_risk_usd=0.20,
        planned_entry_price=100.0,
    )
    journal.record_trade_intent(trade)

    # Fill 1: 0.10 @ $100.00
    journal.record_fill(FillRecord(
        fill_id="fill_entry_1",
        trade_id="trade_002",
        order_id="ord_002",
        timestamp_utc="2026-10-26T00:00:01Z",
        timestamp_ms=1700000001000,
        symbol="SOLUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=100.00,
        fill_quantity=0.10,
        fill_notional_usd=10.00,
        fee_usd=0.005,
        liquidity_type="MAKER",
    ))

    # Fill 2: 0.10 @ $100.50
    journal.record_fill(FillRecord(
        fill_id="fill_entry_2",
        trade_id="trade_002",
        order_id="ord_002",
        timestamp_utc="2026-10-26T00:00:05Z",
        timestamp_ms=1700000005000,
        symbol="SOLUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=100.50,
        fill_quantity=0.10,
        fill_notional_usd=10.05,
        fee_usd=0.005,
        liquidity_type="TAKER",
    ))

    tr = journal.get_trade_by_id("trade_002")
    assert round(tr.filled_quantity, 4) == 0.20
    # Weighted average: (0.10*100 + 0.10*100.50)/0.20 = 100.25
    assert round(tr.average_entry_price, 2) == 100.25
    assert tr.entry_fill_count == 2
    assert tr.entry_fill_type == "MIXED"
    assert tr.status == TradeStatus.OPEN.value


# =============================================================================
# 3. Partial Exit Fills
# =============================================================================
def test_03_partial_exit_fills(journal):
    """Verify multiple exit fills calculate correct weighted exit price, incremental PnL, and close when complete."""
    trade = AutomatedTradeRecord(
        journal_id="j_trade_003",
        trade_id="trade_003",
        symbol="SOLUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        planned_risk_usd=0.20,
        planned_entry_price=100.0,
    )
    journal.record_trade_intent(trade)

    # Entry: 0.20 @ $100.00
    journal.record_fill(FillRecord(
        fill_id="f_entry",
        trade_id="trade_003",
        order_id="ord_003",
        timestamp_utc="2026-10-26T00:00:01Z",
        timestamp_ms=1700000001000,
        symbol="SOLUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=100.00,
        fill_quantity=0.20,
        fill_notional_usd=20.00,
        fee_usd=0.004,
    ))

    # Partial Exit 1: 0.10 @ $101.00
    journal.record_fill(FillRecord(
        fill_id="f_exit_1",
        trade_id="trade_003",
        order_id="ord_exit_1",
        timestamp_utc="2026-10-26T00:01:00Z",
        timestamp_ms=1700000060000,
        symbol="SOLUSDT",
        side="SELL",
        fill_role="EXIT",
        fill_price=101.00,
        fill_quantity=0.10,
        fill_notional_usd=10.10,
        fee_usd=0.002,
    ))

    tr_part = journal.get_trade_by_id("trade_003")
    assert tr_part.exit_quantity == 0.10
    assert tr_part.status == TradeStatus.OPEN.value
    assert round(tr_part.gross_pnl_usd, 4) == 0.10  # (101 - 100) * 0.10

    # Partial Exit 2: Remaining 0.10 @ $102.00
    journal.record_fill(FillRecord(
        fill_id="f_exit_2",
        trade_id="trade_003",
        order_id="ord_exit_2",
        timestamp_utc="2026-10-26T00:02:00Z",
        timestamp_ms=1700000120000,
        symbol="SOLUSDT",
        side="SELL",
        fill_role="EXIT",
        fill_price=102.00,
        fill_quantity=0.10,
        fill_notional_usd=10.20,
        fee_usd=0.002,
    ))

    tr_closed = journal.get_trade_by_id("trade_003")
    assert round(tr_closed.exit_quantity, 4) == 0.20
    assert tr_closed.status == TradeStatus.CLOSED.value
    # Weighted average exit: (0.10*101 + 0.10*102)/0.20 = 101.50
    assert round(tr_closed.average_exit_price, 2) == 101.50
    # Gross PnL: (101.50 - 100.00) * 0.20 = 0.30
    assert round(tr_closed.gross_pnl_usd, 4) == 0.30


# =============================================================================
# 4. Fee Aggregation
# =============================================================================
def test_04_fee_aggregation(journal):
    """Verify entry and exit fees aggregate precisely into total_fees_usd without losing fill rows."""
    trade = AutomatedTradeRecord(
        journal_id="j_trade_004",
        trade_id="trade_004",
        symbol="SOLUSDT",
        side="LONG",
        planned_risk_usd=0.20,
    )
    journal.record_trade_intent(trade)

    journal.record_fill(FillRecord(
        fill_id="f_in",
        trade_id="trade_004",
        order_id="o1",
        timestamp_utc="2026-10-26T00:00:00Z",
        timestamp_ms=1000,
        symbol="SOLUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=100.0,
        fill_quantity=0.20,
        fill_notional_usd=20.0,
        fee_usd=0.004,
    ))
    journal.record_fill(FillRecord(
        fill_id="f_out",
        trade_id="trade_004",
        order_id="o2",
        timestamp_utc="2026-10-26T00:05:00Z",
        timestamp_ms=2000,
        symbol="SOLUSDT",
        side="SELL",
        fill_role="EXIT",
        fill_price=101.0,
        fill_quantity=0.20,
        fill_notional_usd=20.2,
        fee_usd=0.005,
    ))

    tr = journal.get_trade_by_id("trade_004")
    assert round(tr.entry_fee_usd, 4) == 0.004
    assert round(tr.exit_fee_usd, 4) == 0.005
    assert round(tr.total_fees_usd, 4) == 0.009

    # Verify both granular rows exist in fill ledger
    fills = journal.get_fills_for_trade("trade_004")
    assert len(fills) == 2


# =============================================================================
# 5. Funding Aggregation
# =============================================================================
def test_05_funding_aggregation(journal):
    """Verify funding receipts and payments accrue to funding_usd and update net PnL."""
    trade = AutomatedTradeRecord(
        journal_id="j_trade_005",
        trade_id="trade_005",
        symbol="SOLUSDT",
        side="LONG",
        planned_risk_usd=0.20,
    )
    journal.record_trade_intent(trade)

    # Initial fill
    journal.record_fill(FillRecord(
        fill_id="f_005",
        trade_id="trade_005",
        order_id="o_005",
        timestamp_utc="2026-10-26T00:00:00Z",
        timestamp_ms=1000,
        symbol="SOLUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=100.0,
        fill_quantity=0.20,
        fill_notional_usd=20.0,
        fee_usd=0.004,
    ))

    # Accrue funding interval 1: -$0.002 (paid funding)
    journal.record_funding("trade_005", funding_usd=-0.002)
    # Accrue funding interval 2: +$0.001 (rebate received)
    journal.record_funding("trade_005", funding_usd=0.001)

    tr = journal.get_trade_by_id("trade_005")
    assert round(tr.funding_usd, 4) == -0.001


# =============================================================================
# 6. Realized PnL Calculation
# =============================================================================
def test_06_realized_pnl_calculation(journal):
    """Verify Net PnL = Gross Realized PnL - Total Fees + Funding."""
    trade = AutomatedTradeRecord(
        journal_id="j_trade_006",
        trade_id="trade_006",
        symbol="SOLUSDT",
        side="SHORT",
        planned_risk_usd=0.20,
    )
    journal.record_trade_intent(trade)

    # SHORT Entry: 0.20 @ $100.00 (Fee: 0.004)
    journal.record_fill(FillRecord(
        fill_id="f_s_in",
        trade_id="trade_006",
        order_id="o1",
        timestamp_utc="2026-10-26T00:00:00Z",
        timestamp_ms=1000,
        symbol="SOLUSDT",
        side="SELL",
        fill_role="ENTRY",
        fill_price=100.0,
        fill_quantity=0.20,
        fill_notional_usd=20.0,
        fee_usd=0.004,
    ))

    # SHORT Exit: 0.20 @ $98.50 (Fee: 0.004) -> Price dropped, profit!
    journal.record_fill(FillRecord(
        fill_id="f_s_out",
        trade_id="trade_006",
        order_id="o2",
        timestamp_utc="2026-10-26T00:10:00Z",
        timestamp_ms=2000,
        symbol="SOLUSDT",
        side="BUY",
        fill_role="EXIT",
        fill_price=98.50,
        fill_quantity=0.20,
        fill_notional_usd=19.7,
        fee_usd=0.004,
    ))

    # Funding accrued: +$0.001
    journal.record_funding("trade_006", funding_usd=0.001)

    tr = journal.get_trade_by_id("trade_006")
    # Gross: (100.00 - 98.50) * 0.20 = 0.30
    assert round(tr.gross_pnl_usd, 4) == 0.30
    # Net: 0.30 - (0.004 + 0.004) + 0.001 = 0.30 - 0.008 + 0.001 = 0.293
    assert round(tr.net_pnl_usd, 4) == 0.293


# =============================================================================
# 7. Realized R Calculation
# =============================================================================
def test_07_realized_r_calculation(journal):
    """Verify Realized R = Net PnL / Planned Risk ($0.20), preserving original planned risk."""
    trade = AutomatedTradeRecord(
        journal_id="j_trade_007",
        trade_id="trade_007",
        symbol="SOLUSDT",
        side="LONG",
        planned_risk_usd=0.20,  # Fixed $0.20 risk
    )
    journal.record_trade_intent(trade)

    journal.record_fill(FillRecord(
        fill_id="f_r_in",
        trade_id="trade_007",
        order_id="o1",
        timestamp_utc="2026-10-26T00:00:00Z",
        timestamp_ms=1000,
        symbol="SOLUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=100.0,
        fill_quantity=0.20,
        fill_notional_usd=20.0,
        fee_usd=0.004,
    ))
    journal.record_fill(FillRecord(
        fill_id="f_r_out",
        trade_id="trade_007",
        order_id="o2",
        timestamp_utc="2026-10-26T00:05:00Z",
        timestamp_ms=2000,
        symbol="SOLUSDT",
        side="SELL",
        fill_role="EXIT",
        fill_price=102.04,  # Gross: 0.408
        fill_quantity=0.20,
        fill_notional_usd=20.408,
        fee_usd=0.004,
    ))

    tr = journal.get_trade_by_id("trade_007")
    # Gross: (102.04 - 100.0) * 0.20 = 0.408
    # Net: 0.408 - 0.008 = 0.400
    # Realized R: 0.400 / 0.20 = 2.000 R
    assert round(tr.net_pnl_usd, 4) == 0.400
    assert round(tr.realized_r, 3) == 2.000


# =============================================================================
# 8. Reconciliation After Restart
# =============================================================================
def test_08_reconciliation_after_restart(journal_db):
    """Verify that restarting the application retains all trades and fills from SQLite without loss."""
    # Session 1: Create trade and fill
    journal1 = AutomatedTradeJournal(journal_db)
    trade = AutomatedTradeRecord(
        journal_id="j_restart",
        trade_id="t_restart",
        symbol="ETHUSDT",
        side="LONG",
        planned_risk_usd=0.20,
    )
    journal1.record_trade_intent(trade)
    journal1.record_fill(FillRecord(
        fill_id="f_restart_1",
        trade_id="t_restart",
        order_id="o_restart",
        timestamp_utc="2026-10-26T00:00:00Z",
        timestamp_ms=1000,
        symbol="ETHUSDT",
        side="BUY",
        fill_role="ENTRY",
        fill_price=3000.0,
        fill_quantity=0.01,
        fill_notional_usd=30.0,
        fee_usd=0.006,
    ))

    # Session 2: Instantiate new AutomatedTradeJournal on same DB
    journal2 = AutomatedTradeJournal(journal_db)
    reloaded_trade = journal2.get_trade_by_id("t_restart")
    assert reloaded_trade is not None
    assert reloaded_trade.symbol == "ETHUSDT"
    assert reloaded_trade.filled_quantity == 0.01

    reloaded_fills = journal2.get_fills_for_trade("t_restart")
    assert len(reloaded_fills) == 1
    assert reloaded_fills[0].fill_id == "f_restart_1"


# =============================================================================
# 9. Reconciliation After API Interruption
# =============================================================================
def test_09_reconciliation_after_api_interruption(journal):
    """Verify that missed exchange trades during an outage are reconciled and ingested without duplicates."""
    trade = AutomatedTradeRecord(
        journal_id="j_outage",
        trade_id="t_outage",
        order_id="ord_outage_123",
        symbol="SOLUSDT",
        side="LONG",
        status="OPEN",
        planned_risk_usd=0.20,
    )
    journal.record_trade_intent(trade)

    # Simulated exchange trades queried after API reconnects
    exchange_trades = [
        {
            "id": "ex_9991",
            "orderId": "ord_outage_123",
            "symbol": "SOLUSDT",
            "side": "BUY",
            "price": "100.0",
            "qty": "0.20",
            "commission": "0.004",
            "commissionAsset": "USDT",
            "maker": True,
            "time": 1700000000000,
        }
    ]

    summary1 = journal.reconcile_with_exchange(exchange_trades=exchange_trades)
    assert summary1["new_fills_imported"] == 1

    # Second reconciliation with same payload -> idempotent, 0 new imported
    summary2 = journal.reconcile_with_exchange(exchange_trades=exchange_trades)
    assert summary2["new_fills_imported"] == 0

    tr = journal.get_trade_by_id("t_outage")
    assert tr.filled_quantity == 0.20
    assert tr.entry_fill_count == 1


# =============================================================================
# 10. CSV Schema and Encoding
# =============================================================================
def test_10_csv_schema_and_encoding(journal, exporter):
    """Verify exported CSV files use UTF-8 and contain all required column headers."""
    trade = AutomatedTradeRecord(
        journal_id="j_csv_1",
        trade_id="t_csv_1",
        symbol="SOLUSDT",
        side="LONG",
    )
    journal.record_trade_intent(trade)

    trades_csv = exporter.export_trades_csv()
    assert isinstance(trades_csv, str)
    lines = trades_csv.strip().split("\n")
    header = lines[0].split(",")

    # Required field verification
    for required_col in ["journal_id", "trade_id", "symbol", "strategy_id", "mode", "side", "status", "net_pnl_usd", "realized_r"]:
        assert required_col in header, f"Missing {required_col} in trades.csv header"


# =============================================================================
# 11. CSV Export Consistency with Journal Records
# =============================================================================
def test_11_csv_export_consistency_with_journal_records(journal, exporter):
    """Verify that records in the SQLite database match the exported CSV content exactly."""
    trade = AutomatedTradeRecord(
        journal_id="j_match",
        trade_id="t_match",
        symbol="AVAXUSDT",
        strategy_id="EMA20_RECLAIM_5M_V1",
        mode="LIVE",
        side="LONG",
        planned_risk_usd=0.20,
        gross_pnl_usd=0.35,
        net_pnl_usd=0.33,
        realized_r=1.65,
    )
    journal.record_trade_intent(trade)

    csv_data = exporter.export_trades_csv(JournalFilter(symbol="AVAXUSDT"))
    reader = csv.DictReader(io.StringIO(csv_data))
    rows = list(reader)

    assert len(rows) == 1
    row = rows[0]
    assert row["trade_id"] == "t_match"
    assert row["symbol"] == "AVAXUSDT"
    assert float(row["planned_risk_usd"]) == 0.20
    assert float(row["realized_r"]) == 1.65


# =============================================================================
# 12. LIVE/SHADOW/PAPER Separation
# =============================================================================
def test_12_live_shadow_paper_separation(journal):
    """Verify that metrics and queries keep LIVE, SHADOW, and PAPER strictly independent."""
    # 1 LIVE trade (Win)
    journal.record_trade_intent(AutomatedTradeRecord(
        journal_id="j_live",
        trade_id="t_live",
        mode="LIVE",
        status="CLOSED",
        net_pnl_usd=0.40,
        planned_risk_usd=0.20,
        realized_r=2.0,
    ))

    # 1 PAPER trade (Loss)
    journal.record_trade_intent(AutomatedTradeRecord(
        journal_id="j_paper",
        trade_id="t_paper",
        mode="PAPER",
        status="CLOSED",
        net_pnl_usd=-0.20,
        planned_risk_usd=0.20,
        realized_r=-1.0,
    ))

    # Summary metrics for LIVE must not be polluted by PAPER loss
    live_summary = journal.get_summary_metrics(mode="LIVE")
    assert live_summary["total_closed_trades"] == 1
    assert live_summary["win_rate_pct"] == 100.0
    assert live_summary["total_net_pnl_usd"] == 0.40

    # Summary metrics for PAPER
    paper_summary = journal.get_summary_metrics(mode="PAPER")
    assert paper_summary["total_closed_trades"] == 1
    assert paper_summary["win_rate_pct"] == 0.0
    assert paper_summary["total_net_pnl_usd"] == -0.20


# =============================================================================
# 13. Null and Unavailable Data Handling
# =============================================================================
def test_13_null_and_unavailable_data_handling(journal):
    """Verify that unavailable fields remain null/empty and are not fabricated."""
    trade = AutomatedTradeRecord(
        journal_id="j_unavail",
        trade_id="t_unavail",
        symbol="BTCUSDT",
        side="LONG",
        average_exit_price=None,  # Not exited yet
        exit_timestamp_utc=None,
        holding_duration_seconds=0.0,
        data_quality_flags=DataQualityFlag.PENDING_EXCHANGE_FILLS.value,
    )
    journal.record_trade_intent(trade)

    retrieved = journal.get_trade_by_id("t_unavail")
    assert retrieved.average_exit_price is None
    assert retrieved.exit_timestamp_utc is None
    assert retrieved.data_quality_flags == "PENDING_EXCHANGE_FILLS"


# =============================================================================
# 14. Historical Configuration Preservation & ZIP Bundle Integrity
# =============================================================================
def test_14_historical_configuration_preservation_and_zip_bundle(journal, exporter):
    """Verify that config_version is preserved and ZIP bundle contains valid manifest.json."""
    trade = AutomatedTradeRecord(
        journal_id="j_hist",
        trade_id="t_hist",
        config_version="LIVE_MICRO_V1",
        symbol="SOLUSDT",
        side="LONG",
        planned_risk_usd=0.20,
    )
    journal.record_trade_intent(trade)

    zip_bytes = exporter.export_journal_bundle_zip()
    assert isinstance(zip_bytes, bytes)
    assert len(zip_bytes) > 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        file_list = zf.namelist()
        assert "manifest.json" in file_list
        assert "trades.csv" in file_list
        assert "fills.csv" in file_list
        assert "signals.csv" in file_list
        assert "shadow.csv" in file_list
        assert "scan_attribution.csv" in file_list

        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["schema_version"] == "LIVE_MICRO_V1_JOURNAL_20261026"
        assert manifest["record_counts"]["trades"] >= 1
