# AMS Vela Runtime Harness Report

Date: 2026-10-28  
Branch: `feature/avalonia-desktop`  
Production default: `UseVelaChart=false`

## Harness architecture

- Added an explicit `AMS_VELA_HARNESS=1` process mode.
- In harness mode, `LiquidityIntelViewModel` does not initialize the backend data path and enables Vela only for that isolated process.
- `VelaChartHostView` navigates to the local page with `?host=harness`.
- The page displays a persistent `TEST DATA — NOT LIVE` banner and uses deterministic synthetic candles, symbol `TESTBTC`, and timeframe `1h`.
- Harness fixtures never enter the production default path and do not call the backend.

## Runtime environment

- WebView2 runtime: installed, version `155.0.4283.62`.
- Vela assets: packaged and present in the build output.
- The built Avalonia DLL launched successfully in a Windows process with title `Alpha Meridian Systems — Quantitative Intelligence` and `Responding=True` while using the harness environment variable.
- The launch was performed without restarting or contacting the trading backend.

## Runtime/rendering results

**PARTIALLY VERIFIED.** The actual Avalonia process initialized and responded in harness mode. However, no screenshot capture or programmatic visual inspection of the WebView contents was available in this environment. Therefore the following remain NOT VERIFIED: visible Vela attribution, fixture candle pixels/timestamps, symbol/timeframe UI changes, crosshair, zoom/pan, indicators, resize/fullscreen, and actionable error-panel rendering.

## Lifecycle and recovery

**NOT VERIFIED manually.** Missing-asset, initialization-failure, detach/reattach, repeated navigation, shutdown, delayed bridge messages, and native fallback scenarios were not exercised. The graceful close request to the isolated process did not complete within the wait window; the process was not force-terminated, in accordance with the safety restriction.

## Data parity

Not applicable to the isolated fixture run. No production API comparison was performed, and no overlay or indicator parity claim is made.

## Resource profiling

**NOT MEASURED.** No startup, memory, CPU, or interaction timing measurements were collected. No performance claim is made.

## Build and tests

- Desktop solution build: **passed**, 0 warnings, 0 errors using `UsedAvaloniaProducts=` and `UseAppHost=false`.
- Relevant tests: **7 passed**, 0 failed, 0 skipped (`VelaBridgeContractTests` and `LiquidityIntelViewModelTests`).
- Fixture-mode source and packaged assets compiled successfully.
- `UseVelaChart=false` remains the production default.

## Remaining blockers

1. Capture reproducible screenshots from the actual Avalonia/WebView2 window.
2. Exercise interaction, lifecycle, failure recovery, and native fallback paths.
3. Measure native versus Vela resource usage and responsiveness.
4. Run separate read-only production-data parity checks when the backend is available.

This harness does not justify switching the production default.
