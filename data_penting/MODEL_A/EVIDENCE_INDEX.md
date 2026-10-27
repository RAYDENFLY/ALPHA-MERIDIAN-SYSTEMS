# Model A — Evidence Index & Artifact Registry

**Archive Document:** `data_penting/MODEL_A/EVIDENCE_INDEX.md`  
**Purpose:** Comprehensive index of all reports, datasets, logs, and configuration files supporting the Model A forensic analysis.

---

## 1. Primary Research Reports & Postmortems

| Artifact Path | Format | Date | Description & Key Evidence |
| :--- | :---: | :---: | :--- |
| [`docs/profitability_failure_attribution.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/docs/profitability_failure_attribution.md) | Markdown | 2026-10-17 | 11-dimension failure attribution, stop-loss clamping defect analysis, temporal clustering forensics. |
| [`docs/profitability_phase0_audit.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/docs/profitability_phase0_audit.md) | Markdown | 2026-10-17 | Audit of 0 forward paper trades in `data/scanner.db`, candle sync pagination bug, ML wrapper deserialization defect. |
| [`docs/profitability_baseline_report.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/docs/profitability_baseline_report.md) | Markdown | 2026-10-17 | Official benchmark results for baselines B1, B2, B3, and B4. |
| [`ROOT_CAUSE_AND_RECOMMENDATION.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/ROOT_CAUSE_AND_RECOMMENDATION.md) | Markdown | 2026-10-18 | Executive summary of Model A signal drought and recommendations leading to SMC 15m sweep reclaim. |
| [`docs/PROFITABILITY_REMEDIATION_FINAL_REPORT.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/docs/PROFITABILITY_REMEDIATION_FINAL_REPORT.md) | Markdown | 2026-10-18 | Final audit of Phase 1 remediation, stop distance enforcement, and portfolio trade attribution. |
| [`backtest_results/model_a_vs_model_b_comparison/REPORT.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/backtest_results/model_a_vs_model_b_comparison/REPORT.md) | Markdown | 2026-10-24 | 12-month head-to-head backtest of `MODEL_A_FROZEN` vs `MODEL_A_TUNED`. |

---

## 2. Quantitative Datasets & CSV Results

| Dataset Path | Size | Description |
| :--- | :---: | :--- |
| [`baseline_trades.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/baseline_trades.csv) | 32.3 KB | 92 raw candidate setups, 88 feasible standalone setups, 16 portfolio executed trades. |
| [`baseline_vs_experiments.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/baseline_vs_experiments.csv) | 1.5 KB | Comparative metrics across Model A baseline vs experimental iterations. |
| [`phase1_ablation_results.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/phase1_ablation_results.csv) | 2.8 KB | Ablation study isolating dealing range thresholds, volume ratios, and stop clamping. |
| [`candidate_rejection_attribution.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/candidate_rejection_attribution.csv) | 0.7 KB | Categorized breakdown of setup rejections by filter type. |
| [`weekly_policy_comparison.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/weekly_policy_comparison.csv) | 0.8 KB | Evaluation of 3-entry weekly cap vs unlimited entries. |

---

## 3. Reference Implementation Files

| Source File | Function / Section | Role in Model A |
| :--- | :--- | :--- |
| [`src/strategy/generator.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/generator.py#L139-L178) | `is_dealing_range()`, `generate_setups()` | 12-parameter dealing range pre-screen and candidate creation. |
| [`src/strategy/planner.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/planner.py#L72-L97) | `plan_levels()` | The artificial 2.2% / 2.4% stop-loss clamping logic. |
| [`src/backtest/engine.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/backtest/engine.py) | `BacktestEngine.run()` | Event-driven 1H portfolio backtester that exposed first-come symbol bias. |
| [`src/ml/calibration.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/ml/calibration.py) | `CalibratedModelWrapper` | Logistic regression ML gate wrapper that suffered deserialization bugs. |
