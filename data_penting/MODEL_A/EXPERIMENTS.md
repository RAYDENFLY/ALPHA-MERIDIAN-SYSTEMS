# Model A — Experiment Registry & Quantitative Ablation Matrix

**Archive Document:** `data_penting/MODEL_A/EXPERIMENTS.md`  
**Model Name:** `MODEL_A`  
**Dataset Reference:** `baseline_trades.csv`, `phase1_ablation_results.csv`, `baseline_vs_experiments.csv`  

---

## 1. Baseline Configurations & Out-of-Sample Performance

### 1.1 Four Standard Baseline Variants Tested

| Baseline ID | Description | Execution Model | Feasible Setups | Executed Trades | Net PnL | Win Rate | Expectancy | Profit Factor |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **B1** | Dashboard API Default (`[:3]` symbols) | Market Entry (0.05% fee + 0.05% slip) | 12 | 12 | +$7.90 | 58.33% | +$0.6581 | 2.05 |
| **B2** | Full 11-Symbol Portfolio (`BacktestEngine`) | Market Entry (0.05% fee + 0.05% slip) | 92 | 16 | +$7.43 | 56.25% | +$0.4644 | 1.71 |
| **B3** | Standalone All Feasible (Uncapped) | Market Entry (0.05% fee + 0.05% slip) | 88 | 88 | +$61.31 | 64.77% | +$0.6967 | 2.32 |
| **B4** | Standalone Paper Limit-Fill Model | Limit Entry (0.02% maker, 12-bar TTL) | 88 | 74 | +$41.94 | 62.16% | +$0.5668 | 2.70 |

---

## 2. Chronological Walk-Forward & OOS Breakdown

To verify true out-of-sample stability, Model A was divided into 3 chronological splits across 2025-2026:

| Split Period | Date Range | Total 1H Bars | Setups Generated | Trades ($N$) | Win Rate | Gross PnL | Net PnL | Realized Expectancy |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TRAIN** | `2025-10-14` to `2026-06-30` | 62,304 | 32 | 8 | 75.0% | +$8.54 | +$7.43 | +$0.9287 / trade |
| **VAL** | `2026-07-01` to `2026-08-31` | 16,368 | 52 | 6 | 50.0% | +$1.17 | -$1.50 | -$0.2500 / trade |
| **OOS_TEST** | `2026-09-01` to `2026-10-17` | 16,343 | 8 | 2 | 0.0% | -$3.00 | -$1.50 | -$0.7500 / trade |

### Quantitative Finding:
Performance degraded severely over chronological time. In `TRAIN`, the model achieved 75.0% win rate; in `VAL`, win rate dropped to 50.0% (net negative after fees); in `OOS_TEST`, it suffered 100% loss rate.

---

## 3. Head-to-Head Comparison: `MODEL_A_FROZEN` vs `MODEL_A_TUNED`

Source: `backtest_results/model_a_vs_model_b_comparison/REPORT.md` (365-day backtest across all 11 whitelisted pairs):

| Metric | `MODEL_A_FROZEN` (Strict Extreme Filter) | `MODEL_A_TUNED` (Relaxed Filter + Full TP1) | Delta / Assessment |
| :--- | :---: | :---: | :--- |
| **Dealing Range Bounds** | $\le 12\%$ / $\ge 88\%$ | $\le 30\%$ / $\ge 70\%$ | 2.5x wider search zone |
| **Impulse Requirements** | 6.2% / 2.2% | 3.5% / 1.2% | Relaxed momentum hurdle |
| **Exit Model** | 50% partial at 1.5R + trailing | 100% full exit at 1.65R | Eliminated runner fee bleed |
| **Total Trades** | **19 trades** (1.6 trades/month) | **169 trades** (14.1 trades/month) | **+8.9x Trade Volume** |
| **Win Rate** | **63.2%** | **41.4%** | -21.8% Win Rate |
| **Gross PnL** | +$13.86 | +$14.31 | +$0.45 Gross |
| **Total Fees Paid** | $0.58 | $5.22 | +$4.64 Fee Drag |
| **Net PnL** | **+$13.26** | **+$9.72** | **-$3.54 Net Profit** |
| **Profit Factor** | **2.80** | **1.09** | Edge severely compressed |
| **Max Drawdown** | $4.21 (3.7%) | $11.76 (9.7%) | 2.8x higher drawdown |
| **Expectancy / Trade**| **+0.729R** | **+0.086R** | Edge shrank by 88% |

---

## 4. Key Takeaway from Model A Experiments

Relaxing thresholds to fix Model A's signal starvation unlocked trade volume (+8.9x), but **massively degraded the statistical edge (expectancy plunged from +0.729R to +0.086R)**. This proved that simple parameter relaxation cannot solve structural flaws in 1H dealing-range breakout models.
