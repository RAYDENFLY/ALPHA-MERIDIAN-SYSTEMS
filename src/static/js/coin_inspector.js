// =========================================================================
// COIN INSPECTOR & AI CONTEXT EXPORTER (INTERACTIVE QUANT SNAPSHOT VIEWER)
// =========================================================================

let currentInspectorSymbol = null;
let allInspectorSymbols = [];
let currentInspectorData = null;

function loadInspectorTab() {
    if (allInspectorSymbols.length === 0) {
        fetchInspectorSymbols();
    }
    fetchLiveSessionState();
}

async function fetchInspectorSymbols() {
    try {
        const resp = await fetch('/api/inspector/symbols');
        if (!resp.ok) throw new Error('Failed to fetch symbols');
        const data = await resp.json();
        allInspectorSymbols = data.items || [];
        renderInspectorPopularChips(data.popular_chips || []);
    } catch (err) {
        console.error('Inspector: symbols load error:', err);
    }
}

function renderInspectorPopularChips(popular) {
    const container = document.getElementById('inspectorPopularChips');
    if (!container) return;
    if (!popular || popular.length === 0) {
        popular = ['SOLUSDT', 'ETHUSDT', 'ZECUSDT', 'XMRUSDT', 'DOGEUSDT', 'ASTERUSDT', 'LITUSDT'];
    }

    container.innerHTML = popular.map(sym => `
        <button type="button" onclick="selectInspectorSymbol('${sym}')"
            class="px-2.5 py-1 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-cyan-950/80 hover:text-cyan-300 hover:border-cyan-500/50 text-slate-300 border border-slate-700/80 transition shadow-sm">
            ${sym}
        </button>
    `).join('');
}

function onInspectorSearchInput(query) {
    const dropdown = document.getElementById('inspectorDropdownMenu');
    if (!dropdown) return;
    const q = (query || '').trim().toUpperCase();

    if (!q) {
        dropdown.classList.add('hidden');
        return;
    }

    const filtered = allInspectorSymbols.filter(item => item.symbol.includes(q)).slice(0, 15);
    if (filtered.length === 0) {
        dropdown.innerHTML = '<div class="px-3 py-2 text-xs text-slate-500">No matching coins</div>';
        dropdown.classList.remove('hidden');
        return;
    }

    dropdown.innerHTML = filtered.map(item => `
        <div onclick="selectInspectorSymbol('${item.symbol}')"
            class="px-3 py-2 text-xs text-slate-200 hover:bg-cyan-950/70 hover:text-cyan-300 cursor-pointer flex items-center justify-between transition">
            <span class="font-bold font-mono">${item.symbol}</span>
            <span class="text-[10px] text-slate-400 font-mono">Vol: $${formatInspectorKMB(item.volume_24h_usdt)}</span>
        </div>
    `).join('');
    dropdown.classList.remove('hidden');
}

function selectInspectorSymbol(symbol) {
    currentInspectorSymbol = symbol.toUpperCase();
    const input = document.getElementById('inspectorSearchInput');
    if (input) input.value = currentInspectorSymbol;
    const dropdown = document.getElementById('inspectorDropdownMenu');
    if (dropdown) dropdown.classList.add('hidden');
    fetchCoinInspection();
}

async function fetchCoinInspection() {
    const input = document.getElementById('inspectorSearchInput');
    const symbol = (input?.value || currentInspectorSymbol || '').trim().toUpperCase();
    if (!symbol) {
        if (input) {
            input.focus();
            input.classList.add('border-rose-500');
            setTimeout(() => input.classList.remove('border-rose-500'), 1500);
        }
        return;
    }

    currentInspectorSymbol = symbol;
    const balInput = document.getElementById('inspectorBalanceInput');
    const balance = parseFloat(balInput?.value || '15.72') || 15.72;

    const emptyState = document.getElementById('inspectorEmptyState');
    const loadingState = document.getElementById('inspectorLoadingState');
    const resultContainer = document.getElementById('inspectorResultContainer');
    const loadingSymbol = document.getElementById('inspectorLoadingSymbol');

    if (emptyState) emptyState.classList.add('hidden');
    if (resultContainer) resultContainer.classList.add('hidden');
    if (loadingState) {
        if (loadingSymbol) loadingSymbol.textContent = symbol;
        loadingState.classList.remove('hidden');
    }

    try {
        const resp = await fetch(`/api/inspector/coin/${encodeURIComponent(symbol)}?account_balance=${balance}`);
        if (!resp.ok) {
            const errData = await resp.json().catch(() => ({}));
            throw new Error(errData.detail || `Server returned HTTP ${resp.status}`);
        }
        const data = await resp.json();
        currentInspectorData = data;
        renderInspectorSnapshot(data);
    } catch (err) {
        if (loadingState) loadingState.classList.add('hidden');
        if (emptyState) {
            emptyState.classList.remove('hidden');
            emptyState.innerHTML = `
                <div class="text-rose-400 text-sm font-semibold mb-2"><i class="fa-solid fa-triangle-exclamation mr-1"></i> Failed to inspect ${symbol}</div>
                <div class="text-slate-400 text-xs">${err.message || 'Please verify the symbol exists on Aster DEX.'}</div>
                <button type="button" onclick="fetchCoinInspection()" class="mt-3 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs rounded border border-slate-700">Retry</button>
            `;
        }
    }
}

function renderInspectorSnapshot(data) {
    const loadingState = document.getElementById('inspectorLoadingState');
    const resultContainer = document.getElementById('inspectorResultContainer');
    if (loadingState) loadingState.classList.add('hidden');
    if (!resultContainer) return;

    resultContainer.classList.remove('hidden');

    // 1. Mini Cards
    const meta = data.meta || {};
    const compass = data.market_compass || {};
    const mtf = data.multi_timeframe_summary || {};
    const smc = data.smc_and_liquidity_structure?.model_a_smc_context || {};
    const modelB = data.smc_and_liquidity_structure?.model_b_liquidity_context || {};
    const l2 = data.orderbook_l2_microstructure || {};

    // Mark Price Card
    const markEl = document.getElementById('inspectorCardMarkPrice');
    if (markEl) markEl.textContent = `$${meta.mark_price}`;
    const chgEl = document.getElementById('inspectorCardChange24h');
    if (chgEl) {
        const chg = meta.change_24h_pct || 0.0;
        chgEl.textContent = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
        chgEl.className = `text-xs font-semibold ${chg >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
    }
    const fundingEl = document.getElementById('inspectorCardFunding');
    if (fundingEl) fundingEl.textContent = `Funding: ${meta.funding_rate_pct}%`;

    // Trend Card
    const trend1h = mtf['1h']?.latest_indicators?.trend_state || 'NEUTRAL';
    const trend4h = mtf['4h']?.latest_indicators?.trend_state || 'NEUTRAL';
    const trendEl = document.getElementById('inspectorCardTrend');
    if (trendEl) {
        trendEl.textContent = `1H: ${trend1h} | 4H: ${trend4h}`;
        trendEl.className = `text-sm font-bold font-mono ${trend1h === 'BULL' ? 'text-emerald-400' : (trend1h === 'BEAR' ? 'text-rose-400' : 'text-slate-300')}`;
    }
    const compassEl = document.getElementById('inspectorCardCompass');
    if (compassEl) {
        compassEl.textContent = `BTC: ${compass.btc?.alignment || 'CHOP'} • ${compass.market_regime || 'NEUTRAL'}`;
    }

    // 72H Dealing Range Card
    const dr = smc.dealing_range_72h || {};
    const posPct = dr.current_position_pct ?? 50.0;
    const zone = dr.zone_status || 'EQUILIBRIUM';
    const drEl = document.getElementById('inspectorCardDealingRange');
    if (drEl) drEl.textContent = `${posPct}% (${zone})`;
    const drBarEl = document.getElementById('inspectorCardDealingRangeBar');
    if (drBarEl) drBarEl.style.width = `${Math.min(100, Math.max(0, posPct))}%`;
    const drSpanEl = document.getElementById('inspectorCardDealingRangeSpan');
    if (drSpanEl) drSpanEl.textContent = `Span: $${dr.low} - $${dr.high}`;

    // L2 Imbalance Card
    const imbRatio = l2.orderbook_imbalance_ratio ?? 0.5;
    const imbPct = (imbRatio * 100).toFixed(1);
    const imbEl = document.getElementById('inspectorCardImbalance');
    if (imbEl) imbEl.textContent = `${imbPct}% Bids`;
    const l2GateEl = document.getElementById('inspectorCardL2Gate');
    if (l2GateEl) {
        l2GateEl.textContent = l2.l2_gate_passed ? 'L2 Gate: PASSED (≥$5k)' : 'L2 Gate: LOW DEPTH';
        l2GateEl.className = `text-[10px] font-semibold px-1.5 py-0.5 rounded border ${l2.l2_gate_passed ? 'bg-emerald-950/70 border-emerald-800 text-emerald-300' : 'bg-amber-950/70 border-amber-800 text-amber-300'}`;
    }
    const depthEl = document.getElementById('inspectorCardDepth');
    if (depthEl) depthEl.textContent = `Bids: $${formatInspectorKMB(l2.bid_depth_top20_usdt)} | Asks: $${formatInspectorKMB(l2.ask_depth_top20_usdt)}`;

    // 2. Syntax-Highlighted JSON Viewer
    const jsonStr = JSON.stringify(data, null, 2);
    const codeEl = document.getElementById('inspectorJsonCode');
    if (codeEl) {
        codeEl.innerHTML = syntaxHighlightJson(jsonStr);
    }

    // 3. Token & Size Badge
    const tokenBadge = document.getElementById('inspectorTokenBadge');
    if (tokenBadge) {
        const tokenEst = Math.round(jsonStr.length / 4);
        const kbSize = (jsonStr.length / 1024).toFixed(1);
        tokenBadge.textContent = `~${tokenEst.toLocaleString()} Tokens • ${kbSize} KB`;
    }
}

function copyInspectorJson() {
    if (!currentInspectorData) return;
    const jsonStr = JSON.stringify(currentInspectorData, null, 2);
    navigator.clipboard.writeText(jsonStr).then(() => {
        const btn = document.getElementById('copyJsonBtn');
        if (!btn) return;
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-check text-emerald-400"></i> Copied JSON!';
        btn.classList.add('bg-emerald-600', 'hover:bg-emerald-500');
        setTimeout(() => {
            btn.innerHTML = originalHtml;
            btn.classList.remove('bg-emerald-600', 'hover:bg-emerald-500');
        }, 2200);
    }).catch(err => {
        console.error('Failed to copy to clipboard:', err);
    });
}

function copyInspectorPrompt() {
    if (!currentInspectorData) return;
    const sym = currentInspectorData.meta?.symbol || 'COIN';
    const jsonStr = JSON.stringify(currentInspectorData, null, 2);
    const prompt = `You are an expert quantitative crypto trader and SMC/order-book specialist. Analyze the following real-time JSON snapshot for ${sym} and provide:
1. Executive Bias & Market Structure Analysis (SMC 72h dealing range, 15m/1h/4h MTF indicators, and BTC compass alignment).
2. High-Probability Execution Trigger (Model A SMC Discount Rejection or Model B Liquidity Sweep-Reclaim).
3. Exact Invalidation / Stop-Loss & Target levels.
4. Micro-Sizing Recommendation based on the pre-calculated $15.72 account presets.

${jsonStr}`;

    navigator.clipboard.writeText(prompt).then(() => {
        const btn = document.getElementById('copyPromptBtn');
        if (!btn) return;
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-check text-emerald-400"></i> Copied Prompt + JSON!';
        btn.classList.add('bg-emerald-600', 'hover:bg-emerald-500');
        setTimeout(() => {
            btn.innerHTML = originalHtml;
            btn.classList.remove('bg-emerald-600', 'hover:bg-emerald-500');
        }, 2200);
    }).catch(err => {
        console.error('Failed to copy prompt to clipboard:', err);
    });
}

function formatInspectorKMB(val) {
    if (!val || isNaN(val)) return '0';
    const num = Math.abs(val);
    if (num >= 1_000_000) return (num / 1_000_000).toFixed(1) + 'M';
    if (num >= 1_000) return (num / 1_000).toFixed(1) + 'K';
    return num.toFixed(0);
}

function syntaxHighlightJson(json) {
    if (typeof json !== 'string') {
        json = JSON.stringify(json, null, 2);
    }
    json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
        let cls = 'text-amber-400';
        if (/^"/.test(match)) {
            if (/:$/.test(match)) {
                cls = 'text-cyan-400 font-semibold';
            } else {
                cls = 'text-emerald-300';
            }
        } else if (/true|false/.test(match)) {
            cls = 'text-purple-400 font-bold';
        } else if (/null/.test(match)) {
            cls = 'text-slate-500 italic';
        }
        return '<span class="' + cls + '">' + match + '</span>';
    });
}

// Close search dropdown on click outside
document.addEventListener('click', (event) => {
    const dropdown = document.getElementById('inspectorDropdownMenu');
    const input = document.getElementById('inspectorSearchInput');
    if (dropdown && input && !dropdown.contains(event.target) && event.target !== input) {
        dropdown.classList.add('hidden');
    }
});


// =========================================================================
// AI TRADE SETUP EXECUTOR (1-CLICK ASTER DEX LIMIT + BRACKET)
// =========================================================================

let currentAISetupVenue = 'PAPER';

function setAISetupVenue(venue) {
    currentAISetupVenue = (venue || 'PAPER').toUpperCase();
    const btnPaper = document.getElementById('aiVenueBtn-PAPER');
    const btnLive = document.getElementById('aiVenueBtn-LIVE');
    const execBtn = document.getElementById('executeAISetupBtn');
    const execText = document.getElementById('executeAISetupBtnText');

    if (currentAISetupVenue === 'LIVE') {
        if (btnPaper) {
            btnPaper.className = 'px-3 py-1 rounded-md font-bold text-xs transition text-slate-400 hover:text-white';
        }
        if (btnLive) {
            btnLive.className = 'px-3 py-1 rounded-md font-bold text-xs transition bg-amber-600 text-white shadow-sm';
        }
        if (execBtn) {
            execBtn.className = 'px-6 py-2.5 bg-gradient-to-r from-amber-600 to-rose-600 hover:from-amber-500 hover:to-rose-500 text-white font-bold rounded-lg text-xs shadow-lg shadow-amber-950/40 transition flex items-center justify-center gap-2';
        }
        if (execText) {
            execText.textContent = '1-Click Execute AI Trade Setup (LIVE Aster DEX)';
        }
    } else {
        if (btnPaper) {
            btnPaper.className = 'px-3 py-1 rounded-md font-bold text-xs transition bg-emerald-600 text-white shadow-sm';
        }
        if (btnLive) {
            btnLive.className = 'px-3 py-1 rounded-md font-bold text-xs transition text-slate-400 hover:text-white';
        }
        if (execBtn) {
            execBtn.className = 'px-6 py-2.5 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-bold rounded-lg text-xs shadow-lg shadow-emerald-950/40 transition flex items-center justify-center gap-2';
        }
        if (execText) {
            execText.textContent = '1-Click Execute AI Trade Setup (PAPER)';
        }
    }

    // Update execution_venue in json textarea if valid
    const area = document.getElementById('aiSetupInput');
    if (area && area.value.trim()) {
        try {
            let cleaned = area.value.replace(/^```[a-zA-Z]*\n?/, '').replace(/\n?```$/, '').trim();
            cleaned = cleaned.replace(/,\s*([\]}])/g, '$1');
            const parsed = JSON.parse(cleaned);
            parsed.execution_venue = currentAISetupVenue;
            area.value = JSON.stringify(parsed, null, 2);
            onAISetupInput();
        } catch (e) {
            // ignore if incomplete
        }
    }
}

function onAISetupInput() {
    const area = document.getElementById('aiSetupInput');
    const raw = (area?.value || '').trim();
    const statusBadge = document.getElementById('aiSetupStatusBadge');
    const errBox = document.getElementById('aiSetupValidationError');
    const errMsg = document.getElementById('aiSetupValidationMsg');

    if (!raw) {
        if (statusBadge) statusBadge.innerHTML = '<span class="text-slate-500">Awaiting JSON input...</span>';
        if (errBox) errBox.classList.add('hidden');
        resetAISetupPreview();
        return;
    }

    try {
        let cleaned = raw.replace(/^```[a-zA-Z]*\n?/, '').replace(/\n?```$/, '').trim();
        cleaned = cleaned.replace(/,\s*([\]}])/g, '$1');
        const parsed = JSON.parse(cleaned);

        // Required field validation
        const missing = [];
        if (!parsed.symbol) missing.push('symbol');
        if (!parsed.direction) missing.push('direction');
        if (parsed.entry_price === undefined) missing.push('entry_price');
        if (parsed.stop_loss_price === undefined) missing.push('stop_loss_price');
        if (parsed.take_profit_price === undefined) missing.push('take_profit_price');

        if (missing.length > 0) {
            throw new Error(`Missing required fields: ${missing.join(', ')}`);
        }

        const sym = String(parsed.symbol).toUpperCase();
        const dir = String(parsed.direction).toUpperCase();
        const entry = parseFloat(parsed.entry_price) || 0.0;
        const sl = parseFloat(parsed.stop_loss_price) || 0.0;
        const tp = parseFloat(parsed.take_profit_price) || 0.0;
        const lev = parseInt(parsed.leverage || 10) || 10;
        let qty = parseFloat(parsed.qty || 0.0);

        if (entry <= 0 || sl <= 0 || tp <= 0) {
            throw new Error('entry_price, stop_loss_price, and take_profit_price must all be > 0.0');
        }

        const stopDist = Math.abs(entry - sl);
        const targetDist = Math.abs(tp - entry);
        const rr = stopDist > 0 ? (targetDist / stopDist).toFixed(2) : '0.00';

        // Geometric validation check
        if (dir === 'LONG' && !(sl < entry && entry < tp)) {
            throw new Error(`Invalid LONG geometry: SL ($${sl}) must be < Entry ($${entry}) < TP ($${tp})`);
        } else if (dir === 'SHORT' && !(sl > entry && entry > tp)) {
            throw new Error(`Invalid SHORT geometry: SL ($${sl}) must be > Entry ($${entry}) > TP ($${tp})`);
        }

        if (qty <= 0 && parsed.max_risk_usdt && stopDist > 0) {
            qty = parseFloat(parsed.max_risk_usdt) / stopDist;
        }

        const notional = qty * entry;
        const margin = lev > 0 ? notional / lev : 0.0;

        // Render preview fields
        const previewSymSide = document.getElementById('aiPreviewSymbolSide');
        if (previewSymSide) {
            previewSymSide.innerHTML = `${sym} <span class="${dir === 'LONG' ? 'text-emerald-400' : 'text-rose-400'}">${dir}</span>`;
        }

        const previewEntry = document.getElementById('aiPreviewEntry');
        if (previewEntry) previewEntry.textContent = `$${entry}`;

        const previewSL = document.getElementById('aiPreviewSL');
        if (previewSL) {
            const slPct = entry > 0 ? ((sl - entry) / entry * 100).toFixed(2) : '0.00';
            previewSL.textContent = `$${sl} (${slPct}%)`;
        }

        const previewTP = document.getElementById('aiPreviewTP');
        if (previewTP) {
            const tpPct = entry > 0 ? ((tp - entry) / entry * 100).toFixed(2) : '0.00';
            previewTP.textContent = `$${tp} (${tpPct >= 0 ? '+' : ''}${tpPct}%)`;
        }

        const previewSize = document.getElementById('aiPreviewSize');
        if (previewSize) previewSize.textContent = `${qty > 0 ? qty.toFixed(4) : '--'} ${sym.replace('USDT', '')}`;

        const previewNotional = document.getElementById('aiPreviewNotional');
        if (previewNotional) previewNotional.textContent = `$${notional.toFixed(2)}`;

        const previewMargin = document.getElementById('aiPreviewMargin');
        if (previewMargin) previewMargin.textContent = `$${margin.toFixed(2)} (${lev}x)`;

        const previewRR = document.getElementById('aiPreviewRR');
        if (previewRR) previewRR.textContent = `${rr} R`;

        const previewNotes = document.getElementById('aiPreviewNotes');
        if (previewNotes) previewNotes.textContent = parsed.notes ? `"${parsed.notes}"` : '"No hypothesis notes provided"';

        if (statusBadge) {
            statusBadge.innerHTML = '<span class="text-emerald-400 font-bold"><i class="fa-solid fa-circle-check mr-1"></i> Valid Setup</span>';
        }
        if (errBox) errBox.classList.add('hidden');

    } catch (err) {
        if (statusBadge) {
            statusBadge.innerHTML = '<span class="text-rose-400 font-bold"><i class="fa-solid fa-circle-xmark mr-1"></i> Invalid Setup</span>';
        }
        if (errBox && errMsg) {
            errMsg.textContent = err.message || 'JSON parse error';
            errBox.classList.remove('hidden');
        }
    }
}

function resetAISetupPreview() {
    const previewSymSide = document.getElementById('aiPreviewSymbolSide');
    if (previewSymSide) previewSymSide.textContent = '--';
    const previewEntry = document.getElementById('aiPreviewEntry');
    if (previewEntry) previewEntry.textContent = '$0.00';
    const previewSL = document.getElementById('aiPreviewSL');
    if (previewSL) previewSL.textContent = '$0.00';
    const previewTP = document.getElementById('aiPreviewTP');
    if (previewTP) previewTP.textContent = '$0.00';
    const previewSize = document.getElementById('aiPreviewSize');
    if (previewSize) previewSize.textContent = '--';
    const previewNotional = document.getElementById('aiPreviewNotional');
    if (previewNotional) previewNotional.textContent = '$0.00';
    const previewMargin = document.getElementById('aiPreviewMargin');
    if (previewMargin) previewMargin.textContent = '$0.00';
    const previewRR = document.getElementById('aiPreviewRR');
    if (previewRR) previewRR.textContent = '-- R';
    const previewNotes = document.getElementById('aiPreviewNotes');
    if (previewNotes) previewNotes.textContent = '"Paste JSON above to parse AI thesis & target levels"';
}

function loadAISetupFromPreset(direction) {
    if (!currentInspectorData) {
        loadAISetupSample();
        return;
    }

    const data = currentInspectorData;
    const sym = data.meta?.symbol || 'SOLUSDT';
    const presets = data.bot_signals_and_micro_sizing?.sizing?.presets?.fast_15m_standard_risk || {};
    const dirUpper = (direction || 'LONG').toUpperCase();
    const setup = dirUpper === 'LONG' ? presets.long_setup : presets.short_setup;

    if (!setup) {
        loadAISetupSample();
        return;
    }

    const jsonPayload = {
        symbol: sym,
        direction: dirUpper,
        order_type: 'LIMIT',
        entry_price: setup.entry_price,
        stop_loss_price: setup.stop_loss_price,
        take_profit_price: setup.take_profit_price,
        qty: setup.recommended_quantity,
        leverage: 10,
        margin_mode: 'ISOLATED',
        max_risk_usdt: setup.risk_usdt,
        execution_venue: currentAISetupVenue,
        notes: `${sym} 15m Fast Scalper ${dirUpper} preset loaded from Coin Inspector`
    };

    const area = document.getElementById('aiSetupInput');
    if (area) {
        area.value = JSON.stringify(jsonPayload, null, 2);
        onAISetupInput();
    }
}

function loadAISetupSample() {
    const sample = {
        symbol: currentInspectorSymbol || 'SOLUSDT',
        direction: 'LONG',
        order_type: 'LIMIT',
        entry_price: 100.55,
        stop_loss_price: 99.34,
        take_profit_price: 102.30,
        qty: 0.66,
        leverage: 10,
        margin_mode: 'ISOLATED',
        max_risk_usdt: 0.85,
        execution_venue: currentAISetupVenue,
        notes: `${currentInspectorSymbol || 'SOL'} 15m/1h oversold sweep-reclaim into 102.30 Bear FVG`
    };

    const area = document.getElementById('aiSetupInput');
    if (area) {
        area.value = JSON.stringify(sample, null, 2);
        onAISetupInput();
    }
}

function clearAISetup() {
    const area = document.getElementById('aiSetupInput');
    if (area) area.value = '';
    const feedback = document.getElementById('aiSetupExecutionFeedback');
    if (feedback) feedback.innerHTML = '';
    onAISetupInput();
}

async function executeAISetup() {
    const area = document.getElementById('aiSetupInput');
    const raw = (area?.value || '').trim();
    const feedback = document.getElementById('aiSetupExecutionFeedback');
    const btn = document.getElementById('executeAISetupBtn');

    if (!raw) {
        if (feedback) {
            feedback.innerHTML = '<span class="text-amber-400"><i class="fa-solid fa-triangle-exclamation mr-1"></i> Please paste an AI trade setup JSON first.</span>';
        }
        return;
    }

    let payload = null;
    try {
        let cleaned = raw.replace(/^```[a-zA-Z]*\n?/, '').replace(/\n?```$/, '').trim();
        cleaned = cleaned.replace(/,\s*([\]}])/g, '$1');
        payload = JSON.parse(cleaned);
        payload.execution_venue = currentAISetupVenue;
    } catch (err) {
        if (feedback) {
            feedback.innerHTML = `<span class="text-rose-400"><i class="fa-solid fa-circle-xmark mr-1"></i> Invalid JSON: ${err.message}</span>`;
        }
        return;
    }

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-circle-notch animate-spin mr-1"></i> Executing Setup...';
    }
    if (feedback) {
        feedback.innerHTML = '<span class="text-slate-400"><i class="fa-solid fa-arrows-rotate animate-spin mr-1"></i> Submitting order bracket to executor...</span>';
    }

    try {
        const resp = await fetch('/api/inspector/execute-setup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await resp.json();

        if (!resp.ok) {
            throw new Error(data.detail || data.message || `Server returned HTTP ${resp.status}`);
        }

        if (feedback) {
            feedback.innerHTML = `
                <div class="p-2.5 rounded-lg bg-emerald-950/80 border border-emerald-700 text-emerald-200 text-xs font-mono space-y-1">
                    <div class="flex items-center gap-2 font-bold text-emerald-300">
                        <i class="fa-solid fa-circle-check text-emerald-400"></i>
                        <span>${data.message || 'Setup Executed Successfully!'}</span>
                    </div>
                    <div class="text-[11px] text-slate-300 flex flex-wrap items-center gap-3">
                        <span>Order ID: <strong class="text-white">${data.order_id}</strong></span>
                        <span>Mode: <strong class="text-cyan-300">${data.execution_venue}</strong></span>
                        <span>Size: <strong class="text-white">${data.quantity} ${data.symbol}</strong></span>
                        <span>Notional: <strong class="text-white">$${data.notional_usdt}</strong></span>
                        <span>Margin: <strong class="text-white">$${data.margin_usdt} (${data.leverage}x ${data.margin_mode})</strong></span>
                    </div>
                </div>
            `;
        }

    } catch (err) {
        if (feedback) {
            feedback.innerHTML = `
                <div class="p-2.5 rounded-lg bg-rose-950/80 border border-rose-800 text-rose-300 text-xs font-mono flex items-center gap-2">
                    <i class="fa-solid fa-triangle-exclamation text-rose-400"></i>
                    <span>Execution Rejected: ${err.message}</span>
                </div>
            `;
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            setAISetupVenue(currentAISetupVenue);
        }
    }
}

// =========================================================================
// GROQ AI AUTO-HUNTER (10 -> 3 -> 1 FUNNEL PIPELINE)
// =========================================================================

let currentGroqPipelineData = null;

function setFunnelStepState(stepNum, state) {
    const el = document.getElementById(`funnelStep${stepNum}`);
    if (!el) return;
    if (state === 'active') {
        el.className = 'px-2 py-0.5 rounded bg-purple-900/80 border border-purple-500 text-purple-200 font-bold animate-pulse';
    } else if (state === 'complete') {
        el.className = 'px-2 py-0.5 rounded bg-emerald-950/80 border border-emerald-600 text-emerald-300 font-semibold';
    } else if (state === 'error') {
        el.className = 'px-2 py-0.5 rounded bg-rose-950/80 border border-rose-600 text-rose-300 font-semibold';
    } else {
        el.className = 'px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-400';
    }
}

let currentPendingGroqLiveSetup = null;

// --- LIVE AUTO-EXECUTION SESSION CONTROLLER ---
let liveSessionPollTimer = null;

function handleLiveSessionConnectionIssue(reason) {
    const stateBadge = document.getElementById('groqSessionStateBadge');
    if (stateBadge && (stateBadge.textContent === 'INACTIVE' || stateBadge.textContent === 'CONNECTING...')) {
        stateBadge.textContent = 'CONNECTING...';
        stateBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-amber-950 text-amber-300 border border-amber-700 animate-pulse';
    }
    const statusMsg = document.getElementById('groqSessionStatusMessage');
    if (statusMsg) {
        statusMsg.textContent = `Syncing live session state (${reason})...`;
    }
}

async function fetchLiveSessionState() {
    try {
        const resp = await fetch('/api/inspector/live-session');
        if (!resp.ok) {
            handleLiveSessionConnectionIssue(`HTTP ${resp.status}`);
            return;
        }
        const json = await resp.json();
        const data = json.data || {};
        updateLiveSessionUI(data);
    } catch (e) {
        console.warn('Failed to fetch live session state:', e);
        handleLiveSessionConnectionIssue('Server Offline');
    }
}

function syncGroqVenueVisuals(venue) {
    const venueSelect = document.getElementById('groqVenueSelect');
    const warningBanner = document.getElementById('groqMainnetWarningBanner');
    const warningTitle = document.getElementById('groqMainnetWarningTitle');
    const warningDesc = document.getElementById('groqMainnetWarningDesc');
    const autoBtn = document.getElementById('groqRunAutoOrderBtn');
    const scannerPill = document.getElementById('groqAutoScannerStatusPill');

    if (venue === 'LIVE_AUTO') {
        if (venueSelect) {
            venueSelect.className = 'bg-transparent text-purple-400 font-mono font-bold focus:outline-none text-xs';
        }
        if (warningBanner) {
            warningBanner.classList.remove('hidden');
            if (warningTitle) warningTitle.textContent = 'SESSION-AUTHORIZED LIVE AUTO-EXECUTION (ASTER DEX MAINNET)';
            if (warningDesc) warningDesc.textContent = 'Autonomous background scanner executes eligible 15M_SWEEP_RECLAIM_SMC setups directly on Aster DEX Mainnet without per-order confirmation once a session is active. Managed via Session Controls above.';
        }
        // In LIVE_AUTO mode, hide redundant manual order button; show autonomous scanner status pill
        if (autoBtn) {
            autoBtn.classList.add('hidden');
        }
        if (scannerPill) {
            scannerPill.classList.remove('hidden');
        }
    } else if (venue === 'LIVE' || venue === 'LIVE_MANUAL') {
        if (venueSelect) {
            venueSelect.className = 'bg-transparent text-amber-400 font-mono font-bold focus:outline-none text-xs';
        }
        if (warningBanner) {
            warningBanner.classList.remove('hidden');
            if (warningTitle) warningTitle.textContent = 'SUPERVISED LIVE EXECUTION (ASTER DEX MAINNET)';
            if (warningDesc) warningDesc.textContent = 'Orders placed in LIVE MANUAL mode require explicit operator modal confirmation for every order before submission. Isolated 10x leverage, capped $0.20 risk.';
        }
        if (autoBtn) {
            autoBtn.classList.remove('hidden');
            autoBtn.className = "px-4 py-2 bg-gradient-to-r from-amber-600 to-red-600 hover:from-amber-500 hover:to-red-500 text-white font-semibold rounded-lg text-xs flex items-center gap-2 shadow-lg shadow-amber-950/60 border border-amber-400/50 transition";
            autoBtn.innerHTML = '<i class="fa-solid fa-shield-halved"></i><span>Run &amp; Confirm Live Order</span>';
        }
        if (scannerPill) {
            scannerPill.classList.add('hidden');
        }
    } else {
        if (venueSelect) {
            venueSelect.className = 'bg-transparent text-emerald-400 font-mono font-bold focus:outline-none text-xs';
        }
        if (warningBanner) warningBanner.classList.add('hidden');
        if (autoBtn) {
            autoBtn.classList.remove('hidden');
            autoBtn.className = "px-4 py-2 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white font-semibold rounded-lg text-xs flex items-center gap-2 shadow-lg shadow-purple-950/40 border border-purple-400/40 transition";
            autoBtn.innerHTML = '<i class="fa-solid fa-bolt"></i><span>Run &amp; Auto-Place Limit Order (Simulated PAPER)</span>';
        }
        if (scannerPill) {
            scannerPill.classList.add('hidden');
        }
    }
}

function updateLiveSessionUI(data) {
    const stateBadge = document.getElementById('groqSessionStateBadge');
    const modeBadge = document.getElementById('groqSessionModeBadge');
    const durationSpan = document.getElementById('groqSessionDuration');
    const metricEquity = document.getElementById('sessMetricEquity');
    const metricDailyLoss = document.getElementById('sessMetricDailyLoss');
    const metricEntries = document.getElementById('sessMetricEntries');
    const metricLock = document.getElementById('sessMetricLock');
    const statusMsg = document.getElementById('groqSessionStatusMessage');

    const btnStart = document.getElementById('btnStartLiveSession');
    const btnReauth = document.getElementById('btnReauthorizeLiveSession');
    const btnPause = document.getElementById('btnPauseLiveSession');
    const btnResume = document.getElementById('btnResumeLiveSession');
    const btnStop = document.getElementById('btnStopLiveSession');

    const state = data.state || 'INACTIVE';
    const mode = data.mode || 'PAPER';

    // Synchronize mode dropdown without triggering POST mutation
    const venueSelect = document.getElementById('groqVenueSelect');
    if (venueSelect && venueSelect.value !== mode) {
        venueSelect.value = mode;
        syncGroqVenueVisuals(mode);
    }

    // State Badge
    if (stateBadge) {
        stateBadge.textContent = state;
        if (state === 'ACTIVE') {
            stateBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-emerald-950 text-emerald-300 border border-emerald-700 animate-pulse';
        } else if (state === 'PAUSED') {
            stateBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-amber-950 text-amber-300 border border-amber-700';
        } else if (state === 'REAUTHORIZATION_REQUIRED') {
            stateBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-amber-950 text-amber-300 border border-amber-500 animate-pulse';
        } else if (state === 'RECOVERY_REQUIRED') {
            stateBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-rose-950 text-rose-300 border border-rose-600 animate-bounce';
        } else if (state === 'KILL_SWITCH_HALTED') {
            stateBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-rose-950 text-rose-300 border border-rose-700 animate-bounce';
        } else if (state === 'HALTED' || state === 'STOPPED') {
            stateBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-slate-900 text-slate-400 border border-slate-700';
        } else {
            stateBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-slate-800 text-slate-300 border border-slate-700';
        }
    }

    // Mode Badge
    if (modeBadge) {
        modeBadge.textContent = mode;
        if (mode === 'LIVE_AUTO') {
            modeBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-purple-950 text-purple-300 border border-purple-700';
        } else if (mode === 'LIVE_MANUAL') {
            modeBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-amber-950 text-amber-300 border border-amber-700';
        } else {
            modeBadge.className = 'px-2.5 py-0.5 rounded text-xs font-bold bg-blue-950 text-blue-300 border border-blue-800';
        }
    }

    // Duration
    if (data.started_at_utc && (state === 'ACTIVE' || state === 'PAUSED' || state === 'REAUTHORIZATION_REQUIRED')) {
        const startMs = new Date(data.started_at_utc).getTime();
        const nowMs = Date.now();
        const diffSec = Math.max(0, Math.floor((nowMs - startMs) / 1000));
        const hrs = String(Math.floor(diffSec / 3600)).padStart(2, '0');
        const mins = String(Math.floor((diffSec % 3600) / 60)).padStart(2, '0');
        const secs = String(diffSec % 60).padStart(2, '0');
        if (durationSpan) durationSpan.textContent = `${hrs}:${mins}:${secs}`;
    } else if (state !== 'ACTIVE' && durationSpan && state !== 'PAUSED' && state !== 'REAUTHORIZATION_REQUIRED') {
        durationSpan.textContent = '00:00:00';
    }

    // Metrics
    if (metricEquity) {
        const equityValue = Number(data.current_equity_usd);
        metricEquity.textContent = Number.isFinite(equityValue) && data.current_equity_source !== 'UNVERIFIED'
            ? `$${equityValue.toFixed(2)}`
            : 'UNVERIFIED';
    }
    if (metricDailyLoss) {
        const lossSnapshot = data.daily_loss_snapshot || {};
        const lossVal = Number(lossSnapshot.used_usdt);
        const tzLabel = lossSnapshot.timezone ? ` (${lossSnapshot.timezone})` : ' (WIB)';
        metricDailyLoss.textContent = Number.isFinite(lossVal) && lossSnapshot.status === 'VERIFIED'
            ? `$${lossVal.toFixed(2)} / $${Number(lossSnapshot.limit_usdt || 0.45).toFixed(2)}${tzLabel}`
            : `UNVERIFIED / $${Number(lossSnapshot.limit_usdt || 0.45).toFixed(2)}${tzLabel}`;
    }
    if (metricEntries) {
        const placed = data.entries_placed_session !== undefined ? data.entries_placed_session : (data.orders_placed_count !== undefined ? data.orders_placed_count : (data.daily_filled_entries || 0));
        const limit = data.entry_limit !== undefined ? data.entry_limit : 3;
        const filled = data.entries_filled_session !== undefined ? data.entries_filled_session : (data.orders_filled_count || 0);
        metricEntries.textContent = `${placed} / ${limit}`;
        metricEntries.title = `Session: ${placed} placed, ${filled} filled | Daily: ${data.daily_filled_entries || 0} / ${limit} max`;
    }
    if (metricLock) {
        const recStatus = data.reconciliation_status || 'UNVERIFIED';
        if (data.global_lock_held) {
            const displayState = recStatus === 'SUCCESS' ? (data.lock_state || data.global_lock_state || 'ACTIVE') : recStatus;
            metricLock.textContent = `HELD (${data.lock_symbol || data.global_lock_symbol || 'POS'}: ${displayState})`;
            metricLock.className = 'font-bold text-amber-400';
        } else {
            metricLock.textContent = 'AVAILABLE (IDLE)';
            metricLock.className = 'font-bold text-emerald-400';
        }
    }

    const btnKillSwitch = document.getElementById('btnKillSwitchLiveSession');

    // Button visibility strictly bound to state
    if (state === 'ACTIVE') {
        if (btnStart) btnStart.classList.add('hidden');
        if (btnReauth) btnReauth.classList.add('hidden');
        if (btnPause) btnPause.classList.remove('hidden');
        if (btnResume) btnResume.classList.add('hidden');
        if (btnStop) btnStop.classList.remove('hidden');
        if (btnKillSwitch) btnKillSwitch.classList.remove('hidden');
    } else if (state === 'PAUSED') {
        if (btnStart) btnStart.classList.add('hidden');
        if (btnReauth) btnReauth.classList.add('hidden');
        if (btnPause) btnPause.classList.add('hidden');
        if (btnResume) btnResume.classList.remove('hidden');
        if (btnStop) btnStop.classList.remove('hidden');
        if (btnKillSwitch) btnKillSwitch.classList.remove('hidden');
    } else if (state === 'REAUTHORIZATION_REQUIRED' || state === 'RECOVERY_REQUIRED') {
        if (btnStart) btnStart.classList.add('hidden');
        if (btnReauth) btnReauth.classList.remove('hidden');
        if (btnPause) btnPause.classList.add('hidden');
        if (btnResume) btnResume.classList.add('hidden');
        if (btnStop) btnStop.classList.remove('hidden');
        if (btnKillSwitch) btnKillSwitch.classList.remove('hidden');
    } else {
        // INACTIVE or HALTED or KILL_SWITCH_HALTED or STOPPED
        if (btnStart) btnStart.classList.remove('hidden');
        if (btnReauth) btnReauth.classList.add('hidden');
        if (btnPause) btnPause.classList.add('hidden');
        if (btnResume) btnResume.classList.add('hidden');
        if (btnStop) btnStop.classList.add('hidden');
        if (btnKillSwitch) btnKillSwitch.classList.add('hidden');
    }

    // Auto Scanner Status Pill update
    const scannerPill = document.getElementById('groqAutoScannerStatusPill');
    const scannerText = document.getElementById('groqAutoScannerStatusText');
    if (scannerPill && scannerText) {
        if (mode === 'LIVE_AUTO') {
            scannerPill.classList.remove('hidden');
            if (state === 'ACTIVE' && data.scanner_active) {
                scannerPill.className = 'px-3 py-2 rounded-lg text-xs font-mono font-bold flex items-center gap-2 border bg-emerald-950/80 border-emerald-500 text-emerald-300';
                const scanTime = data.last_scan_utc ? new Date(data.last_scan_utc).toLocaleTimeString() : 'starting...';
                scannerText.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 animate-ping inline-block mr-1"></span>AUTONOMOUS SCANNER RUNNING (Last: ${scanTime})`;
            } else if (state === 'PAUSED') {
                scannerPill.className = 'px-3 py-2 rounded-lg text-xs font-mono font-bold flex items-center gap-2 border bg-amber-950/80 border-amber-600 text-amber-300';
                scannerText.innerHTML = `<i class="fa-solid fa-pause text-amber-400 mr-1"></i>AUTO-SCANNER PAUSED`;
            } else if (state === 'REAUTHORIZATION_REQUIRED' || state === 'RECOVERY_REQUIRED') {
                scannerPill.className = 'px-3 py-2 rounded-lg text-xs font-mono font-bold flex items-center gap-2 border bg-rose-950/80 border-rose-600 text-rose-300';
                scannerText.innerHTML = `<i class="fa-solid fa-triangle-exclamation text-rose-400 mr-1"></i>REAUTHORIZATION REQUIRED`;
            } else {
                scannerPill.className = 'px-3 py-2 rounded-lg text-xs font-mono font-bold flex items-center gap-2 border bg-slate-900 border-slate-700 text-slate-400';
                scannerText.innerHTML = `<i class="fa-solid fa-circle-stop text-slate-500 mr-1"></i>SCANNER IDLE (Click 'Start LIVE Auto')`;
            }
        } else {
            scannerPill.classList.add('hidden');
        }
    }

    // Status / Alert message
    if (statusMsg) {
        if (data.last_status_message) {
            statusMsg.textContent = data.last_status_message;
            statusMsg.classList.remove('hidden');
        } else {
            statusMsg.classList.add('hidden');
        }
    }

    // Live Screening Log Feed auto-sync
    if (data.latest_screening) {
        renderScreeningFeed(data.latest_screening);
    }
    if (data.screening_history_count !== undefined) {
        const countBadge = document.getElementById('screeningHistoryCountBadge');
        if (countBadge) countBadge.textContent = data.screening_history_count;
    }
}

async function startLiveAutoSession() {
    const statusMsg = document.getElementById('groqSessionStatusMessage');
    try {
        if (statusMsg) {
            statusMsg.textContent = 'Running 10-point Pre-Flight Safety Audit...';
            statusMsg.classList.remove('hidden');
        }
        const resp = await fetch('/api/inspector/live-session/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ operator_id: 'OPERATOR' })
        });
        const res = await resp.json();
        if (!resp.ok) {
            const err = res.detail || res.message || 'Pre-flight audit failed';
            alert(`LIVE Auto-Execution Activation Rejected:

${err}`);
            if (statusMsg) {
                statusMsg.textContent = `ACTIVATION BLOCKED: ${err}`;
                statusMsg.className = 'text-[11px] p-2 rounded bg-rose-950/80 border border-rose-700 text-rose-200';
                statusMsg.classList.remove('hidden');
            }
            return;
        }

        const venueSelect = document.getElementById('groqVenueSelect');
        if (venueSelect) {
            venueSelect.value = 'LIVE_AUTO';
            onGroqVenueChanged();
        }
        await fetchLiveSessionState();
    } catch (e) {
        alert(`Error starting LIVE session: ${e.message}`);
    }
}

async function pauseLiveAutoSession() {
    try {
        const resp = await fetch('/api/inspector/live-session/pause', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ operator_id: 'OPERATOR', reason: 'OPERATOR_PAUSED' })
        });
        await fetchLiveSessionState();
    } catch (e) {
        alert(`Error pausing session: ${e.message}`);
    }
}

async function reauthorizeLiveAutoSession() {
    try {
        const resp = await fetch('/api/inspector/live-session/reauthorize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ operator_id: 'OPERATOR' })
        });
        const res = await resp.json();
        if (!resp.ok) {
            alert(`Cannot reauthorize session: ${res.detail || res.message}`);
            return;
        }
        await fetchLiveSessionState();
    } catch (e) {
        alert(`Error reauthorizing session: ${e.message}`);
    }
}

async function resumeLiveAutoSession() {
    try {
        const resp = await fetch('/api/inspector/live-session/resume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const res = await resp.json();
        if (!resp.ok) {
            alert(`Cannot resume session: ${res.detail || res.message}`);
            return;
        }
        await fetchLiveSessionState();
    } catch (e) {
        alert(`Error resuming session: ${e.message}`);
    }
}

async function stopLiveAutoSession() {
    if (!confirm(`Are you sure you want to stop the LIVE Auto-Execution Session?

New automated entries will halt immediately. Any open position will remain protected by exchange-side SL/TP.`)) {
        return;
    }
    try {
        const resp = await fetch('/api/inspector/live-session/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ operator_id: 'OPERATOR', reason: 'OPERATOR_STOPPED' })
        });
        const venueSelect = document.getElementById('groqVenueSelect');
        if (venueSelect) {
            venueSelect.value = 'PAPER';
            onGroqVenueChanged();
        }
        await fetchLiveSessionState();
    } catch (e) {
        alert(`Error stopping session: ${e.message}`);
    }
}

async function triggerLiveEmergencyKillSwitch() {
    if (!confirm(`EMERGENCY KILL SWITCH CONFIRMATION

This will IMMEDIATELY:
1. Cancel all resting orders on Aster DEX Mainnet
2. Emergency market-close any active position
3. Permanently HALT auto-execution

Are you sure you want to proceed?`)) {
        return;
    }
    try {
        const resp = await fetch('/api/inspector/live-session/kill-switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ operator_id: 'OPERATOR', reason: 'MANUAL_EMERGENCY_KILL_SWITCH' })
        });
        const res = await resp.json();
        alert(`KILL SWITCH EXECUTED:

${res.message || 'System halted and orders cancelled.'}`);
        const venueSelect = document.getElementById('groqVenueSelect');
        if (venueSelect) {
            venueSelect.value = 'PAPER';
            onGroqVenueChanged();
        }
        await fetchLiveSessionState();
    } catch (e) {
        alert(`Error triggering kill switch: ${e.message}`);
    }
}

async function onGroqVenueChanged() {
    const venueSelect = document.getElementById('groqVenueSelect');
    const venue = venueSelect?.value || 'PAPER';
    
    syncGroqVenueVisuals(venue);

    try {
        await fetch('/api/inspector/live-session/mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: venue })
        });
        await fetchLiveSessionState();
    } catch (e) {
        console.warn('Failed to update live session mode:', e);
    }
}

function closeGroqConfirmModal() {
    const modal = document.getElementById('groqLiveConfirmModal');
    if (modal) modal.classList.add('hidden');
}

async function confirmAndExecuteGroqLiveOrder() {
    closeGroqConfirmModal();
    if (!currentPendingGroqLiveSetup) return;
    await runGroqAutoTrader(true, true);
}

async function runGroqAutoTrader(autoFire = false, operatorConfirmed = false) {
    const riskSelect = document.getElementById('groqRiskPresetSelect');
    const venueSelect = document.getElementById('groqVenueSelect');
    const modelSelect = document.getElementById('groqModelSelect');
    const previewBtn = document.getElementById('groqRunPreviewBtn');
    const autoBtn = document.getElementById('groqRunAutoOrderBtn');
    const modelBadge = document.getElementById('groqAutoHunterModelBadge');

    const loadingBox = document.getElementById('groqFunnelLoading');
    const loadingText = document.getElementById('groqFunnelLoadingText');
    const loadingSubtext = document.getElementById('groqFunnelLoadingSubtext');
    const resultBox = document.getElementById('groqFunnelResult');

    const riskPreset = riskSelect?.value || 'GROWTH_080';
    const venue = venueSelect?.value || 'PAPER';
    const modelOverride = modelSelect?.value || 'qwen/qwen3.8-27b';

    if (modelBadge) {
        modelBadge.textContent = modelOverride.split('/').pop().toUpperCase();
    }

    // Disable action buttons during execution
    if (previewBtn) {
        previewBtn.disabled = true;
        previewBtn.classList.add('opacity-50', 'cursor-not-allowed');
    }
    if (autoBtn) {
        autoBtn.disabled = true;
        autoBtn.classList.add('opacity-50', 'cursor-not-allowed');
    }

    if (autoFire && autoBtn) {
        if (operatorConfirmed) {
            autoBtn.innerHTML = '<i class="fa-solid fa-circle-notch animate-spin"></i><span>Submitting to Mainnet...</span>';
        } else {
            autoBtn.innerHTML = '<i class="fa-solid fa-circle-notch animate-spin"></i><span>Evaluating &amp; Sizing...</span>';
        }
    } else if (!autoFire && previewBtn) {
        previewBtn.innerHTML = '<i class="fa-solid fa-circle-notch animate-spin"></i><span>Screening 10&rarr;3&rarr;1...</span>';
    }

    if (resultBox) resultBox.classList.add('hidden');
    if (loadingBox) {
        loadingBox.classList.remove('hidden');
        if (loadingText) loadingText.textContent = (venue === 'LIVE' && operatorConfirmed)
            ? 'Submitting Verified Limit Order to Aster DEX Mainnet...'
            : 'Executing 10 -> 3 -> 1 Groq AI Trade Funnel...';
        if (loadingSubtext) loadingSubtext.textContent = 'Stage 1: Deterministic Python Pre-Screener (scanning Aster DEX pairs)...';
    }

    setFunnelStepState(1, 'active');
    setFunnelStepState(2, 'inactive');
    setFunnelStepState(3, 'inactive');

    const timer1 = setTimeout(() => {
        setFunnelStepState(1, 'complete');
        setFunnelStepState(2, 'active');
        if (loadingSubtext) loadingSubtext.textContent = `Stage 2: Groq LLM (${modelOverride}) selecting Top 3 finalists...`;
    }, 2000);

    const timer2 = setTimeout(() => {
        setFunnelStepState(2, 'complete');
        setFunnelStepState(3, 'active');
        if (loadingSubtext) loadingSubtext.textContent = 'Stage 3 & 4: Deep Order Book Analysis + 15M_SWEEP_RECLAIM_SMC Strategy & Sizing...';
    }, 4500);

    try {
        const payload = {
            risk_preset: riskPreset,
            execution_venue: venue,
            auto_fire_limit_order: !!autoFire,
            operator_confirmed: !!operatorConfirmed,
            strategy_id: "15M_SWEEP_RECLAIM_SMC",
            model_override: modelOverride,
            selected_setup: currentPendingGroqLiveSetup || null
        };

        const resp = await fetch('/api/inspector/groq-auto-setup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        clearTimeout(timer1);
        clearTimeout(timer2);

        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || data.message || `Server returned HTTP ${resp.status}`);
        }

        const resultData = data.data || data;
        currentGroqPipelineData = resultData;

        setFunnelStepState(1, 'complete');
        setFunnelStepState(2, 'complete');
        setFunnelStepState(3, 'complete');

        if (loadingBox) loadingBox.classList.add('hidden');

        // Check if operator confirmation is required for LIVE order
        if ((venue === 'LIVE' || venue === 'LIVE_MANUAL') && autoFire && !operatorConfirmed && resultData.confirmation_payload) {
            currentPendingGroqLiveSetup = resultData.stage3 || resultData.confirmation_payload;
            openGroqConfirmModal(resultData.confirmation_payload);
        } else if (venue === 'LIVE_AUTO' && autoFire) {
            // In LIVE_AUTO, order is placed directly by server if authorized!
            currentPendingGroqLiveSetup = null;
        } else if (operatorConfirmed) {
            currentPendingGroqLiveSetup = null;
        }

        // Always refresh live session status after pipeline run
        fetchLiveSessionState();

        if (resultBox) {
            resultBox.classList.remove('hidden');
            renderGroqFunnelResult(resultData, autoFire);
        }

    } catch (err) {
        clearTimeout(timer1);
        clearTimeout(timer2);
        setFunnelStepState(1, 'error');
        setFunnelStepState(2, 'error');
        setFunnelStepState(3, 'error');

        if (loadingBox) loadingBox.classList.add('hidden');
        if (resultBox) {
            resultBox.classList.remove('hidden');
            resultBox.innerHTML = `
                <div class="p-4 rounded-xl bg-rose-950/70 border border-rose-800 text-rose-200 text-xs space-y-2">
                    <div class="font-bold flex items-center gap-2 text-rose-300">
                        <i class="fa-solid fa-triangle-exclamation text-rose-400"></i>
                        <span>Funnel Pipeline Interrupted</span>
                    </div>
                    <div class="font-mono text-[11px] text-slate-300">${err.message || 'Unknown error occurred'}</div>
                    <div class="pt-1 flex items-center gap-2">
                        <button type="button" onclick="runGroqAutoTrader(${autoFire}, false)" class="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-white rounded text-xs border border-slate-700">
                            <i class="fa-solid fa-rotate-right mr-1"></i> Retry
                        </button>
                    </div>
                </div>
            `;
        }
    } finally {
        if (previewBtn) {
            previewBtn.disabled = false;
            previewBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            previewBtn.innerHTML = '<i class="fa-solid fa-eye mr-1"></i><span>Preview Setup (Read-Only)</span>';
        }
        if (autoBtn) {
            autoBtn.disabled = false;
            autoBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            syncGroqVenueVisuals(venue);
        }
    }
}

function openGroqConfirmModal(p) {
    const modal = document.getElementById('groqLiveConfirmModal');
    const body = document.getElementById('groqConfirmModalBody');
    if (!modal || !body || !p) return;

    body.innerHTML = `
        <div class="grid grid-cols-2 gap-2 text-[11px] p-3 rounded-xl bg-slate-950 border border-slate-800">
            <div>Symbol: <strong class="text-white text-sm font-bold">${p.symbol}</strong></div>
            <div>Direction: <strong class="${p.direction === 'LONG' ? 'text-emerald-400' : 'text-rose-400'} text-sm font-bold">${p.direction} LIMIT</strong></div>
            <div>Entry Price: <strong class="text-cyan-300">$${p.entry_price}</strong></div>
            <div>Order Qty: <strong class="text-white">${p.qty}</strong></div>
            <div>Stop Loss: <strong class="text-rose-400">$${p.stop_loss_price} (-${p.stop_distance_pct}%)</strong></div>
            <div>Take Profit: <strong class="text-emerald-400">$${p.take_profit_price}</strong></div>
            <div>Notional: <strong class="text-white">$${p.notional_usdt}</strong></div>
            <div>Margin (10x): <strong class="text-purple-300">$${p.isolated_margin_usdt}</strong></div>
            <div>Risk Budget: <strong class="text-amber-400">$${p.max_risk_usdt} (Max $0.20)</strong></div>
            <div>Net R:R: <strong class="text-emerald-400">${p.expected_net_rr}R (Gate &ge; 1.50)</strong></div>
            <div class="col-span-2 text-[10px] text-slate-400 pt-1 border-t border-slate-800/80">
                Strategy: <strong class="text-white">15M_SWEEP_RECLAIM_SMC</strong> &bull; Estimated Roundtrip Fees: <strong class="text-slate-300">$${p.estimated_costs_usd}</strong>
            </div>
        </div>
    `;

    modal.classList.remove('hidden');
}

function renderGroqFunnelResult(data, autoFired) {
    const container = document.getElementById('groqFunnelResult');
    if (!container) return;

    const s1 = data.stage1 || {};
    const s2 = data.stage2 || {};
    const s3 = data.stage3 || {};
    const receipt = data.execution_receipt;
    const gateStatus = data.risk_gate_status || {};
    const regime = s1.market_regime || {};
    const top10 = s1.top_10 || [];
    const top3 = s2.top_3 || [];

    const isLong = s3.direction === 'LONG';
    const sideColor = isLong ? 'emerald' : 'rose';
    const sideIcon = isLong ? 'fa-arrow-trend-up' : 'fa-arrow-trend-down';

    // Status Badge resolution
    const statusVal = s3.status || data.status || 'PREVIEW';
    let statusBadgeColor = 'bg-slate-900 border-slate-700 text-slate-300';
    let statusLabel = statusVal;

    switch (statusVal) {
        case 'PREVIEW':
            statusBadgeColor = 'bg-cyan-950 border-cyan-700 text-cyan-300';
            statusLabel = 'PREVIEW (NON-EXECUTING)';
            break;
        case 'READY':
            statusBadgeColor = 'bg-amber-950 border-amber-600 text-amber-300 animate-pulse';
            statusLabel = 'READY (AWAITING OPERATOR CONFIRMATION)';
            break;
        case 'REJECTED':
            statusBadgeColor = 'bg-rose-950 border-rose-800 text-rose-300';
            statusLabel = 'REJECTED BY RISK / STRATEGY GATE';
            break;
        case 'SUBMITTING':
            statusBadgeColor = 'bg-indigo-950 border-indigo-600 text-indigo-300 animate-pulse';
            statusLabel = 'SUBMITTING ORDER...';
            break;
        case 'OPEN':
            statusBadgeColor = 'bg-blue-950 border-blue-600 text-blue-300';
            statusLabel = 'OPEN (RESTING LIMIT ORDER ON ASTER DEX)';
            break;
        case 'PROTECTED':
            statusBadgeColor = 'bg-emerald-950 border-emerald-500 text-emerald-300 font-bold';
            statusLabel = 'PROTECTED (EXCHANGE-SIDE LIMIT TP + STOP SL ATTACHED)';
            break;
        case 'PARTIALLY_PROTECTED':
        case 'PARTIALLY_PROTECTED (SL ONLY - TP NOT PLACED)':
        case 'TP_NOT_PLACED':
            statusBadgeColor = 'bg-amber-950 border-amber-500 text-amber-300 font-bold';
            statusLabel = 'PARTIALLY PROTECTED (EXCHANGE SL ATTACHED — TP NOT PLACED)';
            break;
        case 'PARTIALLY_FILLED':
            statusBadgeColor = 'bg-amber-950 border-amber-500 text-amber-300';
            statusLabel = 'PARTIALLY FILLED';
            break;
        case 'CLOSED':
            statusBadgeColor = 'bg-slate-900 border-slate-700 text-slate-300';
            statusLabel = 'CLOSED';
            break;
        case 'ERROR':
            statusBadgeColor = 'bg-rose-950 border-rose-600 text-rose-300 font-bold';
            statusLabel = 'EXECUTION ERROR';
            break;
        case 'KILL_SWITCH_HALTED':
            statusBadgeColor = 'bg-red-950 border-red-600 text-red-200 font-bold animate-pulse';
            statusLabel = 'KILL SWITCH HALTED';
            break;
    }

    // Build Execution Receipt HTML (if auto-placed or already executed)
    let receiptHtml = '';
    if (receipt) {
        const isSuccess = receipt.status === 'PROTECTED' || receipt.status === 'OPEN' || receipt.status === 'FILLED' || receipt.status.includes('PARTIALLY_PROTECTED') || receipt.status === 'TP_NOT_PLACED';
        const hasExchangeTp = Boolean(receipt.tp_order_id && receipt.tp_status === 'PLACED');
        const tpStatusBadge = hasExchangeTp
            ? '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-900 border border-emerald-500 text-emerald-200">PLACED ON EXCHANGE</span>'
            : '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-900 border border-amber-500 text-amber-200">TP NOT PLACED</span>';

        const plannedTpVal = receipt.tp_price ? '$' + Number(receipt.tp_price).toFixed(4) : (s3.take_profit_price ? '$' + Number(s3.take_profit_price).toFixed(4) : '--');
        const slPriceVal = receipt.sl_price ? '$' + Number(receipt.sl_price).toFixed(2) : (s3.stop_loss_price ? '$' + Number(s3.stop_loss_price).toFixed(2) : '--');

        receiptHtml = `
            <div class="p-3.5 rounded-xl ${isSuccess ? 'bg-slate-900/90 border border-slate-700/80 text-slate-200' : 'bg-rose-950/70 border border-rose-700/80 text-rose-200'} text-xs font-mono space-y-2.5 shadow-inner">
                <div class="flex items-center justify-between font-bold flex-wrap gap-2">
                    <span class="flex items-center gap-2">
                        <i class="fa-solid ${isSuccess ? 'fa-circle-check text-emerald-400' : 'fa-triangle-exclamation text-rose-400'}"></i>
                        <span>${isSuccess ? 'Exchange Order Dispatched' : 'Execution Notice'} (${receipt.execution_venue || data.execution_venue})</span>
                    </span>
                    <div class="flex items-center gap-2">
                        ${tpStatusBadge}
                        <span class="px-2 py-0.5 rounded ${statusBadgeColor} text-[10px] font-bold border">
                            ${receipt.status || statusVal}
                        </span>
                    </div>
                </div>

                <!-- Comprehensive Protection Breakdown -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] text-slate-300 pt-1 border-t border-slate-800">
                    <div>Entry Order ID: <strong class="text-white">${receipt.order_id || '--'}</strong></div>
                    <div>Filled Qty: <strong class="text-white">${receipt.fill_quantity !== undefined ? receipt.fill_quantity : 0.0}</strong></div>
                    <div>SL Order ID: <strong class="text-rose-400">${receipt.sl_order_id || '--'}</strong></div>
                    <div>SL Status: <strong class="${receipt.sl_status === 'PLACED' ? 'text-emerald-400' : 'text-slate-400'}">${receipt.sl_status || 'PLACED'} (${slPriceVal})</strong></div>
                    <div>Planned TP: <strong class="text-emerald-400">${plannedTpVal}</strong></div>
                    <div>TP Order Type: <strong class="text-white">${receipt.tp_order_type || 'LIMIT'} (reduceOnly)</strong></div>
                    <div>TP Order ID: <strong class="${receipt.tp_order_id ? 'text-emerald-300' : 'text-amber-400'}">${receipt.tp_order_id || 'NOT PLACED'}</strong></div>
                    <div>TP Status: <strong class="${receipt.tp_status === 'PLACED' ? 'text-emerald-400' : 'text-amber-400'}">${receipt.tp_status || (receipt.tp_order_id ? 'PLACED' : 'NOT PLACED')}</strong></div>
                </div>

                ${!hasExchangeTp ? `
                    <div class="p-2 rounded bg-amber-950/60 border border-amber-700/80 text-amber-200 text-[11px] flex items-center justify-between">
                        <span><i class="fa-solid fa-triangle-exclamation mr-1.5 text-amber-400"></i><strong>TP NOT PLACED ON EXCHANGE</strong>: Aster DEX requires an open position before reduce-only Limit TP orders can rest. TP will be submitted automatically upon entry fill.</span>
                        <span class="font-bold text-amber-300 ml-2 whitespace-nowrap">AWAITING FILL</span>
                    </div>
                ` : `
                    <div class="p-2 rounded bg-emerald-950/60 border border-emerald-700/80 text-emerald-200 text-[11px] flex items-center justify-between">
                        <span><i class="fa-solid fa-shield-halved mr-1.5 text-emerald-400"></i><strong>FULLY PROTECTED</strong>: Exchange-side LIMIT Take-Profit (${receipt.tp_order_id}) and STOP_MARKET Stop-Loss (${receipt.sl_order_id}) are active.</span>
                        <span class="font-bold text-emerald-300 ml-2 whitespace-nowrap">PROTECTED</span>
                    </div>
                `}

                ${receipt.journal_trade_id ? `<div class="text-[10px] text-slate-400 border-t border-slate-800/80 pt-1.5 flex items-center justify-between"><span>Journal ID: <strong class="text-white">${receipt.journal_trade_id}</strong></span><span class="text-emerald-400 font-bold"><i class="fa-solid fa-check mr-1"></i>${receipt.reconciliation_status || 'RECORDED'}</span></div>` : ''}
                ${receipt.error ? `<div class="text-[11px] text-rose-300 font-bold border-t border-rose-900/60 pt-1">${receipt.error}</div>` : ''}
                ${receipt.reason ? `<div class="text-[11px] text-amber-300 font-bold border-t border-amber-900/60 pt-1">${receipt.reason}</div>` : ''}
            </div>
        `;
    }

    // Risk Gate Diagnostics Bar
    const riskBarHtml = `
        <div class="p-2.5 rounded-lg bg-slate-950/80 border border-slate-800 text-[11px] font-mono grid grid-cols-2 sm:grid-cols-4 gap-2 text-slate-400">
            <div>Risk Gate: <strong class="${gateStatus.risk_limits_ok ? 'text-emerald-400' : 'text-rose-400'}">${gateStatus.risk_limits_ok ? 'PASSED' : 'BLOCKED'}</strong></div>
            <div>Daily Entries: <strong class="text-white">${gateStatus.daily_filled_entries || 0}/${gateStatus.max_filled_entries || 3}</strong></div>
            <div>Daily Loss: <strong class="text-white">$${(gateStatus.daily_realized_loss_usd || 0).toFixed(3)} / $${(gateStatus.daily_loss_limit_usd || 0.45).toFixed(2)}</strong></div>
            <div>Global Lock: <strong class="${gateStatus.global_lock_available ? 'text-emerald-400' : 'text-amber-400'}">${gateStatus.global_lock_available ? 'AVAILABLE' : 'HELD'}</strong></div>
        </div>
    `;

    // Rejection notice if ineligible
    let rejectionNoticeHtml = '';
    if (!s3.strategy_eligible || s3.status === 'REJECTED') {
        rejectionNoticeHtml = `
            <div class="p-3 rounded-xl bg-amber-950/50 border border-amber-700/60 text-amber-200 text-xs font-mono space-y-1">
                <div class="font-bold flex items-center gap-2 text-amber-300">
                    <i class="fa-solid fa-circle-exclamation text-amber-400"></i>
                    <span>Setup Ineligible for Live Execution</span>
                </div>
                <p class="text-[11px] text-slate-300 font-sans">
                    Reason: <strong class="text-amber-200">${s3.strategy_rejection_reason || 'Reclaim criteria not satisfied or stop distance out of bounds.'}</strong>
                </p>
            </div>
        `;
    }

    // Top 3 Finalists Cards
    const top3Html = top3.map((cand, idx) => {
        let catBadgeColor = 'bg-cyan-950 text-cyan-300 border-cyan-800';
        if (cand.category === 'LIQUIDITY_SWEEP_RECLAIM') catBadgeColor = 'bg-emerald-950 text-emerald-300 border-emerald-800';
        else if (cand.category === 'MOMENTUM_CONTINUATION') catBadgeColor = 'bg-blue-950 text-blue-300 border-blue-800';
        else if (cand.category === 'LAGGARD_CATCH_UP' || cand.category === 'SMC_DISCOUNT_REVERSAL') catBadgeColor = 'bg-amber-950 text-amber-300 border-amber-800';

        const isWinner = cand.symbol === s3.symbol;

        return `
            <div class="p-3.5 rounded-xl border ${isWinner ? 'bg-purple-950/40 border-purple-500/80 ring-1 ring-purple-500/50' : 'bg-slate-900/80 border-slate-800'} space-y-2 flex flex-col justify-between transition hover:border-purple-600/50">
                <div class="space-y-1.5">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <span class="w-5 h-5 rounded-full ${isWinner ? 'bg-purple-600 text-white' : 'bg-slate-800 text-slate-300'} flex items-center justify-center text-[10px] font-bold">#${cand.rank || idx + 1}</span>
                            <span class="font-bold text-white font-mono cursor-pointer hover:text-cyan-400" onclick="selectInspectorSymbol('${cand.symbol}')">${cand.symbol}</span>
                            ${isWinner ? '<span class="px-1.5 py-0.2 rounded bg-purple-900 text-purple-200 text-[9px] font-bold uppercase tracking-wider">#1 Choice</span>' : ''}
                        </div>
                        <span class="text-xs font-mono font-bold text-purple-300">${(cand.score || 0).toFixed(1)}/10</span>
                    </div>
                    <div class="flex items-center gap-1.5">
                        <span class="px-2 py-0.5 rounded text-[10px] font-mono border ${catBadgeColor}">
                            ${cand.category || 'MOMENTUM'}
                        </span>
                    </div>
                    <p class="text-[11px] text-slate-300 leading-relaxed">${cand.thesis || 'No thesis provided.'}</p>
                </div>

                ${(cand.verification_questions && cand.verification_questions.length > 0) ? `
                    <div class="pt-2 border-t border-slate-800/80 text-[10px] text-slate-400 space-y-1">
                        <div class="font-bold text-slate-500 uppercase tracking-wider text-[9px]">Checklist:</div>
                        ${cand.verification_questions.slice(0, 2).map(q => `
                            <div class="flex items-start gap-1.5">
                                <i class="fa-regular fa-square-check text-slate-500 mt-0.5"></i>
                                <span>${q}</span>
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
            </div>
        `;
    }).join('');

    // Stage 1 Top 10 Table Rows
    const top10Rows = top10.map((c, i) => `
        <tr class="hover:bg-slate-800/50 transition border-b border-slate-800/40 text-[11px] font-mono">
            <td class="px-2.5 py-1.5 text-slate-500">#${i + 1}</td>
            <td class="px-2.5 py-1.5 font-bold text-white cursor-pointer hover:text-cyan-400" onclick="selectInspectorSymbol('${c.symbol}')">${c.symbol}</td>
            <td class="px-2.5 py-1.5 text-slate-200">$${c.mark_price}</td>
            <td class="px-2.5 py-1.5 ${c.change_24h_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${c.change_24h_pct >= 0 ? '+' : ''}${c.change_24h_pct.toFixed(2)}%</td>
            <td class="px-2.5 py-1.5 text-slate-300">${c.rel_strength_vs_btc >= 0 ? '+' : ''}${c.rel_strength_vs_btc.toFixed(2)}%</td>
            <td class="px-2.5 py-1.5 text-slate-400">$${c.turnover_24h_m.toFixed(1)}M</td>
            <td class="px-2.5 py-1.5 text-slate-400">${c.spread_bps.toFixed(2)} bps</td>
            <td class="px-2.5 py-1.5 text-slate-300">${c.range_72h_pct.toFixed(1)}%</td>
            <td class="px-2.5 py-1.5 text-slate-400">${c.rsi_15m.toFixed(0)} / ${c.rsi_1h.toFixed(0)}</td>
            <td class="px-2.5 py-1.5 text-[10px] text-slate-400 max-w-xs truncate" title="${c.reason_selected}">${c.reason_selected}</td>
        </tr>
    `).join('');

    // Put it all together
    container.innerHTML = `
        <!-- ================= STAGE 3 WINNING SETUP CARD ================= -->
        <div class="p-4 md:p-5 rounded-xl bg-gradient-to-br from-slate-900 via-purple-950/30 to-slate-900 border-2 border-purple-500/70 shadow-2xl space-y-4 relative overflow-hidden">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-purple-800/40 pb-3">
                <div class="flex items-center gap-2.5">
                    <span class="w-7 h-7 rounded-lg bg-${sideColor}-500/20 text-${sideColor}-400 border border-${sideColor}-500/40 flex items-center justify-center text-sm font-bold">
                        <i class="fa-solid ${sideIcon}"></i>
                    </span>
                    <div>
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="text-sm font-mono text-purple-300 font-bold uppercase tracking-wider">Candidate Winner</span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-${sideColor}-950 text-${sideColor}-300 border border-${sideColor}-800">
                                ${s3.direction} LIMIT
                            </span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-indigo-950 text-indigo-300 border border-indigo-800">
                                ${s3.strategy_id || '15M_SWEEP_RECLAIM_SMC'}
                            </span>
                            <span class="px-2 py-0.5 rounded text-[10px] font-mono border font-bold ${statusBadgeColor}">
                                ${statusLabel}
                            </span>
                        </div>
                        <h4 class="text-lg md:text-xl font-bold font-mono text-white flex items-center gap-2 pt-0.5">
                            <span>${s3.symbol}</span>
                            <span class="text-slate-400 text-sm font-normal">@</span>
                            <span class="text-purple-300">$${s3.entry_price}</span>
                        </h4>
                    </div>
                </div>

                <div class="flex flex-wrap items-center gap-2 text-xs font-mono">
                    <span class="px-2 py-1 rounded bg-slate-950 border border-slate-800 text-slate-300">
                        Lev: <strong class="text-white">${s3.leverage}x ${s3.margin_mode}</strong>
                    </span>
                    <span class="px-2 py-1 rounded bg-slate-950 border border-slate-800 text-slate-300">
                        Risk: <strong class="text-amber-400">$${s3.max_risk_usdt.toFixed(2)}</strong>
                    </span>
                    <span class="px-2 py-1 rounded bg-slate-950 border border-slate-800 text-slate-300">
                        Net-R:R: <strong class="text-emerald-400">${s3.expected_net_rr || s3.rr_ratio}R</strong>
                    </span>
                    <span class="px-2 py-1 rounded bg-slate-950 border border-slate-800 text-slate-300">
                        Stop: <strong class="text-rose-400">${s3.stop_distance_pct}%</strong>
                    </span>
                </div>
            </div>

            <!-- Key Numbers Grid -->
            <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5 font-mono text-xs">
                <div class="p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
                    <span class="text-[10px] text-slate-400 block">Entry Limit:</span>
                    <strong class="text-sm text-cyan-300">$${s3.entry_price}</strong>
                </div>
                <div class="p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
                    <span class="text-[10px] text-slate-400 block">Stop Loss:</span>
                    <strong class="text-sm text-rose-400">$${s3.stop_loss_price}</strong>
                </div>
                <div class="p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
                    <span class="text-[10px] text-slate-400 block">Take Profit:</span>
                    <strong class="text-sm text-emerald-400">$${s3.take_profit_price}</strong>
                </div>
                <div class="p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
                    <span class="text-[10px] text-slate-400 block">Order Size:</span>
                    <strong class="text-sm text-white">${s3.qty} ${s3.symbol.replace('USDT', '')}</strong>
                </div>
                <div class="p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
                    <span class="text-[10px] text-slate-400 block">Notional:</span>
                    <strong class="text-sm text-white">$${s3.notional_usdt.toFixed(2)}</strong>
                </div>
                <div class="p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
                    <span class="text-[10px] text-slate-400 block">Isolated Margin:</span>
                    <strong class="text-sm text-purple-300">$${s3.isolated_margin_usdt.toFixed(2)}</strong>
                </div>
            </div>

            <!-- Risk Gate Bar -->
            ${riskBarHtml}

            <!-- Rejection Notice (if any) -->
            ${rejectionNoticeHtml}

            <!-- Thesis and Reasoning -->
            <div class="p-3 rounded-lg bg-slate-950/50 border border-purple-900/30 text-xs space-y-1.5">
                <div class="flex items-center gap-1.5 font-bold text-purple-300">
                    <i class="fa-solid fa-brain"></i>
                    <span>Quantitative Strategy Hypothesis:</span>
                </div>
                <p class="text-slate-300 leading-relaxed font-sans">${s3.notes || s3.reasoning || 'Clean mathematical bracket setup.'}</p>
                ${s3.reasoning && s3.reasoning !== s3.notes ? `<p class="text-slate-400 font-sans text-[11px]">${s3.reasoning}</p>` : ''}
            </div>

            <!-- Execution Receipt (if order was fired) -->
            ${receiptHtml}

            <!-- Bottom Actions -->
            <div class="flex flex-wrap items-center justify-between gap-3 pt-1">
                <div class="text-[11px] text-slate-400 font-mono">
                    Venue: <strong class="${s3.execution_venue === 'LIVE' ? 'text-rose-400' : 'text-emerald-400'}">${s3.execution_venue}</strong> &bull; Generated: <strong class="text-slate-300">${new Date(data.timestamp_utc).toLocaleTimeString()}</strong>
                </div>
                <div class="flex items-center gap-2.5">
                    <button type="button" onclick="loadGroqSetupIntoExecutor()"
                        class="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-amber-300 font-semibold rounded-lg text-xs border border-amber-800/60 transition flex items-center gap-1.5 shadow-md">
                        <i class="fa-solid fa-arrow-down mr-1"></i>
                        <span>Transfer to Manual Executor</span>
                    </button>
                    ${((s3.execution_venue === 'LIVE_MANUAL' || s3.execution_venue === 'LIVE') && (s3.status === 'READY' || (!receipt && s3.strategy_eligible))) ? `
                        <button type="button" onclick="openGroqConfirmModal(currentGroqPipelineData?.stage3)"
                            class="px-4 py-1.5 bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 text-white font-bold rounded-lg text-xs shadow-md transition flex items-center gap-1.5">
                            <i class="fa-solid fa-shield-halved mr-1"></i>
                            <span>Confirm &amp; Place Real Order (${s3.execution_venue})</span>
                        </button>
                    ` : ''}
                </div>
            </div>
        </div>

        <!-- ================= STAGE 2 TOP 3 FINALISTS ================= -->
        <div class="card-bg p-4 rounded-xl border border-slate-800 space-y-3">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                <div class="flex items-center gap-2">
                    <span class="w-6 h-6 rounded-md bg-purple-950 border border-purple-800 text-purple-400 flex items-center justify-center text-xs font-bold">2</span>
                    <h5 class="text-sm font-bold text-white">Stage 2: Top 3 Curated Finalists (${s2.model_used || 'Groq LLM'})</h5>
                </div>
                <span class="text-xs text-slate-400">Selected from Top 10 by removing late entries & picking complementary setups</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                ${top3Html}
            </div>
        </div>

        <!-- ================= STAGE 1 TOP 10 PRE-SCREENED ================= -->
        <div class="card-bg p-4 rounded-xl border border-slate-800 space-y-3">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                <div class="flex items-center gap-2">
                    <span class="w-6 h-6 rounded-md bg-purple-950 border border-purple-800 text-purple-400 flex items-center justify-center text-xs font-bold">1</span>
                    <h5 class="text-sm font-bold text-white">Stage 1: Pre-Screened Candidates (${top10.length} from ${s1.total_screened_pairs || 600}+ pairs in ${(s1.execution_time_ms || 0).toFixed(0)}ms)</h5>
                </div>
                <div class="text-[11px] font-mono text-slate-400 flex items-center gap-2">
                    <span>BTC: <strong class="text-white">$${regime.btc_price || '--'}</strong> (${(regime.btc_change_24h_pct || 0) >= 0 ? '+' : ''}${(regime.btc_change_24h_pct || 0).toFixed(2)}%)</span>
                    <span class="px-1.5 py-0.2 rounded bg-slate-900 border border-slate-800 text-slate-300">${regime.btc_trend || 'NEUTRAL'}</span>
                </div>
            </div>

            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="text-[10px] uppercase font-mono text-slate-500 border-b border-slate-800">
                            <th class="px-2.5 py-1.5">#</th>
                            <th class="px-2.5 py-1.5">Symbol</th>
                            <th class="px-2.5 py-1.5">Mark</th>
                            <th class="px-2.5 py-1.5">24h %</th>
                            <th class="px-2.5 py-1.5">RS vs BTC</th>
                            <th class="px-2.5 py-1.5">24h Vol</th>
                            <th class="px-2.5 py-1.5">Spread</th>
                            <th class="px-2.5 py-1.5">72h Range</th>
                            <th class="px-2.5 py-1.5">RSI 15m/1h</th>
                            <th class="px-2.5 py-1.5">Screening Rationale</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${top10Rows}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

function loadGroqSetupIntoExecutor() {
    if (!currentGroqPipelineData || !currentGroqPipelineData.stage3) {
        alert('No winning setup available in current pipeline data.');
        return;
    }

    const s3 = currentGroqPipelineData.stage3;
    const jsonPayload = {
        symbol: s3.symbol,
        direction: s3.direction,
        order_type: s3.order_type || 'LIMIT',
        entry_price: s3.entry_price,
        stop_loss_price: s3.stop_loss_price,
        take_profit_price: s3.take_profit_price,
        qty: s3.qty,
        leverage: s3.leverage || 10,
        margin_mode: s3.margin_mode || 'ISOLATED',
        max_risk_usdt: s3.max_risk_usdt,
        execution_venue: s3.execution_venue || currentAISetupVenue,
        notes: s3.notes || `${s3.symbol} 10->3->1 Groq AI Setup`
    };

    const area = document.getElementById('aiSetupInput');
    if (area) {
        area.value = JSON.stringify(jsonPayload, null, 2);
        setAISetupVenue(jsonPayload.execution_venue);
        onAISetupInput();
        area.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}


// --- GROQ AI AUTO-HUNTER SCREENING FEED & TELEMETRY CONTROLLER (10 -> 3 -> 1) ---
let cachedScreeningHistory = [];
let lastRenderedCycleId = null;

function formatWIBTime(isoStr) {
    if (!isoStr) return '--';
    try {
        const d = new Date(isoStr);
        return d.toLocaleTimeString('id-ID', { timeZone: 'Asia/Jakarta', hour12: false }) + ' WIB';
    } catch (e) {
        return isoStr;
    }
}

function toggleScreeningHistoryDrawer() {
    const drawer = document.getElementById('screeningHistoryDrawer');
    if (!drawer) return;
    drawer.classList.toggle('hidden');
    if (!drawer.classList.contains('hidden')) {
        refreshScreeningFeed(false);
    }
}

async function refreshScreeningFeed(manual = false) {
    const icon = document.getElementById('refreshScreeningFeedIcon');
    if (icon && manual) icon.classList.add('fa-spin');
    try {
        const resp = await fetch('/api/autotrader/screening-log?limit=10');
        if (!resp.ok) return;
        const res = await resp.json();
        if (res.history) {
            cachedScreeningHistory = res.history;
            renderScreeningHistory(res.history);
            const countBadge = document.getElementById('screeningHistoryCountBadge');
            if (countBadge) countBadge.textContent = res.history.length;
        }
        if (res.latest) {
            renderScreeningFeed(res.latest);
        }
    } catch (e) {
        console.warn('Failed to refresh screening feed:', e);
    } finally {
        if (icon && manual) {
            setTimeout(() => icon.classList.remove('fa-spin'), 600);
        }
    }
}

function inspectHistoricalCycle(cycleId) {
    const found = cachedScreeningHistory.find(c => c.cycle_id === cycleId);
    if (found) {
        renderScreeningFeed(found);
        const activeContainer = document.getElementById('groqScreeningFeedPanel');
        if (activeContainer) {
            activeContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }
}

function renderScreeningHistory(historyList) {
    const tbody = document.getElementById('screeningHistoryTableBody');
    if (!tbody) return;
    if (!historyList || historyList.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="px-3 py-4 text-center text-slate-500">No cycle history yet</td></tr>';
        return;
    }

    tbody.innerHTML = historyList.map(item => {
        const timeStr = formatWIBTime(item.timestamp_utc);
        const cycleShort = (item.cycle_id || '--').replace('cycle-', '').slice(0, 8);
        const s1Count = item.stage1?.total_screened_pairs || item.stage1?.top_10?.length || 10;
        const finalists = (item.stage2?.top_3 || []).map(f => f.symbol).join(', ') || '--';
        const s3 = item.stage3 || {};
        const winSym = s3.symbol || '--';
        const side = s3.direction || 'LONG';
        const sideClass = side === 'LONG' ? 'text-emerald-400' : 'text-rose-400';

        let statusClass = 'bg-slate-800 text-slate-300 border-slate-700';
        if (item.status === 'OPEN' || item.status === 'PROTECTED') {
            statusClass = 'bg-emerald-950 text-emerald-300 border-emerald-600';
        } else if (item.status === 'REJECTED') {
            statusClass = 'bg-rose-950 text-rose-300 border-rose-800';
        } else if (item.status === 'PREVIEW') {
            statusClass = 'bg-cyan-950 text-cyan-300 border-cyan-800';
        }

        return `
            <tr class="hover:bg-slate-800/40 transition">
                <td class="px-3 py-2 text-slate-300 whitespace-nowrap">${timeStr}</td>
                <td class="px-3 py-2 text-slate-400 font-mono text-[10px]">${cycleShort}</td>
                <td class="px-3 py-2 text-slate-300">${s1Count} pairs</td>
                <td class="px-3 py-2 text-purple-300 font-bold">${finalists}</td>
                <td class="px-3 py-2 font-bold"><span class="${sideClass}">${side}</span> ${winSym}</td>
                <td class="px-3 py-2"><span class="px-2 py-0.5 rounded text-[10px] border font-bold ${statusClass}">${item.status || 'DONE'}</span></td>
                <td class="px-3 py-2 text-right">
                    <button type="button" onclick="inspectHistoricalCycle('${item.cycle_id}')" class="px-2 py-0.5 bg-purple-900/40 hover:bg-purple-900 text-purple-300 border border-purple-700/60 rounded text-[10px] transition">
                        Inspect
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

function renderScreeningFeed(cycle) {
    if (!cycle) return;
    const placeholder = document.getElementById('screeningFeedEmptyPlaceholder');
    const content = document.getElementById('screeningFeedContent');
    const badge = document.getElementById('screeningFeedStatusBadge');
    const timeBadge = document.getElementById('screeningFeedTimestamp');
    const cycleBadge = document.getElementById('screeningFeedCycleId');

    if (placeholder) placeholder.classList.add('hidden');
    if (content) content.classList.remove('hidden');

    const s1 = cycle.stage1 || {};
    const s2 = cycle.stage2 || {};
    const s3 = cycle.stage3 || {};
    const top10 = s1.top_10 || [];
    const top3 = s2.top_3 || [];
    const eliminated = s2.eliminated_summary || {};
    const regime = s1.market_regime || {};
    const top3Symbols = new Set(top3.map(f => f.symbol));

    if (cycleBadge) {
        cycleBadge.textContent = (cycle.cycle_id || 'cycle-unknown').replace('cycle-', '#').slice(0, 10);
        cycleBadge.classList.remove('hidden');
    }

    if (timeBadge) {
        timeBadge.innerHTML = `Last Scan: <strong class="text-white">${formatWIBTime(cycle.timestamp_utc)}</strong>`;
    }

    // Status Badge
    if (badge) {
        const st = s3.status || cycle.status || 'DONE';
        badge.textContent = st;
        if (st === 'PROTECTED' || st === 'OPEN') {
            badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950 text-emerald-300 border border-emerald-600';
        } else if (st === 'REJECTED') {
            badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-950 text-rose-300 border border-rose-800';
        } else if (st === 'PREVIEW') {
            badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-cyan-950 text-cyan-300 border border-cyan-700';
        } else {
            badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-800 text-slate-300 border border-slate-700';
        }
    }

    // Build Stage 1 HTML (10 Screened Coins)
    const stage1Rows = top10.map((c, i) => {
        const inTop3 = top3Symbols.has(c.symbol);
        const rangePct = Number(c.range_72h_pct || 0);
        let rangeClass = 'text-amber-400';
        let rangeLabel = 'Neutral';
        if (rangePct <= 38.0) { rangeClass = 'text-emerald-400 font-bold'; rangeLabel = 'Discount'; }
        else if (rangePct >= 65.0) { rangeClass = 'text-rose-400 font-bold'; rangeLabel = 'Premium'; }

        return `
            <tr class="hover:bg-slate-800/40 transition border-b border-slate-800/40 text-[11px] font-mono ${inTop3 ? 'bg-purple-950/20' : ''}">
                <td class="px-2.5 py-1.5 text-slate-500">#${i + 1}</td>
                <td class="px-2.5 py-1.5 font-bold text-white cursor-pointer hover:text-cyan-400 flex items-center gap-1.5" onclick="selectInspectorSymbol('${c.symbol}')">
                    <span>${c.symbol}</span>
                    ${inTop3 ? '<span class="px-1 py-0.2 rounded bg-purple-900 text-purple-200 text-[8px] font-bold uppercase tracking-wider">Top 3</span>' : ''}
                </td>
                <td class="px-2.5 py-1.5 text-slate-200">$${c.mark_price}</td>
                <td class="px-2.5 py-1.5 ${c.change_24h_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${c.change_24h_pct >= 0 ? '+' : ''}${Number(c.change_24h_pct).toFixed(2)}%</td>
                <td class="px-2.5 py-1.5 text-slate-300">${c.rel_strength_vs_btc >= 0 ? '+' : ''}${Number(c.rel_strength_vs_btc).toFixed(2)}%</td>
                <td class="px-2.5 py-1.5 text-slate-400">$${Number(c.turnover_24h_m).toFixed(1)}M</td>
                <td class="px-2.5 py-1.5 ${rangeClass}">${rangePct.toFixed(1)}% (${rangeLabel})</td>
                <td class="px-2.5 py-1.5 text-[10px] text-slate-400 max-w-xs truncate" title="${c.reason_selected}">${c.reason_selected}</td>
            </tr>
        `;
    }).join('');

    // Build Stage 2 HTML (Top 3 Finalists + 7 Eliminated Candidates)
    const stage2FinalistsHtml = top3.map((cand, idx) => {
        const isWinner = cand.symbol === s3.symbol;
        let catBadgeColor = 'bg-cyan-950 text-cyan-300 border-cyan-800';
        if (cand.category === 'LIQUIDITY_SWEEP_RECLAIM') catBadgeColor = 'bg-emerald-950 text-emerald-300 border-emerald-800';
        else if (cand.category === 'MOMENTUM_CONTINUATION') catBadgeColor = 'bg-blue-950 text-blue-300 border-blue-800';

        return `
            <div class="p-3 rounded-xl border ${isWinner ? 'bg-purple-950/40 border-purple-500/80 ring-1 ring-purple-500/50' : 'bg-slate-900/80 border-slate-800'} space-y-2 flex flex-col justify-between">
                <div class="space-y-1">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <span class="w-5 h-5 rounded-full ${isWinner ? 'bg-purple-600 text-white' : 'bg-slate-800 text-slate-300'} flex items-center justify-center text-[10px] font-bold">#${cand.rank || idx + 1}</span>
                            <span class="font-bold text-white font-mono cursor-pointer hover:text-cyan-400" onclick="selectInspectorSymbol('${cand.symbol}')">${cand.symbol}</span>
                            ${isWinner ? '<span class="px-1.5 py-0.2 rounded bg-purple-900 text-purple-200 text-[9px] font-bold uppercase">#1 Winner</span>' : ''}
                        </div>
                        <span class="text-xs font-mono font-bold text-purple-300">${Number(cand.score || 0).toFixed(1)}/10</span>
                    </div>
                    <span class="inline-block px-2 py-0.5 rounded text-[10px] font-mono border ${catBadgeColor}">${cand.category || 'SMC'}</span>
                    <p class="text-[11px] text-slate-300 leading-relaxed font-sans">${cand.thesis || 'No thesis provided.'}</p>
                </div>
            </div>
        `;
    }).join('');

    const eliminatedKeys = Object.keys(eliminated);
    const eliminatedHtml = eliminatedKeys.length > 0 ? `
        <div class="p-3 rounded-xl bg-slate-900/70 border border-slate-800 space-y-2">
            <div class="flex items-center justify-between text-xs font-mono font-bold text-slate-300">
                <span class="flex items-center gap-1.5 text-slate-400">
                    <i class="fa-solid fa-filter text-purple-400"></i>
                    <span>Pruned Candidates (${eliminatedKeys.length} Coins Eliminated by Qwen)</span>
                </span>
                <span class="text-[10px] text-slate-500">Reasons candidates were rejected</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] font-mono">
                ${eliminatedKeys.map(sym => `
                    <div class="p-2 rounded bg-slate-950/80 border border-slate-800/80 space-y-0.5">
                        <div class="font-bold text-slate-200">${sym}:</div>
                        <div class="text-slate-400 text-[10px] font-sans leading-tight">${eliminated[sym]}</div>
                    </div>
                `).join('')}
            </div>
        </div>
    ` : '';

    // Build Stage 3 & Confluence HTML
    const isLong = s3.direction === 'LONG';
    const sideColor = isLong ? 'emerald' : 'rose';
    const isEligible = s3.strategy_eligible && s3.status !== 'REJECTED';

    let verdictBadgeHtml = isEligible
        ? `<span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950 text-emerald-300 border border-emerald-600"><i class="fa-solid fa-check mr-1"></i>CONFLUENCE APPROVED FOR EXECUTION</span>`
        : `<span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950 text-amber-300 border border-amber-600"><i class="fa-solid fa-shield-halved mr-1"></i>CONFLUENCE VETO / REJECTED</span>`;

    const confluenceHtml = `
        <div class="p-4 rounded-xl bg-gradient-to-br from-slate-900 via-purple-950/20 to-slate-900 border-2 border-purple-500/70 space-y-3 font-mono text-xs">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-purple-900/40 pb-2.5">
                <div class="flex items-center gap-2">
                    <span class="px-2 py-0.5 rounded text-[11px] font-bold bg-${sideColor}-950 text-${sideColor}-300 border border-${sideColor}-800">
                        STAGE 3 WINNER: ${s3.direction} ${s3.symbol}
                    </span>
                    <span class="text-purple-300 font-bold">@ $${s3.entry_price || '--'}</span>
                    ${verdictBadgeHtml}
                </div>
                <div class="text-slate-400 text-[11px]">
                    Risk: <strong class="text-amber-300">$${(s3.max_risk_usdt || 0.20).toFixed(2)}</strong> &bull;
                    Stop: <strong class="text-rose-400">${s3.stop_distance_pct || 1.25}%</strong> &bull;
                    Net-R:R: <strong class="text-emerald-400">${s3.expected_net_rr || s3.rr_ratio || 1.50}R</strong>
                </div>
            </div>

            <!-- Trade Numbers -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
                <div class="p-2 rounded bg-slate-950/80 border border-slate-800">Entry Limit: <strong class="text-cyan-300">$${s3.entry_price || '--'}</strong></div>
                <div class="p-2 rounded bg-slate-950/80 border border-slate-800">Stop Loss: <strong class="text-rose-400">$${s3.stop_loss_price || '--'}</strong></div>
                <div class="p-2 rounded bg-slate-950/80 border border-slate-800">Take Profit: <strong class="text-emerald-400">$${s3.take_profit_price || '--'}</strong></div>
                <div class="p-2 rounded bg-slate-950/80 border border-slate-800">Order Qty: <strong class="text-white">${s3.qty || '--'}</strong></div>
            </div>

            <!-- 4 Dashboard Confluence Gates Audit -->
            <div class="p-3 rounded-lg bg-slate-950/90 border border-slate-800 space-y-2">
                <div class="text-[11px] font-bold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
                    <i class="fa-solid fa-scale-balanced text-purple-400"></i>
                    <span>Dashboard Intelligence Confluence Audit:</span>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] font-sans">
                    <div class="p-2 rounded bg-slate-900/80 border border-slate-800 space-y-1">
                        <div class="font-bold text-slate-200 font-mono text-[10px]">1. 15M Sweep &amp; Reclaim:</div>
                        <div class="text-slate-300 text-[10px] leading-relaxed">${s3.sweep_reclaim_evidence || 'Confirmed structural sweep at dealing range discount boundary.'}</div>
                    </div>
                    <div class="p-2 rounded bg-slate-900/80 border border-slate-800 space-y-1">
                        <div class="font-bold text-slate-200 font-mono text-[10px]">2. Order Flow &amp; Walls:</div>
                        <div class="text-slate-300 text-[10px] leading-relaxed">${s3.orderflow_wall_evidence || 'Orderbook depth verified; no opposing wall blocking entry path.'}</div>
                    </div>
                    <div class="p-2 rounded bg-slate-900/80 border border-slate-800 space-y-1">
                        <div class="font-bold text-slate-200 font-mono text-[10px]">3. Anti-Revenge Memory Guard:</div>
                        <div class="text-slate-300 text-[10px] leading-relaxed">${s3.why_not_repeating_past_sl || 'Priced structurally outside previous 6-hour stop-loss liquidity zones.'}</div>
                    </div>
                    <div class="p-2 rounded bg-slate-900/80 border border-slate-800 space-y-1">
                        <div class="font-bold text-slate-200 font-mono text-[10px]">4. Stop Breathing Room:</div>
                        <div class="text-slate-300 text-[10px] leading-relaxed">Stop distance: <strong class="font-mono text-white">${s3.stop_distance_pct}%</strong> (Safety bounds: 1.15% - 1.45%).</div>
                    </div>
                </div>

                ${!isEligible ? `
                    <div class="p-2 rounded bg-amber-950/60 border border-amber-700/80 text-amber-200 text-[11px] font-sans">
                        <strong class="font-mono text-amber-300">VETO REASON:</strong> ${s3.strategy_rejection_reason || s3.rejection_reason || 'Candidate did not meet all 4 hard confluence gates; order was safely withheld.'}
                    </div>
                ` : ''}
            </div>
        </div>
    `;

    // Render Full Content Container
    content.innerHTML = `
        <!-- STAGE 1: MARKET SCREEN (TOP 10) -->
        <div class="space-y-2">
            <div class="flex items-center justify-between text-xs font-mono font-bold text-slate-300">
                <span class="flex items-center gap-1.5 text-purple-300">
                    <span class="w-5 h-5 rounded-full bg-purple-900 text-purple-200 flex items-center justify-center text-[10px]">1</span>
                    <span>Stage 1: Aster DEX Market Discovery (${top10.length} Pairs Screened)</span>
                </span>
                <span class="text-[10px] text-slate-400 font-normal">
                    Regime: <strong class="text-white">${regime.regime || 'CHOP_RANGE'}</strong> &bull;
                    BTC 24h: <strong class="${(regime.btc_change_24h || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${(regime.btc_change_24h || 0) >= 0 ? '+' : ''}${Number(regime.btc_change_24h || 0).toFixed(2)}%</strong> &bull;
                    Screened in ${Number(s1.execution_time_ms || 0).toFixed(0)}ms
                </span>
            </div>
            <div class="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950/60">
                <table class="w-full text-left font-mono text-[11px]">
                    <thead class="bg-slate-900 text-slate-400 text-[10px] uppercase border-b border-slate-800">
                        <tr>
                            <th class="px-2.5 py-1.5">#</th>
                            <th class="px-2.5 py-1.5">Symbol</th>
                            <th class="px-2.5 py-1.5">Mark Price</th>
                            <th class="px-2.5 py-1.5">24h Chg</th>
                            <th class="px-2.5 py-1.5">RS vs BTC</th>
                            <th class="px-2.5 py-1.5">Turnover</th>
                            <th class="px-2.5 py-1.5">72h Range</th>
                            <th class="px-2.5 py-1.5">Python Screener Rationale</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-800/60 text-slate-300">
                        ${stage1Rows}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- STAGE 2: GROQ QWEN TOP 3 RANKING & ELIMINATION -->
        <div class="space-y-2 pt-2">
            <div class="flex items-center justify-between text-xs font-mono font-bold text-slate-300">
                <span class="flex items-center gap-1.5 text-purple-300">
                    <span class="w-5 h-5 rounded-full bg-purple-900 text-purple-200 flex items-center justify-center text-[10px]">2</span>
                    <span>Stage 2: Groq Qwen Finalists Selection (10 &rarr; Top 3)</span>
                </span>
                <span class="text-[10px] text-slate-400 font-mono">
                    Model: <strong class="text-purple-300">${s2.model_used || 'qwen/qwen3.8-27b'}</strong>
                </span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                ${stage2FinalistsHtml}
            </div>
            ${eliminatedHtml}
        </div>

        <!-- STAGE 3 & 4: SETUP BRACKET & CONFLUENCE AUDIT -->
        <div class="space-y-2 pt-2">
            <div class="flex items-center justify-between text-xs font-mono font-bold text-slate-300">
                <span class="flex items-center gap-1.5 text-purple-300">
                    <span class="w-5 h-5 rounded-full bg-purple-900 text-purple-200 flex items-center justify-center text-[10px]">3</span>
                    <span>Stage 3 &amp; 4: Selected Setup &amp; Confluence Audit (Top 3 &rarr; #1)</span>
                </span>
                <span class="text-[10px] text-slate-400 font-mono">
                    Strategy: <strong class="text-purple-300">15M_SWEEP_RECLAIM_SMC</strong>
                </span>
            </div>
            ${confluenceHtml}
        </div>
    `;
}

// Start polling live session state & screening log
if (typeof window !== 'undefined') {
    fetchLiveSessionState();
    refreshScreeningFeed(false);
    if (!liveSessionPollTimer) {
        liveSessionPollTimer = setInterval(fetchLiveSessionState, 5000);
    }
}
