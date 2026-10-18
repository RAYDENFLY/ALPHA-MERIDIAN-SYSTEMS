"""Unit tests for AsterDEX PositionAnalyzer (Analyze Entry Engine)."""

import pytest
from src.core.types import Candle
from src.intelligence.position_analyzer import (
    ActionRecommendation,
    LiquidationThreat,
    PositionAnalyzer,
    ProtectionStatus,
    ThesisStatus,
)


def make_candle(idx: int, open_: float, high: float, low: float, close: float, tf: str = "1h", sym: str = "BTCUSDT") -> Candle:
    ts = 1700000000000 + idx * 3600000
    return Candle(
        symbol=sym,
        timeframe=tf,
        timestamp_ms=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        close_time_ms=ts + 3599999,
        quote_volume=100.0 * close,
    )


def build_bullish_candles(n: int = 40) -> list[Candle]:
    candles = []
    price = 80000.0
    pattern = [200.0, 300.0, 250.0, -150.0, -100.0, 200.0, 350.0, 200.0, -180.0, -120.0]
    for i in range(n):
        delta = pattern[i % len(pattern)]
        o = price
        c = price + delta
        h = max(o, c) + 80.0
        l = min(o, c) - 80.0
        candles.append(make_candle(i, o, h, l, c))
        price = c
    return candles


def build_bearish_candles(n: int = 40) -> list[Candle]:
    candles = []
    price = 85000.0
    pattern = [-200.0, -300.0, -250.0, 150.0, 100.0, -200.0, -350.0, -200.0, 180.0, 120.0]
    for i in range(n):
        delta = pattern[i % len(pattern)]
        o = price
        c = price + delta
        h = max(o, c) + 80.0
        l = min(o, c) - 80.0
        candles.append(make_candle(i, o, h, l, c))
        price = c
    return candles


class TestPositionAnalyzer:
    """Test suite for position thesis auditing, risk modeling, and recommendations."""

    def test_insufficient_data_handling(self):
        pos = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 0.1,
            "entry_price": 80000.0,
            "mark_price": 80500.0,
            "stop_loss_price": 79000.0,
            "take_profit_price": 83000.0,
        }
        res = PositionAnalyzer.analyze_position(pos, candles_4h=[], candles_1h=[], candles_15m=[])
        assert res.thesis_status == ThesisStatus.INSUFFICIENT_DATA
        assert "Insufficient candle data" in res.thesis_summary
        assert res.risk.has_stop_loss is True

    def test_long_position_valid_thesis(self):
        c_4h = build_bullish_candles(25)
        c_1h = build_bullish_candles(25)
        c_15m = build_bullish_candles(25)

        last_close = c_1h[-1].close
        pos = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 0.5,
            "entry_price": last_close - 500.0,
            "mark_price": last_close,
            "stop_loss_price": last_close - 1500.0,
            "take_profit_price": last_close + 2500.0,
            "liquidation_price": last_close - 20000.0,
            "allocated_margin_usdt": 2000.0,
        }
        res = PositionAnalyzer.analyze_position(
            pos,
            candles_4h=c_4h,
            candles_1h=c_1h,
            candles_15m=c_15m,
            account_equity=10000.0,
        )

        assert res.thesis_status == ThesisStatus.VALID
        assert res.risk.has_stop_loss is True
        assert res.risk.has_take_profit is True
        assert res.risk.protection_status == ProtectionStatus.FULLY_PROTECTED
        assert res.risk.liquidation_threat == LiquidationThreat.SAFE
        assert len(res.conditional_triggers) > 0
        assert len(res.invalidation_conditions) > 0

    def test_long_position_invalidated_when_structure_breaks(self):
        c_4h = build_bearish_candles(25)
        c_1h = build_bearish_candles(25)
        c_15m = build_bearish_candles(25)

        last_close = c_1h[-1].close
        pos = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 0.2,
            "entry_price": 85000.0,
            "mark_price": last_close,
            "stop_loss_price": 75000.0,
            "take_profit_price": 90000.0,
        }
        res = PositionAnalyzer.analyze_position(
            pos,
            candles_4h=c_4h,
            candles_1h=c_1h,
            candles_15m=c_15m,
        )

        assert res.thesis_status == ThesisStatus.INVALIDATED
        assert res.action == ActionRecommendation.CLOSE
        assert "CLOSE RECOMMENDATION" in res.action_summary

    def test_unprotected_position_flags_urgent_adjustment(self):
        c_1h = build_bullish_candles(20)
        pos = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 1.0,
            "entry_price": 80000.0,
            "mark_price": 80500.0,
            "stop_loss_price": None,  # Missing SL
            "take_profit_price": None,
            "liquidation_price": 75000.0,
        }
        res = PositionAnalyzer.analyze_position(pos, candles_1h=c_1h)

        assert res.risk.has_stop_loss is False
        assert res.risk.protection_status == ProtectionStatus.COMPLETELY_UNPROTECTED
        assert res.action == ActionRecommendation.ADJUST
        assert "URGENT ADJUSTMENT" in res.action_summary
        assert any("Place Stop Loss order" in t for t in res.conditional_triggers)

    def test_fee_and_slippage_calculation(self):
        pos = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 1.0,
            "entry_price": 80000.0,
            "mark_price": 80000.0,
            "stop_loss_price": 78000.0,
            "take_profit_price": 84000.0,
        }
        res = PositionAnalyzer.analyze_position(pos, account_equity=10000.0)

        # Gross loss = (80000 - 78000) * 1.0 = 2000.0
        assert res.risk.gross_loss_at_sl_usdt == 2000.0
        # Entry fee = 0.0005 * 80000 * 1 = 40.0
        # Exit fee = 0.0005 * 78000 * 1 = 39.0
        # Slippage = 0.0005 * 78000 * 1 = 39.0
        # All-in loss = 2000 + 40 + 39 + 39 = 2118.0
        assert res.risk.all_in_loss_at_sl_usdt == 2118.0
        # Loss as % of $10,000 equity = 21.18%
        assert res.risk.loss_equity_pct == 21.18

    def test_critical_liquidation_proximity(self):
        pos = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 0.5,
            "entry_price": 80000.0,
            "mark_price": 76000.0,
            "stop_loss_price": 74000.0,
            "liquidation_price": 75000.0,  # Mark is only $1000 away (< 1.5%) and SL is BELOW liquidation
        }
        res = PositionAnalyzer.analyze_position(pos)
        assert res.risk.liquidation_threat == LiquidationThreat.CRITICAL
        assert res.action == ActionRecommendation.CLOSE
        assert any("Stop loss is below liquidation price" in note for note in res.risk.protection_notes)

    def test_portfolio_analysis_aggregation(self):
        positions = [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "position_amt": 0.5,
                "entry_price": 80000.0,
                "mark_price": 80000.0,
                "allocated_margin_usdt": 4000.0,
                "stop_loss_price": 78000.0,
            },
            {
                "symbol": "ETHUSDT",
                "side": "LONG",
                "position_amt": 5.0,
                "entry_price": 3000.0,
                "mark_price": 3000.0,
                "allocated_margin_usdt": 1500.0,
                "stop_loss_price": None,  # Unprotected
            },
        ]
        port = PositionAnalyzer.analyze_portfolio(
            positions=positions,
            account_equity=10000.0,
            available_balance=4500.0,
        )

        assert port.open_positions_count == 2
        assert port.total_notional_usdt == (0.5 * 80000.0) + (5.0 * 3000.0)  # 40k + 15k = 55k
        assert port.total_margin_committed_usdt == 5500.0
        assert port.margin_utilization_pct == 55.0
        assert port.net_directional_bias == "NET_LONG"
        assert port.unprotected_positions_count == 1
        assert port.portfolio_health_status == "CRITICAL_ACTION_REQUIRED"
        assert len(port.positions_analyses) == 2

        # Verify serialization
        p_dict = port.to_dict()
        assert p_dict["open_positions_count"] == 2
        assert isinstance(p_dict["positions_analyses"], list)
