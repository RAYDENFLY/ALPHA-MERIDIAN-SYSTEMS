# AMS Vela Production Migration Report

Date: 2026-10-28  
Branch: `feature/avalonia-desktop`  
Status: **Not migrated — blocked before production changes**

## Verified inspection

- The evaluated package is `@luxalgo/vela@0.9.0`, present under `desktop/poc/vela-chart/node_modules/@luxalgo/vela` and pinned in the PoC package lock.
- The PoC uses the bundled `desktop/poc/vela-chart/web/lib/vela.global.js`, real read-only chart endpoints through `adapter.js`, and visible `Powered by Vela™ by LuxAlgo` attribution.
- The PoC report records Apache-2.0 core licensing, the required attribution notice, and exclusion of the AGPL `@luxalgo/vela-pinets` package.
- The production Liquidity & Order Flow screen is `LiquidityIntelView.axaml` and currently renders `controls:CandlestickChartControl`.
- `LiquidityIntelViewModel` loads real candles, timeframe/symbol state, structural levels, sweep events, and order-book overlay data from the existing API client.
- The production `.csproj` has no WebView2/Avalonia WebView host package or reference. No production Vela host exists in `desktop/src`.
- The reported PoC host measurements are not reproducible as a production Avalonia host from the current repository contents: the PoC directory contains browser assets/server code, but no checked-in C# WebView2 host.

## Why production migration was not claimed

Replacing the native chart now would either leave the production view without a renderer or require introducing and restoring a new WebView2 hosting dependency plus a C#↔JavaScript lifecycle/data bridge. That dependency and bridge are not present, and no production visual verification could be performed safely without them. The native production chart remains intact.

## Data and safety findings

- Existing verified chart data is available through the current `IApiClient`/`LiquidityIntelViewModel` flow and the PoC read-only endpoints.
- Existing order-book/depth and wall models are separate from the native chart and were not changed.
- No order, cancellation, session, leverage, exchange-setting, backend restart, or Auto-Hunter mutation action was invoked.
- Unrelated working-tree changes were preserved.

## Validation

No production migration build or visual parity test was run because no production Vela host was added. A non-mutating validation attempt using a separate `C:\tmp` output path was blocked by the environment denying directory creation under that path (`MSB3191`), followed by copy failures (`MSB3021`). The existing desktop executable was not stopped. No successful production migration build is claimed.

## Required controlled next step

1. Add an explicitly approved, pinned Windows WebView2 host dependency and a production-only chart host control.
2. Copy or package the already-evaluated Vela assets without adding `vela-pinets`.
3. Bridge verified `KlineDto` data, symbol/timeframe changes, loading/error/stale state, and read-only depth data into one host-owned polling path.
4. Add lifecycle disposal, attribution, runtime availability checks, and deterministic host tests.
5. Run visual/manual production verification before switching navigation.

## Rollback

Keep `CandlestickChartControl` and the existing `LiquidityIntelView` binding as the production path until the above checks pass. A future migration must be switchable behind a chart-host setting or feature flag so rollback is a single configuration/navigation change; no trading or exchange code should be part of that switch.
