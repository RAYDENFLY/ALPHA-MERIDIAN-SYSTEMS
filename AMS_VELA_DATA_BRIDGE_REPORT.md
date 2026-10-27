# AMS Vela Data Bridge Report

Date: 2026-10-28  
Branch: `feature/avalonia-desktop`  
Default: `UseVelaChart=false`

## Bridge contract

Added `VelaBridgeMessage` version 1 with:

- `type`: currently `vela.snapshot`
- `symbol`, `timeframe`, and monotonic `sequence`
- validated OHLCV candle array
- explicit state (`LIVE`, `STALE`, `LOADING`, `EMPTY`, `ERROR`, or `DISCONNECTED`)
- optional error text

`VelaBridgeValidator` rejects unsupported versions/types, unsupported symbol/timeframe values, invalid OHLC relationships, negative volume, and non-increasing timestamps. No credentials or arbitrary objects are included.

The host exposes `SendSnapshot`, serializes the validated contract, and invokes only the local page function `window.amsReceiveBridgeMessage`. The JavaScript receiver rejects unsupported versions/types and ignores out-of-order sequence values.

## Polling ownership

The PoC adapter currently has three read-only GET paths: `/api/chart/symbols`, `/api/chart/klines`, and `/api/chart/cross_book`. A production-bridge marker in the page disables its timer once a C# snapshot is received. The existing C# API client remains the intended owner for production data acquisition.

The full LiquidityIntelViewModel-to-host snapshot wiring for candles, structural levels, sweep events, and order-book overlays is not yet complete. Therefore this is a contract and host-surface increment, not a claim of complete production data parity.

## Lifecycle and runtime

- Local packaged navigation remains restricted to `VelaChart/index.html`.
- Missing assets and initialization exceptions surface in the host error panel.
- Detachment calls `Stop()` and resets attachment state.
- `Avalonia.Controls.WebView` 12.1.1 is used with the installed WebView2 runtime.
- Evergreen versus fixed-version runtime packaging remains an installer concern and was not changed here.

## UI and fallback

`UseVelaChart` remains persisted and false by default. The native Avalonia chart remains visible unless explicitly enabled, and no trading, exchange, strategy, or session code was changed. Unsupported SMC/structural/sweep parity is not claimed.

## Validation

- Desktop solution build: **passed**, 0 warnings, 0 errors using `UsedAvaloniaProducts=` and `UseAppHost=false`.
- Bridge tests: **4 passed**, 0 failed.
- Tests cover valid snapshots, invalid versions/types, invalid OHLC, and non-monotonic timestamps.
- Manual WebView2 visual QA, runtime-missing simulation, memory, resize, fullscreen, and full lifecycle testing were not performed.
- No mutation endpoints were invoked and the active backend/Auto-Hunter were not stopped or restarted.

## Remaining limitations and next step

The next required step is wiring `LiquidityIntelViewModel` snapshots to `VelaChartHostView.SendSnapshot` with cancellation/sequence ownership and validated overlay/depth payloads. Keep `UseVelaChart=false` until that wiring and manual Windows visual QA are complete.
