"""Database Repository for AsterDEX Demand-Zone LONG Scanner.

Manages dedicated SQLite storage for:
- demand_zone_candidates: Full candidate setups and audit records
- demand_zone_scans: Telemetry and execution cycles

ISOLATION GUARANTEE:
Never mutates Model A, Model B, trade_journal, or paper execution tables.
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from src.data.database import DatabaseManager
from src.demand_zone.types import (
    DemandZoneCandidate,
    DemandZoneCandidateStatus,
)


class DemandZoneStorage:
    """Dedicated SQLite repository for Demand-Zone LONG candidate records."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._init_tables()

    def _init_tables(self) -> None:
        """Create demand-zone scanner tables if they do not exist."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS demand_zone_candidates (
                candidate_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                datetime_utc TEXT NOT NULL,
                status TEXT NOT NULL,
                current_price REAL NOT NULL,
                demand_zone_upper REAL NOT NULL,
                demand_zone_lower REAL NOT NULL,
                demand_zone_reference REAL NOT NULL,
                distance_to_demand_pct REAL NOT NULL,
                proposed_entry_price REAL NOT NULL,
                entry_rationale TEXT NOT NULL,
                proposed_stop_loss_price REAL NOT NULL,
                stop_loss_distance_pct REAL NOT NULL,
                structural_invalidation_level REAL NOT NULL,
                take_profit_1 REAL NOT NULL,
                take_profit_2 REAL,
                risk_budget_usdt REAL NOT NULL DEFAULT 0.30,
                position_quantity REAL NOT NULL,
                position_notional_usdt REAL NOT NULL,
                required_margin_usdt REAL NOT NULL,
                estimated_leverage REAL NOT NULL DEFAULT 10.0,
                total_estimated_loss_usdt REAL NOT NULL,
                gross_reward_to_risk REAL NOT NULL,
                net_reward_to_risk REAL NOT NULL,
                break_even_price REAL NOT NULL,
                estimated_net_pnl_tp1_usdt REAL NOT NULL,
                volume_24h_usdt REAL NOT NULL,
                bid_ask_spread_bps REAL NOT NULL,
                liquidity_quality_score REAL NOT NULL,
                demand_zone_score REAL NOT NULL,
                composite_setup_score REAL NOT NULL,
                btc_current_price REAL NOT NULL,
                btc_zone_status TEXT NOT NULL,
                btc_policy_used TEXT NOT NULL,
                evidence_points_json TEXT NOT NULL DEFAULT '[]',
                rejection_reasons_json TEXT NOT NULL DEFAULT '[]',
                machine_reasons_json TEXT NOT NULL DEFAULT '[]',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_dz_cand_sym_time
            ON demand_zone_candidates (symbol, timestamp_ms);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_dz_cand_status
            ON demand_zone_candidates (status, composite_setup_score DESC);
            """,
            """
            CREATE TABLE IF NOT EXISTS demand_zone_scans (
                scan_id TEXT PRIMARY KEY,
                timestamp_ms INTEGER NOT NULL,
                datetime_utc TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                total_markets_discovered INTEGER NOT NULL,
                liquid_markets_screened INTEGER NOT NULL,
                ready_count INTEGER NOT NULL,
                approaching_count INTEGER NOT NULL,
                watchlist_count INTEGER NOT NULL,
                infeasible_count INTEGER NOT NULL,
                btc_price REAL NOT NULL,
                btc_zone_status TEXT NOT NULL,
                btc_policy TEXT NOT NULL,
                config_json TEXT NOT NULL,
                rejections_summary_json TEXT NOT NULL DEFAULT '{}'
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_dz_scans_time
            ON demand_zone_scans (timestamp_ms DESC);
            """,
        ]
        with self.db.get_connection() as conn:
            for q in queries:
                conn.execute(q)
            conn.commit()

    def save_candidate(self, cand: DemandZoneCandidate) -> None:
        """Persist or update a demand zone candidate."""
        self.save_candidates_batch([cand])

    def save_candidates_batch(self, candidates: List[DemandZoneCandidate]) -> None:
        """Persist a batch of demand zone candidates atomically."""
        if not candidates:
            return

        query = """
        INSERT OR REPLACE INTO demand_zone_candidates (
            candidate_id, symbol, timestamp_ms, datetime_utc, status,
            current_price, demand_zone_upper, demand_zone_lower, demand_zone_reference,
            distance_to_demand_pct, proposed_entry_price, entry_rationale,
            proposed_stop_loss_price, stop_loss_distance_pct, structural_invalidation_level,
            take_profit_1, take_profit_2, risk_budget_usdt, position_quantity,
            position_notional_usdt, required_margin_usdt, estimated_leverage,
            total_estimated_loss_usdt, gross_reward_to_risk, net_reward_to_risk,
            break_even_price, estimated_net_pnl_tp1_usdt, volume_24h_usdt,
            bid_ask_spread_bps, liquidity_quality_score, demand_zone_score,
            composite_setup_score, btc_current_price, btc_zone_status, btc_policy_used,
            evidence_points_json, rejection_reasons_json, machine_reasons_json,
            details_json, created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params_list = []
        for c in candidates:
            params_list.append((
                c.candidate_id,
                c.symbol,
                c.timestamp_ms,
                c.datetime_utc,
                c.status.value if hasattr(c.status, "value") else str(c.status),
                c.current_price,
                c.demand_zone_upper,
                c.demand_zone_lower,
                c.demand_zone_reference,
                c.distance_to_demand_pct,
                c.proposed_entry_price,
                c.entry_rationale,
                c.proposed_stop_loss_price,
                c.stop_loss_distance_pct,
                c.structural_invalidation_level,
                c.take_profit_1,
                c.take_profit_2,
                c.risk_budget_usdt,
                c.position_quantity,
                c.position_notional_usdt,
                c.required_margin_usdt,
                c.estimated_leverage,
                c.total_estimated_loss_usdt,
                c.gross_reward_to_risk,
                c.net_reward_to_risk,
                c.break_even_price,
                c.estimated_net_pnl_tp1_usdt,
                c.volume_24h_usdt,
                c.bid_ask_spread_bps,
                c.liquidity_quality_score,
                c.demand_zone_score,
                c.composite_setup_score,
                c.btc_current_price,
                c.btc_zone_status,
                c.btc_policy_used,
                json.dumps(c.evidence_points),
                json.dumps(c.rejection_reasons),
                json.dumps(c.machine_reasons),
                json.dumps(c.details),
                c.created_at_utc,
                c.updated_at_utc,
            ))

        with self.db.get_connection() as conn:
            conn.executemany(query, params_list)
            conn.commit()

    def get_candidates(
        self,
        status: Optional[str] = None,
        symbol: Optional[str] = None,
        min_score: float = 0.0,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query stored demand zone candidates with optional filters."""
        conditions = []
        params: List[Any] = []

        if status and status.upper() != "ALL":
            conditions.append("status = ?")
            params.append(status.upper())
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol.upper())
        if min_score > 0:
            conditions.append("composite_setup_score >= ?")
            params.append(min_score)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"""
        SELECT * FROM demand_zone_candidates
        {where}
        ORDER BY composite_setup_score DESC, timestamp_ms DESC
        LIMIT ? OFFSET ?;
        """
        params.extend([limit, offset])

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        results = []
        for r in rows:
            d = dict(r)
            for json_field in ("evidence_points_json", "rejection_reasons_json", "machine_reasons_json", "details_json"):
                plain_key = json_field.replace("_json", "")
                try:
                    d[plain_key] = json.loads(d.get(json_field, "{}"))
                except Exception:
                    d[plain_key] = [] if "reasons" in json_field or "points" in json_field else {}
            results.append(d)
        return results

    def get_candidate_by_id(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single candidate setup by ID."""
        query = "SELECT * FROM demand_zone_candidates WHERE candidate_id = ? LIMIT 1;"
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (candidate_id,))
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        for json_field in ("evidence_points_json", "rejection_reasons_json", "machine_reasons_json", "details_json"):
            plain_key = json_field.replace("_json", "")
            try:
                d[plain_key] = json.loads(d.get(json_field, "{}"))
            except Exception:
                d[plain_key] = [] if "reasons" in json_field or "points" in json_field else {}
        return d

    def record_scan_telemetry(self, telemetry: Dict[str, Any]) -> None:
        """Record scan execution cycle telemetry."""
        query = """
        INSERT OR REPLACE INTO demand_zone_scans (
            scan_id, timestamp_ms, datetime_utc, duration_ms,
            total_markets_discovered, liquid_markets_screened,
            ready_count, approaching_count, watchlist_count, infeasible_count,
            btc_price, btc_zone_status, btc_policy, config_json, rejections_summary_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            telemetry.get("scan_id", f"DZSCAN_{int(datetime.now(timezone.utc).timestamp()*1000)}"),
            telemetry.get("timestamp_ms", int(datetime.now(timezone.utc).timestamp() * 1000)),
            telemetry.get("datetime_utc", datetime.now(timezone.utc).isoformat()),
            telemetry.get("duration_ms", 0.0),
            telemetry.get("total_markets_discovered", 0),
            telemetry.get("liquid_markets_screened", 0),
            telemetry.get("ready_count", 0),
            telemetry.get("approaching_count", 0),
            telemetry.get("watchlist_count", 0),
            telemetry.get("infeasible_count", 0),
            telemetry.get("btc_price", 0.0),
            telemetry.get("btc_zone_status", "UNKNOWN"),
            telemetry.get("btc_policy", "PRIORITIZE_ON_APPROACH"),
            json.dumps(telemetry.get("config", {})),
            json.dumps(telemetry.get("rejections_summary", {})),
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def get_latest_scan_telemetry(self) -> Optional[Dict[str, Any]]:
        """Fetch the most recent scan execution telemetry."""
        query = "SELECT * FROM demand_zone_scans ORDER BY timestamp_ms DESC LIMIT 1;"
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query)
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["config"] = json.loads(d.get("config_json", "{}"))
            d["rejections_summary"] = json.loads(d.get("rejections_summary_json", "{}"))
        except Exception:
            pass
        return d

    def get_recent_candidates(
        self,
        limit: int = 100,
        status: Optional[str] = None,
        symbol: Optional[str] = None,
        min_score: float = 0.0,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Convenience alias for get_candidates."""
        return self.get_candidates(
            status=status,
            symbol=symbol,
            min_score=min_score,
            limit=limit,
            offset=offset,
        )

    def get_recent_scans(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch the most recent scan records."""
        query = "SELECT * FROM demand_zone_scans ORDER BY timestamp_ms DESC LIMIT ?;"
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (limit,))
            rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["config"] = json.loads(d.get("config_json", "{}"))
                d["rejections_summary"] = json.loads(d.get("rejections_summary_json", "{}"))
            except Exception:
                pass
            results.append(d)
        return results
