"""Optional research-only periodic synchronization worker."""
import asyncio
from datetime import datetime, timezone
from typing import Optional
from src.core.logger import logger
from src.research_data.http_sources import PublicResearchHTTP
from src.research_data.ingestion import ResearchIngestor, RetryPolicy
from src.research_data.storage import ResearchDataStore

class ResearchDataScheduler:
    def __init__(self, store: ResearchDataStore, interval_seconds: float = 3600.0, http: Optional[PublicResearchHTTP] = None):
        self.store=store; self.interval_seconds=max(60.0,float(interval_seconds)); self.http=http or PublicResearchHTTP(); self.ingestor=ResearchIngestor(store); self.task=None; self._running=False; self._locks={"treasury_nominal": asyncio.Lock(), "cftc_cot_disaggregated": asyncio.Lock()}
    async def sync_dataset(self, dataset_id: str) -> str:
        lock=self._locks[dataset_id]
        if lock.locked(): return "ALREADY_RUNNING"
        async with lock:
            try:
                fetch = self.http.treasury_nominal if dataset_id == "treasury_nominal" else self.http.cftc
                await asyncio.to_thread(self.ingestor.ingest_chunk, "treasury" if dataset_id == "treasury_nominal" else "cftc", dataset_id, fetch, RetryPolicy())
                return "AVAILABLE"
            except Exception as exc:
                logger.warning("Research sync failed dataset=%s category=%s", dataset_id, type(exc).__name__)
                return "ERROR"
    async def _loop(self):
        while self._running:
            await asyncio.gather(self.sync_dataset("treasury_nominal"), self.sync_dataset("cftc_cot_disaggregated"), return_exceptions=True)
            try: await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError: break
    def start(self):
        if self.task and not self.task.done(): return False
        self._running=True; self.task=asyncio.create_task(self._loop()); return True
    async def stop(self):
        self._running=False
        if self.task and not self.task.done():
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
        self.task=None
