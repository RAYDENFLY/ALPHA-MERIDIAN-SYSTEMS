import asyncio
from datetime import date
from pathlib import Path
from unittest.mock import Mock
from src.data.database import DatabaseManager
from src.research_data.audit import audit_dataset
from src.research_data.scheduler import ResearchDataScheduler

def test_unknown_empty_dataset_is_unknown_not_missing():
    result=audit_dataset(DatabaseManager(Path(f".pytest-op-{id(object())}.db")),"unregistered",date(2024,1,1),date(2024,1,5))
    assert result["coverage"]["status"]=="UNKNOWN"

def test_scheduler_source_failure_isolated_and_shutdown_clean():
    async def run():
        scheduler=ResearchDataScheduler(Mock(),interval_seconds=3600)
        scheduler.http.treasury_nominal=lambda: (_ for _ in ()).throw(RuntimeError("failure"))
        scheduler.http.cftc=lambda: []
        assert await scheduler.sync_dataset("treasury_nominal")=="ERROR"
        assert await scheduler.sync_dataset("cftc_cot_disaggregated")=="AVAILABLE"
        assert scheduler.start() is True
        assert scheduler.start() is False
        await scheduler.stop()
        assert scheduler.task is None
    asyncio.run(run())
