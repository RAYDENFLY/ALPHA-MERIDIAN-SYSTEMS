/**
 * Alpha Meridian Systems — Vela Chart Proof of Concept Controller
 */

(function () {
    'use strict';

    // State
    let chartInstance = null;
    let dataAdapter = null;
    let currentSymbol = 'BTCUSDT';
    let currentTimeframe = '1h';
    let autoRefresh = true;
    let pollInterval = null;
    let currentBars = [];

    // Indicator handles
    const indicatorHandles = {
        ema20: null,
        ema50: null,
        ema100: null,
        ema200: null,
        stoch: null
    };

    const indicatorStates = {
        ema20: true,
        ema50: true,
        ema100: true,
        ema200: true,
        stoch: true
    };

    // DOM Elements
    const elements = {
        symbolSelect: document.getElementById('symbol-select'),
        tfButtons: document.querySelectorAll('.btn-tf'),
        indicatorButtons: document.querySelectorAll('.btn-indicator'),
        statusPill: document.getElementById('status-pill'),
        statusText: document.getElementById('status-text'),
        loadingOverlay: document.getElementById('loading-overlay'),
        errorOverlay: document.getElementById('error-overlay'),
        errorMessage: document.getElementById('error-message'),
        emptyOverlay: document.getElementById('empty-overlay'),
        btnFullscreen: document.getElementById('btn-fullscreen'),
        btnResetZoom: document.getElementById('btn-reset-zoom'),
        btnAutoRefresh: document.getElementById('btn-auto-refresh'),
        btnRetry: document.getElementById('btn-retry'),
        
        // OHLC readouts
        ohlcTime: document.getElementById('ohlc-time'),
        ohlcOpen: document.getElementById('ohlc-open'),
        ohlcHigh: document.getElementById('ohlc-high'),
        ohlcLow: document.getElementById('ohlc-low'),
        ohlcClose: document.getElementById('ohlc-close'),
        ohlcChg: document.getElementById('ohlc-chg'),
        ohlcVol: document.getElementById('ohlc-vol'),

        // Order Book
        obAsks: document.getElementById('ob-asks'),
        obBids: document.getElementById('ob-bids'),
        obMid: document.getElementById('ob-mid'),
        obSpread: document.getElementById('ob-spread')
    };

    async function init() {
        console.log('[AMS] Initializing Vela Chart PoC...');
        dataAdapter = new AmsDataAdapter('');

        setupEventListeners();
        await loadSymbols();
        await initChart();
        startPolling();
    }

    async function loadSymbols() {
        try {
            const result = await dataAdapter.fetchSymbols();
            elements.symbolSelect.innerHTML = '';
            
            result.symbols.forEach(sym => {
                const opt = document.createElement('option');
                opt.value = sym;
                opt.textContent = sym;
                if (sym === result.defaultSymbol) opt.selected = true;
                elements.symbolSelect.appendChild(opt);
            });

            currentSymbol = result.defaultSymbol || 'BTCUSDT';
            currentTimeframe = result.defaultTimeframe || '1h';

            // Sync timeframe buttons
            elements.tfButtons.forEach(btn => {
                if (btn.dataset.tf === currentTimeframe) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
        } catch (err) {
            console.warn('[AMS] Failed to load symbols dynamically, using defaults:', err);
            currentSymbol = 'BTCUSDT';
            currentTimeframe = '1h';
        }
    }

    async function initChart() {
        showOverlay('loading');

        try {
            // Fetch initial historical candles
            currentBars = await dataAdapter.fetchKlines(currentSymbol, currentTimeframe, 150);

            if (!currentBars || currentBars.length === 0) {
                showOverlay('empty');
                return;
            }

            // Create Vela chart instance
            const VelaClass = (window.Vela && window.Vela.Vela) || window.Vela;
            if (!VelaClass) {
                throw new Error('Vela library not loaded. Check vela.global.js.');
            }

            chartInstance = new VelaClass('#chart', {
                data: currentBars,
                timeframe: currentTimeframe,
                theme: {
                    background: '#050505',
                    textColor: '#A0A5B5',
                    gridColor: '#16181F',
                    borderColor: '#262933',
                    upColor: '#00E676',
                    downColor: '#FF3D00',
                    fontFamily: "Consolas, 'Roboto Mono', monospace"
                },
                currentPriceLine: true,
                nativeBackend: 'auto', // WebGL2 if available, else canvas2d
                animations: { pan: true, zoom: 70, autoscale: true }
            });

            await chartInstance.ready();
            console.log('[AMS] Vela chart mounted successfully.');

            // Add Indicators
            setupIndicators();

            // Setup crosshair listener
            setupCrosshair();

            // Update OHLC readout with latest bar
            updateOHLCReadout(currentBars[currentBars.length - 1]);

            // Load initial order book
            await updateOrderBook();

            hideOverlays();
            setStatus(true, 'LIVE /api/chart/klines');

        } catch (err) {
            console.error('[AMS] Chart initialization failed:', err);
            showError('BACKEND CONNECTION ERROR: ' + err.message);
            setStatus(false, 'DISCONNECTED');
        }
    }

    function setupIndicators() {
        if (!chartInstance) return;

        try {
            // EMA 20 (Gold)
            if (indicatorStates.ema20) {
                indicatorHandles.ema20 = chartInstance.addNativeIndicator('ema', {
                    inputs: { length: 20, color: '#FFD700' }
                });
            }

            // EMA 50 (Amber)
            if (indicatorStates.ema50) {
                indicatorHandles.ema50 = chartInstance.addNativeIndicator('ema', {
                    inputs: { length: 50, color: '#FF9D00' }
                });
            }

            // EMA 100 (Cyan)
            if (indicatorStates.ema100) {
                indicatorHandles.ema100 = chartInstance.addNativeIndicator('ema', {
                    inputs: { length: 100, color: '#00E5FF' }
                });
            }

            // EMA 200 (Magenta)
            if (indicatorStates.ema200) {
                indicatorHandles.ema200 = chartInstance.addNativeIndicator('ema', {
                    inputs: { length: 200, color: '#E040FB' }
                });
            }

            // Stochastic 5, 3, 3
            if (indicatorStates.stoch) {
                indicatorHandles.stoch = chartInstance.addNativeIndicator('stochastic', {
                    inputs: {
                        kLength: 5,
                        kSmoothing: 3,
                        dLength: 3,
                        kColor: '#00E5FF',
                        dColor: '#FF9D00',
                        upper: 80,
                        lower: 20
                    }
                });
            }
        } catch (indErr) {
            console.warn('[AMS] Indicator setup notice:', indErr);
        }
    }

    function toggleIndicator(name) {
        indicatorStates[name] = !indicatorStates[name];
        
        // Update button UI
        const btn = document.querySelector(`.btn-indicator[data-ind="${name}"]`);
        if (btn) {
            btn.classList.toggle('active', indicatorStates[name]);
        }

        const handle = indicatorHandles[name];
        if (handle) {
            if (typeof handle.setVisible === 'function') {
                handle.setVisible(indicatorStates[name]);
            } else if (typeof handle.remove === 'function' && !indicatorStates[name]) {
                handle.remove();
                indicatorHandles[name] = null;
            }
        } else if (indicatorStates[name] && chartInstance) {
            // Re-add if was removed
            if (name === 'ema20') indicatorHandles.ema20 = chartInstance.addNativeIndicator('ema', { inputs: { length: 20, color: '#FFD700' } });
            if (name === 'ema50') indicatorHandles.ema50 = chartInstance.addNativeIndicator('ema', { inputs: { length: 50, color: '#FF9D00' } });
            if (name === 'ema100') indicatorHandles.ema100 = chartInstance.addNativeIndicator('ema', { inputs: { length: 100, color: '#00E5FF' } });
            if (name === 'ema200') indicatorHandles.ema200 = chartInstance.addNativeIndicator('ema', { inputs: { length: 200, color: '#E040FB' } });
            if (name === 'stoch') indicatorHandles.stoch = chartInstance.addNativeIndicator('stochastic', { inputs: { kLength: 5, kSmoothing: 3, dLength: 3, kColor: '#00E5FF', dColor: '#FF9D00' } });
        }
    }

    function setupCrosshair() {
        if (!chartInstance || !chartInstance.renderer) return;

        if (typeof chartInstance.renderer.onCrosshairMove === 'function') {
            chartInstance.renderer.onCrosshairMove(e => {
                if (e && e.time && currentBars.length > 0) {
                    // Find bar matching or closest to e.time
                    const bar = findBarByTime(e.time);
                    if (bar) {
                        updateOHLCReadout(bar);
                    }
                }
            });
        }
    }

    function findBarByTime(targetTime) {
        if (!currentBars || currentBars.length === 0) return null;
        let low = 0;
        let high = currentBars.length - 1;

        while (low <= high) {
            const mid = Math.floor((low + high) / 2);
            if (currentBars[mid].time === targetTime) return currentBars[mid];
            if (currentBars[mid].time < targetTime) low = mid + 1;
            else high = mid - 1;
        }

        const idx = Math.min(Math.max(low, 0), currentBars.length - 1);
        return currentBars[idx];
    }

    function updateOHLCReadout(bar) {
        if (!bar) return;
        const d = new Date(bar.time);
        const pad = n => String(n).padStart(2, '0');
        const timeStr = `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;

        const chg = bar.open > 0 ? ((bar.close - bar.open) / bar.open) * 100 : 0;
        const isUp = bar.close >= bar.open;

        elements.ohlcTime.textContent = timeStr;
        elements.ohlcOpen.textContent = formatPrice(bar.open);
        elements.ohlcHigh.textContent = formatPrice(bar.high);
        elements.ohlcLow.textContent = formatPrice(bar.low);
        elements.ohlcClose.textContent = formatPrice(bar.close);
        
        elements.ohlcChg.textContent = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
        elements.ohlcChg.className = `val ${isUp ? 'up' : 'down'}`;

        elements.ohlcVol.textContent = bar.volume ? bar.volume.toFixed(2) : '0.00';
    }

    function formatPrice(p) {
        if (typeof p !== 'number' || !Number.isFinite(p)) return '0.00';
        return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    async function changeMarket(newSymbol, newTf) {
        currentSymbol = newSymbol;
        currentTimeframe = newTf;
        showOverlay('loading');

        try {
            currentBars = await dataAdapter.fetchKlines(currentSymbol, currentTimeframe, 150);

            if (!currentBars || currentBars.length === 0) {
                showOverlay('empty');
                return;
            }

            if (chartInstance) {
                chartInstance.setMarket({
                    data: currentBars,
                    timeframe: currentTimeframe
                });

                if (typeof chartInstance.setVisibleRangePreset === 'function') {
                    chartInstance.setVisibleRangePreset('ALL');
                }
            }

            updateOHLCReadout(currentBars[currentBars.length - 1]);
            await updateOrderBook();

            hideOverlays();
            setStatus(true, 'LIVE /api/chart/klines');
        } catch (err) {
            console.error('[AMS] Failed to switch market:', err);
            showError('FAILED TO SWITCH MARKET: ' + err.message);
            setStatus(false, 'DISCONNECTED');
        }
    }

    async function updateOrderBook() {
        try {
            const ob = await dataAdapter.fetchOrderBook(currentSymbol);
            renderOrderBook(ob);
        } catch (err) {
            console.warn('[AMS] Order book fetch warning:', err);
        }
    }

    function renderOrderBook(ob) {
        if (!ob) return;

        // Render Mid & Spread
        elements.obMid.textContent = formatPrice(ob.midPrice);
        elements.obSpread.textContent = `SPREAD: ${ob.spreadBps.toFixed(2)} bps`;

        // Calculate max notional for depth bars
        let maxNotional = 1;
        ob.asks.forEach(a => { if (a.notional > maxNotional) maxNotional = a.notional; });
        ob.bids.forEach(b => { if (b.notional > maxNotional) maxNotional = b.notional; });

        // Render Asks (descending so best ask is at bottom)
        const sortedAsks = [...ob.asks].reverse();
        elements.obAsks.innerHTML = sortedAsks.map(a => {
            const widthPct = Math.min(100, Math.round((a.notional / maxNotional) * 100));
            return `
                <div class="ob-row ask">
                    <div class="ob-depth-bar" style="width: ${widthPct}%;"></div>
                    <div class="price">${formatPrice(a.price)}</div>
                    <div>${a.size.toFixed(3)}</div>
                    <div>$${Math.round(a.notional).toLocaleString('en-US')}</div>
                </div>
            `;
        }).join('');

        // Render Bids (descending so best bid is at top)
        elements.obBids.innerHTML = ob.bids.map(b => {
            const widthPct = Math.min(100, Math.round((b.notional / maxNotional) * 100));
            return `
                <div class="ob-row bid">
                    <div class="ob-depth-bar" style="width: ${widthPct}%;"></div>
                    <div class="price">${formatPrice(b.price)}</div>
                    <div>${b.size.toFixed(3)}</div>
                    <div>$${Math.round(b.notional).toLocaleString('en-US')}</div>
                </div>
            `;
        }).join('');
    }

    function startPolling() {
        if (pollInterval) clearInterval(pollInterval);

        pollInterval = setInterval(async () => {
            if (!autoRefresh) return;

            try {
                // Poll order book (fast 2.5s)
                await updateOrderBook();

                // Poll forming candle
                const latest = await dataAdapter.fetchKlines(currentSymbol, currentTimeframe, 15);
                if (latest && latest.length > 0 && chartInstance) {
                    const newest = latest[latest.length - 1];
                    const existingNewest = currentBars[currentBars.length - 1];

                    if (existingNewest && newest.time === existingNewest.time) {
                        // Update forming bar
                        currentBars[currentBars.length - 1] = newest;
                    } else if (existingNewest && newest.time > existingNewest.time) {
                        // New bar closed
                        currentBars.push(newest);
                    }
                    updateOHLCReadout(newest);
                }
                setStatus(true, 'LIVE /api/chart/klines');
            } catch (pollErr) {
                console.warn('[AMS] Polling error:', pollErr);
                setStatus(false, 'RETRYING...');
            }
        }, 2500);
    }

    function setupEventListeners() {
        // Symbol switch
        elements.symbolSelect.addEventListener('change', e => {
            changeMarket(e.target.value, currentTimeframe);
        });

        // Timeframe buttons
        elements.tfButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                elements.tfButtons.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                changeMarket(currentSymbol, btn.dataset.tf);
            });
        });

        // Indicator toggle buttons
        elements.indicatorButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                toggleIndicator(btn.dataset.ind);
            });
        });

        // Reset Zoom
        elements.btnResetZoom.addEventListener('click', () => {
            if (chartInstance && typeof chartInstance.setVisibleRangePreset === 'function') {
                chartInstance.setVisibleRangePreset('ALL');
            }
        });

        // Fullscreen Toggle
        elements.btnFullscreen.addEventListener('click', () => {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch(err => {
                    console.warn('[AMS] Fullscreen error:', err);
                });
                elements.btnFullscreen.textContent = '[ ⛶ EXIT ]';
            } else {
                document.exitFullscreen().catch(err => {
                    console.warn('[AMS] Exit fullscreen error:', err);
                });
                elements.btnFullscreen.textContent = '[ ⛶ FULLSCREEN ]';
            }
        });

        // Auto-refresh toggle
        elements.btnAutoRefresh.addEventListener('click', () => {
            autoRefresh = !autoRefresh;
            elements.btnAutoRefresh.textContent = autoRefresh ? 'PAUSE' : 'RESUME';
            elements.btnAutoRefresh.style.color = autoRefresh ? 'var(--text-secondary)' : 'var(--ams-amber)';
        });

        // Retry button
        elements.btnRetry.addEventListener('click', () => {
            init();
        });

        // Window resize
        window.addEventListener('resize', () => {
            if (chartInstance && typeof chartInstance.resize === 'function') {
                chartInstance.resize();
            }
        });

        // Keyboard shortcuts (<F11> for fullscreen, <R> for reset)
        window.addEventListener('keydown', e => {
            if (e.key === 'r' || e.key === 'R') {
                if (chartInstance && typeof chartInstance.setVisibleRangePreset === 'function') {
                    chartInstance.setVisibleRangePreset('ALL');
                }
            }
        });
    }

    function setStatus(isLive, text) {
        elements.statusPill.className = `status-pill ${isLive ? '' : 'error'}`;
        elements.statusText.textContent = `● ${text}`;
    }

    function showOverlay(type) {
        elements.loadingOverlay.classList.add('hidden');
        elements.errorOverlay.classList.add('hidden');
        elements.emptyOverlay.classList.add('hidden');

        if (type === 'loading') elements.loadingOverlay.classList.remove('hidden');
        if (type === 'error') elements.errorOverlay.classList.remove('hidden');
        if (type === 'empty') elements.emptyOverlay.classList.remove('hidden');
    }

    function hideOverlays() {
        elements.loadingOverlay.classList.add('hidden');
        elements.errorOverlay.classList.add('hidden');
        elements.emptyOverlay.classList.add('hidden');
    }

    function showError(msg) {
        elements.errorMessage.textContent = msg;
        showOverlay('error');
    }

    // Boot
    window.addEventListener('DOMContentLoaded', init);
})();
