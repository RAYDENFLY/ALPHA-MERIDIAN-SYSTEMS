# SignalGenerator — Zero-Setup Regression Forensic Audit Report

**Audit Date:** 2026-10-18 01:10:00 UTC  
**Target Repository:** `c:\Users\Administrator\Documents\SignalGenerator`  
**Git Baseline Commit:** `a89b15a4a24b84a4db5f7e622fefffc70c876f75` (HEAD on `research/profitability-validation`)  
**Active Daemon:** PID `27256` (`python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000`)  
**Execution Mode:** `SCANNER_MODE=PAPER`, `live_trading_locked=True`  
**Forward Cohort Anchor:** `2026-10-17 15:00:00 UTC`  

---

## 1. Executive Summary & Final Classification

### Overall Classification: `EXPECTED_FILTERING`

The investigation conclusively establishes that the absence of entry setups in recent scan cycles is **not a strategy logic regression, not a data pipeline failure, and not an API dashboard defect**. It is the deterministic, intended behavior of the baseline strategy reacting to current market conditions, combined with the successful remediation of a stale-signal display bug:

1. **Current Market State (100% Rejection at Dealing-Range Pre-Screen):** All 11 symbols in the watchlist are currently consolidating inside the middle 76% of their 72-hour dealing ranges ($0.1343 \le \text{pos}_{72} \le 0.7474$). Under the non-negotiable baseline parameters (`discount_threshold=0.12`, `premium_threshold=0.88`, `bull_breakout_pos_72h=0.92`), every symbol is classified as being in **Dealing Range Equilibrium**. Not a single symbol qualifies for Mode 1 (Premium Exhaustion Short), Mode 2A (Discount Rejection Long), or Mode 2B (Bull Breakout Expansion Long).
2. **Resolution of Stale Signal Leakage:** The setups that previously appeared on the dashboard were 128 historical rows created on October 14–15, 2026. Every one of those historical setups had failed sizing feasibility (`INFEASIBLE`) or expired (`STALE`). Prior to the stale-signals remediation, the `/api/setups` endpoint fell back to reading historical journal entries whenever the runner's in-memory results were empty. With the remediation deployed, `/api/setups` respects the completed scan state and reports live scan results (`raw_candidates = []`), preventing dead, 3-day-old signals from being displayed as active trades.
3. **Reason-Code Observability Nuance:** In `src/scheduler/runner.py`, when `SetupGenerator.generate_candidate()` returns `None`, the runner unconditionally logs `stage="SETUP_REJECTED"` with `reason_code="NO_SMC_CONFLUENCE"` and detail `"No SMC/SnR structural setup found in dealing range and order blocks."`. This downstream reason code lumps dealing-range equilibrium, lack of momentum impulse, and absence of order blocks into a single generic label, which masked the specific dealing-range rejection identified in previous audit reports.

---

## 2. Root Cause Analysis Ranked by Evidence

### Rank 1 (Primary Physical Cause): 72H Dealing Range Equilibrium Consolidation
- **Mechanism:** In [`src/strategy/generator.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/strategy/generator.py#L118-L178), Stage 0 of candidate generation computes:
  $$\text{pos}_{72} = \frac{P_{\text{close}} - \text{Low}_{72}}{\text{High}_{72} - \text{Low}_{72}}$$
  A candidate can only proceed if it satisfies at least one of three regimes:
  - **Mode 1 (Short):** $\text{pos}_{72} \ge 0.88$ AND $\text{ret}_{48} \ge +6.2\%$ AND $\text{ret}_{12} \ge +2.2\%$ AND $\text{ret}_{168} \le +4.5\%$ AND bear candle.
  - **Mode 2A (Long):** $\text{pos}_{72} \le 0.12$ AND $\text{ret}_{48} \le -6.2\%$ AND $\text{ret}_{12} \le -2.2\%$ AND $\text{ret}_{168} \ge +2.0\%$ AND bull candle.
  - **Mode 2B (Long):** $\text{pos}_{72} \ge 0.92$ AND $\text{ret}_{24} \ge +2.4\%$ AND $\text{ret}_{48} \ge +3.2\%$ AND $\text{ret}_{168} \ge +4.0\%$ AND bull candle AND $\text{VR}(24/12) \ge 1.17$ AND volume in $[1.1\times, 2.3\times]$.
  If none match, line 178 returns `None`.
- **Evidence:** Quantitative inspection of all 11 watchlist symbols on the latest closed 1H candle (`2026-10-17 17:00 UTC`) reveals that **100% of symbols reside between 0.1343 and 0.7474**. BTCUSDT is at 0.5053 (exact center); ETHUSDT is at 0.5373. Zero symbols meet extreme discount or extreme premium thresholds.

### Rank 2 (User Perception Cause): Stale Signal Cleanup & Decoupling Gate in `/api/setups`
- **Mechanism:** In [`src/api/app.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L495-L503):
  ```python
  has_completed_scan = bool(getattr(runner, "has_completed_scan", False)) if runner else False
  has_in_memory_results = bool(runner and runner.last_results)

  if runner and (has_completed_scan or has_in_memory_results) and not include_historical:
      raw_candidates = list(runner.last_results)
  else:
      db_setups = journal.get_latest_setups(limit=50)
  ```
- **Evidence:** 
  - Before the fix, `/api/setups` fell back to SQLite `setup_candidates` whenever `runner.last_results` was empty, presenting 128 historical setups from Oct 14–15 (all of which were `INFEASIBLE`, `STALE`, or `INVALIDATED`).
  - Once the scanner completed its post-reload cycle (`has_completed_scan = True`), `/api/setups` correctly served `runner.last_results` (`[]`).
  - Querying `http://127.0.0.1:8000/api/setups` returns `{"status": "NO_TRADE", "count": 0, "ready_count": 0}`.
  - Querying `http://127.0.0.1:8000/api/setups?include_historical=true` confirms all 11 historical symbols are still present in SQLite, but all 11 are dynamically evaluated as `STALE` because their candle age (~60h) far exceeds the 3.0h staleness threshold.
  - When the user visited the web interface, the dashboard accurately showed "NO TRADE" with 0 cards instead of leaking stale signals.

### Rank 3 (Observability Discrepancy): Coarse Reason-Code Masking in `ScannerRunner`
- **Mechanism:** In [`src/scheduler/runner.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/scheduler/runner.py#L371-L385):
  ```python
  if candidate is None:
      self.journal.save_lifecycle_event(
          candidate_id=f"NO_SETUP_{sym}_{now_ms}",
          symbol=sym,
          stage="SETUP_REJECTED",
          timestamp_ms=now_ms,
          reason_code="NO_SMC_CONFLUENCE",
          reason_detail="No SMC/SnR structural setup found in dealing range and order blocks.",
          metadata={"candle_time_ms": latest_c1.timestamp_ms},
      )
  ```
- **Evidence:** `SetupGenerator.generate_candidate()` returns `None` as soon as line 178 fails. It does not return a detailed rejection object. The runner interprets any `None` as `NO_SMC_CONFLUENCE`. Thus, while previous audit reports correctly diagnosed the mathematical cause (dealing range equilibrium `[0.12, 0.88]`), the runtime lifecycle event logs recorded `NO_SMC_CONFLUENCE`. This is downstream telemetry coarseness, not an independent rejection stage.

---

## 3. Per-Symbol Rejection Matrix (All 11 Watchlist Symbols)

Evaluated as of the latest closed 1H bar (`2026-10-17 17:00:00 UTC`):

| Symbol | 1H Bars | 4H Bars | Close Price | 72H Low | 72H High | 72H Range Pos | 12H Ret | 24H Ret | 48H Ret | 168H Ret | 4H Bias | 1H Struct | OBs / FVGs | First Failing Predicate | Generator Output |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :---: |
| **ADAUSDT** | 8,839 | 2,209 | $0.2204 | $0.2158 | $0.2334 | **0.2614** | -1.69% | -1.12% | -4.63% | -13.64% | BEARISH | BEARISH | 8 / 31 | `EQUILIBRIUM_ZONE_72H` (pos 0.2614 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **XRPUSDT** | 8,839 | 2,209 | $1.2924 | $1.2830 | $1.3530 | **0.1343** | -1.30% | -0.52% | -3.59% | -7.91% | BEARISH | BEARISH | 6 / 29 | `EQUILIBRIUM_ZONE_72H` (pos 0.1343 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **BTCUSDT** | 8,839 | 2,209 | $81,349.0 | $79,743.6 | $82,920.7 | **0.5053** | -0.24% | +0.21% | -1.26% | -1.97% | NEUTRAL | BEARISH | 13 / 20 | `EQUILIBRIUM_ZONE_72H` (pos 0.5053 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **BNBUSDT** | 8,839 | 2,209 | $715.31 | $709.56 | $736.69 | **0.2119** | -1.05% | -0.93% | -2.64% | -4.65% | BEARISH | BEARISH | 8 / 29 | `EQUILIBRIUM_ZONE_72H` (pos 0.2119 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **ETHUSDT** | 8,839 | 2,209 | $2,410.70 | $2,315.20 | $2,492.93 | **0.5373** | -0.69% | -0.02% | -1.43% | -3.74% | BEARISH | BULLISH | 10 / 30 | `EQUILIBRIUM_ZONE_72H` (pos 0.5373 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **SOLUSDT** | 8,839 | 2,209 | $103.39 | $100.56 | $107.37 | **0.4156** | -1.44% | -0.26% | -2.34% | -6.01% | BEARISH | BEARISH | 8 / 24 | `EQUILIBRIUM_ZONE_72H` (pos 0.4156 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **AVAXUSDT** | 8,839 | 2,209 | $9.7700 | $9.3440 | $10.5190 | **0.3626** | -2.38% | -2.31% | -2.07% | -6.92% | BEARISH | BEARISH | 9 / 42 | `EQUILIBRIUM_ZONE_72H` (pos 0.3626 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **LINKUSDT** | 8,839 | 2,209 | $11.956 | $11.820 | $12.765 | **0.1439** | -2.59% | -2.86% | -4.63% | -8.87% | BEARISH | BEARISH | 13 / 36 | `EQUILIBRIUM_ZONE_72H` (pos 0.1439 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **XMRUSDT** | 6,650 | 1,662 | $560.00 | $528.19 | $570.75 | **0.7474** | +0.25% | +1.27% | +2.28% | +7.03% | BULLISH | BEARISH | 17 / 50 | `EQUILIBRIUM_ZONE_72H` (pos 0.7474 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **DOGEUSDT** | 8,839 | 2,209 | $0.07929 | $0.07759 | $0.08283 | **0.3244** | -1.49% | -0.75% | -2.35% | -7.89% | BEARISH | BEARISH | 11 / 28 | `EQUILIBRIUM_ZONE_72H` (pos 0.3244 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |
| **LTCUSDT** | 8,839 | 2,209 | $58.70 | $57.00 | $61.44 | **0.3829** | -1.49% | -1.48% | -2.91% | -8.61% | BEARISH | BEARISH | 13 / 47 | `EQUILIBRIUM_ZONE_72H` (pos 0.3829 $\in [0.12, 0.88]$) | `None` (`NO_TRADE`) |

### Key Takeaways from the Matrix:
- All 11 symbols possess abundant Order Blocks (6 to 17 per symbol) and Fair Value Gaps (20 to 50 per symbol).
- The rejection is **not due to lack of candles, missing SMC primitives, or API errors**.
- Every single symbol fails at line 178 of `generator.py` because price is oscillating between 13.4% and 74.7% of its 72-hour range.
- BTC ($81,349) is sitting at 50.5% of its $79,743–$82,920 range.
- ETH ($2,410) is sitting at 53.7% of its $2,315–$2,492 range.

---

## 4. Old-versus-Current Logic Differences

### Git Baseline Comparison (`git diff a89b15a4a24b84a4db5f7e622fefffc70c876f75`)

| Component | Diff vs Baseline `a89b15a4` | Assessment |
| :--- | :---: | :--- |
| `src/strategy/generator.py` | **0 lines changed (Identical)** | Baseline strategy logic, dealing range thresholds, and filters have **never been modified**. |
| `src/smc/*.py` | **0 lines changed (Identical)** | Swing detection, structure evaluation, order block detection, and FVG detection are unchanged. |
| `src/snr/*.py` | **0 lines changed (Identical)** | Support & resistance clustering and clearance buffers are unchanged. |
| `src/scheduler/runner.py` | Instrumented Lifecycle Events | Added explicit `SCAN_STARTED`, `SETUP_REJECTED`, and `SCAN_COMPLETED` audit telemetry into `paper_lifecycle_events`. No candidate filtering logic was altered. |
| `src/api/app.py` | Risk Sync & Stale Decoupling | Added `risk_mgr.sync_state_from_journal(journal)` on startup. Refined `/api/setups` so completed live scans serve `runner.last_results` instead of falling back to stale DB rows. |

### Origin of Historical Candidates in SQLite (`setup_candidates`)
1. **Timestamp of Existing DB Records:** All 128 rows in `setup_candidates` were inserted between `2026-10-14 18:22:10 UTC` and `2026-10-15 14:01:14 UTC`.
2. **Strategy Evolution Prior to Commit `a89b15a4`:**
   - Forensic examination of `SETUP_BTCUSDT_1792069200000_SHORT` (created Oct 15 14:01 UTC) reveals that on that candle, `pos_72 = 0.0998` (deep discount), yet a SHORT setup was created based purely on a 1H Bearish BOS.
   - The setup lacked any `SMC Premium Dealing Range Exhaustion` evidence tag (which is mandatory in all current SHORT setups).
   - This proves that prior to the initial commit `a89b15a4` on Oct 17, the repository generated naive SMC setups without the 72H dealing-range pre-screen.
   - The All-Weather Dealing Range Pre-Screen was implemented specifically to eliminate those counter-trend, negative-expectancy trades.
   - Importantly, **not a single one of those 128 historical setups was ever `READY` or `VALID`**; 94 were `INFEASIBLE` and 34 were `STALE` or `INVALIDATED`.

---

## 5. API & Dashboard Visibility Findings

### Pipeline Trace: Generator $\to$ Runner $\to$ DB $\to$ API $\to$ Frontend

```mermaid
flowchart TD
    A["ScannerRunner.run_scan_cycle()"] --> B["SetupGenerator.generate_candidate()"]
    B -- "pos_72 in [0.12, 0.88]" --> C["Returns None"]
    C --> D["runner.py logs SETUP_REJECTED (NO_SMC_CONFLUENCE)"]
    D --> E["runner.last_results = [] (0 candidates)"]
    E --> F["ScannerRunner sets has_completed_scan = True"]
    
    G["Client GET /api/setups"] --> H{"has_completed_scan is True?"}
    H -- Yes (Live mode) --> I["raw_candidates = runner.last_results = []"]
    H -- No (Stale fallback) --> J["db_setups = journal.get_latest_setups()"]
    
    I --> K["Return count=0, status=NO_TRADE"]
    K --> L["Dashboard renders 'NO TRADE' container (Cards disappear)"]
    
    subgraph Stale_Remediation ["Stale Signal Safety Gate"]
        J --> M["Evaluate candle_age > 3.0h or now > expires_at_ms"]
        M --> N["Mark status = STALE"]
    end
```

### Explaining the User Experience ("Tadi kan saya buka web nya isinya kek kosong gitu"):
1. In earlier sessions, before the stale signals remediation, opening the dashboard caused `/api/setups` to load 11 old records from SQLite, rendering multiple setup cards (even though they were stale and infeasible).
2. With the deployment of the stale-signal decoupling gate, the API correctly recognizes that a live scan has completed and produced 0 setups.
3. Therefore, `/api/setups` returns `count: 0, status: "NO_TRADE"`, and the frontend displays the informative "NO TRADE" card:
   > *"No setup qualifies across enabled watchlist symbols at current candle close. The deterministic SMC/SnR engine filters out low-probability conditions to protect account capital."*
4. What the user observed was **not a blank/broken website**, but the clean, correct presentation of the `NO_TRADE` market state, free from phantom historical signals.

---

## 6. Controlled Regression Test Results on Identical Candles

To prove that `SetupGenerator` has not suffered any internal logic regression, the current codebase was evaluated across historical candle data stored in `data/scanner.db`:

1. **Test Scope:** 95,007 historical 1H candles across all 11 symbols (spanning October 2025 to October 2026).
2. **Setup Generation Results:**
   - Under current logic, `SetupGenerator` generated **exactly 92 candidate setups** across the full historical dataset.
   - The setups span multiple regimes (`Breakout`, `Pullback`, `Reversal`, `Range/Mean Reversion`).
   - Re-running `SetupGenerator` on the same candle indices produces **100% identical outputs** (identical candidate IDs, directions, entry prices, stops, and take profits).
3. **Temporal Distribution of Setups:**
   - 2026-01-01 to 2026-08-31: 82 setups (~10.2 setups/month across 11 symbols).
   - 2026-09-01 to 2026-09-20: 10 setups.
   - **2026-09-20 14:00 UTC to 2026-10-17 17:00 UTC (past 27 days): 0 setups.**
4. **Conclusion:** Trade generation frequency is naturally selective (~1.8 trades per week across the entire 11-symbol watchlist over 1 year). During extended multi-week consolidations where prices oscillate in range equilibrium, clusters of zero setups are expected and mathematically verified.

---

## 7. Verification of Mandatory Safety Constraints

- `SCANNER_MODE=PAPER`: Verified in running process PID `27256` and `/api/status`.
- `live_trading_locked=True`: Enforced and verified; zero real exchange orders submitted.
- Forward Cohort Anchor: Maintained at `2026-10-17 15:00:00 UTC`.
- Database Integrity: `data/scanner.db` records (`setup_candidates`: 128 rows, `paper_lifecycle_events`: 69 rows, `trade_journal`: 0 rows) fully preserved. Zero rows deleted or reset.
- Strategy Parameters: Strictly untouched:
  - `discount_threshold = 0.12`
  - `premium_threshold = 0.88`
  - `bull_breakout_pos_72h = 0.92`
  - `snr_opposing_wall_buffer_r = 1.2`

---

## 8. Summary & Next Operational Steps

| Aspect | Finding | Recommendation |
| :--- | :--- | :--- |
| **Strategy Health** | `PASS` (Deterministic, zero lookahead, identical to baseline commit) | Maintain current strategy parameters; do not relax filters to artificially force signals. |
| **Data Pipeline** | `PASS` (11/11 symbols synchronized, 8,839 1H bars per symbol) | Pipeline is healthy and ready for live candle intake. |
| **API & Dashboard** | `PASS` (Decoupled, zero stale signal leakage, accurate "NO TRADE" card) | Inform user that empty setup cards represent clean `NO_TRADE` protection, not a UI failure. |
| **Telemetry Precision** | `MINOR OBSERVABILITY GAP` (`NO_SMC_CONFLUENCE` masks dealing range) | In a future non-breaking enhancement, `generate_candidate()` can return a diagnostic reason enum (`EQUILIBRIUM_ZONE_72H`) so lifecycle events distinguish dealing-range rejection from lack of SMC patterns. |
| **Forward Cohort Status** | `READY_FOR_MONITORING` (`UNPROVEN_NO_COMPLETED_FORWARD_TRADES`) | Allow the daemon to continue background hourly scans. When market volatility pushes a symbol into extreme discount ($\le 0.12$) or premium ($\ge 0.88$), paper orders will generate naturally. |
