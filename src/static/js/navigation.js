// =========================================================================
// SIDEBAR & TAB NAVIGATION COORDINATOR (EXPANDABLE CATEGORIES & MUTUALLY EXCLUSIVE)
// =========================================================================

const ALL_KNOWN_TABS = [
    'setups',
    'backtest',
    'watchlist',
    'journal',
    'alerts',
    'model_a',
    'model_b',
    'model_d',
    'liquidity_intel',
    'sweep_chart',
    'demand_zone',
    'ai_analyst'
];

// Mapping each tab to its category
const TAB_TO_CATEGORY_MAP = {
    'setups': 'overview',
    'watchlist': 'trading',
    'journal': 'trading',
    'alerts': 'trading',
    'liquidity_intel': 'intel',
    'sweep_chart': 'intel',
    'demand_zone': 'intel',
    'model_a': 'ai_research',
    'model_b': 'ai_research',
    'model_d': 'ai_research',
    'ai_analyst': 'ai_research',
    'backtest': 'exchange_data'
};

function toggleSidebarCollapse() {
    const sidebar = document.getElementById('sidebarNav');
    const shell = document.querySelector('.app-container');
    if (!sidebar) return;
    const isCollapsed = sidebar.classList.toggle('collapsed');
    if (shell) shell.classList.toggle('sidebar-collapsed', isCollapsed);
    if (!isCollapsed) resetCollapsedCategory();
    try {
        localStorage.setItem('sidebar_collapsed', isCollapsed ? '1' : '0');
    } catch (e) {}
}

function resetCollapsedCategory() {
    const sidebar = document.getElementById('sidebarNav');
    if (!sidebar) return;
    sidebar.classList.remove('category-selected');
    sidebar.querySelectorAll('.sidebar-category.selected').forEach(item => item.classList.remove('selected'));
}

function selectCollapsedCategory(categoryId) {
    const sidebar = document.getElementById('sidebarNav');
    const category = sidebar?.querySelector(`#catGroup-${categoryId}`)?.parentElement;
    if (!sidebar || !category || !sidebar.classList.contains('collapsed')) return;
    sidebar.classList.add('category-selected');
    sidebar.querySelectorAll('.sidebar-category.selected').forEach(item => item.classList.remove('selected'));
    category.classList.add('selected');
}

function ensureLivePositionsInMainContent() {
    const livePositions = document.getElementById('livePositionsSection');
    const mainContent = document.querySelector('.app-container .main-content');
    if (!livePositions || !mainContent || mainContent.contains(livePositions)) return;
    mainContent.insertBefore(livePositions, mainContent.firstElementChild);
}

function toggleMobileSidebar() {
    const sidebar = document.getElementById('sidebarNav');
    const backdrop = document.getElementById('sidebarMobileBackdrop');
    if (!sidebar) return;
    const isOpen = sidebar.classList.toggle('mobile-open');
    if (backdrop) {
        if (isOpen) backdrop.classList.remove('hidden');
        else backdrop.classList.add('hidden');
    }
}

function toggleCategoryAccordion(catId) {
    const group = document.getElementById(`catGroup-${catId}`);
    const chevron = document.getElementById(`catChevron-${catId}`);
    if (!group) return;
    const sidebar = document.getElementById('sidebarNav');
    const category = group.parentElement;
    if (sidebar?.classList.contains('collapsed')) {
        selectCollapsedCategory(catId);
        return;
    }
    const isHidden = group.classList.toggle('hidden');
    if (chevron) {
        if (isHidden) {
            chevron.classList.remove('rotate-180');
        } else {
            chevron.classList.add('rotate-180');
        }
    }
}

function setupSidebarCategories() {
    const sidebar = document.getElementById('sidebarNav');
    if (!sidebar) return;
    sidebar.querySelectorAll('.sidebar-cat-toggle').forEach(toggle => {
        const category = toggle.parentElement;
        const title = toggle.querySelector('.sidebar-cat-title')?.textContent?.trim();
        const categoryId = toggle.getAttribute('onclick')?.match(/'([^']+)'/)?.[1];
        category.classList.add('sidebar-category');
        if (title) toggle.setAttribute('title', title);
        if (categoryId) {
            toggle.dataset.categoryId = categoryId;
            // Use a direct listener for collapsed mode; this also works when
            // inline handlers are blocked by a stricter Content-Security-Policy.
            toggle.removeAttribute('onclick');
            toggle.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                toggleCategoryAccordion(categoryId);
            });
        }
    });
    const back = document.createElement('button');
    back.type = 'button';
    back.className = 'sidebar-collapsed-back sidebar-item';
    back.innerHTML = '<i class="fa-solid fa-arrow-left sidebar-icon" aria-hidden="true"></i><span class="sidebar-text">All categories</span>';
    back.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        resetCollapsedCategory();
    });
    sidebar.insertBefore(back, sidebar.firstElementChild?.nextElementSibling || sidebar.firstElementChild);
    document.addEventListener('click', event => {
        if (!sidebar.contains(event.target)) resetCollapsedCategory();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') resetCollapsedCategory();
    });
}

function switchTab(tabId) {
    if (!tabId) tabId = 'setups';

    // 1. Strictly enforce mutual exclusivity: Hide ALL tab containers in the DOM
    const allTabPanels = document.querySelectorAll('div[id^="tab-"]');
    allTabPanels.forEach(panel => {
        panel.classList.add('hidden');
    });

    ALL_KNOWN_TABS.forEach(t => {
        const el = document.getElementById(`tab-${t}`);
        if (el) el.classList.add('hidden');
    });

    // 2. Reset all sidebar tab buttons
    const allTabButtons = document.querySelectorAll('.sidebar-item');
    allTabButtons.forEach(btn => {
        btn.classList.remove('bg-blue-600/20', 'text-white', 'border-blue-500/60', 'font-semibold');
        btn.classList.add('text-slate-400', 'border-transparent');
    });

    // Also reset old top tab buttons if any still present
    const topTabBtns = document.querySelectorAll('button[id^="tabBtn-"]');
    topTabBtns.forEach(btn => {
        btn.classList.remove('border-indigo-500', 'text-indigo-400', 'font-semibold', 'bg-blue-600/20', 'border-blue-500/60', 'text-white');
        btn.classList.add('border-transparent', 'text-slate-400');
    });

    // 3. Exclusively display active tab panel
    const activeTab = document.getElementById(`tab-${tabId}`);
    if (activeTab) {
        activeTab.classList.remove('hidden');
    }

    // 4. Highlight active sidebar item and ensure its category accordion is expanded
    const activeSidebarBtn = document.getElementById(`sideBtn-${tabId}`);
    if (activeSidebarBtn) {
        activeSidebarBtn.classList.remove('text-slate-400', 'border-transparent');
        activeSidebarBtn.classList.add('bg-blue-600/20', 'text-white', 'border-blue-500/60', 'font-semibold');
    }

    const catId = TAB_TO_CATEGORY_MAP[tabId];
    if (catId) {
        const group = document.getElementById(`catGroup-${catId}`);
        const chevron = document.getElementById(`catChevron-${catId}`);
        if (group && group.classList.contains('hidden')) {
            group.classList.remove('hidden');
            if (chevron) chevron.classList.add('rotate-180');
        }
    }

    // Also highlight legacy top button if rendered
    const activeBtn = document.getElementById(`tabBtn-${tabId}`);
    if (activeBtn) {
        activeBtn.classList.remove('border-transparent', 'text-slate-400');
        activeBtn.classList.add('border-indigo-500', 'text-indigo-400', 'font-semibold', 'bg-blue-600/20', 'border-blue-500/60', 'text-white');
    }

    // Close mobile drawer if opened
    const sidebar = document.getElementById('sidebarNav');
    const backdrop = document.getElementById('sidebarMobileBackdrop');
    if (sidebar && sidebar.classList.contains('mobile-open')) {
        sidebar.classList.remove('mobile-open');
        if (backdrop) backdrop.classList.add('hidden');
    }

    // 5. Manage background polling loops to prevent duplicate API traffic
    if (tabId !== 'sweep_chart' && typeof stopChartAutoRefresh === 'function') {
        stopChartAutoRefresh();
    }
    if (tabId !== 'demand_zone' && typeof stopScreenerAutoRefresh === 'function') {
        stopScreenerAutoRefresh();
    }
    if (tabId !== 'liquidity_intel' && typeof stopLiquidityIntelAutoRefresh === 'function') {
        stopLiquidityIntelAutoRefresh();
    }
    if (tabId !== 'model_d' && typeof stopModelDAutoRefresh === 'function') {
        stopModelDAutoRefresh();
    }
    if (tabId !== 'model_a' && typeof stopModelAAutoRefresh === 'function') {
        stopModelAAutoRefresh();
    }

    // 6. Trigger active tab data loader
    if (tabId === 'setups' && typeof fetchSetups === 'function') fetchSetups();
    if (tabId === 'watchlist' && typeof loadWatchlist === 'function') loadWatchlist();
    if (tabId === 'journal' && typeof loadJournal === 'function') loadJournal();
    if (tabId === 'backtest' && typeof loadBacktest === 'function') loadBacktest();
    if (tabId === 'alerts' && typeof loadAlerts === 'function') loadAlerts();
    if (tabId === 'model_a' && typeof loadModelA === 'function') loadModelA();
    if (tabId === 'model_b' && typeof loadModelB === 'function') loadModelB();
    if (tabId === 'model_d' && typeof loadModelD === 'function') loadModelD();
    if (tabId === 'liquidity_intel') {
        if (typeof loadLiquidityChartSymbols === 'function') loadLiquidityChartSymbols();
        if (typeof startLiquidityIntelAutoRefresh === 'function') {
            startLiquidityIntelAutoRefresh();
        } else if (typeof loadLiquidityIntel === 'function') {
            loadLiquidityIntel();
        }
    }
    if (tabId === 'sweep_chart' && typeof initOrRefreshLiquidityChart === 'function') {
        initOrRefreshLiquidityChart();
    }
    if (tabId === 'demand_zone') {
        if (typeof loadMarketScreener === 'function') {
            loadMarketScreener();
        } else if (typeof loadDemandZoneScanner === 'function') {
            loadDemandZoneScanner();
        }
    }
    if (tabId === 'ai_analyst' && typeof loadAIAnalyst === 'function') {
        loadAIAnalyst();
    }

    // 7. Update browser history / URL hash without reload
    try {
        if (window.history && window.history.replaceState) {
            const url = new URL(window.location);
            url.searchParams.set('tab', tabId);
            window.history.replaceState({ tab: tabId }, '', url.toString());
        }
    } catch (e) {}

    // 8. Trigger i18n DOM translation pass for any newly visible elements
    if (typeof updateDOMTranslations === 'function') {
        updateDOMTranslations();
    }
}

// Initial navigation setup on load
document.addEventListener('DOMContentLoaded', () => {
    ensureLivePositionsInMainContent();
    setupSidebarCategories();

    try {
        const savedCollapsed = localStorage.getItem('sidebar_collapsed');
        if (savedCollapsed === '1') {
            const sidebar = document.getElementById('sidebarNav');
            const shell = document.querySelector('.app-container');
            if (sidebar) sidebar.classList.add('collapsed');
            if (shell) shell.classList.add('sidebar-collapsed');
        }
    } catch (e) {}

    // Parse URL parameter ?tab=...
    try {
        const urlParams = new URLSearchParams(window.location.search);
        const tabParam = urlParams.get('tab');
        if (tabParam && ALL_KNOWN_TABS.includes(tabParam)) {
            switchTab(tabParam);
        } else {
            switchTab('setups');
        }
    } catch (e) {
        switchTab('setups');
    }
});
