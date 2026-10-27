# SignalGenerator — Research Failure Archive & Technical Blueprint (`data_penting/`)

**Archive Created:** 2026-10-27  
**Repository:** `SignalGenerator`  
**Branch:** `research/profitability-validation`  
**Purpose:** Permanent, immutable archive of verified quantitative research failures, empirical backtests, failure attribution reports, legacy deletion safety audits, and the production Groq AI Auto-Hunter architecture blueprint.

---

## 1. Archive Purpose & Governance Mandate

The `data_penting/` directory houses the institutional memory of the SignalGenerator algorithmic trading system. It preserves the critical lessons learned from failed model iterations (`MODEL_A`, `MODEL_B`, `MODE_2C`) and establishes strict architectural guardrails to prevent repeating past mistakes during future research and the desktop migration.

### Mandatory Pre-Experiment Governance Rule:
> **Any future quantitative research, new model proposal, or backtest execution MUST review this archive and document in its hypothesis which historical failure modes (e.g. Dealing Range starvation, fee drag on 15m scalps, structural stop clamping, or adverse selection on limit breakouts) it explicitly addresses.**

---

## 2. Archive Directory Structure

```text
data_penting/
├── README.md                      # This document (Index, governance, and archive mandate)
├── PROJECT_HISTORY.md             # Chronological evolution: Model A -> Model B -> Mode 2C -> Groq Auto-Hunter
├── GROQ_AUTO_HUNTER_BLUEPRINT.md  # Complete 12-stage production trading engine architecture & dependencies
├── DELETION_SAFETY_AUDIT.md       # File-by-file legacy code deletion audit & protection classifications
├── CROSS_MODEL_LESSONS.md         # Synthesized empirical truths across all model experiments
├── REPEATED_FAILURE_PREVENTION.md # Pre-experiment verification checklist and methodological safeguards
├── MODEL_A/
│   ├── FAILURE_REPORT.md          # 12-parameter dealing range overfitting, 27-day starvation, 2.2% SL clamp flaw
│   ├── EXPERIMENTS.md             # Baseline comparisons (B1-B4), Model A Frozen vs Tuned, 11-dimension attribution
│   └── EVIDENCE_INDEX.md          # Pointers to root reports, CSVs, and audit artifacts
└── MODEL_B/
    ├── FAILURE_REPORT.md          # 11,594-trade backtest ruin, $1,118 fee drag, tight stop losses <1.15%
    ├── EXPERIMENTS.md             # V1, V2 Fast, V2 1H, V3 Tuned, Grid Sweep, and fee ablation data
    └── EVIDENCE_INDEX.md          # Pointers to multi-model comparison datasets and backtest outputs
```

---

## 3. Preservation & Immutability Rules

1. **Strict Read-Only Preservation:** Files in this directory are authoritative historical records. They must not be modified, truncated, or removed during routine refactoring.
2. **Conflict Preservation:** If two historical reports contain conflicting conclusions or numbers, the conflict is explicitly recorded with citations rather than silently smoothed over.
3. **No Synthetic Fabrication:** All numbers, win rates, expectancies, and drawdowns cite specific CSV files, test runs, or existing audit reports. Where metrics are missing, they are marked as `UNKNOWN` or `NOT_FOUND`.
4. **Desktop Migration Isolation:** This archive ensures that during the transition to the C# / Avalonia desktop client, no core trading engine logic or historical research context is accidentally discarded.
