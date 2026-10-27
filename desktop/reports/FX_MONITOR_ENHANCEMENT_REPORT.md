# Alpha Meridian Systems — FX Monitor Enhancement Report

**Product:** Alpha Meridian Systems  
**Parent Brand:** AMS Capital  
**Corporate Entity:** PT Authentic Media Services  
**Target Branch:** `feature/avalonia-desktop`  
**Current Baseline Commit:** `bb02c05`  
**Stack:** C# / .NET 8 LTS / Avalonia UI / MVVM  
**Module:** Section 3B Overview FX Monitor (`FxLineChart`, `OverviewViewModel`, `OverviewView`, `FxRateService`)

---

## 1. Executive Summary

As requested in the **Alpha Meridian Systems — FX Monitor Enhancement** specification, two major enhancements were engineered, integrated, and verified on the desktop application's FX monitor without mutating backend services or trading execution systems:

1. **Multi-Pair FX Monitor**:
   - Expanded the single-pair USD/IDR monitor into a multi-pair institutional foreign exchange monitor supporting 6 currency pairs:
     - `USD/IDR` (US Dollar / Indonesian Rupiah)
     - `USD/JPY` (US Dollar / Japanese Yen)
     - `IDR/JPY` (Indonesian Rupiah / Japanese Yen)
     - `EUR/USD` (Euro / US Dollar)
     - `GBP/USD` (British Pound / US Dollar)
     - `AUD/USD` (Australian Dollar / US Dollar)
   - Integrated an institutional dropdown selector styled with Bloomberg terminal dark aesthetics (`#0B0E14` background, `#384252` borders, `#FF9D00` active indicator).
   - Dynamic updating of chart title (`■ {SelectedFxPair} FX MONITOR`), current rate display, rate conversion estimation, and historical series.
   - Distinct source/provider attribution (`European Central Bank (ECB) via Frankfurter API` or fallback `Open Exchange Rates (Cross/Inverse Rate)`), rate classification (`Daily Reference Rate (ECB Fix)`), and UTC sync timestamp.
   - Comprehensive error, weekend/holiday, and unavailable-pair fallback protections without fabricating synthetic candles or intraday ticks.
   - High-precision inverse and cross-rate calculation engine utilizing 28-29 significant digit `decimal` arithmetic.

2. **Interactive Historical Crosshair & Tooltip**:
   - Upgraded `FxLineChart.cs` with pointer hover event handling:
     - **Vertical crosshair**: Rendered as a crisp dashed line (`#384252`) snapping to the X-coordinate of the nearest actual historical observation.
     - **Horizontal crosshair**: Rendered as a crisp dashed line (`#384252`) aligned with the Y-coordinate of the corresponding observation's price level.
     - **Nearest Observation Snapping**: Pure function `FindNearestObservationIndex` with `MidpointRounding.AwayFromZero` snapping directly to recorded data points without interpolating fake historical rates.
     - **Right/Left Price Badge**: High-contrast badge (`#21262D` background, `#FF9D00` accent, white text) highlighting the rate at the exact Y coordinate.
     - **Bottom Date Badge**: High-contrast badge (`#21262D` background, `#8B949E` text) displaying the exact observation date (`yyyy-MM-dd`).
     - **Floating Observation Tooltip**: Adaptive card displaying pair name, rate type classification, observation date, and rate. If the data point contains true OHLC bars (`IsOhlc == true`), it renders individual `Open`, `High`, `Low`, `Close` values; for daily ECB reference rates (`IsOhlc == false`), it displays `RATE: {prefix}{rate}` with clear ECB reference rate attribution.
     - **Zero-Clipping Boundary Protection**: Pure function `CalculateTooltipBounds` dynamically inverting horizontal placement from right-of-cursor to left-of-cursor when approaching the right container margin, and clamping vertical coordinates to ensure zero clipping across all screen resolutions and high-DPI scaling.
     - Clean exit and disposal on pointer leave (`OnPointerExited`).

---

## 2. Multi-Pair Architecture & Configuration Matrix

### 2.1 Currency Pair Configuration (`FxPairConfig`)

Foreign exchange pairs operate under diverse quote conventions, fractional precisions, and currency symbols. In `SignalGenerator.Desktop.Models.TerminalDtos.cs`, a strongly-typed `FxPairConfig` registry defines the mathematical and visual metadata for each pair:

| Pair | Base Currency | Quote Currency | Decimals | Currency Prefix | Default Fallback Rate | Description |
| :--- | :--- | :--- | :---: | :---: | :---: | :--- |
| **USD/IDR** | USD | IDR | 0 | `Rp ` | 18,139 | US Dollar to Indonesian Rupiah |
| **USD/JPY** | USD | JPY | 2 | `¥ ` | 157.82 | US Dollar to Japanese Yen |
| **IDR/JPY** | IDR | JPY | 6 | `¥ ` | 0.008701 | Indonesian Rupiah to Japanese Yen (Micro-rate) |
| **EUR/USD** | EUR | USD | 4 | `$ ` | 1.1150 | Euro to US Dollar |
| **GBP/USD** | GBP | USD | 4 | `$ ` | 1.3328 | British Pound to US Dollar |
| **AUD/USD** | AUD | USD | 4 | `$ ` | 0.7032 | Australian Dollar to US Dollar |

### 2.2 Mathematical Cross-Rate & Inverse-Rate Model

When querying secondary data providers (such as Open Exchange Rates where USD is the base currency), inverse and cross rates are derived strictly through verified financial mathematics using C# `decimal` (128-bit floating point with 28-29 significant digits):

1. **Direct USD Base (`USD / Quote`)**:
   $$\text{Rate} = R_{\text{Quote}}$$
   *Example:* $\text{USD/JPY} = R_{\text{JPY}} = 157.82$

2. **Inverse USD Quote (`Base / USD`)**:
   $$\text{Rate} = \frac{1}{R_{\text{Base}}}$$
   *Example:* $\text{EUR/USD} = \frac{1}{R_{\text{EUR}}} = \frac{1}{0.89686} = 1.1150$

3. **Cross Currency (`Base / Quote`, non-USD)**:
   $$\text{Rate} = \frac{R_{\text{Quote}}}{R_{\text{Base}}}$$
   *Example:* $\text{IDR/JPY} = \frac{R_{\text{JPY}}}{R_{\text{IDR}}} = \frac{157.82}{18139} = 0.0087006$

Precision is explicitly rounded to `config.Decimals` using `Math.Round(rate, config.Decimals, MidpointRounding.AwayFromZero)` only at presentation, preserving raw precision during computations.

---

## 3. Data Source Provenance & Availability Verification

Live API checks against both European Central Bank (via Frankfurter) and fallback providers confirmed 100% direct availability for all 6 pairs:

### 3.1 Primary Provider: European Central Bank (ECB via Frankfurter)
- **Base Endpoint**: `https://api.frankfurter.app/latest?from={Base}&to={Quote}`
- **Historical Endpoint**: `https://api.frankfurter.app/{startDate}..{endDate}?from={Base}&to={Quote}`
- **Provenance Characteristics**:
  - Official ECB reference rates are published once daily at approximately 16:00 CET on working days.
  - No synthetic intraday candles or tick data are fabricated.
  - The application clearly annotates the rate classification: `Daily Reference Rate (ECB Fix)`.
  - For the `1D` period selector, Frankfurter queries the last 5 calendar days to bridge weekends and European TARGET banking holidays, comparing the two most recent business-day ECB fixes to compute true 1-day net change.

### 3.2 Secondary Fallback Provider: Open Exchange Rates API
- **Endpoint**: `https://open.er-api.com/v6/latest/USD`
- **Fallback Trigger**: Activated automatically on HTTP timeout (10s), network failure, or API rate limit.
- **Attribution**: Clearly labeled `Open Exchange Rates (Cross/Inverse Rate)` so operators have complete audit visibility into data origin.

---

## 4. Interactive Historical Crosshair & Tooltip Geometry

### 4.1 Nearest Observation Lookup Algorithm

To prevent fabricating interpolated rates between discrete daily ECB fix dates, the crosshair snaps strictly to the nearest valid historical observation:

```csharp
public static int FindNearestObservationIndex(double mouseX, double padLeft, double plotW, int pointCount)
{
    if (pointCount <= 0) return -1;
    if (pointCount == 1 || plotW <= 0) return 0;

    double t = (mouseX - padLeft) / plotW;
    t = Math.Clamp(t, 0.0, 1.0);
    int idx = (int)Math.Round(t * (pointCount - 1), MidpointRounding.AwayFromZero);
    return Math.Clamp(idx, 0, pointCount - 1);
}
```

### 4.2 Dynamic Zero-Clipping Tooltip Bounds

Tooltips are placed relative to the pointer coordinate with automatic edge flipping:

```csharp
public static Rect CalculateTooltipBounds(Point anchor, Size tooltipSize, Size containerBounds, double margin = 6)
{
    double w = tooltipSize.Width;
    double h = tooltipSize.Height;
    double maxW = containerBounds.Width;
    double maxH = containerBounds.Height;

    // Display to right of anchor; flip to left if exceeding right bound
    double x = anchor.X + 12;
    if (x + w > maxW - margin)
    {
        x = anchor.X - w - 12;
    }

    // Clamp X and Y safely inside margins
    x = Math.Clamp(x, margin, Math.Max(margin, maxW - w - margin));
    double y = Math.Clamp(anchor.Y - (h / 2), margin, Math.Max(margin, maxH - h - margin));

    return new Rect(x, y, w, h);
}
```

### 4.3 Data Provenance Tooltip Rendering

```
┌──────────────────────────────────────────────────┐
│ EUR/USD [Daily Reference Rate (ECB Fix)]        │
│ DATE: 2026-10-27                                 │
│ RATE: $ 1.1150                                   │
└──────────────────────────────────────────────────┘
```

When `pt.IsOhlc == true` (e.g. if loaded from high-frequency candlestick streams), the tooltip adapts dynamically to display:
```
┌──────────────────────────────────────────────────┐
│ BTC/USDT [Intraday Candle]                       │
│ DATE: 2026-10-27 15:00:00 UTC                    │
│ O: $ 80,410.00  H: $ 80,920.00                   │
│ L: $ 80,250.00  C: $ 80,650.00                   │
└──────────────────────────────────────────────────┘
```

---

## 5. Verification & Test Suite

### 5.1 Automated Unit Tests

A comprehensive test suite in `SignalGenerator.Desktop.Tests` verifies the FX monitor enhancements. All 125 tests passed with zero failures:

```
Test run for C:\Users\Administrator\Documents\SignalGenerator\desktop\tests\SignalGenerator.Desktop.Tests\bin\Debug\net8.0\SignalGenerator.Desktop.Tests.dll (.NETCoreApp,Version=v8.0)
Passed!  - Failed: 0, Passed: 125, Skipped: 0, Total: 125, Duration: 1 s
```

#### New Enhancement Test Coverage:
1. `FxPairConfig_DefaultPairs_ContainsRequiredPairs`: Verifies presence and decimal precision for `USD/IDR`, `USD/JPY`, `IDR/JPY`, `EUR/USD`, `GBP/USD`, `AUD/USD`.
2. `FxHistoricalSeriesDto_FormatsRatesCorrectly`: Tests decimal and prefix formatting across standard (`USD/IDR`, `EUR/USD`) and micro-rate (`IDR/JPY`) scales.
3. `NearestObservationLookup_SnapsCorrectlyWithoutInterpolation`: Verifies exact index snapping at left boundary, right boundary, midpoint, out-of-bounds, single point, and empty datasets.
4. `TooltipPositioning_ZeroClipping_WithinContainerBounds`: Validates horizontal flipping when near the right edge, vertical clamping near top and bottom edges, and zero coordinate clipping.
5. `CalculateCrossOrInverseRate_MathPrecision`: Verifies 28-digit precision for direct base, inverse quote, and cross-currency conversions against mathematical expectations.
6. `OverviewViewModel_SelectFxPairCommand_UpdatesSelectionAndTitle`: Verifies MVVM property notifications, title updates (`■ EUR/USD FX MONITOR`), and asynchronous reload triggering.
7. `FxLineChart_AxisFormatting_PreservesDecimalPrecision`: Verifies that axis formatting prevents micro-rates and fractional currencies from collapsing to zero or integer values.

### 5.2 Visual & Runtime Smoke Test Verification

Live desktop runtime captures confirmed expected visual appearance and operational behavior:

1. **USD/IDR Monitor (`alpha_meridian_fx_monitor_enhancement.png`)**:
   - Header: `■ USD/IDR FX MONITOR [USD/IDR v] // Rp 18,139.00 +1.26% (1M)`
   - Rate Type: `Daily Reference Rate (ECB Fix)`
   - Provider: `European Central Bank (ECB) via Frankfurter API`
   - Axis Price Ticks: `18162`, `18082`, `18002`, `17921`, `17841`

2. **EUR/USD Monitor (`alpha_meridian_fx_monitor_eurusd.png`)**:
   - Header: `■ EUR/USD FX MONITOR [EUR/USD v] // $ 1.1150 -2.22% (1M)`
   - Rate Conversion: `~$1.1150 USDT/EUR`
   - Axis Price Ticks: `1.1403`, `1.1340`, `1.1277`, `1.1213`, `1.1150`

3. **USD/JPY Monitor (`alpha_meridian_fx_monitor_usdjpy.png`)**:
   - Header: `■ USD/JPY FX MONITOR [USD/JPY v] // ¥ 157.82 +0.15% (1M)`
   - Rate Conversion: `~¥ 157.82 (1:1 USD Peg Est.)`
   - Axis Price Ticks: `158.60`, `158.15`, `157.70`, `157.25`, `156.80`

---

## 6. Safety & Operational Boundary Audit

| Invariant | Status | Audit Note |
| :--- | :---: | :--- |
| **Branch Isolation** | Verified | All changes isolated to `feature/avalonia-desktop`. `main` remains untouched. |
| **Python Trading Daemon** | Verified | Daemon process (PID 14864 on port 8000) untouched and running continuously. |
| **Execution Isolation** | Verified | No trading execution methods or order placement calls touched. |
| **Data Provenance** | Verified | Daily reference rates clearly distinguished from live/intraday prices; no synthetic ticks. |
| **Zero Secret Leakage** | Verified | No API keys, credentials, or secrets committed or logged. |
| **Chart Engine** | Verified | Vela evaluation kept separate; native Skia/Avalonia charting preserved. |

---

## 7. Deliverables & Modified Files

- `desktop/src/SignalGenerator.Desktop/Models/TerminalDtos.cs`: Added `FxPairConfig`, extended `FxDataPointDto`, `FxHistoricalSeriesDto`, `FxRateSnapshotDto`.
- `desktop/src/SignalGenerator.Desktop/Services/IFxRateService.cs`: Added multi-pair service interface definitions.
- `desktop/src/SignalGenerator.Desktop/Services/FxRateService.cs`: Implemented compound pair/period caching, multi-currency Frankfurter API queries, and cross/inverse rate fallback math.
- `desktop/src/SignalGenerator.Desktop/Controls/FxLineChart.cs`: Implemented interactive vertical/horizontal crosshairs, observation snapping, zero-clipping tooltip bounds, price and date axis badges, and dynamic axis price formatting.
- `desktop/src/SignalGenerator.Desktop/ViewModels/OverviewViewModel.cs`: Added `AvailableFxPairs`, `SelectedFxPair`, `FxMonitorTitle`, and `SelectFxPairCommand`.
- `desktop/src/SignalGenerator.Desktop/Views/OverviewView.axaml`: Bound pair selector ComboBox, dynamic title, rate type badges, and chart styled properties.
- `desktop/src/SignalGenerator.Desktop/App.axaml.cs`: Added `--fx-pair` command line argument handling.
- `desktop/tests/SignalGenerator.Desktop.Tests/FxMonitorEnhancementTests.cs`: Comprehensive 7-test suite for multi-pair configs, cross-rate math, snapping, tooltip geometry, and MVVM bindings.
- `desktop/tests/SignalGenerator.Desktop.Tests/OverviewViewModelTests.cs`: Updated mocks for `IFxRateService` multi-pair overloads.
- `desktop/tests/SignalGenerator.Desktop.Tests/MainViewModelTests.cs`: Updated mocks for `IFxRateService` multi-pair overloads.
- `desktop/reports/FX_MONITOR_ENHANCEMENT_REPORT.md`: Comprehensive engineering report.
