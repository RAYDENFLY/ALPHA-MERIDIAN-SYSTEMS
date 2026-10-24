"""Unit tests for Model C durable storage layer.

Covers:
5. Duplicate event delivery handling.
6. Idempotent storage.
7. Database restart and recovery from checkpoints.
13. Explicit data-gap recording.
"""

from pathlib import Path
import tempfile
import pytest

from src.model_c.storage import ModelCStorage
from src.model_c.types import (
    DataGapRecord,
    ModelCLifecycleState,
    NormalizedTokenRecord,
    NormalizedTradeRecord,
    PumpFunEventType,
    RawEventRecord,
)


@pytest.fixture
def temp_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_research.db"
        storage = ModelCStorage(str(db_path))
        yield storage


class TestModelCStorage:
    def test_5_and_6_duplicate_event_handling_and_idempotence(self, temp_storage):
        event = RawEventRecord(
            signature="5SigUniqueTest111111111111111111111111111111111111",
            slot=290000001,
            block_time=1729800000,
            event_type=PumpFunEventType.CREATE.value,
            raw_payload='{"test": "payload"}',
        )
        event.compute_event_id()

        # First insert: must succeed (returns True)
        first_res = temp_storage.persist_raw_event(event)
        assert first_res is True

        # Second insert of identical event: must be deduplicated idempotently (returns False)
        second_res = temp_storage.persist_raw_event(event)
        assert second_res is False

        # Third attempt: still idempotent
        third_res = temp_storage.persist_raw_event(event)
        assert third_res is False

        # Confirm count in database is exactly 1
        summary = temp_storage.get_metrics_summary()
        assert summary["total_raw_events"] == 1

    def test_token_upsert_idempotence(self, temp_storage):
        token = NormalizedTokenRecord(
            mint_address="MintAddressTest1111111111111111111111111111",
            creation_signature="Sig1",
            creation_slot=290000002,
            creation_timestamp=1729800010,
            creator_address="DevAddress1",
            name="Coin A",
            symbol="CA",
            lifecycle_state=ModelCLifecycleState.DISCOVERED.value,
        )

        # Initial insert
        assert temp_storage.persist_token(token) is True

        # Subsequent update (e.g., token reaches migration)
        token_migrated = NormalizedTokenRecord(
            mint_address="MintAddressTest1111111111111111111111111111",
            creation_signature="Sig1",
            creation_slot=290000002,
            creator_address="DevAddress1",
            lifecycle_state=ModelCLifecycleState.MIGRATED.value,
            migration_status=True,
            migration_timestamp=1729800500,
            migration_destination="RAYDIUM",
        )
        temp_storage.persist_token(token_migrated)

        # Verify state is updated properly without duplicating rows
        saved = temp_storage.get_token("MintAddressTest1111111111111111111111111111")
        assert saved is not None
        assert saved.name == "Coin A"  # Preserved original metadata
        assert saved.lifecycle_state == ModelCLifecycleState.MIGRATED.value
        assert saved.migration_status is True
        assert saved.migration_destination == "RAYDIUM"

        summary = temp_storage.get_metrics_summary()
        assert summary["total_tokens_discovered"] == 1
        assert summary["migrated_tokens_count"] == 1

    def test_7_database_restart_and_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "persistent.db")

            # Session 1: Write data and set checkpoint
            storage1 = ModelCStorage(db_path)
            ev = RawEventRecord(
                signature="SigSession1",
                slot=290500100,
                event_type=PumpFunEventType.TRADE.value,
                raw_payload="{}",
            )
            ev.compute_event_id()
            storage1.persist_raw_event(ev)
            storage1.set_checkpoint("last_processed_slot", "290500100")
            del storage1

            # Session 2: Reopen database and verify state recovery
            storage2 = ModelCStorage(db_path)
            checkpoint = storage2.get_checkpoint("last_processed_slot")
            assert checkpoint == "290500100"

            metrics = storage2.get_metrics_summary()
            assert metrics["total_raw_events"] == 1

            # Verify appending new event succeeds without corrupting prior state
            ev2 = RawEventRecord(
                signature="SigSession2",
                slot=290500105,
                event_type=PumpFunEventType.TRADE.value,
                raw_payload="{}",
            )
            ev2.compute_event_id()
            storage2.persist_raw_event(ev2)

            metrics2 = storage2.get_metrics_summary()
            assert metrics2["total_raw_events"] == 2

    def test_13_data_gap_recording(self, temp_storage):
        gap = DataGapRecord(
            gap_id="gap_test_12345",
            start_time="2026-10-24T12:00:00+00:00",
            end_time="2026-10-24T12:05:00+00:00",
            reason="WEBSOCKET_TIMEOUT",
            last_known_slot=290000000,
            reconnected_slot=290000800,
            estimated_missed_slots=800,
        )
        temp_storage.record_data_gap(gap)

        summary = temp_storage.get_metrics_summary()
        assert summary["total_data_gaps"] == 1
