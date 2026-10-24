import json
from pathlib import Path

from src.data.database import DatabaseManager


def test_model_a_funnel_persists_and_aggregates_without_double_counting(tmp_path: Path):
    db = DatabaseManager(tmp_path / "funnel.db")
    base = {"scan_id": "SCAN-1", "symbol": "BTCUSDT", "scan_started_at_utc": "2026-10-22T00:00:00+00:00", "scan_completed_at_utc": "2026-10-22T00:00:01+00:00", "market_candle_timestamp_ms": 100, "market_candle_close_time_ms": 200, "freshness_status": "LIVE", "history_status": "PASS", "data_quality_status": "PASS", "predicates": {"data_quality": "PASS", "history_sufficiency": "PASS", "equilibrium_zone": "FAIL", "smc_confluence": "NOT_EVALUATED", "reward_risk": "NOT_EVALUATED"}, "final_status": "NO_SETUP", "first_rejection_reason": "EQUILIBRIUM_ZONE_72H"}
    db.save_model_a_funnel(base)
    db.save_model_a_funnel({**base, "symbol": "ETHUSDT", "predicates": {"data_quality": "PASS", "history_sufficiency": "PASS", "equilibrium_zone": "PASS", "smc_confluence": "PASS", "reward_risk": "PASS"}, "final_status": "READY", "first_rejection_reason": None})
    report = db.aggregate_model_a_funnel()
    assert report["rows"] == 2
    assert report["first_rejection_counts"] == {"EQUILIBRIUM_ZONE_72H": 1}
    assert report["predicate_counts"]["smc_confluence"] == {"PASS": 1, "FAIL": 0, "NOT_EVALUATED": 1}
    assert report["ready_count"] == 1


def test_model_a_funnel_row_is_idempotent_by_scan_and_symbol(tmp_path: Path):
    db = DatabaseManager(tmp_path / "funnel.db")
    record = {"scan_id": "SCAN-1", "symbol": "BTCUSDT", "scan_started_at_utc": "t", "final_status": "NO_SETUP", "predicates": {"data_quality": "PASS"}}
    db.save_model_a_funnel(record)
    db.save_model_a_funnel({**record, "final_status": "READY"})
    assert db.aggregate_model_a_funnel()["rows"] == 1
    assert db.aggregate_model_a_funnel()["ready_count"] == 1
