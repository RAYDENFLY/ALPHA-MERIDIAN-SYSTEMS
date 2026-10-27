# AMS Vela Visual QA Report

Date: 2026-10-28  
Branch: `feature/avalonia-desktop`  
Production default: `UseVelaChart=false`

## Actual window evidence

The isolated process was launched with `AMS_VELA_HARNESS=1` and produced the top-level window title `Alpha Meridian Systems — Quantitative Intelligence` with a responsive Avalonia window at 1440×759.

Captures:

- First navigation attempt showing `ERR_FILE_NOT_FOUND`: [vela-harness-window.png](C:\Users\Administrator\Documents\SignalGenerator\desktop\test-artifacts\vela-runtime\vela-harness-window.png)
- Final navigation attempt after switching to `NavigateToString`: [vela-harness-final.png](C:\Users\Administrator\Documents\SignalGenerator\desktop\test-artifacts\vela-runtime\vela-harness-final.png)

## Result

**FAIL / BLOCKED.** The final actual-window capture shows the Avalonia shell and the HTML shell controls, but the packaged CSS/JavaScript did not execute: the `TEST DATA — NOT LIVE` banner is absent, the Vela canvas is absent, the fixture candles are absent, and the page remains in loading/disconnected text state. Vela attribution text is present in the raw HTML shell, but this does not establish a rendered Vela chart.

The reproducible blocker is local asset resolution when the WebView is loaded with `NavigateToString`; the base URI approach did not cause relative `style.css`, `app.js`, and `lib/vela.global.js` resources to execute in the actual WebView2 window. The earlier direct navigation produced `ERR_FILE_NOT_FOUND`.

## Interaction and lifecycle results

Not passed because the Vela renderer did not initialize. Crosshair, zoom, pan, fixture symbol/timeframe changes, indicators, resize/fullscreen, and rendered attribution are **NOT VERIFIED**.

The harness process was launched without contacting the backend. A graceful WM_CLOSE was used for the prior isolated instance and it exited; no force termination was used. The current capture process was not force-terminated. Missing-asset, initialization-failure, delayed-message, detach/reattach, and fallback scenarios remain unverified in the actual window.

## Resource measurements

Not measured. No valid Vela renderer session was available for comparable startup, memory, CPU, or interaction measurements.

## Build and tests

- Desktop solution build after the navigation changes: **passed**, 0 warnings, 0 errors.
- Earlier relevant harness/bridge tests: **7 passed**, 0 failed.
- Production default remains native with `UseVelaChart=false`.
- No backend, trading, Auto-Hunter, session, or exchange mutation action was invoked.

## Required next fix

Serve the packaged Vela directory through a trusted loopback/local WebView content origin, or use a verified WebView virtual-host mapping API, so relative CSS/JS/library assets resolve inside WebView2. Re-run this same capture and interaction matrix only after the `TEST DATA — NOT LIVE` banner and Vela canvas are visible.

Production default must remain unchanged.
