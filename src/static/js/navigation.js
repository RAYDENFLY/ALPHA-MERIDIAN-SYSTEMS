// =========================================================================
// TAB NAVIGATION & ROUTING COORDINATOR
// =========================================================================
        function switchTab(tabId) {
            ['setups', 'backtest', 'watchlist', 'journal', 'alerts', 'model_b', 'liquidity_intel', 'sweep_chart'].forEach(t => {
                const el = document.getElementById(`tab-${t}`);
                if (el) el.classList.add('hidden');
                const btn = document.getElementById(`tabBtn-${t}`);
                if (btn) btn.className = 'pb-3 border-b-2 border-transparent text-slate-400 hover:text-slate-200 flex items-center gap-2 whitespace-nowrap';
            });
            const activeTab = document.getElementById(`tab-${tabId}`);
            if (activeTab) activeTab.classList.remove('hidden');
            const activeBtn = document.getElementById(`tabBtn-${tabId}`);
            if (activeBtn) activeBtn.className = 'pb-3 border-b-2 border-indigo-500 text-indigo-400 font-semibold flex items-center gap-2 whitespace-nowrap';

            if (tabId === 'watchlist') loadWatchlist();
            if (tabId === 'journal') loadJournal();
            if (tabId === 'backtest') loadBacktest();
            if (tabId === 'alerts') loadAlerts();
            if (tabId === 'model_b') loadModelB();
            if (tabId === 'liquidity_intel') {
                loadLiquidityChartSymbols();
                loadLiquidityIntel();
            }
            if (tabId === 'sweep_chart') initOrRefreshLiquidityChart();
        }

        // =========================================================================