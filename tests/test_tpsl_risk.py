"""Unit tests for Risk Management, Loss Limits, and Position Sizing ($16 USDT Account).
"""

import pytest
from src.core.types import Direction, TradeRecord
from src.exchange.metadata import SymbolSpecs
from src.risk.manager import RiskManager
from src.risk.sizing import PositionSizer


BTC_SPECS = SymbolSpecs(
    symbol="BTCUSDT",
    base_asset="BTC",
    quote_asset="USDT",
    contract_type="PERPETUAL",
    status="TRADING",
    tick_size=0.1,
    price_precision=1,
    step_size=0.001,
    quantity_precision=3,
    min_qty=0.001,
    max_qty=1000.0,
    min_notional=5.0,
    maint_margin_percent=2.5,
)

ADA_SPECS = SymbolSpecs(
    symbol="ADAUSDT",
    base_asset="ADA",
    quote_asset="USDT",
    contract_type="PERPETUAL",
    status="TRADING",
    tick_size=0.0001,
    price_precision=4,
    step_size=1.0,
    quantity_precision=0,
    min_qty=1.0,
    max_qty=1000000.0,
    min_notional=5.0,
    maint_margin_percent=5.0,
)


BNB_SPECS = SymbolSpecs(
    symbol="BNBUSDT",
    base_asset="BNB",
    quote_asset="USDT",
    contract_type="PERPETUAL",
    status="TRADING",
    tick_size=0.01,
    price_precision=2,
    step_size=0.01,
    quantity_precision=2,
    min_qty=0.01,
    max_qty=10000.0,
    min_notional=5.0,
    maint_margin_percent=1.0,
)


class TestPositionSizer:
    """Unit tests for Target Isolated Margin ($5.0-$5.5) sizing with 10x max leverage."""

    def test_target_margin_5_0_and_5_5(self):
        # ADA at $0.50, SL $0.49 (stop dist = 0.01, 2% -> loss = $1.00 < $1.50)
        # 1. Target margin $5.00 -> notional = $50.00 -> qty = 100 ADA
        res_5 = PositionSizer.calculate_sizing(
            symbol_specs=ADA_SPECS,
            entry_price=0.50,
            stop_loss=0.49,
            target_isolated_margin=5.0,
            available_equity_usdt=16.0,
            max_leverage=10,
        )
        assert res_5.is_feasible is True
        assert res_5.target_isolated_margin_usdt == 5.0
        assert res_5.quantity == 100.0
        assert res_5.position_notional_usdt == 50.0
        assert res_5.required_margin_usdt == 5.0
        assert res_5.effective_leverage == 3.125  # 50 / 16
        assert res_5.actual_margin_usdt == 5.0

        # 2. Target margin $5.50 -> notional = $55.00 -> qty = 110 ADA
        res_55 = PositionSizer.calculate_sizing(
            symbol_specs=ADA_SPECS,
            entry_price=0.50,
            stop_loss=0.49,
            target_isolated_margin=5.5,
            available_equity_usdt=16.0,
            max_leverage=10,
        )
        assert res_55.is_feasible is True
        assert res_55.target_isolated_margin_usdt == 5.5
        assert res_55.quantity == 110.0
        assert res_55.position_notional_usdt == 55.0
        assert res_55.required_margin_usdt == 5.5

    def test_max_leverage_capped_at_10(self):
        # If caller requests 20x leverage, PositionSizer caps it at 10x
        res = PositionSizer.calculate_sizing(
            symbol_specs=ADA_SPECS,
            entry_price=0.50,
            stop_loss=0.49,
            target_isolated_margin=5.0,
            available_equity_usdt=16.0,
            max_leverage=20,  # exceeds 10
        )
        assert res.is_feasible is True
        # Max leverage is capped at 10x: notional = 5.0 * 10 = 50.0 (not 100.0)
        assert res.position_notional_usdt == 50.0
        assert res.required_margin_usdt == 5.0

    def test_quantity_rounding_floors_to_step_size(self):
        # Entry = 3.33, SL = 3.25, Target notional = 50.0 -> raw_qty = 50 / 3.33 = 15.015015...
        # Without range stepping (target_isolated_margin_max=5.0) -> strictly rounded down = 15.0
        res = PositionSizer.calculate_sizing(
            symbol_specs=ADA_SPECS,
            entry_price=3.33,
            stop_loss=3.25,
            target_isolated_margin=5.0,
            target_isolated_margin_max=5.0,
            available_equity_usdt=16.0,
            max_leverage=10,
        )
        assert res.is_feasible is True
        assert res.quantity == 15.0
        assert res.position_notional_usdt == 15.0 * 3.33

        # With range stepping (target_isolated_margin_max=5.50) -> steps up to 16.0 (margin = $5.328 <= $5.50)
        res_range = PositionSizer.calculate_sizing(
            symbol_specs=ADA_SPECS,
            entry_price=3.33,
            stop_loss=3.25,
            target_isolated_margin=5.0,
            target_isolated_margin_max=5.50,
            available_equity_usdt=16.0,
            max_leverage=10,
        )
        assert res_range.is_feasible is True
        assert res_range.quantity == 16.0
        assert res_range.actual_margin_usdt == pytest.approx(5.328, rel=1e-3)

    def test_minimum_quantity_clamping(self):
        # Symbol with high price where raw_qty < min_qty, but clamped notional stays <= max margin ($5.50)
        # BTC at $52,000: raw_qty = 50 / 52000 = 0.0009615 -> below min_qty 0.001
        # Clamped to min_qty = 0.001 -> notional = $52.00 -> margin = $5.20 <= $5.50
        res = PositionSizer.calculate_sizing(
            symbol_specs=BTC_SPECS,
            entry_price=52000.0,
            stop_loss=51800.0,  # tight stop distance = 200 (loss = 0.001 * 200 = 0.20)
            target_isolated_margin=5.0,
            target_isolated_margin_max=5.50,
            available_equity_usdt=16.0,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
        )
        assert res.quantity == 0.001
        assert res.position_notional_usdt == 52.0
        assert res.actual_margin_usdt == 5.20
        assert res.is_feasible is True

    def test_minimum_notional_rejection(self):
        # High min_notional constraint (e.g. $100 min notional when position notional is $50)
        specs_high_min = SymbolSpecs(
            symbol="TESTUSDT",
            base_asset="TEST",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.01,
            price_precision=2,
            step_size=1.0,
            quantity_precision=0,
            min_qty=1.0,
            max_qty=1000.0,
            min_notional=100.0,  # requires $100 min
            maint_margin_percent=5.0,
        )
        res = PositionSizer.calculate_sizing(
            symbol_specs=specs_high_min,
            entry_price=10.0,
            stop_loss=9.5,
            target_isolated_margin=5.0,  # gives 50.0 notional
            available_equity_usdt=16.0,
            max_leverage=10,
        )
        assert res.is_feasible is False
        assert "below exchange minimum notional" in res.infeasible_reason

    def test_estimated_loss_at_sl_includes_fees_and_slippage(self):
        # Entry = 100.0, SL = 95.0, stop distance = 5.0 (5%)
        # Target margin $5.0 at 10x -> Notional = $50.0, Qty = 0.5
        # Nominal price loss = 0.5 * 5.0 = $2.50
        # Entry fee = 50.0 * 0.0005 = 0.025
        # Exit fee = (0.5 * 95.0) * 0.0005 = 0.02375
        # Total fee = 0.04875
        # Slippage = 0.04875
        # Total loss = 2.50 + 0.04875 + 0.04875 = $2.5975
        # Loss % equity ($16) = (2.5975 / 16.0) * 100 = 16.234375%
        res = PositionSizer.calculate_sizing(
            symbol_specs=BTC_SPECS,
            entry_price=100.0,
            stop_loss=95.0,
            target_isolated_margin=5.0,
            available_equity_usdt=16.0,
            max_leverage=10,
            fee_rate=0.0005,
            slippage_rate=0.0005,
            hard_risk_limit_usdt=5.0,  # allow up to $5
        )
        assert res.is_feasible is True
        assert abs(res.estimated_loss_usdt - 2.5975) < 1e-4
        assert abs(res.estimated_loss_pct - 16.234375) < 1e-4
        assert abs(res.estimated_fee_usdt - 0.04875) < 1e-4
        assert abs(res.estimated_slippage_usdt - 0.04875) < 1e-4

    def test_hard_risk_limit_violation_triggers_infeasible(self):
        # ADA at 0.50, SL 0.45 (distance = 0.05, 10%)
        # Target margin $5.0 at 10x -> Notional $50.0, Qty = 100 ADA
        # Margin = $5.00 (within margin target $5.0-$5.50)
        # Nominal loss = 100 * 0.05 = $5.00 > hard_risk_limit $1.50 -> INFEASIBLE!
        res = PositionSizer.calculate_sizing(
            symbol_specs=ADA_SPECS,
            entry_price=0.50,
            stop_loss=0.45,
            target_isolated_margin=5.0,
            target_isolated_margin_max=5.50,
            available_equity_usdt=16.0,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
        )
        assert res.is_feasible is False
        assert "exceeds hard risk limit" in res.infeasible_reason

    def test_bnb_sizing_example(self):
        # Live audit BNB scenario:
        # Entry: 736.29, SL: 749.0954 (SHORT)
        # Target isolated margin: $5.0 - $5.5 with 10x leverage
        res = PositionSizer.calculate_sizing(
            symbol_specs=BNB_SPECS,
            entry_price=736.29,
            stop_loss=749.0954,
            target_isolated_margin=5.0,
            target_isolated_margin_max=5.5,
            available_equity_usdt=16.0,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
        )
        # Verify feasibility
        assert res.is_feasible is True
        assert res.infeasible_reason is None

        # Verify margin is within $5.00 - $5.50 window
        assert 4.40 <= res.actual_margin_usdt <= 5.50
        assert res.effective_leverage > 0

        # Verify notional and quantity
        assert res.position_notional_usdt >= 5.0  # Above min_notional
        assert res.quantity in (0.06, 0.07)

        # Verify loss estimates
        assert 0.80 <= res.estimated_loss_usdt <= 1.20
        assert 5.0 <= res.estimated_loss_pct <= 7.5
        assert res.estimated_loss_usdt <= 1.50  # Within hard risk limit

    def test_finding_01_btc_min_lot_margin_overshoot_infeasible(self):
        """FINDING-01: BTC with entry $82,293.20 and min_qty 0.001 BTC requires ~$8.23 margin.
        Since $8.23 > $5.50 (target_isolated_margin_max_usdt), candidate must be INFEASIBLE.
        """
        res = PositionSizer.calculate_sizing(
            symbol_specs=BTC_SPECS,
            entry_price=82293.20,
            stop_loss=81500.0,
            target_isolated_margin=5.0,
            target_isolated_margin_max=5.50,
            available_equity_usdt=16.0,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
        )
        assert res.is_feasible is False
        assert res.quantity == 0.001
        assert res.position_notional_usdt == pytest.approx(82.2932, rel=1e-4)
        assert res.actual_margin_usdt == pytest.approx(8.22932, rel=1e-4)
        assert res.actual_margin_usdt > 5.50
        assert "exceeds maximum target margin limit ($5.50)" in res.infeasible_reason

    def test_finding_02_leverage_and_equity_exposure_separation(self):
        """FINDING-02: position_leverage = actual_notional / actual_margin (~10x)
        separated from equity_exposure_ratio = actual_notional / available_equity (~3.22x).
        """
        res = PositionSizer.calculate_sizing(
            symbol_specs=BNB_SPECS,
            entry_price=736.29,
            stop_loss=749.0954,
            target_isolated_margin=5.0,
            target_isolated_margin_max=5.50,
            available_equity_usdt=16.0,
            max_leverage=10,
            hard_risk_limit_usdt=1.50,
        )
        assert res.is_feasible is True
        assert res.position_leverage == pytest.approx(10.0, rel=1e-3)
        assert res.leverage == pytest.approx(10.0, rel=1e-3)
        assert res.equity_exposure_ratio == pytest.approx(res.position_notional_usdt / 16.0, rel=1e-3)
        assert res.effective_leverage == pytest.approx(res.position_notional_usdt / 16.0, rel=1e-3)
        # Position leverage (10x) must NOT be equal to equity exposure ratio (~3.22x)
        assert res.position_leverage != res.equity_exposure_ratio

    def test_finding_05_non_linear_entry_exit_fees(self):
        """FINDING-05: Non-linear entry/exit costs:
        Entry fee/slippage calculated on entry price,
        Exit fee/slippage calculated on stop-loss price.
        """
        # LONG: entry = 100, SL = 90 (exit price is lower than entry)
        res_long = PositionSizer.calculate_sizing(
            symbol_specs=BTC_SPECS,
            entry_price=100.0,
            stop_loss=90.0,
            target_isolated_margin=5.0,
            available_equity_usdt=16.0,
            max_leverage=10,
            fee_rate=0.001,
            slippage_rate=0.001,
            hard_risk_limit_usdt=10.0,
            direction=Direction.LONG,
        )
        # Qty = 0.5. Entry notional = 50.0. Exit notional = 45.0.
        # Entry fee = 50.0 * 0.001 = 0.05
        # Exit fee = 45.0 * 0.001 = 0.045
        # Total fee = 0.095
        assert res_long.estimated_fee_usdt == pytest.approx(0.095, rel=1e-4)
        assert res_long.estimated_slippage_usdt == pytest.approx(0.095, rel=1e-4)

        # SHORT: entry = 100, SL = 110 (exit price is higher than entry)
        res_short = PositionSizer.calculate_sizing(
            symbol_specs=BTC_SPECS,
            entry_price=100.0,
            stop_loss=110.0,
            target_isolated_margin=5.0,
            available_equity_usdt=16.0,
            max_leverage=10,
            fee_rate=0.001,
            slippage_rate=0.001,
            hard_risk_limit_usdt=10.0,
            direction=Direction.SHORT,
        )
        # Qty = 0.5. Entry notional = 50.0. Exit notional = 55.0.
        # Entry fee = 50.0 * 0.001 = 0.05
        # Exit fee = 55.0 * 0.001 = 0.055
        # Total fee = 0.105
        assert res_short.estimated_fee_usdt == pytest.approx(0.105, rel=1e-4)
        assert res_short.estimated_slippage_usdt == pytest.approx(0.105, rel=1e-4)

    def test_finding_03_journal_fallback_risk_assessment_reconstruction(self):
        """FINDING-03: Ensure risk_assessment dictionary from journal/database
        is safely deserialized without fabricating missing values.
        """
        from src.core.types import RiskAssessment
        raw_dict = {
            "symbol": "BNBUSDT",
            "entry_price": 736.29,
            "stop_loss": 749.0954,
            "stop_distance_pct": 0.01739,
            "risk_budget_usdt": 0.0,
            "position_notional_usdt": 51.5403,
            "quantity": 0.07,
            "required_margin_usdt": 5.15403,
            "effective_leverage": 3.22,
            "estimated_fee_usdt": 0.046,
            "estimated_slippage_usdt": 0.046,
            "is_feasible": True,
            "infeasible_reason": None,
            "target_isolated_margin_usdt": 5.0,
            "estimated_loss_usdt": 0.988,
            "estimated_loss_pct": 6.175,
            "position_leverage": 10.0,
            "equity_exposure_ratio": 3.22,
        }
        ra = RiskAssessment(**raw_dict)
        assert ra.is_feasible is True
        assert ra.position_leverage == 10.0
        assert ra.leverage == 10.0
        assert ra.equity_exposure_ratio == 3.22
        assert ra.actual_margin_usdt == pytest.approx(5.15403, rel=1e-4)


class TestRiskManager:
    def test_max_one_open_position(self):
        rm = RiskManager(initial_equity_usdt=16.0, max_open_positions=1)
        can_open, _ = rm.can_open_new_position(1700000000000)
        assert can_open is True

        # Open 1 trade
        trade = TradeRecord(
            trade_id="t1",
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1700000000000,
            entry_price=0.50,
            stop_loss=0.49,
            take_profit=0.53,
            quantity=10.0,
            notional_usdt=5.0,
        )
        rm.record_entry(trade)

        # Attempt to open 2nd trade
        can_open_2, reason = rm.can_open_new_position(1700000000000)
        assert can_open_2 is False
        assert "Maximum open position limit" in reason

    def test_weekly_entries_unlimited_by_count_when_risk_permits(self):
        rm = RiskManager(initial_equity_usdt=16.0)
        base_ts = 1700000000000

        for i in range(5):
            can_open, reason = rm.can_open_new_position(base_ts + i * 3600000)
            assert can_open is True, f"Entry {i+1} should be permitted but got: {reason}"
            t = TradeRecord(
                trade_id=f"t{i}",
                symbol="ADAUSDT",
                direction=Direction.LONG,
                entry_timestamp_ms=base_ts + i * 3600000,
                entry_price=0.50,
                stop_loss=0.49,
                take_profit=0.53,
                quantity=10.0,
                notional_usdt=5.0,
            )
            rm.record_entry(t)
            # Close trade with profit so position count is 0 and loss limits untouched
            t.exit_timestamp_ms = base_ts + i * 3600000 + 1800000
            t.pnl_net = 0.05
            rm.record_exit(t)

        # 5 entries completed, count tracks 5, and 6th trade is still permitted
        assert rm.weekly_entries_count == 5
        can_open_6, reason_6 = rm.can_open_new_position(base_ts + 6 * 3600000)
        assert can_open_6 is True
        assert reason_6 is None

    def test_daily_loss_limit_blocks_entry(self):
        rm = RiskManager(initial_equity_usdt=16.0, daily_loss_limit_usdt=0.18)
        # 2023-11-15 12:00:00 UTC
        ts = 1700049600000

        # Simulate losing trade with $0.19 loss
        t = TradeRecord(
            trade_id="loss_1",
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=ts,
            entry_price=0.50,
            stop_loss=0.48,
            take_profit=0.54,
            quantity=10.0,
            notional_usdt=5.0,
            exit_timestamp_ms=ts + 3600000,
            pnl_net=-0.19,
        )
        rm.record_entry(t)
        rm.record_exit(t)

        # Now daily loss limit is breached within same day
        can_open, reason = rm.can_open_new_position(ts + 7200000)
        assert can_open is False
        assert "Daily loss limit" in reason
