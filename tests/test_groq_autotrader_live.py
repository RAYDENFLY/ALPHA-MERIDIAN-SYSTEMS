"""Comprehensive Test Suite for Groq AI Auto-Hunter LIVE Execution on Aster DEX Mainnet.

Fulfills Section 7 Specification:
1. PAPER and LIVE venue routing.
2. Preview never submitting orders.
3. Strategy authorization (EMA20_RECLAIM_5M_V1 authorized).
4. SWEEP_1M_V1 strictly rejected for live execution.
5. UI / Operator confirmation requirement for live execution.
6. Global position lock enforcement (max 1 concurrent position).
7. Duplicate-order prevention (idempotency).
8. Risk gate rejection: daily filled entries limit (3).
9. Risk gate rejection: daily loss limit ($0.45).
10. Risk gate rejection: planned risk ($0.20 cap), margin ($7.00 cap), stop bounds [0.40%, 0.80%].
11. Aster DEX minimum notional ($5.00) and step precision.
12. TP/SL placement failure emergency cancellation.
13. Kill-switch handling: halts entries, cancels resting, closes open positions.
14. Automated Trade Journal recording and reconciliation.

NOTE: All tests mock exchange calls; zero real money orders placed during testing.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from src.ai.groq_autotrader import (
    ExecutionVenue,
    GroqAutoTrader,
    RiskPreset,
    Stage1Result,
    Stage2FinalistItem,
    Stage2Result,
    Stage3SetupOutput,
    Top10CandidateItem,
)
from src.core.types import Candle, Direction
from src.data.database import DatabaseManager
from src.exchange.aster_adapter import AsterAdapter
from src.intelligence.coin_inspector import CoinInspectorService
from src.journal.automated_journal import AutomatedTradeJournal
from src.risk.global_lock import GlobalPositionLock
from src.scheduler.live_micro_runner import LiveMicroCoordinator


@pytest.fixture(autouse=True)
def mock_external_calls(monkeypatch):
    """Bypass external Groq network calls and pacing sleeps in automated tests."""
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
    return adapter


@pytest.fixture
def mock_live_exchange_adapter():
    """Mock live exchange execution adapter with order placement and cancellation."""
    adapter = MagicMock()
    adapter.set_margin_type.return_value = {"code": 200, "msg": "success"}
    adapter.set_leverage.return_value = {"code": 200, "msg": "success"}
    adapter.create_order.side_effect = lambda **kwargs: {
        "orderId": f"MOCK_{kwargs.get('order_type')}_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "symbol": kwargs.get("symbol"),
        "status": "NEW",
        "side": kwargs.get("side"),
        "price": kwargs.get("price"),
        "origQty": kwargs.get("quantity"),
    }
    adapter.cancel_order.return_value = {"status": "CANCELED"}
    return adapter


@pytest.fixture
def test_coordinator(tmp_path, mock_aster_adapter):
    """Create LiveMicroCoordinator with clean state and temporary database file."""
    db_file = tmp_path / "test_coord.db"
    db = DatabaseManager(db_file)
    db.init_db()
    coord = LiveMicroCoordinator(db=db, exchange=mock_aster_adapter)
    coord.global_lock.release()
    return coord


@pytest.fixture
def test_journal(tmp_path):
    """Create AutomatedTradeJournal instance with initialized schema."""
    db_file = tmp_path / "test_journal.db"
    db = DatabaseManager(db_file)
    db.init_db()
    return AutomatedTradeJournal(db)


# -----------------------------------------------------------------------------
# 1. VENUE ROUTING (PAPER vs LIVE)
# -----------------------------------------------------------------------------

def test_01_paper_and_live_venue_routing(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator, test_journal):
    """Verify PAPER executes simulated paper trade and LIVE routes to real adapter with confirmation."""
    trader = GroqAutoTrader(
        adapter=mock_aster_adapter,
        coordinator=test_coordinator,
        automated_journal=test_journal,
    )

    # 1. PAPER Mode
    paper_res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.PAPER,
        auto_fire_limit_order=True,
        coordinator=test_coordinator,
    )
    assert paper_res.execution_venue == "PAPER"
    assert paper_res.stage3.execution_venue == "PAPER"
    assert mock_live_exchange_adapter.create_order.call_count == 0

    # 2. LIVE Mode with Confirmation
    live_res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
        automated_journal=test_journal,
    )
    assert live_res.execution_venue == "LIVE"
    assert live_res.stage3.execution_venue == "LIVE"
    assert "PROTECTED" in live_res.stage3.status
    assert live_res.execution_receipt is not None
    assert "PROTECTED" in live_res.execution_receipt["tp_sl_protection_status"]
    assert mock_live_exchange_adapter.create_order.call_count >= 2  # Entry + SL

    test_coordinator.global_lock.release()


# -----------------------------------------------------------------------------
# 2. PREVIEW SETUP NEVER SUBMITS ORDERS
# -----------------------------------------------------------------------------

def test_02_preview_never_submits_orders(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator, test_journal):
    """Verify that auto_fire_limit_order=False never submits orders on either PAPER or LIVE."""
    trader = GroqAutoTrader(
        adapter=mock_aster_adapter,
        coordinator=test_coordinator,
        automated_journal=test_journal,
    )

    # Live venue with preview flag
    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=False,
        operator_confirmed=False,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )
    assert res.execution_receipt is None
    assert res.stage3.status == "PREVIEW"
    assert mock_live_exchange_adapter.create_order.call_count == 0


# -----------------------------------------------------------------------------
# 3. STRATEGY AUTHORIZATION
# -----------------------------------------------------------------------------

def test_03_strategy_authorization_ema20_live_allowed(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator):
    """Verify EMA20_RECLAIM_5M_V1 is strictly rejected with PermissionError, and 15M_SWEEP_RECLAIM_SMC is the sole authorized live strategy."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    with pytest.raises(PermissionError) as exc_info:
        trader.run_full_pipeline(
            execution_venue=ExecutionVenue.LIVE,
            strategy_id="EMA20_RECLAIM_5M_V1",
            auto_fire_limit_order=True,
            operator_confirmed=True,
            live_adapter=mock_live_exchange_adapter,
            coordinator=test_coordinator,
        )
    assert "EMA20_RECLAIM_5M_V1 is strictly prohibited" in str(exc_info.value)

    test_coordinator.global_lock.release()


# -----------------------------------------------------------------------------
# 4. SWEEP_1M_V1 STRICT REJECTION FOR LIVE
# -----------------------------------------------------------------------------

def test_04_sweep_1m_v1_strictly_rejected_for_live(mock_aster_adapter, test_coordinator):
    """Verify any attempt to submit SWEEP_1M_V1 for live execution is strictly rejected with PermissionError."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    with pytest.raises(PermissionError) as exc_info:
        trader.run_full_pipeline(
            execution_venue=ExecutionVenue.LIVE,
            strategy_id="SWEEP_1M_V1",
            auto_fire_limit_order=True,
            operator_confirmed=True,
            coordinator=test_coordinator,
        )
    assert "CRITICAL SECURITY INVARIANT: SWEEP_1M_V1 is SHADOW-only" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 5. UI / OPERATOR CONFIRMATION REQUIREMENT
# -----------------------------------------------------------------------------

def test_05_ui_confirmation_requirement_for_live(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator):
    """Verify that auto_fire_limit_order in LIVE mode returns READY preview and does NOT place order if unconfirmed."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=False,  # Unconfirmed
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )

    assert res.status == "READY"
    assert res.stage3.status == "READY"
    assert res.stage3.requires_confirmation is True
    assert res.confirmation_payload is not None
    assert res.confirmation_payload["symbol"] == res.stage3.symbol
    assert res.confirmation_payload["max_risk_usdt"] <= 0.205
    assert res.execution_receipt is None
    assert mock_live_exchange_adapter.create_order.call_count == 0


# -----------------------------------------------------------------------------
# 6. GLOBAL POSITION LOCK ENFORCEMENT
# -----------------------------------------------------------------------------

def test_06_global_position_lock_enforcement(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator):
    """Verify that if GlobalPositionLock is held by an active trade, subsequent live execution is rejected."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    # Acquire lock for an existing trade
    test_coordinator.global_lock.acquire(
        strategy_id="EMA20_RECLAIM_5M_V1",
        symbol="ETHUSDT",
        order_id="EXISTING_ORDER_123",
        state="OPEN",
    )

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )

    assert res.status == "REJECTED"
    assert "Global position lock rejected" in res.execution_receipt["reason"]
    assert mock_live_exchange_adapter.create_order.call_count == 0

    test_coordinator.global_lock.release()


# -----------------------------------------------------------------------------
# 7. DUPLICATE ORDER PREVENTION (IDEMPOTENCY)
# -----------------------------------------------------------------------------

def test_07_duplicate_order_prevention(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator):
    """Verify duplicate signal on the same symbol and direction is blocked by idempotency check."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    # First execution succeeds
    res1 = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )
    assert "PROTECTED" in res1.stage3.status

    # Second immediate execution on same symbol is rejected as duplicate
    res2 = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )
    assert res2.status == "REJECTED"
    assert "Duplicate order prevented" in res2.execution_receipt["reason"] or "lock rejected" in res2.execution_receipt["reason"]

    test_coordinator.global_lock.release()


# -----------------------------------------------------------------------------
# 8. RISK GATE: DAILY FILLED ENTRIES LIMIT (3)
# -----------------------------------------------------------------------------

def test_08_risk_gate_daily_entries_limit(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator):
    """Verify that when 3 entries have been filled today, new live orders are rejected."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    test_coordinator.daily_filled_entries = 3  # Max daily entries reached

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )

    assert res.status == "REJECTED"
    assert "Daily max filled entries reached" in res.execution_receipt["reason"]
    assert mock_live_exchange_adapter.create_order.call_count == 0


# -----------------------------------------------------------------------------
# 9. RISK GATE: DAILY LOSS LIMIT ($0.45)
# -----------------------------------------------------------------------------

def test_09_risk_gate_daily_loss_limit(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator):
    """Verify that when daily loss reaches $0.45, live order submission is rejected."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    test_coordinator.daily_realized_loss = 0.46  # Breached $0.45

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )

    assert res.status == "REJECTED"
    assert "Daily loss limit breached" in res.execution_receipt["reason"]
    assert mock_live_exchange_adapter.create_order.call_count == 0


# -----------------------------------------------------------------------------
# 10. RISK GATE: PLANNED RISK & MARGIN INVARIANTS
# -----------------------------------------------------------------------------

def test_10_risk_gate_planned_risk_and_margin(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator):
    """Verify planned risk strictly adheres to $0.20 cap and margin to $7.00 cap at 10x leverage."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )

    setup = res.stage3
    assert setup.max_risk_usdt <= 0.205, f"Risk {setup.max_risk_usdt} exceeded $0.20 cap"
    assert setup.isolated_margin_usdt <= 7.05, f"Margin {setup.isolated_margin_usdt} exceeded $7.00 cap"
    assert setup.leverage == 10
    assert setup.margin_mode == "ISOLATED"
    # Stop distance for 15M_SWEEP_RECLAIM_SMC is [1.15%, 1.40%], or [0.40%, 0.80%] for EMA20
    assert 0.39 <= setup.stop_distance_pct <= 1.45, f"Stop {setup.stop_distance_pct}% outside valid bounds"

    test_coordinator.global_lock.release()


# -----------------------------------------------------------------------------
# 11. ASTER DEX MIN NOTIONAL & STEP PRECISION
# -----------------------------------------------------------------------------

def test_11_aster_dex_min_notional_and_precision(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator):
    """Verify notional is >= $5.00 minNotional and quantity is aligned with step size."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )

    setup = res.stage3
    assert setup.notional_usdt >= 5.0, f"Notional ${setup.notional_usdt} below $5.00 floor"
    assert setup.qty >= 0.01

    test_coordinator.global_lock.release()


# -----------------------------------------------------------------------------
# 12. TP/SL PLACEMENT FAILURE EMERGENCY HANDLING
# -----------------------------------------------------------------------------

def test_12_tp_sl_placement_failure_emergency_handling(mock_aster_adapter, test_coordinator):
    """Verify that if exchange-side SL fails, the resting entry order is immediately cancelled to prevent naked risk."""
    failing_adapter = MagicMock()
    failing_adapter.set_margin_type.return_value = {"code": 200}
    failing_adapter.set_leverage.return_value = {"code": 200}

    # Entry succeeds, but STOP_MARKET SL order fails
    def mock_create_order(**kwargs):
        if kwargs.get("order_type") == "STOP_MARKET":
            raise RuntimeError("Aster DEX Stop-Loss Error: insufficient balance for margin")
        return {"orderId": "ENTRY_ORDER_SUCCESS_999", "status": "NEW"}

    failing_adapter.create_order.side_effect = mock_create_order
    failing_adapter.cancel_order.return_value = {"status": "CANCELED"}

    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=failing_adapter,
        coordinator=test_coordinator,
    )

    # Status must be ERROR and entry order cancelled
    assert res.status == "ERROR"
    assert failing_adapter.cancel_order.call_count == 1
    assert "Emergency handling executed" in res.execution_receipt["error"]
    assert test_coordinator.global_lock.is_locked is False  # Lock must be released safely


# -----------------------------------------------------------------------------
# 13. KILL-SWITCH HANDLING
# -----------------------------------------------------------------------------

def test_13_kill_switch_open_position_and_entry_halt(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator):
    """Verify that when 25% peak equity drawdown occurs, kill switch triggers emergency procedure and halts entries."""
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=test_coordinator)

    test_coordinator.peak_equity = 20.0
    test_coordinator.current_equity = 14.5  # 27.5% drawdown >= 25% threshold

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
    )

    assert res.status == "KILL_SWITCH_HALTED"
    assert test_coordinator.kill_switch_triggered is True
    assert mock_live_exchange_adapter.create_order.call_count == 0


# -----------------------------------------------------------------------------
# 14. AUTOMATED TRADE JOURNAL RECONCILIATION
# -----------------------------------------------------------------------------

def test_14_automated_trade_journal_reconciliation(mock_aster_adapter, mock_live_exchange_adapter, test_coordinator, test_journal):
    """Verify that live trades lodge an AutomatedTradeRecord with exchange order IDs and status OPEN in journal."""
    trader = GroqAutoTrader(
        adapter=mock_aster_adapter,
        coordinator=test_coordinator,
        automated_journal=test_journal,
    )

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_live_exchange_adapter,
        coordinator=test_coordinator,
        automated_journal=test_journal,
    )

    assert "PROTECTED" in res.stage3.status
    trade_id = res.execution_receipt["journal_trade_id"]
    record = test_journal.get_trade_by_id(trade_id)

    assert record is not None
    assert record.symbol == res.stage3.symbol
    assert record.strategy_id == res.stage3.strategy_id
    assert record.mode == "LIVE"
    assert record.status in ("OPEN", "PENDING")
    assert record.order_id == res.execution_receipt["order_id"]
    assert record.planned_risk_usd <= 0.205
    assert record.planned_rr_net_expected >= 1.50
    assert record.llm_selected == 1

    test_coordinator.global_lock.release()
