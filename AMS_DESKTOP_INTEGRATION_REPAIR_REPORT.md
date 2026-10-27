# AMS Desktop Integration Repair Report

Date: 2026-10-28  
Branch: `feature/avalonia-desktop`

## Root causes found

- Multi-market tiles were initialized with fabricated prices, ladders, walls, and `LIVE` status before a backend response.
- Symbol discovery existed in `IApiClient`/`ApiClient` but the order-book VM used a hardcoded list.
- Replacing a tile symbol recreated synthetic depth before requesting the selected instrument.
- The Vela proof of concept is isolated and already recommends retaining the native Avalonia chart for production.

## Changes made

- Order-book symbol directory now loads from `GET /api/chart/symbols`.
- Existing tile layout is preserved across refreshes; unsupported current symbols are marked `UNAVAILABLE`.
- Initial and replacement tiles use `LOADING` with empty depth until verified data arrives.
- Fabricated initial ladders are no longer displayed as market data.
- Unsupported symbol selections are rejected without silently substituting another instrument.

## Verified mappings and safety

- Order-book data remains read-only through `GET /api/model_b/orderbook/{symbol}`.
- Chart symbol discovery uses `GET /api/chart/symbols`.
- No Start, Stop, Pause, re-authentication, order, position, leverage, or exchange-setting endpoint was invoked.
- The active backend/session was not restarted or stopped.

## Vela status

The isolated PoC and audit report verify real candle mapping, EMA 20/50/100/200, Stochastic 5,3,3, crosshair, pan/zoom, depth, fullscreen, and attribution. Its measured WebView2 overhead and native Avalonia performance lead to the documented decision to retain the production chart and keep Vela isolated.

## Phase 2 build/test result

The initial failure was caused by the sandbox identity having only inherited `ReadAndExecute` access to the Avalonia BuildServices directory; the telemetry log did not exist, so this was not a locked-file failure. The build was validated with the narrowly scoped MSBuild property `UsedAvaloniaProducts=` to bypass the optional Avalonia statistics task. No ACLs, files outside the repository, or running processes were modified.

- Solution build: **passed**, 0 warnings, 0 errors.
- Targeted desktop tests: **12 passed**, 0 failed.
- Additional settings tests: **4 passed**, 0 failed.
- Additional order-book/workstation/DTO tests: **28 passed**, 0 failed.

## Phase 2 freshness and customization

- The verified Model B response contains per-venue `timestamp_ms`; it is now mapped into the desktop DTO.
- A response older than 30 seconds is marked `STALE`; a successful HTTP response alone is not sufficient for `LIVE`.
- Tiles begin as `LOADING`, unsupported selections become `UNAVAILABLE`, and request failures become `ERROR`.
- The UI now supports add, remove, replace, and reorder operations in the VM, with persisted symbol order through `DesktopSettings`; the default remains eight tiles in a 4x2 grid.
- Response time is retained in the tile display. Feed age is not invented when the timestamp is absent.

## Remaining limitations

- The Avalonia template now exposes add-symbol and remove-tile controls; reorder remains available through the VM command and needs a dedicated drag/drop affordance.
- Spot/perpetual and venue metadata require a richer backend catalog contract before they can be shown without inference.
- Deterministic level-change animation tracking and dedicated delayed/malformed-response tests remain outstanding.

## Rollback

Revert only the tracked change to `MultiMarketOrderBookViewModel.cs`; do not reset or clean the worktree. The Vela PoC remains isolated and production chart files are unchanged.
