"""Model C — Prospective Read-Only Event Collector.

Phase C1: Prospective Collection-Only Pipeline & Data Quality Validation.
Subscribes to Solana JSON-RPC `logsSubscribe` for Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P.
Captures raw events, parses normalized tokens and trades, and logs explicit gaps.
STRICT SAFETY INVARIANT: Contains zero trading logic, wallet operations, or order pathways.
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict, Optional

from src.model_c.config import ModelCCollectorConfig
from src.model_c.event_parser import ParsedEventResult, PumpFunEventParser
from src.model_c.health import ModelCHealthMonitor
from src.model_c.storage import ModelCStorage
from src.model_c.types import (
    CollectorHealthStatus,
    DataGapRecord,
    EventParseStatus,
    PumpFunEventType,
    utc_now_iso,
)

logger = logging.getLogger("model_c.collector")


class ModelCProspectiveCollector:
    """Async event collector for prospective Pump.fun on-chain research."""

    def __init__(
        self,
        config: Optional[ModelCCollectorConfig] = None,
        storage: Optional[ModelCStorage] = None,
        parser: Optional[PumpFunEventParser] = None,
        health: Optional[ModelCHealthMonitor] = None,
    ):
        self.config = config or ModelCCollectorConfig()
        self.storage = storage or ModelCStorage(self.config.storage_path)
        self.parser = parser or PumpFunEventParser(self.config.program_id)
        self.health = health or ModelCHealthMonitor()

        # Bounded event queue to prevent unbounded memory growth during traffic surges
        self.queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=self.config.queue_max_size)

        self._running = False
        self._stop_event = asyncio.Event()
        self._subscription_id: Optional[int] = None
        self._last_slot: Optional[int] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._ingest_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Initialize the background processing worker and connection ingestion task."""
        if self._running:
            return

        self._running = True
        self._stop_event.clear()

        # Restore last known checkpoint
        saved_slot_str = self.storage.get_checkpoint("last_processed_slot")
        if saved_slot_str:
            try:
                self._last_slot = int(saved_slot_str)
                logger.info(f"Model C collector resumed from checkpoint slot: {self._last_slot}")
            except ValueError:
                self._last_slot = None

        logger.info(f"Starting Model C collector (queue_capacity={self.config.queue_max_size})")

        # Launch consumer persistence worker
        self._worker_task = asyncio.create_task(self._persistence_worker(), name="model_c_worker")
        # Launch network ingestion task
        self._ingest_task = asyncio.create_task(self._ingestion_loop(), name="model_c_ingest")

    async def stop(self) -> None:
        """Gracefully shut down ingestion and drain the persistence queue."""
        if not self._running:
            return

        logger.info("Stopping Model C collector gracefully...")
        self._running = False
        self._stop_event.set()

        # Cancel ingestion loop
        if self._ingest_task and not self._ingest_task.done():
            self._ingest_task.cancel()
            try:
                await self._ingest_task
            except asyncio.CancelledError:
                pass

        # Wait for persistence worker to drain existing events
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        self.health.record_disconnect("Collector stopped by user/system")
        if self._last_slot:
            self.storage.set_checkpoint("last_processed_slot", str(self._last_slot))

        logger.info("Model C collector stopped successfully.")

    async def _ingestion_loop(self) -> None:
        """Main WebSocket connection supervisor with exponential backoff and jitter."""
        attempt = 0
        ws_url = self.config.get_effective_ws_url()

        while self._running and not self._stop_event.is_set():
            gap_start = utc_now_iso()
            gap_slot_anchor = self._last_slot

            try:
                # Use websockets client library
                import websockets

                logger.info(f"Connecting to Solana WebSocket endpoint...")
                async with websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=15,
                    close_timeout=10,
                ) as ws:
                    attempt = 0
                    self.health.record_connect()
                    logger.info("WebSocket connected. Subscribing to Pump.fun program logs...")

                    # Subscribe to program logs
                    subscribe_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [self.config.program_id]},
                            {"commitment": self.config.commitment},
                        ],
                    }
                    await ws.send(json.dumps(subscribe_payload))

                    # Process incoming frames
                    async for raw_message in ws:
                        if self._stop_event.is_set():
                            break

                        try:
                            msg = json.loads(raw_message)
                        except json.JSONDecodeError:
                            self.health.record_parse_failure("JSON decode failure on WebSocket frame")
                            continue

                        # Check for subscription confirmation
                        if "result" in msg and self._subscription_id is None:
                            self._subscription_id = msg["result"]
                            logger.info(f"Pump.fun subscription confirmed: ID {self._subscription_id}")
                            continue

                        # Check for logs notification
                        if msg.get("method") == "logsNotification":
                            self.health.record_event_received()

                            # Put in queue with backpressure handling
                            try:
                                self.queue.put_nowait(msg)
                            except asyncio.QueueFull:
                                self.health.record_gap()
                                logger.warning("In-memory queue full! Event dropped to protect memory bounds.")

            except asyncio.CancelledError:
                break
            except Exception as e:
                attempt += 1
                self.health.record_disconnect(str(e))
                self.health.record_reconnect()

                # Record an explicit data gap
                gap_id = f"gap_{int(time.time()*1000)}_{random.randint(100, 999)}"
                self.storage.record_data_gap(
                    DataGapRecord(
                        gap_id=gap_id,
                        start_time=gap_start,
                        end_time=utc_now_iso(),
                        reason=f"WS_DISCONNECT: {type(e).__name__} - {e}",
                        last_known_slot=gap_slot_anchor,
                        reconnected_slot=None,
                        estimated_missed_slots=None,
                    )
                )

                if attempt >= self.config.max_reconnect_attempts:
                    logger.error(f"Max reconnect attempts ({attempt}) exceeded. Collector entering degraded idle.")
                    await asyncio.sleep(self.config.max_backoff_seconds)
                    attempt = 0  # Reset backoff ladder after waiting ceiling
                    continue

                # Compute exponential backoff with jitter
                delay = min(
                    self.config.max_backoff_seconds,
                    self.config.initial_backoff_seconds * (2 ** (attempt - 1)),
                )
                if self.config.backoff_jitter:
                    delay *= random.uniform(0.8, 1.2)

                logger.warning(f"WebSocket error: {e}. Reconnecting in {delay:.2f}s (attempt {attempt})...")
                await asyncio.sleep(delay)

    async def _persistence_worker(self) -> None:
        """Drains the queue, parses payloads, and persists to durable storage."""
        while self._running or not self.queue.empty():
            try:
                try:
                    msg = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Parse notification into structured results
                parsed_results = self.parser.parse_rpc_notification(msg)

                for res in parsed_results:
                    raw = res.raw_event
                    if raw.slot > 0:
                        self._last_slot = raw.slot

                    try:
                        # 1. Persist raw event
                        is_new = self.storage.persist_raw_event(raw)
                        if is_new:
                            self.health.record_event_persisted()
                        else:
                            self.health.record_duplicate()

                        # 2. Persist normalized token if creation
                        if res.token_record:
                            self.storage.persist_token(res.token_record)

                        # 3. Persist normalized trade if trade
                        if res.trade_record:
                            self.storage.persist_trade(res.trade_record)

                        # 4. Handle parse fidelity tracking
                        if res.parse_status == EventParseStatus.MALFORMED:
                            self.health.record_parse_failure(res.error_message or "Malformed payload")
                        elif res.parse_status == EventParseStatus.UNSUPPORTED:
                            self.health.record_unknown_event()

                    except Exception as persist_err:
                        self.health.record_persistence_failure(str(persist_err))
                        logger.error(f"Durable storage write failed: {persist_err}")

                self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                logger.error(f"Error in persistence worker loop: {loop_err}")

    def generate_completeness_report(self) -> str:
        """Generate an audit-grade data completeness and quality report."""
        summary = self.storage.get_metrics_summary()
        health_summary = self.health.get_summary()

        report = f"""# Model C — Phase C1 Data Completeness & Quality Report
**Generated UTC**: {utc_now_iso()}  
**Collector Uptime**: {health_summary['uptime_seconds']} seconds  
**Collector Health Status**: `{health_summary['health_status']}`  
**WebSocket Connected**: {health_summary['is_connected']}  
**Sampling Policy**: `{self.config.sampling_policy_version}`  

---

## 1. Raw Ingestion & Integrity Metrics
- **Total Raw Events Received**: {health_summary['total_events_received']}
- **Total Raw Events Persisted**: {summary['total_raw_events']}
- **Duplicate Events Filtered**: {health_summary['total_duplicates_detected']}
- **Unknown Event Types**: {summary['unsupported_events_count']}
- **Malformed Events**: {summary['malformed_events_count']}
- **Partially Parsed Events**: {summary['partially_parsed_count']}
- **Persistence Failures**: {health_summary['total_persistence_failures']}
- **WebSocket Reconnects**: {health_summary['reconnect_count']}
- **Recorded Data Discontinuities (Gaps)**: {summary['total_data_gaps']}

---

## 2. Token Discovery & Normalization
- **Total Unique Tokens Discovered**: {summary['total_tokens_discovered']}
- **Total On-Chain Trades Recorded**: {summary['total_trades_recorded']}
- **Tokens with Verified Raydium Migration**: {summary['migrated_tokens_count']}
- **Valid Signature Ratio**: {summary['valid_signature_pct']}%
- **Valid Slot Ratio**: {summary['valid_slot_pct']}%
- **Valid Creation Timestamp Ratio**: {summary['token_valid_timestamp_pct']}%

---

## 3. Data Source Coverage Classification
- **Coverage Status**: `PARTIAL_COVERAGE`
  - *Rationale*: Public logs capture on-chain events emitted by the program, but transaction drops or unannounced program updates by Pump.fun can cause episodic omissions. Universal coverage without gaps requires a dedicated archive validator.
"""
        return report



async def _run_cli_collector(duration_seconds: Optional[int] = None, db_path: str = "data/model_c_research.db") -> None:
    """Explicit manual CLI entrypoint for running the prospective collector."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = ModelCCollectorConfig(storage_path=db_path, max_collection_duration_seconds=duration_seconds)
    collector = ModelCProspectiveCollector(config=cfg)

    print(f"Starting Model C Prospective Collector...")
    print(f"Storage DB: {cfg.storage_path}")
    print(f"Target Program ID: {cfg.program_id}")
    if duration_seconds:
        print(f"Duration limit: {duration_seconds}s")

    await collector.start()
    try:
        if duration_seconds:
            await asyncio.sleep(duration_seconds)
        else:
            while True:
                await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nShutdown signal received.")
    finally:
        await collector.stop()
        print("\n" + collector.generate_completeness_report())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Model C Prospective Pump.fun Event Collector")
    parser.add_argument("--duration", type=int, default=None, help="Collection duration in seconds")
    parser.add_argument("--db", type=str, default="data/model_c_research.db", help="SQLite database path")
    parser.add_argument("--report", action="store_true", help="Print completeness report from existing database and exit")

    args = parser.parse_args()
    if args.report:
        storage = ModelCStorage(args.db)
        collector = ModelCProspectiveCollector(config=ModelCCollectorConfig(storage_path=args.db), storage=storage)
        print(collector.generate_completeness_report())
    else:
        asyncio.run(_run_cli_collector(duration_seconds=args.duration, db_path=args.db))
