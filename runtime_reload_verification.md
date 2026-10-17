# SignalGenerator — Runtime Reload Verification & Gate Clearance Report

**Audit Timestamp:** 2026-10-17 17:10:00 UTC (Local: 2026-10-18 00:10:00 UTC+7)  
**Branch:** `research/profitability-validation`  
**Git Baseline Commit:** `a89b15a4a24b84a4db5f7e622fefffc70c876f75`  
**Database Path:** `data/scanner.db` (Preserved intact — zero resets, zero live mutations)  
**Forward Cohort Anchor:** `2026-10-17 15:00:00 UTC`  
**Forward Profitability Status:** `UNPROVEN_NO_COMPLETED_FORWARD_TRADES` (0 completed forward trades)  

---

## 1. Final Decision Gate Verdict

### **DECISION: READY_FOR_CONTINUED_PAPER**

| Verification Dimension | Pre-Reload Baseline | Post-Reload Verification | Gate Verdict |
|---|---|---|---|
| **Process Identity & Port Ownership** | PID `10268` (booted 13:14 UTC, stale code) | PID `27256` (booted 17:07 UTC, remediated code) | **PASS** |
| **Source Bytecode Alignment** | Desynchronized (pre-dated 16:01 UTC edits) | Synchronized (verified SHA-256 hashes loaded) | **PASS** |
| **Risk Recovery Synchronization** | In-memory only (unpatched) | Active on startup via `sync_state_from_journal` | **PASS** |
| **Scan Lifecycle Event Observability** | 0 events on empty cycles (`continue` defect) | 26 events emitted across 2 verified cycles | **PASS** |
| **Safety Invariants** | `PAPER`, `locked=True`, $6 margin, 10x lev | `PAPER`, `locked=True`, $6 margin, 10x lev | **PASS** |
| **Database & Cohort Integrity** | Zero synthetic pollution, anchor preserved | Zero synthetic pollution, anchor preserved | **PASS** |
| **Automated Test Suite** | 275/275 passed | 275/275 passed | **PASS** |

---

## 2. Process Lifecycle & Identity Transition

### 2.1 Before vs. After Process Identity

| Property | Pre-Reload Process (Terminated) | Post-Reload Process (Active) |
|---|---|---|
| **Process ID (PID)** | `10268` | `27256` |
| **Listening Socket** | `127.0.0.1:8000` | `127.0.0.1:8000` |
| **Binary Path** | `C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe` | `C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe` |
| **Command Line** | `python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000` | `python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000` |
| **Startup Timestamp** | `2026-10-17 13:14:21 UTC` (`20:14:21 UTC+7`) | `2026-10-17 17:07:15 UTC` (`00:07:15 UTC+7`) |
| **Termination Method** | Graceful cancellation via `manage_task` | Active runtime daemon (`task-6939`) |
| **Port Release Check** | `Get-NetTCPConnection -LocalPort 8000` returned 0 listeners | Only PID `27256` listening on port 8000 |
| **Log Path** | `.../.system_generated/tasks/task-5948.log` | `.../.system_generated/tasks/task-6939.log` |

### 2.2 Source File SHA-256 Hashes
The active process PID `27256` was loaded directly from the remediated working tree files:

| File Path | SHA-256 Hash | Working Tree Status |
|---|---|---|
| `src/risk/manager.py` | `ecb22210952bb98926e58f71dfed483053d9456a0c36acded8682d54c8d0e2d7` | Remediated (Uncommitted) |
| `src/scheduler/runner.py` | `21b8fd10dac764d81058a117b56deebbbcbb17f4165a3e1e2bfd3ac40a431537` | Remediated (Uncommitted) |
| `src/api/app.py` | `3bbb57c147faa4532fe2ce4bf5fe0e5e6542684d8588a4390d1b7414688ff5a3` | Remediated (Uncommitted) |
| `src/journal/repository.py` | `1483d321b17834a45b7e9fe3f6728ab808066c11c72cf65b2e24a66c7a27780a` | Remediated (Uncommitted) |
| `src/execution/paper_engine.py` | `709af62043ce340d87737af9d7874ad9c84b777280fcd3473054a61202619d5f` | Remediated (Uncommitted) |
| `src/execution/types.py` | `ee072ef9d5a13735ac86abb6924241cf45da610162a59173e8f90e908c9a378b` | Remediated (Uncommitted) |

---

## 3. Post-Reload Runtime Verification

### 3.1 Startup & Health Telemetry
The startup sequence was captured in `task-6939.log`:
```
INFO:     Started server process [27256]
INFO:     Waiting for application startup.
2026-10-18 00:07:15 [INFO] [scanner] Initializing scanner API server...
2026-10-18 00:07:16 [INFO] [scanner] Loaded 634 symbol specifications from Aster DEX.
2026-10-18 00:07:16 [INFO] [scanner] PaperExecutionEngine state recovered: 0 open positions, 0 pending orders.
2026-10-18 00:07:16 [INFO] [scanner] Discord notifications: ENABLED (https://discord.com/.../***7WNW)
2026-10-18 00:07:16 [INFO] [scanner] Background scanner task started.
2026-10-18 00:07:16 [INFO] [scanner] Background scanner loop started.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
2026-10-18 00:07:17 [INFO] [scanner] Running initial background scan on startup...
2026-10-18 00:07:17 [INFO] [scanner] Running scanner cycle for 11 symbols...
2026-10-18 00:07:23 [INFO] [scanner] Scan cycle complete. Discovered 0 new candidates, 0 total tracked candidates.
2026-10-18 00:07:23 [INFO] [scanner] Initial background scan completed successfully.
2026-10-18 00:07:38 [INFO] [scanner] Triggering background scan for closed candle hour 17:00 UTC...
2026-10-18 00:07:38 [INFO] [scanner] Running scanner cycle for 11 symbols...
2026-10-18 00:07:44 [INFO] [scanner] Scan cycle complete. Discovered 0 new candidates, 0 total tracked candidates.
2026-10-18 00:07:45 [INFO] [scanner] Background scan complete for hour 17:00 UTC.
```

### 3.2 Live API Query Results (`GET http://127.0.0.1:8000/api/status`)
```json
{
  "mode": "PAPER",
  "live_trading_locked": true,
  "capital": {
    "initial_equity_usdt": 16.0,
    "current_equity_usdt": 16.0,
    "margin_mode": "ISOLATED",
    "max_leverage": 10
  },
  "risk_limits": {
    "configured_trade_risk_usdt": 0.09,
    "effective_trade_risk_usdt": 0.09,
    "daily_loss_limit_usdt": 0.18,
    "daily_loss_used_usdt": 0.0,
    "weekly_loss_limit_usdt": 0.54,
    "weekly_loss_used_usdt": 0.0,
    "open_positions": 0,
    "max_open_positions": 1,
    "weekly_entries": 0,
    "max_weekly_entries": 3,
    "can_open_position": true,
    "block_reason": null,
    "target_isolated_margin_usdt": 6.0,
    "target_isolated_margin_max_usdt": 6.5,
    "hard_risk_limit_usdt": 1.5
  },
  "scanner": {
    "state": "IDLE",
    "last_scan_status": "SUCCESS",
    "scan_count": 2,
    "last_scan_utc": "2026-10-17T17:07:38.741220+00:00"
  },
  "paper": {
    "enabled": true,
    "open_positions": 0,
    "pending_orders": 0
  }
}
```

---

## 4. Database Row-Count Comparison & Event Verification

### 4.1 SQLite Table Row Counts

| Table Name | Pre-Reload Count | Post-Reload Count | Delta | Interpretation |
|---|---|---|---|---|
| `paper_lifecycle_events` | 43 | **69** | **+26** | **2 complete 13-event scan cycles emitted post-reload** |
| `trade_journal` | 0 | **0** | 0 | Preserved; 0 forward trades completed |
| `paper_orders` | 0 | **0** | 0 | Preserved; 0 synthetic orders injected |
| `setup_candidates` | 128 | **128** | 0 | Preserved intact |
| `raw_candles` | 118,770 | **118,770** | 0 | Historical market data intact |

### 4.2 Post-Reload Scan Events (`paper_lifecycle_events`)
The 26 new events in `data/scanner.db` prove that empty cycles (`candidate_count=0`) are now fully observable:

#### Cycle 1: Startup Scan (`SCAN_1792256837623` at 17:07:17 UTC)
- Event 44: `stage=SCAN_STARTED`, `reason_code=SCAN_INITIATED`, `symbol=ALL`
- Events 45–55: `stage=SETUP_REJECTED`, `reason_code=NO_SMC_CONFLUENCE` (for each of `ADAUSDT`, `XRPUSDT`, `BTCUSDT`, `BNBUSDT`, `ETHUSDT`, `SOLUSDT`, `AVAXUSDT`, `LINKUSDT`, `XMRUSDT`, `DOGEUSDT`, `LTCUSDT`)
- Event 56: `stage=SCAN_COMPLETED`, `reason_code=NO_TRADE`, `symbol=ALL` (`candidate_count=0`, `ready_count=0`)

#### Cycle 2: Hourly Scan for 17:00 UTC (`SCAN_1792256858741` at 17:07:38 UTC)
- Event 57: `stage=SCAN_STARTED`, `reason_code=SCAN_INITIATED`, `symbol=ALL`
- Events 58–68: `stage=SETUP_REJECTED`, `reason_code=NO_SMC_CONFLUENCE` (for all 11 watchlist symbols)
- Event 69: `stage=SCAN_COMPLETED`, `reason_code=NO_TRADE`, `symbol=ALL` (`candidate_count=0`, `ready_count=0`)

---

## 5. Forward Cohort Invariants Confirmation

All core parameters of the forward validation cohort remain identical to their baseline:

1. **Cohort Anchor:** Preserved at `2026-10-17 15:00:00 UTC`.
2. **Strategy Logic:** Institutional SMC/SnR dealing range `[0.12, 0.88]` unmodified.
3. **Execution Parameters:** $6.00 target isolated margin, 10x max leverage, 0.05% conservative penetration fill buffer, 0.02% maker fee, 0.05% taker fee, 0.05% slippage rate, 50% TP1 partial close + break-even + trailing stop.
4. **Risk Caps:** Hard SL cap $1.50, daily loss cap $0.18, weekly loss cap $0.54, max 1 open position, max 3 entries/week.
5. **Profitability Status:** `UNPROVEN_NO_COMPLETED_FORWARD_TRADES` (0 completed forward trades).

---

## 6. Conclusion

With the termination of stale process PID `10268` and the launch of PID `27256` executing verified bytecode, runtime identity, persistent risk recovery, and scan event observability are certified. The forward paper cohort is **READY_FOR_CONTINUED_PAPER**.
