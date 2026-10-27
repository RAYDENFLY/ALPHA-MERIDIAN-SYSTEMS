# ALPHA MERIDIAN SYSTEMS — Phase 5: Live Readiness Review

**Document:** `desktop/reports/PHASE_5_LIVE_READINESS_REVIEW.md`  
**Product:** Alpha Meridian Systems  
**Parent Brand:** AMS Capital  
**Corporate Entity:** PT Authentic Media Services  
**Effective Date:** 2026-10-28  
**Target Branch:** `feature/avalonia-desktop`  
**Auditor / Reviewer:** Antigravity Autonomous Coding Agent  

---

## 1. Overall Live Readiness Classification

### **LIVE READINESS STATUS: NO-GO**

> [!CAUTION]
> **LIVE ORDER SUBMISSION IS STRICTLY DISABLED IN THIS RELEASE.**  
> Paper simulation testing has been completed successfully across all 18 scenarios, but LIVE manual exchange order dispatch remains explicitly prohibited and locked out by policy in the desktop terminal. Live activation requires a separate, formal operator authorization review and must never be inferred from automated test passes or build success.

---

## 2. Assessment of Core Readiness Domains

| Readiness Domain | Current Status | Risk Level | Assessment & Findings |
| :--- | :---: | :---: | :--- |
| **Exchange Market Coverage** | `PARTIAL` | **HIGH** | Perpetual Futures architecture is fully established (`https://fapi.asterdex.com`), but AsterDEX Spot is **UNSUPPORTED** in existing Python adapters. Attempting Spot execution on AsterDEX is strictly blocked. |
| **Backend REST Capabilities** | `GAPPED` | **CRITICAL** | The running FastAPI daemon (PID 14864) does **not** expose manual order routing endpoints (`/api/orders`). Creating live orders from the desktop would require either direct private-key signing in the client (a severe security violation) or unreviewed live backend mutations. |
| **Secret & Key Isolation** | `SECURE` | **LOW** | The desktop client contains **zero** private keys, API secrets, or signing credentials. All cryptographic signing remains strictly isolated inside `src/exchange/signer_v3.py` on the backend server. |
| **Concurrency with Bot / Groq Hunter** | `UNRESOLVED` | **HIGH** | The live Python daemon runs an active automated session (`SESS_LIVE_AUTO_1793099684427`) with `15M_SWEEP_RECLAIM_SMC` strategy and automated reconciliation worker (345+ cycles). Manual orders placed concurrently on the same account could interfere with the bot's position sizing, dynamic SL/TP brackets, and daily loss limits ($0.45 USDT limit). |
| **Exchange Order Reconciliation** | `UNVERIFIED` | **HIGH** | While the backend reconciliation worker reconciles automated bot trades into SQLite, a bi-directional reconciliation pipeline for manual desktop exchange fills is not yet verified in production. |
| **Protective Order Invariants** | `UNRESOLVED` | **HIGH** | Manual position modification or closure could inadvertently orphan or desynchronize automated dynamic SL/TP brackets managed by the protection worker. |
| **Idempotency & Timeout Handling** | `VERIFIED (PAPER)` | **MEDIUM** | Verified within the desktop simulation layer using `ClientOrderId` uniqueness checks and `IsActionPending` guards. Live exchange idempotency semantics under HTTP timeouts remain unproven for manual orders. |
| **Precision & Instrument Rules** | `VERIFIED` | **LOW** | Validated against exchange specifications: tick sizes ($0.10 BTC), step sizes (0.001 BTC), and minimum notional ($5.00 USDT). |

---

## 3. Explicit Blocker Inventory

Before any future live manual trading activation can be considered, the following blockers must be formally resolved in a dedicated subsequent phase:

1. **Backend Manual Order Gateway:** A secure, authenticated REST endpoint (e.g., `POST /api/manual/orders`) must be implemented on the backend, authenticated via operator tokens, and strictly wired to `AsterDexV3Adapter`.
2. **Account Capital Partitioning:** A formal sub-account or segregated margin buffer must be established to prevent manual trades from colliding with Groq Auto-Hunter's micro-sizing limits ($0.35-$0.45 USDT daily risk).
3. **Protective Bracket Synchronization:** The protection reconciliation worker must be enhanced to recognize manual vs. automated position ownership.
4. **Spot Adapter Integration:** If AsterDEX Spot trading is required by operators, a dedicated Aster Spot adapter must be authored, tested, and audited in `src/exchange/`.
5. **Operator Multi-Factor Authorization:** Live manual execution requires a secondary hardware or OTP confirmation workflow distinct from routine UI confirmations.

---

## 4. Rollback and Containment Procedure

If unexpected behavior or anomalous state is observed:
1. **Desktop Client:** The operator may restart or close the desktop application safely at any time. The desktop client maintains no persistent background threads that touch the exchange.
2. **Backend Daemon:** The running live daemon (PID 14864) remains completely untouched by Phase 5 and continues executing its normal automated scanning and risk reconciliation cycles.
3. **Emergency Stop:** If live exchange protection is ever required, the operator can invoke the existing backend kill switch via `POST /api/inspector/live-session/kill-switch` through authorized channels.
