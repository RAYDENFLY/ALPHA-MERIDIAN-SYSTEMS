// =========================================================================
// FEATURE E: INTERACTIVE LIQUIDITY SWEEP & RECLAIM CHART ENGINE
// =========================================================================
        function initOrRefreshLiquidityChart() {
            loadLiquidityChartSymbols();
            loadLiquidityChartData();
        }

        async function loadLiquidityChartSymbols() {
            try {
                const res = await fetch('/api/chart/symbols');
                if (!res.ok) return;
                const data = await res.json();
                const symbols = (data && data.symbols && data.symbols.length > 0) ? data.symbols : [];
                if (symbols.length === 0) return;

                // 1. Populate Tab 8 Chart Dropdown & Datalist
                const chartSel = document.getElementById('chartSymbolSelect');
                const chartDatalist = document.getElementById('chartSymbolDatalist');
                if (chartSel) {
                    const existingVals = new Set(Array.from(chartSel.options).map(o => o.value));
                    symbols.forEach(s => {
                        if (!existingVals.has(s)) {
                            const opt = document.createElement('option');
                            opt.value = s;
                            opt.textContent = s;
                            opt.className = 'bg-slate-900';
                            chartSel.appendChild(opt);
                        }
                    });
                }
                if (chartDatalist) {
                    chartDatalist.innerHTML = '';
                    symbols.forEach(s => {
                        const opt = document.createElement('option');
                        opt.value = s;
                        chartDatalist.appendChild(opt);
                    });
                }

                // 2. Populate Tab 7 Intel Dropdown & Datalist
                const intelSel = document.getElementById('intelSymbolSelect');
                const intelDatalist = document.getElementById('intelSymbolDatalist');
                if (intelSel) {
                    const existingVals = new Set(Array.from(intelSel.options).map(o => o.value));
                    symbols.forEach(s => {
                        if (!existingVals.has(s)) {
                            const opt = document.createElement('option');
                            opt.value = s;
                            opt.textContent = s;
                            opt.className = 'bg-slate-900';
                            intelSel.appendChild(opt);
                        }
                    });
                }
                if (intelDatalist) {
                    intelDatalist.innerHTML = '';
                    symbols.forEach(s => {
                        const opt = document.createElement('option');
                        opt.value = s;
                        intelDatalist.appendChild(opt);
                    });
                }
            } catch (e) {
                console.warn('Could not load chart symbols:', e);
            }
        }

        function handleChartSymbolKeydown(event) {
            if (event && event.key === 'Enter') {
                handleChartSymbolInputChange(event);
            }
        }

        function handleChartSymbolInputChange(event) {
            const sym = (event.target.value || 'BTCUSDT').trim().toUpperCase();
            if (!sym) return;
            currentChartSymbol = sym;
            const sel = document.getElementById('chartSymbolSelect');
            if (sel) {
                const hasOption = Array.from(sel.options).some(o => o.value === sym);
                if (hasOption) sel.value = sym;
            }
            if (isReplayMode) toggleReplayMode();
            loadLiquidityChartData();
        }

        function handleChartSymbolChange(event) {
            currentChartSymbol = (event.target.value || 'BTCUSDT').trim().toUpperCase();
            const inp = document.getElementById('chartSymbolInput');
            if (inp) inp.value = currentChartSymbol;
            if (isReplayMode) toggleReplayMode();
            loadLiquidityChartData();
        }

        function togglePriceScaleAuto() {
            chartPriceScaleAuto = !chartPriceScaleAuto;
            const btn = document.getElementById('scaleAutoBtn');
            if (btn) {
                if (chartPriceScaleAuto) {
                    btn.className = 'px-2 py-1 rounded bg-indigo-600 text-white font-bold transition text-[11px]';
                } else {
                    btn.className = 'px-2 py-1 rounded text-slate-400 hover:text-white transition text-[11px]';
                }
            }
            if (tvChart) {
                try {
                    tvChart.priceScale('right').applyOptions({ autoScale: chartPriceScaleAuto });
                } catch (e) {
                    console.warn('Could not apply price scale autoscale:', e);
                }
            }
        }

        function togglePriceScaleLog() {
            chartPriceScaleLog = !chartPriceScaleLog;
            const btn = document.getElementById('scaleLogBtn');
            if (btn) {
                if (chartPriceScaleLog) {
                    btn.className = 'px-2 py-1 rounded bg-indigo-600 text-white font-bold transition text-[11px]';
                    btn.textContent = 'Log [ON]';
                } else {
                    btn.className = 'px-2 py-1 rounded text-slate-400 hover:text-white transition text-[11px]';
                    btn.textContent = 'Log';
                }
            }
            if (tvChart && typeof LightweightCharts !== 'undefined') {
                try {
                    const logMode = chartPriceScaleLog
                        ? (LightweightCharts.PriceScaleMode ? LightweightCharts.PriceScaleMode.Logarithmic : 1)
                        : (LightweightCharts.PriceScaleMode ? LightweightCharts.PriceScaleMode.Normal : 0);
                    tvChart.priceScale('right').applyOptions({ mode: logMode });
                } catch (e) {
                    console.warn('Could not apply price scale mode:', e);
                }
            }
        }

        async function toggleChartFullscreen() {
            const card = document.getElementById('chartCardContainer');
            if (!card) return;

            if (!isChartFullscreen) {
                if (card.requestFullscreen) {
                    try {
                        await card.requestFullscreen();
                        return;
                    } catch (err) {
                        console.warn('Native fullscreen request rejected/unsupported, fallback to CSS:', err);
                    }
                } else if (card.webkitRequestFullscreen) {
                    try {
                        await card.webkitRequestFullscreen();
                        return;
                    } catch (err) {}
                }
                applyCssFullscreen(true);
            } else {
                if (document.fullscreenElement || document.webkitFullscreenElement) {
                    try {
                        if (document.exitFullscreen) await document.exitFullscreen();
                        else if (document.webkitExitFullscreen) await document.webkitExitFullscreen();
                        return;
                    } catch (err) {
                        console.warn('Native exit fullscreen error:', err);
                    }
                }
                applyCssFullscreen(false);
            }
        }

        function applyCssFullscreen(enable) {
            isChartFullscreen = Boolean(enable);
            const card = document.getElementById('chartCardContainer');
            const chartDiv = document.getElementById('tvLightweightChartContainer');
            const floatingBtn = document.getElementById('chartExitFullscreenFloatingBtn');
            const expandIcon = document.getElementById('fullscreenExpandIcon');
            const collapseIcon = document.getElementById('fullscreenCollapseIcon');
            const btnText = document.getElementById('fullscreenBtnText');

            if (!card || !chartDiv) return;

            if (isChartFullscreen) {
                card.classList.add('fixed', 'inset-0', 'z-50', 'w-screen', 'h-screen', 'rounded-none', 'bg-slate-950', 'p-4');
                card.style.zIndex = '9999';
                chartDiv.style.height = 'calc(100vh - 32px)';
                if (floatingBtn) floatingBtn.classList.remove('hidden');
                if (expandIcon) expandIcon.classList.add('hidden');
                if (collapseIcon) collapseIcon.classList.remove('hidden');
                if (btnText) btnText.textContent = 'Exit Fullscreen';
                document.body.style.overflow = 'hidden';
            } else {
                card.classList.remove('fixed', 'inset-0', 'z-50', 'w-screen', 'h-screen', 'rounded-none', 'bg-slate-950', 'p-4');
                card.style.zIndex = '';
                chartDiv.style.height = '520px';
                if (floatingBtn) floatingBtn.classList.add('hidden');
                if (expandIcon) expandIcon.classList.remove('hidden');
                if (collapseIcon) collapseIcon.classList.add('hidden');
                if (btnText) btnText.textContent = 'Fullscreen';
                document.body.style.overflow = '';
            }

            setTimeout(() => {
                if (tvChart && chartDiv) {
                    const rect = chartDiv.getBoundingClientRect();
                    tvChart.applyOptions({
                        width: rect.width || window.innerWidth,
                        height: isChartFullscreen ? (window.innerHeight - 32) : 520
                    });
                    tvChart.timeScale().fitContent();
                }
            }, 100);
        }

        function handleNativeFullscreenChange() {
            const card = document.getElementById('chartCardContainer');
            const chartDiv = document.getElementById('tvLightweightChartContainer');
            const floatingBtn = document.getElementById('chartExitFullscreenFloatingBtn');
            const expandIcon = document.getElementById('fullscreenExpandIcon');
            const collapseIcon = document.getElementById('fullscreenCollapseIcon');
            const btnText = document.getElementById('fullscreenBtnText');
            const isNativeFs = (document.fullscreenElement === card || document.webkitFullscreenElement === card);

            isChartFullscreen = isNativeFs;

            if (isNativeFs) {
                if (chartDiv) chartDiv.style.height = 'calc(100vh - 32px)';
                if (floatingBtn) floatingBtn.classList.remove('hidden');
                if (expandIcon) expandIcon.classList.add('hidden');
                if (collapseIcon) collapseIcon.classList.remove('hidden');
                if (btnText) btnText.textContent = 'Exit Fullscreen';
            } else {
                if (chartDiv) chartDiv.style.height = '520px';
                if (floatingBtn) floatingBtn.classList.add('hidden');
                if (expandIcon) expandIcon.classList.remove('hidden');
                if (collapseIcon) collapseIcon.classList.add('hidden');
                if (btnText) btnText.textContent = 'Fullscreen';
            }

            setTimeout(() => {
                if (tvChart && chartDiv) {
                    const rect = chartDiv.getBoundingClientRect();
                    tvChart.applyOptions({
                        width: rect.width || window.innerWidth,
                        height: isNativeFs ? (window.innerHeight - 32) : 520
                    });
                    tvChart.timeScale().fitContent();
                }
            }, 100);
        }

        document.addEventListener('fullscreenchange', handleNativeFullscreenChange);
        document.addEventListener('webkitfullscreenchange', handleNativeFullscreenChange);
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && isChartFullscreen) {
                toggleChartFullscreen();
            }
        });

        function setChartTimeframe(tf) {
            currentChartTf = tf;
            ['15m', '1h', '4h'].forEach(t => {
                const btn = document.getElementById(`chartTf-${t}`);
                if (btn) {
                    if (t === tf) {
                        btn.className = 'px-2.5 py-1 rounded bg-indigo-600 text-white font-bold transition';
                    } else {
                        btn.className = 'px-2.5 py-1 rounded text-slate-400 hover:text-white transition';
                    }
                }
            });
            if (isReplayMode) toggleReplayMode();
            loadLiquidityChartData();
        }

        async function loadLiquidityChartData(asOfIndex = null) {
            const chartInp = document.getElementById('chartSymbolInput');
            if (chartInp && chartInp.value !== currentChartSymbol) chartInp.value = currentChartSymbol;
            const chartSel = document.getElementById('chartSymbolSelect');
            if (chartSel && Array.from(chartSel.options).some(o => o.value === currentChartSymbol)) {
                chartSel.value = currentChartSymbol;
            }

            const spin = document.getElementById('chartSpinIcon');
            const overlay = document.getElementById('chartLoadingOverlay');
            if (spin) spin.classList.add('animate-spin');
            if (overlay && !tvChart) overlay.classList.remove('hidden');

            try {
                let url = `/api/chart/liquidity_layers?symbol=${encodeURIComponent(currentChartSymbol)}&timeframe=${encodeURIComponent(currentChartTf)}&limit=150`;
                if (asOfIndex !== null && asOfIndex !== undefined) {
                    url += `&as_of_index=${encodeURIComponent(asOfIndex)}`;
                }

                const res = await fetch(url);
                if (!res.ok) {
                    throw new Error(`Failed to load liquidity chart layers: ${res.statusText}`);
                }
                const data = await res.json();
                chartLayersData = data;
                chartEventsData = data.sweep_events || [];

                const feedBadge = document.getElementById('chartFeedBadge');
                if (feedBadge && data.feed_status) {
                    const st = data.feed_status;
                    if (st.status === 'REPLAY') {
                        feedBadge.textContent = `REPLAY: BAR ${data.replay_metadata?.as_of_index ?? '--'}/${data.replay_metadata?.total_bars ?? '--'}`;
                        feedBadge.className = 'px-2 py-0.5 text-[10px] font-mono rounded bg-amber-950 text-amber-300 border border-amber-800';
                    } else if (st.is_stale) {
                        feedBadge.textContent = `FEED: STALE (${st.candle_age_seconds}s ago)`;
                        feedBadge.className = 'px-2 py-0.5 text-[10px] font-mono rounded bg-rose-950 text-rose-300 border border-rose-800';
                    } else {
                        feedBadge.textContent = `FEED: LIVE (${st.source})`;
                        feedBadge.className = 'px-2 py-0.5 text-[10px] font-mono rounded bg-emerald-950 text-emerald-300 border border-emerald-800';
                    }
                }

                renderLightweightChart(data);
                renderEventsList(chartEventsData);

                if (data.replay_metadata) {
                    updateReplaySliderBounds(data.replay_metadata);
                }

            } catch (err) {
                console.error('Error loading liquidity chart:', err);
                const eventsList = document.getElementById('chartEventsList');
                if (eventsList) {
                    eventsList.innerHTML = `<div class="p-4 text-center text-xs text-rose-400 font-mono">Error: ${escapeHtml(err.message)}</div>`;
                }
            } finally {
                if (spin) spin.classList.remove('animate-spin');
                if (overlay) overlay.classList.add('hidden');
            }
        }

        function renderLightweightChart(data) {
            const container = document.getElementById('tvLightweightChartContainer');
            if (!container) return;

            if (typeof LightweightCharts === 'undefined') {
                container.innerHTML = `
                    <div class="p-8 text-center text-slate-400 font-mono">
                        <p class="text-amber-400 font-bold mb-2">TradingView Lightweight Charts Library Loading...</p>
                        <p class="text-xs">If running in an offline environment without CDN access, please check your network connection.</p>
                    </div>`;
                return;
            }

            const chartData = (data.candles || []).map(c => ({
                time: c.time,
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close
            }));

            const volumeData = (data.candles || []).map(c => ({
                time: c.time,
                value: c.volume,
                color: c.close >= c.open ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)'
            }));

            const targetHeight = isChartFullscreen ? (window.innerHeight - 32) : 520;
            const logMode = chartPriceScaleLog
                ? (LightweightCharts.PriceScaleMode ? LightweightCharts.PriceScaleMode.Logarithmic : 1)
                : (LightweightCharts.PriceScaleMode ? LightweightCharts.PriceScaleMode.Normal : 0);

            if (!tvChart) {
                container.innerHTML = '';
                const width = container.clientWidth || 800;

                tvChart = LightweightCharts.createChart(container, {
                    width: width,
                    height: targetHeight,
                    layout: {
                        background: { color: '#0b0f17' },
                        textColor: '#94a3b8',
                        fontFamily: 'ui-sans-serif, system-ui, sans-serif'
                    },
                    grid: {
                        vertLines: { color: 'rgba(30, 41, 59, 0.5)' },
                        horzLines: { color: 'rgba(30, 41, 59, 0.5)' }
                    },
                    crosshair: {
                        mode: LightweightCharts.CrosshairMode.Normal,
                    },
                    rightPriceScale: {
                        borderColor: '#1e293b',
                        autoScale: chartPriceScaleAuto,
                        mode: logMode,
                    },
                    timeScale: {
                        borderColor: '#1e293b',
                        timeVisible: true,
                        secondsVisible: false
                    }
                });

                volumeSeries = tvChart.addHistogramSeries({
                    color: '#26a69a',
                    priceFormat: {
                        type: 'volume',
                    },
                    priceScaleId: 'volume',
                });
                tvChart.priceScale('volume').applyOptions({
                    scaleMargins: {
                        top: 0.8,
                        bottom: 0,
                    },
                });

                candleSeries = tvChart.addCandlestickSeries({
                    upColor: '#10b981',
                    downColor: '#ef4444',
                    borderVisible: false,
                    wickUpColor: '#10b981',
                    wickDownColor: '#ef4444'
                });

                tvChart.subscribeCrosshairMove(param => {
                    if (!param || !param.time || !param.seriesData) return;
                    const c = param.seriesData.get(candleSeries);
                    const v = param.seriesData.get(volumeSeries);
                    if (c) {
                        const d = new Date(param.time * 1000);
                        const timeStr = d.toISOString().replace('T', ' ').slice(0, 16);
                        const hudT = document.getElementById('hudTime');
                        const hudO = document.getElementById('hudO');
                        const hudH = document.getElementById('hudH');
                        const hudL = document.getElementById('hudL');
                        const hudC = document.getElementById('hudC');
                        const hudV = document.getElementById('hudV');
                        if (hudT) hudT.textContent = `Time: ${timeStr}`;
                        if (hudO) hudO.textContent = `O: ${Number(c.open).toFixed(2)}`;
                        if (hudH) hudH.textContent = `H: ${Number(c.high).toFixed(2)}`;
                        if (hudL) hudL.textContent = `L: ${Number(c.low).toFixed(2)}`;
                        if (hudC) hudC.textContent = `C: ${Number(c.close).toFixed(2)}`;
                        if (hudV && v) hudV.textContent = `V: ${Number(v.value).toFixed(1)}`;
                    }
                });

                if (window.ResizeObserver) {
                    const ro = new ResizeObserver(entries => {
                        if (entries && entries[0] && tvChart) {
                            const rect = entries[0].contentRect;
                            tvChart.applyOptions({
                                width: rect.width,
                                height: isChartFullscreen ? (window.innerHeight - 32) : 520
                            });
                        }
                    });
                    ro.observe(container);
                }
            } else {
                try {
                    tvChart.priceScale('right').applyOptions({
                        autoScale: chartPriceScaleAuto,
                        mode: logMode
                    });
                } catch (e) {}
            }

            candleSeries.setData(chartData);
            volumeSeries.setData(volumeData);

            const markers = data.chart_markers || [];
            candleSeries.setMarkers(markers);

            renderChartOverlays(data);

            if (!isReplayMode) {
                tvChart.timeScale().fitContent();
            }
        }

        function renderChartOverlays(data) {
            if (!candleSeries) return;

            activePriceLines.forEach(l => {
                try { candleSeries.removePriceLine(l); } catch (e) {}
            });
            activePriceLines = [];

            if (activeOverlays.structural && data.structural_levels) {
                data.structural_levels.forEach(lvl => {
                    let color = '#3b82f6';
                    let lineStyle = LightweightCharts.LineStyle.Dashed;
                    let title = lvl.source_label || 'BSL';

                    if (lvl.level_type === 'SWING_LOW_SSL') {
                        color = '#10b981';
                        title = lvl.source_label || 'SSL';
                    } else if (lvl.level_type === 'EQUAL_HIGHS') {
                        color = '#6366f1';
                        lineStyle = LightweightCharts.LineStyle.Solid;
                        title = lvl.source_label || 'EQH';
                    } else if (lvl.level_type === 'EQUAL_LOWS') {
                        color = '#14b8a6';
                        lineStyle = LightweightCharts.LineStyle.Solid;
                        title = lvl.source_label || 'EQL';
                    }

                    if (lvl.status === 'SWEPT_PENDING' || lvl.status === 'RECLAIMED') {
                        color = '#f59e0b';
                        title += ` [${lvl.status}]`;
                    } else if (lvl.status === 'INVALIDATED_BROKEN') {
                        color = '#64748b';
                        title += ' [BROKEN]';
                    }

                    try {
                        const pl = candleSeries.createPriceLine({
                            price: lvl.price,
                            color: color,
                            lineWidth: 1,
                            lineStyle: lineStyle,
                            axisLabelVisible: true,
                            title: title,
                        });
                        activePriceLines.push(pl);
                    } catch (e) {}
                });
            }

            if (activeOverlays.orderbook && data.order_book_overlay) {
                const ob = data.order_book_overlay;
                if (ob.walls && ob.walls.length > 0) {
                    ob.walls.forEach(w => {
                        const isBid = w.side === 'BID';
                        const color = isBid ? 'rgba(5, 150, 105, 0.85)' : 'rgba(220, 38, 38, 0.85)';
                        try {
                            const pl = candleSeries.createPriceLine({
                                price: w.price,
                                color: color,
                                lineWidth: 2,
                                lineStyle: LightweightCharts.LineStyle.Solid,
                                axisLabelVisible: true,
                                title: `${w.side} WALL ${w.multiple}x ($${Number(w.depth_usdt).toLocaleString()})`,
                            });
                            activePriceLines.push(pl);
                        } catch (e) {}
                    });
                }
            }

            if (activeOverlays.liquidation && data.liquidation_overlay) {
                const liq = data.liquidation_overlay;
                if (liq.clusters && liq.clusters.length > 0) {
                    liq.clusters.slice(0, 6).forEach(c => {
                        const isLongLiq = c.side === 'LONG_LIQUIDATION';
                        const color = isLongLiq ? 'rgba(249, 115, 22, 0.85)' : 'rgba(168, 85, 247, 0.85)';
                        try {
                            const pl = candleSeries.createPriceLine({
                                price: c.price_level,
                                color: color,
                                lineWidth: 1,
                                lineStyle: LightweightCharts.LineStyle.Dotted,
                                axisLabelVisible: true,
                                title: `[EST LIQ] ${c.side.replace('_LIQUIDATION', '')} ${c.leverage_tier}`,
                            });
                            activePriceLines.push(pl);
                        } catch (e) {}
                    });
                }
            }
        }

        function toggleOverlay(overlayType) {
            if (overlayType === 'structural') {
                activeOverlays.structural = document.getElementById('overlayStructuralCheck')?.checked ?? true;
            } else if (overlayType === 'orderbook') {
                activeOverlays.orderbook = document.getElementById('overlayOrderBookCheck')?.checked ?? true;
            } else if (overlayType === 'liquidation') {
                activeOverlays.liquidation = document.getElementById('overlayLiquidationCheck')?.checked ?? true;
            }
            if (chartLayersData) {
                renderChartOverlays(chartLayersData);
            }
        }

        function renderEventsList(events) {
            const container = document.getElementById('chartEventsList');
            const countBadge = document.getElementById('chartEventsCountBadge');
            if (!container) return;

            let filtered = events || [];
            if (currentChartEventFilter === 'RECLAIMED') {
                filtered = filtered.filter(e => e.event_type === 'SWEEP_RECLAIMED' || e.event_type === 'SWEEP_CONFIRMED');
            } else if (currentChartEventFilter === 'CONFIRMED') {
                filtered = filtered.filter(e => e.event_type === 'SWEEP_CONFIRMED');
            } else if (currentChartEventFilter === 'UNCONFIRMED') {
                filtered = filtered.filter(e => e.event_type === 'SWEEP_UNCONFIRMED');
            }

            if (countBadge) countBadge.textContent = filtered.length;

            if (filtered.length === 0) {
                container.innerHTML = `<div class="py-8 text-center text-xs text-slate-500 font-mono">No events matching filter '${currentChartEventFilter}'.</div>`;
                return;
            }

            container.innerHTML = filtered.map(evt => {
                const isSelected = evt.event_id === currentSelectedEventId;
                const isLong = evt.direction === 'LONG';
                let statusBadge = '<span class="px-1.5 py-0.2 rounded text-[10px] font-bold bg-amber-950 text-amber-300 border border-amber-800">RECLAIMED</span>';
                if (evt.event_type === 'SWEEP_CONFIRMED') {
                    statusBadge = '<span class="px-1.5 py-0.2 rounded text-[10px] font-bold bg-purple-950 text-purple-300 border border-purple-800">CONFIRMED</span>';
                } else if (evt.event_type === 'SWEEP_UNCONFIRMED') {
                    statusBadge = '<span class="px-1.5 py-0.2 rounded text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700">SWEEP ONLY</span>';
                } else if (evt.event_type === 'SWEEP_INVALIDATED') {
                    statusBadge = '<span class="px-1.5 py-0.2 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800">BROKEN</span>';
                }

                const dirColor = isLong ? 'text-emerald-400' : 'text-rose-400';
                const timeStr = evt.provenance?.candle_timestamp_utc ? new Date(evt.provenance.candle_timestamp_utc).toLocaleTimeString() : '--';

                return `
                    <div onclick="selectChartEvent('${evt.event_id}')" class="p-2.5 rounded-lg border transition cursor-pointer ${isSelected ? 'bg-indigo-950/40 border-indigo-500 shadow-sm' : 'bg-slate-900/60 hover:bg-slate-900 border-slate-800'}">
                        <div class="flex items-center justify-between text-xs mb-1">
                            <span class="font-bold font-mono ${dirColor}">${evt.direction} SWEEP</span>
                            ${statusBadge}
                        </div>
                        <div class="text-[11px] text-slate-300 font-mono space-y-0.5">
                            <div class="flex justify-between">
                                <span class="text-slate-500">Level:</span>
                                <span class="font-bold text-white">$${formatPrice(evt.level_price)}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-slate-500">Sweep Wick:</span>
                                <span>$${formatPrice(evt.sweep_price)} (+${evt.sweep_depth_bps} bps)</span>
                            </div>
                            ${evt.reclaim_price ? `
                            <div class="flex justify-between text-amber-300">
                                <span class="text-slate-500">Reclaim Close:</span>
                                <span>$${formatPrice(evt.reclaim_price)}</span>
                            </div>` : ''}
                        </div>
                        <div class="mt-1.5 text-[10px] text-slate-500 flex justify-between items-center border-t border-slate-800/80 pt-1">
                            <span>Bar ${evt.sweep_index} &bull; ${timeStr}</span>
                            <span class="text-indigo-400 hover:underline">Inspect &rarr;</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function filterChartEvents(event) {
            currentChartEventFilter = event.target.value || 'ALL';
            renderEventsList(chartEventsData);
        }

        function selectChartEvent(eventId) {
            currentSelectedEventId = eventId;
            renderEventsList(chartEventsData);

            const evt = chartEventsData.find(e => e.event_id === eventId);
            if (!evt) return;

            if (tvChart && evt.sweep_timestamp_ms) {
                try {
                    tvChart.timeScale().scrollToPosition(-5, true);
                } catch (e) {}
            }

            const card = document.getElementById('selectedEventDetailCard');
            const content = document.getElementById('selectedEventDetailContent');
            const badge = document.getElementById('cardEventStatusBadge');
            if (card && content) {
                card.classList.remove('hidden');
                if (badge) {
                    badge.textContent = evt.event_type;
                    badge.className = evt.event_type === 'SWEEP_CONFIRMED'
                        ? 'px-1.5 py-0.2 text-[10px] font-bold rounded bg-purple-950 text-purple-300 border border-purple-800'
                        : (evt.event_type === 'SWEEP_RECLAIMED'
                            ? 'px-1.5 py-0.2 text-[10px] font-bold rounded bg-amber-950 text-amber-300 border border-amber-800'
                            : 'px-1.5 py-0.2 text-[10px] font-bold rounded bg-slate-800 text-slate-300');
                }

                content.innerHTML = `
                    <div class="flex justify-between border-b border-slate-800/80 pb-1">
                        <span class="text-slate-400">Direction &amp; Level:</span>
                        <span class="font-bold text-white">${evt.direction} across $${formatPrice(evt.level_price)}</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-slate-400">Sweep Penetration:</span>
                        <span class="text-rose-400 font-bold">$${formatPrice(evt.sweep_price)} (${evt.sweep_depth_pct}% / ${evt.sweep_depth_bps} bps)</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-slate-400">Reclaim Price:</span>
                        <span class="text-amber-300 font-bold">${evt.reclaim_price ? '$' + formatPrice(evt.reclaim_price) : 'Pending close'}</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-slate-400">Invalidation Stop (SL):</span>
                        <span class="text-red-400 font-bold">$${formatPrice(evt.invalidation_price)}</span>
                    </div>
                    ${evt.target_tp1 ? `
                    <div class="flex justify-between text-emerald-400">
                        <span class="text-slate-400">Suggested TP1 / TP2:</span>
                        <span>$${formatPrice(evt.target_tp1)} / $${formatPrice(evt.target_tp2)}</span>
                    </div>` : ''}
                    <div class="border-t border-slate-800/80 pt-1 text-[11px] text-slate-400">
                        <p class="leading-relaxed">${escapeHtml(evt.evidence_text)}</p>
                    </div>
                `;
            }
        }

        // =========================================================================
        // Historical Replay Controls
        // =========================================================================

        function toggleReplayMode() {
            isReplayMode = !isReplayMode;
            const btn = document.getElementById('replayModeBtn');
            const dot = document.getElementById('replayDot');
            const text = document.getElementById('replayModeText');
            const group = document.getElementById('replayControlsGroup');
            const scrubber = document.getElementById('replayScrubberContainer');
            const indicator = document.getElementById('replayCurrentTimestamp');

            if (isReplayMode) {
                if (btn) btn.className = 'px-2.5 py-1.5 rounded-lg font-semibold bg-indigo-950 text-indigo-300 border border-indigo-700 transition flex items-center gap-1.5';
                if (dot) dot.className = 'w-2 h-2 rounded-full bg-indigo-400 animate-pulse';
                if (text) text.textContent = 'Replay Active';
                if (group) group.classList.remove('hidden');
                if (scrubber) scrubber.classList.remove('hidden');

                if (chartLayersData && chartLayersData.replay_metadata) {
                    const total = chartLayersData.replay_metadata.total_bars;
                    replayCurrentIndex = Math.max(0, total - 1);
                    const slider = document.getElementById('replayBarSlider');
                    if (slider) slider.value = replayCurrentIndex;
                    loadLiquidityChartData(replayCurrentIndex);
                }
            } else {
                if (replayTimer) {
                    clearInterval(replayTimer);
                    replayTimer = null;
                }
                replayPlaying = false;
                const playText = document.getElementById('replayPlayText');
                if (playText) playText.textContent = 'Play';

                if (btn) btn.className = 'px-2.5 py-1.5 rounded-lg font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center gap-1.5';
                if (dot) dot.className = 'w-2 h-2 rounded-full bg-slate-400';
                if (text) text.textContent = 'Replay Mode';
                if (group) group.classList.add('hidden');
                if (scrubber) scrubber.classList.add('hidden');
                if (indicator) indicator.textContent = 'Live Mode';

                replayCurrentIndex = null;
                loadLiquidityChartData();
            }
        }

        function updateReplaySliderBounds(meta) {
            const slider = document.getElementById('replayBarSlider');
            const minLabel = document.getElementById('replayMinBarLabel');
            const curLabel = document.getElementById('replayCurrentBarLabel');
            const curTime = document.getElementById('replayCurrentTimestamp');

            if (!slider || !meta) return;
            const total = meta.total_bars || 100;
            slider.max = total - 1;
            if (minLabel) minLabel.textContent = 'Bar 0';
            
            const curIdx = meta.as_of_index !== null && meta.as_of_index !== undefined ? meta.as_of_index : (total - 1);
            slider.value = curIdx;
            if (curLabel) curLabel.textContent = `Bar ${curIdx}/${total - 1}`;
            if (curTime) {
                curTime.textContent = isReplayMode ? `Bar ${curIdx} (Anti-Lookahead Active)` : 'Live Mode';
            }
        }

        function handleReplaySliderInput(event) {
            const idx = parseInt(event.target.value, 10);
            replayCurrentIndex = idx;
            loadLiquidityChartData(idx);
        }

        function stepReplayBackward() {
            if (!isReplayMode || replayCurrentIndex === null) return;
            if (replayCurrentIndex > 0) {
                replayCurrentIndex -= 1;
                const slider = document.getElementById('replayBarSlider');
                if (slider) slider.value = replayCurrentIndex;
                loadLiquidityChartData(replayCurrentIndex);
            }
        }

        function stepReplayForward() {
            if (!isReplayMode || replayCurrentIndex === null || !chartLayersData) return;
            const total = chartLayersData.replay_metadata?.total_bars || 100;
            if (replayCurrentIndex < total - 1) {
                replayCurrentIndex += 1;
                const slider = document.getElementById('replayBarSlider');
                if (slider) slider.value = replayCurrentIndex;
                loadLiquidityChartData(replayCurrentIndex);
            }
        }

        function toggleReplayPlay() {
            replayPlaying = !replayPlaying;
            const playText = document.getElementById('replayPlayText');
            const playIcon = document.getElementById('replayPlayIcon');

            if (replayPlaying) {
                if (playText) playText.textContent = 'Pause';
                if (playIcon) playIcon.innerHTML = '&#10074;&#10074;';
                replayTimer = setInterval(() => {
                    if (!chartLayersData || replayCurrentIndex === null) return;
                    const total = chartLayersData.replay_metadata?.total_bars || 100;
                    if (replayCurrentIndex < total - 1) {
                        stepReplayForward();
                    } else {
                        toggleReplayPlay();
                    }
                }, 850);
            } else {
                if (playText) playText.textContent = 'Play';
                if (playIcon) playIcon.innerHTML = '&#9658;';
                if (replayTimer) {
                    clearInterval(replayTimer);
                    replayTimer = null;
                }
            }
        }

        function resetReplayToLatest() {
            if (!chartLayersData) return;
            const total = chartLayersData.replay_metadata?.total_bars || 100;
            replayCurrentIndex = total - 1;
            const slider = document.getElementById('replayBarSlider');
            if (slider) slider.value = replayCurrentIndex;
            loadLiquidityChartData(replayCurrentIndex);
        }