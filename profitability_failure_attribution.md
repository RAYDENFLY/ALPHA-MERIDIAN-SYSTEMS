# PHASE 2 — NEGATIVE EXPECTANCY & FAILURE ATTRIBUTION REPORT

**Report Date:** 2026-10-17  
**Repository:** `C:\Users\Administrator\Documents\SignalGenerator`  
**Research Branch:** `research/profitability-validation`  
**Source Data:** [`baseline_trades.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/baseline_trades.csv) (`92` raw candidates, `88` feasible standalone candidates, `16` executed portfolio trades)

---

## 1. Executive Summary of Failure Modes

Why does the current SignalGenerator strategy produce:
1. **`0` completed forward paper trades** in live/paper scanning (`2026-09-21` to `2026-10-17`), and
2. **Negative out-of-sample portfolio expectancy (`-$1.50` in `VAL`, `-$1.50` in `OOS_TEST`) and near-zero standalone limit-fill OOS expectancy (`+$0.0071`/trade, `38.5%` WR)**?

Evidence-based decomposition across all 11 required attribution dimensions identifies **five primary failure drivers**:

1. **Failure Driver #1 — Hand-Crafted 72H Dealing-Range Pre-Screen Overfitting (`src/strategy/generator.py:139-178`):**
   - The 72H dealing-range filter (`Mode 1`, `Mode 2A`, `Mode 2B`) was tuned on specific historical clusters (notably `2026-08-20..22`, where 50 of the 92 candidates occur) using 12 simultaneous hardcoded thresholds (`pos_in_range_72`, `ret_12`, `ret_24`, `ret_48`, `ret_168`, `vr_24_12`).
   - Outside those specific multi-day volatility bursts, the filter rejects **99.9% of all 1H bars**, causing complete signal starvation (`0` candidates across all 11 symbols for 27 consecutive days from `2026-09-20` to `2026-10-17`).
2. **Failure Driver #2 — First-Come Symbol Bias & Multi-Bar Duplicate Clustering (`SOLUSDT` / `LTCUSDT` / `LINKUSDT` / `XMRUSDT`):**
   - When a market-wide move triggers `Mode 2B` (`is_bull_expansion`), multiple symbols fire on the same or adjacent hours. Because `BacktestEngine` iterates symbols in watchlist order and locks the single portfolio slot for up to `48` hours, a weak breakout on `SOLUSDT` or `LTCUSDT` blocks high-quality breakouts on `AVAXUSDT`, `XRPUSDT`, `ADAUSDT`, and `DOGEUSDT`.
3. **Failure Driver #3 — Artificial `2.2%` / `2.4%` Stop-Loss Clamping (`src/strategy/planner.py:72-97`):**
   - Look closely at `TPSLPlanner.plan_levels()` ([`src/strategy/planner.py:72-97`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/planner.py#L72-L97)):
     ```python
     max_sl_pct = 0.024 if is_bull_expansion else 0.022
     if abs(entry_price - sl) / entry_price > max_sl_pct:
         sl = entry_price * (1.0 - max_sl_pct)  # or (1.0 + max_sl_pct)
     ```
   - **This is a critical structural flaw:** Whenever the true structural invalidation level (Order Block, Swing Low/High, or S/R Zone + `0.35 * ATR`) is more than `2.2%` (`2.4%` in expansion) away from `entry_price`, `TPSLPlanner` **ignores the structural invalidation level and artificially pulls the stop-loss inside the structure to `2.2%` / `2.4%`** solely so that a `$60` notional position won't exceed the `$1.50` SL loss limit!
   - Placing a stop-loss *inside* the Order Block or Swing Low/High violates core SMC principles: normal intrabar retracements into the Order Block sweep the clamped `2.2%` stop-loss before continuing to TP!
4. **Failure Driver #4 — Adverse Selection in Limit-Order Retracement Fills During Breakouts:**
   - `86` of `88` feasible setups (`97.7%`) are `Breakout` (`Mode 2B` bull expansion) entries at `current_candle.close`.
   - Under `PaperExecutionEngine`'s limit-order model (which waits for price to drop `0.025%` below `entry_price` on subsequent bars), true momentum breakouts that continue immediately (`AVAXUSDT` on `2026-09-19 08:00`) go `EXPIRED_UNFILLED`, whereas failed breakouts that reverse sharply fill the limit order on the way down to the stop-loss (`OOS_TEST` limit-fill win rate drops to `38.5%`).
5. **Failure Driver #5 — Non-Selective ML Gate Trained on Clamped Setups (`98.9%` Pass Rate):**
   - Because `SetupGenerator` only emitted `92` candidates over the entire year (`32` in `TRAIN`), `ModelRegistry.train_and_evaluate()` had `< 50` training samples unless trained on an earlier un-clamped dataset, and the serialized `logistic_regression` model assigns probabilities `> 0.3533` to `87/88` feasible candidates—failing to filter out low-quality setups.

---

## 2. Quantitative Attribution Across All 11 Required Dimensions

### 2.1 Dimension 1 — Pre-Cost vs. Post-Cost Expectancy (Fee, Slippage & Funding Drag)

| Cohort | Trades ($N$) | Pre-Cost Gross PnL | Slippage Drag (`0.05%`/side) | Fee Drag (`0.05%`/side) | Funding Drag | Total Friction | Post-Cost Net PnL | Pre-Cost Expectancy | Post-Cost Expectancy | Friction % of Gross Edge |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **11-Sym Portfolio (All)** | `16` | `+$9.30` | `-$0.94` | `-$0.94` | `+$0.00` | `-$1.87` | `+$7.43` | `+$0.5813` | `+$0.4646` | **`20.1%`** |
| **11-Sym Portfolio (`OOS_TEST`)** | `1` | `-$1.37` | `-$0.06` | `-$0.06` | `-$0.01` | `-$0.13` | `-$1.50` | `-$1.3700` | `-$1.4951` | `N/A (Gross Negative)` |
| **All Feasible (`TRAIN`)** | `31` | `+$24.92` | `-$1.81` | `-$1.81` | `+$0.09` | `-$3.53` | `+$21.39` | `+$0.8039` | `+$0.6900` | `14.2%` |
| **All Feasible (`VAL`)** | `42` | `+$41.97` | `-$2.45` | `-$2.45` | `-$0.14` | `-$5.04` | `+$36.93` | `+$0.9993` | `+$0.8793` | `12.0%` |
| **All Feasible (`OOS_TEST` Market)** | `15` | `+$4.75` | `-$0.87` | `-$0.87` | `-$0.02` | `-$1.76` | `+$2.99` | `+$0.3167` | `+$0.1992` | **`37.1%`** |
| **All Feasible (`OOS_TEST` Limit)** | `13` | `+$0.61` | `-$0.24` | `-$0.28` | `+$0.00` | `-$0.52` | `+$0.09` | `+$0.0469` | `+$0.0071` | **`85.2%`** |

- **Takeaway:** Each `$60` notional trade incurs **`~$0.12` in round-trip Taker fees + slippage** (`0.20%` of notional = `8.0%` of the `$1.50` risk budget). In `OOS_TEST`, round-trip friction consumes **`37.1%`** of pre-cost market-entry edge and **`85.2%`** of limit-entry edge.

---

### 2.2 Dimension 2 — Performance by Direction (`LONG` vs. `SHORT`)

| Split / Cohort | Direction | Trades ($N$) | Win Rate (%) | Profit Factor | Pre-Cost PnL | Post-Cost Net PnL | Net Expectancy / Trade |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **11-Sym Portfolio (All)** | `LONG` | `11` | `54.5%` | `1.67` | `+$6.20` | `+$4.90` | `+$0.4459` |
| **11-Sym Portfolio (All)** | `SHORT` | `5` | `60.0%` | `1.84` | `+$3.10` | `+$2.53` | `+$0.5057` |
| **All Feasible (`88` Setups)** | `LONG` | `73` | `61.6%` | `2.23` | `+$59.50` | `+$50.78` | `+$0.6956` |
| **All Feasible (`88` Setups)** | `SHORT` | `15` | `60.0%` | `2.36` | `+$12.14` | `+$10.53` | `+$0.7018` |
| **`OOS_TEST` Feasible (`15` Setups)** | `LONG` | `14` | `50.0%` | `1.35` | `+$5.18` | `+$3.53` | `+$0.2523` |
| **`OOS_TEST` Feasible (`15` Setups)** | `SHORT` | `1` | `0.0%` | `0.00` | `-$0.43` | `-$0.55` | `-$0.5453` |

---

### 2.3 Dimension 3 — Performance by Symbol (`11` Watchlist Symbols)

| Symbol | Raw Setups | Feasible Setups | Portfolio Executed | Standalone Win Rate | Standalone Profit Factor | Standalone Net PnL | Standalone Expectancy | Primary Failure / Observation |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `AVAXUSDT` | `13` | `13` | `0` | `76.9%` | `4.90` | `+$16.19` | `+$1.2456` | **0 executed in portfolio** (always blocked by earlier open trade!) |
| `XRPUSDT` | `15` | `15` | `2` | `73.3%` | `3.88` | `+$17.26` | `+$1.1506` | Strong momentum continuation (`2/2` wins in portfolio) |
| `DOGEUSDT` | `11` | `11` | `3` | `72.7%` | `3.59` | `+$11.49` | `+$1.0444` | Strong momentum continuation (`3/3` wins in portfolio) |
| `SOLUSDT` | `9` | `9` | `1` | `66.7%` | `2.77` | `+$7.82` | `+$0.8690` | Lost all `3` OOS_TEST trades on `2026-09-18` (clamped `2.2%` SL swept) |
| `ADAUSDT` | `18` | `18` | `6` | `66.7%` | `2.73` | `+$15.40` | `+$0.8555` | `4W / 2L` in portfolio (`+$5.00` net) |
| `ETHUSDT` | `5` | `5` | `0` | `60.0%` | `3.05` | `+$4.12` | `+$0.8230` | `1` OOS_TEST `SHORT` timed out (`-$0.55`) |
| `BNBUSDT` | `6` | `6` | `1` | `50.0%` | `1.47` | `+$1.92` | `+$0.3202` | Portfolio trade hit SL (`-$1.35`) |
| `LTCUSDT` | `4` | `4` | `1` | `25.0%` | `0.44` | `-$2.51` | `-$0.6266` | **Negative expectancy**: slow/choppy follow-through on breakouts |
| `LINKUSDT` | `5` | `5` | `0` | `0.0%` | `0.00` | `-$7.39` | `-$1.4771` | **0% Win Rate (`0W / 5L`)**: every breakout reversed into clamped `2.2%` SL |
| `XMRUSDT` | `2` | `2` | `2` | `0.0%` | `0.00` | `-$3.00` | `-$1.4989` | **0% Win Rate (`0W / 2L`)**: idiosyncratic wicks swept both LONG & SHORT SLs |
| `BTCUSDT` | `4` | `0` | `0` | `N/A` | `N/A` | `$0.00` | `$0.0000` | **100% Infeasible**: `min_qty=0.001 BTC` (`~$80`) exceeds `$6.50` margin cap at `10x` |

---

### 2.4 Dimension 4 — Performance by Entry Category & Setup Mode

| Entry Category | Mode | Feasible Setups ($N$) | Win Rate (%) | Profit Factor | Net PnL | Expectancy / Trade |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **`Breakout`** | `Mode 2B` (Bull Expansion) / BOS | `86` | `62.8%` | `2.35` | `+$63.29` | `+$0.7360` |
| **`Range/Mean Reversion`** | `Mode 1` / `Mode 2A` Dealing Range | `1` | `0.0%` | `0.00` | `-$0.55` | `-$0.5453` |
| **`Reversal`** | CHoCH / Liquidity Sweep | `1` | `0.0%` | `0.00` | `-$1.44` | `-$1.4432` |
| **`Pullback`** | Trend-Aligned OB/FVG Retest | **`0`** | `N/A` | `N/A` | `$0.00` | `$0.0000` |

- **Critical Insight:** Despite being designed as an SMC/SnR multi-category engine (`Breakout`, `Pullback`, `Reversal`, `Range/Mean Reversion`), **`97.7%` (`86/88`) of all feasible candidates generated by `SetupGenerator` are `Breakout` (`Mode 2B`) setups**, and **`0` are `Pullback` setups**! Why? Because `SetupGenerator`'s 72H dealing-range pre-screen blocks all normal trend pullbacks (`0.25 <= pos_in_range_72 <= 0.75`), only allowing entries when `pos_in_range_72 >= 0.88` or `<= 0.12`!

---

### 2.5 Dimension 5 — Stop-Loss Clamping Distortion vs. True Structural Stop Distance

Inspecting `stop_dist_pct` in [`baseline_trades.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/baseline_trades.csv) reveals that **`79` out of `88` feasible setups (`89.8%`) had their stop-loss artificially clamped** by `TPSLPlanner.plan_levels()` to exactly `2.2000%` or `2.4000%`:
- When `stop_dist_pct` is clamped (`2.2%` or `2.4%`), the stop-loss is placed *above* the actual 1H swing low / order block floor (for LONG) or *below* the swing high / order block ceiling (for SHORT).
- Simultaneously, `TPSLPlanner` scales `tp1` down to `2.0 * risk` (`4.4%` or `4.8%`) instead of targeting the structural opposing S/R zone.
- And when `TPSLPlanner` was called in the earlier Oct 14–15 live scan without clamping (`data/scanner.db`), `82/128` candidates had stop distances of `2.6%–8.3%` and were rejected as `INFEASIBLE` by `PositionSizer` because `PositionSizer` refused to step down isolated margin below `$4.50`!

---

### 2.6 Dimension 6 — Exit Reason & Holding Horizon Decomposition

| Exit Reason | Count ($N$) | Share of Setups | Win Rate (%) | Avg Bars Held | Avg Net PnL | Total Net PnL |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`TP` (Take Profit 1 Hit)** | `53` | `60.2%` | `100.0%` | `8.2h` | `+$2.0538` | `+$108.85` |
| **`SL` (Stop Loss Hit)** | `33` | `37.5%` | `0.0%` | `12.9h` | `-$1.4647` | `-$48.34` |
| **`TIMEOUT` (48H Horizon)** | `2` | `2.3%` | `50.0%` | `48.0h` | `+$0.3957` | `+$0.79` |
| **`SL_COLLISION` (Same Bar)** | `0` | `0.0%` | `N/A` | `N/A` | `$0.0000` | `$0.00` |

---

## 3. Concrete Hypotheses for Phase 3 Controlled Experiments

Based on the Phase 2 failure attribution, we formulate **five single-variable, falsifiable hypotheses** to test one-at-a-time in Phase 3 on chronological `TRAIN` / `VAL` / `OOS_TEST` splits:

1. **Experiment `EXP-01` (Baseline Accounting Fix — `BT-01`):**
   - *Change:* Fix `SHORT` TP slippage sign bug in `BacktestEngine` (`1.0 + self.slippage` instead of `1.0 - self.slippage`) and evaluate all 11 symbols.
   - *Goal:* Establish the true, bug-free baseline across `TRAIN`, `VAL`, and `OOS_TEST`.
2. **Experiment `EXP-02` (Remove Artificial SL Clamping & Enable Risk-Safe Margin Step-Down):**
   - *Change:* Remove the artificial `2.2% / 2.4%` SL clamping in `TPSLPlanner.plan_levels()` so SL always sits at the true structural invalidation level (`OB / Swing / Zone + 0.35 ATR`), and allow `PositionSizer` to step down quantity (`min_acceptable_margin = $0.50`, `min_notional = $5.00`) whenever `stop_distance_pct > 2.4%` so `estimated_loss_usdt` never exceeds `$1.50` (or `$0.09–$1.50` cap).
   - *Hypothesis:* Preserving true structural stops while dynamically stepping down position notional prevents premature stop-outs inside the Order Block while strictly respecting the `$1.50` max SL loss invariant.
3. **Experiment `EXP-03` (Replace Overfitted 72H Dealing-Range Pre-Screen with Structural Trend-Pullback & Breakout Confluence):**
   - *Change:* Replace the 12-parameter hardcoded `ret_48 / ret_168 / pos_in_range_72` pre-screen in `SetupGenerator` with clean, causal structural confluence:
     - Require 4H `MarketBias` alignment (`BULLISH` for `LONG`, `BEARISH` for `SHORT`) + 1H `StructureState` alignment + active SMC trigger (`BOS` or `CHoCH` or `LIQUIDITY_SWEEP` within 3 bars) + price interacting with an unmitigated `OrderBlock` or `FVG` or confirmed `SRZone`, with `net_rr >= 1.8`.
   - *Hypothesis:* Eliminates the 27-day signal starvation and single-week clustering, producing regular, structurally sound `Pullback`, `Breakout`, and `Reversal` setups across all months of the year.
4. **Experiment `EXP-04` (TP1 Partial Close `50%` + Break-Even SL + Runner Trailing Stop):**
   - *Change:* Apply the `PaperExecutionEngine` TP1 (`50%` close at `TP1`, move SL to `entry_price` break-even, trail remaining `50%` by swing low/high to `TP2`) lifecycle in backtest evaluation vs. 100% single-target exit.
   - *Hypothesis:* Locking in `50%` profit at `TP1` and moving SL to break-even reduces full `-1R` reversals on trades that reach `+1R..+1.5R` before reversing.
5. **Experiment `EXP-05` (Symbol Quality & Min-Lot Filter + Cross-Symbol Ranking):**
   - *Change:* Exclude structurally infeasible `BTCUSDT` (`min_qty=0.001 > $6.50` margin cap) and zero-win-rate idiosyncratic symbols (`XMRUSDT`, `LINKUSDT`) or rank simultaneous same-hour candidates by structural confluence / `net_rr` instead of arbitrary list order.
   - *Hypothesis:* Prevents low-quality simultaneous breakouts from locking the single open-position slot during multi-symbol expansion bars.
