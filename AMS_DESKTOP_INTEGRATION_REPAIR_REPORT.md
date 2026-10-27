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

## Build/test baseline

The baseline `dotnet build desktop/SignalGenerator.Desktop.slnx --no-restore` and the targeted desktop test run were blocked by the environment: Avalonia's build task could not write `C:\Users\Administrator\AppData\Local\AvaloniaUI\BuildServices\buildtasks.log` (`UnauthorizedAccessException`). No application or backend process was changed.

## Remaining limitations

- The order-book model still lacks a timestamp field in the Model B response DTO, so freshness is currently limited to request/update state rather than authoritative feed age.
- Spot/perpetual and venue metadata require a richer backend catalog contract before they can be shown without inference.
- User selection/layout persistence and depth-update animation still need deterministic implementation and tests.

## Rollback

Revert only the tracked change to `MultiMarketOrderBookViewModel.cs`; do not reset or clean the worktree. The Vela PoC remains isolated and production chart files are unchanged.
