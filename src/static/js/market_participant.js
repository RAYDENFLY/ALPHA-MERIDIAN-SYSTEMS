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
        if (state) state.textContent = 'Loading CFTC research data…';
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
            const freshness = payload.freshness || 'UNKNOWN';
            const observed = payload.latest_observation_timestamp || 'UNKNOWN';
            states.forEach(el => { el.textContent = `Source: CFTC · Dataset: ${payload.dataset_id} · Weekly / non-real-time · Freshness: ${freshness} · Observation: ${observed} · Sync: ${payload.status || 'UNKNOWN'}`; });
            const markup = `<table class="min-w-full text-xs"><thead><tr class="text-left text-slate-500"><th class="p-2">Report date</th><th class="p-2">Market</th><th class="p-2">Category</th><th class="p-2">Net position</th></tr></thead><tbody>${rows.map(r => `<tr class="border-t border-slate-800"><td class="p-2 text-slate-300">${r.observation_timestamp || '—'}</td><td class="p-2 text-slate-300">${r.market_id || '—'}</td><td class="p-2 text-slate-300">${r.symbol || '—'}</td><td class="p-2 text-slate-200">${r.value ?? '—'} ${r.unit || ''}</td></tr>`).join('')}</tbody></table>`;
            tables.forEach(el => { el.innerHTML = markup; });
        } catch (error) {
            states.forEach(el => { el.textContent = `Unable to load CFTC research data: ${error.message}`; });
            tables.forEach(el => { el.innerHTML = ''; });
        }
    }
    window.loadMarketParticipant = loadMarketParticipant;
    async function loadRetailPositioning() {
        const panel = document.getElementById('retailPositioningState');
        if (!panel) return;
        panel.innerHTML = '<span class="text-slate-400">Loading Bybit account-ratio data…</span>';
        try {
            const response = await fetch('/api/research/retail_positioning?symbol=BTCUSDT&period=1h&limit=24');
            const data = await response.json();
            if (!response.ok || data.status !== 'AVAILABLE') throw new Error(data.error || 'Retail positioning unavailable');
            const latest=data.observations[0];
            const observedAt = latest.observation_timestamp_ms ? new Date(latest.observation_timestamp_ms).toISOString() : 'unknown';
            panel.className = 'mt-5 p-4 rounded border border-slate-800 bg-slate-900/40 text-sm';
            panel.innerHTML = `<div><div class="text-xs text-slate-400">Bybit · Account ratio · ${data.period}</div><div class="mt-2 text-lg font-bold text-white">${(latest.long_short_account_ratio ?? 0).toFixed(2)} L/S</div><div class="text-xs text-slate-400">Long holders ${(latest.long_account_ratio*100).toFixed(2)}% · Short holders ${(latest.short_account_ratio*100).toFixed(2)}%</div><div class="text-[10px] text-slate-500 mt-2">Observed: ${observedAt} · Holder-count ratio only; not position size and not all crypto retail traders.</div></div>`;
        } catch (error) { panel.innerHTML = `<span class="text-amber-200">UNAVAILABLE — ${error.message}. No retail values are fabricated.</span>`; }
    }
    const originalSwitchTab = window.switchTab;
    window.switchTab = function (tabId) {
        const result = originalSwitchTab.apply(this, arguments);
        if (tabId === 'market_participant' || tabId === 'cot' || tabId === 'institutional_positioning') loadMarketParticipant();
        if (tabId === 'retail_sentiment') loadRetailPositioning();
        return result;
    };
})();
