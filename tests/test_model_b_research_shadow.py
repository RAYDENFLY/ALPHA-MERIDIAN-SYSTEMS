"""Comprehensive Test Suite for Model B Independent Research Shadow Evaluator.

Proves:
1. Canonical signal ID is preserved across all 6 variants (paired comparisons).
2. Variant A (Current LONG Baseline) accepts LONG signals while SHORT variants filter them out.
3. Variant B (Current SHORT Baseline) accepts SHORT signals unconditionally.
4. Variant C (SHORT 4H Trend) filters SHORTs with bullish 4H trend.
5. Variant D (SHORT Confirmed Close) filters SHORTs without confirmed closed-candle reclaim (green body).
6. Variant E (SHORT Order Flow) filters SHORTs with contradictory positive bid depth imbalance (> -5%).
7. Variant F (Combined Validated SHORT) combines all filters strictly.
8. Simulated execution steps model limit penetration, TP/SL triggers, and excursion tracking.
9. Zero leakage: trade_journal and paper_orders are completely untouched.
10. Active execution circuit breaker tripping does NOT halt shadow signal evaluations.
11. Synthetic portfolio computation accurately models combined baseline vs optimized variants.
"""

from datetime import datetime, timezone
from pathlib import Path
import pytest

from src.core.types import Candle, CandidateStatus, Direction, EntryCategory, MarketBias, RiskAssessment, SetupCandidate, StructureState
from src.data.database import DatabaseManager
from src.journal.repository import JournalRepository
from src.research.types import (
    EvaluationDecision,
    ResearchVariant,
    ShadowOrderStatus,
    ShadowSignalRecord,
    ShadowVariantEvaluation,
)
from src.research.rules import VariantRuleEngine
from src.research.storage import ResearchStorage
from src.research.shadow_evaluator import ModelBShadowEvaluator


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_research_shadow.db"
    return DatabaseManager(db_file)


@pytest.fixture
def storage(temp_db: DatabaseManager):
    return ResearchStorage(temp_db)


@pytest.fixture
def evaluator(temp_db: DatabaseManager, storage: ResearchStorage):
    return ModelBShadowEvaluator(db=temp_db, storage=storage)


def make_candidate(
    candidate_id: str = "MCAND_B_TEST_1",
    symbol: str = "TESTUSDT",
    direction: Direction = Direction.SHORT,
    entry_price: float = 100.0,
    stop_loss: float = 102.0,
    take_profit: float = 97.0,
    timestamp_ms: int = 1792340000000,
    status: CandidateStatus = CandidateStatus.READY,
) -> SetupCandidate:
    assessment = RiskAssessment(
        symbol=symbol,
        entry_price=entry_price,
        stop_loss=stop_loss,
        stop_distance_pct=2.0,
        risk_budget_usdt=1.00,
        position_notional_usdt=50.0,
        quantity=0.5,
        required_margin_usdt=5.0,
        effective_leverage=10.0,
        position_leverage=10.0,
        target_isolated_margin_usdt=5.0,
        is_feasible=True,
    )
    return SetupCandidate(
        candidate_id=candidate_id,
        symbol=symbol,
        timestamp_ms=timestamp_ms,
        direction=direction,
        bias_4h=MarketBias.BEARISH,
        structure_1h=StructureState.BEARISH,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit_1=take_profit,
        take_profit_2=take_profit * 0.98,
        gross_reward_to_risk=1.5,
        net_reward_to_risk=1.4,
        invalidation_price=stop_loss,
        status=status,
        entry_category=EntryCategory.REVERSAL,
        model_id="MODEL_B",
        risk_assessment=assessment,
    )


class TestModelBResearchShadowEvaluator:
    """Deterministic verification of shadow evaluation logic and variant behavior."""

    def test_1_canonical_signal_id_preserved_across_all_variants(self, evaluator: ModelBShadowEvaluator, storage: ResearchStorage):
        """1. Every signal generates a single canonical signal_id linked to all 6 variant evaluations."""
        cand = make_candidate()
        sig, evals = evaluator.record_and_evaluate_candidate(cand)

        assert sig.signal_id == f"RSIG_{cand.symbol}_{cand.direction.value}_{cand.timestamp_ms}"
        assert len(evals) == 6
        assert len(set(e.variant for e in evals)) == 6

        # Check database records
        db_sig = storage.get_signal_by_id(sig.signal_id)
        assert db_sig is not None
        assert db_sig["symbol"] == cand.symbol

        paired_evals = storage.get_evaluations_by_signal(sig.signal_id)
        assert len(paired_evals) == 6
        for pe in paired_evals:
            assert pe["signal_id"] == sig.signal_id

    def test_2_long_baseline_variant_accepts_long_signals(self, evaluator: ModelBShadowEvaluator):
        """2. Variant A (Current LONG Baseline) accepts LONG signals; SHORT variants reject due to direction."""
        cand = make_candidate(direction=Direction.LONG, entry_price=100.0, stop_loss=98.0, take_profit=103.0)
        sig, evals = evaluator.record_and_evaluate_candidate(cand)

        eval_by_var = {e.variant: e for e in evals}
        ev_a = eval_by_var[ResearchVariant.VARIANT_A_LONG_BASELINE]
        assert ev_a.decision == EvaluationDecision.ACCEPTED
        assert ev_a.order_status == ShadowOrderStatus.PENDING
        assert ev_a.simulated_entry_price == 100.0
        assert ev_a.simulated_stop_loss == 98.0

        # Variants B through F are SHORT variants and reject LONG signals
        for v in [
            ResearchVariant.VARIANT_B_SHORT_BASELINE,
            ResearchVariant.VARIANT_C_SHORT_4H_TREND,
            ResearchVariant.VARIANT_D_SHORT_CONFIRMED_CLOSE,
            ResearchVariant.VARIANT_E_SHORT_ORDER_FLOW,
            ResearchVariant.VARIANT_F_SHORT_COMBINED,
        ]:
            assert eval_by_var[v].decision == EvaluationDecision.REJECTED
            assert "SIDE_MISMATCH" in eval_by_var[v].rejection_reason

    def test_3_variant_c_filters_shorts_with_bullish_4h_trend(self, evaluator: ModelBShadowEvaluator):
        """3. Variant C rejects SHORT when 4H market structure is bullish, while Variant B (SHORT Baseline) accepts."""
        cand = make_candidate(direction=Direction.SHORT)
        context = {"bias_4h_true": "BULLISH"}

        sig, evals = evaluator.record_and_evaluate_candidate(cand, context=context)
        eval_by_var = {e.variant: e for e in evals}

        # SHORT Baseline accepts
        assert eval_by_var[ResearchVariant.VARIANT_B_SHORT_BASELINE].decision == EvaluationDecision.ACCEPTED
        # Variant C rejects
        assert eval_by_var[ResearchVariant.VARIANT_C_SHORT_4H_TREND].decision == EvaluationDecision.REJECTED
        assert "HTF_BULLISH_TREND" in eval_by_var[ResearchVariant.VARIANT_C_SHORT_4H_TREND].rejection_reason

    def test_4_variant_d_filters_unconfirmed_candle_close(self, evaluator: ModelBShadowEvaluator):
        """4. Variant D rejects SHORT when 1H candle closed as a green bullish bar."""
        cand = make_candidate(direction=Direction.SHORT)
        context = {"candle_body": "BULLISH_GREEN", "is_valid_reclaim_at_close": False}

        sig, evals = evaluator.record_and_evaluate_candidate(cand, context=context)
        eval_by_var = {e.variant: e for e in evals}

        assert eval_by_var[ResearchVariant.VARIANT_B_SHORT_BASELINE].decision == EvaluationDecision.ACCEPTED
        assert eval_by_var[ResearchVariant.VARIANT_D_SHORT_CONFIRMED_CLOSE].decision == EvaluationDecision.REJECTED
        reason = eval_by_var[ResearchVariant.VARIANT_D_SHORT_CONFIRMED_CLOSE].rejection_reason
        assert "UNCONFIRMED_RECLAIM" in reason or "BULLISH_CANDLE_BODY" in reason

    def test_5_variant_e_filters_contradictory_bid_depth(self, evaluator: ModelBShadowEvaluator):
        """5. Variant E rejects SHORT when order book depth has positive bid imbalance (+25%)."""
        cand = make_candidate(direction=Direction.SHORT)
        context = {"imbalance_25bps": 0.25}  # +25% net bids

        sig, evals = evaluator.record_and_evaluate_candidate(cand, context=context)
        eval_by_var = {e.variant: e for e in evals}

        assert eval_by_var[ResearchVariant.VARIANT_B_SHORT_BASELINE].decision == EvaluationDecision.ACCEPTED
        assert eval_by_var[ResearchVariant.VARIANT_E_SHORT_ORDER_FLOW].decision == EvaluationDecision.REJECTED
        assert "CONTRADICTORY_BID_DEPTH" in eval_by_var[ResearchVariant.VARIANT_E_SHORT_ORDER_FLOW].rejection_reason

    def test_6_variant_f_combined_requires_all_filters(self, evaluator: ModelBShadowEvaluator):
        """6. Variant F requires 4H trend, confirmed candle close, negative book imbalance, and ATR feasibility."""
        cand = make_candidate(direction=Direction.SHORT)

        # Passes all
        good_context = {
            "bias_4h_true": "BEARISH",
            "is_valid_reclaim_at_close": True,
            "candle_body": "BEARISH_RED",
            "imbalance_25bps": -0.15,
            "atr_1h": 1.0,
        }
        sig1, evals1 = evaluator.record_and_evaluate_candidate(cand, context=good_context)
        ev_f1 = next(e for e in evals1 if e.variant == ResearchVariant.VARIANT_F_SHORT_COMBINED)
        assert ev_f1.decision == EvaluationDecision.ACCEPTED

        # Fails order flow
        bad_context = dict(good_context)
        bad_context["imbalance_25bps"] = 0.10
        sig2, evals2 = evaluator.record_and_evaluate_candidate(cand, context=bad_context)
        ev_f2 = next(e for e in evals2 if e.variant == ResearchVariant.VARIANT_F_SHORT_COMBINED)
        assert ev_f2.decision == EvaluationDecision.REJECTED
        assert "Order Flow" in ev_f2.rejection_reason

    def test_7_simulated_execution_step_models_fills_exits_and_excursions(self, evaluator: ModelBShadowEvaluator, storage: ResearchStorage):
        """7. Stepping evaluations simulates limit penetration, TP/SL triggers, and excursion tracking."""
        cand = make_candidate(direction=Direction.SHORT, entry_price=100.0, stop_loss=102.0, take_profit=95.0, timestamp_ms=1000)
        sig, evals = evaluator.record_and_evaluate_candidate(cand)

        # Bar 1: Price reaches 100.05 (penetrates limit 100.0) -> FILLED
        c1 = Candle(symbol=cand.symbol, timestamp_ms=2000, close_time_ms=2999, open=99.5, high=100.1, low=99.0, close=99.2, volume=100.0, timeframe="1h")
        evaluator.step_evaluations({cand.symbol: [c1]})

        open_orders = storage.get_open_shadow_orders()
        var_b = next(o for o in open_orders if o.variant == ResearchVariant.VARIANT_B_SHORT_BASELINE)
        assert var_b.order_status == ShadowOrderStatus.FILLED
        assert var_b.fill_price == 100.0
        assert var_b.fee_paid > 0.0

        # Bar 2: Price drops to 94.0 (hits TP at 95.0) -> CLOSED via TAKE_PROFIT
        c2 = Candle(symbol=cand.symbol, timestamp_ms=3000, close_time_ms=3999, open=99.0, high=99.2, low=94.0, close=94.5, volume=150.0, timeframe="1h")
        evaluator.step_evaluations({cand.symbol: [c1, c2]})

        closed = storage.get_evaluations_for_variant(ResearchVariant.VARIANT_B_SHORT_BASELINE.value, status="CLOSED")
        assert len(closed) == 1
        res = closed[0]
        assert res["exit_reason"] == "TAKE_PROFIT"
        assert res["pnl_net"] > 0.0
        assert res["mfe_pct"] >= 5.0  # Dropped from 100 to 95+

    def test_8_zero_leakage_into_active_journal_or_paper_orders(self, evaluator: ModelBShadowEvaluator, temp_db: DatabaseManager):
        """8. Research evaluations never write to trade_journal, paper_orders, or paper_lifecycle_events."""
        cand = make_candidate()
        evaluator.record_and_evaluate_candidate(cand)

        with temp_db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM trade_journal;")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM paper_orders;")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM paper_lifecycle_events;")
            assert cur.fetchone()[0] == 0

    def test_9_circuit_breaker_active_does_not_halt_shadow_signal_collection(self, evaluator: ModelBShadowEvaluator, storage: ResearchStorage):
        """9. Active execution halt (e.g. daily loss circuit breaker) is recorded as metadata while shadow evaluates."""
        cand = make_candidate()
        sig, evals = evaluator.record_and_evaluate_candidate(
            cand,
            active_ineligible_reason="Model B daily loss circuit breaker triggered ($4.45 >= $3.00)",
        )

        assert sig.active_execution_eligible is False
        assert "circuit breaker" in sig.active_ineligible_reason.lower()
        # Shadow variants evaluate and accept normally
        assert any(e.decision == EvaluationDecision.ACCEPTED for e in evals)

        metrics = storage.compute_metrics(ResearchVariant.VARIANT_B_SHORT_BASELINE.value)
        assert metrics.total_signals_seen == 1

    def test_10_synthetic_portfolio_and_metrics_calculation(self, evaluator: ModelBShadowEvaluator, storage: ResearchStorage):
        """10. Storage accurately computes individual variant and synthetic combined portfolio metrics."""
        # 1 LONG signal
        cand_long = make_candidate(candidate_id="MCAND_LONG_1", symbol="LONGUSDT", direction=Direction.LONG, entry_price=100.0, stop_loss=98.0, take_profit=104.0, timestamp_ms=1000)
        evaluator.record_and_evaluate_candidate(cand_long)

        # 1 SHORT signal
        cand_short = make_candidate(candidate_id="MCAND_SHORT_1", symbol="SHORTUSDT", direction=Direction.SHORT, entry_price=50.0, stop_loss=52.0, take_profit=46.0, timestamp_ms=2000)
        evaluator.record_and_evaluate_candidate(cand_short, context={"bias_4h_true": "BEARISH", "imbalance_25bps": -0.10, "is_valid_reclaim_at_close": True, "candle_body": "BEARISH_RED"})

        # Step LONG with winning candle
        c_long1 = Candle(symbol=cand_long.symbol, timestamp_ms=1500, close_time_ms=2499, open=100.5, high=101.0, low=99.9, close=100.2, volume=10.0, timeframe="1h")
        c_long2 = Candle(symbol=cand_long.symbol, timestamp_ms=2500, close_time_ms=3499, open=100.2, high=105.0, low=100.0, close=104.5, volume=20.0, timeframe="1h")
        evaluator.step_evaluations({cand_long.symbol: [c_long1, c_long2]})

        # Step SHORT with winning candle
        c_short1 = Candle(symbol=cand_short.symbol, timestamp_ms=2500, close_time_ms=3499, open=49.8, high=50.1, low=49.0, close=49.5, volume=10.0, timeframe="1h")
        c_short2 = Candle(symbol=cand_short.symbol, timestamp_ms=3500, close_time_ms=4499, open=49.5, high=49.8, low=45.0, close=45.5, volume=20.0, timeframe="1h")
        evaluator.step_evaluations({cand_short.symbol: [c_short1, c_short2]})

        # Check metrics for Variant A (LONG)
        m_a = storage.compute_metrics(ResearchVariant.VARIANT_A_LONG_BASELINE.value)
        assert m_a.simulated_closed == 1
        assert m_a.win_rate_pct == 100.0
        assert m_a.net_pnl_usdt > 0.0

        # Check synthetic portfolio (Variant A + Variant F)
        port = storage.compute_portfolio_metrics(
            long_variant=ResearchVariant.VARIANT_A_LONG_BASELINE.value,
            short_variant=ResearchVariant.VARIANT_F_SHORT_COMBINED.value,
        )
        assert port["closed_trades"] == 2
        assert port["win_rate_pct"] == 100.0
        assert port["long_trades"] == 1
        assert port["short_trades"] == 1
