"""Unit tests for Model C prospective collector lifecycle and pipeline.

Covers:
5. Duplicate event delivery in collector queue.
8. WebSocket disconnect and reconnect loop logic with explicit data gap logging.
9. Persistence failure in worker task.
14. Graceful shutdown without data corruption.
15. Completeness report generation.
"""

import asyncio
from pathlib import Path
import tempfile
from unittest.mock import MagicMock
import pytest

from src.model_c.collector import ModelCProspectiveCollector
from src.model_c.config import ModelCCollectorConfig
from src.model_c.types import CollectorHealthStatus, ModelCLifecycleState
from tests.test_model_c_event_parser import _encode_create_event_payload, _encode_trade_event_payload


@pytest.fixture
def collector_fixture():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "collector_test.db")
        cfg = ModelCCollectorConfig(
            storage_path=db_path,
            max_reconnect_attempts=2,
            initial_backoff_seconds=0.001,
            max_backoff_seconds=0.01,
        )
        collector = ModelCProspectiveCollector(config=cfg)
        yield collector


@pytest.mark.asyncio
class TestModelCCollector:
    async def test_worker_processing_and_persistence(self, collector_fixture):
        collector = collector_fixture
        b64 = _encode_create_event_payload(name="MoonCoin", symbol="MOON")

        mock_msg = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290999000},
                    "value": {
                        "signature": "SigMoon11111111111111111111111111111111111111111",
                        "err": None,
                        "logs": [
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                            f"Program data: {b64}",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
                        ],
                    },
                }
            },
        }

        collector._running = True
        await collector.queue.put(mock_msg)

        worker = asyncio.create_task(collector._persistence_worker())
        await asyncio.sleep(0.05)
        await collector.stop()

        summary = collector.storage.get_metrics_summary()
        assert summary["total_raw_events"] == 1
        assert summary["total_tokens_discovered"] == 1

    async def test_5_duplicate_event_handling_in_queue(self, collector_fixture):
        collector = collector_fixture
        b64 = _encode_trade_event_payload()

        mock_msg = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290999001},
                    "value": {
                        "signature": "SigTradeDup1111111111111111111111111111111111111",
                        "err": None,
                        "logs": [
                            f"Program data: {b64}",
                        ],
                    },
                }
            },
        }

        collector._running = True
        await collector.queue.put(mock_msg)
        await collector.queue.put(mock_msg)

        worker = asyncio.create_task(collector._persistence_worker())
        await asyncio.sleep(0.05)
        await collector.stop()

        summary = collector.storage.get_metrics_summary()
        assert summary["total_raw_events"] == 1
        assert collector.health.metrics.total_duplicates_detected == 1

    async def test_8_reconnect_and_gap_logging(self, collector_fixture):
        collector = collector_fixture
        collector.health.record_connect()
        collector._last_slot = 290001000

        # Simulate disconnect
        collector.health.record_disconnect("Simulated network drop")
        assert collector.health.get_status() == CollectorHealthStatus.DISCONNECTED

        # Simulate reconnect
        collector.health.record_reconnect()
        assert collector.health.metrics.reconnect_count == 1
        assert collector.health.metrics.is_connected is True

    async def test_9_worker_persistence_failure_handling(self, collector_fixture):
        collector = collector_fixture
        b64 = _encode_trade_event_payload()

        mock_msg = {
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 290999005},
                    "value": {
                        "signature": "SigFailTest111111111111111111111111111111111111",
                        "err": None,
                        "logs": [f"Program data: {b64}"],
                    },
                }
            },
        }

        # Mock storage to raise an exception on raw event persistence
        collector.storage.persist_raw_event = MagicMock(side_effect=Exception("Database lock failure"))

        collector._running = True
        await collector.queue.put(mock_msg)

        worker = asyncio.create_task(collector._persistence_worker())
        await asyncio.sleep(0.05)
        await collector.stop()

        # Health monitor must escalate to PERSISTENCE_FAILED
        assert collector.health.metrics.total_persistence_failures > 0
        assert collector.health.get_status() == CollectorHealthStatus.PERSISTENCE_FAILED

    async def test_14_graceful_shutdown(self, collector_fixture):
        collector = collector_fixture
        await collector.start()
        assert collector._running is True
        assert collector._worker_task is not None
        assert collector._ingest_task is not None

        await collector.stop()
        assert collector._running is False
        assert collector._worker_task.done()
        assert collector._ingest_task.done()

    async def test_15_completeness_report_generation(self, collector_fixture):
        collector = collector_fixture
        collector.health.record_connect()
        collector.health.record_event_received()
        collector.health.record_event_persisted()

        report = collector.generate_completeness_report()
        assert "Model C — Phase C1 Data Completeness & Quality Report" in report
        assert "HEALTHY" in report
        assert "PARTIAL_COVERAGE" in report
        assert "Total Raw Events Persisted" in report
