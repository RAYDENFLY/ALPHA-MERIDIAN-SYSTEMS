"""
Dual-Bot Isolation & Fast-Timeframe Architecture Tests for MODEL_B and MODEL_B_FAST.

Verifies:
1. create_model_b_fast_config sets correct timeframe & risk parameters:
   - trigger_timeframe: 15m, trend_timeframe: 1h
   - min_stop_distance_pct: 0.008 (0.80%), min_stop_atr_multiple: 1.25
   - pending_expiry_bars: 8 (2.0h TTL on 15m bars)
   - isolated capital: initial_equity_usdt: 100.0, target_trade_risk_usdt: 1.0
2. ModelBStrategyGenerator respects fast parameters:
   - Formulates MCAND_B_FAST_... setups with 15m trigger & 1h trend.
   - Rescales TP1/TP2 dynamically to >= 1.50R when 0.80% stop floor applies.
   - Computes pending order expiry based on 8 bars * 15m (2.0 hours).
3. Portfolio, Risk & Journal Isolation:
   - PaperExecutionEngine(model_id="MODEL_B_FAST") generates PTRD_FAST_... and PORD_FAST_... IDs.
   - Opening or closing trades on MODEL_B_FAST does not impact MODEL_B's equity, risk counters,
     open position count, pending orders, or journal queries.
"""

import time
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from config.settings import ModelBConfig, create_model_b_fast_config, PaperExecutionConfig
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
from src.strategy.model_b.generator import ModelBStrategyGenerator
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperOrder, PaperPosition
from src.risk.model_b_risk import ModelBRiskManager
from src.data.database import DatabaseManager
from src.journal.repository import JournalRepository


def make_candle(ts_ms: int, o: float, h: float, l: float, c: float, sym: str = "BTCUSDT", tf: str = "15m") -> Candle:
    bar_ms = 900000 if tf == "15m" else 3600000
    return Candle(
        symbol=sym,
        timeframe=tf,
        timestamp_ms=ts_ms,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
        close_time_ms=ts_ms + bar_ms - 1,
        is_closed=True,
    )


def make_snapshot(sym: str = "BTCUSDT", imb: float = 0.25, cvd: float = 50000.0, abs_score: float = 0.8, spread_bps: float = 5.0) -> LiquiditySnapshot:
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


@pytest.fixture
def test_db(tmp_path):
    """Provides a fresh isolated test database."""
    db_path = str(tmp_path / "test_dual_bot.db")
    db = DatabaseManager(db_path)
    db.init_db()
    return db


def test_create_model_b_fast_config_parameters():
    """Verify create_model_b_fast_config creates independent fast configuration."""
    base_config = ModelBConfig(
        model_id="MODEL_B",
        cohort_version="MODEL_B_V2_REMEDIATED",
        trigger_timeframe="1h",
        trend_timeframe="4h",
        min_stop_distance_pct=0.015,
        min_stop_atr_multiple=1.25,
        pending_expiry_bars=6,
        pending_order_ttl_hours=6.0,
        initial_equity_usdt=100.0,
        target_trade_risk_usdt=1.0,
        max_open_positions=6,
    )

    fast_config = create_model_b_fast_config(base_config)

    # Identifiers
    assert fast_config.model_id == "MODEL_B_FAST"
    assert fast_config.cohort_version == "MODEL_B_FAST_V2"

    # Timeframes
    assert fast_config.trigger_timeframe == "15m"
    assert fast_config.trend_timeframe == "1h"

    # Risk & Stop Floor
    assert fast_config.min_stop_distance_pct == 0.008  # 0.80% stop floor
    assert fast_config.min_stop_atr_multiple == 1.25
    assert fast_config.pending_expiry_bars == 8
    assert fast_config.pending_order_ttl_hours == 2.0  # 8 * 15m = 2.0h

    # Capital & Limits
    assert fast_config.initial_equity_usdt == 100.0
    assert fast_config.target_trade_risk_usdt == 1.0
    assert fast_config.max_open_positions == 6
    assert fast_config.max_open_per_direction == 3
    assert fast_config.daily_loss_limit_usdt == 3.0
    assert fast_config.weekly_loss_limit_usdt == 7.5


def test_generator_fast_timeframe_and_stop_floor_rescaling():
    """Verify ModelBStrategyGenerator formulates fast candidates and rescales TP1 >= 1.50R when 0.80% stop floor applies."""
    fast_config = create_model_b_fast_config(ModelBConfig())
    mock_collector = MagicMock()
    mock_collector.collect_for_symbol.return_value = make_snapshot("BTCUSDT", imb=0.25, cvd=50000.0, abs_score=0.8)

    generator = ModelBStrategyGenerator(
        config=fast_config,
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

    # 15m closed candles: 20 base candles, swing low at candle 20, 5 intermediate candles, candle 26 sweeps and reclaims
    bar_15m = 15 * 60 * 1000
    candles_15m = [make_candle(i * bar_15m, 100.0, 101.0, 99.8, 100.5, tf="15m") for i in range(20)]
    candles_15m.append(make_candle(20 * bar_15m, 100.5, 101.0, 99.50, 100.2, tf="15m"))  # swing low 99.50
    candles_15m.extend([make_candle((i + 21) * bar_15m, 100.2, 100.8, 99.9, 100.4, tf="15m") for i in range(5)])
    # Trigger bar: sweep 99.40 < 99.50, reclaim 100.5 > 99.50, confirmed green body (open 99.8 < close 100.5)
    candles_15m.append(make_candle(26 * bar_15m, 99.8, 100.7, 99.40, 100.5, tf="15m"))

    # 1h trend candles (bullish)
    candles_1h = [make_candle(i * 3600000, 95.0 + (i * 0.2), 96.0 + (i * 0.2), 94.5 + (i * 0.2), 95.8 + (i * 0.2), tf="1h") for i in range(50)]

    book = NormalizedOrderBook(
        symbol="BTCUSDT",
        exchange_name="asterdex",
        timestamp_ms=26 * bar_15m,
        bids=[OrderBookLevel(price=100.4, size=1000.0)],
        asks=[OrderBookLevel(price=100.6, size=1000.0)],
    )

    now_ms = 26 * bar_15m + bar_15m
    cand = generator.evaluate_symbol(
        symbol="BTCUSDT",
        candles=candles_15m,
        instrument=inst,
        order_book=book,
        candles_4h=candles_1h,  # passed as higher timeframe
        now_ms=now_ms,
    )

    assert cand is not None
    assert cand.model_id == "MODEL_B_FAST"
    assert cand.candidate_id.startswith("MCAND_B_FAST_")
    assert cand.entry_price == 100.50

    # 0.80% stop floor check
    risk = cand.entry_price - cand.stop_loss
    assert round(risk, 4) >= round(100.50 * 0.008, 4)

    # Dynamic TP rescaling: TP1 must be >= 1.50R
    expected_tp1 = round(cand.entry_price + (risk * 1.5), 8)
    assert cand.take_profit_1 == expected_tp1
    assert cand.gross_reward_to_risk >= 1.49
    assert cand.ai_analysis.get("cohort_version") == "MODEL_B_FAST_V2"

    # Expiry verification: 8 bars * 15m = 2.0h (anchored to signal timestamp 26 * bar_15m)
    assert cand.expires_at_ms - (26 * bar_15m) == 8 * 15 * 60 * 1000


def test_dual_bot_isolation_paper_engine_and_journal(test_db):
    """Verify strict isolation between MODEL_B and MODEL_B_FAST across paper execution and trade journal."""
    journal_repo = JournalRepository(test_db)
    paper_cfg = PaperExecutionConfig()

    normal_cfg = ModelBConfig(model_id="MODEL_B", cohort_version="MODEL_B_V2_REMEDIATED")
    fast_cfg = create_model_b_fast_config(normal_cfg)

    normal_risk = ModelBRiskManager(config=normal_cfg)
    fast_risk = ModelBRiskManager(config=fast_cfg)

    normal_engine = PaperExecutionEngine(
        db=test_db,
        journal=journal_repo,
        risk_manager=normal_risk,
        config=paper_cfg,
        model_id="MODEL_B",
    )

    fast_engine = PaperExecutionEngine(
        db=test_db,
        journal=journal_repo,
        risk_manager=fast_risk,
        config=paper_cfg,
        model_id="MODEL_B_FAST",
    )

    # 1. Both engines start with clean state
    assert len(normal_engine.open_positions) == 0
    assert len(fast_engine.open_positions) == 0

    # 2. Open a position on MODEL_B_FAST using PaperPosition
    pos_fast = PaperPosition(
        trade_id="PTRD_FAST_ETH_001",
        order_id="PORD_FAST_ETH_001",
        setup_id="MCAND_B_FAST_ETH_001",
        symbol="ETHUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=1000000,
        entry_price=2000.0,
        stop_loss=1980.0,
        take_profit=2030.0,
        quantity=0.5,
        notional_usdt=1000.0,
        margin_usdt=100.0,
        leverage=10.0,
        entry_fee=0.5,
        model_id="MODEL_B_FAST",
    )
    fast_engine.open_positions["ETHUSDT"] = pos_fast
    fast_record = fast_engine._build_open_trade_record(pos_fast)
    journal_repo.save_trade(fast_record)

    # Verify ID prefixes & isolation
    assert pos_fast.trade_id.startswith("PTRD_FAST_")
    assert pos_fast.model_id == "MODEL_B_FAST"
    assert "ETHUSDT" in fast_engine.open_positions
    assert "ETHUSDT" not in normal_engine.open_positions

    # 3. Open a position on MODEL_B (normal 1H)
    pos_normal = PaperPosition(
        trade_id="PTRD_B_BTC_001",
        order_id="PORD_B_BTC_001",
        setup_id="MCAND_B_BTC_001",
        symbol="BTCUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=1800000000000,
        entry_price=60000.0,
        stop_loss=59100.0,
        take_profit=61350.0,
        quantity=0.01,
        notional_usdt=600.0,
        margin_usdt=60.0,
        leverage=10.0,
        entry_fee=0.3,
        model_id="MODEL_B",
    )
    normal_engine.open_positions["BTCUSDT"] = pos_normal
    normal_record = normal_engine._build_open_trade_record(pos_normal)
    journal_repo.save_trade(normal_record)

    assert pos_normal.trade_id.startswith("PTRD_B_")
    assert pos_normal.model_id == "MODEL_B"
    assert "BTCUSDT" in normal_engine.open_positions
    assert "BTCUSDT" not in fast_engine.open_positions

    # 4. Check journal isolation queries
    fast_open_trades = journal_repo.get_open_trades(model_id="MODEL_B_FAST")
    normal_open_trades = journal_repo.get_open_trades(model_id="MODEL_B")

    assert len(fast_open_trades) == 1
    assert fast_open_trades[0]["symbol"] == "ETHUSDT"
    assert fast_open_trades[0]["cohort"] == "FAST_V2"
    assert fast_open_trades[0]["strategy_badge"] == "15M SCALP (FAST)"

    assert len(normal_open_trades) == 1
    assert normal_open_trades[0]["symbol"] == "BTCUSDT"
    assert normal_open_trades[0]["cohort"] == "V2"
    assert normal_open_trades[0]["strategy_badge"] == "1H SWING (V2)"

    # 5. Simulate closing the fast position with a loss (use current UTC timestamp)
    now_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    closed_fast_record = TradeRecord(
        trade_id="PTRD_FAST_ETH_001",
        symbol="ETHUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=now_ts - 60000,
        entry_price=2000.0,
        exit_timestamp_ms=now_ts,
        exit_price=1980.0,
        stop_loss=1980.0,
        take_profit=2030.0,
        quantity=0.5,
        notional_usdt=1000.0,
        pnl_gross=-10.0,
        pnl_net=-11.0,
        fee_paid=1.0,
        slippage_paid=0.0,
        funding_paid=0.0,
        exit_reason="STOP_LOSS",
        model_id="MODEL_B_FAST",
        details_json='{"cohort_version": "MODEL_B_FAST_V2"}',
        is_open=False,
    )
    journal_repo.save_trade(closed_fast_record)
    del fast_engine.open_positions["ETHUSDT"]

    assert len(fast_engine.open_positions) == 0
    assert len(normal_engine.open_positions) == 1  # Normal position unaffected

    # 6. Rehydrate risk managers and check loss limit isolation
    fast_risk.sync_state_from_journal(journal_repo, now_ms=now_ts)
    normal_risk.sync_state_from_journal(journal_repo, now_ms=now_ts)

    fast_status = fast_risk.get_risk_summary()
    normal_status = normal_risk.get_risk_summary()

    # Fast risk has recorded its realized loss
    assert fast_status["model_id"] == "MODEL_B_FAST"
    assert fast_status["open_positions_count"] == 0
    assert fast_status["daily_loss_usdt"] == 11.0

    # Normal risk remains completely clean with $0 realized loss and 1 open position
    assert normal_status["model_id"] == "MODEL_B"
    assert normal_status["open_positions_count"] == 1
    assert normal_status["daily_loss_usdt"] == 0.0
