import json
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SWEEP_CHART_JS = PROJECT_ROOT / "src" / "static" / "js" / "sweep_chart.js"
DASHBOARD_HTML = PROJECT_ROOT / "src" / "api" / "dashboard.html"
CHARTS_CSS = PROJECT_ROOT / "src" / "static" / "css" / "charts.css"


def _run_fullscreen_simulation() -> dict:
    """Execute the fullscreen workspace state machine, collapsible events drawer, resize observer,
    Escape handling, and symbol/timeframe switching inside Node.js VM.
    """
    harness = f"""
const fs = require('fs');
const vm = require('vm');

const elements = {{}};
function makeEl(id, initialW, initialH, classes = []) {{
    const classSet = new Set(classes);
    const attrs = {{}};
    const el = {{
        id,
        clientWidth: initialW,
        clientHeight: initialH,
        offsetHeight: initialH,
        style: {{}},
        textContent: '',
        innerHTML: '',
        value: 'BTCUSDT',
        options: [{{ value: 'BTCUSDT' }}, {{ value: 'ETHUSDT' }}],
        classList: {{
            add: (...cls) => cls.forEach(c => classSet.add(c)),
            remove: (...cls) => cls.forEach(c => classSet.delete(c)),
            contains: (c) => classSet.has(c),
            toArray: () => Array.from(classSet)
        }},
        setAttribute: (k, v) => {{ attrs[k] = String(v); }},
        getAttribute: (k) => attrs[k] ?? null,
        getBoundingClientRect: () => ({{
            width: el.clientWidth,
            height: el.clientHeight,
            left: 0,
            top: 0
        }}),
        addEventListener: () => {{}},
        getContext: () => ({{
            clearRect: () => {{}},
            save: () => {{}},
            restore: () => {{}},
            beginPath: () => {{}},
            moveTo: () => {{}},
            lineTo: () => {{}},
            stroke: () => {{}},
            fillText: () => {{}},
            arc: () => {{}},
            fill: () => {{}}
        }})
    }};
    elements[id] = el;
    return el;
}}

makeEl('chartWorkspace', 1200, 780, ['chart-workspace', 'space-y-5']);
makeEl('chartToolbar', 1200, 48);
makeEl('chartOverlaysToolbar', 1200, 36);
makeEl('chartVenueTelemetryBar', 1200, 32);
makeEl('chartViewportDensityBar', 1200, 32);
makeEl('chartVenueWarningBanner', 1200, 0, ['hidden']);
makeEl('chartMainSplitGrid', 1200, 580, ['grid', 'grid-cols-1', 'lg:grid-cols-4', 'gap-5']);
makeEl('chartPrimaryColumn', 900, 580, ['lg:col-span-3', 'space-y-3']);
makeEl('chartCardContainer', 900, 540);
makeEl('chartDrawingToolbar', 880, 34);
makeEl('chartCanvasStack', 880, 520);
makeEl('tvLightweightChartContainer', 880, 520);
makeEl('chartDrawingCanvasOverlay', 880, 520, ['pointer-events-none']);
makeEl('chartReplayBar', 900, 38);
makeEl('chartEventsDrawerColumn', 280, 580, ['space-y-4']);
makeEl('chartEventsPrimaryCard', 280, 500);
makeEl('chartEventsList', 260, 420);
makeEl('chartEventsDrawerToggleBtn', 110, 28);
makeEl('chartEventsDrawerToggleText', 80, 18);
makeEl('chartExpandEventsFloatingBtn', 95, 24, ['hidden']);
makeEl('chartFullscreenBtn', 100, 28);
makeEl('fullscreenExpandIcon', 14, 14);
makeEl('fullscreenCollapseIcon', 14, 14, ['hidden']);
makeEl('fullscreenBtnText', 70, 16).textContent = 'Fullscreen';
makeEl('chartExitFullscreenFloatingBtn', 130, 28, ['hidden']);
makeEl('chartSymbolInput', 90, 24);
makeEl('chartSymbolSelect', 90, 24);
makeEl('chartTf-15m', 40, 24);
makeEl('chartTf-1h', 40, 24);
makeEl('chartTf-4h', 40, 24);
makeEl('chartTf-1d', 40, 24);

const docListeners = {{}};
const winListeners = {{}};
let appliedChartOptions = [];
let logicalRangeState = {{ from: 380, to: 499 }};

const mockTvChart = {{
    applyOptions: (opts) => {{
        appliedChartOptions.push(opts);
        if (opts.width) elements['tvLightweightChartContainer'].clientWidth = opts.width;
        if (opts.height) {{
            elements['tvLightweightChartContainer'].clientHeight = opts.height;
            elements['chartCanvasStack'].clientHeight = opts.height;
        }}
    }},
    timeScale: () => ({{
        getVisibleLogicalRange: () => logicalRangeState,
        setVisibleLogicalRange: (r) => {{ logicalRangeState = r; }},
        subscribeVisibleLogicalRangeChange: () => {{}},
        timeToCoordinate: () => 100,
        logicalToCoordinate: () => 100
    }}),
    priceScale: () => ({{
        applyOptions: () => {{}}
    }}),
    addHistogramSeries: () => ({{ setData: () => {{}}, update: () => {{}} }}),
    addCandlestickSeries: () => ({{
        setData: () => {{}},
        update: () => {{}},
        setMarkers: () => {{}},
        createPriceLine: () => ({{}}),
        removePriceLine: () => {{}},
        priceToCoordinate: () => 200,
        coordinateToPrice: () => 85000
    }}),
    subscribeCrosshairMove: () => {{}}
}};

const sandbox = {{
    window: {{
        innerWidth: 1920,
        innerHeight: 1080,
        addEventListener: (evt, fn) => {{ (winListeners[evt] = winListeners[evt] || []).push(fn); }}
    }},
    document: {{
        fullscreenElement: null,
        webkitFullscreenElement: null,
        documentElement: {{ clientHeight: 1080, clientWidth: 1920 }},
        body: {{ style: {{ overflow: '' }} }},
        getElementById: (id) => elements[id] || null,
        addEventListener: (evt, fn) => {{ (docListeners[evt] = docListeners[evt] || []).push(fn); }},
        exitFullscreen: async () => {{
            sandbox.document.fullscreenElement = null;
            (docListeners['fullscreenchange'] || []).forEach(fn => fn());
        }}
    }},
    requestAnimationFrame: (fn) => fn(),
    setTimeout: (fn) => {{ fn(); return 1; }},
    clearTimeout: () => {{}},
    setInterval: () => 1,
    clearInterval: () => {{}},
    fetch: async () => ({{
        ok: true,
        json: async () => ({{
            symbol: sandbox.currentChartSymbol,
            timeframe: sandbox.currentChartTf,
            candles: Array.from({{ length: 500 }}, (_, i) => ({{
                time: 1700000000 + i * 3600,
                timestamp_ms: (1700000000 + i * 3600) * 1000,
                open: 80000 + i,
                high: 80100 + i,
                low: 79900 + i,
                close: 80050 + i,
                volume: 100
            }})),
            structural_levels: [],
            sweep_events: [],
            chart_markers: [],
            history_metadata: {{
                db_total_candles: 1880,
                db_earliest_timestamp_ms: 1630454400000,
                db_latest_timestamp_ms: 1792800000000,
                oldest_loaded_timestamp_ms: 1700000000000,
                newest_loaded_timestamp_ms: 1792800000000,
                has_more_before: true
            }},
            drawings: []
        }})
    }}),
    LightweightCharts: {{
        createChart: () => mockTvChart,
        LineStyle: {{ Solid: 0, Dashed: 2, Dotted: 3 }},
        PriceScaleMode: {{ Normal: 0, Logarithmic: 1 }},
        CrosshairMode: {{ Normal: 0 }}
    }},
    currentChartSymbol: 'BTCUSDT',
    currentChartTf: '1h',
    currentChartEventFilter: 'ALL',
    currentSelectedEventId: null,
    isReplayMode: false,
    replayCurrentIndex: null,
    replayTimer: null,
    replayPlaying: false,
    tvChart: mockTvChart,
    candleSeries: mockTvChart.addCandlestickSeries(),
    volumeSeries: mockTvChart.addHistogramSeries(),
    activePriceLines: [],
    chartLayersData: {{ candles: [] }},
    chartEventsData: [],
    isChartFullscreen: false,
    chartPriceScaleAuto: true,
    chartPriceScaleLog: false,
    activeOverlays: {{ structural: true, orderbook: true, liquidation: true }},
    formatPrice: (p) => Number(p).toFixed(2),
    escapeHtml: (s) => String(s || ''),
    console: console,
    Date: Date,
    Math: Math,
    Number: Number,
    Array: Array,
    Object: Object,
    Map: Map,
    Set: Set
}};

let requestedFullscreenElementId = null;
elements['chartWorkspace'].requestFullscreen = async () => {{
    requestedFullscreenElementId = elements['chartWorkspace'].id;
    sandbox.document.fullscreenElement = elements['chartWorkspace'];
    elements['chartWorkspace'].clientWidth = 1920;
    elements['chartWorkspace'].clientHeight = 1080;
    elements['chartCanvasStack'].clientWidth = 1410;
    (docListeners['fullscreenchange'] || []).forEach(fn => fn());
}};

vm.createContext(sandbox);
const jsCode = fs.readFileSync({json.dumps(str(SWEEP_CHART_JS))}, 'utf8');
vm.runInContext(jsCode, sandbox);

(async () => {{
    const report = {{}};

    // 1. Normal mode baseline
    sandbox.syncChartWorkspaceDimensions();
    report.normalMode = {{
        isChartFullscreen: vm.runInContext('isChartFullscreen', sandbox),
        width: appliedChartOptions[appliedChartOptions.length - 1].width,
        height: appliedChartOptions[appliedChartOptions.length - 1].height,
        btnText: elements['fullscreenBtnText'].textContent
    }};

    // 2. Enter native fullscreen via toggleChartFullscreen()
    await sandbox.toggleChartFullscreen();
    report.fullscreenEntered = {{
        requestedFullscreenElementId,
        isChartFullscreen: vm.runInContext('isChartFullscreen', sandbox),
        hasFullscreenActiveClass: elements['chartWorkspace'].classList.contains('fullscreen-active'),
        width: appliedChartOptions[appliedChartOptions.length - 1].width,
        height: appliedChartOptions[appliedChartOptions.length - 1].height,
        btnText: elements['fullscreenBtnText'].textContent,
        floatingExitHidden: elements['chartExitFullscreenFloatingBtn'].classList.contains('hidden')
    }};

    // 3. Collapse Events Panel while in fullscreen -> chart expands to full 4-column width
    elements['chartCanvasStack'].clientWidth = 1890;
    sandbox.toggleChartEventsDrawer();
    report.drawerCollapsedInFullscreen = {{
        isCollapsed: vm.runInContext('isChartEventsDrawerCollapsed', sandbox),
        drawerHidden: elements['chartEventsDrawerColumn'].classList.contains('hidden'),
        primaryColSpan4: elements['chartPrimaryColumn'].classList.contains('lg:col-span-4'),
        floatingShowEventsHidden: elements['chartExpandEventsFloatingBtn'].classList.contains('hidden'),
        chartWidthAfterCollapse: appliedChartOptions[appliedChartOptions.length - 1].width,
        chartHeightAfterCollapse: appliedChartOptions[appliedChartOptions.length - 1].height
    }};

    // 4. Reopen Events Panel while in fullscreen
    elements['chartCanvasStack'].clientWidth = 1410;
    sandbox.toggleChartEventsDrawer();
    report.drawerReopenedInFullscreen = {{
        isCollapsed: vm.runInContext('isChartEventsDrawerCollapsed', sandbox),
        drawerHidden: elements['chartEventsDrawerColumn'].classList.contains('hidden'),
        primaryColSpan3: elements['chartPrimaryColumn'].classList.contains('lg:col-span-3'),
        chartWidthAfterReopen: appliedChartOptions[appliedChartOptions.length - 1].width
    }};

    // 5. Browser resize while fullscreen (e.g. 2560x1440 monitor)
    sandbox.window.innerWidth = 2560;
    sandbox.window.innerHeight = 1440;
    elements['chartCanvasStack'].clientWidth = 1880;
    (winListeners['resize'] || []).forEach(fn => fn());
    report.resizedWhileFullscreen = {{
        width: appliedChartOptions[appliedChartOptions.length - 1].width,
        height: appliedChartOptions[appliedChartOptions.length - 1].height
    }};

    // 6. Symbol and Timeframe change while fullscreen -> remains fullscreen
    sandbox.setChartTimeframe('1d');
    sandbox.handleChartSymbolChange({{ target: {{ value: 'ETHUSDT' }} }});
    report.symbolTfChangeWhileFullscreen = {{
        isChartFullscreen: vm.runInContext('isChartFullscreen', sandbox),
        symbol: vm.runInContext('currentChartSymbol', sandbox),
        timeframe: vm.runInContext('currentChartTf', sandbox)
    }};

    // 7. Exit fullscreen via button
    elements['chartCanvasStack'].clientWidth = 880;
    await sandbox.toggleChartFullscreen();
    report.exitedViaButton = {{
        isChartFullscreen: vm.runInContext('isChartFullscreen', sandbox),
        hasFullscreenActiveClass: elements['chartWorkspace'].classList.contains('fullscreen-active'),
        width: appliedChartOptions[appliedChartOptions.length - 1].width,
        height: appliedChartOptions[appliedChartOptions.length - 1].height,
        btnText: elements['fullscreenBtnText'].textContent,
        floatingExitHidden: elements['chartExitFullscreenFloatingBtn'].classList.contains('hidden'),
        bodyOverflow: sandbox.document.body.style.overflow
    }};

    // 8. CSS fallback fullscreen + Exit via Escape key
    delete elements['chartWorkspace'].requestFullscreen;
    await sandbox.toggleChartFullscreen();
    const cssFsActive = vm.runInContext('isChartFullscreen', sandbox);
    (docListeners['keydown'] || []).forEach(fn => fn({{ key: 'Escape' }}));
    report.cssFallbackAndEscapeExit = {{
        enteredCssFullscreen: cssFsActive,
        exitedAfterEscape: !vm.runInContext('isChartFullscreen', sandbox),
        restoredHeight: appliedChartOptions[appliedChartOptions.length - 1].height
    }};

    process.stdout.write(JSON.stringify(report));
}})();
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Node simulation error: {proc.stderr.decode('utf-8', errors='replace')}")
    return json.loads(proc.stdout.decode("utf-8"))


class TestChartWorkspaceFullscreen:
    """Verification suite for True Chart-Workspace Fullscreen Mode."""

    def test_1_workspace_dom_hierarchy_contains_all_required_elements(self) -> None:
        """Verify #chartWorkspace is the outer workspace container and wraps all required controls and panels."""
        html = DASHBOARD_HTML.read_text(encoding="utf-8")

        ws_start = html.index('id="chartWorkspace"')
        next_tab = html.index('id="tab-demand_zone"')
        workspace_slice = html[ws_start:next_tab]

        required_ids_inside_workspace = [
            'id="chartToolbar"',                  # Chart title, feed status, symbol, timeframe, scale, fullscreen button
            'id="chartFeedBadge"',                # Feed status badge
            'id="chartSymbolInput"',              # Symbol input
            'id="chartSymbolSelect"',             # Symbol selector
            'id="chartTf-15m"',                   # Timeframe selector 15M
            'id="chartTf-1h"',                    # Timeframe selector 1H
            'id="chartTf-4h"',                    # Timeframe selector 4H
            'id="chartTf-1d"',                    # Timeframe selector 1D
            'id="chartEventsDrawerToggleBtn"',    # Collapsible events panel toggle button
            'id="chartFullscreenBtn"',            # Fullscreen button
            'id="chartOverlaysToolbar"',          # Overlay controls & venue selector
            'id="overlayStructuralCheck"',        # Structural liquidity toggle
            'id="overlay1DMacroCheck"',           # 1D Macro toggle
            'id="overlayOrderBookCheck"',         # Order book toggle
            'id="overlayLiquidationCheck"',       # Liquidation toggle
            'id="venueBtn-combined"',             # Venue selector
            'id="chartVenueTelemetryBar"',        # Active venue telemetry bar
            'id="chartViewportDensityBar"',       # Viewport range presets & 5-state density telemetry
            'id="chartMainSplitGrid"',            # Main chart + events side drawer grid
            'id="chartPrimaryColumn"',            # Primary chart column
            'id="chartCardContainer"',            # Chart card container
            'id="chartDrawingToolbar"',           # Drawing toolbar
            'id="chartCanvasStack"',              # Candlestick + drawing overlay stack
            'id="tvLightweightChartContainer"',   # Lightweight Charts mount
            'id="chartDrawingCanvasOverlay"',     # Drawing overlay canvas
            'id="chartReplayBar"',                # Chronological replay bar
            'id="chartEventsDrawerColumn"',       # Collapsible Sweep & Reclaim Events panel
            'id="chartCollapseEventsPanelBtn"',   # Drawer collapse button
            'id="chartEventsList"',               # Events list
        ]

        for req_id in required_ids_inside_workspace:
            assert req_id in workspace_slice, f"Missing {req_id} inside #chartWorkspace"

    def test_2_fullscreen_css_rules_prevent_clipping_and_use_flex_viewport_layout(self) -> None:
        """Verify charts.css defines flex column 100vw x 100dvh layout for both :fullscreen and .fullscreen-active."""
        css = CHARTS_CSS.read_text(encoding="utf-8")
        assert "#chartWorkspace:fullscreen" in css
        assert "#chartWorkspace.fullscreen-active" in css
        assert "height: 100dvh !important;" in css
        assert "flex-direction: column !important;" in css
        assert "#chartWorkspace:fullscreen #chartMainSplitGrid" in css
        assert "#chartWorkspace:fullscreen #chartCanvasStack" in css

    def test_3_interactive_fullscreen_lifecycle_and_resize_behavior(self) -> None:
        """Verify normal mode, fullscreen entry, collapsible events panel, browser resize, symbol/TF switch, and exits."""
        sim = _run_fullscreen_simulation()

        # 1. Normal mode baseline
        assert sim["normalMode"]["isChartFullscreen"] is False
        assert sim["normalMode"]["height"] == 520
        assert sim["normalMode"]["btnText"] == "Fullscreen"

        # 2. Fullscreen entry targets #chartWorkspace and expands height dynamically to fill 1080p viewport
        fs = sim["fullscreenEntered"]
        assert fs["requestedFullscreenElementId"] == "chartWorkspace"
        assert fs["isChartFullscreen"] is True
        assert fs["hasFullscreenActiveClass"] is True
        assert fs["width"] == 1410
        assert fs["height"] > 750  # Dynamic height for 1080p minus compact toolbars (~808px)
        assert fs["btnText"] == "Exit Fullscreen"
        assert fs["floatingExitHidden"] is False

        # 3. Collapsing Events Panel in fullscreen expands chart width to full workspace width
        collapsed = sim["drawerCollapsedInFullscreen"]
        assert collapsed["isCollapsed"] is True
        assert collapsed["drawerHidden"] is True
        assert collapsed["primaryColSpan4"] is True
        assert collapsed["floatingShowEventsHidden"] is False
        assert collapsed["chartWidthAfterCollapse"] == 1890

        # 4. Reopening Events Panel restores 3:1 split grid and resizes chart
        reopened = sim["drawerReopenedInFullscreen"]
        assert reopened["isCollapsed"] is False
        assert reopened["drawerHidden"] is False
        assert reopened["primaryColSpan3"] is True
        assert reopened["chartWidthAfterReopen"] == 1410

        # 5. Browser resize to 2560x1440 while fullscreen dynamically increases chart width & height
        resized = sim["resizedWhileFullscreen"]
        assert resized["width"] == 1880
        assert resized["height"] > fs["height"]  # 1440p height > 1080p height

        # 6. Symbol and timeframe changes while fullscreen stay inside fullscreen
        sym_tf = sim["symbolTfChangeWhileFullscreen"]
        assert sym_tf["isChartFullscreen"] is True
        assert sym_tf["symbol"] == "ETHUSDT"
        assert sym_tf["timeframe"] == "1d"

        # 7. Exiting fullscreen via button restores original 520px dashboard layout
        exited = sim["exitedViaButton"]
        assert exited["isChartFullscreen"] is False
        assert exited["hasFullscreenActiveClass"] is False
        assert exited["width"] == 880
        assert exited["height"] == 520
        assert exited["btnText"] == "Fullscreen"
        assert exited["floatingExitHidden"] is True
        assert exited["bodyOverflow"] == ""

        # 8. CSS fallback fullscreen and Escape key exit
        esc = sim["cssFallbackAndEscapeExit"]
        assert esc["enteredCssFullscreen"] is True
        assert esc["exitedAfterEscape"] is True
        assert esc["restoredHeight"] == 520
