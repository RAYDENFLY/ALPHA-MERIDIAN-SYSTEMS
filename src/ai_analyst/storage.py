"""Dedicated SQLite Repository for Groq AI Trading Analyst History.

Ensures strict persistence isolation:
- Operates on dedicated table `ai_analyst_records`
- Never reads or modifies Model A / Model B trade journals or paper execution orders
- Manages cached requests and historical analysis retrieval
"""

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional

from src.ai_analyst.types import AIAnalystResult
from src.data.database import DatabaseManager


class AIAnalystStorage:
    """Manages dedicated SQLite persistence for AI Analyst recommendations and query history."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._init_tables()

    def _init_tables(self) -> None:
        queries = [
            """
            CREATE TABLE IF NOT EXISTS ai_analyst_records (
                analysis_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                datetime_utc TEXT NOT NULL,
                primary_bias TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                confidence_score REAL NOT NULL,
                uncertainty_score REAL NOT NULL,
                risk_budget_usdt REAL NOT NULL DEFAULT 1.00,
                model_id TEXT NOT NULL,
                market_snapshot_json TEXT NOT NULL DEFAULT '{}',
                long_scenario_json TEXT NOT NULL DEFAULT '{}',
                short_scenario_json TEXT NOT NULL DEFAULT '{}',
                recommended_plan_json TEXT DEFAULT '{}',
                penjelasan_indonesia_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at_utc TEXT NOT NULL
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_ai_analyst_sym_tf
            ON ai_analyst_records (symbol, timeframe, timestamp_ms DESC);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_ai_analyst_time
            ON ai_analyst_records (timestamp_ms DESC);
            """,
        ]
        with self.db.get_connection() as conn:
            for q in queries:
                conn.execute(q)
            conn.commit()

    def save_analysis(self, result: AIAnalystResult) -> None:
        """Persist an analysis result atomically."""
        query = """
        INSERT OR REPLACE INTO ai_analyst_records (
            analysis_id, symbol, timeframe, timestamp_ms, datetime_utc,
            primary_bias, recommendation, confidence_score, uncertainty_score,
            risk_budget_usdt, model_id, market_snapshot_json,
            long_scenario_json, short_scenario_json, recommended_plan_json,
            penjelasan_indonesia_json, result_json, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        budget = result.recommended_plan.risk_budget_usdt if result.recommended_plan else 1.00
        params = (
            result.analysis_id,
            result.symbol.upper(),
            result.timeframe.lower(),
            result.timestamp_ms,
            result.datetime_utc,
            result.primary_bias,
            result.recommendation,
            result.confidence_score,
            result.uncertainty_score,
            budget,
            result.model_id,
            json.dumps(result.market_snapshot.to_dict()),
            json.dumps(result.long_scenario.to_dict()),
            json.dumps(result.short_scenario.to_dict()),
            json.dumps(result.recommended_plan.to_dict()) if result.recommended_plan else "{}",
            json.dumps(result.penjelasan_indonesia.to_dict()),
            json.dumps(result.to_dict()),
            datetime.now(timezone.utc).isoformat(),
        )
        with self.db.get_connection() as conn:
            conn.execute(query, params)
            conn.commit()

    def get_latest_cached(
        self,
        symbol: str,
        timeframe: str,
        max_age_ms: int = 60000,
        now_ms: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve recent analysis for deduplication if within max_age_ms."""
        current_time = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
        cutoff_ms = current_time - max_age_ms

        query = """
        SELECT result_json FROM ai_analyst_records
        WHERE symbol = ? AND timeframe = ? AND timestamp_ms >= ?
        ORDER BY timestamp_ms DESC LIMIT 1;
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (symbol.upper(), timeframe.lower(), cutoff_ms))
            row = cur.fetchone()

        if row and row[0]:
            try:
                res = json.loads(row[0])
                res["is_cached"] = True
                return res
            except Exception:
                return None
        return None

    def get_history(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query historical analysis records with optional filtering."""
        conditions = []
        params: List[Any] = []

        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol.upper())
        if timeframe:
            conditions.append("timeframe = ?")
            params.append(timeframe.lower())

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"""
        SELECT result_json FROM ai_analyst_records
        {where}
        ORDER BY timestamp_ms DESC
        LIMIT ? OFFSET ?;
        """
        params.extend([limit, offset])

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()

        results = []
        for r in rows:
            if r and r[0]:
                try:
                    results.append(json.loads(r[0]))
                except Exception:
                    pass
        return results

    def get_by_id(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific analysis record by ID."""
        query = "SELECT result_json FROM ai_analyst_records WHERE analysis_id = ? LIMIT 1;"
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, (analysis_id,))
            row = cur.fetchone()

        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception:
                return None
        return None
