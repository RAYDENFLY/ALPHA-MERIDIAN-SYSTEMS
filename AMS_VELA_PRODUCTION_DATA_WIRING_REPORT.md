# AMS Vela Production Data Wiring Report

Date: 2026-10-28  
Branch: `feature/avalonia-desktop`  
Default: `UseVelaChart=false`

## Actual data flow

`LiquidityIntelViewModel.RefreshIntelAsync` remains the single authoritative refresh path. It calls the existing `IApiClient.GetLiquidityLayersAsync(symbol, timeframe, cancellationToken)`, validates and orders candles, and emits `VelaSnapshotReady` only after the response still matches the request symbol/timeframe and sequence.

`LiquidityIntelView` subscribes the active ViewModel to `VelaChartHostView.SendSnapshot`. Delivery is gated by `UseVelaChart`; the native chart remains active by default. The snapshot contains typed candles, structural levels, sweep events, and verified order-book wall/depth summary data when available.

## Bridge contract and sequencing

Version 1 `vela.snapshot` messages include symbol, timeframe, monotonic sequence, state, validated OHLCV candles, typed structural/sweep overlays, and typed depth/wall data. `VelaBridgeValidator` rejects unsupported versions/types, symbols/timeframes, invalid OHLC relationships, negative volume, and non-increasing timestamps.

Each refresh increments `_velaSequence`, cancels the previous request, captures symbol/timeframe, and rejects late responses before updating or emitting. The JavaScript receiver ignores messages with an older sequence. Host detachment stops the WebView and prevents later delivery through the view’s `UseVelaChart` gate.

## Polling ownership

The host loads the local page with `?host=production`; the PoC JavaScript detects this mode and does not start its own polling timer. Standalone PoC mode remains unchanged. Production data ownership stays in the existing C# API client path; no second exchange connection was added.

## Lifecycle and fallback

The native chart remains the immediate fallback. Missing assets and WebView initialization errors remain visible in the host error panel. `UseVelaChart` is persisted but remains false. No default switch was made.

## Validation

- Desktop solution build: **passed**, 0 warnings, 0 errors using `UsedAvaloniaProducts=` and `UseAppHost=false`.
- Vela bridge tests: **4 passed**, 0 failed.
- Tests cover valid messages, invalid versions/types/OHLC, and non-monotonic timestamps.
- Manual Windows visual QA, WebView2 runtime-missing simulation, memory/resource profiling, fullscreen, resize, and repeated open/close verification were not performed.
- No order, session, leverage, exchange-setting, backend restart, or Auto-Hunter mutation action was invoked.

## Remaining limitations

- Vela JavaScript currently updates candle data through the optional `setData` API when available; indicator and overlay synchronization is not manually verified against the native renderer.
- The host event subscription is designed for the active view lifecycle but requires manual repeated navigation testing before operational enablement.
- Keep `UseVelaChart=false` until manual Windows visual QA and verified data parity are complete.
