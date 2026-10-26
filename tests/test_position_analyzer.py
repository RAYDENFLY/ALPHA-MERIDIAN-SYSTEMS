"""Unit tests for AsterDEX PositionAnalyzer (Analyze Entry Engine)."""

import pytest
from src.core.types import Candle
from src.intelligence.position_analyzer import (
    ActionRecommendation,
    LiquidationThreat,
    PositionAnalyzer,
    PositionRiskStatus,
    ProtectionStatus,
    ThesisStatus,
)


def make_candle(
    idx: int,
    total_count: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    tf: str = "1h",
    sym: str = "BTCUSDT",
    end_ts: int = 1700000000000,
) -> Candle:
    step_ms = 900000 if tf == "15m" else (3600000 if tf == "1h" else 14400000)
    # Align the last candle close with end_ts for exact multi-timeframe synchronization
    close_ts = end_ts - (total_count - 1 - idx) * step_ms
    open_ts = close_ts - step_ms
    return Candle(
        symbol=sym,
        timeframe=tf,
        timestamp_ms=open_ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        close_time_ms=close_ts,
        quote_volume=100.0 * close,
    )


def build_bullish_candles(
    n: int = 40,
    tf: str = "1h",
    sym: str = "BTCUSDT",
    base_price: float = 80000.0,
    end_ts: int = 1700000000000,
) -> list[Candle]:
    candles = []
    price = base_price
    pattern = [200.0, 300.0, 250.0, -150.0, -100.0, 200.0, 350.0, 200.0, -180.0, -120.0]
    for i in range(n):
        delta = pattern[i % len(pattern)]
        o = price
        c = price + delta
        h = max(o, c) + 80.0
        l = min(o, c) - 80.0
        candles.append(make_candle(i, n, o, h, l, c, tf=tf, sym=sym, end_ts=end_ts))
        price = c
    return candles


def build_bearish_candles(
    n: int = 40,
    tf: str = "1h",
    sym: str = "BTCUSDT",
    base_price: float = 85000.0,
    end_ts: int = 1700000000000,
) -> list[Candle]:
    candles = []
    price = base_price
    pattern = [-200.0, -300.0, -250.0, 150.0, 100.0, -200.0, -350.0, -200.0, 180.0, 120.0]
    for i in range(n):
        delta = pattern[i % len(pattern)]
        o = price
        c = price + delta
        h = max(o, c) + 80.0
        l = min(o, c) - 80.0
        candles.append(make_candle(i, n, o, h, l, c, tf=tf, sym=sym, end_ts=end_ts))
        price = c
    return candles


def build_neutral_candles(
    n: int = 40,
    tf: str = "1h",
    sym: str = "BTCUSDT",
    base_price: float = 80000.0,
    end_ts: int = 1700000000000,
) -> list[Candle]:
    candles = []
    price = base_price
    pattern = [30.0, -30.0, 20.0, -20.0, 40.0, -40.0, 10.0, -10.0]
    for i in range(n):
        delta = pattern[i % len(pattern)]
        o = price
        c = price + delta
        h = max(o, c) + 40.0
        l = min(o, c) - 40.0
        candles.append(make_candle(i, n, o, h, l, c, tf=tf, sym=sym, end_ts=end_ts))
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
        shared_end = 1700000000000
        c_4h = build_bullish_candles(25, tf="4h", end_ts=shared_end)
        c_1h = build_bullish_candles(25, tf="1h", end_ts=shared_end)
        c_15m = build_bullish_candles(25, tf="15m", end_ts=shared_end)

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
            "is_exchange_verified": True,
            "sl_orders": [{"order_id": "sl_001", "orig_qty": 0.5, "price": last_close - 1500.0}],
            "tp_orders": [{"order_id": "tp_001", "orig_qty": 0.5, "price": last_close + 2500.0}],
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
        shared_end = 1700000000000
        c_4h = build_bearish_candles(25, tf="4h", end_ts=shared_end)
        c_1h = build_bearish_candles(25, tf="1h", end_ts=shared_end)
        c_15m = build_bearish_candles(25, tf="15m", end_ts=shared_end)

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
        assert res.action in (ActionRecommendation.CLOSE, ActionRecommendation.EXIT)
        assert "CLOSE RECOMMENDATION" in res.action_summary or "EXIT" in res.action_summary

    def test_unprotected_position_flags_urgent_adjustment(self):
        c_1h = build_bullish_candles(20, tf="1h")
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
        assert res.risk.protection_status in (ProtectionStatus.COMPLETELY_UNPROTECTED, ProtectionStatus.UNPROTECTED_NO_SL)
        assert res.action in (ActionRecommendation.ADJUST, ActionRecommendation.REVIEW)
        assert "URGENT ADJUSTMENT" in res.action_summary or "REVIEW" in res.action_summary
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
        assert res.action in (ActionRecommendation.CLOSE, ActionRecommendation.EXIT)
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

    # =========================================================================
    # Section 10: Specific Regression & Multi-Timeframe Coverage Tests
    # =========================================================================

    def test_multitimeframe_all_bullish(self):
        """All timeframes (15M, 1H, 4H) bullish -> CONFLUENT_BULLISH_ACROSS_ALL_TIMEFRAMES."""
        shared_end = 1700000000000
        c_4h = build_bullish_candles(30, tf="4h", end_ts=shared_end)
        c_1h = build_bullish_candles(30, tf="1h", end_ts=shared_end)
        c_15m = build_bullish_candles(30, tf="15m", end_ts=shared_end)
        last_close = c_15m[-1].close

        pos = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 0.5,
            "entry_price": last_close - 200.0,
            "mark_price": last_close,
            "stop_loss_price": last_close - 1000.0,
            "take_profit_price": last_close + 2000.0,
            "is_exchange_verified": True,
            "sl_orders": [{"order_id": "sl1", "orig_qty": 0.5}],
            "tp_orders": [{"order_id": "tp1", "orig_qty": 0.5}],
        }
        res = PositionAnalyzer.analyze_position(pos, candles_4h=c_4h, candles_1h=c_1h, candles_15m=c_15m)
        assert res.multi_timeframe_synthesis == "CONFLUENT_BULLISH_ACROSS_ALL_TIMEFRAMES"
        assert res.thesis_status == ThesisStatus.THESIS_INTACT
        assert res.timeframe_15m.position_alignment == "SUPPORTS"

    def test_multitimeframe_all_bearish(self):
        """All timeframes (15M, 1H, 4H) bearish -> CONFLUENT_BEARISH_ACROSS_ALL_TIMEFRAMES."""
        shared_end = 1700000000000
        c_4h = build_bearish_candles(30, tf="4h", end_ts=shared_end)
        c_1h = build_bearish_candles(30, tf="1h", end_ts=shared_end)
        c_15m = build_bearish_candles(30, tf="15m", end_ts=shared_end)
        last_close = c_15m[-1].close

        pos = {
            "symbol": "BTCUSDT",
            "side": "SHORT",
            "position_amt": 0.5,
            "entry_price": last_close + 200.0,
            "mark_price": last_close,
            "stop_loss_price": last_close + 1000.0,
            "take_profit_price": last_close - 2000.0,
            "is_exchange_verified": True,
            "sl_orders": [{"order_id": "sl1", "orig_qty": 0.5}],
            "tp_orders": [{"order_id": "tp1", "orig_qty": 0.5}],
        }
        res = PositionAnalyzer.analyze_position(pos, candles_4h=c_4h, candles_1h=c_1h, candles_15m=c_15m)
        assert res.multi_timeframe_synthesis == "CONFLUENT_BEARISH_ACROSS_ALL_TIMEFRAMES"
        assert res.thesis_status == ThesisStatus.THESIS_INTACT
        assert res.timeframe_15m.position_alignment == "SUPPORTS"

    def test_multitimeframe_mixed_bullish_recovery_in_htf_bearish(self):
        """
        Mixed timeframes: 15M Bullish, 1H Neutral/Sideways, 4H Bearish.
        MUST explicitly evaluate to SHORT_TERM_BULLISH_RECOVERY_WITHIN_HIGHER_TIMEFRAME_BEARISH_STRUCTURE.
        Must never collapse into a single bullish or bearish label.
        """
        shared_end = 1700000000000
        c_4h = build_bearish_candles(35, tf="4h", end_ts=shared_end)
        c_1h = build_neutral_candles(35, tf="1h", end_ts=shared_end)
        c_15m = build_bullish_candles(35, tf="15m", end_ts=shared_end)
        last_close = c_15m[-1].close

        pos = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 0.25,
            "entry_price": last_close - 100.0,
            "mark_price": last_close,
            "stop_loss_price": last_close - 600.0,
            "take_profit_price": last_close + 1200.0,
            "is_exchange_verified": True,
            "sl_orders": [{"order_id": "sl1", "orig_qty": 0.25}],
        }
        res = PositionAnalyzer.analyze_position(pos, candles_4h=c_4h, candles_1h=c_1h, candles_15m=c_15m)
        
        # Mandated synthesis code
        assert res.multi_timeframe_synthesis == "SHORT_TERM_BULLISH_RECOVERY_WITHIN_HIGHER_TIMEFRAME_BEARISH_STRUCTURE"
        # 15M and 4H must retain their independent directions
        assert res.timeframe_15m.trend == "BULLISH"
        assert res.timeframe_4h.trend == "BEARISH"
        # Since 4H conflicts, recommendation should be REDUCE or REVIEW, never unconditional HOLD
        assert res.action in (ActionRecommendation.REVIEW, ActionRecommendation.REDUCE)
        assert res.action != ActionRecommendation.HOLD

    def test_multitimeframe_missing_and_stale_data(self):
        """Missing 4H candle data should flag staleness/uncertainty without crashing."""
        c_1h = build_bullish_candles(25, tf="1h")
        c_15m = build_bullish_candles(25, tf="15m")
        pos = {
            "symbol": "SOLUSDT",
            "side": "LONG",
            "position_amt": 1.0,
            "entry_price": 100.0,
            "mark_price": 101.0,
            "stop_loss_price": 98.0,
        }
        res = PositionAnalyzer.analyze_position(pos, candles_4h=[], candles_1h=c_1h, candles_15m=c_15m)
        assert res.timeframe_4h.trend == "INSUFFICIENT_DATA"
        assert res.multi_timeframe_synthesis == "STRUCTURAL_UNCERTAINTY_INSUFFICIENT_DATA"
        assert res.freshness is not None
        assert any("4h" in r.lower() for r in res.freshness.staleness_reasons)

    def test_protection_verified_vs_unverified(self):
        """Distinguish between local position config SL vs confirmed active exchange order."""
        c_1h = build_bullish_candles(25, tf="1h")
        
        # 1. Unverified: SL exists in dict, but is_exchange_verified is False and sl_orders is None
        pos_unverified = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 0.5,
            "entry_price": 80000.0,
            "mark_price": 80500.0,
            "stop_loss_price": 79000.0,
            "take_profit_price": 83000.0,
            "is_exchange_verified": False,
        }
        res_unverified = PositionAnalyzer.analyze_position(pos_unverified, candles_1h=c_1h)
        assert res_unverified.risk.protection_status == ProtectionStatus.PROTECTION_UNVERIFIED
        assert res_unverified.risk.risk_status == PositionRiskStatus.PROTECTION_UNVERIFIED
        assert any("PROTECTION UNVERIFIED" in n for n in res_unverified.risk.protection_notes)

        # 2. Verified: is_exchange_verified is True and matching order present
        pos_verified = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 0.5,
            "entry_price": 80000.0,
            "mark_price": 80500.0,
            "stop_loss_price": 79000.0,
            "take_profit_price": 83000.0,
            "is_exchange_verified": True,
            "sl_orders": [{"order_id": "sl_x99", "orig_qty": 0.5, "price": 79000.0}],
            "tp_orders": [{"order_id": "tp_x99", "orig_qty": 0.5, "price": 83000.0}],
        }
        res_verified = PositionAnalyzer.analyze_position(pos_verified, candles_1h=c_1h)
        assert res_verified.risk.protection_status == ProtectionStatus.FULLY_PROTECTED
        assert res_verified.risk.is_exchange_verified is True
        assert res_verified.risk.verified_sl_order_id == "sl_x99"

    def test_protection_quantity_mismatch_returns_partially_protected(self):
        """If exchange SL order qty covers only part of the position, flag PARTIALLY_PROTECTED."""
        c_1h = build_bullish_candles(25, tf="1h")
        pos = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 1.0,  # Full position is 1.0
            "entry_price": 80000.0,
            "mark_price": 80500.0,
            "stop_loss_price": 79000.0,
            "is_exchange_verified": True,
            "sl_orders": [{"order_id": "sl_partial", "orig_qty": 0.4}],  # Only 0.4 covered
        }
        res = PositionAnalyzer.analyze_position(pos, candles_1h=c_1h)
        assert res.risk.protection_status == ProtectionStatus.PARTIALLY_PROTECTED
        assert any("PARTIAL PROTECTION" in n for n in res.risk.protection_notes)

    def test_short_position_risk_and_reward_math(self):
        """
        Exact math validation for SHORT positions:
        Entry = 80,000, Mark = 79,000
        SL = 82,000 (above entry)
        TP = 76,000 (below entry)
        Qty = 1.0
        """
        pos = {
            "symbol": "BTCUSDT",
            "side": "SHORT",
            "position_amt": 1.0,
            "entry_price": 80000.0,
            "mark_price": 79000.0,
            "stop_loss_price": 82000.0,
            "take_profit_price": 76000.0,
        }
        res = PositionAnalyzer.analyze_position(pos, account_equity=10000.0)

        # Gross loss at SL for SHORT = (SL - Entry) * Qty = (82000 - 80000) * 1.0 = 2000.0
        assert res.risk.gross_loss_at_sl_usdt == 2000.0

        # Gross profit at TP for SHORT = (Entry - TP) * Qty = (80000 - 76000) * 1.0 = 4000.0
        assert res.risk.gross_profit_at_tp_usdt == 4000.0

        # Gross R:R = 4000 / 2000 = 2.0
        assert res.risk.gross_risk_reward_ratio == 2.0

        # Estimated fees and slippage must increase net loss and reduce net profit
        # Entry fee = 0.0005 * 80000 * 1 = 40.0
        # Exit fee at SL = 0.0005 * 82000 * 1 = 41.0
        # Slippage at SL = 0.0005 * 82000 * 1 = 41.0
        # Net loss = 2000 + 40 + 41 + 41 = 2122.0
        assert res.risk.net_loss_at_sl_usdt == 2122.0
        assert res.risk.net_loss_at_sl_usdt > res.risk.gross_loss_at_sl_usdt

        # Net profit at TP = 4000 - 40 (entry fee) - (0.0005*76000 exit fee) - (0.0005*76000 slippage)
        # 4000 - 40 - 38 - 38 = 3884.0
        assert res.risk.net_profit_at_tp_usdt == 3884.0
        assert res.risk.net_profit_at_tp_usdt < res.risk.gross_profit_at_tp_usdt

        # Net R:R must be strictly less than gross R:R
        assert res.risk.net_risk_reward_ratio < res.risk.gross_risk_reward_ratio
        assert res.risk.net_risk_reward_ratio == round(3884.0 / 2122.0, 2)

    def test_inverted_stop_loss_and_take_profit_flagged(self):
        """Inverted SL or TP prices must be detected and flagged as RISK_BREACHED."""
        # LONG with SL ABOVE mark price
        pos_inverted_sl = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "position_amt": 1.0,
            "entry_price": 80000.0,
            "mark_price": 80500.0,
            "stop_loss_price": 82000.0,  # Invalid: above mark/entry
        }
        res_inverted_sl = PositionAnalyzer.analyze_position(pos_inverted_sl)
        assert any("Stop loss is above mark price" in n for n in res_inverted_sl.risk.protection_notes)
        assert res_inverted_sl.risk.risk_status == PositionRiskStatus.RISK_BREACHED

        # SHORT with SL BELOW mark price
        pos_inverted_short_sl = {
            "symbol": "BTCUSDT",
            "side": "SHORT",
            "position_amt": 1.0,
            "entry_price": 80000.0,
            "mark_price": 79500.0,
            "stop_loss_price": 78000.0,  # Invalid: below mark/entry
        }
        res_inverted_short_sl = PositionAnalyzer.analyze_position(pos_inverted_short_sl)
        assert any("Stop loss is below mark price" in n for n in res_inverted_short_sl.risk.protection_notes)
        assert res_inverted_short_sl.risk.risk_status == PositionRiskStatus.RISK_BREACHED

    def test_market_regime_altcoin_countertrend_to_btc(self):
        """An altcoin LONG position against a bearish BTC trend must flag COUNTER_TREND_TO_BTC."""
        shared_end = 1700000000000
        c_4h = build_bullish_candles(25, tf="4h", sym="SOLUSDT", base_price=145.0, end_ts=shared_end)
        c_1h = build_bullish_candles(25, tf="1h", sym="SOLUSDT", base_price=150.0, end_ts=shared_end)
        c_15m = build_bullish_candles(25, tf="15m", sym="SOLUSDT", base_price=151.0, end_ts=shared_end)

        btc_4h = build_bearish_candles(25, tf="4h", sym="BTCUSDT", base_price=85000.0, end_ts=shared_end)
        btc_1h = build_bearish_candles(25, tf="1h", sym="BTCUSDT", base_price=83000.0, end_ts=shared_end)

        pos = {
            "symbol": "SOLUSDT",
            "side": "LONG",
            "position_amt": 2.0,
            "entry_price": 150.0,
            "mark_price": 152.0,
            "stop_loss_price": 147.0,
            "take_profit_price": 160.0,
            "is_exchange_verified": True,
            "sl_orders": [{"order_id": "sl_sol", "orig_qty": 2.0}],
        }
        res = PositionAnalyzer.analyze_position(
            pos,
            candles_4h=c_4h,
            candles_1h=c_1h,
            candles_15m=c_15m,
            btc_candles_1h=btc_1h,
            btc_candles_4h=btc_4h,
            btc_ticker={"lastPrice": 81000.0, "priceChangePercent": -4.2},
        )
        assert res.market_regime is not None
        assert res.market_regime.position_correlation == "COUNTER_TREND_TO_BTC"
        assert res.market_regime.btc_trend_1h == "BEARISH"
        assert any("Counter-trend" in ev for ev in res.structural_evidence)
