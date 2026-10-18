# PHASE 1 — REPRODUCIBLE 1-YEAR HISTORICAL BASELINE REPORT

**Report Date:** 2026-10-17  
**Repository:** `C:\Users\Administrator\Documents\SignalGenerator`  
**Research Branch:** `research/profitability-validation`  
**Per-Trade Baseline Export:** [`baseline_trades.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/baseline_trades.csv) (`92` raw candidate setups, including the `16` executed portfolio trades and `88` feasible standalone setups)

---

## 1. Historical Dataset Verification (`data/scanner.db`)

Prior to running the baseline backtest, the SQLite historical candle dataset (`raw_candles` and `funding_rates`) was audited across all 11 watchlist symbols:

| Symbol | 1H Candles | 1H Expected | Missing / Gaps | 4H Candles | Date Range (UTC) | Funding Records |
| :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| `ADAUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| `AVAXUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| `BNBUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| `BTCUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| `DOGEUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| `ETHUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| `LINKUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| `LTCUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| `SOLUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| `XMRUSDT` | `6,645` | `6,645` | `0` | `1,661` | `2026-01-13 16:00` – `2026-10-17 12:00` | `1,219` |
| `XRPUSDT` | `8,834` | `8,834` | `0` | `2,208` | `2025-10-14 11:00` – `2026-10-17 12:00` | `1,546` |
| **Total** | **`95,015`** | **`95,015`** | **`0`** | **`23,711`** | **`368 Days` (`118,726` total bars)** | **`16,679`** |

- **Timestamp Integrity:** Strictly increasing `timestamp_ms`, zero duplicate primary keys, zero missing bars between `min_ts` and `max_ts`.
- **Execution Friction Assumptions:**
  - Maker fee: `0.02%` (`0.0002`), Taker fee: `0.05%` (`0.0005`), Slippage: `0.05%` (`0.0005`) per side.
  - Intrabar TP/SL collision policy: Conservative **SL-first** (`SL_COLLISION`).
  - Maximum holding horizon: `48` hours (`TIMEOUT`).
  - Account risk constraints: `$16.00` initial equity, `$6.00` target isolated margin (`$6.50` max), `10x` max leverage, `$1.50` hard SL loss limit, `$0.18` daily loss limit, `$0.54` weekly loss limit, `1` max concurrent position, `3` max entries per calendar week.

---

## 2. Unmodified Baseline Backtest Results (Portfolio & Standalone)

### 2.1 Candidate Funnel Across the 1-Year Dataset (`95,015` Symbol-Hours)
Across `95,015` 1H symbol-bars evaluated by the unmodified `SetupGenerator`:
- **Total Raw Candidate Setups Generated:** `92` (occurring on only **18 distinct calendar days** out of 368 days).
- **Blocked by Position Sizing (`INFEASIBLE`):** `4` (`BTCUSDT` minimum lot size `0.001 BTC` requiring `~$7.30–$8.10` margin at `10x` leverage, exceeding the `$6.50` max margin cap).
- **Feasible Standalone Setups:** `88` (`87` pass the dynamic break-even ML confidence gate `p >= 0.3533`).
- **Blocked by Portfolio Concurrency (`1` Max Open Position):** `38` setups skipped because another trade was already open.
- **Blocked by Weekly Entry Cap (`3` Max Entries/Week):** `37` setups skipped because 3 trades had already entered in the same calendar week.
- **Blocked by Daily Loss Cap (`$0.18` Daily Loss Limit):** `1` setup skipped after a losing trade on the same UTC day.
- **Total Executed Portfolio Trades:** **`16` trades** across the entire year (`1.33` trades/month).

### 2.2 Baseline Performance Summary Table

The table below reports the unmodified baseline under four views:
1. **3-Symbol Dashboard Subset (`ADAUSDT, XRPUSDT, BTCUSDT`)**: What `/api/backtest` previously displayed due to `active_symbols[:3]` truncation.
2. **11-Symbol Portfolio (Unmodified `BacktestEngine` with Bug BT-01)**: Full watchlist with `1` max open pos and `3` max entries/week.
3. **11-Symbol Portfolio (With Bug BT-01 Fixed — Adverse Slippage on SHORT TP)**: Exact net PnL after fixing `exit_price = trade.take_profit * (1.0 + self.slippage)` for `SHORT` TP exits.
4. **All 88 Feasible Candidates (Standalone Market-on-Close vs. Paper Limit-Fill Model)**: Evaluating every feasible setup independently to avoid sample-size starvation from portfolio concurrency locks.

| Metric | 3-Sym Dashboard Subset (`/api/backtest`) | 11-Sym Portfolio (Unmodified Buggy) | 11-Sym Portfolio (Fixed SHORT TP Slip) | All 88 Feasible (Market-on-Close) | All 74 Filled of 88 Feasible (Paper Limit Model) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Total Trades** | `10` | `16` | `16` | `88` | `74` (`13` inv before fill, `1` expired) |
| **Win / Loss / Timeout** | `8W / 2L / 0T` | `8W / 7L / 1T (win)` | `8W / 7L / 1T (win)` | `53W / 33L / 2T` | `49W / 24L / 1T` |
| **Win Rate (%)** | `80.00%` | `56.25%` | `56.25%` | `61.36%` | `66.22%` |
| **Pre-Cost Gross PnL** | `+$14.65` | `+$9.30` | `+$9.30` | `+$71.64` | `+$73.92` |
| **Slippage Cost** | `$0.60` | `$0.82` (buggy) | `$0.94` | `$5.13` | `$1.48` (SL/Timeout only) |
| **Trading Fees Paid** | `$0.60` | `$0.94` | `$0.94` | `$5.13` | `$2.35` (Maker entry/TP) |
| **Funding Paid** | `+$0.02` | `-$0.0005` | `-$0.0005` | `+$0.07` | `+$0.00` |
| **Post-Cost Net PnL** | `+$13.44` | `+$7.55` | `+$7.43` | `+$61.31` | `+$70.09` |
| **Net Expectancy / Trade** | `+$1.3443` | `+$0.4717` | `+$0.4646` | `+$0.6967` | `+$0.9472` |
| **Profit Factor** | `5.57` | `1.73` | `1.72` | `2.25` | `3.01` |
| **Max Drawdown (USDT / %)** | `$1.50` (`7.13%`) | `$5.79` (`26.09%`) | `$5.82` (`26.28%`) | N/A (unconstrained) | N/A (unconstrained) |
| **Average Holding Time** | `10.5h` | `11.1h` | `11.1h` | `10.8h` | `9.4h` |

---

## 3. Why the Baseline Fails Chronological Out-of-Sample Validation

At first glance, the aggregate 1-year numbers (`+$7.43` net PnL on `16` portfolio trades, `+$61.31` across `88` feasible setups) look positive. **Why, then, does the strategy fail out-of-sample profitability validation?**

When we decompose the exact same baseline chronologically using the model's recorded split epochs (`TRAIN`: `< 2026-08-20 15:00 UTC`; `VAL`: `2026-08-20 15:00 UTC` to `2026-09-03 19:00 UTC`; `OOS_TEST`: `>= 2026-09-03 19:00 UTC`):

### 3.1 Chronological Split Breakdown (11-Symbol Portfolio vs. Standalone Feasible)

| Chronological Split | Date Window (UTC) | 11-Sym Portfolio Trades (`N=16`) | 11-Sym Portfolio Net PnL / Expectancy | All Feasible Market Entry (`N=88`) | All Feasible Market Net PnL / Expectancy | Paper Limit Model Filled (`N=74`) | Paper Limit Net PnL / Expectancy |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`TRAIN` (In-Sample)** | `2025-10-14` – `2026-08-20` | `14` (`64.3%` WR) | `+$10.42` (`+$0.7445`/trade) | `31` (`61.3%` WR) | `+$21.39` (`+$0.6900`/trade) | `28` (`67.9%` WR) | `+$27.81` (`+$0.9934`/trade) |
| **`VAL` (Validation)** | `2026-08-20` – `2026-09-03` | `1` (`0.0%` WR) | `-$1.50` (`-$1.4954`/trade) | `42` (`66.7%` WR) | `+$36.93` (`+$0.8793`/trade) | `33` (`75.8%` WR) | `+$42.18` (`+$1.2783`/trade) |
| **`OOS_TEST` (Out-of-Sample)** | `2026-09-03` – `2026-10-17` | **`1` (`0.0%` WR)** | **`-$1.50` (`-$1.4951`/trade)** | **`15` (`46.7%` WR)** | **`+$2.99` (`+$0.1992`/trade)** | **`13` (`38.5%` WR)** | **`+$0.09` (`+$0.0071`/trade)** |

### 3.2 Critical Findings from the Baseline Decomposition
1. **Severe Regime & Date Clustering (`84%` of all setups occur in 6 days):**
   - Out of `92` raw candidates over 368 days, `50` (`54.3%`) occurred during a single 3-day market-wide rally on `2026-08-20` to `2026-08-22`, and `15` (`16.3%`) occurred during `2026-09-18` to `2026-09-20`.
   - Between `2026-09-20 14:00 UTC` and `2026-10-17 12:00 UTC` (**27 consecutive days**), the baseline strategy generated **zero (`0`) candidates across all 11 symbols**.
2. **Negative Portfolio Out-of-Sample Expectancy (`-$1.50` in `VAL` and `-$1.50` in `OOS_TEST`):**
   - Under actual account execution constraints (`1` open position, `3` entries/week, `$0.18` daily loss cap), the portfolio took only **1 trade in `VAL`** (`ADAUSDT LONG` on `2026-08-20 16:00`, losing `-$1.50`) and **1 trade in `OOS_TEST`** (`SOLUSDT LONG` on `2026-09-18 16:00`, losing `-$1.50`).
   - Because `SOLUSDT LONG` held for 34 hours (`2026-09-18 16:00` to `2026-09-20 02:00`) before hitting SL (`-$1.50`), it blocked all `13` concurrent setups on `2026-09-18` and `2026-09-19`, and its `-$1.50` loss triggered the `$0.18` daily loss limit on `2026-09-20`, blocking the remaining `AVAXUSDT LONG` setup!
3. **Collapse of Edge in `OOS_TEST` Under Paper Limit-Fill Rules (`38.5%` WR, `+$0.0071`/trade):**
   - Even when all `15` `OOS_TEST` setups are evaluated independently under `PaperExecutionEngine`'s limit-fill model, win rate drops from `67.9%` (`TRAIN`) to **`38.5%` (`5W / 8L`)** with near-zero net expectancy (`+$0.0071`/trade).
4. **Complete Absence of `ML` Filtering Power:**
   - Notice in Section 1 of our diagnostic run that `11-Sym Det` and `11-Sym ML` produced the **exact same 16 trades and `+$7.55` PnL**! Why? Because the trained Logistic Regression model (`calibrated_model.joblib`) passed `87` out of `88` feasible setups (`98.9%` pass rate), providing zero selective filtering against losing setups.
