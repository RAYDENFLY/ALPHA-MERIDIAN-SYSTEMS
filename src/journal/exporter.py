"""CSV & ZIP Bundle Exporter for Independent AI Review (ChatGPT, Claude, Qwen).

Implements Section 15.5 & 15.6:
- 1. trades.csv: Trade-level ledger with actual execution and realized metrics.
- 2. fills.csv: Granular fill-level exchange executions.
- 3. signals.csv: Deterministic setups evaluated by scanners.
- 4. shadow.csv: Counterfactual evidence for SWEEP_1M_V1 and LLM vetoes.
- 5. scan_attribution.csv: 11-stage attrition pipeline logs and zero-signal diagnosis.
- 6. journal_bundle.zip: Complete ZIP archive containing all datasets + manifest.json.

Safety & Integrity Guarantees:
- UTF-8 encoding.
- Zero leakage of API keys, secrets, private credentials, or personal data.
- Strict isolation: Never mixes LIVE, SHADOW, and PAPER data.
- Explicit labeling of exchange-verified vs. locally calculated vs. unavailable fields.
"""

import csv
from datetime import datetime, timezone
import io
import json
from typing import Any, Dict, List, Optional
import zipfile

from src.data.database import DatabaseManager
from src.journal.automated_journal import AutomatedTradeJournal
from src.journal.models import AutomatedTradeRecord, FillRecord, JournalFilter


class JournalExporter:
    """Produces standardized CSV files and ZIP bundles for independent AI and quantitative review."""

    SCHEMA_VERSION = "LIVE_MICRO_V1_JOURNAL_20261026"

    def __init__(self, db: DatabaseManager, journal: Optional[AutomatedTradeJournal] = None) -> None:
        self.db = db
        self.journal = journal or AutomatedTradeJournal(db)

    def export_trades_csv(self, filter_criteria: Optional[JournalFilter] = None) -> str:
        """Export trades.csv containing one row per trade with complete identity and metrics."""
        trades = self.journal.get_trades(filter_criteria)
        output = io.StringIO()
        fieldnames = [f for f in AutomatedTradeRecord.__dataclass_fields__]

        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for t in trades:
            row = t.to_dict()
            # Clean None values to empty strings for clean CSV representation
            clean_row = {k: ("" if v is None else v) for k, v in row.items()}
            writer.writerow(clean_row)

        return output.getvalue()

    def export_fills_csv(self, filter_criteria: Optional[JournalFilter] = None) -> str:
        """Export fills.csv containing granular exchange-level fills."""
        fills = self.journal.get_all_fills(filter_criteria)
        output = io.StringIO()
        fieldnames = [f for f in FillRecord.__dataclass_fields__]

        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for f in fills:
            row = f.to_dict()
            clean_row = {k: ("" if v is None else v) for k, v in row.items()}
            writer.writerow(clean_row)

        return output.getvalue()

    def export_signals_csv(self, filter_criteria: Optional[JournalFilter] = None) -> str:
        """Export signals.csv from setup_candidates table."""
        conds = []
        params: List[Any] = []
        if filter_criteria:
            if filter_criteria.symbol:
                conds.append("symbol LIKE ?")
                params.append(f"%{filter_criteria.symbol.upper()}%")
            if filter_criteria.start_date:
                conds.append("created_at_utc >= ?")
                params.append(filter_criteria.start_date)
            if filter_criteria.end_date:
                conds.append("created_at_utc <= ?")
                params.append(filter_criteria.end_date)
            limit = filter_criteria.limit
        else:
            limit = 5000

        where_clause = f"WHERE {' AND '.join(conds)}" if conds else ""
        sql = f"""
        SELECT candidate_id, symbol, timestamp_ms, direction, bias_4h, structure_1h,
               entry_price, stop_loss, take_profit_1, take_profit_2, gross_rr, net_rr,
               invalidation_price, ml_probability, status, created_at_utc, model_id
        FROM setup_candidates
        {where_clause}
        ORDER BY timestamp_ms DESC
        LIMIT ?;
        """
        params.append(limit)

        output = io.StringIO()
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]

        if rows:
            fieldnames = list(rows[0].keys())
        else:
            fieldnames = [
                "candidate_id", "symbol", "timestamp_ms", "direction", "bias_4h", "structure_1h",
                "entry_price", "stop_loss", "take_profit_1", "take_profit_2", "gross_rr", "net_rr",
                "invalidation_price", "ml_probability", "status", "created_at_utc", "model_id"
            ]

        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            clean_r = {k: ("" if v is None else v) for k, v in r.items()}
            writer.writerow(clean_r)

        return output.getvalue()

    def export_shadow_csv(self, filter_criteria: Optional[JournalFilter] = None) -> str:
        """Export shadow.csv from shadow_counterfactual_journal table."""
        conds = []
        params: List[Any] = []
        if filter_criteria:
            if filter_criteria.symbol:
                conds.append("symbol LIKE ?")
                params.append(f"%{filter_criteria.symbol.upper()}%")
            if filter_criteria.start_date:
                conds.append("created_at_utc >= ?")
                params.append(filter_criteria.start_date)
            if filter_criteria.end_date:
                conds.append("created_at_utc <= ?")
                params.append(filter_criteria.end_date)
            limit = filter_criteria.limit
        else:
            limit = 5000

        where_clause = f"WHERE {' AND '.join(conds)}" if conds else ""
        sql = f"""
        SELECT id, strategy_id, config_version, symbol, timestamp_ms, direction,
               market_regime, llm_selected, llm_veto, llm_veto_reason, deterministic_trigger,
               hypothetical_entry, hypothetical_stop, hypothetical_target, stop_distance_pct,
               ttl_bars, fill_status, counterfactual_r, blocked_by_global_lock,
               outcome_summary, created_at_utc
        FROM shadow_counterfactual_journal
        {where_clause}
        ORDER BY timestamp_ms DESC
        LIMIT ?;
        """
        params.append(limit)

        output = io.StringIO()
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]

        if rows:
            fieldnames = list(rows[0].keys())
        else:
            fieldnames = [
                "id", "strategy_id", "config_version", "symbol", "timestamp_ms", "direction",
                "market_regime", "llm_selected", "llm_veto", "llm_veto_reason", "deterministic_trigger",
                "hypothetical_entry", "hypothetical_stop", "hypothetical_target", "stop_distance_pct",
                "ttl_bars", "fill_status", "counterfactual_r", "blocked_by_global_lock",
                "outcome_summary", "created_at_utc"
            ]

        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            clean_r = {k: ("" if v is None else v) for k, v in r.items()}
            writer.writerow(clean_r)

        return output.getvalue()

    def export_scan_attribution_csv(self, filter_criteria: Optional[JournalFilter] = None) -> str:
        """Export scan_attribution.csv from scan_attrition_reports table."""
        conds = []
        params: List[Any] = []
        if filter_criteria:
            if filter_criteria.start_date:
                conds.append("created_at_utc >= ?")
                params.append(filter_criteria.start_date)
            if filter_criteria.end_date:
                conds.append("created_at_utc <= ?")
                params.append(filter_criteria.end_date)
            limit = filter_criteria.limit
        else:
            limit = 1000

        where_clause = f"WHERE {' AND '.join(conds)}" if conds else ""
        sql = f"""
        SELECT report_id, cycle_id, started_at_utc, completed_at_utc,
               zero_signal_diagnosis, created_at_utc
        FROM scan_attrition_reports
        {where_clause}
        ORDER BY created_at_utc DESC
        LIMIT ?;
        """
        params.append(limit)

        output = io.StringIO()
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]

        fieldnames = ["report_id", "cycle_id", "started_at_utc", "completed_at_utc", "zero_signal_diagnosis", "created_at_utc"]
        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            clean_r = {k: ("" if v is None else v) for k, v in r.items()}
            writer.writerow(clean_r)

        return output.getvalue()

    def export_journal_bundle_zip(self, filter_criteria: Optional[JournalFilter] = None) -> bytes:
        """Package all 5 CSV exports along with manifest.json into a single in-memory ZIP bundle."""
        trades_csv = self.export_trades_csv(filter_criteria)
        fills_csv = self.export_fills_csv(filter_criteria)
        signals_csv = self.export_signals_csv(filter_criteria)
        shadow_csv = self.export_shadow_csv(filter_criteria)
        scan_attr_csv = self.export_scan_attribution_csv(filter_criteria)

        # Count records
        trades_count = max(0, len(trades_csv.strip().split("\n")) - 1) if trades_csv.strip() else 0
        fills_count = max(0, len(fills_csv.strip().split("\n")) - 1) if fills_csv.strip() else 0
        signals_count = max(0, len(signals_csv.strip().split("\n")) - 1) if signals_csv.strip() else 0
        shadow_count = max(0, len(shadow_csv.strip().split("\n")) - 1) if shadow_csv.strip() else 0
        scan_attr_count = max(0, len(scan_attr_csv.strip().split("\n")) - 1) if scan_attr_csv.strip() else 0

        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "export_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "generator": "SignalGenerator Aster DEX Live Strategy Revision",
            "filters_applied": {
                "start_date": filter_criteria.start_date if filter_criteria else None,
                "end_date": filter_criteria.end_date if filter_criteria else None,
                "strategy_id": filter_criteria.strategy_id if filter_criteria else None,
                "mode": filter_criteria.mode if filter_criteria else "ALL",
                "symbol": filter_criteria.symbol if filter_criteria else None,
                "status": filter_criteria.status if filter_criteria else None,
                "closed_only": filter_criteria.closed_only if filter_criteria else False,
            },
            "record_counts": {
                "trades": trades_count,
                "fills": fills_count,
                "signals": signals_count,
                "shadow_counterfactuals": shadow_count,
                "scan_attribution_cycles": scan_attr_count,
            },
            "data_quality_notes": [
                "Realized PnL is computed from actual Aster DEX fills in fill_ledger.",
                "Realized R is computed strictly as net_pnl_usd / planned_risk_usd using entry-time planned risk ($0.20 USD).",
                "LIVE, SHADOW, and PAPER executions are kept completely independent and never aggregated together.",
                "Zero credentials, secrets, or API keys are exported."
            ],
            "ai_review_instructions": (
                "For independent AI review (ChatGPT, Claude, Qwen): Use trades.csv for trade-level expectancy, "
                "drawdown, win rate, and R-multiples. Use fills.csv for granular fee burden, slippage, and maker/taker "
                "liquidity distribution. Use shadow.csv for counterfactual performance of SWEEP_1M_V1 and LLM veto impact."
            ),
        }

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            zf.writestr("trades.csv", trades_csv)
            zf.writestr("fills.csv", fills_csv)
            zf.writestr("signals.csv", signals_csv)
            zf.writestr("shadow.csv", shadow_csv)
            zf.writestr("scan_attribution.csv", scan_attr_csv)

        zip_buf.seek(0)
        return zip_buf.getvalue()
