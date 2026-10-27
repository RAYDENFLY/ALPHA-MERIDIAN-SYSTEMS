# Model A — Forensic Failure Attribution & Root-Cause Report

**Archive Document:** `data_penting/MODEL_A/FAILURE_REPORT.md`  
**Model Name:** `MODEL_A` (`MODEL_A_FROZEN` / `MODEL_A_TUNED`)  
**Timeframes:** 1H execution / 4H higher-timeframe bias  
**Primary Source Reports:** `docs/profitability_failure_attribution.md`, `docs/profitability_phase0_audit.md`, `ROOT_CAUSE_AND_RECOMMENDATION.md`  
**Primary Dataset:** `data/scanner.db`, `baseline_trades.csv` (1-Year Sample, 11 Symbols)  

---

## 1. Executive Summary of Failure Modes

Model A represented the initial production trading architecture of SignalGenerator. It evaluated 1H candlestick bars using higher-timeframe SMC concepts, 72-hour Dealing Range regimes, and a calibrated logistic regression machine learning filter.

In empirical out-of-sample forward paper testing and 1-year historical backtests, Model A suffered two fatal operational failures:
1. **Complete Signal Starvation (0 Live/Paper Trades for 27 Days):** Between `2026-09-21` and `2026-10-17`, the scanner generated **0 executed trades** across all 11 watchlist symbols (`trade_journal = 0`, `paper_orders = 0`).
2. **Negative Out-of-Sample Expectancy:** In chronologically strict out-of-sample testing (`VAL` and `OOS_TEST`), the portfolio delivered **-$1.50 in VAL** and **-$1.50 in OOS_TEST**, with standalone limit-order win rates collapsing to **38.5%**.

---

## 2. The Five Concrete Root Causes

### Failure Driver #1: Overfitted 12-Parameter Dealing-Range Filter (`src/strategy/generator.py:139-178`)
- The 72-hour dealing-range pre-screen required simultaneously meeting 12 rigid hardcoded conditions (`pos_in_range_72`, `ret_12`, `ret_24`, `ret_48`, `ret_168`, `vr_24_12`, boundary touches $\ge 4$, outside closes $\le 20\%$).
- This filter was accidentally tuned to historical volatility clusters (specifically `2026-08-20..22`, where 50 of the 92 lifetime candidates were generated).
- Outside of those specific multi-day volatility bursts, the filter rejected **99.9% of all 1H bars**, producing complete signal starvation across the entire market during multi-week consolidation phases.

### Failure Driver #2: Artificial Stop-Loss Clamping Defect (`src/strategy/planner.py:72-97`)
- In `TPSLPlanner.plan_levels()`, the code enforced:
  ```python
  max_sl_pct = 0.024 if is_bull_expansion else 0.022
  if abs(entry_price - sl) / entry_price > max_sl_pct:
      sl = entry_price * (1.0 - max_sl_pct)
  ```
- **Structural Invalidation Destroyed:** When the true structural invalidation level (Order Block, Swing Low, or S/R Zone + $0.35 \times \text{ATR}$) was more than $2.2\%$ away from entry, `TPSLPlanner` **ignored the structure and artificially pulled the stop-loss inside the structure to 2.2%** solely so a $60 notional position would not exceed the $1.50 risk cap.
- Normal intrabar retracements into the Order Block routinely triggered the clamped stop-loss before the trade moved toward Take-Profit.

### Failure Driver #3: First-Come Symbol Bias & Multi-Bar Duplicate Clustering
- When a market-wide expansion occurred, multiple symbols generated valid breakouts simultaneously.
- `BacktestEngine` evaluated symbols alphabetically based on watchlist order (`ADAUSDT` $\rightarrow$ `AVAXUSDT` $\rightarrow$ `BTCUSDT` $\rightarrow$ `ETHUSDT` $\rightarrow$ `LINKUSDT` $\rightarrow$ `LTCUSDT` $\rightarrow$ `SOLUSDT`).
- Because a single position locked the portfolio slot for up to 48 hours, a low-quality breakout on `SOLUSDT` or `LTCUSDT` blocked high-expectancy setups on `AVAXUSDT`, `XRPUSDT`, and `DOGEUSDT`.

### Failure Driver #4: Adverse Selection in Limit-Order Retracement Fills
- $86$ of $88$ feasible setups ($97.7\%$) were breakout entries at `current_candle.close`.
- The paper limit-fill model placed an entry limit order $0.025\%$ below close with a 12-bar TTL.
- True momentum breakouts that continued immediately went unfilled (`EXPIRED_UNFILLED`). Conversely, false breakouts that immediately collapsed pierced the limit order on the way down to the stop-loss, causing the out-of-sample win rate to collapse to $38.5\%$.

### Failure Driver #5: Non-Selective ML Gate (98.9% Pass Rate)
- Because `SetupGenerator` only emitted 92 candidates over an entire year (only 32 in `TRAIN`), `ModelRegistry.train_and_evaluate()` suffered from severe sample scarcity.
- The trained logistic regression model assigned probabilities $>0.3533$ to $87 / 88$ feasible candidates, failing to provide any discriminant filtering.

---

## 3. Quantitative Attribution Matrix (1-Year Baseline Sample)

| Cohort | Feasible Setups | Trades Executed | Win Rate | Gross PnL | Net PnL | Profit Factor | Expectancy / Trade | Max DD |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **B1: Top 3 Symbols (`BTC`, `ETH`, `SOL`)** | 12 | 12 | 58.33% | +$9.62 | +$7.90 | 2.05 | +$0.6581 | $4.50 (18.1%) |
| **B2: Full 11-Symbol Portfolio** | 92 | 16 | 56.25% | +$9.71 | +$7.43 | 1.71 | +$0.4644 | $4.50 (17.6%) |
| **B3: Standalone All Feasible (Uncapped)** | 88 | 88 | 64.77% | +$73.60 | +$61.31 | 2.32 | +$0.6967 | $13.48 |
| **B4: Paper Limit-Fill Model** | 88 | 74 filled | 62.16% | +$47.62 | +$41.94 | 2.70 | +$0.5668 | $7.02 |

---

## 4. Key Takeaway & Successor Strategy

Model A proved that **rigid dealing-range thresholds on 1H charts create fatal signal starvation**, and that **clamping stop-losses to satisfy position limits violates market physics**. The live production engine resolved this by adopting `15M_SWEEP_RECLAIM_SMC`, dynamic structural take-profit targeting, and true structural stops sized via dollar risk rather than price clamping.
