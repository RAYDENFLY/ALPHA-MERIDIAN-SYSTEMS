from src.research_data.ingestion import ResearchIngestor, RetryPolicy, TransientIngestionError
from src.research_data.storage import ResearchDataStore
from src.research_data.types import ResearchObservation
import uuid

def obs(ts="2024-01-01"):
    return [ResearchObservation("x","d","m","Y",ts,None,ts,1.0,"percent","u","DELAYED","VALID","h"+ts)]

def test_retry_and_idempotent_chunk():
    store=ResearchDataStore(__import__('src.data.database',fromlist=['DatabaseManager']).DatabaseManager(f'.pytest-ingestion-{uuid.uuid4().hex}.db'))
    calls=[]
    def fetch():
        calls.append(1)
        if len(calls)<2: raise TransientIngestionError("429", retry_after=0)
        return obs()
    ing=ResearchIngestor(store,sleep=lambda _:None,random_fn=lambda:0)
    assert ing.ingest_chunk("x","d",fetch,RetryPolicy(max_attempts=3))==1
    assert ing.ingest_chunk("x","d",lambda:obs(),RetryPolicy(max_attempts=1))==0
    assert len(store.list_observations("d"))==1

def test_failed_chunk_does_not_checkpoint():
    store=ResearchDataStore(__import__('src.data.database',fromlist=['DatabaseManager']).DatabaseManager(f'.pytest-ingestion-fail-{uuid.uuid4().hex}.db'))
    ing=ResearchIngestor(store,sleep=lambda _:None)
    try: ing.backfill("x","d",[("2024-01-01","2024-01-31")],lambda *_: (_ for _ in ()).throw(ValueError("bad")))
    except ValueError: pass
    with store.db.get_connection() as c:
        row=c.execute("SELECT completed_through,sync_status FROM research_sync_state WHERE dataset_id='d'").fetchone()
    assert row[0] is None and row[1]=="INVALID"
