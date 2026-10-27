# ALPHA MERIDIAN SYSTEMS — Phase 5: API Contract & Exchange Infrastructure Audit

**Document:** `desktop/reports/PHASE_5_API_CONTRACT_AUDIT.md`  
**Product:** Alpha Meridian Systems  
**Parent Brand:** AMS Capital  
**Corporate Entity:** PT Authentic Media Services  
**Audit Timestamp:** 2026-10-28T03:26:00+07:00  
**Target Branch:** `feature/avalonia-desktop`  
**Auditor:** Antigravity Autonomous Coding Agent  

---

## 1. Safety Baseline and Discovery

In accordance with Section 2.1 and 2.2 of the Phase 5 Mission, this baseline audit is non-mutating and captures the verified runtime state of the active system without altering processes, ports, or exchange states.

### 1.1 Git Working Tree Baseline
- **Current Git Branch:** `feature/avalonia-desktop`
- **HEAD Commit:** `8ede3a3` (*feat(desktop): implement Phase 4D AI Auto-Hunter controls & native news workspace*)
- **Working Tree Cleanliness:** Clean. No unstaged or untracked changes in repository.
- **Git Invariant Status:** Confirmed. Operating strictly on `feature/avalonia-desktop`. `main` remains untouched.

### 1.2 Running Process & Host Discovery
- **Process ID (PID):** `14864`
- **Process Creation Timestamp:** 2026-10-27T16:47:31Z
- **Process Command Line:**
  `"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe" "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\Scripts\uvicorn.exe" src.api.app:app --host 127.0.0.1 --port 8000`
- **Listening Host / Port:** `127.0.0.1:8000`
- **Operational Constraint:** PID `14864` is running the live trading daemon. It MUST NOT be killed, restarted, or reloaded during Phase 5.

### 1.3 Verified Read-Only Telemetry Baseline
Queried via `http://127.0.0.1:8000/api/inspector/live-session` and `http://127.0.0.1:8000/api/account`:
- **Current Session Mode:** `LIVE_AUTO`
- **Current Session State:** `ACTIVE` (`is_active: true`, `is_paused: false`)
- **Configured Strategy ID:** `15M_SWEEP_RECLAIM_SMC` (Status: `OK`, Dynamic SL/TP active)
- **Global Risk Lock Status:** Held = `false`, State = `IDLE`, Symbol = `null`
- **Kill Switch Status:** `false` (Not triggered)
- **Active Open Positions:** `0` (`open_positions_count: 0`, `position_amt: 0.0`)
- **Active Open Orders:** `0`
- **Active Protective Orders:** `0` (`active_protective_order_count: 0`)
- **Reconciliation Worker:** Status = `RUNNING`, Cycle Count = `345+`, Last Result = `SUCCESS`
- **Account Total Margin Equity:** `$14.3438 USDT` (Source: `ASTERDEX_ACCOUNT_SNAPSHOT`)
- **Account Available Balance:** `$12.2915 USDT`
- **Daily Loss Guard:** Used: `$0.00 USDT` / Limit: `$0.45 USDT` (Status: `VERIFIED`)

---

## 2. AsterDEX Capabilities & Adapter Audit

### 2.1 Perpetual Futures (`PERPETUAL`) — Confirmed Architecture
The codebase contains deep, verified integration with AsterDEX Perpetual Futures across three layers:
1. **Public Market Data (`src/exchange/aster_adapter.py` & `src/exchange/aster.py`):**
   - Base URL: `https://fapi.asterdex.com`
   - Endpoints: Level-2 Order Book (`/fapi/v1/depth`), Aggregated Trades (`/fapi/v1/aggTrades`), Recent Trades (`/fapi/v1/trades`), Klines (`/fapi/v1/klines`), Funding Rates (`/fapi/v1/fundingRate`), Premium Index / Mark Price (`/fapi/v1/premiumIndex`), Open Interest (`/fapi/v1/openInterest`).
2. **Authenticated Pro API V3 (`src/exchange/aster_v3.py` & `src/exchange/signer_v3.py`):**
   - Base URL: `https://fapi.asterdex.com` (Mainnet) / `https://fapi.asterdex-testnet.com` (Testnet)
   - Authentication: Cryptographic EIP-712 structured data signing using `AsterV3Signer` with Ethereum wallet private key and account address.
   - Verified Adapter Methods:
     - `create_order(...)`: Submits LIMIT, MARKET, STOP_MARKET, TAKE_PROFIT orders. Guarded by `live_trading_locked`.
     - `cancel_order(...)`: Cancels specific open order by `order_id` or `client_order_id`.
     - `get_open_orders(...)`: Queries active orders.
     - `get_positions(...)`: Queries open perpetual positions and liquidation prices.
     - `get_account_balance(...)` & `get_account_info(...)`: Queries margin and wallet balance.
     - `get_user_trades(...)`: Queries trade fills and execution history.
     - `set_leverage(...)` & `set_margin_type(...)`: Configures contract leverage and isolated/cross margin.
3. **Read-Only Account Service (`src/exchange/account_service.py`):**
   - Thread-safe cached polling with configurable TTL.
   - Enforces strict data integrity flags: `CONNECTED`, `ACCOUNT_DATA_UNAVAILABLE`, `STALE_ACCOUNT_DATA`, `UNAUTHENTICATED`.

### 2.2 Spot Markets (`SPOT`) — Verified Gaps & Limitations
- **Findings:** A complete audit of `src/exchange/` confirms that no AsterDEX Spot adapter or Spot API endpoints exist in this repository. All `aster*.py` modules are designed exclusively for `https://fapi.asterdex.com` (Perpetual Futures).
- **Classification:** **UNSUPPORTED on AsterDEX in Current Codebase**.
- **UX Treatment:** The Terminal UI will allow selecting the `SPOT` market type, but when paired with AsterDEX, the UI will explicitly display:  
  `UNSUPPORTED: AsterDEX Spot is not supported by the exchange adapter. Perpetual Futures only.`

---

## 3. API Endpoint Inventory

| Endpoint | Method | Applicability | Side Effects | Backend Handler | Desktop Status | Verification Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/api/status` | `GET` | System | None (Read-only) | `get_status` | Used (`ApiClient.GetStatusAsync`) | **READ_ONLY_VERIFIED** |
| `/api/inspector/live-session` | `GET` | System / Perp | None (Read-only) | `get_live_session_status` | Used (`ApiClient.GetLiveSessionAsync`) | **READ_ONLY_VERIFIED** |
| `/api/account` | `GET` | Perp | None (Read-only) | `get_account_status` | Available via `/api/status` | **READ_ONLY_VERIFIED** |
| `/api/account/positions` | `GET` | Perp | None (Read-only) | `get_account_positions` | Available via session | **READ_ONLY_VERIFIED** |
| `/api/chart/symbols` | `GET` | Perp / Spot | None (Read-only) | `chart_symbols_endpoint` | Used (`ApiClient.GetChartSymbolsAsync`) | **READ_ONLY_VERIFIED** |
| `/api/chart/klines` | `GET` | Perp | None (Read-only) | `chart_klines_endpoint` | Used (`ApiClient.GetKlinesAsync`) | **READ_ONLY_VERIFIED** |
| `/api/chart/cross_book` | `GET` | Perp | None (Read-only) | `chart_cross_book_endpoint` | Verified via curl (AsterDEX, Binance, Bybit) | **READ_ONLY_VERIFIED** |
| `/api/model_b/orderbook/{symbol}` | `GET` | Perp | None (Read-only) | `get_model_b_orderbook` | Verified via curl (Top 5 depth per venue) | **READ_ONLY_VERIFIED** |
| `/api/screener/markets` | `GET` | Perp | None (Read-only) | `get_screener_markets` | Used (`ApiClient.GetScreenerMarketsAsync`) | **READ_ONLY_VERIFIED** |
| `/api/trades` | `GET` | Perp | None (Read-only) | `get_trades` | Used (`ApiClient.GetTradesAsync`) | **READ_ONLY_VERIFIED** |
| `/api/v1/journal/trades` | `GET` | Perp | None (Read-only) | `get_automated_trades` | Used (`ApiClient.GetJournalTradesAsync`) | **READ_ONLY_VERIFIED** |
| `/api/v1/journal/summary` | `GET` | Perp | None (Read-only) | `get_journal_summary` | Used (`ApiClient.GetJournalSummaryAsync`) | **READ_ONLY_VERIFIED** |
| `/api/autotrader/screening-log` | `GET` | Perp | None (Read-only) | `get_autotrader_screening_log` | Used (`ApiClient.GetScreeningLogAsync`) | **READ_ONLY_VERIFIED** |
| `/api/alerts/*` | `CRUD` | Alerts DB | Local DB Mutate | `alert_routes` | Used (`ApiClient.Alerts`) | **READ_ONLY_VERIFIED** |
| `/api/scanner/start` | `POST` | Scanner | Local Worker Mutate | `start_scanner` | Used (`ApiClient.StartScannerAsync`) | **MUTATING_PAPER_ONLY** |
| `/api/scanner/stop` | `POST` | Scanner | Local Worker Mutate | `stop_scanner` | Used (`ApiClient.StopScannerAsync`) | **MUTATING_PAPER_ONLY** |
| `/api/inspector/live-session/reauthorize`| `POST` | Live Session | Session Flag Mutate | `reauthorize_live_session` | Used (`ApiClient.ReauthorizeSessionAsync`)| **MUTATING_LIVE_REQUIRES_AUTHORIZATION** |
| `/api/inspector/groq-auto-setup` | `POST` | Paper / Screening | Single Cycle Run | `run_groq_auto_setup` | Used (`ApiClient.TriggerSingleScanAsync`) | **MUTATING_PAPER_ONLY** |
| `/api/orders` | `POST` | Perp | **EXCHANGE MUTATION** | **DOES NOT EXIST ON FASTAPI** | Not Present | **UNSUPPORTED / BLOCKED** |
| `/api/orders/{id}` | `DELETE` | Perp | **EXCHANGE MUTATION** | **DOES NOT EXIST ON FASTAPI** | Not Present | **UNSUPPORTED / BLOCKED** |

---

## 4. Architectural Analysis & Safe Implementation Path

### 4.1 Identified Backend Gap
The running FastAPI daemon exposes rich read-only market data, order books, account snapshots, and journal history, but does **NOT** expose manual REST endpoints for creating or cancelling arbitrary exchange orders. The backend's order execution capabilities are encapsulated in `src/exchange/aster_v3.py` and strictly controlled by the automated scheduler and `live_trading_locked` guard.

### 4.2 Non-Negotiable Safety Path
1. **Live Daemon Isolation:** Under no circumstances will the running Uvicorn daemon (PID 14864) be stopped or modified.
2. **Desktop-Native Paper Simulator:** To deliver the full Phase 5 Trading Workstation and Multi-Market Order Book without risking unintended exchange mutations or violating the read-only security boundary, the desktop application will implement a deterministic `IPaperTradingService` / `PaperTradingService`.
3. **Paper Architecture:**
   - Isolated in-memory and persistent SQLite simulated ledger (`paper_trading.db`).
   - Order validation: tick size, step size, minimum notional ($5.00), available simulated funds.
   - Execution model: simulated market orders fill at live reference price (best bid/ask); limit orders fill when market crosses limit price or immediately if marketable.
   - Positions, unrealized PnL, realized PnL, margin, and order status are tracked with microsecond timestamps and decimal-safe arithmetic.
   - All simulated orders and positions are prominently watermarked: `[SIMULATED - PAPER TRADING]`.
4. **Live Trading Isolation:** LIVE order dispatch from the desktop remains strictly disabled. The live readiness status will be recorded as `NO-GO` pending a separate, explicit operator authorization phase.
