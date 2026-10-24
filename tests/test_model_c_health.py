"""Unit tests for Model C health monitoring.

Covers:
9. Persistence failure tracking and status escalation.
15. Collector health classification rules (HEALTHY, DEGRADED, DISCONNECTED, PERSISTENCE_FAILED).
"""

import pytest
from src.model_c.health import ModelCHealthMonitor
from src.model_c.types import CollectorHealthStatus


class TestModelCHealthMonitor:
    def setup_method(self):
        self.health = ModelCHealthMonitor()

    def test_initial_disconnected_state(self):
        assert self.health.get_status() == CollectorHealthStatus.DISCONNECTED

    def test_healthy_connected_state(self):
        self.health.record_connect()
        self.health.record_event_received()
        self.health.record_event_persisted()
        assert self.health.get_status() == CollectorHealthStatus.HEALTHY

    def test_disconnect_state(self):
        self.health.record_connect()
        self.health.record_disconnect("Connection closed by peer")
        assert self.health.get_status() == CollectorHealthStatus.DISCONNECTED
        assert self.health.metrics.last_error_message == "Connection closed by peer"

    def test_9_persistence_failure_escalation(self):
        self.health.record_connect()
        self.health.record_event_received()

        # Receiving event but persistence throws an error
        self.health.record_persistence_failure("disk I/O error or SQLite constraint violation")

        # Must NOT report HEALTHY when persistence is failing
        status = self.health.get_status()
        assert status == CollectorHealthStatus.PERSISTENCE_FAILED

    def test_degraded_state_on_high_parse_error_rate(self):
        self.health.record_connect()
        # Feed 30 events with 5 parse failures (> 5%)
        for _ in range(25):
            self.health.record_event_received()
            self.health.record_event_persisted()

        for _ in range(5):
            self.health.record_event_received()
            self.health.record_parse_failure("Malformed binary payload")

        assert self.health.get_status() == CollectorHealthStatus.DEGRADED

    def test_health_summary_fields(self):
        self.health.record_connect()
        self.health.record_reconnect()
        self.health.record_duplicate()
        self.health.record_unknown_event()
        self.health.record_gap()

        summary = self.health.get_summary()
        assert summary["is_connected"] is True
        assert summary["reconnect_count"] == 1
        assert summary["total_duplicates_detected"] == 1
        assert summary["total_unknown_events"] == 1
        assert summary["total_data_gaps"] == 1
        assert summary["uptime_seconds"] >= 0.0
