"""Unit tests for Dashboard Intelligence Confluence Gates and Web Payload Integration."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

from src.intelligence.dashboard_confluence import (
    build_web_intelligence_payload,
    evaluate_dashboard_confluence_gates,
    build_qwen_dashboard_prompt_block,
    ConfluenceGateResult,
)


@pytest.fixture
def base_web_intelligence():
    """Provides a valid baseline web intelligence payload for SOLUSDT LONG in Discount."""
    return {
        "liquidity_sweep_and_reclaim_chart_state": {
            "symbol": "SOLUSDT",
            "dealing_range_72h_pct": 28.5,
            "dealing_range_high": 104.97,
            "dealing_range_low": 98.57,
            "zone_status": "DISCOUNT",
            "15m_swing_high": 102.26,
            "15m_swing_low": 100.50,
            "1h_swing_high": 102.26,
            "1h_swing_low": 99.84,
            "recent_sweep_detected": True,
            "sweep_type": "BULL_SWEEP_LOW",
            "swept_level": 100.50,
            "reclaim_confirmed": True,
            "rejection_wick_ratio": 0.42,
            "nearest_unmitigated_bull_fvg": [99.80, 100.40],
            "nearest_unmitigated_bear_fvg": None,
        },
        "market_liquidity_and_orderflow_intelligence": {
            "orderbook_imbalance_ratio": 0.542,  # 54.2% Bids
            "bid_depth_top20_usdt": 3500000.0,
            "ask_depth_top20_usdt": 2950000.0,
            "nearest_bid_wall": {
                "price": 99.80,
                "size_usdt": 480000.0,
                "distance_pct": 0.35,
            },
            "nearest_ask_wall": {
                "price": 103.50,
                "size_usdt": 350000.0,
                "distance_pct": 1.95,  # > 0.50% away (clear)
            },
            "spread_bps": 0.95,
            "funding_rate_pct": 0.005,
            "volume_24h_usdt": 45000000.0,
            "l2_gate_passed": True,
        },
        "recent_journal_losses_and_blocked_zones": {
            "symbol": "SOLUSDT",
            "recent_sl_levels_6h": [],
            "has_recent_loss_in_6h": False,
            "recent_loss_count_6h": 0,
        },
    }


class TestDashboardConfluenceGates:
    """Test suite for the 4 non-negotiable Python dashboard confluence gates."""

    # -------------------------------------------------------------------------
    # 1. Sweep & Reclaim Gate Tests
    # -------------------------------------------------------------------------
    def test_sweep_gate_rejects_long_in_top_half_dealing_range(self, base_web_intelligence):
        """LONG is strictly vetoed if 72H dealing range is > 38.0% unless Bull FVG pullback."""
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["dealing_range_72h_pct"] = 58.4
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["nearest_unmitigated_bull_fvg"] = None

        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=101.45,
            stop_loss_price=100.15,
            intelligence=base_web_intelligence,
        )
        assert not res.passed
        assert res.gate_name == "SWEEP_RECLAIM_GATE"
        assert "Hard Law 2: No buying top half" in res.rejection_reason

    def test_sweep_gate_rejects_long_without_sweep_or_fvg(self, base_web_intelligence):
        """LONG is vetoed if there is neither a confirmed sweep nor an active Bull FVG."""
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["recent_sweep_detected"] = False
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["reclaim_confirmed"] = False
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["nearest_unmitigated_bull_fvg"] = None

        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=100.40,
            stop_loss_price=99.10,
            intelligence=base_web_intelligence,
        )
        assert not res.passed
        assert res.gate_name == "SWEEP_RECLAIM_GATE"
        assert "No confirmed structural liquidity sweep" in res.rejection_reason

    def test_sweep_gate_passes_long_with_confirmed_discount_sweep(self, base_web_intelligence):
        """LONG passes when confirmed sweep + reclaim occurs in discount (28.5%)."""
        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=100.60,
            stop_loss_price=99.30,  # 1.29% stop
            intelligence=base_web_intelligence,
        )
        assert res.passed
        assert "Confirmed BULL_SWEEP_LOW" in res.sweep_reclaim_evidence

    def test_sweep_gate_rejects_short_in_bottom_half_dealing_range(self, base_web_intelligence):
        """SHORT is strictly vetoed if 72H dealing range is < 65.0% (Hard Law 1)."""
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["dealing_range_72h_pct"] = 42.0

        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="SHORT",
            entry_price=100.60,
            stop_loss_price=101.90,
            intelligence=base_web_intelligence,
        )
        assert not res.passed
        assert res.gate_name == "SWEEP_RECLAIM_GATE"
        assert "Hard Law 1: No shorting bottom half" in res.rejection_reason

    # -------------------------------------------------------------------------
    # 2. Order Flow Intelligence Gate Tests
    # -------------------------------------------------------------------------
    def test_order_flow_gate_rejects_unsupportive_imbalance_for_long(self, base_web_intelligence):
        """LONG is rejected if orderbook imbalance ratio is < 0.48."""
        base_web_intelligence["market_liquidity_and_orderflow_intelligence"]["orderbook_imbalance_ratio"] = 0.45

        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=100.60,
            stop_loss_price=99.30,
            intelligence=base_web_intelligence,
        )
        assert not res.passed
        assert res.gate_name == "ORDER_FLOW_GATE"
        assert "below 0.48 minimum supportive threshold" in res.rejection_reason

    def test_order_flow_gate_rejects_blocking_ask_wall_above_long_entry(self, base_web_intelligence):
        """LONG is rejected if a massive ask wall (>= $5k) blocks within < 0.50% above entry."""
        # Entry at 100.60, massive ask wall at 100.80 (+0.199% above entry)
        base_web_intelligence["market_liquidity_and_orderflow_intelligence"]["nearest_ask_wall"] = {
            "price": 100.80,
            "size_usdt": 350000.0,
            "distance_pct": 0.20,
        }

        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=100.60,
            stop_loss_price=99.30,
            intelligence=base_web_intelligence,
        )
        assert not res.passed
        assert res.gate_name == "ORDER_FLOW_GATE"
        assert "Massive ask wall" in res.rejection_reason
        assert "< 0.50% ceiling" in res.rejection_reason

    def test_order_flow_gate_rejects_blocking_bid_wall_below_short_entry(self, base_web_intelligence):
        """SHORT is rejected if a massive bid wall blocks within < 0.50% below entry."""
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["dealing_range_72h_pct"] = 72.0
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["recent_sweep_detected"] = True
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["sweep_type"] = "BEAR_SWEEP_HIGH"
        base_web_intelligence["liquidity_sweep_and_reclaim_chart_state"]["reclaim_confirmed"] = True
        base_web_intelligence["market_liquidity_and_orderflow_intelligence"]["orderbook_imbalance_ratio"] = 0.42

        # Short entry at 103.00, massive bid wall at 102.80 (only 0.19% below entry)
        base_web_intelligence["market_liquidity_and_orderflow_intelligence"]["nearest_bid_wall"] = {
            "price": 102.80,
            "size_usdt": 400000.0,
            "distance_pct": 0.19,
        }

        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="SHORT",
            entry_price=103.00,
            stop_loss_price=104.30,
            intelligence=base_web_intelligence,
        )
        assert not res.passed
        assert res.gate_name == "ORDER_FLOW_GATE"
        assert "Massive bid wall" in res.rejection_reason
        assert "< 0.50% floor" in res.rejection_reason

    # -------------------------------------------------------------------------
    # 3. Anti-Repeat "Same Hole" Lock (Journal Memory Gate - 6 Hours)
    # -------------------------------------------------------------------------
    def test_same_hole_lock_forbids_rebuying_around_previous_sl(self, base_web_intelligence):
        """If SOLUSDT hit SL at 100.59 in last 6h, entering LONG around 101.45 is strictly forbidden."""
        now = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)
        base_web_intelligence["recent_journal_losses_and_blocked_zones"]["recent_sl_levels_6h"] = [
            {
                "trade_id": "TRD_SOL_1",
                "symbol": "SOLUSDT",
                "side": "LONG",
                "entry_price": 101.38,
                "sl_exit_price": 100.59,
                "exit_timestamp_utc": (now - timedelta(hours=2)).isoformat(),
                "age_minutes": 120.0,
                "failure_reasons": ["TIGHT_STOP_<0.80PCT"],
            }
        ]

        # Attempt to buy at 101.45 (above previous SL exit 100.59)
        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=101.45,
            stop_loss_price=100.15,
            intelligence=base_web_intelligence,
            current_time=now,
        )
        assert not res.passed
        assert res.gate_name == "SAME_HOLE_MEMORY_GATE"
        assert "repeats the same losing zone" in res.rejection_reason

    def test_same_hole_lock_permits_entry_when_price_sweeps_below_previous_sl(self, base_web_intelligence):
        """If price sweeps below previous SL (100.59) to 99.30 (or >= 1.20% deeper), entry is permitted."""
        now = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)
        base_web_intelligence["recent_journal_losses_and_blocked_zones"]["recent_sl_levels_6h"] = [
            {
                "trade_id": "TRD_SOL_1",
                "symbol": "SOLUSDT",
                "side": "LONG",
                "entry_price": 101.38,
                "sl_exit_price": 100.59,
                "exit_timestamp_utc": (now - timedelta(hours=2)).isoformat(),
                "age_minutes": 120.0,
                "failure_reasons": ["TIGHT_STOP_<0.80PCT"],
            }
        ]

        # Entry at 99.30 (swept below 100.59 and is 1.28% deeper)
        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=99.30,
            stop_loss_price=98.00,  # 1.31% stop
            intelligence=base_web_intelligence,
            current_time=now,
        )
        assert res.passed
        assert "Entry 99.3000 is structurally deeper" in res.why_not_repeating_past_sl

    # -------------------------------------------------------------------------
    # 4. Stop-Loss Breathing Room Gate Tests
    # -------------------------------------------------------------------------
    def test_stop_breathing_room_rejects_tight_stop_under_1_15_pct(self, base_web_intelligence):
        """Stop distance of 0.70% is strictly rejected."""
        # Entry at 100.60, SL at 99.90 (0.696% stop)
        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=100.60,
            stop_loss_price=99.90,
            intelligence=base_web_intelligence,
        )
        assert not res.passed
        assert res.gate_name == "STOP_BREATHING_ROOM_GATE"
        assert "below the 1.15% structural breathing room minimum floor" in res.rejection_reason

    def test_stop_breathing_room_rejects_wide_stop_over_1_45_pct(self, base_web_intelligence):
        """Stop distance > 1.45% is strictly rejected."""
        # Entry at 100.60, SL at 99.00 (1.59% stop)
        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=100.60,
            stop_loss_price=99.00,
            intelligence=base_web_intelligence,
        )
        assert not res.passed
        assert res.gate_name == "STOP_BREATHING_ROOM_GATE"
        assert "exceeds the 1.45% maximum risk boundary" in res.rejection_reason

    # -------------------------------------------------------------------------
    # 5. Full Confluence Success & Dossier Prompt Formatting
    # -------------------------------------------------------------------------
    def test_all_four_gates_pass_with_full_evidence(self, base_web_intelligence):
        """When all 4 modules agree, gates pass and complete evidence strings are returned."""
        res = evaluate_dashboard_confluence_gates(
            symbol="SOLUSDT",
            direction="LONG",
            entry_price=100.60,
            stop_loss_price=99.30,  # 1.29% stop
            intelligence=base_web_intelligence,
        )
        assert res.passed
        assert res.sweep_reclaim_evidence != ""
        assert res.orderflow_wall_evidence != ""
        assert res.why_not_repeating_past_sl != ""

    def test_qwen_dashboard_prompt_block_formatting(self, base_web_intelligence):
        """Dossier prompt builder outputs clean Markdown formatted with all 3 modules."""
        block = build_qwen_dashboard_prompt_block([base_web_intelligence])
        assert "WEB DASHBOARD INTELLIGENCE & ORDER FLOW DOSSIER" in block
        assert "Candidate 1: SOLUSDT" in block
        assert "72H Dealing Range at 28.5% (DISCOUNT)" in block
        assert "Top-20 Imbalance: 54.2% Bids" in block
        assert "Nearest Bid Wall: 99.8" in block
