"""CLI Script to extract dataset, train, calibrate, and save local ML models.
"""

import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_default_config
from src.core.logger import logger
from src.data.database import DatabaseManager
from src.ml.labeling import SetupLabeler
from src.ml.models import ModelRegistry
from src.ml.pipeline import FeaturePipeline
from src.strategy.generator import SetupGenerator


def main():
    cfg = get_default_config()
    db = DatabaseManager(cfg.db_path)
    registry = ModelRegistry(cfg.data_dir / "models")
    generator = SetupGenerator()

    horizon_bars = max(int(getattr(cfg.ml, "label_horizon_bars", 48)), 48)
    logger.info(
        f"Extracting candidate setups and labels from 365-day historical dataset "
        f"(horizon={horizon_bars}h)..."
    )

    candidate_records = []
    outcomes_breakdown = {"TP_HIT": 0, "SL_HIT": 0, "SL_COLLISION": 0, "TIMEOUT": 0}

    for sym in cfg.enabled_symbols:
        logger.info(f"Scanning history for {sym}...")
        c1_list = db.get_candles(sym, "1h")
        c4_list = db.get_candles(sym, "4h")

        if len(c1_list) < 175 + horizon_bars:
            continue

        funding_rates = db.get_funding_rates(sym)
        funding_map = {r["funding_time_ms"]: float(r["funding_rate"]) for r in funding_rates}

        # Step bar-by-bar (1H) to capture all high-precision Dealing Range setups
        for i in range(170, len(c1_list) - horizon_bars, 1):
            candidate = generator.generate_candidate(
                symbol=sym,
                candles_1h=c1_list,
                candles_4h=c4_list,
                as_of_index_1h=i,
            )
            if candidate is None:
                continue

            forward_slice = c1_list[i + 1 :]
            outcome = SetupLabeler.label_candidate(
                candidate=candidate,
                forward_candles=forward_slice,
                horizon_bars=horizon_bars,
            )

            outcomes_breakdown[outcome.outcome_reason] = (
                outcomes_breakdown.get(outcome.outcome_reason, 0) + 1
            )

            recent_funding = funding_map.get(candidate.timestamp_ms, 0.0)
            feats = FeaturePipeline.extract_features(
                candidate, c1_list[: i + 1], funding_rate=recent_funding
            )
            candidate_records.append(
                {
                    "timestamp_ms": int(candidate.timestamp_ms),
                    "symbol": sym,
                    "features": feats,
                    "target_hit": int(outcome.target_hit),
                }
            )

    # Sort globally by timestamp_ms across all symbols before splitting
    candidate_records.sort(key=lambda r: (r["timestamp_ms"], r["symbol"]))

    all_features = [r["features"] for r in candidate_records]
    all_labels = [r["target_hit"] for r in candidate_records]
    all_timestamps = [r["timestamp_ms"] for r in candidate_records]

    logger.info(f"Total labeled candidates extracted: {len(all_labels)}")
    logger.info(f"Outcome breakdown: {outcomes_breakdown}")

    if len(all_labels) < cfg.ml.min_samples_required:
        logger.warning(
            f"Insufficient samples ({len(all_labels)} < {cfg.ml.min_samples_required}). "
            "ML confidence will report INSUFFICIENT_DATA until more data is generated."
        )

    df_feats = FeaturePipeline.to_dataframe(all_features)
    y_arr = np.array(all_labels)

    n_total = len(y_arr)
    train_end = int(n_total * cfg.ml.train_split)
    val_end = int(n_total * (cfg.ml.train_split + cfg.ml.val_split))

    split_epochs = {}
    if n_total > 0 and train_end > 0 and val_end > train_end and n_total > val_end:
        split_epochs = {
            "train_start_ms": all_timestamps[0],
            "train_end_ms": all_timestamps[train_end - 1],
            "val_start_ms": all_timestamps[train_end],
            "val_end_ms": all_timestamps[val_end - 1],
            "test_start_ms": all_timestamps[val_end],
            "test_end_ms": all_timestamps[-1],
        }
        logger.info(f"Chronological split boundaries (ms): {split_epochs}")

    logger.info("Training and calibrating ML models chronologically...")
    best_model, summaries = registry.train_and_evaluate(
        features_df=df_feats,
        labels=y_arr,
        train_split=cfg.ml.train_split,
        val_split=cfg.ml.val_split,
        calibration_method=cfg.ml.calibration_method,
    )

    if best_model is not None:
        best_model["test_samples"] = n_total - val_end
        best_model["label_horizon_bars"] = horizon_bars
        best_model["split_epochs"] = split_epochs
        registry.save_model(best_model, "calibrated_model.joblib")

        report_data = {
            "total_candidates": n_total,
            "label_horizon_bars": horizon_bars,
            "split_epochs": split_epochs,
            "outcomes": outcomes_breakdown,
            "models": {
                name: {
                    "train_samples": s.train_samples,
                    "val_samples": s.val_samples,
                    "test_samples": s.test_samples,
                    "uncalibrated": {
                        "brier_score": s.uncalibrated_report.brier_score,
                        "log_loss": s.uncalibrated_report.log_loss_value,
                        "roc_auc": s.uncalibrated_report.roc_auc,
                        "ece": s.uncalibrated_report.expected_calibration_error,
                    },
                    "calibrated": {
                        "brier_score": s.calibrated_report.brier_score,
                        "log_loss": s.calibrated_report.log_loss_value,
                        "roc_auc": s.calibrated_report.roc_auc,
                        "ece": s.calibrated_report.expected_calibration_error,
                    },
                }
                for name, s in summaries.items()
            },
        }

        report_path = cfg.data_dir / "models" / "training_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)

        logger.info(f"Saved training report to {report_path}")
        print("\n=== MODEL TRAINING AND CALIBRATION REPORT ===")
        print(json.dumps(report_data, indent=2))


if __name__ == "__main__":
    main()