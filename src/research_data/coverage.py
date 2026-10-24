"""Source-aware coverage assessment; never forward-fills or invents observations."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List, Optional, Set

@dataclass
class CoverageAssessment:
    status: str
    requested_start: str
    requested_end: str
    earliest_observation: Optional[str]
    latest_observation: Optional[str]
    total_observations: int
    expected_observations: Optional[int]
    missing_expected_dates: List[str]
    unexpected_dates: List[str]
    coverage_ratio: Optional[float]
    explanation: str

def treasury_expected_dates(start: date, end: date, holidays: Iterable[date] = ()) -> Set[str]:
    excluded = set(holidays); result=set(); current=start
    while current <= end:
        if current.weekday() < 5 and current not in excluded: result.add(current.isoformat())
        current += timedelta(days=1)
    return result

def cot_expected_dates(start: date, end: date, anchor_weekday: int = 1) -> Set[str]:
    """Weekly COT report-date expectation; default Tuesday, not publication date."""
    if not 0 <= anchor_weekday <= 6: raise ValueError("invalid anchor weekday")
    current=start + timedelta(days=(anchor_weekday-start.weekday()) % 7); result=set()
    while current <= end: result.add(current.isoformat()); current += timedelta(days=7)
    return result

def assess_coverage(observed: Iterable[str], start: date, end: date, expected: Optional[Set[str]], explanation: str = "") -> CoverageAssessment:
    dates=set(observed); in_range={d for d in dates if start.isoformat() <= d <= end.isoformat()}
    if expected is None:
        return CoverageAssessment("UNKNOWN",start.isoformat(),end.isoformat(),min(dates) if dates else None,max(dates) if dates else None,len(dates),None,[],sorted(dates),None,explanation or "Expected schedule cannot be determined")
    if not in_range:
        return CoverageAssessment("MISSING",start.isoformat(),end.isoformat(),min(dates) if dates else None,max(dates) if dates else None,len(dates),0,sorted(expected),sorted(dates-set(expected)),0.0,explanation or "No observations in requested range")
    missing=sorted(expected-in_range); unexpected=sorted(in_range-expected); ratio=(len(expected)-len(missing))/len(expected) if expected else None
    status="COMPLETE" if not missing else "PARTIAL"
    return CoverageAssessment(status,start.isoformat(),end.isoformat(),min(dates),max(dates),len(dates),len(expected),missing,unexpected,ratio,explanation)
