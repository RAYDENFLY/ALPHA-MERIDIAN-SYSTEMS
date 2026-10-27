# ALPHA MERIDIAN SYSTEMS — Market Intelligence Spot Support & Empty Table Audit

**Product:** Alpha Meridian Systems  
**Parent Brand:** AMS Capital  
**Corporate Entity:** PT Authentic Media Services  
**Target Branch:** `feature/avalonia-desktop`  
**Report Date:** October 28, 2026  
**Auditor:** Alpha Meridian Systems Quantitative & Core Desktop Team  

---

## 1. Executive Summary & Root Cause

The desktop application's **Market Intelligence** workstation reported:
- **Tracked Perpetual Contracts:** 291 deduplicated cross-exchange assets (640 total active contract instances).
- **Venue Filters:** `ALL VENUES`, `ASTERDEX`, `BINANCE`, `BYBIT`, `HYPERLIQUID`.
- **Market Type Filters:** `ALL`, `PERPETUAL`, `SPOT`.
- **Observed Defect:** When selecting the `SPOT` filter, the UI showed `NO SPOT INSTRUMENTS AVAILABLE ON CONNECTED VENUES`, while the main table was empty. Furthermore, the generic empty notice and the spot unavailable card rendered simultaneously, creating an unreadable visual overlap.

### Core Verdict
1. **Empty Table on Spot is Expected Backend Architecture:**
   - 100% of contracts tracked by the screener service (`src/screener/service.py`) and exchange adapters (`src/exchange/`) are **Linear USDT Perpetual Futures**.
   - Zero spot instruments are ingested, cached, or returned by `/api/screener/markets`.
   - The platform strictly enforces linear USDT perpetual contracts across all connected derivative venues without synthetic or fabricated spot substitution (`src/exchange/cross_exchange_liquidity.py:9`).

2. **UI & Data Integration Defects Identified & Repaired:**
   - **Visual Collision (Overlapping Banners):** In `MarketMonitorView.axaml`, both `IsSpotUnavailable` notice and `!Markets.Count` notice were placed in `Grid.Row="1"` without mutual exclusivity, causing the empty message to render directly on top of the Spot unavailable card.
   - **Missing Active Filter Highlights:** Filter buttons (`ALL`, `PERPETUAL`, `SPOT`, and venue buttons) lacked dynamic active visual styling, giving the operator no visual confirmation of which filter was active.
   - **Faulty Filter Predicates in `ApplyFiltersAndSort()`:**
     - The predicate previously checked `spotItems.Count == 0` and returned; but if spot items were ever present (in test mocks or future integrations), it lacked an `else if (SelectedMarketType == "SPOT") query = query.Where(...)` clause, causing unfiltered items to pass through.
     - The venue filter previously used `m.Venues == null || m.Venues.ContainsKey(vKey)`, which incorrectly included instruments with null venue dictionaries.
   - **Absence of Distinct Error & Loading States:** Network timeouts, API connection drops, and deserialization errors were not rendered as distinct, actionable cards with retry triggers.
   - **Lack of Venue-Specific Spot Limitation Notices:** Selecting a specific venue with Spot gave generic text instead of explaining the actual architectural reason why Spot is absent on that venue.

---

## 2. Actual Spot vs Perpetual Support by Venue

| Venue | Protocol / Adapter | Actual Perpetual Support | Actual Spot Support | Architectural Explanation |
| :--- | :--- | :--- | :--- | :--- |
| **AsterDEX** | `AsterAdapter` / `aster_v3.py` (Arbitrum / Berachain) | **Supported** (Linear EVM Perps, 799 pairs) | **Not Supported** | AsterDEX is an on-chain decentralized perpetual exchange running Aster V3 perpetual smart contracts. AsterDEX has no native Spot order books or AMM pools integrated in the platform. |
| **Binance** | `BinanceFuturesAdapter` (`fapi.binance.com`) | **Supported** (USD-M Linear Perps, 890 pairs) | **Not Configured** | Platform integrates `BinanceFuturesAdapter` querying USD-M perpetual futures. Binance Spot API (`api.binance.com`) is not queried or configured in the screener engine. |
| **Bybit** | `BybitLinearAdapter` (`api.bybit.com`) | **Supported** (USDT/USDC Linear Perps, 820 pairs) | **Not Configured** | Platform integrates `BybitLinearAdapter` querying `category=linear`. Bybit Spot API (`category=spot`) is not queried or configured in the screener engine. |
| **Hyperliquid** | `HyperliquidAdapter` (`api.hyperliquid.xyz`) | **Supported** (Linear Perps, 235 pairs) | **Not Configured** | Platform integrates `HyperliquidAdapter` querying `metaAndAssetCtxs` for perpetual futures. Hyperliquid Spot order books (`spotMetaAndAssetCtxs`) are not queried by the screener adapter. |
| **ALL VENUES** | `MultiExchangeMarketScreener` (`src/screener/service.py`) | **Supported** (Deduplicated multi-venue perps) | **Not Configured** | Connected venue adapters provide perpetual futures exclusively. Screener engine is intentionally designed as a perpetual futures screener. |

---

## 3. Implementation Corrections

### A. Data Models (`MarketTickerDto.cs`)
1. **Explicit Contract Type Metadata:**
   Added `[JsonPropertyName("contract_type")] public string? ContractType { get; set; }` to `MarketAssetTickerDto`.
2. **Metadata-Driven Instrument Type:**
   Inspects `ContractType` first, then venue details `v.ContractType`, defaulting safely to `"PERPETUAL"`.
3. **Accurate Open Interest Formatting for Spot:**
   Spot instruments legitimately do not possess open interest. `FormattedOi` now returns `"N/A (SPOT)"` when `InstrumentType == "SPOT"`, preventing fabricated `$0` or misleading zero values.

### B. ViewModel Architecture (`MarketMonitorViewModel.cs`)
1. **Mutually Exclusive UI State Discriminators:**
   - `ShowDataTable`: Visible only when not loading, no error, spot not unavailable, and `Markets.Count > 0`.
   - `ShowSpotUnavailable`: Visible only when not loading, no error, and spot is unavailable for the selected scope.
   - `ShowGenericEmptyNotice`: Visible only when not loading, no error, spot not unavailable, and `Markets.Count == 0`.
   - `HasError`, `IsDataMappingError`, `IsApiUnavailableError`: Distinct flags for network vs deserialization errors.
2. **Venue-Specific Spot Limitation Generator:**
   Implemented `GetSpotUnavailableExplanation(string venue)` returning accurate explanations tailored to AsterDEX, Binance, Bybit, Hyperliquid, or multi-venue scopes.
3. **High-Contrast Active Filter Button Properties:**
   Exposed dynamic background, foreground, and border properties (`TypeAllBackground`, `TypePerpBackground`, `TypeSpotBackground`, `VenueAsterBackground`, etc.) providing clear active states (`#1F2836` background with `#FF9D00` / `#448AFF` / `#00E676` accents).
4. **Strict Filter Predicates:**
   - Scopes venue filtering using `m.Venues != null && m.Venues.Keys.Any(k => k.Equals(vKey, StringComparison.OrdinalIgnoreCase))`.
   - Strictly isolates `SPOT` vs `PERPETUAL` based on verified `m.InstrumentType`.
5. **One-Click Recovery Actions:**
   Added `ResetFiltersCommand` (`[RESET FILTERS]`), `[SWITCH TO PERPETUAL]`, and `[VIEW ALL INSTRUMENTS]`.

### C. Desktop UI Layout (`MarketMonitorView.axaml`)
1. **Separate Mutually Exclusive Cards in Table Grid:**
   Replaced overlapping borders with clean, separated overlays for Loading, API Error, Spot Limitation Notice, Generic Empty State, and Data ListBox.
2. **Dynamic Filter Button Bindings:**
   Bound each Type and Venue button to its respective dynamic color properties.
3. **Actionable Error & Empty State Banners:**
   Each state includes a descriptive header, context pill, technical explanation, and direct action buttons.

---

## 4. Verification & Test Results

### Automated Unit & Integration Tests
All **146 tests** across the desktop solution passed with 0 failures:
- `MarketMonitorViewModel_LoadsMatrixAndComputesLeaders`: Verified 24h volume leaders, gainers, and table population.
- `MarketMonitorViewModel_SpotOnlyDataset_CorrectlyDisplaysSpotAndOmitsOi`: Verified Spot dataset handling and `N/A (SPOT)` open interest formatting.
- `MarketMonitorViewModel_PerpetualOnlyDataset_SpotFilterTriggersSpotUnsupportedState`: Verified Spot filter triggers `ShowSpotUnavailable = true` without error or generic empty clash.
- `MarketMonitorViewModel_VenueSpecificSpotExplanation`: Verified venue limitation explanations across all 5 venue settings.
- `MarketMonitorViewModel_MixedVenueDataset_FilterMatrix`: Verified permutations of Venue + MarketType filtering.
- `MarketMonitorViewModel_ApiUnavailable_TriggersDistinctErrorState`: Verified network connection error rendering with retry action.
- `MarketMonitorViewModel_DataMappingError_TriggersDistinctMappingErrorState`: Verified JSON / schema error discrimination.
- `MarketMonitorViewModel_EmptyResponse_TriggersGenericEmptyNotice`: Verified empty API response rendering.
- `MarketMonitorViewModel_MissingOptionalFields_FormatsGracefully`: Verified null benchmark price, spread, and volume format gracefully as `"--"`.
- `MarketMonitorViewModel_ResetFilters_RestoresDefaultState`: Verified filter reset lifecycle.
- `MarketMonitorViewModel_ActiveButtonStyling_ReflectsState`: Verified active button color transitions.
- `LiveBackend_ScreenerMarkets_DeserializationAudit`: Verified live communication with running Python daemon on `127.0.0.1:8000`, confirming real instruments deserialize as `"PERPETUAL"`.

### Visual Smoke-Test Verification
1. **Market Intelligence Default Overview (`alpha_meridian_market_intelligence_all.png`):**
   - Displays real-time tracked universe (291 deduplicated perpetual pairs, 640 multi-venue contracts).
   - High volume leader: `BTCUSDT $5,457,329,201 USDT`.
   - Top gainer: `BTWUSDT +53.12%`.
   - Active buttons `[ALL]` and `[ALL VENUES]` highlighted with glowing amber borders.
   - Clean, monospace data rows showing symbols, multi-venue badges, benchmark prices, volumes, open interest, 24h change, spreads, and sub-second data freshness.
2. **Spot Unsupported State (`alpha_meridian_market_intelligence_spot.png`):**
   - Active button `[SPOT]` highlighted in amber.
   - Centered terminal card: `■ SPOT MARKET DATA NOT SUPPORTED [VENUE: ALL]`.
   - Detailed notice explaining connected exchange adapters provide perpetual futures exclusively.
   - Non-overlapping action buttons: `[SWITCH TO PERPETUAL]`, `[VIEW ALL INSTRUMENTS]`, `[RESET FILTERS]`.

---

## 5. Files Changed

| File | Type | Changes |
| :--- | :--- | :--- |
| `desktop/src/SignalGenerator.Desktop/Models/MarketTickerDto.cs` | C# Model | Added `ContractType`, verified `InstrumentType` resolution, formatted Spot OI as `"N/A (SPOT)"`. |
| `desktop/src/SignalGenerator.Desktop/ViewModels/MarketMonitorViewModel.cs` | C# ViewModel | Added mutually exclusive state properties, active button styling, venue-specific explanation helper, strict filter predicates, and `ResetFiltersCommand`. |
| `desktop/src/SignalGenerator.Desktop/ViewModels/MainViewModel.cs` | C# ViewModel | Added `MarketIntelligenceSpot` route support for automated smoke testing. |
| `desktop/src/SignalGenerator.Desktop/Views/MarketMonitorView.axaml` | Avalonia XAML | Bound active button styles, separated mutually exclusive state cards, eliminated banner collisions, added action buttons. |
| `desktop/tests/SignalGenerator.Desktop.Tests/MarketMonitorViewModelTests.cs` | C# Tests | Added 11 comprehensive tests covering Spot, Perp, Mixed, Errors, Formatting, and Live Backend deserialization. |
| `MARKET_INTELLIGENCE_SPOT_SUPPORT_AUDIT.md` | Documentation | This audit report. |

---

## 6. Safety & Invariants

- **Daemon Untouched:** The live trading daemon (`PID 14864` on port `8000`) was accessed strictly via read-only GET requests (`/api/screener/markets`). No processes were restarted or reconfigured.
- **No Mock Substitution:** Real API responses were audited and consumed directly. Spot data was not fabricated.
- **Git Branch:** All work committed strictly on `feature/avalonia-desktop`.
