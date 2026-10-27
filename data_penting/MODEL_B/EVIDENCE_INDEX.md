# Model B — Evidence Index & Artifact Registry

**Archive Document:** `data_penting/MODEL_B/EVIDENCE_INDEX.md`  
**Purpose:** Index of all backtest reports, CSV results, scripts, and logs supporting the Model B failure forensics.

---

## 1. Primary Research Reports & Multi-Cohort Evaluations

| Artifact Path | Format | Date | Description |
| :--- | :---: | :---: | :--- |
| [`backtest_results/model_a_vs_model_b_comparison/REPORT.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/backtest_results/model_a_vs_model_b_comparison/REPORT.md) | Markdown | 2026-10-24 | 12-month head-to-head backtest comparing Model A vs Model B (V1, V2 Fast, V2 1H, V3). |
| [`backtest_results/model_b_fast_annual_top10/BACKTEST_REPORT.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/backtest_results/model_b_fast_annual_top10/BACKTEST_REPORT.md) | Markdown | 2026-10-24 | Annual backtest results of Model B Fast across Top 10 pairs. |
| [`backtest_results/model_b_1h_annual_top10/REPORT.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/backtest_results/model_b_1h_annual_top10/REPORT.md) | Markdown | 2026-10-24 | Evaluation of shifting Model B execution from 15m to 1H candles. |

---

## 2. Quantitative Backtest CSV Datasets

| Dataset Path | Rows / Size | Description |
| :--- | :---: | :--- |
| [`backtest_results/model_b_v1_v2_v3_comparison/master_portfolio_comparison.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/backtest_results/model_b_v1_v2_v3_comparison/master_portfolio_comparison.csv) | Full Portfolio | Summary metrics of V1, V2, and V3 across all 11 pairs. |
| [`backtest_results/model_b_v1_v2_v3_comparison/top10_pairs_v1_v2_v3_comparison.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/backtest_results/model_b_v1_v2_v3_comparison/top10_pairs_v1_v2_v3_comparison.csv) | Per-Symbol | Per-pair win rate, net PnL, fee drag, and max DD. |
| [`backtest_results/model_b_v1_v2_v3_comparison/v3_portfolio_trades.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/backtest_results/model_b_v1_v2_v3_comparison/v3_portfolio_trades.csv) | 3,757 Trades | Individual trade execution records for Model B V3. |
| [`backtest_results/model_b_v3_grid_sweep/grid_sweep_results.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/backtest_results/model_b_v3_grid_sweep/grid_sweep_results.csv) | Parameter Grid | Multi-parameter grid sweep evaluating stop-loss floors and TP targets. |
| [`backtest_results/model_b_refined_annual/master_refined_comparison.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/backtest_results/model_b_refined_annual/master_refined_comparison.csv) | Refined Runs | Evaluation of chop pair exclusion (`BNB`, `AVAX`, `XRP`). |

---

## 3. Reference Implementation Scripts

| Script Path | Purpose in Research |
| :--- | :--- |
| [`src/strategy/model_b/generator.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/model_b/generator.py) | 15m breakout setup generation logic. |
| [`src/strategy/model_b/evaluator.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/model_b/evaluator.py) | Model B setup evaluation and variance ratio filtering. |
| [`src/strategy/model_b/risk_controls.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/model_b/risk_controls.py) | 1.20% minimum stop-loss floor enforcement. |
| [`src/backtest/model_b_v3_fast_grid_sweep.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/backtest/model_b_v3_fast_grid_sweep.py) | Automated grid search harness across 15m data. |
