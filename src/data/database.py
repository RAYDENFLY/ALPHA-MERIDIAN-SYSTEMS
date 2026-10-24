"""SQLite Database Storage for Raw Candles, Sync State, Funding Rates, and Setups.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
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
    entry_category TEXT DEFAULT 'Unclassified',
    model_id TEXT NOT NULL DEFAULT 'MODEL_A'
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
    entry_category TEXT DEFAULT 'Unclassified',
    model_id TEXT NOT NULL DEFAULT 'MODEL_A'
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
    entry_category TEXT DEFAULT 'Unclassified',
    model_id TEXT NOT NULL DEFAULT 'MODEL_A'
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
    metadata_json TEXT NOT NULL DEFAULT '{}',
    model_id TEXT NOT NULL DEFAULT 'MODEL_A'
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_cand_time
ON paper_lifecycle_events (candidate_id, timestamp_ms);

CREATE TABLE IF NOT EXISTS model_a_scan_funnel (
    scan_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    scan_started_at_utc TEXT NOT NULL,
    scan_completed_at_utc TEXT,
    market_candle_timestamp_ms INTEGER,
    market_candle_close_time_ms INTEGER,
    freshness_status TEXT NOT NULL,
    history_status TEXT NOT NULL,
    data_quality_status TEXT NOT NULL,
    predicates_json TEXT NOT NULL DEFAULT '{}',
    values_json TEXT NOT NULL DEFAULT '{}',
    strategy_version TEXT NOT NULL DEFAULT 'MODEL_A_FROZEN',
    final_status TEXT NOT NULL,
    first_rejection_reason TEXT,
    exception_detail TEXT,
    PRIMARY KEY (scan_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_model_a_funnel_time
ON model_a_scan_funnel (scan_started_at_utc, symbol);

CREATE TABLE IF NOT EXISTS order_book_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    bids_json TEXT NOT NULL,
    asks_json TEXT NOT NULL,
    mid_price REAL NOT NULL,
    spread_bps REAL NOT NULL,
    imbalance_25bps REAL NOT NULL,
    imbalance_50bps REAL NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_book_snapshots_sym_time 
ON order_book_snapshots (venue, symbol, timestamp_ms);

CREATE TABLE IF NOT EXISTS whale_events (
    event_id TEXT PRIMARY KEY,
    timestamp_ms INTEGER NOT NULL,
    created_at_utc TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    original_symbol TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    distance_bps REAL NOT NULL,
    baseline_multiple REAL NOT NULL,
    persistence_seconds REAL DEFAULT 0.0,
    lifecycle_state TEXT NOT NULL DEFAULT 'ACTIVE',
    is_spoof_candidate INTEGER DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}',
    mark_price_at_event REAL NOT NULL DEFAULT 0.0,
    price_15m_later REAL,
    price_1h_later REAL,
    price_4h_later REAL,
    sweep_reclaimed_later INTEGER DEFAULT 0,
    correlation_outcome TEXT DEFAULT 'PENDING'
);

CREATE INDEX IF NOT EXISTS idx_whale_sym_time 
ON whale_events (symbol, timestamp_ms);

CREATE INDEX IF NOT EXISTS idx_whale_type_state 
ON whale_events (event_type, lifecycle_state);

CREATE TABLE IF NOT EXISTS chart_drawings (
    drawing_id TEXT PRIMARY KEY,
    user_profile TEXT NOT NULL DEFAULT 'default',
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    tool_type TEXT NOT NULL,
    points_json TEXT NOT NULL DEFAULT '[]',
    style_json TEXT NOT NULL DEFAULT '{}',
    is_visible INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chart_drawings_lookup
ON chart_drawings (user_profile, symbol, timeframe);

CREATE TABLE IF NOT EXISTS research_sources (
    source_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    requires_api_key INTEGER NOT NULL DEFAULT 0,
    requires_login INTEGER NOT NULL DEFAULT 0,
    license_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_datasets (
    dataset_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES research_sources(source_id),
    dataset_name TEXT NOT NULL,
    dataset_description TEXT NOT NULL DEFAULT '',
    update_frequency TEXT NOT NULL,
    historical_coverage_start TEXT,
    historical_coverage_end TEXT,
    unit TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    market_id TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    observation_timestamp TEXT NOT NULL,
    publication_timestamp TEXT,
    reporting_period TEXT,
    value REAL,
    unit TEXT NOT NULL,
    source_url TEXT NOT NULL,
    freshness_state TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    retrieved_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(dataset_id, market_id, symbol, observation_timestamp, payload_hash)
);
CREATE INDEX IF NOT EXISTS idx_research_obs_dataset_time ON research_observations(dataset_id, observation_timestamp);
CREATE INDEX IF NOT EXISTS idx_research_obs_symbol_time ON research_observations(symbol, observation_timestamp);

CREATE TABLE IF NOT EXISTS research_sync_state (
    source_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    last_attempt_at TEXT,
    last_success_at TEXT,
    last_observation_timestamp TEXT,
    last_publication_timestamp TEXT,
    last_error TEXT,
    records_inserted INTEGER NOT NULL DEFAULT 0,
    records_updated INTEGER NOT NULL DEFAULT 0,
    records_rejected INTEGER NOT NULL DEFAULT 0,
    sync_status TEXT NOT NULL DEFAULT 'NEVER_RUN',
    PRIMARY KEY(source_id, dataset_id)
);
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

        cur.execute("PRAGMA table_info(research_sync_state);")
        research_sync_columns = {row["name"] for row in cur.fetchall()}
        for name, definition in [
            ("requested_start", "TEXT"), ("requested_end", "TEXT"),
            ("completed_through", "TEXT"), ("observation_count", "INTEGER NOT NULL DEFAULT 0"),
            ("error_category", "TEXT"),
        ]:
            if name not in research_sync_columns:
                cur.execute(f"ALTER TABLE research_sync_state ADD COLUMN {name} {definition};")
        cur.execute("PRAGMA table_info(research_observations);")
        observation_columns = {row["name"] for row in cur.fetchall()}
        if "retrieved_at" not in observation_columns:
            cur.execute("ALTER TABLE research_observations ADD COLUMN retrieved_at TEXT;")

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
            ("model_id", "TEXT NOT NULL DEFAULT 'MODEL_A'"),
        ]

        for col_name, col_def in new_columns:
            if col_name not in tj_columns:
                cur.execute(f"ALTER TABLE trade_journal ADD COLUMN {col_name} {col_def};")

        # setup_candidates migrations
        cur.execute("PRAGMA table_info(setup_candidates);")
        sc_columns = {row["name"] for row in cur.fetchall()}
        if "entry_category" not in sc_columns:
            cur.execute("ALTER TABLE setup_candidates ADD COLUMN entry_category TEXT DEFAULT 'Unclassified';")
        if "model_id" not in sc_columns:
            cur.execute("ALTER TABLE setup_candidates ADD COLUMN model_id TEXT NOT NULL DEFAULT 'MODEL_A';")

        # paper_orders migrations
        cur.execute("PRAGMA table_info(paper_orders);")
        po_columns = {row["name"] for row in cur.fetchall()}
        if "entry_category" not in po_columns:
            cur.execute("ALTER TABLE paper_orders ADD COLUMN entry_category TEXT DEFAULT 'Unclassified';")
        if "take_profit_2" not in po_columns:
            cur.execute("ALTER TABLE paper_orders ADD COLUMN take_profit_2 REAL;")
        if "model_id" not in po_columns:
            cur.execute("ALTER TABLE paper_orders ADD COLUMN model_id TEXT NOT NULL DEFAULT 'MODEL_A';")

        # paper_lifecycle_events migrations
        cur.execute("PRAGMA table_info(paper_lifecycle_events);")
        ple_columns = {row["name"] for row in cur.fetchall()}
        if "model_id" not in ple_columns:
            cur.execute("ALTER TABLE paper_lifecycle_events ADD COLUMN model_id TEXT NOT NULL DEFAULT 'MODEL_A';")

        cur.execute("PRAGMA table_info(model_a_scan_funnel);")
        funnel_columns = {row["name"] for row in cur.fetchall()}
        if funnel_columns:
            if "values_json" not in funnel_columns:
                cur.execute("ALTER TABLE model_a_scan_funnel ADD COLUMN values_json TEXT NOT NULL DEFAULT '{}';")
            if "strategy_version" not in funnel_columns:
                cur.execute("ALTER TABLE model_a_scan_funnel ADD COLUMN strategy_version TEXT NOT NULL DEFAULT 'MODEL_A_FROZEN';")

        # Create compound indexes for multi-model queries
        cur.execute("CREATE INDEX IF NOT EXISTS idx_setups_model_sym ON setup_candidates(model_id, symbol, timestamp_ms);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_model_time ON trade_journal(model_id, entry_timestamp_ms);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_model_status ON paper_orders(model_id, symbol, status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_lifecycle_model_cand ON paper_lifecycle_events(model_id, candidate_id);")


    def init_db(self) -> None:
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
            self._run_migrations(conn)
            conn.commit()

    def save_model_a_funnel(self, record: Dict[str, Any]) -> None:
        """Persist one non-decision diagnostic row for a Model A symbol evaluation."""
        with self.get_connection() as conn:
            conn.execute("""INSERT OR REPLACE INTO model_a_scan_funnel
                (scan_id,symbol,scan_started_at_utc,scan_completed_at_utc,
                 market_candle_timestamp_ms,market_candle_close_time_ms,
                 freshness_status,history_status,data_quality_status,predicates_json,values_json,strategy_version,
                 final_status,first_rejection_reason,exception_detail)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                record["scan_id"], record["symbol"], record["scan_started_at_utc"],
                record.get("scan_completed_at_utc"), record.get("market_candle_timestamp_ms"),
                record.get("market_candle_close_time_ms"), record.get("freshness_status", "NOT_EVALUATED"),
                record.get("history_status", "NOT_EVALUATED"), record.get("data_quality_status", "NOT_EVALUATED"),
                json.dumps(record.get("predicates", {}), sort_keys=True), json.dumps(record.get("values", {}), sort_keys=True),
                record.get("strategy_version", "MODEL_A_FROZEN"), record.get("final_status", "NOT_EVALUATED"),
                record.get("first_rejection_reason"), record.get("exception_detail")))
            conn.commit()

    def aggregate_model_a_funnel(self, since_ms: Optional[int] = None) -> Dict[str, Any]:
        """Aggregate diagnostic rows with explicit row-based denominators."""
        clauses = ["1=1"]; params: List[Any] = []
        if since_ms is not None: clauses.append("market_candle_timestamp_ms >= ?"); params.append(since_ms)
        where = " AND ".join(clauses)
        with self.get_connection() as conn:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM model_a_scan_funnel WHERE {where} ORDER BY scan_started_at_utc, symbol", params)]
        scans: Dict[str, Dict[str, Any]] = {}
        symbols: Dict[str, Dict[str, int]] = {}
        reasons: Dict[str, int] = {}
        predicates: Dict[str, Dict[str, int]] = {}
        for row in rows:
            scan = scans.setdefault(row["scan_id"], {"symbols": 0, "completed": 0, "incomplete": 0})
            scan["symbols"] += 1; scan["completed"] += bool(row["scan_completed_at_utc"]); scan["incomplete"] += not bool(row["scan_completed_at_utc"])
            symbol = symbols.setdefault(row["symbol"], {"evaluations": 0, "ready": 0, "published": 0})
            symbol["evaluations"] += 1; symbol["ready"] += row["final_status"] == "READY"
            if row["first_rejection_reason"]: reasons[row["first_rejection_reason"]] = reasons.get(row["first_rejection_reason"], 0) + 1
            for name, value in json.loads(row["predicates_json"] or "{}").items():
                bucket = predicates.setdefault(name, {"PASS": 0, "FAIL": 0, "NOT_EVALUATED": 0})
                bucket[value] = bucket.get(value, 0) + 1
        return {"rows": len(rows), "scan_cycles": scans, "scan_cycle_count": len(scans), "symbols": symbols,
                "first_rejection_counts": reasons, "predicate_counts": predicates,
                "ready_count": sum(v["ready"] for v in symbols.values()), "published_count": 0,
                "missing_symbol_completions": sum(v["incomplete"] for v in scans.values())}


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
        before_time_ms: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Candle]:
        """Fetch chronologically ordered candles from database, supporting cursor pagination via before_time_ms."""
        conditions = ["symbol = ?", "timeframe = ?"]
        params: List[Any] = [symbol, timeframe]

        if start_time_ms is not None:
            conditions.append("timestamp_ms >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            conditions.append("timestamp_ms <= ?")
            params.append(end_time_ms)
        if before_time_ms is not None:
            conditions.append("timestamp_ms < ?")
            params.append(before_time_ms)

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

    def save_whale_event(self, event: Dict[str, Any]) -> bool:
        """Persist a single whale activity event with deduplication/update."""
        now_utc = datetime.now(timezone.utc).isoformat()
        query = """
        INSERT INTO whale_events (
            event_id, timestamp_ms, created_at_utc, venue, symbol, original_symbol,
            event_type, side, price, size, notional_usdt, distance_bps,
            baseline_multiple, persistence_seconds, lifecycle_state, is_spoof_candidate,
            details_json, mark_price_at_event, price_15m_later, price_1h_later,
            price_4h_later, sweep_reclaimed_later, correlation_outcome
        ) VALUES (
            :event_id, :timestamp_ms, :created_at_utc, :venue, :symbol, :original_symbol,
            :event_type, :side, :price, :size, :notional_usdt, :distance_bps,
            :baseline_multiple, :persistence_seconds, :lifecycle_state, :is_spoof_candidate,
            :details_json, :mark_price_at_event, :price_15m_later, :price_1h_later,
            :price_4h_later, :sweep_reclaimed_later, :correlation_outcome
        )
        ON CONFLICT(event_id) DO UPDATE SET
            persistence_seconds = excluded.persistence_seconds,
            lifecycle_state = excluded.lifecycle_state,
            is_spoof_candidate = excluded.is_spoof_candidate,
            details_json = excluded.details_json,
            price_15m_later = coalesce(excluded.price_15m_later, whale_events.price_15m_later),
            price_1h_later = coalesce(excluded.price_1h_later, whale_events.price_1h_later),
            price_4h_later = coalesce(excluded.price_4h_later, whale_events.price_4h_later),
            sweep_reclaimed_later = coalesce(excluded.sweep_reclaimed_later, whale_events.sweep_reclaimed_later),
            correlation_outcome = coalesce(excluded.correlation_outcome, whale_events.correlation_outcome);
        """
        params = {
            "event_id": event["event_id"],
            "timestamp_ms": int(event.get("timestamp_ms", 0)),
            "created_at_utc": event.get("created_at_utc", now_utc),
            "venue": event.get("venue", "asterdex"),
            "symbol": event["symbol"],
            "original_symbol": event.get("original_symbol", event["symbol"]),
            "event_type": event.get("event_type", ""),
            "side": event.get("side", "NEUTRAL"),
            "price": float(event.get("price", 0.0)),
            "size": float(event.get("size", 0.0)),
            "notional_usdt": float(event.get("notional_usdt", 0.0)),
            "distance_bps": float(event.get("distance_bps", 0.0)),
            "baseline_multiple": float(event.get("baseline_multiple", 1.0)),
            "persistence_seconds": float(event.get("persistence_seconds", 0.0)),
            "lifecycle_state": event.get("lifecycle_state", "ACTIVE"),
            "is_spoof_candidate": 1 if event.get("is_spoof_candidate") else 0,
            "details_json": json.dumps(event.get("details", {})) if isinstance(event.get("details"), (dict, list)) else str(event.get("details", "{}")),
            "mark_price_at_event": float(event.get("mark_price_at_event", event.get("price", 0.0))),
            "price_15m_later": event.get("price_15m_later"),
            "price_1h_later": event.get("price_1h_later"),
            "price_4h_later": event.get("price_4h_later"),
            "sweep_reclaimed_later": 1 if event.get("sweep_reclaimed_later") else 0,
            "correlation_outcome": event.get("correlation_outcome", "PENDING"),
        }

        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                conn.commit()
                return True
        except Exception as err:
            logger.error(f"DatabaseManager: Failed to save whale event {event.get('event_id')}: {err}")
            return False

    def save_whale_events(self, events: List[Dict[str, Any]]) -> int:
        """Batch save whale events."""
        saved = 0
        for ev in events:
            if self.save_whale_event(ev):
                saved += 1
        return saved

    def get_whale_events(
        self,
        symbol: Optional[str] = None,
        event_type: Optional[str] = None,
        venue: Optional[str] = None,
        lifecycle_state: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieve historical whale events filtered by symbol, type, or venue."""
        conditions = []
        params: List[Any] = []

        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol.strip().upper())
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type.strip())
        if venue:
            conditions.append("venue = ?")
            params.append(venue.strip().lower())
        if lifecycle_state:
            conditions.append("lifecycle_state = ?")
            params.append(lifecycle_state.strip())

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
        SELECT 
            event_id, timestamp_ms, created_at_utc, venue, symbol, original_symbol,
            event_type, side, price, size, notional_usdt, distance_bps,
            baseline_multiple, persistence_seconds, lifecycle_state, is_spoof_candidate,
            details_json, mark_price_at_event, price_15m_later, price_1h_later,
            price_4h_later, sweep_reclaimed_later, correlation_outcome
        FROM whale_events
        {where_clause}
        ORDER BY timestamp_ms DESC
        LIMIT ?;
        """
        params.append(limit)

        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                rows = cur.fetchall()
                results = []
                for r in rows:
                    row_dict = dict(r)
                    try:
                        row_dict["details"] = json.loads(row_dict.get("details_json", "{}"))
                    except Exception:
                        row_dict["details"] = row_dict.get("details_json", "")
                    results.append(row_dict)
                return results
        except Exception as err:
            logger.error(f"DatabaseManager: Failed to query whale events: {err}")
            return []

    def update_whale_event_forward_price(
        self,
        event_id: str,
        horizon: str,
        price: float,
        outcome: Optional[str] = None,
    ) -> bool:
        """Update forward horizon price (+15m, +1h, +4h) for retrospective evaluation."""
        col = f"price_{horizon}_later"
        if col not in ("price_15m_later", "price_1h_later", "price_4h_later"):
            return False

        set_clauses = [f"{col} = ?"]
        params: List[Any] = [price]
        if outcome:
            set_clauses.append("correlation_outcome = ?")
            params.append(outcome)
        params.append(event_id)

        query = f"UPDATE whale_events SET {', '.join(set_clauses)} WHERE event_id = ?;"
        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                conn.commit()
                return cur.rowcount > 0
        except Exception as err:
            logger.error(f"DatabaseManager: Failed to update forward price for {event_id}: {err}")
            return False

    def get_unresolved_whale_events(self, horizon: str = "15m", max_age_ms: int = 86400000) -> List[Dict[str, Any]]:
        """Retrieve events that have passed the horizon duration but have not had forward price recorded."""
        col = f"price_{horizon}_later"
        if col not in ("price_15m_later", "price_1h_later", "price_4h_later"):
            return []

        horizon_ms = 900000 if horizon == "15m" else (3600000 if horizon == "1h" else 14400000)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        target_cutoff = now_ms - horizon_ms
        oldest_cutoff = now_ms - max_age_ms

        query = f"""
        SELECT event_id, timestamp_ms, symbol, venue, event_type, side, price, mark_price_at_event
        FROM whale_events
        WHERE {col} IS NULL
          AND timestamp_ms <= ?
          AND timestamp_ms >= ?
        LIMIT 100;
        """
        try:
            with self.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(query, (target_cutoff, oldest_cutoff))
                return [dict(r) for r in cur.fetchall()]
        except Exception as err:
            logger.error(f"DatabaseManager: Failed to get unresolved whale events: {err}")
            return []

    def save_chart_drawing(self, drawing: Dict[str, Any]) -> Dict[str, Any]:
        """Persist or update a chart drawing in SQLite."""
        now_utc = datetime.now(timezone.utc).isoformat()
        drawing_id = str(drawing["drawing_id"])
        user_profile = str(drawing.get("user_profile") or "default").strip()
        symbol = str(drawing["symbol"]).strip().upper().replace(".P", "")
        timeframe = str(drawing["timeframe"]).strip().lower()
        tool_type = str(drawing["tool_type"]).strip()
        points = drawing.get("points") if isinstance(drawing.get("points"), list) else []
        style = drawing.get("style") if isinstance(drawing.get("style"), dict) else {}
        is_visible = 1 if drawing.get("is_visible", True) else 0
        created_at = str(drawing.get("created_at_utc") or now_utc)

        query = """
        INSERT INTO chart_drawings (
            drawing_id, user_profile, symbol, timeframe, tool_type,
            points_json, style_json, is_visible, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(drawing_id) DO UPDATE SET
            user_profile = excluded.user_profile,
            symbol = excluded.symbol,
            timeframe = excluded.timeframe,
            tool_type = excluded.tool_type,
            points_json = excluded.points_json,
            style_json = excluded.style_json,
            is_visible = excluded.is_visible,
            updated_at_utc = excluded.updated_at_utc;
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                query,
                (
                    drawing_id,
                    user_profile,
                    symbol,
                    timeframe,
                    tool_type,
                    json.dumps(points),
                    json.dumps(style),
                    is_visible,
                    created_at,
                    now_utc,
                ),
            )
            conn.commit()
        return {
            "drawing_id": drawing_id,
            "user_profile": user_profile,
            "symbol": symbol,
            "timeframe": timeframe,
            "tool_type": tool_type,
            "points": points,
            "style": style,
            "is_visible": bool(is_visible),
            "created_at_utc": created_at,
            "updated_at_utc": now_utc,
        }

    def get_chart_drawings(
        self,
        symbol: str,
        timeframe: str,
        user_profile: str = "default",
    ) -> List[Dict[str, Any]]:
        """Retrieve all persisted chart drawings for (user_profile, symbol, timeframe)."""
        clean_sym = symbol.strip().upper().replace(".P", "")
        clean_tf = timeframe.strip().lower()
        clean_prof = (user_profile or "default").strip()
        query = """
        SELECT drawing_id, user_profile, symbol, timeframe, tool_type,
               points_json, style_json, is_visible, created_at_utc, updated_at_utc
        FROM chart_drawings
        WHERE user_profile = ? AND symbol = ? AND timeframe = ?
        ORDER BY created_at_utc ASC;
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (clean_prof, clean_sym, clean_tf))
            rows = cur.fetchall()
            results: List[Dict[str, Any]] = []
            for r in rows:
                try:
                    pts = json.loads(r["points_json"] or "[]")
                except Exception:
                    pts = []
                try:
                    st = json.loads(r["style_json"] or "{}")
                except Exception:
                    st = {}
                results.append(
                    {
                        "drawing_id": r["drawing_id"],
                        "user_profile": r["user_profile"],
                        "symbol": r["symbol"],
                        "timeframe": r["timeframe"],
                        "tool_type": r["tool_type"],
                        "points": pts,
                        "style": st,
                        "is_visible": bool(r["is_visible"]),
                        "created_at_utc": r["created_at_utc"],
                        "updated_at_utc": r["updated_at_utc"],
                    }
                )
            return results

    def delete_chart_drawing(
        self,
        drawing_id: str,
        user_profile: str = "default",
    ) -> bool:
        """Delete a single persisted chart drawing by drawing_id."""
        query = "DELETE FROM chart_drawings WHERE drawing_id = ? AND user_profile = ?;"
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (drawing_id, (user_profile or "default").strip()))
            conn.commit()
            return cur.rowcount > 0

    def clear_chart_drawings(
        self,
        symbol: str,
        timeframe: str,
        user_profile: str = "default",
    ) -> int:
        """Delete all persisted chart drawings for (user_profile, symbol, timeframe)."""
        clean_sym = symbol.strip().upper().replace(".P", "")
        clean_tf = timeframe.strip().lower()
        clean_prof = (user_profile or "default").strip()
        query = "DELETE FROM chart_drawings WHERE user_profile = ? AND symbol = ? AND timeframe = ?;"
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (clean_prof, clean_sym, clean_tf))
            conn.commit()
            return cur.rowcount

