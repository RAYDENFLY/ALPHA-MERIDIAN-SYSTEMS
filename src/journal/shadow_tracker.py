"""Shadow & Counterfactual Tracking Engine for SWEEP_1M_V1 and LLM Vetoes.

Persists and queries counterfactual setups without polluting live realized PnL.
Enforces:
- Exact recording of MISS, EXPIRED, FILLED, and CLOSED outcomes.
- Tracking of whether Global Position Lock would have blocked the trade.
- Recording of LLM candidate selection, LLM veto, and veto rationale.
- Calculation of counterfactual expectancy and stop distribution.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from src.data.database import DatabaseManager
from src.strategy.sweep_1m import Sweep1MSetup


class ShadowTracker:
    """Manages persistence and metrics for shadow/counterfactual strategies."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create shadow_counterfactual_journal table if not present."""
        query = """
        CREATE TABLE IF NOT EXISTS shadow_counterfactual_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL,
            config_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            direction TEXT NOT NULL,
            market_regime TEXT DEFAULT 'UNKNOWN',
            llm_selected INTEGER DEFAULT 0,
            llm_veto INTEGER DEFAULT 0,
            llm_veto_reason TEXT DEFAULT '',
            deterministic_trigger TEXT NOT NULL,
            hypothetical_entry REAL NOT NULL,
            hypothetical_stop REAL NOT NULL,
            hypothetical_target REAL NOT NULL,
            stop_distance_pct REAL NOT NULL,
            ttl_bars INTEGER NOT NULL,
            fill_status TEXT NOT NULL,
            counterfactual_r REAL DEFAULT 0.0,
            blocked_by_global_lock INTEGER DEFAULT 0,
            price_path_json TEXT DEFAULT '[]',
            outcome_summary TEXT DEFAULT '',
            details_json TEXT DEFAULT '{}',
            created_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_strat_sym ON shadow_counterfactual_journal (strategy_id, symbol);
        """
        with self.db.get_connection() as conn:
            conn.executescript(query)
            conn.commit()

    def record_shadow_setup(
        self,
        setup: Sweep1MSetup,
        market_regime: str = "UNKNOWN",
        llm_selected: bool = False,
        llm_veto: bool = False,
        llm_veto_reason: str = "",
    ) -> int:
        """Persist a shadow setup outcome."""
        now_utc = datetime.now(timezone.utc).isoformat()
        price_path_str = json.dumps(setup.price_path)
        details_str = json.dumps(setup.to_dict())

        query = """
        INSERT INTO shadow_counterfactual_journal (
            strategy_id, config_version, symbol, timestamp_ms, direction,
            market_regime, llm_selected, llm_veto, llm_veto_reason,
            deterministic_trigger, hypothetical_entry, hypothetical_stop,
            hypothetical_target, stop_distance_pct, ttl_bars, fill_status,
            counterfactual_r, blocked_by_global_lock, price_path_json,
            outcome_summary, details_json, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            setup.strategy_id,
            setup.config_version,
            setup.symbol,
            setup.trigger_candle_time_ms,
            setup.direction.value if hasattr(setup.direction, "value") else str(setup.direction),
            market_regime,
            1 if llm_selected else 0,
            1 if llm_veto else 0,
            llm_veto_reason,
            "; ".join(setup.reasons),
            setup.entry_price,
            setup.stop_loss,
            setup.take_profit,
            setup.stop_distance_pct,
            setup.ttl_bars,
            setup.fill_status,
            setup.counterfactual_r,
            1 if setup.blocked_by_global_lock else 0,
            price_path_str,
            setup.outcome_summary,
            details_str,
            now_utc,
        )
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            conn.commit()
            return cur.lastrowid

    def get_shadow_records(
        self,
        strategy_id: Optional[str] = "SWEEP_1M_V1",
        symbol: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Retrieve historical shadow counterfactual records."""
        conditions = []
        params = []
        if strategy_id:
            conditions.append("strategy_id = ?")
            params.append(strategy_id)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
        SELECT * FROM shadow_counterfactual_journal
        {where_clause}
        ORDER BY timestamp_ms DESC
        LIMIT ?;
        """
        params.append(limit)

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    get_shadow_journal_entries = get_shadow_records

    def get_shadow_expectancy_summary(
        self,
        strategy_id: str = "SWEEP_1M_V1",
    ) -> Dict[str, Any]:
        """Compute aggregate counterfactual expectancy metrics without mixing into live PnL."""
        records = self.get_shadow_records(strategy_id=strategy_id, limit=5000)
        total = len(records)
        if total == 0:
            return {
                "strategy_id": strategy_id,
                "total_hypothetical_setups": 0,
                "miss_count": 0,
                "expired_count": 0,
                "filled_count": 0,
                "miss_rate_pct": 0.0,
                "expired_rate_pct": 0.0,
                "fill_rate_pct": 0.0,
                "counterfactual_win_rate_pct": 0.0,
                "average_counterfactual_r": 0.0,
                "blocked_by_global_lock_count": 0,
                "conclusion": "INSUFFICIENT_SHADOW_EVIDENCE",
            }

        miss_cnt = sum(1 for r in records if r.get("fill_status") == "MISS")
        exp_cnt = sum(1 for r in records if r.get("fill_status") == "EXPIRED")
        filled_cnt = sum(1 for r in records if r.get("fill_status") == "FILLED")
        blocked_cnt = sum(1 for r in records if r.get("blocked_by_global_lock") == 1)

        filled_records = [r for r in records if r.get("fill_status") == "FILLED"]
        wins = sum(1 for r in filled_records if float(r.get("counterfactual_r", 0.0)) > 0)
        win_rate = (wins / len(filled_records) * 100.0) if filled_records else 0.0
        avg_r = (sum(float(r.get("counterfactual_r", 0.0)) for r in records) / total) if total else 0.0

        return {
            "strategy_id": strategy_id,
            "total_hypothetical_setups": total,
            "miss_count": miss_cnt,
            "expired_count": exp_cnt,
            "filled_count": filled_cnt,
            "miss_rate_pct": round(miss_cnt / total * 100.0, 2),
            "expired_rate_pct": round(exp_cnt / total * 100.0, 2),
            "fill_rate_pct": round(filled_cnt / total * 100.0, 2),
            "counterfactual_win_rate_pct": round(win_rate, 2),
            "average_counterfactual_r": round(avg_r, 3),
            "blocked_by_global_lock_count": blocked_cnt,
            "conclusion": "SHADOW_MONITORING_ACTIVE",
        }
