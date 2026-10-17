"""Model Training, Validation, and Registry.

Compares Logistic Regression baseline vs LightGBM with chronological 60/20/20 splits
and probability calibration without data leakage.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb

from src.core.logger import logger
from src.ml.calibration import CalibrationReport, ModelCalibrator
from src.ml.pipeline import FEATURE_NAMES


@dataclass
class ModelEvaluationSummary:
    """Out-of-sample comparative evaluation summary."""
    model_name: str
    train_samples: int
    val_samples: int
    test_samples: int
    uncalibrated_report: CalibrationReport
    calibrated_report: CalibrationReport


class ModelRegistry:
    """Manages training, calibration, and disk serialization of setup evaluators."""

    def __init__(self, models_dir: Path = Path("./data/models")) -> None:
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def train_and_evaluate(
        self,
        features_df: pd.DataFrame,
        labels: np.ndarray,
        train_split: float = 0.60,
        val_split: float = 0.20,
        calibration_method: str = "isotonic",
    ) -> Tuple[Any, Dict[str, ModelEvaluationSummary]]:
        """Train Logistic Regression and LightGBM models chronologically."""
        n = len(features_df)
        if n < 50:
            logger.warning(f"Insufficient samples for ML training ({n} < 50).")
            return None, {}

        # 1. Chronological splitting (no random shuffling!)
        train_end = int(n * train_split)
        val_end = int(n * (train_split + val_split))

        x_train = features_df.iloc[:train_end].values
        y_train = labels[:train_end]

        x_val = features_df.iloc[train_end:val_end].values
        y_val = labels[train_end:val_end]

        x_test = features_df.iloc[val_end:].values
        y_test = labels[val_end:]

        logger.info(f"Chronological split sizes: Train={len(x_train)}, Val={len(x_val)}, Test={len(x_test)}")

        # 2. Fit Scaler strictly on Training set
        scaler = StandardScaler()
        x_train_scaled = scaler.fit_transform(x_train)
        x_val_scaled = scaler.transform(x_val)
        x_test_scaled = scaler.transform(x_test)

        summaries: Dict[str, ModelEvaluationSummary] = {}
        best_model = None
        best_brier = 999.0

        # Model A: Baseline Logistic Regression
        lr_base = LogisticRegression(max_iter=500, class_weight="balanced", random_state=42)
        lr_base.fit(x_train_scaled, y_train)

        # Calibrate on validation set
        try:
            lr_calibrated = ModelCalibrator.calibrate_model(
                lr_base, x_val_scaled, y_val, method=calibration_method, y_train=y_train
            )

            # Validation evaluation strictly for model selection (LEAKAGE-FREE)
            y_val_prob_lr_cal = lr_calibrated.predict_proba(x_val_scaled)[:, 1]
            val_rep_cal_lr = ModelCalibrator.evaluate_calibration(y_val, y_val_prob_lr_cal)
            val_brier_lr = val_rep_cal_lr.brier_score

            # Final frozen test set evaluation strictly for reporting (NOT for model selection)
            y_test_prob_lr_raw = lr_base.predict_proba(x_test_scaled)[:, 1]
            y_test_prob_lr_cal = lr_calibrated.predict_proba(x_test_scaled)[:, 1]

            rep_uncal_lr = ModelCalibrator.evaluate_calibration(y_test, y_test_prob_lr_raw)
            rep_cal_lr = ModelCalibrator.evaluate_calibration(y_test, y_test_prob_lr_cal)

            summaries["logistic_regression"] = ModelEvaluationSummary(
                model_name="logistic_regression",
                train_samples=len(x_train),
                val_samples=len(x_val),
                test_samples=len(x_test),
                uncalibrated_report=rep_uncal_lr,
                calibrated_report=rep_cal_lr,
            )

            # Select model strictly on validation set Brier score
            if val_brier_lr < best_brier:
                best_brier = val_brier_lr
                best_model = {"model": lr_calibrated, "scaler": scaler, "name": "logistic_regression"}
        except Exception as e:
            logger.warning(f"Logistic regression calibration failed: {e}")

        # Model B: LightGBM Classifier
        lgb_base = lgb.LGBMClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.03,
            min_child_samples=max(5, int(len(x_train) * 0.05)),
            random_state=42,
            verbose=-1,
        )
        lgb_base.fit(x_train_scaled, y_train)

        try:
            lgb_calibrated = ModelCalibrator.calibrate_model(
                lgb_base, x_val_scaled, y_val, method=calibration_method, y_train=y_train
            )

            # Validation evaluation strictly for model selection (LEAKAGE-FREE)
            y_val_prob_lgb_cal = lgb_calibrated.predict_proba(x_val_scaled)[:, 1]
            val_rep_cal_lgb = ModelCalibrator.evaluate_calibration(y_val, y_val_prob_lgb_cal)
            val_brier_lgb = val_rep_cal_lgb.brier_score

            # Final frozen test set evaluation strictly for reporting (NOT for model selection)
            y_test_prob_lgb_raw = lgb_base.predict_proba(x_test_scaled)[:, 1]
            y_test_prob_lgb_cal = lgb_calibrated.predict_proba(x_test_scaled)[:, 1]

            rep_uncal_lgb = ModelCalibrator.evaluate_calibration(y_test, y_test_prob_lgb_raw)
            rep_cal_lgb = ModelCalibrator.evaluate_calibration(y_test, y_test_prob_lgb_cal)

            summaries["lightgbm"] = ModelEvaluationSummary(
                model_name="lightgbm",
                train_samples=len(x_train),
                val_samples=len(x_val),
                test_samples=len(x_test),
                uncalibrated_report=rep_uncal_lgb,
                calibrated_report=rep_cal_lgb,
            )

            # Select model strictly on validation set Brier score
            if val_brier_lgb < best_brier:
                best_brier = val_brier_lgb
                best_model = {"model": lgb_calibrated, "scaler": scaler, "name": "lightgbm"}
        except Exception as e:
            logger.warning(f"LightGBM calibration failed: {e}")

        return best_model, summaries

    def save_model(self, model_bundle: Dict[str, Any], filename: str = "calibrated_model.joblib") -> Path:
        target = self.models_dir / filename
        joblib.dump(model_bundle, target)
        logger.info(f"Saved model bundle to {target}")
        return target

    def load_model(self, filename: str = "calibrated_model.joblib") -> Optional[Dict[str, Any]]:
        target = self.models_dir / filename
        if not target.exists():
            return None
        return joblib.load(target)
