from pathlib import Path
import re


ROOT = Path("src")
HTML = ROOT / "api" / "dashboard.html"
CHART_JS = ROOT / "static" / "js" / "sweep_chart.js"


def test_chart_controls_and_panels_share_workspace():
    html = HTML.read_text(encoding="utf-8")
    start = html.index('id="chartWorkspace"')
    end = html.index('</div>', start)
    workspace = html[start:]
    assert 'id="chartToolbar"' in workspace
    assert 'id="tvLightweightChartContainer"' in workspace
    assert 'id="chartEventsList"' in workspace
    assert workspace.count('id="chartSymbolInput"') == 1
    assert workspace.count('id="chartTf-1h"') == 1
    assert workspace.count('id="chartFullscreenBtn"') == 1
    assert workspace.count('id="chartRefreshBtn"') == 1


def test_fullscreen_targets_workspace():
    js = CHART_JS.read_text(encoding="utf-8")
    assert "document.getElementById('chartWorkspace')" in js
    assert "document.getElementById('chartCardContainer')" not in js.split('function toggleChartFullscreen()', 1)[1].split('function applyCssFullscreen', 1)[0]


def test_chart_controls_keep_existing_handlers():
    html = HTML.read_text(encoding="utf-8")
    for handler in (
        "handleChartSymbolKeydown(event)",
        "handleChartSymbolInputChange(event)",
        "handleChartSymbolChange(event)",
        "setChartTimeframe('1h')",
        "togglePriceScaleAuto()",
        "togglePriceScaleLog()",
        "toggleChartFullscreen()",
        "loadLiquidityChartData()",
    ):
        assert handler in html
