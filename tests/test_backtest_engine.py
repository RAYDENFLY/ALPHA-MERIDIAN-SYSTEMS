"""Unit tests for Event-Driven Backtesting Engine and Conservative Collisions.
"""

from unittest.mock import MagicMock
import pytest
from src.backtest.engine import BacktestEngine
from src.backtest.performance import PerformanceCalculator
from src.core.types import Candle, Direction, TradeRecord
from src.exchange.metadata import SymbolSpecs


SAMPLE_SPECS = SymbolSpecs(
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
    max_qty=100000.0,
    min_notional=5.0,
    maint_margin_percent=5.0,
)


def make_candle(h: float, l: float, c: float, ts: int = 1700000000000) -> Candle:
    return Candle(
        symbol="ADAUSDT",
        timeframe="1h",
        timestamp_ms=ts,
        open=(h + l) / 2,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
        close_time_ms=ts + 3599999,
        quote_volume=1000.0 * c,
        trades_count=100,
        is_closed=True,
    )


class TestBacktestEngine:
    def test_conservative_tp_sl_collision_triggers_sl(self):
        mock_db = MagicMock()
        engine = BacktestEngine(
            db=mock_db,
            symbol_specs_map={"ADAUSDT": SAMPLE_SPECS},
            initial_equity=16.0,
            taker_fee=0.0005,
            slippage=0.0005,
        )

        # Long position: Entry=0.50, SL=0.48, TP=0.55
        trade = TradeRecord(
            trade_id="coll_test",
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1700000000000,
            entry_price=0.50,
            stop_loss=0.48,
            take_profit=0.55,
            quantity=10.0,
            notional_usdt=5.0,
            fee_paid=0.0025,
            is_open=True,
        )

        # Candle wicks up to 0.56 (past TP 0.55) AND down to 0.47 (past SL 0.48)
        collision_candle = make_candle(h=0.56, l=0.47, c=0.52)

        closed = engine._process_open_trade_bar(trade, collision_candle)

        # Must close with SL_COLLISION (conservative SL-first policy)
        assert closed is True
        assert trade.exit_reason == "SL_COLLISION"
        assert trade.pnl_net < 0  # Loss recorded, never favorable outcome!

    def test_clean_tp_exit(self):
        mock_db = MagicMock()
        engine = BacktestEngine(
            db=mock_db,
            symbol_specs_map={"ADAUSDT": SAMPLE_SPECS},
            initial_equity=16.0,
            taker_fee=0.0005,
            slippage=0.0005,
        )

        trade = TradeRecord(
            trade_id="tp_test",
            symbol="ADAUSDT",
            direction=Direction.LONG,
            entry_timestamp_ms=1700000000000,
            entry_price=0.50,
            stop_loss=0.48,
            take_profit=0.55,
            quantity=10.0,
            notional_usdt=5.0,
            fee_paid=0.0025,
            is_open=True,
        )

        # Candle reaches TP without touching SL
        tp_candle = make_candle(h=0.56, l=0.495, c=0.555)
        closed = engine._process_open_trade_bar(trade, tp_candle)

        assert closed is True
        assert trade.exit_reason == "TP"
        assert trade.pnl_net > 0

    def test_performance_calculation(self):
        trades = [
            TradeRecord(
                trade_id="t1",
                symbol="ADAUSDT",
                direction=Direction.LONG,
                entry_timestamp_ms=1000,
                entry_price=0.50,
                stop_loss=0.49,
                take_profit=0.52,
                quantity=10.0,
                notional_usdt=5.0,
                exit_timestamp_ms=2000,
                exit_price=0.52,
                pnl_gross=0.20,
                pnl_net=0.18,
                fee_paid=0.01,
                funding_paid=0.01,
                exit_reason="TP",
                is_open=False,
            ),
            TradeRecord(
                trade_id="t2",
                symbol="ADAUSDT",
                direction=Direction.LONG,
                entry_timestamp_ms=3000,
                entry_price=0.50,
                stop_loss=0.49,
                take_profit=0.52,
                quantity=10.0,
                notional_usdt=5.0,
                exit_timestamp_ms=4000,
                exit_price=0.49,
                pnl_gross=-0.10,
                pnl_net=-0.11,
                fee_paid=0.005,
                funding_paid=0.005,
                exit_reason="SL",
                is_open=False,
            ),
        ]

        report = PerformanceCalculator.calculate_report(trades, initial_equity=16.0)
        assert report.total_trades == 2
        assert report.winning_trades == 1
        assert report.losing_trades == 1
        assert report.win_rate_pct == 50.0
        assert report.total_net_pnl == 0.07
        assert report.final_equity == 16.07
        assert report.profit_factor > 1.0
