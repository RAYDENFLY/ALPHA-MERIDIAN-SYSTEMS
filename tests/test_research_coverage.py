from datetime import date
from src.research_data.coverage import assess_coverage, cot_expected_dates, treasury_expected_dates

def test_treasury_weekends_and_holidays_are_not_expected():
    expected=treasury_expected_dates(date(2024,1,1),date(2024,1,5),[date(2024,1,1)])
    result=assess_coverage(expected,date(2024,1,1),date(2024,1,5),expected)
    assert result.status=="COMPLETE" and result.expected_observations==4

def test_partial_and_unknown_coverage_are_distinct():
    start,end=date(2024,1,1),date(2024,1,31)
    expected=cot_expected_dates(start,end)
    partial=assess_coverage(sorted(expected)[:-1],start,end,expected)
    unknown=assess_coverage(["2024-01-03"],start,end,None)
    assert partial.status=="PARTIAL" and partial.coverage_ratio is not None
    assert unknown.status=="UNKNOWN" and unknown.coverage_ratio is None

def test_empty_range_is_missing_not_available():
    result=assess_coverage([],date(2024,1,1),date(2024,1,5),treasury_expected_dates(date(2024,1,1),date(2024,1,5)))
    assert result.status=="MISSING"
