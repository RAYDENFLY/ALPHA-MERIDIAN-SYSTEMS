"""
Unit Tests for MODEL_B_V3 / MODEL_B_V1_PRO (Responsive V1 Entry + Anti-Short Bleed + Correlation Cap).
=======================================================================================================
Verifies:
1. create_model_b_v3_config parameters:
   - model_id: 'MODEL_B_V3', cohort_version: 'MODEL_B_V1_PRO'
   - trigger_timeframe: '15m', trend_timeframe: '1h'
   - require_confirmed_body: False (responsive V1 sweep reclaim)
   - require_htf_ema_alignment: False (no lagging top-buying)
   - allowed_directions: ['LONG'] (anti-short bleed filter)
   - max_open_positions: 2, max_open_per_direction: 1 (correlation cap)
   - full_exit_at_tp1: True, tp1_rr_multiple: 1.50
2. Fast V2 Correlation Cap:
   - fast_cfg.max_open_positions: 2, fast_cfg.max_open_per_direction: 1
3. Responsive V1 Entry:
   - V1-Pro triggers on red sweep-reclaim candle without waiting for lagging green close.
   - V2 requires green close.
4. Anti-Short Bleed:
   - V1-Pro rejects SHORT setups completely.
5. In-Flight Correlation Protection:
   - When 1 LONG order is pending or open, 2nd LONG order is rejected by in-flight directional cap.
6. 100% Full Exit at TP1 (1.50R).
7. Journal Cohort Classification:
   - Classifies MODEL_B_V1_PRO as 'V1_PRO' with '15M V1-PRO'.
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from config.settings import ModelBConfig, create_model_b_fast_config, create_model_b_v3_config, PaperExecutionConfig
from src.core.types import (
    Candle,
    CandidateStatus,
    Direction,
    MarketBias,
    SetupCandidate,
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
from src.strategy.model_b.evaluator import evaluate_liquidity_setup
from src.execution.paper_engine import PaperExecutionEngine
from src.execution.types import PaperOrder, PaperPosition
from src.risk.model_b_risk import ModelBRiskManager
from src.data.database import DatabaseManager
from src.journal.repository import JournalRepository


def make_candle(
    ts_ms: int,
    o: float,
    h: float,
    l: float,
    c: float,
    sym: str = "ETHUSDT",
    tf: str = "15m",
    vol: float = 1000.0,
) -> Candle:
    bar_ms = 900000 if tf == "15m" else 3600000
    return Candle(
        symbol=sym,
        timeframe=tf,
        timestamp_ms=ts_ms,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=vol,
        close_time_ms=ts_ms + bar_ms - 1,
        is_closed=True,
    )


def make_snapshot(
    sym: str = "ETHUSDT",
    imb: float = 0.25,
    cvd: float = 50000.0,
    abs_score: float = 0.8,
    spread_bps: float = 5.0,
) -> LiquiditySnapshot:
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
    db_path = str(tmp_path / "test_v1_pro.db")
    db = DatabaseManager(db_path)
    db.init_db()
    return db


# ==============================================================================
# Test 1: Config Parameters & Correlation Caps
# ==============================================================================
def test_create_model_b_v3_config_parameters():
    """Verify create_model_b_v3_config produces exact V1-Pro parameters while Fast and Normal stay configured."""
    v2_default = ModelBConfig()
    assert v2_default.model_id == "MODEL_B"
    assert v2_default.cohort_version == "MODEL_B_V2_REMEDIATED"
    assert v2_default.trigger_timeframe == "1h"
    assert v2_default.trend_timeframe == "4h"

    # Fast Scalper config updated with correlation cap (max 2 open, max 1 per direction)
    fast_cfg = create_model_b_fast_config(v2_default)
    assert fast_cfg.model_id == "MODEL_B_FAST"
    assert fast_cfg.cohort_version == "MODEL_B_FAST_V2"
    assert fast_cfg.trigger_timeframe == "15m"
    assert fast_cfg.trend_timeframe == "1h"
    assert fast_cfg.max_open_positions == 2
    assert fast_cfg.max_open_per_direction == 1

    # V3 (V1-Pro) config
    v3_cfg = create_model_b_v3_config(v2_default)
    assert v3_cfg.model_id == "MODEL_B_V3"
    assert v3_cfg.cohort_version == "MODEL_B_V1_PRO"
    assert v3_cfg.trigger_timeframe == "15m"
    assert v3_cfg.trend_timeframe == "1h"
    assert v3_cfg.min_stop_distance_pct == 0.010
    assert v3_cfg.require_confirmed_body is False
    assert v3_cfg.require_htf_ema_alignment is False
    assert v3_cfg.allowed_directions == ["LONG"]
    assert v3_cfg.max_open_positions == 2
    assert v3_cfg.max_open_per_direction == 1
    assert v3_cfg.full_exit_at_tp1 is True
    assert v3_cfg.tp1_rr_multiple == 1.50
    assert v3_cfg.symbol_blacklist == []


# ==============================================================================
# Test 2: V1 Responsive Entry on Red Sweep-Reclaim Candle
# ==============================================================================
def test_model_b_v1_pro_responsive_entry_on_red_candle():
    """Verify V1-Pro (require_confirmed_body=False) enters immediately on a red sweep-reclaim candle."""
    candles = [make_candle(1000 + i * 900000, 100.0, 105.0, 95.0, 100.0) for i in range(20)]
    candles.append(make_candle(1000 + 20 * 900000, 100.0, 102.0, 90.0, 98.0))  # Swing low at 90.0
    candles.append(make_candle(1000 + 21 * 900000, 98.0, 101.0, 95.0, 100.0))
    candles.append(make_candle(1000 + 22 * 900000, 100.0, 102.0, 96.0, 99.0))

    # Red candle (Open=95.0, Low=88.0, Close=92.0 < Open) that swept 90.0 and reclaimed 92.0 > 90.0
    red_sweep_candle = make_candle(1000 + 23 * 900000, 95.0, 96.0, 88.0, 92.0)
    test_candles = candles + [red_sweep_candle]
    snap = make_snapshot(sym="ETHUSDT", imb=0.25)

    # 1. V1-Pro (require_confirmed_body=False): ACCEPTED immediately at the bottom
    sig_v1_pro = evaluate_liquidity_setup(
        symbol="ETHUSDT",
        candles=test_candles,
        snapshot=snap,
        timeframe_label="15M",
        require_confirmed_body=False,
    )
    assert sig_v1_pro is not None
    assert sig_v1_pro.direction == Direction.LONG
    assert sig_v1_pro.entry_price == 92.0

    # 2. V2 (require_confirmed_body=True): REJECTED because candle is red
    sig_v2 = evaluate_liquidity_setup(
        symbol="ETHUSDT",
        candles=test_candles,
        snapshot=snap,
        timeframe_label="15M",
        require_confirmed_body=True,
    )
    assert sig_v2 is None, "V2 must reject red candle close."


# ==============================================================================
# Test 3: Anti-Short Bleed Filter (LONG Only)
# ==============================================================================
def test_model_b_v1_pro_filters_short_entries():
    """Verify V1-Pro with allowed_directions=['LONG'] completely vetoes SHORT signals."""
    candles = [make_candle(1000 + i * 900000, 100.0, 105.0, 95.0, 100.0) for i in range(20)]
    candles.append(make_candle(1000 + 20 * 900000, 100.0, 110.0, 98.0, 102.0))  # Swing high at 110.0
    candles.append(make_candle(1000 + 21 * 900000, 102.0, 105.0, 99.0, 100.0))
    candles.append(make_candle(1000 + 22 * 900000, 100.0, 103.0, 98.0, 101.0))

    # Candle that swept 110.0 (High=112.0) and closed below (Close=108.0)
    short_sweep_candle = make_candle(1000 + 23 * 900000, 105.0, 112.0, 104.0, 108.0)
    test_candles = candles + [short_sweep_candle]
    snap = make_snapshot(sym="ETHUSDT", imb=-0.25, cvd=-50000.0)

    # When allowed_directions=['LONG'], SHORT signal is suppressed
    sig_long_only = evaluate_liquidity_setup(
        symbol="ETHUSDT",
        candles=test_candles,
        snapshot=snap,
        timeframe_label="15M",
        require_confirmed_body=False,
        allowed_directions=["LONG"],
    )
    assert sig_long_only is None, "SHORT signal must be blocked when allowed_directions=['LONG']."

    # When allowed_directions includes 'SHORT', it is detected
    sig_with_short = evaluate_liquidity_setup(
        symbol="ETHUSDT",
        candles=test_candles,
        snapshot=snap,
        timeframe_label="15M",
        require_confirmed_body=False,
        allowed_directions=["LONG", "SHORT"],
    )
    assert sig_with_short is not None
    assert sig_with_short.direction == Direction.SHORT


# ==============================================================================
# Test 4: In-Flight Correlation Cap Prevents Simultaneous Multi-Fills
# ==============================================================================
def test_model_b_in_flight_correlation_cap(test_db):
    """Verify that when 1 LONG order is pending or open, a 2nd LONG order is rejected by in-flight cap."""
    journal = JournalRepository(test_db)
    v3_cfg = create_model_b_v3_config()
    v3_risk = ModelBRiskManager(config=v3_cfg)
    paper_cfg = PaperExecutionConfig(full_exit_at_tp1=True)

    engine = PaperExecutionEngine(
        db=test_db,
        journal=journal,
        risk_manager=v3_risk,
        config=paper_cfg,
        model_id="MODEL_B_V3",
    )

    assessment = MagicMock()
    assessment.is_feasible = True
    assessment.quantity = 1.0
    assessment.position_notional_usdt = 100.0
    assessment.target_isolated_margin_usdt = 10.0
    assessment.position_leverage = 10.0

    cand_1 = SetupCandidate(
        candidate_id="MCAND_B_V3_ZECUSDT_LONG_1000",
        symbol="ZECUSDT",
        timestamp_ms=1000,
        status=CandidateStatus.READY,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=100.0,
        stop_loss=98.50,
        take_profit_1=102.25,
        take_profit_2=None,
        gross_reward_to_risk=1.5,
        net_reward_to_risk=1.4,
        invalidation_price=98.50,
        invalidation_reason="SL",
        risk_assessment=assessment,
        model_id="MODEL_B_V3",
    )

    # 1. First order submitted -> creates pending order
    order_1 = engine.submit_candidate(cand_1, now_ms=1000)
    assert order_1 is not None
    assert len(engine.pending_orders) == 1

    # 2. Second LONG candidate submitted in the same cycle (e.g. correlated DOGEUSDT pump)
    cand_2 = SetupCandidate(
        candidate_id="MCAND_B_V3_DOGEUSDT_LONG_1000",
        symbol="DOGEUSDT",
        timestamp_ms=1000,
        status=CandidateStatus.READY,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=0.20,
        stop_loss=0.197,
        take_profit_1=0.2045,
        take_profit_2=None,
        gross_reward_to_risk=1.5,
        net_reward_to_risk=1.4,
        invalidation_price=0.197,
        invalidation_reason="SL",
        risk_assessment=assessment,
        model_id="MODEL_B_V3",
    )

    # Must be REJECTED because directional in-flight cap (1) is already consumed by ZECUSDT pending order!
    order_2 = engine.submit_candidate(cand_2, now_ms=1000)
    assert order_2 is None, "Second LONG order must be rejected by in-flight directional cap."
    assert len(engine.pending_orders) == 1


# ==============================================================================
# Test 5: 100% Full Exit at TP1 (1.50R)
# ==============================================================================
def test_model_b_v3_executes_full_exit_at_tp1(test_db):
    """Verify MODEL_B_V3 closes 100% at TP1 with full_exit_at_tp1=True while V2 partially closes."""
    journal = JournalRepository(test_db)
    v3_cfg = create_model_b_v3_config()
    v3_risk = ModelBRiskManager(config=v3_cfg)
    v3_paper_cfg = PaperExecutionConfig(full_exit_at_tp1=True, maker_fee_rate=0.0002, taker_fee_rate=0.0005)
    v3_engine = PaperExecutionEngine(
        db=test_db,
        journal=journal,
        risk_manager=v3_risk,
        config=v3_paper_cfg,
        model_id="MODEL_B_V3",
    )

    assessment = MagicMock()
    assessment.is_feasible = True
    assessment.quantity = 1.0
    assessment.position_notional_usdt = 100.0
    assessment.target_isolated_margin_usdt = 10.0
    assessment.position_leverage = 10.0

    cand_v3 = SetupCandidate(
        candidate_id="MCAND_B_V3_SOLUSDT_LONG_1000",
        symbol="SOLUSDT",
        timestamp_ms=1000,
        status=CandidateStatus.READY,
        direction=Direction.LONG,
        bias_4h=MarketBias.BULLISH,
        structure_1h=StructureState.BULLISH,
        entry_price=100.0,
        stop_loss=98.50,
        take_profit_1=102.25,
        take_profit_2=None,
        gross_reward_to_risk=1.5,
        net_reward_to_risk=1.4,
        invalidation_price=98.50,
        invalidation_reason="SL",
        risk_assessment=assessment,
        model_id="MODEL_B_V3",
    )

    order_v3 = v3_engine.submit_candidate(cand_v3, now_ms=1000)
    assert order_v3 is not None

    fill_candle = make_candle(1001, 100.5, 101.0, 99.8, 100.0, sym="SOLUSDT")
    filled_orders = v3_engine.evaluate_pending_orders({"SOLUSDT": [fill_candle]}, now_ms=1001)
    assert len(filled_orders) == 1
    assert "SOLUSDT" in v3_engine.open_positions

    # Bar hits TP1
    tp_candle = make_candle(2000, 100.2, 103.0, 100.0, 102.5, sym="SOLUSDT")
    closed_v3 = v3_engine.evaluate_open_positions({"SOLUSDT": [tp_candle]}, now_ms=2000)

    assert len(closed_v3) == 1
    assert closed_v3[0].exit_reason == "TAKE_PROFIT"
    assert "SOLUSDT" not in v3_engine.open_positions, "Position must be 100% closed."


# ==============================================================================
# Test 6: Candidate ID Generation & Journal Cohort Classification
# ==============================================================================
def test_model_b_v3_candidate_prefix_and_journal_cohort(test_db):
    """Verify MCAND_B_V3_ prefix and journal cohort tag 'V1_PRO'."""
    v3_cfg = create_model_b_v3_config()
    gen = ModelBStrategyGenerator(config=v3_cfg)

    gen.liquidity_collector = MagicMock()
    gen.liquidity_collector.collect_for_symbol.return_value = make_snapshot(sym="ETHUSDT")

    candles = [make_candle(1000 + i * 900000, 100.0, 105.0, 95.0, 100.0) for i in range(20)]
    candles.append(make_candle(1000 + 20 * 900000, 100.0, 102.0, 90.0, 98.0))
    candles.append(make_candle(1000 + 21 * 900000, 98.0, 101.0, 95.0, 100.0))
    candles.append(make_candle(1000 + 22 * 900000, 100.0, 102.0, 96.0, 99.0))
    trigger_candle = make_candle(1000 + 23 * 900000, 95.0, 105.0, 88.0, 104.0)
    candles.append(trigger_candle)

    inst = NormalizedInstrument(
        symbol="ETHUSDT",
        exchange_name="asterdex",
        base_asset="ETH",
        quote_asset="USDT",
        min_qty=0.001,
        step_size=0.001,
        tick_size=0.01,
        min_notional=5.0,
        contract_type="PERPETUAL",
        volume_status="AVAILABLE",
        volume_24h_usdt=5000000.0,
    )

    cand = gen.evaluate_symbol(symbol="ETHUSDT", candles=candles, instrument=inst)
    assert cand is not None
    assert cand.candidate_id.startswith("MCAND_B_V3_ETHUSDT_LONG_")
    assert cand.model_id == "MODEL_B_V3"
    assert cand.ai_analysis["cohort_version"] == "MODEL_B_V1_PRO"
    assert cand.ai_analysis["strategy"] == "Model_B_V1_Pro"

    # Journal repository classification test
    journal = JournalRepository(test_db)
    record = TradeRecord(
        trade_id="PTRD_V3_ETHUSDT_1000",
        symbol="ETHUSDT",
        direction=Direction.LONG,
        entry_timestamp_ms=1000,
        entry_price=100.0,
        stop_loss=98.5,
        take_profit=101.5,
        quantity=1.0,
        notional_usdt=100.0,
        exit_timestamp_ms=2000,
        exit_price=101.5,
        pnl_gross=1.50,
        pnl_net=1.40,
        fee_paid=0.10,
        funding_paid=0.0,
        exit_reason="TAKE_PROFIT",
        is_open=False,
        setup_id=cand.candidate_id,
        timeframe="15m",
        model_id="MODEL_B_V3",
        details_json=json.dumps({"cohort_version": "MODEL_B_V1_PRO"}),
    )
    journal.save_trade(record)

    trades = journal.get_trades(model_id="MODEL_B_V3")
    assert len(trades) == 1
    t = trades[0]
    assert t["model_id"] == "MODEL_B_V3"
    assert t["cohort"] == "V1_PRO"
    assert t["strategy_badge"] == "15M V1-PRO"
    assert t["cohort_label"] == "15M V1-PRO (LONG-BIASED)"
