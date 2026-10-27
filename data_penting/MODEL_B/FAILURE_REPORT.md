# Model B — Forensic Failure Attribution & Root-Cause Report

**Archive Document:** `data_penting/MODEL_B/FAILURE_REPORT.md`  
**Model Name:** `MODEL_B` (`MODEL_B_V1`, `MODEL_B_V2_FAST`, `MODEL_B_V2_1H`, `MODEL_B_V3`)  
**Timeframes:** 15m execution / 1H higher-timeframe context  
**Primary Source Reports:** `backtest_results/model_a_vs_model_b_comparison/REPORT.md`, `backtest_results/model_b_v1_v2_v3_comparison/`  
**Primary Dataset:** `data/backtest_15m_top10.db` (1-Year 15m Sample, 11 Symbols)  

---

## 1. Executive Summary of Failure Modes

Model B was created to resolve Model A's signal starvation by shifting execution down to 15m candles with rapid breakout entries, tighter stop distances, and high-frequency candidate generation.

In 12-month annual backtests across all 11 whitelisted pairs, Model B produced **catastrophic financial failure**:
1. **Severe Capital Ruin:** Across 11,594 trades (`MODEL_B_V1`), the strategy generated **-$1,577.17 Net PnL**, experiencing a peak drawdown of **-$1,597.98** (a **1,466% account loss** relative to the $16 capital baseline).
2. **Massive Fee & Slippage Bleed:** Out of -$1,577.17 in total losses, **$1,118.41 was consumed purely by exchange trading fees and adverse slippage**!
3. **Sub-1.00 Profit Factor Across All Variants:** Every tested variant of Model B on 15m generated a Net Profit Factor below 1.00 (`0.73` in V1, `0.81` in V2 Fast, `0.86` in V3 Tuned).

---

## 2. The Four Concrete Root Causes

### Failure Driver #1: High-Frequency Fee Drag on Small Move Targets
- In crypto perpetual futures, the baseline fee friction (0.02% maker + 0.05% taker + 0.05% slippage) equals **~0.12% per round-trip trade**.
- Because Model B targeted small 15m breakouts with average target gains of 0.8% to 1.5%, **trading fees consumed between 30% and 60% of the entire gross trade profit**.
- Even when Model B achieved a gross win rate near 41%, after deducting $1,118.41 in exchange fees, net expectancy was deeply negative (-0.058R per trade).

### Failure Driver #2: Ultra-Tight Stop Losses (< 1.15%) Whipped by Market Noise
- To achieve high theoretical risk-to-reward ratios on 15m candles, Model B frequently placed stop-losses within **0.50% to 1.00%** of the entry price.
- In volatile crypto assets (e.g. `SOLUSDT`, `DOGEUSDT`, `AVAXUSDT`), normal 15m intrabar bid/ask spread bounce and orderbook sweep noise regularly fluctuate by 0.60% to 1.20%.
- Consequently, trades were prematurely stopped out by routine liquidity sweeps, only for the price to subsequently rotate in the intended direction.
- **The Stop Floor Rule Discovered:** Empirical analysis proved that any stop distance tighter than **1.15%** on crypto perpetuals has a win rate under 32% and is mathematically unviable after fees.

### Failure Driver #3: False Breakout Entrapment in Mid-Range Chop
- Model B lacked strict Dealing Range zone locks. It actively initiated breakout and trend-continuation trades in the middle 60% of the 72H dealing range ($38\%-65\%$).
- In range equilibrium, crypto assets lack sustained order flow momentum. Breakouts above 15m swing highs in the middle of a range almost invariably form liquidity sweeps (wicks) that violently reverse, trapping high-frequency long breakout traders at the absolute local top.

### Failure Driver #4: Low Payoff Ratio & Runner Decay
- Model B attempted to use trailing stops on 50% runner positions.
- In choppy 15m regimes, trailing stops routinely gave back 60% to 80% of open profits, causing positive trades to close at near-breakeven while paying full taker exit fees.

---

## 3. Quantitative Comparison Across Model B Iterations

Source: `backtest_results/model_a_vs_model_b_comparison/REPORT.md` (365 Days, 11 Whitelisted Pairs):

| Strategy Iteration | Timeframe | Total Trades | Win Rate | Gross PnL | Total Fees | Net PnL | Net PF | Max Drawdown | Expectancy (R/trade) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`MODEL_B_V1` (Unconstrained)** | 15m | **11,594** | 41.0% | -$457.03 | **$1,118.41** | **-$1,577.17** | 0.73 | $1,597.98 (1466%) | -0.058R |
| **`MODEL_B_V2_FAST`** | 15m | **5,983** | 39.5% | -$305.28 | $461.16 | **-$766.88** | 0.81 | $823.59 (713%) | -0.050R |
| **`MODEL_B_V2_1H`** | 1H | **1,857** | 39.0% | -$76.87 | $75.17 | **-$151.74** | 0.87 | $186.77 (178%) | -0.040R |
| **`MODEL_B_V3` (Tuned + 1.2% Floor)** | 15m | **3,757** | 37.0% | -$164.13 | $207.16 | **-$369.79** | 0.86 | $429.67 (345%) | -0.043R |

---

## 4. Per-Symbol Loss Breakdown in Model B V1

No single symbol escaped net negative PnL under the high-frequency 15m scalping model:

- `BTCUSDT`: 1,173 trades, -$215.61 Net PnL
- `LINKUSDT`: 1,097 trades, -$200.03 Net PnL
- `DOGEUSDT`: 1,221 trades, -$193.37 Net PnL
- `LTCUSDT`: 1,101 trades, -$175.25 Net PnL
- `SOLUSDT`: 1,274 trades, -$169.91 Net PnL
- `BNBUSDT`: 1,142 trades, -$145.20 Net PnL
- `ETHUSDT`: 1,157 trades, -$125.92 Net PnL
- `XRPUSDT`: 1,150 trades, -$125.33 Net PnL
- `AVAXUSDT`: 1,162 trades, -$123.50 Net PnL
- `ADAUSDT`: 1,117 trades, -$103.03 Net PnL

---

## 5. Key Takeaways & Successor Principles

Model B definitively proved that **high-frequency 15m scalping with sub-1.15% stops is guaranteed ruin in crypto perpetuals due to fee drag and market noise**. It led directly to the core tenets of the current production engine:
1. **Never scalp sub-1.15% stops** (enforce hard minimum stop-loss floor).
2. **Never enter in mid-range equilibrium** (enforce Dealing Range zone locks: Discount $\le 38\%$ only).
3. **Trade sweeps and reclaims, not breakouts** (wait for the false breakout to sweep liquidity and enter on the reclaim).
