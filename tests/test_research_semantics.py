from datetime import datetime, timezone
from src.research_data.freshness import dataset_freshness, cftc_freshness

def test_freshness_is_independent_and_deterministic():
    ref=datetime(2024,1,3,tzinfo=timezone.utc)
    assert dataset_freshness("2024-01-02",ref,1)=="FRESH"
    assert dataset_freshness("2023-12-01",ref,1)=="STALE"
    assert dataset_freshness(None,ref,1)=="MISSING"
    assert dataset_freshness("2024-01-02",ref,None)=="UNKNOWN"

def test_cftc_freshness_uses_observation_date_only():
    ref=datetime(2024,1,22,tzinfo=timezone.utc)
    assert cftc_freshness("2024-01-20", ref) == "FRESH"
    assert cftc_freshness("2024-01-05", ref) == "AGING"
    assert cftc_freshness("2023-12-01", ref) == "STALE"
    assert cftc_freshness(None, ref) == "UNKNOWN"
    assert cftc_freshness("not-a-date", ref) == "UNKNOWN"
    assert cftc_freshness("2024-02-01", ref) == "UNKNOWN"
