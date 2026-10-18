// =========================================================================
// WATCHLIST & PAIRS CONTROLS
// =========================================================================
        async function loadWatchlist() {
            try {
                const res = await fetch('/api/symbols');
                const symbols = await res.json();
                const tbody = document.getElementById('watchlistTableBody');
                tbody.innerHTML = symbols.map(s => `
                    <tr class="hover:bg-slate-800/50">
                        <td class="px-4 py-3 font-bold text-white">${s.symbol}</td>
                        <td class="px-4 py-3 font-mono">$${s.min_notional.toFixed(2)}</td>
                        <td class="px-4 py-3 font-mono">${s.step_size}</td>
                        <td class="px-4 py-3 font-mono">${s.tick_size}</td>
                        <td class="px-4 py-3">
                            <span class="px-2 py-0.5 text-xs rounded ${s.enabled ? 'badge-green' : 'badge-red'}">
                                ${s.enabled ? 'ENABLED' : 'DISABLED'}
                            </span>
                        </td>
                        <td class="px-4 py-3 text-right">
                            <button onclick="toggleSymbol('${s.symbol}', ${!s.enabled})" class="px-3 py-1 text-xs rounded ${s.enabled ? 'bg-red-800/80 hover:bg-red-700 text-red-200' : 'bg-emerald-800/80 hover:bg-emerald-700 text-emerald-200'}">
                                ${s.enabled ? 'Disable' : 'Enable'}
                            </button>
                        </td>
                    </tr>
                `).join('');
            } catch (err) {
                console.error("Watchlist error:", err);
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
