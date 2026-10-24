from datetime import date
from pathlib import Path
from src.data.database import DatabaseManager
from src.research_data.audit import audit_dataset
from src.research_data.storage import ResearchDataStore
from src.research_data.types import ResearchObservation

def test_persisted_audit_reports_partial_coverage_and_invalid_numeric():
    db=DatabaseManager(Path(f".pytest-audit-{id(object())}.db")); store=ResearchDataStore(db)
    store.insert_observations([ResearchObservation("treasury","treasury_nominal","US","10Y","2024-01-02",None,None,"not-a-number","percent","u","DELAYED","VALID","audit")])
    result=audit_dataset(db,"treasury_nominal",date(2024,1,1),date(2024,1,5))
    assert result["quality_status"]=="INVALID" and result["coverage"]["status"]=="INVALID"

def test_unknown_dataset_schedule_is_not_complete():
    db=DatabaseManager(Path(f".pytest-audit-unknown-{id(object())}.db")); result=audit_dataset(db,"future_dataset",date(2024,1,1),date(2024,1,5))
    assert result["coverage"]["status"]=="UNKNOWN"
