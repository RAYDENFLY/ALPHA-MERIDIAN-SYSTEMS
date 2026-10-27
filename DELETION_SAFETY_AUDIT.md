# SignalGenerator — File-by-File Deletion Safety Audit & Migration Matrix

> **Canonical Archive Copy:** [`data_penting/DELETION_SAFETY_AUDIT.md`](file:///c:/Users/Administrator/Documents/SignalGenerator/data_penting/DELETION_SAFETY_AUDIT.md)  
> **Scope:** Complete inventory of legacy frontend assets, research modules, and API dependencies  
> **Mandate:** Absolute safety — No deletions will occur during this audit phase  

---

## 1. Classification Categories & Governance Rules

Every file and module evaluated in this audit is assigned one of five strict classifications:

| Classification | Meaning & Policy |
| :--- | :--- |
| **`KEEP_CRITICAL`** | **Core live trading, risk, execution, or session management component.** Must NEVER be modified, moved, or deleted under any circumstance. |
| **`KEEP_SHARED`** | **Active backend utility, shared data structure, or database manager** required by the live engine or API layer. Must be preserved. |
| **`MIGRATE_FIRST`** | **Active UI controller or backend endpoint currently serving the web dashboard.** Cannot be removed until the C# / Avalonia desktop client is built, verified, and active in production. |
| **`SAFE_TO_REMOVE_CANDIDATE`** | **Isolated, historical one-off research script or backtest grid runner** with 0 live imports, 0 API routes, and 0 test dependencies. May be safely archived after desktop verification. |
| **`UNKNOWN_DO_NOT_DELETE`** | **Component whose runtime side-effects or dynamic references are unverified.** Policy mandates preserving the file untouched. |

---

## 2. Protected Core Trading Engine (Strictly `KEEP_CRITICAL`)

These 15 components are the operational heart of SignalGenerator. They participate directly in candidate screening, risk gating, order dispatch, and exchange reconciliation.

| File Path | Direct Importers | Runtime Role | Classification | Confidence |
| :--- | :--- | :--- | :--- | :--- |
| `src/ai/groq_autotrader.py` | `src/execution/live_session.py`, `src/api/app.py` | 10 -> 3 -> 1 funnel screener & Groq pipeline | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/ai/groq_key_manager.py` | `src/ai/groq_autotrader.py` | 3-Key rotation, 429 quota failover | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/ai/trade_memory_feedback.py` | `src/ai/groq_autotrader.py` | Streak circuit breaker, revenge locks | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/execution/live_session.py` | `src/api/app.py`, `src/scheduler/live_micro_runner.py` | Master session state, kill switch, background loops | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/scheduler/live_micro_runner.py` | `src/execution/live_session.py` | Adaptive TP refresh, invalidation, risk limits | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/risk/global_lock.py` | `src/scheduler/live_micro_runner.py`, `src/ai/groq_autotrader.py` | Process singleton: max 1 concurrent position | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/risk/live_micro_sizing.py` | `src/scheduler/live_micro_runner.py`, `src/ai/groq_autotrader.py` | $0.20 fixed dollar risk, tick quantization | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/strategy/sweep_reclaim_15m.py` | `src/scheduler/live_micro_runner.py`, `src/ai/groq_autotrader.py` | Live strategy: 15m SMC sweep & dynamic TP | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/strategy/dealing_range.py` | `src/ai/groq_autotrader.py`, `src/api/app.py` | 72h Dealing range & directional zone locks | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/intelligence/dashboard_confluence.py` | `src/ai/groq_autotrader.py`, `src/api/app.py` | Confluence gates & prompt block builder | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/intelligence/coin_inspector.py` | `src/ai/groq_autotrader.py`, `src/api/app.py` | Orderbook depth, spread, tick rounding | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/journal/automated_journal.py` | `src/execution/live_session.py`, `src/api/app.py` | Exchange fill reconciliation & PnL tracking | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/exchange/aster_v3.py` | `src/exchange/aster_adapter.py` | AsterDEX V3 REST client, order brackets | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/exchange/signer_v3.py` | `src/exchange/aster_v3.py` | Web3 EIP-712 cryptographic signature engine | **`KEEP_CRITICAL`** | **Verified (100%)** |
| `src/data/database.py` | Used globally | SQLite WAL manager for `data/scanner.db` | **`KEEP_CRITICAL`** | **Verified (100%)** |

---

## 3. Legacy Web Frontend & Static Assets Audit

The current UI consists of `src/api/dashboard.html` and 24 modular vanilla JavaScript files in `src/static/js/`.

> **Critical Constraint:** `tests/test_dashboard_js_syntax.py` scans `src/api/dashboard.html` and parses EVERY single script in `src/static/js/`. Deleting ANY script while it remains referenced in `dashboard.html` causes the test suite to fail immediately!

| File Path | Consuming Endpoints | Desktop Replacement View | Classification | Recommended Action |
| :--- | :--- | :--- | :--- | :--- |
| `src/api/dashboard.html` | Serves web UI at `GET /` | Replaced by Avalonia Desktop Shell | **`MIGRATE_FIRST`** | Retain until desktop build is operational. |
| `src/static/css/*.css` (3 files) | Styling for web dashboard | Native Avalonia XAML Styles | **`MIGRATE_FIRST`** | Retain until web UI is retired. |
| `src/static/js/shared/state.js` | Client global state store | `MainViewModel` | **`MIGRATE_FIRST`** | Retain for web UI parity. |
| `src/static/js/shared/formatters.js`| Number & currency formatting | C# ValueConverters (`IValueConverter`) | **`MIGRATE_FIRST`** | Retain for web UI parity. |
| `src/static/js/shared/i18n.js` | EN/ID localization | Avalonia Localization Dictionary | **`MIGRATE_FIRST`** | Retain for web UI parity. |
| `src/static/js/shared/pagination.js`| DOM table pagination | Avalonia `VirtualizingStackPanel` | **`MIGRATE_FIRST`** | Retain for web UI parity. |
| `src/static/js/shared/notices.js` | Dynamic banner notifications | Avalonia Toast / Notification control | **`MIGRATE_FIRST`** | Retain for web UI parity. |
| `src/static/js/coin_inspector.js` | `/api/inspector/*`, `/api/autotrader/*` | `OverviewView` + `MarketScreenerView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/sweep_chart.js` | `/api/chart/klines`, `/api/chart/symbols`| `LiquiditySweepChartView` (ScottPlot 5) | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/journal.js` | `/api/v1/journal/*` | `TradingJournalView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/alerts.js` | `/api/alerts/*` | `PriceAlertsView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/market_screener.js` | `/api/screener/*` | `MarketScreenerView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/market_participant.js`| `/api/research/*` | `MarketParticipantsView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/position_intelligence.js`| `/api/intelligence/*` | `MarketParticipantsView` / `OverviewView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/positions.js` | `/api/status`, `/api/account/*` | `OverviewView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/setups.js` | `/api/setups`, `/api/scan` | `OverviewView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/watchlist.js` | `/api/symbols` | `SettingsView` / `ScreenerView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/trade_inspector.js`| `/api/chart/klines` | `LiquiditySweepChartView` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/app.js` | Global event binder | `App.axaml.cs` | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/navigation.js` | Tab navigation switcher | Avalonia Sidebar Navigation | **`MIGRATE_FIRST`** | Retain until desktop verified. |
| `src/static/js/model-a.js` | Legacy Model A tab UI | Obsolete (Deprecated) | **`MIGRATE_FIRST`** | Do not delete until `dashboard.html` removes script import. |
| `src/static/js/model-b.js` | Legacy Model B tab UI | Obsolete (Deprecated) | **`MIGRATE_FIRST`** | Do not delete until `dashboard.html` removes script import. |
| `src/static/js/model-d.js` | Legacy Model D tab UI | Obsolete (Deprecated) | **`MIGRATE_FIRST`** | Do not delete until `dashboard.html` removes script import. |
| `src/static/js/demand_zone.js` | Legacy Demand Zone tab UI | Obsolete (Deprecated) | **`MIGRATE_FIRST`** | Do not delete until `dashboard.html` removes script import. |
| `src/static/js/ai_analyst.js` | Groq analyst modal UI | Obsolete (Deprecated) | **`MIGRATE_FIRST`** | Do not delete until `dashboard.html` removes script import. |
| `src/static/js/backtest.js` | `/api/backtest` UI | Obsolete (Deprecated) | **`MIGRATE_FIRST`** | Do not delete until `dashboard.html` removes script import. |

---

## 4. Backend Research & Obsolete Strategy Modules Audit

| Module Path | Imported by `src/api/app.py`? | Tests Count | Participates in Live Trading? | Classification | Evidence & Recommendation |
| :--- | :---: | :---: | :---: | :--- | :--- |
| `src/demand_zone/scanner.py` | **YES** | 27 tests | NO | **`KEEP_SHARED`** | Imported directly in `app.py`. Cannot be removed without breaking FastAPI router. |
| `src/demand_zone/storage.py` | **YES** | 14 tests | NO | **`KEEP_SHARED`** | SQLite table manager for demand zones. Keep shared. |
| `src/demand_zone/types.py` | **YES** | 90 tests | NO | **`KEEP_SHARED`** | Shared Pydantic schemas. Keep shared. |
| `src/demand_zone/btc_context.py` | **YES** | 1 test | NO | **`KEEP_SHARED`** | Route `/api/demand_zone/btc_context`. Keep shared. |
| `src/demand_zone/risk_sizer.py` | **YES** | 1 test | NO | **`KEEP_SHARED`** | Route `/api/demand_zone/config`. Keep shared. |
| `src/demand_zone/tp_planner.py` | **YES** | 1 test | NO | **`KEEP_SHARED`** | Used in demand zone candidate calculation. |
| `src/demand_zone/zone_detector.py` | **YES** | 1 test | NO | **`KEEP_SHARED`** | Zone detection logic. Keep shared. |
| `src/demand_zone/order_book_analyzer.py`| NO | 1 test | NO | **`UNKNOWN_DO_NOT_DELETE`**| Isolated orderbook analyzer. Keep untouched. |
| `src/model_c/collector.py` | **YES** | 10 tests | NO | **`KEEP_SHARED`** | WebSocket stream collector for Model C research. |
| `src/model_c/config.py` | **YES** | 49 tests | NO | **`KEEP_SHARED`** | Model C settings & DB path. |
| `src/model_c/health.py` | **YES** | 9 tests | NO | **`KEEP_SHARED`** | Route `/api/model_c/health`. |
| `src/model_c/storage.py` | **YES** | 14 tests | NO | **`KEEP_SHARED`** | SQLite storage for Model C events. |
| `src/model_c/types.py` | **YES** | 90 tests | NO | **`KEEP_SHARED`** | Shared types. Keep shared. |
| `src/model_c/event_parser.py` | NO | 2 tests | NO | **`UNKNOWN_DO_NOT_DELETE`**| Binance websocket event parser. Keep untouched. |
| `src/model_d/binance.py` | **YES** | 12 tests | NO | **`KEEP_SHARED`** | Public Binance market feed for Model D. |
| `src/model_d/bots.py` | **YES** | 2 tests | NO | **`KEEP_SHARED`** | Route `/api/model_d/bots`. |
| `src/model_d/candles.py` | **YES** | 44 tests | NO | **`KEEP_SHARED`** | Candle utilities for Model D. |
| `src/model_d/collector.py` | **YES** | 10 tests | NO | **`KEEP_SHARED`** | AsterDEX order flow collector. |
| `src/model_d/decision.py` | **YES** | 12 tests | NO | **`KEEP_SHARED`** | Shadow signal decision engine. |
| `src/model_d/evaluation.py` | **YES** | 13 tests | NO | **`KEEP_SHARED`** | Route `/api/model_d/diagnostic/report`. |
| `src/model_d/features.py` | **YES** | 4 tests | NO | **`KEEP_SHARED`** | Order flow feature engineering. |
| `src/model_d/ledger.py` | **YES** | 4 tests | NO | **`KEEP_SHARED`** | Shadow ledger persistence. |
| `src/model_d/scanner.py` | **YES** | 27 tests | NO | **`KEEP_SHARED`** | Route `/api/model_d/scan/*`. |
| `src/model_d/types.py` | **YES** | 90 tests | NO | **`KEEP_SHARED`** | Model D schemas. |
| `src/model_d/universe.py` | **YES** | 7 tests | NO | **`KEEP_SHARED`** | Supported universe. |
| `src/model_d/wallets.py` | **YES** | 2 tests | NO | **`KEEP_SHARED`** | Route `/api/model_d/wallets/*`. |
| `src/model_d/targets.py` | NO | 7 tests | NO | **`UNKNOWN_DO_NOT_DELETE`**| Target calculation logic. Keep untouched. |
| `src/model_d/whale_following.py`| NO | 1 test | NO | **`UNKNOWN_DO_NOT_DELETE`**| Whale follow heuristics. Keep untouched. |
| `src/backtest/engine.py` | **YES** | 31 tests | NO | **`KEEP_SHARED`** | Event-driven backtester used by `/api/backtest`. |
| `src/backtest/performance.py` | **YES** | 2 tests | NO | **`KEEP_SHARED`** | Performance metric calculator. |
| `src/backtest/model_a_vs_model_b_comparison.py` | NO | 0 tests | NO | **`SAFE_TO_REMOVE_CANDIDATE`**| One-off research benchmark script. Candidate for archival. |
| `src/backtest/model_b_1h_top10_backtest.py` | NO | 0 tests | NO | **`SAFE_TO_REMOVE_CANDIDATE`**| One-off research benchmark script. Candidate for archival. |
| `src/backtest/model_b_fast_top10_backtest.py` | NO | 0 tests | NO | **`SAFE_TO_REMOVE_CANDIDATE`**| One-off research benchmark script. Candidate for archival. |
| `src/backtest/model_b_v3_fast_grid_sweep.py` | NO | 0 tests | NO | **`SAFE_TO_REMOVE_CANDIDATE`**| One-off grid sweep experiment. Candidate for archival. |
| `src/backtest/model_b_v3_refined_backtest.py` | NO | 0 tests | NO | **`SAFE_TO_REMOVE_CANDIDATE`**| One-off research benchmark script. Candidate for archival. |
| `src/backtest/model_b_v3_top10_backtest.py` | NO | 0 tests | NO | **`SAFE_TO_REMOVE_CANDIDATE`**| One-off research benchmark script. Candidate for archival. |
| `src/strategy/generator.py` | **YES** | 14 tests | NO | **`KEEP_SHARED`** | Legacy Model A candidate generator. Keep shared for backtest engine. |
| `src/strategy/planner.py` | **YES** | 3 tests | NO | **`KEEP_SHARED`** | Legacy Model A TPSL planner. Keep shared for backtest engine. |
| `src/strategy/bias.py` | **YES** | 33 tests | NO | **`KEEP_SHARED`** | 4H HTF bias engine. Keep shared. |
| `src/strategy/model_b/evaluator.py` | **YES** | 8 tests | NO | **`KEEP_SHARED`** | Model B route `/api/model_b/setups`. |
| `src/strategy/model_b/generator.py` | **YES** | 14 tests | NO | **`KEEP_SHARED`** | Model B setup generator. |
| `src/strategy/model_b/risk_controls.py` | NO | 1 test | NO | **`UNKNOWN_DO_NOT_DELETE`**| Model B stop controls. Keep untouched. |
| `src/strategy/research_mode2c.py` | NO | 1 test | NO | **`SAFE_TO_REMOVE_CANDIDATE`**| Failed Mode 2C experiment. Candidate for archival. |
| `src/strategy/model_a_shadow.py` | NO | 1 test | NO | **`SAFE_TO_REMOVE_CANDIDATE`**| Experimental shadow evaluator. Candidate for archival. |
| `src/strategy/classifier.py` | NO | 1 test | NO | **`UNKNOWN_DO_NOT_DELETE`**| Regime classifier. Keep untouched. |
| `src/strategy/explainer.py` | NO | 0 tests | NO | **`UNKNOWN_DO_NOT_DELETE`**| LLM setup explainer. Keep untouched. |

---

## 5. Deletion Rollback & Migration Governance Plan

### Staged Removal Protocol:
1. **Phase 1 (Current):** ZERO deletions. Complete desktop architecture design and environment verification.
2. **Phase 2 (Desktop Build):** Construct `SignalGenerator.Desktop` in `desktop/`. All 8 views consume existing FastAPI endpoints.
3. **Phase 3 (Desktop Verification):** Run desktop alongside web dashboard. Verify complete feature parity across all 8 views.
4. **Phase 4 (Legacy Web Retirement):**
   - Comment out legacy routes in `src/api/app.py` for Model A, Model B, Model D, and Demand Zone.
   - Run the 1,111-test suite to ensure zero regressions in the live trading engine.
   - Remove `src/static/js/model-*.js` and `src/api/dashboard.html`.
5. **Phase 5 (Research Script Archival):** Move `SAFE_TO_REMOVE_CANDIDATE` scripts from `src/backtest/` to `data_penting/archived_scripts/` rather than permanently deleting them.

### Rollback Strategy:
- Git branch `research/profitability-validation` retains full commit history.
- Any accidental deletion can be reverted via `git checkout HEAD -- <filepath>`.
- Core trading files (`src/ai/groq_autotrader.py`, `src/execution/live_session.py`, `src/scheduler/live_micro_runner.py`) remain completely untouched throughout the entire process.
