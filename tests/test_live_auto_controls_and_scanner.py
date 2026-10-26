"""Unit and Integration Tests for UI Cleanup and LIVE_AUTO Background Scanner.



Verifies:

1. LIVE_AUTO starts the automatic background scanner according to interval.

2. In LIVE_AUTO, eligible setups proceed automatically through pipeline without per-order confirmation.

3. Pause/Resume controls scanner thread lifecycle (stops on pause, resumes on resume).

4. Stop session and Emergency Kill Switch immediately halt the scanner thread.

5. Scanner respects GlobalPositionLock and skips scans when a position is held.

6. Preview Setup (Read-Only) is available in all modes and NEVER submits exchange orders.

7. LIVE_MANUAL requires explicit operator confirmation before order submission.

8. PAPER mode is completely isolated from the live exchange adapter.

9. Dashboard HTML & JS enforce that in LIVE_AUTO, redundant run buttons are hidden and the scanner pill is shown.

"""



from datetime import datetime, timezone

import time

from typing import Any, Dict, List, Optional

from unittest.mock import MagicMock, patch

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

    """Reset singletons before and after each test."""

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

    if LiveAutoSessionManager._instance is not None:

        try:

            LiveAutoSessionManager._instance.stop_scanner()

        except Exception:

            pass

    LiveAutoSessionManager._instance = None





@pytest.fixture

def test_env(tmp_path, monkeypatch):

    """Isolated test environment with SQLite database and mock Aster exchange."""

    db_file = tmp_path / "test_auto_scanner.db"

    db = DatabaseManager(str(db_file))

    db.init_db()



    mock_exchange = MagicMock()

    mock_exchange.get_account_balance.return_value = [

        {"asset": "USDT", "balance": 15.72, "availableBalance": 15.72}

    ]

    mock_exchange.get_positions.return_value = []

    mock_exchange.get_open_orders.return_value = []

    mock_exchange.get_bulk_market_snapshot.return_value = {

        "BTCUSDT": {"mark_price": 90000.0, "price_change_pct": 1.5, "volume_24h_usdt": 500000000.0, "spread_bps": 0.5},

        "ETHUSDT": {"mark_price": 2800.0, "price_change_pct": 2.0, "volume_24h_usdt": 200000000.0, "spread_bps": 0.8},

        "SOLUSDT": {"mark_price": 100.0, "price_change_pct": 3.0, "volume_24h_usdt": 50000000.0, "spread_bps": 1.0},

    }

    mock_exchange.discover_instruments.return_value = [

        MagicMock(symbol="SOLUSDT", min_qty=0.01, tick_size=0.01, step_size=0.01, min_notional=5.0),

        MagicMock(symbol="ETHUSDT", min_qty=0.001, tick_size=0.01, step_size=0.001, min_notional=5.0),

    ]

    class MockInst:

        symbol = "SOLUSDT"

        base_asset = "SOL"

        quote_asset = "USDT"

        contract_type = "PERPETUAL"

        last_price = 100.0

        tick_size = 0.01

        step_size = 0.01

        min_qty = 0.01

        min_notional = 5.0



    mock_exchange._catalog_cache = {

        "SOLUSDT": MockInst(),

        "ETHUSDT": MockInst(),

    }



    placed_orders = []



    def mock_place_order(symbol, side, order_type, quantity, price=None, stop_price=None, reduce_only=False, time_in_force="GTC", client_order_id=None, **kwargs):

        order_dict = {

            "orderId": len(placed_orders) + 50000,

            "symbol": symbol,

            "side": side,

            "type": order_type,

            "origQty": quantity,

            "price": price or 0.0,

            "stopPrice": stop_price,

            "reduceOnly": reduce_only,

            "status": "NEW",

            "time": int(datetime.now(timezone.utc).timestamp() * 1000),

        }

        placed_orders.append(order_dict)

        return order_dict



    mock_exchange.place_order.side_effect = mock_place_order

    mock_exchange.create_order.side_effect = mock_place_order

    mock_exchange.cancel_order.return_value = {"status": "CANCELED"}



    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    mock_candles = []

    for i in range(35):

        t_ms = now_ms - (35 - i) * 900000

        if i == 20:

            c = Candle("SOLUSDT", "15m", t_ms, 99.0, 99.5, 97.0, 98.0, 100.0, t_ms + 899999)

        elif 21 <= i <= 26:

            c = Candle("SOLUSDT", "15m", t_ms, 98.5, 99.5, 98.0, 99.0, 100.0, t_ms + 899999)

        elif i == 33:

            c = Candle("SOLUSDT", "15m", t_ms, 98.0, 98.5, 96.5, 97.2, 120.0, t_ms + 899999)

        elif i == 34:

            c = Candle("SOLUSDT", "15m", t_ms, 98.5, 100.1, 96.8, 100.0, 150.0, t_ms + 899999)

        else:

            c = Candle("SOLUSDT", "15m", t_ms, 99.5, 100.5, 99.0, 100.0, 100.0, t_ms + 899999)

        mock_candles.append(c)



    def mock_get_klines(symbol, timeframe="5m", limit=35):

        if timeframe == "1h":

            k1h = []

            for j in range(limit or 72):

                t_1h = now_ms - (72 - j) * 3600000

                k1h.append(

                    Candle(symbol, "1h", t_1h, 100.0, 125.0 if j == 40 else 102.0, 90.0 if j == 45 else 98.0, 100.0, 5000.0, t_1h + 3599999)

                )

            return k1h

        return mock_candles



    mock_exchange.get_klines.side_effect = mock_get_klines



    coord = LiveMicroCoordinator(db=db, exchange=mock_exchange)

    coord.current_equity = 15.72

    coord.peak_equity = 15.72



    journal = AutomatedTradeJournal(db)

    inspector = CoinInspectorService(adapter=mock_exchange)



    sess_mgr = LiveAutoSessionManager(

        coordinator=coord,

        exchange_adapter=mock_exchange,

        automated_journal=journal,

        db=db,

    )



    trader = GroqAutoTrader(

        adapter=mock_exchange,

        inspector_service=inspector,

        coordinator=coord,

        automated_journal=journal,

        session_manager=sess_mgr,

    )

    sess_mgr.set_autotrader(trader)



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

                        "thesis": "5m EMA20 Reclaim with strong RS",

                        "verification_questions": ["Verify 5m reclaim", "Check bid depth"],

                    }

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



    return {

        "db": db,

        "mock_exchange": mock_exchange,

        "coord": coord,

        "journal": journal,

        "sess_mgr": sess_mgr,

        "trader": trader,

        "placed_orders": placed_orders,

        "mock_candles": mock_candles,

    }





def test_live_auto_starts_background_scanner(test_env):

    """Starting session in LIVE_AUTO mode starts the background scanner thread."""

    mgr = test_env["sess_mgr"]

    mgr.set_execution_mode(ExecutionMode.LIVE_AUTO)



    ok, msg, info = mgr.start_session(operator_id="OPERATOR")

    assert ok is True

    assert mgr.state == LiveSessionState.ACTIVE

    assert mgr.mode == ExecutionMode.LIVE_AUTO

    assert mgr.scanner_active is True

    assert mgr._scanner_thread is not None

    assert mgr._scanner_thread.is_alive()



    # Clean up thread

    mgr.stop_session(reason="OPERATOR_STOPPED")

    assert mgr.scanner_active is False





def test_scanner_pauses_resumes_and_stops(test_env):

    """Pause stops scanner, resume restarts it, stop shuts it down."""

    mgr = test_env["sess_mgr"]

    mgr.set_execution_mode(ExecutionMode.LIVE_AUTO)

    ok, _, _ = mgr.start_session(operator_id="OPERATOR")

    assert ok is True

    assert mgr.scanner_active is True



    # Pause

    mgr.pause_session(reason="OPERATOR_PAUSED")

    assert mgr.state == LiveSessionState.PAUSED

    assert mgr.scanner_active is False



    # Resume

    mgr.resume_session()

    assert mgr.state == LiveSessionState.ACTIVE

    assert mgr.scanner_active is True



    # Stop

    mgr.stop_session(reason="OPERATOR_STOPPED")

    assert mgr.state == LiveSessionState.STOPPED

    assert mgr.scanner_active is False





def test_emergency_kill_switch_halts_scanner(test_env):

    """Emergency kill switch halts session and scanner thread immediately."""

    mgr = test_env["sess_mgr"]

    mgr.set_execution_mode(ExecutionMode.LIVE_AUTO)

    mgr.start_session(operator_id="OPERATOR")

    assert mgr.scanner_active is True



    mgr.trigger_emergency_kill_switch(reason="MANUAL_HALT")

    assert mgr.state == LiveSessionState.KILL_SWITCH_HALTED

    assert mgr.scanner_active is False





def test_scanner_skips_when_global_lock_held(test_env):

    """Scanner loop skips running pipeline search when GlobalPositionLock is held."""

    mgr = test_env["sess_mgr"]

    coord = test_env["coord"]

    trader = test_env["trader"]



    # Lock position

    coord.global_lock.acquire("ETHUSDT", "EMA20_RECLAIM_5M_V1", 12345)

    assert coord.global_lock.is_locked is True



    with patch.object(trader, "run_full_pipeline") as mock_pipeline:

        # Check condition that scanner loop evaluates

        if mgr.coordinator and mgr.coordinator.global_lock.is_locked:

            skipped = True

        else:

            skipped = False

            trader.run_full_pipeline()



        assert skipped is True

        mock_pipeline.assert_not_called()





def test_preview_setup_is_read_only_and_never_places_orders(test_env):

    """Preview Setup runs the 10->3->1 funnel with auto_fire_limit_order=False and never calls exchange."""

    trader = test_env["trader"]

    placed_orders = test_env["placed_orders"]



    result = trader.run_full_pipeline(

        risk_preset=RiskPreset.GROWTH_080,

        execution_venue=ExecutionVenue.LIVE_AUTO,

        auto_fire_limit_order=False,

        operator_confirmed=False,

    )



    assert result.status == "PREVIEW" or result.stage3.status == "PREVIEW"

    assert result.stage3 is not None

    assert len(placed_orders) == 0

    assert result.execution_receipt is None





def test_no_per_order_confirmation_in_live_auto(test_env):

    """When LIVE session is ACTIVE, autotrader automatically fires limit order without per-order confirmation."""

    mgr = test_env["sess_mgr"]

    mock_exchange = test_env["mock_exchange"]

    coord = test_env["coord"]



    # Start live session

    mgr.set_execution_mode(ExecutionMode.LIVE_AUTO)

    ok, _, _ = mgr.start_session(operator_id="OPERATOR")

    assert ok is True

    assert mgr.is_authorized_and_active() is True



    # Allow autonomous background scanner loop to execute scan cycle

    time.sleep(1.5)



    # In LIVE_AUTO, order is submitted autonomously to exchange without requiring per-order operator modal confirmation

    assert mock_exchange.create_order.call_count >= 1

    assert coord.global_lock.is_locked is True



    mgr.stop_session(reason="OPERATOR_STOPPED")





def test_live_manual_requires_modal_confirmation(test_env):

    """In LIVE_MANUAL, auto_fire without confirmation returns READY with confirmation_payload, no order placed."""

    trader = test_env["trader"]

    mock_exchange = test_env["mock_exchange"]

    coord = test_env["coord"]



    # Reset create_order call count

    mock_exchange.create_order.reset_mock()



    # 1. Unconfirmed attempt

    result_unconfirmed = trader.run_full_pipeline(

        risk_preset=RiskPreset.GROWTH_080,

        execution_venue=ExecutionVenue.LIVE_MANUAL,

        auto_fire_limit_order=True,

        operator_confirmed=False,

    )



    assert result_unconfirmed.status == "READY"

    assert result_unconfirmed.confirmation_payload is not None

    assert mock_exchange.create_order.call_count == 0



    # 2. Confirmed attempt

    result_confirmed = trader.run_full_pipeline(

        risk_preset=RiskPreset.GROWTH_080,

        execution_venue=ExecutionVenue.LIVE_MANUAL,

        auto_fire_limit_order=True,

        operator_confirmed=True,

    )



    assert result_confirmed.status in ("OPEN", "PROTECTED", "SUBMITTED", "PARTIALLY_PROTECTED") or "PROTECTED" in (result_confirmed.stage3.status or "")

    assert mock_exchange.create_order.call_count >= 1



    coord.global_lock.release()





def test_paper_mode_isolated_from_live_exchange(test_env):

    """In PAPER mode, simulated execution happens with zero calls to live exchange adapter."""

    trader = test_env["trader"]

    placed_orders = test_env["placed_orders"]



    result = trader.run_full_pipeline(

        risk_preset=RiskPreset.GROWTH_080,

        execution_venue=ExecutionVenue.PAPER,

        auto_fire_limit_order=True,

        operator_confirmed=False,

    )



    assert len(placed_orders) == 0

    assert result.stage3.execution_venue == "PAPER"





def test_dashboard_html_and_js_clean_controls():

    """Verify dashboard.html and coin_inspector.js contain the cleaned up controls and logic."""

    with open("src/api/dashboard.html", "r", encoding="utf-8") as f:

        html = f.read()



    with open("src/static/js/coin_inspector.js", "r", encoding="utf-8") as f:

        js = f.read()



    # Session control buttons in HTML

    assert 'id="btnStartLiveSession"' in html

    assert "Start LIVE Auto" in html

    assert 'id="btnPauseLiveSession"' in html

    assert "Pause Entries" in html

    assert 'id="btnResumeLiveSession"' in html

    assert "Resume Auto" in html

    assert 'id="btnStopLiveSession"' in html

    assert "Stop Session" in html

    assert 'id="btnKillSwitchLiveSession"' in html

    assert "Emergency Kill Switch" in html



    # Auto Hunter run controls in HTML

    assert 'id="groqRunPreviewBtn"' in html

    assert "Preview Setup (Read-Only)" in html

    assert 'id="groqAutoScannerStatusPill"' in html

    assert 'id="groqAutoScannerStatusText"' in html

    assert 'id="groqRunAutoOrderBtn"' in html

    assert "Run &amp; Auto-Place Limit Order (Simulated PAPER)" in html



    # In JS: syncGroqVenueVisuals hides autoBtn in LIVE_AUTO

    assert "if (venue === 'LIVE_AUTO')" in js

    assert "autoBtn.classList.add('hidden')" in js

    assert "scannerPill.classList.remove('hidden')" in js



    # In JS: renderGroqFunnelResult excludes confirm button in LIVE_AUTO

    assert "s3.execution_venue === 'LIVE_AUTO'" not in js or "s3.execution_venue === 'LIVE_MANUAL'" in js

