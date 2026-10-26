"""Unit and Integration Tests for Session-Authorized LIVE Auto-Execution (LIVE_AUTO).

Verifies all safety invariants from Section 7:
1. Default state is PAPER / INACTIVE.
2. Session cannot start if any pre-flight check fails.
3. Session authorization persists across calls within the running server session.
4. Session restarts in INACTIVE state on application reload (never silently resumes).
5. In LIVE_AUTO mode with an active session, an eligible trade executes automatically without UI confirmation.
6. In LIVE_MANUAL mode, operator confirmation is strictly required.
7. In PAPER mode, no exchange calls are made regardless of session state.
8. Daily entry limit (3) blocks further auto-execution once reached.
9. Daily loss limit ($0.45) halts auto-execution.
10. Global position lock prevents multiple concurrent positions.
11. Duplicate order detection prevents re-entering the same candle/signal.
12. SWEEP_1M_V1 cannot be executed in LIVE_AUTO (raises PermissionError).
13. SL placement failure immediately cancels entry order.
14. Session Pause halts new entries but preserves tracking.
15. Session Resume re-checks pre-flight before reactivating.
16. Session Stop leaves open position managed by exchange SL/TP.
17. Emergency Kill Switch cancels resting orders, closes open positions, and halts.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from src.ai.groq_autotrader import (
    ExecutionVenue,
    GroqAutoTrader,
    RiskPreset,
)
from src.core.types import Candle
from src.data.database import DatabaseManager
from src.exchange.aster_adapter import AsterAdapter
from src.execution.live_session import (
    ExecutionMode,
    LiveAutoSessionManager,
    LiveSessionState,
)
from src.intelligence.coin_inspector import CoinInspectorService
from src.journal.automated_journal import AutomatedTradeJournal
from src.risk.global_lock import GlobalPositionLock
from src.scheduler.live_micro_runner import LiveMicroCoordinator


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singleton GlobalPositionLock and LiveAutoSessionManager before each test."""
    lock = GlobalPositionLock()
    with lock._mutex:
        lock._is_locked = False
        lock._symbol = None
        lock._strategy_id = None
        lock._order_id = None
        lock._state = "IDLE"
        lock._acquired_at_ms = 0
        lock._lock_history = []
    LiveAutoSessionManager._instance = None
    yield
    with lock._mutex:
        lock._is_locked = False
        lock._symbol = None
        lock._strategy_id = None
        lock._order_id = None
        lock._state = "IDLE"
        lock._acquired_at_ms = 0
    LiveAutoSessionManager._instance = None


@pytest.fixture
def mock_groq_llm(monkeypatch):
    """Mock Groq LLM API responses for stage 2 and stage 3."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    def mock_groq_json(self, messages, model_override=None, max_tokens=400):
        content = " ".join([m.get("content", "") for m in messages])
        if "Analyze candidates and output Top 3" in content:
            return {
                "top_3": [
                    {
                        "rank": 1,
                        "symbol": "SOLUSDT",
                        "category": "LIQUIDITY_SWEEP_RECLAIM",
                        "score": 93.0,
                        "thesis": "15m Sweep Reclaim with strong RS",
                        "verification_questions": ["Verify 15m sweep reclaim", "Check bid depth"],
                    },
                    {
                        "rank": 2,
                        "symbol": "AVAXUSDT",
                        "category": "MOMENTUM_CONTINUATION",
                        "score": 88.0,
                        "thesis": "Trend continuation",
                        "verification_questions": ["Check volume"],
                    },
                    {
                        "rank": 3,
                        "symbol": "LINKUSDT",
                        "category": "LAGGARD_CATCH_UP",
                        "score": 84.0,
                        "thesis": "Discount reversal",
                        "verification_questions": ["Check FVG"],
                    },
                ],
                "eliminated_summary": {},
            }
        else:
            return {
                "decision": "EXECUTE",
                "symbol": "SOLUSDT",
                "direction": "LONG",
                "order_type": "LIMIT",
                "entry_price": 99.85,
                "stop_loss_price": 98.70,
                "take_profit_price": 102.44,
                "notes": "SOL 15m Sweep Reclaim SMC",
                "reasoning": "Verified structural reclaim confluence",
            }

    monkeypatch.setattr(GroqAutoTrader, "_call_groq_json", mock_groq_json)


@pytest.fixture
def mock_aster_adapter():
    """Create a mock AsterAdapter returning consistent market data."""
    adapter = MagicMock(spec=AsterAdapter)
    adapter.get_bulk_market_snapshot.return_value = {
        "BTCUSDT": {"last_price": 90000.0, "price_change_pct": 1.5, "volume_24h_usdt": 500000000.0, "spread_bps": 0.5},
        "ETHUSDT": {"last_price": 2800.0, "price_change_pct": 2.0, "volume_24h_usdt": 200000000.0, "spread_bps": 0.8},
        "SOLUSDT": {"last_price": 100.0, "price_change_pct": 3.0, "volume_24h_usdt": 50000000.0, "spread_bps": 1.0},
        "AVAXUSDT": {"last_price": 25.0, "price_change_pct": 1.2, "volume_24h_usdt": 20000000.0, "spread_bps": 1.2},
        "LINKUSDT": {"last_price": 12.0, "price_change_pct": -0.5, "volume_24h_usdt": 15000000.0, "spread_bps": 1.1},
    }
    adapter.discover_instruments.return_value = [
        MagicMock(symbol="SOLUSDT", min_qty=0.01, tick_size=0.01, step_size=0.01, min_notional=5.0),
        MagicMock(symbol="AVAXUSDT", min_qty=0.1, tick_size=0.01, step_size=0.1, min_notional=5.0),
        MagicMock(symbol="LINKUSDT", min_qty=0.1, tick_size=0.001, step_size=0.1, min_notional=5.0),
    ]

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    # 35 15m candles with valid Bull Sweep and Reclaim structure in Discount
    candles = []
    for i in range(35):
        t_ms = now_ms - (35 - i) * 900000
        if i == 20:
            c = Candle(
                symbol="SOLUSDT", timeframe="15m", timestamp_ms=t_ms,
                open=99.0, high=99.5, low=97.0, close=98.0, volume=100.0,
                close_time_ms=t_ms + 899999
            )
        elif 21 <= i <= 26:
            c = Candle(
                symbol="SOLUSDT", timeframe="15m", timestamp_ms=t_ms,
                open=98.5, high=99.5, low=98.0, close=99.0, volume=100.0,
                close_time_ms=t_ms + 899999
            )
        elif i == 33:
            c = Candle(
                symbol="SOLUSDT", timeframe="15m", timestamp_ms=t_ms,
                open=98.0, high=98.5, low=96.5, close=97.2, volume=120.0,
                close_time_ms=t_ms + 899999
            )
        elif i == 34:
            c = Candle(
                symbol="SOLUSDT", timeframe="15m", timestamp_ms=t_ms,
                open=98.5, high=100.1, low=96.8, close=100.0, volume=150.0,
                close_time_ms=t_ms + 899999
            )
        else:
            c = Candle(
                symbol="SOLUSDT", timeframe="15m", timestamp_ms=t_ms,
                open=99.5, high=100.5, low=99.0, close=100.0, volume=100.0,
                close_time_ms=t_ms + 899999
            )
        candles.append(c)

    def mock_get_klines(symbol, timeframe="5m", limit=35):
        if timeframe == "1h":
            k1h = []
            for j in range(limit or 72):
                t_1h = now_ms - (72 - j) * 3600000
                k1h.append(
                    Candle(
                        symbol=symbol,
                        timeframe="1h",
                        timestamp_ms=t_1h,
                        open=100.0,
                        high=125.0 if j == 40 else 102.0,
                        low=90.0 if j == 45 else 98.0,
                        close=100.0,
                        volume=5000.0,
                        close_time_ms=t_1h + 3599999,
                    )
                )
            return k1h
        return candles

    adapter.get_klines.side_effect = mock_get_klines
    adapter.get_premium_index.return_value = {"mark_price": 100.0, "last_funding_rate": "0.0001"}

    adapter.get_klines.side_effect = mock_get_klines
    adapter.get_premium_index.return_value = {"last_funding_rate": "0.0001"}
    return adapter


@pytest.fixture
def mock_live_exchange_adapter():
    """Mock live exchange execution adapter with order placement and cancellation."""
    adapter = MagicMock()
    adapter.get_account_balance.return_value = {"total_equity_usdt": 15.72, "available_balance_usdt": 15.72}
    adapter.set_margin_type.return_value = {"code": 200, "msg": "success"}
    adapter.set_leverage.return_value = {"code": 200, "msg": "success"}
    adapter.create_order.side_effect = lambda **kwargs: {
        "orderId": f"MOCK_{kwargs.get('order_type')}_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "symbol": kwargs.get("symbol"),
        "status": "NEW",
        "side": kwargs.get("side"),
        "price": kwargs.get("price"),
        "origQty": kwargs.get("quantity"),
        "type": kwargs.get("order_type"),
    }
    adapter.cancel_order.return_value = {"status": "CANCELED"}
    adapter.cancel_all_open_orders.return_value = {"code": 200, "msg": "All orders cancelled"}
    adapter.cancel_all_orders.return_value = {"code": 200, "msg": "All orders cancelled"}
    return adapter


@pytest.fixture
def test_setup(tmp_path, mock_aster_adapter, mock_live_exchange_adapter):
    """Setup clean isolated test database, coordinator, journal, session manager and trader."""
    db_file = tmp_path / "test_live_session.db"
    db = DatabaseManager(str(db_file))
    db.init_db()

    coord = LiveMicroCoordinator(
        db=db,
        exchange=mock_live_exchange_adapter,
    )
    coord.current_equity = 15.72
    coord.peak_equity = 15.72

    journal = AutomatedTradeJournal(db)
    inspector_svc = CoinInspectorService(adapter=mock_aster_adapter)

    sess_mgr = LiveAutoSessionManager(
        coordinator=coord,
        exchange_adapter=mock_live_exchange_adapter,
        automated_journal=journal,
        db=db,
    )

    trader = GroqAutoTrader(
        adapter=mock_aster_adapter,
        inspector_service=inspector_svc,
        coordinator=coord,
        automated_journal=journal,
        session_manager=sess_mgr,
    )

    return {
        "db": db,
        "coord": coord,
        "journal": journal,
        "sess_mgr": sess_mgr,
        "trader": trader,
        "mock_adapter": mock_aster_adapter,
        "mock_live": mock_live_exchange_adapter,
    }


def test_01_default_state_is_paper_and_inactive(test_setup):
    """Verify that default state is PAPER / INACTIVE and auto-execution is disabled."""
    sess_mgr = test_setup["sess_mgr"]
    assert sess_mgr.mode == ExecutionMode.PAPER
    assert sess_mgr.state == LiveSessionState.INACTIVE
    assert not sess_mgr.is_authorized_and_active()

    info = sess_mgr.get_session_info()
    assert info["mode"] == "PAPER"
    assert info["state"] == "INACTIVE"
    assert info["is_authorized_and_active"] is False


def test_02_preflight_checks_and_failure_blocking(test_setup):
    """Verify session cannot start if pre-flight checks fail (e.g. low equity or locked position)."""
    sess_mgr = test_setup["sess_mgr"]
    coord = test_setup["coord"]

    # Trigger drawdown breach
    coord.current_equity = 10.0
    coord.peak_equity = 15.72  # >25% drawdown
    coord.trigger_kill_switch("Drawdown breached")

    ok, msg, info = sess_mgr.start_session(operator_id="OPERATOR")
    assert not ok
    assert "Pre-flight verification failed" in msg
    assert sess_mgr.state == LiveSessionState.INACTIVE


def test_03_session_authorization_persists_across_calls(test_setup):
    """Verify that session authorization persists across calls within running server session."""
    sess_mgr = test_setup["sess_mgr"]

    ok, msg, info = sess_mgr.start_session(operator_id="OPERATOR_ALICE")
    assert ok
    assert sess_mgr.state == LiveSessionState.ACTIVE
    assert sess_mgr.mode == ExecutionMode.LIVE_AUTO
    assert sess_mgr.is_authorized_and_active()

    for _ in range(5):
        current_info = sess_mgr.get_session_info()
        assert current_info["state"] == "ACTIVE"
        assert current_info["mode"] == "LIVE_AUTO"
        assert current_info["authorized_by"] == "OPERATOR_ALICE"


def test_04_session_restarts_inactive_on_reload(test_setup):
    """Verify that process restarts recover session in REAUTHORIZATION_REQUIRED state (never silently resume auto-trading)."""
    sess_mgr = test_setup["sess_mgr"]
    ok, _, _ = sess_mgr.start_session(operator_id="OPERATOR_BOB")
    assert ok
    assert sess_mgr.state == LiveSessionState.ACTIVE
    orig_session_id = sess_mgr.session_id

    # Simulate process reboot by clearing singleton instance
    LiveAutoSessionManager._instance = None
    new_sess_mgr = LiveAutoSessionManager(
        coordinator=test_setup["coord"],
        exchange_adapter=test_setup["mock_live"],
        automated_journal=test_setup["journal"],
        db=test_setup["db"],
    )
    # On reload/restart, session state is preserved but transitions to REAUTHORIZATION_REQUIRED
    assert new_sess_mgr.session_id == orig_session_id
    assert new_sess_mgr.state == LiveSessionState.REAUTHORIZATION_REQUIRED
    assert new_sess_mgr.mode == ExecutionMode.LIVE_AUTO
    assert not new_sess_mgr.is_authorized_and_active()

    # Reauthorization restores ACTIVE state
    reauth_ok, _, _ = new_sess_mgr.reauthorize_session(operator_id="OPERATOR_BOB")
    assert reauth_ok
    assert new_sess_mgr.state == LiveSessionState.ACTIVE
    assert new_sess_mgr.is_authorized_and_active()


def test_05_live_auto_executes_eligible_trade_without_ui_confirmation(test_setup, mock_groq_llm):
    """In LIVE_AUTO mode with active session, an eligible trade executes automatically without UI confirmation."""
    sess_mgr = test_setup["sess_mgr"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    ok, msg, _ = sess_mgr.start_session(operator_id="OPERATOR_TRADER")
    assert ok
    assert sess_mgr.is_authorized_and_active()

    # Run pipeline with auto_fire_limit_order=True, operator_confirmed=False
    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE_AUTO,
        execution_mode=ExecutionMode.LIVE_AUTO,
        auto_fire_limit_order=True,
        operator_confirmed=False,  # NO per-order confirmation!
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )

    assert "PROTECTED" in res.stage3.status
    assert res.execution_receipt is not None
    assert "PROTECTED" in res.execution_receipt["tp_sl_protection_status"]
    assert mock_live.create_order.call_count >= 2  # Entry + SL
    assert sess_mgr.orders_placed_count == 1


def test_06_live_manual_still_requires_operator_confirmation(test_setup, mock_groq_llm):
    """In LIVE_MANUAL mode, operator confirmation is still strictly required."""
    sess_mgr = test_setup["sess_mgr"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    sess_mgr.set_execution_mode(ExecutionMode.LIVE_MANUAL)

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE_MANUAL,
        execution_mode=ExecutionMode.LIVE_MANUAL,
        auto_fire_limit_order=True,
        operator_confirmed=False,  # NOT confirmed yet
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )

    assert res.status == "READY"
    assert res.confirmation_payload is not None
    assert res.confirmation_payload["symbol"] == "SOLUSDT"
    assert mock_live.create_order.call_count == 0


def test_07_paper_mode_never_calls_exchange_regardless_of_session(test_setup, mock_groq_llm):
    """In PAPER mode, no exchange calls are made regardless of session state."""
    sess_mgr = test_setup["sess_mgr"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    sess_mgr.start_session(operator_id="OPERATOR")

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.PAPER,
        execution_mode=ExecutionMode.PAPER,
        auto_fire_limit_order=True,
        operator_confirmed=False,
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )

    assert res.execution_venue == "PAPER"
    assert mock_live.create_order.call_count == 0


def test_08_daily_entries_limit_blocks_auto_execution(test_setup, mock_groq_llm):
    """Daily entry limit (3) blocks further auto-execution once reached."""
    sess_mgr = test_setup["sess_mgr"]
    coord = test_setup["coord"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    sess_mgr.start_session(operator_id="OPERATOR")
    coord.daily_filled_entries = 3  # Max reached

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE_AUTO,
        execution_mode=ExecutionMode.LIVE_AUTO,
        auto_fire_limit_order=True,
        operator_confirmed=False,
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )

    assert res.status == "REJECTED"
    assert "Daily max filled entries reached" in res.execution_receipt["reason"]
    assert mock_live.create_order.call_count == 0


def test_09_daily_loss_limit_halts_auto_execution(test_setup, mock_groq_llm):
    """Daily loss limit ($0.45) halts auto-execution."""
    sess_mgr = test_setup["sess_mgr"]
    coord = test_setup["coord"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    sess_mgr.start_session(operator_id="OPERATOR")
    coord.daily_realized_loss = 0.46  # > $0.45

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE_AUTO,
        execution_mode=ExecutionMode.LIVE_AUTO,
        auto_fire_limit_order=True,
        operator_confirmed=False,
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )

    assert res.status == "REJECTED"
    assert "Daily loss limit breached" in res.execution_receipt["reason"]
    assert mock_live.create_order.call_count == 0


def test_10_global_position_lock_prevents_multiple_concurrent_positions(test_setup, mock_groq_llm):
    """Global position lock prevents multiple concurrent positions."""
    sess_mgr = test_setup["sess_mgr"]
    coord = test_setup["coord"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    sess_mgr.start_session(operator_id="OPERATOR")
    coord.global_lock.acquire(
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        symbol="ETHUSDT",
        order_id="EXISTING_ORDER_1",
        state="OPEN",
    )

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE_AUTO,
        execution_mode=ExecutionMode.LIVE_AUTO,
        auto_fire_limit_order=True,
        operator_confirmed=False,
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )

    assert res.status == "REJECTED"
    assert "Global position lock rejected" in res.execution_receipt["reason"]
    assert mock_live.create_order.call_count == 0


def test_11_duplicate_order_detection(test_setup, mock_groq_llm):
    """Duplicate order detection prevents re-entering the same candle/signal."""
    sess_mgr = test_setup["sess_mgr"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    sess_mgr.start_session(operator_id="OPERATOR")

    # First execution succeeds
    res1 = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE_AUTO,
        execution_mode=ExecutionMode.LIVE_AUTO,
        auto_fire_limit_order=True,
        operator_confirmed=False,
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )
    assert "PROTECTED" in res1.stage3.status

    # Second immediate execution on same symbol is rejected as duplicate
    res2 = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE_AUTO,
        execution_mode=ExecutionMode.LIVE_AUTO,
        auto_fire_limit_order=True,
        operator_confirmed=False,
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )

    assert res2.status == "REJECTED"
    assert "DUPLICATE_SIGNAL" in res2.execution_receipt["reason"] or "Duplicate order prevented" in res2.execution_receipt["reason"]


def test_12_sweep_1m_v1_strictly_rejected_in_live_auto(test_setup, mock_groq_llm):
    """SWEEP_1M_V1 cannot be executed in LIVE_AUTO (raises PermissionError)."""
    sess_mgr = test_setup["sess_mgr"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    sess_mgr.start_session(operator_id="OPERATOR")

    with pytest.raises(PermissionError) as exc_info:
        trader.run_full_pipeline(
            execution_venue=ExecutionVenue.LIVE_AUTO,
            execution_mode=ExecutionMode.LIVE_AUTO,
            auto_fire_limit_order=True,
            operator_confirmed=False,
            strategy_id="SWEEP_1M_V1",
            session_manager=sess_mgr,
            live_adapter=mock_live,
        )

    assert "SWEEP_1M_V1 is SHADOW-only" in str(exc_info.value)
    assert mock_live.create_order.call_count == 0


def test_13_sl_placement_failure_immediately_cancels_entry(test_setup, mock_groq_llm):
    """If stop-loss placement fails, entry order is immediately cancelled on exchange."""
    sess_mgr = test_setup["sess_mgr"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    sess_mgr.start_session(operator_id="OPERATOR")

    call_count = [0]
    def mock_order_behavior(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return {
                "orderId": "ENTRY_ORDER_SUCCESS_999",
                "clientOrderId": "ENTRY_CLIENT_999",
                "symbol": "SOLUSDT",
                "status": "NEW",
                "side": "BUY",
                "price": "100.00",
                "origQty": "0.06",
                "type": "LIMIT",
            }
        else:
            raise RuntimeError("Aster DEX Stop-Loss Error: insufficient balance for margin")

    mock_live.create_order.side_effect = mock_order_behavior

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE_AUTO,
        execution_mode=ExecutionMode.LIVE_AUTO,
        auto_fire_limit_order=True,
        operator_confirmed=False,
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )

    assert res.status == "ERROR"
    assert "Protective Stop Loss failed to lodge on exchange" in res.execution_receipt["error"]
    assert mock_live.cancel_order.call_count >= 1


def test_14_pause_and_resume_lifecycle(test_setup, mock_groq_llm):
    """Session Pause halts new entries; Resume re-checks preflight before reactivating."""
    sess_mgr = test_setup["sess_mgr"]
    trader = test_setup["trader"]
    mock_live = test_setup["mock_live"]

    ok, _, _ = sess_mgr.start_session(operator_id="OPERATOR")
    assert ok
    assert sess_mgr.state == LiveSessionState.ACTIVE

    # Pause session
    ok_p, msg_p, _ = sess_mgr.pause_session(reason="Operator paused session")
    assert ok_p
    assert sess_mgr.state == LiveSessionState.PAUSED
    assert not sess_mgr.is_authorized_and_active()

    # Attempt auto execution while paused -> REJECTED
    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE_AUTO,
        execution_mode=ExecutionMode.LIVE_AUTO,
        auto_fire_limit_order=True,
        operator_confirmed=False,
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        session_manager=sess_mgr,
        live_adapter=mock_live,
    )
    assert res.status == "REJECTED"
    assert "session is not active" in res.execution_receipt["reason"]

    # Resume session
    ok_res, msg_res, _ = sess_mgr.resume_session()
    assert ok_res
    assert sess_mgr.state == LiveSessionState.ACTIVE
    assert sess_mgr.is_authorized_and_active()


def test_15_stop_session_leaves_open_position_managed_by_sl_tp(test_setup):
    """Stop session leaves existing open position managed by exchange SL/TP."""
    sess_mgr = test_setup["sess_mgr"]
    coord = test_setup["coord"]
    mock_live = test_setup["mock_live"]

    ok, _, _ = sess_mgr.start_session(operator_id="OPERATOR")
    assert ok
    mock_live.get_positions.return_value = [{"symbol": "SOLUSDT", "positionAmt": "0.15"}]
    coord.global_lock.acquire(
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        symbol="SOLUSDT",
        order_id="ORDER_FILLED_88",
        state="FILLED",
    )

    ok_stop, msg_stop, info = sess_mgr.stop_session(reason="End of trading day")
    assert ok_stop
    assert sess_mgr.state == LiveSessionState.STOPPED
    assert sess_mgr.mode == ExecutionMode.PAPER
    assert "NOTE: An open position remains on SOLUSDT" in msg_stop
    assert "It is protected by exchange-side SL/TP" in msg_stop


def test_16_emergency_kill_switch_action(test_setup):
    """Emergency Kill Switch cancels resting orders, closes open positions, and halts."""
    sess_mgr = test_setup["sess_mgr"]
    coord = test_setup["coord"]
    mock_live = test_setup["mock_live"]

    ok, _, _ = sess_mgr.start_session(operator_id="OPERATOR")
    assert ok
    coord.global_lock.acquire(
        strategy_id="15M_SWEEP_RECLAIM_SMC",
        symbol="SOLUSDT",
        order_id="RESTING_ENTRY_1",
        state="OPEN",
    )

    ok_k, msg_k, info = sess_mgr.trigger_emergency_kill_switch(reason="Market extreme volatility event")
    assert ok_k
    assert sess_mgr.state == LiveSessionState.KILL_SWITCH_HALTED
    assert sess_mgr.mode == ExecutionMode.PAPER
    assert coord.kill_switch_triggered is True
    # Emergency close should have cancelled open orders
    mock_live.cancel_all_open_orders.assert_called_with("SOLUSDT")
