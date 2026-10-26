// =========================================================================
// LIVE ASTERDEX POSITIONS & ACCOUNT STATUS
// =========================================================================
        async function syncLiveAccount() {
            const btn = document.getElementById('syncLiveAccountBtn');
            const spinIcon = document.getElementById('syncLiveSpinIcon');
            if (btn) btn.disabled = true;
            if (spinIcon) spinIcon.classList.add('animate-spin');
            try {
                const res = await fetch('/api/account/sync', { method: 'POST' });
                if (res.ok) {
                    const accData = await res.json();
                    renderLivePositions(accData);
                    await fetchStatus();
                    showNotice('Live AsterDEX account state refreshed successfully.', 'info');
                } else {
                    showNotice('Failed to refresh live AsterDEX account state.', 'error');
                }
            } catch (e) {
                showNotice('Live sync error: ' + e.message, 'error');
            } finally {
                if (btn) btn.disabled = false;
                if (spinIcon) spinIcon.classList.remove('animate-spin');
            }
        }

        function renderLivePositions(acc) {
            const container = document.getElementById('livePositionsContainer');
            const countBadge = document.getElementById('livePosCountBadge');
            const syncTimeEl = document.getElementById('livePosSyncTime');
            const warningBanner = document.getElementById('livePosWarningBanner');
            const warningText = document.getElementById('livePosWarningText');
            const pulseEl = document.getElementById('livePosPulse');
            const dotEl = document.getElementById('livePosDot');

            if (!container) return;

            // Handle missing or uninitialized account data
            if (!acc || acc.status === 'ACCOUNT_DATA_UNAVAILABLE' || acc.status === 'UNAUTHENTICATED' || acc.status === 'ERROR') {
                if (countBadge) {
                    countBadge.innerText = 'OFFLINE';
                    countBadge.className = 'px-2.5 py-0.5 text-xs font-semibold rounded-full bg-red-950 text-rose-300 border border-red-800 font-mono';
                }
                if (pulseEl) pulseEl.className = 'hidden';
                if (dotEl) dotEl.className = 'relative inline-flex rounded-full h-3 w-3 bg-rose-500';
                if (syncTimeEl) syncTimeEl.innerText = 'Sync: Unavailable';

                if (warningBanner && warningText) {
                    warningText.innerText = `Exchange account data unavailable (${acc && acc.error_message ? acc.error_message : 'No response'}). Paper trading and forward cohort continue unaffected.`;
                    warningBanner.classList.remove('hidden');
                }

                container.innerHTML = `
                    <div class="p-6 text-center rounded-lg bg-slate-900/40 border border-slate-800 text-slate-400 space-y-2">
                        <div class="text-rose-400 font-semibold text-sm">Live Account Data Unavailable</div>
                        <div class="text-xs text-slate-500 max-w-md mx-auto font-mono">
                            ${escapeHtml(acc && acc.error_message ? acc.error_message : 'Unable to query Aster DEX authenticated endpoints.')}
                        </div>
                    </div>
                `;
                return;
            }

            // Staleness notification
            if (acc.status === 'STALE_ACCOUNT_DATA' || acc.is_stale) {
                if (warningBanner && warningText) {
                    warningText.innerText = `Notice: Displaying cached AsterDEX position state (${acc.last_sync_age_seconds !== null && acc.last_sync_age_seconds !== undefined ? acc.last_sync_age_seconds + 's' : ''} old). New live orders cannot be fetched until connectivity restores.`;
                    warningBanner.classList.remove('hidden');
                }
                if (pulseEl) pulseEl.className = 'hidden';
                if (dotEl) dotEl.className = 'relative inline-flex rounded-full h-3 w-3 bg-amber-500';
            } else {
                if (warningBanner) warningBanner.classList.add('hidden');
                if (pulseEl) pulseEl.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75';
                if (dotEl) dotEl.className = 'relative inline-flex rounded-full h-3 w-3 bg-emerald-500';
            }

            // Sync timing display
            const ageStr = (acc.last_sync_age_seconds !== null && acc.last_sync_age_seconds !== undefined)
                ? `${acc.last_sync_age_seconds}s ago`
                : (acc.last_sync_utc ? new Date(acc.last_sync_utc).toLocaleTimeString() : 'just now');
            if (syncTimeEl) {
                syncTimeEl.innerText = `Sync: ${ageStr}`;
            }

            const positions = acc.positions_summary || [];
            if (countBadge) {
                countBadge.innerText = `${positions.length} Open Position${positions.length === 1 ? '' : 's'}`;
                countBadge.className = positions.length > 0
                    ? 'px-2.5 py-0.5 text-xs font-semibold rounded-full bg-emerald-950 text-emerald-300 border border-emerald-800 font-mono'
                    : 'px-2.5 py-0.5 text-xs font-semibold rounded-full bg-slate-800 text-slate-400 border border-slate-700 font-mono';
            }

            // Zero-positions empty state
            if (positions.length === 0) {
                container.innerHTML = `
                    <div class="p-6 text-center rounded-lg bg-slate-900/30 border border-slate-800 text-slate-400 space-y-2">
                        <div class="text-slate-300 font-semibold text-sm">No open AsterDEX positions detected. Account is currently in cash.</div>
                        <div class="text-xs text-slate-500 font-mono">
                            Last checked at ${acc.last_sync_utc ? new Date(acc.last_sync_utc).toUTCString() : 'recent sync'} &bull; Ready to track live positions on AsterDEX perpetuals.
                        </div>
                    </div>
                `;
                return;
            }

            // Render Desktop Table and Mobile Cards
            let desktopRowsHtml = '';
            let mobileCardsHtml = '';

            positions.forEach(p => {
                const isLong = p.side === 'LONG';
                const sideBadgeClass = isLong
                    ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                    : 'bg-rose-950 text-rose-400 border border-rose-800';

                const marginModeBadge = p.margin_mode === 'ISOLATED'
                    ? '<span class="px-1.5 py-0.5 text-[9px] font-mono font-semibold rounded bg-cyan-950 text-cyan-300 border border-cyan-800/60">ISOLATED</span>'
                    : '<span class="px-1.5 py-0.5 text-[9px] font-mono font-semibold rounded bg-indigo-950 text-indigo-300 border border-indigo-800/60">CROSS</span>';

                const baseCoin = p.symbol ? p.symbol.replace(/USDT$/i, '') : '';
                const upnl = Number(p.unrealized_pnl_usdt || 0);
                const isUpnlPositive = upnl >= 0;
                const upnlClass = isUpnlPositive ? 'text-emerald-400' : 'text-rose-400';
                const upnlSign = isUpnlPositive ? '+' : '';

                // Take Profit formatting
                let tpDisplay = '<span class="text-amber-300 font-bold text-xs">TP NOT PLACED</span>';
                if (p.take_profit_price) {
                    const tpState = p.tp_status || (p.tp_order_id ? 'PLACED' : 'NOT_PLACED');
                    const tpId = p.tp_order_id ? ` · ID ${escapeHtml(String(p.tp_order_id))}` : '';
                    const tpQty = Number(p.tp_quantity || 0) > 0 ? ` · Qty ${escapeHtml(String(p.tp_quantity))}` : '';
                    tpDisplay = `<span class="${tpState === 'PLACED' ? 'text-emerald-400' : 'text-amber-300'} font-mono font-medium">TP ${escapeHtml(p.take_profit_label || ('$' + formatPrice(p.take_profit_price)))}</span><span class="block text-[10px] text-slate-500">${escapeHtml(tpState)}${tpId}${tpQty}</span>`;
                }

                // Stop Loss formatting with explicit protection status
                let slDisplay = '<span class="text-rose-400/80 italic text-xs font-medium">SL: Not configured (UNPROTECTED)</span>';
                if (p.stop_loss_price) {
                    const protectedNow = p.protection_status === 'PROTECTED';
                    const protBadge = protectedNow
                        ? '<span class="ml-1 px-1.5 py-0.2 text-[9px] font-bold rounded bg-emerald-950 text-emerald-300 border border-emerald-800">PROTECTED</span>'
                        : `<span class="ml-1 px-1.5 py-0.2 text-[9px] font-bold rounded bg-amber-950 text-amber-300 border border-amber-800">${escapeHtml(p.protection_status || 'UNPROTECTED')}</span>`;
                    const slId = p.sl_order_id ? ` · ID ${escapeHtml(String(p.sl_order_id))}` : '';
                    const slQty = Number(p.sl_quantity || 0) > 0 ? ` · Qty ${escapeHtml(String(p.sl_quantity))}` : '';
                    slDisplay = `<span class="text-rose-400 font-mono font-medium">SL ${escapeHtml(p.stop_loss_label || ('$' + formatPrice(p.stop_loss_price)))}</span>${protBadge}<span class="block text-[10px] text-slate-500">${escapeHtml(p.sl_status || (p.sl_order_id ? 'PLACED' : 'NOT_PLACED'))}${slId}${slQty}</span>`;
                }

                // Liquidation Price formatting
                let liqDisplay = '<span class="text-slate-500 italic text-xs">Unavailable</span>';
                if (p.liquidation_price && Number(p.liquidation_price) > 0) {
                    liqDisplay = `<span class="text-amber-400 font-mono font-semibold">$${formatPrice(p.liquidation_price)}</span>`;
                } else if (p.liquidation_label) {
                    liqDisplay = `<span class="text-slate-500 italic text-xs">${escapeHtml(p.liquidation_label)}</span>`;
                }

                // Allocated Margin formatting
                const allocMarginDisplay = p.allocated_margin_label || `$${Number(p.allocated_margin_usdt || 0).toFixed(2)}`;

                // Hedge mode indicator
                const hedgeBadge = (p.position_side && p.position_side !== 'BOTH')
                    ? `<span class="px-1.5 py-0.5 text-[9px] font-mono font-semibold rounded bg-purple-950 text-purple-300 border border-purple-800/60">${p.position_side}</span>`
                    : '';

                // Desktop Table Row
                desktopRowsHtml += `
                    <tr class="border-b border-slate-800/60 hover:bg-slate-800/30 transition font-mono text-xs">
                        <td class="py-3 px-3">
                            <div class="flex items-center gap-2">
                                <span class="font-bold text-white text-sm font-sans">${escapeHtml(p.symbol)}</span>
                                <span class="px-1.5 py-0.5 text-[10px] font-bold rounded ${sideBadgeClass}">${p.side} ${p.leverage}x</span>
                                ${hedgeBadge}
                                ${marginModeBadge}
                            </div>
                        </td>
                        <td class="py-3 px-3">
                            <div class="text-slate-200 font-medium">${p.position_amt} ${baseCoin}</div>
                            <div class="text-[11px] text-slate-400 font-normal">$${Number(p.notional_usdt || 0).toFixed(2)} USDT</div>
                        </td>
                        <td class="py-3 px-3">
                            <div class="text-slate-200 font-medium">$${formatPrice(p.entry_price)}</div>
                            <div class="text-[11px] text-slate-400 font-normal">Mark: $${formatPrice(p.mark_price)}</div>
                        </td>
                        <td class="py-3 px-3">
                            <div class="text-slate-200">${allocMarginDisplay}</div>
                            <div class="text-[10px] text-slate-500">${p.margin_mode === 'ISOLATED' ? 'Allocated' : 'Cross Initial'}</div>
                        </td>
                        <td class="py-3 px-3">
                            <div>${tpDisplay}</div>
                            <div class="mt-0.5">${slDisplay}</div>
                        </td>
                        <td class="py-3 px-3">
                            <div>${liqDisplay}</div>
                        </td>
                        <td class="py-3 px-3 text-right">
                            <div class="font-bold ${upnlClass}">${upnlSign}$${upnl.toFixed(4)} USDT</div>
                            <div class="text-[11px] ${upnlClass} font-semibold">${upnlSign}${Number(p.roe_pct || 0).toFixed(2)}% <span class="text-[10px] text-slate-400 font-normal">(ROE vs Margin)</span></div>
                            <div class="text-[10px] text-slate-500 font-normal">${upnlSign}${Number(p.roi_pct || 0).toFixed(2)}% (ROI vs Notional)</div>
                        </td>
                        <td class="py-3 px-3 text-right">
                            <button onclick="analyzePositionEntry('${escapeHtml(p.symbol)}')" class="px-2.5 py-1 text-xs font-semibold rounded bg-indigo-600/90 hover:bg-indigo-500 text-white transition flex items-center gap-1 ml-auto shadow-sm">
                                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>
                                <span>Analyze Entry</span>
                            </button>
                        </td>
                    </tr>
                `;

                // Mobile Card View
                mobileCardsHtml += `
                    <div class="card-inner p-4 rounded-xl border border-slate-800 space-y-3">
                        <div class="flex items-center justify-between">
                            <div class="flex items-center gap-2">
                                <span class="font-bold text-white text-base font-sans">${escapeHtml(p.symbol)}</span>
                                <span class="px-1.5 py-0.5 text-[10px] font-bold rounded ${sideBadgeClass}">${p.side} ${p.leverage}x</span>
                                ${hedgeBadge}
                                ${marginModeBadge}
                            </div>
                            <div class="text-right">
                                <div class="font-bold text-sm ${upnlClass} font-mono">${upnlSign}$${upnl.toFixed(4)}</div>
                                <div class="text-[11px] ${upnlClass} font-mono font-semibold">${upnlSign}${Number(p.roe_pct || 0).toFixed(2)}% ROE</div>
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-2 text-xs font-mono pt-2 border-t border-slate-800/80">
                            <div>
                                <span class="text-slate-500 block text-[10px] uppercase">Size / Notional</span>
                                <span class="text-slate-200 font-medium">${p.position_amt} ${baseCoin}</span>
                                <span class="text-slate-400 block text-[11px]">$${Number(p.notional_usdt || 0).toFixed(2)} USDT</span>
                            </div>
                            <div>
                                <span class="text-slate-500 block text-[10px] uppercase">Entry / Mark</span>
                                <span class="text-slate-200 font-medium">$${formatPrice(p.entry_price)}</span>
                                <span class="text-slate-400 block text-[11px]">Mark: $${formatPrice(p.mark_price)}</span>
                            </div>
                            <div>
                                <span class="text-slate-500 block text-[10px] uppercase">Allocated Margin</span>
                                <span class="text-slate-200">${allocMarginDisplay}</span>
                            </div>
                            <div>
                                <span class="text-slate-500 block text-[10px] uppercase">Liquidation Price</span>
                                <span>${liqDisplay}</span>
                            </div>
                            <div>
                                <span class="text-slate-500 block text-[10px] uppercase">Take Profit (TP)</span>
                                <span>${tpDisplay}</span>
                            </div>
                            <div>
                                <span class="text-slate-500 block text-[10px] uppercase">Stop Loss (SL)</span>
                                <span>${slDisplay}</span>
                            </div>
                        </div>

                        <div class="text-[10px] text-slate-500 font-mono pt-1 border-t border-slate-800/60 flex items-center justify-between">
                            <span>Denominator: ${escapeHtml(p.denominator_label || 'Allocated Margin')}</span>
                            <span>ROI: ${upnlSign}${Number(p.roi_pct || 0).toFixed(2)}% (on Notional)</span>
                        </div>

                        <div class="pt-2 border-t border-slate-800/80 flex items-center justify-between">
                            <span class="text-[11px] text-slate-400 font-medium">Position Intelligence</span>
                            <button onclick="analyzePositionEntry('${escapeHtml(p.symbol)}')" class="px-2.5 py-1 text-xs font-semibold rounded bg-indigo-600 hover:bg-indigo-500 text-white transition flex items-center gap-1 shadow-sm">
                                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>
                                <span>Analyze Entry</span>
                            </button>
                        </div>
                    </div>
                `;
            });

            container.innerHTML = `
                <!-- Desktop Table View -->
                <div class="hidden md:block overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="border-b border-slate-800 text-slate-400 text-[11px] uppercase tracking-wider font-semibold">
                                <th class="py-2 px-3">Symbol & Mode</th>
                                <th class="py-2 px-3">Size & Notional</th>
                                <th class="py-2 px-3">Entry / Mark</th>
                                <th class="py-2 px-3">Allocated Margin</th>
                                <th class="py-2 px-3">TP / SL Targets</th>
                                <th class="py-2 px-3">Liquidation</th>
                                <th class="py-2 px-3 text-right">Unrealized PnL</th>
                                <th class="py-2 px-3 text-right">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${desktopRowsHtml}
                        </tbody>
                    </table>
                </div>

                <!-- Mobile Cards View -->
                <div class="block md:hidden space-y-3">
                    ${mobileCardsHtml}
                </div>
            `;
        }

        // Account Status Polling (Every 15s)
        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();
                
                const modeEl = document.getElementById('modeBadge');
                if (modeEl) {
                    modeEl.innerText = data.mode;
                    if (data.mode === 'LIVE_AUTO') {
                        modeEl.className = 'px-2 py-0.5 text-[10px] font-bold uppercase rounded bg-purple-950 text-purple-300 border border-purple-700 tracking-wide';
                    } else if (data.mode === 'LIVE_MANUAL') {
                        modeEl.className = 'px-2 py-0.5 text-[10px] font-bold uppercase rounded bg-amber-950 text-amber-300 border border-amber-700 tracking-wide';
                    } else {
                        modeEl.className = 'px-2 py-0.5 text-[10px] font-bold uppercase rounded badge-blue tracking-wide';
                    }
                }

                // Account Equity & AsterDEX Live Balance Card
                const acc = data.account;
                const equityEl = document.getElementById('equityDisplay');
                const badgeEl = document.getElementById('accountConnBadge');
                const availEl = document.getElementById('availBalDisplay');
                const usedEl = document.getElementById('usedMarginDisplay');
                const upnlEl = document.getElementById('unrealizedPnlDisplay');
                const syncEl = document.getElementById('accountSyncTimeDisplay');

                if (acc && acc.status === 'CONNECTED' && acc.total_equity_usdt !== null && acc.total_equity_usdt !== undefined) {
                    equityEl.innerText = `$${Number(acc.total_equity_usdt).toFixed(2)} USDT`;
                    badgeEl.innerText = '● LIVE CONNECTED';
                    badgeEl.className = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-700';
                    if (availEl) availEl.innerText = `$${Number(acc.available_balance_usdt || 0).toFixed(2)}`;
                    if (usedEl) usedEl.innerText = `$${Number(acc.used_margin_usdt || 0).toFixed(2)}`;
                    if (upnlEl) {
                        const upnl = Number(acc.unrealized_pnl_usdt || 0);
                        upnlEl.innerText = `${upnl >= 0 ? '+' : ''}$${upnl.toFixed(2)}`;
                        upnlEl.className = upnl >= 0 ? 'text-emerald-400' : 'text-red-400';
                    }
                    if (syncEl) {
                        const ageStr = acc.last_sync_age_seconds !== null && acc.last_sync_age_seconds !== undefined ? `${acc.last_sync_age_seconds}s ago` : 'just now';
                        syncEl.innerText = `Synced: ${ageStr}`;
                    }
                } else if (acc && acc.status === 'STALE_ACCOUNT_DATA') {
                    if (acc.total_equity_usdt !== null) {
                        equityEl.innerText = `$${Number(acc.total_equity_usdt).toFixed(2)} USDT`;
                    }
                    badgeEl.innerText = '● STALE DATA';
                    badgeEl.className = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-400 border border-amber-700';
                    if (syncEl) syncEl.innerText = `Stale cache (${acc.last_sync_age_seconds}s ago)`;
                } else {
                    equityEl.innerText = 'UNAVAILABLE';
                    badgeEl.innerText = acc ? `● ${acc.status}` : '● UNAVAILABLE';
                    badgeEl.className = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-950 text-rose-400 border border-red-700';
                    if (availEl) availEl.innerText = '--';
                    if (usedEl) usedEl.innerText = '--';
                    if (upnlEl) upnlEl.innerText = '--';
                    if (syncEl) syncEl.innerText = 'Exchange sync required';
                }

                // Header Live Equity Display
                const headerLiveEquityEl = document.getElementById('headerLiveEquityDisplay');
                if (headerLiveEquityEl) {
                    if (acc && acc.total_equity_usdt !== null && acc.total_equity_usdt !== undefined) {
                        headerLiveEquityEl.innerText = `$${Number(acc.total_equity_usdt).toFixed(2)} USDT`;
                    } else {
                        headerLiveEquityEl.innerText = '--';
                    }
                }

                // Update Live Exchange Position Count in Exposure Card
                const livePosCardEl = document.getElementById('livePosCardCount');
                if (livePosCardEl) {
                    const lCount = (acc && acc.open_positions_count !== undefined)
                        ? acc.open_positions_count
                        : (acc && acc.positions_summary ? acc.positions_summary.length : '--');
                    livePosCardEl.innerText = `Live Exchange Pos: ${lCount} Active`;
                }

                // Render Live AsterDEX Positions Panel
                renderLivePositions(acc);

                // Target Isolated Margin & Tier
                if (data.capital && data.capital.active_tier) {
                    const at = data.capital.active_tier;
                    const targetMarginEl = document.getElementById('targetMarginDisplay');
                    if (targetMarginEl) targetMarginEl.innerText = `$${at.target_margin_min.toFixed(2)}–$${at.target_margin_max.toFixed(2)}`;
                    const hardRiskEl = document.getElementById('hardRiskDisplay');
                    if (hardRiskEl) hardRiskEl.innerText = `${at.tier_name} (${data.capital.margin_mode} \u2022 ${data.capital.max_leverage}x)`;
                } else {
                    const minM = data.risk_limits.target_isolated_margin_usdt !== undefined ? data.risk_limits.target_isolated_margin_usdt.toFixed(2) : '6.00';
                    const maxM = data.risk_limits.target_isolated_margin_max_usdt !== undefined ? data.risk_limits.target_isolated_margin_max_usdt.toFixed(2) : '6.50';
                    const targetMarginEl = document.getElementById('targetMarginDisplay');
                    if (targetMarginEl) targetMarginEl.innerText = `$${minM}–$${maxM}`;
                    const hardRiskEl = document.getElementById('hardRiskDisplay');
                    if (hardRiskEl && data.risk_limits.hard_risk_limit_usdt !== undefined) {
                        hardRiskEl.innerText = `Hard Risk Limit: $${data.risk_limits.hard_risk_limit_usdt.toFixed(2)}`;
                    }
                }

                const lossUsed = Number(data.risk_limits.daily_loss_used_usdt);
                const lossState = data.risk_limits.daily_loss_status || 'UNVERIFIED';
                document.getElementById('dailyLossDisplay').innerText = Number.isFinite(lossUsed)
                    ? `$${lossUsed.toFixed(2)} / $${Number(data.risk_limits.daily_loss_limit_usdt).toFixed(2)}${lossState === 'VERIFIED' ? '' : ' (UNVERIFIED)'}`
                    : `UNVERIFIED / $${Number(data.risk_limits.daily_loss_limit_usdt).toFixed(2)}`;
                document.getElementById('weeklyLossDisplay').innerText = `$${data.risk_limits.weekly_loss_used_usdt.toFixed(2)} / $${data.risk_limits.weekly_loss_limit_usdt.toFixed(2)}`;
                document.getElementById('capsDisplay').innerText = `${data.risk_limits.open_positions} / ${data.risk_limits.max_open_positions} Paper Pos`;
                const entriesUsed = data.risk_limits.daily_entries_used;
                const entriesCap = data.risk_limits.max_daily_entries || 3;
                document.getElementById('entriesDisplay').innerText = `Entries Today: ${entriesUsed ?? 'UNVERIFIED'} / ${entriesCap}`;

                // Scanner State & Metadata
                if (data.scanner) {
                    const st = data.scanner.state || 'IDLE';

                    const badge = document.getElementById('scannerStateBadge');
                    badge.innerText = st === 'STALE' ? 'STALE (INACTIVE)' : st;
                    badge.className = `px-2 py-0.5 rounded text-[11px] font-bold ${
                        st === 'RUNNING' ? 'badge-blue animate-pulse' :
                        st === 'STOPPED' ? 'bg-amber-950 text-amber-300 border border-amber-700' :
                        st === 'STALE' ? 'bg-red-950 text-red-300 border border-red-700' :
                        st === 'ERROR' ? 'badge-red' :
                        'badge-green'
                    }`;

                    if (data.scanner.last_scan_utc) {
                        const d = new Date(data.scanner.last_scan_utc);
                        const ageMin = data.scanner.last_scan_age_seconds !== undefined && data.scanner.last_scan_age_seconds !== null
                            ? ` (${Math.floor(data.scanner.last_scan_age_seconds / 60)}m ago)`
                            : '';
                        document.getElementById('lastScanTimeDisplay').innerText = `Last Scan: ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}${ageMin}`;
                    }

                    if (data.scanner.candle_age_seconds !== null && data.scanner.candle_age_seconds !== undefined) {
                        const m = Math.floor(data.scanner.candle_age_seconds / 60);
                        const freshEl = document.getElementById('dataFreshnessHeader');
                        if (freshEl) {
                            freshEl.innerText = `Candle Age: ${m}m ago (${data.scanner.is_stale ? 'STALE' : 'Fresh'})`;
                        }
                    }
                }

                // Forward Paper Validation Cohort & Execution Pipeline Counters
                if (data.forward_validation) {
                    const fv = data.forward_validation;
                    const fvBadge = document.getElementById('forwardValidationBadge');
                    const fvNote = document.getElementById('forwardValidationNote');
                    if (fvBadge) {
                        const fullStatus = fv.validation_status || 'UNPROVEN_NO_COMPLETED_FORWARD_TRADES';
                        const compactStatus = {
                            'UNPROVEN_NO_COMPLETED_FORWARD_TRADES': 'UNPROVEN',
                            'PRELIMINARY_POSITIVE_NOT_VALIDATED': 'PRELIMINARY',
                            'NEGATIVE_NET_EXPECTANCY': 'NEGATIVE',
                            'INSUFFICIENT_SAMPLE': 'INSUFFICIENT',
                            'UNAVAILABLE': 'UNAVAILABLE'
                        }[fullStatus] || fullStatus;
                        fvBadge.innerText = compactStatus;
                        fvBadge.title = fullStatus;
                        fvBadge.setAttribute('aria-label', `Forward validation status: ${fullStatus}`);
                        fvBadge.dataset.fullStatus = fullStatus;
                        fvBadge.className = `px-1.5 py-0.5 text-[9px] leading-tight font-bold rounded font-mono whitespace-nowrap overflow-hidden text-ellipsis max-w-[100px] ${
                            fv.validation_status === 'PRELIMINARY_POSITIVE_NOT_VALIDATED' ? 'badge-blue' :
                            fv.validation_status === 'NEGATIVE_NET_EXPECTANCY' ? 'badge-red' :
                            'badge-yellow'
                        }`;
                    }
                    if (fvNote && fv.validation_note) {
                        fvNote.innerText = fv.validation_note;
                    }
                    const ordCreatedEl = document.getElementById('paperOrdersCreatedCount');
                    if (ordCreatedEl) ordCreatedEl.innerText = fv.orders_created !== undefined ? fv.orders_created : 0;
                    const ordPendingEl = document.getElementById('paperOrdersPendingCount');
                    if (ordPendingEl) ordPendingEl.innerText = fv.orders_pending !== undefined ? fv.orders_pending : 0;
                    const ordFilledEl = document.getElementById('paperOrdersFilledCount');
                    if (ordFilledEl) ordFilledEl.innerText = fv.orders_filled !== undefined ? fv.orders_filled : 0;
                    const trdClosedEl = document.getElementById('paperTradesCompletedCount');
                    if (trdClosedEl) trdClosedEl.innerText = fv.closed_trades_count !== undefined ? fv.closed_trades_count : 0;
                    const expEl = document.getElementById('forwardExpectancyDisplay');
                    if (expEl && fv.net_expectancy_per_trade !== undefined) {
                        const expVal = Number(fv.net_expectancy_per_trade);
                        expEl.innerText = `${expVal >= 0 ? '+$' : '-$'}${Math.abs(expVal).toFixed(4)}/trade`;
                        expEl.className = expVal > 0 ? 'text-emerald-400' : (expVal < 0 ? 'text-red-400' : 'text-slate-300');
                    }
                }
            } catch (err) {
                console.error("Status polling error:", err);
            }
        }

        // Setups Polling (Every 30s)
