"""Unit and Integration Tests for AsterDEX-Inspired Entry & Position View.

Audits and verifies:
1. Exact fee-adjusted break-even formulas for LONG and SHORT (Net PnL == 0).
2. Distinguishes fee-only break-even from break-even including slippage.
3. Planned entry vs market price: stale/infeasible candidates never appear actionable.
4. Risk/reward consistency: verifies dynamic gross R:R matching entry, SL, and TP prices.
5. Reference price provenance: labeled as 1H closed candle reference, keeping source timestamp visible.
6. Structural Invalidation Level vs Stop Loss separation.
7. Unrealized PnL & ROE semantics (N/A when no open position; calculated when open).
8. Dashboard HTML template integrity (provenance labels, non-actionable badge, toggle function).
"""

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from src.core.types import (
    CandidateStatus,
    Direction,
    ExecutionStatus,
    MarketBias,
    RiskAssessment,
    SetupCandidate,
    StructureState,
)
from src.api.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_long_setup_exact_breakeven_formulas():
    """Verify exact closed-form break-even for LONG positions where Net PnL == 0.
    
    Formula: P_x = P_e * (1 + f_open) / (1 - f_close)
    """
    entry = 50000.0
    fee_rate = 0.0005  # 0.05% taker

    cand = SetupCandidate(
        candidate_id="SETUP_TEST_LONG_EXACT_BE",
        symbol="BTCUSDT",
        timestamp_ms=1700000000000,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=entry,
        stop_loss=49000.0,
        take_profit_1=52000.0,
        gross_reward_to_risk=2.0,
        net_reward_to_risk=1.9,
        invalidation_price=48800.0,
        invalidation_reason="1H candle close below 48800 breaks structure.",
        estimated_fee_rate=fee_rate,
        estimated_slippage_rate=0.0005,
        status=CandidateStatus.READY,
    )

    # 1. Exact fee-only break-even
    expected_be_fee = round(entry * (1.0 + fee_rate) / (1.0 - fee_rate), 6)
    assert cand.calculated_break_even_fee_only == expected_be_fee
    assert cand.calculated_break_even_fee_only == 50050.025013
    assert cand.calculated_break_even_fee_only > entry

    # Verify Net PnL is mathematically 0 at break-even price:
    # Net PnL = (P_x - P_e) - (P_e * f_open) - (P_x * f_close)
    exit_p = cand.calculated_break_even_fee_only
    entry_fee = entry * fee_rate
    exit_fee = exit_p * fee_rate
    gross_pnl = exit_p - entry
    net_pnl = gross_pnl - entry_fee - exit_fee
    assert abs(net_pnl) < 1e-4  # Zero net PnL

    # 2. Break-even with execution slippage included
    # Formula: P_x = P_e * [(1 + s)*(1 + f)] / [(1 - s)*(1 - f)]
    slip = 0.0005
    expected_be_slip = round(entry * ((1.0 + slip) * (1.0 + fee_rate)) / ((1.0 - slip) * (1.0 - fee_rate)), 6)
    assert cand.calculated_break_even_with_slippage == expected_be_slip
    assert cand.calculated_break_even_with_slippage == 50100.100075
    assert cand.calculated_break_even_with_slippage > cand.calculated_break_even_fee_only

    # Serialization
    d = cand.to_dict()
    assert d["break_even_price"] == 50050.025013
    assert d["break_even_price_fee_only"] == 50050.025013
    assert d["break_even_price_with_slippage"] == 50100.100075


def test_short_setup_exact_breakeven_formulas():
    """Verify exact closed-form break-even for SHORT positions where Net PnL == 0.
    
    Formula: P_x = P_e * (1 - f_open) / (1 + f_close)
    """
    entry = 3000.0
    fee_rate = 0.0005  # 0.05% taker

    cand = SetupCandidate(
        candidate_id="SETUP_TEST_SHORT_EXACT_BE",
        symbol="ETHUSDT",
        timestamp_ms=1700000000000,
        direction=Direction.SHORT,
        bias_4h=MarketBias.BEARISH,
        structure_1h=StructureState.BEARISH,
        entry_price=entry,
        stop_loss=3060.0,
        take_profit_1=2880.0,
        gross_reward_to_risk=2.0,
        net_reward_to_risk=1.85,
        invalidation_price=3080.0,
        invalidation_reason="1H candle close above 3080 breaks structure.",
        estimated_fee_rate=fee_rate,
        estimated_slippage_rate=0.0005,
        status=CandidateStatus.READY,
    )

    # 1. Exact fee-only break-even
    expected_be_fee = round(entry * (1.0 - fee_rate) / (1.0 + fee_rate), 6)
    assert cand.calculated_break_even_fee_only == expected_be_fee
    assert cand.calculated_break_even_fee_only == 2997.001499
    assert cand.calculated_break_even_fee_only < entry

    # Verify Net PnL is mathematically 0 at break-even price:
    # Net PnL = (P_e - P_x) - (P_e * f_open) - (P_x * f_close)
    exit_p = cand.calculated_break_even_fee_only
    entry_fee = entry * fee_rate
    exit_fee = exit_p * fee_rate
    gross_pnl = entry - exit_p
    net_pnl = gross_pnl - entry_fee - exit_fee
    assert abs(net_pnl) < 1e-4  # Zero net PnL

    # 2. Break-even with execution slippage included
    # Formula: P_x = P_e * [(1 - s)*(1 - f)] / [(1 + s)*(1 + f)]
    slip = 0.0005
    expected_be_slip = round(entry * ((1.0 - slip) * (1.0 - fee_rate)) / ((1.0 + slip) * (1.0 + fee_rate)), 6)
    assert cand.calculated_break_even_with_slippage == expected_be_slip
    assert cand.calculated_break_even_with_slippage == 2994.005996
    assert cand.calculated_break_even_with_slippage < cand.calculated_break_even_fee_only

    # Serialization
    d = cand.to_dict()
    assert d["break_even_price"] == 2997.001499
    assert d["break_even_price_fee_only"] == 2997.001499
    assert d["break_even_price_with_slippage"] == 2994.005996


def test_gross_rr_consistency_long_and_short():
    """Verify that calculated gross R:R matches actual entry, SL, and TP1 prices."""
    # LONG: Entry 100, SL 95 (risk 5), TP1 110 (reward 10) -> 2.0R
    cand_long = SetupCandidate(
        candidate_id="RR_LONG",
        symbol="SOLUSDT",
        timestamp_ms=1700000000000,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit_1=110.0,
        gross_reward_to_risk=2.0,
        status=CandidateStatus.READY,
    )
    assert cand_long.calculated_gross_rr == 2.0
    assert abs(cand_long.calculated_gross_rr - cand_long.gross_reward_to_risk) < 0.001

    # SHORT: Entry 200, SL 210 (risk 10), TP1 176 (reward 24) -> 2.4R
    cand_short = SetupCandidate(
        candidate_id="RR_SHORT",
        symbol="AVAXUSDT",
        timestamp_ms=1700000000000,
        direction=Direction.SHORT,
        bias_4h=MarketBias.BEARISH,
        structure_1h=StructureState.BEARISH,
        entry_price=200.0,
        stop_loss=210.0,
        take_profit_1=176.0,
        gross_reward_to_risk=2.4,
        status=CandidateStatus.READY,
    )
    assert cand_short.calculated_gross_rr == 2.4
    assert abs(cand_short.calculated_gross_rr - cand_short.gross_reward_to_risk) < 0.001


def test_stale_or_infeasible_not_rendered_actionable_by_reference_price(client):
    """Verify that stale or infeasible candidates never display as executable or actionable."""
    response = client.get("/api/setups")
    assert response.status_code == 200
    data = response.json()

    for s in data["setups"]:
        if s["status"] in ("STALE", "INFEASIBLE", "INVALIDATED", "WATCHLIST"):
            assert s["is_executable"] is False
            # Execution status remains decoupled
            assert s["execution_status"] in (
                "NOT_SUBMITTED",
                "SUBMITTED",
                "SUBMITTING",
                "REJECTED",
                "CLOSED",
                "FILLED",
                "PROTECTED",
            )


def test_reference_price_provenance_and_data_age():
    """Verify that reference price provenance is explicitly tracked and distinguished from live ticker."""
    cand = SetupCandidate(
        candidate_id="PROVENANCE_TEST",
        symbol="BTCUSDT",
        timestamp_ms=1700000000000,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=50000.0,
        stop_loss=49000.0,
        take_profit_1=52000.0,
        reference_price=50120.0,
        reference_price_time_utc="2026-10-17T01:00:00+00:00",
        reference_price_type="1H_CLOSED_CANDLE",
        reference_data_age_hours=3.5,
        is_reference_data_stale=True,
    )

    d = cand.to_dict()
    assert d["reference_price"] == 50120.0
    assert d["reference_price_type"] == "1H_CLOSED_CANDLE"
    assert d["reference_price_time_utc"] == "2026-10-17T01:00:00+00:00"
    assert d["reference_data_age_hours"] == 3.5
    assert d["is_reference_data_stale"] is True


def test_unrealized_pnl_semantics_no_open_position():
    """Verify that when no position is open, PnL and ROE are explicitly None."""
    cand = SetupCandidate(
        candidate_id="SETUP_TEST_NO_POS",
        symbol="SOLUSDT",
        timestamp_ms=1700000000000,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=100.0,
        stop_loss=97.0,
        take_profit_1=104.5,
        status=CandidateStatus.WATCHLIST,
        execution_status=ExecutionStatus.NOT_SUBMITTED,
    )

    d = cand.to_dict()
    assert d["unrealized_pnl_usdt"] is None
    assert d["roe_pct"] is None


def test_dashboard_html_contains_audited_provenance_and_dynamic_target(client):
    """Verify that dashboard HTML contains audited provenance labels, non-actionable badges, and dynamic targets."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    # Header badge
    assert "PAPER / RESEARCH — NO LIVE ORDERS" in html

    # Audited provenance labels
    assert "1H Candle Ref Price" in html
    assert "1H Candle Store (Local DB)" in html
    assert "Fee-Only Break-Even" in html
    assert "Break-Even (+Slip)" in html
    assert "NON-ACTIONABLE / REFERENCE ONLY" in html

    # Distinct structural invalidation level
    assert "STRUCTURAL INVALIDATION LEVEL" in html
    assert "Distinct from Stop Loss" in html

    # Collapsible SMC & SnR Research Evidence
    assert "Institutional SMC &amp; SnR Research Evidence" in html or "Institutional SMC & SnR Research Evidence" in html
    assert "toggleAccordion" in html


def test_api_status_reports_paper_mode_and_live_locked(client):
    """Verify safety invariants in /api/status."""
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()

    assert data["mode"] == "PAPER"
    assert data["live_trading_locked"] is True
