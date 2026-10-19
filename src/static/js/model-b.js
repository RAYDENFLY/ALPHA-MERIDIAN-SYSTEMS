        // MODEL B LIQUIDITY RESEARCH ENGINE JAVASCRIPT
        // =========================================================================

        async function loadModelB() {
            await Promise.allSettled([
                fetchModelBStatus(),
                fetchModelBPositions(),
                fetchModelBCoverage(),
                fetchModelBSetups(),
                fetchModelBOrders(),
                fetchModelBJournal(),
                fetchModelBOrderBook(),
                fetchModelBWhaleWatchlist(),
                fetchModelBWhaleWalls(),
                fetchModelBWhaleEvents(),
                fetchModelBResearchMetrics(),
                fetchModelBResearchSignals(),
            ]);
        }

        async function fetchModelBStatus() {
            try {
                const res = await fetch('/api/model_b/status');
                if (!res.ok) return;
                const data = await res.json();
                modelBCachedStatus = data;

                const eqEl = document.getElementById('modelBEquityDisplay');
                if (eqEl) eqEl.innerText = `$${Number(data.capital.current_equity_usdt || 100.0).toFixed(2)} USDT`;

                const dailyEl = document.getElementById('modelBDailyLossDisplay');
                if (dailyEl) dailyEl.innerText = `$${Number(data.risk_limits.daily_loss_realized_usdt || 0).toFixed(2)} / $${Number(data.risk_limits.daily_loss_limit_usdt || 3.0).toFixed(2)}`;

                const dailyStat = document.getElementById('modelBDailyStatus');
                if (dailyStat) {
                    if (data.risk_limits.daily_circuit_breaker_active) {
                        dailyStat.innerText = 'CIRCUIT BREAKER (HALTED)';
                        dailyStat.className = 'text-xs text-rose-400 font-mono font-bold animate-pulse';
                    } else {
                        dailyStat.innerText = 'Active (3R Cap)';
                        dailyStat.className = 'text-xs text-emerald-400 font-mono';
                    }
                }

                const weeklyEl = document.getElementById('modelBWeeklyLossDisplay');
                if (weeklyEl) weeklyEl.innerText = `$${Number(data.risk_limits.weekly_loss_realized_usdt || 0).toFixed(2)} / $${Number(data.risk_limits.weekly_loss_limit_usdt || 7.5).toFixed(2)}`;

                const weeklyStat = document.getElementById('modelBWeeklyStatus');
                if (weeklyStat) {
                    if (data.risk_limits.weekly_circuit_breaker_active) {
                        weeklyStat.innerText = 'CIRCUIT BREAKER (HALTED)';
                        weeklyStat.className = 'text-xs text-rose-400 font-mono font-bold animate-pulse';
                    } else {
                        weeklyStat.innerText = 'Active (7.5R Cap)';
                        weeklyStat.className = 'text-xs text-emerald-400 font-mono';
                    }
                }

                const expEl = document.getElementById('modelBExposureDisplay');
                if (expEl) expEl.innerText = `${data.risk_limits.open_positions_count || 0} / ${data.risk_limits.max_open_positions || 3} Open`;

                const pendEl = document.getElementById('modelBPendingOrdersDisplay');
                if (pendEl) pendEl.innerText = `${data.risk_limits.pending_orders_count || 0} Pending`;

                const statPend = document.getElementById('modelBStatPendingOrders');
                if (statPend) statPend.innerText = `${data.risk_limits.pending_orders_count || 0} Pending`;

                const statOpen = document.getElementById('modelBStatOpenPositions');
                if (statOpen) statOpen.innerText = `${data.risk_limits.open_positions_count || 0} Open`;

                const statClosed = document.getElementById('modelBStatClosedTrades');
                if (statClosed && data.execution_metrics) {
                    statClosed.innerText = `${data.execution_metrics.trades_closed || 0} Closed`;
                }

                const discEl = document.getElementById('modelBDiscoveredCount');
                if (discEl) discEl.innerText = `${data.discovered_markets_count || 0} Markets Discovered`;

                const scanTimeEl = document.getElementById('modelBLastScanTime');
                if (scanTimeEl) {
                    scanTimeEl.innerText = data.last_scan_utc ? `Last Scan: ${new Date(data.last_scan_utc).toLocaleTimeString()}` : 'Last Scan: Never';
                }

                // Render Universal AsterDEX Scanner Engine Telemetry
                const perf = data.scanner_performance;
                if (perf) {
                    const cov = perf.coverage || {};
                    const rot = perf.rotation || {};
                    const reqs = perf.requests || {};
                    const lat = reqs.latency_stats || {};
                    const cand = perf.candidates || {};

                    const cycleIdEl = document.getElementById('scannerCycleId');
                    if (cycleIdEl) cycleIdEl.innerText = `Cycle: ${perf.cycle_id || '--'}`;

                    const durEl = document.getElementById('scannerDuration');
                    if (durEl) durEl.innerText = `Scan: ${Number(perf.total_duration_ms || 0).toFixed(0)} ms`;

                    const hitEl = document.getElementById('scannerCacheHit');
                    if (hitEl) hitEl.innerText = `Cache: ${Number(perf.cache?.hit_rate_pct || 0).toFixed(0)}%`;

                    const discEl2 = document.getElementById('scannerDiscoveredTotal');
                    if (discEl2) discEl2.innerText = `${cov.discovered_asterdex || 578} Pairs`;

                    const bulkEl = document.getElementById('scannerBulkScreened');
                    if (bulkEl) bulkEl.innerText = `Screened: ${cov.bulk_screened || cov.screened || 0}`;

                    const qualEl = document.getElementById('scannerQualifiedPool');
                    if (qualEl) qualEl.innerText = `${cov.screened || 0} Liquid`;

                    const deepEl = document.getElementById('scannerDeepEvaluated');
                    if (deepEl) deepEl.innerText = `${cov.deep_evaluated || cov.evaluated || 0} / Cycle`;

                    const rotProgEl = document.getElementById('scannerRotationProgress');
                    if (rotProgEl) rotProgEl.innerText = `${Number(rot.progress_pct || 0).toFixed(1)}%`;

                    const rotOffEl = document.getElementById('scannerRotationOffset');
                    if (rotOffEl) rotOffEl.innerText = `Offset: ${rot.offset || 0} / ${rot.pool_size || 0}`;

                    const p95El = document.getElementById('scannerP95Latency');
                    if (p95El) p95El.innerText = `p95: ${Number(lat.p95_ms || 0).toFixed(0)} ms`;

                    const medEl = document.getElementById('scannerMedianLatency');
                    if (medEl) medEl.innerText = `med: ${Number(lat.median_ms || 0).toFixed(0)} ms`;

                    const candYieldEl = document.getElementById('scannerCandidateYield');
                    if (candYieldEl) candYieldEl.innerText = `${cand.found || 0} Formulated`;

                    const readyYieldEl = document.getElementById('scannerReadyYield');
                    if (readyYieldEl) readyYieldEl.innerText = `${cand.ready || 0} READY`;

                    const progBar = document.getElementById('scannerProgressBar');
                    if (progBar) progBar.style.width = `${Math.min(100, Math.max(0, rot.progress_pct || 0))}%`;

                    const progText = document.getElementById('scannerProgressBarText');
                    if (progText) progText.innerText = `${rot.offset || 0} / ${rot.pool_size || 0} Liquid Contracts Rotated (${Number(rot.progress_pct || 0).toFixed(1)}%)`;
                }

            } catch (err) {
                console.error("Model B status error:", err);
            }
        }

        async function fetchModelBCoverage() {
            try {
                const res = await fetch('/api/model_b/coverage');
                if (!res.ok) return;
                const data = await res.json();
                const container = document.getElementById('modelBCoverageContainer');
                if (!container) return;

                const venues = data.venues || {};
                container.innerHTML = Object.entries(venues).map(([vName, vData]) => {
                    const isConn = vData.status === 'CONNECTED';
                    const badgeClass = isConn ? 'bg-emerald-950 text-emerald-400 border-emerald-700' : 'bg-slate-800 text-slate-400 border-slate-700';
                    const sample = (vData.sample_symbols || []).slice(0, 3).join(', ');

                    return `
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800/80 flex items-center justify-between">
                            <div>
                                <div class="font-bold text-white uppercase text-xs">${escapeHtml(vName)}</div>
                                <div class="text-[10px] text-slate-400 mt-0.5">Sample: ${sample || 'None'}</div>
                            </div>
                            <div class="text-right">
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${badgeClass}">
                                    ${vData.count} Perps
                                </span>
                            </div>
                        </div>
                    `;
                }).join('');

            } catch (err) {
                console.error("Model B coverage error:", err);
            }
        }

        async function fetchModelBSetups() {
            try {
                const res = await fetch('/api/model_b/setups?limit=25');
                if (!res.ok) return;
                const data = await res.json();
                const tbody = document.getElementById('modelBSetupsTableBody');
                const badge = document.getElementById('modelBSetupsCountBadge');
                if (!tbody) return;

                const setups = data.setups || [];
                if (badge) badge.innerText = `${setups.length} Candidate${setups.length === 1 ? '' : 's'}`;

                if (setups.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-6 text-center text-slate-500">No Model B setups recorded. Click "Scan Universal Markets Now" to trigger a 1H cycle.</td></tr>';
                    return;
                }

                tbody.innerHTML = setups.map(s => {
                    const isLong = (s.direction || '').toUpperCase() === 'LONG';
                    const dirClass = isLong ? 'text-emerald-400' : 'text-rose-400';
                    const dirBg = isLong ? 'border-emerald-600 bg-emerald-950/40' : 'border-rose-600 bg-rose-950/40';

                    let statusBadge = 'bg-slate-800 text-slate-300 border-slate-700';
                    if (s.status === 'READY') statusBadge = 'bg-emerald-950 text-emerald-300 border-emerald-600 font-bold';
                    else if (s.status === 'WATCHLIST') statusBadge = 'bg-amber-950 text-amber-300 border-amber-600';
                    else if (s.status === 'INFEASIBLE') statusBadge = 'bg-rose-950 text-rose-300 border-rose-600';
                    else if (s.status === 'INVALIDATED') statusBadge = 'bg-orange-950 text-orange-300 border-orange-600';

                    let execBadge = 'bg-slate-800 text-slate-400 border-slate-700';
                    let execSub = '';
                    const execState = (s.execution_state || 'NOT_SUBMITTED').toUpperCase();

                    if (execState === 'ENTRY_PENDING') {
                        execBadge = 'bg-amber-950 text-amber-300 border-amber-600 font-bold animate-pulse';
                        execSub = `<div class="text-[10px] text-amber-400/90 font-mono mt-0.5 truncate max-w-xs">${escapeHtml(s.order_id || '')} &bull; Awaiting Penetration</div>`;
                    } else if (execState === 'OPEN') {
                        execBadge = 'bg-emerald-950 text-emerald-300 border-emerald-600 font-bold';
                        execSub = `<div class="text-[10px] text-emerald-400/90 font-mono mt-0.5 truncate max-w-xs">${escapeHtml(s.order_id || '')} &bull; Position Active</div>`;
                    } else if (execState === 'CLOSED') {
                        execBadge = 'bg-purple-950 text-purple-300 border-purple-600 font-bold';
                        execSub = `<div class="text-[10px] text-purple-400/90 font-mono mt-0.5">Exited & Journaled</div>`;
                    } else if (execState === 'REJECTED') {
                        execBadge = 'bg-rose-950 text-rose-300 border-rose-600 font-bold';
                        execSub = `<div class="text-[10px] text-rose-400 font-mono mt-0.5 truncate max-w-xs">${escapeHtml(s.rejection_reason || 'Risk limit rejected')}</div>`;
                    } else if (execState === 'EXPIRED') {
                        execBadge = 'bg-orange-950 text-orange-300 border-orange-600';
                        execSub = `<div class="text-[10px] text-orange-400 font-mono mt-0.5">TTL Expired (12 bars)</div>`;
                    } else if (execState === 'CANCELLED') {
                        execBadge = 'bg-orange-950 text-orange-300 border-orange-600';
                        execSub = `<div class="text-[10px] text-orange-400 font-mono mt-0.5">Structural Invalidation</div>`;
                    } else if (execState === 'READY') {
                        execBadge = 'bg-blue-950 text-blue-300 border-blue-600 font-bold';
                        execSub = `<div class="text-[10px] text-blue-400/90 font-mono mt-0.5">Qualified &bull; Ready</div>`;
                    } else {
                        execBadge = 'bg-slate-800 text-slate-400 border-slate-700';
                        const notSubReason = s.not_submitted_reason || s.rejection_reason || 'Not Submitted';
                        execSub = `<div class="text-[10px] text-slate-400 font-mono mt-0.5 truncate max-w-xs" title="${escapeHtml(notSubReason)}">${escapeHtml(notSubReason)}</div>`;
                    }

                    const meta = s.metadata || (s.details && s.details.ai_analysis) || {};
                    const risk = s.risk_assessment || (s.details && s.details.risk_assessment) || {};

                    const spreadStr = (meta.spread_bps !== undefined && meta.spread_bps !== null)
                        ? `${meta.spread_bps.toFixed(1)} bps`
                        : '<span class="text-slate-500 italic">UNAVAILABLE</span>';

                    const imbStr = (meta.imbalance_25bps !== undefined && meta.imbalance_25bps !== null)
                        ? `${(meta.imbalance_25bps * 100).toFixed(1)}%`
                        : '<span class="text-slate-500 italic">UNAVAILABLE</span>';

                    const cvdStr = (meta.cvd_1h !== undefined && meta.cvd_1h !== null)
                        ? `$${Math.round(meta.cvd_1h).toLocaleString()}`
                        : '<span class="text-slate-500 italic">UNAVAILABLE</span>';

                    const rawQty = (risk.quantity !== undefined && risk.quantity !== null) ? risk.quantity : s.quantity;
                    const qtyStr = (rawQty !== undefined && rawQty !== null && rawQty > 0)
                        ? `${rawQty}`
                        : '<span class="text-slate-500 italic">UNAVAILABLE (Risk pending)</span>';

                    return `
                        <tr class="hover:bg-slate-800/40 transition">
                            <td class="px-4 py-3">
                                <div class="font-bold text-white flex items-center gap-1.5">
                                    <span>${escapeHtml(s.symbol)}</span>
                                    <span class="px-1.5 py-0.2 rounded text-[10px] font-bold border ${dirBg} ${dirClass}">${s.direction}</span>
                                </div>
                                <div class="text-[10px] text-slate-500">${meta.signal_type || 'SWEEP_RECLAIM'}</div>
                            </td>
                            <td class="px-4 py-3 font-semibold text-white">$${formatPrice(s.entry_price)}</td>
                            <td class="px-4 py-3 text-rose-400 font-medium">$${formatPrice(s.stop_loss)}</td>
                            <td class="px-4 py-3">
                                <div class="text-emerald-400 font-medium">TP1: $${formatPrice(s.take_profit_1)}</div>
                                <div class="text-slate-400 text-[10px]">TP2: ${s.take_profit_2 ? '$' + formatPrice(s.take_profit_2) : '--'}</div>
                            </td>
                            <td class="px-4 py-3 text-[11px]">
                                <div>Risk: <strong class="text-amber-300">$1.00 USDT</strong></div>
                                <div class="text-slate-400 text-[10px]">Qty: ${qtyStr}</div>
                            </td>
                            <td class="px-4 py-3 text-[10px] text-slate-400">
                                <div>Spread: <span class="text-slate-300">${spreadStr}</span></div>
                                <div>Depth Imb: <span class="${meta.imbalance_25bps >= 0 ? 'text-emerald-400' : 'text-rose-400'} font-bold">${imbStr}</span></div>
                                <div>CVD: <span class="text-slate-300">${cvdStr}</span></div>
                            </td>
                            <td class="px-4 py-3">
                                <span class="px-2 py-0.5 rounded text-[10px] border ${statusBadge}">${s.status}</span>
                                ${s.rejection_reasons && s.rejection_reasons.length ? `<div class="text-[10px] text-slate-500 max-w-xs truncate mt-0.5">${escapeHtml(s.rejection_reasons[0])}</div>` : ''}
                            </td>
                            <td class="px-4 py-3">
                                <span class="px-2 py-0.5 rounded text-[10px] border ${execBadge}">${execState}</span>
                                ${execSub}
                            </td>
                        </tr>
                    `;

                }).join('');

            } catch (err) {
                console.error("Model B setups error:", err);
            }
        }

        let modelBCachedPositions = [];

        async function fetchModelBPositions() {
            try {
                const res = await fetch('/api/model_b/positions');
                if (!res.ok) return;
                const data = await res.json();
                const positions = data.positions || [];
                modelBCachedPositions = positions;

                const countBadge = document.getElementById('modelBPositionsCountBadge');
                if (countBadge) {
                    countBadge.innerText = `${positions.length} Open Position${positions.length === 1 ? '' : 's'}`;
                    countBadge.className = positions.length > 0
                        ? 'px-2.5 py-0.5 text-xs font-semibold rounded-full bg-emerald-950 text-emerald-300 border border-emerald-800 font-mono'
                        : 'px-2.5 py-0.5 text-xs font-semibold rounded-full bg-slate-800 text-slate-400 border border-slate-700 font-mono';
                }

                const syncTimeEl = document.getElementById('modelBPositionsSyncTime');
                if (syncTimeEl) {
                    syncTimeEl.innerText = `Sync: ${new Date().toLocaleTimeString()}`;
                }

                const statOpen = document.getElementById('modelBStatOpenPositions');
                if (statOpen) statOpen.innerText = `${positions.length} Open`;

                const tbody = document.getElementById('modelBPositionsTableBody');
                if (!tbody) return;

                if (positions.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-6 text-center text-slate-500">No open Model B paper positions. Filled paper limit orders will appear here with live mark-to-market PnL.</td></tr>';
                    return;
                }

                tbody.innerHTML = positions.map(p => {
                    const isLong = (p.side || '').toUpperCase() === 'LONG';
                    const sideBadgeClass = isLong
                        ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                        : 'bg-rose-950 text-rose-400 border border-rose-800';

                    const baseCoin = (p.symbol || '').replace(/USDT$/i, '');
                    const upnl = Number(p.unrealized_pnl_net_usdt !== undefined ? p.unrealized_pnl_net_usdt : (p.unrealized_pnl_usdt || 0));
                    const isUpnlPositive = upnl >= 0;
                    const upnlClass = isUpnlPositive ? 'text-emerald-400' : 'text-rose-400';
                    const upnlSign = isUpnlPositive ? '+' : '';

                    // Mode formatting
                    const modeDisplay = escapeHtml(p.margin_mode_display || 'SIMULATED (10x)');

                    // Take Profit formatting
                    let tpDisplay = '<span class="text-slate-500 italic text-xs">None</span>';
                    if (p.take_profit_price) {
                        tpDisplay = `<span class="text-emerald-400 font-mono font-medium">${escapeHtml(p.take_profit_label || ('$' + formatPrice(p.take_profit_price)))}</span>`;
                    }

                    // Stop Loss formatting with explicit protection status
                    let slDisplay = '<span class="text-rose-400/80 italic text-xs font-medium">None</span>';
                    if (p.stop_loss_price) {
                        const protBadge = `<span class="ml-1 px-1.5 py-0.2 text-[9px] font-bold rounded bg-emerald-950 text-emerald-300 border border-emerald-800">PROTECTED</span>`;
                        slDisplay = `<span class="text-rose-400 font-mono font-medium">${escapeHtml(p.stop_loss_label || ('$' + formatPrice(p.stop_loss_price)))}</span>${protBadge}`;
                    }

                    // Liquidation Price formatting
                    const liqDisplay = `<span class="px-1.5 py-0.5 text-[10px] font-mono text-slate-400 bg-slate-900 border border-slate-800 rounded font-semibold">${escapeHtml(p.liquidation_label || 'NOT MODELED')}</span>`;

                    // Allocated Margin formatting
                    const allocMarginDisplay = p.allocated_margin_label || `$${Number(p.allocated_margin_usdt || 0).toFixed(2)}`;

                    return `
                        <tr class="hover:bg-slate-800/30 transition font-mono text-xs border-b border-slate-800/60">
                            <td class="py-3 px-4">
                                <div class="flex items-center gap-2">
                                    <span class="font-bold text-white text-sm font-sans">${escapeHtml(p.symbol)}</span>
                                    <span class="px-1.5 py-0.5 text-[10px] font-bold rounded ${sideBadgeClass}">${p.side} ${p.leverage}x</span>
                                    <span class="px-1.5 py-0.5 text-[9px] font-bold rounded bg-amber-950 text-amber-300 border border-amber-800">PAPER</span>
                                    <span class="px-1.5 py-0.5 text-[9px] font-mono font-semibold rounded bg-cyan-950 text-cyan-300 border border-cyan-800/60">${modeDisplay}</span>
                                </div>
                            </td>
                            <td class="py-3 px-4">
                                <div class="text-slate-200 font-medium">${p.position_amt} ${baseCoin}</div>
                                <div class="text-[11px] text-slate-400 font-normal">$${Number(p.notional_usdt || 0).toFixed(2)} USDT</div>
                            </td>
                            <td class="py-3 px-4">
                                <div class="text-slate-200 font-medium">$${formatPrice(p.entry_price)}</div>
                                <div class="text-[11px] text-slate-400 font-normal">Mark: $${formatPrice(p.mark_price)}</div>
                            </td>
                            <td class="py-3 px-4">
                                <div class="text-slate-200 font-semibold">${allocMarginDisplay}</div>
                                <div class="text-[10px] text-slate-500">Simulated 10x Margin</div>
                            </td>
                            <td class="py-3 px-4">
                                <div>${tpDisplay}</div>
                                <div class="mt-0.5">${slDisplay}</div>
                            </td>
                            <td class="py-3 px-4">
                                <div>${liqDisplay}</div>
                            </td>
                            <td class="py-3 px-4 text-right">
                                <div class="font-bold ${upnlClass}">${upnlSign}$${upnl.toFixed(4)} USDT</div>
                                <div class="text-[11px] ${upnlClass} font-semibold">${upnlSign}${Number(p.roe_pct || 0).toFixed(2)}% <span class="text-[10px] text-slate-400 font-normal">ROE</span></div>
                                <div class="text-[10px] text-slate-500 font-normal">Net fees &amp; funding</div>
                            </td>
                            <td class="py-3 px-4 text-right">
                                <button onclick="inspectModelBPosition('${escapeHtml(p.symbol)}')" class="px-2.5 py-1 text-xs font-semibold rounded bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center gap-1 ml-auto shadow-sm">
                                    <svg class="w-3.5 h-3.5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                                    <span>Details</span>
                                </button>
                            </td>
                        </tr>
                    `;
                }).join('');
            } catch (err) {
                console.error("Model B positions error:", err);
            }
        }

        function inspectModelBPosition(symbol) {
            const pos = modelBCachedPositions.find(p => p.symbol === symbol);
            if (!pos) {
                showNotice(`Position details for ${symbol} not found.`, 'info');
                return;
            }
            const modal = document.getElementById('modelBPositionModal');
            const titleEl = document.getElementById('modelBPosModalTitle');
            const contentEl = document.getElementById('modelBPositionModalContent');

            if (titleEl) titleEl.innerText = `Model B Paper Position: ${pos.symbol}`;

            const isLong = (pos.side || '').toUpperCase() === 'LONG';
            const netVal = Number(pos.unrealized_pnl_net_usdt !== undefined ? pos.unrealized_pnl_net_usdt : (pos.unrealized_pnl_usdt || 0));
            const netClass = netVal >= 0 ? 'text-emerald-400' : 'text-rose-400';
            const sign = netVal >= 0 ? '+' : '';

            if (contentEl) {
                contentEl.innerHTML = `
                    <div class="grid grid-cols-2 gap-3 text-xs font-mono">
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Trade ID</span>
                            <span class="text-white font-semibold truncate block">${escapeHtml(pos.trade_id || '--')}</span>
                        </div>
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Order ID</span>
                            <span class="text-amber-400 font-semibold truncate block">${escapeHtml(pos.order_id || '--')}</span>
                        </div>
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Direction & Leverage</span>
                            <span class="font-bold ${isLong ? 'text-emerald-400' : 'text-rose-400'}">${pos.side} ${pos.leverage}x (${escapeHtml(pos.margin_mode_display || 'SIMULATED')})</span>
                        </div>
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Allocated Margin</span>
                            <span class="text-white font-semibold">${pos.allocated_margin_label || ('$' + Number(pos.allocated_margin_usdt || 0).toFixed(2))}</span>
                        </div>
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Filled Quantity</span>
                            <span class="text-white font-semibold">${pos.position_amt} ${(pos.symbol || '').replace(/USDT$/i, '')}</span>
                            <span class="text-slate-500 block text-[10px]">$${Number(pos.notional_usdt || 0).toFixed(2)} USDT Notional</span>
                        </div>
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Entry / Mark Price</span>
                            <span class="text-white font-semibold">$${formatPrice(pos.entry_price)}</span>
                            <span class="text-slate-400 block text-[10px]">Live Mark: $${formatPrice(pos.mark_price)}</span>
                        </div>
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Entry Fee Paid</span>
                            <span class="text-amber-400 font-semibold">$${Number(pos.entry_fee_paid || 0).toFixed(6)} USDT</span>
                        </div>
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Estimated Exit Fee</span>
                            <span class="text-amber-400 font-semibold">$${Number(pos.estimated_exit_fee || 0).toFixed(6)} USDT</span>
                        </div>
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Unrealized Net PnL</span>
                            <span class="font-bold text-sm ${netClass}">${sign}$${netVal.toFixed(4)} USDT</span>
                            <span class="text-[10px] text-slate-500 block">Deducts fees & funding</span>
                        </div>
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                            <span class="text-slate-500 block text-[10px] uppercase">Simulated ROE & ROI</span>
                            <span class="font-bold ${netClass}">${sign}${Number(pos.roe_pct || 0).toFixed(2)}% ROE</span>
                            <span class="text-[10px] text-slate-500 block">${sign}${Number(pos.roi_pct || 0).toFixed(2)}% ROI</span>
                        </div>
                    </div>
                    <div class="p-3 rounded-lg bg-slate-900/40 border border-slate-800 space-y-1 text-xs font-mono">
                        <div class="flex justify-between">
                            <span class="text-slate-400">Liquidation Modeling:</span>
                            <span class="text-slate-400 italic font-semibold">NOT MODELED (Simulated 10x Isolated)</span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-slate-400">Stop Loss Protection:</span>
                            <span class="text-emerald-400 font-semibold">${escapeHtml(pos.stop_loss_label || 'None')} (PROTECTED)</span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-slate-400">Take Profit:</span>
                            <span class="text-emerald-400 font-semibold">${escapeHtml(pos.take_profit_label || 'None')}</span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-slate-400">Entry Timestamp UTC:</span>
                            <span class="text-slate-300">${escapeHtml(pos.entry_time_utc || '--')}</span>
                        </div>
                    </div>
                `;
            }

            if (modal) modal.classList.remove('hidden');
        }

        function closeModelBPositionModal() {
            const modal = document.getElementById('modelBPositionModal');
            if (modal) modal.classList.add('hidden');
        }

        async function fetchModelBOrders() {
            try {
                const res = await fetch('/api/model_b/orders?status=PENDING&limit=25');
                if (!res.ok) return;
                const data = await res.json();
                const tbody = document.getElementById('modelBPendingOrdersTableBody');
                const badge = document.getElementById('modelBPendingOrdersBadge');
                if (!tbody) return;

                const orders = data.orders || [];
                if (badge) badge.innerText = `${orders.length} Pending Order${orders.length === 1 ? '' : 's'}`;

                if (orders.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="px-4 py-6 text-center text-slate-500">No pending limit orders. Conservative limit orders are queued when candidates qualify and await decisive price penetration.</td></tr>';
                    return;
                }

                tbody.innerHTML = orders.map(o => {
                    const isLong = (o.direction || '').toUpperCase() === 'LONG';
                    const dirClass = isLong ? 'text-emerald-400' : 'text-rose-400';
                    const dirBg = isLong ? 'border-emerald-600 bg-emerald-950/40' : 'border-rose-600 bg-rose-950/40';

                    const notional = Number(o.notional_usdt || (o.quantity * o.price) || 0).toFixed(2);
                    const margin = Number(o.margin_usdt || (notional / (o.leverage || 10.0)) || 0).toFixed(2);
                    const createdStr = o.created_at_utc ? new Date(o.created_at_utc).toLocaleTimeString() : '--';

                    return `
                        <tr class="hover:bg-slate-800/40 transition">
                            <td class="px-4 py-3">
                                <div class="font-bold text-white">${escapeHtml(o.symbol)}</div>
                                <div class="text-[10px] text-amber-400 font-mono">${escapeHtml(o.order_id)}</div>
                            </td>
                            <td class="px-4 py-3">
                                <span class="px-1.5 py-0.2 rounded text-[10px] font-bold border ${dirBg} ${dirClass}">${o.direction}</span>
                                <span class="text-slate-400 text-[10px] ml-1">${o.order_type || 'LIMIT'}</span>
                            </td>
                            <td class="px-4 py-3 font-semibold text-white">$${formatPrice(o.price)}</td>
                            <td class="px-4 py-3 text-[11px]">
                                <div class="text-rose-400">SL: $${formatPrice(o.stop_loss)}</div>
                                <div class="text-emerald-400">TP: $${formatPrice(o.take_profit)}</div>
                            </td>
                            <td class="px-4 py-3 text-[11px]">
                                <div class="text-white">$${notional} USDT (${o.leverage || 10}x)</div>
                                <div class="text-slate-400 text-[10px]">Margin: $${margin}</div>
                            </td>
                            <td class="px-4 py-3 text-[10px] text-slate-400 font-mono">${createdStr}</td>
                            <td class="px-4 py-3">
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-300 border border-amber-600">
                                    AWAITING FILL
                                </span>
                                <div class="text-[10px] text-slate-500 mt-0.5">Eval Bars: ${o.evaluated_bars_count || 0} / 12</div>
                            </td>
                        </tr>
                    `;
                }).join('');
            } catch (err) {
                console.error("Model B orders error:", err);
            }
        }

        async function fetchModelBJournal() {
            try {
                const res = await fetch('/api/model_b/journal?limit=25');
                if (!res.ok) return;
                const data = await res.json();

                const metrics = data.metrics || {};
                const wrEl = document.getElementById('modelBWinRate');
                if (wrEl) wrEl.innerText = `${metrics.win_rate_pct || 0.0}%`;

                const statWr = document.getElementById('modelBStatWinRate');
                if (statWr) statWr.innerText = `${metrics.win_rate_pct || 0.0}%`;

                const netEl = document.getElementById('modelBNetPnl');
                const statNet = document.getElementById('modelBStatRealizedPnl');
                const netVal = Number(metrics.net_pnl || 0.0);
                const netText = `${netVal >= 0 ? '+$' : '-$'}${Math.abs(netVal).toFixed(2)}`;
                const netCls = netVal > 0 ? 'text-emerald-400' : (netVal < 0 ? 'text-rose-400' : 'text-white');
                if (netEl) {
                    netEl.innerText = netText;
                    netEl.className = netCls;
                }
                if (statNet) {
                    statNet.innerText = netText;
                    statNet.className = `text-lg font-bold mt-0.5 ${netCls}`;
                }

                const statClosed = document.getElementById('modelBStatClosedTrades');
                if (statClosed) {
                    statClosed.innerText = `${metrics.closed_trades_count || 0} Closed`;
                }

                const badge = document.getElementById('modelBJournalMetricsBadge');
                if (badge) badge.innerText = `${metrics.closed_trades_count || 0} Closed / ${metrics.open_trades_count || 0} Open`;

                const tbody = document.getElementById('modelBJournalTableBody');
                if (!tbody) return;

                const trades = data.trades || [];
                if (trades.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="px-4 py-6 text-center text-slate-500">No Model B trades executed yet.</td></tr>';
                    return;
                }

                tbody.innerHTML = trades.map(t => {
                    const isLong = (t.direction || '').toUpperCase() === 'LONG';
                    const dirClass = isLong ? 'text-emerald-400' : 'text-rose-400';
                    const netPnl = Number(t.pnl_net || 0);
                    const netClass = netPnl > 0 ? 'text-emerald-400' : (netPnl < 0 ? 'text-rose-400' : 'text-slate-300');

                    return `
                        <tr class="hover:bg-slate-800/40 transition">
                            <td class="px-4 py-3">
                                <div class="font-bold text-white">${escapeHtml(t.symbol)}</div>
                                <div class="text-[10px] text-slate-500">${escapeHtml(t.trade_id.substring(0, 10))}</div>
                            </td>
                            <td class="px-4 py-3 ${dirClass} font-semibold">${t.direction}</td>
                            <td class="px-4 py-3">
                                <div class="text-white">$${formatPrice(t.entry_price)}</div>
                                <div class="text-[10px] text-slate-400">Exit: ${t.exit_price ? '$' + formatPrice(t.exit_price) : 'OPEN'}</div>
                            </td>
                            <td class="px-4 py-3 text-slate-300">$${Number(t.pnl_gross || 0).toFixed(3)}</td>
                            <td class="px-4 py-3 text-amber-400">$${Number(t.fee_paid || 0).toFixed(3)}</td>
                            <td class="px-4 py-3 font-bold ${netClass}">${netPnl >= 0 ? '+$' : '-$'}${Math.abs(netPnl).toFixed(3)}</td>
                            <td class="px-4 py-3">
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold ${t.outcome === 'WIN' ? 'bg-emerald-950 text-emerald-300 border border-emerald-600' : (t.outcome === 'LOSS' ? 'bg-rose-950 text-rose-300 border border-rose-600' : 'bg-slate-800 text-slate-300')}">
                                    ${t.outcome || (t.is_open ? 'OPEN' : 'CLOSED')}
                                </span>
                            </td>
                        </tr>
                    `;
                }).join('');

            } catch (err) {
                console.error("Model B journal error:", err);
            }
        }

        async function fetchModelBOrderBook() {
            const symInput = document.getElementById('modelBInspectSymbol');
            const sym = symInput ? symInput.value.trim().toUpperCase() : 'BTCUSDT';
            const container = document.getElementById('modelBOrderBookContainer');
            if (!container) return;

            try {
                const res = await fetch(`/api/model_b/orderbook/${sym}`);
                if (!res.ok) return;
                const data = await res.json();
                const books = data.books || {};

                container.innerHTML = ['asterdex', 'binance', 'hyperliquid'].map(ex => {
                    const b = books[ex];
                    if (!b) {
                        return `
                            <div class="p-4 rounded-lg bg-slate-900/60 border border-slate-800/80 space-y-2">
                                <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                                    <span class="font-bold text-white uppercase text-xs">${escapeHtml(ex)}</span>
                                    <span class="px-1.5 py-0.2 rounded text-[9px] font-bold bg-slate-800 text-slate-400">UNAVAILABLE</span>
                                </div>
                                <div class="text-xs text-slate-500 py-4 text-center">No depth data for ${sym}</div>
                            </div>
                        `;
                    }

                    const imb = b.spread_bps !== undefined ? b.spread_bps : 0.0;
                    const bb = b.best_bid !== null ? `$${formatPrice(b.best_bid)}` : '--';
                    const ba = b.best_ask !== null ? `$${formatPrice(b.best_ask)}` : '--';

                    return `
                        <div class="p-4 rounded-lg bg-slate-900/60 border border-slate-800/80 space-y-3">
                            <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                                <span class="font-bold text-white uppercase text-xs">${escapeHtml(ex)}</span>
                                <span class="px-1.5 py-0.2 rounded text-[9px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">LIVE FEED</span>
                            </div>
                            <div class="grid grid-cols-2 gap-2 text-xs font-mono">
                                <div>
                                    <span class="text-slate-500 block text-[10px]">Best Bid</span>
                                    <span class="text-emerald-400 font-bold">${bb}</span>
                                </div>
                                <div>
                                    <span class="text-slate-500 block text-[10px]">Best Ask</span>
                                    <span class="text-rose-400 font-bold">${ba}</span>
                                </div>
                            </div>
                            <div class="pt-2 border-t border-slate-800/60 flex items-center justify-between text-xs font-mono">
                                <span class="text-slate-400">Spread: <strong class="text-white">${b.spread_bps || 0} bps</strong></span>
                                <span class="text-slate-400">Mid: <strong class="text-white">$${formatPrice(b.mid_price)}</strong></span>
                            </div>
                        </div>
                    `;
                }).join('');

            } catch (err) {
                console.error("Model B orderbook error:", err);
            }
        }

        async function triggerModelBScan() {
            const btn = document.getElementById('modelBScanBtn');
            const spin = document.getElementById('modelBScanSpin');
            if (btn) btn.disabled = true;
            if (spin) spin.classList.add('animate-spin');

            try {
                const res = await fetch('/api/model_b/scan', { method: 'POST' });
                const data = await res.json();
                showNotice(`Model B scan completed: evaluated ${data.total_evaluated || 0} pairs, found ${data.ready_count || 0} READY setups.`, 'info');
                await loadModelB();
            } catch (err) {
                showNotice('Model B scan error: ' + err, 'error');
            } finally {
                if (btn) btn.disabled = false;
                if (spin) spin.classList.remove('animate-spin');
            }
        }

        // =========================================================================
        // MODEL B WHALE FLOW INTELLIGENCE & WATCHLIST
        // =========================================================================
        let cachedWhaleStreamEvents = [];
        let currentWhaleStreamFilter = 'ALL';

        async function fetchModelBWhaleWatchlist(forceRefresh = false) {
            const spin = document.getElementById('modelBWhaleSpin');
            const btn = document.getElementById('modelBWhaleScanBtn');
            if (spin && forceRefresh) spin.classList.add('animate-spin');
            if (btn && forceRefresh) btn.disabled = true;

            try {
                const url = forceRefresh ? '/api/model_b/whale_watchlist?max_symbols=20&force_refresh=true' : '/api/model_b/whale_watchlist?max_symbols=20';
                const res = await fetch(url);
                if (!res.ok) return;
                const data = await res.json();
                renderWhaleTopBuy(data.top_buy_activity || []);
                renderWhaleTopSell(data.top_sell_activity || []);
                if (data.active_resting_walls) {
                    renderWhaleWalls(data.active_resting_walls);
                }
            } catch (err) {
                console.error("Whale watchlist error:", err);
            } finally {
                if (spin) spin.classList.remove('animate-spin');
                if (btn) btn.disabled = false;
            }
        }

        async function fetchModelBWhaleWalls() {
            try {
                const res = await fetch('/api/model_b/whale_active_walls');
                if (!res.ok) return;
                const data = await res.json();
                renderWhaleWalls(data.walls || []);
            } catch (err) {
                console.error("Whale walls error:", err);
            }
        }

        async function fetchModelBWhaleEvents() {
            try {
                const res = await fetch('/api/model_b/whale_events?limit=50');
                if (!res.ok) return;
                const data = await res.json();
                cachedWhaleStreamEvents = data.events || [];
                renderWhaleEvents();
            } catch (err) {
                console.error("Whale events error:", err);
            }
        }

        function renderWhaleTopBuy(items) {
            const tbody = document.getElementById('modelBWhaleTopBuyBody');
            if (!tbody) return;

            if (!items || items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="py-4 text-center text-slate-500">No unusual buying flow detected.</td></tr>';
                return;
            }

            tbody.innerHTML = items.map(item => {
                const score = Number(item.whale_buy_score || 0).toFixed(1);
                const bids = Number(item.resting_bid_notional_usdt || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
                const persist = item.max_bid_persistence_sec ? `${Math.round(item.max_bid_persistence_sec)}s` : '--';
                
                let interpBadge = 'bg-slate-800 text-slate-300 border-slate-700';
                if (item.interpretation === 'BULLISH_CONTINUATION_SUPPORT') {
                    interpBadge = 'bg-emerald-950 text-emerald-300 border-emerald-700 font-bold';
                } else if (item.interpretation === 'ABSORPTION_ZONE') {
                    interpBadge = 'bg-cyan-950 text-cyan-300 border-cyan-700 font-bold';
                }

                return `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="py-2.5 pr-2">
                            <div class="font-bold text-white flex items-center gap-1.5">
                                <span>${escapeHtml(item.symbol)}</span>
                                <span class="text-[10px] text-slate-400 font-normal">$${formatPrice(item.mark_price)}</span>
                            </div>
                            <div class="text-[10px] text-slate-500 flex gap-1 mt-0.5">
                                ${(item.venues_covered || []).map(v => `<span class="uppercase font-semibold">${v.substring(0, 3)}</span>`).join('&bull;')}
                            </div>
                        </td>
                        <td class="py-2.5 px-2 text-right">
                            <span class="px-2 py-0.5 rounded text-xs font-bold bg-emerald-950 text-emerald-300 border border-emerald-700">
                                ${score}
                            </span>
                        </td>
                        <td class="py-2.5 px-2 text-right text-emerald-400 font-semibold">$${bids}</td>
                        <td class="py-2.5 px-2 text-right text-slate-300">${persist}</td>
                        <td class="py-2.5 px-2">
                            <span class="px-1.5 py-0.5 rounded text-[10px] border ${interpBadge}">
                                ${escapeHtml(item.interpretation || 'BALANCED')}
                            </span>
                        </td>
                        <td class="py-2.5 pl-2 text-center">
                            <button onclick="openInSweepChart('${item.symbol}')" class="px-2 py-1 text-[10px] bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded border border-slate-700 transition" title="View on Liquidity Sweep Chart">
                                Chart &rarr;
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        function renderWhaleTopSell(items) {
            const tbody = document.getElementById('modelBWhaleTopSellBody');
            if (!tbody) return;

            if (!items || items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="py-4 text-center text-slate-500">No unusual selling pressure detected.</td></tr>';
                return;
            }

            tbody.innerHTML = items.map(item => {
                const score = Number(item.whale_sell_score || 0).toFixed(1);
                const asks = Number(item.resting_ask_notional_usdt || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
                const persist = item.max_ask_persistence_sec ? `${Math.round(item.max_ask_persistence_sec)}s` : '--';

                let interpBadge = 'bg-slate-800 text-slate-300 border-slate-700';
                if (item.interpretation === 'BEARISH_PRESSURE_CAP') {
                    interpBadge = 'bg-rose-950 text-rose-300 border-rose-700 font-bold';
                } else if (item.interpretation === 'ABSORPTION_ZONE') {
                    interpBadge = 'bg-cyan-950 text-cyan-300 border-cyan-700 font-bold';
                }

                return `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="py-2.5 pr-2">
                            <div class="font-bold text-white flex items-center gap-1.5">
                                <span>${escapeHtml(item.symbol)}</span>
                                <span class="text-[10px] text-slate-400 font-normal">$${formatPrice(item.mark_price)}</span>
                            </div>
                            <div class="text-[10px] text-slate-500 flex gap-1 mt-0.5">
                                ${(item.venues_covered || []).map(v => `<span class="uppercase font-semibold">${v.substring(0, 3)}</span>`).join('&bull;')}
                            </div>
                        </td>
                        <td class="py-2.5 px-2 text-right">
                            <span class="px-2 py-0.5 rounded text-xs font-bold bg-rose-950 text-rose-300 border border-rose-700">
                                ${score}
                            </span>
                        </td>
                        <td class="py-2.5 px-2 text-right text-rose-400 font-semibold">$${asks}</td>
                        <td class="py-2.5 px-2 text-right text-slate-300">${persist}</td>
                        <td class="py-2.5 px-2">
                            <span class="px-1.5 py-0.5 rounded text-[10px] border ${interpBadge}">
                                ${escapeHtml(item.interpretation || 'BALANCED')}
                            </span>
                        </td>
                        <td class="py-2.5 pl-2 text-center">
                            <button onclick="openInSweepChart('${item.symbol}')" class="px-2 py-1 text-[10px] bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded border border-slate-700 transition" title="View on Liquidity Sweep Chart">
                                Chart &rarr;
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        function renderWhaleWalls(walls) {
            const tbody = document.getElementById('modelBWhaleWallsBody');
            const badge = document.getElementById('modelBActiveWallsBadge');
            if (badge) badge.innerText = `${(walls || []).length} Active Wall${(walls || []).length === 1 ? '' : 's'}`;
            if (!tbody) return;

            if (!walls || walls.length === 0) {
                tbody.innerHTML = '<tr><td colspan="9" class="py-4 text-center text-slate-500">No active resting depth walls currently resting in order books.</td></tr>';
                return;
            }

            tbody.innerHTML = walls.map(w => {
                const isBid = w.side === 'BID';
                const sideClass = isBid ? 'bg-emerald-950 text-emerald-300 border-emerald-700' : 'bg-rose-950 text-rose-300 border-rose-700';
                const notional = Number(w.notional_usdt || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });

                let spoofBadge = '';
                if (w.is_spoof_candidate) {
                    spoofBadge = '<span class="px-1.5 py-0.2 rounded text-[9px] font-bold bg-amber-950 text-amber-300 border border-amber-700 ml-1">PRE-TOUCH CANCEL</span>';
                }

                return `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="py-2.5 px-3 font-bold text-white">${escapeHtml(w.symbol)}</td>
                        <td class="py-2.5 px-3 uppercase text-[10px] text-slate-400 font-semibold">${escapeHtml(w.venue || 'asterdex')}</td>
                        <td class="py-2.5 px-3"><span class="px-2 py-0.5 rounded text-[10px] font-bold border ${sideClass}">${w.side}</span></td>
                        <td class="py-2.5 px-3 text-right text-white font-semibold">$${formatPrice(w.price)}</td>
                        <td class="py-2.5 px-3 text-right ${isBid ? 'text-emerald-400' : 'text-rose-400'} font-semibold">$${notional}</td>
                        <td class="py-2.5 px-3 text-right text-slate-400">${Number(w.distance_bps || 0).toFixed(1)} bps</td>
                        <td class="py-2.5 px-3 text-right text-slate-300">${Number(w.persistence_seconds || 0).toFixed(1)}s</td>
                        <td class="py-2.5 px-3 text-[11px] text-slate-300">
                            <span>RESTING WALL</span>
                            ${spoofBadge}
                        </td>
                        <td class="py-2.5 px-3 text-center">
                            <button onclick="openInSweepChart('${w.symbol}', ${w.price})" class="px-2 py-1 text-[10px] bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded border border-slate-700 transition">
                                Inspect &rarr;
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        function setWhaleStreamFilter(filt) {
            currentWhaleStreamFilter = filt;
            ['ALL', 'RESTING', 'EXECUTED', 'SPOOFS', 'ABSORPTION'].forEach(f => {
                const btn = document.getElementById(`streamFilt-${f}`);
                if (btn) {
                    btn.className = (f === filt)
                        ? 'px-2 py-0.5 text-[10px] rounded font-bold bg-indigo-900 text-indigo-200 border border-indigo-700'
                        : 'px-2 py-0.5 text-[10px] rounded bg-slate-800 text-slate-400 border border-slate-700';
                }
            });
            renderWhaleEvents();
        }

        function renderWhaleEvents() {
            const tbody = document.getElementById('modelBWhaleEventsBody');
            if (!tbody) return;

            let filtered = cachedWhaleStreamEvents;
            if (currentWhaleStreamFilter === 'RESTING') {
                filtered = filtered.filter(e => e.event_class === 'RESTING' || (e.event_type && (e.event_type.includes('BID') || e.event_type.includes('ASK'))));
            } else if (currentWhaleStreamFilter === 'EXECUTED') {
                filtered = filtered.filter(e => e.event_class === 'EXECUTED' || (e.event_type && e.event_type.includes('AGGRESSIVE')));
            } else if (currentWhaleStreamFilter === 'SPOOFS') {
                filtered = filtered.filter(e => e.is_spoof_candidate || (e.event_type && (e.event_type.includes('SPOOF') || e.event_type.includes('CANCELLED'))));
            } else if (currentWhaleStreamFilter === 'ABSORPTION') {
                filtered = filtered.filter(e => (e.event_type && e.event_type.includes('ABSORPTION')));
            }

            if (!filtered || filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="9" class="py-4 text-center text-slate-500">No events matching filter '${currentWhaleStreamFilter}'.</td></tr>`;
                return;
            }

            tbody.innerHTML = filtered.slice(0, 30).map(e => {
                const timeStr = e.timestamp_ms ? new Date(e.timestamp_ms).toLocaleTimeString() : '--';
                const isBuy = e.side === 'BUY' || e.side === 'BID';
                const sideClass = isBuy ? 'text-emerald-400 font-semibold' : (e.side === 'SELL' || e.side === 'ASK' ? 'text-rose-400 font-semibold' : 'text-slate-400');
                const notional = Number(e.notional_usdt || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });

                let outcomeBadge = '<span class="text-slate-500 text-[10px] font-mono">PENDING</span>';
                if (e.correlation_outcome === 'CONTINUATION') {
                    outcomeBadge = '<span class="px-1.5 py-0.2 rounded text-[9px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-700">CONTINUATION</span>';
                } else if (e.correlation_outcome === 'REVERSAL') {
                    outcomeBadge = '<span class="px-1.5 py-0.2 rounded text-[9px] font-bold bg-rose-950 text-rose-300 border border-rose-700">REVERSAL</span>';
                } else if (e.correlation_outcome === 'CHOP') {
                    outcomeBadge = '<span class="px-1.5 py-0.2 rounded text-[9px] bg-slate-800 text-slate-400 border border-slate-700">CHOP</span>';
                }

                return `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="py-2.5 px-3 text-slate-400 font-mono text-[10px]">${timeStr}</td>
                        <td class="py-2.5 px-3 font-bold text-white">
                            <a href="javascript:void(0)" onclick="openInSweepChart('${e.symbol}')" class="hover:text-cyan-400 transition underline underline-offset-2">
                                ${escapeHtml(e.symbol)}
                            </a>
                        </td>
                        <td class="py-2.5 px-3 uppercase text-[10px] text-slate-400 font-semibold">${escapeHtml(e.venue || 'asterdex')}</td>
                        <td class="py-2.5 px-3 font-mono text-[10px] text-indigo-300">${escapeHtml(e.event_type || '')}</td>
                        <td class="py-2.5 px-3 ${sideClass}">${e.side || '--'}</td>
                        <td class="py-2.5 px-3 text-right text-white font-mono">$${formatPrice(e.price)}</td>
                        <td class="py-2.5 px-3 text-right font-semibold text-slate-200">$${notional}</td>
                        <td class="py-2.5 px-3 text-[10px] text-slate-400 max-w-xs truncate" title="${escapeHtml(e.details || '')}">
                            ${escapeHtml(e.details || '')}
                        </td>
                        <td class="py-2.5 px-3">${outcomeBadge}</td>
                    </tr>
                `;
            }).join('');
        }

        function openInSweepChart(symbol, priceLevel = null) {
            const sym = (symbol || 'BTCUSDT').trim().toUpperCase().replace('.P', '');
            const chartInput = document.getElementById('chartSymbolInput');
            const chartSel = document.getElementById('chartSymbolSelect');
            if (chartInput) chartInput.value = sym;
            if (chartSel) chartSel.value = sym;

            if (typeof switchTab === 'function') {
                switchTab('sweep_chart');
            } else {
                window.location.hash = '#sweep_chart';
            }

            if (typeof loadLiquidityChartData === 'function') {
                loadLiquidityChartData();
            }
        }

        // Periodic Model B refresh when Model B tab is active
        setInterval(() => {
            const tab = document.getElementById('tab-model_b');
            if (tab && !tab.classList.contains('hidden')) {
                loadModelB();
            }
        }, 10000);

        // -------------------------------------------------------------------------
        // Model B Live Shadow Research Client Implementation
        // -------------------------------------------------------------------------
        let researchCachedMetrics = null;
        let researchCachedSignals = [];
        let researchFilterDirection = 'ALL';
        let researchFilterSymbol = '';

        async function fetchModelBResearchMetrics() {
            try {
                const res = await fetch('/api/model_b/research/metrics');
                if (!res.ok) return;
                const data = await res.json();
                researchCachedMetrics = data;

                // 1. Active Portfolio vs Shadow Engine Status
                const actPort = data.active_portfolio || {};
                const rLimits = actPort.risk_limits || {};
                const activeEl = document.getElementById('researchActivePortfolioStatus');
                if (activeEl) {
                    const isHalted = actPort.circuit_breaker_active;
                    if (isHalted) {
                        const dailyLoss = Number(rLimits.daily_loss_realized_usdt || 0).toFixed(2);
                        const dailyCap = Number(rLimits.daily_loss_limit_usdt || 3.0).toFixed(2);
                        activeEl.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800 animate-pulse">HALTED (Daily Cap $${dailyLoss} &ge; $${dailyCap})</span>`;
                    } else {
                        activeEl.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800">ACTIVE &bull; 0/3R LOSS</span>`;
                    }
                }

                // Freshness
                const fresh = data.market_data_freshness || {};
                const freshEl = document.getElementById('researchDataFreshness');
                if (freshEl && fresh.last_scan_utc) {
                    const dt = new Date(fresh.last_scan_utc).toLocaleTimeString();
                    const dur = fresh.latest_cycle_duration_ms ? ` (${Math.round(fresh.latest_cycle_duration_ms)}ms)` : '';
                    freshEl.innerText = `Last Scan: ${dt}${dur} &bull; ${fresh.discovered_markets_count || 578} Pairs`;
                }

                // 2. Synthetic Portfolios
                const ports = data.portfolios || {};
                const basePort = ports.baseline || {};
                const optPort = ports.combined_optimized || {};

                // Baseline
                const bPnl = document.getElementById('baselinePortPnl');
                if (bPnl) {
                    const v = Number(basePort.net_pnl_usdt || 0);
                    bPnl.innerText = `${v >= 0 ? '+' : ''}$${v.toFixed(2)} USDT`;
                    bPnl.className = `text-xs font-mono font-bold ${v >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
                }
                const bClosed = document.getElementById('baselinePortClosed');
                if (bClosed) bClosed.innerText = basePort.closed_trades || 0;
                const bWin = document.getElementById('baselinePortWinRate');
                if (bWin) bWin.innerText = `${Number(basePort.win_rate_pct || 0).toFixed(1)}%`;
                const bPf = document.getElementById('baselinePortPf');
                if (bPf) bPf.innerText = Number(basePort.profit_factor || 0).toFixed(2);
                const bDd = document.getElementById('baselinePortDd');
                if (bDd) bDd.innerText = `$${Number(basePort.max_drawdown_usdt || 0).toFixed(2)}`;
                const bLongs = document.getElementById('baselinePortLongs');
                if (bLongs) bLongs.innerText = `${basePort.long_trades || 0} LONG`;
                const bShorts = document.getElementById('baselinePortShorts');
                if (bShorts) bShorts.innerText = `${basePort.short_trades || 0} SHORT`;

                // Optimized
                const oPnl = document.getElementById('optimizedPortPnl');
                if (oPnl) {
                    const v = Number(optPort.net_pnl_usdt || 0);
                    oPnl.innerText = `${v >= 0 ? '+' : ''}$${v.toFixed(2)} USDT`;
                    oPnl.className = `text-xs font-mono font-bold ${v >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
                }
                const oClosed = document.getElementById('optimizedPortClosed');
                if (oClosed) oClosed.innerText = optPort.closed_trades || 0;
                const oWin = document.getElementById('optimizedPortWinRate');
                if (oWin) oWin.innerText = `${Number(optPort.win_rate_pct || 0).toFixed(1)}%`;
                const oPf = document.getElementById('optimizedPortPf');
                if (oPf) oPf.innerText = Number(optPort.profit_factor || 0).toFixed(2);
                const oDd = document.getElementById('optimizedPortDd');
                if (oDd) oDd.innerText = `$${Number(optPort.max_drawdown_usdt || 0).toFixed(2)}`;
                const oLongs = document.getElementById('optimizedPortLongs');
                if (oLongs) oLongs.innerText = `${optPort.long_trades || 0} LONG`;
                const oShorts = document.getElementById('optimizedPortShorts');
                if (oShorts) oShorts.innerText = `${optPort.short_trades || 0} SHORT`;

                // Delta
                const deltaEl = document.getElementById('researchPortfolioDeltaBadge');
                if (deltaEl) {
                    const winDelta = Number(optPort.win_rate_pct || 0) - Number(basePort.win_rate_pct || 0);
                    const pnlDelta = Number(optPort.net_pnl_usdt || 0) - Number(basePort.net_pnl_usdt || 0);
                    const ddDelta = Number(basePort.max_drawdown_usdt || 0) - Number(optPort.max_drawdown_usdt || 0);
                    deltaEl.innerText = `Win Rate: +${winDelta.toFixed(1)}% | Net PnL: +$${pnlDelta.toFixed(2)} | Drawdown Cut: -$${ddDelta.toFixed(2)}`;
                }

                // 3. Render 6-Variant Cards
                renderResearchVariantCards(data.variants || {}, data.metadata || {});

            } catch (err) {
                console.error("fetchModelBResearchMetrics error:", err);
            }
        }

        function renderResearchVariantCards(variants, metadata) {
            const container = document.getElementById('researchVariantCardsContainer');
            if (!container) return;

            const variantKeys = [
                'VARIANT_A_LONG_BASELINE',
                'VARIANT_B_SHORT_BASELINE',
                'VARIANT_C_SHORT_4H_TREND',
                'VARIANT_D_SHORT_CONFIRMED_CLOSE',
                'VARIANT_E_SHORT_ORDER_FLOW',
                'VARIANT_F_SHORT_COMBINED',
            ];

            container.innerHTML = variantKeys.map(k => {
                const v = variants[k] || {};
                const m = metadata[k] || {};
                const letter = m.letter || k.split('_')[1];
                const name = m.name || v.variant_name || k;
                const side = m.side || (k.includes('LONG') ? 'LONG' : 'SHORT');
                const pnl = Number(v.net_pnl_usdt || 0);
                const pnlColor = pnl > 0 ? 'text-emerald-400' : (pnl < 0 ? 'text-rose-400' : 'text-slate-300');
                const winRate = Number(v.win_rate_pct || 0).toFixed(1);
                const pf = Number(v.profit_factor || 0).toFixed(2);
                const dd = Number(v.max_drawdown_usdt || 0).toFixed(2);
                const sideBadgeClass = side === 'LONG' ? 'bg-emerald-950 text-emerald-300 border-emerald-800' : (letter === 'F' ? 'bg-indigo-950 text-indigo-300 border-indigo-700' : 'bg-rose-950 text-rose-300 border-rose-800');

                return `
                    <div class="p-4 rounded-xl bg-slate-900/70 border border-slate-800 space-y-3 flex flex-col justify-between hover:border-slate-700 transition">
                        <div>
                            <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                                <div class="flex items-center gap-2">
                                    <span class="w-6 h-6 rounded-full bg-slate-800 text-white font-mono font-bold text-xs flex items-center justify-center border border-slate-700">${letter}</span>
                                    <div>
                                        <div class="font-bold text-xs text-white">${escapeHtml(name)}</div>
                                        <div class="text-[10px] text-slate-500">${escapeHtml(m.description || '')}</div>
                                    </div>
                                </div>
                                <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border ${sideBadgeClass}">${side}</span>
                            </div>

                            <div class="grid grid-cols-3 gap-2 text-center text-xs font-mono pt-3">
                                <div class="p-2 rounded bg-slate-950/60 border border-slate-800/80">
                                    <div class="text-slate-400 text-[10px]">Net PnL</div>
                                    <div class="text-xs font-bold ${pnlColor} mt-0.5">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</div>
                                </div>
                                <div class="p-2 rounded bg-slate-950/60 border border-slate-800/80">
                                    <div class="text-slate-400 text-[10px]">Win Rate</div>
                                    <div class="text-xs font-bold text-white mt-0.5">${winRate}%</div>
                                </div>
                                <div class="p-2 rounded bg-slate-950/60 border border-slate-800/80">
                                    <div class="text-slate-400 text-[10px]">Profit Factor</div>
                                    <div class="text-xs font-bold text-white mt-0.5">${pf}</div>
                                </div>
                            </div>

                            <div class="space-y-1.5 pt-3 text-[11px] font-mono text-slate-400">
                                <div class="flex justify-between">
                                    <span>Signals (Seen / Acc / Rej):</span>
                                    <span class="text-slate-200">${v.total_signals_seen || 0} / <strong class="text-emerald-400">${v.signals_accepted || 0}</strong> / <strong class="text-rose-400">${v.signals_rejected || 0}</strong></span>
                                </div>
                                <div class="flex justify-between">
                                    <span>Sim Fills / Pending / Missed:</span>
                                    <span class="text-slate-200">${v.simulated_fills || 0} / ${v.pending_orders || 0} / <strong class="text-amber-400">${v.missed_fills || 0}</strong></span>
                                </div>
                                <div class="flex justify-between">
                                    <span>Trades (Closed / Win / Loss):</span>
                                    <span class="text-slate-200">${v.simulated_closed || 0} / <strong class="text-emerald-400">${v.winning_trades || 0}</strong> / <strong class="text-rose-400">${v.losing_trades || 0}</strong></span>
                                </div>
                                <div class="flex justify-between">
                                    <span>Max Drawdown:</span>
                                    <span class="text-rose-400 font-bold">$${dd}</span>
                                </div>
                                <div class="flex justify-between">
                                    <span>Total Drag (Fee/Slip/Fund):</span>
                                    <span class="text-slate-300">$${Number(v.total_fee_drag_usdt || 0).toFixed(2)} / $${Number(v.total_slippage_drag_usdt || 0).toFixed(2)} / $${Number(v.total_funding_drag_usdt || 0).toFixed(2)}</span>
                                </div>
                                <div class="flex justify-between">
                                    <span>Excursions (MAE / MFE):</span>
                                    <span class="text-slate-300">${Number(v.avg_mae_pct || 0).toFixed(2)}% / ${Number(v.avg_mfe_pct || 0).toFixed(2)}%</span>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function fetchModelBResearchSignals() {
            try {
                let url = `/api/model_b/research/signals?limit=60`;
                if (researchFilterDirection && researchFilterDirection !== 'ALL') {
                    url += `&direction=${researchFilterDirection}`;
                }
                if (researchFilterSymbol) {
                    url += `&symbol=${encodeURIComponent(researchFilterSymbol)}`;
                }

                const res = await fetch(url);
                if (!res.ok) return;
                const data = await res.json();
                researchCachedSignals = data.signals || [];

                const badge = document.getElementById('researchSignalsCountBadge');
                if (badge) badge.innerText = `${data.count || 0} Signals`;

                const tbody = document.getElementById('researchSignalsTableBody');
                if (!tbody) return;

                if (researchCachedSignals.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="7" class="px-4 py-6 text-center text-slate-500">No research signals found matching filters.</td></tr>`;
                    return;
                }

                tbody.innerHTML = researchCachedSignals.map(sig => {
                    const timeStr = new Date(sig.timestamp_ms).toLocaleString();
                    const isLong = sig.direction === 'LONG';
                    const dirClass = isLong ? 'text-emerald-400 bg-emerald-950 border-emerald-800' : 'text-rose-400 bg-rose-950 border-rose-800';

                    // Active Eligibility Pill
                    let eligBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-800">ELIGIBLE</span>';
                    if (!sig.active_execution_eligible) {
                        const reason = sig.active_ineligible_reason || 'HALTED';
                        eligBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-mono bg-amber-950 text-amber-300 border border-amber-800" title="${escapeHtml(reason)}">HALTED: ${escapeHtml(reason.slice(0, 22))}...</span>`;
                    }

                    // Paired decisions mini pills: A, B, C, D, E, F
                    const evals = sig.evaluations || [];
                    const evMap = {};
                    evals.forEach(e => { evMap[e.variant] = e; });

                    const variantLetters = [
                        ['A', 'VARIANT_A_LONG_BASELINE'],
                        ['B', 'VARIANT_B_SHORT_BASELINE'],
                        ['C', 'VARIANT_C_SHORT_4H_TREND'],
                        ['D', 'VARIANT_D_SHORT_CONFIRMED_CLOSE'],
                        ['E', 'VARIANT_E_SHORT_ORDER_FLOW'],
                        ['F', 'VARIANT_F_SHORT_COMBINED'],
                    ];

                    const decisionPills = variantLetters.map(([lettr, vKey]) => {
                        const ev = evMap[vKey];
                        if (!ev) return `<span class="w-5 h-5 rounded text-[10px] font-bold flex items-center justify-center bg-slate-800 text-slate-500">${lettr}</span>`;
                        const isAcc = ev.decision === 'ACCEPTED';
                        const cls = isAcc ? 'bg-emerald-900/80 text-emerald-300 border border-emerald-700' : 'bg-rose-950/80 text-rose-400 border border-rose-800/60';
                        const title = `${lettr}: ${ev.decision}${ev.rejection_reason ? ' (' + ev.rejection_reason + ')' : ''}`;
                        return `<span class="w-5 h-5 rounded text-[10px] font-mono font-bold flex items-center justify-center ${cls}" title="${escapeHtml(title)}">${lettr}</span>`;
                    }).join(' ');

                    // Net PnL display across evaluated variants
                    const closedEvals = evals.filter(e => e.order_status === 'CLOSED');
                    let pnlStr = '<span class="text-slate-500">--</span>';
                    if (closedEvals.length > 0) {
                        const rep = closedEvals[0];
                        const pnl = Number(rep.pnl_net || 0);
                        const pnlCls = pnl > 0 ? 'text-emerald-400' : (pnl < 0 ? 'text-rose-400' : 'text-slate-300');
                        pnlStr = `<span class="${pnlCls} font-bold">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</span>`;
                    } else if (evals.some(e => e.order_status === 'EXPIRED')) {
                        pnlStr = '<span class="text-amber-400 font-mono text-[10px]">MISSED (TTL)</span>';
                    } else if (evals.some(e => e.order_status === 'PENDING')) {
                        pnlStr = '<span class="text-indigo-400 font-mono text-[10px]">PENDING</span>';
                    }

                    return `
                        <tr class="hover:bg-slate-900/50 transition">
                            <td class="px-3.5 py-3">
                                <div class="text-white font-medium">${timeStr}</div>
                                <div class="text-[10px] text-slate-500 font-mono">${escapeHtml(sig.signal_id)}</div>
                            </td>
                            <td class="px-3.5 py-3">
                                <span class="font-bold text-white">${escapeHtml(sig.symbol)}</span>
                                <span class="px-1.5 py-0.5 rounded text-[10px] font-bold border ml-1.5 ${dirClass}">${sig.direction}</span>
                            </td>
                            <td class="px-3.5 py-3 font-mono">
                                <div><span class="text-slate-500">Entry:</span> $${formatPrice(sig.entry_price)}</div>
                                <div class="text-[10px] text-slate-400"><span class="text-slate-500">SL:</span> $${formatPrice(sig.stop_loss)} | <span class="text-slate-500">TP:</span> $${formatPrice(sig.take_profit_1)}</div>
                            </td>
                            <td class="px-3.5 py-3">${eligBadge}</td>
                            <td class="px-3.5 py-3">
                                <div class="flex items-center justify-center gap-1">${decisionPills}</div>
                            </td>
                            <td class="px-3.5 py-3 text-right font-mono">${pnlStr}</td>
                            <td class="px-3.5 py-3 text-center">
                                <button onclick="inspectResearchSignal('${sig.signal_id}')" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-indigo-300 hover:text-white rounded border border-slate-700 text-[10px] font-mono transition">
                                    Evidence
                                </button>
                            </td>
                        </tr>
                    `;
                }).join('');

            } catch (err) {
                console.error("fetchModelBResearchSignals error:", err);
            }
        }

        function setResearchFilterDirection(dir) {
            researchFilterDirection = dir;
            ['ALL', 'LONG', 'SHORT'].forEach(d => {
                const btn = document.getElementById(`resDir-${d}`);
                if (btn) {
                    if (d === dir) {
                        btn.className = 'px-2.5 py-1 rounded bg-indigo-600 text-white font-bold transition';
                    } else {
                        btn.className = 'px-2.5 py-1 rounded text-slate-400 hover:text-white transition';
                    }
                }
            });
            fetchModelBResearchSignals();
        }

        function handleResearchFilterSymbol(event) {
            researchFilterSymbol = (event.target.value || '').trim().toUpperCase();
            fetchModelBResearchSignals();
        }

        function inspectResearchSignal(signalId) {
            const sig = researchCachedSignals.find(s => s.signal_id === signalId);
            if (!sig) return;

            const modal = document.getElementById('researchEvidenceModal');
            if (!modal) return;

            const titleEl = document.getElementById('modalSignalTitle');
            if (titleEl) {
                titleEl.innerText = `${sig.symbol} ${sig.direction} &bull; ${sig.signal_id}`;
            }

            // Snapshot Grid
            const ctx = sig.market_context || {};
            const snapGrid = document.getElementById('modalSnapshotGrid');
            if (snapGrid) {
                const bias4h = ctx.bias_4h_true || ctx.bias_4h || 'N/A';
                const body = ctx.candle_body || 'N/A';
                const imb = ctx.imbalance_25bps !== undefined ? `${(ctx.imbalance_25bps * 100).toFixed(1)}%` : 'N/A';
                const atr = ctx.atr_1h ? `$${Number(ctx.atr_1h).toFixed(4)}` : 'N/A';
                const elig = sig.active_execution_eligible ? 'ELIGIBLE' : (sig.active_ineligible_reason || 'HALTED');

                snapGrid.innerHTML = `
                    <div class="p-2.5 rounded bg-slate-900 border border-slate-800">
                        <div class="text-slate-500 text-[10px]">4H Market Structure</div>
                        <div class="text-xs font-bold text-white mt-0.5">${escapeHtml(bias4h)}</div>
                    </div>
                    <div class="p-2.5 rounded bg-slate-900 border border-slate-800">
                        <div class="text-slate-500 text-[10px]">1H Completed Candle Body</div>
                        <div class="text-xs font-bold text-white mt-0.5">${escapeHtml(body)}</div>
                    </div>
                    <div class="p-2.5 rounded bg-slate-900 border border-slate-800">
                        <div class="text-slate-500 text-[10px]">Order Book Imbalance (25bps)</div>
                        <div class="text-xs font-bold text-white mt-0.5">${escapeHtml(imb)}</div>
                    </div>
                    <div class="p-2.5 rounded bg-slate-900 border border-slate-800">
                        <div class="text-slate-500 text-[10px]">1H ATR Volatility</div>
                        <div class="text-xs font-bold text-white mt-0.5">${escapeHtml(atr)}</div>
                    </div>
                    <div class="p-2.5 rounded bg-slate-900 border border-slate-800 col-span-2">
                        <div class="text-slate-500 text-[10px]">Active Portfolio Eligibility at Entry</div>
                        <div class="text-xs font-bold text-amber-300 mt-0.5">${escapeHtml(elig)}</div>
                    </div>
                    <div class="p-2.5 rounded bg-slate-900 border border-slate-800 col-span-2">
                        <div class="text-slate-500 text-[10px]">Limit Fill Execution Rule</div>
                        <div class="text-xs font-bold text-white mt-0.5">Conservative Penetration (&plusmn;0.025% buffer) &bull; 12 Bars TTL</div>
                    </div>
                `;
            }

            // Variant breakdown table
            const tbody = document.getElementById('modalVariantsTableBody');
            if (tbody) {
                const evals = sig.evaluations || [];
                tbody.innerHTML = evals.map(ev => {
                    const isAcc = ev.decision === 'ACCEPTED';
                    const decBadge = isAcc ? '<span class="text-emerald-400 font-bold">ACCEPTED</span>' : '<span class="text-rose-400 font-bold">REJECTED</span>';
                    const pnl = Number(ev.pnl_net || 0);
                    const pnlColor = pnl > 0 ? 'text-emerald-400' : (pnl < 0 ? 'text-rose-400' : 'text-slate-400');
                    const reason = ev.rejection_reason || (isAcc ? 'Qualified & Sim Order Queued' : '--');

                    return `
                        <tr class="hover:bg-slate-900/40">
                            <td class="px-3 py-2 font-bold text-white">${escapeHtml(ev.variant.replace('VARIANT_', ''))}</td>
                            <td class="px-3 py-2">${decBadge}</td>
                            <td class="px-3 py-2 text-[10px] text-slate-300">${escapeHtml(reason)}</td>
                            <td class="px-3 py-2 font-mono text-[10px] text-amber-300">${escapeHtml(ev.order_status || '--')}</td>
                            <td class="px-3 py-2 font-mono text-[10px] text-slate-300">${escapeHtml(ev.exit_reason || '--')}</td>
                            <td class="px-3 py-2 text-right font-mono ${pnlColor} font-bold">${ev.order_status === 'CLOSED' ? (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) : '--'}</td>
                            <td class="px-3 py-2 text-right font-mono text-[10px] text-slate-400">${ev.order_status === 'CLOSED' ? Number(ev.mae_pct || 0).toFixed(1) + '% / ' + Number(ev.mfe_pct || 0).toFixed(1) + '%' : '--'}</td>
                        </tr>
                    `;
                }).join('');
            }

            modal.classList.remove('hidden');
        }

        function closeResearchEvidenceModal() {
            const modal = document.getElementById('researchEvidenceModal');
            if (modal) modal.classList.add('hidden');
        }

        async function triggerModelBResearchReplay() {
            const spin = document.getElementById('researchReplaySpin');
            const btn = document.getElementById('researchReplayBtn');
            if (spin) spin.classList.add('animate-spin');
            if (btn) btn.disabled = true;

            try {
                const res = await fetch('/api/model_b/research/replay', { method: 'POST' });
                if (res.ok) {
                    await Promise.allSettled([
                        fetchModelBResearchMetrics(),
                        fetchModelBResearchSignals(),
                    ]);
                }
            } catch (err) {
                console.error("triggerModelBResearchReplay error:", err);
            } finally {
                if (spin) spin.classList.remove('animate-spin');
                if (btn) btn.disabled = false;
            }
        }

        // =========================================================================