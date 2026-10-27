# Alpha Meridian Systems — Vela Chart PoC: Phase 0 Audit Report

**Date:** 2026-10-28  
**Branch:** `feature/avalonia-desktop`  
**Baseline Commit:** `70b1226` (Clean working tree)  
**Evaluation Target:** LuxAlgo Vela Charting Engine (`@luxalgo/vela@0.9.0`)  
**Status:** Phase 0 Audit Completed

---

## 1. Repository, Environment & Solution Architecture

- **Git Branch & Status:** Current branch is strictly verified as `feature/avalonia-desktop`. Working tree is clean. Unrelated user changes and existing production code are strictly preserved.
- **Solution:** `desktop/SignalGenerator.Desktop.slnx`
- **Target Framework:** `net8.0` (Windows x64)
- **Avalonia Version:** `12.1.4` (with Fluent theme, Inter fonts, CommunityToolkit.Mvvm `8.4.2`, ScottPlot `5.1.59`)
- **Existing Chart Implementation:**
  - Located in `desktop/src/SignalGenerator.Desktop/Controls/CandlestickChartControl.cs` (~33 KB)
  - Driven by `LiquidityIntelViewModel.cs` and `LiquidityIntelView.axaml`
  - Utilizes Skia / DrawingContext for native rendering of candles, volume bars, EMA overlays, and Stochastic (5, 3, 3) sub-chart.
  - Production chart files are strictly preserved and left untouched during this evaluation.

---

## 2. Host Runtime & System Resources

- **Microsoft Edge WebView2 Runtime:** **VERIFIED INSTALLED**
  - Path: `C:\Program Files (x86)\Microsoft\EdgeWebView\Application`
  - Version: `155.0.4283.62` (Registry key `HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`)
- **Available Hardware Resources:**
  - Total Memory: 16,658,020 KB (~16.0 GB)
  - Available Physical RAM: ~1,986,668 KB (~1.9 GB)
  - Process Architecture: 64-bit Windows
- **Tooling Runtimes:**
  - Node.js: `v22.19.0`
  - npm: `11.18.0`
  - .NET SDK: `10.0.403` (targeting `net8.0`)

---

## 3. Verified Backend API Endpoints & Data Schemas

All endpoints verified against the active local FastAPI daemon (`PID 14864` on `127.0.0.1:8000`). No state or process was mutated.

### A. Historical Candles (`GET /api/chart/klines`)
- **Parameters:**
  - `symbol` (e.g. `BTCUSDT`)
  - `timeframe` (supported: `15m`, `1h`, `4h`, `1d`)
  - `limit` (integer, **must be >= 10**, default 100)
- **Schema:**
  ```json
  [
    {
      "time": 1793095200,             // Epoch timestamp in seconds
      "timestamp_ms": 1793095200000,  // Epoch timestamp in milliseconds
      "open": 79758.6,
      "high": 80132.1,
      "low": 79480.9,
      "close": 79943.6,
      "volume": 256.36,
      "quote_volume": 20463114.14,
      "datetime_utc": "2026-10-27T10:00:00+00:00"
    }
  ]
  ```
- **Vela Data Mapping:**
  - Vela expects `OHLCV`: `{ time: Millis, open: number, high: number, low: number, close: number, volume?: number }`
  - Field mapping: `time = timestamp_ms` (or `time * 1000`), `open = open`, `high = high`, `low = low`, `close = close`, `volume = volume`.

### B. Symbol & Timeframe Directory (`GET /api/chart/symbols`)
- **Schema:**
  ```json
  {
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", ...],
    "default_symbol": "BTCUSDT",
    "default_timeframe": "1h",
    "supported_timeframes": ["15m", "1h", "4h", "1d"]
  }
  ```

### C. Order Book Depth (`GET /api/chart/cross_book` & `GET /api/model_b/orderbook/{symbol}`)
- **Cross-Book Schema (`/api/chart/cross_book?symbol={symbol}`):**
  - `bids_buckets`: `[{ bucket_price, total_quantity, total_notional_usdt, venue_count, confirmation_tier }, ...]`
  - `asks_buckets`: `[{ bucket_price, total_quantity, total_notional_usdt, venue_count, confirmation_tier }, ...]`
  - `venue_summaries`: per-exchange best bid/ask, spread, latency, and top 50 depth.
- **Model B L2 Book (`/api/model_b/orderbook/{symbol}`):**
  - Top 5 bids and asks per exchange (`asterdex`, `binance`, `hyperliquid`).

---

## 4. LuxAlgo Vela Audit & Version Pinning

- **Pinned Version:** `@luxalgo/vela@0.9.0`
- **Git Commit Hash:** `aecb13847dff3c1a50a2f3433e9804d26fc581ed`
- **Core License:** `Apache-2.0` (Permissive open source)
- **Notice & Attribution Requirements:**
  - `NOTICE` file Section 4(d): *"Any product, website, or application that displays charts rendered by this software must show a visible attribution to the Vela project on every page or screen where such a chart is displayed."*
  - Satisfied by keeping Vela's built-in logomark link or providing explicit visible attribution on screen.
- **AGPL-3.0 Separation:**
  - The separately licensed `@luxalgo/vela-pinets` package (Pine Script engine) is licensed under `AGPL-3.0`.
  - **Audit Finding:** `@luxalgo/vela-pinets` is **NOT** needed. Vela core includes native built-in indicators (`ema`, `stochastic`, `rsi`, `macd`, etc.) that compute directly from OHLCV bars without any scripting engine.
  - Zero AGPL-3.0 dependencies are installed or referenced.
- **Native Indicator Specifications in Vela:**
  - `ema`: type `"ema"`, inputs `{ length: number, source: string, color: string }`.
  - `stochastic`: type `"stochastic"`, inputs `{ kLength: number, kSmoothing: number, dLength: number, upper: number, lower: number, kColor: string, dColor: string }`.
  - Supports multiple instances (e.g. EMA 20, 50, 100, 200).

---

## 5. Architectural Decision & Integration Strategy

1. **Isolation Boundary:**
   - All PoC code will live inside an isolated directory: `desktop/poc/vela-chart/`.
   - Production project `SignalGenerator.Desktop` remains untouched and functional.
2. **Hosting Approach:**
   - Vela is a TypeScript/WebGL2/Canvas library.
   - For desktop integration, Microsoft Edge WebView2 is verified present on the host OS.
   - A dedicated local HTML/JS harness bundles Vela with a custom Alpha Meridian Systems institutional dark theme, Consolas typography, crosshair, indicator controls, order-book depth panel, and real-time polling against `127.0.0.1:8000`.
   - A lightweight C# .NET 8 host window in `desktop/poc/vela-chart/host/` embeds WebView2 to evaluate native desktop embedding, measuring memory, CPU, resize behavior, and cold start.
3. **Data Security & Safety:**
   - Only GET requests (`/api/chart/klines`, `/api/chart/symbols`, `/api/chart/cross_book`).
   - Zero credentials, API keys, or `.env` values exposed.
