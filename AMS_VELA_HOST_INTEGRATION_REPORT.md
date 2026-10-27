# AMS Vela Host Integration Report

Date: 2026-10-28  
Branch: `feature/avalonia-desktop`  
Status: **Isolated host built behind an opt-in flag; native chart remains default**

## Selected dependency

- `Avalonia.Controls.WebView` **12.1.1**, compatible with the existing Avalonia 12.1.4/net8.0 project.
- Windows uses the installed WebView2 runtime; deployment must either require the Evergreen runtime or ship the approved fixed-version runtime with the installer.
- Vela remains `@luxalgo/vela@0.9.0`; `@luxalgo/vela-pinets` was not added.
- Vela core/NOTICE attribution remains in the packaged PoC HTML and is visible in the Vela page.

## Host architecture and lifecycle

- Added `Views/VelaChartHostView.axaml` and code-behind.
- The host loads only the packaged local `VelaChart/index.html` file and rejects initialization when the asset is absent.
- It reports actionable initialization errors, navigates to no arbitrary remote URL, stops the browser on detachment, and resets initialization state for reattachment.
- PoC assets are copied to the application output under `VelaChart/` without regenerating or modifying the existing PoC.
- Added `DesktopSettings.UseVelaChart`, default `false`, and `LiquidityIntelViewModel.UseVelaChart` reads that setting.
- Liquidity view contains both hosts, but the native `CandlestickChartControl` remains the default and is hidden only when the explicit flag is enabled.

## Data bridge status

The host is isolated and buildable, but the current PoC JavaScript still fetches its own read-only chart endpoints. A C#-owned message/data bridge for `KlineDto`, structural levels, sweep events, and order-book data has not yet been implemented. Therefore the flag is not recommended for operational use until duplicate polling and full state synchronization are completed.

The native path remains authoritative for production data and trading-adjacent overlays. No order-entry, exchange adapter, strategy, or live-session code was changed.

## Validation

- Production solution build: **passed**, 0 warnings, 0 errors using `UsedAvaloniaProducts=` and `UseAppHost=false` to avoid optional telemetry and the running executable output lock.
- Host source compiles against `Avalonia.Controls.WebView` 12.1.1.
- Runtime visual startup, WebView2-missing behavior, interaction parity, memory, resize, fullscreen, and disposal were not manually verified in this environment.
- No backend, Auto-Hunter, order, cancellation, leverage, exchange-setting, or session mutation endpoint was invoked.

## Known limitations

- No C#↔JavaScript schema-validated bridge exists yet; the PoC page owns its GET polling when enabled.
- Existing production overlays (SMC/structural levels, sweep events, and wall coordinates) are not bridged into Vela.
- WebView2 runtime availability is surfaced by initialization failure, but a dedicated preflight runtime probe and installer packaging are still required.
- The setting is persisted, but no user-facing production toggle was added; enabling currently requires settings/configuration control.

## Rollback

Leave `use_vela_chart` false. The native Avalonia chart remains immediately available and unchanged. If an enabled host fails, set the flag back to false; the view returns to `CandlestickChartControl` without changing trading or backend state.
