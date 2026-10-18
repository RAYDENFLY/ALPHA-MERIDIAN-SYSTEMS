        // MODEL B LIQUIDITY RESEARCH ENGINE JAVASCRIPT
        // =========================================================================

        async function loadModelB() {
            await Promise.allSettled([
                fetchModelBStatus(),
                fetchModelBCoverage(),
                fetchModelBSetups(),
                fetchModelBOrders(),
                fetchModelBJournal(),
                fetchModelBOrderBook(),
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

                const discEl = document.getElementById('modelBDiscoveredCount');
                if (discEl) discEl.innerText = `${data.discovered_markets_count || 0} Markets Discovered`;

                const scanTimeEl = document.getElementById('modelBLastScanTime');
                if (scanTimeEl) {
                    scanTimeEl.innerText = data.last_scan_utc ? `Last Scan: ${new Date(data.last_scan_utc).toLocaleTimeString()}` : 'Last Scan: Never';
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

                const netEl = document.getElementById('modelBNetPnl');
                if (netEl) {
                    const netVal = Number(metrics.net_pnl || 0.0);
                    netEl.innerText = `${netVal >= 0 ? '+$' : '-$'}${Math.abs(netVal).toFixed(2)}`;
                    netEl.className = netVal > 0 ? 'text-emerald-400' : (netVal < 0 ? 'text-rose-400' : 'text-white');
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

        // Periodic Model B refresh when Model B tab is active
        setInterval(() => {
            const tab = document.getElementById('tab-model_b');
            if (tab && !tab.classList.contains('hidden')) {
                loadModelB();
            }
        }, 10000);

        // =========================================================================