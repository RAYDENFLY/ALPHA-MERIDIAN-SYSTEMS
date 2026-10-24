"""Model C — Health Monitoring & Data Integrity Tracker.

Phase C1: Prospective Collection-Only Pipeline.
Monitors WebSocket connection status, event flow velocity, persistence fidelity,
and explicit error metrics. Enforces strict health state transitions.
"""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, Optional

from src.model_c.types import CollectorHealthStatus, utc_now_iso


@dataclass
class CollectorHealthMetrics:
    """Live telemetry and operational statistics for the prospective collector."""
    is_connected: bool = False
    start_time_monotonic: float = field(default_factory=time.monotonic)
    start_time_iso: str = field(default_factory=utc_now_iso)
    last_received_epoch_ms: Optional[int] = None
    last_persisted_epoch_ms: Optional[int] = None
    reconnect_count: int = 0
    total_events_received: int = 0
    total_events_persisted: int = 0
    total_duplicates_detected: int = 0
    total_parse_failures: int = 0
    total_persistence_failures: int = 0
    total_unknown_events: int = 0
    total_data_gaps: int = 0
    last_error_message: Optional[str] = None
    last_error_time_iso: Optional[str] = None


class ModelCHealthMonitor:
    """Tracks collector health, data stream continuity, and persistence reliability."""

    def __init__(self):
        self.metrics = CollectorHealthMetrics()

    def record_connect(self) -> None:
        """Mark WebSocket connection established."""
        self.metrics.is_connected = True

    def record_disconnect(self, error: Optional[str] = None) -> None:
        """Mark WebSocket disconnected and record failure details."""
        self.metrics.is_connected = False
        if error:
            self.metrics.last_error_message = error
            self.metrics.last_error_time_iso = utc_now_iso()

    def record_reconnect(self) -> None:
        """Increment reconnection counter."""
        self.metrics.reconnect_count += 1
        self.metrics.is_connected = True

    def record_event_received(self) -> None:
        """Record receipt of an inbound event from the transport."""
        self.metrics.total_events_received += 1
        self.metrics.last_received_epoch_ms = int(time.time() * 1000)

    def record_event_persisted(self) -> None:
        """Record successful storage commit."""
        self.metrics.total_events_persisted += 1
        self.metrics.last_persisted_epoch_ms = int(time.time() * 1000)

    def record_duplicate(self) -> None:
        """Increment duplicate event tally."""
        self.metrics.total_duplicates_detected += 1

    def record_parse_failure(self, error: str) -> None:
        """Record event parsing failure or malformed payload."""
        self.metrics.total_parse_failures += 1
        self.metrics.last_error_message = error
        self.metrics.last_error_time_iso = utc_now_iso()

    def record_persistence_failure(self, error: str) -> None:
        """Record failure to write event to durable storage."""
        self.metrics.total_persistence_failures += 1
        self.metrics.last_error_message = error
        self.metrics.last_error_time_iso = utc_now_iso()

    def record_unknown_event(self) -> None:
        """Record receipt of an unrecognized on-chain event discriminator."""
        self.metrics.total_unknown_events += 1

    def record_gap(self) -> None:
        """Record an explicit discontinuity or missed slot sequence."""
        self.metrics.total_data_gaps += 1

    def get_status(self) -> CollectorHealthStatus:
        """Determine comprehensive operational health state.
        
        Strict Safety Rule:
        Never report HEALTHY if the collector is connected but persistence is failing.
        """
        # If persistence is failing, immediately classify as PERSISTENCE_FAILED
        if self.metrics.total_persistence_failures > 0:
            # If more than 1 persistence failure or 0 persisted despite receiving events
            if self.metrics.total_events_persisted == 0 and self.metrics.total_events_received > 0:
                return CollectorHealthStatus.PERSISTENCE_FAILED
            # If persistence failure rate exceeds 2%
            if (self.metrics.total_persistence_failures / max(1, self.metrics.total_events_received)) > 0.02:
                return CollectorHealthStatus.PERSISTENCE_FAILED

        if not self.metrics.is_connected:
            return CollectorHealthStatus.DISCONNECTED

        # Check for parse failure rate degradation (> 5% failures)
        if self.metrics.total_events_received > 20:
            failure_rate = self.metrics.total_parse_failures / self.metrics.total_events_received
            if failure_rate > 0.05:
                return CollectorHealthStatus.DEGRADED

        return CollectorHealthStatus.HEALTHY

    def get_summary(self) -> Dict[str, Any]:
        """Generate human-readable and machine-auditable telemetry summary."""
        uptime = round(time.monotonic() - self.metrics.start_time_monotonic, 2)
        status = self.get_status()

        return {
            "health_status": status.value,
            "is_connected": self.metrics.is_connected,
            "uptime_seconds": uptime,
            "start_time_iso": self.metrics.start_time_iso,
            "last_received_epoch_ms": self.metrics.last_received_epoch_ms,
            "last_persisted_epoch_ms": self.metrics.last_persisted_epoch_ms,
            "reconnect_count": self.metrics.reconnect_count,
            "total_events_received": self.metrics.total_events_received,
            "total_events_persisted": self.metrics.total_events_persisted,
            "total_duplicates_detected": self.metrics.total_duplicates_detected,
            "total_parse_failures": self.metrics.total_parse_failures,
            "total_persistence_failures": self.metrics.total_persistence_failures,
            "total_unknown_events": self.metrics.total_unknown_events,
            "total_data_gaps": self.metrics.total_data_gaps,
            "last_error_message": self.metrics.last_error_message,
            "last_error_time_iso": self.metrics.last_error_time_iso,
        }
