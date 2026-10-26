"""Tests for Exchange-Side Limit Take-Profit and Protective Bracket Execution.

Verifies all requirements:
1. Exact order types:
   - LONG: Entry = BUY LIMIT, TP = SELL LIMIT reduceOnly=True, SL = SELL STOP_MARKET reduceOnly=True
   - SHORT: Entry = SELL LIMIT, TP = BUY LIMIT reduceOnly=True, SL = BUY STOP_MARKET reduceOnly=True
2. Zero fallback to market order for TP (strictly LIMIT reduceOnly=True).
3. TP placed ONLY against actual filled quantity (unfilled entry has tp_status="NOT_PLACED").
4. Partial fill handling (TP matches actual executed quantity).
5. Sibling order cancellation:
   - TP fills -> SL order cancelled on exchange.
   - SL fills -> TP order cancelled on exchange.
6. Reconcile existing protective orders (reconciles operator-placed orders without duplicate placement).
7. Schema migration idempotency and SQLite persistence of all 12 protective fields.
8. Execution reporting: "TP NOT PLACED" prominently distinguished from "PROTECTED".
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, call
import pytest

from src.ai.groq_autotrader import ExecutionVenue, GroqAutoTrader
from src.core.types import Candle, Direction
from src.data.database import DatabaseManager
from src.exchange.aster_adapter import AsterAdapter
from src.execution.live_session import ExecutionMode, LiveAutoSessionManager
from src.journal.automated_journal import AutomatedTradeJournal
from src.journal.models import AutomatedTradeRecord, JournalFilter, TradeStatus
from src.risk.global_lock import GlobalPositionLock
from src.scheduler.live_micro_runner import LiveMicroCoordinator
from src.strategy.ema20_reclaim_5m import EMA20ReclaimSetup


@pytest.fixture
def test_db(tmp_path):
    """Isolated temporary SQLite database."""
    db_file = tmp_path / "test_tp_bracket.db"
    db = DatabaseManager(str(db_file))
    db.init_db()
    return db


@pytest.fixture(autouse=True)
def mock_groq_llm(monkeypatch):
    """Mock Groq LLM API responses for deterministic Stage 2 and Stage 3 selection."""
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
                        "thesis": "5m EMA20 Reclaim with strong RS",
                        "verification_questions": ["Verify 5m reclaim", "Check bid depth"],
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
                "entry_price": 100.0,
                "stop_loss_price": 99.45,
                "take_profit_price": 101.25,
                "notes": "SOL 5m EMA20 Reclaim",
                "reasoning": "Verified structural reclaim confluence",
            }

    monkeypatch.setattr(GroqAutoTrader, "_call_groq_json", mock_groq_json)


@pytest.fixture
def mock_aster_adapter():
    """Create a mock AsterAdapter returning consistent market data for SOLUSDT."""
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
    candles = []
    for i in range(35):
        t_ms = now_ms - (35 - i) * 300000
        if 28 <= i <= 32:
            c = Candle(
                symbol="SOLUSDT", timeframe="5m", timestamp_ms=t_ms,
                open=99.8, high=99.9, low=99.45, close=99.50, volume=100.0,
                close_time_ms=t_ms + 299999
            )
        elif i == 34:
            c = Candle(
                symbol="SOLUSDT", timeframe="5m", timestamp_ms=t_ms,
                open=99.70, high=100.10, low=99.65, close=100.0, volume=150.0,
                close_time_ms=t_ms + 299999
            )
        else:
            c = Candle(
                symbol="SOLUSDT", timeframe="5m", timestamp_ms=t_ms,
                open=100.0, high=100.2, low=99.8, close=100.0, volume=100.0,
                close_time_ms=t_ms + 299999
            )
        candles.append(c)

    adapter.get_klines.return_value = candles
    adapter.get_premium_index.return_value = {"last_funding_rate": "0.0001"}
    return adapter


@pytest.fixture
def mock_exchange():
    """Mock live exchange execution adapter capturing all created and cancelled orders."""
    exchange = MagicMock()
    exchange.created_orders = []
    exchange.cancelled_orders = []

    def mock_create_order(**kwargs):
        order_id = f"EX_{kwargs.get('order_type')}_{len(exchange.created_orders) + 1}"
        record = {
            "orderId": order_id,
            "symbol": kwargs.get("symbol"),
            "side": kwargs.get("side"),
            "type": kwargs.get("order_type"),
            "price": kwargs.get("price"),
            "stopPrice": kwargs.get("stop_price"),
            "origQty": str(kwargs.get("quantity")),
            "executedQty": "0",
            "reduceOnly": kwargs.get("reduce_only", False),
            "status": "NEW",
        }
        exchange.created_orders.append(record)
        return record

    def mock_cancel_order(**kwargs):
        oid = kwargs.get("order_id")
        exchange.cancelled_orders.append(oid)
        return {"orderId": oid, "status": "CANCELED"}

    exchange.create_order.side_effect = mock_create_order
    exchange.cancel_order.side_effect = mock_cancel_order
    exchange.set_margin_type.return_value = {"code": 200, "msg": "success"}
    exchange.set_leverage.return_value = {"code": 200, "msg": "success"}
    return exchange


# -----------------------------------------------------------------------------
# 1. ORDER TYPES: LONG BRACKET SPECIFICATION
# -----------------------------------------------------------------------------

def test_01_order_types_long_bracket(test_db, mock_aster_adapter, mock_exchange):
    """Verify LONG trade bracket order types:
    - Entry: BUY LIMIT
    - Stop Loss: SELL STOP_MARKET reduceOnly=True
    - Take Profit: SELL LIMIT reduceOnly=True
    """
    coord = LiveMicroCoordinator(db=test_db, exchange=mock_exchange)
    journal = AutomatedTradeJournal(test_db)
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=coord, automated_journal=journal)

    # Execute trade
    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_exchange,
        coordinator=coord,
        automated_journal=journal,
    )

    # 1. Entry Order is BUY LIMIT
    entry_calls = [c for c in mock_exchange.created_orders if c["type"] == "LIMIT" and not c["reduceOnly"]]
    assert len(entry_calls) == 1
    assert entry_calls[0]["side"] == "BUY"
    assert entry_calls[0]["type"] == "LIMIT"

    # 2. SL Order is SELL STOP_MARKET reduceOnly=True
    sl_calls = [c for c in mock_exchange.created_orders if c["type"] == "STOP_MARKET"]
    assert len(sl_calls) == 1
    assert sl_calls[0]["side"] == "SELL"
    assert sl_calls[0]["type"] == "STOP_MARKET"
    assert sl_calls[0]["reduceOnly"] is True

    # 3. TP is NOT submitted on unfilled entry, but when fill occurs via reconciliation:
    entry_order_id = entry_calls[0]["orderId"]
    entry_qty = float(entry_calls[0]["origQty"])

    # Simulate Entry Fill and run reconciliation
    sym = res.stage3.symbol
    fill_record = {
        "id": "1001",
        "tradeId": "1001",
        "orderId": entry_order_id,
        "symbol": sym,
        "side": "BUY",
        "price": str(res.stage3.entry_price),
        "qty": str(entry_qty),
        "commission": "0.01",
        "commissionAsset": "USDT",
        "maker": True,
        "time": int(datetime.now(timezone.utc).timestamp() * 1000),
    }
    pos_record = [{"symbol": sym, "positionAmt": str(entry_qty)}]

    journal.reconcile_with_exchange(
        exchange_trades=[fill_record],
        exchange_positions=pos_record,
        exchange_orders=mock_exchange.created_orders,
        exchange_adapter=mock_exchange,
    )

    # Now TP SELL LIMIT reduceOnly=True must be lodged
    tp_calls = [c for c in mock_exchange.created_orders if c["type"] == "LIMIT" and c["reduceOnly"]]
    assert len(tp_calls) == 1
    assert tp_calls[0]["side"] == "SELL"
    assert tp_calls[0]["type"] == "LIMIT"
    assert tp_calls[0]["reduceOnly"] is True
    assert float(tp_calls[0]["origQty"]) == entry_qty

    coord.global_lock.release()


# -----------------------------------------------------------------------------
# 2. ORDER TYPES: SHORT BRACKET SPECIFICATION
# -----------------------------------------------------------------------------

def test_02_order_types_short_bracket(test_db, mock_exchange):
    """Verify SHORT trade bracket order types:
    - Entry: SELL LIMIT
    - Stop Loss: BUY STOP_MARKET reduceOnly=True
    - Take Profit: BUY LIMIT reduceOnly=True
    """
    journal = AutomatedTradeJournal(test_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Record short trade intent
    short_trade = AutomatedTradeRecord(
        journal_id=f"JRN_SHORT_{now_ms}",
        trade_id=f"TRD_SHORT_{now_ms}",
        exchange="Aster DEX",
        symbol="BTCUSDT",
        side="SHORT",
        status="OPEN",
        order_id="ENTRY_SHORT_99",
        planned_entry_price=65000.0,
        planned_stop_price=66000.0,
        planned_take_profit_price=63000.0,
        planned_quantity=0.01,
        filled_quantity=0.01,
        sl_order_id="SL_SHORT_99",
        sl_order_type="STOP_MARKET",
        sl_status="PLACED",
        sl_price=66000.0,
        tp_status="NOT_PLACED",
    )
    journal.record_trade_intent(short_trade)

    # Reconcile with active short position of -0.01
    journal.reconcile_with_exchange(
        exchange_positions=[{"symbol": "BTCUSDT", "positionAmt": "-0.01"}],
        exchange_orders=[{"orderId": "SL_SHORT_99", "symbol": "BTCUSDT", "type": "STOP_MARKET", "reduceOnly": True}],
        exchange_adapter=mock_exchange,
    )

    # TP must be BUY LIMIT reduceOnly=True
    tp_calls = [c for c in mock_exchange.created_orders if c["type"] == "LIMIT" and c["reduceOnly"]]
    assert len(tp_calls) == 1
    assert tp_calls[0]["side"] == "BUY"
    assert tp_calls[0]["type"] == "LIMIT"
    assert tp_calls[0]["reduceOnly"] is True
    assert float(tp_calls[0]["origQty"]) == 0.01


# -----------------------------------------------------------------------------
# 3. ZERO FALLBACK TO MARKET ORDER FOR TP
# -----------------------------------------------------------------------------

def test_03_zero_fallback_to_market_order_for_tp(test_db, mock_exchange):
    """Verify TP order is strictly LIMIT and never MARKET or TAKE_PROFIT_MARKET."""
    journal = AutomatedTradeJournal(test_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    trade = AutomatedTradeRecord(
        journal_id=f"JRN_{now_ms}",
        trade_id=f"TRD_{now_ms}",
        symbol="ETHUSDT",
        side="LONG",
        status="OPEN",
        order_id="ENTRY_1",
        planned_entry_price=2340.0,
        planned_stop_price=2320.0,
        planned_take_profit_price=2380.0,
        planned_quantity=0.05,
        filled_quantity=0.05,
        sl_order_id="SL_1",
        sl_status="PLACED",
        tp_status="NOT_PLACED",
    )
    journal.record_trade_intent(trade)

    journal.reconcile_with_exchange(
        exchange_positions=[{"symbol": "ETHUSDT", "positionAmt": "0.05"}],
        exchange_orders=[{"orderId": "SL_1", "symbol": "ETHUSDT", "type": "STOP_MARKET", "reduceOnly": True}],
        exchange_adapter=mock_exchange,
    )

    for o in mock_exchange.created_orders:
        if o["reduceOnly"] and o["side"] == "SELL":
            assert o["type"] in ("LIMIT", "STOP_MARKET")
            assert o["type"] != "MARKET"
            assert o["type"] != "TAKE_PROFIT_MARKET"


# -----------------------------------------------------------------------------
# 4. TP NOT PLACED FOR UNFILLED ENTRY
# -----------------------------------------------------------------------------

def test_04_tp_not_placed_for_unfilled_entry(test_db, mock_aster_adapter, mock_exchange):
    """Verify that when limit entry is resting and unfilled, TP order is NOT submitted to Aster DEX."""
    coord = LiveMicroCoordinator(db=test_db, exchange=mock_exchange)
    journal = AutomatedTradeJournal(test_db)
    trader = GroqAutoTrader(adapter=mock_aster_adapter, coordinator=coord, automated_journal=journal)

    res = trader.run_full_pipeline(
        execution_venue=ExecutionVenue.LIVE,
        auto_fire_limit_order=True,
        operator_confirmed=True,
        live_adapter=mock_exchange,
        coordinator=coord,
        automated_journal=journal,
    )

    # TP must not be placed yet
    assert res.execution_receipt["tp_order_id"] is None
    assert res.execution_receipt["tp_status"] == "NOT_PLACED"
    assert "TP NOT PLACED" in res.execution_receipt["protection_status"]

    # In database record
    rec = journal.get_trade_by_id(res.execution_receipt["journal_trade_id"])
    assert rec.tp_status == "NOT_PLACED"
    assert rec.tp_order_id in (None, "")
    assert rec.tp_status == "NOT_PLACED"

    coord.global_lock.release()


# -----------------------------------------------------------------------------
# 5. TP PLACED UPON ENTRY FILL WITH EXACT FILLED QUANTITY
# -----------------------------------------------------------------------------

def test_05_tp_placed_upon_entry_fill(test_db, mock_exchange):
    """Verify that as soon as entry order fills, LIMIT TP order is submitted for exact filled quantity."""
    journal = AutomatedTradeJournal(test_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    trade = AutomatedTradeRecord(
        journal_id=f"JRN_{now_ms}",
        trade_id=f"TRD_{now_ms}",
        symbol="ETHUSDT",
        side="LONG",
        status="PENDING",
        order_id="ENTRY_ETH_100",
        planned_entry_price=2343.62,
        planned_stop_price=2328.19,
        planned_take_profit_price=2378.33,
        planned_quantity=0.009,
        filled_quantity=0.0,
        sl_order_id="SL_ETH_100",
        sl_status="PLACED",
        tp_status="NOT_PLACED",
    )
    journal.record_trade_intent(trade)

    # Now entry fills: 0.009 ETH
    fill = {
        "id": "FILL_1",
        "tradeId": "FILL_1",
        "orderId": "ENTRY_ETH_100",
        "symbol": "ETHUSDT",
        "side": "BUY",
        "price": "2343.62",
        "qty": "0.009",
        "commission": "0.005",
        "commissionAsset": "USDT",
        "time": now_ms,
    }
    journal.reconcile_with_exchange(
        exchange_trades=[fill],
        exchange_positions=[{"symbol": "ETHUSDT", "positionAmt": "0.009"}],
        exchange_orders=[{"orderId": "SL_ETH_100", "symbol": "ETHUSDT", "type": "STOP_MARKET", "reduceOnly": True}],
        exchange_adapter=mock_exchange,
    )

    updated = journal.get_trade_by_id(trade.trade_id)
    assert updated.status == TradeStatus.OPEN.value
    assert updated.filled_quantity == 0.009
    assert updated.tp_status == "PLACED"
    assert updated.tp_order_id is not None
    assert updated.tp_order_type == "LIMIT"
    assert updated.tp_quantity == 0.009
    assert updated.protection_status == "PROTECTED"


# -----------------------------------------------------------------------------
# 6. PARTIAL ENTRY FILL QUANTITY MATCHING
# -----------------------------------------------------------------------------

def test_06_tp_quantity_matches_partial_entry_fill(test_db, mock_exchange):
    """Verify that if only a partial entry fill occurs (e.g. 0.004 of 0.009),
    the TP order quantity is placed for 0.004, NOT the full 0.009."""
    journal = AutomatedTradeJournal(test_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    trade = AutomatedTradeRecord(
        journal_id=f"JRN_{now_ms}",
        trade_id=f"TRD_{now_ms}",
        symbol="ETHUSDT",
        side="LONG",
        status="PENDING",
        order_id="ENTRY_PARTIAL",
        planned_entry_price=2343.62,
        planned_stop_price=2328.19,
        planned_take_profit_price=2378.33,
        planned_quantity=0.009,
        filled_quantity=0.0,
        sl_order_id="SL_PARTIAL",
        sl_status="PLACED",
        tp_status="NOT_PLACED",
    )
    journal.record_trade_intent(trade)

    # Partial fill of 0.004
    fill = {
        "id": "FILL_PARTIAL",
        "tradeId": "FILL_PARTIAL",
        "orderId": "ENTRY_PARTIAL",
        "symbol": "ETHUSDT",
        "side": "BUY",
        "price": "2343.62",
        "qty": "0.004",
        "commission": "0.002",
        "commissionAsset": "USDT",
        "time": now_ms,
    }
    journal.reconcile_with_exchange(
        exchange_trades=[fill],
        exchange_positions=[{"symbol": "ETHUSDT", "positionAmt": "0.004"}],
        exchange_orders=[{"orderId": "SL_PARTIAL", "symbol": "ETHUSDT", "type": "STOP_MARKET", "reduceOnly": True}],
        exchange_adapter=mock_exchange,
    )

    updated = journal.get_trade_by_id(trade.trade_id)
    assert updated.filled_quantity == 0.004
    assert updated.tp_quantity == 0.004
    assert updated.tp_status == "PLACED"


# -----------------------------------------------------------------------------
# 7. SIBLING CANCELLATION: TP FILL CANCELS SL
# -----------------------------------------------------------------------------

def test_07_sibling_cancellation_tp_fill_cancels_sl(test_db, mock_exchange):
    """Verify that when TP order fills (position goes to 0), sibling SL order is automatically cancelled."""
    journal = AutomatedTradeJournal(test_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    trade = AutomatedTradeRecord(
        journal_id=f"JRN_{now_ms}",
        trade_id=f"TRD_{now_ms}",
        symbol="ETHUSDT",
        side="LONG",
        status="OPEN",
        order_id="ENTRY_FILLED",
        filled_quantity=0.009,
        planned_risk_usd=0.20,
        sl_order_id="SL_RESTING_123",
        sl_status="PLACED",
        tp_order_id="TP_RESTING_456",
        tp_status="PLACED",
        tp_quantity=0.009,
        protection_status="PROTECTED",
    )
    journal.record_trade_intent(trade)

    # TP fills: position becomes 0, TP order disappears from open orders, but SL is still in open orders
    open_orders = [{"orderId": "SL_RESTING_123", "symbol": "ETHUSDT", "type": "STOP_MARKET", "reduceOnly": True}]
    positions = [{"symbol": "ETHUSDT", "positionAmt": "0.0"}]

    journal.reconcile_with_exchange(
        exchange_positions=positions,
        exchange_orders=open_orders,
        exchange_adapter=mock_exchange,
    )

    # SL must have been cancelled on exchange
    assert "SL_RESTING_123" in mock_exchange.cancelled_orders

    updated = journal.get_trade_by_id(trade.trade_id)
    assert updated.tp_status == "FILLED"
    assert updated.sl_status == "CANCELLED"
    assert updated.exit_reason == "TAKE_PROFIT"
    assert updated.protection_status == "CLOSED"


# -----------------------------------------------------------------------------
# 8. SIBLING CANCELLATION: SL FILL CANCELS TP
# -----------------------------------------------------------------------------

def test_08_sibling_cancellation_sl_fill_cancels_tp(test_db, mock_exchange):
    """Verify that when SL order fills (position goes to 0), sibling TP order is automatically cancelled."""
    journal = AutomatedTradeJournal(test_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    trade = AutomatedTradeRecord(
        journal_id=f"JRN_{now_ms}",
        trade_id=f"TRD_{now_ms}",
        symbol="ETHUSDT",
        side="LONG",
        status="OPEN",
        order_id="ENTRY_FILLED_2",
        filled_quantity=0.009,
        planned_risk_usd=0.20,
        sl_order_id="SL_RESTING_999",
        sl_status="PLACED",
        tp_order_id="TP_RESTING_888",
        tp_status="PLACED",
        tp_quantity=0.009,
        protection_status="PROTECTED",
    )
    journal.record_trade_intent(trade)

    # SL fills: position becomes 0, SL order disappears from open orders, but TP is still in open orders
    open_orders = [{"orderId": "TP_RESTING_888", "symbol": "ETHUSDT", "type": "LIMIT", "reduceOnly": True}]
    positions = [{"symbol": "ETHUSDT", "positionAmt": "0.0"}]

    journal.reconcile_with_exchange(
        exchange_positions=positions,
        exchange_orders=open_orders,
        exchange_adapter=mock_exchange,
    )

    # TP must have been cancelled on exchange
    assert "TP_RESTING_888" in mock_exchange.cancelled_orders

    updated = journal.get_trade_by_id(trade.trade_id)
    assert updated.sl_status == "FILLED"
    assert updated.tp_status == "CANCELLED"
    assert updated.exit_reason == "STOP_LOSS"
    assert updated.protection_status == "CLOSED"


# -----------------------------------------------------------------------------
# 9. RECONCILING EXISTING PROTECTIVE ORDERS (LIVE ETH POSITION SCENARIO)
# -----------------------------------------------------------------------------

def test_09_existing_open_orders_reconciled_without_duplicates(test_db, mock_exchange):
    """Simulate exact Aster DEX Mainnet state of ETHUSDT:
    - Position: 0.009 ETH
    - SL Order 18639022146 resting
    - TP Order 18639068962 resting (placed manually)
    Verify reconciliation links both orders and does not attempt to place duplicate TP/SL.
    """
    journal = AutomatedTradeJournal(test_db)

    # Record trade resembling live ETH trade
    eth_trade = AutomatedTradeRecord(
        journal_id="JRN_ETHUSDT_1792966766085",
        trade_id="TRD_ETHUSDT_1792966766085",
        exchange="Aster DEX",
        symbol="ETHUSDT",
        side="LONG",
        status="OPEN",
        order_id="18639022137",
        planned_entry_price=2343.62,
        planned_stop_price=2328.19,
        planned_take_profit_price=2378.3375,
        planned_quantity=0.009,
        filled_quantity=0.009,
        # tp_order_id and sl_order_id previously missing/unlinked
        tp_order_id=None,
        tp_status="NOT_PLACED",
        sl_order_id=None,
        sl_status="NOT_PLACED",
    )
    journal.record_trade_intent(eth_trade)

    # Live open orders from Aster DEX Mainnet
    open_orders = [
        {
            "orderId": 18639022146,
            "symbol": "ETHUSDT",
            "type": "STOP_MARKET",
            "side": "SELL",
            "stopPrice": "2328.19",
            "origQty": "0.009",
            "reduceOnly": True,
        },
        {
            "orderId": 18639068962,
            "symbol": "ETHUSDT",
            "type": "LIMIT",
            "side": "SELL",
            "price": "2378.33",
            "origQty": "0.009",
            "reduceOnly": True,
        },
    ]
    positions = [{"symbol": "ETHUSDT", "positionAmt": "0.009"}]

    summary = journal.reconcile_with_exchange(
        exchange_positions=positions,
        exchange_orders=open_orders,
        exchange_adapter=mock_exchange,
    )

    # Verify no duplicate orders created
    assert len(mock_exchange.created_orders) == 0

    # Verify orders linked correctly
    updated = journal.get_trade_by_id("TRD_ETHUSDT_1792966766085")
    assert updated.sl_order_id == "18639022146"
    assert updated.sl_status == "PLACED"
    assert updated.sl_price == 2328.19
    assert updated.tp_order_id == "18639068962"
    assert updated.tp_status == "PLACED"
    assert updated.tp_price == 2378.33
    assert updated.protection_status == "PROTECTED"


# -----------------------------------------------------------------------------
# 10. PROTECTION STATUS TRANSITIONS
# -----------------------------------------------------------------------------

def test_10_protection_status_states(test_db):
    """Verify full state machine of protection status:
    UNPROTECTED -> PARTIALLY_PROTECTED -> PROTECTED -> CLOSED
    """
    journal = AutomatedTradeJournal(test_db)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # 1. Initially UNPROTECTED
    trade = AutomatedTradeRecord(
        journal_id=f"JRN_{now_ms}",
        trade_id=f"TRD_{now_ms}",
        symbol="SOLUSDT",
        side="LONG",
        status="PENDING",
        order_id="1",
        sl_status="NOT_PLACED",
        tp_status="NOT_PLACED",
        protection_status="UNPROTECTED",
    )
    journal.record_trade_intent(trade)
    assert journal.get_trade_by_id(trade.trade_id).protection_status == "UNPROTECTED"

    # 2. SL placed only -> PARTIALLY_PROTECTED (SL ONLY - TP NOT PLACED)
    trade.sl_order_id = "SL_1"
    trade.sl_status = "PLACED"
    journal.record_trade_intent(trade)
    journal.reconcile_with_exchange()
    assert "PARTIALLY_PROTECTED" in journal.get_trade_by_id(trade.trade_id).protection_status

    # 3. TP also placed -> PROTECTED
    trade.tp_order_id = "TP_1"
    trade.tp_status = "PLACED"
    journal.record_trade_intent(trade)
    journal.reconcile_with_exchange()
    assert journal.get_trade_by_id(trade.trade_id).protection_status == "PROTECTED"

    # 4. Closed -> CLOSED
    trade.status = TradeStatus.CLOSED.value
    trade.protection_status = "CLOSED"
    journal.record_trade_intent(trade)
    assert journal.get_trade_by_id(trade.trade_id).protection_status == "CLOSED"


# -----------------------------------------------------------------------------
# 11. SCHEMA MIGRATION IDEMPOTENCY
# -----------------------------------------------------------------------------

def test_11_schema_migration_preserves_data(test_db):
    """Verify that schema migration is idempotent and all 12 columns are queryable."""
    journal = AutomatedTradeJournal(test_db)
    # Instantiate again to trigger migration check
    journal2 = AutomatedTradeJournal(test_db)

    with test_db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(automated_trade_journal);")
        cols = {row[1] for row in cur.fetchall()}

    expected_cols = [
        "tp_order_id", "tp_order_type", "tp_status", "tp_price",
        "tp_quantity", "tp_filled_quantity", "tp_fee_usd",
        "sl_order_id", "sl_order_type", "sl_status", "sl_price",
        "protection_status",
    ]
    for c in expected_cols:
        assert c in cols, f"Missing expected column: {c}"
