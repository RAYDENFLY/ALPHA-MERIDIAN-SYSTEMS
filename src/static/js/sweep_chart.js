// =========================================================================
// FEATURE E: INTERACTIVE LIQUIDITY SWEEP & RECLAIM CHART ENGINE
// Viewport-Aware Rendering & Deterministic Density Management Architecture
// =========================================================================
        let chartAutoRefreshTimer = null;
        let currentVenueSource = 'combined';
        let currentViewportPreset = 'default';
        let viewportDebounceTimer = null;
        let lastChartRenderKey = null;

        // -----------------------------------------------------------------
        // 1. Historical Data Storage (Separated from Viewport & Rendering)
        // -----------------------------------------------------------------
        let chartRequestEpoch = 0;
        let isIncrementalHistoryLoading = false;
        const INITIAL_CANDLE_WINDOW = 500;
        const HISTORY_PAGE_BATCH = 500;
        const SCROLL_LEFT_PREFETCH_THRESHOLD_BARS = 50;

        const sweepChartStore = {
            cacheByKey: new Map(), // key -> full API response payload
            activeKey: null,
            symbol: 'BTCUSDT',
            timeframe: '1h',
            candles: [],
            candlesByTimeSec: new Map(),
            structuralLevels: [],
            levelsById: new Map(),
            sweepEvents: [],
            eventsById: new Map(),
            chartMarkers: [],
            orderBookOverlay: null,
            liquidationOverlay: null,
            replayMetadata: null,
            historyMetadata: {
                dbTotalCandles: 0,
                dbEarliestTimestampMs: null,
                dbLatestTimestampMs: null,
                oldestLoadedTimestampMs: null,
                newestLoadedTimestampMs: null,
                hasMoreBefore: true,
                nextBeforeTimeMs: null
            },
            feedStatus: null,
            lastVisibleRange: null, // { from, to }
            lastViewportMetrics: null
        };

        // -----------------------------------------------------------------
        // 1B. TradingView-Style Drawing Engine State (SQLite-Persisted)
        // -----------------------------------------------------------------
        const chartDrawingsState = {
            userProfile: 'default',
            activeTool: 'crosshair',
            isVisible: true,
            drawings: [],            // persisted drawings for active (symbol, timeframe)
            selectedDrawingId: null,
            inProgressDrawing: null, // while placing multi-point drawing
            dragState: null,         // { drawingId, pointIndex, startLogical, startPrice }
            undoStack: [],           // array of snapshots
            redoStack: [],
            listenersBound: false
        };

        function buildChartCacheKey(symbol, tf, include1d, venue, asOfIndex) {
            const idxPart = (asOfIndex !== null && asOfIndex !== undefined) ? String(asOfIndex) : 'live';
            return `${symbol}|${tf}|${include1d ? '1d' : 'no1d'}|${venue}|${idxPart}`;
        }

        function deduplicateAndSortCandles(existingCandles, incomingCandles) {
            const byTimeSec = new Map();
            (existingCandles || []).forEach(c => {
                if (c && typeof c.time === 'number' && Number.isFinite(c.open) && Number.isFinite(c.close)) {
                    byTimeSec.set(c.time, c);
                }
            });
            (incomingCandles || []).forEach(c => {
                if (c && typeof c.time === 'number' && Number.isFinite(c.open) && Number.isFinite(c.close)) {
                    byTimeSec.set(c.time, c);
                }
            });
            const merged = Array.from(byTimeSec.values());
            merged.sort((a, b) => a.time - b.time);
            return merged;
        }

        function deduplicateLevels(existingLevels, incomingLevels) {
            const byId = new Map();
            (existingLevels || []).forEach(l => {
                if (!l) return;
                const k = l.level_id || `${l.level_type}_${l.price}_${l.created_timestamp_ms || 0}`;
                byId.set(k, l);
            });
            (incomingLevels || []).forEach(l => {
                if (!l) return;
                const k = l.level_id || `${l.level_type}_${l.price}_${l.created_timestamp_ms || 0}`;
                byId.set(k, l);
            });
            return Array.from(byId.values());
        }

        function deduplicateEvents(existingEvents, incomingEvents) {
            const byId = new Map();
            (existingEvents || []).forEach(e => {
                if (!e) return;
                const k = e.event_id || `${e.direction}_${e.sweep_timestamp_ms}_${e.level_price}`;
                byId.set(k, e);
            });
            (incomingEvents || []).forEach(e => {
                if (!e) return;
                const k = e.event_id || `${e.direction}_${e.sweep_timestamp_ms}_${e.level_price}`;
                byId.set(k, e);
            });
            const merged = Array.from(byId.values());
            merged.sort((a, b) => (Number(a.sweep_timestamp_ms) || 0) - (Number(b.sweep_timestamp_ms) || 0));
            return merged;
        }

        function deduplicateMarkers(existingMarkers, incomingMarkers) {
            const byId = new Map();
            (existingMarkers || []).forEach(m => {
                if (!m) return;
                const k = m.id || `${m.time}_${m.position}_${m.text}`;
                byId.set(k, m);
            });
            (incomingMarkers || []).forEach(m => {
                if (!m) return;
                const k = m.id || `${m.time}_${m.position}_${m.text}`;
                byId.set(k, m);
            });
            const merged = Array.from(byId.values());
            merged.sort((a, b) => a.time - b.time);
            return merged;
        }

        function syncHistoryMetadataFromData(data, mergedCandles) {
            const hm = data.history_metadata || {};
            const rm = data.replay_metadata || {};
            const oldestMs = mergedCandles.length > 0
                ? (mergedCandles[0].timestamp_ms || mergedCandles[0].time * 1000)
                : (hm.oldest_loaded_timestamp_ms ?? null);
            const newestMs = mergedCandles.length > 0
                ? (mergedCandles[mergedCandles.length - 1].timestamp_ms || mergedCandles[mergedCandles.length - 1].time * 1000)
                : (hm.newest_loaded_timestamp_ms ?? null);
            const dbEarliestMs = hm.db_earliest_timestamp_ms ?? rm.db_earliest_timestamp_ms ?? oldestMs;
            const dbLatestMs = hm.db_latest_timestamp_ms ?? rm.db_latest_timestamp_ms ?? newestMs;
            const dbTotal = Math.max(
                mergedCandles.length,
                Number(hm.db_total_candles || rm.db_total_bars || rm.total_bars || mergedCandles.length)
            );
            const hasMore = Boolean(oldestMs && dbEarliestMs && oldestMs > dbEarliestMs);

            sweepChartStore.historyMetadata = {
                dbTotalCandles: dbTotal,
                dbEarliestTimestampMs: dbEarliestMs,
                dbLatestTimestampMs: dbLatestMs,
                oldestLoadedTimestampMs: oldestMs,
                newestLoadedTimestampMs: newestMs,
                hasMoreBefore: hasMore,
                nextBeforeTimeMs: hasMore ? oldestMs : null
            };
        }

        function storeHistoricalDataset(key, data, mergeWithExisting = false) {
            const sameSeries = (sweepChartStore.activeKey === key) && mergeWithExisting;
            sweepChartStore.activeKey = key;
            sweepChartStore.symbol = data.symbol || currentChartSymbol;
            sweepChartStore.timeframe = data.timeframe || currentChartTf;

            const incomingCandles = Array.isArray(data.candles) ? data.candles : [];
            const incomingLevels = Array.isArray(data.structural_levels) ? data.structural_levels : [];
            const incomingEvents = Array.isArray(data.sweep_events) ? data.sweep_events : [];
            const incomingMarkers = Array.isArray(data.chart_markers) ? data.chart_markers : [];

            sweepChartStore.candles = sameSeries
                ? deduplicateAndSortCandles(sweepChartStore.candles, incomingCandles)
                : deduplicateAndSortCandles([], incomingCandles);
            sweepChartStore.structuralLevels = sameSeries
                ? deduplicateLevels(sweepChartStore.structuralLevels, incomingLevels)
                : deduplicateLevels([], incomingLevels);
            sweepChartStore.sweepEvents = sameSeries
                ? deduplicateEvents(sweepChartStore.sweepEvents, incomingEvents)
                : deduplicateEvents([], incomingEvents);
            sweepChartStore.chartMarkers = sameSeries
                ? deduplicateMarkers(sweepChartStore.chartMarkers, incomingMarkers)
                : deduplicateMarkers([], incomingMarkers);

            if (data.order_book_overlay) sweepChartStore.orderBookOverlay = data.order_book_overlay;
            if (data.liquidation_overlay) sweepChartStore.liquidationOverlay = data.liquidation_overlay;
            if (data.replay_metadata) sweepChartStore.replayMetadata = data.replay_metadata;
            if (data.feed_status) sweepChartStore.feedStatus = data.feed_status;

            syncHistoryMetadataFromData(data, sweepChartStore.candles);

            // Keep chartLayersData synchronized with full merged store so overlays/events never lose prepended history
            chartLayersData = Object.assign({}, data, {
                candles: sweepChartStore.candles,
                structural_levels: sweepChartStore.structuralLevels,
                sweep_events: sweepChartStore.sweepEvents,
                chart_markers: sweepChartStore.chartMarkers,
                order_book_overlay: sweepChartStore.orderBookOverlay,
                liquidation_overlay: sweepChartStore.liquidationOverlay,
                history_metadata: sweepChartStore.historyMetadata
            });
            chartEventsData = sweepChartStore.sweepEvents;

            sweepChartStore.cacheByKey.set(key, {
                data: chartLayersData,
                fetchedAtMs: Date.now()
            });
        }

        function startChartAutoRefresh() {
            stopChartAutoRefresh();
            chartAutoRefreshTimer = setInterval(() => {
                const tabEl = document.getElementById('tab-sweep_chart');
                if (tabEl && !tabEl.classList.contains('hidden') && !isReplayMode) {
                    loadLiquidityChartData(null, true);
                }
            }, 10000);
        }

        function stopChartAutoRefresh() {
            if (chartAutoRefreshTimer) {
                clearInterval(chartAutoRefreshTimer);
                chartAutoRefreshTimer = null;
            }
        }

        function initOrRefreshLiquidityChart() {
            loadLiquidityChartSymbols();
            loadLiquidityChartData(null, false);
            startChartAutoRefresh();
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
            loadLiquidityChartData(null, false);
        }

        function handleChartSymbolChange(event) {
            currentChartSymbol = (event.target.value || 'BTCUSDT').trim().toUpperCase();
            const inp = document.getElementById('chartSymbolInput');
            if (inp) inp.value = currentChartSymbol;
            if (isReplayMode) toggleReplayMode();
            loadLiquidityChartData(null, false);
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

        let isChartEventsDrawerCollapsed = false;

        function computeAvailableChartCanvasSize() {
            const workspace = document.getElementById('chartWorkspace');
            const stackDiv = document.getElementById('chartCanvasStack');
            const chartDiv = document.getElementById('tvLightweightChartContainer');
            const targetEl = stackDiv || chartDiv;

            const measuredWidth = Math.round(
                targetEl?.getBoundingClientRect?.().width ||
                targetEl?.clientWidth ||
                workspace?.clientWidth ||
                window.innerWidth ||
                800
            );

            if (!isChartFullscreen) {
                return {
                    width: Math.max(280, measuredWidth),
                    height: 520
                };
            }

            const viewportH = window.innerHeight || document.documentElement?.clientHeight || 900;
            const toolbarH = document.getElementById('chartToolbar')?.offsetHeight || 52;
            const overlaysH = document.getElementById('chartOverlaysToolbar')?.offsetHeight || 38;
            const venueH = document.getElementById('chartVenueTelemetryBar')?.offsetHeight || 34;
            const densityH = document.getElementById('chartViewportDensityBar')?.offsetHeight || 34;
            const warnEl = document.getElementById('chartVenueWarningBanner');
            const warnH = (warnEl && !warnEl.classList.contains('hidden')) ? (warnEl.offsetHeight || 36) : 0;
            const drawingH = document.getElementById('chartDrawingToolbar')?.offsetHeight || 38;
            const replayH = document.getElementById('chartReplayBar')?.offsetHeight || 42;

            // Total non-canvas chrome inside #chartWorkspace in fullscreen (including flex gaps & padding)
            const chromeOverheadPx = toolbarH + overlaysH + venueH + densityH + warnH + drawingH + replayH + 54;
            const dynamicHeight = Math.max(260, viewportH - chromeOverheadPx);

            if (stackDiv) {
                stackDiv.style.height = `${dynamicHeight}px`;
            }

            const finalHeight = dynamicHeight;

            return {
                width: Math.max(280, measuredWidth),
                height: finalHeight
            };
        }

        function syncChartWorkspaceDimensions() {
            const stackDiv = document.getElementById('chartCanvasStack');
            const chartDiv = document.getElementById('tvLightweightChartContainer');
            if (!chartDiv) return;

            if (!isChartFullscreen) {
                if (stackDiv) stackDiv.style.height = '520px';
                chartDiv.style.height = '100%';
            } else {
                chartDiv.style.height = '100%';
            }

            const dims = computeAvailableChartCanvasSize();
            if (tvChart) {
                let savedLogicalRange = null;
                try {
                    savedLogicalRange = tvChart.timeScale().getVisibleLogicalRange();
                } catch (e) {}

                tvChart.applyOptions({
                    width: dims.width,
                    height: dims.height
                });

                if (savedLogicalRange) {
                    try {
                        tvChart.timeScale().setVisibleLogicalRange(savedLogicalRange);
                    } catch (e) {}
                }
            }
            refreshViewportOverlays();
            renderInteractiveChartDrawings();
        }

        function syncFullscreenControlsUI(active) {
            const card = document.getElementById('chartWorkspace');
            const floatingBtn = document.getElementById('chartExitFullscreenFloatingBtn');
            const expandIcon = document.getElementById('fullscreenExpandIcon');
            const collapseIcon = document.getElementById('fullscreenCollapseIcon');
            const btnText = document.getElementById('fullscreenBtnText');

            if (active) {
                if (card) card.classList.add('fullscreen-active');
                if (floatingBtn) floatingBtn.classList.remove('hidden');
                if (expandIcon) expandIcon.classList.add('hidden');
                if (collapseIcon) collapseIcon.classList.remove('hidden');
                if (btnText) btnText.textContent = 'Exit Fullscreen';
                document.body.style.overflow = 'hidden';
            } else {
                if (card) {
                    card.classList.remove('fullscreen-active', 'fixed', 'inset-0', 'z-50', 'w-screen', 'h-screen', 'rounded-none', 'bg-slate-950', 'p-4', 'overflow-y-auto');
                    card.style.zIndex = '';
                }
                if (floatingBtn) floatingBtn.classList.add('hidden');
                if (expandIcon) expandIcon.classList.remove('hidden');
                if (collapseIcon) collapseIcon.classList.add('hidden');
                if (btnText) btnText.textContent = 'Fullscreen';
                document.body.style.overflow = '';
            }
        }

        function toggleChartEventsDrawer(forceCollapse) {
            if (typeof forceCollapse === 'boolean') {
                isChartEventsDrawerCollapsed = forceCollapse;
            } else {
                isChartEventsDrawerCollapsed = !isChartEventsDrawerCollapsed;
            }

            const drawerCol = document.getElementById('chartEventsDrawerColumn');
            const primaryCol = document.getElementById('chartPrimaryColumn');
            const toggleBtn = document.getElementById('chartEventsDrawerToggleBtn');
            const toggleText = document.getElementById('chartEventsDrawerToggleText');
            const floatingShowBtn = document.getElementById('chartExpandEventsFloatingBtn');

            if (isChartEventsDrawerCollapsed) {
                if (drawerCol) drawerCol.classList.add('hidden');
                if (primaryCol) {
                    primaryCol.classList.remove('lg:col-span-3');
                    primaryCol.classList.add('lg:col-span-4');
                }
                if (toggleBtn) {
                    toggleBtn.setAttribute('aria-expanded', 'false');
                    toggleBtn.className = 'px-2.5 py-1.5 text-xs font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1.5 shadow-sm';
                }
                if (toggleText) toggleText.textContent = 'Show Events';
                if (floatingShowBtn) floatingShowBtn.classList.remove('hidden');
            } else {
                if (drawerCol) drawerCol.classList.remove('hidden');
                if (primaryCol) {
                    primaryCol.classList.remove('lg:col-span-4');
                    primaryCol.classList.add('lg:col-span-3');
                }
                if (toggleBtn) {
                    toggleBtn.setAttribute('aria-expanded', 'true');
                    toggleBtn.className = 'px-2.5 py-1.5 text-xs font-medium rounded bg-indigo-950/80 hover:bg-indigo-900 text-indigo-200 border border-indigo-700/80 transition flex items-center gap-1.5 shadow-sm';
                }
                if (toggleText) toggleText.textContent = 'Hide Events';
                if (floatingShowBtn) floatingShowBtn.classList.add('hidden');
            }

            requestAnimationFrame(() => {
                syncChartWorkspaceDimensions();
            });
        }

        async function toggleChartFullscreen() {
            const card = document.getElementById('chartWorkspace');
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
            syncFullscreenControlsUI(isChartFullscreen);
            requestAnimationFrame(() => {
                syncChartWorkspaceDimensions();
                setTimeout(syncChartWorkspaceDimensions, 80);
            });
        }

        function handleNativeFullscreenChange() {
            const card = document.getElementById('chartWorkspace');
            const isNativeFs = Boolean(card && (document.fullscreenElement === card || document.webkitFullscreenElement === card));
            isChartFullscreen = isNativeFs;
            syncFullscreenControlsUI(isNativeFs);
            requestAnimationFrame(() => {
                syncChartWorkspaceDimensions();
                setTimeout(syncChartWorkspaceDimensions, 80);
            });
        }

        document.addEventListener('fullscreenchange', handleNativeFullscreenChange);
        document.addEventListener('webkitfullscreenchange', handleNativeFullscreenChange);
        if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
            window.addEventListener('resize', () => {
                if (isChartFullscreen || tvChart) {
                    syncChartWorkspaceDimensions();
                }
            });
        }
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && isChartFullscreen) {
                if (typeof chartDrawingsState !== 'undefined' && chartDrawingsState.activeTool !== 'crosshair') {
                    return;
                }
                if (!document.fullscreenElement && !document.webkitFullscreenElement) {
                    applyCssFullscreen(false);
                }
            }
        });

        function setVenueSource(venue) {
            currentVenueSource = venue || 'combined';
            ['combined', 'asterdex', 'binance', 'bybit'].forEach(v => {
                const btn = document.getElementById(`venueBtn-${v}`);
                if (btn) {
                    if (v === currentVenueSource) {
                        btn.className = 'px-2 py-0.5 rounded bg-emerald-600 text-white font-bold transition shadow-sm';
                    } else {
                        btn.className = 'px-2 py-0.5 rounded text-slate-400 hover:text-white transition';
                    }
                }
            });
            // Preserve viewport zoom/pan during venue toggling
            loadLiquidityChartData(null, true);
        }

        function setChartTimeframe(tf) {
            currentChartTf = tf;
            ['15m', '1h', '4h', '1d'].forEach(t => {
                const btn = document.getElementById(`chartTf-${t}`);
                if (btn) {
                    if (t === tf) {
                        btn.className = 'px-2.5 py-1 rounded bg-indigo-600 text-white font-bold transition';
                    } else {
                        btn.className = 'px-2.5 py-1 rounded text-slate-400 hover:text-white transition';
                    }
                }
            });

            const macroContainer = document.getElementById('overlay1DMacroContainer');
            if (macroContainer) {
                if (tf === '1d') {
                    macroContainer.classList.add('opacity-40', 'pointer-events-none');
                } else {
                    macroContainer.classList.remove('opacity-40', 'pointer-events-none');
                }
            }

            if (isReplayMode) toggleReplayMode();
            loadLiquidityChartData(null, false);
        }

        async function setChartViewportPreset(preset) {
            currentViewportPreset = preset || 'default';
            const presets = ['zoomed_in', 'default', 'multi_week', 'multi_month', 'full_history'];
            presets.forEach(p => {
                const btn = document.getElementById(`vpBtn-${p}`);
                if (btn) {
                    if (p === currentViewportPreset) {
                        btn.className = 'px-2 py-0.5 rounded bg-indigo-600 text-white font-bold transition';
                    } else {
                        btn.className = 'px-2 py-0.5 rounded text-slate-300 hover:text-white transition';
                    }
                }
            });

            // When user clicks 'Full History' or 'Multi-Month', ensure all available older pages are loaded first
            if ((currentViewportPreset === 'full_history' || currentViewportPreset === 'multi_month') && sweepChartStore.historyMetadata.hasMoreBefore) {
                let safetyPages = 0;
                while (sweepChartStore.historyMetadata.hasMoreBefore && safetyPages < 8) {
                    safetyPages += 1;
                    const loadedCount = await loadOlderHistoricalCandles();
                    if (!loadedCount) break;
                }
            }

            applyViewportPresetRange(currentViewportPreset);
        }

        function applyViewportPresetRange(preset) {
            if (!tvChart || !sweepChartStore.candles || sweepChartStore.candles.length === 0) return;
            const total = sweepChartStore.candles.length;
            let barsToShow = 120;
            if (preset === 'zoomed_in') barsToShow = 75;
            else if (preset === 'default') barsToShow = 120;
            else if (preset === 'multi_week') barsToShow = Math.min(total, 360);
            else if (preset === 'multi_month') barsToShow = Math.min(total, 1000);
            else if (preset === 'full_history') barsToShow = total;

            try {
                if (preset === 'full_history') {
                    tvChart.timeScale().fitContent();
                } else {
                    const fromIdx = Math.max(0, total - barsToShow);
                    const toIdx = total + 4;
                    tvChart.timeScale().setVisibleLogicalRange({ from: fromIdx, to: toIdx });
                }
            } catch (e) {
                console.warn('Could not apply viewport preset range:', e);
            }
            refreshViewportOverlays();
        }

        function renderVenueMetadata(data) {
            const ob = data.order_book_overlay || {};
            const fs = data.feed_status || {};
            const venueNameEl = document.getElementById('chartActiveVenueName');
            const statusBadge = document.getElementById('chartVenueStatusBadge');
            const contribContainer = document.getElementById('chartContributingVenuesContainer');
            const midEl = document.getElementById('chartVenueMidDisplay');
            const spreadEl = document.getElementById('chartVenueSpreadDisplay');
            const depthEl = document.getElementById('chartVenueDepthDisplay');
            const wallsCountEl = document.getElementById('chartVenueWallsCountDisplay');
            const freshnessEl = document.getElementById('chartVenueFreshnessDisplay');
            const warningBanner = document.getElementById('chartVenueWarningBanner');
            const warningMsg = document.getElementById('chartVenueWarningMessage');

            const venue = ob.venue_filter || currentVenueSource || 'combined';
            const vName = ob.venue_name || (venue === 'combined' ? 'Combined (Cross-Exchange)' : venue.toUpperCase());
            if (venueNameEl) venueNameEl.textContent = vName;

            const st = ob.status || 'AVAILABLE';
            if (statusBadge) {
                if (st === 'LIVE' || st === 'AVAILABLE') {
                    statusBadge.textContent = 'LIVE';
                    statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800';
                } else if (st === 'STALE') {
                    statusBadge.textContent = `STALE (${(ob.age_seconds || 0).toFixed(1)}s)`;
                    statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-300 border border-amber-800';
                } else if (st === 'IP_BANNED') {
                    statusBadge.textContent = 'HTTP 418 IP BAN';
                    statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800';
                } else if (st === 'RATE_LIMITED' || st === 'CIRCUIT_OPEN') {
                    statusBadge.textContent = st === 'RATE_LIMITED' ? 'HTTP 429 RATE LIMIT' : 'CIRCUIT OPEN';
                    statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-300 border border-amber-800';
                } else if (st === 'INVALID_SYMBOL') {
                    statusBadge.textContent = 'INVALID SYMBOL';
                    statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800';
                } else if (st === 'TIMEOUT') {
                    statusBadge.textContent = 'TIMEOUT';
                    statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800';
                } else if (st === 'UNAVAILABLE') {
                    statusBadge.textContent = 'UNAVAILABLE';
                    statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800';
                } else {
                    statusBadge.textContent = st;
                    statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950 text-rose-300 border border-rose-800';
                }
            }

            if (contribContainer) {
                const venues = ob.contributing_venues || [];
                if (venues.length > 0) {
                    contribContainer.innerHTML = venues.map(v => 
                        `<span class="px-1.5 py-0.2 rounded text-[9px] font-mono bg-slate-800 text-slate-300 border border-slate-700">${v.toUpperCase()}</span>`
                    ).join('');
                } else {
                    contribContainer.innerHTML = '';
                }
            }

            if (midEl) {
                midEl.textContent = ob.mid_price ? `$${formatPrice(ob.mid_price)}` : '--';
            }
            if (spreadEl) {
                spreadEl.textContent = ob.spread_bps !== undefined && ob.spread_bps !== null ? `${ob.spread_bps.toFixed(2)} bps` : '--';
            }
            if (depthEl) {
                if (ob.total_bid_depth_usdt !== undefined && ob.total_ask_depth_usdt !== undefined && ob.mid_price) {
                    depthEl.textContent = `Bids $${(ob.total_bid_depth_usdt / 1e6).toFixed(2)}M / Asks $${(ob.total_ask_depth_usdt / 1e6).toFixed(2)}M`;
                } else {
                    depthEl.textContent = '--';
                }
            }
            if (wallsCountEl) {
                const walls = ob.walls || [];
                wallsCountEl.textContent = `${walls.length} Wall${walls.length === 1 ? '' : 's'}`;
            }
            if (freshnessEl) {
                const age = ob.age_seconds !== undefined ? ob.age_seconds : (fs.order_book_age_seconds !== undefined ? fs.order_book_age_seconds : 0);
                freshnessEl.textContent = `${age.toFixed(1)}s ago`;
                freshnessEl.className = age <= 10.0 ? 'text-emerald-400 font-bold' : (age <= 30.0 ? 'text-amber-400 font-medium' : 'text-rose-400 font-medium');
            }

            if (warningBanner) {
                if (!['LIVE', 'AVAILABLE'].includes(st) && ob.error_message) {
                    warningBanner.classList.remove('hidden');
                    if (warningMsg) warningMsg.textContent = ob.error_message;
                } else if (['UNAVAILABLE', 'ERROR', 'IP_BANNED', 'RATE_LIMITED', 'CIRCUIT_OPEN', 'INVALID_SYMBOL', 'TIMEOUT', 'MALFORMED_RESPONSE', 'EMPTY_ORDER_BOOK', 'HTTP_ERROR'].includes(st)) {
                    warningBanner.classList.remove('hidden');
                    if (warningMsg) warningMsg.textContent = ob.error_message || `${vName} order book status: ${st} for ${currentChartSymbol}.`;
                } else {
                    warningBanner.classList.add('hidden');
                }
            }
        }

        // -----------------------------------------------------------------
        // 2. API Data Retrieval & Cursor-Based Incremental Historical Loading
        // -----------------------------------------------------------------
        function setHistoryLoadingIndicator(isLoading, messageText) {
            const pill = document.getElementById('chartHistoryLoadingPill');
            const textEl = document.getElementById('chartHistoryLoadingText');
            if (textEl && messageText) textEl.textContent = messageText;
            if (pill) {
                if (isLoading) pill.classList.remove('hidden');
                else pill.classList.add('hidden');
            }
        }

        async function loadLiquidityChartData(asOfIndex = null, preserveViewport = false) {
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

            // If switching symbol/timeframe (not a background poll), advance epoch to invalidate stale in-flight requests
            if (!preserveViewport) {
                chartRequestEpoch += 1;
                isIncrementalHistoryLoading = false;
                setHistoryLoadingIndicator(false);
            }
            const reqEpoch = chartRequestEpoch;
            const reqSymbol = currentChartSymbol;
            const reqTf = currentChartTf;

            try {
                const include1d = Boolean(activeOverlays.macro_1d && reqTf !== '1d');
                const cacheKey = buildChartCacheKey(reqSymbol, reqTf, include1d, currentVenueSource, asOfIndex);
                const initialLimit = INITIAL_CANDLE_WINDOW;

                let url = `/api/chart/liquidity_layers?symbol=${encodeURIComponent(reqSymbol)}&timeframe=${encodeURIComponent(reqTf)}&limit=${initialLimit}`;
                if (include1d) {
                    url += `&include_1d_overlay=true`;
                }
                if (currentVenueSource) {
                    url += `&venue_source=${encodeURIComponent(currentVenueSource)}`;
                }
                if (asOfIndex !== null && asOfIndex !== undefined) {
                    url += `&as_of_index=${encodeURIComponent(asOfIndex)}`;
                }

                const res = await fetch(url);
                // Ignore stale responses if user switched symbol or timeframe while request was in flight
                if (reqEpoch !== chartRequestEpoch || reqSymbol !== currentChartSymbol || reqTf !== currentChartTf) {
                    return;
                }
                if (!res.ok) {
                    let detail = '';
                    try {
                        const body = await res.json();
                        detail = body.detail ? `: ${typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)}` : '';
                    } catch (_) { detail = await res.text().catch(() => ''); }
                    throw new Error(`Failed to load liquidity chart layers (${res.status} ${res.statusText})${detail} [${url}]`);
                }
                const data = await res.json();
                if (reqEpoch !== chartRequestEpoch || reqSymbol !== currentChartSymbol || reqTf !== currentChartTf) {
                    return;
                }

                // When preserveViewport is true (e.g., 10s live poll or venue switch), merge into existing loaded history
                // so prepended historical candles are never wiped out by live updates!
                storeHistoricalDataset(cacheKey, data, preserveViewport);

                // Load persisted SQLite drawings when switching symbol or timeframe
                if (!preserveViewport) {
                    loadPersistedChartDrawings(reqSymbol, reqTf);
                }

                // Render Venue Metadata Bar
                renderVenueMetadata(chartLayersData);

                // Update Feed Badge with Venue-specific Freshness
                const feedBadge = document.getElementById('chartFeedBadge');
                if (feedBadge && data.feed_status) {
                    const st = data.feed_status;
                    const ob = data.order_book_overlay || {};
                    const vName = ob.venue_name || (currentVenueSource === 'combined' ? 'Combined' : currentVenueSource.toUpperCase());
                    const obAge = (ob.age_seconds !== undefined ? ob.age_seconds : 0.0).toFixed(1);

                    if (st.status === 'REPLAY') {
                        feedBadge.textContent = `REPLAY: BAR ${data.replay_metadata?.as_of_index ?? '--'}/${data.replay_metadata?.total_bars ?? '--'}`;
                        feedBadge.className = 'px-2 py-0.5 text-[10px] font-mono rounded bg-amber-950 text-amber-300 border border-amber-800';
                    } else if (ob.status === 'UNAVAILABLE') {
                        feedBadge.textContent = `ORDER BOOK: UNAVAILABLE (${vName})`;
                        feedBadge.className = 'px-2 py-0.5 text-[10px] font-mono rounded bg-rose-950 text-rose-300 border border-rose-800';
                    } else if (ob.status === 'ERROR') {
                        feedBadge.textContent = `FEED ERROR (${vName})`;
                        feedBadge.className = 'px-2 py-0.5 text-[10px] font-mono rounded bg-rose-950 text-rose-300 border border-rose-800';
                    } else if (st.is_stale || ob.status === 'STALE') {
                        feedBadge.textContent = `FEED: STALE (${obAge}s • ${vName})`;
                        feedBadge.className = 'px-2 py-0.5 text-[10px] font-mono rounded bg-amber-950 text-amber-300 border border-amber-800';
                    } else {
                        feedBadge.textContent = `FEED: LIVE (${vName} • ${obAge}s)`;
                        feedBadge.className = 'px-2 py-0.5 text-[10px] font-mono rounded bg-emerald-950 text-emerald-300 border border-emerald-800';
                    }
                }

                renderLightweightChart(chartLayersData, preserveViewport);
                renderEventsList(chartEventsData);

                if (data.replay_metadata) {
                    updateReplaySliderBounds(data.replay_metadata);
                }

            } catch (err) {
                console.error('Error loading liquidity chart:', err);
                const eventsList = document.getElementById('chartEventsList');
                const canvas = document.getElementById('liquidityChart');
                if (canvas) canvas.innerHTML = `<div class="h-full min-h-[220px] flex items-center justify-center p-6 text-center text-xs text-rose-300 font-mono">${escapeHtml(err.message)}<br><button class="mt-3 px-3 py-1 rounded bg-indigo-600 text-white" onclick="loadLiquidityChartData()">Retry</button></div>`;
                if (eventsList) {
                    eventsList.innerHTML = `<div class="p-4 text-center text-xs text-rose-400 font-mono">Error: ${escapeHtml(err.message)}</div>`;
                }
            } finally {
                if (spin) spin.classList.remove('animate-spin');
                if (overlay) overlay.classList.add('hidden');
            }
        }

        async function loadOlderHistoricalCandles() {
            if (isIncrementalHistoryLoading || isReplayMode) return 0;
            const hm = sweepChartStore.historyMetadata;
            if (!hm || !hm.hasMoreBefore || !hm.oldestLoadedTimestampMs) {
                return 0;
            }

            isIncrementalHistoryLoading = true;
            const reqEpoch = chartRequestEpoch;
            const reqSymbol = currentChartSymbol;
            const reqTf = currentChartTf;
            const beforeMs = hm.oldestLoadedTimestampMs;
            const include1d = Boolean(activeOverlays.macro_1d && reqTf !== '1d');
            const cacheKey = buildChartCacheKey(reqSymbol, reqTf, include1d, currentVenueSource, null);

            const beforeDateStr = new Date(beforeMs).toISOString().slice(0, 10);
            setHistoryLoadingIndicator(true, `Loading history before ${beforeDateStr}...`);

            try {
                let url = `/api/chart/liquidity_layers?symbol=${encodeURIComponent(reqSymbol)}&timeframe=${encodeURIComponent(reqTf)}&limit=${HISTORY_PAGE_BATCH}&before_time_ms=${encodeURIComponent(beforeMs)}`;
                if (include1d) url += `&include_1d_overlay=true`;
                if (currentVenueSource) url += `&venue_source=${encodeURIComponent(currentVenueSource)}`;

                const res = await fetch(url);
                if (reqEpoch !== chartRequestEpoch || reqSymbol !== currentChartSymbol || reqTf !== currentChartTf) {
                    return 0;
                }
                if (!res.ok) {
                    return 0;
                }
                const olderData = await res.json();
                if (reqEpoch !== chartRequestEpoch || reqSymbol !== currentChartSymbol || reqTf !== currentChartTf) {
                    return 0;
                }

                const prevLen = sweepChartStore.candles.length;
                let savedLogicalRange = null;
                if (tvChart) {
                    try {
                        savedLogicalRange = tvChart.timeScale().getVisibleLogicalRange();
                    } catch (e) {}
                }

                const olderCandles = Array.isArray(olderData.candles) ? olderData.candles : [];
                if (olderCandles.length === 0) {
                    sweepChartStore.historyMetadata.hasMoreBefore = false;
                    sweepChartStore.historyMetadata.nextBeforeTimeMs = null;
                    refreshViewportOverlays();
                    return 0;
                }

                // Merge older candles, levels, events, and markers with existing store
                storeHistoricalDataset(cacheKey, olderData, true);
                const newLen = sweepChartStore.candles.length;
                const prependedCount = Math.max(0, newLen - prevLen);

                if (prependedCount === 0) {
                    sweepChartStore.historyMetadata.hasMoreBefore = false;
                    sweepChartStore.historyMetadata.nextBeforeTimeMs = null;
                    refreshViewportOverlays();
                    return 0;
                }

                // Update chart series while shifting logical viewport by +prependedCount so user's view never jumps!
                if (tvChart && candleSeries && volumeSeries) {
                    const chartData = sweepChartStore.candles.map(c => ({
                        time: c.time,
                        open: c.open,
                        high: c.high,
                        low: c.low,
                        close: c.close
                    }));
                    const volumeData = sweepChartStore.candles.map(c => ({
                        time: c.time,
                        value: c.volume,
                        color: c.close >= c.open ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)'
                    }));
                    candleSeries.setData(chartData);
                    volumeSeries.setData(volumeData);

                    if (savedLogicalRange && typeof savedLogicalRange.from === 'number' && typeof savedLogicalRange.to === 'number') {
                        const shiftedRange = {
                            from: savedLogicalRange.from + prependedCount,
                            to: savedLogicalRange.to + prependedCount
                        };
                        try {
                            tvChart.timeScale().setVisibleLogicalRange(shiftedRange);
                            sweepChartStore.lastVisibleRange = shiftedRange;
                        } catch (e) {}
                    }
                }

                renderEventsList(chartEventsData);
                refreshViewportOverlays();
                return prependedCount;
            } catch (err) {
                console.warn('Error loading older historical candles:', err);
                return 0;
            } finally {
                if (reqEpoch === chartRequestEpoch) {
                    isIncrementalHistoryLoading = false;
                    setHistoryLoadingIndicator(false);
                }
            }
        }

        function checkAndTriggerIncrementalHistoryLoad(logicalRange) {
            if (!logicalRange || isIncrementalHistoryLoading || isReplayMode) return;
            const hm = sweepChartStore.historyMetadata;
            if (!hm || !hm.hasMoreBefore) return;
            if (typeof logicalRange.from === 'number' && logicalRange.from <= SCROLL_LEFT_PREFETCH_THRESHOLD_BARS) {
                loadOlderHistoricalCandles();
            }
        }

        // -----------------------------------------------------------------
        // 3. Viewport Filtering & Deterministic Density Management Engine
        // -----------------------------------------------------------------
        function computeViewportFilteredOverlays(storeData, logicalRange, chartHeightPx, overlaysConfig) {
            const candles = storeData.candles || [];
            const allLevels = storeData.structural_levels || [];
            const allEvents = storeData.sweep_events || [];
            const allMarkers = storeData.chart_markers || [];
            const obOverlay = storeData.order_book_overlay || {};
            const liqOverlay = storeData.liquidation_overlay || {};
            const obWalls = Array.isArray(obOverlay.walls) ? obOverlay.walls : [];
            const liqClusters = Array.isArray(liqOverlay.clusters) ? liqOverlay.clusters : [];

            const totalCandles = candles.length;
            if (totalCandles === 0) {
                return {
                    visibleMarkers: [],
                    visiblePriceLines: [],
                    telemetry: {
                        totalCandles: 0,
                        visibleCandles: 0,
                        totalLevels: allLevels.length,
                        visibleLevels: 0,
                        totalEvents: allEvents.length,
                        visibleEvents: 0,
                        totalMarkers: allMarkers.length,
                        renderedMarkers: 0,
                        maxRightAxisLabels: 8,
                        renderedRightAxisLabels: 0,
                        zoomMode: 'EMPTY'
                    }
                };
            }

            // Determine logical index bounds
            let fromIdx = 0;
            let toIdx = totalCandles - 1;
            if (logicalRange && typeof logicalRange.from === 'number' && typeof logicalRange.to === 'number') {
                fromIdx = Math.max(0, Math.floor(logicalRange.from));
                toIdx = Math.min(totalCandles - 1, Math.ceil(logicalRange.to));
                if (toIdx < fromIdx) {
                    fromIdx = Math.max(0, totalCandles - 120);
                    toIdx = totalCandles - 1;
                }
            } else {
                fromIdx = Math.max(0, totalCandles - 120);
                toIdx = totalCandles - 1;
            }

            const visibleCandlesSlice = candles.slice(fromIdx, toIdx + 1);
            const visibleBarsCount = Math.max(1, visibleCandlesSlice.length);
            const visStartSec = visibleCandlesSlice[0]?.time ?? candles[0].time;
            const visEndSec = visibleCandlesSlice[visibleCandlesSlice.length - 1]?.time ?? candles[totalCandles - 1].time;
            const visStartMs = visStartSec * 1000;
            const visEndMs = visEndSec * 1000;
            const includesLiveEdge = (toIdx >= totalCandles - 15);

            // Compute visible price range from visible candles
            let visLow = Infinity;
            let visHigh = -Infinity;
            for (let i = 0; i < visibleCandlesSlice.length; i++) {
                const c = visibleCandlesSlice[i];
                if (c.low < visLow) visLow = c.low;
                if (c.high > visHigh) visHigh = c.high;
            }
            if (!Number.isFinite(visLow) || !Number.isFinite(visHigh) || visHigh <= 0) {
                visLow = candles[totalCandles - 1].low;
                visHigh = candles[totalCandles - 1].high;
            }

            const refPrice = visibleCandlesSlice[visibleCandlesSlice.length - 1]?.close || candles[totalCandles - 1].close;
            const rawPriceSpan = Math.max(visHigh - visLow, refPrice * 0.008);
            // Expand price filter window by +/- 30% of visible candle span so nearby overhead/underfoot pools remain visible
            const priceMargin = rawPriceSpan * 0.30;
            const filterMinPrice = visLow - priceMargin;
            const filterMaxPrice = visHigh + priceMargin;

            // Determine Zoom Tier
            let zoomMode = 'DETAIL VIEW';
            if (visibleBarsCount > 550) {
                zoomMode = 'MACRO CLUSTERED';
            } else if (visibleBarsCount > 180) {
                zoomMode = 'MEDIUM OVERVIEW';
            }

            // Maximum right-axis labels scaled by chart height (1 label per ~42px, bounded 6..16)
            const heightPx = Math.max(320, Number(chartHeightPx) || 520);
            const maxRightAxisLabels = Math.max(6, Math.min(16, Math.floor(heightPx / 42)));
            const maxTotalPriceLines = Math.max(10, Math.min(22, Math.floor(heightPx / 28)));

            // -------------------------------------------------------------
            // A. Filter & Deduplicate Horizontal Price Lines (Phase 3 & 4)
            // -------------------------------------------------------------
            const candidateLines = [];

            // 1. Live Order-Book Walls (Strictly Live L2 Resting Liquidity)
            // Only display live order-book walls when orderbook overlay is enabled AND within expanded price window
            if (overlaysConfig.orderbook && includesLiveEdge && obWalls.length > 0) {
                obWalls.forEach((w, idx) => {
                    const price = Number(w.price);
                    if (!Number.isFinite(price) || price < filterMinPrice || price > filterMaxPrice) return;
                    const isBid = w.side === 'BID';
                    const color = isBid ? 'rgba(16, 185, 129, 0.95)' : 'rgba(244, 63, 94, 0.95)';
                    let venueBadge = '';
                    if (w.venues && w.venues.length > 0) {
                        venueBadge = ` [${w.venues.map(v => v.toUpperCase()).join('/')}]`;
                    }
                    const confBadge = w.is_cross_confirmed ? ` [${w.confirmation_tier}]` : '';
                    const depthK = Number(w.depth_usdt) >= 1e6
                        ? `$${(Number(w.depth_usdt) / 1e6).toFixed(2)}M`
                        : `$${Math.round(Number(w.depth_usdt) / 1e3)}k`;
                    const title = `[LIVE WALL] ${w.side}${confBadge}${venueBadge} ${depthK} (${w.distance_bps}bps)`;
                    const distRatio = Math.abs(price - refPrice) / rawPriceSpan;
                    const priorityScore = 1000 + (w.is_cross_confirmed ? 50 : 0) - Math.min(80, distRatio * 40) - idx;

                    candidateLines.push({
                        id: `ob_wall_${w.side}_${price}`,
                        category: 'LIVE_WALL',
                        price: price,
                        color: color,
                        lineWidth: w.is_cross_confirmed ? 3 : 2,
                        lineStyle: 'Solid',
                        title: title,
                        priority: priorityScore,
                        status: 'LIVE'
                    });
                });
            }

            // 2. Historical Structural Liquidity Levels (BSL / SSL / EQH / EQL / 1D HTF)
            if (overlaysConfig.structural && allLevels.length > 0) {
                allLevels.forEach(lvl => {
                    const price = Number(lvl.price);
                    if (!Number.isFinite(price)) return;
                    if (price < filterMinPrice || price > filterMaxPrice) return;

                    const status = lvl.status || 'ACTIVE';
                    const isHtf = Boolean(lvl.is_htf);
                    const createdMs = Number(lvl.confirmation_timestamp_ms || lvl.pivot_timestamp_ms || 0);
                    const sweptMs = Number(lvl.last_swept_timestamp_ms || 0);

                    // Temporal relevance check:
                    // - Do not render levels that were only confirmed AFTER the visible time window
                    if (createdMs > 0 && createdMs > visEndMs) return;

                    // - Suppress long-expired or long-broken historical levels unless their sweep occurred inside the visible window
                    if (!isHtf && (status === 'EXPIRED' || status === 'INVALIDATED_BROKEN')) {
                        const sweptInViewport = sweptMs >= visStartMs && sweptMs <= visEndMs;
                        // In wide zoom (>180 bars), never draw broken/expired horizontal lines across the entire canvas
                        if (visibleBarsCount > 180 || !sweptInViewport) {
                            return;
                        }
                    }

                    // Determine visual styling & clear [HIST] vs [1D HTF] prefix
                    let color = '#3b82f6';
                    let lineStyle = 'Dashed';
                    let shortType = lvl.source_label || 'BSL';
                    if (lvl.level_type === 'SWING_LOW_SSL') {
                        color = '#10b981';
                        shortType = lvl.source_label || 'SSL';
                    } else if (lvl.level_type === 'EQUAL_HIGHS') {
                        color = '#6366f1';
                        lineStyle = 'Solid';
                        shortType = lvl.source_label || 'EQH';
                    } else if (lvl.level_type === 'EQUAL_LOWS') {
                        color = '#14b8a6';
                        lineStyle = 'Solid';
                        shortType = lvl.source_label || 'EQL';
                    }

                    let title = '';
                    let basePriority = 500;
                    if (isHtf) {
                        color = (lvl.level_type && lvl.level_type.includes('HIGH')) ? '#c084fc' : '#a855f7';
                        lineStyle = 'Dashed';
                        title = `[1D HTF] ${shortType}`;
                        basePriority = 880;
                    } else if (status === 'RECLAIMED' || status === 'SWEPT_PENDING') {
                        color = '#f59e0b';
                        title = `[HIST ${shortType}] ${status === 'RECLAIMED' ? 'RECLAIMED' : 'SWEPT'}`;
                        basePriority = 780;
                    } else if (status === 'ACTIVE') {
                        const isEqualPool = (lvl.level_type === 'EQUAL_HIGHS' || lvl.level_type === 'EQUAL_LOWS');
                        title = `[HIST] ${shortType}`;
                        basePriority = isEqualPool ? 720 : 640;
                    } else {
                        // INVALIDATED_BROKEN in zoomed-in viewport
                        color = '#64748b';
                        lineStyle = 'Dotted';
                        title = `[HIST ${shortType}] BROKEN`;
                        basePriority = 320;
                    }

                    const distRatio = Math.abs(price - refPrice) / rawPriceSpan;
                    const touchBonus = Math.min(30, (Number(lvl.touch_count) || 1) * 8);
                    const recencyBonus = createdMs >= visStartMs ? 25 : 0;
                    const priorityScore = basePriority + touchBonus + recencyBonus - Math.min(120, distRatio * 65);

                    candidateLines.push({
                        id: lvl.level_id || `lvl_${price}`,
                        category: isHtf ? 'HTF_STRUCTURAL' : 'HIST_STRUCTURAL',
                        price: price,
                        color: color,
                        lineWidth: isHtf ? 2 : 1,
                        lineStyle: lineStyle,
                        title: title,
                        priority: priorityScore,
                        status: status,
                        levelType: lvl.level_type
                    });
                });
            }

            // 3. Estimated Liquidation Clusters (Capped & Visually Distinct)
            if (overlaysConfig.liquidation && includesLiveEdge && liqClusters.length > 0) {
                liqClusters.slice(0, 6).forEach((c, idx) => {
                    const price = Number(c.price_level);
                    if (!Number.isFinite(price) || price < filterMinPrice || price > filterMaxPrice) return;
                    const isLongLiq = c.side === 'LONG_LIQUIDATION';
                    const color = isLongLiq ? 'rgba(249, 115, 22, 0.80)' : 'rgba(168, 85, 247, 0.80)';
                    const distRatio = Math.abs(price - refPrice) / rawPriceSpan;
                    const priorityScore = 460 - Math.min(90, distRatio * 50) - idx * 5;

                    candidateLines.push({
                        id: `liq_${c.side}_${c.leverage_tier}_${price}`,
                        category: 'EST_LIQUIDATION',
                        price: price,
                        color: color,
                        lineWidth: 1,
                        lineStyle: 'Dotted',
                        title: `[EST LIQ] ${c.side.replace('_LIQUIDATION', '')} ${c.leverage_tier}`,
                        priority: priorityScore,
                        status: 'ESTIMATED'
                    });
                });
            }

            // Sort candidate lines by deterministic priority descending
            candidateLines.sort((a, b) => b.priority - a.priority);

            // Price-zone deduplication: merge structural levels that sit at nearly identical prices
            // Dynamic cluster tolerance: max(15 bps of price, 1.4% of visible price span)
            const dedupedLines = [];
            for (let i = 0; i < candidateLines.length; i++) {
                const cand = candidateLines[i];
                const minClusterGap = Math.max(cand.price * 0.0015, rawPriceSpan * 0.014);
                let mergedInto = null;
                for (let j = 0; j < dedupedLines.length; j++) {
                    const existing = dedupedLines[j];
                    // Never merge a LIVE_WALL with a HIST_STRUCTURAL level so they stay distinct;
                    // only merge within same category or historical structural levels in same price zone
                    const canCluster = (existing.category === cand.category) ||
                        (existing.category !== 'LIVE_WALL' && cand.category !== 'LIVE_WALL');
                    if (canCluster && Math.abs(existing.price - cand.price) <= minClusterGap) {
                        mergedInto = existing;
                        break;
                    }
                }
                if (mergedInto) {
                    mergedInto.mergedCount = (mergedInto.mergedCount || 1) + 1;
                } else {
                    dedupedLines.push(Object.assign({ mergedCount: 1 }, cand));
                }
            }

            // Cap total horizontal lines to maxTotalPriceLines
            const cappedLines = dedupedLines.slice(0, maxTotalPriceLines);

            // Right-Axis Label Allocation (Phase 4):
            // Enforce both a hard cap (maxRightAxisLabels) AND a minimum vertical price separation
            // so right-axis labels never collide or obscure candle price action.
            const minAxisLabelPriceGap = rawPriceSpan / Math.max(5, maxRightAxisLabels * 0.95);
            const labeledPrices = [];
            let renderedRightAxisLabels = 0;

            cappedLines.forEach(line => {
                if (line.mergedCount > 1 && !line.title.includes('(+')) {
                    line.title = `${line.title} (+${line.mergedCount - 1} zone)`;
                }
                const hasRoomOnAxis = renderedRightAxisLabels < maxRightAxisLabels &&
                    labeledPrices.every(lp => Math.abs(lp - line.price) >= minAxisLabelPriceGap);
                if (hasRoomOnAxis) {
                    line.axisLabelVisible = true;
                    labeledPrices.push(line.price);
                    renderedRightAxisLabels += 1;
                } else {
                    line.axisLabelVisible = false;
                }
            });

            // -------------------------------------------------------------
            // B. Viewport-Aware Sweep & Reclaim Marker Density (Phase 3)
            // -------------------------------------------------------------
            // Filter events to those occurring within the visible time window
            const visibleEvents = allEvents.filter(evt => {
                const swpSec = Math.floor((Number(evt.sweep_timestamp_ms) || 0) / 1000);
                const recSec = evt.reclaim_timestamp_ms ? Math.floor(Number(evt.reclaim_timestamp_ms) / 1000) : swpSec;
                const cnfSec = evt.confirmation_timestamp_ms ? Math.floor(Number(evt.confirmation_timestamp_ms) / 1000) : recSec;
                return (swpSec >= visStartSec && swpSec <= visEndSec) ||
                       (recSec >= visStartSec && recSec <= visEndSec) ||
                       (cnfSec >= visStartSec && cnfSec <= visEndSec);
            });

            const eventPriorityRank = {
                'SWEEP_CONFIRMED': 4,
                'SWEEP_RECLAIMED': 3,
                'SWEEP_UNCONFIRMED': 2,
                'SWEEP_INVALIDATED': 1
            };

            const visibleMarkers = [];

            if (visibleBarsCount <= 180) {
                // DETAIL VIEW: Show individual events, but consolidate same-bar markers so a single candle
                // never gets 2-3 stacked markers on the exact same bar & side.
                const perBarSideMap = new Map(); // key: `${timeSec}|${position}`

                visibleEvents.forEach(evt => {
                    const rank = eventPriorityRank[evt.event_type] || 1;
                    // In detail view > 100 bars, skip noisy SWEEP_INVALIDATED markers unless selected
                    if (visibleBarsCount > 100 && evt.event_type === 'SWEEP_INVALIDATED' && evt.event_id !== currentSelectedEventId) {
                        return;
                    }

                    const pos = evt.direction === 'SHORT' ? 'aboveBar' : 'belowBar';
                    const swpSec = Math.floor((Number(evt.sweep_timestamp_ms) || 0) / 1000);
                    const recSec = evt.reclaim_timestamp_ms ? Math.floor(Number(evt.reclaim_timestamp_ms) / 1000) : null;
                    const cnfSec = evt.confirmation_timestamp_ms ? Math.floor(Number(evt.confirmation_timestamp_ms) / 1000) : null;

                    // Candidate markers for this event
                    const evtMarkers = [];
                    if (swpSec >= visStartSec && swpSec <= visEndSec) {
                        // If reclaim happened on the exact same candle as sweep, combine into one clear label
                        const sameBarReclaim = (recSec !== null && recSec === swpSec);
                        evtMarkers.push({
                            time: swpSec,
                            position: pos,
                            color: sameBarReclaim ? '#f59e0b' : (evt.direction === 'SHORT' ? '#ef4444' : '#10b981'),
                            shape: sameBarReclaim ? 'circle' : (evt.direction === 'SHORT' ? 'arrowDown' : 'arrowUp'),
                            text: sameBarReclaim
                                ? `SWP+REC ${Math.round(Number(evt.sweep_depth_bps) || 0)}bps`
                                : `SWEEP ${Math.round(Number(evt.sweep_depth_bps) || 0)}bps`,
                            id: `marker_swp_${evt.event_id}`,
                            priority: rank * 10 + (sameBarReclaim ? 5 : 2) + (Number(evt.sweep_depth_bps) || 0) * 0.01
                        });
                    }
                    if (recSec !== null && recSec !== swpSec && recSec >= visStartSec && recSec <= visEndSec) {
                        evtMarkers.push({
                            time: recSec,
                            position: pos,
                            color: '#f59e0b',
                            shape: 'circle',
                            text: 'RECLAIM',
                            id: `marker_rec_${evt.event_id}`,
                            priority: rank * 10 + 4
                        });
                    }
                    if (cnfSec !== null && cnfSec >= visStartSec && cnfSec <= visEndSec) {
                        evtMarkers.push({
                            time: cnfSec,
                            position: pos,
                            color: '#8b5cf6',
                            shape: 'square',
                            text: 'CONFIRMED',
                            id: `marker_cnf_${evt.event_id}`,
                            priority: rank * 10 + 8
                        });
                    }

                    evtMarkers.forEach(m => {
                        const key = `${m.time}|${m.position}`;
                        const existing = perBarSideMap.get(key);
                        if (!existing || m.priority > existing.priority) {
                            const count = existing ? (existing.mergedCount + 1) : 1;
                            perBarSideMap.set(key, Object.assign({}, m, { mergedCount: count }));
                        } else {
                            existing.mergedCount += 1;
                        }
                    });
                });

                // Enforce minimum horizontal bar spacing when > 90 bars so adjacent labels do not overlap
                const rawBarMarkers = Array.from(perBarSideMap.values()).sort((a, b) => b.priority - a.priority);
                const maxMarkersInDetail = Math.min(36, Math.max(14, Math.floor(visibleBarsCount * 0.28)));
                const barIntervalSec = visibleCandlesSlice.length > 1
                    ? Math.max(60, visibleCandlesSlice[1].time - visibleCandlesSlice[0].time)
                    : 3600;
                const minTimeGapSec = visibleBarsCount > 90 ? barIntervalSec * 3 : barIntervalSec;

                const acceptedMarkers = [];
                for (let i = 0; i < rawBarMarkers.length; i++) {
                    if (acceptedMarkers.length >= maxMarkersInDetail) break;
                    const cand = rawBarMarkers[i];
                    const tooClose = acceptedMarkers.some(
                        am => am.position === cand.position && Math.abs(am.time - cand.time) < minTimeGapSec
                    );
                    if (!tooClose) {
                        acceptedMarkers.push(cand);
                    }
                }

                acceptedMarkers.forEach(m => {
                    visibleMarkers.push({
                        time: m.time,
                        position: m.position,
                        color: m.color,
                        shape: m.shape,
                        text: m.mergedCount > 1 ? `${m.text} (+${m.mergedCount - 1})` : m.text,
                        id: m.id
                    });
                });
            } else {
                // MEDIUM OVERVIEW (181-550 bars) & MACRO CLUSTERED (>550 bars):
                // Focus on confirmed/reclaimed events and aggregate nearby events into time buckets
                const numBuckets = visibleBarsCount <= 550 ? 18 : 14;
                const windowSec = Math.max(1, visEndSec - visStartSec);
                const bucketSpanSec = Math.max(60, Math.ceil(windowSec / numBuckets));
                const buckets = new Map(); // key: `${bucketIdx}|${position}`

                visibleEvents.forEach(evt => {
                    const rank = eventPriorityRank[evt.event_type] || 1;
                    // When zoomed out, prioritize SWEEP_CONFIRMED and SWEEP_RECLAIMED
                    if (rank < 3 && evt.event_id !== currentSelectedEventId) return;

                    const anchorMs = evt.confirmation_timestamp_ms || evt.reclaim_timestamp_ms || evt.sweep_timestamp_ms || 0;
                    const anchorSec = Math.floor(Number(anchorMs) / 1000);
                    if (anchorSec < visStartSec || anchorSec > visEndSec) return;

                    const pos = evt.direction === 'SHORT' ? 'aboveBar' : 'belowBar';
                    const bucketIdx = Math.floor((anchorSec - visStartSec) / bucketSpanSec);
                    const bKey = `${bucketIdx}|${pos}`;
                    const score = rank * 100 + (Number(evt.sweep_depth_bps) || 0);

                    const existing = buckets.get(bKey);
                    if (!existing) {
                        buckets.set(bKey, {
                            bestEvent: evt,
                            anchorSec: anchorSec,
                            position: pos,
                            score: score,
                            count: 1
                        });
                    } else {
                        existing.count += 1;
                        if (score > existing.score) {
                            existing.bestEvent = evt;
                            existing.anchorSec = anchorSec;
                            existing.score = score;
                        }
                    }
                });

                buckets.forEach(b => {
                    const evt = b.bestEvent;
                    const isConfirmed = evt.event_type === 'SWEEP_CONFIRMED';
                    const baseLabel = isConfirmed ? 'CONFIRMED' : 'RECLAIM';
                    const text = b.count > 1 ? `${baseLabel} (+${b.count - 1})` : baseLabel;
                    visibleMarkers.push({
                        time: b.anchorSec,
                        position: b.position,
                        color: isConfirmed ? '#8b5cf6' : '#f59e0b',
                        shape: isConfirmed ? 'square' : 'circle',
                        text: text,
                        id: `marker_cluster_${evt.event_id}`
                    });
                });
            }

            // Lightweight Charts strictly requires markers sorted chronologically by time
            visibleMarkers.sort((a, b) => a.time - b.time);

            const hm = storeData.history_metadata || sweepChartStore.historyMetadata || {};
            const dbAvailableCandles = Math.max(totalCandles, Number(hm.dbTotalCandles || hm.db_total_candles || totalCandles));
            const renderedEventsCount = visibleMarkers.length;

            return {
                visibleMarkers: visibleMarkers,
                visiblePriceLines: cappedLines,
                telemetry: {
                    // Explicitly separated 5 core state dimensions:
                    historicalDataAvailable: dbAvailableCandles,
                    candlesCurrentlyLoaded: totalCandles,
                    candlesCurrentlyVisible: visibleBarsCount,
                    historicalEventsAvailable: allEvents.length,
                    eventsCurrentlyRendered: renderedEventsCount,
                    // Backward-compatible keys:
                    totalCandles: totalCandles,
                    visibleCandles: visibleBarsCount,
                    totalLevels: allLevels.length,
                    visibleLevels: cappedLines.filter(l => l.category !== 'LIVE_WALL' && l.category !== 'EST_LIQUIDATION').length,
                    totalOrderBookWalls: obWalls.length,
                    visibleOrderBookWalls: cappedLines.filter(l => l.category === 'LIVE_WALL').length,
                    totalEvents: allEvents.length,
                    visibleEvents: visibleEvents.length,
                    totalMarkers: allMarkers.length,
                    renderedMarkers: visibleMarkers.length,
                    maxRightAxisLabels: maxRightAxisLabels,
                    renderedRightAxisLabels: renderedRightAxisLabels,
                    zoomMode: zoomMode,
                    visiblePriceRange: [visLow, visHigh],
                    visibleTimeRangeSec: [visStartSec, visEndSec]
                }
            };
        }

        function updateDensityTelemetryUI(telemetry) {
            if (!telemetry) return;
            sweepChartStore.lastViewportMetrics = telemetry;

            const candlesEl = document.getElementById('densityCandlesStat');
            const levelsEl = document.getElementById('densityLevelsStat');
            const eventsEl = document.getElementById('densityEventsStat');
            const markersEl = document.getElementById('densityMarkersStat');
            const axisEl = document.getElementById('densityAxisLabelsStat');
            const modeBadge = document.getElementById('chartZoomModeBadge');
            const boundaryBadge = document.getElementById('chartHistoryBoundaryBadge');

            if (candlesEl) {
                candlesEl.textContent = `${telemetry.candlesCurrentlyVisible}/${telemetry.candlesCurrentlyLoaded}/${telemetry.historicalDataAvailable}`;
            }
            if (levelsEl) {
                const liveWallsCount = telemetry.visibleOrderBookWalls || 0;
                levelsEl.textContent = `${telemetry.visibleLevels}/${telemetry.totalLevels}${liveWallsCount > 0 ? ` (+${liveWallsCount} Live)` : ''}`;
            }
            if (eventsEl) {
                eventsEl.textContent = `${telemetry.eventsCurrentlyRendered}/${telemetry.historicalEventsAvailable}`;
            }
            if (markersEl) {
                markersEl.textContent = `${telemetry.renderedMarkers}/${telemetry.totalMarkers}`;
            }
            if (axisEl) {
                axisEl.textContent = `${telemetry.renderedRightAxisLabels}/${telemetry.maxRightAxisLabels} max`;
            }
            if (modeBadge) {
                modeBadge.textContent = telemetry.zoomMode;
                if (telemetry.zoomMode === 'DETAIL VIEW') {
                    modeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-950/90 text-indigo-300 border border-indigo-800/70';
                } else if (telemetry.zoomMode === 'MEDIUM OVERVIEW') {
                    modeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950/90 text-amber-300 border border-amber-800/70';
                } else {
                    modeBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-purple-950/90 text-purple-300 border border-purple-800/70';
                }
            }
            if (boundaryBadge) {
                const hm = sweepChartStore.historyMetadata || {};
                const oldestMs = hm.oldestLoadedTimestampMs;
                const oldestDateStr = oldestMs ? new Date(oldestMs).toISOString().slice(0, 10) : '--';
                if (hm.hasMoreBefore) {
                    boundaryBadge.textContent = `Loaded from ${oldestDateStr} (Scroll left for older)`;
                    boundaryBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-950/80 text-indigo-300 border border-indigo-800/70';
                } else {
                    boundaryBadge.textContent = `Boundary: ${oldestDateStr} (Complete)`;
                    boundaryBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/70';
                }
            }
        }

        // -----------------------------------------------------------------
        // 4. Visual Rendering (Lightweight Charts Series & Overlays)
        // -----------------------------------------------------------------
        function refreshViewportOverlays() {
            if (!tvChart || !candleSeries || !chartLayersData) return;

            let logicalRange = null;
            try {
                logicalRange = tvChart.timeScale().getVisibleLogicalRange();
            } catch (e) {}

            if (logicalRange) {
                sweepChartStore.lastVisibleRange = logicalRange;
            }

            const container = document.getElementById('tvLightweightChartContainer');
            const chartHeightPx = isChartFullscreen
                ? computeAvailableChartCanvasSize().height
                : (container?.clientHeight || 520);

            const filtered = computeViewportFilteredOverlays(
                chartLayersData,
                logicalRange,
                chartHeightPx,
                activeOverlays
            );

            // Apply filtered markers to candlestick series
            try {
                candleSeries.setMarkers(filtered.visibleMarkers);
            } catch (e) {
                console.warn('Error setting viewport markers:', e);
            }

            // Replace active price lines cleanly without touching candleSeries data
            activePriceLines.forEach(l => {
                try { candleSeries.removePriceLine(l); } catch (e) {}
            });
            activePriceLines = [];

            const lineStyleMap = (typeof LightweightCharts !== 'undefined' && LightweightCharts.LineStyle)
                ? {
                    Solid: LightweightCharts.LineStyle.Solid,
                    Dashed: LightweightCharts.LineStyle.Dashed,
                    Dotted: LightweightCharts.LineStyle.Dotted
                }
                : { Solid: 0, Dashed: 2, Dotted: 3 };

            filtered.visiblePriceLines.forEach(line => {
                try {
                    const pl = candleSeries.createPriceLine({
                        price: line.price,
                        color: line.color,
                        lineWidth: line.lineWidth,
                        lineStyle: lineStyleMap[line.lineStyle] ?? lineStyleMap.Dashed,
                        axisLabelVisible: Boolean(line.axisLabelVisible),
                        title: line.axisLabelVisible ? line.title : '',
                    });
                    activePriceLines.push(pl);
                } catch (e) {}
            });

            updateDensityTelemetryUI(filtered.telemetry);
            renderInteractiveChartDrawings();
        }

        function scheduleViewportOverlayRefresh() {
            if (viewportDebounceTimer) {
                clearTimeout(viewportDebounceTimer);
            }
            viewportDebounceTimer = setTimeout(() => {
                viewportDebounceTimer = null;
                refreshViewportOverlays();
            }, 45);
        }

        function renderLightweightChart(data, preserveViewport = false) {
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

            const targetDims = computeAvailableChartCanvasSize();
            const targetHeight = targetDims.height;
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
                        secondsVisible: false,
                        rightOffset: 4
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

                // Subscribe to pan/zoom logical range changes for viewport-aware overlay updates, drawings, and cursor history loading
                if (tvChart.timeScale && typeof tvChart.timeScale().subscribeVisibleLogicalRangeChange === 'function') {
                    tvChart.timeScale().subscribeVisibleLogicalRangeChange(logicalRange => {
                        renderInteractiveChartDrawings();
                        checkAndTriggerIncrementalHistoryLoad(logicalRange);
                        scheduleViewportOverlayRefresh();
                    });
                }
                initChartDrawingCanvasEvents();

                if (window.ResizeObserver) {
                    let lastWidth = width;
                    let lastHeight = targetHeight;
                    const ro = new ResizeObserver(entries => {
                        if (entries && entries[0] && tvChart) {
                            const rect = entries[0].contentRect;
                            const nextW = Math.round(rect.width);
                            const nextH = isChartFullscreen
                                ? computeAvailableChartCanvasSize().height
                                : 520;
                            if (nextW > 0 && (Math.abs(nextW - lastWidth) > 2 || Math.abs(nextH - lastHeight) > 2)) {
                                lastWidth = nextW;
                                lastHeight = nextH;
                                tvChart.applyOptions({
                                    width: nextW,
                                    height: nextH
                                });
                                scheduleViewportOverlayRefresh();
                                renderInteractiveChartDrawings();
                            }
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

            // Capture current viewport before updating data if preserveViewport is true
            let savedLogicalRange = null;
            if (preserveViewport && tvChart) {
                try {
                    savedLogicalRange = tvChart.timeScale().getVisibleLogicalRange();
                } catch (e) {}
            }

            const currentSeriesKey = `${currentChartSymbol}|${currentChartTf}|${isReplayMode ? replayCurrentIndex : 'live'}`;
            const isIncrementalPoll = preserveViewport && (lastChartRenderKey === currentSeriesKey) && chartData.length > 0;

            if (isIncrementalPoll && typeof candleSeries.update === 'function') {
                // Fast incremental update on 10s poll without resetting full 2000-bar series
                const lastCandle = chartData[chartData.length - 1];
                const lastVol = volumeData[volumeData.length - 1];
                try {
                    candleSeries.update(lastCandle);
                    volumeSeries.update(lastVol);
                } catch (e) {
                    candleSeries.setData(chartData);
                    volumeSeries.setData(volumeData);
                }
            } else {
                candleSeries.setData(chartData);
                volumeSeries.setData(volumeData);
                lastChartRenderKey = currentSeriesKey;
            }

            if (preserveViewport && savedLogicalRange) {
                try {
                    tvChart.timeScale().setVisibleLogicalRange(savedLogicalRange);
                } catch (e) {}
            } else if (!preserveViewport && !isReplayMode) {
                // Set a clean, readable initial viewport (e.g. latest 120 bars) instead of crushing 2000 bars
                applyViewportPresetRange(currentViewportPreset || 'default');
            }

            refreshViewportOverlays();
        }

        function renderChartOverlays(data) {
            if (data) {
                chartLayersData = data;
            }
            refreshViewportOverlays();
        }

        function toggleOverlay(overlayType) {
            if (overlayType === 'structural') {
                activeOverlays.structural = document.getElementById('overlayStructuralCheck')?.checked ?? true;
            } else if (overlayType === '1d_macro') {
                activeOverlays.macro_1d = document.getElementById('overlay1DMacroCheck')?.checked ?? false;
                loadLiquidityChartData(null, true);
                return;
            } else if (overlayType === 'orderbook') {
                activeOverlays.orderbook = document.getElementById('overlayOrderBookCheck')?.checked ?? true;
            } else if (overlayType === 'liquidation') {
                activeOverlays.liquidation = document.getElementById('overlayLiquidationCheck')?.checked ?? true;
            }
            refreshViewportOverlays();
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

            const pEl = document.getElementById('chartEventsPaginationContainer');

            if (filtered.length === 0) {
                container.innerHTML = `<div class="py-8 text-center text-xs text-slate-500 font-mono">No events matching filter '${currentChartEventFilter}'.</div>`;
                if (pEl) pEl.innerHTML = '';
                return;
            }

            const paged = (typeof paginateList === 'function')
                ? paginateList('chart_events', filtered)
                : filtered;

            container.innerHTML = paged.map(evt => {
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

            if (typeof renderPaginationControls === 'function') {
                renderPaginationControls('chart_events', 'chartEventsPaginationContainer', () => {
                    renderEventsList(chartEventsData);
                });
            }
        }

        function filterChartEvents(event) {
            currentChartEventFilter = event.target.value || 'ALL';
            if (typeof resetPaginationPage === "function") resetPaginationPage("chart_events");
            renderEventsList(chartEventsData);
        }

        function selectChartEvent(eventId) {
            currentSelectedEventId = eventId;
            renderEventsList(chartEventsData);

            const evt = chartEventsData.find(e => e.event_id === eventId);
            if (!evt) return;

            if (tvChart && sweepChartStore.candles.length > 0 && evt.sweep_index !== undefined) {
                try {
                    const total = sweepChartStore.candles.length;
                    const idx = Math.max(0, Math.min(total - 1, Number(evt.sweep_index)));
                    const halfSpan = 40;
                    tvChart.timeScale().setVisibleLogicalRange({
                        from: Math.max(0, idx - halfSpan),
                        to: Math.min(total + 4, idx + halfSpan)
                    });
                    refreshViewportOverlays();
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
            if (isReplayMode) {
                stopChartAutoRefresh();
            } else {
                startChartAutoRefresh();
            }
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
                    loadLiquidityChartData(replayCurrentIndex, true);
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
                loadLiquidityChartData(null, true);
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
            loadLiquidityChartData(idx, true);
        }

        function stepReplayBackward() {
            if (!isReplayMode || replayCurrentIndex === null) return;
            if (replayCurrentIndex > 0) {
                replayCurrentIndex -= 1;
                const slider = document.getElementById('replayBarSlider');
                if (slider) slider.value = replayCurrentIndex;
                loadLiquidityChartData(replayCurrentIndex, true);
            }
        }

        function stepReplayForward() {
            if (!isReplayMode || replayCurrentIndex === null || !chartLayersData) return;
            const total = chartLayersData.replay_metadata?.total_bars || 100;
            if (replayCurrentIndex < total - 1) {
                replayCurrentIndex += 1;
                const slider = document.getElementById('replayBarSlider');
                if (slider) slider.value = replayCurrentIndex;
                loadLiquidityChartData(replayCurrentIndex, true);
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
            loadLiquidityChartData(replayCurrentIndex, true);
        }





        // =========================================================================

        // 5. TradingView-Style Interactive Drawing Engine (SQLite-Persisted)

        // =========================================================================

        const DRAWING_TOOL_CONFIG = {

            crosshair: { pointsRequired: 0, label: 'Crosshair' },

            trend_line: { pointsRequired: 2, label: 'Trend Line', color: '#38bdf8' },

            ray: { pointsRequired: 2, label: 'Ray', color: '#fbbf24' },

            horizontal_line: { pointsRequired: 1, label: 'Horizontal Line', color: '#a855f7' },

            vertical_line: { pointsRequired: 1, label: 'Vertical Line', color: '#60a5fa' },

            rectangle: { pointsRequired: 2, label: 'Rectangle Zone', color: '#6366f1' },

            fib_retracement: { pointsRequired: 2, label: 'Fib Retracement', color: '#f59e0b' },

            long_position: { pointsRequired: 2, label: 'Long Position R:R', color: '#10b981' },

            short_position: { pointsRequired: 2, label: 'Short Position R:R', color: '#f43f5e' }

        };



        function generateDrawingId(toolType) {

            return `drw_${toolType}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

        }



        function cloneDrawingsList(list) {

            return JSON.parse(JSON.stringify(list || []));

        }



        function pushDrawingUndoState() {

            chartDrawingsState.undoStack.push(cloneDrawingsList(chartDrawingsState.drawings));

            if (chartDrawingsState.undoStack.length > 30) {

                chartDrawingsState.undoStack.shift();

            }

            chartDrawingsState.redoStack = [];

        }



        function updateDrawingToolbarButtons() {

            const tools = Object.keys(DRAWING_TOOL_CONFIG);

            tools.forEach(t => {

                const btn = document.getElementById(`drawToolBtn-${t}`);

                if (!btn) return;

                if (t === chartDrawingsState.activeTool) {

                    btn.className = 'px-2 py-1 rounded bg-indigo-600 text-white font-bold transition shadow-xs';

                } else if (t === 'long_position') {

                    btn.className = 'px-2 py-1 rounded text-emerald-300 hover:bg-slate-800 transition';

                } else if (t === 'short_position') {

                    btn.className = 'px-2 py-1 rounded text-rose-300 hover:bg-slate-800 transition';

                } else {

                    btn.className = 'px-2 py-1 rounded text-slate-300 hover:bg-slate-800 hover:text-white transition';

                }

            });



            const visBtn = document.getElementById('drawVisibilityToggleBtn');

            const countBadge = document.getElementById('drawingsCountBadge');

            if (countBadge) countBadge.textContent = String(chartDrawingsState.drawings.length);

            if (visBtn) {

                const count = chartDrawingsState.drawings.length;

                if (chartDrawingsState.isVisible) {

                    visBtn.innerHTML = `Drawings: ON (<span id="drawingsCountBadge">${count}</span>)`;

                    visBtn.className = 'px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-emerald-300 border border-slate-700 transition';

                } else {

                    visBtn.innerHTML = `Drawings: OFF (<span id="drawingsCountBadge">${count}</span>)`;

                    visBtn.className = 'px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-400 border border-slate-700 transition';

                }

            }



            const canvas = document.getElementById('chartDrawingCanvasOverlay');

            if (canvas) {

                // Enable pointer events on overlay when placing a drawing or dragging an existing handle

                if (chartDrawingsState.activeTool !== 'crosshair') {

                    canvas.classList.remove('pointer-events-none');

                    canvas.style.cursor = 'crosshair';

                } else {

                    canvas.classList.add('pointer-events-none');

                    canvas.style.cursor = 'default';

                }

            }

        }



        function setActiveDrawingTool(toolName) {

            const tool = DRAWING_TOOL_CONFIG[toolName] ? toolName : 'crosshair';

            chartDrawingsState.activeTool = tool;

            chartDrawingsState.inProgressDrawing = null;

            updateDrawingToolbarButtons();

            renderInteractiveChartDrawings();

        }



        function toggleDrawingsVisibility() {

            chartDrawingsState.isVisible = !chartDrawingsState.isVisible;

            updateDrawingToolbarButtons();

            renderInteractiveChartDrawings();

        }



        async function loadPersistedChartDrawings(symbol, timeframe) {

            const sym = (symbol || currentChartSymbol || 'BTCUSDT').trim().toUpperCase();

            const tf = (timeframe || currentChartTf || '1h').trim().toLowerCase();

            try {

                const url = `/api/chart/drawings?symbol=${encodeURIComponent(sym)}&timeframe=${encodeURIComponent(tf)}&user_profile=${encodeURIComponent(chartDrawingsState.userProfile)}`;

                const res = await fetch(url);

                if (!res.ok) return;

                const payload = await res.json();

                chartDrawingsState.drawings = Array.isArray(payload.drawings) ? payload.drawings : [];

                chartDrawingsState.selectedDrawingId = null;

                chartDrawingsState.inProgressDrawing = null;

                chartDrawingsState.undoStack = [];

                chartDrawingsState.redoStack = [];

                updateDrawingToolbarButtons();

                renderInteractiveChartDrawings();

            } catch (e) {

                console.warn('Could not load persisted chart drawings:', e);

            }

        }



        async function persistDrawingToBackend(drawing) {

            if (!drawing || !drawing.drawing_id) return;

            try {

                await fetch('/api/chart/drawings', {

                    method: 'POST',

                    headers: { 'Content-Type': 'application/json' },

                    body: JSON.stringify({

                        drawing_id: drawing.drawing_id,

                        user_profile: chartDrawingsState.userProfile,

                        symbol: drawing.symbol || currentChartSymbol,

                        timeframe: drawing.timeframe || currentChartTf,

                        tool_type: drawing.tool_type,

                        points: drawing.points || [],

                        style: drawing.style || {},

                        is_visible: drawing.is_visible !== false

                    })

                });

            } catch (e) {

                console.warn('Could not persist drawing to SQLite:', e);

            }

        }



        async function deleteDrawingFromBackend(drawingId) {

            if (!drawingId) return;

            try {

                await fetch(`/api/chart/drawings/${encodeURIComponent(drawingId)}?user_profile=${encodeURIComponent(chartDrawingsState.userProfile)}`, {

                    method: 'DELETE'

                });

            } catch (e) {

                console.warn('Could not delete drawing from SQLite:', e);

            }

        }



        async function removeSelectedChartDrawing() {

            const selId = chartDrawingsState.selectedDrawingId;

            if (!selId) return;

            pushDrawingUndoState();

            chartDrawingsState.drawings = chartDrawingsState.drawings.filter(d => d.drawing_id !== selId);

            chartDrawingsState.selectedDrawingId = null;

            updateDrawingToolbarButtons();

            renderInteractiveChartDrawings();

            await deleteDrawingFromBackend(selId);

        }



        async function clearAllChartDrawings() {

            if (chartDrawingsState.drawings.length === 0) return;

            pushDrawingUndoState();

            chartDrawingsState.drawings = [];

            chartDrawingsState.selectedDrawingId = null;

            chartDrawingsState.inProgressDrawing = null;

            updateDrawingToolbarButtons();

            renderInteractiveChartDrawings();

            try {

                await fetch(`/api/chart/drawings?symbol=${encodeURIComponent(currentChartSymbol)}&timeframe=${encodeURIComponent(currentChartTf)}&user_profile=${encodeURIComponent(chartDrawingsState.userProfile)}`, {

                    method: 'DELETE'

                });

            } catch (e) {

                console.warn('Could not clear drawings in SQLite:', e);

            }

        }



        async function syncDrawingsSnapshotToBackend(snapshot) {

            try {

                await fetch(`/api/chart/drawings?symbol=${encodeURIComponent(currentChartSymbol)}&timeframe=${encodeURIComponent(currentChartTf)}&user_profile=${encodeURIComponent(chartDrawingsState.userProfile)}`, {

                    method: 'DELETE'

                });

                for (let i = 0; i < snapshot.length; i++) {

                    await persistDrawingToBackend(snapshot[i]);

                }

            } catch (e) {}

        }



        async function undoChartDrawing() {

            if (chartDrawingsState.undoStack.length === 0) return;

            chartDrawingsState.redoStack.push(cloneDrawingsList(chartDrawingsState.drawings));

            const prev = chartDrawingsState.undoStack.pop();

            chartDrawingsState.drawings = prev;

            chartDrawingsState.selectedDrawingId = null;

            updateDrawingToolbarButtons();

            renderInteractiveChartDrawings();

            await syncDrawingsSnapshotToBackend(prev);

        }



        async function redoChartDrawing() {

            if (chartDrawingsState.redoStack.length === 0) return;

            chartDrawingsState.undoStack.push(cloneDrawingsList(chartDrawingsState.drawings));

            const next = chartDrawingsState.redoStack.pop();

            chartDrawingsState.drawings = next;

            chartDrawingsState.selectedDrawingId = null;

            updateDrawingToolbarButtons();

            renderInteractiveChartDrawings();

            await syncDrawingsSnapshotToBackend(next);

        }



        function coordinateToChartPoint(xPx, yPx) {

            if (!tvChart || !candleSeries) return null;

            const candles = sweepChartStore.candles || [];

            if (candles.length === 0) return null;



            let price = null;

            try {

                price = candleSeries.coordinateToPrice(yPx);

            } catch (e) {}

            if (price === null || !Number.isFinite(price)) return null;



            let timeSec = null;

            try {

                const t = tvChart.timeScale().coordinateToTime(xPx);

                if (typeof t === 'number') timeSec = t;

            } catch (e) {}



            if (timeSec === null) {

                try {

                    const logical = tvChart.timeScale().coordinateToLogical(xPx);

                    if (typeof logical === 'number' && Number.isFinite(logical)) {

                        const idx = Math.max(0, Math.min(candles.length - 1, Math.round(logical)));

                        timeSec = candles[idx]?.time ?? candles[candles.length - 1].time;

                    }

                } catch (e) {}

            }

            if (timeSec === null) {

                timeSec = candles[candles.length - 1].time;

            }

            return {

                time: Math.round(timeSec),

                price: Number(Number(price).toFixed(6))

            };

        }



        function chartPointToCoordinate(pt) {

            if (!pt || !tvChart || !candleSeries) return null;

            let y = null;

            try {

                y = candleSeries.priceToCoordinate(Number(pt.price));

            } catch (e) {}

            if (y === null || !Number.isFinite(y)) return null;



            let x = null;

            try {

                x = tvChart.timeScale().timeToCoordinate(Number(pt.time));

            } catch (e) {}



            if ((x === null || !Number.isFinite(x)) && sweepChartStore.candles.length > 0) {

                // Binary search nearest candle index and map logical index to coordinate

                const candles = sweepChartStore.candles;

                const targetTime = Number(pt.time);

                let lo = 0;

                let hi = candles.length - 1;

                while (lo < hi) {

                    const mid = Math.floor((lo + hi) / 2);

                    if (candles[mid].time < targetTime) lo = mid + 1;

                    else hi = mid;

                }

                try {

                    x = tvChart.timeScale().logicalToCoordinate(lo);

                } catch (e) {}

            }



            if (x === null || !Number.isFinite(x)) return null;

            return { x: Number(x), y: Number(y) };

        }



        function hitTestChartDrawings(xPx, yPx) {

            if (!chartDrawingsState.isVisible) return null;

            const list = chartDrawingsState.drawings || [];

            const handleRadius = 8;



            for (let i = list.length - 1; i >= 0; i--) {

                const d = list[i];

                if (d.is_visible === false) continue;

                const pts = (d.points || []).map(p => chartPointToCoordinate(p));



                // 1. Check control point handles first (for dragging/editing)

                for (let pIdx = 0; pIdx < pts.length; pIdx++) {

                    const c = pts[pIdx];

                    if (c && Math.hypot(c.x - xPx, c.y - yPx) <= handleRadius) {

                        return { drawing: d, pointIndex: pIdx };

                    }

                }



                // 2. Check line/shape body

                if (d.tool_type === 'horizontal_line' && pts[0]) {

                    if (Math.abs(pts[0].y - yPx) <= 6) return { drawing: d, pointIndex: 0 };

                } else if (d.tool_type === 'vertical_line' && pts[0]) {

                    if (Math.abs(pts[0].x - xPx) <= 6) return { drawing: d, pointIndex: 0 };

                } else if (pts[0] && pts[1]) {

                    const x1 = pts[0].x, y1 = pts[0].y;

                    const x2 = pts[1].x, y2 = pts[1].y;

                    if (d.tool_type === 'rectangle' || d.tool_type === 'fib_retracement' || d.tool_type === 'long_position' || d.tool_type === 'short_position') {

                        const minX = Math.min(x1, x2) - 4, maxX = Math.max(x1, x2) + 4;

                        const minY = Math.min(y1, y2) - 4, maxY = Math.max(y1, y2) + 4;

                        if (xPx >= minX && xPx <= maxX && yPx >= minY && yPx <= maxY) {

                            return { drawing: d, pointIndex: -1 };

                        }

                    } else {

                        // Distance from point to segment (trend_line / ray)

                        const dx = x2 - x1, dy = y2 - y1;

                        const lenSq = dx * dx + dy * dy;

                        let t = lenSq > 0 ? ((xPx - x1) * dx + (yPx - y1) * dy) / lenSq : 0;

                        if (d.tool_type === 'trend_line') t = Math.max(0, Math.min(1, t));

                        else t = Math.max(0, t);

                        const projX = x1 + t * dx, projY = y1 + t * dy;

                        if (Math.hypot(projX - xPx, projY - yPx) <= 6) {

                            return { drawing: d, pointIndex: -1 };

                        }

                    }

                }

            }

            return null;

        }



        function initChartDrawingCanvasEvents() {

            if (chartDrawingsState.listenersBound) return;

            const stack = document.getElementById('chartCanvasStack');

            if (!stack) return;

            chartDrawingsState.listenersBound = true;



            stack.addEventListener('mousedown', async (e) => {

                if (e.button !== 0) return;

                const rect = stack.getBoundingClientRect();

                const x = e.clientX - rect.left;

                const y = e.clientY - rect.top;



                const tool = chartDrawingsState.activeTool;

                if (tool === 'crosshair') {

                    const hit = hitTestChartDrawings(x, y);

                    if (hit) {

                        chartDrawingsState.selectedDrawingId = hit.drawing.drawing_id;

                        if (hit.pointIndex >= 0) {

                            pushDrawingUndoState();

                            chartDrawingsState.dragState = {

                                drawingId: hit.drawing.drawing_id,

                                pointIndex: hit.pointIndex

                            };

                            e.stopPropagation();

                            e.preventDefault();

                        }

                        renderInteractiveChartDrawings();

                    } else if (chartDrawingsState.selectedDrawingId) {

                        chartDrawingsState.selectedDrawingId = null;

                        renderInteractiveChartDrawings();

                    }

                    return;

                }



                // Active drawing tool placement

                const pt = coordinateToChartPoint(x, y);

                if (!pt) return;

                const cfg = DRAWING_TOOL_CONFIG[tool];

                if (!cfg) return;



                if (cfg.pointsRequired === 1) {

                    pushDrawingUndoState();

                    const newDrawing = {

                        drawing_id: generateDrawingId(tool),

                        user_profile: chartDrawingsState.userProfile,

                        symbol: currentChartSymbol,

                        timeframe: currentChartTf,

                        tool_type: tool,

                        points: [pt],

                        style: { color: cfg.color || '#38bdf8', lineWidth: 2 },

                        is_visible: true

                    };

                    chartDrawingsState.drawings.push(newDrawing);

                    chartDrawingsState.selectedDrawingId = newDrawing.drawing_id;

                    setActiveDrawingTool('crosshair');

                    await persistDrawingToBackend(newDrawing);

                } else if (cfg.pointsRequired === 2) {

                    if (!chartDrawingsState.inProgressDrawing) {

                        chartDrawingsState.inProgressDrawing = {

                            drawing_id: generateDrawingId(tool),

                            user_profile: chartDrawingsState.userProfile,

                            symbol: currentChartSymbol,

                            timeframe: currentChartTf,

                            tool_type: tool,

                            points: [pt, Object.assign({}, pt)],

                            style: { color: cfg.color || '#38bdf8', lineWidth: 2 },

                            is_visible: true

                        };

                        renderInteractiveChartDrawings();

                    } else {

                        pushDrawingUndoState();

                        chartDrawingsState.inProgressDrawing.points[1] = pt;

                        const finished = chartDrawingsState.inProgressDrawing;

                        chartDrawingsState.inProgressDrawing = null;

                        chartDrawingsState.drawings.push(finished);

                        chartDrawingsState.selectedDrawingId = finished.drawing_id;

                        setActiveDrawingTool('crosshair');

                        await persistDrawingToBackend(finished);

                    }

                }

            }, true);



            stack.addEventListener('mousemove', (e) => {

                const rect = stack.getBoundingClientRect();

                const x = e.clientX - rect.left;

                const y = e.clientY - rect.top;



                if (chartDrawingsState.inProgressDrawing) {

                    const pt = coordinateToChartPoint(x, y);

                    if (pt) {

                        chartDrawingsState.inProgressDrawing.points[1] = pt;

                        renderInteractiveChartDrawings();

                    }

                    return;

                }



                if (chartDrawingsState.dragState) {

                    const pt = coordinateToChartPoint(x, y);

                    if (pt) {

                        const target = chartDrawingsState.drawings.find(d => d.drawing_id === chartDrawingsState.dragState.drawingId);

                        if (target && target.points[chartDrawingsState.dragState.pointIndex]) {

                            target.points[chartDrawingsState.dragState.pointIndex] = pt;

                            renderInteractiveChartDrawings();

                        }

                    }

                }

            });



            window.addEventListener('mouseup', async () => {

                if (chartDrawingsState.dragState) {

                    const draggedId = chartDrawingsState.dragState.drawingId;

                    chartDrawingsState.dragState = null;

                    const target = chartDrawingsState.drawings.find(d => d.drawing_id === draggedId);

                    if (target) {

                        await persistDrawingToBackend(target);

                    }

                }

            });



            document.addEventListener('keydown', (e) => {

                if (e.key === 'Delete' || e.key === 'Backspace') {

                    const activeTag = document.activeElement?.tagName?.toLowerCase();

                    if (activeTag !== 'input' && activeTag !== 'textarea' && chartDrawingsState.selectedDrawingId) {

                        removeSelectedChartDrawing();

                    }

                } else if (e.key === 'Escape' && chartDrawingsState.activeTool !== 'crosshair') {

                    setActiveDrawingTool('crosshair');

                }

            });

        }



        function renderSingleDrawingOnCanvas(ctx, d, width, height, isSelected) {

            if (!d || d.is_visible === false) return;

            const pts = (d.points || []).map(p => chartPointToCoordinate(p));

            const color = d.style?.color || DRAWING_TOOL_CONFIG[d.tool_type]?.color || '#38bdf8';



            ctx.save();

            ctx.strokeStyle = color;

            ctx.fillStyle = color;

            ctx.lineWidth = isSelected ? 2.5 : (d.style?.lineWidth || 2);

            ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';



            if (d.tool_type === 'horizontal_line' && pts[0]) {

                const y = pts[0].y;

                ctx.beginPath();

                ctx.moveTo(0, y);

                ctx.lineTo(width, y);

                ctx.stroke();

                ctx.fillText(`$${formatPrice(d.points[0].price)}`, 10, Math.max(12, y - 5));

            } else if (d.tool_type === 'vertical_line' && pts[0]) {

                const x = pts[0].x;

                ctx.beginPath();

                ctx.moveTo(x, 0);

                ctx.lineTo(x, height);

                ctx.stroke();

                const dt = new Date(d.points[0].time * 1000).toISOString().slice(0, 10);

                ctx.fillText(dt, x + 5, 16);

            } else if (pts[0] && pts[1]) {

                const x1 = pts[0].x, y1 = pts[0].y;

                const x2 = pts[1].x, y2 = pts[1].y;



                if (d.tool_type === 'trend_line') {

                    ctx.beginPath();

                    ctx.moveTo(x1, y1);

                    ctx.lineTo(x2, y2);

                    ctx.stroke();

                } else if (d.tool_type === 'ray') {

                    const dx = x2 - x1, dy = y2 - y1;

                    const len = Math.hypot(dx, dy) || 1;

                    const extX = x1 + (dx / len) * (width * 2);

                    const extY = y1 + (dy / len) * (height * 2);

                    ctx.beginPath();

                    ctx.moveTo(x1, y1);

                    ctx.lineTo(extX, extY);

                    ctx.stroke();

                } else if (d.tool_type === 'rectangle') {

                    const rx = Math.min(x1, x2), ry = Math.min(y1, y2);

                    const rw = Math.abs(x2 - x1), rh = Math.abs(y2 - y1);

                    ctx.fillStyle = 'rgba(99, 102, 241, 0.16)';

                    ctx.fillRect(rx, ry, rw, rh);

                    ctx.strokeRect(rx, ry, rw, rh);

                } else if (d.tool_type === 'fib_retracement') {

                    const levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0];

                    const p1 = Number(d.points[0].price);

                    const p2 = Number(d.points[1].price);

                    const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);

                    levels.forEach(lvl => {

                        const lvlPrice = p1 + (p2 - p1) * lvl;

                        let lvlY = null;

                        try { lvlY = candleSeries.priceToCoordinate(lvlPrice); } catch (e) {}

                        if (lvlY !== null && Number.isFinite(lvlY)) {

                            ctx.beginPath();

                            ctx.setLineDash(lvl === 0.5 || lvl === 0.618 ? [] : [4, 4]);

                            ctx.moveTo(minX, lvlY);

                            ctx.lineTo(maxX, lvlY);

                            ctx.stroke();

                            ctx.setLineDash([]);

                            ctx.fillText(`${lvl} ($${formatPrice(lvlPrice)})`, minX + 4, lvlY - 3);

                        }

                    });

                } else if (d.tool_type === 'long_position' || d.tool_type === 'short_position') {

                    const isLong = d.tool_type === 'long_position';

                    const entryPx = Number(d.points[0].price);

                    const targetPx = Number(d.points[1].price);

                    const rewardDist = Math.abs(targetPx - entryPx);

                    const stopDist = Math.max(rewardDist * 0.5, entryPx * 0.005);

                    const stopPx = isLong ? (entryPx - stopDist) : (entryPx + stopDist);

                    const rr = stopDist > 0 ? (rewardDist / stopDist).toFixed(2) : '2.00';



                    let stopY = null;

                    try { stopY = candleSeries.priceToCoordinate(stopPx); } catch (e) {}

                    const rx = Math.min(x1, x2), rw = Math.max(40, Math.abs(x2 - x1));



                    // Target profit box

                    ctx.fillStyle = 'rgba(16, 185, 129, 0.18)';

                    ctx.fillRect(rx, Math.min(y1, y2), rw, Math.abs(y2 - y1));

                    ctx.strokeStyle = '#10b981';

                    ctx.strokeRect(rx, Math.min(y1, y2), rw, Math.abs(y2 - y1));



                    // Stop loss box

                    if (stopY !== null && Number.isFinite(stopY)) {

                        ctx.fillStyle = 'rgba(244, 63, 94, 0.18)';

                        ctx.fillRect(rx, Math.min(y1, stopY), rw, Math.abs(stopY - y1));

                        ctx.strokeStyle = '#f43f5e';

                        ctx.strokeRect(rx, Math.min(y1, stopY), rw, Math.abs(stopY - y1));

                    }



                    ctx.fillStyle = '#f8fafc';

                    ctx.fillText(`${isLong ? 'LONG' : 'SHORT'} R:R ${rr}R | Entry $${formatPrice(entryPx)} | TP $${formatPrice(targetPx)}`, rx + 4, y1 - 4);

                }

            }



            // Draw editable control handles when selected or in-progress

            if (isSelected) {

                pts.forEach(c => {

                    if (!c) return;

                    ctx.beginPath();

                    ctx.arc(c.x, c.y, 4.5, 0, Math.PI * 2);

                    ctx.fillStyle = '#0f172a';

                    ctx.fill();

                    ctx.lineWidth = 2;

                    ctx.strokeStyle = '#f8fafc';

                    ctx.stroke();

                });

            }

            ctx.restore();

        }



        function renderInteractiveChartDrawings() {

            const canvas = document.getElementById('chartDrawingCanvasOverlay');

            const stack = document.getElementById('chartCanvasStack') || document.getElementById('tvLightweightChartContainer');

            if (!canvas || !stack) return;



            const width = stack.clientWidth || 800;

            const height = stack.clientHeight || 520;

            if (canvas.width !== width || canvas.height !== height) {

                canvas.width = width;

                canvas.height = height;

            }



            const ctx = canvas.getContext('2d');

            if (!ctx) return;

            ctx.clearRect(0, 0, width, height);



            if (!chartDrawingsState.isVisible || !tvChart || !candleSeries) return;



            const list = chartDrawingsState.drawings || [];

            list.forEach(d => {

                const isSel = d.drawing_id === chartDrawingsState.selectedDrawingId;

                renderSingleDrawingOnCanvas(ctx, d, width, height, isSel);

            });



            if (chartDrawingsState.inProgressDrawing) {

                renderSingleDrawingOnCanvas(ctx, chartDrawingsState.inProgressDrawing, width, height, true);

            }

        }



