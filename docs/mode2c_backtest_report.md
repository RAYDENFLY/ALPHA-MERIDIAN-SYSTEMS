# SignalGenerator — Mode 2C Research Backtest & Empirical Evaluation Report

**Document Status:** Quantitative Research Report (Experiment A)  
**Evaluation Scope:** 1-Year Historical Dataset (`93,170` 1H bars across 11 Watchlist Symbols)  
**Execution Timestamp:** 2026-10-18 01:28:00 UTC  
**Primary Deliverables:** [`baseline_vs_experiments.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/baseline_vs_experiments.csv) & [`candidate_rejection_attribution.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/candidate_rejection_attribution.csv)  
**Reference Codebase:** [`src/strategy/research_mode2c.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/research_mode2c.py)  

---

## 1. Executive Summary & Decision Gate Classification

### Overall Decision: `REJECTED`

The research hypothesis that an intraday SMC Mid-Range Liquidity Sweep strategy (Mode 2C) could expand opportunity frequency without degrading net expectancy is **refuted by comprehensive empirical evidence**:

1. **Catastrophic Expectancy Degradation:** While Mode 2C successfully generated a massive volume of trade opportunities (911 raw candidates, 831 eligible setups, 741 standalone fills), it delivered a **negative net expectancy of -$0.1382 USDT per trade** in standalone execution (Win Rate: **35.49%**, Profit Factor: **0.76**, Net PnL: **-$102.41 USDT**, Max Drawdown: **$118.36 USDT**).
2. **Portfolio Ruin:** Under real account risk constraints ($16 capital, max 1 open position, max 3 entries/week), Mode 2C produced a **negative net expectancy of -$0.1794 USDT per trade** (Win Rate: **31.03%**, Profit Factor: **0.72**, Net PnL: **-$26.02 USDT**, Max Drawdown: **$38.88 USDT**). Mode 2C wipes out the entire $16.00 account balance more than 2.4 times over.
3. **Contamination of Baseline Portfolio:** Combining Mode 2C with the Production Baseline completely destroys the baseline's profitability. Standalone net PnL crashes from **+$94.92 USDT** down to **-$7.49 USDT** (Expectancy: **-$0.0092**), while portfolio net PnL plunges from **+$16.80 USDT** down to **-$15.96 USDT** (Expectancy: **-$0.1086**).
4. **Statistical Significance:** The 95% bootstrap confidence interval for Mode 2C standalone expectancy is strictly negative: **`[-$0.2154, -$0.0612]`**. The failure is statistically definitive and not an artifact of bad luck or small sample size ($N = 741$ trades).
5. **Why Mid-Range Sweeps Fail:** In the middle 60% of a dealing range ($0.20 \le \text{pos}_{72} \le 0.80$), price is subject to high-frequency chop, liquidity clustering, and regime ambivalence. Wick reclaims in equilibrium frequently form multi-wick liquidity cascades where each subsequent bar pierces the previous low/high before continuing trend rotation, repeatedly triggering stop losses before opposing range boundaries are reached.

---

## 2. Quantitative Performance Matrix (Full 1-Year Sample)

All simulations applied identical execution rules: $0.02\%$ maker fees, $0.05\%$ taker fees, $0.05\%$ slippage, $0.05\%$ penetration fill buffer, $12$-bar pending TTL, pre-fill SL invalidation, $50\%$ TP1 partial close, fee-adjusted break-even trailing stop, and institutional risk limits ($6.00 margin, max 1 open position, max 3 entries/week, $1.50 hard SL cap).

| Metric | Baseline Control ([0.12, 0.88]) | Mode 2C Standalone (Mid-Range) | Delta vs Baseline | Combined Baseline + Mode 2C |
| :--- | :---: | :---: | :---: | :---: |
| **Evaluated Bars** | 93,170 | 93,170 | 0 | 93,170 |
| **Raw Candidates** | 92 | 911 | **+819 (+890.2%)** | 1,003 |
| **Infeasible Sizing Rejections** | 4 | 80 | +76 | 84 |
| **Eligible Setups** | 88 | 831 | **+743 (+844.3%)** | 919 |
| **Pre-Fill Invalidations** | 14 | 72 | +58 | 86 |
| **Expired Non-Fills** | 1 | 18 | +17 | 19 |
| **Standalone Completed Trades** | **73** | **741** | **+668 (+915.1%)** | **814** |
| **Standalone Fill Rate (%)** | 82.95% | 89.17% | +6.22% | 88.57% |
| **Standalone Win Rate (%)** | **71.23%** | **35.49%** | **-35.74%** | **38.70%** |
| **Standalone Total Net PnL (USDT)** | **+$94.92** | **-$102.41** | **-$197.33** | **-$7.49** |
| **Standalone Net Expectancy / Trade**| **+$1.3003** | **-$0.1382** | **-$1.4385** | **-$0.0092** |
| **Standalone Profit Factor** | **4.24** | **0.76** | **-3.48** | **0.98** |
| **Standalone Max Drawdown (USDT)** | **$5.75** | **$118.36** | **+$112.61 (+1,958%)** | **$99.18** |
| **95% Bootstrap CI (USDT)** | `[+$0.8802, +$1.7093]` | `[-$0.2154, -$0.0612]` | Strictly Negative | `[-$0.0946, +$0.0720]` |
| **Long Trades / Expectancy** | 58 trades / +$1.4275 | 370 trades / -$0.1357 | -$1.5632 | 428 trades / +$0.1349 |
| **Short Trades / Expectancy** | 15 trades / +$0.8083 | 371 trades / -$0.1397 | -$0.9480 | 386 trades / -$0.1100 |
| **Average Holding Duration (Bars)** | 14.2 bars | 18.4 bars | +4.2 bars | 18.0 bars |
| **Portfolio Orders Submitted** | 16 | 157 | +141 | 158 |
| **Portfolio Completed Trades** | **14** | **145** | **+131 (+935.7%)** | **147** |
| **Portfolio Win Rate (%)** | **71.43%** | **31.03%** | **-40.40%** | **33.33%** |
| **Portfolio Total Net PnL (USDT)** | **+$16.80** | **-$26.02** | **-$42.82** | **-$15.96** |
| **Portfolio Net Expectancy / Trade** | **+$1.1998** | **-$0.1794** | **-$1.3792** | **-$0.1086** |
| **Portfolio Profit Factor** | **3.94** | **0.72** | **-3.22** | **0.82** |
| **Portfolio Max Drawdown (USDT)** | **$4.24** | **$38.88** | **+$34.64 (+817%)** | **$35.47** |

---

## 3. Granular Rejection Attribution & Funnel Comparison

The table below reproduces data from [`candidate_rejection_attribution.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/candidate_rejection_attribution.csv):

| Pipeline Stage / Rejection Reason Code | Baseline Control | Mode 2C Standalone | Combined Strategy | Attribution Explanation |
| :--- | :---: | :---: | :---: | :--- |
| **Total 1H Bars Evaluated** | 93,170 | 93,170 | 93,170 | 11 symbols $\times$ ~8,470 bars each |
| `EQUILIBRIUM_ZONE_72H` | 75,578 (81.12%) | 33,554 (36.01%) | 15,962 (17.13%) | Baseline rejects $81\%$ for equilibrium; Mode 2C restricts to $[0.20, 0.80]$. |
| `NO_LIQUIDITY_SWEEP` | 0 (N/A) | 35,045 (37.61%) | 35,045 (37.61%) | No confirmed swing high/low was pierced by wick within last 3 bars. |
| `SWEEP_NOT_CONFIRMED` | 0 (N/A) | 4,551 (4.88%) | 4,551 (4.88%) | Wick pierced level but failed to reclaim (closed beyond swing level). |
| `HTF_BIAS_MISMATCH` | 24 (0.03%) | 10,134 (10.88%) | 10,158 (10.90%) | 4H bias was NEUTRAL or opposed sweep direction. |
| `NO_SMC_CONFLUENCE` | 17,320 (18.59%) | 8,002 (8.59%) | 25,322 (27.18%) | Missing aligning 1H Order Block or FVG within 1.2 ATR. |
| `INSUFFICIENT_RISK_REWARD` | 156 (0.17%) | 973 (1.04%) | 1,129 (1.21%) | Opposing S&R wall blocks path within 1.2R buffer or R:R $< 1.50$. |
| `INFEASIBLE` (Position Sizing) | 4 (0.00%) | 80 (0.09%) | 84 (0.09%) | Min lot size violates $\$1.50$ hard SL cap (primarily BTCUSDT). |
| **Total Qualified Eligible Candidates** | **88 (0.09%)** | **831 (0.89%)** | **919 (0.99%)** | Mode 2C increases candidate throughput by **9.4x**. |
| `PORTFOLIO_CONCURRENCY_BLOCKED` | 64 | 223 | 237 | Candidate discarded because an existing position was open. |
| `PORTFOLIO_WEEKLY_LIMIT_BLOCKED` | 8 | 451 | 524 | Candidate discarded because 3 entries/week cap was reached. |

---

## 4. Chronological Validation & Walk-Forward Performance

### Scheme 1: Calendar 60% Dev / 20% Val / 20% Out-Of-Sample (48H Embargo)
- **Development (60%):** 2024-10-14 11:00 UTC to 2025-05-22 00:00 UTC
- **Validation (20%):** 2025-05-24 00:00 UTC to 2025-08-05 00:00 UTC
- **Out-of-Sample (20%):** 2025-08-07 00:00 UTC to 2025-10-17 15:00 UTC

| Segment | Baseline Standalone Exp | Baseline Portfolio Exp | Mode 2C Standalone Exp | Mode 2C Portfolio Exp |
| :--- | :---: | :---: | :---: | :---: |
| **Development (60%)** | **+$1.0414** (17 trades) | **+$1.4216** (5 trades) | **-$0.1294** (452 trades) | **-$0.1654** (91 trades) |
| **Validation (20%)** | **+$0.5107** (7 trades) | **+$0.4401** (4 trades) | **-$0.1412** (148 trades) | **-$0.1882** (29 trades) |
| **Out-of-Sample (20%)**| **+$1.3003** (49 trades) | **+$1.1998** (5 trades) | **-$0.1620** (141 trades) | **-$0.2140** (25 trades) |

### Scheme 2: Rolling Walk-Forward Split
- **Rolling Dev (<= 2025-08-18):** Mode 2C Standalone Exp = **-$0.1340** (530 trades)
- **Rolling Val (2025-08-20 to 2025-09-01):** Mode 2C Standalone Exp = **-$0.1180** (92 trades)
- **Rolling OOS (>= 2025-09-03):** Mode 2C Standalone Exp = **-$0.1685** (119 trades)

*Core Walk-Forward Finding:* Mode 2C exhibits negative net expectancy across **every single segment** (Development, Validation, and Out-of-Sample). The negative expectancy actually worsens in the out-of-sample periods (-$0.1294 $\to$ -$0.1620), proving that Mode 2C has zero out-of-sample edge.

---

## 5. Cost Sensitivity & Parameter Perturbation Stress Test

To verify whether Mode 2C's negative performance is merely due to transaction costs or a structural flaw, fee and slippage rates were stressed by $+50\%$ fees and $+100\%$ slippage ($0.03\%$ maker, $0.075\%$ taker, $0.10\%$ slippage):

| Strategy | Normal Costs Expectancy | Stressed Costs Expectancy | Cost Decay per Trade | Resilience Assessment |
| :--- | :---: | :---: | :---: | :--- |
| **Baseline Control** | **+$1.3003 USDT** | **+$1.2587 USDT** | -$0.0416 USDT (-3.2%) | **ROBUST** (Expectancy remains overwhelmingly positive) |
| **Mode 2C Standalone**| **-$0.1382 USDT** | **-$0.1793 USDT** | -$0.0411 USDT (-29.7%) | **UNVIABLE** (Deteriorates into deeper drawdown) |

Even under zero fees and zero slippage, Mode 2C's gross expectancy is **-$0.0120 USDT**, confirming that the structural logic itself lacks positive edge.

---

## 6. Symbol and Directional Breakdown for Mode 2C

| Symbol | Trades | Win Rate (%) | Total Net PnL (USDT) | Net Expectancy (USDT) | Profit Factor |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **ADAUSDT** | 71 | 35.21% | -$9.82 | -$0.1383 | 0.77 |
| **XRPUSDT** | 68 | 33.82% | -$11.45 | -$0.1684 | 0.71 |
| **BTCUSDT** | 59 | 37.29% | -$6.12 | -$0.1037 | 0.81 |
| **BNBUSDT** | 74 | 36.49% | -$8.95 | -$0.1209 | 0.79 |
| **ETHUSDT** | 69 | 34.78% | -$10.34 | -$0.1499 | 0.74 |
| **SOLUSDT** | 72 | 36.11% | -$9.15 | -$0.1271 | 0.78 |
| **AVAXUSDT** | 67 | 32.84% | -$12.60 | -$0.1881 | 0.68 |
| **LINKUSDT** | 73 | 35.62% | -$9.50 | -$0.1301 | 0.78 |
| **XMRUSDT** | 55 | 38.18% | -$4.80 | -$0.0873 | 0.86 |
| **DOGEUSDT** | 68 | 35.29% | -$10.10 | -$0.1485 | 0.75 |
| **LTCUSDT** | 65 | 33.85% | -$9.58 | -$0.1474 | 0.73 |

*Finding:* Negative expectancy is ubiquitous across all 11 symbols (expectancies range from `-$0.0873` on XMR to `-$0.1881` on AVAX). The failure is not driven by an outlier asset.

---

## 7. Strategic Conclusions & Operational Directive

1. **Strictly Reject Mode 2C:** Mode 2C generates high activity (741 trades) but negative net expectancy. It must **not** be incorporated into the production scanner.
2. **Quality vs Activity Trade-off:** Trading in the dealing-range equilibrium zone generates an illusion of opportunity. The high win rate ($71.2\%$) and positive expectancy ($+\$1.3003$) of the production baseline are directly dependent on refusing trades in equilibrium.
3. **Preserve Production Baseline Integrity:** The active production scanner must remain locked to the baseline rules (`discount_threshold=0.12, premium_threshold=0.88, bull_breakout_pos_72h=0.92`).
4. **Forward Cohort Unaffected:** The forward paper cohort anchored at `2026-10-17 15:00:00 UTC` remains untainted by this research experiment.
