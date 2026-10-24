from datetime import datetime, timedelta, timezone
from typing import Optional

def dataset_freshness(latest_observation: Optional[str], reference_time: datetime, cadence_days: Optional[float], publication_lag_days: float = 0.0) -> str:
    if not latest_observation: return "MISSING"
    if cadence_days is None: return "UNKNOWN"
    if reference_time.tzinfo is None: reference_time = reference_time.replace(tzinfo=timezone.utc)
    try: observed = datetime.fromisoformat(latest_observation.replace("Z", "+00:00"))
    except ValueError: return "INVALID"
    if observed.tzinfo is None: observed = observed.replace(tzinfo=timezone.utc)
    return "FRESH" if reference_time - observed <= timedelta(days=cadence_days + publication_lag_days) else "STALE"
