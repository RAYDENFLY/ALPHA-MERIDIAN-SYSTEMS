# ALPHA MERIDIAN SYSTEMS — Phase 5: Trading Safety Matrix

**Document:** `desktop/reports/PHASE_5_TRADING_SAFETY_MATRIX.md`  
**Product:** Alpha Meridian Systems  
**Parent Brand:** AMS Capital  
**Corporate Entity:** PT Authentic Media Services  
**Effective Date:** 2026-10-28  
**Target Branch:** `feature/avalonia-desktop`  

---

## 1. Classification Categories

Every endpoint, command, and interaction within the Alpha Meridian Systems terminal is strictly categorized into one of five safety classifications:

1. `READ_ONLY_VERIFIED`: Safe read-only data query against verified local or backend endpoints. Does not mutate system, database, or exchange state.
2. `MUTATING_PAPER_ONLY`: Allowed only within the isolated deterministic Paper Simulation engine. Mutates local simulated state only; never touches real exchange accounts.
3. `MUTATING_LIVE_REQUIRES_AUTHORIZATION`: Live exchange-mutating action. **STRICTLY PROHIBITED AND DISABLED IN THIS PHASE**. Requires explicit operator authorization flow, external risk clearance, and formal release approval.
4. `UNSUPPORTED`: Operations or combinations not supported by exchange adapters or current architecture (e.g., AsterDEX Spot trading). Must display an explicit warning and fail-safe block.
5. `UNKNOWN_DO_NOT_CALL`: Unverified, speculative, or undocumented endpoints or methods. Never to be invoked under any condition.

---

## 2. Operation Safety Matrix

| Operation / Capability | Target Subsystem / Route | Classification | Safety Constraints & Verification Enforcements |
| :--- | :--- | :--- | :--- |
| **Account Equity & Balance Inspection** | `GET /api/account` | `READ_ONLY_VERIFIED` | Read-only. Verifies connection to AsterDEX FAPI V3. Flags stale or missing data cleanly. |
| **Open Positions Inspection** | `GET /api/account/positions` | `READ_ONLY_VERIFIED` | Read-only. Displays verified open positions, liquidation prices, and mark prices. |
| **Cross-Venue Order Book Depth** | `GET /api/chart/cross_book` | `READ_ONLY_VERIFIED` | Read-only. Aggregates AsterDEX, Binance, and Bybit order books. Zero exchange mutation. |
| **Multi-Exchange Level-2 Order Book** | `GET /api/model_b/orderbook/{symbol}` | `READ_ONLY_VERIFIED` | Read-only. Provides top-5 / top-50 bid/ask levels for multi-market ladders. |
| **Candlestick Klines** | `GET /api/chart/klines` | `READ_ONLY_VERIFIED` | Read-only. Streams historical candles for charting. |
| **Market Screener & Metadata** | `GET /api/screener/markets` | `READ_ONLY_VERIFIED` | Read-only. Delivers 24h volume, price change, and verified contract metadata. |
| **Historical Fills & Journal** | `GET /api/v1/journal/trades` | `READ_ONLY_VERIFIED` | Read-only. Exchange-reconciled trade history. |
| **Live Session Telemetry** | `GET /api/inspector/live-session` | `READ_ONLY_VERIFIED` | Read-only. Live bot status, risk-lock state, reconciliation status. |
| **Price Alert CRUD** | `/api/alerts/*` | `READ_ONLY_VERIFIED` / Local Mutate | Mutates local alerts SQLite database only. Strictly authorized in desktop client. |
| **AI Scanner Start / Stop** | `POST /api/scanner/*` | `MUTATING_PAPER_ONLY` | Starts/stops local background scanning worker. Does not dispatch orders. |
| **Advisory Single Groq Cycle** | `POST /api/inspector/groq-auto-setup` | `MUTATING_PAPER_ONLY` | Runs single 10->3->1 cycle in PAPER mode (`auto_fire_limit_order: false`). |
| **Paper Order Submission (Limit/Market)** | `IPaperTradingService` (Local) | `MUTATING_PAPER_ONLY` | Simulated order entry. Validates tick size, step size, min notional. Mutates paper ledger only. |
| **Paper Order Cancellation** | `IPaperTradingService` (Local) | `MUTATING_PAPER_ONLY` | Cancels simulated order. Reconciles simulated available balance. |
| **Paper Order Amend / Replace** | `IPaperTradingService` (Local) | `MUTATING_PAPER_ONLY` | Cancel-and-replace simulation. Atomic or non-atomic transition tracking. |
| **Paper Simulation Reset** | `IPaperTradingService` (Local) | `MUTATING_PAPER_ONLY` | Resets simulated balance and clears paper history upon operator request. |
| **Live Order Entry (AsterDEX)** | `/fapi/v3/order` / Python adapter | `MUTATING_LIVE_REQUIRES_AUTHORIZATION` | **DISABLED IN THIS RELEASE (NO-GO)**. Desktop client enforces read-only boundary. |
| **Live Order Cancellation (AsterDEX)** | `/fapi/v3/order` [DELETE] | `MUTATING_LIVE_REQUIRES_AUTHORIZATION` | **DISABLED IN THIS RELEASE (NO-GO)**. Desktop client enforces read-only boundary. |
| **Live Position Closure / Reduce** | `/fapi/v3/order` (Reduce-Only) | `MUTATING_LIVE_REQUIRES_AUTHORIZATION` | **DISABLED IN THIS RELEASE (NO-GO)**. Desktop client enforces read-only boundary. |
| **Live Session Reauthorization** | `POST /api/inspector/live-session/reauthorize` | `MUTATING_LIVE_REQUIRES_AUTHORIZATION` | Authorized operator lifecycle control only. Requires manual confirmation. |
| **AsterDEX Spot Market Trading** | Venue: `AsterDEX`, Market: `SPOT` | `UNSUPPORTED` | Exchange adapter in this repository does NOT support AsterDEX Spot. UI displays explicit warning. |
| **Native Exchange Atomic Amend** | AsterDEX FAPI V3 | `UNSUPPORTED` | AsterDEX API lacks atomic order amend; requires cancel-and-replace workflow. |
| **Mainnet Bulk Cancel All Orders** | `cancel_all_open_orders` | `UNSUPPORTED` | Restricted to Aster Testnet only in `src/exchange/aster_v3.py`. Blocked on Mainnet. |
| **Trailing Stop Orders** | Order Entry | `UNSUPPORTED` | Not supported by AsterDEX FAPI V3 adapter. |
| **Undeclared Manual Order Routes** | `/api/orders` | `UNKNOWN_DO_NOT_CALL` | Endpoint does not exist in FastAPI runtime. Calling will result in HTTP 404 / error. |
| **Direct Desktop Key Signing** | Private Key Injection | `UNKNOWN_DO_NOT_CALL` | Desktop client must NEVER hold or sign with live wallet private keys. |

---

## 3. Strict Security Invariants for Desktop Terminal

1. **Read-Only API Boundary:** The desktop `ApiClient.ValidateAuthorizedMutationRoute` continues to enforce strict security boundaries, rejecting any attempts to invoke live trading or exchange-mutating endpoints.
2. **Watermarking & Visual Differentiation:**
   - Any order, position, or execution created via the paper simulator is prominently labeled `[SIMULATED - PAPER TRADING]`.
   - UI switches between `PAPER` (Cyan `#448AFF`) and `LIVE` (Amber `#FF9D00`), with LIVE order submission completely disabled.
3. **Idempotency & Duplicate Guards:** Order submission controls are disabled during in-flight operations (`IsActionPending = true`), preventing double-clicks or multiple order generation.
4. **Order Prefill Safety:** Clicking an order-book level in the Multi-Market Order Book or Trading Workstation only prefills the Limit Price field; it **NEVER** submits an order automatically.
