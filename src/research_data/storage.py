from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from src.data.database import DatabaseManager
from src.research_data.types import ResearchObservation
def now(): return datetime.now(timezone.utc).isoformat()
class ResearchDataStore:
    def __init__(self, db: DatabaseManager): self.db=db
    def upsert_source(self, s):
        with self.db.get_connection() as c:
            t=now(); c.execute("INSERT INTO research_sources VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET source_name=excluded.source_name,base_url=excluded.base_url,updated_at=excluded.updated_at",(s['source_id'],s['source_name'],s['base_url'],s['source_type'],int(s.get('requires_api_key',False)),int(s.get('requires_login',False)),s.get('license_notes',''),t,t)); c.commit()
    def upsert_dataset(self,d):
        with self.db.get_connection() as c:
            t=now(); c.execute("INSERT INTO research_datasets VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dataset_id) DO UPDATE SET dataset_name=excluded.dataset_name,updated_at=excluded.updated_at",(d['dataset_id'],d['source_id'],d['dataset_name'],d.get('dataset_description',''),d['update_frequency'],d.get('historical_coverage_start'),d.get('historical_coverage_end'),d['unit'],int(d.get('enabled',True)),t,t)); c.commit()
    def insert_observations(self, items: Iterable[ResearchObservation]):
        rows=[]; t=now()
        for x in items: x.validate(); rows.append((x.source_id,x.dataset_id,x.market_id,x.symbol,x.observation_timestamp,x.publication_timestamp,x.reporting_period,x.value,x.unit,x.source_url,x.freshness_state,x.quality_state,x.payload_hash,t))
        with self.db.get_connection() as c:
            n=c.executemany("INSERT OR IGNORE INTO research_observations(source_id,dataset_id,market_id,symbol,observation_timestamp,publication_timestamp,reporting_period,value,unit,source_url,freshness_state,quality_state,payload_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",rows).rowcount; c.commit(); return n
    def list_observations(self,dataset_id,symbol=None,limit=500):
        q="SELECT * FROM research_observations WHERE dataset_id=?"; p=[dataset_id]
        if symbol: q+=" AND symbol=?"; p.append(symbol)
        q+=" ORDER BY observation_timestamp DESC LIMIT ?"; p.append(max(1,min(limit,5000)))
        with self.db.get_connection() as c: return [dict(r) for r in c.execute(q,p)]
    def update_sync_state(self, source_id, dataset_id, **values):
        allowed={"last_attempt_at","last_success_at","last_observation_timestamp","last_publication_timestamp","last_error","records_inserted","records_updated","records_rejected","sync_status","requested_start","requested_end","completed_through","observation_count","error_category"}
        values={k:v for k,v in values.items() if k in allowed}; values.update(source_id=source_id,dataset_id=dataset_id)
        cols=",".join(values); marks=",".join("?" for _ in values); updates=",".join(f"{k}=excluded.{k}" for k in values if k not in ('source_id','dataset_id'))
        with self.db.get_connection() as c:
            c.execute(f"INSERT INTO research_sync_state({cols}) VALUES({marks}) ON CONFLICT(source_id,dataset_id) DO UPDATE SET {updates}",tuple(values.values())); c.commit()
    def get_sync_state(self, dataset_id):
        with self.db.get_connection() as c:
            row=c.execute("SELECT * FROM research_sync_state WHERE dataset_id=?",(dataset_id,)).fetchone()
            return dict(row) if row else None
