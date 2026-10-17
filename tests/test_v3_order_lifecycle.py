"""Automated Failure and Lifecycle Tests for Aster Futures V3 Order Execution.

Tests all 9 mandatory failure and recovery scenarios:
1. Entry succeeds; SL and TP both succeed.
2. Entry succeeds; SL fails (transitions to UNPROTECTED, halts entries, executes emergency policy).
3. Entry succeeds; TP fails (transitions to PARTIALLY_PROTECTED, alerts).
4. Entry is partially filled (protection sized to actual executedQty, not intended quantity).
5. Entry request times out with unknown outcome (reconciled by origClientOrderId).
6. Exchange rejects an SL/TP price (unprotected transition, emergency handling).
7. Bot restarts with an open position but missing protection (startup reconciliation alert).
8. Duplicate requests and retries do not create duplicate positions or protective orders.
9. LONG and SHORT protection sides are strictly verified (closing opposite to entry).
"""

from typing import Optional
from unittest.mock import MagicMock
import pytest

from src.core.types import CandidateStatus, Direction, MarketBias, RiskAssessment, SetupCandidate, StructureState
from src.execution.v3_lifecycle import EmergencyPolicy, ManagedPosition, ProtectionState, V3OrderLifecycleManager
from src.risk.manager import RiskManager


def make_candidate(
    symbol: str = "BTCUSDT",
    direction: Direction = Direction.LONG,
    qty: float = 0.005,
    now_ms: Optional[int] = None,
) -> SetupCandidate:
    import time
    ts = now_ms or int(time.time() * 1000)
    assessment = RiskAssessment(
        symbol=symbol,
        entry_price=60000.0,
        stop_loss=59000.0 if direction == Direction.LONG else 61000.0,
        stop_distance_pct=0.016,
        risk_budget_usdt=0.09,
        position_notional_usdt=300.0,
        quantity=qty,
        required_margin_usdt=5.0,
        effective_leverage=10.0,
        estimated_fee_usdt=0.03,
        estimated_slippage_usdt=0.03,
        is_feasible=True,
    )
    return SetupCandidate(
        candidate_id=f"SETUP_{symbol}_{direction.value}_001",
        symbol=symbol,
        timestamp_ms=ts,
        direction=direction,
        bias_4h=MarketBias.BULLISH if direction == Direction.LONG else MarketBias.BEARISH,
        structure_1h=StructureState.BULLISH if direction == Direction.LONG else StructureState.BEARISH,
        entry_price=60000.0,
        stop_loss=59000.0 if direction == Direction.LONG else 61000.0,
        take_profit_1=62000.0 if direction == Direction.LONG else 58000.0,
        invalidation_price=59000.0 if direction == Direction.LONG else 61000.0,
        status=CandidateStatus.READY,
        expires_at_ms=ts + (12 * 3600 * 1000),
        risk_assessment=assessment,
    )


@pytest.fixture
def risk_mgr():
    return RiskManager(initial_equity_usdt=16.0, trade_risk_usdt=0.09, max_open_positions=1)


class TestV3OrderLifecycleAndFailureScenarios:
    """Rigorous tests covering all 9 failure and lifecycle safety requirements."""

    def test_1_entry_and_protection_both_succeed(self, risk_mgr):
        """1. Entry succeeds; SL and TP both succeed."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"}, # Entry
            {"orderId": 102, "status": "NEW"}, # SL
            {"orderId": 103, "status": "NEW"}, # TP
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        assert pos.entry_status == "FILLED"
        assert pos.is_open is True
        assert pos.protection_state == ProtectionState.PROTECTED
        assert pos.sl_order_id == "102"
        assert pos.tp_order_id == "103"
        assert manager.entries_frozen is False

    def test_2_entry_succeeds_sl_fails_unprotected_emergency(self, risk_mgr):
        """2. Entry succeeds; SL fails -> marks UNPROTECTED, halts entries, executes emergency close."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"}, # Entry
            Exception("Exchange error: Stop Loss rejected (-2010 Insufficient Balance)"), # SL Fails
            {"orderId": 103, "status": "NEW"}, # TP
            {"orderId": 104, "status": "FILLED"}, # Emergency market close
        ]

        manager = V3OrderLifecycleManager(
            adapter=adapter,
            risk_manager=risk_mgr,
            emergency_policy=EmergencyPolicy.CLOSE_MARKET_IMMEDIATELY,
        )
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        # Invariant: Never silently treat position as protected
        assert pos.sl_order_id is None
        assert pos.protection_state == ProtectionState.EMERGENCY_CLOSED
        assert manager.entries_frozen is True
        assert len(manager.critical_alerts) >= 1
        assert "CRITICAL SAFETY VIOLATION" in manager.critical_alerts[0]["message"]

    def test_3_entry_succeeds_tp_fails(self, risk_mgr):
        """3. Entry succeeds; TP fails -> transitions to PARTIALLY_PROTECTED and alerts."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"}, # Entry
            {"orderId": 102, "status": "NEW"}, # SL succeeds
            Exception("Take Profit price exceeds limit"), # TP fails
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        assert pos.entry_status == "FILLED"
        assert pos.sl_order_id == "102"
        assert pos.tp_order_id is None
        assert pos.protection_state == ProtectionState.PARTIALLY_PROTECTED
        assert len(pos.alerts) >= 1
        assert "TP_SUBMISSION_FAILED" in pos.alerts[0]

    def test_4_entry_partially_filled_protection_resized(self, risk_mgr):
        """4. Entry is partially filled -> SL/TP sized to actual executedQty (0.002, not 0.005)."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "PARTIALLY_FILLED", "executedQty": "0.002", "avgPrice": "60000.0"}, # Entry
            {"orderId": 102, "status": "NEW"}, # SL
            {"orderId": 103, "status": "NEW"}, # TP
        ]
        def mock_get_order(symbol, order_id=None, client_order_id=None):
            if str(order_id) == "101" or (client_order_id and "ENT" in str(client_order_id)):
                return {"orderId": 101, "status": "PARTIALLY_FILLED", "executedQty": "0.002", "avgPrice": "60000.0"}
            elif str(order_id) == "102" or (client_order_id and "SL" in str(client_order_id)):
                return {"orderId": 102, "status": "NEW", "side": "SELL", "origQty": "0.002", "stopPrice": "59000.0"}
            elif str(order_id) == "103" or (client_order_id and "TP" in str(client_order_id)):
                return {"orderId": 103, "status": "NEW", "side": "SELL", "origQty": "0.002", "stopPrice": "62000.0"}
            return None
        adapter.get_order.side_effect = mock_get_order

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate(qty=0.005)
        pos = manager.execute_order_lifecycle(cand)

        assert pos.filled_quantity == 0.002
        assert adapter.create_order.call_count == 3
        
        # Verify SL call arguments used executedQty (0.002)
        sl_call_kwargs = adapter.create_order.call_args_list[1][1]
        assert sl_call_kwargs["quantity"] == 0.002
        assert sl_call_kwargs["order_type"] == "STOP_MARKET"

        # Verify TP call arguments used executedQty (0.002)
        tp_call_kwargs = adapter.create_order.call_args_list[2][1]
        assert tp_call_kwargs["quantity"] == 0.002
        assert tp_call_kwargs["order_type"] == "TAKE_PROFIT_MARKET"

    def test_5_entry_request_times_out_reconciled(self, risk_mgr):
        """5. Entry request times out with unknown outcome -> reconciled via client_order_id."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            TimeoutError("Connection timed out waiting for exchange response"),
            {"orderId": 102, "status": "NEW"}, # SL
            {"orderId": 103, "status": "NEW"}, # TP
        ]
        def mock_get_order(symbol, order_id=None, client_order_id=None):
            if str(order_id) == "101" or (client_order_id and "ENT" in str(client_order_id)):
                return {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"}
            elif str(order_id) == "102" or (client_order_id and "SL" in str(client_order_id)):
                return {"orderId": 102, "status": "NEW", "side": "SELL", "origQty": "0.005", "stopPrice": "59000.0"}
            elif str(order_id) == "103" or (client_order_id and "TP" in str(client_order_id)):
                return {"orderId": 103, "status": "NEW", "side": "SELL", "origQty": "0.005", "stopPrice": "62000.0"}
            return None
        adapter.get_order.side_effect = mock_get_order

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        assert pos.entry_status == "FILLED"
        assert pos.filled_quantity == 0.005
        assert pos.protection_state == ProtectionState.PROTECTED
        adapter.get_order.assert_called()

    def test_6_exchange_rejects_sl_price(self, risk_mgr):
        """6. Exchange rejects an SL price (e.g. Price too close or outside valid filter)."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"},
            Exception("HTTP 400: Price out of bounds (code=-1102)"), # SL Rejected
            {"orderId": 103, "status": "NEW"}, # TP
            {"orderId": 104, "status": "FILLED"}, # Emergency close
        ]

        manager = V3OrderLifecycleManager(
            adapter=adapter,
            risk_manager=risk_mgr,
            emergency_policy=EmergencyPolicy.CLOSE_MARKET_IMMEDIATELY,
        )
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        assert pos.protection_state == ProtectionState.EMERGENCY_CLOSED
        assert manager.entries_frozen is True
        assert any("SL_SUBMISSION_FAILED" in a for a in pos.alerts)

    def test_7_bot_restarts_with_open_position_missing_protection(self, risk_mgr):
        """7. Bot restarts with open position but missing protection on exchange -> reconciles, triggers alert."""
        adapter = MagicMock()
        # Exchange reports an open position of 0.005 BTC
        adapter.get_positions.return_value = [
            {"symbol": "BTCUSDT", "positionAmt": "0.005", "entryPrice": "60000.0"}
        ]
        # But open orders on exchange has NO stop loss order
        adapter.get_open_orders.return_value = []
        adapter.create_order.return_value = {"orderId": 999, "status": "FILLED"} # Emergency close

        manager = V3OrderLifecycleManager(
            adapter=adapter,
            risk_manager=risk_mgr,
            emergency_policy=EmergencyPolicy.CLOSE_MARKET_IMMEDIATELY,
        )
        is_ok, reason = manager.reconcile_startup_protection("BTCUSDT")

        assert is_ok is False
        assert "STARTUP RECONCILIATION FAILED" in reason
        assert manager.entries_frozen is True
        assert len(manager.critical_alerts) >= 1
        adapter.create_order.assert_called_once() # Emergency close executed

    def test_8_duplicate_submission_prevented(self, risk_mgr):
        """8. Duplicate requests do not create duplicate positions or protective orders."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"},
            {"orderId": 102, "status": "NEW"},
            {"orderId": 103, "status": "NEW"},
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate()

        # First execution
        pos1 = manager.execute_order_lifecycle(cand)
        assert adapter.create_order.call_count == 3

        # Duplicate call with identical candidate_id
        pos2 = manager.execute_order_lifecycle(cand)
        # Call count MUST remain 3 (no duplicate calls)
        assert adapter.create_order.call_count == 3
        assert pos1 is pos2

    def test_9_long_and_short_protection_sides_correct(self, risk_mgr):
        """9. LONG and SHORT protection sides are strictly verified (opposite of entry, reduceOnly=True)."""
        adapter = MagicMock()
        # Return filled for entries, new for protective orders
        adapter.create_order.return_value = {"orderId": 100, "status": "FILLED", "executedQty": "0.005"}

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)

        # A. Test LONG setup: Entry BUY -> Protection SELL
        cand_long = make_candidate("BTCUSDT", Direction.LONG)
        pos_long = manager.execute_order_lifecycle(cand_long)
        assert pos_long.protection_state == ProtectionState.PROTECTED
        calls = adapter.create_order.call_args_list
        assert calls[0][1]["side"] == "BUY"   # Entry
        assert calls[1][1]["side"] == "SELL"  # SL
        assert calls[1][1]["reduce_only"] is True
        assert calls[2][1]["side"] == "SELL"  # TP
        assert calls[2][1]["reduce_only"] is True

        adapter.create_order.reset_mock()

        # B. Test SHORT setup: Entry SELL -> Protection BUY
        cand_short = make_candidate("ETHUSDT", Direction.SHORT)
        pos_short = manager.execute_order_lifecycle(cand_short)
        assert pos_short.protection_state == ProtectionState.PROTECTED
        calls_short = adapter.create_order.call_args_list
        assert calls_short[0][1]["side"] == "SELL" # Entry
        assert calls_short[1][1]["side"] == "BUY"  # SL
        assert calls_short[1][1]["reduce_only"] is True
        assert calls_short[2][1]["side"] == "BUY"  # TP
        assert calls_short[2][1]["reduce_only"] is True

    def test_10_sl_and_tp_both_rejected(self, risk_mgr):
        """10. SL and TP both rejected by exchange -> marks UNPROTECTED, halts entries, executes emergency close."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"}, # Entry
            Exception("SL rejected (-2010 Insufficient Balance)"),
            Exception("TP rejected (-2010 Insufficient Balance)"),
            {"orderId": 104, "status": "FILLED"}, # Emergency close
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        assert pos.protection_state == ProtectionState.EMERGENCY_CLOSED
        assert manager.entries_frozen is True
        assert any("SL_SUBMISSION_FAILED" in a for a in pos.alerts)
        assert any("TP_SUBMISSION_FAILED" in a for a in pos.alerts)

    def test_11_emergency_market_close_fails_or_times_out(self, risk_mgr):
        """11. Emergency market close fails or times out -> state EMERGENCY_FAILED, entries frozen, stays open."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"}, # Entry
            Exception("SL rejected"),
            {"orderId": 103, "status": "NEW"},
            TimeoutError("Exchange gateway timed out during emergency close!"),
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        assert pos.protection_state == ProtectionState.EMERGENCY_FAILED
        assert pos.is_open is True
        assert manager.entries_frozen is True
        assert any("EMERGENCY" in a and "CLOSE FAILED" in a for a in pos.alerts)

    def test_12_emergency_close_only_partially_filled(self, risk_mgr):
        """12. Emergency close is only partially filled -> remaining qty stays open, UNPROTECTED, entries frozen."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"}, # Entry
            Exception("SL rejected"),
            {"orderId": 103, "status": "NEW"},
            # Emergency close executed for 0.005, but only fills 0.002
            {"orderId": 104, "status": "PARTIALLY_FILLED", "executedQty": "0.002"},
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        assert pos.protection_state == ProtectionState.UNPROTECTED
        assert pos.is_open is True
        assert pos.filled_quantity == pytest.approx(0.003)
        assert manager.entries_frozen is True
        assert any("Emergency market close partially filled" in a for a in pos.alerts)

    def test_13_entry_partially_fills_and_cancelled(self, risk_mgr):
        """13. Entry partially fills and remainder is cancelled -> protection sized to executed portion only."""
        adapter = MagicMock()
        # Initial response says NEW
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "NEW", "executedQty": "0.0", "avgPrice": "0.0"}, # Entry
            {"orderId": 102, "status": "NEW"}, # SL
            {"orderId": 103, "status": "NEW"}, # TP
        ]
        # Query reveals order was partially filled for 0.002 and then CANCELED
        def mock_get_order(symbol, order_id=None, client_order_id=None):
            if str(order_id) == "101" or (client_order_id and "ENT" in str(client_order_id)):
                return {"orderId": 101, "status": "CANCELED", "executedQty": "0.002", "avgPrice": "60000.0"}
            elif str(order_id) == "102" or (client_order_id and "SL" in str(client_order_id)):
                return {"orderId": 102, "status": "NEW", "side": "SELL", "origQty": "0.002", "stopPrice": "59000.0"}
            elif str(order_id) == "103" or (client_order_id and "TP" in str(client_order_id)):
                return {"orderId": 103, "status": "NEW", "side": "SELL", "origQty": "0.002", "stopPrice": "62000.0"}
            return None
        adapter.get_order.side_effect = mock_get_order

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate(qty=0.005)
        pos = manager.execute_order_lifecycle(cand)

        assert pos.filled_quantity == 0.002
        assert pos.is_open is True
        assert pos.protection_state == ProtectionState.PROTECTED
        assert adapter.create_order.call_args_list[1][1]["quantity"] == 0.002

    def test_14_sl_active_but_tp_missing(self, risk_mgr):
        """14. SL is active but TP is missing -> state is PARTIALLY_PROTECTED with warning alert."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"}, # Entry
            {"orderId": 102, "status": "NEW"}, # SL
            Exception("TP rejected by exchange"), # TP fails
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        assert pos.protection_state == ProtectionState.PARTIALLY_PROTECTED
        assert pos.sl_order_id == "102"
        assert pos.tp_order_id is None
        assert manager.entries_frozen is False
        assert any("TP_SUBMISSION_FAILED" in a for a in pos.alerts)

    def test_15_protective_order_accepted_by_post_but_absent_from_query(self, risk_mgr):
        """15. Protective order accepted by POST but absent from open-order query -> treated as UNPROTECTED."""
        adapter = MagicMock()
        adapter.create_order.side_effect = [
            {"orderId": 101, "status": "FILLED", "executedQty": "0.005", "avgPrice": "60000.0"}, # Entry
            {"orderId": 102, "status": "NEW"}, # SL POST returns 200
            {"orderId": 103, "status": "NEW"}, # TP POST
            {"orderId": 104, "status": "FILLED"}, # Emergency close
        ]
        # Exchange open orders query returns EMPTY LIST (order disappeared or was not registered)
        adapter.get_open_orders.return_value = []
        adapter.get_order.return_value = None

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cand = make_candidate()
        pos = manager.execute_order_lifecycle(cand)

        # Invariant: Never treat HTTP 200 alone as proof of protection!
        assert pos.protection_state == ProtectionState.EMERGENCY_CLOSED
        assert manager.entries_frozen is True
        assert any("SL_VERIFICATION_FAILED" in a for a in pos.alerts)

    def test_16_position_already_closed_when_recovery_runs(self, risk_mgr):
        """16. Position is already closed when recovery runs -> clean status, no emergency close."""
        adapter = MagicMock()
        adapter.get_positions.return_value = [
            {"symbol": "BTCUSDT", "positionAmt": "0.0", "entryPrice": "0.0"}
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        is_ok, msg = manager.reconcile_startup_protection("BTCUSDT")

        assert is_ok is True
        assert "position is closed" in msg
        assert manager.entries_frozen is False
        assert adapter.create_order.call_count == 0

    def test_17_startup_reconciliation_cannot_reach_exchange(self, risk_mgr):
        """17. Startup reconciliation cannot reach exchange -> fails closed, freezes entries, alerts."""
        adapter = MagicMock()
        adapter.get_positions.side_effect = Exception("502 Bad Gateway / Network Timeout")

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        is_ok, msg = manager.reconcile_startup_protection("BTCUSDT")

        assert is_ok is False
        assert "could not reach exchange" in msg
        assert manager.entries_frozen is True
        assert len(manager.critical_alerts) >= 1
        assert "RECON_ERR_BTCUSDT" in manager.critical_alerts[0]["candidate_id"]

    def test_18_sl_and_tp_both_trigger_or_race(self, risk_mgr):
        """18. SL triggers -> bracket fill cancels opposing TP leg to prevent reversal race."""
        adapter = MagicMock()
        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        
        pos = ManagedPosition(
            candidate_id="SETUP_BTCUSDT_LONG_001",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            intended_quantity=0.005,
            filled_quantity=0.005,
            sl_order_id="102",
            tp_order_id="103",
            protection_state=ProtectionState.PROTECTED,
            is_open=True,
        )

        manager.handle_bracket_fill(pos, filled_order_type="STOP_MARKET")

        assert pos.is_open is False
        assert pos.sl_order_id is None
        adapter.cancel_order.assert_called_once_with(symbol="BTCUSDT", order_id="103")

    def test_19_restart_retry_cannot_duplicate_protective_orders(self, risk_mgr):
        """19. Restart/retry with existing SL on exchange -> verifies protected, no duplicate orders."""
        adapter = MagicMock()
        adapter.get_positions.return_value = [
            {"symbol": "BTCUSDT", "positionAmt": "0.005", "entryPrice": "60000.0"}
        ]
        # SL order already active on exchange
        adapter.get_open_orders.return_value = [
            {"orderId": 555, "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET", "origQty": "0.005"}
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        is_ok, msg = manager.reconcile_startup_protection("BTCUSDT")

        assert is_ok is True
        assert "verified PROTECTED" in msg
        assert adapter.create_order.call_count == 0  # No duplicate orders submitted!

    def test_20_tp_triggers_cancels_sl_and_handles_cancellation_failure(self, risk_mgr):
        """20. TP triggers -> cancels SL; if cancel fails, freezes entries and raises critical alert."""
        adapter = MagicMock()
        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)

        pos = ManagedPosition(
            candidate_id="SETUP_BTCUSDT_LONG_002",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            intended_quantity=0.005,
            filled_quantity=0.005,
            sl_order_id="202",
            tp_order_id="203",
            protection_state=ProtectionState.PROTECTED,
            is_open=True,
        )

        # Failure mode: Exchange rejects or times out cancelling opposing SL leg
        adapter.cancel_order.side_effect = Exception("504 Gateway Timeout on cancel")
        adapter.get_open_orders.return_value = []

        manager.handle_bracket_fill(pos, filled_order_type="TAKE_PROFIT_MARKET")

        assert pos.is_open is False
        assert pos.tp_order_id is None
        assert manager.entries_frozen is True
        assert len(manager.critical_alerts) >= 1
        assert any("Failed to cancel opposing bracket leg" in a for a in pos.alerts)

    def test_21_orphan_purge_scoped_preserves_external_orders(self, risk_mgr):
        """21. Orphan purge only cancels bot orders, preserving external manual orders."""
        adapter = MagicMock()
        adapter.get_open_orders.return_value = [
            # Bot-owned SL order
            {"orderId": 801, "type": "STOP_MARKET", "clientOrderId": "SL_cand1_123"},
            # External manual stop order
            {"orderId": 901, "type": "STOP_MARKET", "clientOrderId": "web_manual_stop_456"},
        ]

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        cancelled = manager.purge_orphaned_protective_orders("BTCUSDT")

        assert "801" in cancelled
        assert "901" not in cancelled
        # Only order 801 should have been cancelled
        adapter.cancel_order.assert_called_once_with(symbol="BTCUSDT", order_id="801")

    def test_22_tp1_transition_to_breakeven_and_tp2_atomic_replacement(self, risk_mgr):
        """22. TP1 partial fill -> places & verifies new break-even SL BEFORE cancelling old SL, and sets TP2."""
        adapter = MagicMock()
        call_sequence = []

        def mock_create_order(**kwargs):
            cid = kwargs.get("client_order_id", "")
            if cid.startswith("SLBE_"):
                call_sequence.append("CREATE_NEW_BE_SL")
                return {"orderId": 302, "status": "NEW"}
            elif cid.startswith("TP2_"):
                call_sequence.append("CREATE_NEW_TP2")
                return {"orderId": 303, "status": "NEW"}
            return {"orderId": 300, "status": "NEW"}

        def mock_cancel_order(symbol, order_id=None, **kwargs):
            call_sequence.append(f"CANCEL_{order_id}")
            return {"status": "CANCELED"}

        def mock_get_order(symbol, order_id=None, client_order_id=None):
            if str(order_id) == "302":
                return {"orderId": 302, "status": "NEW", "side": "SELL", "origQty": "0.0025", "stopPrice": str(60000.0 * 1.0014)}
            if str(order_id) == "303":
                return {"orderId": 303, "status": "NEW", "side": "SELL", "origQty": "0.0025", "stopPrice": "64000.0"}
            return None

        adapter.create_order.side_effect = mock_create_order
        adapter.cancel_order.side_effect = mock_cancel_order
        adapter.get_order.side_effect = mock_get_order
        adapter.get_open_orders.return_value = None

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        pos = ManagedPosition(
            candidate_id="SETUP_BTC_TP1_001",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            intended_quantity=0.005,
            filled_quantity=0.005,
            entry_price=60000.0,
            stop_loss_price=59000.0,
            take_profit_price=61500.0,
            take_profit_2_price=64000.0,
            initial_stop_loss_price=59000.0,
            sl_order_id="102",
            tp_order_id="103",
            protection_state=ProtectionState.PROTECTED,
            is_open=True,
        )

        ok, msg = manager.transition_to_tp1_breakeven(pos, executed_tp1_qty=0.0025, fee_buffer_pct=0.0014)
        assert ok is True
        assert pos.tp1_hit is True
        assert pos.trailing_active is True
        assert pos.filled_quantity == pytest.approx(0.0025)
        assert pos.stop_loss_price == pytest.approx(60000.0 * 1.0014)
        assert pos.take_profit_price == pytest.approx(64000.0)
        assert pos.sl_order_id == "302"
        assert pos.tp_order_id == "303"
        # Prove atomic order: CREATE_NEW_BE_SL happened BEFORE CANCEL_102
        assert call_sequence.index("CREATE_NEW_BE_SL") < call_sequence.index("CANCEL_102")

    def test_23_update_trailing_stop_ratchets_monotonically(self, risk_mgr):
        """23. update_trailing_stop ratchets SL up on higher highs and ignores lower highs."""
        adapter = MagicMock()
        adapter.create_order.return_value = {"orderId": 402, "status": "NEW"}
        adapter.get_open_orders.return_value = None
        adapter.get_order.return_value = {
            "orderId": 402,
            "status": "NEW",
            "side": "SELL",
            "origQty": "0.0025",
            "stopPrice": "61800.0",
        }

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        pos = ManagedPosition(
            candidate_id="SETUP_BTC_TRAIL_001",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            intended_quantity=0.005,
            filled_quantity=0.0025,
            entry_price=60000.0,
            stop_loss_price=60084.0,
            take_profit_price=64000.0,
            initial_stop_loss_price=59000.0,
            sl_order_id="302",
            tp_order_id="303",
            tp1_hit=True,
            trailing_active=True,
            trailing_distance=1000.0,
            breakeven_stop_price=60084.0,
            highest_favorable_price=61500.0,
            protection_state=ProtectionState.PROTECTED,
            is_open=True,
        )

        # High reaches 62800 -> new SL should ratchet to 61800.0
        updated, msg = manager.update_trailing_stop(pos, current_high=62800.0, current_low=61400.0)
        assert updated is True
        assert pos.stop_loss_price == pytest.approx(61800.0)
        assert pos.sl_order_id == "402"
        adapter.cancel_order.assert_called_once_with(symbol="BTCUSDT", order_id="302")

        # Subsequent bar with lower high (62200) must NOT lower the trailing stop
        adapter.create_order.reset_mock()
        updated_again, _ = manager.update_trailing_stop(pos, current_high=62200.0, current_low=61900.0)
        assert updated_again is False
        assert pos.stop_loss_price == pytest.approx(61800.0)
        adapter.create_order.assert_not_called()

    def test_24_atomic_sl_replacement_preserves_old_sl_if_new_sl_rejected(self, risk_mgr):
        """24. If new trailing SL submission fails on exchange, old SL is NEVER cancelled."""
        adapter = MagicMock()
        adapter.create_order.side_effect = Exception("Exchange busy (-1001)")

        manager = V3OrderLifecycleManager(adapter=adapter, risk_manager=risk_mgr)
        pos = ManagedPosition(
            candidate_id="SETUP_BTC_TRAIL_FAIL_001",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            intended_quantity=0.005,
            filled_quantity=0.0025,
            entry_price=60000.0,
            stop_loss_price=60084.0,
            take_profit_price=64000.0,
            initial_stop_loss_price=59000.0,
            sl_order_id="302",
            tp_order_id="303",
            tp1_hit=True,
            trailing_active=True,
            trailing_distance=1000.0,
            breakeven_stop_price=60084.0,
            highest_favorable_price=61500.0,
            protection_state=ProtectionState.PROTECTED,
            is_open=True,
        )

        updated, msg = manager.update_trailing_stop(pos, current_high=63000.0, current_low=61500.0)
        assert updated is False
        # Old SL 302 at 60084.0 must remain untouched!
        assert pos.sl_order_id == "302"
        assert pos.stop_loss_price == pytest.approx(60084.0)
        adapter.cancel_order.assert_not_called()



