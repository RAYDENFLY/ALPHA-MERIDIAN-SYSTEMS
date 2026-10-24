import asyncio
from unittest.mock import Mock
from src.research_data.http_sources import PublicResearchHTTP
from src.research_data.scheduler import ResearchDataScheduler

def test_http_429_becomes_transient_error():
    s=Mock(); r=Mock(status_code=429, headers={"Retry-After":"2"}, text="rate limited"); s.get.return_value=r
    try: PublicResearchHTTP(session=s)._get("https://example.test")
    except Exception as e: assert getattr(e,"retry_after",None)==2.0
    else: assert False

def test_scheduler_deduplicates_same_dataset():
    async def run():
        scheduler=ResearchDataScheduler(Mock(), interval_seconds=3600)
        scheduler.ingestor.ingest_chunk=Mock(return_value=0)
        scheduler.http.treasury_nominal=lambda: []
        first=await asyncio.gather(scheduler.sync_dataset("treasury_nominal"), scheduler.sync_dataset("treasury_nominal"))
        assert "ALREADY_RUNNING" in first
    asyncio.run(run())
