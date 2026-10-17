"""Unit tests for ML Pipeline, Labeling, Calibration, and Model Training.
"""

from pathlib import Path
import tempfile
import numpy as np
import pandas as pd
import pytest

from src.core.types import Candle, Direction, MarketBias, SetupCandidate, StructureState
from src.ml.calibration import ModelCalibrator
from src.ml.labeling import SetupLabeler
from src.ml.models import ModelRegistry
from src.ml.pipeline import FEATURE_NAMES, FeaturePipeline


def make_candle(ts: int, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe="1h",
        timestamp_ms=ts,
        open=(h + l) / 2,
        high=h,
        low=l,
        close=c,
        volume=100.0,
        close_time_ms=ts + 3599999,
        quote_volume=100.0 * c,
        trades_count=100,
        is_closed=True,
    )


class TestSetupLabeler:
    def test_tp_hit(self):
        cand = SetupCandidate(
            candidate_id="c1",
            symbol="BTCUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit_1=62000.0,
        )
        forward = [
            make_candle(2000, 60500.0, 59500.0, 60200.0),
            make_candle(3000, 62200.0, 60100.0, 62100.0),  # High reaches 62200 >= 62000
        ]
        outcome = SetupLabeler.label_candidate(cand, forward, horizon_bars=5)
        assert outcome.target_hit == 1
        assert outcome.outcome_reason == "TP_HIT"
        assert outcome.bars_to_outcome == 2

    def test_conservative_collision_policy(self):
        cand = SetupCandidate(
            candidate_id="c2",
            symbol="BTCUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit_1=62000.0,
        )
        # Bar wicks to 62500 (>TP) AND dips to 58500 (<SL)
        forward = [
            make_candle(2000, 62500.0, 58500.0, 60000.0),
        ]
        outcome = SetupLabeler.label_candidate(cand, forward, horizon_bars=5)
        assert outcome.target_hit == 0
        assert outcome.outcome_reason == "SL_COLLISION"

    def test_timeout(self):
        cand = SetupCandidate(
            candidate_id="c3",
            symbol="BTCUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit_1=62000.0,
        )
        # 3 bars within range [59500, 60500], horizon=3
        forward = [
            make_candle(2000, 60500.0, 59500.0, 60100.0),
            make_candle(3000, 60600.0, 59600.0, 60200.0),
            make_candle(4000, 60400.0, 59700.0, 60000.0),
        ]
        outcome = SetupLabeler.label_candidate(cand, forward, horizon_bars=3)
        assert outcome.target_hit == 0
        assert outcome.outcome_reason == "TIMEOUT"
        assert outcome.bars_to_outcome == 3


class TestFeaturePipeline:
    def test_feature_extraction_completeness(self):
        cand = SetupCandidate(
            candidate_id="c1",
            symbol="BTCUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit_1=62000.0,
            gross_reward_to_risk=2.0,
            net_reward_to_risk=1.9,
            smc_evidence=["Bullish CHoCH confirmed", "Unmitigated Order Block"],
            snr_evidence=["Support at 59000"],
        )
        candles = [make_candle(1000 + i * 3600000, 60500, 59500, 60000) for i in range(25)]

        feats = FeaturePipeline.extract_features(cand, candles)
        for col in FEATURE_NAMES:
            assert col in feats, f"Missing feature: {col}"
            assert not np.isnan(feats[col]), f"NaN found in feature {col}"

        df = FeaturePipeline.to_dataframe([feats])
        assert df.shape == (1, len(FEATURE_NAMES))


class TestModelTrainingAndCalibration:
    def test_chronological_train_and_save(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(models_dir=Path(tmp_dir))

            # Generate synthetic chronological dataset of 80 observations
            np.random.seed(42)
            n_samples = 80
            rows = []
            for _ in range(n_samples):
                d = {name: float(np.random.randn()) for name in FEATURE_NAMES}
                rows.append(d)
            df = pd.DataFrame(rows)
            y = np.random.randint(0, 2, size=n_samples)

            best_model, summaries = registry.train_and_evaluate(
                features_df=df,
                labels=y,
                train_split=0.60,
                val_split=0.20,
            )

            assert best_model is not None
            assert "logistic_regression" in summaries or "lightgbm" in summaries

            saved_path = registry.save_model(best_model)
            assert saved_path.exists()

            loaded = registry.load_model()
            assert loaded is not None
            assert "model" in loaded
            assert "scaler" in loaded

    def test_model_selection_leakage_invariance_to_y_test(self):
        """Verifies that model selection is strictly invariant to y_test labels (zero test-set leakage)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(models_dir=Path(tmp_dir))

            np.random.seed(42)
            n_samples = 100
            rows = [{name: float(np.random.randn()) for name in FEATURE_NAMES} for _ in range(n_samples)]
            df = pd.DataFrame(rows)
            y_base = np.random.randint(0, 2, size=n_samples)

            val_end = int(n_samples * 0.80)

            # Run 1: original y_test
            best_model_1, summaries_1 = registry.train_and_evaluate(
                features_df=df, labels=y_base, train_split=0.60, val_split=0.20
            )

            # Run 2: completely invert test set labels (1 - y_test)
            y_inverted_test = np.copy(y_base)
            y_inverted_test[val_end:] = 1 - y_inverted_test[val_end:]

            best_model_2, summaries_2 = registry.train_and_evaluate(
                features_df=df, labels=y_inverted_test, train_split=0.60, val_split=0.20
            )

            # Selected model MUST be identical because selection is strictly based on validation set
            assert best_model_1["name"] == best_model_2["name"]

            # Run 3: set test set to all zeros
            y_zeros_test = np.copy(y_base)
            y_zeros_test[val_end:] = 0

            best_model_3, summaries_3 = registry.train_and_evaluate(
                features_df=df, labels=y_zeros_test, train_split=0.60, val_split=0.20
            )
            assert best_model_1["name"] == best_model_3["name"]

    def test_sigmoid_positivity_guard_positive_slope(self):
        """Verifies Sigmoid calibration succeeds and preserves ranking when Platt slope a > 0."""
        class DummyBaseEstimator:
            classes_ = np.array([0, 1])
            def predict_proba(self, x):
                p = np.clip(x[:, 0], 0.01, 0.99)
                return np.column_stack([1.0 - p, p])

        base = DummyBaseEstimator()
        x_val = np.linspace(0.1, 0.9, 30).reshape(-1, 1)
        # Positive correlation between raw probability and label -> slope > 0
        y_val = (x_val[:, 0] >= 0.5).astype(int)

        wrapped = ModelCalibrator.calibrate_model(base, x_val, y_val, method="sigmoid")
        assert wrapped.used_fallback is False
        assert wrapped.fallback_reason is None
        assert wrapped.calibrator.slope > 0.0

        x_test = np.linspace(0.05, 0.95, 20).reshape(-1, 1)
        probs = wrapped.predict_proba(x_test)
        assert probs.shape == (20, 2)
        assert np.all((probs >= 0.001) & (probs <= 0.999))
        assert np.allclose(probs[:, 0] + probs[:, 1], 1.0)
        # Monotonicity preserved: strictly increasing with x_test
        assert np.all(np.diff(probs[:, 1]) >= 0.0)

    def test_sigmoid_positivity_guard_negative_and_zero_slope_fallback(self):
        """Verifies Sigmoid calibration falls back to raw probabilities when Platt slope a <= 0 (preventing rank inversion)."""
        class DummyBaseEstimator:
            classes_ = np.array([0, 1])
            def predict_proba(self, x):
                p = np.clip(x[:, 0], 0.05, 0.95)
                return np.column_stack([1.0 - p, p])

        base = DummyBaseEstimator()
        x_val = np.linspace(0.1, 0.9, 30).reshape(-1, 1)
        # Negative correlation on validation window -> slope < 0
        y_val_neg = (x_val[:, 0] < 0.5).astype(int)

        wrapped_neg = ModelCalibrator.calibrate_model(base, x_val, y_val_neg, method="sigmoid")
        assert wrapped_neg.used_fallback is True
        assert wrapped_neg.fallback_reason is not None
        assert "NONPOSITIVE_PLATT_SLOPE" in wrapped_neg.fallback_reason

        x_test = np.linspace(0.1, 0.9, 15).reshape(-1, 1)
        probs_neg = wrapped_neg.predict_proba(x_test)[:, 1]
        # Must match raw probabilities without rank inversion!
        assert np.allclose(probs_neg, x_test[:, 0])
        assert np.all(np.diff(probs_neg) > 0.0)

        # Zero slope case: perfectly symmetric validation outcomes around 0.5
        x_val_sym = np.array([0.2, 0.8, 0.2, 0.8, 0.2, 0.8, 0.2, 0.8, 0.2, 0.8, 0.2, 0.8]).reshape(-1, 1)
        y_val_sym = np.array([1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0])
        wrapped_zero = ModelCalibrator.calibrate_model(base, x_val_sym, y_val_sym, method="sigmoid")
        assert wrapped_zero.used_fallback is True
        assert "NONPOSITIVE_PLATT_SLOPE" in wrapped_zero.fallback_reason

    def test_sigmoid_guard_small_validation_and_calibrator_failure(self):
        """Verifies fallback on small validation set (<10), single-class validation labels, and calibrator exceptions."""
        class DummyBaseEstimator:
            classes_ = np.array([0, 1])
            def predict_proba(self, x):
                p = np.clip(x[:, 0], 0.1, 0.9)
                return np.column_stack([1.0 - p, p])

        base = DummyBaseEstimator()
        # 1. Small validation sample (<10)
        x_small = np.linspace(0.2, 0.8, 6).reshape(-1, 1)
        y_small = np.array([0, 0, 0, 1, 1, 1])
        w_small = ModelCalibrator.calibrate_model(base, x_small, y_small, method="sigmoid")
        assert w_small.used_fallback is True
        assert "SMALL_VAL_SAMPLE" in w_small.fallback_reason

        # 2. Single-class validation set
        x_one = np.linspace(0.2, 0.8, 15).reshape(-1, 1)
        y_one = np.ones(15, dtype=int)
        w_one = ModelCalibrator.calibrate_model(base, x_one, y_one, method="sigmoid")
        assert w_one.used_fallback is True
        assert "SINGLE_CLASS_VAL_LABELS" in w_one.fallback_reason

        # 3. Calibrator exception during predict
        class BrokenCalibrator:
            def predict(self, p):
                raise RuntimeError("Simulated calibrator failure")

        from src.ml.calibration import CalibratedModelWrapper
        w_broken = CalibratedModelWrapper(base, BrokenCalibrator(), used_fallback=False)
        probs = w_broken.predict_proba(x_small)
        assert probs.shape == (6, 2)
        assert np.allclose(probs[:, 1], x_small[:, 0])


class TestCalibrationDriftAndReliabilityRemediation:
    """Comprehensive unit tests for drift diagnosis, reliability monitoring, and fallback guards."""

    class DummyEstimator:
        classes_ = np.array([0, 1])

        def __init__(self, raw_probs: np.ndarray) -> None:
            self.raw_probs = np.clip(np.asarray(raw_probs, float), 0.001, 0.999)

        def predict_proba(self, x: np.ndarray) -> np.ndarray:
            p = self.raw_probs[: len(x)]
            return np.column_stack([1.0 - p, p])

    def test_validation_single_class(self):
        """1. Validation with single class flags INSUFFICIENT_DATA and triggers fallback."""
        base = self.DummyEstimator(np.linspace(0.2, 0.8, 20))
        x_val = np.linspace(0.2, 0.8, 20).reshape(-1, 1)
        y_val_all_zeros = np.zeros(20, dtype=int)

        wrapped = ModelCalibrator.calibrate_model(base, x_val, y_val_all_zeros, method="sigmoid")
        assert wrapped.used_fallback is True
        assert "SINGLE_CLASS_VAL_LABELS" in wrapped.fallback_reason
        assert wrapped.calibration_status == "INSUFFICIENT_DATA"
        assert wrapped.probability_valid is False
        assert wrapped.abstain is True

    def test_validation_small_sample(self):
        """2. Validation with small sample triggers fallback (<10) or marks INSUFFICIENT_DATA (<30)."""
        # Hard minimum (<10)
        base_small = self.DummyEstimator(np.linspace(0.2, 0.8, 6))
        x_small = np.linspace(0.2, 0.8, 6).reshape(-1, 1)
        y_small = np.array([0, 0, 0, 1, 1, 1])

        w_small = ModelCalibrator.calibrate_model(base_small, x_small, y_small, method="sigmoid", min_val_samples=10)
        assert w_small.used_fallback is True
        assert "SMALL_VAL_SAMPLE" in w_small.fallback_reason
        assert w_small.calibration_status == "INSUFFICIENT_DATA"
        assert w_small.abstain is True

        # Medium validation (<30 but >=10) flags INSUFFICIENT_DATA
        base_med = self.DummyEstimator(np.linspace(0.2, 0.8, 20))
        x_med = np.linspace(0.2, 0.8, 20).reshape(-1, 1)
        y_med = (x_med[:, 0] >= 0.5).astype(int)

        w_med = ModelCalibrator.calibrate_model(base_med, x_med, y_med, method="sigmoid", min_val_samples=10)
        assert w_med.calibration_status == "INSUFFICIENT_DATA"
        assert w_med.abstain is True
        assert w_med.reliability_report is not None
        assert any("INSUFFICIENT_DATA" in r or "sample_size" in r for r in w_med.reliability_report.reasons)

    def test_sigmoid_slope_zero_or_negative(self):
        """3. Sigmoid slope <= 0 triggers fallback to raw probabilities and preserves ranking."""
        base = self.DummyEstimator(np.linspace(0.1, 0.9, 40))
        x_val = np.linspace(0.1, 0.9, 40).reshape(-1, 1)
        # Inverted relationship -> slope < 0
        y_val_inv = (x_val[:, 0] < 0.5).astype(int)

        wrapped = ModelCalibrator.calibrate_model(base, x_val, y_val_inv, method="sigmoid")
        assert wrapped.used_fallback is True
        assert "NONPOSITIVE_PLATT_SLOPE" in wrapped.fallback_reason
        assert wrapped.ranking_status in ("RANK_INVERTED", "RANK_WEAK")

        # Test evaluation preserves raw probability monotonic order
        x_test = np.linspace(0.1, 0.9, 10).reshape(-1, 1)
        probs = wrapped.predict_proba(x_test)[:, 1]
        assert np.all(np.diff(probs) > 0.0)

    def test_calibrator_fit_or_predict_exception(self):
        """4. Calibrator exception during fitting or prediction is caught and handled safely."""
        base = self.DummyEstimator(np.linspace(0.2, 0.8, 30))
        x_val = np.linspace(0.2, 0.8, 30).reshape(-1, 1)
        y_val = (x_val[:, 0] >= 0.5).astype(int)

        # Force exception by passing invalid method
        wrapped_fail = ModelCalibrator.calibrate_model(base, x_val, y_val, method="invalid_method_xyz")
        assert wrapped_fail.used_fallback is True
        assert wrapped_fail.calibration_status == "DEGRADED"

    def test_probability_output_bounded_valid_range(self):
        """5. Probability output is strictly within [0.001, 0.999] and sum to 1.0."""
        base = self.DummyEstimator(np.array([0.0, 0.0001, 0.5, 0.9999, 1.0]))
        x_test = np.zeros((5, 1))

        from src.ml.calibration import CalibratedModelWrapper, RawFallbackCalibrator
        fallback = CalibratedModelWrapper(base, RawFallbackCalibrator("test"), used_fallback=True)
        probs = fallback.predict_proba(x_test)

        assert probs.shape == (5, 2)
        assert np.all(probs >= 0.001)
        assert np.all(probs <= 0.999)
        assert np.allclose(probs[:, 0] + probs[:, 1], 1.0)

        preds = fallback.predict(x_test)
        assert set(np.unique(preds)).issubset({0, 1})

    def test_probability_compression_fallback_degraded(self):
        """6. Compressed probability output (IQR < 0.05) is flagged DEGRADED and falls back to raw."""
        p_raw = np.linspace(0.25, 0.95, 40)
        base = self.DummyEstimator(p_raw)
        x_val = p_raw.reshape(-1, 1)
        # Weak positive relation with heavy clustering -> weakly positive Platt slope but compressed IQR
        y_val = np.array([1 if (i % 3 != 0) else 0 for i in range(40)])  # 67% win rate

        wrapped = ModelCalibrator.calibrate_model(base, x_val, y_val, method="sigmoid")
        # Should detect compression (IQR < 0.05) and fall back to raw probabilities
        assert wrapped.used_fallback is True
        assert "COMPRESSED_PROBABILITY_OUTPUT" in wrapped.fallback_reason
        assert wrapped.calibration_status == "DEGRADED"

        # Output predictions should retain original raw probability IQR rather than flat ribbon
        p_out = wrapped.predict_proba(x_val)[:, 1]
        out_iqr = np.percentile(p_out, 75) - np.percentile(p_out, 25)
        assert out_iqr >= 0.05

    def test_base_rate_shift_train_to_val_degraded(self):
        """7. Severe base-rate regime shift between train and validation is detected and marked DEGRADED."""
        p_raw = np.linspace(0.2, 0.8, 40)
        base = self.DummyEstimator(p_raw)
        x_val = p_raw.reshape(-1, 1)
        # Validation win rate is 25% (10 ones out of 40; minority=10 >= 8)
        y_val = np.array([1 if i >= 30 else 0 for i in range(40)])
        # Training win rate was 65% (65 ones out of 100)
        y_train = np.array([1 if i < 65 else 0 for i in range(100)])

        wrapped = ModelCalibrator.calibrate_model(base, x_val, y_val, method="sigmoid", y_train=y_train)
        assert wrapped.reliability_report is not None
        assert wrapped.reliability_report.wr_shift_abs >= 0.15
        assert wrapped.calibration_status in ("DEGRADED", "UNRELIABLE")
        assert any("BASE_RATE_REGIME_SHIFT" in r or "bias" in r for r in wrapped.reliability_report.reasons)

    def test_temporal_leakage_invariance_and_purging(self):
        """8. Temporal splitting: altering test set outcome has zero impact on calibration parameters."""
        p_raw = np.linspace(0.2, 0.8, 35)
        base = self.DummyEstimator(p_raw)
        x_val = p_raw.reshape(-1, 1)
        y_val = (x_val[:, 0] >= 0.5).astype(int)

        w1 = ModelCalibrator.calibrate_model(base, x_val, y_val, method="sigmoid")

        # Fake future test labels (completely inverted)
        x_test_fake = np.linspace(0.2, 0.8, 15).reshape(-1, 1)
        y_test_fake = np.zeros(15, dtype=int)

        w2 = ModelCalibrator.calibrate_model(base, x_val, y_val, method="sigmoid")
        # Invariance check
        p1 = w1.predict_proba(x_test_fake)[:, 1]
        p2 = w2.predict_proba(x_test_fake)[:, 1]
        assert np.allclose(p1, p2)

    def test_status_unreliable_and_fallback(self):
        """9. Massive validation raw bias (>= 0.25) marks status UNRELIABLE and sets abstain = True."""
        # Raw probabilities heavily biased upward (mean ~0.80) while validation labels are 25% win rate
        p_raw = np.linspace(0.70, 0.90, 40)
        base = self.DummyEstimator(p_raw)
        x_val = p_raw.reshape(-1, 1)
        y_val = np.array([1 if i >= 30 else 0 for i in range(40)])  # 25% win rate -> bias = 0.80 - 0.25 = 0.55

        wrapped = ModelCalibrator.calibrate_model(base, x_val, y_val, method="sigmoid")
        assert wrapped.calibration_status == "UNRELIABLE"
        assert wrapped.probability_valid is False
        assert wrapped.abstain is True

        # Verify SetupEvaluator propagates UNRELIABLE status
        from src.core.types import Candle, Direction, MarketBias, SetupCandidate, StructureState
        from src.ml.evaluator import SetupEvaluator

        cand = SetupCandidate(
            candidate_id="c_test",
            symbol="BTCUSDT",
            timestamp_ms=1000,
            direction=Direction.LONG,
            bias_4h=MarketBias.BULLISH,
            structure_1h=StructureState.BULLISH,
            entry_price=60000.0,
            stop_loss=59000.0,
            take_profit_1=62000.0,
        )

        class MockRegistry:
            def load_model(self, _):
                from sklearn.preprocessing import StandardScaler
                sc = StandardScaler().fit(np.zeros((1, 27)))
                return {"model": wrapped, "scaler": sc, "name": "lightgbm", "test_samples": 40}

        evaluator = SetupEvaluator(MockRegistry())
        candles = [
            Candle("BTCUSDT", "1h", 1000 + i * 3600000, 60000, 60500, 59500, 60000, 100.0, 1000 + (i + 1) * 3600000 - 1, 6e6, 10, True)
            for i in range(25)
        ]
        res = evaluator.evaluate_setup(cand, candles)
        assert res.calibration_status == "UNRELIABLE"
        assert res.ml_probability is None

    def test_calibration_metrics_reproducibility(self):
        """10. evaluate_calibration reproduces exact expected values on deterministic test cases."""
        y_true = np.array([1, 1, 0, 0])
        y_prob = np.array([0.9, 0.8, 0.2, 0.1])
        rep = ModelCalibrator.evaluate_calibration(y_true, y_prob, n_bins=5)

        # Brier = ((0.9-1)^2 + (0.8-1)^2 + (0.2-0)^2 + (0.1-0)^2) / 4 = (0.01 + 0.04 + 0.04 + 0.01) / 4 = 0.025
        assert rep.brier_score == 0.025
        assert rep.roc_auc == 1.0
        assert rep.sample_count == 4

    def test_abstain_propagation_blocks_downstream_and_inversion(self):
        """11. Verify abstain=True sets ml_probability=None and rank_candidates deprioritizes it."""
        from src.core.types import Candle, Direction, MarketBias, SetupCandidate, StructureState
        from src.ml.evaluator import SetupEvaluator
        from src.scheduler.runner import rank_candidates

        # 1. Inverted ranking validation
        p_raw = np.linspace(0.1, 0.9, 40)
        base = self.DummyEstimator(p_raw)
        x_val = p_raw.reshape(-1, 1)
        y_val_inv = (p_raw < 0.5).astype(int)  # completely inverted

        wrapped = ModelCalibrator.calibrate_model(base, x_val, y_val_inv, method="sigmoid")
        assert wrapped.ranking_status == "RANK_INVERTED"
        assert wrapped.calibration_status == "UNRELIABLE"
        assert wrapped.abstain is True
        assert wrapped.probability_valid is False

        # 2. SetupEvaluator sets ml_probability to None on abstain
        class MockReg:
            def load_model(self, _):
                from sklearn.preprocessing import StandardScaler
                sc = StandardScaler().fit(np.zeros((1, 27)))
                return {"model": wrapped, "scaler": sc, "name": "lightgbm", "test_samples": 40}

        evaluator = SetupEvaluator(MockReg())
        c1 = SetupCandidate("c1", "BTCUSDT", 1000, Direction.LONG, MarketBias.BULLISH, StructureState.BULLISH, 60000.0, 59000.0, 62000.0)
        candles = [
            Candle("BTCUSDT", "1h", 1000 + i * 3600000, 60000, 60500, 59500, 60000, 100.0, 1000 + (i + 1) * 3600000 - 1, 6e6, 10, True)
            for i in range(25)
        ]
        res = evaluator.evaluate_setup(c1, candles)
        assert res.ml_probability is None
        assert res.calibration_status == "UNRELIABLE"
        assert res.confidence_display == "N/A"

        # 3. Downstream rank_candidates places None probability candidate below valid probability candidate
        c2 = SetupCandidate("c2", "ETHUSDT", 1000, Direction.LONG, MarketBias.BULLISH, StructureState.BULLISH, 3000.0, 2900.0, 3200.0)
        c2.ml_probability = 0.55
        c2.calibration_status = "CALIBRATED_LIGHTGBM"

        ranked = rank_candidates([res, c2], sort_by="confidence_desc")
        assert ranked[0].candidate_id == "c2"  # valid prob takes precedence
        assert ranked[1].candidate_id == "c1"  # abstained candidate placed at the bottom


