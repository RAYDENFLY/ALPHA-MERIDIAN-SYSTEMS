"""Stage-by-Stage Scanner Attrition Tracker.

Records exact attrition metrics and rejection reasons for every scan cycle across:
1. Universe: Total instruments scanned
2. Market data: Valid fresh data
3. Spread: Passed spread filter
4. Liquidity: Passed volume/turnover filter
5. Volatility: Passed volatility bounds
6. Candidate ranking: Selected candidates
7. Strategy trigger: Valid deterministic triggers
8. Risk gate: Passed risk and margin checks ($0.20 risk, $7.00 margin cap)
9. Cost gate: Passed net-RR gate (Expected Net-RR >= 1.50) & stop bounds [0.40%, 0.80%]
10. Global lock: Rejected due to active position
11. Executor: Submitted, rejected, or errored

Guarantees that a scan with zero signals is fully diagnosable without relaxing filters.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class AttritionStageRecord:
    stage_name: str
    passed_count: int
    rejected_count: int
    rejection_reasons: Dict[str, int] = field(default_factory=dict)


class ScannerAttritionTracker:
    """Tracks attrition pipeline for a scan cycle."""

    STAGES_ORDER = [
        "Universe",
        "Market data",
        "Spread",
        "Liquidity",
        "Volatility",
        "Candidate ranking",
        "Strategy trigger",
        "Risk gate",
        "Cost gate",
        "Global lock",
        "Executor",
    ]

    def __init__(self, cycle_id: Optional[str] = None) -> None:
        self.cycle_id = cycle_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.started_at_utc = datetime.now(timezone.utc).isoformat()
        self.completed_at_utc: Optional[str] = None
        self.stages: Dict[str, AttritionStageRecord] = {}
        self.symbol_rejections: Dict[str, List[str]] = {}
        self.zero_signal_diagnosis: str = ""

        # Initialize default zero counts
        for stage in self.STAGES_ORDER:
            self.stages[stage] = AttritionStageRecord(stage_name=stage, passed_count=0, rejected_count=0)

    def record_stage(
        self,
        stage: str,
        passed_count: int,
        rejected_count: int = 0,
        rejection_reasons: Optional[Dict[str, int]] = None,
    ) -> None:
        """Record count and rejection breakdown for a pipeline stage."""
        if stage not in self.stages:
            self.stages[stage] = AttritionStageRecord(stage_name=stage, passed_count=0, rejected_count=0)

        record = self.stages[stage]
        record.passed_count = passed_count
        if rejected_count > 0:
            record.rejected_count = rejected_count
        elif record.rejection_reasons:
            record.rejected_count = sum(record.rejection_reasons.values())
        if rejection_reasons:
            for reason, cnt in rejection_reasons.items():
                record.rejection_reasons[reason] = record.rejection_reasons.get(reason, 0) + cnt

    def log_symbol_rejection(self, symbol: str, stage: str, reason: str) -> None:
        """Log granular rejection reason for a specific symbol."""
        if symbol not in self.symbol_rejections:
            self.symbol_rejections[symbol] = []
        self.symbol_rejections[symbol].append(f"[{stage}] {reason}")

        if stage in self.stages:
            reasons = self.stages[stage].rejection_reasons
            reasons[reason] = reasons.get(reason, 0) + 1
            self.stages[stage].rejected_count += 1

    def finalize(self) -> None:
        """Finalize the scan cycle and generate diagnostic explanation."""
        self.completed_at_utc = datetime.now(timezone.utc).isoformat()
        exec_passed = self.stages.get("Executor", AttritionStageRecord("Executor", 0, 0)).passed_count

        if exec_passed == 0:
            # Diagnose which stage caused zero final signals
            bottleneck_stage = "Unknown"
            bottleneck_reason = "No data"
            for stage in self.STAGES_ORDER:
                rec = self.stages.get(stage)
                if rec and rec.passed_count == 0:
                    bottleneck_stage = stage
                    if rec.rejection_reasons:
                        top_reason = max(rec.rejection_reasons.items(), key=lambda x: x[1])
                        bottleneck_reason = f"{top_reason[0]} ({top_reason[1]} occurrences)"
                    else:
                        bottleneck_reason = f"All candidates dropped at {stage}"
                    break
            self.zero_signal_diagnosis = (
                f"Zero signals produced in cycle {self.cycle_id}. "
                f"Primary bottleneck: Stage '{bottleneck_stage}' ({bottleneck_reason})."
            )
        else:
            self.zero_signal_diagnosis = f"Cycle {self.cycle_id} produced {exec_passed} executable signal(s)."

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "stages": {
                name: {
                    "passed": rec.passed_count,
                    "rejected": rec.rejected_count,
                    "rejection_reasons": rec.rejection_reasons,
                }
                for name, rec in self.stages.items()
            },
            "zero_signal_diagnosis": self.zero_signal_diagnosis,
            "symbol_rejections_sample": dict(list(self.symbol_rejections.items())[:15]),
        }

    def to_markdown_table(self) -> str:
        """Render a formatted markdown table conforming to Section 10 spec."""
        rows = []
        rows.append("| Stage | Passed | Rejected | Top Rejection Reasons |")
        rows.append("|:---|:---:|:---:|:---|")

        for stage in self.STAGES_ORDER:
            rec = self.stages.get(stage, AttritionStageRecord(stage, 0, 0))
            if rec.rejection_reasons:
                reasons_str = ", ".join(f"{k} ({v})" for k, v in sorted(rec.rejection_reasons.items(), key=lambda x: -x[1])[:3])
            else:
                reasons_str = "None"
            rows.append(f"| {stage} | {rec.passed_count} | {rec.rejected_count} | {reasons_str} |")

        return "\n".join(rows)

    def persist_to_db(self, db: Any) -> None:
        """Persist this attrition report to scan_attrition_reports table."""
        import json
        now_utc = datetime.now(timezone.utc).isoformat()
        report_id = f"attrition_{self.cycle_id}"
        table_md = self.to_markdown_table()
        payload = json.dumps(self.to_dict())

        query = """
        INSERT OR REPLACE INTO scan_attrition_reports (
            report_id, cycle_id, started_at_utc, completed_at_utc,
            zero_signal_diagnosis, attrition_table_markdown, payload_json, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """
        with db.get_connection() as conn:
            conn.execute(
                query,
                (
                    report_id,
                    self.cycle_id,
                    self.started_at_utc,
                    self.completed_at_utc or now_utc,
                    self.zero_signal_diagnosis,
                    table_md,
                    payload,
                    now_utc,
                ),
            )
            conn.commit()

