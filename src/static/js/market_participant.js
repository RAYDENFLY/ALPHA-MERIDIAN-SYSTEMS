(function () {
    async function loadMarketParticipant() {
        const state = document.getElementById('cotState');
        const table = document.getElementById('cotTable');
        const institutionalState = document.getElementById('institutionalState');
        const institutionalTable = document.getElementById('institutionalTable');
        if (!state && !institutionalState) return;
        const states = [state, institutionalState].filter(Boolean);
        const tables = [table, institutionalTable].filter(Boolean);
        states.forEach(el => { el.textContent = 'Loading CFTC research data…'; });
        state.textContent = 'Loading CFTC research data…';
        try {
            const response = await fetch('/api/research/data/cftc_cot_disaggregated?limit=200');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            const rows = payload.observations || [];
            if (!rows.length) {
                states.forEach(el => { el.textContent = 'UNAVAILABLE — no persisted CFTC observations are available.'; });
                tables.forEach(el => { el.innerHTML = ''; });
                return;
            }
            states.forEach(el => { el.textContent = `Source: CFTC · Dataset: ${payload.dataset_id} · Sync: ${payload.status || 'UNKNOWN'}`; });
            const markup = `<table class="min-w-full text-xs"><thead><tr class="text-left text-slate-500"><th class="p-2">Report date</th><th class="p-2">Market</th><th class="p-2">Category</th><th class="p-2">Net position</th></tr></thead><tbody>${rows.map(r => `<tr class="border-t border-slate-800"><td class="p-2 text-slate-300">${r.observation_timestamp || '—'}</td><td class="p-2 text-slate-300">${r.market_id || '—'}</td><td class="p-2 text-slate-300">${r.symbol || '—'}</td><td class="p-2 text-slate-200">${r.value ?? '—'} ${r.unit || ''}</td></tr>`).join('')}</tbody></table>`;
            tables.forEach(el => { el.innerHTML = markup; });
        } catch (error) {
            states.forEach(el => { el.textContent = `Unable to load CFTC research data: ${error.message}`; });
            tables.forEach(el => { el.innerHTML = ''; });
        }
    }
    window.loadMarketParticipant = loadMarketParticipant;
    const originalSwitchTab = window.switchTab;
    window.switchTab = function (tabId) {
        const result = originalSwitchTab.apply(this, arguments);
        if (tabId === 'market_participant' || tabId === 'cot' || tabId === 'institutional_positioning') loadMarketParticipant();
        return result;
    };
})();
