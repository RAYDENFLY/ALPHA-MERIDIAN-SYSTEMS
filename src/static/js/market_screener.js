/**
 * Multi-Exchange Perpetual Futures Market Screener.
 * Inspired by Coinglass-style cross-exchange comparison tables.
 * Strictly READ-ONLY. No entry signals or order placement.
 */

let screenerCurrentPage = 1;
let screenerPageSize = 25;
let screenerSortBy = "volume_desc";
let screenerMinVolume = 1000000.0; // Default $1M quote turnover
let screenerMaxVolume = null;
let screenerMinOI = null;
let screenerMaxOI = null;
let screenerFundingDirection = "ALL";
let screenerMinFundingMag = null;
let screenerMaxSpread = null;
let screenerSearchQuery = "";
let screenerCrossListedOnly = false;
let screenerSelectedExchanges = ["asterdex", "binance", "bybit", "hyperliquid"];

let screenerDataCache = null;
let screenerExpandedRows = new Set();
let screenerAutoRefreshTimer = null;
let screenerIsLoading = false;

function initOrRefreshMarketScreener() {
    loadMarketScreenerData();
    startScreenerAutoRefresh();
}

// Alias for navigation compatibility
function loadDemandZoneScanner() {
    initOrRefreshMarketScreener();
}
function loadMarketScreener() {
    initOrRefreshMarketScreener();
}

function startScreenerAutoRefresh() {
    stopScreenerAutoRefresh();
    screenerAutoRefreshTimer = setInterval(() => {
        const tabEl = document.getElementById('tab-demand_zone');
        if (tabEl && !tabEl.classList.contains('hidden')) {
            loadMarketScreenerData(false, false);
        }
    }, 20000); // 20s polling
}

function stopScreenerAutoRefresh() {
    if (screenerAutoRefreshTimer) {
        clearInterval(screenerAutoRefreshTimer);
        screenerAutoRefreshTimer = null;
    }
}

async function loadMarketScreenerData(showSpinner = true, forceRefresh = false) {
    if (screenerIsLoading) return;
    screenerIsLoading = true;

    const spin = document.getElementById('screenerRefreshSpin');
    if (spin && showSpinner) spin.classList.add('animate-spin');

    try {
        const params = new URLSearchParams({
            page: screenerCurrentPage.toString(),
            page_size: screenerPageSize.toString(),
            sort_by: screenerSortBy,
            min_volume_usdt: screenerMinVolume.toString(),
        });

        if (screenerSearchQuery) params.append('search', screenerSearchQuery);
        if (screenerMaxVolume) params.append('max_volume_usdt', screenerMaxVolume.toString());
        if (screenerMinOI) params.append('min_oi_usdt', screenerMinOI.toString());
        if (screenerMaxOI) params.append('max_oi_usdt', screenerMaxOI.toString());
        if (screenerFundingDirection && screenerFundingDirection !== 'ALL') {
            params.append('funding_direction', screenerFundingDirection);
        }
        if (screenerMinFundingMag) params.append('min_funding_abs_pct', screenerMinFundingMag.toString());
        if (screenerMaxSpread) params.append('max_spread_bps', screenerMaxSpread.toString());
        if (screenerCrossListedOnly) params.append('cross_listed_only', 'true');
        if (screenerSelectedExchanges.length > 0 && screenerSelectedExchanges.length < 4) {
            params.append('exchanges', screenerSelectedExchanges.join(','));
        }
        if (forceRefresh) params.append('force_refresh', 'true');

        const res = await fetch(`/api/screener/markets?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);

        const json = await res.json();
        screenerDataCache = json;

        renderScreenerKPIs(json);
        renderScreenerTable(json);
        renderScreenerPagination(json);

    } catch (err) {
        console.error("Market Screener fetch error:", err);
        const tbody = document.getElementById('screenerTableBody');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="12" class="py-12 text-center text-rose-400 font-mono text-xs">
                        Failed to load market data: ${escapeHtml(err.message || String(err))}.
                        <button onclick="loadMarketScreenerData(true, true)" class="ml-2 underline hover:text-white">Retry</button>
                    </td>
                </tr>
            `;
        }
    } finally {
        screenerIsLoading = false;
        if (spin) spin.classList.remove('animate-spin');
    }
}

function renderScreenerKPIs(data) {
    const elUniverse = document.getElementById('screenerMetricUniverse');
    const elMatched = document.getElementById('screenerMetricMatched');
    const elVolume = document.getElementById('screenerMetricVolume');
    const elOI = document.getElementById('screenerMetricOI');
    const elUpdated = document.getElementById('screenerMetricLastUpdated');

    if (elUniverse) elUniverse.textContent = data.total_universe_count ? `${data.total_universe_count} Pairs` : '--';
    if (elMatched) elMatched.textContent = data.total_matched !== undefined ? `${data.total_matched} Matched` : '--';
    if (elVolume) elVolume.textContent = data.combined_volume_all_usdt ? `$${(data.combined_volume_all_usdt / 1e9).toFixed(2)}B` : '--';
    if (elOI) elOI.textContent = data.combined_oi_all_usdt ? `$${(data.combined_oi_all_usdt / 1e9).toFixed(2)}B` : '--';
    
    if (elUpdated && data.last_fetch_utc) {
        const d = new Date(data.last_fetch_utc);
        elUpdated.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    // Render venue status pills
    const tel = data.venues_telemetry || {};
    ['asterdex', 'binance', 'bybit', 'hyperliquid'].forEach(ex => {
        const v = tel[ex] || {};
        const badge = document.getElementById(`screenerVenuePill-${ex}`);
        const cntEl = document.getElementById(`screenerVenueCnt-${ex}`);
        const latEl = document.getElementById(`screenerVenueLat-${ex}`);

        if (badge) {
            if (v.status === 'AVAILABLE') {
                badge.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono bg-slate-900 border border-slate-700/80 text-slate-300';
            } else {
                badge.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono bg-rose-950/60 border border-rose-800 text-rose-300';
            }
        }
        if (cntEl) cntEl.textContent = v.total_contracts ? `${v.total_contracts}mkts` : (v.status === 'AVAILABLE' ? 'Online' : 'Offline');
        if (latEl) latEl.textContent = v.latency_ms ? `${Math.round(v.latency_ms)}ms` : '--';
    });
}

function renderScreenerTable(data) {
    const tbody = document.getElementById('screenerTableBody');
    if (!tbody) return;

    const items = data.data || [];
    if (items.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="12" class="py-12 text-center text-slate-500 font-mono text-xs">
                    No perpetual markets matched current filter thresholds. Try lowering the Min Volume ($1M) or clearing the search query.
                </td>
            </tr>
        `;
        return;
    }

    let html = '';
    items.forEach((item, idx) => {
        const isExpanded = screenerExpandedRows.has(item.normalized_symbol);
        const base = item.base_asset || item.normalized_symbol.replace('USDT', '');
        const sym = item.normalized_symbol;
        const px = item.benchmark_price ? formatScreenerPrice(item.benchmark_price) : '--';
        
        const chg = item.price_change_24h_pct;
        const chgClass = chg > 0 ? 'text-emerald-400' : (chg < 0 ? 'text-rose-400' : 'text-slate-400');
        const chgFormatted = chg !== null && chg !== undefined ? `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%` : '--';

        const volStr = item.total_volume_24h_usdt ? formatCurrencyCompact(item.total_volume_24h_usdt) : '$0';
        const oiStr = item.total_open_interest_usdt ? formatCurrencyCompact(item.total_open_interest_usdt) : '$0';

        const oiChg = item.oi_change_1h_pct;
        const oiChgHtml = oiChg !== null && oiChg !== undefined ? 
            `<span class="text-[9px] font-mono ${oiChg >= 0 ? 'text-emerald-400' : 'text-rose-400'}">(${oiChg >= 0 ? '+' : ''}${oiChg.toFixed(1)}% 1h)</span>` : '';

        // Price divergence
        const divBps = item.price_divergence_bps;
        const divUsdt = item.price_divergence_usdt;
        let divHtml = '<span class="text-slate-500 font-mono text-[11px] font-medium">Single Venue</span>';
        if (item.cross_listed && divBps !== null) {
            const divColor = divBps > 15.0 ? 'text-amber-400 font-bold' : (divBps > 30.0 ? 'text-rose-400 font-bold' : 'text-slate-300 font-medium');
            divHtml = `
                <div class="font-mono text-xs ${divColor}">
                    ${divBps.toFixed(2)} bps
                </div>
                <div class="text-[9px] text-slate-500 font-mono">
                    $${formatScreenerPrice(divUsdt)} diff
                </div>
            `;
        }

        // Venue badges
        const venues = item.venues || {};
        const vBadges = Object.keys(venues).map(ex => {
            const shortName = ex === 'asterdex' ? 'ASTER' : (ex === 'binance' ? 'BN' : (ex === 'bybit' ? 'BYB' : 'HL'));
            return `<span class="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-slate-800 text-slate-300 border border-slate-700">${shortName}</span>`;
        }).join(' ');

        // Funding rate badges for each exchange
        const renderFrBadge = (ex) => {
            const vm = venues[ex];
            if (!vm || vm.funding_rate === null || vm.funding_rate === undefined) {
                return '<span class="text-slate-600 font-mono text-[10px]">—</span>';
            }
            const fr8h = vm.normalized_8h_funding_rate;
            const frRaw = vm.funding_rate;
            const pct8h = (fr8h * 100.0);
            const isPos = fr8h > 0;
            const isNeg = fr8h < 0;
            const color = isPos ? 'text-amber-300 bg-amber-950/60 border-amber-800/80' : (isNeg ? 'text-purple-300 bg-purple-950/60 border-purple-800/80' : 'text-slate-400 bg-slate-900 border-slate-800');
            const sign = isPos ? '+' : '';
            return `
                <div class="inline-flex flex-col items-center">
                    <span class="px-1.5 py-0.2 rounded text-[10px] font-mono font-bold border ${color}">
                        ${sign}${pct8h.toFixed(4)}%
                    </span>
                    <span class="text-[8px] text-slate-500 font-mono mt-0.5">${vm.funding_interval_hours}h</span>
                </div>
            `;
        };

        const bestSpreadStr = item.best_spread_bps !== null && item.best_spread_bps !== undefined ? 
            `${item.best_spread_bps.toFixed(1)} bps` : '--';
        const bestSpreadVenue = item.lowest_spread_venue ? item.lowest_spread_venue.toUpperCase() : '';

        html += `
            <tr class="hover:bg-slate-800/40 transition cursor-pointer border-b border-slate-800/60 ${isExpanded ? 'bg-slate-800/30' : ''}" onclick="toggleScreenerRow('${sym}', event)">
                <td class="py-3 px-3 text-center w-8">
                    <button class="w-5 h-5 rounded flex items-center justify-center bg-slate-800 hover:bg-slate-700 text-slate-400 font-mono text-xs">
                        ${isExpanded ? '&#8722;' : '&#43;'}
                    </button>
                </td>
                <td class="py-3 px-3">
                    <div class="flex items-center gap-2">
                        <span class="font-bold text-white text-xs tracking-wide">${base}</span>
                        <span class="px-1.5 py-0.2 rounded text-[9px] font-mono bg-slate-800 text-slate-400 border border-slate-700">PERP</span>
                    </div>
                    <div class="text-[10px] text-slate-500 font-mono">${sym}</div>
                </td>
                <td class="py-3 px-3 font-mono">
                    <div class="text-xs font-bold text-slate-100">$${px}</div>
                    <div class="text-[10px] font-semibold ${chgClass}">${chgFormatted}</div>
                </td>
                <td class="py-3 px-3 font-mono">
                    <div class="text-xs font-bold text-emerald-400">${volStr}</div>
                    <div class="text-[9px] text-slate-500">${venues[item.highest_volume_venue]?.exchange ? item.highest_volume_venue.toUpperCase() : ''} Top</div>
                </td>
                <td class="py-3 px-3 font-mono">
                    <div class="text-xs font-bold text-indigo-300">${oiStr}</div>
                    ${oiChgHtml}
                </td>
                <td class="py-3 px-3 font-mono">
                    ${divHtml}
                </td>
                <td class="py-3 px-2 text-center">
                    ${renderFrBadge('asterdex')}
                </td>
                <td class="py-3 px-2 text-center">
                    ${renderFrBadge('binance')}
                </td>
                <td class="py-3 px-2 text-center">
                    ${renderFrBadge('bybit')}
                </td>
                <td class="py-3 px-2 text-center">
                    ${renderFrBadge('hyperliquid')}
                </td>
                <td class="py-3 px-3 font-mono text-xs text-slate-300">
                    <div>${bestSpreadStr}</div>
                    <div class="text-[9px] text-slate-500">${bestSpreadVenue}</div>
                </td>
                <td class="py-3 px-2">
                    <div class="flex flex-wrap items-center gap-1">${vBadges}</div>
                </td>
                <td class="py-3 px-3 text-right" onclick="event.stopPropagation()">
                    <div class="flex items-center justify-end gap-1.5">
                        <button onclick="openScreenerAssetInChart('${sym}')" title="Open in Feature E Liquidity Chart" class="px-2 py-1 bg-indigo-600/80 hover:bg-indigo-600 text-white rounded text-[10px] font-medium transition shadow-sm">
                            Chart
                        </button>
                        <button onclick="copyScreenerSymbol('${sym}', this)" title="Copy Symbol" class="p-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-[10px] transition">
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"/></svg>
                        </button>
                    </div>
                </td>
            </tr>
            ${isExpanded ? renderScreenerExpandedDrawer(item) : ''}
        `;
    });

    tbody.innerHTML = html;
}

function renderScreenerExpandedDrawer(item) {
    const venues = item.venues || {};
    const venueKeys = ['asterdex', 'binance', 'bybit', 'hyperliquid'];
    
    let cardsHtml = '';
    venueKeys.forEach(ex => {
        const vm = venues[ex];
        if (!vm) {
            cardsHtml += `
                <div class="p-3.5 rounded-lg bg-slate-900/60 border border-slate-800/80 flex flex-col justify-center items-center text-center opacity-60">
                    <div class="text-xs font-bold text-slate-400 uppercase tracking-wider">${ex.toUpperCase()}</div>
                    <div class="text-[11px] text-slate-500 mt-2 font-mono">Not Listed on Venue</div>
                </div>
            `;
            return;
        }

        const mid = vm.best_bid && vm.best_ask ? ((vm.best_bid + vm.best_ask) / 2.0) : vm.mark_price;
        const diffBps = (item.benchmark_price && mid) ? (((mid - item.benchmark_price) / item.benchmark_price) * 10000.0) : null;
        const diffSign = diffBps !== null && diffBps > 0 ? '+' : '';
        const diffColor = diffBps !== null ? (Math.abs(diffBps) > 10.0 ? 'text-amber-400' : 'text-slate-400') : 'text-slate-500';

        const fr8h = vm.normalized_8h_funding_rate !== null ? `${(vm.normalized_8h_funding_rate * 100.0).toFixed(4)}%` : '--';
        const apr = vm.annualized_funding_pct !== null ? `${vm.annualized_funding_pct.toFixed(2)}%` : '--';
        
        let nextFundingCountdown = '--';
        if (vm.next_funding_time_ms) {
            const msLeft = vm.next_funding_time_ms - Date.now();
            if (msLeft > 0) {
                const h = Math.floor(msLeft / 3600000);
                const m = Math.floor((msLeft % 3600000) / 60000);
                nextFundingCountdown = `in ${h}h ${m}m`;
            } else {
                nextFundingCountdown = 'Settling';
            }
        }

        cardsHtml += `
            <div class="p-3.5 rounded-lg bg-slate-900/90 border border-slate-700/80 space-y-2.5">
                <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full ${vm.status === 'AVAILABLE' ? 'bg-emerald-400' : 'bg-rose-400'}"></span>
                        <strong class="text-xs font-bold text-white uppercase tracking-wider">${ex.toUpperCase()}</strong>
                    </div>
                    <span class="text-[10px] font-mono text-slate-400">${vm.latency_ms}ms</span>
                </div>

                <div class="grid grid-cols-2 gap-2 text-[11px] font-mono">
                    <div>
                        <span class="text-slate-500">Last / Mark:</span>
                        <div class="text-white font-bold">$${formatScreenerPrice(vm.last_price || vm.mark_price)}</div>
                    </div>
                    <div>
                        <span class="text-slate-500">Mid Basis:</span>
                        <div class="${diffColor} font-bold">${diffBps !== null ? `${diffSign}${diffBps.toFixed(2)} bps` : '--'}</div>
                    </div>
                    <div>
                        <span class="text-slate-500">24h Volume:</span>
                        <div class="text-emerald-400 font-bold">${vm.volume_24h_usdt ? formatCurrencyCompact(vm.volume_24h_usdt) : '$0'}</div>
                    </div>
                    <div>
                        <span class="text-slate-500">Open Interest:</span>
                        <div class="text-indigo-300 font-bold">${vm.open_interest_notional ? formatCurrencyCompact(vm.open_interest_notional) : '--'}</div>
                    </div>
                </div>

                <div class="pt-2 border-t border-slate-800 text-[11px] font-mono space-y-1">
                    <div class="flex justify-between">
                        <span class="text-slate-500">Funding (8h / APR):</span>
                        <span class="text-amber-300 font-bold">${fr8h} <span class="text-[9px] text-slate-400">(${apr} APR)</span></span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-slate-500">Funding Cycle:</span>
                        <span class="text-slate-300">${vm.funding_interval_hours}h &bull; ${nextFundingCountdown}</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-slate-500">Best Bid / Ask:</span>
                        <span class="text-slate-300">$${formatScreenerPrice(vm.best_bid)} / $${formatScreenerPrice(vm.best_ask)}</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-slate-500">Spread:</span>
                        <span class="text-slate-300 font-bold">${vm.spread_bps !== null ? `${vm.spread_bps.toFixed(2)} bps` : '--'}</span>
                    </div>
                </div>
            </div>
        `;
    });

    return `
        <tr class="bg-slate-900/40 border-b border-slate-800/80">
            <td colspan="12" class="p-4">
                <div class="space-y-2">
                    <div class="flex items-center justify-between text-xs font-mono text-slate-400 px-1">
                        <span>Side-by-Side Venue Comparison for <strong class="text-white">${item.normalized_symbol}</strong></span>
                        <span class="text-[10px] text-slate-500">Note: Basis divergence reflects order-book mid difference, not guaranteed arbitrage.</span>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
                        ${cardsHtml}
                    </div>
                </div>
            </td>
        </tr>
    `;
}

function toggleScreenerRow(symbol, event) {
    if (screenerExpandedRows.has(symbol)) {
        screenerExpandedRows.delete(symbol);
    } else {
        screenerExpandedRows.add(symbol);
    }
    if (screenerDataCache) renderScreenerTable(screenerDataCache);
}

function renderScreenerPagination(data) {
    const container = document.getElementById('screenerPaginationContainer');
    if (!container) return;

    if (typeof getPaginationState === 'function') {
        const state = getPaginationState('screener');
        state.currentPage = data.page || 1;
        state.pageSize = data.page_size || 25;
        state.totalItems = data.total_matched || 0;
        state.totalPages = data.total_pages || 1;
    }

    if (typeof renderPaginationControls === 'function') {
        renderPaginationControls('screener', 'screenerPaginationContainer', () => {
            const state = getPaginationState('screener');
            screenerCurrentPage = state?.currentPage || 1;
            screenerPageSize = state?.pageSize || screenerPageSize;
            loadMarketScreenerData(true);
        });
    }
}

// User Filter & Sort Handlers
function handleScreenerSearch(query) {
    screenerSearchQuery = query.trim();
    screenerCurrentPage = 1;
    loadMarketScreenerData(true);
}

function setScreenerVolumeFilter(minVol) {
    screenerMinVolume = minVol;
    screenerCurrentPage = 1;
    
    // Update button states
    ['0', '500k', '1m', '2m', '3m', '5m', '10m', '50m'].forEach(k => {
        const btn = document.getElementById(`screenerVolBtn-${k}`);
        if (btn) {
            btn.className = (btn.getAttribute('data-val') == minVol)
                ? 'px-2.5 py-1 rounded bg-indigo-600 text-white font-bold text-xs transition shadow-sm'
                : 'px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition';
        }
    });

    loadMarketScreenerData(true);
}

function handleScreenerOIFilter(minOI) {
    screenerMinOI = minOI ? parseFloat(minOI) : null;
    screenerCurrentPage = 1;
    loadMarketScreenerData(true);
}

function handleScreenerFundingDirection(direction) {
    screenerFundingDirection = direction;
    screenerCurrentPage = 1;
    loadMarketScreenerData(true);
}

function handleScreenerMaxSpread(spreadBps) {
    screenerMaxSpread = spreadBps ? parseFloat(spreadBps) : null;
    screenerCurrentPage = 1;
    loadMarketScreenerData(true);
}

function toggleScreenerCrossListed() {
    screenerCrossListedOnly = !screenerCrossListedOnly;
    const btn = document.getElementById('screenerCrossListedBtn');
    if (btn) {
        if (screenerCrossListedOnly) {
            btn.className = 'px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-bold text-xs transition flex items-center gap-1.5 shadow-sm';
        } else {
            btn.className = 'px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition flex items-center gap-1.5';
        }
    }
    screenerCurrentPage = 1;
    loadMarketScreenerData(true);
}

function toggleScreenerExchange(ex) {
    if (screenerSelectedExchanges.includes(ex)) {
        if (screenerSelectedExchanges.length > 1) {
            screenerSelectedExchanges = screenerSelectedExchanges.filter(e => e !== ex);
        }
    } else {
        screenerSelectedExchanges.push(ex);
    }

    ['asterdex', 'binance', 'bybit', 'hyperliquid'].forEach(e => {
        const btn = document.getElementById(`screenerExToggle-${e}`);
        if (btn) {
            const active = screenerSelectedExchanges.includes(e);
            btn.className = active ? 
                'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-indigo-600 text-white transition' :
                'px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-400 hover:text-slate-200 transition';
        }
    });

    screenerCurrentPage = 1;
    loadMarketScreenerData(true);
}

function handleScreenerSort(col) {
    if (screenerSortBy === `${col}_desc`) {
        screenerSortBy = `${col}_asc`;
    } else {
        screenerSortBy = `${col}_desc`;
    }
    screenerCurrentPage = 1;
    loadMarketScreenerData(true);
}

function handleScreenerPageSize(size) {
    screenerPageSize = parseInt(size, 10) || 25;
    screenerCurrentPage = 1;
    loadMarketScreenerData(true);
}

function openScreenerAssetInChart(symbol) {
    if (typeof switchTab === 'function') {
        switchTab('sweep_chart');
        const inp = document.getElementById('chartSymbolInput');
        if (inp) inp.value = symbol;
        if (typeof changeChartSymbol === 'function') {
            changeChartSymbol(symbol);
        }
    }
}

function copyScreenerSymbol(symbol, btnEl) {
    if (navigator.clipboard) {
        navigator.clipboard.writeText(symbol).then(() => {
            if (btnEl) {
                const orig = btnEl.innerHTML;
                btnEl.innerHTML = '<span class="text-emerald-400 font-bold text-[10px]">&check;</span>';
                setTimeout(() => { btnEl.innerHTML = orig; }, 1500);
            }
        });
    }
}

// Formatters
function formatScreenerPrice(p) {
    if (p === null || p === undefined) return '--';
    const num = Number(p);
    if (num >= 1000) return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (num >= 1) return num.toFixed(3);
    if (num >= 0.01) return num.toFixed(4);
    return num.toFixed(6);
}

function formatCurrencyCompact(v) {
    if (v === null || v === undefined) return '$0';
    const num = Number(v);
    if (num >= 1e9) return `$${(num / 1e9).toFixed(2)}B`;
    if (num >= 1e6) return `$${(num / 1e6).toFixed(2)}M`;
    if (num >= 1e3) return `$${(num / 1e3).toFixed(1)}K`;
    return `$${num.toFixed(0)}`;
}

function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
