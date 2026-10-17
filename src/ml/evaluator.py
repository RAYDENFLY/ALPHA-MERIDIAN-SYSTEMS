"""Setup Evaluator for Live & Paper Inference.

Scores candidate setups using locally trained, calibrated ML models.
Attaches calibrated probability, calibration status, and sample support.
"""

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from src.core.types import Candle, SetupCandidate
from src.ml.models import ModelRegistry
from src.ml.pipeline import FEATURE_NAMES, FeaturePipeline


class SetupEvaluator:
    """Evaluates candidate setups with local calibrated ML model."""

    def __init__(self, model_registry: ModelRegistry, model_filename: str = "calibrated_model.joblib") -> None:
        self.registry = model_registry
        self.model_bundle = self.registry.load_model(model_filename)

    def evaluate_setup(
        self,
        candidate: SetupCandidate,
        candles_1h: List[Candle],
        funding_rate: float = 0.0,
    ) -> SetupCandidate:
        """Score candidate setup using calibrated local model."""
        if self.model_bundle is None:
            candidate.ml_probability = None
            candidate.calibration_status = "INSUFFICIENT_DATA"
            candidate.eval_sample_count = 0
            return candidate

        model = self.model_bundle["model"]
        scaler = self.model_bundle["scaler"]
        model_name = self.model_bundle.get("name", "calibrated_model")

        # Extract features
        feats_dict = FeaturePipeline.extract_features(candidate, candles_1h, funding_rate)
        df_feats = FeaturePipeline.to_dataframe([feats_dict])
        x_scaled = scaler.transform(df_feats.values)

        # Calibrated probability
        prob = float(model.predict_proba(x_scaled)[0, 1])

        rel_report = getattr(model, "reliability_report", None)
        if rel_report is not None:
            if rel_report.calibration_status == "OK":
                candidate.calibration_status = f"CALIBRATED_{model_name.upper()}"
            elif rel_report.calibration_status == "DEGRADED":
                candidate.calibration_status = f"DEGRADED_{model_name.upper()}"
            elif rel_report.calibration_status == "UNRELIABLE":
                candidate.calibration_status = "UNRELIABLE"
            else:
                candidate.calibration_status = "INSUFFICIENT_DATA"

            if rel_report.abstain:
                candidate.ml_probability = None
            else:
                candidate.ml_probability = round(prob, 4)
        else:
            candidate.calibration_status = f"CALIBRATED_{model_name.upper()}"
            candidate.ml_probability = round(prob, 4)

        candidate.eval_sample_count = self.model_bundle.get("test_samples", 100)

        return candidate
