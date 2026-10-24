import json
import subprocess
import urllib.request
from pathlib import Path

import pytest


BASE_URL = "http://127.0.0.1:8000"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SWEEP_CHART_JS = PROJECT_ROOT / "src" / "static" / "js" / "sweep_chart.js"
DASHBOARD_HTML = PROJECT_ROOT / "src" / "api" / "dashboard.html"


def _fetch_liquidity_layers(symbol: str = "BTCUSDT", timeframe: str = "1h", limit: int = 2000):
    url = (
        f"{BASE_URL}/api/chart/liquidity_layers"
        f"?symbol={symbol}&timeframe={timeframe}&limit={limit}&include_1d_overlay=true"
    )
    with urllib.request.urlopen(url, timeout=30) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def _run_js_viewport_engine(payload: dict, logical_range: dict, chart_height_px: int = 520, overlays: dict = None):
    """Execute the actual computeViewportFilteredOverlays function from sweep_chart.js in Node.js."""
    if overlays is None:
        overlays = {"structural": True, "macro_1d": True, "orderbook": True, "liquidation": True}

    harness = f"""
const fs = require('fs');
const vm = require('vm');

const sandbox = {{
    window: {{ innerHeight: 900, innerWidth: 1440 }},
    document: {{
        addEventListener: () => {{}},
        getElementById: () => null
    }},
    LightweightCharts: {{
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
    tvChart: null,
    candleSeries: null,
    volumeSeries: null,
    activePriceLines: [],
    chartLayersData: null,
    chartEventsData: [],
    isChartFullscreen: false,
    chartPriceScaleAuto: true,
    chartPriceScaleLog: false,
    activeOverlays: {json.dumps(overlays)},
    formatPrice: (p) => Number(p).toFixed(2),
    escapeHtml: (s) => String(s || ''),
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    setInterval: setInterval,
    clearInterval: clearInterval,
    Date: Date,
    Math: Math,
    Number: Number,
    Array: Array,
    Object: Object,
    Map: Map,
    Set: Set
}};

vm.createContext(sandbox);
const jsCode = fs.readFileSync({json.dumps(str(SWEEP_CHART_JS))}, 'utf8');
vm.runInContext(jsCode, sandbox);

const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const logicalRange = {json.dumps(logical_range)};
const heightPx = {int(chart_height_px)};

sandbox.storeHistoricalDataset('test_key', payload);
const result = sandbox.computeViewportFilteredOverlays(payload, logicalRange, heightPx, sandbox.activeOverlays);
const storeCounts = vm.runInContext('({{ candles: sweepChartStore.candles.length, structuralLevels: sweepChartStore.structuralLevels.length, sweepEvents: sweepChartStore.sweepEvents.length, chartMarkers: sweepChartStore.chartMarkers.length }})', sandbox);
result.storedCounts = storeCounts;
process.stdout.write(JSON.stringify(result));
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Node error: {proc.stderr.decode('utf-8', errors='replace')}")
    return json.loads(proc.stdout.decode("utf-8"))


class TestSweepChartDensityAndViewport:
    """Phase 1 - Phase 6 verification suite for Liquidity Sweep Chart density & viewport rendering."""

    @pytest.mark.parametrize("timeframe,min_candles", [
        ("15m", 200),
        ("1h", 1000),
        ("4h", 1000),
        ("1d", 200),
    ])
    def test_all_timeframes_preserve_full_historical_data_and_bound_viewport_density(
        self, timeframe: str, min_candles: int
    ):
        """Test 15M, 1H, 4H, 1D across Zoomed-In, Multi-Week, Multi-Month, and Full History views."""
        payload = _fetch_liquidity_layers("BTCUSDT", timeframe, 2000)
        total_candles = len(payload["candles"])
        total_levels = len(payload["structural_levels"])
        total_events = len(payload["sweep_events"])
        total_markers = len(payload["chart_markers"])

        assert total_candles >= min_candles
        assert total_levels > 0
        assert total_events > 0
        assert total_markers > 0

        viewports = {
            "zoomed_in": {"from": max(0, total_candles - 75), "to": total_candles - 1},
            "default_120": {"from": max(0, total_candles - 120), "to": total_candles - 1},
            "multi_week": {"from": max(0, total_candles - 360), "to": total_candles - 1},
            "multi_month": {"from": max(0, total_candles - 1000), "to": total_candles - 1},
            "full_history": {"from": 0, "to": total_candles - 1},
        }

        for vp_name, lrange in viewports.items():
            res = _run_js_viewport_engine(payload, lrange, chart_height_px=520)

            # 1. Zero historical data deletion: store retains 100% of API dataset
            assert res["storedCounts"]["candles"] == total_candles
            assert res["storedCounts"]["structuralLevels"] == total_levels
            assert res["storedCounts"]["sweepEvents"] == total_events
            assert res["storedCounts"]["chartMarkers"] == total_markers

            # 2. Right-axis label cap strictly respected (<= floor(520 / 42) = 12)
            tel = res["telemetry"]
            assert tel["maxRightAxisLabels"] == 12
            assert tel["renderedRightAxisLabels"] <= tel["maxRightAxisLabels"], (
                f"{timeframe} {vp_name} exceeded right-axis label cap"
            )
            axis_visible_lines = [l for l in res["visiblePriceLines"] if l.get("axisLabelVisible")]
            assert len(axis_visible_lines) == tel["renderedRightAxisLabels"]

            # 3. Minimum vertical price separation between right-axis labels (no collision)
            if len(axis_visible_lines) >= 2:
                prices_sorted = sorted(l["price"] for l in axis_visible_lines)
                vis_low, vis_high = tel["visiblePriceRange"]
                raw_span = max(vis_high - vis_low, prices_sorted[-1] * 0.008)
                min_gap = raw_span / (tel["maxRightAxisLabels"] * 0.95)
                for idx in range(1, len(prices_sorted)):
                    assert (prices_sorted[idx] - prices_sorted[idx - 1]) >= (min_gap * 0.98)

            # 4. Rendered markers are strictly bounded and chronologically sorted
            markers = res["visibleMarkers"]
            assert len(markers) <= 36, f"{timeframe} {vp_name} rendered too many markers: {len(markers)}"
            times = [m["time"] for m in markers]
            assert times == sorted(times), "Lightweight Charts requires markers sorted by timestamp"

            # 5. Never stack multiple markers on the same candle & side
            bar_side_keys = [(m["time"], m["position"]) for m in markers]
            assert len(bar_side_keys) == len(set(bar_side_keys)), (
                f"Duplicate stacked markers on same candle & position in {timeframe} {vp_name}"
            )

    def test_historical_vs_live_order_book_wall_distinction_and_historical_panning(self):
        """Verify live order-book walls are distinct from historical levels and hidden when panned far into history."""
        payload = _fetch_liquidity_layers("BTCUSDT", "1h", 2000)
        total_candles = len(payload["candles"])
        assert total_candles >= 1000

        # 1. At live edge (last 120 bars), live order-book walls within price range have category LIVE_WALL and [LIVE WALL] prefix
        live_res = _run_js_viewport_engine(
            payload,
            {"from": total_candles - 120, "to": total_candles - 1},
            chart_height_px=520,
        )
        for line in live_res["visiblePriceLines"]:
            if line["category"] == "LIVE_WALL":
                assert line["title"].startswith("[LIVE WALL]")
                assert line["lineStyle"] == "Solid"
                assert line["lineWidth"] >= 2
            elif line["category"] == "HIST_STRUCTURAL":
                assert line["title"].startswith("[HIST")
            elif line["category"] == "HTF_STRUCTURAL":
                assert line["title"].startswith("[1D HTF]")
            elif line["category"] == "EST_LIQUIDATION":
                assert line["title"].startswith("[EST LIQ]")

        # 2. When panned back into historical bars (e.g., bars 200..320, far from live edge),
        # live L2 order-book walls and live liquidation estimates are NOT projected onto historical bars
        hist_res = _run_js_viewport_engine(
            payload,
            {"from": 200, "to": 320},
            chart_height_px=520,
        )
        live_walls_in_hist = [l for l in hist_res["visiblePriceLines"] if l["category"] == "LIVE_WALL"]
        est_liq_in_hist = [l for l in hist_res["visiblePriceLines"] if l["category"] == "EST_LIQUIDATION"]
        assert len(live_walls_in_hist) == 0
        assert len(est_liq_in_hist) == 0

    def test_price_zone_deduplication_merges_overlapping_structural_levels(self):
        """Verify levels at nearly identical prices are clustered into a single price zone line with (+N zone)."""
        synthetic_payload = {
            "candles": [
                {"time": 1700000000 + i * 3600, "open": 80000, "high": 80500, "low": 79500, "close": 80100, "volume": 100}
                for i in range(120)
            ],
            "structural_levels": [
                {
                    "level_id": "lvl_1",
                    "level_type": "SWING_HIGH_BSL",
                    "price": 80400.0,
                    "status": "ACTIVE",
                    "source_label": "BSL",
                    "touch_count": 1,
                    "confirmation_timestamp_ms": 1700003600 * 1000,
                },
                {
                    "level_id": "lvl_2",
                    "level_type": "EQUAL_HIGHS",
                    "price": 80405.0,  # within 0.6 bps of 80400.0 -> should cluster!
                    "status": "ACTIVE",
                    "source_label": "EQH (2x)",
                    "touch_count": 2,
                    "confirmation_timestamp_ms": 1700007200 * 1000,
                },
                {
                    "level_id": "lvl_3",
                    "level_type": "SWING_HIGH_BSL",
                    "price": 80410.0,  # also within cluster tolerance
                    "status": "ACTIVE",
                    "source_label": "BSL",
                    "touch_count": 1,
                    "confirmation_timestamp_ms": 1700010800 * 1000,
                },
            ],
            "sweep_events": [],
            "chart_markers": [],
            "order_book_overlay": {"walls": []},
            "liquidation_overlay": {"clusters": []},
        }

        res = _run_js_viewport_engine(synthetic_payload, {"from": 0, "to": 119}, chart_height_px=520)
        # All 3 levels remain in storage
        assert res["storedCounts"]["structuralLevels"] == 3
        # Only 1 deduplicated line is rendered, prioritizing EQUAL_HIGHS and annotating (+2 zone)
        assert len(res["visiblePriceLines"]) == 1
        top_line = res["visiblePriceLines"][0]
        assert top_line["id"] == "lvl_2"
        assert top_line["mergedCount"] == 3
        assert "(+2 zone)" in top_line["title"]

    def test_dashboard_and_safety_invariants_intact(self):
        """Verify dashboard HTML has viewport density controls and paper-trading safety locks remain untouched."""
        html = DASHBOARD_HTML.read_text(encoding="utf-8")
        assert 'id="chartViewportDensityBar"' in html
        assert "setChartViewportPreset('zoomed_in')" in html
        assert "setChartViewportPreset('multi_week')" in html
        assert "setChartViewportPreset('multi_month')" in html
        assert "setChartViewportPreset('full_history')" in html

        with urllib.request.urlopen(f"{BASE_URL}/api/model_a/status", timeout=15) as resp:
            status_data = json.loads(resp.read().decode("utf-8"))
        assert status_data["strategy_status"]["mode"] == "PAPER"
        assert status_data["strategy_status"]["live_trading_locked"] is True
        assert status_data["strategy_status"]["paper_only"] is True
