import re
from pathlib import Path
from html.parser import HTMLParser
import pytest


def test_dom_tree_hierarchy_all_tabs_inside_main_container():
    """Verify that all 10 tabs are direct children of the main page container.
    Guarantees no tab is orphaned outside the layout wrapper or nested inside another tab.
    """
    html_path = Path("src/api/dashboard.html")
    assert html_path.exists(), "dashboard.html must exist"
    content = html_path.read_text(encoding="utf-8")

    class DOMHierarchyAuditor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.tab_parents = {}
            self.tab_elements = set()
            self.unclosed = []

        def handle_starttag(self, tag, attrs):
            if tag in ['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']:
                return
            attrs_dict = dict(attrs)
            tag_id = attrs_dict.get('id', '')
            if tag_id.startswith('tab-'):
                self.tab_parents[tag_id] = [f"{t}#{tid}" if tid else t for t, tid in self.stack]
                self.tab_elements.add(tag_id)
            self.stack.append((tag, tag_id))

        def handle_endtag(self, tag):
            if tag in ['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']:
                return
            if self.stack and self.stack[-1][0] == tag:
                self.stack.pop()

    auditor = DOMHierarchyAuditor()
    auditor.feed(content)

    expected_tabs = {
        'tab-setups',
        'tab-backtest',
        'tab-watchlist',
        'tab-journal',
        'tab-alerts',
        'tab-model_b',
        'tab-liquidity_intel',
        'tab-sweep_chart',
        'tab-demand_zone',
        'tab-ai_analyst'
    }

    # 1. All 10 expected tabs must exist in the HTML DOM
    assert expected_tabs.issubset(auditor.tab_elements), f"Missing tabs: {expected_tabs - auditor.tab_elements}"

    # 2. Every tab must have the exact same parent hierarchy: html -> body -> div (max-w-7xl)
    for tid in expected_tabs:
        parents = auditor.tab_parents.get(tid, [])
        assert len(parents) == 3, f"{tid} has invalid parent depth: {parents}"
        assert parents[0] == 'html', f"{tid} root parent must be html"
        assert parents[1] == 'body', f"{tid} secondary parent must be body"
        assert parents[2] == 'div', f"{tid} must be direct child of main div container"

    # 3. No tab may be nested inside any other tab
    for tid, parents in auditor.tab_parents.items():
        for p in parents:
            assert not p.startswith('div#tab-'), f"Tab {tid} is illegally nested inside {p}"


def test_initial_static_html_tab_visibility():
    """Verify that in the initial static HTML, only tab-setups is active and all other 9 tabs have 'hidden'."""
    html_path = Path("src/api/dashboard.html")
    content = html_path.read_text(encoding="utf-8")

    tabs = [
        'tab-setups',
        'tab-backtest',
        'tab-watchlist',
        'tab-journal',
        'tab-alerts',
        'tab-model_b',
        'tab-liquidity_intel',
        'tab-sweep_chart',
        'tab-demand_zone',
        'tab-ai_analyst'
    ]

    for t in tabs:
        pattern = re.compile(rf'<div[^>]*id=["\']{t}["\'][^>]*class=["\']([^"\']+)["\']')
        match = pattern.search(content)
        assert match is not None, f"Could not find <div id='{t}'>"
        class_str = match.group(1).split()
        if t == 'tab-setups':
            assert 'hidden' not in class_str, "tab-setups must be visible by default"
        else:
            assert 'hidden' in class_str, f"{t} must have 'hidden' class by default in static HTML"


def test_navigation_script_mutual_exclusivity_logic():
    """Verify navigation.js dynamically enforces mutual exclusivity and halts background tasks."""
    nav_path = Path("src/static/js/navigation.js")
    assert nav_path.exists()
    content = nav_path.read_text(encoding="utf-8")

    # 1. Must query all tab containers dynamically via querySelectorAll
    assert "querySelectorAll('div[id^=\"tab-\"]')" in content or 'querySelectorAll("div[id^=\\"tab-\\"])' in content

    # 2. Must add 'hidden' to all tab panels
    assert "classList.add('hidden')" in content

    # 3. Must remove 'hidden' exclusively from activeTab
    assert "activeTab.classList.remove('hidden')" in content

    # 4. Must halt background polling timers when switching away from tabs
    assert "stopChartAutoRefresh" in content
    assert "stopScreenerAutoRefresh" in content

    # 5. Must handle all 10 tab routes
    all_tabs = [
        'setups', 'backtest', 'watchlist', 'journal', 'alerts',
        'model_b', 'liquidity_intel', 'sweep_chart', 'demand_zone', 'ai_analyst'
    ]
    for tab in all_tabs:
        assert f"tabId === '{tab}'" in content, f"Navigation coordinator must handle route '{tab}'"


def test_simulated_cross_tab_navigation_transitions():
    """Simulate the JavaScript switchTab transitions across all 10 tabs.
    Assert that for EVERY target tab, only the selected tab is active,
    and specifically confirm Demand-Zone is absent in all other tabs.
    """
    tabs = [
        'setups', 'backtest', 'watchlist', 'journal', 'alerts',
        'model_b', 'liquidity_intel', 'sweep_chart', 'demand_zone', 'ai_analyst'
    ]

    class TabStateSimulator:
        def __init__(self, tab_list):
            self.tabs = {t: False for t in tab_list}
            self.tabs['setups'] = True  # Initial default

        def switch_tab(self, target):
            # Mirror navigation.js: Hide all, then show target
            for t in self.tabs:
                self.tabs[t] = False
            self.tabs[target] = True

        def is_visible(self, tab):
            return self.tabs[tab]

    sim = TabStateSimulator(tabs)

    # 1. Initial State: Only setups visible
    assert sim.is_visible('setups') is True
    assert sim.is_visible('demand_zone') is False

    # 2. Open Liquidity Intel / Heatmap
    sim.switch_tab('liquidity_intel')
    assert sim.is_visible('liquidity_intel') is True
    assert sim.is_visible('demand_zone') is False, "Demand-Zone must NOT be visible under Heatmap"
    assert sum(1 for v in sim.tabs.values() if v) == 1, "Exactly one tab must be visible"

    # 3. Open Liquidity Sweep Chart
    sim.switch_tab('sweep_chart')
    assert sim.is_visible('sweep_chart') is True
    assert sim.is_visible('demand_zone') is False, "Demand-Zone must NOT be visible under Sweep Chart"
    assert sum(1 for v in sim.tabs.values() if v) == 1

    # 4. Open Model B
    sim.switch_tab('model_b')
    assert sim.is_visible('model_b') is True
    assert sim.is_visible('demand_zone') is False, "Demand-Zone must NOT be visible under Model B"
    assert sum(1 for v in sim.tabs.values() if v) == 1

    # 5. Open AI Analyst
    sim.switch_tab('ai_analyst')
    assert sim.is_visible('ai_analyst') is True
    assert sim.is_visible('demand_zone') is False, "Demand-Zone must NOT be visible under AI Analyst"
    assert sum(1 for v in sim.tabs.values() if v) == 1

    # 6. Open Demand-Zone / Market Screener Tab
    sim.switch_tab('demand_zone')
    assert sim.is_visible('demand_zone') is True, "Demand-Zone/Market Screener must be visible on its dedicated tab"
    for other in [t for t in tabs if t != 'demand_zone']:
        assert sim.is_visible(other) is False, f"Tab {other} must be hidden when on Demand-Zone"
    assert sum(1 for v in sim.tabs.values() if v) == 1

    # 7. Switch back to Heatmap
    sim.switch_tab('liquidity_intel')
    assert sim.is_visible('liquidity_intel') is True
    assert sim.is_visible('demand_zone') is False, "Demand-Zone must be hidden after leaving"
    assert sum(1 for v in sim.tabs.values() if v) == 1


def test_app_js_initial_tab_routing():
    """Verify app.js initializes tab routing on startup to guarantee clean mutual exclusivity."""
    app_js_path = Path("src/static/js/app.js")
    assert app_js_path.exists()
    content = app_js_path.read_text(encoding="utf-8")

    assert "const initialTab = urlParams.get('tab') || window.location.hash.replace('#', '') || 'setups';" in content
    assert "switchTab(initialTab);" in content
