"""Model C — Durable SQLite Storage Layer.

Phase C1: Prospective Collection-Only Pipeline.
Separates raw immutable events from normalized token records, trades, and collection health checkpoints.
Enforces deterministic deduplication and transaction atomicity.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Tuple

from src.model_c.types import (
    DataGapRecord,
    NormalizedTokenRecord,
    NormalizedTradeRecord,
    RawEventRecord,
    utc_now_iso,
)

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- Raw immutable events as streamed from the RPC node
CREATE TABLE IF NOT EXISTS model_c_raw_events (
    event_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    slot INTEGER NOT NULL,
    block_time INTEGER,
    received_at TEXT NOT NULL,
    program_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    raw_payload TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_c_raw_sig ON model_c_raw_events(signature);
CREATE INDEX IF NOT EXISTS idx_c_raw_slot ON model_c_raw_events(slot);
CREATE INDEX IF NOT EXISTS idx_c_raw_type ON model_c_raw_events(event_type);

-- Normalized prospective token records
CREATE TABLE IF NOT EXISTS model_c_tokens (
    mint_address TEXT PRIMARY KEY,
    creation_signature TEXT NOT NULL,
    creation_slot INTEGER NOT NULL,
    creation_timestamp INTEGER,
    creator_address TEXT NOT NULL,
    bonding_curve_pda TEXT,
    name TEXT,
    symbol TEXT,
    metadata_uri TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    migration_status INTEGER NOT NULL DEFAULT 0,
    migration_timestamp INTEGER,
    migration_pool_address TEXT,
    migration_destination TEXT,
    data_quality_status TEXT NOT NULL,
    tracked_for_sampling INTEGER NOT NULL DEFAULT 1,
    sampling_reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_c_token_slot ON model_c_tokens(creation_slot);
CREATE INDEX IF NOT EXISTS idx_c_token_creator ON model_c_tokens(creator_address);
CREATE INDEX IF NOT EXISTS idx_c_token_lifecycle ON model_c_tokens(lifecycle_state);

-- Normalized on-chain trades
CREATE TABLE IF NOT EXISTS model_c_trades (
    event_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    slot INTEGER NOT NULL,
    block_time INTEGER,
    mint_address TEXT NOT NULL,
    trader_address TEXT NOT NULL,
    is_buy INTEGER NOT NULL,
    sol_amount_lamports INTEGER NOT NULL,
    token_amount_units INTEGER NOT NULL,
    virtual_sol_reserves_lamports INTEGER NOT NULL,
    virtual_token_reserves_units INTEGER NOT NULL,
    real_sol_reserves_lamports INTEGER NOT NULL,
    real_token_reserves_units INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_c_trades_mint ON model_c_trades(mint_address);
CREATE INDEX IF NOT EXISTS idx_c_trades_slot ON model_c_trades(slot);

-- Explicitly recorded discontinuities and data gaps
CREATE TABLE IF NOT EXISTS model_c_data_gaps (
    gap_id TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT,
    reason TEXT NOT NULL,
    last_known_slot INTEGER,
    reconnected_slot INTEGER,
    estimated_missed_slots INTEGER,
    created_at TEXT NOT NULL
);

-- Persistent collector checkpoints
CREATE TABLE IF NOT EXISTS model_c_checkpoints (
    checkpoint_key TEXT PRIMARY KEY,
    checkpoint_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class ModelCStorage:
    """Manages SQLite storage for Model C raw and normalized research datasets."""

    def __init__(self, db_path: str = "data/model_c_research.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database tables and performance PRAGMAs."""
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing an isolated SQLite connection with row factories."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode by default; manual transactions with BEGIN
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for atomic multi-statement transactions with rollback on failure."""
        with self.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def persist_raw_event(self, event: RawEventRecord) -> bool:
        """Persist an immutable raw event record deterministically.
        
        Returns:
            True if event was newly inserted, False if it was an idempotent duplicate.
        """
        event_id = event.event_id or event.compute_event_id()
        now = utc_now_iso()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO model_c_raw_events (
                    event_id, signature, slot, block_time, received_at,
                    program_id, event_type, raw_payload, parse_status,
                    parser_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (
                    event_id,
                    event.signature,
                    event.slot,
                    event.block_time,
                    event.received_at,
                    event.program_id,
                    event.event_type,
                    event.raw_payload,
                    event.parse_status,
                    event.parser_version,
                    now,
                ),
            )
            return cursor.rowcount > 0

    def persist_token(self, token: NormalizedTokenRecord) -> bool:
        """Insert or update a normalized token discovery record.
        
        Returns:
            True if newly inserted, False if updated existing record.
        """
        now = utc_now_iso()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO model_c_tokens (
                    mint_address, creation_signature, creation_slot, creation_timestamp,
                    creator_address, bonding_curve_pda, name, symbol, metadata_uri,
                    first_seen_at, last_seen_at, lifecycle_state, migration_status,
                    migration_timestamp, migration_pool_address, migration_destination,
                    data_quality_status, tracked_for_sampling, sampling_reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mint_address) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    lifecycle_state = CASE 
                        WHEN excluded.lifecycle_state = 'MIGRATED' THEN 'MIGRATED'
                        ELSE model_c_tokens.lifecycle_state
                    END,
                    migration_status = CASE 
                        WHEN excluded.migration_status = 1 THEN 1 
                        ELSE model_c_tokens.migration_status 
                    END,
                    migration_timestamp = COALESCE(excluded.migration_timestamp, model_c_tokens.migration_timestamp),
                    migration_pool_address = COALESCE(excluded.migration_pool_address, model_c_tokens.migration_pool_address),
                    migration_destination = COALESCE(excluded.migration_destination, model_c_tokens.migration_destination),
                    updated_at = excluded.updated_at
                """,
                (
                    token.mint_address,
                    token.creation_signature,
                    token.creation_slot,
                    token.creation_timestamp,
                    token.creator_address,
                    token.bonding_curve_pda,
                    token.name,
                    token.symbol,
                    token.metadata_uri,
                    token.first_seen_at,
                    token.last_seen_at,
                    token.lifecycle_state,
                    int(token.migration_status),
                    token.migration_timestamp,
                    token.migration_pool_address,
                    token.migration_destination,
                    token.data_quality_status,
                    int(token.tracked_for_sampling),
                    token.sampling_reason,
                    now,
                    now,
                ),
            )
            # rowcount == 1 means insert, 2 means update in SQLite upsert
            return cursor.rowcount == 1

    def persist_trade(self, trade: NormalizedTradeRecord) -> bool:
        """Persist a normalized trade event. Returns True if newly inserted."""
        now = utc_now_iso()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO model_c_trades (
                    event_id, signature, slot, block_time, mint_address, trader_address,
                    is_buy, sol_amount_lamports, token_amount_units,
                    virtual_sol_reserves_lamports, virtual_token_reserves_units,
                    real_sol_reserves_lamports, real_token_reserves_units,
                    received_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (
                    trade.event_id,
                    trade.signature,
                    trade.slot,
                    trade.block_time,
                    trade.mint_address,
                    trade.trader_address,
                    int(trade.is_buy),
                    trade.sol_amount_lamports,
                    trade.token_amount_units,
                    trade.virtual_sol_reserves_lamports,
                    trade.virtual_token_reserves_units,
                    trade.real_sol_reserves_lamports,
                    trade.real_token_reserves_units,
                    trade.received_at,
                    now,
                ),
            )
            return cursor.rowcount > 0

    def record_data_gap(self, gap: DataGapRecord) -> None:
        """Log an explicit discontinuity or unrecoverable gap."""
        now = utc_now_iso()
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO model_c_data_gaps (
                    gap_id, start_time, end_time, reason, last_known_slot,
                    reconnected_slot, estimated_missed_slots, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gap_id) DO UPDATE SET
                    end_time = excluded.end_time,
                    reconnected_slot = excluded.reconnected_slot,
                    estimated_missed_slots = excluded.estimated_missed_slots
                """,
                (
                    gap.gap_id,
                    gap.start_time,
                    gap.end_time,
                    gap.reason,
                    gap.last_known_slot,
                    gap.reconnected_slot,
                    gap.estimated_missed_slots,
                    now,
                ),
            )

    def set_checkpoint(self, key: str, value: str) -> None:
        """Durable checkpoint persistence."""
        now = utc_now_iso()
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO model_c_checkpoints (checkpoint_key, checkpoint_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(checkpoint_key) DO UPDATE SET
                    checkpoint_value = excluded.checkpoint_value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def get_checkpoint(self, key: str) -> Optional[str]:
        """Retrieve checkpoint value by key."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT checkpoint_value FROM model_c_checkpoints WHERE checkpoint_key = ?",
                (key,),
            ).fetchone()
            return row["checkpoint_value"] if row else None

    def get_token(self, mint: str) -> Optional[NormalizedTokenRecord]:
        """Fetch a normalized token record by mint address."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM model_c_tokens WHERE mint_address = ?",
                (mint,),
            ).fetchone()
            if not row:
                return None
            return NormalizedTokenRecord(
                mint_address=row["mint_address"],
                creation_signature=row["creation_signature"],
                creation_slot=row["creation_slot"],
                creation_timestamp=row["creation_timestamp"],
                creator_address=row["creator_address"],
                bonding_curve_pda=row["bonding_curve_pda"],
                name=row["name"],
                symbol=row["symbol"],
                metadata_uri=row["metadata_uri"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
                lifecycle_state=row["lifecycle_state"],
                migration_status=bool(row["migration_status"]),
                migration_timestamp=row["migration_timestamp"],
                migration_pool_address=row["migration_pool_address"],
                migration_destination=row["migration_destination"],
                data_quality_status=row["data_quality_status"],
                tracked_for_sampling=bool(row["tracked_for_sampling"]),
                sampling_reason=row["sampling_reason"],
            )

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Compute aggregate counts across raw events, tokens, trades, and gaps."""
        with self.get_connection() as conn:
            raw_count = conn.execute("SELECT COUNT(*) FROM model_c_raw_events").fetchone()[0]
            token_count = conn.execute("SELECT COUNT(*) FROM model_c_tokens").fetchone()[0]
            trade_count = conn.execute("SELECT COUNT(*) FROM model_c_trades").fetchone()[0]
            gap_count = conn.execute("SELECT COUNT(*) FROM model_c_data_gaps").fetchone()[0]

            # Parse status breakdown
            parse_status_rows = conn.execute(
                "SELECT parse_status, COUNT(*) as c FROM model_c_raw_events GROUP BY parse_status"
            ).fetchall()
            status_map = {r["parse_status"]: r["c"] for r in parse_status_rows}

            # Event type breakdown
            event_type_rows = conn.execute(
                "SELECT event_type, COUNT(*) as c FROM model_c_raw_events GROUP BY event_type"
            ).fetchall()
            event_map = {r["event_type"]: r["c"] for r in event_type_rows}

            # Valid signature & slot ratio
            valid_sig_count = conn.execute(
                "SELECT COUNT(*) FROM model_c_raw_events WHERE length(signature) > 10"
            ).fetchone()[0]
            valid_slot_count = conn.execute(
                "SELECT COUNT(*) FROM model_c_raw_events WHERE slot > 0"
            ).fetchone()[0]
            valid_token_ts_count = conn.execute(
                "SELECT COUNT(*) FROM model_c_tokens WHERE creation_timestamp IS NOT NULL"
            ).fetchone()[0]
            migrated_tokens_count = conn.execute(
                "SELECT COUNT(*) FROM model_c_tokens WHERE migration_status = 1"
            ).fetchone()[0]

            return {
                "total_raw_events": raw_count,
                "total_tokens_discovered": token_count,
                "total_trades_recorded": trade_count,
                "total_data_gaps": gap_count,
                "parse_status_breakdown": status_map,
                "event_type_breakdown": event_map,
                "parsed_events_count": status_map.get("PARSED", 0),
                "unsupported_events_count": status_map.get("UNSUPPORTED", 0),
                "malformed_events_count": status_map.get("MALFORMED", 0),
                "partially_parsed_count": status_map.get("PARTIALLY_PARSED", 0),
                "valid_signature_pct": round((valid_sig_count / max(1, raw_count)) * 100.0, 2),
                "valid_slot_pct": round((valid_slot_count / max(1, raw_count)) * 100.0, 2),
                "token_valid_timestamp_pct": round((valid_token_ts_count / max(1, token_count)) * 100.0, 2),
                "migrated_tokens_count": migrated_tokens_count,
            }
