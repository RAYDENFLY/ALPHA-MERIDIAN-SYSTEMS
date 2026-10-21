// =========================================================================
// TAB NAVIGATION & ROUTING COORDINATOR (MUTUALLY EXCLUSIVE & RESILIENT)
// =========================================================================

let currentNavCategory = 'all';

function setNavCategory(category) {
    currentNavCategory = category || 'all';
    const categories = ['all', 'exec', 'models', 'intel'];
    
    categories.forEach(cat => {
        const btn = document.getElementById(`navCat-${cat}`);
        if (btn) {
            if (cat === currentNavCategory) {
                btn.className = 'px-2.5 py-1 text-xs font-semibold rounded bg-blue-600 text-white shadow-sm transition';
            } else {
                btn.className = 'px-2.5 py-1 text-xs font-medium rounded bg-[#131929] hover:bg-slate-800 text-slate-300 border border-slate-700/60 transition';
            }
        }
    });

    const strip = document.getElementById('mainTabStrip');
    if (!strip) return;

    const tabButtons = strip.querySelectorAll('button[data-nav-cat]');
    tabButtons.forEach(btn => {
        const cat = btn.getAttribute('data-nav-cat');
        if (currentNavCategory === 'all' || cat === currentNavCategory) {
            btn.classList.remove('hidden');
        } else {
            btn.classList.add('hidden');
        }
    });
}

const ALL_KNOWN_TABS = [
    'setups',
    'backtest',
    'watchlist',
    'journal',
    'alerts',
    'model_b',
    'model_d',
    'liquidity_intel',
    'sweep_chart',
    'demand_zone',
    'ai_analyst'
];

function switchTab(tabId) {
    if (!tabId) tabId = 'setups';

    // 1. Strictly enforce mutual exclusivity: Hide ALL tab containers in the DOM
    const allTabPanels = document.querySelectorAll('div[id^="tab-"]');
    allTabPanels.forEach(panel => {
        panel.classList.add('hidden');
    });

    // Also explicitly ensure all registered known tabs are hidden as fallback
    ALL_KNOWN_TABS.forEach(t => {
        const el = document.getElementById(`tab-${t}`);
        if (el) el.classList.add('hidden');
    });

    // 2. Reset all tab navigation buttons
    const allTabButtons = document.querySelectorAll('button[id^="tabBtn-"]');
    allTabButtons.forEach(btn => {
        btn.classList.remove('border-indigo-500', 'text-indigo-400', 'font-semibold', 'bg-blue-600/20', 'border-blue-500/60', 'text-white');
        btn.classList.add('border-transparent', 'text-slate-400');
    });

    // 3. Exclusively display active tab panel
    const activeTab = document.getElementById(`tab-${tabId}`);
    if (activeTab) {
        activeTab.classList.remove('hidden');
    }

    // 4. Highlight active button and handle category strip scrolling
    const activeBtn = document.getElementById(`tabBtn-${tabId}`);
    if (activeBtn) {
        const tabCat = activeBtn.getAttribute('data-nav-cat');
        if (tabCat && currentNavCategory !== 'all' && currentNavCategory !== tabCat) {
            setNavCategory('all');
        }

        activeBtn.classList.remove('border-transparent', 'text-slate-400');
        activeBtn.classList.add('border-indigo-500', 'text-indigo-400', 'font-semibold', 'bg-blue-600/20', 'border-blue-500/60', 'text-white');

        try {
            activeBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        } catch (e) {
            // Ignore scroll errors on older browsers
        }
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

    // 6. Trigger active tab data loader
    if (tabId === 'setups' && typeof fetchSetups === 'function') fetchSetups();
    if (tabId === 'watchlist' && typeof loadWatchlist === 'function') loadWatchlist();
    if (tabId === 'journal' && typeof loadJournal === 'function') loadJournal();
    if (tabId === 'backtest' && typeof loadBacktest === 'function') loadBacktest();
    if (tabId === 'alerts' && typeof loadAlerts === 'function') loadAlerts();
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
    setNavCategory('all');
});
