"""Unit and Integration Tests for 3-Tier Dynamic Structural Invalidation SL & Ratchet Engine.

Tests:
1. Dynamic Structural Invalidation calculation for LONG (Sweep wick + zone bottom + L2 bid wall shield).
2. Dynamic Structural Invalidation calculation for SHORT (Sweep wick + zone top + L2 ask wall shield).
3. Minimum Breathing-Room Floor (1.20%): Prevents 0.75% stop; widens to 1.20% (FLOOR_WIDENED_1.20PCT).
4. Maximum Structural Width Cap (> 2.20%): Vetoes overly wide invalidations (STRUCTURAL_INVALIDATION_TOO_WIDE).
5. Dynamic Risk-Constant Position Sizing ($0.20 target risk, quantized to step_size).
6. Hard Risk Cap Veto ($0.22 max): Vetoes when min_qty forces risk > $0.22 (MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL).
7. Live Dynamic SL Ratchet: Stage A initial placement if missing on exchange.
8. Live Dynamic SL Ratchet: Stage B Break-Even at >= +1.0R or 50% Dealing Range midline.
9. Live Dynamic SL Ratchet: Stage C Structural Swing Trail at >= +1.5R with confirmed 15m swings.
10. Strict One-Way Ratchet Invariant: Active SL is NEVER loosened (LONG moves up only; SHORT moves down only).
11. Operator Widened SL Protection: Active SL is never reset to tighter initial Stage A.
12. Atomic replacement and duplicate SL hygiene: new SL created before cancelling old SL orders.
13. Dry-run safety: No exchange order mutations when dry_run=True.
"""

from unittest.mock import MagicMock
import pytest

from src.core.types import Candle, Direction
from src.strategy.sweep_reclaim_15m import (
    DynamicSLResult,
    SweepReclaim15MStrategy,
    compute_dynamic_structural_sl,
)
from src.scheduler.live_micro_runner import LiveMicroCoordinator


def _make_dummy_candles(
    count: int = 20,
    base_price: float = 100.0,
    step: float = 0.5,
    high_offset: float = 1.0,
    low_offset: float = 1.0,
    symbol: str = "ETHUSDT",
    timeframe: str = "15m",
) -> list[Candle]:
    candles = []
    for i in range(count):
        p = base_price + i * step
        candles.append(Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp_ms=1000 * 60 * 15 * i,
            open=p,
            high=p + high_offset,
            low=p - low_offset,
            close=p,
            volume=100.0,
            close_time_ms=1000 * 60 * 15 * (i + 1) - 1,
        ))
    return candles


class TestDynamicStructuralSLCalculation:
    """Tests for compute_dynamic_structural_sl anchor aggregation and guard bands."""

    def test_01_long_dynamic_sl_combines_three_anchors(self):
        """LONG: Anchor 1 (sweep wick), Anchor 2 (zone bottom), Anchor 3 (protecting bid wall)."""
        entry = 100.0
        # Candles with lowest low at 98.70
        candles_15m = _make_dummy_candles(count=10, base_price=100.0, step=0.0, high_offset=0.5, low_offset=0.5)
        candles_15m[-1].low = 98.80
        candles_15m[-2].low = 98.70  # Sweep wick low

        # 1H candles
        candles_1h = _make_dummy_candles(count=5, base_price=100.0, step=0.0, high_offset=0.5, low_offset=0.8, timeframe="1h")

        # Protecting bid wall at 98.60 (1.40% below entry -> within [0.40%, 1.60%])
        bid_wall = 98.60
        zone_bottom = 98.65

        res = compute_dynamic_structural_sl(
            entry_price=entry,
            direction=Direction.LONG,
            candles_15m=candles_15m,
            candles_1h=candles_1h,
            sweep_wick_extreme=98.70,
            structural_zone_extreme=zone_bottom,
            protecting_wall_price=bid_wall,
            atr_15m=0.40,  # 0.25 * atr = 0.10
            tick_size=0.01,
            step_size=0.01,
            min_qty=0.01,
            min_risk_budget_usd=0.20,
        )

        assert res.is_eligible is True
        # Invalidation = min(98.70, 98.65, 98.60) - 0.10 = 98.50
        assert res.raw_invalidation_price == pytest.approx(98.50, abs=1e-4)
        assert res.stop_loss_price == 98.50
        assert res.stop_distance_pct == 1.50  # 1.50%
        assert res.sl_invalidation_type == "L2_WALL_SHIELDED_SL"
        assert res.planned_quantity == pytest.approx(0.13, abs=0.01)  # 0.20 / 1.50 = 0.1333 -> 0.13
        assert res.planned_risk_usd <= 0.22

    def test_02_short_dynamic_sl_combines_three_anchors(self):
        """SHORT: Anchor 1 (sweep wick), Anchor 2 (zone top), Anchor 3 (protecting ask wall)."""
        entry = 100.0
        candles_15m = _make_dummy_candles(count=10, base_price=100.0, step=0.0, high_offset=0.5, low_offset=0.5)
        candles_15m[-1].high = 101.20
        candles_15m[-2].high = 101.30  # Sweep wick high

        ask_wall = 101.40  # 1.40% above entry
        zone_top = 101.35

        res = compute_dynamic_structural_sl(
            entry_price=entry,
            direction=Direction.SHORT,
            candles_15m=candles_15m,
            sweep_wick_extreme=101.30,
            structural_zone_extreme=zone_top,
            protecting_wall_price=ask_wall,
            atr_15m=0.40,  # 0.25 * 0.40 = 0.10
            tick_size=0.01,
            step_size=0.01,
            min_qty=0.01,
            min_risk_budget_usd=0.20,
        )

        assert res.is_eligible is True
        # Invalidation = max(101.30, 101.35, 101.40) + 0.10 = 101.50
        assert res.raw_invalidation_price == pytest.approx(101.50, abs=1e-4)
        assert res.stop_loss_price == 101.50
        assert res.stop_distance_pct == 1.50
        assert res.sl_invalidation_type == "L2_WALL_SHIELDED_SL"
        assert res.planned_risk_usd <= 0.22

    def test_03_minimum_breathing_room_floor_widens_tight_stop(self):
        """When raw stop is tight (e.g. 0.75% like on ZECUSDT), automatically widen to 1.20%."""
        entry = 1000.0
        # Suppose raw invalidation is 1007.50 (0.75% distance for SHORT)
        candles_15m = _make_dummy_candles(count=10, base_price=1000.0, step=0.0, high_offset=2.0, low_offset=2.0)

        res = compute_dynamic_structural_sl(
            entry_price=entry,
            direction=Direction.SHORT,
            candles_15m=candles_15m,
            sweep_wick_extreme=1005.0,  # 0.50%
            atr_15m=10.0,  # 0.25 * 10 = 2.5 -> raw invalidation = 1007.50 (0.75%)
            tick_size=0.10,
            step_size=0.001,
            min_qty=0.001,
            min_risk_budget_usd=0.20,
        )

        assert res.is_eligible is True
        # Raw stop was 0.75%, but floor widens to 1.20% = 1012.0
        assert res.raw_invalidation_price == pytest.approx(1007.50, abs=1e-4)
        assert res.stop_loss_price == pytest.approx(1012.0, abs=0.1)
        assert res.stop_distance_pct >= 1.20
        assert res.sl_invalidation_type == "FLOOR_WIDENED_1.20PCT"

    def test_04_maximum_structural_width_cap_vetoes_wide_stops(self):
        """When raw structural stop exceeds 2.20%, VETO setup (STRUCTURAL_INVALIDATION_TOO_WIDE)."""
        entry = 100.0
        candles_15m = _make_dummy_candles(count=10, base_price=100.0, step=0.0, high_offset=0.5, low_offset=0.5)

        res = compute_dynamic_structural_sl(
            entry_price=entry,
            direction=Direction.LONG,
            candles_15m=candles_15m,
            sweep_wick_extreme=97.0,  # 3.0% below entry
            atr_15m=0.80,
            tick_size=0.01,
            step_size=0.001,
            min_qty=0.001,
        )

        assert res.is_eligible is False
        assert "STRUCTURAL_INVALIDATION_TOO_WIDE" in res.rejection_reason
        assert res.sl_invalidation_type == "STRUCTURAL_INVALIDATION_TOO_WIDE"

    def test_05_hard_risk_cap_veto_when_min_qty_exceeds_budget(self):
        """When min_qty at structural stop distance exceeds $0.22, VETO (MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL)."""
        entry = 100.0
        candles_15m = _make_dummy_candles(count=10, base_price=100.0, step=0.0, high_offset=0.5, low_offset=0.5)

        # Stop distance is 1.50% ($1.50). Min_qty is 0.20. Risk at min_qty = 0.20 * 1.50 = $0.30 > $0.22
        res = compute_dynamic_structural_sl(
            entry_price=entry,
            direction=Direction.LONG,
            candles_15m=candles_15m,
            sweep_wick_extreme=98.60,
            atr_15m=0.40,  # 98.60 - 0.10 = 98.50 -> dist = $1.50
            tick_size=0.01,
            step_size=0.10,
            min_qty=0.20,  # Large exchange min_qty
            min_risk_budget_usd=0.20,
            max_risk_cap_usd=0.22,
        )

        assert res.is_eligible is False
        assert "MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL" in res.rejection_reason
        assert res.sl_invalidation_type == "MIN_QTY_EXCEEDS_RISK_AT_STRUCTURAL_SL"


class TestLiveDynamicSLRatchet:
    """Tests for monitor_and_update_dynamic_sl 3-stage ratchet and invariants."""

    def test_06_stage_a_missing_sl_recovery(self):
        """Stage A: Missing exchange SL order is restored from planned structural stop."""
        mock_exchange = MagicMock()
        mock_exchange.get_positions.return_value = [{
            "symbol": "BTCUSDT",
            "positionAmt": "0.01",
            "entryPrice": "50000.0",
            "markPrice": "50100.0",
        }]
        mock_exchange.get_open_orders.return_value = []  # No SL order on exchange
        mock_exchange.create_order.return_value = {"orderId": "SL_NEW_1"}

        mock_db = MagicMock()
        mock_conn = MagicMock()
        # Journal returns planned_stop_price = 49200.0
        mock_conn.execute.return_value.fetchone.return_value = (
            "JRN_BTC", "TRD_BTC", 49200.0, 50000.0, "SL_NEW_1", 49200.0,
        )
        mock_db.get_connection.return_value.__enter__.return_value = mock_conn

        coord = LiveMicroCoordinator(exchange=mock_exchange, db=mock_db)
        updates = coord.monitor_and_update_dynamic_sl(symbol="BTCUSDT", tick_size=1.0)

        assert len(updates) == 1
        up = updates[0]
        assert up["status"] == "MOVED_TO_STAGE_A_INITIAL_STRUCTURAL"
        assert up["new_sl_price"] == 49200.0
        assert up["ratchet_stage"] == "STAGE_A_INITIAL_STRUCTURAL"
        mock_exchange.create_order.assert_called_once()

    def test_07_stage_b_break_even_at_1r_profit(self):
        """Stage B: Moves SL to break-even (entry +/- 0.08%) when unrealized profit >= +1.0R."""
        mock_exchange = MagicMock()
        # LONG position: Entry 100.0, Mark 102.0. Initial SL 98.50 (Risk = 1.50). Profit = 2.0 >= 1.50 (+1.33R >= +1.0R)
        mock_exchange.get_positions.return_value = [{
            "symbol": "ETHUSDT",
            "positionAmt": "0.10",
            "entryPrice": "100.0",
            "markPrice": "102.0",
        }]
        mock_exchange.get_open_orders.return_value = [{
            "orderId": "SL_OLD",
            "symbol": "ETHUSDT",
            "type": "STOP_MARKET",
            "stopPrice": "98.50",
            "reduceOnly": True,
        }]
        mock_exchange.create_order.return_value = {"orderId": "SL_BE"}

        coord = LiveMicroCoordinator(exchange=mock_exchange)
        updates = coord.monitor_and_update_dynamic_sl(symbol="ETHUSDT", tick_size=0.01)

        assert len(updates) == 1
        up = updates[0]
        assert up["status"] == "MOVED_TO_BREAKEVEN"
        # Entry 100.0 * 1.0008 = 100.08
        assert up["new_sl_price"] == 100.08
        assert up["ratchet_stage"] == "STAGE_B_BREAK_EVEN"
        mock_exchange.create_order.assert_called_once_with(
            symbol="ETHUSDT",
            side="SELL",
            order_type="STOP_MARKET",
            quantity=0.10,
            stop_price=100.08,
            reduce_only=True,
        )
        mock_exchange.cancel_order.assert_called_once_with(symbol="ETHUSDT", order_id="SL_OLD")

    def test_08_stage_c_structural_swing_trail_at_1_5r(self):
        """Stage C: Trails behind confirmed 15m higher-low when unrealized profit >= +1.5R."""
        mock_exchange = MagicMock()
        # LONG position: Entry 100.0, Mark 104.0. Initial SL 98.50 (Risk = 1.50). Profit = 4.0 (+2.67R >= +1.5R)
        mock_exchange.get_positions.return_value = [{
            "symbol": "SOLUSDT",
            "positionAmt": "1.0",
            "entryPrice": "100.0",
            "markPrice": "104.0",
        }]
        # Existing SL is already at BE (100.08)
        mock_exchange.get_open_orders.return_value = [{
            "orderId": "SL_BE_OLD",
            "symbol": "SOLUSDT",
            "type": "STOP_MARKET",
            "stopPrice": "100.08",
            "reduceOnly": True,
        }]
        mock_exchange.create_order.return_value = {"orderId": "SL_TRAIL"}

        # Construct 15m candles with a prominent swing low at 101.50 (formed past entry of 100.0)
        candles_15m = _make_dummy_candles(count=20, base_price=100.0, step=0.2, high_offset=0.5, low_offset=0.2, symbol="SOLUSDT")
        # Prominent swing low around index 10: low = 101.50
        candles_15m[10].low = 101.50
        candles_15m[10].high = 102.50
        for offset in (-3, -2, -1, 1, 2):
            candles_15m[10 + offset].low = 102.0
            candles_15m[10 + offset].high = 103.0
        mock_exchange.get_klines.return_value = candles_15m

        coord = LiveMicroCoordinator(exchange=mock_exchange)
        updates = coord.monitor_and_update_dynamic_sl(symbol="SOLUSDT", tick_size=0.01)

        assert len(updates) == 1
        up = updates[0]
        assert up["status"] == "MOVED_TO_STAGE_C_STRUCTURAL_SWING_TRAIL"
        assert up["ratchet_stage"] == "STAGE_C_STRUCTURAL_SWING_TRAIL"
        # ATR is small (~0.4), so trail stop = 101.50 - 0.15*ATR ~ 101.44 > 100.08
        assert up["new_sl_price"] > 100.08
        assert up["new_sl_price"] >= 101.0

    def test_09_strict_one_way_ratchet_invariant(self):
        """Active SL must NEVER be loosened (LONG SL cannot decrease; SHORT SL cannot increase)."""
        mock_exchange = MagicMock()
        # LONG position: Entry 100.0, Mark 99.50 (negative profit).
        mock_exchange.get_positions.return_value = [{
            "symbol": "ETHUSDT",
            "positionAmt": "0.10",
            "entryPrice": "100.0",
            "markPrice": "99.50",
        }]
        # Active SL at 98.80
        mock_exchange.get_open_orders.return_value = [{
            "orderId": "SL_ACTIVE",
            "symbol": "ETHUSDT",
            "type": "STOP_MARKET",
            "stopPrice": "98.80",
            "reduceOnly": True,
        }]

        coord = LiveMicroCoordinator(exchange=mock_exchange)
        # No updates should occur because price has not reached +1.0R
        updates = coord.monitor_and_update_dynamic_sl(symbol="ETHUSDT", tick_size=0.01)
        assert len(updates) == 0
        mock_exchange.create_order.assert_not_called()
        mock_exchange.cancel_order.assert_not_called()

    def test_10_dry_run_mode_never_sends_exchange_orders(self):
        """In dry_run mode, planned ratchets are computed and returned without modifying exchange."""
        mock_exchange = MagicMock()
        mock_exchange.get_positions.return_value = [{
            "symbol": "ETHUSDT",
            "positionAmt": "0.10",
            "entryPrice": "100.0",
            "markPrice": "102.0",
        }]
        mock_exchange.get_open_orders.return_value = [{
            "orderId": "SL_OLD",
            "symbol": "ETHUSDT",
            "type": "STOP_MARKET",
            "stopPrice": "98.50",
            "reduceOnly": True,
        }]

        coord = LiveMicroCoordinator(exchange=mock_exchange)
        updates = coord.monitor_and_update_dynamic_sl(symbol="ETHUSDT", tick_size=0.01, dry_run=True)

        assert len(updates) == 1
        assert updates[0]["status"] == "DRY_RUN_RATCHET_PLANNED"
        mock_exchange.create_order.assert_not_called()
        mock_exchange.cancel_order.assert_not_called()
    def test_11_short_stage_c_structural_swing_trail(self):
        """SHORT Stage C: Trails behind confirmed 15m lower-high when profit >= +1.5R."""
        mock_exchange = MagicMock()
        # SHORT position: Entry 100.0, Mark 96.0. Initial SL 101.50 (Risk = 1.50). Profit = 4.0 (+2.67R >= +1.5R)
        mock_exchange.get_positions.return_value = [{
            "symbol": "BTCUSDT",
            "positionAmt": "-0.01",
            "entryPrice": "100.0",
            "markPrice": "96.0",
        }]
        # Existing SL is already at BE (99.92)
        mock_exchange.get_open_orders.return_value = [{
            "orderId": "SL_BE_OLD",
            "symbol": "BTCUSDT",
            "type": "STOP_MARKET",
            "stopPrice": "99.92",
            "reduceOnly": True,
        }]
        mock_exchange.create_order.return_value = {"orderId": "SL_TRAIL"}

        # Construct 15m candles with a prominent swing high at 98.50 (formed past entry of 100.0)
        candles_15m = _make_dummy_candles(count=20, base_price=100.0, step=-0.2, high_offset=0.2, low_offset=0.5, symbol="BTCUSDT")
        candles_15m[10].high = 98.50
        candles_15m[10].low = 97.50
        for offset in (-3, -2, -1, 1, 2):
            candles_15m[10 + offset].high = 97.0
            candles_15m[10 + offset].low = 96.0
        mock_exchange.get_klines.return_value = candles_15m

        coord = LiveMicroCoordinator(exchange=mock_exchange)
        updates = coord.monitor_and_update_dynamic_sl(symbol="BTCUSDT", tick_size=0.01)

        assert len(updates) == 1
        up = updates[0]
        assert up["status"] == "MOVED_TO_STAGE_C_STRUCTURAL_SWING_TRAIL"
        assert up["ratchet_stage"] == "STAGE_C_STRUCTURAL_SWING_TRAIL"
        # Trail SL must be lower than BE (99.92) and near 98.50 + 0.15*ATR
        assert up["new_sl_price"] < 99.92
        assert up["new_sl_price"] <= 99.0

    def test_12_quantization_floor_for_long_and_ceil_for_short(self):
        """Tick size quantization: FLOOR for LONG SL (never round up into entry), CEIL for SHORT SL (never round down into entry)."""
        candles_15m = _make_dummy_candles(count=10, base_price=100.0, step=0.0, high_offset=0.5, low_offset=0.5)

        # LONG: raw SL = 98.525. With tick_size=0.10, FLOOR must give 98.50 (not 98.60)
        res_long = compute_dynamic_structural_sl(
            entry_price=100.0,
            direction=Direction.LONG,
            candles_15m=candles_15m,
            sweep_wick_extreme=98.60,
            atr_15m=0.30,  # 98.60 - 0.075 = 98.525
            tick_size=0.10,
        )
        assert res_long.stop_loss_price == 98.50

        # SHORT: raw SL = 101.475. With tick_size=0.10, CEIL must give 101.50 (not 101.40)
        res_short = compute_dynamic_structural_sl(
            entry_price=100.0,
            direction=Direction.SHORT,
            candles_15m=candles_15m,
            sweep_wick_extreme=101.40,
            atr_15m=0.30,  # 101.40 + 0.075 = 101.475
            tick_size=0.10,
        )
        assert res_short.stop_loss_price == 101.50

    def test_13_groq_autotrader_guardrails_prevent_tight_and_wide_stops(self):
        """Stage 3 / LLM sanitation enforces 1.20% floor and vetoes >2.20% width and >$0.22 risk."""
        from src.ai.groq_autotrader import GroqAutoTrader, ExecutionVenue
        from src.exchange.metadata import SymbolSpecs

        autotrader = GroqAutoTrader(api_key="mock_key")
        specs = SymbolSpecs(
            symbol="ETHUSDT",
            base_asset="ETH",
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            tick_size=0.01,
            price_precision=2,
            step_size=0.001,
            quantity_precision=3,
            min_qty=0.001,
            max_qty=1000000.0,
            min_notional=5.0,
            maint_margin_percent=0.02,
        )

        # Tight stop proposal: 0.75% stop on LONG (entry 1000, sl 992.5)
        raw_setup_tight = {
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "entry_price": 1000.0,
            "stop_loss_price": 992.5,
            "take_profit_price": 1025.0,
        }
        res_tight = autotrader.apply_mathematical_guardrails(
            raw_setup=raw_setup_tight,
            specs=specs,
            risk_preset="MICRO_CONSERVATIVE",
            execution_venue=ExecutionVenue.LIVE,
            target_risk_cap=0.20,
        )
        # Should be widened to 1.20% floor (sl = 988.0)
        assert res_tight.stop_loss_price == 988.0
        assert res_tight.sl_invalidation_type == "FLOOR_WIDENED_1.20PCT"

        # Overly wide stop proposal: 3.0% stop on LONG (entry 1000, sl 970)
        raw_setup_wide = {
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "entry_price": 1000.0,
            "stop_loss_price": 970.0,
            "take_profit_price": 1060.0,
        }
        res_wide = autotrader.apply_mathematical_guardrails(
            raw_setup=raw_setup_wide,
            specs=specs,
            risk_preset="MICRO_CONSERVATIVE",
            execution_venue=ExecutionVenue.LIVE,
            target_risk_cap=0.20,
        )
        assert res_wide.status == "REJECTED"
        assert res_wide.veto_code == "STRUCTURAL_INVALIDATION_TOO_WIDE"
