# SignalGenerator — Weekly Entry Hard Cap Removal & Risk Governance Audit

**Document Status:** Operational Risk & Policy Audit Report  
**Implementation Date:** 2026-10-18 01:44:10 UTC  
**Git HEAD Revision:** `28ffeb8` (with working-tree policy remediation)  
**Execution Mode:** `SCANNER_MODE=PAPER`, `live_trading_locked=True`  
**Forward Cohort Anchor:** `2026-10-17 15:00:00 UTC`  
**Forward Cohort Profitability Status:** `UNPROVEN_NO_COMPLETED_FORWARD_TRADES` (0 completed forward paper trades)  

---

## 1. Executive Summary & Policy Boundary

The hard restriction enforcing `max_entries_per_week=3` has been **completely removed as an entry blocker** across all components of SignalGenerator.

### Core Policy Directives:
1. **Opportunity-Driven Frequency:** Entry frequency is now governed exclusively by **market opportunity** (independently valid SMC/SnR setups) and **hierarchical risk controls**, rather than an arbitrary calendar quota.
2. **Preservation of Mandatory Risk Invariants:**
   - Single concurrent position limit: **`max_open_positions = 1`** (strictly enforced).
   - Daily loss limit: **`$0.18 USDT`** (net realized loss including fees, slippage, and funding).
   - Weekly loss limit: **`$0.54 USDT`** (net realized loss across current UTC week).
   - Per-trade hard SL cap: **`$1.50 USDT`** (enforced by `PositionSizer` during candidate sizing).
   - Leverage: capped at **`10x`** (Isolated Margin mode only).
   - Target margin: **`$6.00`** (max `$6.50`).
   - Setup quality gates: `READY`, `WATCHLIST`, `INFEASIBLE`, `INVALIDATED`, `STALE`, and duplicate suppression remain 100% active.
3. **Telemetry Preservation:** `weekly_entries_count` is retained strictly as an observational metric and telemetry counter. It increments on every valid trade entry and automatically resets at `00:00 UTC` every Monday. It no longer participates in trade gating.
4. **Forward Cohort Configuration Boundary:**
   - Pre-change cohort boundary: Operating under `max_entries_per_week=3` from `2026-10-17 15:00:00 UTC` to `2026-10-18 01:44:10 UTC`.
   - Post-change cohort boundary: Operating under opportunity-driven entry policy from `2026-10-18 01:44:10 UTC` onward.
   - Historical records and forward paper cohort status remain untainted (`0` completed paper trades, database untouched).

---

## 2. Enforcement Points Discovered & Remediated

An exhaustive audit of the entire codebase identified and remediated all points associated with weekly entry enforcement:

| Component | File Path & Lines | Before Remediation | After Remediation |
| :--- | :--- | :--- | :--- |
| **Risk Manager Gate** | [`src/risk/manager.py#L202-L215`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/risk/manager.py#L202-L215) | `if self.weekly_entries_count >= self.max_entries_per_week:` returned `False` with `"Weekly entry limit (3) reached..."` | **Removed completely.** `can_open_new_position()` evaluates only concurrent open positions, daily/weekly loss limits, and remaining loss budget. |
| **Risk Manager Signature** | [`src/risk/manager.py#L26-L34`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/risk/manager.py#L26-L34) | `max_entries_per_week: int = 3` | `max_entries_per_week: Optional[int] = None` (retained for backward compatibility and telemetry metadata). |
| **Settings Schema** | [`config/settings.py#L47`](file:///c:/Users/Administrator/Documents/SignalGenerator/config/settings.py#L47) | `max_entries_per_week: int = Field(default=3, ge=1, le=10)` | `max_entries_per_week: Optional[int] = Field(default=None, description="Deprecated weekly entry hard cap; entry frequency governed by risk controls")` |
| **Default Configuration** | [`config/default_config.yaml#L53`](file:///c:/Users/Administrator/Documents/SignalGenerator/config/default_config.yaml#L53) | `max_entries_per_week: 3` | `max_entries_per_week: null` |
| **Backtest Simulator** | [`src/backtest/engine.py#L33`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/backtest/engine.py#L33) | `max_entries_per_week: int = 3` | `max_entries_per_week: Optional[int] = None` |
| **Scheduler Runner Message** | [`src/scheduler/runner.py#L478`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/scheduler/runner.py#L478) | Fallback rejection mentioned `"(awaiting position closure or quota reset)"` | Updated to `"(awaiting position closure or daily/weekly loss budget reset)"` |
| **API System Status** | [`src/api/app.py#L333`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/api/app.py#L333) | `"max_weekly_entries": 3` | `"max_weekly_entries": null` (truthfully communicates to clients that no weekly quota cap exists). |
| **Dashboard Exposure Display** | [`src/api/dashboard.html#L99,L603`](file:///c:/Users/Administrator/Documents/SignalGenerator/src/api/dashboard.html#L99) | Showed `"0 / 3 Entries This Week"` in exposure caps card | Formatted as telemetry: `Entries This Week: 0` (via DOM and dynamic JS refresh). |

---

## 3. Risk-Guard Hierarchy and Interaction Analysis

The interaction between per-trade sizing, hard SL protection, and multi-tier calendar loss limits was audited for potential conflicts:

```
[Candidate Setup: SMC/SnR Validated & Fresh]
                      │
                      ▼
        [1. Concurrency Check] ──────────► If open_positions >= 1: BLOCKED
                      │
                      ▼
        [2. Calendar Loss Limits] ────────► If daily_loss >= $0.18: BLOCKED
                      │                   If weekly_loss >= $0.54: BLOCKED
                      │                   If remaining_budget <= $0.01: BLOCKED
                      ▼
     [3. Position Sizing & Hard Cap] ─────► Target margin: $6.00 (max $6.50)
                      │                   Risk budget: min($0.09, rem_daily, rem_weekly)
                      │                   Max loss at SL <= $1.50 (inclusive of fees & slippage)
                      │                   If min lot size exceeds $1.50: INFEASIBLE (BLOCKED)
                      ▼
             [4. Order Permitted]
```

### Key Safety Invariant Confirmations:
1. **Target Sizing vs Loss Caps:** Standard trades risk target `$0.09` ($6 margin at 10x leverage, ~1.5% SL distance). Two consecutive full losses on the same day ($0.18) immediately exhaust the daily loss limit and block all subsequent entries until `00:00 UTC`. Six consecutive losses across a single week ($0.54) exhaust the weekly loss limit and block all entries until Monday `00:00 UTC`.
2. **Hard SL Ceiling ($1.50):** The `$1.50` cap acts as an emergency per-trade catastrophic stop. In the event of a catastrophic gap or slippage event where a single trade realizes $\ge \$0.18$, the daily loss cap is breached instantly. If it realizes $\ge \$0.54$, the weekly loss cap is breached instantly, shutting down trading for the rest of the week.
3. **Journal Recovery & Idempotency:** Startup and restart procedures invoke `RiskManager.sync_state_from_journal(journal)`. Daily loss, weekly loss, and weekly entry counts are reconstructed directly from authoritative SQLite trade records using UTC timestamps. Restarting after a loss cannot reset or wipe loss accounting.

---

## 4. Empirical Test Suite Results

All 272 automated tests in the test suite pass cleanly (19 external exchange tests deselected):

```
============== 272 passed, 19 deselected, 16 warnings in 38.34s ===============
```

### Dedicated Policy Invariant Suite ([`tests/test_weekly_entry_policy.py`](file:///c:/Users/Administrator/Documents/SignalGenerator/tests/test_weekly_entry_policy.py)):

| Test Case | Invariant Validated | Result |
| :--- | :--- | :---: |
| `test_1_fourth_and_subsequent_entries_not_blocked_by_weekly_count` | 4th, 5th, 6th, and 7th profitable trades in the same week are permitted without quota blocking. | **PASSED** |
| `test_2_daily_loss_guard_breached_blocks_entry` | Setup is strictly blocked once cumulative daily loss reaches $0.18. | **PASSED** |
| `test_3_weekly_loss_guard_breached_blocks_entry` | Setup is strictly blocked once cumulative weekly loss reaches $0.54. | **PASSED** |
| `test_4_second_concurrent_position_blocked` | A second concurrent position is blocked while an active position is open. | **PASSED** |
| `test_5_stale_invalidated_infeasible_duplicate_blocked` | Infeasible sizing, candle age $>3.0$h, and duplicate submissions are rejected at execution gate. | **PASSED** |
| `test_6_watchlist_setups_cannot_bypass_confirmation` | `WATCHLIST` setups are never converted to pending orders in `PaperExecutionEngine`. | **PASSED** |
| `test_7_weekly_entry_telemetry_increments_correctly` | `weekly_entries_count` accurately increments to 10 without capping entry decisions. | **PASSED** |
| `test_8_weekly_counters_and_risk_state_recover_after_restart` | Process restart restores realized daily/weekly losses and entry count from journal idempotently. | **PASSED** |
| `test_9_live_trading_locked_prevents_live_orders` | Any attempt to call exchange `create_order` raises `ExchangeAPIError` while `live_trading_locked=True`. | **PASSED** |
| `test_10_baseline_strategy_outputs_unchanged` | SetupGenerator parameters (`0.12, 0.88, 0.92`) and logic remain 100% invariant. | **PASSED** |

### Updated Regression Tests:
- [`tests/test_tpsl_risk.py::test_weekly_entries_unlimited_by_count_when_risk_permits`](file:///c:/Users/Administrator/Documents/SignalGenerator/tests/test_tpsl_risk.py#L412): **PASSED** (Replaces legacy 3-entry blocker expectation).
- [`tests/test_api.py::test_get_status`](file:///c:/Users/Administrator/Documents/SignalGenerator/tests/test_api.py#L22): **PASSED** (Validates `max_weekly_entries is None`).
- [`tests/test_dashboard_scheduler.py::test_risk_limits_preserved`](file:///c:/Users/Administrator/Documents/SignalGenerator/tests/test_dashboard_scheduler.py#L363): **PASSED** (Validates `max_weekly_entries is None`).

---

## 5. Live Runtime & API Verification

The production PAPER server was restarted in a controlled manner (PID `27256` terminated, new daemon PID `12196` launched).

### Live `/api/status` Response:
```json
{
  "configured_trade_risk_usdt": 0.09,
  "effective_trade_risk_usdt": 0.09,
  "daily_loss_limit_usdt": 0.18,
  "daily_loss_used_usdt": 0.0,
  "weekly_loss_limit_usdt": 0.54,
  "weekly_loss_used_usdt": 0.0,
  "open_positions": 0,
  "max_open_positions": 1,
  "weekly_entries": 0,
  "max_weekly_entries": null,
  "can_open_position": true,
  "block_reason": null,
  "target_isolated_margin_usdt": 6.0,
  "target_isolated_margin_max_usdt": 6.5,
  "hard_risk_limit_usdt": 1.5
}
```

### Live Dashboard Verification:
- URL: `http://127.0.0.1:8000/`
- Exposure card HTML element: `<div class="text-xs text-slate-400 mt-1 font-mono" id="entriesDisplay">Entries This Week: 0</div>`
- Dynamic JS updater: `document.getElementById('entriesDisplay').innerText = `Entries This Week: ${data.risk_limits.weekly_entries}`;`
- Hard quota `0 / 3 Entries This Week` has been eliminated from both the initial markup and runtime rendering.

---

## 6. Forward Cohort Integrity & Profitability Posture

1. **Cohort Anchor:** Preserved at `2026-10-17 15:00:00 UTC`.
2. **Database Integrity:** Authoritative tables in `data/scanner.db` (`paper_orders = 0`, `trade_journal = 0`, `paper_lifecycle_events = 237`) were not modified, reset, or fabricated.
3. **Profitability Classification:** **`UNPROVEN_NO_COMPLETED_FORWARD_TRADES`**. Removing the weekly entry hard cap does not create edge by itself; edge must be validated by forward paper trade outcomes in live market conditions.

---

## 7. Git Diff Summary

```diff
diff --git a/config/default_config.yaml b/config/default_config.yaml
--- a/config/default_config.yaml
+++ b/config/default_config.yaml
@@ -53,3 +53,3 @@ risk:
   max_open_positions: 1
-  max_entries_per_week: 3
+  max_entries_per_week: null
   min_notional_usdt: 5.0

diff --git a/config/settings.py b/config/settings.py
--- a/config/settings.py
+++ b/config/settings.py
@@ -47,3 +47,3 @@ class RiskConfig(BaseModel):
   max_open_positions: int = Field(default=1, ge=1, le=1)
-  max_entries_per_week: int = Field(default=3, ge=1, le=10)
+  max_entries_per_week: Optional[int] = Field(default=None, description="Deprecated weekly entry hard cap; entry frequency governed by risk controls")
   min_notional_usdt: float = Field(default=5.0, gt=0)

diff --git a/src/api/dashboard.html b/src/api/dashboard.html
--- a/src/api/dashboard.html
+++ b/src/api/dashboard.html
@@ -99,3 +99,3 @@
                 <div class="text-xl font-bold text-white mt-1" id="capsDisplay">0 / 1 Open Pos</div>
-                <div class="text-xs text-slate-400 mt-1 font-mono" id="entriesDisplay">0 / 3 Entries This Week</div>
+                <div class="text-xs text-slate-400 mt-1 font-mono" id="entriesDisplay">Entries This Week: 0</div>
@@ -603,3 +603,3 @@
                 document.getElementById('capsDisplay').innerText = `${data.risk_limits.open_positions} / ${data.risk_limits.max_open_positions} Open Pos`;
-                document.getElementById('entriesDisplay').innerText = `${data.risk_limits.weekly_entries} / ${data.risk_limits.max_weekly_entries} Entries This Week`;
+                document.getElementById('entriesDisplay').innerText = `Entries This Week: ${data.risk_limits.weekly_entries}`;

diff --git a/src/backtest/engine.py b/src/backtest/engine.py
--- a/src/backtest/engine.py
+++ b/src/backtest/engine.py
@@ -33,3 +33,3 @@ class BacktestEngine:
   max_open_positions: int = 1,
-  max_entries_per_week: int = 3,
+  max_entries_per_week: Optional[int] = None,

diff --git a/src/risk/manager.py b/src/risk/manager.py
--- a/src/risk/manager.py
+++ b/src/risk/manager.py
@@ -26,3 +26,3 @@ class RiskManager:
   max_open_positions: int = 1,
-  max_entries_per_week: int = 3,
+  max_entries_per_week: Optional[int] = None,
@@ -205,4 +202,0 @@ class RiskManager:
-        if self.weekly_entries_count >= self.max_entries_per_week:
-            return False, f"Weekly entry limit ({self.max_entries_per_week}) reached for week {self.current_week_str}."

diff --git a/src/scheduler/runner.py b/src/scheduler/runner.py
--- a/src/scheduler/runner.py
+++ b/src/scheduler/runner.py
@@ -478,3 +478,3 @@ class ScannerRunner:
-             reason or "Account limits reached (awaiting position closure or quota reset)."
+             reason or "Account limits reached (awaiting position closure or daily/weekly loss budget reset)."
```
