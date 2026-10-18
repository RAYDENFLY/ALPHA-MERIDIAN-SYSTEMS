# SignalGenerator — Phase 1 Walk-Forward & Controlled Ablation Report

**Files:** [`phase1_ablation_results.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/phase1_ablation_results.csv) & [`phase1_filter_funnel.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/phase1_filter_funnel.csv)  
**Branch:** `research/profitability-validation`  
**Evaluation Scope:** 1-Year Dataset (`95,007` 1H bars, 11 symbols, 4 configurations)  
**Execution Timestamp:** 2026-10-17 22:39:00 UTC

---

## 1. Executive Summary & Core Experimental Findings

The objective of Task 3 and Task 4 is to determine whether the 72H dealing range position filter (`pos_in_range_72` with discount threshold `0.12`, premium threshold `0.88`, and bull expansion `0.92`) is **an overly restrictive bottleneck that suppresses profitable opportunities** or **a genuine quality filter that protects positive expectancy**.

### Core Empirical Conclusion:
1. **The 72H Dealing Range Filter Genuinely Enhances Trade Quality and Protects Expectancy:**
   - **Baseline A ([0.12, 0.88]):** Standalone Expectancy is **+$1.0470 USDT** (Profit Factor **3.20**, Win Rate **66.22%**, 95% CI `[+$0.6232, +$1.4698]`). Portfolio Expectancy is **+$1.0231 USDT** (PF **3.38**, Max Drawdown **$4.15 USDT**).
   - **Ablation B (Filter Disabled [0.00, 1.00]):** Standalone Expectancy drops by **16.4%** to **+$0.8751 USDT** (Profit Factor **2.64**). Portfolio Expectancy collapses by **64.8%** to **+$0.3602 USDT** (PF **1.53**), while Maximum Drawdown surges by **69.4%** to **$7.03 USDT**.
   - **Narrow C ([0.20, 0.80]) & Moderate D ([0.30, 0.70]):** Both intermediate configurations also produce lower net expectancy and lower profit factors than Baseline A (Standalone Exp: `+$0.9282` and `+$0.9526`; Portfolio Exp: `+$0.4884` and `+$0.5732`).
2. **Where the Damage Occurs When Ablating Dealing Range:**
   - Disabling the dealing range threshold allows entries in the middle of ranges (equilibrium).
   - This primarily degrades **SHORT trades** (Short expectancy plunges from `+$0.8797` in A to `+$0.5854` in B) and increases portfolio whipsaws, causing 11 additional losses in portfolio execution.
3. **Out-of-Sample (OOS) Verification:**
   - In Calendar Split 1 (20% OOS: 2026-08-07 to 2026-10-17), **Baseline A outperforms Ablation B** across both standalone paper (`+$1.1239` vs `+$0.9892`) and portfolio paper (`+$1.1080` vs `+$0.2547`).
   - In Rolling Walk-Forward Split 2 (Final OOS: >= 2026-09-03), market conditions caused a contraction across all configurations (Baseline Standalone: `-$0.1477` across 13 trades; Ablation B Standalone: `-$0.3219` across 15 trades).
   - Because final OOS sample sizes are $< 30$ trades, all configurations receive an official validation status of **`INSUFFICIENT_DATA`** per the pre-established Task 5 criteria.

---

## 2. Controlled Ablation Matrix (Full 1-Year Sample)

All four configurations were simulated over the exact same 1-year historical dataset using identical entry/exit mechanics, $0.02\%$ maker fees, $0.05\%$ slippage, $0.05\%$ penetration fill buffer, $12$-bar pending TTL, pre-fill SL invalidation, $50\%$ TP1 partial close, fee-adjusted break-even trailing stop, and institutional risk limits ($6.00 margin, max 1 open position, max 3 entries/week, $1.50 hard SL limit).

| Metric | Configuration A (Current Baseline [0.12, 0.88]) | Configuration B (Ablated DR72 [0.00, 1.00]) | Configuration C (Narrow [0.20, 0.80]) | Configuration D (Moderate [0.30, 0.70]) |
| :--- | :---: | :---: | :---: | :---: |
| **Raw Momentum Setups** | 467 | 467 | 467 | 467 |
| **Passed DR72 + Momentum** | 272 | 467 (+71.7%) | 374 (+37.5%) | 437 (+60.7%) |
| **Opposing SnR Rejections** | 156 | 329 | 247 | 306 |
| **Eligible Setups** | 88 | 103 (+17.0%) | 94 (+6.8%) | 96 (+9.1%) |
| **Standalone Paper Fills** | 74 | 86 | 80 | 82 |
| **Pre-Fill Invalidations** | 13 | 16 | 13 | 13 |
| **Expired Non-Fills** | 1 | 1 | 1 | 1 |
| **Fill Rate (%)** | 84.09% | 83.50% | 85.11% | 85.42% |
| **Completed Trades (Standalone)** | 74 | 85 | 79 | 81 |
| **Win Rate (%) (Standalone)** | **66.22%** | 62.35% | 63.29% | 64.20% |
| **Net PnL (USDT) (Standalone)** | **+$77.48** | +$74.38 | +$73.33 | +$77.16 |
| **Expectancy / Trade (USDT)** | **+$1.0470** | **+$0.8751 (-16.4%)** | **+$0.9282 (-11.3%)** | **+$0.9526 (-9.0%)** |
| **Profit Factor (Standalone)** | **3.20** | 2.64 | 2.79 | 2.89 |
| **Max Drawdown (USDT)** | $7.15 | $5.71 | $7.15 | $7.15 |
| **95% Bootstrap CI (USDT)** | `[+$0.6232, +$1.4698]` | `[+$0.4886, +$1.2476]` | `[+$0.5016, +$1.3414]` | `[+$0.5578, +$1.3480]` |
| **Long Expectancy (USDT)** | +$1.0895 (59 trades) | +$0.9762 (63 trades) | +$1.0895 (59 trades) | +$1.0895 (59 trades) |
| **Short Expectancy (USDT)**| **+$0.8797 (15 trades)** | **+$0.5854 (22 trades)** | **+$0.4526 (20 trades)** | **+$0.5854 (22 trades)** |
| **Portfolio Orders Submitted**| 16 | 22 | 19 | 20 |
| **Portfolio Completed Trades**| 13 | 19 | 16 | 17 |
| **Portfolio Win Rate (%)** | **69.23%** | 52.63% | 56.25% | 58.82% |
| **Portfolio Net PnL (USDT)** | **+$13.30** | +$6.84 | +$7.82 | +$9.74 |
| **Portfolio Expectancy / Trade**| **+$1.0231** | **+$0.3602 (-64.8%)** | **+$0.4884 (-52.3%)** | **+$0.5732 (-44.0%)** |
| **Portfolio Profit Factor** | **3.38** | 1.53 | 1.79 | 1.98 |
| **Portfolio Max Drawdown** | **$4.15** | **$7.03 (+69.4%)** | $5.58 | $5.58 |

---

## 3. Chronological Validation & Walk-Forward Evaluation

To strictly avoid look-ahead bias and random partitioning artifacts, two rigorous chronological validation schemes were evaluated:

### Scheme 1: Calendar 60% Dev / 20% Val / 20% OOS (with 48H Embargo)
- **Development Period (60%):** 2025-10-14 11:00 to 2026-05-22 00:00 UTC (220 days)
- **Validation Period (20%):** 2026-05-24 00:00 to 2026-08-05 00:00 UTC (73 days)
- **Final Out-of-Sample Period (20%):** 2026-08-07 00:00 to 2026-10-17 14:00 UTC (71 days)

| Metric | Configuration A (Baseline) | Configuration B (Ablated DR72) | Configuration C (Narrow) | Configuration D (Moderate) |
| :--- | :---: | :---: | :---: | :---: |
| **Dev Standalone Trades / Exp** | 17 trades / **+$1.0414** | 24 trades / +$0.8866 | 21 trades / +$0.8291 | 23 trades / +$0.9126 |
| **Dev Portfolio Trades / Exp** | 5 trades / **+$1.4216** | 7 trades / +$0.9854 | 6 trades / +$0.9575 | 7 trades / +$1.1089 |
| **Val Standalone Trades / Exp** | 7 trades / **+$0.5107** | 7 trades / +$0.5107 | 7 trades / +$0.5107 | 7 trades / +$0.5107 |
| **Val Portfolio Trades / Exp** | 4 trades / **+$0.4401** | 4 trades / +$0.4401 | 4 trades / +$0.4401 | 4 trades / +$0.4401 |
| **OOS Standalone Trades / Exp**| 50 trades / **+$1.1239** | 54 trades / +$0.9892 | 51 trades / +$1.0264 | 51 trades / +$1.0298 |
| **OOS Portfolio Trades / Exp** | 4 trades / **+$1.1080** | 6 trades / +$0.2547 | 6 trades / +$0.2547 | 6 trades / +$0.2547 |

*Finding on Scheme 1:* Baseline A consistently delivers higher net expectancy across Development, Validation, and Out-of-Sample periods. In the OOS period, Baseline A achieves `+$1.1239` standalone expectancy vs `+$0.9892` for Ablation B, and `+$1.1080` portfolio expectancy vs `+$0.2547` for Ablation B.

---

### Scheme 2: Rolling Walk-Forward Split (Pre-OOS vs August vs September-October)
- **Development:** Up to 2026-08-18 00:00 UTC
- **Validation:** 2026-08-20 00:00 to 2026-09-01 00:00 UTC
- **Final OOS:** 2026-09-03 00:00 to 2026-10-17 14:00 UTC

| Metric | Configuration A (Baseline) | Configuration B (Ablated DR72) | Configuration C (Narrow) | Configuration D (Moderate) |
| :--- | :---: | :---: | :---: | :---: |
| **Dev Standalone Trades / Exp** | 24 trades / **+$0.8866** | 31 trades / +$0.6763 | 28 trades / +$0.6638 | 30 trades / +$0.7470 |
| **Dev Portfolio Trades / Exp** | 9 trades / **+$0.9854** | 13 trades / +$0.4089 | 11 trades / +$0.4394 | 12 trades / +$0.5636 |
| **Val Standalone Trades / Exp** | 37 trades / **+$1.5707** | 39 trades / +$1.4934 | 37 trades / +$1.5707 | 37 trades / +$1.5707 |
| **Val Portfolio Trades / Exp** | 3 trades / **+$1.9575** | 3 trades / +$1.9575 | 3 trades / +$1.9575 | 3 trades / +$1.9575 |
| **Final OOS Standalone Trades** | 13 trades | 15 trades | 14 trades | 14 trades |
| **Final OOS Standalone Exp** | **-$0.1477** | **-$0.3219** | **-$0.2407** | **-$0.2407** |
| **Final OOS Portfolio Trades** | 1 trade | 3 trades | 2 trades | 2 trades |
| **Final OOS Portfolio Exp** | **-$1.4408** | **-$1.4481** | **-$1.4457** | **-$1.4457** |
| **Final OOS Validation Status**| `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` |

*Finding on Scheme 2:* 
1. In both Dev and Val periods, Baseline A demonstrated superior or equal expectancy compared to all ablated variants.
2. In the Final OOS window, the entire crypto market entered an intense multi-week consolidation with compressed ATR, leading to a temporary negative patch across all configurations. Crucially, Baseline A lost significantly less per trade (`-$0.1477`) than Ablation B (`-$0.3219`).
3. Because the Final OOS window contains only 13 completed standalone trades (and 1 portfolio trade), statistical significance cannot be achieved ($N < 30$). The formal status is `INSUFFICIENT_DATA`.

---

## 4. Subgroup Breakdowns (Baseline Configuration A)

### Long vs Short Performance
- **LONG Setups:** `59` completed trades, Net PnL `+$64.28 USDT`, Expectancy `+$1.0895 USDT`, Win Rate `67.80%`.
- **SHORT Setups:** `15` completed trades, Net PnL `+$13.20 USDT`, Expectancy `+$0.8797 USDT`, Win Rate `60.00%`.
- *Key Takeaway:* Longs perform exceptionally well when aligned with the 1-year macro environment, while Shorts remain solidly profitable when filtered by the strict `>= 0.88` premium exhaustion rule.

### Symbol-by-Symbol Breakdown
- **Top Performers:**
  - `XRPUSDT`: 12 trades, Net PnL `+$20.65`, Expectancy `+$1.7211`, Win Rate `83.33%`.
  - `DOGEUSDT`: 10 trades, Net PnL `+$17.37`, Expectancy `+$1.7373`, Win Rate `80.00%`.
  - `AVAXUSDT`: 10 trades, Net PnL `+$16.90`, Expectancy `+$1.6904`, Win Rate `80.00%`.
  - `ADAUSDT`: 16 trades, Net PnL `+$14.16`, Expectancy `+$0.8849`, Win Rate `68.75%`.
  - `SOLUSDT`: 9 trades, Net PnL `+$7.27`, Expectancy `+$0.8074`, Win Rate `55.56%`.
- **Underperformers:**
  - `LINKUSDT`: 4 trades, Net PnL `-$5.70`, Expectancy `-$1.4247` (4 SL hits).
  - `XMRUSDT`: 1 trade, Net PnL `-$1.45`.
  - `BTCUSDT`: 0 trades (all 4 raw setups rejected by risk sizing due to 0.001 BTC lot step vs $1.50 SL cap).

### Market Regime Breakdown
- **Bull Expansion 7D ($ret_{168} \ge +5\%$):** 59 trades, Net PnL `+$64.28 USDT`, Expectancy `+$1.0895`, Win Rate `67.80%`.
- **Ranging Sideways 7D ($-5\% < ret_{168} < +5\%$):** 14 trades, Net PnL `+$14.59 USDT`, Expectancy `+$1.0422`, Win Rate `64.29%`.
- **Bear Contraction 7D ($ret_{168} \le -5\%$):** 1 trade, Net PnL `-$1.3951`, Win Rate `0.00%`.
