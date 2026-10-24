"""Read-only contract for Signal Generator research consumers."""
from datetime import date
from typing import Any, Dict
from src.data.database import DatabaseManager
from src.research_data.audit import audit_dataset
from src.research_data.storage import ResearchDataStore

class ResearchDataAccess:
    def __init__(self, db: DatabaseManager): self.db=db; self.store=ResearchDataStore(db)
    def get_range(self, dataset_id: str, start: date, end: date) -> Dict[str, Any]:
        if start > end: raise ValueError("start date must not be after end date")
        with self.db.get_connection() as conn:
            rows=[dict(r) for r in conn.execute("SELECT * FROM research_observations WHERE dataset_id=? AND observation_timestamp>=? AND observation_timestamp<=? ORDER BY observation_timestamp,symbol",(dataset_id,start.isoformat(),end.isoformat()))]
        return {"dataset_id":dataset_id,"requested_start":start.isoformat(),"requested_end":end.isoformat(),"observations":rows,"read_only":True}
    def readiness(self, dataset_id: str, start: date, end: date) -> Dict[str, Any]:
        data=self.get_range(dataset_id,start,end); audit=audit_dataset(self.db,dataset_id,start,end)
        sync=self.store.get_sync_state(dataset_id)
        return {"dataset_id":dataset_id,"availability":"AVAILABLE" if data["observations"] else "MISSING","quality":audit["quality_status"],"coverage":audit["coverage"]["status"],"freshness":"UNKNOWN","observations_count":len(data["observations"]),"audit":audit,"sync":sync,"read_only":True}
