from datetime import datetime, timezone
from src.research_data.freshness import dataset_freshness

def test_freshness_is_independent_and_deterministic():
    ref=datetime(2024,1,3,tzinfo=timezone.utc)
    assert dataset_freshness("2024-01-02",ref,1)=="FRESH"
    assert dataset_freshness("2023-12-01",ref,1)=="STALE"
    assert dataset_freshness(None,ref,1)=="MISSING"
    assert dataset_freshness("2024-01-02",ref,None)=="UNKNOWN"
