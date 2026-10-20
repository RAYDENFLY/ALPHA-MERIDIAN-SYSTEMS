// =========================================================================
// WATCHLIST & PAIRS CONTROLS (PAGINATED & LOCALIZED)
// =========================================================================

let lastWatchlistSymbols = [];

async function loadWatchlist() {
    try {
        const res = await fetch('/api/symbols');
        if (!res.ok) return;
        lastWatchlistSymbols = await res.json() || [];
        renderWatchlistTable();
    } catch (err) {
        console.error("Watchlist error:", err);
    }
}

function renderWatchlistTable() {
    const tbody = document.getElementById('watchlistTableBody');
    if (!tbody) return;

    if (!lastWatchlistSymbols || lastWatchlistSymbols.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-8 text-center text-slate-500 font-mono text-xs">No symbols configured in watchlist.</td></tr>';
        const pEl = document.getElementById('watchlistPaginationContainer');
        if (pEl) pEl.innerHTML = '';
        return;
    }

    const pagedSymbols = (typeof paginateList === 'function')
        ? paginateList('watchlist', lastWatchlistSymbols)
        : lastWatchlistSymbols;

    const translate = typeof t === 'function' ? t : (k, fallback) => fallback;
    const enabledLabel = translate('stat_enabled', 'ENABLED');
    const disabledLabel = translate('stat_disabled', 'DISABLED');
    const disableBtnText = translate('btn_disable', 'Disable');
    const enableBtnText = translate('btn_enable', 'Enable');

    tbody.innerHTML = pagedSymbols.map(s => `
        <tr class="hover:bg-slate-800/50">
            <td class="px-4 py-3 font-bold text-white">${s.symbol}</td>
            <td class="px-4 py-3 font-mono">$${s.min_notional.toFixed(2)}</td>
            <td class="px-4 py-3 font-mono">${s.step_size}</td>
            <td class="px-4 py-3 font-mono">${s.tick_size}</td>
            <td class="px-4 py-3">
                <span class="px-2 py-0.5 text-xs rounded ${s.enabled ? 'badge-green' : 'badge-red'}">
                    ${s.enabled ? enabledLabel : disabledLabel}
                </span>
            </td>
            <td class="px-4 py-3 text-right">
                <button onclick="toggleSymbol('${s.symbol}', ${!s.enabled})" class="px-3 py-1 text-xs rounded ${s.enabled ? 'bg-red-800/80 hover:bg-red-700 text-red-200' : 'bg-emerald-800/80 hover:bg-emerald-700 text-emerald-200'}">
                    ${s.enabled ? disableBtnText : enableBtnText}
                </button>
            </td>
        </tr>
    `).join('');

    if (typeof renderPaginationControls === 'function') {
        renderPaginationControls('watchlist', 'watchlistPaginationContainer', () => {
            renderWatchlistTable();
        });
    }
}

async function toggleSymbol(symbol, enable) {
    try {
        await fetch(`/api/symbols/${symbol}/toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enable })
        });
        await loadWatchlist();
    } catch (err) {
        alert("Toggle error: " + err);
    }
}
