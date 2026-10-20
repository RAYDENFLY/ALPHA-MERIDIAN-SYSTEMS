/**
 * AI Trading Analyst Frontend Controller (Groq LLM + Quantitative Engine)
 * Handles symbol analysis requests, Groq model inference, risk calculations,
 * LONG vs SHORT scenario comparison, and Indonesian institutional narrative.
 */

let aiAnalystCurrentResult = null;
let aiAnalystHistory = [];
let aiAnalystConfig = null;
let aiAnalystSelectedSymbol = 'ETHUSDT';
let aiAnalystSelectedTimeframe = '1h';
let aiAnalystRiskBudget = 1.0;
let aiAnalystIsLoading = false;

// Quick-pick popular symbols
const AI_ANALYST_QUICK_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'LTCUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT', 'BNBUSDT'];

async function loadAIAnalyst() {
    try {
        await Promise.all([
            fetchAIAnalystConfig(),
            fetchAIAnalystSymbols(),
            fetchAIAnalystHistory()
        ]);
        renderAIAnalystQuickPicks();
    } catch (err) {
        console.error('Error initializing AI Analyst:', err);
    }
}

async function fetchAIAnalystConfig() {
    try {
        const res = await fetch('/api/ai_analyst/config');
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === 'SUCCESS' && data.config) {
            aiAnalystConfig = data.config;
            renderAIAnalystConfigHeader();
        }
    } catch (e) {
        console.warn('Could not fetch AI Analyst config:', e);
    }
}

async function fetchAIAnalystSymbols() {
    try {
        const res = await fetch('/api/ai_analyst/symbols');
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === 'SUCCESS' && Array.isArray(data.symbols)) {
            const datalist = document.getElementById('aiSymbolDatalist');
            if (datalist) {
                datalist.innerHTML = data.symbols.map(s => `<option value="${s}">`).join('');
            }
        }
    } catch (e) {
        console.warn('Could not fetch AI Analyst symbols:', e);
    }
}

async function fetchAIAnalystHistory(limit = 15) {
    try {
        const res = await fetch(`/api/ai_analyst/history?limit=${limit}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === 'SUCCESS') {
            const list = data.records || data.history || [];
            if (Array.isArray(list)) {
                aiAnalystHistory = list;
                renderAIAnalystHistoryTable();
                // If no active result displayed yet and history exists, render the latest one
                if (!aiAnalystCurrentResult && aiAnalystHistory.length > 0) {
                    aiAnalystCurrentResult = aiAnalystHistory[0];
                    renderAIAnalysisResult(aiAnalystCurrentResult);
                }
            }
        }
    } catch (e) {
        console.warn('Could not fetch AI Analyst history:', e);
    }
}

function renderAIAnalystConfigHeader() {
    if (!aiAnalystConfig) return;
    const modelBadge = document.getElementById('aiModelBadge');
    if (modelBadge) {
        const modelId = aiAnalystConfig.groq_model_id || aiAnalystConfig.model_id || 'qwen/qwen3.8-27b';
        const hasKey = aiAnalystConfig.has_api_key ?? aiAnalystConfig.groq_configured ?? true;
        const prov = hasKey ? 'GROQ' : 'FALLBACK';
        modelBadge.textContent = `${prov}: ${modelId}`;
        modelBadge.className = hasKey
            ? 'px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-purple-950 text-purple-300 border border-purple-800'
            : 'px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-amber-950 text-amber-300 border border-amber-800';
    }
}

function renderAIAnalystQuickPicks() {
    const container = document.getElementById('aiQuickPicksContainer');
    if (!container) return;
    container.innerHTML = AI_ANALYST_QUICK_SYMBOLS.map(sym => {
        const isSelected = (sym === aiAnalystSelectedSymbol);
        const cls = isSelected
            ? 'px-2.5 py-1 text-xs font-mono font-bold rounded bg-purple-600 text-white shadow'
            : 'px-2.5 py-1 text-xs font-mono font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition';
        return `<button type="button" onclick="selectAISymbol('${sym}')" class="${cls}">${sym.replace('USDT', '')}</button>`;
    }).join('');
}

function selectAISymbol(symbol) {
    aiAnalystSelectedSymbol = symbol.toUpperCase().trim();
    const input = document.getElementById('aiSymbolInput');
    if (input) input.value = aiAnalystSelectedSymbol;
    renderAIAnalystQuickPicks();
}

function selectAITimeframe(tf) {
    aiAnalystSelectedTimeframe = tf.toLowerCase().trim();
    ['15m', '1h', '4h', '1d'].forEach(t => {
        const btn = document.getElementById(`aiTfBtn-${t}`);
        if (!btn) return;
        if (t === aiAnalystSelectedTimeframe) {
            btn.className = 'px-3 py-1.5 text-xs font-semibold rounded-lg bg-purple-600 text-white shadow transition';
        } else {
            btn.className = 'px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 border border-slate-700 transition';
        }
    });
}

async function triggerAIAnalysis(forceRefresh = false) {
    if (aiAnalystIsLoading) return;

    const symbolInput = document.getElementById('aiSymbolInput');
    if (symbolInput && symbolInput.value.trim()) {
        aiAnalystSelectedSymbol = symbolInput.value.trim().toUpperCase();
    }

    const budgetInput = document.getElementById('aiRiskBudgetInput');
    if (budgetInput && budgetInput.value) {
        aiAnalystRiskBudget = parseFloat(budgetInput.value) || 1.0;
    }

    const forceCheckbox = document.getElementById('aiForceRefreshCheckbox');
    if (forceCheckbox && forceCheckbox.checked) {
        forceRefresh = true;
    }

    setAIAnalysisLoadingState(true);

    try {
        const payload = {
            symbol: aiAnalystSelectedSymbol,
            timeframe: aiAnalystSelectedTimeframe,
            risk_budget_usdt: aiAnalystRiskBudget,
            force_refresh: forceRefresh
        };

        const response = await fetch('/api/ai_analyst/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.detail || `Analysis request failed with status ${response.status}`);
        }

        const data = await response.json();
        if (data.status === 'SUCCESS' && data.result) {
            aiAnalystCurrentResult = data.result;
            renderAIAnalysisResult(data.result);
            fetchAIAnalystHistory(); // Update history list in background
        } else {
            throw new Error(data.error || 'Invalid analysis response structure');
        }
    } catch (err) {
        console.error('AI Analysis failed:', err);
        showAIError(err.message || 'Analysis error occurred');
    } finally {
        setAIAnalysisLoadingState(false);
    }
}

function setAIAnalysisLoadingState(isLoading) {
    aiAnalystIsLoading = isLoading;
    const btn = document.getElementById('aiRunAnalysisBtn');
    const spinIcon = document.getElementById('aiSpinIcon');
    const btnText = document.getElementById('aiRunBtnText');
    const statusNote = document.getElementById('aiAnalysisStatusNote');

    if (btn) btn.disabled = isLoading;
    if (spinIcon) spinIcon.classList.toggle('hidden', !isLoading);
    if (btnText) btnText.textContent = isLoading ? 'Analyzing Market...' : 'Run AI Analysis';
    if (statusNote) {
        statusNote.textContent = isLoading
            ? `Retrieving ${aiAnalystSelectedSymbol} AsterDEX order book, SMC structure & prompting Groq LLM...`
            : '';
    }
}

function showAIError(msg) {
    const errorBanner = document.getElementById('aiErrorBanner');
    const errorText = document.getElementById('aiErrorText');
    if (errorBanner && errorText) {
        errorText.textContent = msg;
        errorBanner.classList.remove('hidden');
        setTimeout(() => {
            errorBanner.classList.add('hidden');
        }, 8000);
    }
}

function renderAIAnalysisResult(result) {
    if (!result) return;

    // Show result container, hide empty placeholder
    const emptyPlaceholder = document.getElementById('aiEmptyState');
    const resultsContainer = document.getElementById('aiResultsContainer');
    if (emptyPlaceholder) emptyPlaceholder.classList.add('hidden');
    if (resultsContainer) resultsContainer.classList.remove('hidden');

    // 1. Header Badges & Freshness
    const symEl = document.getElementById('aiResSymbol');
    const tfEl = document.getElementById('aiResTimeframe');
    const biasBadge = document.getElementById('aiResBiasBadge');
    const modelBadge = document.getElementById('aiResModel');
    const cachedBadge = document.getElementById('aiResCachedBadge');
    const latencyEl = document.getElementById('aiResLatency');
    const timeEl = document.getElementById('aiResTime');
    const recActionBadge = document.getElementById('aiResRecAction');

    if (symEl) symEl.textContent = result.symbol;
    if (tfEl) tfEl.textContent = (result.timeframe || '1h').toUpperCase();
    if (biasBadge) {
        biasBadge.textContent = (result.primary_bias || 'NEUTRAL').replace('_', ' ');
        biasBadge.className = getBiasBadgeClass(result.primary_bias);
    }
    const modelStr = result.model_id || result.model_used || 'Groq Reasoner';
    if (modelBadge) {
        modelBadge.textContent = `${modelStr}`;
        if (modelStr.includes('Fallback') || modelStr.includes('rule_based')) {
            modelBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-amber-950 text-amber-300 border border-amber-800';
        } else {
            modelBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-purple-950 text-purple-300 border border-purple-800';
        }
    }
    if (cachedBadge) {
        cachedBadge.classList.toggle('hidden', !result.is_cached);
    }
    if (latencyEl) {
        latencyEl.textContent = result.data_age_sec !== undefined ? `${Math.round(result.data_age_sec)}s age` : '--';
    }
    if (timeEl) {
        const d = new Date(result.timestamp_ms);
        timeEl.textContent = d.toLocaleTimeString() + ' ' + d.toLocaleDateString();
    }
    const recStr = result.recommendation || result.recommended_action || 'STAND_ASIDE';
    if (recActionBadge) {
        recActionBadge.textContent = recStr.replace(/_/g, ' ');
        recActionBadge.className = getActionBadgeClass(recStr);
    }

    // 2. Confluence Score & Uncertainty Gauge
    const confVal = document.getElementById('aiResConfluenceVal');
    const confBar = document.getElementById('aiResConfluenceBar');
    const uncertVal = document.getElementById('aiResUncertaintyVal');
    const uncertBar = document.getElementById('aiResUncertaintyBar');

    const confluence = Math.round(result.confidence_score ?? result.confluence_score ?? 50);
    const uncertainty = Math.round(result.uncertainty_score ?? 30);

    if (confVal) confVal.textContent = `${confluence}/100`;
    if (confBar) {
        confBar.style.width = `${confluence}%`;
        confBar.className = confluence >= 65 ? 'h-full bg-emerald-500 rounded-full' : (confluence >= 45 ? 'h-full bg-amber-500 rounded-full' : 'h-full bg-rose-500 rounded-full');
    }
    if (uncertVal) uncertVal.textContent = `${uncertainty}%`;
    if (uncertBar) {
        uncertBar.style.width = `${uncertainty}%`;
        uncertBar.className = uncertainty <= 35 ? 'h-full bg-emerald-500 rounded-full' : (uncertainty <= 65 ? 'h-full bg-amber-500 rounded-full' : 'h-full bg-rose-500 rounded-full');
    }

    // 3. Executive Summary
    const summaryEl = document.getElementById('aiResExecutiveSummary');
    if (summaryEl) {
        summaryEl.textContent = result.executive_summary || 'No executive summary available.';
    }

    // 4. Data Freshness Warning
    const freshnessBanner = document.getElementById('aiFreshnessWarningBanner');
    const freshnessText = document.getElementById('aiFreshnessWarningText');
    if (freshnessBanner && freshnessText) {
        if (result.is_stale) {
            freshnessText.textContent = `Warning: Market data age is ${Math.round(result.data_age_sec / 60)} minutes old. Exercise caution on fast execution.`;
            freshnessBanner.classList.remove('hidden');
        } else {
            freshnessBanner.classList.add('hidden');
        }
    }

    // 5. Scenarios (LONG vs SHORT)
    renderScenarioCard('long', result.long_scenario);
    renderScenarioCard('short', result.short_scenario);

    // 6. Execution Plan
    renderExecutionPlan(result.recommended_plan || result.execution_plan);

    // 7. Market Snapshot
    renderMarketSnapshot(result.market_snapshot);

    // 8. Indonesian Narrative
    renderIndonesianExplanation(result.penjelasan_indonesia);
}

function renderScenarioCard(side, sc) {
    const pfx = side === 'long' ? 'aiLong' : 'aiShort';
    if (!sc) return;

    const biasBadge = document.getElementById(`${pfx}Bias`);
    const confEl = document.getElementById(`${pfx}Confluence`);
    const viabBadge = document.getElementById(`${pfx}Viability`);
    const invLevel = document.getElementById(`${pfx}Invalidation`);
    const invCond = document.getElementById(`${pfx}InvalidationCond`);
    const ratList = document.getElementById(`${pfx}RationaleList`);
    const confList = document.getElementById(`${pfx}ConfluencesList`);
    const riskList = document.getElementById(`${pfx}RisksList`);

    if (biasBadge) {
        biasBadge.textContent = (sc.direction || sc.bias || (side === 'long' ? 'BULLISH' : 'BEARISH')).replace('_', ' ');
        biasBadge.className = getBiasBadgeClass(sc.direction || sc.bias);
    }
    if (confEl) {
        confEl.textContent = `${Math.round(sc.confluence_score || 0)}% Confluence`;
    }
    if (viabBadge) {
        viabBadge.textContent = sc.probability_rating || sc.viability || 'MODERATE';
        viabBadge.className = getViabilityClass(sc.probability_rating || sc.viability);
    }
    if (invLevel) {
        const inv = sc.invalidation_level || sc.suggested_stop_loss;
        invLevel.textContent = inv ? `$${formatNumber(inv)}` : 'Structural Level';
    }
    if (invCond) {
        invCond.textContent = sc.invalidation_condition || 'Price closure past invalidation boundary.';
    }

    const rationale = sc.key_catalysts || sc.rationale || [];
    if (ratList) {
        ratList.innerHTML = rationale.map(r => `<li class="flex items-start gap-1.5"><span class="text-indigo-400 mt-0.5">&bull;</span><span>${escapeHtml(r)}</span></li>`).join('');
    }
    const confluences = sc.key_confluences || [sc.key_entry_zone ? `Entry Zone: ${sc.key_entry_zone}` : 'Confluence alignment', `Net R:R: ${(sc.net_reward_to_risk || 0).toFixed(2)}R`];
    if (confList) {
        confList.innerHTML = confluences.map(c => `<li class="flex items-start gap-1.5"><span class="text-emerald-400 mt-0.5">&check;</span><span>${escapeHtml(c)}</span></li>`).join('');
    }
    const risks = sc.risk_factors || [];
    if (riskList) {
        riskList.innerHTML = risks.map(k => `<li class="flex items-start gap-1.5"><span class="text-rose-400 mt-0.5">&excl;</span><span>${escapeHtml(k)}</span></li>`).join('');
    }
}

function renderExecutionPlan(plan) {
    if (!plan) return;

    const dirBadge = document.getElementById('aiPlanDirection');
    const l1Px = document.getElementById('aiPlanLimit1');
    const l2Px = document.getElementById('aiPlanLimit2');
    const avgEntry = document.getElementById('aiPlanAvgEntry');
    const slPx = document.getElementById('aiPlanSL');
    const tp1Px = document.getElementById('aiPlanTP1');
    const tp2Px = document.getElementById('aiPlanTP2');
    const posNotional = document.getElementById('aiPlanNotional');
    const posMargin = document.getElementById('aiPlanMargin');
    const posContracts = document.getElementById('aiPlanContracts');
    const feesEl = document.getElementById('aiPlanFees');
    const bePx = document.getElementById('aiPlanBreakEven');
    const netRREl = document.getElementById('aiPlanNetRR');
    const grossRREl = document.getElementById('aiPlanGrossRR');
    const minNotionalBadge = document.getElementById('aiPlanMinNotionalBadge');

    const dir = plan.direction || plan.target_direction || 'LONG';
    if (dirBadge) {
        dirBadge.textContent = dir;
        dirBadge.className = dir === 'LONG'
            ? 'px-2 py-0.5 rounded text-xs font-bold bg-emerald-950 text-emerald-300 border border-emerald-700'
            : (dir === 'SHORT'
                ? 'px-2 py-0.5 rounded text-xs font-bold bg-rose-950 text-rose-300 border border-rose-700'
                : 'px-2 py-0.5 rounded text-xs font-bold bg-slate-800 text-slate-400 border border-slate-700');
    }

    const ladder = plan.entry_ladder || [];
    if (l1Px && ladder[0]) l1Px.textContent = `$${formatNumber(ladder[0].price)}`;
    if (l2Px && ladder[1]) l2Px.textContent = `$${formatNumber(ladder[1].price)}`;
    if (avgEntry) avgEntry.textContent = `$${formatNumber(plan.primary_entry || plan.weighted_average_entry)}`;
    if (slPx) slPx.textContent = `$${formatNumber(plan.stop_loss)}`;
    if (tp1Px) tp1Px.textContent = `$${formatNumber(plan.take_profit_1)}`;
    if (tp2Px) tp2Px.textContent = plan.take_profit_2 ? `$${formatNumber(plan.take_profit_2)}` : '--';

    const notional = plan.position_notional_usdt ?? plan.total_notional_usd ?? 0.0;
    const margin = plan.required_margin_usdt ?? plan.estimated_margin_usd ?? 0.0;
    const qty = plan.suggested_quantity ?? plan.total_position_contracts ?? 0.0;
    const costs = plan.total_estimated_costs_usdt ?? (plan.estimated_roundtrip_fees_usd + plan.estimated_slippage_usd) ?? 0.0;

    if (posNotional) posNotional.textContent = `$${notional.toFixed(2)}`;
    if (posMargin) posMargin.textContent = `$${margin.toFixed(2)}`;
    if (posContracts) posContracts.textContent = `${qty} units`;
    if (feesEl) feesEl.textContent = `$${costs.toFixed(3)}`;
    if (bePx) bePx.textContent = `$${formatNumber(plan.break_even_price)}`;

    const netRR = plan.net_reward_to_risk ?? plan.net_reward_risk_ratio ?? 0.0;
    if (netRREl) {
        netRREl.textContent = `${netRR.toFixed(2)}R`;
        netRREl.className = netRR >= 1.5 ? 'text-lg font-bold text-emerald-400 font-mono' : 'text-lg font-bold text-amber-400 font-mono';
    }
    if (grossRREl) {
        grossRREl.textContent = `Gross: ${(plan.gross_reward_to_risk || (netRR * 1.1)).toFixed(2)}R`;
    }

    const isNotionalOk = plan.is_notional_compliant ?? plan.meets_min_notional ?? (notional >= 5.0);
    if (minNotionalBadge) {
        if (isNotionalOk) {
            minNotionalBadge.textContent = 'ASTERDEX $5 MIN MET';
            minNotionalBadge.className = 'px-1.5 py-0.2 rounded text-[10px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800';
        } else {
            minNotionalBadge.textContent = 'BELOW $5 MIN NOTIONAL';
            minNotionalBadge.className = 'px-1.5 py-0.2 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800';
        }
    }
}

function renderMarketSnapshot(snap) {
    if (!snap) return;

    const curPx = document.getElementById('aiSnapCurrentPx');
    const trend24h = document.getElementById('aiSnapTrend24h');
    const swHigh = document.getElementById('aiSnapSwingHigh');
    const swLow = document.getElementById('aiSnapSwingLow');
    const demZone = document.getElementById('aiSnapDemandZone');
    const supZone = document.getElementById('aiSnapSupplyZone');
    const sweepBadge = document.getElementById('aiSnapSweepBadge');
    const atrEl = document.getElementById('aiSnapATR');
    const btcPx = document.getElementById('aiSnapBtcPx');
    const btcTrend = document.getElementById('aiSnapBtcTrend');
    const rsBtc = document.getElementById('aiSnapRsBtc');
    const obBidDepth = document.getElementById('aiSnapObBids');
    const obAskDepth = document.getElementById('aiSnapObAsks');
    const obImbalance = document.getElementById('aiSnapObImbalance');

    if (curPx) curPx.textContent = `$${formatNumber(snap.current_price)}`;
    if (trend24h) {
        const spread = snap.spread_bps !== undefined ? `${snap.spread_bps.toFixed(1)} bps spread` : '';
        trend24h.textContent = `${spread}`;
        trend24h.className = 'text-xs font-mono text-slate-400';
    }
    if (swHigh) swHigh.textContent = snap.swing_high ? `$${formatNumber(snap.swing_high)}` : '--';
    if (swLow) swLow.textContent = snap.swing_low ? `$${formatNumber(snap.swing_low)}` : '--';

    if (demZone) {
        const dLower = snap.demand_zone_lower;
        const dUpper = snap.demand_zone_upper;
        demZone.textContent = (dLower && dUpper) ? `$${formatNumber(dLower)} – $${formatNumber(dUpper)}` : 'None detected';
    }
    if (supZone) {
        const sLower = snap.supply_zone_lower;
        const sUpper = snap.supply_zone_upper;
        supZone.textContent = (sLower && sUpper) ? `$${formatNumber(sLower)} – $${formatNumber(sUpper)}` : 'None detected';
    }

    if (sweepBadge) {
        if (snap.recent_liquidity_sweep) {
            sweepBadge.textContent = 'LIQUIDITY SWEEP DETECTED';
            sweepBadge.className = 'px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-700';
        } else {
            sweepBadge.textContent = 'NO RECENT SWEEP';
            sweepBadge.className = 'px-2 py-0.5 rounded text-[11px] font-mono text-slate-400 bg-slate-800 border border-slate-700';
        }
    }

    if (atrEl) atrEl.textContent = `$${formatNumber(snap.atr_14)} (${(snap.atr_pct || 0).toFixed(2)}%)`;
    if (btcPx) btcPx.textContent = `$${Number(snap.btc_price).toLocaleString('en-US', { maximumFractionDigits: 1 })}`;
    if (btcTrend) {
        btcTrend.textContent = snap.btc_zone_status || 'INSIDE_ZONE';
        btcTrend.className = snap.btc_zone_status === 'INSIDE_ZONE' ? 'text-xs font-mono text-emerald-400' : 'text-xs font-mono text-amber-400';
    }
    if (rsBtc) {
        const rs = snap.relative_strength_vs_btc_24h || 0.0;
        rsBtc.textContent = `${rs >= 0 ? '+' : ''}${rs.toFixed(2)}% vs BTC`;
        rsBtc.className = rs >= 0 ? 'text-xs font-mono text-emerald-400 font-semibold' : 'text-xs font-mono text-rose-400 font-semibold';
    }

    if (obBidDepth) obBidDepth.textContent = `$${Math.round(snap.orderbook_bid_depth_25bps).toLocaleString()}`;
    if (obAskDepth) obAskDepth.textContent = `$${Math.round(snap.orderbook_ask_depth_25bps).toLocaleString()}`;
    if (obImbalance) {
        const imb = snap.orderbook_imbalance_25bps || 0.0;
        obImbalance.textContent = `${imb >= 0 ? '+' : ''}${imb.toFixed(1)}%`;
        obImbalance.className = imb >= 10 ? 'text-sm font-bold font-mono text-emerald-400' : (imb <= -10 ? 'text-sm font-bold font-mono text-rose-400' : 'text-sm font-bold font-mono text-slate-300');
    }
}

function renderIndonesianExplanation(exp) {
    if (!exp) return;

    const ringkasanEl = document.getElementById('aiIdRingkasan');
    const strukturEl = document.getElementById('aiIdStruktur');
    const obEl = document.getElementById('aiIdOrderBook');
    const rekomendasiEl = document.getElementById('aiIdRekomendasi');
    const invalidasiEl = document.getElementById('aiIdInvalidasi');

    if (ringkasanEl) ringkasanEl.textContent = exp.ringkasan_struktur || exp.ringkasan_eksekutif || '--';
    if (strukturEl) strukturEl.textContent = exp.analisis_skenario_long || exp.analisis_struktur_pasar || '--';
    if (obEl) obEl.textContent = exp.analisis_skenario_short || exp.analisis_order_book || '--';
    if (rekomendasiEl) rekomendasiEl.textContent = exp.panduan_entry_dan_risiko || exp.rekomendasi_eksekusi || '--';
    if (invalidasiEl) invalidasiEl.textContent = exp.kondisi_pembatalan || '--';
}

function renderAIAnalystHistoryTable() {
    const tbody = document.getElementById('aiHistoryTableBody');
    const emptyRow = document.getElementById('aiHistoryEmptyRow');
    const pEl = document.getElementById('aiHistoryPaginationContainer');
    if (!tbody) return;

    if (!aiAnalystHistory || aiAnalystHistory.length === 0) {
        if (emptyRow) emptyRow.classList.remove('hidden');
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-6 text-slate-500 font-mono text-xs">No analysis queries recorded yet.</td></tr>';
        if (pEl) pEl.innerHTML = '';
        return;
    }

    if (emptyRow) emptyRow.classList.add('hidden');

    const paged = (typeof paginateList === 'function')
        ? paginateList('ai_history', aiAnalystHistory)
        : aiAnalystHistory;

    tbody.innerHTML = paged.map(rec => {
        const d = new Date(rec.timestamp_ms);
        const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const biasCls = getBiasBadgeClass(rec.primary_bias);
        const recId = rec.analysis_id || '';
        const plan = rec.recommended_plan || rec.execution_plan;
        const netRRVal = plan ? (plan.net_reward_to_risk ?? plan.net_reward_risk_ratio) : null;
        const netRR = netRRVal !== null ? `${netRRVal.toFixed(2)}R` : '--';
        const isCachedBadge = rec.is_cached
            ? '<span class="px-1.5 py-0.2 rounded text-[9px] bg-slate-800 text-slate-400 font-mono">CACHED</span>'
            : '<span class="px-1.5 py-0.2 rounded text-[9px] bg-purple-950 text-purple-300 font-mono">LIVE</span>';

        const conf = Math.round(rec.confidence_score ?? rec.confluence_score ?? 0);

        return `
            <tr class="hover:bg-slate-800/40 border-b border-slate-800/50 transition font-mono text-xs">
                <td class="py-2.5 px-3 font-semibold text-white">${rec.symbol}</td>
                <td class="py-2.5 px-3 text-slate-300 uppercase">${rec.timeframe}</td>
                <td class="py-2.5 px-3"><span class="${biasCls}">${(rec.primary_bias || '').replace('_', ' ')}</span></td>
                <td class="py-2.5 px-3 text-slate-200 font-semibold">${conf}/100</td>
                <td class="py-2.5 px-3 text-emerald-400 font-bold">${netRR}</td>
                <td class="py-2.5 px-3">${isCachedBadge}</td>
                <td class="py-2.5 px-3 text-slate-400">${timeStr}</td>
                <td class="py-2.5 px-3 text-right">
                    <button type="button" onclick="loadHistoricalAIAnalysis('${recId}')" class="px-2.5 py-1 text-[11px] rounded bg-slate-800 hover:bg-slate-700 text-indigo-300 border border-slate-700 transition">
                        View
                    </button>
                </td>
            </tr>
        `;
    }).join('');

    if (typeof renderPaginationControls === 'function') {
        renderPaginationControls('ai_history', 'aiHistoryPaginationContainer', () => {
            renderAIAnalystHistoryTable();
        });
    }
}

async function loadHistoricalAIAnalysis(analysisId) {
    if (!analysisId) return;
    try {
        const res = await fetch(`/api/ai_analyst/${analysisId}`);
        if (!res.ok) return;
        const data = await res.json();
        const record = data.result || data.record;
        if (data.status === 'SUCCESS' && record) {
            aiAnalystCurrentResult = record;
            aiAnalystSelectedSymbol = record.symbol;
            aiAnalystSelectedTimeframe = record.timeframe;
            selectAISymbol(aiAnalystSelectedSymbol);
            selectAITimeframe(aiAnalystSelectedTimeframe);
            renderAIAnalysisResult(record);
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    } catch (e) {
        console.error('Failed to load past AI analysis:', e);
    }
}

// Config Modal Handlers
function openAIAnalystConfigModal() {
    const modal = document.getElementById('aiAnalystConfigModal');
    if (!modal) return;
    if (aiAnalystConfig) {
        const modelInput = document.getElementById('aiConfigModelInput');
        const ttlInput = document.getElementById('aiConfigTTLInput');
        if (modelInput) modelInput.value = aiAnalystConfig.groq_model_id || aiAnalystConfig.model_id || 'qwen/qwen3.8-27b';
        if (ttlInput) ttlInput.value = aiAnalystConfig.cache_ttl_sec || 60;
    }
    modal.classList.remove('hidden');
}

function closeAIAnalystConfigModal() {
    const modal = document.getElementById('aiAnalystConfigModal');
    if (modal) modal.classList.add('hidden');
}

async function saveAIAnalystConfig() {
    const modelInput = document.getElementById('aiConfigModelInput');
    const ttlInput = document.getElementById('aiConfigTTLInput');

    const payload = {
        groq_model_id: modelInput ? modelInput.value.trim() : 'qwen/qwen3.8-27b',
        default_risk_budget_usdt: aiAnalystRiskBudget
    };

    try {
        const res = await fetch('/api/ai_analyst/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            const data = await res.json();
            const updated = data.config || data.updated_config;
            if (updated) {
                aiAnalystConfig = updated;
                renderAIAnalystConfigHeader();
                closeAIAnalystConfigModal();
            }
        } else {
            const err = await res.json().catch(() => ({}));
            alert(`Error updating config: ${err.detail || 'Access denied or server error'}`);
        }
    } catch (e) {
        console.error('Failed to save config:', e);
    }
}

// Helpers
function formatNumber(num) {
    if (num === null || num === undefined || isNaN(num)) return '--';
    const n = Number(num);
    if (n >= 1000) return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (n >= 1) return n.toFixed(4);
    return n.toFixed(6);
}

function getBiasBadgeClass(bias) {
    switch (bias) {
        case 'BULLISH':
        case 'STRONG_LONG':
            return 'px-2.5 py-0.5 rounded text-[11px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-700';
        case 'MODERATE_LONG':
            return 'px-2.5 py-0.5 rounded text-[11px] font-bold bg-emerald-900/50 text-emerald-300 border border-emerald-800';
        case 'BEARISH':
        case 'STRONG_SHORT':
            return 'px-2.5 py-0.5 rounded text-[11px] font-bold bg-rose-950 text-rose-300 border border-rose-700';
        case 'MODERATE_SHORT':
            return 'px-2.5 py-0.5 rounded text-[11px] font-bold bg-rose-900/50 text-rose-300 border border-rose-800';
        case 'CHOPPY':
        case 'NEUTRAL':
            return 'px-2.5 py-0.5 rounded text-[11px] font-bold bg-slate-800 text-slate-300 border border-slate-700';
        default:
            return 'px-2.5 py-0.5 rounded text-[11px] font-bold bg-slate-800 text-slate-400 border border-slate-700';
    }
}

function getActionBadgeClass(action) {
    switch (action) {
        case 'LONG':
        case 'PREPARE_LIMIT_ENTRY':
            return 'px-2 py-0.5 rounded text-[11px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-700';
        case 'SHORT':
            return 'px-2 py-0.5 rounded text-[11px] font-bold bg-rose-950 text-rose-300 border border-rose-700';
        case 'WAIT_FOR_CONFIRMATION':
            return 'px-2 py-0.5 rounded text-[11px] font-bold bg-amber-950 text-amber-300 border border-amber-700';
        case 'NEUTRAL_STAND_ASIDE':
        case 'STAND_ASIDE':
        default:
            return 'px-2 py-0.5 rounded text-[11px] font-bold bg-slate-800 text-slate-400 border border-slate-700';
    }
}

function getViabilityClass(viability) {
    switch (viability) {
        case 'HIGH':
            return 'px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800';
        case 'MODERATE':
        case 'MEDIUM':
            return 'px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-300 border border-amber-800';
        case 'LOW':
            return 'px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700';
        case 'INVALID':
        default:
            return 'px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800';
    }
}
