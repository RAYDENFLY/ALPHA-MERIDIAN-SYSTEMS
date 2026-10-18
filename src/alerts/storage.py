"""SQLite persistence repository for Universal AsterDEX Perpetual Price Alerts.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from src.alerts.models import (
    AlertDirection,
    AlertEvent,
    AlertSource,
    AlertStatus,
    AlertType,
    PriceAlert,
)
from src.core.logger import logger
from src.data.database import DatabaseManager


ALERT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS price_alerts (
    alert_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    target_price REAL NOT NULL,
    direction TEXT NOT NULL,
    price_source TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    note TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    status TEXT DEFAULT 'ACTIVE',
    armed INTEGER DEFAULT 1,
    last_checked_price REAL,
    last_checked_at TEXT,
    trigger_count INTEGER DEFAULT 0,
    last_triggered_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_alerts_sym ON price_alerts(symbol);
CREATE INDEX IF NOT EXISTS idx_price_alerts_active ON price_alerts(is_active);

CREATE TABLE IF NOT EXISTS price_alert_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    target_price REAL NOT NULL,
    trigger_price REAL NOT NULL,
    direction TEXT NOT NULL,
    price_source TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    note TEXT DEFAULT '',
    acknowledged INTEGER DEFAULT 0,
    discord_status TEXT DEFAULT 'DISABLED',
    discord_attempts INTEGER DEFAULT 0,
    discord_sent_at TEXT,
    discord_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_alert_events_alert_id ON price_alert_events(alert_id);
CREATE INDEX IF NOT EXISTS idx_alert_events_triggered_at ON price_alert_events(triggered_at);
"""


class AlertStorage:
    """Manages SQLite persistence for price alerts and historical trigger events."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db = db_manager
        self.init_db()

    def init_db(self) -> None:
        """Initialize database schema for alerts and trigger events."""
        with self.db.get_connection() as conn:
            conn.executescript(ALERT_SCHEMA_SQL)
            # Run non-destructive column migrations for existing databases
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(price_alert_events);")
            existing_cols = {row["name"] for row in cur.fetchall()}
            if "discord_status" not in existing_cols:
                cur.execute("ALTER TABLE price_alert_events ADD COLUMN discord_status TEXT DEFAULT 'DISABLED';")
            if "discord_attempts" not in existing_cols:
                cur.execute("ALTER TABLE price_alert_events ADD COLUMN discord_attempts INTEGER DEFAULT 0;")
            if "discord_sent_at" not in existing_cols:
                cur.execute("ALTER TABLE price_alert_events ADD COLUMN discord_sent_at TEXT;")
            if "discord_error" not in existing_cols:
                cur.execute("ALTER TABLE price_alert_events ADD COLUMN discord_error TEXT;")

            # Safe to create index now that discord_status column is guaranteed to exist
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alert_events_discord_status ON price_alert_events(discord_status);")
            conn.commit()
            logger.info("AlertStorage schema and migrations initialized successfully.")

    def create_alert(self, alert: PriceAlert) -> PriceAlert:
        """Insert a new price alert."""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO price_alerts (
                    alert_id, symbol, target_price, direction, price_source,
                    alert_type, note, is_active, status, armed,
                    last_checked_price, last_checked_at, trigger_count,
                    last_triggered_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.alert_id,
                    alert.symbol,
                    alert.target_price,
                    alert.direction.value,
                    alert.price_source.value,
                    alert.alert_type.value,
                    alert.note,
                    1 if alert.is_active else 0,
                    alert.status.value,
                    1 if alert.armed else 0,
                    alert.last_checked_price,
                    alert.last_checked_at,
                    alert.trigger_count,
                    alert.last_triggered_at,
                    alert.created_at,
                    alert.updated_at,
                ),
            )
            conn.commit()
        return alert

    def get_alert(self, alert_id: str) -> Optional[PriceAlert]:
        """Retrieve an alert by its unique ID."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM price_alerts WHERE alert_id = ?", (alert_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_alert(row)

    def list_alerts(
        self,
        symbol: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> List[PriceAlert]:
        """List price alerts with optional filtering."""
        query = "SELECT * FROM price_alerts"
        params: List[Any] = []
        conditions: List[str] = []

        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol.upper().strip())

        if is_active is not None:
            conditions.append("is_active = ?")
            params.append(1 if is_active else 0)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC"

        with self.db.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_alert(r) for r in rows]

    def update_alert(self, alert: PriceAlert) -> None:
        """Update existing alert record."""
        now_iso = datetime.now(timezone.utc).isoformat()
        alert.updated_at = now_iso
        with self.db.get_connection() as conn:
            conn.execute(
                """
                UPDATE price_alerts SET
                    symbol = ?,
                    target_price = ?,
                    direction = ?,
                    price_source = ?,
                    alert_type = ?,
                    note = ?,
                    is_active = ?,
                    status = ?,
                    armed = ?,
                    last_checked_price = ?,
                    last_checked_at = ?,
                    trigger_count = ?,
                    last_triggered_at = ?,
                    updated_at = ?
                WHERE alert_id = ?
                """,
                (
                    alert.symbol,
                    alert.target_price,
                    alert.direction.value,
                    alert.price_source.value,
                    alert.alert_type.value,
                    alert.note,
                    1 if alert.is_active else 0,
                    alert.status.value,
                    1 if alert.armed else 0,
                    alert.last_checked_price,
                    alert.last_checked_at,
                    alert.trigger_count,
                    alert.last_triggered_at,
                    alert.updated_at,
                    alert.alert_id,
                ),
            )
            conn.commit()

    def delete_alert(self, alert_id: str) -> bool:
        """Delete an alert and its associated events."""
        with self.db.get_connection() as conn:
            cur = conn.execute("DELETE FROM price_alerts WHERE alert_id = ?", (alert_id,))
            conn.execute("DELETE FROM price_alert_events WHERE alert_id = ?", (alert_id,))
            conn.commit()
            return cur.rowcount > 0

    def record_event(self, event: AlertEvent) -> AlertEvent:
        """Persist a triggered alert event."""
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO price_alert_events (
                    alert_id, symbol, target_price, trigger_price,
                    direction, price_source, triggered_at, note, acknowledged,
                    discord_status, discord_attempts, discord_sent_at, discord_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.alert_id,
                    event.symbol,
                    event.target_price,
                    event.trigger_price,
                    event.direction.value,
                    event.price_source.value,
                    event.triggered_at,
                    event.note,
                    1 if event.acknowledged else 0,
                    event.discord_status,
                    event.discord_attempts,
                    event.discord_sent_at,
                    event.discord_error,
                ),
            )
            event.event_id = cur.lastrowid
            conn.commit()
        return event

    def get_event(self, event_id: Any) -> Optional[AlertEvent]:
        """Retrieve a specific alert event by ID."""
        if hasattr(event_id, "event_id"):
            event_id = event_id.event_id
        if event_id is None:
            return None
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM price_alert_events WHERE event_id = ?", (int(event_id),)
            ).fetchone()
            if not row:
                return None
            return self._row_to_event(row)

    def update_event_discord_status(
        self,
        event_id: Any,
        status: str,
        attempts: int,
        sent_at: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update Discord delivery status, attempt counts, and errors for an alert event."""
        if hasattr(event_id, "event_id"):
            event_id = event_id.event_id
        if event_id is None:
            return
        with self.db.get_connection() as conn:
            conn.execute(
                """
                UPDATE price_alert_events SET
                    discord_status = ?,
                    discord_attempts = ?,
                    discord_sent_at = ?,
                    discord_error = ?
                WHERE event_id = ?
                """,
                (status, attempts, sent_at, error, int(event_id)),
            )
            conn.commit()

    def list_events(
        self,
        limit: int = 100,
        unack_only: bool = False,
    ) -> List[AlertEvent]:
        """Retrieve recent triggered alert events."""
        query = "SELECT * FROM price_alert_events"
        params: List[Any] = []

        if unack_only:
            query += " WHERE acknowledged = 0"

        query += " ORDER BY triggered_at DESC, event_id DESC LIMIT ?"
        params.append(limit)

        with self.db.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_event(r) for r in rows]

    def acknowledge_events(self, event_ids: Optional[List[int]] = None) -> int:
        """Mark events as acknowledged."""
        with self.db.get_connection() as conn:
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                cur = conn.execute(
                    f"UPDATE price_alert_events SET acknowledged = 1 WHERE event_id IN ({placeholders})",
                    event_ids,
                )
            else:
                cur = conn.execute("UPDATE price_alert_events SET acknowledged = 1 WHERE acknowledged = 0")
            conn.commit()
            return cur.rowcount

    def clear_events(self) -> int:
        """Clear all historical events."""
        with self.db.get_connection() as conn:
            cur = conn.execute("DELETE FROM price_alert_events")
            conn.commit()
            return cur.rowcount

    @staticmethod
    def _row_to_alert(row: sqlite3.Row) -> PriceAlert:
        return PriceAlert(
            alert_id=row["alert_id"],
            symbol=row["symbol"],
            target_price=float(row["target_price"]),
            direction=AlertDirection(row["direction"]),
            price_source=AlertSource(row["price_source"]),
            alert_type=AlertType(row["alert_type"]),
            note=row["note"] or "",
            is_active=bool(row["is_active"]),
            status=AlertStatus(row["status"]),
            armed=bool(row["armed"]),
            last_checked_price=float(row["last_checked_price"]) if row["last_checked_price"] is not None else None,
            last_checked_at=row["last_checked_at"],
            trigger_count=int(row["trigger_count"] or 0),
            last_triggered_at=row["last_triggered_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AlertEvent:
        keys = row.keys() if hasattr(row, "keys") else []
        return AlertEvent(
            event_id=row["event_id"],
            alert_id=row["alert_id"],
            symbol=row["symbol"],
            target_price=float(row["target_price"]),
            trigger_price=float(row["trigger_price"]),
            direction=AlertDirection(row["direction"]),
            price_source=AlertSource(row["price_source"]),
            triggered_at=row["triggered_at"],
            note=row["note"] or "",
            acknowledged=bool(row["acknowledged"]),
            discord_status=row["discord_status"] if "discord_status" in keys and row["discord_status"] is not None else "DISABLED",
            discord_attempts=int(row["discord_attempts"]) if "discord_attempts" in keys and row["discord_attempts"] is not None else 0,
            discord_sent_at=row["discord_sent_at"] if "discord_sent_at" in keys else None,
            discord_error=row["discord_error"] if "discord_error" in keys else None,
        )
