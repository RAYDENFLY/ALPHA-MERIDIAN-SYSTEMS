# ALPHA MERIDIAN SYSTEMS — Phase 4C Delivery Report
## TradingView-Style Interactive Chart, Technical Indicators & Cumulative Order Book Depth

**Product:** ALPHA MERIDIAN SYSTEMS  
**Parent Entity:** AMS Capital  
**Date:** October 28, 2026  
**Target Branch:** `feature/avalonia-desktop`  
**Baseline Commit:** `3f63bee`  
**Architecture:** Native C# · .NET 8 LTS · Avalonia UI · MVVM Pattern  

---

## 1. Executive Summary

In Phase 4C, the **Liquidity & Order Flow Workstation** was upgraded into an institutional-grade financial analysis terminal. While Phase 4 resolved JSON parsing mismatches and Phase 4B audited runtime data provenance, Phase 4C completes the user interaction, technical analysis, and market microstructure visualization layers:

1. **TradingView-Style Chart Navigation**: Implemented cursor-anchored mouse wheel zooming (preserving the exact candle under the cursor), drag-to-pan navigation with pointer capture (`e.Pointer.Capture`), dynamic autoscale, and an autoscale/reset button.
2. **Technical Indicators Engine**: Implemented full-series recursive Exponential Moving Averages (EMA 20, 50, 100, 200) with SMA warm-up initialization, plus an integrated Stochastic Oscillator (5, 3, 3) sub-pane with horizontal guides (80, 50, 20) synchronized to the main chart's time axis.
3. **On-Chart Liquidity Wall Mapping**: Projected high-significance order book walls directly onto the candlestick price canvas as horizontal stepped rays with dodge-spaced price labels, preventing right-axis scale collisions.
4. **Cumulative Market Depth Chart Control**: Built a custom native Avalonia control (`MarketDepthChartControl`) that visualizes bid and ask order book steps, cumulative volume, mid-price center line, best bid/ask markers, and real-time spread telemetry.
5. **Rigorous Verification & Quality Assurance**: Created 16 new automated unit and integration tests across 3 dedicated test fixtures (bringing test suite total to **65 passing tests, 0 failures**). Conducted live runtime smoke testing with high-resolution window rendering capture.

---

## 2. System Integrity & Safety Audit

In accordance with institutional deployment protocols:

- **Git Isolation**: All work conducted exclusively on `feature/avalonia-desktop`. Zero commits or merges into `main`.
- **Live Trading Backend Untouched**: The live Uvicorn daemon (PID `14864`, port `8000`) was preserved in continuous operation without reload, restart, or signal interruption.
- **Zero Order Mutation**: Zero order submissions, modifications, or cancellations were executed. Read-only market data contracts were strictly utilized.
- **Copyright & Corporate Branding**: Preserved the exact institutional copyright footer:
  `© 2026 PT Authentic Media Services. Alpha Meridian Systems is a product of AMS Capital.`

---

## 3. Technical Implementation Details

### 3.1 Mathematical Formulations & Indicator Engine
Located in `desktop/src/SignalGenerator.Desktop/Indicators/TechnicalIndicators.cs`:

#### Exponential Moving Average (EMA)
The recursive formulation requires:
$$\alpha = \frac{2}{N + 1}$$
$$EMA_t = \alpha \cdot Close_t + (1 - \alpha) \cdot EMA_{t-1}$$
- **Warm-Up Initialization**: For $t < N - 1$, EMA is undefined (`null`). At index $t = N - 1$, the seed value is initialized as the Simple Moving Average:
  $$EMA_{N-1} = \frac{1}{N} \sum_{i=0}^{N-1} Close_i$$
- **Zoom/Pan Invariance**: The EMA series is pre-computed over the entire historical candle cache rather than the visible viewport, guaranteeing that zooming or panning never alters indicator values.
- **Configured Bands**:
  - **EMA 20** (Fast / Short-term): `#FFD700` (Gold)
  - **EMA 50** (Medium / Pullback Reference): `#FF9100` (Amber/Orange)
  - **EMA 100** (Intermediate Trend): `#00E5FF` (Cyan)
  - **EMA 200** (Institutional Baseline): `#E040FB` (Magenta/Purple)

#### Stochastic Oscillator (5, 3, 3)
- **Raw %K**:
  $$\%K_{raw} = \frac{Close_t - \min_{i=0..4}(Low_{t-i})}{\max_{i=0..4}(High_{t-i}) - \min_{i=0..4}(Low_{t-i})} \times 100$$
- If the 5-bar high equals the 5-bar low ($\Delta = 0$), the oscillator defaults neutrally to $50.0$.
- **Fast %K**: 3-period Simple Moving Average of $\%K_{raw}$.
- **Slow %D**: 3-period Simple Moving Average of Fast $\%K$.
- **Visual Sub-Pane**: Rendered directly beneath the price pane with overbought ($80$), neutral ($50$), and oversold ($20$) horizontal guides.

---

### 3.2 TradingView-Style Interaction Model
Located in `desktop/src/SignalGenerator.Desktop/Controls/CandlestickChartControl.cs`:

1. **Cursor-Anchored Zooming**:
   - On `PointerWheelChanged`, the mouse X coordinate is projected into the visible candle index space:
     $$r = \frac{X - X_{left}}{W_{plot}}$$
     $$\text{anchorIdx} = \text{startIndex} + r \cdot (\text{visibleBars} - 1)$$
   - New visible bar count is calculated based on scroll delta:
     $$\text{newVisibleBars} = \text{visibleBars} \times (1 - \delta \cdot 0.15)$$
   - New start index is re-anchored to ensure the candle under the cursor stays stationary:
     $$\text{newStartIndex} = \text{anchorIdx} - r \cdot (\text{newVisibleBars} - 1)$$
     $$\text{panOffset} = \text{newStartIndex} - (\text{totalBars} - \text{newVisibleBars})$$
2. **Smooth Drag-to-Pan**:
   - `OnPointerPressed` records the initial pointer X and initial `_panOffset`, capturing the pointer via `e.Pointer.Capture(this)`.
   - `OnPointerMoved` updates the pan offset proportionally to bar width:
     $$\Delta \text{bars} = \frac{X - X_{initial}}{\text{barWidth}}$$
     $$\text{_panOffset} = \text{initialPanOffset} - \Delta \text{bars}$$
   - Pointer is cleanly released on `OnPointerReleased` or `OnPointerCaptureLost`.
3. **Autoscale & Reset View**:
   - Reset command resets `_panOffset = 0` and restores default bar count ($80$ candles), snapping immediately to the live edge.
4. **Collision-Free Badge Placement**:
   - Candlestick structural level tags (Break of Structure / Change of Character) are drawn anchored to the left margin with subtle dotted horizontal guide lines, completely decoupling them from the right price scale and preventing text collisions.
5. **Synchronized Crosshair & Two-Row Telemetry Ribbon**:
   - Row 1: `T`, `O`, `H`, `L`, `C`, `VOL`, `CHG%`.
   - Row 2: Active cursor values for `EMA 20`, `EMA 50`, `EMA 100`, `EMA 200`, and `Stoch %K / %D`.

---

### 3.3 On-Chart Liquidity Wall Mapping
- Walls from the active order book (`/api/chart/liquidity_layers`) are projected across the candlestick chart.
- **Color Coding**: Bid walls in Neon Green (`#00E676`), Ask walls in Coral Red (`#FF5252`).
- **Significance Tiering**: Line stroke thickness scales with wall size ($1.5$ px to $2.5$ px).
- **Collision Avoidance**: Dynamic dodge-spacing algorithm groups adjacent wall labels within $16$ vertical pixels and offsets labels horizontally to preserve absolute readability.

---

### 3.4 Cumulative Market Depth Chart Control
Located in `desktop/src/SignalGenerator.Desktop/Controls/MarketDepthChartControl.cs`:

- Custom native Avalonia control using direct GPU-accelerated `DrawingContext`.
- Parses order book buckets from `TerminalDtos.cs` (`bids_buckets` and `asks_buckets`).
- Computes cumulative volume curves stepping from best bid outward to lowest bid, and best ask outward to highest ask.
- Renders semi-transparent filled step polygons (`#1800E676` for bids, `#18FF5252` for asks) and solid step borders (`#00E676` and `#FF5252`).
- Features a center vertical dashed white line for the mid-price, best bid/ask triangle glyphs, real-time spread telemetry badge, and interactive hover tooltip showing price, cumulative volume, and distance from mid in basis points.

---

## 4. Visual Runtime Verification

The application was launched with `--view LiquidityIntel` connected to live Aster DEX feeds. A full-window graphical screenshot was captured directly from the running desktop window handle.

### Key Observed Visual Verifications:
1. **Interactive Candlestick Chart**: Renders clear green/red candles on a deep black background.
2. **Four Color-Coded EMAs**: Gold (20), Amber (50), Cyan (100), and Purple (200) overlay curves trace price movement smoothly across the series.
3. **Stochastic Sub-Pane**: Displays cyan %K and orange %D oscillating between the horizontal reference lines (80, 50, 20) in exact time alignment with the candlestick chart.
4. **Liquidity Walls**: Red ask walls and green bid walls project horizontally from order book levels across the price canvas.
5. **Cumulative Depth Chart**: Rendered in the bottom pane with symmetric bid/ask curves, mid-price marker, and spread indicator.
6. **Telemetry & Wall Inspector**: Right-hand pane displays dense L2 order book buckets and live wall distances in basis points.

---

## 5. Automated Unit & Integration Test Suite

All 65 automated tests pass with zero errors and zero warnings:

```text
Test run for SignalGenerator.Desktop.Tests.dll (.NETCoreApp,Version=v8.0)
Passed!  - Failed: 0, Passed: 65, Skipped: 0, Total: 65, Duration: 1 s
```

### Test Fixture Breakdown:
- **`TechnicalIndicatorsTests.cs`** (7 tests):
  - `ComputeEma_NullOrEmptySeries_ReturnsEmpty`
  - `ComputeEma_InsufficientPeriod_ReturnsAllNulls`
  - `ComputeEma_MatchesRecursiveFormulaAndSmaSeed`
  - `ComputeEma_IsIndependentOfVisibleSubSlice_WhenComputedOnFullSeries`
  - `ComputeStochastic_CalculatesFastKAndSlowD_Correctly`
  - `ComputeStochastic_ConstantPrices_ReturnsNeutral50`
  - `ComputeStochastic_InsufficientData_ReturnsEmpty`
- **`CoordinateMappingAndChartTests.cs`** (4 tests):
  - `CursorAnchoredZoom_CalculatesCorrectPanOffsetAndVisibleBars`
  - `PanOffset_ClampsProperlyAtBoundaries`
  - `PriceToY_LinearMapping_MatchesExpectedCoordinates`
  - `TimeToX_MapsCandleIndicesUniformly`
- **`MarketDepthCalculationTests.cs`** (3 tests):
  - `ComputeCumulativeVolume_AscendingBidsAndDescendingAsks_MonotonicallyIncreases`
  - `DepthCoordinates_MapPriceAndVolumeToValidNormalizedBounds`
  - `SpreadBps_ComputesCorrectBasisPoints`
- **`LiquidityIntelViewModelTests.cs`** (2 integration tests):
  - `ApplyLiquidityLayers_PopulatesWallsAndCumulativeDepth`
  - `IndicatorVisibilityToggles_PersistStateCorrectly`
- **Existing Suite** (49 tests): All Overview, Journal, Screener, and Navigation tests continue passing.

---

## 6. Deliverable File Artifacts

| Category | File Path | Description |
| :--- | :--- | :--- |
| **Indicator Engine** | `desktop/src/SignalGenerator.Desktop/Indicators/TechnicalIndicators.cs` | High-performance recursive EMA and Stochastic 5,3,3 engine |
| **Depth Control** | `desktop/src/SignalGenerator.Desktop/Controls/MarketDepthChartControl.cs` | Custom step-curve cumulative market depth canvas control |
| **Candle Chart** | `desktop/src/SignalGenerator.Desktop/Controls/CandlestickChartControl.cs` | TradingView-style cursor zoom, drag pan, EMA/Stoch rendering, walls |
| **DTO Envelopes** | `desktop/src/SignalGenerator.Desktop/Models/TerminalDtos.cs` | Depth buckets, cumulative volumes, and wall payload mapping |
| **ViewModel** | `desktop/src/SignalGenerator.Desktop/ViewModels/LiquidityIntelViewModel.cs` | Indicator toggles, depth collections, reset view command |
| **View** | `desktop/src/SignalGenerator.Desktop/Views/LiquidityIntelView.axaml` | Upgraded Bloomberg-style workstation layout with indicator ribbon |
| **Unit Tests** | `desktop/tests/SignalGenerator.Desktop.Tests/TechnicalIndicatorsTests.cs` | Precision verification for EMA & Stochastic calculation |
| **Unit Tests** | `desktop/tests/SignalGenerator.Desktop.Tests/CoordinateMappingAndChartTests.cs` | Verification for cursor-anchored zoom & coordinate math |
| **Unit Tests** | `desktop/tests/SignalGenerator.Desktop.Tests/MarketDepthCalculationTests.cs` | Verification for cumulative order book and spread telemetry |

---

## 7. Conclusion & Next Steps

Phase 4C successfully delivers institutional TradingView-style chart navigation, multi-timeframe EMA trend indicators, Stochastic momentum analysis, and cumulative market depth visualization within the native Avalonia terminal. The solution maintains strict read-only operation and preserves the approved Bloomberg-inspired institutional aesthetic.
