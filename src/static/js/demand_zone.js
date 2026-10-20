/**
 * AsterDEX Demand-Zone LONG Scanner Frontend Controller
 * Handles real-time telemetry, candidate setups, BTC context, risk sizing, and order book auditing.
 */

let dzCurrentCandidates = [];
let dzCurrentBTCContext = null;
let dzCurrentConfig = null;
let dzStatusFilter = 'ALL';
let dzSearchTerm = '';
let dzMinScore = 0;
let dzAutoRefreshTimer = null;

async function loadDemandZoneScanner() {
    try {
        const [configRes, btcRes, candRes] = await Promise.all([
            fetch('/api/demand_zone/config').then(r => r.json()).catch(() => null),
            fetch('/api/demand_zone/btc_context').then(r => r.json()).catch(() => null),
            fetch('/api/demand_zone/candidates?limit=200').then(r => r.json()).catch(() => null),
        ]);

        if (configRes && configRes.config) {
            dzCurrentConfig = configRes.config;
            renderDemandZoneConfig(dzCurrentConfig);
        }

        if (btcRes && btcRes.btc_context) {
            dzCurrentBTCContext = btcRes.btc_context;
            renderDemandZoneBTCContext(dzCurrentBTCContext);
        }

        if (candRes && candRes.status === 'SUCCESS') {
            dzCurrentCandidates = candRes.candidates || [];
            if (candRes.btc_context && !dzCurrentBTCContext) {
                dzCurrentBTCContext = candRes.btc_context;
                renderDemandZoneBTCContext(dzCurrentBTCContext);
            }
            renderDemandZoneMetrics(candRes);
            renderDemandZoneCandidatesTable();
        }

        updateDemandZoneHeaderBadge();
    } catch (err) {
        console.error('Failed to load demand-zone scanner data:', err);
    }
}

function updateDemandZoneHeaderBadge() {
    const readyCount = dzCurrentCandidates.filter(c => c.status === 'READY').length;
    const badge = document.getElementById('demandZoneTabBadge');
    if (badge) {
        badge.textContent = `${readyCount} READY`;
        badge.className = readyCount > 0
            ? 'px-1.5 py-0.2 rounded-full text-[10px] bg-emerald-950 text-emerald-300 font-bold border border-emerald-700 font-mono'
            : 'px-1.5 py-0.2 rounded-full text-[10px] bg-slate-900 text-slate-400 font-bold border border-slate-700 font-mono';
    }
}

function renderDemandZoneBTCContext(btc) {
    if (!btc) return;
    const pxEl = document.getElementById('dzBtcPriceDisplay');
    const statusEl = document.getElementById('dzBtcZoneStatusBadge');
    const policyEl = document.getElementById('dzBtcPolicyDisplay');
    const policySelect = document.getElementById('dzBtcPolicySelect');
    const explEl = document.getElementById('dzBtcExplanationDisplay');
    const rangeEl = document.getElementById('dzBtcZoneRangeDisplay');
    const distEl = document.getElementById('dzBtcDistanceDisplay');
    const eligibleEl = document.getElementById('dzBtcEligibleBadge');

    if (pxEl) pxEl.textContent = `$${Number(btc.current_price).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}`;
    if (rangeEl) rangeEl.textContent = `$${Number(btc.zone_lower).toLocaleString()} – $${Number(btc.zone_upper).toLocaleString()}`;

    if (distEl) {
        const dUpper = btc.distance_to_upper_pct;
        distEl.textContent = `${dUpper >= 0 ? '+' : ''}${dUpper.toFixed(2)}% to upper bound`;
    }

    if (statusEl) {
        statusEl.textContent = btc.zone_status;
        if (btc.zone_status === 'INSIDE_ZONE') {
            statusEl.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-700';
        } else if (btc.zone_status === 'APPROACHING_ZONE') {
            statusEl.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-amber-950 text-amber-300 border border-amber-700 animate-pulse';
        } else if (btc.zone_status === 'BELOW_ZONE') {
            statusEl.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-rose-950 text-rose-300 border border-rose-700';
        } else {
            statusEl.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-slate-800 text-slate-300 border border-slate-700';
        }
    }

    if (policyEl) policyEl.textContent = btc.active_policy;
    if (policySelect) policySelect.value = btc.active_policy;

    if (eligibleEl) {
        if (btc.allows_ready_status) {
            eligibleEl.textContent = 'READY ELIGIBLE';
            eligibleEl.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-900/60 text-emerald-300 border border-emerald-700';
        } else {
            eligibleEl.textContent = 'HELD IN WATCHLIST';
            eligibleEl.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-amber-900/60 text-amber-300 border border-amber-700';
        }
    }

    if (explEl) explEl.textContent = btc.status_explanation || 'Monitoring BTC position relative to reference zone.';
}

function renderDemandZoneMetrics(data) {
    const counts = data.status_counts || {};
    const telemetry = data.telemetry || {};

    const elScreened = document.getElementById('dzMetricScreened');
    const elReady = document.getElementById('dzMetricReady');
    const elApproaching = document.getElementById('dzMetricApproaching');
    const elWatchlist = document.getElementById('dzMetricWatchlist');
    const elInfeasible = document.getElementById('dzMetricInfeasible');
    const elTotal = document.getElementById('dzMetricTotalDiscovered');
    const elDuration = document.getElementById('dzMetricDuration');
    const elLastScan = document.getElementById('dzLastScanTimeDisplay');

    if (elScreened) elScreened.textContent = telemetry.liquid_markets_screened || data.total_count || '0';
    if (elTotal) elTotal.textContent = `${telemetry.total_markets_discovered || 578} Discovered`;
    if (elReady) elReady.textContent = counts.READY || 0;
    if (elApproaching) elApproaching.textContent = counts.APPROACHING || 0;
    if (elWatchlist) elWatchlist.textContent = counts.WATCHLIST || 0;
    if (elInfeasible) elInfeasible.textContent = counts.INFEASIBLE || 0;
    if (elDuration) elDuration.textContent = telemetry.duration_ms ? `${(telemetry.duration_ms / 1000).toFixed(1)}s` : '--';
    if (elLastScan && telemetry.datetime_utc) {
        elLastScan.textContent = `Last scan: ${new Date(telemetry.datetime_utc).toLocaleTimeString()}`;
    }

    // Update filter badge counts
    ['ALL', 'READY', 'APPROACHING', 'WATCHLIST', 'INFEASIBLE', 'INVALIDATED'].forEach(st => {
        const badge = document.getElementById(`dz-count-${st}`);
        if (badge) {
            if (st === 'ALL') badge.textContent = data.total_count || dzCurrentCandidates.length;
            else badge.textContent = counts[st] || 0;
        }
    });
}

function renderDemandZoneConfig(cfg) {
    if (!cfg) return;
    const volInput = document.getElementById('dzCfgMinVolume');
    const riskInput = document.getElementById('dzCfgRiskBudget');
    const rrInput = document.getElementById('dzCfgMinRR');
    const minStopInput = document.getElementById('dzCfgMinStop');
    const maxStopInput = document.getElementById('dzCfgMaxStop');
    const btcLowerInput = document.getElementById('dzCfgBtcLower');
    const btcUpperInput = document.getElementById('dzCfgBtcUpper');

    if (volInput) volInput.value = (cfg.min_24h_volume_usdt / 1000000).toFixed(1);
    if (riskInput) riskInput.value = cfg.risk_budget_usdt.toFixed(2);
    if (rrInput) rrInput.value = cfg.min_net_reward_to_risk.toFixed(1);
    if (minStopInput) minStopInput.value = cfg.min_stop_loss_dist_pct.toFixed(1);
    if (maxStopInput) maxStopInput.value = cfg.max_stop_loss_dist_pct.toFixed(1);
    if (btcLowerInput) btcLowerInput.value = cfg.btc_zone_lower;
    if (btcUpperInput) btcUpperInput.value = cfg.btc_zone_upper;
}

function renderDemandZoneCandidatesTable() {
    const tbody = document.getElementById('dzCandidatesTableBody');
    if (!tbody) return;

    let filtered = dzCurrentCandidates;

    if (dzStatusFilter !== 'ALL') {
        filtered = filtered.filter(c => c.status === dzStatusFilter);
    }

    if (dzSearchTerm) {
        const term = dzSearchTerm.toUpperCase();
        filtered = filtered.filter(c => c.symbol.toUpperCase().includes(term));
    }

    if (dzMinScore > 0) {
        filtered = filtered.filter(c => c.composite_setup_score >= dzMinScore);
    }

    const pEl = document.getElementById('dzPaginationContainer');

    if (filtered.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="py-10 text-center text-slate-500 font-mono text-xs">
                    No candidate setups matching filter criteria (${dzStatusFilter}).
                </td>
            </tr>
        `;
        if (pEl) pEl.innerHTML = '';
        return;
    }

    const paged = (typeof paginateList === 'function')
        ? paginateList('demand_zone', filtered)
        : filtered;

    tbody.innerHTML = paged.map(c => {
        const statusBadge = getDemandZoneStatusBadge(c.status);
        const scoreColor = c.composite_setup_score >= 80 ? 'text-emerald-400 font-bold' : (c.composite_setup_score >= 65 ? 'text-indigo-300 font-semibold' : 'text-slate-400');
        const distColor = c.distance_to_demand_pct <= 3.5 && c.distance_to_demand_pct >= 1.5 ? 'text-emerald-400 font-semibold' : (c.distance_to_demand_pct < 0 ? 'text-amber-300 font-semibold' : 'text-slate-300');
        const rrColor = c.net_reward_to_risk >= 2.0 ? 'text-emerald-400 font-bold' : (c.net_reward_to_risk >= 1.5 ? 'text-sky-300' : 'text-rose-400');

        return `
            <tr class="border-b border-slate-800/80 hover:bg-slate-900/60 transition text-xs font-mono">
                <!-- Symbol & Direction -->
                <td class="py-3 px-3">
                    <div class="flex items-center gap-2">
                        <span class="font-bold text-white tracking-wide">${c.symbol}</span>
                        <span class="px-1.5 py-0.2 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">LONG</span>
                    </div>
                    <div class="text-[10px] text-slate-500 mt-0.5">
                        Vol: $${(c.volume_24h_usdt / 1000000).toFixed(1)}M &bull; Sprd: ${c.bid_ask_spread_bps ? c.bid_ask_spread_bps.toFixed(1) : '0'} bps
                    </div>
                </td>

                <!-- Status -->
                <td class="py-3 px-3">
                    ${statusBadge}
                </td>

                <!-- Setup Score -->
                <td class="py-3 px-3">
                    <div class="flex items-center gap-1.5">
                        <span class="${scoreColor} text-sm">${c.composite_setup_score.toFixed(1)}</span>
                        <span class="text-[10px] text-slate-500">/100</span>
                    </div>
                    <div class="text-[10px] text-slate-500">
                        Zone: ${c.demand_zone_score ? c.demand_zone_score.toFixed(0) : '--'} &bull; OB: ${c.liquidity_quality_score ? c.liquidity_quality_score.toFixed(0) : '--'}
                    </div>
                </td>

                <!-- Current Price & Distance -->
                <td class="py-3 px-3">
                    <div class="text-white font-medium">${formatPrice(c.current_price)}</div>
                    <div class="${distColor} text-[10px]">
                        ${c.distance_to_demand_pct >= 0 ? '+' : ''}${c.distance_to_demand_pct.toFixed(2)}% to demand
                    </div>
                </td>

                <!-- Validated Demand Zone -->
                <td class="py-3 px-3">
                    <div class="text-slate-300 font-medium">
                        [${formatPrice(c.demand_zone_lower)} – ${formatPrice(c.demand_zone_upper)}]
                    </div>
                    <div class="text-[10px] text-slate-500">
                        Invalidation: <span class="text-rose-400 font-mono">${formatPrice(c.structural_invalidation_level)}</span>
                    </div>
                </td>

                <!-- Entry, SL, TP1 -->
                <td class="py-3 px-3">
                    <div class="flex items-center gap-1 text-[11px]">
                        <span class="text-slate-400">Entry:</span>
                        <span class="text-emerald-400 font-bold">${formatPrice(c.proposed_entry_price)}</span>
                    </div>
                    <div class="flex items-center gap-2 text-[10px] text-slate-400 mt-0.5">
                        <span>SL: <span class="text-rose-400">${formatPrice(c.proposed_stop_loss_price)}</span> (${c.stop_loss_distance_pct.toFixed(2)}%)</span>
                        <span>TP1: <span class="text-sky-300">${formatPrice(c.take_profit_1)}</span></span>
                    </div>
                </td>

                <!-- Net R:R & Targets -->
                <td class="py-3 px-3">
                    <div class="${rrColor} font-bold text-xs">
                        ${c.net_reward_to_risk.toFixed(2)}R Net
                    </div>
                    <div class="text-[10px] text-slate-500">
                        Gross: ${c.gross_reward_to_risk ? c.gross_reward_to_risk.toFixed(2) : '--'}R &bull; BE: ${formatPrice(c.break_even_price)}
                    </div>
                </td>

                <!-- Sizing & Risk ($0.30 budget) -->
                <td class="py-3 px-3">
                    <div class="text-slate-200">
                        $${c.position_notional_usdt.toFixed(2)} <span class="text-slate-500 text-[10px]">(${c.position_quantity} units)</span>
                    </div>
                    <div class="text-[10px] text-slate-400">
                        Margin: <span class="text-amber-400 font-mono">$${c.required_margin_usdt.toFixed(2)}</span> &bull; Risk: <span class="text-rose-400">$${c.total_estimated_loss_usdt.toFixed(3)}</span>
                    </div>
                </td>

                <!-- Actions -->
                <td class="py-3 px-3 text-right">
                    <button onclick="openDemandZoneInspector('${c.candidate_id}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 hover:text-white rounded text-[11px] font-semibold border border-slate-700 transition flex items-center gap-1 ml-auto">
                        <svg class="w-3.5 h-3.5 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
                        <span>Inspect</span>
                    </button>
                </td>
            </tr>
        `;
    }).join('');

    if (typeof renderPaginationControls === 'function') {
        renderPaginationControls('demand_zone', 'dzPaginationContainer', () => {
            renderDemandZoneCandidatesTable();
        });
    }
}

function getDemandZoneStatusBadge(status) {
    switch (status) {
        case 'READY':
            return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-700">READY</span>';
        case 'APPROACHING':
            return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-sky-950 text-sky-300 border border-sky-700">APPROACHING</span>';
        case 'WATCHLIST':
            return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-300 border border-amber-700">WATCHLIST</span>';
        case 'INFEASIBLE':
            return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800">INFEASIBLE</span>';
        case 'INVALIDATED':
            return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-orange-950 text-orange-300 border border-orange-800">INVALIDATED</span>';
        case 'STALE':
            return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700">STALE</span>';
        default:
            return `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">${status}</span>`;
    }
}

function setDemandZoneStatusFilter(status) {
    dzStatusFilter = status;
    if (typeof resetPaginationPage === "function") resetPaginationPage("demand_zone");
    ['ALL', 'READY', 'APPROACHING', 'WATCHLIST', 'INFEASIBLE', 'INVALIDATED'].forEach(st => {
        const btn = document.getElementById(`dz-filter-${st}`);
        if (btn) {
            if (st === status) {
                btn.className = 'px-3 py-1 text-xs rounded-lg bg-indigo-600 text-white font-semibold transition flex items-center gap-1.5 shadow-sm';
            } else {
                btn.className = 'px-3 py-1 text-xs rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1.5';
            }
        }
    });
    renderDemandZoneCandidatesTable();
}

function handleDemandZoneSearch(val) {
    dzSearchTerm = val.trim();
    if (typeof resetPaginationPage === "function") resetPaginationPage("demand_zone");
    renderDemandZoneCandidatesTable();
}

async function triggerDemandZoneScan() {
    const btn = document.getElementById('dzTriggerScanBtn');
    const spin = document.getElementById('dzScanSpinIcon');
    const text = document.getElementById('dzScanBtnText');

    if (btn) btn.disabled = true;
    if (spin) spin.classList.remove('hidden');
    if (text) text.textContent = 'Scanning AsterDEX Universe...';

    try {
        const res = await fetch('/api/demand_zone/scan?max_symbols=50', { method: 'POST' });
        const data = await res.json();
        await loadDemandZoneScanner();
    } catch (e) {
        console.error('Demand zone scan trigger failed:', e);
    } finally {
        if (btn) btn.disabled = false;
        if (spin) spin.classList.add('hidden');
        if (text) text.textContent = 'Scan Demand Zones';
    }
}

async function saveDemandZoneConfig() {
    const vol = parseFloat(document.getElementById('dzCfgMinVolume')?.value || '1.0') * 1000000;
    const risk = parseFloat(document.getElementById('dzCfgRiskBudget')?.value || '0.30');
    const rr = parseFloat(document.getElementById('dzCfgMinRR')?.value || '1.5');
    const minStop = parseFloat(document.getElementById('dzCfgMinStop')?.value || '2.5');
    const maxStop = parseFloat(document.getElementById('dzCfgMaxStop')?.value || '4.0');
    const btcLower = parseFloat(document.getElementById('dzCfgBtcLower')?.value || '79864.0');
    const btcUpper = parseFloat(document.getElementById('dzCfgBtcUpper')?.value || '80400.0');
    const policy = document.getElementById('dzBtcPolicySelect')?.value || 'PRIORITIZE_ON_APPROACH';

    try {
        const res = await fetch('/api/demand_zone/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                min_24h_volume_usdt: vol,
                risk_budget_usdt: risk,
                min_net_reward_to_risk: rr,
                min_stop_loss_dist_pct: minStop,
                max_stop_loss_dist_pct: maxStop,
                btc_zone_lower: btcLower,
                btc_zone_upper: btcUpper,
                btc_policy: policy,
            }),
        });
        const data = await res.json();
        if (data.status === 'SUCCESS') {
            await loadDemandZoneScanner();
            alert('Demand-zone scanner configuration saved successfully.');
        }
    } catch (err) {
        console.error('Failed to save config:', err);
    }
}

async function changeBTCPolicy(policy) {
    try {
        await fetch('/api/demand_zone/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ btc_policy: policy }),
        });
        await loadDemandZoneScanner();
    } catch (err) {
        console.error('Failed to update BTC policy:', err);
    }
}

function openDemandZoneInspector(candidateId) {
    const cand = dzCurrentCandidates.find(c => c.candidate_id === candidateId);
    if (!cand) return;

    const modal = document.getElementById('demandZoneInspectorModal');
    const content = document.getElementById('demandZoneInspectorContent');
    const title = document.getElementById('dzModalTitle');
    const badge = document.getElementById('dzModalStatusBadge');
    const score = document.getElementById('dzModalScore');

    if (title) title.textContent = `${cand.symbol} LONG Setup`;
    if (badge) badge.innerHTML = getDemandZoneStatusBadge(cand.status);
    if (score) score.textContent = `${cand.composite_setup_score.toFixed(1)} / 100`;

    const details = cand.details || {};
    const dz = details.demand_zone || {};
    const risk = details.risk_sizing || {};
    const tp = details.target_planning || {};
    const ob = details.order_book_assessment || {};

    if (content) {
        content.innerHTML = `
            <!-- Top Alert / Rejection Banner (if INFEASIBLE or APPROACHING) -->
            ${cand.rejection_reasons && cand.rejection_reasons.length > 0 ? `
                <div class="p-3 rounded-xl bg-rose-950/40 border border-rose-800/80 space-y-1">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-rose-300 uppercase tracking-wide">
                        <svg class="w-4 h-4 text-rose-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
                        <span>Audit Downgrade / Infeasibility Criteria:</span>
                    </div>
                    <ul class="text-xs text-rose-200 list-disc list-inside space-y-0.5">
                        ${cand.rejection_reasons.map(r => `<li>${r}</li>`).join('')}
                    </ul>
                </div>
            ` : `
                <div class="p-3 rounded-xl bg-emerald-950/40 border border-emerald-800/80 flex items-center gap-2">
                    <span class="w-2.5 h-2.5 rounded-full bg-emerald-400"></span>
                    <span class="text-xs font-semibold text-emerald-200">All mandatory filters passed. Setup is READY for paper limit execution.</span>
                </div>
            `}

            <!-- 4-Column Technical Breakdown Grid -->
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 text-xs font-mono">
                <!-- Card 1: 1H Demand Zone Geometry -->
                <div class="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
                    <div class="text-[11px] font-bold text-indigo-300 uppercase border-b border-slate-800 pb-1">
                        1. 1H Demand Zone
                    </div>
                    <div class="space-y-1">
                        <div>Upper (Entry): <strong class="text-emerald-400 font-bold">${formatPrice(cand.demand_zone_upper)}</strong></div>
                        <div>Lower Boundary: <span class="text-slate-300">${formatPrice(cand.demand_zone_lower)}</span></div>
                        <div>Invalidation: <span class="text-rose-400 font-bold">${formatPrice(cand.structural_invalidation_level)}</span></div>
                        <div>Current Price: <span class="text-white">${formatPrice(cand.current_price)}</span></div>
                        <div>Distance: <span class="text-emerald-400">${cand.distance_to_demand_pct >= 0 ? '+' : ''}${cand.distance_to_demand_pct.toFixed(2)}%</span></div>
                        <div>Displacement: <span class="text-slate-300">${dz.displacement_score ? dz.displacement_score.toFixed(1) : '1.0'}x ATR</span></div>
                        <div>Reactions: <span class="text-slate-300">${dz.reaction_count || 1}</span> &bull; Fresh: <span class="${dz.is_fresh ? 'text-emerald-400' : 'text-amber-400'}">${dz.is_fresh ? 'YES' : 'TESTED'}</span></div>
                        <div>Sweep &amp; Reclaim: <span class="${dz.liquidity_swept ? 'text-emerald-400' : 'text-slate-400'}">${dz.liquidity_swept ? 'CONFIRMED' : 'NO'}</span></div>
                    </div>
                </div>

                <!-- Card 2: Risk Budget & Sizing ($0.30) -->
                <div class="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
                    <div class="text-[11px] font-bold text-amber-300 uppercase border-b border-slate-800 pb-1">
                        2. Risk Sizing ($0.30)
                    </div>
                    <div class="space-y-1">
                        <div>Risk Budget: <strong class="text-amber-300">$${cand.risk_budget_usdt.toFixed(2)} USDT</strong></div>
                        <div>Stop Distance: <span class="text-rose-400">${cand.stop_loss_distance_pct.toFixed(2)}%</span></div>
                        <div>Contract Units: <span class="text-white">${cand.position_quantity}</span></div>
                        <div>Position Notional: <span class="text-slate-200">$${cand.position_notional_usdt.toFixed(2)}</span></div>
                        <div>Required Margin: <strong class="text-amber-400">$${cand.required_margin_usdt.toFixed(2)}</strong> (10x)</div>
                        <div>Est Total Loss: <span class="text-rose-400">$${cand.total_estimated_loss_usdt.toFixed(4)}</span></div>
                        <div>Maker Fee: <span class="text-slate-400">$${risk.estimated_entry_fee_usdt ? risk.estimated_entry_fee_usdt.toFixed(4) : '0.0020'}</span></div>
                        <div>SL Taker Fee: <span class="text-slate-400">$${risk.estimated_exit_fee_usdt ? risk.estimated_exit_fee_usdt.toFixed(4) : '0.0050'}</span></div>
                        <div>SL Slippage: <span class="text-slate-400">$${risk.estimated_slippage_usdt ? risk.estimated_slippage_usdt.toFixed(4) : '0.0050'}</span></div>
                    </div>
                </div>

                <!-- Card 3: Take-Profit & Structural Targets -->
                <div class="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
                    <div class="text-[11px] font-bold text-sky-300 uppercase border-b border-slate-800 pb-1">
                        3. Structural Targets
                    </div>
                    <div class="space-y-1">
                        <div>TP1 Target: <strong class="text-sky-300 font-bold">${formatPrice(cand.take_profit_1)}</strong></div>
                        <div>TP2 (Optional): <span class="text-slate-400">${cand.take_profit_2 ? formatPrice(cand.take_profit_2) : 'None'}</span></div>
                        <div>Net Reward/Risk: <strong class="text-emerald-400 font-bold">${cand.net_reward_to_risk.toFixed(2)}R</strong></div>
                        <div>Gross R:R: <span class="text-slate-300">${cand.gross_reward_to_risk.toFixed(2)}R</span></div>
                        <div>Break-Even Price: <span class="text-slate-200">${formatPrice(cand.break_even_price)}</span></div>
                        <div>Net PnL @ TP1: <span class="text-emerald-400">+$${cand.estimated_net_pnl_tp1_usdt.toFixed(4)}</span></div>
                        <div>Nearest Resist: <span class="text-slate-400">${tp.nearest_resistance_price ? formatPrice(tp.nearest_resistance_price) : 'Unobstructed'}</span></div>
                        <div>Obstruction Check: <span class="${tp.is_obstructed_by_resistance ? 'text-rose-400 font-bold' : 'text-emerald-400'}">${tp.is_obstructed_by_resistance ? 'OBSTRUCTED (< 1.2R)' : 'CLEAR'}</span></div>
                    </div>
                </div>

                <!-- Card 4: Order-Book Liquidity & Walls -->
                <div class="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
                    <div class="text-[11px] font-bold text-teal-300 uppercase border-b border-slate-800 pb-1">
                        4. Order-Book &amp; Walls
                    </div>
                    <div class="space-y-1">
                        <div>Spread: <span class="text-slate-200">${cand.bid_ask_spread_bps ? cand.bid_ask_spread_bps.toFixed(1) : '0'} bps</span></div>
                        <div>Bid Depth (25 bps): <span class="text-emerald-400">$${ob.bid_depth_25bps_usdt ? Number(ob.bid_depth_25bps_usdt).toLocaleString() : '--'}</span></div>
                        <div>Depth / Order Ratio: <span class="text-slate-200">${ob.order_size_depth_ratio ? ob.order_size_depth_ratio.toFixed(1) : '--'}x</span></div>
                        <div>Liquidity Score: <strong class="text-teal-400">${cand.liquidity_quality_score.toFixed(0)}/100</strong></div>
                        <div>Wall Persistence: <span class="text-slate-300">${ob.wall_persistence_status || 'NEUTRAL'}</span></div>
                        <div>Spoofing Risk: <span class="${ob.spoofing_warning ? 'text-rose-400 font-bold' : 'text-emerald-400'}">${ob.spoofing_warning ? 'WARNING' : 'LOW'}</span></div>
                        <div>Supporting Walls: <span class="text-slate-300">${ob.supporting_bid_walls ? ob.supporting_bid_walls.length : 0} resting walls</span></div>
                    </div>
                </div>
            </div>

            <!-- Evidence Synthesis Bullet Points -->
            <div class="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
                <div class="text-xs font-bold text-slate-300 uppercase tracking-wide">
                    Evidence Synthesis &amp; Trade Rationale:
                </div>
                <ul class="text-xs text-slate-300 list-disc list-inside space-y-1">
                    ${cand.evidence_points && cand.evidence_points.length > 0 ? cand.evidence_points.map(e => `<li>${e}</li>`).join('') : '<li>No explicit evidence logged.</li>'}
                </ul>
            </div>
        `;
    }

    modal.classList.remove('hidden');
}

function closeDemandZoneInspector() {
    const modal = document.getElementById('demandZoneInspectorModal');
    if (modal) modal.classList.add('hidden');
}

function formatPrice(px) {
    if (px === undefined || px === null) return '--';
    const n = Number(px);
    if (n >= 1000) return n.toFixed(2);
    if (n >= 1) return n.toFixed(4);
    if (n >= 0.01) return n.toFixed(5);
    return n.toFixed(6);
}
