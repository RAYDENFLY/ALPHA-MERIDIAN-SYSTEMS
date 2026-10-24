"""Read-only persisted research-data quality audit."""
from datetime import date
import math
from typing import Any, Dict, Optional
from src.data.database import DatabaseManager
from src.research_data.coverage import assess_coverage, cot_expected_dates, treasury_expected_dates

def audit_dataset(db: DatabaseManager, dataset_id: str, start: date, end: date, holidays=()) -> Dict[str, Any]:
    with db.get_connection() as conn:
        rows=[dict(r) for r in conn.execute("SELECT * FROM research_observations WHERE dataset_id=? AND observation_timestamp>=? AND observation_timestamp<=? ORDER BY observation_timestamp",(dataset_id,start.isoformat(),end.isoformat()))]
    violations=[]; dates=[]
    for row in rows:
        value=row.get("value")
        try:
            parsed=date.fromisoformat(row["observation_timestamp"][:10]); dates.append(parsed.isoformat())
        except (TypeError,ValueError): violations.append({"type":"INVALID_DATE","id":row.get("id")})
        if value is not None and (not isinstance(value,(int,float)) or not math.isfinite(float(value))): violations.append({"type":"INVALID_NUMERIC","id":row.get("id")})
    if dataset_id.startswith("treasury"):
        expected=treasury_expected_dates(start,end,holidays); policy="TREASURY_WEEKDAYS_EXPLICIT_HOLIDAYS"
    elif dataset_id.startswith("cftc"):
        expected=cot_expected_dates(start,end); policy="CFTC_WEEKLY_REPORT_DATE"
    else:
        expected=None; policy="UNKNOWN"
    coverage=assess_coverage(dates,start,end,expected,policy)
    if violations: coverage.status="INVALID"
    return {"dataset_id":dataset_id,"requested_start":start.isoformat(),"requested_end":end.isoformat(),"coverage":coverage.__dict__,"violations":violations,"quality_status":"INVALID" if violations else "VALID","read_only":True}
