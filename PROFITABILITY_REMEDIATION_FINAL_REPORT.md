# PROFITABILITY RESEARCH & FORWARD PAPER VALIDATION — FINAL REMEDIATION REPORT

**Repository:** `c:\Users\Administrator\Documents\SignalGenerator`  
**Git Branch:** `research/profitability-validation`  
**Target Exchange:** AsterDEX Perpetual Futures (`https://fapi.asterdex.com`)  
**Execution Governance:** `SCANNER_MODE=PAPER` | `ExecutionMode.RESEARCH` | `live_trading_locked=True`  
**Audit Date:** `2026-10-17`

---

## 1. Root Cause of 0 Forward Paper Trades (`data/scanner.db`)

An end-to-end forensic audit of `data/scanner.db` and the execution pipeline (`SetupGenerator` $\rightarrow$ `SetupEvaluator` $\rightarrow$ `RiskManager` $\rightarrow$ `ScannerRunner` $\rightarrow$ `PaperExecutionEngine` $\rightarrow$ `JournalRepository`) established **5 concrete root causes** explaining why `trade_journal = 0` and `paper_orders = 0`:

1. **Historical Pre-Remediation Rejection (`96 / 128` stored candidates rejected):**
   - `data/scanner.db` contained `128` rows in `setup_candidates`, all generated between `2026-10-14 17:00 UTC` and `2026-10-15 13:00 UTC`.
   - `94` (`73.4%`) were rejected as `INFEASIBLE`:
     - `82` failed because wide structural stop distances (`>2.80%`) exceeded the `$1.50` hard SL loss limit at `$50–$60` notional before SL clamping (`clamp_stop_loss_to_risk_cap`) was introduced.
     - `12` (`BTCUSDT`) failed because `0.001 BTC` minimum lot size at `~$80,192` required `$80.19` notional (`$8.02` margin $> \$6.50$ margin cap).
   - `2` were `INVALIDATED`.
2. **Legacy Blocking ML Gate & Offline Window (`32 / 128` feasible candidates never submitted):**
   - `32` historical rows had `is_feasible=True`, but `30 / 32` had `ml_probability` between `0.081` and `0.242`, which were downgraded to `WATCHLIST` by the earlier blocking ML threshold (`0.3533`).
   - All `128` candidates expired (`expires_at_ms < now_ms`) while the scanner was offline for 47 hours between `2026-10-15 13:00 UTC` and `2026-10-17 12:00 UTC`, and were reconciled to `STALE` on startup.
3. **27-Day Signal Drought in `SetupGenerator` (`2026-09-20` to `2026-10-17`):**
   - Across the most recent 27 days (`648` hours $\times$ 11 symbols = `7,128` 1H bars), the 12-parameter 72H dealing-range pre-screen (`is_dealing_range` requiring `range_pct <= 12%`, `>=4` boundary touches, and `<=20%` outside closes simultaneously) generated **0 candidates**.
4. **Multi-Day Candle Sync Pagination Bug (`src/data/sync.py`):**
   - When `ScannerRunner.run_scan(..., sync_live=True)` invoked `CandleSynchronizer.sync_symbol(symbol, "1h", target_bars=20)` after an offline gap $>50$ hours, `sync_symbol()` requested `limit = max(20, 50) = 50` starting from `latest.timestamp_ms + 1` and stopped after a single page. Any offline gap $>50$ hours left the local SQLite database behind real time on the first scan cycle.
5. **Legacy Deserialization Bug in `CalibratedModelWrapper` (`src/ml/calibration.py`):**
   - Loading older `calibrated_model.joblib` artifacts produced before `self.used_fallback` was added raised `AttributeError: 'CalibratedModelWrapper' object has no attribute 'used_fallback'` inside `predict_proba()`, which was caught by `SetupEvaluator.evaluate_setup()` and silently degraded `ml_probability` to `None`.

---

## 2. Root Cause of Historical Backtest Underperformance & OOS Failure

We audited the 1-year historical dataset (`2025-10-14` to `2026-10-17`, `95,015` 1H candles and `23,711` 4H candles across 11 symbols, **0 missing bars**) across 4 baseline configurations and 11 failure attribution dimensions (documented in [`profitability_baseline_report.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/profitability_baseline_report.md) and [`profitability_failure_attribution.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/profitability_failure_attribution.md)):

| Baseline Configuration | Symbols | Fill Model | Trades | Win Rate | Gross PnL | Total Costs | Net PnL | Expectancy / Trade | Profit Factor | Max DD |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **B1: Dashboard API Default (`[:3]` symbols)** | 3 (`BTC`, `ETH`, `SOL`) | Market Entry (`0.05%` fee + `0.05%` slip) | `12` | `58.33%` | `+$9.62` | `$1.72` | `+$7.90` | `+$0.6581` | `2.05` | `$4.50` (`18.14%`) |
| **B2: Full 11-Symbol Portfolio (`BacktestEngine`)** | 11 | Market Entry (`0.05%` fee + `0.05%` slip) | `16` | `56.25%` | `+$9.71` | `$2.28` | `+$7.43` | `+$0.4644` | `1.71` | `$4.50` (`17.61%`) |
| **B3: Standalone All Feasible Setups (Uncapped)** | 11 | Market Entry (`0.05%` fee + `0.05%` slip) | `88` | `64.77%` | `+$73.60` | `$12.29` | `+$61.31` | `+$0.6967` | `2.32` | `$13.48` |
| **B4: Standalone Paper Limit-Fill Model (`PaperExecutionEngine`)** | 11 | Limit Entry (`0.02%` maker, `0.05%` buffer, 12-bar TTL, TP1 partial + BE + trail) | `74` filled (`14` expired) | `62.16%` | `+$47.62` | `$5.67` | `+$41.94` | `+$0.5668` | `2.70` | `$7.02` |

### Why the Strategy Fails Strict Chronological Out-of-Sample Validation
1. **Severe Temporal Clustering (`84%` of setups occur on just 6 calendar days):**
   - Out of `92` raw setups (`88` feasible) across 368 days, `50` setups (`54.3%`) occurred in a single 48-hour cluster (`2026-08-20` to `2026-08-22`) and `15` setups (`16.3%`) occurred in `2026-09-18` to `2026-09-20`.
2. **Portfolio Slot Blocking + `$0.18` Daily Loss Guard Collapses Out-of-Sample Portfolio PnL:**
   - Under the chronological walk-forward split with 48-hour pre-boundary purge/embargo (`TRAIN`: `2025-10-14` to `2026-08-18`; `VAL`: `2026-08-20` to `2026-09-01`; `OOS_TEST`: `2026-09-03` to `2026-10-17`):
     - **Portfolio `TRAIN`**: `12` trades, `58.3%` WR, `+$6.23` Net PnL (`+$0.5194`/trade).
     - **Portfolio `VAL`**: `2` trades (`3` after ranking fix), `50.0%`–`66.7%` WR, `+$0.60` to `+$6.19` Net PnL.
     - **Portfolio `OOS_TEST`**: **`1` trade (`SOLUSDT LONG` at `2026-09-18 16:00 UTC`), `0%` WR, `-$1.50` Net PnL (`-$1.5000`/trade)**. That single trade remained open for 34 hours across all `13` concurrent setups on `2026-09-18..19` (`max_open_positions=1`), and its `-$1.50` SL exit triggered the `$0.18` daily loss cap (`daily_loss_usdt >= $0.18`), blocking the remaining `2026-09-20` setups.
3. **Standalone Out-of-Sample Paper Limit-Fill Expectancy Is Near Break-Even (`+$0.0071`/trade):**
   - Even if every OOS setup is evaluated independently under `PaperExecutionEngine` (`B4`), the `13` filled OOS trades (`2026-09-18..20`) achieve only `38.46%` WR and `+$0.09` total net PnL (`+$0.0071`/trade), because round-trip fees + slippage consume `85.2%` of gross profit (`+$0.62` gross $\rightarrow$ `+$0.09` net).
4. **Backtest Accounting Defect (`BT-01`):**
   - In [`src/backtest/engine.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/backtest/engine.py#L290-L295), `SHORT` Take-Profit exits applied `setup.take_profit_1 * (1.0 - self.slippage)` (favorable downward slippage on a short cover) instead of adverse upward slippage `(1.0 + self.slippage)`, overstating `SHORT` TP PnL by `~$0.06` per winning short trade.

---

## 3. Experiments Tested & Rejected vs Kept (`profitability_experiment_registry.csv`)

Nine controlled single-variable experiments (`EXP-00` through `EXP-08`) were executed across the chronological `TRAIN` / `VAL` / `OOS_TEST` splits with a 48-hour purge/embargo window (full metrics recorded in [`profitability_experiment_registry.csv`](file:///c:/Users/Administrator/Documents/SignalGenerator/profitability_experiment_registry.csv)):

| Experiment ID | Hypothesis & Single Variable Changed | Portfolio Train (`n` / Exp) | Portfolio Val (`n` / Exp) | Portfolio OOS (`n` / Exp) | Standalone OOS (`n` / Exp) | Decision & Rationale |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **EXP-00** | Unmodified Baseline (Pre-BT-01 fix) | `12` / `+$0.5243` | `2` / `+$0.2980` | `1` / `-$1.5000` | `15` / `+$0.2202` | **SUPERSEDED** by `EXP-01` (contained `SHORT` TP slippage sign bug). |
| **EXP-01** | Fix `SHORT` TP slippage sign (`1.0 + slippage`) + populate `entry_category` & `setup_id` | `12` / `+$0.5194` | `2` / `+$0.2980` | `1` / `-$1.5000` | `15` / `+$0.2001` | **KEPT (Bug Fix)** — Restores conservative slippage accounting symmetry. |
| **EXP-02** | Disable pre-sizing SL clamping (`require natural structural stop <= 2.30%`) | `10` / `+$0.5747` | `2` / `+$0.2980` | `1` / `-$1.5000` | `15` / `+$0.2001` | **REJECTED** — Eliminates 53% of feasible setups (`88 -> 41`) with zero OOS improvement. |
| **EXP-03** | Tighter SL risk cap (`$1.00` instead of `$1.50`) | `10` / `-$0.1455` | `3` / `+$0.9228` | `2` / `-$0.9964` | `15` / `+$0.1235` | **REJECTED** — Degrades Train expectancy to `-$0.15`/trade due to premature stop-outs (`1.47%` max stop distance). |
| **EXP-04** | Rank concurrent same-timestamp setups by `net_rr * ml_prob` instead of watchlist array order | `12` / `+$0.5194` | `3` / `+$2.0628` | `1` / `-$1.5000` | `15` / `+$0.2001` | **KEPT (Execution Determinism Fix)** — Eliminates arbitrary watchlist index bias (`AVAXUSDT` first) during multi-symbol clusters, improving Validation split from `+$0.60` to `+$6.19`. |
| **EXP-05** | Relax dealing-range pre-screen (`range <= 18%`, `touches >= 3`, `outside <= 25%`) | `36` / `+$0.5561` | `3` / `+$0.1419` | `5` / `-$0.7506` | `47` / `-$0.0493` | **REJECTED** — Increases OOS sample size (`15 -> 47` standalone, `1 -> 5` portfolio), but **OOS expectancy turns negative (`-$0.0493`/trade standalone, `-$0.7506`/trade portfolio)**. Proves looser filters add noise, not edge. |
| **EXP-06** | Enforce `min_net_rr >= 1.8` filter | `10` / `+$0.4090` | `3` / `+$2.0628` | `1` / `-$1.5000` | `15` / `+$0.2001` | **REJECTED** — Reduces Train expectancy (`+$0.52 -> +$0.41`) with no OOS change. |
| **EXP-07** | Long-only regime filter (`direction == LONG`) | `8` / `+$0.8588` | `2` / `+$1.8058` | `1` / `-$1.5000` | `6` / `+$0.3401` | **REJECTED** — Higher in-sample expectancy, but cuts total sample size in half (`88 -> 38`, OOS standalone `15 -> 6`), worsening sample insufficiency. |
| **EXP-08** | Enforce `ML confidence >= 0.45` gate | `12` / `+$0.5194` | `3` / `+$2.0628` | `1` / `-$1.5000` | `15` / `+$0.2001` | **REJECTED** — In-sample overfitted model assigns `>0.48` to all historical setups while assigning `<0.25` to live October 2026 setups. Keeping ML informative-only is mandatory. |

---

## 4. Code & Pipeline Remediations Implemented

1. **Backtest Engine & Concurrent Ranking Fixes ([`src/backtest/engine.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/backtest/engine.py)):**
   - Fixed `SHORT` TP slippage sign (`1.0 + self.slippage`).
   - Populated `setup_id` and `entry_category` on backtest `TradeRecord` instances.
   - Ranked concurrent same-hour candidate setups deterministically by `net_reward_to_risk * ml_probability` rather than watchlist array order.
2. **ML Calibration Wrapper Backward Compatibility ([`src/ml/calibration.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/ml/calibration.py)):**
   - Added `__setstate__` and `getattr(self, "used_fallback", False)` guard so legacy serialized `calibrated_model.joblib` models never raise `AttributeError` during live or backtest evaluation.
3. **Multi-Page Candle Synchronization ([`src/data/sync.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/data/sync.py)):**
   - Updated `CandleSynchronizer.sync_symbol()` to paginate forward in batches of up to `500` candles (`max_pages=30`) whenever the local DB gap exceeds a single batch, ensuring live scans always evaluate the latest closed 1H/4H bar.
4. **10-Stage Auditable Paper Lifecycle State Machine ([`src/data/database.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/data/database.py), [`src/execution/types.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/execution/types.py), [`src/journal/repository.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/journal/repository.py), [`src/scheduler/runner.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/scheduler/runner.py), [`src/execution/paper_engine.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/execution/paper_engine.py)):**
   - Created `paper_lifecycle_events` table in SQLite and wired all 10 stages:
     `SCAN -> SETUP_DETECTED -> RISK_VALIDATION -> ELIGIBLE -> PAPER_ORDER_CREATED -> ENTRY_FILLED -> POSITION_OPEN -> EXIT_TRIGGERED -> POSITION_CLOSED -> JOURNAL_FINALIZED`
   - Every transition records `candidate_id`, `symbol`, `stage`, `timestamp_ms`, `reason_code`, `reason_detail`, `order_id`, `trade_id`, and JSON metadata.
5. **Forward Paper Validation Cohort & Dashboard Truthfulness ([`src/journal/repository.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/journal/repository.py), [`src/api/app.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/api/app.py), [`src/api/dashboard.html`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/api/dashboard.html)):**
   - Added `JournalRepository.compute_forward_validation_cohort(min_sample_size=30)` returning explicit validation statuses:
     - `UNPROVEN_NO_COMPLETED_FORWARD_TRADES` (`0` closed trades)
     - `INSUFFICIENT_FORWARD_EVIDENCE` (`1–29` closed trades)
     - `NEGATIVE_NET_EXPECTANCY` ($\ge 30$ closed trades with net expectancy $\le 0$)
     - `PRELIMINARY_POSITIVE_NOT_VALIDATED` ($\ge 30$ closed trades with net expectancy $> 0$)
   - Removed the `[:3]` symbol truncation in `/api/backtest` so the endpoint evaluates all enabled watchlist symbols and returns `chronological_splits` (`in_sample_train`, `validation`, `out_of_sample_test`, `historical_validation_status`) alongside `cost_assumptions`.
   - Updated `dashboard.html` to prominently display the Forward Paper Validation status banner, paper order/fill/completed trade counters, cost assumptions (`0.02%` maker / `0.05%` taker / `0.05%` slip), and the chronological Train / Val / OOS Test split breakdown.

---

## 5. Forward Paper Validation Status

- **Current Forward Paper Status:** **`UNPROVEN_NO_COMPLETED_FORWARD_TRADES`**
- **Current Database State (`data/scanner.db`):**
  - `candles`: `118,864` rows (synced through `2026-10-17 13:00 UTC` across all 11 watchlist symbols)
  - `setup_candidates`: `128` historical rows (`0` READY in current scan cycle)
  - `paper_orders`: `0` (`0` pending, `0` filled)
  - `trade_journal`: `0` (`0` open, `0` closed)
  - `paper_lifecycle_events`: active and recording every scan cycle (`SCAN` events logged for all 11 symbols)

---

## 6. Final Readiness Verdict

- **Strategy Edge Verdict:** **UNPROVEN / INSUFFICIENT OUT-OF-SAMPLE & FORWARD EVIDENCE**
  - While the 1-year full-sample backtest shows positive aggregate net PnL (`+$7.43` portfolio across `16` trades; `+$41.94` standalone across `74` paper limit-fill trades), **84% of all historical setups occurred on just 6 calendar days**, and the chronological Out-of-Sample Test split (`2026-09-03` to `2026-10-17`) produced only **`1` portfolio trade (`-$1.50` net PnL)** and **`13` standalone paper limit-fill trades (`+$0.0071`/trade net expectancy)**.
  - Relaxing the dealing-range filters to force more trades (`EXP-05`) was empirically tested and **rejected** because it drove Out-of-Sample expectancy negative (`-$0.0493`/trade standalone, `-$0.7506`/trade portfolio).
- **Live Trading Readiness:** **NOT READY FOR LIVE TRADING (`live_trading_locked=True` MUST REMAIN ENFORCED)**
  - Do **not** unlock live trading or loosen risk caps (`$6.00` isolated margin, `10x` leverage, `$1.50` hard SL cap, `$0.18` daily / `$0.54` weekly loss limits, `1` max open position, `3` max entries/week).
  - Keep the background paper scanner running continuously to accumulate a minimum cohort of `30` completed forward paper trades before any live-readiness re-evaluation.
