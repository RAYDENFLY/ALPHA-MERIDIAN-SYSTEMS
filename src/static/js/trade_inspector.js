// =========================================================================
// MODEL B & UNIVERSAL PAPER TRADE CHART INSPECTOR (TRADINGVIEW-GRADE UX)
// Intelligent Auto-Scaling, Fit Price Action vs Fit All, Off-Screen Pills,
// Synchronized Crosshair Legend, ResizeObserver, & Multi-Venue OHLCV
// =========================================================================

let inspectorChart = null;
let inspectorCandleSeries = null;
let inspectorEmaSeries = {};
let inspectorPriceLines = [];
let inspectorResizeObserver = null;

let currentInspectedTrade = null;
let currentInspectorCandles = [];
let currentInspectorContextHours = 24; // 12, 24, 48, 72
let currentInspectorFitMode = 'PRICE_ACTION'; // 'PRICE_ACTION' or 'ALL'
let currentVisiblePriceRange = { min: null, max: null };

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

function roundTo6(val) {
    return Math.round(val * 1000000) / 1000000;
}

function calculateEMA(data, period) {
    if (!data || data.length < period) return [];
    const k = 2 / (period + 1);
    const emaArray = [];
    
    let sum = 0;
    for (let i = 0; i < period; i++) {
        sum += data[i].close;
    }
    let prevEma = sum / period;
    emaArray.push({ time: data[period - 1].time, value: roundTo6(prevEma) });

    for (let i = period; i < data.length; i++) {
        const currentEma = (data[i].close * k) + (prevEma * (1 - k));
        emaArray.push({ time: data[i].time, value: roundTo6(currentEma) });
        prevEma = currentEma;
    }
    return emaArray;
}

function calculateStochastic(data, periodK = 14, smoothK = 3, periodD = 3) {
    if (!data || data.length < periodK) return { kLine: [], dLine: [] };
    const rawK = [];
    for (let i = periodK - 1; i < data.length; i++) {
        let lowestLow = Infinity;
        let highestHigh = -Infinity;
        for (let j = i - periodK + 1; j <= i; j++) {
            if (data[j].low < lowestLow) lowestLow = data[j].low;
            if (data[j].high > highestHigh) highestHigh = data[j].high;
        }
        const denom = highestHigh - lowestLow;
        const currentClose = data[i].close;
        const fastK = denom === 0 ? 50 : ((currentClose - lowestLow) / denom) * 100;
        rawK.push({ time: data[i].time, val: fastK });
    }

    const smoothedK = [];
    for (let i = smoothK - 1; i < rawK.length; i++) {
        let sum = 0;
        for (let j = i - smoothK + 1; j <= i; j++) {
            sum += rawK[j].val;
        }
        smoothedK.push({ time: rawK[i].time, value: Math.round((sum / smoothK) * 100) / 100 });
    }

    const lineD = [];
    for (let i = periodD - 1; i < smoothedK.length; i++) {
        let sum = 0;
        for (let j = i - periodD + 1; j <= i; j++) {
            sum += smoothedK[j].value;
        }
        lineD.push({ time: smoothedK[i].time, value: Math.round((sum / periodD) * 100) / 100 });
    }

    return { kLine: smoothedK, dLine: lineD };
}

/**
 * Compute price range for "PRICE_ACTION" vs "ALL" mode.
 * Returns { minPrice, maxPrice, offscreenAbove: [], offscreenBelow: [] }
 */
function computeInspectorPriceRange(candles, trade, fitMode) {
    if (!candles || candles.length === 0) {
        return { minPrice: null, maxPrice: null, offscreenAbove: [], offscreenBelow: [] };
    }

    let cMin = Infinity;
    let cMax = -Infinity;
    candles.forEach(c => {
        if (c.low < cMin) cMin = c.low;
        if (c.high > cMax) cMax = c.high;
    });

    const entryPx = Number(trade.entry_price || 0);
    const markPx = Number(trade.mark_price || trade.exit_price || 0);
    const slPx = Number(trade.stop_loss || trade.stop_loss_price || 0);
    const tp1Px = Number(trade.take_profit || trade.take_profit_price || trade.take_profit_1 || 0);
    const tp2Px = Number(trade.take_profit_2 || 0);

    // Core price action always includes visible candles + entry + mark/exit
    let coreMin = cMin;
    let coreMax = cMax;
    if (entryPx > 0) {
        coreMin = Math.min(coreMin, entryPx);
        coreMax = Math.max(coreMax, entryPx);
    }
    if (markPx > 0) {
        coreMin = Math.min(coreMin, markPx);
        coreMax = Math.max(coreMax, markPx);
    }

    const coreSpan = Math.max(coreMax - coreMin, (coreMax || 1) * 0.005);

    const levels = [];
    if (slPx > 0) levels.push({ key: 'SL', label: 'Stop Loss', price: slPx, color: 'rose' });
    if (tp1Px > 0) levels.push({ key: 'TP1', label: 'TP1', price: tp1Px, color: 'emerald' });
    if (tp2Px > 0) levels.push({ key: 'TP2', label: 'TP2', price: tp2Px, color: 'teal' });

    if (fitMode === 'ALL') {
        let allMin = coreMin;
        let allMax = coreMax;
        levels.forEach(lv => {
            allMin = Math.min(allMin, lv.price);
            allMax = Math.max(allMax, lv.price);
        });
        const pad = (allMax - allMin) * 0.06;
        return {
            minPrice: allMin - pad,
            maxPrice: allMax + pad,
            offscreenAbove: [],
            offscreenBelow: []
        };
    }

    // FIT_PRICE_ACTION mode:
    // Only include a protection level automatically if it is within 35% of the core candle span
    // Otherwise keep vertical scale focused tightly on price action and flag distant levels as off-screen pills
    let actionMin = coreMin;
    let actionMax = coreMax;
    const proximityThreshold = coreSpan * 0.35;

    levels.forEach(lv => {
        if (lv.price >= coreMin - proximityThreshold && lv.price <= coreMax + proximityThreshold) {
            actionMin = Math.min(actionMin, lv.price);
            actionMax = Math.max(actionMax, lv.price);
        }
    });

    const pad = (actionMax - actionMin) * 0.08;
    const finalMin = actionMin - pad;
    const finalMax = actionMax + pad;

    const offscreenAbove = [];
    const offscreenBelow = [];

    levels.forEach(lv => {
        if (lv.price > finalMax) {
            offscreenAbove.push(lv);
        } else if (lv.price < finalMin) {
            offscreenBelow.push(lv);
        }
    });

    return {
        minPrice: finalMin,
        maxPrice: finalMax,
        offscreenAbove,
        offscreenBelow
    };
}

async function openTradeInspectorModal(tradeData) {
    if (!tradeData) return;
    currentInspectedTrade = tradeData;

    // Reset state cleanly so switching between positions never retains stale viewport
    currentInspectorCandles = [];
    currentInspectorFitMode = 'PRICE_ACTION';
    updateFitModeButtonsUI();
    updateContextButtonsUI();

    const modal = document.getElementById('tradeInspectorModal');
    if (!modal) return;
    modal.classList.remove('hidden');

    renderInspectorHeader(tradeData);
    renderInspectorDetails(tradeData);

    initInspectorChartContainer(tradeData);
    await loadInspectorChartData(tradeData);
}

function closeTradeInspectorModal() {
    const modal = document.getElementById('tradeInspectorModal');
    if (modal) modal.classList.add('hidden');

    if (inspectorResizeObserver) {
        try { inspectorResizeObserver.disconnect(); } catch (e) {}
        inspectorResizeObserver = null;
    }

    if (inspectorChart) {
        try { inspectorChart.remove(); } catch (e) {}
        inspectorChart = null;
        inspectorCandleSeries = null;
        inspectorEmaSeries = {};
        inspectorPriceLines = [];
    }
    currentInspectedTrade = null;
    currentInspectorCandles = [];
}

function renderInspectorHeader(t) {
    const titleEl = document.getElementById('inspectorModalTitle');
    const badgeContainer = document.getElementById('inspectorHeaderBadges');
    const legendSym = document.getElementById('inspectorLegendSymbol');
    if (!titleEl || !badgeContainer) return;

    const sym = t.symbol || 'UNKNOWN';
    const side = (t.direction || t.side || 'LONG').toUpperCase();
    const isLong = side === 'LONG';
    const isClosed = !t.is_open && Boolean(t.exit_timestamp_ms || t.exit_price);
    const outcome = t.outcome || (isClosed ? 'CLOSED' : 'OPEN');
    const tf = (t.timeframe || '1h').toUpperCase();

    titleEl.innerText = `${sym} ${side} Paper Position`;
    if (legendSym) legendSym.innerText = `${sym} \u2022 ${tf}`;

    let outcomeCls = 'bg-slate-800 text-slate-300 border-slate-700';
    if (outcome === 'WIN') outcomeCls = 'bg-emerald-950/90 text-emerald-300 border-emerald-600 font-bold';
    else if (outcome === 'LOSS') outcomeCls = 'bg-rose-950/90 text-rose-300 border-rose-600 font-bold';
    else if (outcome === 'OPEN') outcomeCls = 'bg-blue-950/90 text-blue-300 border-blue-600 font-bold animate-pulse';

    const sideCls = isLong
        ? 'bg-emerald-950/80 text-emerald-400 border-emerald-700'
        : 'bg-rose-950/80 text-rose-400 border-rose-700';

    const venue = t.signal_source || t.data_source || 'AsterDEX';
    const isModelA = String(t.model_id || '').toUpperCase() === 'MODEL_A';
    const modelBadge = isModelA
        ? '<span class="px-1.5 py-0.5 rounded text-[10px] font-mono bg-blue-950 text-blue-300 border border-blue-800">MODEL A PAPER</span>'
        : '<span class="px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-950 text-amber-300 border border-amber-800">MODEL B PAPER</span>';

    const isV2 = t.cohort === 'V2' || t.cohort_version === 'MODEL_B_V2_REMEDIATED';
    const cohortBadge = !isModelA
        ? `<span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold ${isV2 ? 'bg-purple-950 text-purple-300 border border-purple-500' : 'bg-slate-800 text-slate-400 border border-slate-700'}">${isV2 ? 'COHORT V2' : 'COHORT V1'}</span>`
        : '';

    badgeContainer.innerHTML = `
        <span class="px-2 py-0.5 rounded text-[11px] font-bold border ${sideCls}">${side} ${t.leverage || 10}x</span>
        <span class="px-2 py-0.5 rounded text-[11px] font-bold border ${outcomeCls}">${outcome}</span>
        <span class="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-300 border border-slate-700">Venue: ${escapeHtml(venue)}</span>
        ${modelBadge}
        ${cohortBadge}
    `;
}

function renderInspectorDetails(t) {
    const container = document.getElementById('inspectorDetailsContent');
    if (!container) return;

    const entryTs = t.entry_timestamp_ms;
    const exitTs = t.exit_timestamp_ms;
    const entryWib = formatJakartaTime(entryTs);
    const exitWib = exitTs ? formatJakartaTime(exitTs) : (t.is_open ? '<span class="text-blue-400 font-semibold animate-pulse">Position Active</span>' : '--');

    const notional = Number(t.notional_usdt || ((t.entry_price || 0) * (t.quantity || 0))).toFixed(2);
    const margin = Number(t.margin_usdt || t.allocated_margin_usdt || (notional / (t.leverage || 10))).toFixed(2);

    const netPnlVal = t.pnl_net !== undefined && t.pnl_net !== null ? Number(t.pnl_net) : (t.unrealized_pnl_net_usdt !== undefined ? Number(t.unrealized_pnl_net_usdt) : 0);
    const pnlSign = netPnlVal >= 0 ? '+' : '';
    const pnlCls = netPnlVal > 0 ? 'text-emerald-400' : (netPnlVal < 0 ? 'text-rose-400' : 'text-slate-300');

    const markPx = t.mark_price !== null && t.mark_price !== undefined ? ('$' + formatPrice(t.mark_price)) : '--';
    const exitPx = t.exit_price ? ('$' + formatPrice(t.exit_price)) : (t.is_open ? markPx : '--');

    const tp1Val = t.take_profit || t.take_profit_price || t.take_profit_1;
    const tp2Val = t.take_profit_2;

    const maeDisplay = t.mae_pct ? (t.mae_pct > 0 ? `-${Number(t.mae_pct).toFixed(2)}%` : `${Number(t.mae_pct).toFixed(2)}%`) : '--';
    const mfeDisplay = t.mfe_pct ? (t.mfe_pct > 0 ? `+${Number(t.mfe_pct).toFixed(2)}%` : `${Number(t.mfe_pct).toFixed(2)}%`) : '--';

    container.innerHTML = `
        <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 text-xs font-mono">
            <div class="p-2.5 rounded-lg bg-slate-900/70 border border-slate-800">
                <span class="text-[10px] text-slate-500 uppercase block">Entry Price</span>
                <span class="text-sm font-bold text-white">$${formatPrice(t.entry_price)}</span>
                <span class="text-[10px] text-slate-400 block truncate" title="${entryWib}">${entryWib}</span>
            </div>
            <div class="p-2.5 rounded-lg bg-slate-900/70 border border-slate-800">
                <span class="text-[10px] text-slate-500 uppercase block">${t.is_open ? 'Current Mark Px' : 'Exit Price'}</span>
                <span class="text-sm font-bold ${t.is_open ? 'text-indigo-300' : 'text-white'}">${exitPx}</span>
                <span class="text-[10px] text-slate-400 block truncate" title="${exitWib}">${exitWib}</span>
            </div>
            <div class="p-2.5 rounded-lg bg-slate-900/70 border border-slate-800">
                <span class="text-[10px] text-slate-500 uppercase block">Protection Levels</span>
                <div class="text-[11px]"><span class="text-rose-400 font-semibold">SL:</span> $${formatPrice(t.stop_loss || t.stop_loss_price)}</div>
                <div class="text-[11px]"><span class="text-emerald-400 font-semibold">TP1:</span> $${formatPrice(tp1Val)} ${tp2Val ? `<span class="text-teal-400 ml-1">TP2: $${formatPrice(tp2Val)}</span>` : ''}</div>
            </div>
            <div class="p-2.5 rounded-lg bg-slate-900/70 border border-slate-800">
                <span class="text-[10px] text-slate-500 uppercase block">Size &amp; Margin</span>
                <div class="text-white font-semibold">${t.quantity || t.position_amt || '--'} ${(t.symbol || '').replace(/USDT$/i, '')}</div>
                <div class="text-[10px] text-slate-400">$${margin} Margin ($${notional} Notional)</div>
            </div>
            <div class="p-2.5 rounded-lg bg-slate-900/70 border border-slate-800">
                <span class="text-[10px] text-slate-500 uppercase block">${t.is_open ? 'Floating Net UPnL' : 'Realized Net PnL'}</span>
                <div class="text-sm font-bold ${pnlCls}">${pnlSign}$${netPnlVal.toFixed(3)} USDT</div>
                <div class="text-[10px] text-slate-400">Duration: ${t.duration_display || (t.is_open ? 'Active' : '--')}</div>
            </div>
            <div class="p-2.5 rounded-lg bg-slate-900/70 border border-slate-800">
                <span class="text-[10px] text-slate-500 uppercase block">Excursion &amp; Reason</span>
                <div class="text-[11px] text-slate-300">MFE: <span class="text-emerald-400 font-semibold">${mfeDisplay}</span> / MAE: <span class="text-rose-400 font-semibold">${maeDisplay}</span></div>
                <div class="text-[10px] text-indigo-300 truncate" title="${t.entry_reason || t.entry_category || 'Setup'}">${t.entry_reason || t.entry_category || 'Setup'}</div>
            </div>
        </div>
    `;
}

function initInspectorChartContainer(t) {
    const container = document.getElementById('inspectorChartCanvas');
    if (!container) return;
    container.innerHTML = '';

    if (inspectorResizeObserver) {
        try { inspectorResizeObserver.disconnect(); } catch (e) {}
        inspectorResizeObserver = null;
    }

    if (inspectorChart) {
        try { inspectorChart.remove(); } catch (e) {}
        inspectorChart = null;
    }

    const width = container.clientWidth || 850;
    const height = 460;
    const sym = (t && t.symbol) ? t.symbol : '';

    inspectorChart = LightweightCharts.createChart(container, {
        width: width,
        height: height,
        layout: {
            background: { color: '#090d16' },
            textColor: '#94a3b8',
            fontSize: 11,
        },
        watermark: {
            visible: true,
            fontSize: 28,
            horzAlign: 'center',
            vertAlign: 'center',
            color: 'rgba(148, 163, 184, 0.04)',
            text: `${sym} PAPER INSPECTOR`,
        },
        grid: {
            vertLines: { color: 'rgba(30, 41, 59, 0.35)' },
            horzLines: { color: 'rgba(30, 41, 59, 0.35)' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                width: 1,
                color: 'rgba(148, 163, 184, 0.4)',
                style: LightweightCharts.LineStyle.Dashed,
                labelBackgroundColor: '#1e293b',
            },
            horzLine: {
                width: 1,
                color: 'rgba(148, 163, 184, 0.4)',
                style: LightweightCharts.LineStyle.Dashed,
                labelBackgroundColor: '#1e293b',
            },
        },
        handleScroll: {
            mouseWheel: true,
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: true,
        },
        handleScale: {
            axisPressedMouseMove: {
                time: true,
                price: true,
            },
            axisDoubleClickReset: {
                time: true,
                price: true,
            },
            mouseWheel: true,
            pinch: true,
        },
        rightPriceScale: {
            borderColor: '#1e293b',
            autoScale: true,
            scaleMargins: {
                top: 0.08,
                bottom: 0.08,
            },
        },
        timeScale: {
            borderColor: '#1e293b',
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 6,
            barSpacing: 12,
            minBarSpacing: 4,
        },
    });

    // Determine precision based on entry price
    let precision = 2;
    let minMove = 0.01;
    const refPx = Number((t && t.entry_price) || 100);
    if (refPx < 0.01) { precision = 6; minMove = 0.000001; }
    else if (refPx < 1) { precision = 5; minMove = 0.00001; }
    else if (refPx < 10) { precision = 4; minMove = 0.0001; }
    else if (refPx < 1000) { precision = 3; minMove = 0.001; }

    inspectorCandleSeries = inspectorChart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#ef4444',
        borderVisible: true,
        borderUpColor: '#10b981',
        borderDownColor: '#ef4444',
        wickUpColor: '#10b981',
        wickDownColor: '#ef4444',
        priceFormat: {
            type: 'price',
            precision: precision,
            minMove: minMove,
        },
        autoscaleInfoProvider: () => {
            if (!currentInspectorCandles || currentInspectorCandles.length === 0 || !currentInspectedTrade) {
                return null;
            }
            const rangeInfo = computeInspectorPriceRange(currentInspectorCandles, currentInspectedTrade, currentInspectorFitMode);
            if (rangeInfo.minPrice === null || rangeInfo.maxPrice === null) {
                return null;
            }
            currentVisiblePriceRange = { min: rangeInfo.minPrice, max: rangeInfo.maxPrice };
            return {
                priceRange: {
                    minValue: rangeInfo.minPrice,
                    maxValue: rangeInfo.maxPrice,
                },
            };
        },
    });

    // Subscribe to crosshair move for live OHLCV readout
    inspectorChart.subscribeCrosshairMove(param => {
        const legendEl = document.getElementById('inspectorLegendOHLC');
        if (!legendEl) return;
        if (!param || !param.time || !param.seriesData) {
            if (currentInspectorCandles.length > 0) {
                const last = currentInspectorCandles[currentInspectorCandles.length - 1];
                updateLegendOHLC(legendEl, last);
            }
            return;
        }
        const data = param.seriesData.get(inspectorCandleSeries);
        if (data) {
            updateLegendOHLC(legendEl, data);
        }
    });

    // Double-click chart canvas to restore default viewport
    container.addEventListener('dblclick', () => {
        resetInspectorZoom();
    });

    // ResizeObserver for modal and sidebar transitions
    if (typeof ResizeObserver !== 'undefined') {
        inspectorResizeObserver = new ResizeObserver(entries => {
            for (const entry of entries) {
                const newWidth = entry.contentRect.width;
                if (inspectorChart && newWidth > 0) {
                    inspectorChart.applyOptions({ width: newWidth });
                }
            }
        });
        inspectorResizeObserver.observe(container);
    }
}

function updateLegendOHLC(el, c) {
    if (!c || c.open === undefined) return;
    const isBull = c.close >= c.open;
    const cls = isBull ? 'text-emerald-400' : 'text-rose-400';
    const volStr = c.volume !== undefined ? `V: ${Number(c.volume).toLocaleString()}` : '';
    el.innerHTML = `
        <span>O: <strong class="${cls}">${formatPrice(c.open)}</strong></span>
        <span>H: <strong class="${cls}">${formatPrice(c.high)}</strong></span>
        <span>L: <strong class="${cls}">${formatPrice(c.low)}</strong></span>
        <span>C: <strong class="${cls}">${formatPrice(c.close)}</strong></span>
        ${volStr ? `<span class="text-slate-400 ml-1">${volStr}</span>` : ''}
    `;
}

async function loadInspectorChartData(t) {
    const loadingBanner = document.getElementById('inspectorChartLoading');
    const warningBanner = document.getElementById('inspectorChartWarning');
    const warningText = document.getElementById('inspectorChartWarningText');

    if (loadingBanner) loadingBanner.classList.remove('hidden');
    if (warningBanner) warningBanner.classList.add('hidden');

    const symbol = t.symbol;
    const timeframe = t.timeframe || '1h';
    const entryTs = t.entry_timestamp_ms;
    const exitTs = t.exit_timestamp_ms;
    const nowMs = Date.now();

    // Calculate candle limit and time window based on selected context hours (12H, 24H, 48H, 72H)
    const contextMs = currentInspectorContextHours * 3600 * 1000;
    let startMs = entryTs ? Math.max(0, entryTs - contextMs) : (nowMs - contextMs);
    let endMs = exitTs ? (exitTs + Math.min(contextMs, 24 * 3600 * 1000)) : null;

    // Calculate appropriate bar limit so we fetch enough candles for the window + EMA warmup
    const barCountNeeded = Math.min(500, Math.max(60, currentInspectorContextHours * 2 + 50));

    const venue = t.signal_source || t.data_source || 'asterdex';

    const params = new URLSearchParams();
    params.append('symbol', symbol);
    params.append('timeframe', timeframe);
    params.append('limit', String(barCountNeeded));
    if (startMs) params.append('start_time_ms', String(startMs));
    if (endMs) params.append('end_time_ms', String(endMs));
    if (venue) params.append('venue', venue);

    try {
        const res = await fetch(`/api/chart/klines?${params.toString()}`);
        if (!res.ok) {
            throw new Error(`Klines API HTTP error ${res.status}`);
        }
        let candles = await res.json();

        // Filter or slice candles to match the requested context window around entry/current time
        if (candles && candles.length > 0 && entryTs) {
            const cutoffStartSec = Math.floor((entryTs - contextMs) / 1000);
            const filtered = candles.filter(c => c.time >= cutoffStartSec);
            if (filtered.length >= 8) {
                candles = filtered;
            } else {
                candles = candles.slice(-Math.max(12, currentInspectorContextHours));
            }
        } else if (candles && candles.length > currentInspectorContextHours) {
            candles = candles.slice(-currentInspectorContextHours);
        }

        currentInspectorCandles = candles || [];

        if (currentInspectorCandles.length === 0) {
            if (warningBanner && warningText) {
                warningBanner.classList.remove('hidden');
                warningText.innerText = `Data-Availability Notice: No historical ${timeframe} candles could be retrieved for ${symbol} on ${venue}.`;
            }
            renderOffscreenIndicators([]);
            return;
        }

        // Set candlestick data
        inspectorCandleSeries.setData(currentInspectorCandles);

        // Update initial OHLC legend with latest bar
        const legendEl = document.getElementById('inspectorLegendOHLC');
        if (legendEl) {
            updateLegendOHLC(legendEl, currentInspectorCandles[currentInspectorCandles.length - 1]);
        }

        // Apply Indicators
        applyInspectorEMAs(currentInspectorCandles);
        applyInspectorStochastic(currentInspectorCandles);

        // Apply Trade Markers & Price Lines (Entry, Mark, SL, TP1, TP2)
        applyTradeMarkersAndLevels(t, currentInspectorCandles);

        // Apply viewport scaling and offscreen indicators
        applyInspectorViewportScaling();

    } catch (err) {
        console.error("Inspector chart data fetch error:", err);
        if (warningBanner && warningText) {
            warningBanner.classList.remove('hidden');
            warningText.innerText = `Data-Availability Warning: Failed to load market candles (${err.message}).`;
        }
    } finally {
        if (loadingBanner) loadingBanner.classList.add('hidden');
    }
}

function applyInspectorViewportScaling() {
    if (!inspectorChart || !inspectorCandleSeries || !currentInspectedTrade) return;

    // Reset autoScale on rightPriceScale so autoscaleInfoProvider takes effect cleanly
    inspectorChart.priceScale('right').applyOptions({
        autoScale: true,
    });

    // Adjust barSpacing dynamically so smaller windows (12H/24H) have wider, clearer candles
    const count = currentInspectorCandles.length;
    const container = document.getElementById('inspectorChartCanvas');
    const width = (container && container.clientWidth) ? container.clientWidth : 850;
    if (count > 0) {
        const idealSpacing = Math.max(6, Math.min(28, Math.floor((width * 0.82) / count)));
        inspectorChart.timeScale().applyOptions({
            barSpacing: idealSpacing,
            rightOffset: 5,
        });
    }

    inspectorChart.timeScale().fitContent();

    // Update off-screen indicators
    const rangeInfo = computeInspectorPriceRange(currentInspectorCandles, currentInspectedTrade, currentInspectorFitMode);
    renderOffscreenIndicators(rangeInfo.offscreenAbove, rangeInfo.offscreenBelow);
}

function renderOffscreenIndicators(aboveList = [], belowList = []) {
    const topEl = document.getElementById('inspectorOffscreenTop');
    const botEl = document.getElementById('inspectorOffscreenBottom');
    if (!topEl || !botEl) return;

    if (!aboveList || aboveList.length === 0) {
        topEl.classList.add('hidden');
        topEl.innerHTML = '';
    } else {
        topEl.classList.remove('hidden');
        topEl.innerHTML = aboveList.map(lv => {
            const cls = lv.color === 'rose'
                ? 'bg-rose-950/90 text-rose-300 border-rose-700 hover:bg-rose-900'
                : 'bg-emerald-950/90 text-emerald-300 border-emerald-700 hover:bg-emerald-900';
            return `
                <button type="button" onclick="setInspectorFitMode('ALL')" class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border shadow-md transition flex items-center gap-1 ${cls}" title="Click to expand chart scale to view ${lv.label}">
                    <span>\u2191 ${lv.label}: $${formatPrice(lv.price)} (Off-Screen)</span>
                </button>
            `;
        }).join('');
    }

    if (!belowList || belowList.length === 0) {
        botEl.classList.add('hidden');
        botEl.innerHTML = '';
    } else {
        botEl.classList.remove('hidden');
        botEl.innerHTML = belowList.map(lv => {
            const cls = lv.color === 'rose'
                ? 'bg-rose-950/90 text-rose-300 border-rose-700 hover:bg-rose-900'
                : 'bg-emerald-950/90 text-emerald-300 border-emerald-700 hover:bg-emerald-900';
            return `
                <button type="button" onclick="setInspectorFitMode('ALL')" class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border shadow-md transition flex items-center gap-1 ${cls}" title="Click to expand chart scale to view ${lv.label}">
                    <span>\u2193 ${lv.label}: $${formatPrice(lv.price)} (Off-Screen)</span>
                </button>
            `;
        }).join('');
    }
}

function onInspectorIndicatorToggle() {
    if (!currentInspectedTrade || !currentInspectorCandles || currentInspectorCandles.length === 0) return;
    applyInspectorEMAs(currentInspectorCandles);
    applyInspectorStochastic(currentInspectorCandles);
}

function applyInspectorEMAs(candles) {
    const emaToggle = document.getElementById('inspectorToggleEMA');
    const showEMA = emaToggle ? emaToggle.checked : true;

    Object.values(inspectorEmaSeries).forEach(series => {
        try { inspectorChart.removeSeries(series); } catch (e) {}
    });
    inspectorEmaSeries = {};

    if (!showEMA) return;

    const configs = [
        { period: 20, color: '#38bdf8', title: 'EMA 20' },
        { period: 50, color: '#818cf8', title: 'EMA 50' },
        { period: 100, color: '#f59e0b', title: 'EMA 100' },
        { period: 200, color: '#ec4899', title: 'EMA 200' },
    ];

    configs.forEach(cfg => {
        const emaData = calculateEMA(candles, cfg.period);
        if (emaData && emaData.length > 0) {
            const series = inspectorChart.addLineSeries({
                color: cfg.color,
                lineWidth: 1,
                title: cfg.title,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
                autoscaleInfoProvider: () => null, // Never let historical EMA outliers compress price action
            });
            series.setData(emaData);
            inspectorEmaSeries[cfg.period] = series;
        }
    });
}

function applyInspectorStochastic(candles) {
    const stochToggle = document.getElementById('inspectorToggleStoch');
    const showStoch = stochToggle ? stochToggle.checked : false;
    const stochContainer = document.getElementById('inspectorStochPane');

    if (!showStoch) {
        if (stochContainer) stochContainer.classList.add('hidden');
        return;
    }

    if (stochContainer) {
        stochContainer.classList.remove('hidden');
        const stochData = calculateStochastic(candles, 14, 3, 3);
        const lastK = stochData.kLine.length > 0 ? stochData.kLine[stochData.kLine.length - 1].value : '--';
        const lastD = stochData.dLine.length > 0 ? stochData.dLine[stochData.dLine.length - 1].value : '--';
        const kEl = document.getElementById('inspectorStochKVal');
        const dEl = document.getElementById('inspectorStochDVal');
        if (kEl) kEl.innerText = lastK;
        if (dEl) dEl.innerText = lastD;
    }
}

function applyTradeMarkersAndLevels(t, candles) {
    const markers = [];
    const isLong = (t.direction || t.side || 'LONG').toUpperCase() === 'LONG';
    const entryTsSec = t.entry_timestamp_ms ? Math.floor(t.entry_timestamp_ms / 1000) : null;
    const exitTsSec = t.exit_timestamp_ms ? Math.floor(t.exit_timestamp_ms / 1000) : null;

    if (entryTsSec && candles.length > 0) {
        let matchedTime = entryTsSec;
        const exists = candles.some(c => c.time === entryTsSec);
        if (!exists) {
            let minDiff = Infinity;
            candles.forEach(c => {
                const diff = Math.abs(c.time - entryTsSec);
                if (diff < minDiff) {
                    minDiff = diff;
                    matchedTime = c.time;
                }
            });
        }

        markers.push({
            time: matchedTime,
            position: isLong ? 'belowBar' : 'aboveBar',
            color: isLong ? '#10b981' : '#f43f5e',
            shape: isLong ? 'arrowUp' : 'arrowDown',
            text: `${isLong ? 'LONG' : 'SHORT'} ENTRY`,
        });
    }

    if (exitTsSec && t.exit_price && candles.length > 0) {
        let matchedTime = exitTsSec;
        const exists = candles.some(c => c.time === exitTsSec);
        if (!exists) {
            let minDiff = Infinity;
            candles.forEach(c => {
                const diff = Math.abs(c.time - exitTsSec);
                if (diff < minDiff) {
                    minDiff = diff;
                    matchedTime = c.time;
                }
            });
        }

        const isWin = t.outcome === 'WIN';
        markers.push({
            time: matchedTime,
            position: isLong ? 'aboveBar' : 'belowBar',
            color: isWin ? '#34d399' : '#fb7185',
            shape: 'circle',
            text: `EXIT (${t.exit_reason || 'CLOSED'})`,
        });
    }

    inspectorCandleSeries.setMarkers(markers.sort((a, b) => a.time - b.time));

    inspectorPriceLines.forEach(pl => {
        try { inspectorCandleSeries.removePriceLine(pl); } catch (e) {}
    });
    inspectorPriceLines = [];

    // 1. Entry Price Line (Subtle dashed 1px so it never obscures candle bodies/wicks)
    if (t.entry_price) {
        const entryLine = inspectorCandleSeries.createPriceLine({
            price: Number(t.entry_price),
            color: '#3b82f6',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: `ENTRY $${formatPrice(t.entry_price)}`,
        });
        inspectorPriceLines.push(entryLine);
    }

    // 2. Current Mark Price Line (For open positions)
    if (t.is_open && t.mark_price) {
        const markLine = inspectorCandleSeries.createPriceLine({
            price: Number(t.mark_price),
            color: '#a855f7',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dotted,
            axisLabelVisible: true,
            title: `MARK $${formatPrice(t.mark_price)}`,
        });
        inspectorPriceLines.push(markLine);
    }

    // 3. Stop Loss Line
    const slVal = t.stop_loss || t.stop_loss_price;
    if (slVal) {
        const slPrice = Number(slVal);
        const slLine = inspectorCandleSeries.createPriceLine({
            price: slPrice,
            color: '#ef4444',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: `SL $${formatPrice(slPrice)}`,
        });
        inspectorPriceLines.push(slLine);
    }

    // 4. Take Profit 1 Line
    const tp1Val = t.take_profit || t.take_profit_price || t.take_profit_1;
    if (tp1Val) {
        const tpPrice = Number(tp1Val);
        const tpLine = inspectorCandleSeries.createPriceLine({
            price: tpPrice,
            color: '#10b981',
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: `TP1 $${formatPrice(tpPrice)}`,
        });
        inspectorPriceLines.push(tpLine);
    }

    // 5. Take Profit 2 Line
    if (t.take_profit_2) {
        const tp2Price = Number(t.take_profit_2);
        const tp2Line = inspectorCandleSeries.createPriceLine({
            price: tp2Price,
            color: '#14b8a6',
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dotted,
            axisLabelVisible: true,
            title: `TP2 $${formatPrice(tp2Price)}`,
        });
        inspectorPriceLines.push(tp2Line);
    }

    renderRiskRewardBox(t);
}

function renderRiskRewardBox(t) {
    const box = document.getElementById('inspectorRiskRewardStats');
    if (!box) return;

    const entry = Number(t.entry_price || 0);
    const sl = Number(t.stop_loss || t.stop_loss_price || 0);
    const tp = Number(t.take_profit || t.take_profit_price || t.take_profit_1 || 0);

    if (entry <= 0 || sl <= 0) {
        box.innerHTML = '<span class="text-slate-500 italic">R:R not computable (Missing SL/Entry)</span>';
        return;
    }

    const risk = Math.abs(entry - sl);
    const reward = tp > 0 ? Math.abs(tp - entry) : 0;
    const rrRatio = risk > 0 && reward > 0 ? (reward / risk).toFixed(2) : '--';

    const riskPct = ((risk / entry) * 100).toFixed(2);
    const rewardPct = tp > 0 ? ((reward / entry) * 100).toFixed(2) : '--';

    box.innerHTML = `
        <div class="flex items-center gap-2.5 text-xs font-mono">
            <span class="flex items-center gap-1">
                <span class="text-slate-400">R:R:</span>
                <strong class="text-indigo-400 text-sm">1:${rrRatio}</strong>
            </span>
            <span class="text-slate-700">|</span>
            <span class="text-rose-400">Risk: ${riskPct}%</span>
            <span class="text-slate-700">|</span>
            <span class="text-emerald-400">Reward: ${rewardPct}%</span>
        </div>
    `;
}

function updateContextButtonsUI() {
    [12, 24, 48, 72].forEach(h => {
        const btn = document.getElementById(`inspectorCtxBtn-${h}`);
        if (btn) {
            if (h === currentInspectorContextHours) {
                btn.className = 'px-2 py-0.5 text-xs font-bold rounded bg-blue-600 text-white shadow-sm transition';
            } else {
                btn.className = 'px-2 py-0.5 text-xs rounded bg-slate-800 text-slate-400 hover:text-white border border-slate-700 transition';
            }
        }
    });
}

function updateFitModeButtonsUI() {
    const btnPA = document.getElementById('inspectorFitBtn-PRICE_ACTION');
    const btnAll = document.getElementById('inspectorFitBtn-ALL');
    if (btnPA) {
        btnPA.className = currentInspectorFitMode === 'PRICE_ACTION'
            ? 'px-2.5 py-0.5 text-xs font-bold rounded bg-indigo-600 text-white border border-indigo-500 shadow-sm transition'
            : 'px-2.5 py-0.5 text-xs rounded bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition';
    }
    if (btnAll) {
        btnAll.className = currentInspectorFitMode === 'ALL'
            ? 'px-2.5 py-0.5 text-xs font-bold rounded bg-indigo-600 text-white border border-indigo-500 shadow-sm transition'
            : 'px-2.5 py-0.5 text-xs rounded bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition';
    }
}

function setInspectorContextHours(hours) {
    currentInspectorContextHours = Number(hours) || 24;
    updateContextButtonsUI();

    if (currentInspectedTrade) {
        loadInspectorChartData(currentInspectedTrade);
    }
}

function setInspectorFitMode(mode) {
    currentInspectorFitMode = (mode === 'ALL') ? 'ALL' : 'PRICE_ACTION';
    updateFitModeButtonsUI();

    if (currentInspectedTrade && currentInspectorCandles.length > 0) {
        // Trigger series data re-evaluation so autoscaleInfoProvider runs with the new mode
        inspectorCandleSeries.setData(currentInspectorCandles);
        applyInspectorViewportScaling();
    }
}

function resetInspectorZoom() {
    currentInspectorFitMode = 'PRICE_ACTION';
    updateFitModeButtonsUI();
    if (currentInspectedTrade && currentInspectorCandles.length > 0) {
        inspectorCandleSeries.setData(currentInspectorCandles);
        applyInspectorViewportScaling();
    }
}
