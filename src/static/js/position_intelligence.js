        // ASTERDEX POSITION INTELLIGENCE & LIQUIDITY JAVASCRIPT
        // =========================================================================

        // -------------------------------------------------------------------------
        // Single Position Intelligence & Entry Audit Modal
        // -------------------------------------------------------------------------
        async function analyzePositionEntry(symbol) {
            currentAnalyzedSymbol = symbol;
            const modal = document.getElementById('positionAnalysisModal');
            const content = document.getElementById('positionAnalysisModalContent');
            const titleEl = document.getElementById('modalPosTitle');
            const sideEl = document.getElementById('modalPosSideBadge');
            const marginEl = document.getElementById('modalPosMarginBadge');

            if (!modal || !content) return;
            modal.classList.remove('hidden');
            content.innerHTML = `
                <div class="py-16 text-center space-y-3">
                    <div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500"></div>
                    <div class="text-sm font-mono text-slate-300">Auditing 4H/1H/15M structure &amp; risk for ${escapeHtml(symbol)}...</div>
                    <div class="text-xs text-slate-500">Checking BOS/CHoCH, unmitigated order blocks, fee-adjusted SL, and liquidation distance.</div>
                </div>
            `;

            try {
                const res = await fetch('/api/intelligence/analyze_position', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol: symbol }),
                });

                if (!res.ok) {
                    throw new Error(`API responded with status ${res.status}`);
                }

                const data = await res.json();
                renderPositionAnalysis(data);
            } catch (err) {
                content.innerHTML = `
                    <div class="p-6 text-center rounded-xl bg-red-950/40 border border-red-800 text-rose-300 space-y-2">
                        <div class="font-bold text-sm">Failed to generate position analysis</div>
                        <div class="text-xs font-mono text-slate-400">${escapeHtml(err.message)}</div>
                    </div>
                `;
            }
        }

        function refreshActivePositionAnalysis() {
            if (currentAnalyzedSymbol) {
                analyzePositionEntry(currentAnalyzedSymbol);
            }
        }

        function closePositionAnalysisModal() {
            const modal = document.getElementById('positionAnalysisModal');
            if (modal) modal.classList.add('hidden');
        }

        function renderPositionAnalysis(data) {
            const content = document.getElementById('positionAnalysisModalContent');
            const titleEl = document.getElementById('modalPosTitle');
            const sideEl = document.getElementById('modalPosSideBadge');
            const marginEl = document.getElementById('modalPosMarginBadge');
            if (!content) return;

            const safeFP = (p) => (typeof formatPrice === 'function' ? formatPrice(p) : (p != null ? Number(p).toFixed(4) : '--'));
            const safeEH = (str) => (typeof escapeHtml === 'function' ? escapeHtml(str) : (str != null ? String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;') : ''));

            const isLong = data.side === 'LONG';
            const sideBadgeClass = isLong ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-rose-950 text-rose-400 border border-rose-800';
            
            if (titleEl) titleEl.innerText = `${data.symbol} Position Entry Audit`;
            if (sideEl) {
                sideEl.className = `px-2 py-0.5 rounded text-xs font-bold font-mono border ${sideBadgeClass}`;
                sideEl.innerText = `${data.side}`;
            }
            if (marginEl) {
                marginEl.innerText = (data.risk && data.risk.margin_mode) ? data.risk.margin_mode : 'ISOLATED';
            }

            // 1. Thesis Status Badge
            let thesisBadgeClass = 'bg-slate-800 text-slate-300 border-slate-700';
            if (data.thesis_status === 'THESIS_INTACT' || data.thesis_status === 'VALID') {
                thesisBadgeClass = 'bg-emerald-950 text-emerald-300 border-emerald-700';
            } else if (data.thesis_status === 'THESIS_WEAKENING' || data.thesis_status === 'WEAKENING') {
                thesisBadgeClass = 'bg-amber-950 text-amber-300 border-amber-700';
            } else if (data.thesis_status === 'THESIS_INVALIDATED' || data.thesis_status === 'INVALIDATED') {
                thesisBadgeClass = 'bg-rose-950 text-rose-300 border-rose-700 animate-pulse';
            }

            // 2. Position Risk Status Badge
            const riskStatus = (data.risk && data.risk.risk_status) ? data.risk.risk_status : 'PROTECTION_UNVERIFIED';
            let riskBadgeClass = 'bg-slate-800 text-slate-300 border-slate-700';
            if (riskStatus === 'RISK_WITHIN_LIMIT') riskBadgeClass = 'bg-emerald-950 text-emerald-300 border-emerald-700';
            else if (riskStatus === 'RISK_WARNING') riskBadgeClass = 'bg-amber-950 text-amber-300 border-amber-700';
            else if (riskStatus === 'RISK_BREACHED') riskBadgeClass = 'bg-rose-950 text-rose-300 border-rose-700 animate-pulse';
            else if (riskStatus === 'PROTECTION_UNVERIFIED') riskBadgeClass = 'bg-yellow-950 text-yellow-300 border-yellow-700';

            // 3. Protection Status Badge
            const protStatus = (data.risk && data.risk.protection_status) ? data.risk.protection_status : 'PROTECTION_UNVERIFIED';
            let protBadgeClass = 'bg-slate-800 text-slate-300 border-slate-700';
            if (protStatus === 'FULLY_PROTECTED') protBadgeClass = 'bg-emerald-950 text-emerald-300 border-emerald-800';
            else if (protStatus === 'PROTECTED_NO_TP') protBadgeClass = 'bg-cyan-950 text-cyan-300 border-cyan-800';
            else if (protStatus === 'PARTIALLY_PROTECTED') protBadgeClass = 'bg-amber-950 text-amber-300 border-amber-800';
            else if (protStatus === 'PROTECTION_UNVERIFIED') protBadgeClass = 'bg-yellow-950 text-yellow-300 border-yellow-800';
            else if (protStatus === 'UNPROTECTED_NO_SL') protBadgeClass = 'bg-rose-950 text-rose-300 border-rose-800 animate-pulse';

            // 4. Action Badge
            let actionBadgeClass = 'bg-indigo-950 text-indigo-300 border-indigo-700';
            if (data.action === 'HOLD') actionBadgeClass = 'bg-emerald-950 text-emerald-300 border-emerald-700';
            else if (data.action === 'REDUCE') actionBadgeClass = 'bg-amber-950 text-amber-300 border-amber-700';
            else if (data.action === 'EXIT' || data.action === 'CLOSE') actionBadgeClass = 'bg-rose-950 text-rose-300 border-rose-700 animate-pulse';
            else if (data.action === 'REVIEW' || data.action === 'ADJUST') actionBadgeClass = 'bg-cyan-950 text-cyan-300 border-cyan-700';

            // Liquidation Threat Badge
            const liqThreat = (data.risk && data.risk.liquidation_threat) ? data.risk.liquidation_threat : 'SAFE';
            let liqBadgeClass = 'bg-slate-800 text-slate-300 border-slate-700';
            if (liqThreat === 'SAFE') liqBadgeClass = 'bg-emerald-950 text-emerald-300 border-emerald-800';
            else if (liqThreat === 'MODERATE') liqBadgeClass = 'bg-yellow-950 text-yellow-300 border-yellow-800';
            else if (liqThreat === 'ELEVATED') liqBadgeClass = 'bg-amber-950 text-amber-300 border-amber-800';
            else if (liqThreat === 'CRITICAL') liqBadgeClass = 'bg-rose-950 text-rose-300 border-rose-800 animate-pulse';

            const upnl = Number(data.unrealized_pnl_usdt || 0);
            const upnlClass = upnl >= 0 ? 'text-emerald-400' : 'text-rose-400';
            const upnlSign = upnl >= 0 ? '+' : '';

            // Format Structural Evidence List
            const evidenceHtml = (data.structural_evidence || []).map(e => `
                <li class="flex items-start gap-2 text-xs text-slate-300">
                    <span class="text-indigo-400 mt-0.5">&bull;</span>
                    <span>${safeEH(e)}</span>
                </li>
            `).join('') || '<li class="text-xs text-slate-500 italic">No structural anomalies detected.</li>';

            // Format Invalidation Conditions
            const invalidationsHtml = (data.invalidation_conditions || []).map(inv => `
                <li class="flex items-start gap-2 text-xs text-rose-200 bg-rose-950/30 p-2 rounded-lg border border-rose-900/40">
                    <span class="text-rose-400 font-bold">&#9888;</span>
                    <span>${safeEH(inv)}</span>
                </li>
            `).join('') || '<div class="text-xs text-slate-500">None specified.</div>';

            // Helper to render Timeframe Structure card
            const renderTfCard = (tfLabel, tf) => {
                if (!tf) return `<div class="p-3 rounded-lg bg-slate-900/40 border border-slate-800 text-slate-500 text-xs">${tfLabel}: Data unavailable</div>`;
                const trendClass = tf.trend === 'BULLISH' ? 'text-emerald-400 bg-emerald-950/60 border border-emerald-800/60' : (tf.trend === 'BEARISH' ? 'text-rose-400 bg-rose-950/60 border border-rose-800/60' : 'text-slate-400 bg-slate-800 border border-slate-700');
                const alignClass = tf.position_alignment === 'SUPPORTS' ? 'text-emerald-400' : (tf.position_alignment === 'CONFLICTS' ? 'text-rose-400 font-bold' : 'text-slate-400');
                const isTfStale = tf.data_freshness && tf.data_freshness.is_stale;
                const freshBadge = isTfStale ? '<span class="px-1.5 py-0.2 rounded text-[9px] bg-amber-950 text-amber-300 border border-amber-800">STALE</span>' : '<span class="px-1.5 py-0.2 rounded text-[9px] bg-emerald-950 text-emerald-300 border border-emerald-800">FRESH</span>';

                return `
                    <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800/90 space-y-2">
                        <div class="flex items-center justify-between border-b border-slate-800/60 pb-1.5">
                            <div class="flex items-center gap-1.5">
                                <span class="font-bold text-indigo-400 text-xs">${tfLabel}</span>
                                ${freshBadge}
                            </div>
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold ${trendClass}">${tf.trend || 'NEUTRAL'}</span>
                        </div>
                        <div class="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                            <div><span class="text-slate-500">EMA Stack:</span> <span class="text-slate-200 font-semibold">${tf.ema_alignment || '--'}</span></div>
                            <div><span class="text-slate-500">Alignment:</span> <span class="${alignClass}">${tf.position_alignment || 'NEUTRAL'}</span></div>
                            <div><span class="text-slate-500">EMA20:</span> <span class="text-slate-300">${tf.ema20 ? '$' + safeFP(tf.ema20) : '--'}</span></div>
                            <div><span class="text-slate-500">EMA50:</span> <span class="text-slate-300">${tf.ema50 ? '$' + safeFP(tf.ema50) : '--'}</span></div>
                            <div><span class="text-slate-500">RSI(14):</span> <span class="text-indigo-300 font-bold">${tf.rsi_14 != null ? tf.rsi_14.toFixed(1) : '--'}</span></div>
                            <div class="truncate" title="${safeEH(tf.momentum_condition || '')}"><span class="text-slate-500">Mom:</span> <span class="text-slate-300">${tf.momentum_condition ? tf.momentum_condition.replace('MOMENTUM_', '') : '--'}</span></div>
                            <div><span class="text-slate-500">Struct Supp:</span> <span class="text-emerald-400">${tf.structural_support ? '$' + safeFP(tf.structural_support) : (tf.last_swing_low ? '$' + safeFP(tf.last_swing_low) : '--')}</span></div>
                            <div><span class="text-slate-500">Struct Res:</span> <span class="text-rose-400">${tf.structural_resistance ? '$' + safeFP(tf.structural_resistance) : (tf.last_swing_high ? '$' + safeFP(tf.last_swing_high) : '--')}</span></div>
                        </div>
                        <div class="text-[10px] text-slate-500 pt-1 border-t border-slate-800/50 flex justify-between">
                            <span>BOS: ${tf.bos_count || 0} | CHoCH: ${tf.choch_count || 0}</span>
                            <span>SwH: ${tf.last_swing_high ? '$' + safeFP(tf.last_swing_high) : '--'} / SwL: ${tf.last_swing_low ? '$' + safeFP(tf.last_swing_low) : '--'}</span>
                        </div>
                    </div>
                `;
            };

            // Format Structured Triggers Table
            let triggersTableHtml = '';
            const structuredTriggers = data.structured_triggers || [];
            if (structuredTriggers.length > 0) {
                triggersTableHtml = `
                    <div class="overflow-x-auto rounded-lg border border-slate-800/80">
                        <table class="min-w-full divide-y divide-slate-800 text-left text-xs font-mono">
                            <thead class="bg-slate-900/80 text-[10px] text-slate-400 uppercase tracking-wider">
                                <tr>
                                    <th class="px-2.5 py-1.5">Type</th>
                                    <th class="px-2.5 py-1.5">Price</th>
                                    <th class="px-2.5 py-1.5">Distance</th>
                                    <th class="px-2.5 py-1.5">TF</th>
                                    <th class="px-2.5 py-1.5">Confirmation</th>
                                    <th class="px-2.5 py-1.5">Action Implication</th>
                                    <th class="px-2.5 py-1.5">Status</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-slate-800/60 bg-slate-950/40 text-[11px]">
                                ${structuredTriggers.map(tr => {
                                    const typeClass = tr.trigger_type === 'TAKE_PROFIT' ? 'text-emerald-400 bg-emerald-950/50 border border-emerald-800' : (tr.trigger_type === 'STOP_LOSS' ? 'text-rose-400 bg-rose-950/50 border border-rose-800' : 'text-amber-300 bg-amber-950/50 border border-amber-800');
                                    const distSign = (tr.distance_pct || 0) >= 0 ? '+' : '';
                                    return `
                                        <tr class="hover:bg-slate-800/20">
                                            <td class="px-2.5 py-2 font-bold whitespace-nowrap"><span class="px-1.5 py-0.5 rounded text-[10px] ${typeClass}">${tr.trigger_type}</span></td>
                                            <td class="px-2.5 py-2 text-white font-bold whitespace-nowrap">$${safeFP(tr.price)}</td>
                                            <td class="px-2.5 py-2 ${tr.distance_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'} whitespace-nowrap">${distSign}${Number(tr.distance_pct || 0).toFixed(2)}%</td>
                                            <td class="px-2.5 py-2 text-indigo-300 whitespace-nowrap">${tr.timeframe}</td>
                                            <td class="px-2.5 py-2 text-slate-400 whitespace-nowrap">${tr.required_confirmation}</td>
                                            <td class="px-2.5 py-2 text-slate-200 font-semibold">${tr.action_implication}</td>
                                            <td class="px-2.5 py-2 whitespace-nowrap">
                                                <span class="px-1.5 py-0.5 rounded text-[10px] ${tr.is_active ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : 'bg-slate-800 text-slate-400'}">${tr.is_active ? 'ACTIVE' : 'PENDING'}</span>
                                            </td>
                                        </tr>
                                    `;
                                }).join('')}
                            </tbody>
                        </table>
                    </div>
                `;
            } else if ((data.conditional_triggers || []).length > 0) {
                triggersTableHtml = `
                    <ul class="space-y-1.5">
                        ${data.conditional_triggers.map(t => `
                            <li class="flex items-start gap-2 text-xs text-slate-200 bg-slate-900/60 p-2 rounded-lg border border-slate-800">
                                <span class="text-cyan-400 font-bold font-mono">&#10148;</span>
                                <span>${safeEH(t)}</span>
                            </li>
                        `).join('')}
                    </ul>
                `;
            } else {
                triggersTableHtml = '<div class="text-xs text-slate-500">Maintain standard parameters.</div>';
            }

            // Market Regime summary section
            const regime = data.market_regime || {};
            const regClass = regime.regime && regime.regime.includes('BULL') ? 'text-emerald-400 bg-emerald-950/60 border border-emerald-800' : (regime.regime && regime.regime.includes('BEAR') ? 'text-rose-400 bg-rose-950/60 border border-rose-800' : 'text-slate-300 bg-slate-800 border border-slate-700');
            const corrClass = regime.position_correlation === 'ALIGNED_WITH_MACRO' ? 'text-emerald-400 bg-emerald-950/60 border border-emerald-800' : (regime.position_correlation === 'COUNTER_TREND_TO_BTC' ? 'text-rose-400 bg-rose-950/60 border border-rose-800' : 'text-slate-300 bg-slate-800 border border-slate-700');

            // Freshness summary
            const freshness = data.freshness || {};
            const isFresh = freshness.overall_freshness === 'FRESH';
            const freshClass = isFresh ? 'text-emerald-400 bg-emerald-950/60 border border-emerald-800' : 'text-amber-400 bg-amber-950/60 border border-amber-800';

            content.innerHTML = `
                <!-- 1. Multi-Timeframe Synthesis & Alignment Banner -->
                <div class="p-3.5 rounded-xl bg-gradient-to-r from-slate-900 via-indigo-950/40 to-slate-900 border border-indigo-900/60 space-y-2">
                    <div class="flex flex-wrap items-center justify-between gap-2">
                        <div class="flex items-center gap-2">
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-indigo-950 text-indigo-300 border border-indigo-700 uppercase tracking-wide">MTF Synthesis</span>
                            <span class="text-xs font-bold text-white font-mono">${safeEH(data.multi_timeframe_synthesis || 'MIXED_TIMEFRAME_CONSOLIDATION')}</span>
                        </div>
                        <div class="flex items-center gap-2 font-mono text-[11px]">
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${thesisBadgeClass}" title="Structural Thesis Status">THESIS: ${data.thesis_status}</span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${riskBadgeClass}" title="Account Risk Limit Status">RISK: ${riskStatus}</span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${protBadgeClass}" title="Exchange Order Protection Status">PROT: ${protStatus}</span>
                        </div>
                    </div>
                    <p class="text-xs text-slate-300 leading-relaxed font-sans">${safeEH(data.synthesis_narrative || data.thesis_summary || '')}</p>
                </div>

                <!-- 2. Market Regime & BTC/ETH Context Banner -->
                <div class="p-3 rounded-xl bg-slate-900/50 border border-slate-800 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 text-xs font-mono">
                    <div class="space-y-1">
                        <div class="flex items-center gap-2">
                            <span class="text-slate-400 uppercase text-[10px] font-bold">Macro Regime:</span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold ${regClass}">${regime.regime || 'NEUTRAL'}</span>
                            <span class="text-slate-400 uppercase text-[10px] font-bold ml-2">Correlation:</span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold ${corrClass}">${regime.position_correlation || 'NEUTRAL'}</span>
                        </div>
                        <div class="text-slate-400 text-[11px] font-sans">${safeEH(regime.summary || 'Market regime data synchronized.')}</div>
                    </div>
                    <div class="flex items-center gap-4 text-[11px] text-slate-300 bg-slate-950/60 px-3 py-1.5 rounded-lg border border-slate-800/80 whitespace-nowrap">
                        <div>BTC: <span class="text-white font-bold">${regime.btc_trend_1h || '--'}</span> <span class="text-slate-500">(4H ${regime.btc_trend_4h || '--'})</span> <span class="${(regime.btc_change_24h_pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${(regime.btc_change_24h_pct || 0) >= 0 ? '+' : ''}${Number(regime.btc_change_24h_pct || 0).toFixed(2)}%</span></div>
                        <div class="border-l border-slate-800 pl-4">ETH: <span class="text-white font-bold">${regime.eth_trend_1h || '--'}</span> <span class="text-slate-500">(4H ${regime.eth_trend_4h || '--'})</span> <span class="${(regime.eth_change_24h_pct || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${(regime.eth_change_24h_pct || 0) >= 0 ? '+' : ''}${Number(regime.eth_change_24h_pct || 0).toFixed(2)}%</span></div>
                    </div>
                </div>

                <!-- 3. Top Summary Metrics Cards -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
                    <div class="card-inner p-3 rounded-xl border border-slate-800">
                        <span class="text-slate-500 block text-[10px] uppercase">Entry &amp; Mark</span>
                        <div class="text-white font-bold text-sm">$${safeFP(data.entry_price)}</div>
                        <div class="text-slate-400 text-[11px]">Mark: $${safeFP(data.mark_price)}</div>
                    </div>
                    <div class="card-inner p-3 rounded-xl border border-slate-800">
                        <span class="text-slate-500 block text-[10px] uppercase">Position Size &amp; Notional</span>
                        <div class="text-white font-bold text-sm">${data.position_amt}</div>
                        <div class="text-slate-400 text-[11px]">$${Number(data.risk ? data.risk.notional_usdt : 0).toFixed(2)} USDT</div>
                    </div>
                    <div class="card-inner p-3 rounded-xl border border-slate-800">
                        <span class="text-slate-500 block text-[10px] uppercase">Allocated Margin</span>
                        <div class="text-white font-bold text-sm">$${Number(data.risk ? data.risk.allocated_margin_usdt : 0).toFixed(2)}</div>
                        <div class="text-slate-400 text-[11px]">${data.risk ? data.risk.margin_mode : 'ISOLATED'}</div>
                    </div>
                    <div class="card-inner p-3 rounded-xl border border-slate-800">
                        <span class="text-slate-500 block text-[10px] uppercase">Unrealized PnL</span>
                        <div class="font-bold text-sm ${upnlClass}">${upnlSign}$${upnl.toFixed(4)}</div>
                        <div class="text-[11px] ${upnlClass} font-semibold">${upnlSign}${Number(data.roe_pct || 0).toFixed(2)}% ROE</div>
                    </div>
                </div>

                <!-- 4. Thesis Audit & Recommendation Section -->
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <!-- Thesis Audit Card -->
                    <div class="card-inner p-4 rounded-xl border border-slate-800 space-y-3">
                        <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
                            <span class="text-xs font-bold text-slate-300 uppercase tracking-wide">Position Thesis Audit</span>
                            <span class="px-2.5 py-0.5 rounded-full text-xs font-bold border font-mono ${thesisBadgeClass}">${data.thesis_status}</span>
                        </div>
                        <p class="text-xs font-medium text-slate-200 leading-relaxed font-sans">${safeEH(data.thesis_summary)}</p>
                        <div class="space-y-1.5 pt-1">
                            <span class="text-[10px] text-slate-500 font-semibold uppercase block">Structural Evidence:</span>
                            <ul class="space-y-1.5">${evidenceHtml}</ul>
                        </div>
                    </div>

                    <!-- Action Recommendation Card -->
                    <div class="card-inner p-4 rounded-xl border border-slate-800 space-y-3">
                        <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
                            <span class="text-xs font-bold text-slate-300 uppercase tracking-wide">Action Recommendation</span>
                            <span class="px-2.5 py-0.5 rounded-full text-xs font-bold border font-mono ${actionBadgeClass}">${data.action}</span>
                        </div>
                        <p class="text-xs font-medium text-slate-200 leading-relaxed font-sans">${safeEH(data.action_summary)}</p>
                        
                        <!-- Structural Invalidation Criteria -->
                        <div class="space-y-1.5 pt-2 border-t border-slate-800/80">
                            <div class="flex items-center justify-between">
                                <span class="text-[10px] text-rose-400 font-semibold uppercase block">Structural Invalidation Criteria:</span>
                                <span class="text-[9px] text-slate-500">(Premise Failure)</span>
                            </div>
                            <ul class="space-y-1">${invalidationsHtml}</ul>
                        </div>
                    </div>
                </div>

                <!-- 5. Multi-Timeframe Structural Evidence Grid (4H Primary, 1H Local, 15M Momentum) -->
                <div class="card-inner p-4 rounded-xl border border-slate-800 space-y-3">
                    <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
                        <span class="text-xs font-bold text-slate-300 uppercase tracking-wide block">Independent Multi-Timeframe Structure (4H, 1H, 15M)</span>
                        <span class="text-[10px] text-slate-400">Trend &bull; EMA Stack &bull; RSI &bull; Key S/R &bull; Freshness</span>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-3 font-mono text-xs">
                        ${renderTfCard('4H PRIMARY', data.timeframe_4h)}
                        ${renderTfCard('1H LOCAL', data.timeframe_1h)}
                        ${renderTfCard('15M EXECUTION', data.timeframe_15m)}
                    </div>
                </div>

                <!-- 6. Structured Conditional Triggers -->
                <div class="card-inner p-4 rounded-xl border border-slate-800 space-y-3">
                    <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
                        <span class="text-xs font-bold text-slate-300 uppercase tracking-wide">Structured Conditional Triggers</span>
                        <span class="text-[10px] text-slate-400">Active Market Watchers &amp; Invalidation Levels</span>
                    </div>
                    ${triggersTableHtml}
                </div>

                <!-- 7. Risk & Protection Breakdown -->
                <div class="card-inner p-4 rounded-xl border border-slate-800 space-y-3">
                    <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
                        <div class="flex items-center gap-2">
                            <span class="text-xs font-bold text-slate-300 uppercase tracking-wide">Risk &amp; Protection Breakdown</span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold font-mono border ${riskBadgeClass}">${riskStatus}</span>
                        </div>
                        <span class="px-2 py-0.5 rounded text-[10px] font-bold font-mono border ${protBadgeClass}">
                            ${protStatus}
                        </span>
                    </div>

                    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 font-mono text-xs">
                        <!-- Stop Loss & All-In Fee Loss -->
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800/80 space-y-1.5">
                            <span class="text-slate-400 block text-[11px] font-semibold">STOP LOSS &amp; ALL-IN LOSS</span>
                            <div class="text-slate-200 font-bold">${data.risk.stop_loss_price ? '$' + safeFP(data.risk.stop_loss_price) : '<span class="text-rose-400 italic">No Stop Loss set</span>'}</div>
                            ${data.risk.has_stop_loss ? `
                                <div class="text-slate-400 text-[11px]">Distance: $${Number(data.risk.sl_distance_usdt).toFixed(2)} (${data.risk.sl_distance_pct}%)</div>
                                <div class="text-rose-400 font-bold pt-1 border-t border-slate-800">Net Loss: -$${Number(data.risk.net_loss_at_sl_usdt != null ? data.risk.net_loss_at_sl_usdt : (data.risk.all_in_loss_at_sl_usdt || 0)).toFixed(2)} USDT</div>
                                <div class="text-slate-500 text-[10px]">Gross Loss: -$${Number(data.risk.gross_loss_at_sl_usdt || 0).toFixed(2)} USDT</div>
                                <div class="text-slate-400 text-[10px]">Fees: $${Number(data.risk.est_taker_fees_usdt || 0).toFixed(3)} | Slip: $${Number(data.risk.est_slippage_usdt || 0).toFixed(3)} | Fund: $${Number(data.risk.est_funding_fee_usdt || 0).toFixed(3)}</div>
                                ${(data.risk.net_loss_equity_pct !== null || data.risk.loss_equity_pct !== null) ? `<div class="text-amber-400 text-[10px] font-semibold">Equity Risk: ${Number(data.risk.net_loss_equity_pct != null ? data.risk.net_loss_equity_pct : data.risk.loss_equity_pct).toFixed(2)}% of equity</div>` : ''}
                            ` : '<div class="text-rose-400 text-[11px] font-semibold pt-1">Unprotected position: exposed to full liquidation.</div>'}
                        </div>

                        <!-- Take Profit & R:R -->
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800/80 space-y-1.5">
                            <span class="text-slate-400 block text-[11px] font-semibold">TAKE PROFIT &amp; TARGETS</span>
                            <div class="text-slate-200 font-bold">${data.risk.take_profit_price ? '$' + safeFP(data.risk.take_profit_price) : '<span class="text-slate-500 italic">No TP configured</span>'}</div>
                            ${data.risk.has_take_profit ? `
                                <div class="text-slate-400 text-[11px]">Distance: $${Number(data.risk.tp_distance_usdt).toFixed(2)} (${data.risk.tp_distance_pct}%)</div>
                                <div class="text-emerald-400 font-bold pt-1 border-t border-slate-800">Net Profit: +$${Number(data.risk.net_profit_at_tp_usdt != null ? data.risk.net_profit_at_tp_usdt : (data.risk.gross_profit_at_tp_usdt || 0)).toFixed(2)} USDT</div>
                                <div class="text-slate-500 text-[10px]">Gross Profit: +$${Number(data.risk.gross_profit_at_tp_usdt || 0).toFixed(2)} USDT</div>
                                <div class="text-indigo-400 text-[11px] font-bold pt-0.5">Net R:R: 1 : ${data.risk.net_risk_reward_ratio != null ? data.risk.net_risk_reward_ratio : (data.risk.risk_reward_ratio || '--')}</div>
                                <div class="text-slate-500 text-[10px]">Gross R:R: 1 : ${data.risk.gross_risk_reward_ratio != null ? data.risk.gross_risk_reward_ratio : '--'}</div>
                            ` : '<div class="text-slate-500 text-[11px] pt-1">Target exit level not set on exchange.</div>'}
                        </div>

                        <!-- Liquidation Distance & Threat Level -->
                        <div class="p-3 rounded-lg bg-slate-900/60 border border-slate-800/80 space-y-1.5">
                            <div class="flex items-center justify-between">
                                <span class="text-slate-400 text-[11px] font-semibold">LIQUIDATION PROXIMITY</span>
                                <span class="px-1.5 py-0.2 rounded text-[9px] font-bold border ${liqBadgeClass}">${liqThreat}</span>
                            </div>
                            <div class="text-slate-200 font-bold">${data.risk.liquidation_price ? '$' + safeFP(data.risk.liquidation_price) : '<span class="text-slate-500 italic">Unavailable</span>'}</div>
                            ${data.risk.has_liquidation_price ? `
                                <div class="text-slate-400 text-[11px]">Distance: $${Number(data.risk.liquidation_distance_usdt).toFixed(2)} (${data.risk.liquidation_distance_pct}%)</div>
                                <div class="text-[10px] text-slate-400 pt-1 border-t border-slate-800 font-sans">${safeEH(data.risk.margin_mode_assessment)}</div>
                            ` : '<div class="text-slate-500 text-[11px] pt-1">Exchange reported 0 or no liquidation.</div>'}
                        </div>
                    </div>

                    <!-- Protection Verification and Audit Notes -->
                    <div class="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80 space-y-1.5 font-mono text-[11px]">
                        <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/60 pb-1">
                            <div class="flex items-center gap-2">
                                <span class="text-slate-400">Exchange Order Verification:</span>
                                <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${data.risk.is_exchange_verified ? 'text-emerald-400 bg-emerald-950 border border-emerald-800' : 'text-amber-400 bg-amber-950 border border-amber-800'}">
                                    ${data.risk.is_exchange_verified ? 'VERIFIED ACTIVE ORDERS' : 'UNVERIFIED / LOCAL CONFIG ONLY'}
                                </span>
                            </div>
                            <div class="text-slate-500 text-[10px]">SL ID: ${data.risk.verified_sl_order_id || 'None'} | TP ID: ${data.risk.verified_tp_order_id || 'None'}</div>
                        </div>
                        ${(data.risk.protection_notes || []).length > 0 ? `
                            <div class="text-amber-300 text-[11px] space-y-0.5 pt-1">
                                ${data.risk.protection_notes.map(n => `<div>&bull; ${safeEH(n)}</div>`).join('')}
                            </div>
                        ` : '<div class="text-slate-500 text-[10px]">No protection warnings.</div>'}
                    </div>
                </div>

                <!-- 8. Data Freshness & Verification Timestamps Footer -->
                <div class="p-3 rounded-xl bg-slate-950/80 border border-slate-800/80 flex flex-wrap items-center justify-between gap-2 text-[10px] font-mono text-slate-400">
                    <div class="flex items-center gap-2">
                        <span class="px-1.5 py-0.5 rounded font-bold ${freshClass}">${freshness.overall_freshness || 'FRESH'}</span>
                        <span>Generated: ${data.analyzed_at_utc ? new Date(data.analyzed_at_utc).toLocaleTimeString() : '--'}</span>
                    </div>
                    <div class="flex items-center gap-3">
                        <span>Pos Snap: ${freshness.position_snapshot_utc ? new Date(freshness.position_snapshot_utc).toLocaleTimeString() : '--'}</span>
                        <span>15M: ${freshness.candle_timestamps && freshness.candle_timestamps['15m'] ? freshness.candle_timestamps['15m'].age_seconds + 's ago' : '--'}</span>
                        <span>1H: ${freshness.candle_timestamps && freshness.candle_timestamps['1h'] ? freshness.candle_timestamps['1h'].age_seconds + 's ago' : '--'}</span>
                        <span>4H: ${freshness.candle_timestamps && freshness.candle_timestamps['4h'] ? freshness.candle_timestamps['4h'].age_seconds + 's ago' : '--'}</span>
                    </div>
                </div>
            `;
        }

        // -------------------------------------------------------------------------
        // Portfolio-Level Exposure & Risk Audit Modal
        // -------------------------------------------------------------------------
        async function openPortfolioAnalysisModal() {
            const modal = document.getElementById('portfolioAnalysisModal');
            const content = document.getElementById('portfolioAnalysisModalContent');
            const healthBadge = document.getElementById('modalPortHealthBadge');

            if (!modal || !content) return;
            modal.classList.remove('hidden');
            if (healthBadge) {
                healthBadge.innerText = 'ANALYZING...';
                healthBadge.className = 'px-2 py-0.5 rounded text-xs font-bold bg-slate-800 text-slate-300 font-mono';
            }

            content.innerHTML = `
                <div class="py-16 text-center space-y-3">
                    <div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500"></div>
                    <div class="text-sm font-mono text-slate-300">Synthesizing live AsterDEX portfolio exposure &amp; directional risk...</div>
                </div>
            `;

            try {
                const res = await fetch('/api/intelligence/analyze_portfolio');
                if (!res.ok) throw new Error(`API returned status ${res.status}`);
                const data = await res.json();
                renderPortfolioAnalysis(data);
            } catch (err) {
                content.innerHTML = `
                    <div class="p-6 text-center rounded-xl bg-red-950/40 border border-red-800 text-rose-300 space-y-2">
                        <div class="font-bold text-sm">Failed to generate portfolio risk analysis</div>
                        <div class="text-xs font-mono text-slate-400">${escapeHtml(err.message)}</div>
                    </div>
                `;
            }
        }

        function closePortfolioAnalysisModal() {
            const modal = document.getElementById('portfolioAnalysisModal');
            if (modal) modal.classList.add('hidden');
        }

        function renderPortfolioAnalysis(data) {
            const content = document.getElementById('portfolioAnalysisModalContent');
            const healthBadge = document.getElementById('modalPortHealthBadge');
            if (!content) return;

            let healthClass = 'bg-slate-800 text-slate-300 border border-slate-700';
            if (data.portfolio_health_status === 'HEALTHY') healthClass = 'bg-emerald-950 text-emerald-300 border border-emerald-800';
            else if (data.portfolio_health_status === 'MODERATE_RISK') healthClass = 'bg-yellow-950 text-yellow-300 border border-yellow-800';
            else if (data.portfolio_health_status === 'ELEVATED_RISK') healthClass = 'bg-amber-950 text-amber-300 border border-amber-800';
            else if (data.portfolio_health_status === 'CRITICAL_ACTION_REQUIRED') healthClass = 'bg-rose-950 text-rose-300 border border-rose-800 animate-pulse';

            if (healthBadge) {
                healthBadge.className = `px-2 py-0.5 rounded text-xs font-bold font-mono ${healthClass}`;
                healthBadge.innerText = data.portfolio_health_status;
            }

            // Concentration & Correlation Warnings
            const allWarnings = [...(data.concentration_warnings || []), ...(data.correlation_warnings || [])];
            const warningsHtml = allWarnings.length > 0 ? `
                <div class="p-3 rounded-xl bg-amber-950/50 border border-amber-800/80 text-xs text-amber-300 space-y-1">
                    <span class="font-bold block uppercase text-[10px] text-amber-400">&#9888; Portfolio Risk Alerts:</span>
                    ${allWarnings.map(w => `<div>&bull; ${escapeHtml(w)}</div>`).join('')}
                </div>
            ` : '';

            // Table of positions
            const positionsRows = (data.positions_analyses || []).map(p => {
                const isLong = p.side === 'LONG';
                const sideClass = isLong ? 'text-emerald-400 bg-emerald-950/60' : 'text-rose-400 bg-rose-950/60';
                const upnl = Number(p.unrealized_pnl_usdt || 0);
                const upnlClass = upnl >= 0 ? 'text-emerald-400' : 'text-rose-400';
                const upnlSign = upnl >= 0 ? '+' : '';

                return `
                    <tr class="border-b border-slate-800/60 hover:bg-slate-800/30 transition font-mono text-xs">
                        <td class="py-2.5 px-3">
                            <span class="font-bold text-white">${escapeHtml(p.symbol)}</span>
                            <span class="ml-1.5 px-1.5 py-0.2 rounded text-[10px] font-bold ${sideClass}">${p.side}</span>
                        </td>
                        <td class="py-2.5 px-3">$${Number(p.risk.notional_usdt || 0).toFixed(2)}</td>
                        <td class="py-2.5 px-3">$${Number(p.risk.allocated_margin_usdt || 0).toFixed(2)}</td>
                        <td class="py-2.5 px-3 ${upnlClass} font-bold">${upnlSign}$${upnl.toFixed(2)}</td>
                        <td class="py-2.5 px-3">
                            <span class="px-1.5 py-0.2 rounded text-[10px] font-bold ${p.thesis_status === 'VALID' ? 'text-emerald-300 bg-emerald-950' : 'text-amber-300 bg-amber-950'}">${p.thesis_status}</span>
                        </td>
                        <td class="py-2.5 px-3">
                            <span class="px-1.5 py-0.2 rounded text-[10px] font-bold ${p.action === 'HOLD' ? 'text-emerald-300 bg-emerald-950' : 'text-cyan-300 bg-cyan-950'}">${p.action}</span>
                        </td>
                        <td class="py-2.5 px-3 text-right">
                            <button onclick="closePortfolioAnalysisModal(); analyzePositionEntry('${escapeHtml(p.symbol)}')" class="px-2 py-0.5 text-[11px] font-medium rounded bg-indigo-600 hover:bg-indigo-500 text-white transition">
                                Inspect
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');

            content.innerHTML = `
                <!-- Overview Metric Cards -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
                    <div class="card-inner p-3 rounded-xl border border-slate-800">
                        <span class="text-slate-500 block text-[10px] uppercase">Open Positions</span>
                        <div class="text-white font-bold text-base">${data.open_positions_count} Active</div>
                        <div class="text-slate-400 text-[10px]">${data.unprotected_positions_count} Unprotected</div>
                    </div>
                    <div class="card-inner p-3 rounded-xl border border-slate-800">
                        <span class="text-slate-500 block text-[10px] uppercase">Total Notional Exposure</span>
                        <div class="text-white font-bold text-base">$${Number(data.total_notional_usdt || 0).toFixed(2)}</div>
                        <div class="text-indigo-400 text-[10px]">${data.net_directional_bias} ($${Number(data.net_exposure_usdt || 0).toFixed(2)})</div>
                    </div>
                    <div class="card-inner p-3 rounded-xl border border-slate-800">
                        <span class="text-slate-500 block text-[10px] uppercase">Margin Utilization</span>
                        <div class="text-white font-bold text-base">${Number(data.margin_utilization_pct || 0).toFixed(1)}%</div>
                        <div class="text-slate-400 text-[10px]">$${Number(data.total_margin_committed_usdt || 0).toFixed(2)} Committed</div>
                    </div>
                    <div class="card-inner p-3 rounded-xl border border-slate-800">
                        <span class="text-slate-500 block text-[10px] uppercase">All-In Stop Risk</span>
                        <div class="text-rose-400 font-bold text-base">-$${Number(data.total_all_in_risk_at_stops_usdt || 0).toFixed(2)}</div>
                        <div class="text-slate-400 text-[10px]">${data.portfolio_risk_equity_pct !== null ? data.portfolio_risk_equity_pct + '% Equity' : 'Equity N/A'}</div>
                    </div>
                </div>

                <!-- Action Narrative -->
                <div class="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800 text-xs text-slate-300">
                    <span class="text-[10px] text-indigo-400 font-semibold uppercase block">Portfolio Governance Directive:</span>
                    <p class="mt-1">${escapeHtml(data.portfolio_action_summary)}</p>
                </div>

                ${warningsHtml}

                <!-- Active Positions Table -->
                <div class="card-inner p-4 rounded-xl border border-slate-800 space-y-2">
                    <span class="text-xs font-bold text-slate-300 uppercase tracking-wide block">Audited Positions Matrix</span>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="border-b border-slate-800 text-slate-400 text-[10px] uppercase tracking-wider font-semibold font-mono">
                                    <th class="py-2 px-3">Symbol</th>
                                    <th class="py-2 px-3">Notional</th>
                                    <th class="py-2 px-3">Margin</th>
                                    <th class="py-2 px-3">UPnL</th>
                                    <th class="py-2 px-3">Thesis</th>
                                    <th class="py-2 px-3">Action</th>
                                    <th class="py-2 px-3 text-right">Details</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${positionsRows || '<tr><td colspan="7" class="py-6 text-center text-slate-500 font-mono text-xs">No active positions to audit.</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        }

        // -------------------------------------------------------------------------
        // Market Liquidity Intelligence Tab Engine
        // -------------------------------------------------------------------------
        function handleIntelSymbolKeydown(event) {
            if (event && event.key === 'Enter') {
                triggerManualIntelRefresh();
            }
        }

        function handleIntelSymbolChange(event) {
            const sym = (event.target.value || '').trim().toUpperCase();
            if (!sym) return;
            const sel = document.getElementById('intelSymbolSelect');
            if (sel) {
                const hasOption = Array.from(sel.options).some(o => o.value === sym);
                if (hasOption) sel.value = sym;
            }
            triggerManualIntelRefresh();
        }

        function handleIntelSymbolSelect(event) {
            const sym = (event.target.value || '').trim().toUpperCase();
            if (!sym) return;
            const inp = document.getElementById('intelSymbolInput');
            if (inp) inp.value = sym;
            triggerManualIntelRefresh();
        }

        function quickSelectIntelSymbol(sym) {
            const inp = document.getElementById('intelSymbolInput');
            if (inp) inp.value = sym;
            const sel = document.getElementById('intelSymbolSelect');
            if (sel) sel.value = sym;
            triggerManualIntelRefresh();
        }

        // -------------------------------------------------------------------------
        // Market Liquidity Intelligence Tab Engine & Auto-Refresh Infrastructure
        // -------------------------------------------------------------------------
        let intelAutoRefreshEnabled = true;
        let intelCountdownTimer = null;
        let intelCountdownSeconds = 5;
        let isIntelFetchInFlight = false;
        let consecutiveIntelErrors = 0;
        let lastIntelRefreshTimestamp = null;
        let lastHeatmapRefreshMs = 0;
        let lastWhaleRefreshMs = 0;

        function renderVenueHealth(venueSummaries) {
            if (!venueSummaries) return;
            const venues = ['asterdex', 'binance', 'bybit'];
            venues.forEach(v => {
                const snap = venueSummaries[v];
                const el = document.getElementById(`intelExBadge-${v}`);
                if (!el) return;
                const statusSpan = el.querySelector('.badge-status');
                const dot = el.querySelector('.badge-dot');

                let status = 'UNAVAIL';
                let cls = 'bg-slate-900 text-slate-500 border-slate-800';
                let dotCls = 'bg-slate-500';

                if (snap) {
                    const rawStatus = (snap.freshness_status || snap.status || '').toUpperCase();
                    if (rawStatus === 'LIVE' || rawStatus === 'AVAILABLE') {
                        status = 'LIVE';
                        cls = 'bg-emerald-950/80 text-emerald-300 border-emerald-800/80';
                        dotCls = 'bg-emerald-400';
                    } else if (rawStatus === 'STALE') {
                        status = 'STALE';
                        cls = 'bg-amber-950/80 text-amber-300 border-amber-800/80';
                        dotCls = 'bg-amber-400';
                    } else if (rawStatus === 'ERROR') {
                        status = 'ERROR';
                        cls = 'bg-rose-950/80 text-rose-300 border-rose-800/80';
                        dotCls = 'bg-rose-400';
                    }
                }

                el.className = `px-2 py-0.5 rounded text-[10px] font-bold flex items-center gap-1 border ${cls}`;
                if (statusSpan) statusSpan.innerText = status;
                if (dot) dot.className = `badge-dot w-1.5 h-1.5 rounded-full ${dotCls}`;
            });
        }

        async function executeStaggeredIntelRefresh(forceFull = false) {
            if (isIntelFetchInFlight) return;
            isIntelFetchInFlight = true;

            const inp = document.getElementById('intelSymbolInput');
            const sym = (inp && inp.value ? inp.value : 'BTCUSDT').trim().toUpperCase();
            const statusBadge = document.getElementById('intelRequestStatusBadge');

            if (statusBadge) {
                statusBadge.innerText = 'REFRESHING';
                statusBadge.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-cyan-950 text-cyan-300 border border-cyan-800';
            }

            const now = Date.now();
            const shouldRefreshWhale = forceFull || (now - lastWhaleRefreshMs >= 10000);
            const shouldRefreshHeatmap = forceFull || (now - lastHeatmapRefreshMs >= 30000);

            const promises = [
                fetch(`/api/intelligence/market_context?symbol=${sym}`),
                fetch(`/api/chart/cross_book?symbol=${sym}&bucket_bps=5.0&venue_source=combined`),
            ];
            if (shouldRefreshWhale) {
                promises.push(fetch(`/api/intelligence/whale_activity?symbol=${sym}`));
            }
            if (shouldRefreshHeatmap) {
                promises.push(fetch(`/api/intelligence/liquidation_heatmap?symbol=${sym}`));
            }

            try {
                const results = await Promise.all(promises);
                const [ctxRes, crossRes] = results;

                if (ctxRes && ctxRes.ok) {
                    const ctxData = await ctxRes.json();
                    renderMarketPosture(ctxData);

                    // Update AsterDEX freshness indicator if specific telemetry present
                    if (ctxData.data_freshness && ctxData.data_freshness.source_exchange === 'AsterDEX') {
                        const asterBadge = document.getElementById('intelExBadge-asterdex');
                        if (asterBadge) {
                            const statusSpan = asterBadge.querySelector('.badge-status');
                            const dot = asterBadge.querySelector('.badge-dot');
                            const st = (ctxData.data_freshness.status || 'LIVE').toUpperCase();
                            if (statusSpan) statusSpan.innerText = st;
                            if (st === 'LIVE') {
                                asterBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold flex items-center gap-1 border bg-emerald-950/80 text-emerald-300 border-emerald-800/80';
                                if (dot) dot.className = 'badge-dot w-1.5 h-1.5 rounded-full bg-emerald-400';
                            } else if (st === 'STALE') {
                                asterBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold flex items-center gap-1 border bg-amber-950/80 text-amber-300 border-amber-800/80';
                                if (dot) dot.className = 'badge-dot w-1.5 h-1.5 rounded-full bg-amber-400';
                            }
                        }
                    }
                }

                if (crossRes && crossRes.ok) {
                    const crossData = await crossRes.json();
                    renderVenueHealth(crossData.venue_summaries);
                }

                let resIdx = 2;
                if (shouldRefreshWhale && results[resIdx]) {
                    const whaleRes = results[resIdx++];
                    if (whaleRes && whaleRes.ok) {
                        const whaleData = await whaleRes.json();
                        cachedWhaleEvents = whaleData.recent_events || [];
                        renderWhaleActivity(whaleData);
                        lastWhaleRefreshMs = now;
                    }
                }

                if (shouldRefreshHeatmap && results[resIdx]) {
                    const hmRes = results[resIdx++];
                    if (hmRes && hmRes.ok) {
                        const hmData = await hmRes.json();
                        renderLiquidityHeatmap(hmData);
                        lastHeatmapRefreshMs = now;
                    }
                }

                consecutiveIntelErrors = 0;
                lastIntelRefreshTimestamp = Date.now();
                if (statusBadge) {
                    statusBadge.innerText = 'IDLE';
                    statusBadge.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-slate-900 text-slate-300 border border-slate-700';
                }
            } catch (err) {
                console.error("Auto-refresh error:", err);
                consecutiveIntelErrors++;
                const backoffSec = Math.min(60, Math.round(5 * Math.pow(1.5, consecutiveIntelErrors)) + Math.floor(Math.random() * 2));
                intelCountdownSeconds = backoffSec;
                if (statusBadge) {
                    statusBadge.innerText = `BACKOFF (${backoffSec}s)`;
                    statusBadge.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-rose-950 text-rose-300 border border-rose-800';
                }
            } finally {
                isIntelFetchInFlight = false;
            }
        }

        async function triggerManualIntelRefresh() {
            const inp = document.getElementById('intelSymbolInput');
            const sym = (inp && inp.value ? inp.value : 'BTCUSDT').trim().toUpperCase();
            const sel = document.getElementById('intelSymbolSelect');
            if (sel && Array.from(sel.options).some(o => o.value === sym)) {
                sel.value = sym;
            }
            const btn = document.getElementById('intelRefreshBtn');
            const spin = document.getElementById('intelSpinIcon');

            if (btn) btn.disabled = true;
            if (spin) spin.classList.add('animate-spin');

            try {
                await executeStaggeredIntelRefresh(true);
                intelCountdownSeconds = 5;
            } finally {
                if (btn) btn.disabled = false;
                if (spin) spin.classList.remove('animate-spin');
            }
        }

        function loadLiquidityIntel() {
            triggerManualIntelRefresh();
        }

        function startLiquidityIntelAutoRefresh() {
            stopLiquidityIntelAutoRefresh();
            if (!intelAutoRefreshEnabled) return;

            intelCountdownSeconds = 5;
            if (!lastIntelRefreshTimestamp || (Date.now() - lastIntelRefreshTimestamp > 5000)) {
                executeStaggeredIntelRefresh(true);
            }

            intelCountdownTimer = setInterval(() => {
                if (!intelAutoRefreshEnabled) return;
                if (document.hidden) return;

                intelCountdownSeconds--;
                const secEl = document.getElementById('intelCountdownSec');
                if (secEl) secEl.innerText = `${Math.max(0, intelCountdownSeconds)}s`;

                const lastUpEl = document.getElementById('intelLastUpdatedDisplay');
                if (lastUpEl && lastIntelRefreshTimestamp) {
                    const elapsed = Math.round((Date.now() - lastIntelRefreshTimestamp) / 1000);
                    lastUpEl.innerText = elapsed < 3 ? 'Last sync: Just now' : `Last sync: ${elapsed}s ago`;
                }

                if (intelCountdownSeconds <= 0) {
                    intelCountdownSeconds = 5;
                    executeStaggeredIntelRefresh(false);
                }
            }, 1000);
        }

        function stopLiquidityIntelAutoRefresh() {
            if (intelCountdownTimer) {
                clearInterval(intelCountdownTimer);
                intelCountdownTimer = null;
            }
            const statusBadge = document.getElementById('intelRequestStatusBadge');
            if (statusBadge && intelAutoRefreshEnabled) {
                statusBadge.innerText = 'IDLE';
                statusBadge.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-slate-900 text-slate-300 border border-slate-700';
            }
        }

        function toggleIntelAutoRefresh() {
            intelAutoRefreshEnabled = !intelAutoRefreshEnabled;
            const btn = document.getElementById('intelAutoRefreshToggleBtn');
            const label = document.getElementById('intelAutoRefreshLabel');
            const ind = document.getElementById('intelAutoRefreshIndicator');
            const statusBadge = document.getElementById('intelRequestStatusBadge');

            if (intelAutoRefreshEnabled) {
                if (btn) btn.className = 'px-2.5 py-1 rounded-md text-xs font-semibold flex items-center gap-1.5 transition bg-emerald-950/80 text-emerald-300 border border-emerald-700/80 hover:bg-emerald-900 cursor-pointer';
                if (label) label.innerText = 'Auto-Refresh: ON';
                if (ind) ind.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
                startLiquidityIntelAutoRefresh();
            } else {
                if (btn) btn.className = 'px-2.5 py-1 rounded-md text-xs font-semibold flex items-center gap-1.5 transition bg-slate-800 text-slate-400 border border-slate-700 hover:bg-slate-700 cursor-pointer';
                if (label) label.innerText = 'Auto-Refresh: OFF';
                if (ind) ind.className = 'w-2 h-2 rounded-full bg-slate-500';
                if (statusBadge) {
                    statusBadge.innerText = 'PAUSED';
                    statusBadge.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-slate-800 text-slate-400 border border-slate-700';
                }
                stopLiquidityIntelAutoRefresh();
            }
        }

        if (typeof document !== 'undefined') {
            document.addEventListener('visibilitychange', () => {
                const tabEl = document.getElementById('tab-liquidity_intel');
                const isIntelTabActive = tabEl && !tabEl.classList.contains('hidden');

                if (!document.hidden && isIntelTabActive && intelAutoRefreshEnabled) {
                    intelCountdownSeconds = 1;
                }
            });
        }

        function renderMarketPosture(ctx) {
            const markEl = document.getElementById('intelMarkPriceDisplay');
            const syncEl = document.getElementById('intelSyncTime');
            const narrEl = document.getElementById('intelPostureNarrative');
            const indMacro = document.getElementById('indMacroTrend');
            const indLocal = document.getElementById('indLocalTrend');
            const indMom = document.getElementById('ind15mMomentum');
            const indDepth = document.getElementById('indOrderBook');
            const indTaker = document.getElementById('indTakerFlow');
            const indLiq = document.getElementById('indLiqRisk');
            const indWhale = document.getElementById('indWhaleFlow');
            const levelsContainer = document.getElementById('intelKeyLevelsContainer');

            if (markEl) markEl.innerText = `Mark: $${formatPrice(ctx.mark_price)}`;
            if (syncEl) syncEl.innerText = `Updated: ${new Date(ctx.generated_at_utc).toLocaleTimeString()}`;
            if (narrEl) narrEl.innerText = ctx.market_posture_summary;

            // Indicator tags styling helper
            function styleTrendEl(el, val) {
                if (!el) return;
                el.innerText = val || '--';
                if (val === 'BULLISH' || val === 'BID_HEAVY' || val === 'BUY_DOMINANT' || val === 'BULLISH_FLOW') {
                    el.className = 'text-xs font-bold text-emerald-400 mt-1 block font-mono';
                } else if (val === 'BEARISH' || val === 'ASK_HEAVY' || val === 'SELL_DOMINANT' || val === 'BEARISH_FLOW') {
                    el.className = 'text-xs font-bold text-rose-400 mt-1 block font-mono';
                } else {
                    el.className = 'text-xs font-bold text-slate-300 mt-1 block font-mono';
                }
            }

            styleTrendEl(indMacro, ctx.structural_indicators.macro_4h_trend);
            styleTrendEl(indLocal, ctx.structural_indicators.local_1h_trend);
            styleTrendEl(indMom, ctx.structural_indicators.execution_15m_momentum);
            styleTrendEl(indDepth, ctx.structural_indicators.order_book_bias);
            styleTrendEl(indTaker, ctx.structural_indicators.taker_flow_bias);
            styleTrendEl(indLiq, ctx.structural_indicators.liquidation_risk_profile);
            styleTrendEl(indWhale, ctx.structural_indicators.whale_flow_bias);

            // Key levels cards
            if (levelsContainer && ctx.key_levels_to_watch) {
                levelsContainer.innerHTML = ctx.key_levels_to_watch.map(lvl => {
                    const isRes = lvl.type === 'RESISTANCE' || lvl.type === 'LIQUIDATION_MAGNET' && lvl.label.includes('Short');
                    const colorClass = isRes ? 'border-rose-900/60 bg-rose-950/20 text-rose-300' : 'border-emerald-900/60 bg-emerald-950/20 text-emerald-300';
                    return `
                        <div class="p-2.5 rounded-lg border ${colorClass} space-y-1">
                            <div class="flex items-center justify-between text-[10px] text-slate-400">
                                <span>${escapeHtml(lvl.type)}</span>
                                <span>${lvl.distance_pct}% away</span>
                            </div>
                            <div class="text-sm font-bold text-white">$${formatPrice(lvl.price)}</div>
                            <div class="text-[10px] text-slate-400 truncate">${escapeHtml(lvl.label)}</div>
                        </div>
                    `;
                }).join('') || '<div class="text-xs text-slate-500 py-2">No key levels detected.</div>';
            }
        }

        function renderLiquidityHeatmap(hm) {
            const ratioBadge = document.getElementById('intelLiqRatioBadge');
            const riskBadge = document.getElementById('intelLiqRiskBadge');
            const oiDisplay = document.getElementById('intelOiDisplay');
            const oiContracts = document.getElementById('intelOiContracts');
            const fundingDisplay = document.getElementById('intelFundingDisplay');
            const fundingAnnual = document.getElementById('intelFundingAnnual');
            const longPool = document.getElementById('intelLongLiqPool');
            const shortPool = document.getElementById('intelShortLiqPool');
            const nearestLong = document.getElementById('intelNearestLongLiq');
            const nearestShort = document.getElementById('intelNearestShortLiq');
            const markBadge = document.getElementById('intelMarkPriceBadge');
            const shortClustersEl = document.getElementById('intelShortClustersContainer');
            const longClustersEl = document.getElementById('intelLongClustersContainer');

            if (ratioBadge) ratioBadge.innerText = `L/S Liq Ratio: ${Number(hm.long_short_liquidation_ratio).toFixed(2)}`;
            if (riskBadge) {
                riskBadge.innerText = hm.predominant_risk_side;
                riskBadge.className = hm.predominant_risk_side === 'LONG_CASCADE_RISK'
                    ? 'px-2 py-0.5 rounded text-[11px] font-bold bg-amber-950 text-amber-300 border border-amber-800'
                    : (hm.predominant_risk_side === 'SHORT_SQUEEZE_RISK'
                        ? 'px-2 py-0.5 rounded text-[11px] font-bold bg-rose-950 text-rose-300 border border-rose-800'
                        : 'px-2 py-0.5 rounded text-[11px] font-bold bg-slate-800 text-slate-300 border border-slate-700');
            }

            if (oiDisplay) oiDisplay.innerText = hm.open_interest_usdt ? `$${(hm.open_interest_usdt / 1_000_000).toFixed(2)}M` : '--';
            if (oiContracts) oiContracts.innerText = hm.open_interest_contracts ? `${Number(hm.open_interest_contracts).toFixed(1)} contracts` : '';
            if (fundingDisplay) fundingDisplay.innerText = `${(Number(hm.funding_rate || 0) * 100).toFixed(4)}%`;
            if (fundingAnnual) fundingAnnual.innerText = `${Number(hm.funding_rate_annualized_pct || 0).toFixed(2)}% Annualized`;
            if (longPool) longPool.innerText = `$${(Number(hm.long_liquidation_volume_usdt || 0) / 1_000_000).toFixed(2)}M`;
            if (shortPool) shortPool.innerText = `$${(Number(hm.short_liquidation_volume_usdt || 0) / 1_000_000).toFixed(2)}M`;
            if (nearestLong) nearestLong.innerText = hm.nearest_long_cluster_pct !== null ? `Nearest: ${hm.nearest_long_cluster_pct}%` : '';
            if (nearestShort) nearestShort.innerText = hm.nearest_short_cluster_pct !== null ? `Nearest: ${hm.nearest_short_cluster_pct}%` : '';
            if (markBadge) markBadge.innerText = `CURRENT MARK PRICE: $${formatPrice(hm.mark_price)}`;

            // Render Short Clusters (Overhead)
            if (shortClustersEl) {
                const sClusters = [...(hm.short_clusters || [])].reverse(); // higher prices at top
                shortClustersEl.innerHTML = sClusters.map(c => `
                    <div class="p-2 rounded-lg bg-slate-900/60 border border-slate-800 hover:border-rose-800/80 transition space-y-1">
                        <div class="flex items-center justify-between text-[11px]">
                            <div class="flex items-center gap-2">
                                <span class="font-bold text-white">$${formatPrice(c.price_level)}</span>
                                <span class="px-1.5 py-0.2 rounded text-[9px] font-bold bg-rose-950 text-rose-300 border border-rose-800">${c.leverage_tier}</span>
                                <span class="text-[10px] text-slate-400">+$${Number(c.distance_usdt).toFixed(2)} (+${c.distance_pct}%)</span>
                            </div>
                            <div class="flex items-center gap-2">
                                <span class="text-rose-400 font-bold">$${(c.estimated_volume_usdt / 1_000).toFixed(1)}k</span>
                                <span class="px-1.5 py-0.2 rounded text-[9px] ${c.confidence_rating === 'HIGH' ? 'bg-indigo-950 text-indigo-300 border border-indigo-800' : 'bg-slate-800 text-slate-400'}">${c.confidence_rating}</span>
                            </div>
                        </div>
                        <div class="w-full bg-slate-800/80 h-1.5 rounded-full overflow-hidden">
                            <div class="bg-rose-500 h-1.5 rounded-full" style="width: ${c.relative_intensity_pct}%"></div>
                        </div>
                    </div>
                `).join('') || '<div class="text-xs text-slate-500 py-2">No short clusters detected.</div>';
            }

            // Render Long Clusters (Below)
            if (longClustersEl) {
                longClustersEl.innerHTML = (hm.long_clusters || []).map(c => `
                    <div class="p-2 rounded-lg bg-slate-900/60 border border-slate-800 hover:border-emerald-800/80 transition space-y-1">
                        <div class="flex items-center justify-between text-[11px]">
                            <div class="flex items-center gap-2">
                                <span class="font-bold text-white">$${formatPrice(c.price_level)}</span>
                                <span class="px-1.5 py-0.2 rounded text-[9px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800">${c.leverage_tier}</span>
                                <span class="text-[10px] text-slate-400">-$${Number(c.distance_usdt).toFixed(2)} (-${c.distance_pct}%)</span>
                            </div>
                            <div class="flex items-center gap-2">
                                <span class="text-emerald-400 font-bold">$${(c.estimated_volume_usdt / 1_000).toFixed(1)}k</span>
                                <span class="px-1.5 py-0.2 rounded text-[9px] ${c.confidence_rating === 'HIGH' ? 'bg-indigo-950 text-indigo-300 border border-indigo-800' : 'bg-slate-800 text-slate-400'}">${c.confidence_rating}</span>
                            </div>
                        </div>
                        <div class="w-full bg-slate-800/80 h-1.5 rounded-full overflow-hidden">
                            <div class="bg-emerald-500 h-1.5 rounded-full" style="width: ${c.relative_intensity_pct}%"></div>
                        </div>
                    </div>
                `).join('') || '<div class="text-xs text-slate-500 py-2">No long clusters detected.</div>';
            }
        }

        function renderWhaleActivity(whale) {
            const biasBadge = document.getElementById('intelWhaleBiasBadge');
            const wallsBadge = document.getElementById('intelActiveWallsBadge');
            const spoofsBadge = document.getElementById('intelSpoofsBadge');

            if (biasBadge) {
                biasBadge.innerText = `Bias: ${whale.net_whale_bias}`;
                biasBadge.className = whale.net_whale_bias === 'BULLISH_FLOW'
                    ? 'px-2 py-0.5 rounded font-bold bg-emerald-950 text-emerald-300 border border-emerald-800'
                    : (whale.net_whale_bias === 'BEARISH_FLOW'
                        ? 'px-2 py-0.5 rounded font-bold bg-rose-950 text-rose-300 border border-rose-800'
                        : 'px-2 py-0.5 rounded font-bold bg-slate-800 text-slate-300 border border-slate-700');
            }
            if (wallsBadge) wallsBadge.innerText = `${whale.active_walls_count} Active Walls`;
            if (spoofsBadge) spoofsBadge.innerText = `${whale.spoof_cancellation_count} Spoof(s)`;

            renderWhaleEventsTable();
        }

        function setWhaleFilter(filt) {
            currentWhaleFilter = filt;
            ['ALL', 'WALLS', 'TRADES', 'SPOOFS', 'ABSORPTION'].forEach(f => {
                const btn = document.getElementById(`whaleFilt-${f}`);
                if (btn) {
                    btn.className = f === filt
                        ? 'px-2.5 py-1 rounded-lg bg-indigo-600 text-white font-medium'
                        : 'px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 font-medium';
                }
            });
            renderWhaleEventsTable();
        }

        function renderWhaleEventsTable() {
            const tbody = document.getElementById('intelWhaleEventsTableBody');
            if (!tbody) return;

            let filtered = cachedWhaleEvents;
            if (currentWhaleFilter === 'WALLS') {
                filtered = filtered.filter(e => e.event_type.includes('WALL') || e.event_type.includes('RESTING'));
            } else if (currentWhaleFilter === 'TRADES') {
                filtered = filtered.filter(e => e.event_type.includes('AGGRESSIVE'));
            } else if (currentWhaleFilter === 'SPOOFS') {
                filtered = filtered.filter(e => e.event_type.includes('SPOOF') || e.event_type.includes('CANCELLATION'));
            } else if (currentWhaleFilter === 'ABSORPTION') {
                filtered = filtered.filter(e => e.event_type.includes('ABSORPTION') || e.event_type.includes('OI_'));
            }

            const pEl = document.getElementById('intelWhaleEventsPaginationContainer');

            if (!filtered || filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" class="py-6 text-center text-slate-500">No events matching filter '${currentWhaleFilter}'.</td></tr>`;
                if (pEl) pEl.innerHTML = '';
                return;
            }

            const paged = (typeof paginateList === 'function')
                ? paginateList('intel_whale_events', filtered)
                : filtered;

            tbody.innerHTML = paged.map(ev => {
                let badgeClass = 'bg-slate-800 text-slate-300 border-slate-700';
                if (ev.event_type.includes('BID') || ev.event_type.includes('BUY')) badgeClass = 'bg-emerald-950 text-emerald-300 border-emerald-800';
                else if (ev.event_type.includes('ASK') || ev.event_type.includes('SELL')) badgeClass = 'bg-rose-950 text-rose-300 border-rose-800';
                else if (ev.event_type.includes('SPOOF') || ev.event_type.includes('CANCELLATION')) badgeClass = 'bg-amber-950 text-amber-300 border-amber-800';
                else if (ev.event_type.includes('ABSORPTION')) badgeClass = 'bg-purple-950 text-purple-300 border-purple-800';

                const timeStr = ev.timestamp_utc ? new Date(ev.timestamp_utc).toLocaleTimeString() : '--';
                return `
                    <tr class="hover:bg-slate-800/30 transition border-b border-slate-800/50">
                        <td class="py-2.5 px-3 text-slate-400 whitespace-nowrap">${timeStr}</td>
                        <td class="py-2.5 px-3">
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${badgeClass}">${ev.event_type}</span>
                        </td>
                        <td class="py-2.5 px-3 font-semibold ${ev.side === 'BUY' || ev.side === 'BID' ? 'text-emerald-400' : (ev.side === 'SELL' || ev.side === 'ASK' ? 'text-rose-400' : 'text-slate-400')}">
                            ${ev.side}
                        </td>
                        <td class="py-2.5 px-3">
                            <div class="text-white font-medium">$${formatPrice(ev.price)}</div>
                            <div class="text-[10px] text-slate-400">${ev.distance_bps} bps</div>
                        </td>
                        <td class="py-2.5 px-3">
                            <div class="text-slate-200 font-bold">$${Number(ev.notional_usdt).toFixed(2)}</div>
                            <div class="text-[10px] text-slate-400">${Number(ev.size).toFixed(3)} qty</div>
                        </td>
                        <td class="py-2.5 px-3 font-bold text-indigo-300">${ev.baseline_multiple}x</td>
                        <td class="py-2.5 px-3 max-w-xs truncate text-[11px] text-slate-300" title="${escapeHtml(ev.details)}">
                            ${escapeHtml(ev.details)}
                        </td>
                    </tr>
                `;
            }).join('');

            if (typeof renderPaginationControls === 'function') {
                renderPaginationControls('intel_whale_events', 'intelWhaleEventsPaginationContainer', () => {
                    renderWhaleEventsTable();
                });
            }
        }

        // Window exports for cross-module coordination
        if (typeof window !== 'undefined') {
            window.startLiquidityIntelAutoRefresh = startLiquidityIntelAutoRefresh;
            window.stopLiquidityIntelAutoRefresh = stopLiquidityIntelAutoRefresh;
            window.toggleIntelAutoRefresh = toggleIntelAutoRefresh;
            window.triggerManualIntelRefresh = triggerManualIntelRefresh;
            window.loadLiquidityIntel = loadLiquidityIntel;
        }

        // =========================================================================