from pathlib import Path
import uuid
from types import SimpleNamespace
from src.data.database import DatabaseManager
from src.model_d.scanner import ModelDScanWorker


def test_disabled_worker_does_not_start_or_collect():
    db = DatabaseManager(Path('data') / f'model_d_scan_{uuid.uuid4().hex}.db')
    config = SimpleNamespace(enabled=False, collector_enabled=True, scan_interval_sec=300)
    worker = ModelDScanWorker(db, None, None, config)
    assert worker.start() is False
    assert worker.run_once()['status'] == 'DISABLED'


def test_worker_isolates_symbol_failures_and_persists_cycle():
    db = DatabaseManager(Path('data') / f'model_d_scan_{uuid.uuid4().hex}.db')
    config = SimpleNamespace(enabled=True, collector_enabled=True, scan_interval_sec=300)
    class Universe:
        def refresh(self): return [SimpleNamespace(symbol='GOOD'), SimpleNamespace(symbol='BAD')]
    class Collector:
        def collect_symbol(self, symbol):
            if symbol == 'BAD': raise RuntimeError('network')
            return {'sources': {'trades': {'status': 'UNAVAILABLE', 'exchange_timestamp_ms': None}}}
    worker = ModelDScanWorker(db, Universe(), Collector(), config, max_workers=2)
    result = worker.run_once()
    assert result['status'] == 'OK' and result['scanned'] == 2 and result['failed'] == 1
    assert len(worker.recent(10)) == 2
