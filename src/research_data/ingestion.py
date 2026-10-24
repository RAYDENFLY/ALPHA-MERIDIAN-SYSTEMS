"""Bounded, resumable ingestion orchestration for research datasets."""
from dataclasses import dataclass
import random
import time
from typing import Callable, Iterable, Optional

from src.research_data.storage import ResearchDataStore, now
from src.research_data.types import ResearchObservation


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 8.0
    jitter_seconds: float = 0.05


class TransientIngestionError(Exception):
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class ResearchIngestor:
    def __init__(self, store: ResearchDataStore, sleep: Callable[[float], None] = time.sleep, random_fn: Callable[[], float] = random.random):
        self.store, self.sleep, self.random = store, sleep, random_fn

    def _fetch_with_retry(self, fetch: Callable[[], Iterable[ResearchObservation]], policy: RetryPolicy):
        last = None
        for attempt in range(policy.max_attempts):
            try:
                return list(fetch())
            except (TransientIngestionError, TimeoutError, ConnectionError) as exc:
                last = exc
                if attempt + 1 >= policy.max_attempts: break
                retry_after = getattr(exc, "retry_after", None)
                delay = retry_after if retry_after is not None else min(policy.max_delay_seconds, policy.base_delay_seconds * (2 ** attempt))
                self.sleep(delay + policy.jitter_seconds * self.random())
        raise last or RuntimeError("ingestion failed")

    def ingest_chunk(self, source_id: str, dataset_id: str, fetch: Callable[[], Iterable[ResearchObservation]], policy: Optional[RetryPolicy] = None) -> int:
        policy = policy or RetryPolicy()
        self.store.update_sync_state(source_id, dataset_id, last_attempt_at=now(), sync_status="RUNNING", error_category=None, last_error=None)
        try:
            observations = self._fetch_with_retry(fetch, policy)
            inserted = self.store.insert_observations(observations)
            latest = max((x.observation_timestamp for x in observations), default=None)
            outcome = "SUCCESS_WITH_NEW_DATA" if inserted else "SUCCESS_NO_NEW_DATA"
            self.store.update_sync_state(source_id, dataset_id, last_success_at=now(), last_observation_timestamp=latest, observation_count=inserted, sync_status=outcome, records_inserted=inserted, error_category=None, last_error=None)
            return inserted
        except ValueError as exc:
            self.store.update_sync_state(source_id, dataset_id, sync_status="INVALID", error_category="VALIDATION", last_error=str(exc))
            raise
        except Exception as exc:
            self.store.update_sync_state(source_id, dataset_id, sync_status="ERROR", error_category="TRANSPORT", last_error=str(exc))
            raise

    def backfill(self, source_id: str, dataset_id: str, chunks: Iterable[tuple[str, str]], fetch_chunk: Callable[[str, str], Iterable[ResearchObservation]], policy: Optional[RetryPolicy] = None) -> int:
        """Process deterministic chunks; checkpoint advances only after insert commits."""
        total = 0
        for start, end in chunks:
            total += self.ingest_chunk(source_id, dataset_id, lambda s=start, e=end: fetch_chunk(s, e), policy)
            self.store.update_sync_state(source_id, dataset_id, completed_through=end, sync_status="PARTIAL")
        self.store.update_sync_state(source_id, dataset_id, sync_status="AVAILABLE")
        return total
