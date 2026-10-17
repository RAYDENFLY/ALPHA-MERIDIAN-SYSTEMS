"""Automated regression test suite for Dashboard HTML & JavaScript integrity.

Verifies:
1. JavaScript syntax validity of embedded script in dashboard.html (via Node VM).
2. All functions referenced in HTML event handlers (onclick, oninput, onchange) are defined.
3. Key user interaction controls and buttons exist in DOM.
4. GET / endpoint serves valid HTML with embedded script.
5. No broken local script imports or missing assets.
"""

from pathlib import Path
import re
import subprocess
import pytest
from fastapi.testclient import TestClient

from src.api.app import app


DASHBOARD_PATH = Path(__file__).parent.parent / "src" / "api" / "dashboard.html"


def get_embedded_javascript(html_content: str) -> str:
    """Extract embedded JavaScript from the dashboard HTML file."""
    start_tag = "<script>"
    end_tag = "</script>"
    start_idx = html_content.rfind(start_tag)
    end_idx = html_content.rfind(end_tag)
    assert start_idx != -1 and end_idx != -1, "Could not locate main <script> tag in dashboard.html"
    return html_content[start_idx + len(start_tag):end_idx]


class TestDashboardJavaScriptSyntax:
    """Proves that dashboard JavaScript compiles without syntax errors."""

    def test_dashboard_file_exists(self):
        assert DASHBOARD_PATH.exists(), f"Dashboard HTML not found at {DASHBOARD_PATH}"

    def test_javascript_compiles_in_node(self):
        """Validates that Node.js VM can parse and compile the dashboard script without syntax errors."""
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        js_code = get_embedded_javascript(html)

        node_script = """
        const vm = require('vm');
        let code = '';
        process.stdin.setEncoding('utf8');
        process.stdin.on('data', chunk => { code += chunk; });
        process.stdin.on('end', () => {
            try {
                new vm.Script(code);
                console.log('COMPILED_OK');
            } catch (err) {
                console.error('SYNTAX_ERROR:', err.message);
                process.exit(1);
            }
        });
        """

        # Run node compile test via stdin with explicit utf-8 encoding
        proc = subprocess.run(
            ["node", "-e", node_script],
            input=js_code,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 0, f"Node syntax error in dashboard script: {proc.stderr}\n{proc.stdout}"
        assert "COMPILED_OK" in proc.stdout

    def test_all_html_event_handlers_are_defined(self):
        """Validates that every function called from an inline event handler exists in JavaScript."""
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        js_code = get_embedded_javascript(html)

        # Find all inline event handlers (onclick, oninput, onchange, etc.)
        raw_matches = re.findall(r'\bon[a-z]+=["\']([^"\']+)["\']', html)
        called_functions = set()
        for call_str in raw_matches:
            match = re.match(r'([a-zA-Z0-9_]+)\(', call_str.strip())
            if match:
                called_functions.add(match.group(1))

        # Expected core interaction functions
        expected_core = {
            "switchTab",
            "setStatusFilter",
            "handleSearchInput",
            "clearSearch",
            "handleSortChange",
            "triggerScan",
            "toggleAccordion",
            "toggleSymbol",
            "loadBacktest",
            "dismissNotice",
        }
        for fn in expected_core:
            assert fn in called_functions, f"Expected event handler {fn}() not found in HTML"

        # Verify each called function is defined in js_code
        for fn in called_functions:
            pattern = rf'\b(?:function\s+{fn}|async\s+function\s+{fn}|{fn}\s*=\s*(?:function|\())\b'
            assert re.search(pattern, js_code) is not None, (
                f"Function '{fn}()' is invoked from an HTML event handler but is NOT defined in dashboard JavaScript!"
            )

    def test_key_interaction_elements_in_html(self):
        """Verifies critical UI controls exist and Pause Scanner button is completely removed."""
        html = DASHBOARD_PATH.read_text(encoding="utf-8")

        # Explicitly verify Pause Scanner button has been removed from UI
        assert 'id="scannerToggleBtn"' not in html, "Pause Scanner button id='scannerToggleBtn' should be removed"
        assert "Pause Scanner" not in html, "Text 'Pause Scanner' should be removed from dashboard UI"

        required_ids = [
            "scanBtn",             # Scan Watchlist Now button
            "symbolSearchInput",   # Symbol search input
            "clearSearchBtn",      # Clear search button
            "sortSelect",          # Sort select dropdown
            "setupsContainer",     # Setup cards container
            "watchlistTableBody",  # Watchlist table body
            "filter-ALL",          # Filter buttons
            "filter-READY",
            "filter-WATCHLIST",
            "filter-INFEASIBLE",
            "filter-INVALIDATED",
            "filter-STALE",
            "tab-setups",          # Navigation tabs
            "tab-watchlist",
            "tab-backtest",
            "tab-journal",
        ]

        for elem_id in required_ids:
            assert f'id="{elem_id}"' in html, f"Required UI element with id='{elem_id}' is missing from dashboard HTML"

    def test_no_broken_local_scripts(self):
        """Ensures dashboard only references verified external CDNs and no missing local script files."""
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        script_srcs = re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', html)
        for src in script_srcs:
            assert src.startswith("https://"), f"Suspicious or broken local script src found: {src}"

    def test_root_endpoint_serves_valid_dashboard(self):
        """Tests that GET / returns HTTP 200 with the complete HTML document."""
        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "SMC-SNR ML Perpetual Setup Scanner" in response.text
        assert "switchTab" in response.text
        assert "setStatusFilter" in response.text
