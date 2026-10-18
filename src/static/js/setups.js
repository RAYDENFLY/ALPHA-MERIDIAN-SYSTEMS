// =========================================================================
// CANDIDATE SETUPS & SCANNER EXECUTION CONTROLS
// =========================================================================
        async function fetchSetups() {
            try {
                // Call API with preserved filter and sort parameters
                const params = new URLSearchParams();
                if (currentFilter && currentFilter !== 'ALL') params.append('status', currentFilter);
                if (currentCategoryFilter && currentCategoryFilter !== 'ALL') params.append('category', currentCategoryFilter);
                if (currentSort) params.append('sort_by', currentSort);
                if (searchQuery) params.append('symbol', searchQuery);
                if (typeof showHistoricalArchive !== 'undefined' && showHistoricalArchive) {
                    params.append('include_historical', 'true');
                }

                const res = await fetch(`/api/setups?${params.toString()}`);
                if (!res.ok) return;
                const data = await res.json();
                lastRawSetupsData = data;
                renderSetups(data);
                updateBadgeCounters(data.counts_by_status);
                updateCategoryBadgeCounters(data.counts_by_category);
            } catch (err) {
                console.error("Setups polling error:", err);
            }
        }

        function updateBadgeCounters(counts) {
            if (!counts) return;
            ['ALL', 'READY', 'WATCHLIST', 'INFEASIBLE', 'INVALIDATED', 'STALE', 'ERROR'].forEach(st => {
                const el = document.getElementById(`badge-count-${st}`);
                if (el) el.innerText = counts[st] !== undefined ? counts[st] : 0;
            });

            const summaryEl = document.getElementById('summaryCountsDisplay');
            if (summaryEl) {
                const total = counts['ALL'] || 0;
                const ready = counts['READY'] || 0;
                summaryEl.innerText = `${total} candidates tracked \u2022 ${ready} ready for execution`;
            }
        }

        function updateCategoryBadgeCounters(counts) {
            if (!counts) return;
            const mapping = {
                'ALL': 'badge-cat-ALL',
                'Breakout': 'badge-cat-Breakout',
                'Pullback': 'badge-cat-Pullback',
                'Reversal': 'badge-cat-Reversal',
                'Range/Mean Reversion': 'badge-cat-Range',
                'Unclassified': 'badge-cat-Unclassified'
            };
            for (const [k, elemId] of Object.entries(mapping)) {
                const el = document.getElementById(elemId);
                if (el) el.innerText = counts[k] !== undefined ? counts[k] : 0;
            }
        }

        function setStatusFilter(status) {
            currentFilter = status;
            ['ALL', 'READY', 'WATCHLIST', 'INFEASIBLE', 'INVALIDATED', 'STALE', 'ERROR'].forEach(st => {
                const btn = document.getElementById(`filter-${st}`);
                if (btn) {
                    if (st === status) btn.classList.add('filter-btn-active');
                    else btn.classList.remove('filter-btn-active');
                }
            });
            fetchSetups();
        }

        function setCategoryFilter(category) {
            currentCategoryFilter = category;
            const catButtons = {
                'ALL': 'catFilter-ALL',
                'Breakout': 'catFilter-Breakout',
                'Pullback': 'catFilter-Pullback',
                'Reversal': 'catFilter-Reversal',
                'Range/Mean Reversion': 'catFilter-Range',
                'Unclassified': 'catFilter-Unclassified'
            };
            for (const [k, btnId] of Object.entries(catButtons)) {
                const btn = document.getElementById(btnId);
                if (btn) {
                    if (k === category) btn.classList.add('filter-btn-active');
                    else btn.classList.remove('filter-btn-active');
                }
            }
            fetchSetups();
        }

        function handleSortChange(sortOption) {
            currentSort = sortOption;
            fetchSetups();
        }

        function handleSearchInput(query) {
            searchQuery = query.trim();
            const clearBtn = document.getElementById('clearSearchBtn');
            if (searchQuery.length > 0) {
                clearBtn.classList.remove('hidden');
            } else {
                clearBtn.classList.add('hidden');
            }
            fetchSetups();
        }

        function clearSearch() {
            document.getElementById('symbolSearchInput').value = '';
            handleSearchInput('');
        }

        function toggleHistoricalArchive(val) {
            showHistoricalArchive = Boolean(val);
            fetchSetups();
        }

        function renderSetups(data) {
            const container = document.getElementById('setupsContainer');
            if (!data.setups || data.setups.length === 0) {
                const filterDesc = currentFilter !== 'ALL' ? `matching status "${currentFilter}"` : '';
                const searchDesc = searchQuery ? `matching symbol "${searchQuery}"` : '';
                const activeQueryText = [filterDesc, searchDesc].filter(Boolean).join(' and ');
                const syms = (data.symbols_attempted && data.symbols_attempted.length > 0)
                    ? data.symbols_attempted.join(', ')
                    : 'ADAUSDT, AVAXUSDT, BNBUSDT, BTCUSDT, DOGEUSDT, ETHUSDT, LINKUSDT, LTCUSDT, SOLUSDT, XMRUSDT, XRPUSDT';
                const scanStatusBadge = data.has_completed_scan
                    ? `<span class="px-2 py-0.5 rounded text-[11px] font-mono badge-green">LIVE SCAN COMPLETED (${(data.symbols_attempted || []).length || 11}/11 SYMBOLS OK)</span>`
                    : `<span class="px-2 py-0.5 rounded text-[11px] font-mono badge-blue">INITIALIZING SCAN...</span>`;

                container.innerHTML = `
                    <div class="card-bg p-8 rounded-xl text-center space-y-3">
                        <div class="w-12 h-12 mx-auto text-slate-500">
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                        </div>
                        <div class="flex items-center justify-center gap-2">
                            ${scanStatusBadge}
                        </div>
                        <h3 class="text-lg font-semibold text-white">NO ACTIONABLE SETUP AT CURRENT CANDLE (${data.status || 'NO_TRADE'})</h3>
                        <p class="text-sm text-slate-400 max-w-xl mx-auto">
                            No setup candidates qualified ${activeQueryText || 'at the latest 1H/4H candle close'}.
                            All watchlist pairs are currently trading mid-range (72H dealing range 0.24–0.55) without hitting premium exhaustion (&ge;0.88), deep discount rejection (&le;0.12), or bullish variance-ratio expansion.
                        </p>
                        <div class="text-xs font-mono text-slate-500 max-w-2xl mx-auto pt-1">
                            Scanned Symbols: ${syms}
                        </div>
                        <div class="pt-3 flex items-center justify-center gap-3">
                            <button onclick="switchTab('watchlist')" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg text-xs font-medium transition">
                                View Watchlist & Specs (11 Pairs)
                            </button>
                            <button onclick="toggleHistoricalArchive(true)" class="px-3 py-1.5 bg-indigo-950/80 hover:bg-indigo-900 text-indigo-300 border border-indigo-700/60 rounded-lg text-xs font-medium transition">
                                Inspect Historical Setup Archive (Stale/Past Signals)
                            </button>
                        </div>
                    </div>`;
                return;
            }

            const archiveBannerHtml = (typeof showHistoricalArchive !== 'undefined' && showHistoricalArchive) ? `
                <div class="p-3 rounded-xl bg-amber-950/60 border border-amber-700/80 text-amber-200 text-xs flex flex-wrap items-center justify-between gap-2">
                    <div class="flex items-center gap-2">
                        <span>🗄️</span>
                        <span><strong>HISTORICAL ARCHIVE VIEW:</strong> Showing past setup records from SQLite database (marked <code>STALE</code>). Current live scan has 0 active candidates.</span>
                    </div>
                    <button onclick="toggleHistoricalArchive(false)" class="px-3 py-1 bg-amber-800 hover:bg-amber-700 text-white rounded-lg font-semibold transition">
                        &larr; Return to Live Scan View
                    </button>
                </div>
            ` : '';

            container.innerHTML = archiveBannerHtml + data.setups.map(s => {
                const isLong = s.direction === 'LONG';
                const sideBadge = isLong ? 'badge-green' : 'badge-red';
                
                // Status specific border and badge styling
                let statusBadge = 'badge-yellow';
                let cardClass = 'card-bg p-5 rounded-xl space-y-4';
                let statusTitle = s.status;

                if (s.status === 'READY' || s.status === 'VALID') {
                    statusBadge = 'badge-green';
                    cardClass += ' card-ready border border-emerald-500/40';
                    statusTitle = 'SETUP: READY (STRATEGY QUALIFIED)';
                } else if (s.status === 'WATCHLIST') {
                    statusBadge = 'badge-yellow';
                    cardClass += ' card-watchlist';
                    const rText = ((s.rejection_reasons || []).join(' ') + ' ' + (s.opposing_reasons || []).join(' ')).toLowerCase();
                    if (rText.includes('account limit') || rText.includes('open position') || rText.includes('weekly entries') || rText.includes('loss limit')) {
                        statusTitle = 'SETUP: WATCHLIST (Account Limit)';
                    } else if (rText.includes('live sync failed') || rText.includes('pending verified data') || rText.includes('sync failed')) {
                        statusTitle = 'SETUP: WATCHLIST (Data Pending)';
                    } else if (rText.includes('trigger') || rText.includes('entry price') || rText.includes('breakout')) {
                        statusTitle = 'SETUP: WATCHLIST (Awaiting Trigger)';
                    } else {
                        statusTitle = 'SETUP: WATCHLIST';
                    }
                } else if (s.status === 'INFEASIBLE') {
                    statusBadge = 'badge-red';
                    cardClass += ' card-infeasible';
                    statusTitle = 'SETUP: INFEASIBLE (Capital / Sizing)';
                } else if (s.status === 'INVALIDATED') {
                    statusBadge = 'badge-orange';
                    cardClass += ' card-invalidated';
                    statusTitle = 'SETUP: INVALIDATED (Structural Breach)';
                } else if (s.status === 'STALE') {
                    statusBadge = 'badge-gray';
                    cardClass += ' card-stale opacity-75 border border-slate-700/60 bg-slate-950/40';
                    statusTitle = 'SETUP: STALE (Expired / Data Outdated)';
                } else if (s.status === 'ERROR') {
                    statusBadge = 'badge-red';
                    cardClass += ' card-error';
                    statusTitle = 'SETUP: ERROR (Evaluation Failed)';
                }

                // Decoupled Execution Status logic
                let execBadgeClass = 'border border-slate-700 text-slate-400 bg-slate-900/80';
                let execTitle = 'EXEC: NOT SUBMITTED';
                const exStatus = s.execution_status || 'NOT_SUBMITTED';

                if (exStatus === 'SUBMITTED' || exStatus === 'SUBMITTING') {
                    execBadgeClass = 'bg-amber-950/80 border border-amber-600 text-amber-300 font-semibold';
                    execTitle = 'EXEC: PENDING LIMIT ORDER';
                } else if (exStatus === 'FILLED') {
                    execBadgeClass = 'bg-blue-950/80 border border-blue-600 text-blue-300 font-semibold';
                    execTitle = 'EXEC: POSITION OPEN';
                } else if (exStatus === 'PROTECTED') {
                    execBadgeClass = 'bg-emerald-950/80 border border-emerald-600 text-emerald-300 font-semibold';
                    execTitle = 'EXEC: POSITION OPEN (PROTECTED)';
                } else if (exStatus === 'CLOSED') {
                    execBadgeClass = 'bg-slate-800 border border-slate-600 text-slate-300';
                    execTitle = 'EXEC: POSITION CLOSED';
                } else if (exStatus === 'REJECTED') {
                    execBadgeClass = 'bg-red-950/80 border border-red-700 text-red-300';
                    execTitle = 'EXEC: ORDER REJECTED';
                } else if (exStatus === 'EXECUTION_UNKNOWN') {
                    execBadgeClass = 'bg-purple-950/80 border border-purple-700 text-purple-300';
                    execTitle = 'EXEC: UNKNOWN';
                }

                // Confidence display semantics (explicit N/A handling)
                const mlProbText = s.ml_probability !== null && s.ml_probability !== undefined 
                    ? `${(s.ml_probability * 100).toFixed(1)}%` 
                    : 'N/A';

                // Prominent Primary Status Reason Banner definition
                let bannerClass = 'bg-slate-900 border-slate-800 text-slate-300';
                let bannerIcon = 'ℹ️';
                let bannerTitle = 'STATUS DETAILS';
                let primaryReason = s.rejection_reasons && s.rejection_reasons.length ? s.rejection_reasons[0] : 'Candidate condition normal.';

                if (s.status === 'READY' || s.status === 'VALID') {
                    bannerClass = 'bg-emerald-950/70 border-emerald-700 text-emerald-200';
                    bannerIcon = '✅';
                    bannerTitle = `STRATEGY SETUP QUALIFIED &bull; [${execTitle}]`;
                    primaryReason = 'Strategy rules confirmed, sizing feasible, and risk limits permitted. Note: Live trading is locked; order execution is decoupled from setup generation.';
                } else if (s.status === 'WATCHLIST') {
                    bannerClass = 'bg-amber-950/70 border-amber-700 text-amber-200';
                    bannerIcon = '👁️';
                    const rText = ((s.rejection_reasons || []).join(' ') + ' ' + (s.opposing_reasons || []).join(' ')).toLowerCase();
                    if (rText.includes('account limit') || rText.includes('open position') || rText.includes('weekly entries') || rText.includes('loss limit')) {
                        bannerTitle = 'WATCHLIST (ACCOUNT LIMIT REACHED)';
                    } else if (rText.includes('live sync failed') || rText.includes('pending verified data') || rText.includes('sync failed')) {
                        bannerTitle = 'WATCHLIST (DATA VERIFICATION PENDING)';
                    } else if (rText.includes('trigger') || rText.includes('entry price') || rText.includes('breakout')) {
                        bannerTitle = 'WATCHLIST (AWAITING TRIGGER)';
                    } else {
                        bannerTitle = 'WATCHLIST (NOT EXECUTABLE)';
                    }
                    primaryReason = s.rejection_reasons && s.rejection_reasons.length ? s.rejection_reasons[0] : 'Awaiting confirmation.';
                } else if (s.status === 'INFEASIBLE') {
                    bannerClass = 'bg-red-950/70 border-red-700 text-red-200';
                    bannerIcon = '🚫';
                    bannerTitle = 'CAPITAL / SIZING INFEASIBLE';
                    primaryReason = s.rejection_reasons && s.rejection_reasons.length ? s.rejection_reasons[0] : 'Position sizing exceeds $16 account constraints.';
                } else if (s.status === 'INVALIDATED') {
                    bannerClass = 'bg-orange-950/70 border-orange-700 text-orange-200';
                    bannerIcon = '⛔';
                    bannerTitle = 'STRUCTURAL INVALIDATION BREACHED';
                    primaryReason = s.rejection_reasons && s.rejection_reasons.length ? s.rejection_reasons[0] : (s.invalidation_reason || 'Price wicks violated structural invalidation.');
                } else if (s.status === 'STALE') {
                    bannerClass = 'bg-slate-900 border-slate-700 text-slate-300';
                    bannerIcon = '⏳';
                    bannerTitle = `STALE SIGNAL / EXPIRED (NO ACTION)`;
                    primaryReason = s.rejection_reasons && s.rejection_reasons.length ? s.rejection_reasons[0] : 'Candle data age exceeds 3-hour limit or 12-hour TTL expired. Scanner stopped or market moved.';
                } else if (s.status === 'ERROR') {
                    bannerClass = 'bg-rose-950/70 border-rose-700 text-rose-200';
                    bannerIcon = '❌';
                    bannerTitle = 'EVALUATION ERROR';
                    primaryReason = s.rejection_reasons && s.rejection_reasons.length ? s.rejection_reasons[0] : 'Candidate evaluation failed.';
                }

                // Entry Category Badge
                const cat = s.entry_category || 'Unclassified';
                let catBadgeClass = 'bg-slate-800 text-slate-300 border-slate-700';
                let catIcon = '❓';
                if (cat === 'Breakout') { catBadgeClass = 'bg-indigo-950/80 text-indigo-300 border-indigo-700'; catIcon = '⚡'; }
                else if (cat === 'Pullback') { catBadgeClass = 'bg-sky-950/80 text-sky-300 border-sky-700'; catIcon = '🔄'; }
                else if (cat === 'Reversal') { catBadgeClass = 'bg-amber-950/80 text-amber-300 border-amber-700'; catIcon = '🔀'; }
                else if (cat === 'Range/Mean Reversion' || cat === 'Range') { catBadgeClass = 'bg-teal-950/80 text-teal-300 border-teal-700'; catIcon = '↔️'; }

                // Direction accent styling
                const dirAccent = isLong ? 'border-t border-t-emerald-500/40' : 'border-t border-t-rose-500/40';
                cardClass += ' ' + dirAccent;

                return `
                <div class="${cardClass}">
                    <div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
                        <div class="flex flex-wrap items-center gap-2.5">
                            <span class="text-xl font-extrabold text-white tracking-wide">${s.symbol}</span>
                            <span class="px-3 py-0.5 text-xs font-extrabold rounded-lg ${sideBadge} border ${isLong ? 'border-emerald-400' : 'border-rose-400'}">${s.direction}</span>
                            <span class="px-2 py-0.5 text-xs font-semibold rounded bg-slate-800 text-slate-300 border border-slate-700">10.0x Isolated</span>
                            <span class="px-2 py-0.5 text-xs font-semibold rounded border ${catBadgeClass}" title="Structural Entry Category">${catIcon} ${cat}</span>
                            <span class="px-2.5 py-0.5 text-xs font-semibold rounded ${statusBadge}" title="Strategy qualification status">${statusTitle}</span>
                            <span class="px-2.5 py-0.5 text-[11px] rounded ${execBadgeClass}" title="Exchange / Paper execution status">${execTitle}</span>
                            <span class="text-xs text-slate-400 font-mono">${s.datetime_utc || ''}</span>
                        </div>
                        <div class="flex items-center gap-2 font-mono">
                            <span class="text-xs text-slate-400" title="Informative score; does not guarantee edge">ML Score (Informative):</span>
                            <span class="px-2.5 py-0.5 text-xs font-bold rounded ${s.ml_probability !== null ? 'badge-purple' : 'badge-gray'}">${mlProbText}</span>
                            <span class="text-[11px] text-slate-500">(${s.eval_sample_count || 0} samples)</span>
                        </div>
                    </div>

                    <!-- Primary Reason Banner (High Visibility) -->
                    <div class="p-3 rounded-lg border text-xs flex items-start gap-2.5 ${bannerClass}">
                        <span class="text-base select-none shrink-0">${bannerIcon}</span>
                        <div class="space-y-0.5 flex-1">
                            <div class="font-bold tracking-wide">${bannerTitle}</div>
                            <div class="text-[11px] leading-relaxed opacity-95">${primaryReason}</div>
                        </div>
                    </div>

                    <!-- AsterDEX-Inspired Entry & Position Specifications Panel -->
                    <div class="bg-slate-900/90 border border-slate-800/90 rounded-xl p-4 space-y-3 font-mono">
                        <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-2 text-xs">
                            <div class="flex items-center gap-2 font-bold uppercase tracking-wider ${isLong ? 'text-emerald-400' : 'text-rose-400'}">
                                <span>⚡ AsterDEX Entry & Bracket Specifications</span>
                                <span class="px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-300 border border-slate-700">Isolated 10.0x</span>
                                <span class="px-2 py-0.5 rounded text-[10px] border ${catBadgeClass}">${catIcon} ${cat}</span>
                                ${!s.is_executable ? `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700">NON-ACTIONABLE / REFERENCE ONLY</span>` : ''}
                            </div>
                            <div class="flex items-center gap-3 text-[11px] text-slate-400">
                                <span>Feed: <strong class="text-slate-300">1H Candle Store (Local DB)</strong></span>
                                <span>|</span>
                                <span>Candle Close: <strong class="text-slate-300">${s.reference_price_time_utc ? s.reference_price_time_utc.slice(11, 19) + ' UTC' : 'Pending'}</strong></span>
                                ${s.is_reference_data_stale ? `<span class="px-1.5 py-0.2 rounded text-[10px] badge-yellow">Candle Data Stale (${s.reference_data_age_hours !== null && s.reference_data_age_hours !== undefined ? s.reference_data_age_hours.toFixed(1) + 'h' : '--'})</span>` : ''}
                            </div>
                        </div>

                        <!-- Core Order Pricing & Brackets Grid -->
                        <div class="grid grid-cols-2 md:grid-cols-6 gap-2.5 text-center">
                            <div class="card-inner p-2.5 rounded border border-slate-800/70">
                                <div class="text-[11px] text-slate-400 uppercase">Planned Entry</div>
                                <div class="text-sm font-bold text-white mt-0.5">$${s.entry_price}</div>
                                <div class="text-[10px] font-semibold mt-0.5 ${s.status === 'READY' ? 'text-emerald-400' : (s.status === 'INFEASIBLE' ? 'text-red-400' : (s.status === 'INVALIDATED' ? 'text-orange-400' : 'text-slate-400'))}">
                                    ${s.status === 'READY' ? '● ACTIONABLE (READY)' : '🚫 ' + s.status + ' (NO TRADE)'}
                                </div>
                            </div>
                            <div class="card-inner p-2.5 rounded border border-slate-800/70">
                                <div class="text-[11px] text-slate-400 uppercase" title="Latest closed 1H candle close price from local SQLite store, not live WebSocket ticker">1H Candle Ref Price</div>
                                <div class="text-sm font-bold text-cyan-300 mt-0.5">${s.reference_price !== null && s.reference_price !== undefined ? '$' + s.reference_price : (s.mark_price !== null && s.mark_price !== undefined ? '$' + s.mark_price : '--')}</div>
                                <div class="text-[10px] ${s.is_reference_data_stale ? 'text-amber-400 font-semibold' : 'text-slate-500'} mt-0.5">${s.reference_data_age_hours !== null && s.reference_data_age_hours !== undefined ? s.reference_data_age_hours.toFixed(1) + 'h age' : 'No Candle'}</div>
                            </div>
                            <div class="card-inner p-2.5 rounded border border-red-900/30">
                                <div class="text-[11px] text-slate-400 uppercase">Stop Loss (SL)</div>
                                <div class="text-sm font-bold text-red-400 mt-0.5">$${s.stop_loss}</div>
                                <div class="text-[10px] text-red-400/80 mt-0.5">-${s.stop_loss_pct || '--'}% distance</div>
                            </div>
                            <div class="card-inner p-2.5 rounded border border-emerald-900/30">
                                <div class="text-[11px] text-slate-400 uppercase">Take Profit 1</div>
                                <div class="text-sm font-bold text-emerald-400 mt-0.5">$${s.take_profit_1}</div>
                                <div class="text-[10px] text-emerald-400/80 mt-0.5">+${s.take_profit_1_pct || '--'}% (${s.gross_reward_to_risk ? s.gross_reward_to_risk.toFixed(1) + 'R target' : 'Target'})</div>
                            </div>
                            <div class="card-inner p-2.5 rounded border border-slate-800/70">
                                <div class="text-[11px] text-slate-400 uppercase">Take Profit 2</div>
                                <div class="text-sm font-bold ${s.take_profit_2 ? 'text-emerald-300' : 'text-slate-500'} mt-0.5">${s.take_profit_2 ? '$' + s.take_profit_2 : '--'}</div>
                                <div class="text-[10px] text-slate-500 mt-0.5">${s.take_profit_2_pct ? '+' + s.take_profit_2_pct + '% (Runner)' : 'Optional'}</div>
                            </div>
                            <div class="card-inner p-2.5 rounded border border-amber-900/30">
                                <div class="text-[11px] text-slate-400 uppercase" title="Exact fee-only break-even: covers 0.05% taker fee on entry and exit">Fee-Only Break-Even</div>
                                <div class="text-sm font-bold text-amber-300 mt-0.5">${s.break_even_price_fee_only !== null && s.break_even_price_fee_only !== undefined ? '$' + s.break_even_price_fee_only : (s.break_even_price !== null && s.break_even_price !== undefined ? '$' + s.break_even_price : '--')}</div>
                                <div class="text-[10px] text-amber-400/80 mt-0.5">0.05% taker/side (Net PnL=0)</div>
                            </div>
                        </div>

                        <!-- AsterDEX Position Sizing, Margin, Fees & Unrealized PnL -->
                        <div class="p-3 bg-slate-950/60 rounded-lg border border-slate-800 text-xs space-y-2">
                            <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                                <div class="flex flex-wrap items-center gap-4">
                                    <span>Quantity: <strong class="text-white">${s.risk_assessment ? s.risk_assessment.quantity : '--'}</strong></span>
                                    <span>Position Notional: <strong class="text-white">$${s.risk_assessment ? s.risk_assessment.position_notional_usdt.toFixed(2) : '--'} USDT</strong></span>
                                    <span>Target Margin: <strong class="text-emerald-400">$${s.risk_assessment ? (s.risk_assessment.actual_margin_usdt !== undefined ? s.risk_assessment.actual_margin_usdt : s.risk_assessment.required_margin_usdt).toFixed(2) : '6.00'} USDT</strong></span>
                                    <span>Leverage: <strong class="text-cyan-400">10.0x Isolated</strong></span>
                                </div>
                                <div class="flex items-center gap-2">
                                    <span class="text-slate-400">Net R:R:</span>
                                    <strong class="text-indigo-400">${s.net_reward_to_risk}:1</strong>
                                    <span class="text-[10px] text-slate-500">(Gross ${s.gross_reward_to_risk}:1)</span>
                                </div>
                            </div>

                            <div class="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                                <div class="flex flex-wrap items-center gap-4">
                                    <span>Est. Loss at SL: <strong class="text-red-400">$${s.risk_assessment && s.risk_assessment.estimated_loss_usdt !== undefined ? s.risk_assessment.estimated_loss_usdt.toFixed(3) : '--'} USDT</strong></span>
                                    <span>Equity Impact: <strong class="text-amber-400">${s.risk_assessment && s.risk_assessment.estimated_loss_pct !== undefined ? s.risk_assessment.estimated_loss_pct.toFixed(2) + '%' : '--'}</strong></span>
                                    <span>Break-Even (+Slip): <strong class="text-amber-300">${s.break_even_price_with_slippage !== null && s.break_even_price_with_slippage !== undefined ? '$' + s.break_even_price_with_slippage : '--'}</strong></span>
                                    <span>Costs: <span class="text-slate-400">0.05% fee + 0.05% slip / side (Excl. 8h funding)</span></span>
                                </div>
                                <div class="flex items-center gap-2">
                                    <span class="text-slate-400">Unrealized PnL:</span>
                                    ${s.unrealized_pnl_usdt !== null && s.unrealized_pnl_usdt !== undefined ? `
                                        <strong class="${s.unrealized_pnl_usdt >= 0 ? 'text-emerald-400' : 'text-red-400'}">$${s.unrealized_pnl_usdt.toFixed(4)} USDT (${s.roe_pct !== null && s.roe_pct !== undefined ? (s.roe_pct >= 0 ? '+' : '') + s.roe_pct.toFixed(2) + '%' : '--'})</strong>
                                    ` : `
                                        <span class="text-slate-400">N/A <span class="text-[10px] text-slate-500">(No open position)</span></span>
                                    `}
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Distinct Structural Invalidation Level Callout -->
                    <div class="p-3 rounded-lg border border-amber-900/40 bg-amber-950/20 text-xs flex items-start gap-2.5">
                        <span class="text-base select-none shrink-0">🛡️</span>
                        <div class="space-y-0.5 flex-1">
                            <div class="flex items-center justify-between">
                                <span class="font-bold text-amber-300">STRUCTURAL INVALIDATION LEVEL: <span class="font-mono text-amber-200">$${s.invalidation_price}</span></span>
                                <span class="text-[10px] px-2 py-0.5 rounded bg-amber-900/50 text-amber-200 border border-amber-700/50">Market Structure Thesis</span>
                            </div>
                            <div class="text-[11px] text-slate-300 leading-relaxed">${s.invalidation_reason || 'Breached if 1H candle closes beyond structural swing low/high.'}</div>
                            <div class="text-[10px] text-slate-400 italic">Distinct from Stop Loss: Structural Invalidation violates the technical thesis, while Stop Loss ($${s.stop_loss}) enforces exchange bracket risk exit.</div>
                        </div>
                    </div>

                    <!-- SMC & SnR Research Evidence Accordion -->
                    <div class="border border-slate-800 rounded-lg bg-slate-900/60 overflow-hidden text-xs">
                        <button type="button" onclick="toggleAccordion('evidence-${s.candidate_id}')" class="w-full p-3 flex items-center justify-between text-left hover:bg-slate-800/40 transition">
                            <div class="flex items-center gap-2 font-semibold text-slate-200">
                                <span>🔬 Institutional SMC & SnR Research Evidence</span>
                                <span class="text-[11px] font-normal text-slate-400">(4H Bias: <strong class="text-white">${s.bias_4h}</strong> &bull; 1H Structure: <strong class="text-white">${s.structure_1h}</strong>)</span>
                            </div>
                            <span id="evidence-icon-evidence-${s.candidate_id}" class="text-slate-400 text-xs font-mono">[− Collapse]</span>
                        </button>
                        
                        <div id="evidence-${s.candidate_id}" class="p-3 pt-0 border-t border-slate-800/60 space-y-3">
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
                                <div class="space-y-1.5">
                                    <div class="font-semibold text-slate-300">SMC & SnR Technical Confirmation:</div>
                                    <ul class="list-disc list-inside text-slate-400 space-y-0.5">
                                        ${s.smc_evidence && s.smc_evidence.length ? s.smc_evidence.map(e => `<li>${e}</li>`).join('') : '<li class="text-slate-500">No active SMC events</li>'}
                                        ${s.snr_evidence && s.snr_evidence.length ? s.snr_evidence.map(e => `<li>${e}</li>`).join('') : ''}
                                    </ul>
                                </div>
                                <div class="space-y-1.5">
                                    <div class="font-semibold text-slate-300">Strategy Decision Context:</div>
                                    ${s.supporting_reasons && s.supporting_reasons.length ? `
                                    <div class="text-emerald-400 space-y-0.5">
                                        ${s.supporting_reasons.map(r => `<div>&bull; ${r}</div>`).join('')}
                                    </div>` : ''}
                                    ${s.opposing_reasons && s.opposing_reasons.length ? `
                                    <div class="text-amber-400 space-y-0.5">
                                        ${s.opposing_reasons.map(r => `<div>&bull; ${r}</div>`).join('')}
                                    </div>` : ''}
                                    ${(!s.supporting_reasons || !s.supporting_reasons.length) && (!s.opposing_reasons || !s.opposing_reasons.length) ? `
                                    <div class="text-slate-500 italic">No additional supporting or opposing notes.</div>
                                    ` : ''}
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Status Semantics & Constraints -->
                    <div class="space-y-1.5 text-xs pt-1">
                        <div class="font-semibold text-slate-300">Status Semantics & Constraints:</div>
                        ${s.rejection_reasons && s.rejection_reasons.length ? `
                        <div class="p-2.5 rounded bg-slate-900 border border-slate-800 text-slate-300 space-y-1">
                            ${s.rejection_reasons.map(r => `
                                <div class="flex items-start gap-1.5 ${s.status === 'READY' ? 'text-emerald-400' : 'text-amber-300'}">
                                    <span>&bull;</span>
                                    <span>${r}</span>
                                </div>
                            `).join('')}
                        </div>` : `
                        <div class="p-2 rounded bg-slate-900/60 border border-emerald-900/40 text-emerald-300">
                            &bull; Setup satisfies strategy validation, position sizing, and risk limits. Ready for manual review. ML score is informative only and unproven to deliver positive edge.
                        </div>`}
                    </div>

                    <!-- Optional Qwen2.5-7B AI Signal Analysis Section -->
                    ${s.ai_analysis && s.ai_analysis.output ? `
                    <div class="mt-3 p-3 rounded-lg bg-slate-900/80 border border-indigo-900/50 space-y-2">
                        <div class="flex items-center justify-between text-xs">
                            <div class="flex items-center gap-1.5 font-semibold text-indigo-300">
                                <svg class="w-4 h-4 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                                <span>AI Qualitative Analysis (Qwen2.5-7B-Instruct)</span>
                                <span class="text-[10px] text-slate-500 font-mono">(${s.ai_analysis.latency_ms}ms)</span>
                            </div>
                            <div class="flex items-center gap-2">
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold ${s.ai_analysis.output.direction_assessment === 'SUPPORTS_SETUP' ? 'badge-green' : (s.ai_analysis.output.direction_assessment === 'CONFLICTS_WITH_SETUP' ? 'badge-red' : 'badge-yellow')}">
                                    ${s.ai_analysis.output.direction_assessment}
                                </span>
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold ${s.ai_analysis.output.setup_quality === 'HIGH' ? 'badge-green' : (s.ai_analysis.output.setup_quality === 'MEDIUM' ? 'badge-yellow' : 'badge-gray')}">
                                    QUALITY: ${s.ai_analysis.output.setup_quality}
                                </span>
                                ${s.ai_analysis.output.abstain ? '<span class="px-2 py-0.5 rounded text-[10px] font-bold badge-orange">ABSTAIN</span>' : ''}
                            </div>
                        </div>
                        <p class="text-xs text-slate-300 italic">"${s.ai_analysis.output.summary}"</p>
                        ${s.ai_analysis.output.reasons && s.ai_analysis.output.reasons.length ? `
                        <div class="text-[11px] text-slate-400 space-y-0.5">
                            <span class="font-semibold text-slate-300">Key AI Observations:</span>
                            <ul class="list-disc list-inside space-y-0.5 text-slate-400">
                                ${s.ai_analysis.output.reasons.map(r => `<li>${r}</li>`).join('')}
                            </ul>
                        </div>` : ''}
                        ${s.ai_analysis.deterministic_guards_applied && s.ai_analysis.deterministic_guards_applied.length ? `
                        <div class="text-[10px] text-amber-400 font-mono bg-amber-950/40 p-1.5 rounded border border-amber-900/40">
                            🛡️ Client Guards: ${s.ai_analysis.deterministic_guards_applied.join('; ')}
                        </div>` : ''}
                    </div>` : (s.ai_analysis && s.ai_analysis.status !== 'DISABLED' ? `
                    <div class="mt-2 p-2 rounded bg-slate-900 border border-slate-800 text-xs text-slate-400 flex items-center justify-between">
                        <span>AI Analysis Status: <strong class="text-amber-400">${s.ai_analysis.status}</strong> (${s.ai_analysis.error_message || 'Bypassed'})</span>
                        <span class="text-[10px] text-slate-500 font-mono">${s.ai_analysis.model_id}</span>
                    </div>` : '')}
                </div>
            `;
            }).join('');
        }

        // Research Evidence Accordion Toggle
        function toggleAccordion(id) {
            const el = document.getElementById(id);
            const icon = document.getElementById('evidence-icon-' + id);
            if (!el) return;
            if (el.classList.contains('hidden')) {
                el.classList.remove('hidden');
                if (icon) icon.textContent = '[− Collapse]';
            } else {
                el.classList.add('hidden');
                if (icon) icon.textContent = '[+ Expand]';
            }
        }


        // Safe Manual Trigger Scan with Anti-Overlap Handling
        async function triggerScan() {
            const btn = document.getElementById('scanBtn');
            btn.disabled = true;
            btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg> Scanning Watchlist...`;
            
            try {
                const res = await fetch('/api/scan', { method: 'POST' });
                const data = await res.json();

                if (data.status === 'ALREADY_RUNNING') {
                    showNotice(data.message || 'A background scan is currently in progress. Displaying cached results.', 'warn');
                } else {
                    dismissNotice();
                }

                await fetchSetups();
                await fetchStatus();
            } catch (err) {
                showNotice("Manual scan request failed: " + err, 'error');
            } finally {
                btn.disabled = false;
                btn.innerHTML = `<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg> Scan Watchlist Now`;
            }
        }
