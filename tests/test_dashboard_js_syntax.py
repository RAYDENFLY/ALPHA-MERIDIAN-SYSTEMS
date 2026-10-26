"""Automated regression test suite for Dashboard HTML & JavaScript integrity.

Verifies:
1. JavaScript syntax validity of all modular scripts in src/static/js/ (via Node VM).
2. All functions referenced in HTML event handlers (onclick, oninput, onchange) are defined across modular scripts.
3. Key user interaction controls and buttons exist in DOM.
4. GET / endpoint serves valid HTML with modular scripts and stylesheets.
5. No broken local script or stylesheet imports (all exist on disk and serve HTTP 200).
6. External scripts use secure HTTPS CDNs.
"""

from pathlib import Path
import re
import subprocess
import pytest
from fastapi.testclient import TestClient

from src.api.app import app


DASHBOARD_PATH = Path(__file__).parent.parent / "src" / "api" / "dashboard.html"
STATIC_DIR = Path(__file__).parent.parent / "src" / "static"


def get_all_modular_javascript() -> str:
    """Concatenate all modular JavaScript files referenced in dashboard.html in load order."""
    html_content = DASHBOARD_PATH.read_text(encoding="utf-8")
    script_srcs = re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', html_content)
    local_scripts = [s for s in script_srcs if s.startswith("/static/")]
    
    combined_code = []
    for s in local_scripts:
        clean_s = s.split("?")[0]
        rel_path = clean_s.replace("/static/", "")
        file_path = STATIC_DIR / rel_path
        assert file_path.exists(), f"Script file missing on disk: {file_path}"
        combined_code.append(file_path.read_text(encoding="utf-8"))
    
    return "\n\n".join(combined_code)


class TestDashboardJavaScriptSyntax:
    """Proves that dashboard JavaScript compiles without syntax errors and assets are intact."""

    def test_dashboard_file_exists(self):
        assert DASHBOARD_PATH.exists(), f"Dashboard HTML not found at {DASHBOARD_PATH}"

    def test_javascript_compiles_in_node(self):
        """Validates that Node.js VM can parse and compile every modular script without syntax errors."""
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        script_srcs = re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', html)
        local_scripts = [s for s in script_srcs if s.startswith("/static/")]
        assert len(local_scripts) > 0, "No modular static scripts found in dashboard.html"

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

        # Validate each individual modular script file
        for s in local_scripts:
            clean_s = s.split("?")[0]
            rel_path = clean_s.replace("/static/", "")
            file_path = STATIC_DIR / rel_path
            code = file_path.read_text(encoding="utf-8")
            proc = subprocess.run(
                ["node", "-e", node_script],
                input=code,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            assert proc.returncode == 0, f"Node syntax error in {s}: {proc.stderr}\n{proc.stdout}"
            assert "COMPILED_OK" in proc.stdout

    def test_all_html_event_handlers_are_defined(self):
        """Validates that every function called from an inline event handler exists in modular JavaScript."""
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        js_code = get_all_modular_javascript()

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
            pattern = rf'\b(?:function\s+{fn}|async\s+function\s+{fn}|(?:window\.)?{fn}\s*=\s*(?:function|\())\b'
            assert re.search(pattern, js_code) is not None, (
                f"Function '{fn}()' is invoked from an HTML event handler but is NOT defined in modular JavaScript!"
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
        """Ensures dashboard references valid external CDNs and all local modular scripts and stylesheets exist and serve 200 OK."""
        html = DASHBOARD_PATH.read_text(encoding="utf-8")
        client = TestClient(app)

        # 1. Script checks
        script_srcs = re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', html)
        assert len(script_srcs) >= 14, "Expected modular script tags in dashboard HTML"
        for src in script_srcs:
            if src.startswith("/static/"):
                clean_src = src.split("?")[0]
                rel_path = clean_src.replace("/static/", "")
                file_path = STATIC_DIR / rel_path
                assert file_path.exists(), f"Local script file not found on disk: {file_path}"
                resp = client.get(src)
                assert resp.status_code == 200, f"Local script {src} returned HTTP {resp.status_code}"
            else:
                assert src.startswith("https://"), f"Suspicious non-HTTPS script src found: {src}"

        # 2. Stylesheet checks
        link_hrefs = re.findall(r'<link\s+[^>]*href=["\']([^"\']+)["\']', html)
        assert len(link_hrefs) >= 3, "Expected modular stylesheet links in dashboard HTML"
        for href in link_hrefs:
            if href.startswith("/static/"):
                clean_href = href.split("?")[0]
                rel_path = clean_href.replace("/static/", "")
                file_path = STATIC_DIR / rel_path
                assert file_path.exists(), f"Local stylesheet file not found on disk: {file_path}"
                resp = client.get(href)
                assert resp.status_code == 200, f"Local stylesheet {href} returned HTTP {resp.status_code}"
                assert "text/css" in resp.headers["content-type"]
            else:
                assert href.startswith("https://"), f"Suspicious non-HTTPS stylesheet href found: {href}"

    def test_root_endpoint_serves_valid_dashboard(self):
        """Tests that GET / returns HTTP 200 with the complete modular HTML document."""
        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "SMC-SNR ML Perpetual Setup Scanner" in response.text
        assert "switchTab" in response.text
        assert "setStatusFilter" in response.text
        assert "/static/css/base.css" in response.text
        assert "/static/js/app.js" in response.text
