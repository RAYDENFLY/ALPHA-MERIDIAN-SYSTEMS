// =========================================================================
// TRADING JOURNAL & PERFORMANCE ANALYTICS
// =========================================================================
        function setJournalModelFilter(model) {
            journalModelFilter = model;
            if (typeof resetPaginationPage === "function") resetPaginationPage("journal");
            ['ALL', 'MODEL_A', 'MODEL_B'].forEach(m => {
                const btn = document.getElementById(`jModel-${m}`);
                if (btn) {
                    if (m === model) {
                        btn.className = 'px-3 py-1.5 text-xs font-semibold rounded-lg bg-indigo-600 text-white border border-indigo-500 shadow-sm transition flex items-center gap-1.5';
                    } else {
                        btn.className = 'px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1.5';
                    }
                }
            });

            const scopeLabel = document.getElementById('journalModelScopeLabel');
            if (scopeLabel) {
                if (model === 'MODEL_A') {
                    scopeLabel.innerHTML = 'Active Scope: <strong class="text-indigo-400">Model A — Defensive SMC/SnR (Frozen Forward Cohort)</strong>';
                } else if (model === 'MODEL_B') {
                    scopeLabel.innerHTML = 'Active Scope: <strong class="text-amber-400">Model B — Aggressive Liquidity & Catalyst (Research Engine)</strong>';
                } else {
                    scopeLabel.innerHTML = 'Active Scope: <strong class="text-indigo-300">All Models Combined</strong>';
                }
            }

            loadJournal();
        }

        function setJournalOutcomeFilter(outcome) {
            journalOutcomeFilter = outcome;
            if (typeof resetPaginationPage === "function") resetPaginationPage("journal");
            ['ALL', 'WIN', 'LOSS', 'BREAKEVEN', 'OPEN'].forEach(o => {
                const btn = document.getElementById(`jOutcome-${o}`);
                if (btn) {
                    if (o === outcome) btn.classList.add('filter-btn-active');
                    else btn.classList.remove('filter-btn-active');
                }
            });
            loadJournal();
        }

        function handleJournalCategoryChange(val) {
            journalCategoryFilter = val;
            if (typeof resetPaginationPage === "function") resetPaginationPage("journal");
            loadJournal();
        }

        function handleJournalDirectionChange(val) {
            journalDirectionFilter = val;
            if (typeof resetPaginationPage === "function") resetPaginationPage("journal");
            loadJournal();
        }

        function handleJournalPeriodChange(val) {
            journalPeriodFilter = val;
            if (typeof resetPaginationPage === "function") resetPaginationPage("journal");
            loadJournal();
        }

        function handleJournalSearchInput(val) {
            journalSearchQuery = val.trim();
            if (typeof resetPaginationPage === "function") resetPaginationPage("journal");
            loadJournal();
        }

        function resetJournalFilters() {
            if (typeof resetPaginationPage === "function") resetPaginationPage("journal");
            journalOutcomeFilter = 'ALL';
            journalCategoryFilter = 'ALL';
            journalDirectionFilter = 'ALL';
            journalPeriodFilter = 'all';
            journalSearchQuery = '';

            ['ALL', 'WIN', 'LOSS', 'BREAKEVEN', 'OPEN'].forEach(o => {
                const btn = document.getElementById(`jOutcome-${o}`);
                if (btn) {
                    if (o === 'ALL') btn.classList.add('filter-btn-active');
                    else btn.classList.remove('filter-btn-active');
                }
            });

            const catSel = document.getElementById('journalCategorySelect');
            if (catSel) catSel.value = 'ALL';
            const dirSel = document.getElementById('journalDirectionSelect');
            if (dirSel) dirSel.value = 'ALL';
            const perSel = document.getElementById('journalPeriodSelect');
            if (perSel) perSel.value = 'all';
            const symInp = document.getElementById('journalSymbolInput');
            if (symInp) symInp.value = '';

            // Preserves journalModelFilter as requested
            loadJournal();
        }

        async function loadJournal() {
            const refreshBtn = document.getElementById('refreshJournalBtn');
            if (refreshBtn) refreshBtn.classList.add('opacity-75');

            try {
                const params = new URLSearchParams();
                if (journalModelFilter && journalModelFilter !== 'ALL') params.append('model_id', journalModelFilter);
                if (journalOutcomeFilter && journalOutcomeFilter !== 'ALL') params.append('outcome', journalOutcomeFilter);
                if (journalCategoryFilter && journalCategoryFilter !== 'ALL') params.append('category', journalCategoryFilter);
                if (journalDirectionFilter && journalDirectionFilter !== 'ALL') params.append('direction', journalDirectionFilter);
                if (journalPeriodFilter && journalPeriodFilter !== 'all') params.append('period', journalPeriodFilter);
                if (journalSearchQuery) params.append('symbol', journalSearchQuery);
                params.append('limit', '100');

                const res = await fetch(`/api/trades?${params.toString()}`);
                if (!res.ok) return;
                const data = await res.json();

                // Metrics calculation (strictly isolating realized closed trades from floating open trades)
                const m = data.filtered_metrics || data.metrics || {};
                const closedCount = m.closed_trades_count || 0;
                const winsCount = m.wins_count || 0;
                const lossesCount = m.losses_count || 0;
                const beCount = m.breakeven_count || 0;
                const winRate = m.win_rate_pct !== undefined ? m.win_rate_pct.toFixed(1) : '0.0';
                const grossPnl = m.gross_pnl !== undefined ? m.gross_pnl.toFixed(2) : '0.00';
                const totalFees = m.total_fees !== undefined ? m.total_fees.toFixed(2) : '0.00';
                const netPnl = m.net_pnl !== undefined ? m.net_pnl.toFixed(2) : '0.00';
                const avgNetPnl = m.avg_net_pnl !== undefined ? m.avg_net_pnl.toFixed(2) : '0.00';
                const openTradesCount = m.open_trades_count !== undefined ? m.open_trades_count : (data.metrics ? data.metrics.open_trades_count : 0);

                const closedEl = document.getElementById('journal-metric-closed-trades');
                if (closedEl) closedEl.innerText = closedCount;

                const bdEl = document.getElementById('journal-metric-trades-breakdown');
                if (bdEl) bdEl.innerText = `${winsCount} Wins \u2022 ${lossesCount} Losses \u2022 ${beCount} BE`;

                const wrEl = document.getElementById('journal-metric-winrate');
                if (wrEl) wrEl.innerText = `${winRate}%`;

                const grossEl = document.getElementById('journal-metric-gross-pnl');
                if (grossEl) {
                    const grossNum = parseFloat(grossPnl);
                    grossEl.innerText = `${grossNum >= 0 ? '$' : '-$'}${Math.abs(grossNum).toFixed(2)}`;
                    grossEl.className = `text-lg font-bold mt-1 ${grossNum > 0 ? 'text-emerald-400' : (grossNum < 0 ? 'text-red-400' : 'text-slate-300')}`;
                }

                const feesEl = document.getElementById('journal-metric-fees');
                if (feesEl) feesEl.innerText = `$${totalFees}`;

                const netEl = document.getElementById('journal-metric-net-pnl');
                if (netEl) {
                    const netNum = parseFloat(netPnl);
                    netEl.innerText = `${netNum >= 0 ? '$' : '-$'}${Math.abs(netNum).toFixed(2)}`;
                    netEl.className = `text-lg font-bold mt-1 ${netNum > 0 ? 'text-emerald-400' : (netNum < 0 ? 'text-red-400' : 'text-slate-300')}`;
                }

                const avgEl = document.getElementById('journal-metric-avg-pnl');
                if (avgEl) {
                    const avgNum = parseFloat(avgNetPnl);
                    avgEl.innerText = `${avgNum >= 0 ? '$' : '-$'}${Math.abs(avgNum).toFixed(2)}`;
                    avgEl.className = `text-lg font-bold mt-1 ${avgNum > 0 ? 'text-emerald-400' : (avgNum < 0 ? 'text-red-400' : 'text-slate-300')}`;
                }

                // Update per-model breakdown indicators
                const pmm = data.per_model_metrics || {};
                const ma = pmm.MODEL_A || {};
                const mb = pmm.MODEL_B || {};
                const maEl = document.getElementById('journal-scope-model-a');
                if (maEl) {
                    const maNet = (ma.net_pnl || 0).toFixed(2);
                    maEl.innerText = `${ma.closed_trades_count || 0} Closed \u2022 ${ma.open_trades_count || 0} Open \u2022 $${maNet} Net Realized`;
                }
                const mbEl = document.getElementById('journal-scope-model-b');
                if (mbEl) {
                    const mbNet = (mb.net_pnl || 0).toFixed(2);
                    const mbNotional = mb.open_trades_notional ? ` ($${mb.open_trades_notional.toFixed(2)} Notional)` : '';
                    mbEl.innerText = `${mb.closed_trades_count || 0} Closed \u2022 ${mb.open_trades_count || 0} Open \u2022 $${mbNet} Net Realized${mbNotional}`;
                }

                const openEl = document.getElementById('journal-metric-open-trades');
                if (openEl) {
                    if (journalModelFilter === 'MODEL_A') {
                        openEl.innerText = `Active Open Positions: ${m.open_trades_count || 0} (Model A)`;
                    } else if (journalModelFilter === 'MODEL_B') {
                        openEl.innerText = `Active Open Positions: ${m.open_trades_count || 0} (Model B)`;
                    } else {
                        openEl.innerText = `Active Open Positions: ${openTradesCount} (Model A: ${ma.open_trades_count || 0} \u2022 Model B: ${mb.open_trades_count || 0})`;
                    }
                }

                lastJournalTrades = data.trades || [];
                renderJournalRows();
            } catch (err) {
                console.error("Journal polling error:", err);
            } finally {
                if (refreshBtn) refreshBtn.classList.remove('opacity-75');
            }
        }

        let lastJournalTrades = [];

        function renderJournalRows() {
            const tbody = document.getElementById('journalTableBody');
            const emptyState = document.getElementById('journalEmptyState');
            const pEl = document.getElementById('journalPaginationContainer');

            if (!lastJournalTrades || lastJournalTrades.length === 0) {
                if (tbody) tbody.innerHTML = '';
                if (pEl) pEl.innerHTML = '';
                if (emptyState) {
                    emptyState.classList.remove('hidden');
                    const emptyTitle = document.getElementById('journalEmptyTitle');
                    const emptyMsg = document.getElementById('journalEmptyMessage');
                    const hasActiveFilter = (journalModelFilter !== 'ALL') || (journalOutcomeFilter !== 'ALL') || (journalCategoryFilter !== 'ALL') || (journalDirectionFilter !== 'ALL') || Boolean(journalSearchQuery) || (journalPeriodFilter !== 'all');
                    if (hasActiveFilter) {
                        if (emptyTitle) emptyTitle.innerText = 'No Trades Match Filter Criteria';
                        if (emptyMsg) emptyMsg.innerText = 'No trade executions match the active filters for the selected model scope. Try resetting filters or switching the model selector.';
                    } else {
                        if (emptyTitle) emptyTitle.innerText = 'No Paper Trades Recorded Yet';
                        if (emptyMsg) emptyMsg.innerText = 'Paper trades are recorded automatically when qualifying setups decisively fill simulated limit orders and subsequently trigger take-profit or stop-loss brackets.';
                    }
                }
                return;
            }

            if (emptyState) emptyState.classList.add('hidden');

            const fmtP = (p) => {
                if (p === undefined || p === null || isNaN(p)) return '--';
                const num = Number(p);
                if (num === 0) return '$0.00';
                if (Math.abs(num) < 0.001) return '$' + num.toFixed(6);
                if (Math.abs(num) < 1) return '$' + num.toFixed(4);
                return '$' + num.toFixed(2);
            };

            const pagedTrades = (typeof paginateList === 'function')
                ? paginateList('journal', lastJournalTrades)
                : lastJournalTrades;

            tbody.innerHTML = pagedTrades.map(t => {
                    const isLong = (t.direction || '').toUpperCase() === 'LONG';
                    const sideColor = isLong ? 'text-emerald-400' : 'text-rose-400';
                    const sideBg = isLong ? 'border-emerald-500/40 bg-emerald-950/40' : 'border-rose-500/40 bg-rose-950/40';

                    const outcome = t.outcome || (t.is_open ? 'OPEN' : 'BREAKEVEN');
                    let outcomeBadge = 'bg-slate-800 text-slate-300 border-slate-700';
                    if (outcome === 'WIN') outcomeBadge = 'bg-emerald-950/80 text-emerald-300 border-emerald-600 font-bold';
                    else if (outcome === 'LOSS') outcomeBadge = 'bg-rose-950/80 text-rose-300 border-rose-600 font-bold';
                    else if (outcome === 'BREAKEVEN') outcomeBadge = 'bg-slate-800 text-slate-300 border-slate-600';
                    else if (outcome === 'OPEN') outcomeBadge = 'bg-blue-950/80 text-blue-300 border-blue-600 font-bold animate-pulse';

                    const cat = t.entry_category || 'Unclassified';
                    let catColor = 'text-slate-400';
                    let catIcon = '❓';
                    if (cat === 'Breakout') { catColor = 'text-indigo-300'; catIcon = '⚡'; }
                    else if (cat === 'Pullback') { catColor = 'text-sky-300'; catIcon = '🔄'; }
                    else if (cat === 'Reversal') { catColor = 'text-amber-300'; catIcon = '🔀'; }
                    else if (cat === 'Range/Mean Reversion' || cat === 'Range') { catColor = 'text-teal-300'; catIcon = '↔️'; }

                    const netPnlVal = parseFloat(t.pnl_net || 0);
                    const grossPnlVal = parseFloat(t.pnl_gross || 0);
                    const feesVal = parseFloat(t.total_fees !== undefined ? t.total_fees : ((t.fee_paid || 0) + (t.funding_paid || 0) + (t.slippage_paid || 0)));

                    const netColor = netPnlVal > 0.0001 ? 'text-emerald-400' : (netPnlVal < -0.0001 ? 'text-rose-400' : 'text-slate-300');
                    const grossColor = grossPnlVal > 0.0001 ? 'text-emerald-400' : (grossPnlVal < -0.0001 ? 'text-rose-400' : 'text-slate-300');

                    const entryTime = t.entry_time_utc ? t.entry_time_utc.substring(5, 16).replace('T', ' ') + ' UTC' : '--';
                    const exitTime = t.exit_time_utc ? t.exit_time_utc.substring(5, 16).replace('T', ' ') + ' UTC' : (t.is_open ? '<span class="text-blue-400">In Progress</span>' : '--');

                    const tradeId = t.trade_id || '--';
                    const tradeIdShort = tradeId.length > 20 ? tradeId.substring(0, 20) + '...' : tradeId;
                    const setupIdShort = t.setup_id ? `<div class="text-[10px] text-slate-500 font-mono" title="${t.setup_id}">Ref: ${t.setup_id.length > 18 ? t.setup_id.substring(0, 18) + '...' : t.setup_id}</div>` : '';

                    const modelId = (t.model_id || 'UNKNOWN').toUpperCase();
                    let modelBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 border border-slate-600 text-slate-400">UNKNOWN</span>';
                    let modelSub = '<div class="text-[9px] text-slate-500">Unclassified</div>';

                    if (modelId === 'MODEL_A') {
                        modelBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-950/90 border border-indigo-600/80 text-indigo-300">Model A</span>';
                        modelSub = '<div class="text-[9px] text-indigo-400/80">Defensive SMC/SnR</div>';
                    } else if (modelId === 'MODEL_B') {
                        modelBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950/90 border border-amber-600/80 text-amber-300">Model B</span>';
                        modelSub = '<div class="text-[9px] text-amber-400/80">Aggressive Liquidity</div>';
                    }

                    return `
                        <tr class="hover:bg-slate-800/40 text-xs font-mono transition">
                            <td class="px-3 py-3">
                                <div class="font-bold text-white">${tradeIdShort}</div>
                                ${setupIdShort}
                            </td>
                            <td class="px-3 py-3">
                                ${modelBadge}
                                ${modelSub}
                            </td>
                            <td class="px-3 py-3">
                                <div class="font-bold text-white flex items-center gap-1.5">
                                    <span>${t.symbol}</span>
                                    <span class="px-1.5 py-0.2 rounded text-[10px] border ${sideBg} ${sideColor}">${t.direction}</span>
                                </div>
                                <div class="text-[10px] text-slate-500">${t.leverage || 10.0}x Isolated</div>
                            </td>
                            <td class="px-3 py-3">
                                <div class="flex items-center gap-1 ${catColor}">
                                    <span>${catIcon}</span>
                                    <span class="font-semibold">${cat}</span>
                                </div>
                            </td>
                            <td class="px-3 py-3">
                                <div class="text-white font-semibold">${fmtP(t.entry_price)}</div>
                                <div class="text-[10px] text-slate-400">${entryTime}</div>
                            </td>
                            <td class="px-3 py-3">
                                <div class="font-semibold ${t.exit_price ? 'text-white' : 'text-slate-500'}">${t.exit_price ? fmtP(t.exit_price) : '--'}</div>
                                <div class="text-[10px] text-slate-400">${exitTime}</div>
                            </td>
                            <td class="px-3 py-3 text-[11px]">
                                <div>SL: <span class="text-rose-400">${fmtP(t.stop_loss)}</span></div>
                                <div>TP: <span class="text-emerald-400">${fmtP(t.take_profit)}</span></div>
                            </td>
                            <td class="px-3 py-3 text-slate-300">
                                ${t.duration_display || '--'}
                            </td>
                            <td class="px-3 py-3 font-semibold ${grossColor}">
                                ${grossPnlVal >= 0 ? '+$' : '-$'}${Math.abs(grossPnlVal).toFixed(3)}
                            </td>
                            <td class="px-3 py-3 text-amber-400 font-mono">
                                $${feesVal.toFixed(3)}
                            </td>
                            <td class="px-3 py-3">
                                <div class="font-bold ${netColor}">${netPnlVal >= 0 ? '+$' : '-$'}${Math.abs(netPnlVal).toFixed(3)}</div>
                                <span class="inline-block px-1.5 py-0.2 rounded text-[10px] border ${outcomeBadge} mt-0.5">${outcome}</span>
                            </td>
                            <td class="px-3 py-3 text-slate-400 text-[11px]">
                                <span class="px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800">${t.exit_reason || (t.is_open ? 'Position Active' : '--')}</span>
                            </td>
                        </tr>
                    `;
                }).join('');

            if (typeof renderPaginationControls === 'function') {
                renderPaginationControls('journal', 'journalPaginationContainer', () => {
                    renderJournalRows();
                });
            }
        }