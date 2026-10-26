// =========================================================================
// AUTOMATED ASTER DEX TRADE JOURNAL & UNIFIED TABLE INSPECTOR
// =========================================================================

// State variables are declared in shared/state.js.
// Initialize defaults if not already defined:
if (typeof journalModeFilter === 'undefined') window.journalModeFilter = 'LIVE';
if (typeof journalStrategyFilter === 'undefined') window.journalStrategyFilter = 'ALL';
if (typeof journalSideFilter === 'undefined') window.journalSideFilter = 'ALL';
if (typeof journalStatusFilter === 'undefined') window.journalStatusFilter = 'ALL';
if (typeof journalReconFilter === 'undefined') window.journalReconFilter = 'ALL';
if (typeof journalStartDate === 'undefined') window.journalStartDate = '';
if (typeof journalEndDate === 'undefined') window.journalEndDate = '';
if (typeof journalSearchQuery === 'undefined') window.journalSearchQuery = '';
if (typeof journalSortBy === 'undefined') window.journalSortBy = 'NEWEST';

if (typeof lastFetchedTrades === 'undefined') window.lastFetchedTrades = [];
if (typeof cachedFillsByTrade === 'undefined') window.cachedFillsByTrade = {};
if (typeof expandedTrades === 'undefined') window.expandedTrades = new Set();

function setJournalMode(mode) {
    journalModeFilter = mode;
    ['LIVE', 'PAPER', 'SHADOW', 'ALL'].forEach(m => {
        const btn = document.getElementById(`jMode-${m}`);
        if (btn) {
            if (m === mode) {
                if (m === 'LIVE') {
                    btn.className = 'px-3 py-1.5 text-xs font-semibold rounded-lg bg-emerald-600 text-white border border-emerald-500 shadow-sm transition flex items-center gap-1.5';
                } else if (m === 'PAPER') {
                    btn.className = 'px-3 py-1.5 text-xs font-semibold rounded-lg bg-sky-600 text-white border border-sky-500 shadow-sm transition flex items-center gap-1.5';
                } else if (m === 'SHADOW') {
                    btn.className = 'px-3 py-1.5 text-xs font-semibold rounded-lg bg-purple-600 text-white border border-purple-500 shadow-sm transition flex items-center gap-1.5';
                } else {
                    btn.className = 'px-3 py-1.5 text-xs font-semibold rounded-lg bg-indigo-600 text-white border border-indigo-500 shadow-sm transition flex items-center gap-1.5';
                }
            } else {
                btn.className = 'px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1.5';
            }
        }
    });

    const scopeLabel = document.getElementById('journalModeScopeLabel');
    if (scopeLabel) {
        if (mode === 'LIVE') {
            scopeLabel.innerHTML = 'Active Scope: <strong class="text-emerald-400">LIVE (AsterDEX Mainnet Verified)</strong>';
        } else if (mode === 'PAPER') {
            scopeLabel.innerHTML = 'Active Scope: <strong class="text-sky-400">PAPER (Simulated Execution)</strong>';
        } else if (mode === 'SHADOW') {
            scopeLabel.innerHTML = 'Active Scope: <strong class="text-purple-400">SHADOW (SWEEP_1M_V1 Shadow Tracker)</strong>';
        } else {
            scopeLabel.innerHTML = 'Active Scope: <strong class="text-indigo-300">All Execution Modes Combined</strong>';
        }
    }

    // Update export buttons to include active mode
    updateExportLinks();

    loadJournal();
}

function updateExportLinks() {
    const exportUnifiedBtn = document.getElementById('exportUnifiedCsvBtn');
    if (exportUnifiedBtn) {
        exportUnifiedBtn.href = `/api/v1/journal/export/unified.csv?mode=${journalModeFilter}`;
    }
}

function handleJournalSearchInput(val) {
    journalSearchQuery = (val || '').trim();
    renderJournalTable();
}

function handleJournalStrategyChange(val) {
    journalStrategyFilter = val;
    loadJournal();
}

function handleJournalSideChange(val) {
    journalSideFilter = val;
    renderJournalTable();
}

function handleJournalStatusChange(val) {
    journalStatusFilter = val;
    loadJournal();
}

function handleJournalReconChange(val) {
    journalReconFilter = val;
    loadJournal();
}

function handleJournalDateChange() {
    const startInp = document.getElementById('journalStartDate');
    const endInp = document.getElementById('journalEndDate');
    journalStartDate = startInp ? startInp.value : '';
    journalEndDate = endInp ? endInp.value : '';
    loadJournal();
}

function handleJournalSortChange(val) {
    journalSortBy = val;
    renderJournalTable();
}

function resetJournalFilters() {
    journalStrategyFilter = 'ALL';
    journalSideFilter = 'ALL';
    journalStatusFilter = 'ALL';
    journalReconFilter = 'ALL';
    journalStartDate = '';
    journalEndDate = '';
    journalSearchQuery = '';
    journalSortBy = 'NEWEST';

    const sInp = document.getElementById('journalSearchInput');
    if (sInp) sInp.value = '';
    const stratSel = document.getElementById('journalStrategySelect');
    if (stratSel) stratSel.value = 'ALL';
    const sideSel = document.getElementById('journalSideSelect');
    if (sideSel) sideSel.value = 'ALL';
    const statSel = document.getElementById('journalStatusSelect');
    if (statSel) statSel.value = 'ALL';
    const recSel = document.getElementById('journalReconSelect');
    if (recSel) recSel.value = 'ALL';
    const startInp = document.getElementById('journalStartDate');
    if (startInp) startInp.value = '';
    const endInp = document.getElementById('journalEndDate');
    if (endInp) endInp.value = '';
    const sortSel = document.getElementById('journalSortSelect');
    if (sortSel) sortSel.value = 'NEWEST';

    loadJournal();
}

function formatJakartaTime(isoOrMs) {
    if (!isoOrMs) return '--';
    try {
        const d = typeof isoOrMs === 'number' ? new Date(isoOrMs) : new Date(isoOrMs);
        if (isNaN(d.getTime())) return '--';
        const parts = new Intl.DateTimeFormat('id-ID', {
            timeZone: 'Asia/Jakarta',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        }).formatToParts(d);
        const map = {};
        parts.forEach(p => map[p.type] = p.value);
        return `${map.year}-${map.month}-${map.day} ${map.hour}:${map.minute} WIB`;
    } catch (e) {
        return String(isoOrMs);
    }
}

function formatUtcTime(isoOrMs) {
    if (!isoOrMs) return '--';
    try {
        const d = typeof isoOrMs === 'number' ? new Date(isoOrMs) : new Date(isoOrMs);
        if (isNaN(d.getTime())) return '--';
        return d.toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
    } catch (e) {
        return String(isoOrMs);
    }
}

function formatPrice(p) {
    if (p === undefined || p === null || isNaN(p) || p === '') return '--';
    const num = Number(p);
    if (num === 0) return '$0.00';
    if (Math.abs(num) < 0.001) return '$' + num.toFixed(6);
    if (Math.abs(num) < 1) return '$' + num.toFixed(4);
    if (Math.abs(num) < 100) return '$' + num.toFixed(3);
    return '$' + num.toFixed(2);
}

function formatCurrency(c) {
    if (c === undefined || c === null || isNaN(c) || c === '') return '$0.00';
    const num = Number(c);
    return `${num >= 0 ? '$' : '-$'}${Math.abs(num).toFixed(2)}`;
}

async function loadJournal() {
    const refreshBtn = document.getElementById('refreshJournalBtn');
    const tbody = document.getElementById('journalTableBody');
    const emptyState = document.getElementById('journalEmptyState');
    const title = document.getElementById('journalEmptyTitle');
    const msg = document.getElementById('journalEmptyMessage');
    const countEl = document.getElementById('journalTableRecordCount');

    if (refreshBtn) refreshBtn.classList.add('opacity-75');

    // Show initial loading state if table has no rows
    if ((!lastFetchedTrades || lastFetchedTrades.length === 0) && emptyState) {
        emptyState.classList.remove('hidden');
        if (title) title.innerText = 'Loading Trade Records...';
        if (msg) msg.innerText = 'Fetching verified execution and fill data from Aster DEX...';
        if (countEl) countEl.innerText = 'Loading trades...';
    }

    try {
        const params = new URLSearchParams();
        if (journalModeFilter && journalModeFilter !== 'ALL') {
            params.append('mode', journalModeFilter);
        }
        if (journalStrategyFilter && journalStrategyFilter !== 'ALL') {
            params.append('strategy_id', journalStrategyFilter);
        }
        if (journalStatusFilter && journalStatusFilter !== 'ALL') {
            params.append('status', journalStatusFilter);
        }
        if (journalReconFilter && journalReconFilter !== 'ALL') {
            params.append('reconciliation_status', journalReconFilter);
        }
        if (journalStartDate) params.append('start_date', journalStartDate);
        if (journalEndDate) params.append('end_date', journalEndDate);
        params.append('limit', '500');

        const [tradesRes, summaryRes] = await Promise.all([
            fetch(`/api/v1/journal/trades?${params.toString()}`),
            fetch(`/api/v1/journal/summary?mode=${journalModeFilter}`)
        ]);

        if (!tradesRes.ok) {
            throw new Error(`HTTP ${tradesRes.status}: Unable to retrieve trades from Aster DEX API`);
        }

        const data = await tradesRes.json();
        lastFetchedTrades = data.trades || [];

        if (summaryRes.ok) {
            const summary = await summaryRes.json();
            renderJournalSummary(summary);
        }

        renderJournalTable();
    } catch (err) {
        console.error("Journal loading error:", err);
        if (emptyState) {
            emptyState.classList.remove('hidden');
            if (title) title.innerText = 'Unable to Load Journal';
            if (msg) msg.innerHTML = `Error: ${err.message || 'Failed to connect to Aster DEX trade journal API.'} <button onclick="loadJournal()" class="px-2.5 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-semibold ml-2 shadow-sm transition">Retry</button>`;
        }
        if (countEl) countEl.innerText = 'Unable to load trades';
    } finally {
        if (refreshBtn) refreshBtn.classList.remove('opacity-75');
    }
}

function renderJournalSummary(s) {
    if (!s) return;

    const closedEl = document.getElementById('journal-metric-closed-trades');
    if (closedEl) closedEl.innerText = s.total_closed_trades || 0;

    const bdEl = document.getElementById('journal-metric-trades-breakdown');
    if (bdEl) {
        bdEl.innerText = `${s.wins_count || 0} W • ${s.losses_count || 0} L • ${s.breakeven_count || 0} BE`;
    }

    const wrEl = document.getElementById('journal-metric-winrate');
    if (wrEl) wrEl.innerText = `${(s.win_rate_pct || 0).toFixed(1)}%`;

    const netEl = document.getElementById('journal-metric-net-pnl');
    if (netEl) {
        const val = s.total_net_pnl_usd || 0;
        netEl.innerText = `${val >= 0 ? '+$' : '-$'}${Math.abs(val).toFixed(2)}`;
        netEl.className = `text-base font-bold mt-1 ${val > 0.0001 ? 'text-emerald-400' : (val < -0.0001 ? 'text-rose-400' : 'text-slate-300')}`;
    }

    const grossEl = document.getElementById('journal-metric-gross-pnl');
    if (grossEl) {
        const gVal = s.total_gross_pnl_usd || 0;
        grossEl.innerText = `Gross: ${gVal >= 0 ? '$' : '-$'}${Math.abs(gVal).toFixed(2)}`;
    }

    const avgREl = document.getElementById('journal-metric-avg-r');
    if (avgREl) {
        const rVal = s.average_realized_r || 0;
        avgREl.innerText = `${rVal >= 0 ? '+' : ''}${rVal.toFixed(3)} R`;
        avgREl.className = `text-base font-bold mt-1 ${rVal > 0.001 ? 'text-emerald-400' : (rVal < -0.001 ? 'text-rose-400' : 'text-slate-300')}`;
    }

    const pfEl = document.getElementById('journal-metric-profit-factor');
    if (pfEl) pfEl.innerText = (s.profit_factor || 0).toFixed(2);

    const feesEl = document.getElementById('journal-metric-fees');
    if (feesEl) feesEl.innerText = `Fees: $${(s.total_fees_paid_usd || 0).toFixed(4)}`;

    const openEl = document.getElementById('journal-metric-open-trades');
    if (openEl) {
        const openCnt = s.open_positions_count !== undefined ? s.open_positions_count : (s.open_count || 0);
        openEl.innerText = `${openCnt} Open`;
        openEl.className = `text-base font-bold mt-1 ${openCnt > 0 ? 'text-blue-400 animate-pulse' : 'text-slate-400'}`;
    }

    const dailyLossEl = document.getElementById('journal-metric-daily-loss');
    if (dailyLossEl) {
        const used = s.daily_loss_usage_usd || 0;
        const cap = s.daily_loss_limit_usd || 0.45;
        dailyLossEl.innerText = `$${used.toFixed(2)} / $${cap.toFixed(2)}`;
        dailyLossEl.className = `text-base font-bold mt-1 ${used >= cap ? 'text-rose-400 font-extrabold' : (used > 0 ? 'text-amber-400' : 'text-slate-300')}`;
    }

    const dailyEntriesEl = document.getElementById('journal-metric-daily-entries');
    if (dailyEntriesEl) {
        const rem = s.remaining_entries !== undefined ? s.remaining_entries : 3;
        dailyEntriesEl.innerText = `${rem} remaining`;
        dailyEntriesEl.className = `text-base font-bold mt-1 ${rem === 0 ? 'text-rose-400' : 'text-white'}`;
    }

    const reconLabel = document.getElementById('journalReconciliationStatusLabel');
    const reconcileBadge = document.getElementById('reconcileBadge');
    if (s.reconciliation_ok) {
        if (reconLabel) {
            reconLabel.innerText = 'ALL RECONCILED';
            reconLabel.className = 'text-emerald-400 font-bold';
        }
        if (reconcileBadge) {
            reconcileBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-blue-950 border border-blue-500 text-blue-300';
            reconcileBadge.innerText = 'IDEMPOTENT RECONCILED';
        }
    } else {
        if (reconLabel) {
            reconLabel.innerText = 'DISCREPANCIES DETECTED';
            reconLabel.className = 'text-rose-400 font-bold animate-pulse';
        }
        if (reconcileBadge) {
            reconcileBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 border border-rose-500 text-rose-300';
            reconcileBadge.innerText = 'REQUIRES RECONCILIATION';
        }
    }
}

function renderJournalTable() {
    const tbody = document.getElementById('journalTableBody');
    const emptyState = document.getElementById('journalEmptyState');
    const countEl = document.getElementById('journalTableRecordCount');

    let trades = [...(lastFetchedTrades || [])];

    // Client-side search filtering (symbol or trade_id)
    if (journalSearchQuery) {
        const q = journalSearchQuery.toUpperCase();
        trades = trades.filter(t => {
            const sym = (t.symbol || '').toUpperCase();
            const tid = (t.trade_id || '').toUpperCase();
            const oid = (t.order_id || '').toUpperCase();
            return sym.includes(q) || tid.includes(q) || oid.includes(q);
        });
    }

    // Client-side side filtering
    if (journalSideFilter && journalSideFilter !== 'ALL') {
        trades = trades.filter(t => (t.side || '').toUpperCase() === journalSideFilter);
    }

    // Sorting logic
    trades.sort((a, b) => {
        if (journalSortBy === 'NEWEST') {
            const ta = a.first_fill_timestamp_utc || a.created_at_utc || '';
            const tb = b.first_fill_timestamp_utc || b.created_at_utc || '';
            return tb.localeCompare(ta);
        } else if (journalSortBy === 'OLDEST') {
            const ta = a.first_fill_timestamp_utc || a.created_at_utc || '';
            const tb = b.first_fill_timestamp_utc || b.created_at_utc || '';
            return ta.localeCompare(tb);
        } else if (journalSortBy === 'PNL_DESC') {
            return (b.net_pnl_usd || 0) - (a.net_pnl_usd || 0);
        } else if (journalSortBy === 'PNL_ASC') {
            return (a.net_pnl_usd || 0) - (b.net_pnl_usd || 0);
        } else if (journalSortBy === 'R_DESC') {
            const ra = a.status === 'CLOSED' ? (a.realized_r || (a.net_pnl_usd / (a.planned_risk_usd || 0.20))) : -999;
            const rb = b.status === 'CLOSED' ? (b.realized_r || (b.net_pnl_usd / (b.planned_risk_usd || 0.20))) : -999;
            return rb - ra;
        } else if (journalSortBy === 'R_ASC') {
            const ra = a.status === 'CLOSED' ? (a.realized_r || (a.net_pnl_usd / (a.planned_risk_usd || 0.20))) : 999;
            const rb = b.status === 'CLOSED' ? (b.realized_r || (b.net_pnl_usd / (b.planned_risk_usd || 0.20))) : 999;
            return ra - rb;
        }
        return 0;
    });

    if (countEl) {
        countEl.innerText = `Showing ${trades.length} trade record${trades.length === 1 ? '' : 's'}`;
    }

    if (!trades || trades.length === 0) {
        if (tbody) tbody.innerHTML = '';
        if (emptyState) {
            emptyState.classList.remove('hidden');
            const title = document.getElementById('journalEmptyTitle');
            const msg = document.getElementById('journalEmptyMessage');
            if (title) title.innerText = 'No Trade Records Found';
            if (msg) msg.innerHTML = 'No automated trade executions found matching the selected filters. <button onclick="resetJournalFilters()" class="text-indigo-400 hover:text-indigo-300 underline font-semibold ml-1 cursor-pointer">Reset Filters</button>';
        }
        return;
    }

    if (emptyState) emptyState.classList.add('hidden');

    tbody.innerHTML = trades.map(t => {
        const isLong = (t.side || 'LONG').toUpperCase() === 'LONG';
        const sideColor = isLong ? 'text-emerald-400' : 'text-rose-400';
        const sideBadge = isLong ? 'bg-emerald-950/80 text-emerald-300 border-emerald-600' : 'bg-rose-950/80 text-rose-300 border-rose-600';

        const isClosed = (t.status || '').toUpperCase() === 'CLOSED';
        const isOpen = (t.status || '').toUpperCase() === 'OPEN';

        // Mode Badge
        let modeBadge = 'bg-slate-800 text-slate-300 border-slate-700';
        if (t.mode === 'LIVE') modeBadge = 'bg-emerald-950/90 text-emerald-300 border-emerald-500 font-bold';
        else if (t.mode === 'PAPER') modeBadge = 'bg-sky-950/90 text-sky-300 border-sky-500 font-bold';
        else if (t.mode === 'SHADOW') modeBadge = 'bg-purple-950/90 text-purple-300 border-purple-500 font-bold';

        // Status Badge
        let statusBadge = 'bg-slate-800 text-slate-300 border-slate-700';
        if (isOpen) statusBadge = 'bg-blue-950/90 text-blue-300 border-blue-500 font-bold animate-pulse';
        else if (isClosed) statusBadge = 'bg-slate-800 text-slate-200 border-slate-600 font-semibold';
        else if (t.status === 'CANCELLED') statusBadge = 'bg-amber-950 text-amber-300 border-amber-600';
        else if (t.status === 'REJECTED') statusBadge = 'bg-rose-950 text-rose-300 border-rose-600';

        // Reconciliation Badge
        let reconBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950/80 border border-emerald-500 text-emerald-300">RECONCILED</span>';
        if (t.reconciliation_status === 'REQUIRES_RECONCILIATION') {
            reconBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950/90 border border-rose-500 text-rose-300 animate-pulse">REQUIRES RECON</span>';
        } else if (t.reconciliation_status === 'DISCREPANCY') {
            reconBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 border border-amber-500 text-amber-300">DISCREPANCY</span>';
        } else if (t.reconciliation_status === 'UNVERIFIED' || t.data_quality_flags === 'UNAVAILABLE') {
            reconBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 border border-amber-500 text-amber-300">UNVERIFIED</span>';
        }

        // Realized R: CRITICAL DATA INTEGRITY GUARANTEE
        // Do not calculate Realized R for open trades; open trades must show "--"
        let realizedRDisplay = '--';
        let realizedRColor = 'text-slate-500';
        if (isClosed) {
            const risk = t.planned_risk_usd && t.planned_risk_usd > 0 ? t.planned_risk_usd : 0.20;
            const rVal = (t.net_pnl_usd || 0) / risk;
            realizedRDisplay = `${rVal >= 0 ? '+' : ''}${rVal.toFixed(3)} R`;
            realizedRColor = rVal > 0.001 ? 'text-emerald-400 font-bold' : (rVal < -0.001 ? 'text-rose-400 font-bold' : 'text-slate-300');
        }

        // Net PnL display
        let netPnlDisplay = '$0.00';
        let netPnlColor = 'text-slate-400';
        if (isClosed) {
            const pnl = t.net_pnl_usd || 0;
            netPnlDisplay = `${pnl >= 0 ? '+$' : '-$'}${Math.abs(pnl).toFixed(4)}`;
            netPnlColor = pnl > 0.0001 ? 'text-emerald-400 font-bold' : (pnl < -0.0001 ? 'text-rose-400 font-bold' : 'text-slate-300');
        } else if (isOpen) {
            netPnlDisplay = 'Floating';
            netPnlColor = 'text-blue-400 font-semibold';
        }

        const entryTime = t.first_fill_timestamp_utc || t.signal_timestamp_utc || t.created_at_utc;
        const exitTime = t.exit_timestamp_utc;
        const isExpanded = expandedTrades.has(t.trade_id);

        const tradeIdShort = t.trade_id.length > 22 ? t.trade_id.substring(0, 22) + '...' : t.trade_id;

        // Protection status badge
        let protBadge = `<span class="text-[9px] px-1 py-0.2 rounded bg-slate-900 border border-slate-700 text-slate-400">${t.protection_status || 'UNPROTECTED'}</span>`;
        if (t.protection_status === 'PROTECTED') {
            protBadge = `<span class="text-[9px] px-1 py-0.2 rounded bg-emerald-950 border border-emerald-600 text-emerald-300">PROTECTED</span>`;
        } else if ((t.protection_status || '').includes('PARTIALLY')) {
            protBadge = `<span class="text-[9px] px-1 py-0.2 rounded bg-amber-950 border border-amber-600 text-amber-300">SL ONLY (TP MISSING)</span>`;
        }

        return `
            <tr class="hover:bg-slate-800/40 text-xs font-mono transition cursor-pointer border-b border-slate-800/60" onclick="toggleTradeDetails('${t.trade_id}')">
                <td class="px-2.5 py-3 text-center">
                    <button class="w-5 h-5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 flex items-center justify-center font-bold text-xs border border-slate-700">
                        ${isExpanded ? '−' : '+'}
                    </button>
                </td>
                <td class="px-3 py-3">
                    <div class="font-bold text-white text-[11px]">${formatUtcTime(entryTime)}</div>
                    <div class="text-[10px] text-slate-400">${formatJakartaTime(entryTime)}</div>
                </td>
                <td class="px-2.5 py-3">
                    <span class="px-2 py-0.5 rounded text-[10px] border ${modeBadge}">${t.mode || 'LIVE'}</span>
                </td>
                <td class="px-3 py-3">
                    <div class="font-bold text-white flex items-center gap-1.5">
                        <span>${t.symbol}</span>
                        <span class="px-1.5 py-0.2 rounded text-[10px] border ${sideBadge}">${t.side}</span>
                    </div>
                    <div class="text-[10px] text-slate-500 font-mono">${t.trade_id}</div>
                </td>
                <td class="px-3 py-3">
                    <div class="text-indigo-300 font-semibold">${t.strategy_id}</div>
                    <div class="text-[10px] text-slate-500">${t.config_version || 'LIVE_MICRO_V1'}</div>
                </td>
                <td class="px-3 py-3">
                    <div class="text-white font-semibold">${formatPrice(t.average_entry_price || t.planned_entry_price)}</div>
                    <div class="text-[10px] text-slate-400">Plan: ${formatPrice(t.planned_entry_price)}</div>
                </td>
                <td class="px-3 py-3">
                    <div class="text-rose-400 font-semibold">${formatPrice(t.sl_price || t.planned_stop_price)}</div>
                    <div class="text-[10px] text-slate-500">Plan: ${formatPrice(t.planned_stop_price)}</div>
                </td>
                <td class="px-3 py-3">
                    <div class="text-emerald-400 font-semibold">${formatPrice(t.tp_price || t.planned_take_profit_price)}</div>
                    <div class="text-[10px] text-slate-500">Plan: ${formatPrice(t.planned_take_profit_price)}</div>
                </td>
                <td class="px-3 py-3">
                    <div class="text-white font-semibold">${t.filled_quantity || t.planned_quantity || '--'}</div>
                    <div class="text-[10px] text-slate-400">${formatCurrency(t.planned_notional_usd)}</div>
                </td>
                <td class="px-2.5 py-3 text-slate-300">
                    <div>${t.leverage || 10}x</div>
                    <div class="text-[9px] text-slate-500">${t.margin_mode || 'ISOLATED'}</div>
                </td>
                <td class="px-3 py-3 text-amber-400">
                    <div>$${(t.entry_fee_usd || 0).toFixed(4)}</div>
                    <div class="text-[10px] text-slate-500">Slip: ${(t.entry_slippage_pct || 0).toFixed(2)}%</div>
                </td>
                <td class="px-3 py-3">
                    <div class="${t.average_exit_price ? 'text-white font-semibold' : 'text-slate-500'}">${t.average_exit_price ? formatPrice(t.average_exit_price) : '--'}</div>
                    <div class="text-[10px] text-slate-400">${exitTime ? formatUtcTime(exitTime) : (isOpen ? 'In Progress' : '--')}</div>
                </td>
                <td class="px-3 py-3 text-amber-400">
                    <div>$${(t.exit_fee_usd || 0).toFixed(4)}</div>
                    <div class="text-[10px] text-slate-500">Tot: $${(t.total_fees_usd || 0).toFixed(4)}</div>
                </td>
                <td class="px-3 py-3">
                    <div class="${netPnlColor}">${netPnlDisplay}</div>
                    <div class="text-[10px] text-slate-500 font-sans">${t.exit_reason || (isOpen ? 'Active' : '--')}</div>
                </td>
                <td class="px-2.5 py-3 text-slate-300">
                    $${(t.planned_risk_usd || 0.20).toFixed(2)}
                </td>
                <td class="px-3 py-3">
                    <span class="text-xs ${realizedRColor}">${realizedRDisplay}</span>
                </td>
                <td class="px-3 py-3">
                    <div><span class="px-1.5 py-0.5 rounded text-[10px] border ${statusBadge}">${t.status}</span></div>
                    <div class="mt-1">${protBadge}</div>
                </td>
                <td class="px-3 py-3">
                    ${reconBadge}
                </td>
            </tr>

            <!-- EXPANDABLE TRADE DETAILS & FILL LEDGER ACCORDION -->
            <tr id="trade-details-${t.trade_id}" class="${isExpanded ? '' : 'hidden'} bg-slate-950/80 border-b border-indigo-900/40">
                <td colspan="18" class="p-4 space-y-3">
                    <div class="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs bg-slate-900/90 p-3 rounded-lg border border-slate-800 font-mono">
                        <div>
                            <span class="text-slate-500 block uppercase text-[10px]">Trade / Order References</span>
                            <div class="text-white font-bold truncate" title="${t.trade_id}">Trade ID: ${t.trade_id}</div>
                            <div class="text-slate-300">Entry Order ID: <span class="text-indigo-400">${t.order_id || '--'}</span></div>
                            <div class="text-slate-300">Stop Loss Order: <span class="text-rose-400">${t.sl_order_id || '--'} (${t.sl_status || 'NOT_PLACED'})</span></div>
                            <div class="text-slate-300">Take Profit Order: <span class="text-emerald-400">${t.tp_order_id || '--'} (${t.tp_status || 'NOT_PLACED'})</span></div>
                        </div>
                        <div>
                            <span class="text-slate-500 block uppercase text-[10px]">Planned vs Actual Metrics</span>
                            <div>Planned Entry: <span class="text-slate-300">${formatPrice(t.planned_entry_price)}</span> &bull; Actual: <span class="text-white font-bold">${formatPrice(t.average_entry_price)}</span></div>
                            <div>Planned SL: <span class="text-slate-300">${formatPrice(t.planned_stop_price)}</span> &bull; Actual: <span class="text-rose-400 font-bold">${formatPrice(t.sl_price)}</span></div>
                            <div>Planned TP: <span class="text-slate-300">${formatPrice(t.planned_take_profit_price)}</span> &bull; Actual: <span class="text-emerald-400 font-bold">${formatPrice(t.tp_price)}</span></div>
                            <div>Holding Duration: <span class="text-white">${(t.holding_duration_seconds || 0).toFixed(1)}s</span></div>
                        </div>
                        <div>
                            <span class="text-slate-500 block uppercase text-[10px]">Financial Execution PnL</span>
                            <div>Gross Realized: <span class="text-white font-semibold">${formatCurrency(t.gross_pnl_usd)}</span></div>
                            <div>Entry Fee: <span class="text-amber-400">$${(t.entry_fee_usd || 0).toFixed(6)}</span> &bull; Exit Fee: <span class="text-amber-400">$${(t.exit_fee_usd || 0).toFixed(6)}</span></div>
                            <div>Funding Paid: <span class="text-slate-300">$${(t.funding_usd || 0).toFixed(6)}</span></div>
                            <div>Total Friction: <span class="text-amber-400 font-bold">$${(t.total_fees_usd || 0).toFixed(6)}</span></div>
                            <div>Net Realized PnL: <span class="${netPnlColor}">${netPnlDisplay}</span></div>
                        </div>
                        <div>
                            <span class="text-slate-500 block uppercase text-[10px]">Audit, Reason & Notes</span>
                            <div class="text-slate-300"><strong class="text-indigo-400">Trigger:</strong> ${t.trigger_reason || '5m EMA20 Reclaim-Pullback'}</div>
                            <div class="text-slate-300 truncate" title="${t.llm_reason}"><strong class="text-indigo-400">LLM Selection:</strong> ${t.llm_reason || 'Deterministic strategy rule'}</div>
                            <div class="text-slate-400 text-[11px] mt-1 break-words">${t.notes ? `<span class="text-amber-400/90 font-semibold">Notes:</span> ${t.notes}` : '<span class="text-slate-600">No discrepancies noted. Fully reconciled.</span>'}</div>
                        </div>
                    </div>

                    <!-- Granular Fill Ledger Table -->
                    <div class="bg-slate-900/60 p-3 rounded-lg border border-slate-800 space-y-2">
                        <div class="flex items-center justify-between">
                            <span class="text-xs font-bold text-white flex items-center gap-1.5">
                                <span>🧾 Granular Fill Ledger</span>
                                <span class="text-[10px] font-normal text-slate-400">(Aster DEX userTrades / fill_ledger)</span>
                            </span>
                            <button onclick="fetchFillsForTrade('${t.trade_id}', true)" class="px-2 py-0.5 text-[10px] bg-slate-800 hover:bg-slate-700 text-slate-300 rounded border border-slate-700 transition">
                                Reload Fills
                            </button>
                        </div>
                        <div id="fills-container-${t.trade_id}">
                            <div class="text-xs text-slate-400 py-2">Loading fill records from exchange ledger...</div>
                        </div>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    // Trigger lazy loading of fills for any expanded trades
    expandedTrades.forEach(tid => {
        loadFillsForTrade(tid);
    });
}

function toggleTradeDetails(tradeId) {
    if (expandedTrades.has(tradeId)) {
        expandedTrades.delete(tradeId);
        const row = document.getElementById(`trade-details-${tradeId}`);
        if (row) row.classList.add('hidden');
        renderJournalTable();
    } else {
        expandedTrades.add(tradeId);
        const row = document.getElementById(`trade-details-${tradeId}`);
        if (row) row.classList.remove('hidden');
        renderJournalTable();
        loadFillsForTrade(tradeId);
    }
}

async function loadFillsForTrade(tradeId) {
    const container = document.getElementById(`fills-container-${tradeId}`);
    if (!container) return;

    if (cachedFillsByTrade[tradeId]) {
        renderFillsTable(container, cachedFillsByTrade[tradeId]);
        return;
    }

    try {
        const res = await fetch(`/api/v1/journal/fills?trade_id=${tradeId}`);
        if (!res.ok) {
            container.innerHTML = `<div class="text-xs text-rose-400">Failed to fetch fills (HTTP ${res.status}).</div>`;
            return;
        }
        const data = await res.json();
        cachedFillsByTrade[tradeId] = data.fills || [];
        renderFillsTable(container, cachedFillsByTrade[tradeId]);
    } catch (e) {
        container.innerHTML = `<div class="text-xs text-rose-400">Error loading fills: ${e.message}</div>`;
    }
}

async function fetchFillsForTrade(tradeId, force = false) {
    if (force) delete cachedFillsByTrade[tradeId];
    loadFillsForTrade(tradeId);
}

function renderFillsTable(container, fills) {
    if (!fills || fills.length === 0) {
        container.innerHTML = `
            <div class="text-xs text-slate-500 py-2 font-mono">
                No individual fill ledger records registered for this trade yet (Pending exchange fill reconciliation).
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="overflow-x-auto">
            <table class="w-full text-left text-[11px] font-mono">
                <thead class="text-slate-400 bg-slate-950/80 border-b border-slate-800">
                    <tr>
                        <th class="px-2.5 py-1.5">Timestamp (UTC)</th>
                        <th class="px-2.5 py-1.5">Fill ID / Trade ID</th>
                        <th class="px-2.5 py-1.5">Order ID</th>
                        <th class="px-2.5 py-1.5">Role</th>
                        <th class="px-2.5 py-1.5">Side</th>
                        <th class="px-2.5 py-1.5">Fill Price</th>
                        <th class="px-2.5 py-1.5">Quantity</th>
                        <th class="px-2.5 py-1.5">Notional</th>
                        <th class="px-2.5 py-1.5">Fee Paid</th>
                        <th class="px-2.5 py-1.5">Liquidity</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-slate-800/60">
                    ${fills.map(f => {
                        const isBuy = (f.side || '').toUpperCase() === 'BUY';
                        const sideColor = isBuy ? 'text-emerald-400' : 'text-rose-400';
                        const roleBadge = f.fill_role === 'ENTRY' ? 'text-indigo-300' : 'text-amber-300';
                        const liqColor = f.liquidity_type === 'MAKER' ? 'text-emerald-400 font-bold' : 'text-slate-400';
                        return `
                            <tr class="hover:bg-slate-800/30">
                                <td class="px-2.5 py-1 text-slate-300">${formatUtcTime(f.timestamp_utc)}</td>
                                <td class="px-2.5 py-1 text-slate-400 truncate max-w-[140px]" title="${f.fill_id}">${f.exchange_trade_id || f.fill_id}</td>
                                <td class="px-2.5 py-1 text-indigo-400">${f.order_id}</td>
                                <td class="px-2.5 py-1 ${roleBadge} font-semibold">${f.fill_role}</td>
                                <td class="px-2.5 py-1 ${sideColor} font-bold">${f.side}</td>
                                <td class="px-2.5 py-1 text-white font-semibold">${formatPrice(f.fill_price)}</td>
                                <td class="px-2.5 py-1 text-slate-200">${f.fill_quantity}</td>
                                <td class="px-2.5 py-1 text-slate-300">${formatCurrency(f.fill_notional_usd)}</td>
                                <td class="px-2.5 py-1 text-amber-400">$${(f.fee_usd || 0).toFixed(6)} ${f.fee_asset || 'USDT'}</td>
                                <td class="px-2.5 py-1 ${liqColor}">${f.liquidity_type || 'UNKNOWN'}</td>
                            </tr>
                        `;
                    }).join('')}
                </tbody>
            </table>
        </div>
    `;
}

async function triggerReconciliation() {
    const btn = document.getElementById('reconcileBtn');
    const badge = document.getElementById('reconcileBadge');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>Reconciling...</span>';
    }
    try {
        const resp = await fetch('/api/v1/journal/reconcile', { method: 'POST' });
        const data = await resp.json();
        if (typeof showNotice === 'function') {
            showNotice(`Reconciliation complete: ${data.status} (${data.new_fills_imported || 0} new fills, ${data.discrepancies_found || 0} discrepancies)`, 'info');
        }
        if (badge) {
            if (data.status === 'OK' && (data.discrepancies_found || 0) === 0) {
                badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-blue-950 border border-blue-500 text-blue-300';
                badge.innerText = 'IDEMPOTENT RECONCILED';
            } else {
                badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 border border-rose-500 text-rose-300';
                badge.innerText = 'REQUIRES RECONCILIATION';
            }
        }
        // Invalidate cache
        cachedFillsByTrade = {};
        await loadJournal();
    } catch (e) {
        if (typeof showNotice === 'function') showNotice(`Reconciliation failed: ${e.message}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg><span>Reconcile AsterDEX</span>';
        }
    }
}

// =========================================================================
// GLOBAL EXPORTS & INITIALIZATION
// =========================================================================
window.setJournalMode = setJournalMode;
window.updateExportLinks = updateExportLinks;
window.handleJournalSearchInput = handleJournalSearchInput;
window.handleJournalStrategyChange = handleJournalStrategyChange;
window.handleJournalSideChange = handleJournalSideChange;
window.handleJournalStatusChange = handleJournalStatusChange;
window.handleJournalReconChange = handleJournalReconChange;
window.handleJournalDateChange = handleJournalDateChange;
window.handleJournalSortChange = handleJournalSortChange;
window.resetJournalFilters = resetJournalFilters;
window.loadJournal = loadJournal;
window.renderJournalTable = renderJournalTable;
window.renderJournalSummary = renderJournalSummary;
window.toggleTradeDetails = toggleTradeDetails;
window.triggerReconciliation = triggerReconciliation;

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        updateExportLinks();
        const tabParam = new URLSearchParams(window.location.search).get('tab');
        if (tabParam === 'journal' || window.location.hash === '#tab-journal') {
            loadJournal();
        }
    });
} else {
    updateExportLinks();
    const tabParam = new URLSearchParams(window.location.search).get('tab');
    if (tabParam === 'journal' || window.location.hash === '#tab-journal') {
        loadJournal();
    }
}
