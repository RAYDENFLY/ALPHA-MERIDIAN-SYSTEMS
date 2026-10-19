"""Structured Performance Telemetry Collector for Model B Scanner.

Tracks real-world speed, universe coverage, request latencies, endpoint counts,
cache efficiency, and candidate generation per scan cycle.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import statistics
import threading
import time
from typing import Any, Dict, List, Optional


@dataclass
class ScanCycleTelemetry:
    """Detailed performance and coverage metrics for a single scan cycle."""
    cycle_id: str
    start_time_utc: str
    end_time_utc: str = ""
    total_duration_ms: float = 0.0
    discovery_duration_ms: float = 0.0
    evaluation_duration_ms: float = 0.0

    # Universe coverage breakdown
    symbols_discovered_total: int = 0
    symbols_discovered_asterdex: int = 0
    symbols_eligible_asterdex: int = 0
    symbols_screened: int = 0
    symbols_bulk_screened: int = 0
    symbols_ranked: int = 0
    symbols_evaluated: int = 0
    symbols_deep_evaluated: int = 0
    coverage_pct: float = 0.0

    # Fair rotation tracking
    rotation_offset: int = 0
    rotation_pool_size: int = 0
    rotation_progress_pct: float = 0.0

    # Data quality breakdown
    symbols_valid_ticker: int = 0
    symbols_valid_candles: int = 0
    symbols_valid_l2: int = 0
    symbols_missing_volume: int = 0
    symbols_stale: int = 0
    symbols_failed: int = 0
    symbols_skipped: int = 0

    # Request accounting
    request_count_total: int = 0
    request_count_by_endpoint: Dict[str, int] = field(default_factory=dict)
    request_latencies_ms: List[float] = field(default_factory=list)
    latency_stats: Dict[str, float] = field(default_factory=dict)
    rate_limit_hits: int = 0
    retries_count: int = 0

    # Cache efficiency
    cache_hits: int = 0
    cache_misses: int = 0
    cache_hit_rate_pct: float = 0.0

    # Per-engine duration in ms
    engine_durations_ms: Dict[str, float] = field(default_factory=dict)

    # Candidate formulation breakdown
    candidates_found: int = 0
    candidates_ready: int = 0
    candidates_infeasible: int = 0
    rejections_by_reason: Dict[str, int] = field(default_factory=dict)

    # Active monitoring
    active_monitored_symbols_count: int = 0

    def compute_aggregates(self) -> None:
        """Compute summary statistics (median, p95, p99 latencies, coverage %)."""
        if self.symbols_discovered_asterdex > 0:
            self.coverage_pct = round((self.symbols_evaluated / self.symbols_discovered_asterdex) * 100.0, 2)
        elif self.symbols_discovered_total > 0:
            self.coverage_pct = round((self.symbols_evaluated / self.symbols_discovered_total) * 100.0, 2)
        else:
            self.coverage_pct = 0.0

        total_cache_ops = self.cache_hits + self.cache_misses
        if total_cache_ops > 0:
            self.cache_hit_rate_pct = round((self.cache_hits / total_cache_ops) * 100.0, 2)

        if self.request_latencies_ms:
            sorted_l = sorted(self.request_latencies_ms)
            n = len(sorted_l)
            self.latency_stats = {
                "count": n,
                "min_ms": round(sorted_l[0], 2),
                "max_ms": round(sorted_l[-1], 2),
                "avg_ms": round(statistics.mean(sorted_l), 2),
                "median_ms": round(statistics.median(sorted_l), 2),
                "p95_ms": round(sorted_l[int(n * 0.95)] if n > 1 else sorted_l[0], 2),
                "p99_ms": round(sorted_l[int(n * 0.99)] if n > 1 else sorted_l[0], 2),
            }
        else:
            self.latency_stats = {
                "count": 0,
                "min_ms": 0.0,
                "max_ms": 0.0,
                "avg_ms": 0.0,
                "median_ms": 0.0,
                "p95_ms": 0.0,
                "p99_ms": 0.0,
            }

    def to_dict(self) -> Dict[str, Any]:
        """Convert telemetry to dictionary format for API serialization."""
        return {
            "cycle_id": self.cycle_id,
            "start_time_utc": self.start_time_utc,
            "end_time_utc": self.end_time_utc,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "discovery_duration_ms": round(self.discovery_duration_ms, 2),
            "evaluation_duration_ms": round(self.evaluation_duration_ms, 2),
            "coverage": {
                "discovered_asterdex": self.symbols_discovered_asterdex,
                "discovered_total": self.symbols_discovered_total,
                "eligible_asterdex": self.symbols_eligible_asterdex,
                "screened": self.symbols_screened,
                "bulk_screened": self.symbols_bulk_screened or self.symbols_screened,
                "ranked": self.symbols_ranked,
                "evaluated": self.symbols_evaluated,
                "deep_evaluated": self.symbols_deep_evaluated or self.symbols_evaluated,
                "coverage_pct": self.coverage_pct,
                "active_monitored": self.active_monitored_symbols_count,
            },
            "rotation": {
                "offset": self.rotation_offset,
                "pool_size": self.rotation_pool_size,
                "progress_pct": self.rotation_progress_pct,
            },
            "data_quality": {
                "valid_ticker": self.symbols_valid_ticker,
                "valid_candles": self.symbols_valid_candles,
                "valid_l2": self.symbols_valid_l2,
                "missing_volume": self.symbols_missing_volume,
                "stale": self.symbols_stale,
                "failed": self.symbols_failed,
                "skipped": self.symbols_skipped,
            },
            "requests": {
                "total_count": self.request_count_total,
                "by_endpoint": self.request_count_by_endpoint,
                "latency_stats": self.latency_stats,
                "rate_limit_hits": self.rate_limit_hits,
                "retries": self.retries_count,
            },
            "cache": {
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "hit_rate_pct": self.cache_hit_rate_pct,
            },
            "engine_durations_ms": {k: round(v, 2) for k, v in self.engine_durations_ms.items()},
            "candidates": {
                "found": self.candidates_found,
                "ready": self.candidates_ready,
                "infeasible": self.candidates_infeasible,
                "rejections_by_reason": self.rejections_by_reason,
            },
        }


class ScannerTelemetryTracker:
    """Thread-safe collector for current and historical scan cycle telemetry."""

    def __init__(self, max_history: int = 20) -> None:
        self.max_history = max_history
        self._current_cycle: Optional[ScanCycleTelemetry] = None
        self._history: List[ScanCycleTelemetry] = []
        self._cycle_start_time: float = 0.0
        self._lock = threading.Lock()

    def start_cycle(self, cycle_id: str) -> ScanCycleTelemetry:
        """Initialize telemetry for a new scan cycle."""
        with self._lock:
            self._cycle_start_time = time.time()
            start_utc = datetime.now(timezone.utc).isoformat()
            self._current_cycle = ScanCycleTelemetry(
                cycle_id=cycle_id,
                start_time_utc=start_utc,
            )
            return self._current_cycle

    @property
    def current(self) -> Optional[ScanCycleTelemetry]:
        with self._lock:
            return self._current_cycle

    def record_request(
        self,
        endpoint: str,
        latency_ms: float,
        is_cached: bool = False,
        is_rate_limited: bool = False,
        is_retry: bool = False,
    ) -> None:
        """Record an outbound HTTP or cache request event."""
        with self._lock:
            if not self._current_cycle:
                return

            if is_cached:
                self._current_cycle.cache_hits += 1
                return

            self._current_cycle.cache_misses += 1
            self._current_cycle.request_count_total += 1
            self._current_cycle.request_count_by_endpoint[endpoint] = (
                self._current_cycle.request_count_by_endpoint.get(endpoint, 0) + 1
            )
            self._current_cycle.request_latencies_ms.append(latency_ms)

            if is_rate_limited:
                self._current_cycle.rate_limit_hits += 1
            if is_retry:
                self._current_cycle.retries_count += 1

    def record_engine_duration(self, engine_name: str, duration_ms: float) -> None:
        """Record execution duration of a specific strategy engine."""
        with self._lock:
            if not self._current_cycle:
                return
            self._current_cycle.engine_durations_ms[engine_name] = (
                self._current_cycle.engine_durations_ms.get(engine_name, 0.0) + duration_ms
            )

    def finish_cycle(self) -> ScanCycleTelemetry:
        """Finalize the active scan cycle, compute stats, and append to history."""
        with self._lock:
            if not self._current_cycle:
                return ScanCycleTelemetry(cycle_id="UNKNOWN", start_time_utc="", end_time_utc="")

            now = time.time()
            self._current_cycle.end_time_utc = datetime.now(timezone.utc).isoformat()
            self._current_cycle.total_duration_ms = (now - self._cycle_start_time) * 1000.0
            self._current_cycle.compute_aggregates()

            finished = self._current_cycle
            self._history.append(finished)
            if len(self._history) > self.max_history:
                self._history.pop(0)

            self._current_cycle = None
            return finished

    def get_latest(self) -> Optional[Dict[str, Any]]:
        """Return the most recently completed scan cycle telemetry."""
        with self._lock:
            if self._history:
                return self._history[-1].to_dict()
            if self._current_cycle:
                return self._current_cycle.to_dict()
            return None

    def get_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recent scan cycle telemetry records."""
        return [c.to_dict() for c in reversed(self._history[-limit:])]
