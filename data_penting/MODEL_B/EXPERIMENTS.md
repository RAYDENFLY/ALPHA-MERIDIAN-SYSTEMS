# Model B — Experiment Registry & Quantitative Ablation Matrix

**Archive Document:** `data_penting/MODEL_B/EXPERIMENTS.md`  
**Model Name:** `MODEL_B`  
**Dataset Reference:** `backtest_results/model_b_v1_v2_v3_comparison/`, `data/backtest_15m_top10.db`  

---

## 1. Experiment Registry: Multi-Cohort Evaluation

Model B underwent extensive engineering attempts to salvage the 15m intraday breakout model before being permanently retired in favor of `15M_SWEEP_RECLAIM_SMC`.

| Cohort ID | Core Strategy Modification | Execution Timeframe | Trades ($N$) | Win Rate | Fees Paid | Net PnL | Net Profit Factor |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **V1 Baseline** | Unconstrained 15m breakout & momentum continuation | 15m | 11,594 | 41.0% | $1,118.41 | -$1,577.17 | 0.73 |
| **V2 Fast** | Added variance ratio filter & volume surge confirmation | 15m | 5,983 | 39.5% | $461.16 | -$766.88 | 0.81 |
| **V2 1H Upgrade** | Shifted execution from 15m to 1H to reduce fee friction | 1H | 1,857 | 39.0% | $75.17 | -$151.74 | 0.87 |
| **V3 Tuned** | Hard 1.20% stop floor + blacklisted chop pairs (`BNB`, `AVAX`, `XRP`) | 15m | 3,757 | 37.0% | $207.16 | -$369.79 | 0.86 |
| **V3 Grid Sweep**| Systematic grid sweep across stop distance & TP multipliers | 15m | Varied | 34%–42% | Varied | Negative | 0.70–0.89 |

---

## 2. Stop-Distance Floor Ablation Study

To understand why tighter stops failed, an ablation was run varying the minimum allowed stop-loss distance across the 15m universe:

| Minimum Stop Floor | Total Trades | Win Rate | Average Winner ($) | Average Loser ($) | Net PnL | Net Profit Factor | Root Cause Finding |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **0.50% – 0.80%** | 8,241 | 33.2% | +$0.42 | -$0.28 | -$892.14 | 0.71 | Whipped by 15m orderbook spread and wick noise. |
| **0.80% – 1.15%** | 5,412 | 38.4% | +$0.58 | -$0.32 | -$412.30 | 0.82 | Moderate improvement, but fee friction consumed edge. |
| **$\ge$ 1.20% (V3)**| 3,757 | 37.0% | +$0.81 | -$0.35 | -$369.79 | 0.86 | Better survival, but breakout strategy in chop still negative. |

### Conclusion:
Sub-1.15% stops in crypto perpetuals are statistically unviable. However, widening the stop to 1.20% on a fundamentally flawed breakout strategy merely reduced the rate of loss; it did not turn the net expectancy positive.

---

## 3. Fee Drag & Slippage Friction Impact

Analysis of fee drag as a percentage of gross turnover:

- In `MODEL_B_V1`, gross trading result was **-$457.03**. Exchange fees and slippage added **-$1,118.41**, deepening the total loss by **+244%**!
- Even when an individual pair generated positive gross edge (e.g. `SOLUSDT` gross profit +$41.20), the round-trip maker/taker fees ($211.11) converted it into a **-$169.91 Net Loss**.
- This empirical evidence proved that **scalping crypto perpetuals with high turnover is mathematically structurally negative-sum unless average winning trade size is at least 5x to 8x the round-trip fee floor**.
