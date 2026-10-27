# AMS Vela Windows Runtime QA Report

Date: 2026-10-28  
Branch: `feature/avalonia-desktop`  
Default: `UseVelaChart=false`

## Environment and preflight

- Branch verified: `feature/avalonia-desktop`.
- Existing working-tree changes were preserved.
- WebView2 runtime verified installed at `C:\Program Files (x86)\Microsoft\EdgeWebView\Application\155.0.4283.62`.
- Packaged assets verified under the build output: `index.html`, `adapter.js`, `app.js`, `style.css`, and `lib/vela.global.js` (2,226,697 bytes).
- No running Alpha Meridian desktop process was found during preflight. No process was terminated.
- No running Python backend process was found during preflight. The backend was not restarted.

## Manual visual verification

**NOT VERIFIED.** The actual Avalonia desktop window could not be exercised against the active backend in this environment. Consequently there is no screenshot or runtime evidence proving Vela candle rendering, symbol/timeframe synchronization, indicators, crosshair, pan/zoom, overlays, attribution, resize/fullscreen behavior, or duplicate-polling behavior in the production application.

The packaged local assets and compiled host are evidence of packaging/build only, not visual runtime proof.

## Lifecycle and failure tests

**NOT VERIFIED manually.** WebView2-missing, initialization failure, asset-missing, backend disconnection, repeated navigation, attach/detach, shutdown, resize/fullscreen, and native fallback scenarios were not exercised in the desktop process. No live backend or trading session was interrupted to create failure conditions.

The host code retains local-asset checks, initialization error display, WebView stop-on-detach, and `UseVelaChart=false` native fallback behavior, but those behaviors remain code-reviewed rather than runtime-proven here.

## Resource measurements

**NOT MEASURED.** No honest startup, memory, CPU, or interaction-latency comparison was possible without a live desktop/WebView2 session and backend data. No performance claim is made.

## Build and test results

- Desktop solution build: **passed**, 0 errors, 1 warning (`MSB3061`: unable to delete the existing `AlphaMeridianSystems.exe`, which was not terminated).
- Relevant tests: **7 passed**, 0 failed, 0 skipped (`VelaBridgeContractTests` and `LiquidityIntelViewModelTests`).
- Asset packaging verification: **passed** by direct file inspection.
- `UseVelaChart=false` remains the persisted default; no default switch was made.
- No order, cancellation, session mutation, leverage, exchange-setting, backend restart, or Auto-Hunter action was invoked.

## Remaining release blockers

1. Manual Windows QA in the actual Avalonia application.
2. Runtime failure and repeated attach/detach verification.
3. Resource profiling and interaction responsiveness measurements.
4. Verified parity of Vela indicators/overlays against the native chart.

The production default must remain native until these gates are completed.
