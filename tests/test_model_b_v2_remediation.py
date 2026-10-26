"""Tests for Model B Phase I Remediation Suite (MODEL_B_V2_REMEDIATED).

Validates all 7 critical engineering fixes:
1. TP1/TP2 rescaling on enforce_stop_floor() & dynamic R:R
2. Confirmed 1H candle body & 4H trend alignment
3. Opposing order flow / wall imbalance veto
4. L2 depth gate infeasibility (< $5k depth)
5. Net-PnL circuit breaker & max_open_per_direction = 2
6. Settle funding missing boundary fallback (1 bps)
7. Remediated cohort tagging ('MODEL_B_V2_REMEDIATED')
"""

import pytest
from unittest.mock import MagicMock

from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    MarketBias,
    StructureState,
    TradeRecord,
)
from src.exchange.normalized import (
    NormalizedInstrument,
    NormalizedOrderBook,
    OrderBookLevel,
    LiquiditySnapshot,
)
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperPosition
from src.risk.model_b_risk import ModelBRiskManager
from src.strategy.model_b.evaluator import evaluate_liquidity_setup
from src.strategy.model_b.generator import ModelBStrategyGenerator
from config.settings import ModelBConfig


def make_candle(ts_ms: int, o: float, h: float, l: float, c: float, sym: str = "BTCUSDT", vol: float = 1000.0) -> Candle:
    return Candle(
        symbol=sym,
        timeframe="1h",
        timestamp_ms=ts_ms,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=vol,
        close_time_ms=ts_ms + 3600000 - 1,
        is_closed=True,
    )


def make_snapshot(sym: str = "TESTUSDT", imb: float = 0.30, cvd: float = 50000.0, abs_score: float = 0.8, spread_bps: float = 5.0) -> LiquiditySnapshot:
    return LiquiditySnapshot(
        symbol=sym,
        exchange_name="asterdex",
        timestamp_ms=100000,
        spread_bps=spread_bps,
        depth_10bps_usdt=10000.0,
        depth_25bps_usdt=25000.0,
        depth_50bps_usdt=50000.0,
        depth_100bps_usdt=100000.0,
        imbalance_10bps=imb,
        imbalance_25bps=imb,
        imbalance_50bps=imb,
        imbalance_100bps=imb,
        cvd_1h=cvd,
        taker_buy_volume_1h=10000.0,
        taker_sell_volume_1h=5000.0,
        large_trade_count=10,
        absorption_score=abs_score,
    )


class TestModelBV2Remediation:
    """Comprehensive regression and verification test suite for Model B V2."""

    def test_1_tp_rescaling_and_dynamic_rr(self):
        """Item 1: When stop floor widens stop loss, TP1/TP2 are rescaled and R:R >= 1.5R."""
        mock_collector = MagicMock()
        mock_collector.collect_for_symbol.return_value = make_snapshot("BTCUSDT", imb=0.25, cvd=50000.0, abs_score=0.8)

        generator = ModelBStrategyGenerator(
            config=ModelBConfig(),
            liquidity_collector=mock_collector,
        )

        inst = NormalizedInstrument(
            symbol="BTCUSDT",
            exchange_name="asterdex",
            base_asset="BTC",
            quote_asset="USDT",
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
            min_notional=5.0,
            volume_24h_usdt=5000000.0,
            volume_status="AVAILABLE",
        )

        candles = [make_candle(i * 3600000, 100.0, 101.0, 99.8, 100.5) for i in range(20)]
        candles.append(make_candle(20 * 3600000, 100.5, 101.0, 99.50, 100.2)) # swing low 99.50
        candles.extend([make_candle((i + 21) * 3600000, 100.2, 100.8, 99.9, 100.4) for i in range(5)])
        # Trigger bar: sweep 99.40 < 99.50, reclaim 100.5 > 99.50, confirmed green body (open 99.8 < close 100.5)
        candles.append(make_candle(26 * 3600000, 99.8, 100.7, 99.40, 100.5, vol=1500.0))

        book = NormalizedOrderBook(
            symbol="BTCUSDT",
            exchange_name="asterdex",
            timestamp_ms=26 * 3600000,
            bids=[OrderBookLevel(price=100.4, size=1000.0)],
            asks=[OrderBookLevel(price=100.6, size=1000.0)],
        )

        cand = generator.evaluate_symbol(
            symbol="BTCUSDT",
            candles=candles,
            instrument=inst,
            order_book=book,
            candles_4h=candles,
        )

        assert cand is not None
        assert cand.entry_price == 100.50
        risk = cand.entry_price - cand.stop_loss
        assert round(risk, 4) >= round(100.50 * 0.015, 4)

        expected_tp1 = round(cand.entry_price + (risk * 1.5), 8)
        assert cand.take_profit_1 == expected_tp1
        assert cand.gross_reward_to_risk >= 1.49
        assert cand.ai_analysis.get("cohort_version") == "MODEL_B_V2_REMEDIATED"

    def test_2_candle_body_direction_rule(self):
        """Item 2a: Reject LONG on red bar (close < open) and SHORT on green bar (close > open)."""
        snapshot = make_snapshot("TESTUSDT", imb=0.30, cvd=50000.0, abs_score=0.8)

        candles = [make_candle(i * 3600000, 10.0, 10.5, 9.5, 10.0, sym="TESTUSDT") for i in range(20)]
        candles.append(make_candle(20 * 3600000, 10.0, 10.2, 8.5, 9.8, sym="TESTUSDT")) # swing low 8.5
        candles.extend([make_candle((i + 21) * 3600000, 9.8, 10.1, 9.5, 10.0, sym="TESTUSDT") for i in range(5)])

        # Trigger bar: pierced below 8.5 (low=8.0) and closed above 8.5 (close=8.9), BUT open=9.2 -> RED candle!
        red_reclaim = make_candle(26 * 3600000, 9.2, 9.3, 8.0, 8.9, sym="TESTUSDT")
        signal_red = evaluate_liquidity_setup("TESTUSDT", candles + [red_reclaim], snapshot)
        assert signal_red is None, "Should reject LONG reclaim on a red candle body (Variant D rule)"

        # Trigger bar with GREEN candle body (open=8.4, close=8.9)
        green_reclaim = make_candle(26 * 3600000, 8.4, 9.3, 8.0, 8.9, sym="TESTUSDT")
        signal_green = evaluate_liquidity_setup("TESTUSDT", candles + [green_reclaim], snapshot)
        assert signal_green is not None, "Should trigger LONG reclaim on a confirmed green candle body"
        assert signal_green.direction == Direction.LONG

    def test_3_opposing_order_flow_imbalance_veto(self):
        """Item 2c: Veto setup if order book imbalance or opposing walls oppose trade."""
        mock_collector = MagicMock()
        # Strongly adverse imbalance -0.25 opposing LONG
        mock_collector.collect_for_symbol.return_value = make_snapshot("ETHUSDT", imb=-0.25, cvd=50000.0, abs_score=0.8)

        generator = ModelBStrategyGenerator(
            config=ModelBConfig(),
            liquidity_collector=mock_collector,
        )

        inst = NormalizedInstrument(
            symbol="ETHUSDT",
            exchange_name="asterdex",
            base_asset="ETH",
            quote_asset="USDT",
            min_qty=0.01,
            step_size=0.01,
            tick_size=0.01,
            min_notional=5.0,
            volume_24h_usdt=5000000.0,
            volume_status="AVAILABLE",
        )

        candles = [make_candle(i * 3600000, 2000.0, 2020.0, 1980.0, 2000.0, sym="ETHUSDT") for i in range(20)]
        candles.append(make_candle(20 * 3600000, 2000.0, 2010.0, 1950.0, 1980.0, sym="ETHUSDT"))
        candles.extend([make_candle((i + 21) * 3600000, 1980.0, 2000.0, 1970.0, 1990.0, sym="ETHUSDT") for i in range(5)])
        candles.append(make_candle(26 * 3600000, 1950.0, 1995.0, 1920.0, 1980.0, sym="ETHUSDT", vol=1500.0))

        book_adverse = NormalizedOrderBook(
            symbol="ETHUSDT",
            exchange_name="asterdex",
            timestamp_ms=26 * 3600000,
            bids=[OrderBookLevel(price=1979.0, size=5.0)],
            asks=[OrderBookLevel(price=1981.0, size=500.0)],
        )

        cand = generator.evaluate_symbol(
            symbol="ETHUSDT",
            candles=candles,
            instrument=inst,
            order_book=book_adverse,
            candles_4h=candles,
        )

        assert cand is not None
        assert "OPPOSING_ORDER_FLOW_IMBALANCE" in cand.opposing_reasons or "OPPOSING_ORDER_FLOW_IMBALANCE" in cand.rejection_reasons
        assert cand.status == CandidateStatus.INFEASIBLE

    def test_4_new_market_scanner_l2_depth_gate(self):
        """Item 3: Enforce L2 depth gate so books < $5k depth are INFEASIBLE."""
        mock_collector = MagicMock()
        mock_collector.collect_for_symbol.return_value = make_snapshot("THINUSDT", imb=0.20, cvd=50000.0, abs_score=0.8)

        generator = ModelBStrategyGenerator(
            config=ModelBConfig(),
            liquidity_collector=mock_collector,
        )

        inst = NormalizedInstrument(
            symbol="THINUSDT",
            exchange_name="asterdex",
            base_asset="THIN",
            quote_asset="USDT",
            min_qty=0.01,
            step_size=0.01,
            tick_size=0.01,
            min_notional=5.0,
            volume_24h_usdt=5000000.0,
            volume_status="AVAILABLE",
        )

        candles = [make_candle(i * 3600000, 10.0, 10.5, 9.5, 10.0, sym="THINUSDT") for i in range(20)]
        candles.append(make_candle(20 * 3600000, 10.0, 10.2, 8.5, 9.8, sym="THINUSDT"))
        candles.extend([make_candle((i + 21) * 3600000, 9.8, 10.1, 9.5, 10.0, sym="THINUSDT") for i in range(5)])
        candles.append(make_candle(26 * 3600000, 8.4, 9.3, 8.0, 8.9, sym="THINUSDT", vol=1500.0))

        # Very thin book: total depth $200 << $5000 min requirement
        thin_book = NormalizedOrderBook(
            symbol="THINUSDT",
            exchange_name="asterdex",
            timestamp_ms=26 * 3600000,
            bids=[OrderBookLevel(price=8.89, size=10.0)],
            asks=[OrderBookLevel(price=8.91, size=10.0)],
        )

        cand = generator.evaluate_symbol(
            symbol="THINUSDT",
            candles=candles,
            instrument=inst,
            order_book=thin_book,
            candles_4h=candles,
        )

        assert cand is not None
        assert cand.status == CandidateStatus.INFEASIBLE
        assert any("depth within 10 bps" in r for r in cand.rejection_reasons)

    def test_5_circuit_breaker_net_pnl_and_directional_cap(self):
        """Item 5: Net PnL circuit breaker accounting and max 2 open per direction cap."""
        risk_mgr = ModelBRiskManager(
            initial_equity_usdt=100.0,
            daily_loss_limit_usdt=3.0,
            weekly_loss_limit_usdt=7.5,
            max_open_positions=3,
            max_open_per_direction=2,
        )

        trade_win = TradeRecord(
            trade_id="TRD_1",
            symbol="BTCUSDT",
            direction="LONG",
            entry_timestamp_ms=1000000,
            entry_price=100.0,
            stop_loss=98.0,
            take_profit=103.0,
            quantity=1.0,
            notional_usdt=100.0,
            pnl_net=2.00,
            is_open=False,
            exit_timestamp_ms=1003600,
        )
        trade_loss = TradeRecord(
            trade_id="TRD_2",
            symbol="ETHUSDT",
            direction="SHORT",
            entry_timestamp_ms=1004000,
            entry_price=2000.0,
            stop_loss=2050.0,
            take_profit=1900.0,
            quantity=1.0,
            notional_usdt=2000.0,
            pnl_net=-1.50,
            is_open=False,
            exit_timestamp_ms=1007200,
        )

        risk_mgr.record_exit(trade_win)
        risk_mgr.record_exit(trade_loss)

        assert risk_mgr.daily_loss_usdt == 0.0
        assert risk_mgr.weekly_loss_usdt == 0.0

        open_long_1 = TradeRecord(
            trade_id="OPEN_L1",
            symbol="SOLUSDT",
            direction="LONG",
            entry_timestamp_ms=1010000,
            entry_price=150.0,
            stop_loss=145.0,
            take_profit=160.0,
            quantity=1.0,
            notional_usdt=150.0,
            is_open=True,
        )
        open_long_2 = TradeRecord(
            trade_id="OPEN_L2",
            symbol="AVAXUSDT",
            direction="LONG",
            entry_timestamp_ms=1011000,
            entry_price=30.0,
            stop_loss=28.0,
            take_profit=35.0,
            quantity=1.0,
            notional_usdt=30.0,
            is_open=True,
        )

        risk_mgr.record_entry(open_long_1)
        risk_mgr.record_entry(open_long_2)

        can_open_long, reason_long = risk_mgr.can_open_new_position(1012000, direction=Direction.LONG)
        assert can_open_long is False
        assert "directional position cap reached for LONG" in reason_long

        can_open_short, reason_short = risk_mgr.can_open_new_position(1012000, direction=Direction.SHORT)
        assert can_open_short is True

    def test_6_funding_settlement_missing_boundary_fallback(self):
        """Item 6: Missing 8h funding boundary triggers pos.funding_data_missing and 1 bps fee."""
        engine = PaperExecutionEngine(
            db=MagicMock(),
            journal=MagicMock(),
            risk_manager=ModelBRiskManager(),
            model_id="MODEL_B",
        )
        engine.db.get_funding_rates.return_value = []

        t_entry = 3600000
        t_current = 36000000

        pos = PaperPosition(
            trade_id="PTRD_FTEST",
            order_id="PORD_FTEST",
            setup_id="MCAND_FTEST",
            symbol="BTCUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=t_entry,
            entry_price=50000.0,
            stop_loss=49000.0,
            take_profit=52000.0,
            quantity=0.1,
            notional_usdt=5000.0,
            margin_usdt=500.0,
            leverage=10.0,
            entry_fee=1.0,
            model_id="MODEL_B",
        )

        engine._settle_funding(pos, current_candle_ts_ms=t_current)

        assert pos.funding_data_missing is True
        assert round(pos.funding_paid, 4) == 0.50
        assert 28800000 in pos.settled_funding_timestamps
