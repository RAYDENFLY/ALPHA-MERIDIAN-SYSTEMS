"""Tests for Exchange Price Precision, Tick-Size Normalization, and Journal UI.

Verifies:
1. Deterministic price normalization to exchange tick size (SOLUSDT, ETHUSDT, BTCUSDT).
2. Directional rounding preserving strategy intent (LONG: SL floored, TP nearest; SHORT: SL ceiled, TP nearest).
3. Strategy rejection when normalized prices violate bounds or Net-RR gate (< 1.50).
4. Automated trade journal reconciliation prevents repeated submission loops on REJECTED TP.
5. SymbolSpecs dynamic tick-size precision compliance.
"""

import pytest
from decimal import Decimal

from src.risk.live_micro_sizing import (
    LiveMicroSizingEngine,
    LiveMicroSizingResult,
    normalize_price_to_tick,
)
from src.exchange.metadata import SymbolSpecs
from src.core.types import Candle, Direction
from src.strategy.ema20_reclaim_5m import EMA20ReclaimStrategy, EMA20ReclaimSetup
from src.journal.automated_journal import AutomatedTradeJournal
from src.journal.models import AutomatedTradeRecord, ReconciliationStatus
from src.data.database import DatabaseManager


# =============================================================================
# 1. Price Normalization & Tick-Size Conformance Tests
# =============================================================================

class TestPriceNormalizationToTick:
    """Tests for normalize_price_to_tick function."""

    def test_sol_tp_normalization_resolves_error_4014(self):
        """SOLUSDT rejected 103.1575 with code -4014; tick 0.01 must produce 103.16."""
        raw_tp = 103.1575
        sol_tick = 0.01
        normalized = normalize_price_to_tick(raw_tp, sol_tick, rounding="NEAREST")
        assert normalized == 103.16
        # Verify it has no extra decimal digits
        assert str(normalized) == "103.16"

    def test_eth_and_btc_tick_normalization(self):
        """ETHUSDT (tick 0.01) and BTCUSDT (tick 0.1) conformance."""
        # ETH
        eth_tp = 2378.3375
        assert normalize_price_to_tick(eth_tp, 0.01, rounding="NEAREST") == 2378.34

        # BTC
        btc_entry = 67450.28
        assert normalize_price_to_tick(btc_entry, 0.1, rounding="NEAREST") == 67450.3

    def test_directional_rounding_long(self):
        """LONG: SL must be floored to prevent tightening towards entry."""
        raw_sl = 100.598
        tick = 0.01
        floored_sl = normalize_price_to_tick(raw_sl, tick, rounding="FLOOR")
        assert floored_sl == 100.59
        assert floored_sl <= raw_sl

    def test_directional_rounding_short(self):
        """SHORT: SL must be ceiled to prevent tightening towards entry."""
        raw_sl = 100.591
        tick = 0.01
        ceiled_sl = normalize_price_to_tick(raw_sl, tick, rounding="CEIL")
        assert ceiled_sl == 100.60
        assert ceiled_sl >= raw_sl

    def test_invalid_price_or_tick_raises_value_error(self):
        """Negative or zero prices/ticks must raise ValueError."""
        with pytest.raises(ValueError, match="Price must be positive"):
            normalize_price_to_tick(-10.0, 0.01)

        with pytest.raises(ValueError, match="Price must be positive"):
            normalize_price_to_tick(0.0, 0.01)

        with pytest.raises(ValueError, match="Tick size must be positive"):
            normalize_price_to_tick(100.0, 0.0)

        with pytest.raises(ValueError, match="Tick size must be positive"):
            normalize_price_to_tick(100.0, -0.01)


# =============================================================================
# 2. SymbolSpecs Conformance Tests
# =============================================================================

class TestSymbolSpecsRoundPrice:
    """Tests for SymbolSpecs.round_price with dynamic tick size."""

    def test_symbol_specs_sol_round_price(self):
        specs = SymbolSpecs(
            symbol="SOLUSDT",
            base_asset="SOL",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.01,
            price_precision=4,
            step_size=0.01,
            quantity_precision=2,
            min_qty=0.01,
            max_qty=1000000.0,
            min_notional=5.0,
            maint_margin_percent=0.01,
        )
        assert specs.round_price(103.1575) == 103.16
        assert specs.round_price(100.598, rounding="FLOOR") == 100.59
        assert specs.round_price(100.591, rounding="CEIL") == 100.60

    def test_symbol_specs_subdollar_precision(self):
        """Coins like DOGE with 0.0001 tick size."""
        specs = SymbolSpecs(
            symbol="DOGEUSDT",
            base_asset="DOGE",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.0001,
            price_precision=5,
            step_size=1.0,
            quantity_precision=0,
            min_qty=1.0,
            max_qty=1000000.0,
            min_notional=5.0,
            maint_margin_percent=0.01,
        )
        assert specs.round_price(0.14328) == 0.1433
        assert specs.round_price(0.14328, rounding="FLOOR") == 0.1432


# =============================================================================
# 3. Strategy Tick-Size Normalization & Sizing Gate Tests
# =============================================================================

class TestStrategyTickNormalization:
    """Tests that EMA20_RECLAIM_5M_V1 enforces tick-size normalization and Net-RR."""

    def _create_candles(self, base_price: float, count: int = 30) -> list:
        candles = []
        for i in range(count):
            c = Candle(
                symbol="SOLUSDT",
                timeframe="5m",
                timestamp_ms=(1700000000 + i * 300) * 1000,
                open=base_price + (i * 0.05),
                high=base_price + (i * 0.05) + 0.1,
                low=base_price + (i * 0.05) - 0.1,
                close=base_price + (i * 0.05),
                volume=1000.0,
                close_time_ms=(1700000000 + (i + 1) * 300) * 1000,
            )
            candles.append(c)
        return candles

    def test_strategy_produces_normalized_tp_and_sl(self):
        strat = EMA20ReclaimStrategy()
        # Create candles where last candle reclaims EMA20
        # Price around 100.00 with SOL tick 0.01
        candles = self._create_candles(base_price=100.0, count=30)
        # Force a pullback on candle -2 (low dips below EMA)
        candles[-2].low = 99.40
        candles[-2].close = 99.45
        # Reclaim candle -1 closes above EMA
        candles[-1].open = 99.50
        candles[-1].close = 100.00
        candles[-1].high = 100.05
        candles[-1].low = 99.48

        setup, reason = strat.evaluate(
            symbol="SOLUSDT",
            candles_5m=candles,
            tick_size=0.01,
            step_size=0.01,
            min_notional=5.0,
            account_equity=16.0,
        )

        if setup is not None:
            # Entry, SL, and TP must be exact multiples of tick_size
            assert (setup.entry_price * 100) % 1 == 0
            assert (setup.stop_loss * 100) % 1 == 0
            assert (setup.take_profit * 100) % 1 == 0
            # LONG invariants
            assert setup.stop_loss < setup.entry_price < setup.take_profit
            assert setup.sizing.expected_net_rr >= 1.50

    def test_rejection_when_net_rr_below_gate(self):
        """When Gross RR is compressed, Net-RR drops below 1.50 and candidate is rejected."""
        sizing_engine = LiveMicroSizingEngine()
        # Sizing with very small TP distance that fails 1.50 Net-RR gate
        result = sizing_engine.size_position(
            entry_price=100.0,
            stop_loss=99.50,  # 0.50% stop
            take_profit=100.55,  # 1.10 Gross RR -> Expected Net-RR < 1.50
            direction="LONG",
            step_size=0.01,
            min_notional=5.0,
            account_equity=16.0,
        )
        assert not result.is_feasible
        assert "NET_RR_BELOW_GATE" in result.rejection_reason


# =============================================================================
# 4. Reconciliation Repeated Submission Prevention Tests
# =============================================================================

class TestReconciliationNoRepeatedRetryOnRejectedTP:
    """Verify that reconciliation does NOT retry rejected TP in an infinite loop."""

    def test_reconciliation_skips_rejected_tp_submission(self, tmp_path):
        db_file = tmp_path / "test_recon.db"
        db = DatabaseManager(db_file)
        db.init_db()
        journal = AutomatedTradeJournal(db=db)

        # Create a trade record with tp_status = "REJECTED"
        trade = AutomatedTradeRecord(
            journal_id="JRN_TEST_SOL_001",
            trade_id="TRD_TEST_SOL_001",
            exchange="Aster DEX",
            symbol="SOLUSDT",
            strategy_id="EMA20_RECLAIM_5M_V1",
            config_version="LIVE_MICRO_V1",
            signal_id="SIG_TEST_001",
            order_id="ORD_1001",
            client_order_id="CLO_1001",
            mode="LIVE",
            side="LONG",
            status="OPEN",
            planned_entry_price=101.38,
            planned_take_profit_price=103.1575,
            planned_stop_price=100.59,
            tp_status="REJECTED",
            sl_status="PLACED",
            protection_status="PARTIALLY_PROTECTED",
            filled_quantity=0.19,
            average_entry_price=101.38,
            notes="Previous TP rejected by exchange: HTTP 400 - Price not increased by tick size",
        )
        journal.record_trade_intent(trade)

        # Mock adapter tracking calls
        class MockExchangeAdapter:
            def __init__(self):
                self.create_order_calls = 0

            def create_order(self, **kwargs):
                self.create_order_calls += 1
                return {"orderId": "NEW_TP_001"}

            def get_open_orders(self, symbol=None):
                return []

        mock_adapter = MockExchangeAdapter()

        # Run reconciliation with allow_tp_submission=True
        summary = journal.reconcile_with_exchange(
            exchange_trades=[],
            exchange_positions=[{"symbol": "SOLUSDT", "positionAmt": "0.19"}],
            exchange_orders=[],
            exchange_adapter=mock_adapter,
            allow_tp_submission=True,
        )

        # Must NOT call create_order for a trade whose tp_status is REJECTED
        assert mock_adapter.create_order_calls == 0

        # Reload trade from db
        updated_trade = journal.get_trade_by_id("TRD_TEST_SOL_001")
        assert updated_trade.tp_status == "REJECTED"
        assert updated_trade.tp_order_id is None
