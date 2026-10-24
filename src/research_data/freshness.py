from datetime import datetime, timedelta, timezone
from typing import Optional

CFTC_FRESH_DAYS = 10.0
CFTC_AGING_DAYS = 21.0

def dataset_freshness(latest_observation: Optional[str], reference_time: datetime, cadence_days: Optional[float], publication_lag_days: float = 0.0) -> str:
    if not latest_observation: return "MISSING"
    if cadence_days is None: return "UNKNOWN"
    if reference_time.tzinfo is None: reference_time = reference_time.replace(tzinfo=timezone.utc)
    try: observed = datetime.fromisoformat(latest_observation.replace("Z", "+00:00"))
    except ValueError: return "INVALID"
    if observed.tzinfo is None: observed = observed.replace(tzinfo=timezone.utc)
    return "FRESH" if reference_time - observed <= timedelta(days=cadence_days + publication_lag_days) else "STALE"


def cftc_freshness(observation_date: Optional[str], reference_time: datetime) -> str:
    """Classify CFTC report-date age; retrieval time is intentionally not used."""
    if not observation_date:
        return "UNKNOWN"
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    try:
        observed = datetime.fromisoformat(str(observation_date)[:10]).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "UNKNOWN"
    age = reference_time - observed
    if age.total_seconds() < 0:
        return "UNKNOWN"
    if age <= timedelta(days=CFTC_FRESH_DAYS):
        return "FRESH"
    if age <= timedelta(days=CFTC_AGING_DAYS):
        return "AGING"
    return "STALE"
