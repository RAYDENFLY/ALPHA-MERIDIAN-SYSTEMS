"""Probability Calibration and Reliability Evaluation.

Computes Brier score, Log loss, Expected Calibration Error (ECE),
and fits Isotonic/Platt calibration on validation data.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple
import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


@dataclass
class CalibrationReport:
    """Quantitative calibration and reliability metrics."""
    brier_score: float
    log_loss_value: float
    roc_auc: float
    expected_calibration_error: float
    sample_count: int
    bin_centers: List[float]
    empirical_frequencies: List[float]


@dataclass
class ReliabilityReport:
    """Pre-specified calibration and ranking reliability diagnostics."""
    calibration_status: str       # "OK", "DEGRADED", "UNRELIABLE", "INSUFFICIENT_DATA"
    ranking_status: str           # "RANK_OK", "RANK_WEAK", "RANK_INVERTED", "RANK_UNKNOWN"
    probability_valid: bool       # True iff calibration_status == "OK"
    abstain: bool                 # True iff calibration_status in ("UNRELIABLE", "INSUFFICIENT_DATA")
    reasons: List[str]
    calib_auc: float | None = None
    raw_bias_abs: float | None = None
    wr_shift_abs: float | None = None
    output_iqr: float | None = None


class SigmoidWrapper:
    """Monotonic Platt scaling calibrator wrapper."""

    def __init__(self, model: LogisticRegression, slope: float, intercept: float) -> None:
        self.model = model
        self.slope = float(slope)
        self.intercept = float(intercept)

    def predict(self, p: np.ndarray) -> np.ndarray:
        p_arr = np.asarray(p, dtype=float).reshape(-1, 1)
        pos_idx = list(self.model.classes_).index(1)
        return self.model.predict_proba(p_arr)[:, pos_idx]


class RawFallbackCalibrator:
    """Identity fallback calibrator that preserves validated raw probabilities when post-hoc calibration is invalid."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def predict(self, p: np.ndarray) -> np.ndarray:
        return np.clip(np.asarray(p, dtype=float), 0.001, 0.999)


class CalibratedModelWrapper:
    """Wraps a trained base model with an Isotonic, Sigmoid, or RawFallback probability calibrator."""

    def __init__(
        self,
        base_estimator,
        calibrator,
        used_fallback: bool = False,
        fallback_reason: str | None = None,
        reliability_report: ReliabilityReport | None = None,
    ) -> None:
        self.base_estimator = base_estimator
        self.calibrator = calibrator
        self.used_fallback = bool(used_fallback)
        self.fallback_reason = fallback_reason
        self.reliability_report = reliability_report

    @property
    def calibration_status(self) -> str:
        rel = getattr(self, "reliability_report", None)
        if rel is not None:
            return rel.calibration_status
        return "DEGRADED" if getattr(self, "used_fallback", False) else "OK"

    @property
    def ranking_status(self) -> str:
        rel = getattr(self, "reliability_report", None)
        if rel is not None:
            return rel.ranking_status
        return "RANK_UNKNOWN"

    @property
    def probability_valid(self) -> bool:
        rel = getattr(self, "reliability_report", None)
        if rel is not None:
            return rel.probability_valid
        return not getattr(self, "used_fallback", False)

    @property
    def abstain(self) -> bool:
        rel = getattr(self, "reliability_report", None)
        if rel is not None:
            return rel.abstain
        return False

    def _extract_positive_raw_probs(self, x: np.ndarray) -> np.ndarray:
        proba = self.base_estimator.predict_proba(x)
        classes = getattr(self.base_estimator, "classes_", None)
        if classes is not None and 1 in list(classes):
            pos_idx = list(classes).index(1)
        else:
            pos_idx = 1 if proba.shape[1] > 1 else 0
        return np.clip(np.asarray(proba[:, pos_idx], dtype=float), 0.001, 0.999)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        raw_probs = self._extract_positive_raw_probs(x)
        if getattr(self, "used_fallback", False) or getattr(self, "calibrator", None) is None:
            cal_probs = raw_probs
        else:
            try:
                cal_probs = self.calibrator.predict(raw_probs)
            except Exception:
                cal_probs = raw_probs
        cal_probs = np.clip(np.asarray(cal_probs, dtype=float), 0.001, 0.999)
        return np.column_stack([1.0 - cal_probs, cal_probs])

    def predict(self, x: np.ndarray) -> np.ndarray:
        probs = self.predict_proba(x)[:, 1]
        return (probs >= 0.5).astype(int)


class ModelCalibrator:
    """Manages probability calibration and reliability diagnostics."""

    # Pre-specified thresholds (leakage-free; fixed prior to out-of-sample testing)
    MIN_VAL_SAMPLES_HARD: int = 10
    MIN_VAL_SAMPLES_RELIABLE: int = 30
    MIN_MINORITY_SAMPLES: int = 8
    MIN_COMPRESSED_IQR: float = 0.05
    BIAS_DEGRADED_THRESHOLD: float = 0.15
    BIAS_UNRELIABLE_THRESHOLD: float = 0.25
    WR_SHIFT_DEGRADED_THRESHOLD: float = 0.15
    RANK_OK_AUC_THRESHOLD: float = 0.55

    @staticmethod
    def evaluate_calibration(
        y_true: np.ndarray,
        y_prob: np.ndarray,
        n_bins: int = 5,
    ) -> CalibrationReport:
        """Calculate Brier score, log loss, AUC, and ECE."""
        y_true = np.asarray(y_true).astype(int)
        eps = 1e-6
        y_prob_clipped = np.clip(np.asarray(y_prob, dtype=float), eps, 1.0 - eps)

        brier = float(brier_score_loss(y_true, y_prob_clipped))
        try:
            ll = float(log_loss(y_true, y_prob_clipped, labels=[0, 1]))
        except Exception:
            ll = float(log_loss(y_true, y_prob_clipped))
        try:
            auc = float(roc_auc_score(y_true, y_prob_clipped)) if len(np.unique(y_true)) > 1 else 0.5
        except Exception:
            auc = 0.5

        try:
            prob_true, prob_pred = calibration_curve(y_true, y_prob_clipped, n_bins=n_bins, strategy="uniform")
        except Exception:
            prob_true, prob_pred = np.array([]), np.array([])

        # Expected Calibration Error (ECE)
        bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
        ece = 0.0
        n_total = len(y_true)

        for i in range(n_bins):
            if i == n_bins - 1:
                in_bin = (y_prob_clipped >= bin_edges[i]) & (y_prob_clipped <= bin_edges[i + 1])
            else:
                in_bin = (y_prob_clipped >= bin_edges[i]) & (y_prob_clipped < bin_edges[i + 1])
            bin_size = np.sum(in_bin)
            if bin_size > 0:
                bin_acc = np.mean(y_true[in_bin])
                bin_conf = np.mean(y_prob_clipped[in_bin])
                ece += (bin_size / n_total) * abs(bin_acc - bin_conf)

        return CalibrationReport(
            brier_score=round(brier, 4),
            log_loss_value=round(ll, 4),
            roc_auc=round(auc, 4),
            expected_calibration_error=round(float(ece), 4),
            sample_count=len(y_true),
            bin_centers=[round(float(x), 4) for x in prob_pred],
            empirical_frequencies=[round(float(x), 4) for x in prob_true],
        )

    @classmethod
    def calibrate_model(
        cls,
        base_estimator,
        x_val: np.ndarray,
        y_val: np.ndarray,
        method: str = "isotonic",
        min_val_samples: int = 10,
        y_train: np.ndarray | None = None,
    ) -> CalibratedModelWrapper:
        """Calibrate base model probabilities on validation set with positivity, compression, and drift guards."""
        from src.core.logger import logger

        y_val_arr = np.asarray(y_val).astype(int)
        proba = base_estimator.predict_proba(x_val)
        classes = getattr(base_estimator, "classes_", None)
        if classes is not None and 1 in list(classes):
            pos_idx = list(classes).index(1)
        else:
            pos_idx = 1 if proba.shape[1] > 1 else 0
        val_raw_probs = np.clip(np.asarray(proba[:, pos_idx], dtype=float), 0.001, 0.999)

        # Guard 1: Hard minimum sample size check
        if len(y_val_arr) < min_val_samples:
            reason = f"SMALL_VAL_SAMPLE: len(y_val)={len(y_val_arr)} < min_val_samples={min_val_samples}"
            logger.warning(f"Calibration fallback to validated raw probabilities ({reason}).")
            report = ReliabilityReport(
                calibration_status="INSUFFICIENT_DATA",
                ranking_status="RANK_UNKNOWN",
                probability_valid=False,
                abstain=True,
                reasons=[reason],
            )
            return CalibratedModelWrapper(
                base_estimator,
                RawFallbackCalibrator(reason),
                used_fallback=True,
                fallback_reason=reason,
                reliability_report=report,
            )

        unique_classes = np.unique(y_val_arr)
        if len(unique_classes) < 2:
            reason = f"SINGLE_CLASS_VAL_LABELS: only class {unique_classes.tolist()} present in y_val"
            logger.warning(f"Calibration fallback to validated raw probabilities ({reason}).")
            report = ReliabilityReport(
                calibration_status="INSUFFICIENT_DATA",
                ranking_status="RANK_UNKNOWN",
                probability_valid=False,
                abstain=True,
                reasons=[reason],
            )
            return CalibratedModelWrapper(
                base_estimator,
                RawFallbackCalibrator(reason),
                used_fallback=True,
                fallback_reason=reason,
                reliability_report=report,
            )

        if np.std(val_raw_probs) < 1e-8:
            reason = "DEGENERATE_RAW_PROBS: zero variance in validation raw probabilities"
            logger.warning(f"Calibration fallback to validated raw probabilities ({reason}).")
            report = ReliabilityReport(
                calibration_status="UNRELIABLE",
                ranking_status="RANK_UNKNOWN",
                probability_valid=False,
                abstain=True,
                reasons=[reason],
            )
            return CalibratedModelWrapper(
                base_estimator,
                RawFallbackCalibrator(reason),
                used_fallback=True,
                fallback_reason=reason,
                reliability_report=report,
            )

        # Pre-evaluation of sample sufficiency & ranking quality
        n_pos = int(np.sum(y_val_arr == 1))
        n_neg = int(np.sum(y_val_arr == 0))
        minority = min(n_pos, n_neg)
        is_insufficient = (len(y_val_arr) < cls.MIN_VAL_SAMPLES_RELIABLE or minority < cls.MIN_MINORITY_SAMPLES)

        try:
            val_auc = float(roc_auc_score(y_val_arr, val_raw_probs)) if len(unique_classes) > 1 else 0.5
        except Exception:
            val_auc = 0.5

        if val_auc >= cls.RANK_OK_AUC_THRESHOLD:
            ranking_status = "RANK_OK"
        elif val_auc >= 0.50:
            ranking_status = "RANK_WEAK"
        else:
            ranking_status = "RANK_INVERTED"

        reasons: List[str] = []
        if is_insufficient:
            reasons.append(f"INSUFFICIENT_DATA: n_val={len(y_val_arr)} < {cls.MIN_VAL_SAMPLES_RELIABLE} or minority={minority} < {cls.MIN_MINORITY_SAMPLES}")

        try:
            if method == "isotonic":
                calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99)
                calibrator.fit(val_raw_probs, y_val_arr)
                val_cal_probs = calibrator.predict(val_raw_probs)
                cal_iqr = float(np.percentile(val_cal_probs, 75) - np.percentile(val_cal_probs, 25))
                used_fallback = False
                fallback_reason = None
            elif method == "sigmoid":
                lr = LogisticRegression()
                lr.fit(val_raw_probs.reshape(-1, 1), y_val_arr)
                slope = float(lr.coef_[0][0])
                intercept = float(lr.intercept_[0])

                # Guard 2: Sigmoid positivity guard (prevent rank inversion when Platt slope a <= 0)
                if not np.isfinite(slope) or slope <= 0.0:
                    used_fallback = True
                    fallback_reason = f"NONPOSITIVE_PLATT_SLOPE: slope={slope:.6f} <= 0.0 (intercept={intercept:.6f})"
                    logger.warning(
                        f"Sigmoid calibration rejected due to nonpositive slope ({fallback_reason}); "
                        "falling back to validated raw probabilities to preserve ranking monotonicity."
                    )
                    calibrator = RawFallbackCalibrator(fallback_reason)
                    val_cal_probs = val_raw_probs
                    cal_iqr = float(np.percentile(val_cal_probs, 75) - np.percentile(val_cal_probs, 25))
                else:
                    pos_idx = list(lr.classes_).index(1)
                    val_cal_probs = lr.predict_proba(val_raw_probs.reshape(-1, 1))[:, pos_idx]
                    cal_iqr = float(np.percentile(val_cal_probs, 75) - np.percentile(val_cal_probs, 25))

                    # Guard 3: Compression Guard (Platt slope positive but small, destroying discriminative IQR)
                    if cal_iqr < cls.MIN_COMPRESSED_IQR:
                        used_fallback = True
                        fallback_reason = (
                            f"COMPRESSED_PROBABILITY_OUTPUT: val_iqr={cal_iqr:.4f} < {cls.MIN_COMPRESSED_IQR:.2f} "
                            f"(slope={slope:.4f})"
                        )
                        logger.warning(
                            f"Sigmoid calibration rejected due to probability compression ({fallback_reason}); "
                            "falling back to validated raw probabilities to preserve discriminative ranking."
                        )
                        calibrator = RawFallbackCalibrator(fallback_reason)
                    else:
                        used_fallback = False
                        fallback_reason = None
                        calibrator = SigmoidWrapper(lr, slope=slope, intercept=intercept)
            else:
                raise ValueError(f"Unknown calibration method: {method}")

            # Health diagnosis on calibration window & train-to-val shift
            raw_bias = abs(float(np.mean(val_raw_probs)) - float(np.mean(y_val_arr)))
            wr_shift = 0.0
            if y_train is not None and len(y_train) > 0:
                wr_shift = abs(float(np.mean(y_val_arr)) - float(np.mean(y_train)))

            if is_insufficient:
                cal_status = "INSUFFICIENT_DATA"
            elif ranking_status == "RANK_INVERTED":
                cal_status = "UNRELIABLE"
                reasons.append(f"INVERTED_RAW_RANKING: val_auc={val_auc:.4f} < 0.50")
            elif raw_bias >= cls.BIAS_UNRELIABLE_THRESHOLD:
                cal_status = "UNRELIABLE"
                reasons.append(f"EXCESSIVE_RAW_BIAS: bias={raw_bias:.3f} >= {cls.BIAS_UNRELIABLE_THRESHOLD}")
            elif raw_bias >= cls.BIAS_DEGRADED_THRESHOLD:
                cal_status = "DEGRADED"
                reasons.append(f"MODERATE_RAW_BIAS: bias={raw_bias:.3f} >= {cls.BIAS_DEGRADED_THRESHOLD}")
            elif wr_shift >= cls.WR_SHIFT_DEGRADED_THRESHOLD:
                cal_status = "DEGRADED"
                reasons.append(f"BASE_RATE_REGIME_SHIFT: shift={wr_shift:.3f} >= {cls.WR_SHIFT_DEGRADED_THRESHOLD}")
            elif used_fallback:
                cal_status = "DEGRADED"
                reasons.append(f"FALLBACK_ACTIVE: {fallback_reason}")
            else:
                cal_status = "OK"

            prob_valid = (cal_status == "OK" and ranking_status == "RANK_OK")
            abstain = (cal_status in ("UNRELIABLE", "INSUFFICIENT_DATA") or ranking_status == "RANK_INVERTED")

            reliability_report = ReliabilityReport(
                calibration_status=cal_status,
                ranking_status=ranking_status,
                probability_valid=prob_valid,
                abstain=abstain,
                reasons=reasons,
                calib_auc=round(val_auc, 4),
                raw_bias_abs=round(raw_bias, 4),
                wr_shift_abs=round(wr_shift, 4),
                output_iqr=round(cal_iqr, 4),
            )

            return CalibratedModelWrapper(
                base_estimator,
                calibrator,
                used_fallback=used_fallback,
                fallback_reason=fallback_reason,
                reliability_report=reliability_report,
            )

        except Exception as exc:
            reason = f"CALIBRATOR_FIT_EXCEPTION: {type(exc).__name__}: {exc}"
            logger.warning(f"Calibration failed ({reason}); falling back to validated raw probabilities.")
            report = ReliabilityReport(
                calibration_status="DEGRADED",
                ranking_status="RANK_UNKNOWN",
                probability_valid=False,
                abstain=False,
                reasons=[reason],
            )
            return CalibratedModelWrapper(
                base_estimator,
                RawFallbackCalibrator(reason),
                used_fallback=True,
                fallback_reason=reason,
                reliability_report=report,
            )

