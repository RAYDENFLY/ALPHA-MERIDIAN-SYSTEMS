"""SQLite Database Storage for Raw Candles, Sync State, Funding Rates, and Setups.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional

from src.core.types import Candle, Direction, MarketBias, SetupCandidate, StructureState, CandidateStatus
from src.core.logger import logger


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS raw_candles (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    close_time_ms INTEGER NOT NULL,
    quote_volume REAL NOT NULL DEFAULT 0.0,
    trades_count INTEGER NOT NULL DEFAULT 0,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe, timestamp_ms)
);

CREATE INDEX IF NOT EXISTS idx_candles_sym_tf_time 
ON raw_candles (symbol, timeframe, timestamp_ms);

CREATE TABLE IF NOT EXISTS candle_sync_state (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    earliest_timestamp_ms INTEGER,
    latest_timestamp_ms INTEGER,
    total_candles INTEGER DEFAULT 0,
    last_sync_utc TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    symbol TEXT NOT NULL,
    funding_time_ms INTEGER NOT NULL,
    funding_rate REAL NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (symbol, funding_time_ms)
);

CREATE INDEX IF NOT EXISTS idx_funding_sym_time 
ON funding_rates (symbol, funding_time_ms);

CREATE TABLE IF NOT EXISTS setup_candidates (
    candidate_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    direction TEXT NOT NULL,
    bias_4h TEXT NOT NULL,
    structure_1h TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit_1 REAL NOT NULL,
    take_profit_2 REAL,
    gross_rr REAL NOT NULL,
    net_rr REAL NOT NULL,
    invalidation_price REAL NOT NULL,
    ml_probability REAL,
    calibration_status TEXT,
    eval_sample_count INTEGER,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    entry_category TEXT DEFAULT 'Unclassified'
);

CREATE INDEX IF NOT EXISTS idx_setups_sym_time 
ON setup_candidates (symbol, timestamp_ms);

CREATE TABLE IF NOT EXISTS trade_journal (
    trade_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_timestamp_ms INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    quantity REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    exit_timestamp_ms INTEGER,
    exit_price REAL,
    pnl_gross REAL DEFAULT 0.0,
    pnl_net REAL DEFAULT 0.0,
    fee_paid REAL DEFAULT 0.0,
    funding_paid REAL DEFAULT 0.0,
    exit_reason TEXT DEFAULT '',
    is_open INTEGER DEFAULT 1,
    created_at_utc TEXT NOT NULL,
    setup_id TEXT DEFAULT '',
    timeframe TEXT DEFAULT '1h',
    order_status TEXT DEFAULT 'FILLED',
    leverage REAL DEFAULT 10.0,
    margin_usdt REAL DEFAULT 5.0,
    slippage_paid REAL DEFAULT 0.0,
    fill_model_version TEXT DEFAULT 'v1_conservative',
    data_source TEXT DEFAULT 'asterdex_live',
    rejection_reason TEXT DEFAULT '',
    funding_data_missing INTEGER DEFAULT 0,
    details_json TEXT DEFAULT '{}',
    entry_category TEXT DEFAULT 'Unclassified'
);

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL NOT NULL,
    stop_price REAL,
    take_profit REAL,
    take_profit_2 REAL,
    quantity REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    margin_usdt REAL NOT NULL,
    leverage REAL NOT NULL,
    status TEXT NOT NULL,
    created_bar_time_ms INTEGER NOT NULL,
    evaluated_bars_count INTEGER DEFAULT 0,
    filled_time_ms INTEGER,
    fill_price REAL,
    slippage_paid REAL DEFAULT 0.0,
    fee_paid REAL DEFAULT 0.0,
    rejection_reason TEXT DEFAULT '',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    entry_category TEXT DEFAULT 'Unclassified'
);

CREATE INDEX IF NOT EXISTS idx_paper_orders_sym_status
ON paper_orders (symbol, status);

CREATE TABLE IF NOT EXISTS paper_lifecycle_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    stage TEXT NOT NULL,
    reason_code TEXT NOT NULL DEFAULT 'OK',
    reason_detail TEXT NOT NULL DEFAULT '',
    order_id TEXT DEFAULT '',
    trade_id TEXT DEFAULT '',
    timestamp_ms INTEGER NOT NULL,
    created_at_utc TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_cand_time
ON paper_lifecycle_events (candidate_id, timestamp_ms);
"""


class DatabaseManager:
    """Manages SQLite storage for market data and trading state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply non-destructive column additions to existing tables."""
        cur = conn.cursor()

        # trade_journal migrations
        cur.execute("PRAGMA table_info(trade_journal);")
        tj_columns = {row["name"] for row in cur.fetchall()}

        new_columns = [
            ("setup_id", "TEXT DEFAULT ''"),
            ("timeframe", "TEXT DEFAULT '1h'"),
            ("order_status", "TEXT DEFAULT 'FILLED'"),
            ("leverage", "REAL DEFAULT 10.0"),
            ("margin_usdt", "REAL DEFAULT 5.0"),
            ("slippage_paid", "REAL DEFAULT 0.0"),
            ("fill_model_version", "TEXT DEFAULT 'v1_conservative'"),
            ("data_source", "TEXT DEFAULT 'asterdex_live'"),
            ("rejection_reason", "TEXT DEFAULT ''"),
            ("funding_data_missing", "INTEGER DEFAULT 0"),
            ("details_json", "TEXT DEFAULT '{}'"),
            ("entry_category", "TEXT DEFAULT 'Unclassified'"),
        ]

        for col_name, col_def in new_columns:
            if col_name not in tj_columns:
                cur.execute(f"ALTER TABLE trade_journal ADD COLUMN {col_name} {col_def};")

        # setup_candidates migrations
        cur.execute("PRAGMA table_info(setup_candidates);")
        sc_columns = {row["name"] for row in cur.fetchall()}
        if "entry_category" not in sc_columns:
            cur.execute("ALTER TABLE setup_candidates ADD COLUMN entry_category TEXT DEFAULT 'Unclassified';")

        # paper_orders migrations
        cur.execute("PRAGMA table_info(paper_orders);")
        po_columns = {row["name"] for row in cur.fetchall()}
        if "entry_category" not in po_columns:
            cur.execute("ALTER TABLE paper_orders ADD COLUMN entry_category TEXT DEFAULT 'Unclassified';")
        if "take_profit_2" not in po_columns:
            cur.execute("ALTER TABLE paper_orders ADD COLUMN take_profit_2 REAL;")


    def init_db(self) -> None:
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
            self._run_migrations(conn)
            conn.commit()


    def insert_candles(self, candles: List[Candle]) -> int:
        """Insert candles with deduplication (INSERT OR IGNORE). Returns count of inserted candles."""
        if not candles:
            return 0

        now_utc = datetime.now(timezone.utc).isoformat()
        records = [
            (
                c.symbol,
                c.timeframe,
                c.timestamp_ms,
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
                c.close_time_ms,
                c.quote_volume,
                c.trades_count,
                now_utc,
            )
            for c in candles
        ]

        query = """
        INSERT OR IGNORE INTO raw_candles (
            symbol, timeframe, timestamp_ms, open, high, low, close, volume,
            close_time_ms, quote_volume, trades_count, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.executemany(query, records)
            inserted_count = cur.rowcount
            conn.commit()

            # Update sync state
            if inserted_count > 0:
                symbol = candles[0].symbol
                timeframe = candles[0].timeframe
                self._update_sync_state(conn, symbol, timeframe)

        return inserted_count

    def _update_sync_state(self, conn: sqlite3.Connection, symbol: str, timeframe: str) -> None:
        stats_query = """
        SELECT MIN(timestamp_ms) as min_ts, MAX(timestamp_ms) as max_ts, COUNT(*) as cnt
        FROM raw_candles
        WHERE symbol = ? AND timeframe = ?;
        """
        cur = conn.cursor()
        cur.execute(stats_query, (symbol, timeframe))
        row = cur.fetchone()
        if row and row["cnt"] > 0:
            now_utc = datetime.now(timezone.utc).isoformat()
            upsert_sync = """
            INSERT INTO candle_sync_state (
                symbol, timeframe, earliest_timestamp_ms, latest_timestamp_ms, total_candles, last_sync_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET
                earliest_timestamp_ms = excluded.earliest_timestamp_ms,
                latest_timestamp_ms = excluded.latest_timestamp_ms,
                total_candles = excluded.total_candles,
                last_sync_utc = excluded.last_sync_utc;
            """
            cur.execute(upsert_sync, (symbol, timeframe, row["min_ts"], row["max_ts"], row["cnt"], now_utc))
            conn.commit()

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Candle]:
        """Fetch chronologically ordered candles from database."""
        conditions = ["symbol = ?", "timeframe = ?"]
        params: List[Any] = [symbol, timeframe]

        if start_time_ms is not None:
            conditions.append("timestamp_ms >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            conditions.append("timestamp_ms <= ?")
            params.append(end_time_ms)

        where_clause = " AND ".join(conditions)
        if limit is not None:
            limit_val = max(0, int(limit))
            params.append(limit_val)
            query = f"""
            SELECT symbol, timeframe, timestamp_ms, open, high, low, close, volume,
                   close_time_ms, quote_volume, trades_count
            FROM (
                SELECT symbol, timeframe, timestamp_ms, open, high, low, close, volume,
                       close_time_ms, quote_volume, trades_count
                FROM raw_candles
                WHERE {where_clause}
                ORDER BY timestamp_ms DESC
                LIMIT ?
            )
            ORDER BY timestamp_ms ASC;
            """
        else:
            query = f"""
            SELECT symbol, timeframe, timestamp_ms, open, high, low, close, volume,
                   close_time_ms, quote_volume, trades_count
            FROM raw_candles
            WHERE {where_clause}
            ORDER BY timestamp_ms ASC;
            """

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        return [
            Candle(
                symbol=r["symbol"],
                timeframe=r["timeframe"],
                timestamp_ms=r["timestamp_ms"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
                close_time_ms=r["close_time_ms"],
                quote_volume=r["quote_volume"],
                trades_count=r["trades_count"],
                is_closed=True,
            )
            for r in rows
        ]

    def get_candle_count(self, symbol: str, timeframe: str) -> int:
        """Return total number of saved candles for symbol and timeframe."""
        query = "SELECT COUNT(*) FROM raw_candles WHERE symbol = ? AND timeframe = ?;"
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (symbol, timeframe))
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def get_latest_candle(self, symbol: str, timeframe: str = "1h") -> Optional[Candle]:
        """Fetch the most recent closed candle for symbol and timeframe."""
        query = """
        SELECT symbol, timeframe, timestamp_ms, open, high, low, close, volume,
               close_time_ms, quote_volume, trades_count
        FROM raw_candles
        WHERE symbol = ? AND timeframe = ?
        ORDER BY timestamp_ms DESC
        LIMIT 1;
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (symbol, timeframe))
            row = cur.fetchone()
            if not row:
                return None
            return Candle(
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                timestamp_ms=row["timestamp_ms"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                close_time_ms=row["close_time_ms"],
                quote_volume=row["quote_volume"],
                trades_count=row["trades_count"],
                is_closed=True,
            )

    def get_sync_state(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        """Return sync metadata for symbol and timeframe."""
        query = """
        SELECT symbol, timeframe, earliest_timestamp_ms, latest_timestamp_ms, total_candles, last_sync_utc
        FROM candle_sync_state
        WHERE symbol = ? AND timeframe = ?;
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (symbol, timeframe))
            row = cur.fetchone()
            return dict(row) if row else None

    def insert_funding_rates(self, symbol: str, rates: List[Dict[str, Any]]) -> int:
        """Insert funding rate records with deduplication."""
        if not rates:
            return 0
        now_utc = datetime.now(timezone.utc).isoformat()
        records = [
            (
                symbol,
                int(r.get("fundingTime", r.get("funding_time_ms", 0))),
                float(r.get("fundingRate", r.get("funding_rate", 0.0))),
                now_utc,
            )
            for r in rates
            if "fundingTime" in r or "funding_time_ms" in r
        ]

        query = """
        INSERT OR IGNORE INTO funding_rates (symbol, funding_time_ms, funding_rate, created_at_utc)
        VALUES (?, ?, ?, ?);
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.executemany(query, records)
            count = cur.rowcount
            conn.commit()
            return count

    def get_funding_rates(
        self,
        symbol: str,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        conditions = ["symbol = ?"]
        params: List[Any] = [symbol]
        if start_time_ms is not None:
            conditions.append("funding_time_ms >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            conditions.append("funding_time_ms <= ?")
            params.append(end_time_ms)

        where_clause = " AND ".join(conditions)
        query = f"""
        SELECT symbol, funding_time_ms, funding_rate, created_at_utc
        FROM funding_rates
        WHERE {where_clause}
        ORDER BY funding_time_ms ASC;
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
