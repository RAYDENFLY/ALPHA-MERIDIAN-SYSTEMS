        // MODEL B LIQUIDITY RESEARCH ENGINE JAVASCRIPT
        // =========================================================================

        let currentModelBInstance = 'MODEL_B'; // 'MODEL_B' (Normal 1H/4H) or 'MODEL_B_FAST' (Fast Scalper 15m/1H)
        let modelBTelemetryTimer = null;
        let modelBHeavyDataTimer = null;
        let modelBIsFetchingStatus = false;
        let modelBStatusAbortController = null;
        let modelBLastSuccessfulStatusMs = 0;
        const MODEL_B_STATUS_POLL_INTERVAL_MS = 3000; // 3 to 5 seconds real-time polling
        const MODEL_B_HEAVY_POLL_INTERVAL_MS = 15000; // 15 seconds for heavy tables

        function updateModelBBotModeUI(modelId, parallelInstances = null) {
            const isFast = modelId === 'MODEL_B_FAST';
            const btnNormal = document.getElementById('btnModelBModeNormal');
            const btnFast = document.getElementById('btnModelBModeFast');
            if (btnNormal && btnFast) {
                if (isFast) {
                    btnNormal.className = 'px-3.5 py-2 rounded-lg text-xs font-semibold text-slate-300 bg-slate-950/80 border border-slate-800 hover:border-indigo-500/50 hover:text-indigo-300 transition-all flex items-center gap-2 cursor-pointer';
                    btnFast.className = 'px-3.5 py-2 rounded-lg text-xs font-bold bg-cyan-600 text-white shadow-md shadow-cyan-950/80 ring-2 ring-cyan-400 transition-all flex items-center gap-2 cursor-pointer';
                } else {
                    btnNormal.className = 'px-3.5 py-2 rounded-lg text-xs font-bold bg-indigo-600 text-white shadow-md shadow-indigo-950/80 ring-2 ring-indigo-400 transition-all flex items-center gap-2 cursor-pointer';
                    btnFast.className = 'px-3.5 py-2 rounded-lg text-xs font-semibold text-slate-300 bg-slate-950/80 border border-slate-800 hover:border-cyan-500/50 hover:text-cyan-300 transition-all flex items-center gap-2 cursor-pointer';
                }
            }

            if (parallelInstances) {
                const normInfo = parallelInstances.MODEL_B || {};
                const fastInfo = parallelInstances.MODEL_B_FAST || {};
                const badgeNorm = document.getElementById('badgeModelBModeNormal');
                const badgeFast = document.getElementById('badgeModelBModeFast');
                if (badgeNorm && normInfo.current_equity_usdt !== undefined) {
                    badgeNorm.innerText = `1H SWING \u2022 $${Number(normInfo.current_equity_usdt).toFixed(2)} (${normInfo.open_positions_count || 0} Open)`;
                }
                if (badgeFast && fastInfo.current_equity_usdt !== undefined) {
                    badgeFast.innerText = `15M SCALP \u2022 $${Number(fastInfo.current_equity_usdt).toFixed(2)} (${fastInfo.open_positions_count || 0} Open)`;
                }
                const dualBadge = document.getElementById('modelBDualRuntimeStatusBadge');
                if (dualBadge) {
                    const bothRunning = Boolean(normInfo.is_running && fastInfo.is_running);
                    dualBadge.innerText = bothRunning ? '2 PARALLEL BOTS RUNNING' : 'PARALLEL ENGINE ACTIVE';
                }
            }

            const configSummary = document.getElementById('modelBActiveConfigSummary');
            if (configSummary) {
                configSummary.innerText = isFast
                    ? 'Trigger: 15m | Trend: 1H EMA20/50 | Stop Floor: 0.80% (1.25x ATR) | TTL: 8 Bars (2.0h)'
                    : 'Trigger: 1H | Trend: 4H EMA20/50 | Stop Floor: 1.50% (1.25x ATR) | TTL: 6 Bars (6.0h)';
            }

            const titleEl = document.getElementById('modelBActiveBotTitle');
            if (titleEl) {
                titleEl.innerText = isFast
                    ? 'Model B Fast Scalper: 15m Liquidity Sweep Engine (15m / 1H Trend)'
                    : 'Model B: Aggressive Liquidity Research Engine (1H / 4H Swing)';
            }

            const tfBadge = document.getElementById('modelBActiveTimeframeBadge');
            if (tfBadge) {
                tfBadge.innerText = isFast ? '15M TRIGGER / 1H TREND' : '1H TRIGGER / 4H TREND';
                tfBadge.className = isFast
                    ? 'px-2 py-0.5 rounded text-[10px] font-bold bg-cyan-950 text-cyan-300 border border-cyan-600'
                    : 'px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-950 text-indigo-300 border border-indigo-700';
            }

            const descEl = document.getElementById('modelBActiveDescription');
            if (descEl) {
                descEl.innerText = isFast
                    ? 'Fast-timeframe parallel paper bot scanning closed 15m sweep-reclaims aligned with 1H EMA20/EMA50 structure, >=$5,000 L2 depth, 0.80% stop floor, and >=1.5R dynamic TP rescaling ($100.00 isolated paper equity).'
                    : 'Scans the universal perpetual universe (~578 contracts) using 1H structural sweep-reclaims, 4H EMA20/EMA50 trend alignment, multi-exchange order book depth (>=$5,000), and dynamic >=1.5R TP rescaling.';
            }

            const pipeTitle = document.getElementById('modelBCandidatesPipelineTitle');
            if (pipeTitle) {
                pipeTitle.innerText = isFast
                    ? 'Model B Fast (15m / 1H) Candidates Pipeline'
                    : 'Model B Normal (1H / 4H) Candidates Pipeline';
            }

            const journalTitle = document.getElementById('modelBJournalPanelTitle');
            if (journalTitle) {
                journalTitle.innerText = isFast
                    ? 'Model B Fast Scalper (15m / 1H) Paper Portfolio & Journal'
                    : 'Model B Normal (1H / 4H) Paper Portfolio & Journal';
            }

            const v2Label = document.getElementById('modelBCohortV2Label');
            const btnV1 = document.getElementById('modelBCohortFilterV1');
            if (v2Label) {
                v2Label.innerText = isFast ? '15M SCALP (FAST)' : '1H SWING (V2)';
            }
            if (btnV1) {
                if (isFast) {
                    btnV1.classList.add('hidden');
                } else {
                    btnV1.classList.remove('hidden');
                }
            }
        }

        window.switchModelBInstance = async function(modelId) {
            const cleanId = (String(modelId || 'MODEL_B').toUpperCase() === 'MODEL_B_FAST') ? 'MODEL_B_FAST' : 'MODEL_B';
            currentModelBInstance = cleanId;
            if (cleanId === 'MODEL_B_FAST' && currentModelBCohortFilter === 'V1') {
                currentModelBCohortFilter = 'ALL';
                updateModelBCohortButtonsUI('ALL');
            }
            updateModelBBotModeUI(cleanId);
            if (modelBStatusAbortController) {
                try { modelBStatusAbortController.abort(); } catch (e) {}
                modelBStatusAbortController = null;
            }
            modelBIsFetchingStatus = false;
            await Promise.allSettled([
                fetchModelBStatus(),
                fetchModelBPositions(),
                fetchModelBSetups(),
                fetchModelBOrders(),
                fetchModelBJournal(),
                fetchModelBDiagnosticReport(),
            ]);
        };

        function isModelBTabActive() {
            const tab = document.getElementById('tab-model_b');
            return Boolean(tab && !tab.classList.contains('hidden'));
        }

        function startModelBAutoRefresh() {
            stopModelBAutoRefresh();

            // 1. Immediate telemetry update
            fetchModelBStatus(true);

            // 2. High-frequency telemetry & rotation progress bar polling (3000ms)
            modelBTelemetryTimer = setInterval(() => {
                if (!document.hidden && isModelBTabActive()) {
                    fetchModelBStatus(true);
                }
            }, MODEL_B_STATUS_POLL_INTERVAL_MS);

            // 3. Moderate interval for positions, coverage, and setups (15000ms)
            modelBHeavyDataTimer = setInterval(() => {
                if (!document.hidden && isModelBTabActive()) {
                    if (typeof fetchModelBPositions === 'function') fetchModelBPositions();
                    if (typeof fetchModelBSetups === 'function') fetchModelBSetups();
                    if (typeof fetchModelBOrders === 'function') fetchModelBOrders();
                }
            }, MODEL_B_HEAVY_POLL_INTERVAL_MS);
        }

        function stopModelBAutoRefresh() {
            if (modelBTelemetryTimer) {
                clearInterval(modelBTelemetryTimer);
                modelBTelemetryTimer = null;
            }
            if (modelBHeavyDataTimer) {
                clearInterval(modelBHeavyDataTimer);
                modelBHeavyDataTimer = null;
            }
            if (modelBStatusAbortController) {
                try { modelBStatusAbortController.abort(); } catch (e) {}
                modelBStatusAbortController = null;
            }
            modelBIsFetchingStatus = false;
        }

        window.startModelBAutoRefresh = startModelBAutoRefresh;
        window.stopModelBAutoRefresh = stopModelBAutoRefresh;
        window.startModelBTelemetryPolling = startModelBAutoRefresh;
        window.stopModelBTelemetryPolling = stopModelBAutoRefresh;
        window.isModelBTabActive = isModelBTabActive;

        function handleModelBStatusError(errMsg) {
            if (Date.now() - modelBLastSuccessfulStatusMs > 12000) {
                const liveDot = document.getElementById('scannerTelemetryLiveDot');
                if (liveDot) {
                    liveDot.className = 'w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse';
                }
                const liveBadge = document.getElementById('scannerTelemetryLiveBadge');
                if (liveBadge) {
                    liveBadge.className = 'px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-amber-950 text-amber-300 border border-amber-800 flex items-center gap-1.5';
                    liveBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span><span>SYNCING...</span>';
                }
            }
        }

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
                fetchModelBDiagnosticReport(),
            ]);
        }

        async function fetchModelBStatus(silent = false) {
            if (modelBIsFetchingStatus) return;
            modelBIsFetchingStatus = true;

            if (modelBStatusAbortController) {
                try { modelBStatusAbortController.abort(); } catch (e) {}
            }
            modelBStatusAbortController = new AbortController();
            const timeoutHandle = setTimeout(() => {
                if (modelBStatusAbortController) {
                    try { modelBStatusAbortController.abort(); } catch (e) {}
                }
            }, 4500);

            try {
                const res = await fetch(`/api/model_b/status?model_id=${encodeURIComponent(currentModelBInstance)}`, {
                    signal: modelBStatusAbortController.signal,
                    headers: { 'Cache-Control': 'no-cache' }
                });
                clearTimeout(timeoutHandle);
                if (!res.ok) {
                    handleModelBStatusError(`HTTP ${res.status}`);
                    return;
                }
                const data = await res.json();
                modelBCachedStatus = data;
                modelBLastSuccessfulStatusMs = Date.now();
                updateModelBBotModeUI(currentModelBInstance, data.parallel_instances || null);

                // 1. Live status indicator heartbeat
                const liveDot = document.getElementById('scannerTelemetryLiveDot');
                if (liveDot) {
                    liveDot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse';
                }
                const liveBadge = document.getElementById('scannerTelemetryLiveBadge');
                if (liveBadge) {
                    liveBadge.className = 'px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-cyan-950 text-cyan-300 border border-cyan-800/80 flex items-center gap-1.5';
                    liveBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping"></span><span>LIVE (3s)</span>';
                }

                // 2. Risk, capital & kill switch status
                const currentEq = data.capital?.current_equity_usdt ?? 100.0;
                const eqEl = document.getElementById('modelBEquityDisplay');
                if (eqEl) eqEl.innerText = `$${Number(currentEq).toFixed(2)} USDT`;

                const dailyRealized = data.risk_limits?.daily_loss_realized_usdt ?? 0;
                const dailyLimit = data.risk_limits?.daily_loss_limit_usdt ?? 3.0;
                const dailyEl = document.getElementById('modelBDailyLossDisplay');
                if (dailyEl) dailyEl.innerText = `$${Number(dailyRealized).toFixed(2)} / $${Number(dailyLimit).toFixed(2)}`;

                const weeklyRealized = data.risk_limits?.weekly_loss_realized_usdt ?? 0;
                const weeklyLimit = data.risk_limits?.weekly_loss_limit_usdt ?? 7.5;
                const weeklyEl = document.getElementById('modelBWeeklyLossDisplay');
                if (weeklyEl) weeklyEl.innerText = `$${Number(weeklyRealized).toFixed(2)} / $${Number(weeklyLimit).toFixed(2)}`;

                const overrideActive = Boolean(data.risk_limits?.diagnostic_override_active || data.diagnostic?.override_active);
                const killSwitchOn = Boolean(data.risk_limits?.entry_kill_switch || data.diagnostic?.entry_kill_switch);

                const bannerEl = document.getElementById('modelBDiagnosticBanner');
                if (bannerEl) {
                    if (overrideActive) bannerEl.classList.remove('hidden');
                    else bannerEl.classList.add('hidden');
                }

                const killBtn = document.getElementById('modelBKillSwitchBtn');
                const killBtnText = document.getElementById('modelBKillSwitchBtnText');
                const killBtnDot = document.getElementById('modelBKillSwitchDot');
                if (killBtn && killBtnText) {
                    if (killSwitchOn) {
                        killBtn.className = 'px-3.5 py-2 rounded-lg text-xs font-bold transition flex items-center gap-2 shadow-sm bg-rose-600 hover:bg-rose-500 text-white border border-rose-500 animate-pulse';
                        killBtnText.innerText = 'Deactivate Kill Switch (ENTRIES BLOCKED)';
                        if (killBtnDot) killBtnDot.className = 'w-2 h-2 rounded-full bg-white';
                    } else {
                        killBtn.className = 'px-3.5 py-2 rounded-lg text-xs font-bold transition flex items-center gap-2 shadow-sm bg-rose-900/80 hover:bg-rose-800 text-rose-200 border border-rose-600';
                        killBtnText.innerText = 'Activate Kill Switch';
                        if (killBtnDot) killBtnDot.className = 'w-2 h-2 rounded-full bg-rose-400';
                    }
                }

                const dailyStat = document.getElementById('modelBDailyStatus');
                if (dailyStat) {
                    if (killSwitchOn) {
                        dailyStat.innerText = 'KILL SWITCH (ENTRIES BLOCKED)';
                        dailyStat.className = 'text-xs text-rose-400 font-mono font-bold animate-pulse';
                    } else if (data.risk_limits?.daily_circuit_breaker_active) {
                        if (overrideActive) {
                            dailyStat.innerText = 'BYPASSED (DIAGNOSTIC)';
                            dailyStat.className = 'text-xs text-amber-400 font-mono font-bold';
                        } else {
                            dailyStat.innerText = 'CIRCUIT BREAKER (HALTED)';
                            dailyStat.className = 'text-xs text-rose-400 font-mono font-bold animate-pulse';
                        }
                    } else {
                        dailyStat.innerText = 'Active (3R Cap)';
                        dailyStat.className = 'text-xs text-emerald-400 font-mono';
                    }
                }

                const weeklyStat = document.getElementById('modelBWeeklyStatus');
                if (weeklyStat) {
                    if (killSwitchOn) {
                        weeklyStat.innerText = 'KILL SWITCH (ENTRIES BLOCKED)';
                        weeklyStat.className = 'text-xs text-rose-400 font-mono font-bold animate-pulse';
                    } else if (data.risk_limits?.weekly_circuit_breaker_active) {
                        if (overrideActive) {
                            weeklyStat.innerText = 'BYPASSED (DIAGNOSTIC)';
                            weeklyStat.className = 'text-xs text-amber-400 font-mono font-bold';
                        } else {
                            weeklyStat.innerText = 'CIRCUIT BREAKER (HALTED)';
                            weeklyStat.className = 'text-xs text-rose-400 font-mono font-bold animate-pulse';
                        }
                    } else {
                        weeklyStat.innerText = 'Active (7.5R Cap)';
                        weeklyStat.className = 'text-xs text-emerald-400 font-mono';
                    }
                }

                const expEl = document.getElementById('modelBExposureDisplay');
                if (expEl) expEl.innerText = `${data.risk_limits?.open_positions_count || 0} / ${data.risk_limits?.max_open_positions || 10} Open`;

                const pendEl = document.getElementById('modelBPendingOrdersDisplay');
                if (pendEl) pendEl.innerText = `${data.risk_limits?.pending_orders_count || 0} Pending`;

                const statPend = document.getElementById('modelBStatPendingOrders');
                if (statPend) statPend.innerText = `${data.risk_limits?.pending_orders_count || 0} Pending`;

                const statOpen = document.getElementById('modelBStatOpenPositions');
                if (statOpen) statOpen.innerText = `${data.risk_limits?.open_positions_count || 0} Open`;

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

                // 3. Render Universal AsterDEX Scanner Engine Telemetry & Fair Rotation Progress Bar
                const perf = data.scanner_performance;
                const isRunning = Boolean(data.is_running);
                const scanCount = Number(data.scan_count || 0);

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
                    if (discEl2) discEl2.innerText = `${cov.discovered_asterdex ?? (data.discovered_markets_count || '--')} Pairs`;

                    const bulkEl = document.getElementById('scannerBulkScreened');
                    if (bulkEl) bulkEl.innerText = `Screened: ${cov.bulk_screened ?? cov.screened ?? 0}`;

                    const qualEl = document.getElementById('scannerQualifiedPool');
                    if (qualEl) qualEl.innerText = `${cov.screened ?? 0} Liquid`;

                    const deepEl = document.getElementById('scannerDeepEvaluated');
                    if (deepEl) deepEl.innerText = `${cov.deep_evaluated ?? cov.evaluated ?? 0} / Cycle`;

                    const rotProgPct = Number(rot.progress_pct || 0);
                    const rotProgEl = document.getElementById('scannerRotationProgress');
                    if (rotProgEl) rotProgEl.innerText = `${rotProgPct.toFixed(1)}%`;

                    const rotOffEl = document.getElementById('scannerRotationOffset');
                    if (rotOffEl) rotOffEl.innerText = `Offset: ${rot.offset || 0} / ${rot.pool_size || 0}`;

                    const p95El = document.getElementById('scannerP95Latency');
                    if (p95El) p95El.innerText = `p95: ${lat.p95_ms !== undefined && lat.p95_ms !== null ? Number(lat.p95_ms).toFixed(0) + ' ms' : '-- ms'}`;

                    const medEl = document.getElementById('scannerMedianLatency');
                    if (medEl) medEl.innerText = `med: ${lat.median_ms !== undefined && lat.median_ms !== null ? Number(lat.median_ms).toFixed(0) + ' ms' : '-- ms'}`;

                    const candYieldEl = document.getElementById('scannerCandidateYield');
                    if (candYieldEl) candYieldEl.innerText = `${cand.found || 0} Formulated`;

                    const readyYieldEl = document.getElementById('scannerReadyYield');
                    if (readyYieldEl) readyYieldEl.innerText = `${cand.ready || 0} READY`;

                    // Progress bar dynamically width adjusted
                    const progBar = document.getElementById('scannerProgressBar');
                    if (progBar) {
                        const clampedWidth = Math.min(100, Math.max(0, rotProgPct));
                        progBar.style.width = `${clampedWidth}%`;
                    }

                    // Progress bar dynamic summary text
                    const progText = document.getElementById('scannerProgressBarText');
                    if (progText) {
                        progText.innerText = `${rot.offset || 0} / ${rot.pool_size || 0} Liquid Contracts Rotated (${rotProgPct.toFixed(1)}%)`;
                    }

                    const covBadge = document.getElementById('scannerCoverageBadge');
                    if (covBadge) {
                        covBadge.innerText = `${cov.bulk_screened || cov.screened || 0} BULK SCREENED`;
                    }
                } else {
                    // Explicit IDLE / INITIALIZING display
                    const idleLabel = isRunning ? (scanCount > 0 ? 'SYNCHRONIZING' : 'INITIALIZING') : 'IDLE / STOPPED';
                    const cycleIdEl = document.getElementById('scannerCycleId');
                    if (cycleIdEl) cycleIdEl.innerText = `Cycle: -- (${idleLabel})`;

                    const durEl = document.getElementById('scannerDuration');
                    if (durEl) durEl.innerText = 'Scan: -- ms';

                    const hitEl = document.getElementById('scannerCacheHit');
                    if (hitEl) hitEl.innerText = 'Cache: --%';

                    const discEl2 = document.getElementById('scannerDiscoveredTotal');
                    if (discEl2) discEl2.innerText = data.discovered_markets_count ? `${data.discovered_markets_count} Pairs` : '--';

                    const bulkEl = document.getElementById('scannerBulkScreened');
                    if (bulkEl) bulkEl.innerText = 'Screened: --';

                    const qualEl = document.getElementById('scannerQualifiedPool');
                    if (qualEl) qualEl.innerText = '--';

                    const deepEl = document.getElementById('scannerDeepEvaluated');
                    if (deepEl) deepEl.innerText = '-- / Cycle';

                    const rotProgEl = document.getElementById('scannerRotationProgress');
                    if (rotProgEl) rotProgEl.innerText = '0.0%';

                    const rotOffEl = document.getElementById('scannerRotationOffset');
                    if (rotOffEl) rotOffEl.innerText = 'Offset: 0 / 0';

                    const p95El = document.getElementById('scannerP95Latency');
                    if (p95El) p95El.innerText = 'p95: -- ms';

                    const medEl = document.getElementById('scannerMedianLatency');
                    if (medEl) medEl.innerText = 'med: -- ms';

                    const candYieldEl = document.getElementById('scannerCandidateYield');
                    if (candYieldEl) candYieldEl.innerText = '0 Formulated';

                    const readyYieldEl = document.getElementById('scannerReadyYield');
                    if (readyYieldEl) readyYieldEl.innerText = '0 READY';

                    const progBar = document.getElementById('scannerProgressBar');
                    if (progBar) progBar.style.width = '0%';

                    const progText = document.getElementById('scannerProgressBarText');
                    if (progText) progText.innerText = `0 / 0 Liquid Contracts Rotated (${idleLabel})`;
                }
            } catch (err) {
                if (err.name === 'AbortError') return;
                console.warn("Model B status polling hiccup (handled):", err);
                handleModelBStatusError(err.message || String(err));
            } finally {
                clearTimeout(timeoutHandle);
                modelBIsFetchingStatus = false;
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
                const res = await fetch(`/api/model_b/setups?limit=25&model_id=${encodeURIComponent(currentModelBInstance)}`);
                if (!res.ok) return;
                const data = await res.json();
                const tbody = document.getElementById('modelBSetupsTableBody');
                const badge = document.getElementById('modelBSetupsCountBadge');
                if (!tbody) return;

                const setups = data.setups || [];
                if (badge) badge.innerText = `${setups.length} Candidate${setups.length === 1 ? '' : 's'}`;

                const pElSetups = document.getElementById('modelBSetupsPaginationContainer');
                if (setups.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-6 text-center text-slate-500">No Model B setups recorded. Click "Scan Universal Markets Now" to trigger a 1H cycle.</td></tr>';
                    if (pElSetups) pElSetups.innerHTML = '';
                    return;
                }

                const pagedSetups = (typeof paginateList === 'function')
                    ? paginateList('model_b_setups', setups)
                    : setups;

                tbody.innerHTML = pagedSetups.map(s => {
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

                if (typeof renderPaginationControls === 'function') {
                    renderPaginationControls('model_b_setups', 'modelBSetupsPaginationContainer', () => {
                        fetchModelBSetups();
                    });
                }

            } catch (err) {
                console.error("Model B setups error:", err);
            }
        }

        let modelBCachedPositions = [];

        async function fetchModelBPositions() {
            try {
                const res = await fetch(`/api/model_b/positions?model_id=${encodeURIComponent(currentModelBInstance)}`);
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
                    const rawUpnl = p.unrealized_pnl_net_usdt !== undefined ? p.unrealized_pnl_net_usdt : p.unrealized_pnl_usdt;
                    const hasUpnl = rawUpnl !== null && rawUpnl !== undefined;
                    const upnl = hasUpnl ? Number(rawUpnl) : 0;
                    const isUpnlPositive = upnl >= 0;
                    const upnlClass = !hasUpnl ? 'text-slate-400' : (isUpnlPositive ? 'text-emerald-400' : 'text-rose-400');
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
                                <div class="text-[11px] text-slate-300 font-normal flex items-center gap-1.5 mt-0.5">
                                    <span>Mark: $${p.mark_price !== null && p.mark_price !== undefined ? formatPrice(p.mark_price) : '--'}</span>
                                    ${p.mark_price_status ? `<span class="px-1 py-0.2 text-[9px] font-bold rounded ${p.mark_price_status === 'FRESH' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800/80' : p.mark_price_status === 'STALE' ? 'bg-amber-950 text-amber-400 border border-amber-800/80' : 'bg-rose-950 text-rose-400 border border-rose-800/80'}">${p.mark_price_status}</span>` : ''}
                                </div>
                                ${p.mark_price_time_utc ? `<div class="text-[9px] text-slate-500 font-mono">${new Date(p.mark_price_time_utc).toLocaleTimeString()}</div>` : ''}
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
                                <div class="font-bold ${upnlClass}">${hasUpnl ? `${upnlSign}$${upnl.toFixed(4)} USDT` : '--'}</div>
                                <div class="text-[11px] ${upnlClass} font-semibold">${hasUpnl && p.roe_pct !== null && p.roe_pct !== undefined ? `${upnlSign}${Number(p.roe_pct).toFixed(2)}% <span class="text-[10px] text-slate-400 font-normal">ROE</span>` : '<span class="text-slate-500 font-normal">ROE --</span>'}</div>
                                <div class="text-[10px] text-slate-500 font-normal">Net fees &amp; funding</div>
                            </td>
                            <td class="py-3 px-4 text-right">
                                <div class="flex items-center justify-end gap-1.5">
                                    <button onclick="inspectPositionChart('${escapeHtml(p.symbol)}')" class="px-2.5 py-1 text-xs font-semibold rounded bg-indigo-600 hover:bg-indigo-500 text-white transition flex items-center gap-1 shadow-sm" title="Inspect Interactive Setup Chart">
                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
                                        <span>Chart</span>
                                    </button>
                                    <button onclick="inspectModelBPosition('${escapeHtml(p.symbol)}')" class="px-2 py-1 text-xs font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1 shadow-sm" title="View Numerical Metrics">
                                        <span>Details</span>
                                    </button>
                                </div>
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
                            <div class="text-slate-300 flex items-center gap-1.5 text-[10px] mt-0.5">
                                <span>Live Mark: $${pos.mark_price !== null && pos.mark_price !== undefined ? formatPrice(pos.mark_price) : '--'}</span>
                                ${pos.mark_price_status ? `<span class="px-1 py-0.2 text-[9px] font-bold rounded ${pos.mark_price_status === 'FRESH' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' : 'bg-amber-950 text-amber-400 border border-amber-800'}">${pos.mark_price_status}</span>` : ''}
                            </div>
                            ${pos.mark_price_time_utc ? `<span class="text-slate-500 block text-[9px] font-mono">${pos.mark_price_time_utc}</span>` : ''}
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
                const res = await fetch(`/api/model_b/orders?status=PENDING&limit=25&model_id=${encodeURIComponent(currentModelBInstance)}`);
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

        let modelBAllRawJournalTrades = [];
        let currentModelBCohortFilter = 'ALL';

        function isV2Trade(t) {
            if (!t) return false;
            if (t.cohort === 'V2' || t.cohort === 'FAST_V2') return true;
            if (t.cohort_version === 'MODEL_B_V2_REMEDIATED' || t.cohort_version === 'MODEL_B_FAST_V2') return true;
            if (typeof t.details_json === 'string' && (t.details_json.includes('MODEL_B_V2_REMEDIATED') || t.details_json.includes('MODEL_B_FAST_V2'))) return true;
            if (t.trade_id && t.trade_id.startsWith('PTRD_FAST_')) return true;
            const entryTs = Number(t.entry_timestamp_ms || 0);
            if (entryTs >= 1792838400000 && t.exit_reason !== 'COHORT_CUTOVER_V2') return true;
            return false;
        }

        function updateModelBCohortButtonsUI(filter) {
            const btnAll = document.getElementById('modelBCohortFilterAll');
            const btnV2 = document.getElementById('modelBCohortFilterV2');
            const btnV1 = document.getElementById('modelBCohortFilterV1');
            if (!btnAll || !btnV2 || !btnV1) return;

            const baseClass = "px-3 py-1.5 rounded-lg text-xs font-semibold transition-all flex items-center gap-1.5 cursor-pointer select-none ";
            if (filter === 'V2') {
                btnAll.className = baseClass + "text-slate-400 bg-slate-900/80 border border-slate-800 hover:text-white hover:border-slate-700";
                btnV2.className = baseClass + "bg-purple-600 text-white shadow-md shadow-purple-950/80 ring-2 ring-purple-400 font-bold";
                btnV1.className = baseClass + "text-slate-400 bg-slate-900/80 border border-slate-800 hover:text-slate-200 hover:border-slate-700";
            } else if (filter === 'V1') {
                btnAll.className = baseClass + "text-slate-400 bg-slate-900/80 border border-slate-800 hover:text-white hover:border-slate-700";
                btnV2.className = baseClass + "text-slate-400 bg-slate-900/80 border border-slate-800 hover:text-purple-300 hover:border-purple-500/40";
                btnV1.className = baseClass + "bg-slate-700 text-white shadow-md shadow-slate-950/80 ring-2 ring-slate-400 font-bold";
            } else {
                btnAll.className = baseClass + "bg-indigo-600 text-white shadow-md shadow-indigo-950/80 ring-2 ring-indigo-400 font-bold";
                btnV2.className = baseClass + "text-slate-400 bg-slate-900/80 border border-slate-800 hover:text-purple-300 hover:border-purple-500/40";
                btnV1.className = baseClass + "text-slate-400 bg-slate-900/80 border border-slate-800 hover:text-slate-200 hover:border-slate-700";
            }
        }

        window.setModelBCohortFilter = function(filter) {
            const fUpper = String(filter || 'ALL').toUpperCase();
            currentModelBCohortFilter = fUpper;
            updateModelBCohortButtonsUI(fUpper);

            if (typeof resetPaginationPage === 'function') {
                resetPaginationPage('model_b_journal');
            }

            renderModelBJournalTable();
        };

        function renderModelBJournalTable() {
            const tbody = document.getElementById('modelBJournalTableBody');
            if (!tbody) return;

            // Filter in-memory trades smoothly
            let filteredTrades = modelBAllRawJournalTrades;
            if (currentModelBCohortFilter === 'V2') {
                filteredTrades = modelBAllRawJournalTrades.filter(isV2Trade);
            } else if (currentModelBCohortFilter === 'V1') {
                filteredTrades = modelBAllRawJournalTrades.filter(t => !isV2Trade(t));
            }

            modelBCachedJournalTrades = filteredTrades;

            // Update cohort counts on buttons
            const allCount = modelBAllRawJournalTrades.length;
            const v2Count = modelBAllRawJournalTrades.filter(isV2Trade).length;
            const v1Count = allCount - v2Count;

            const countAllEl = document.getElementById('modelBCountAll');
            const countV2El = document.getElementById('modelBCountV2');
            const countV1El = document.getElementById('modelBCountV1');
            if (countAllEl) countAllEl.innerText = String(allCount);
            if (countV2El) countV2El.innerText = String(v2Count);
            if (countV1El) countV1El.innerText = String(v1Count);

            // Compute dynamic metrics for the selected cohort filter
            const closedTrades = filteredTrades.filter(t => !t.is_open && Boolean(t.exit_timestamp_ms || t.exit_price));
            const closedCount = closedTrades.length;
            const openCount = filteredTrades.filter(t => t.is_open || !t.exit_timestamp_ms).length;
            const wins = closedTrades.filter(t => t.outcome === 'WIN');
            const winRate = closedCount > 0 ? (wins.length / closedCount * 100.0).toFixed(1) : '0.0';
            const netPnl = closedTrades.reduce((acc, t) => acc + Number(t.pnl_net || 0), 0.0);

            const wrEl = document.getElementById('modelBWinRate');
            if (wrEl) wrEl.innerText = `${winRate}%`;

            const netEl = document.getElementById('modelBNetPnl');
            if (netEl) {
                netEl.innerText = `${netPnl >= 0 ? '+$' : '-$'}${Math.abs(netPnl).toFixed(2)}`;
                netEl.className = netPnl > 0 ? 'text-emerald-400 font-bold' : (netPnl < 0 ? 'text-rose-400 font-bold' : 'text-slate-300 font-bold');
            }

            const badge = document.getElementById('modelBJournalMetricsBadge');
            if (badge) {
                const cohortSuffix = currentModelBCohortFilter === 'V2' ? ' (V2 Remediated)' : (currentModelBCohortFilter === 'V1' ? ' (V1 Legacy)' : ' (All)');
                badge.innerText = `${closedCount} Closed / ${openCount} Open${cohortSuffix}`;
            }

            const pElJournal = document.getElementById('modelBJournalPaginationContainer');
            if (filteredTrades.length === 0) {
                const emptyMsg = currentModelBCohortFilter === 'V2'
                    ? `<div class="max-w-md mx-auto space-y-2 py-4">
                         <div class="inline-flex p-2.5 rounded-full bg-purple-950/80 text-purple-400 border border-purple-800">
                           <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                         </div>
                         <div class="font-bold text-white text-sm">No V2 Remediated Trades Closed Yet</div>
                         <div class="text-xs text-slate-400">Model B V2 is actively monitoring markets with a clean $0 drawdown slate under remediated 1.0R dynamic TP floor and order-flow filters. Upcoming paper fills will appear here tagged as V2.</div>
                         <button onclick="setModelBCohortFilter('ALL')" class="mt-2 px-3 py-1 text-xs font-semibold rounded bg-purple-600 hover:bg-purple-500 text-white transition">View All 42 Trades</button>
                       </div>`
                    : '<div class="py-4 text-slate-500">No Model B trades found for selected filter.</div>';
                tbody.innerHTML = `<tr><td colspan="9" class="px-4 py-6 text-center">${emptyMsg}</td></tr>`;
                if (pElJournal) pElJournal.innerHTML = '';
                return;
            }

            const pagedTrades = (typeof paginateList === 'function')
                ? paginateList('model_b_journal', filteredTrades)
                : filteredTrades;

            tbody.innerHTML = pagedTrades.map(t => {
                const isLong = (t.direction || '').toUpperCase() === 'LONG';
                const dirClass = isLong ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold';
                const netPnlVal = Number(t.pnl_net || 0);
                const netClass = netPnlVal > 0 ? 'text-emerald-400 font-bold' : (netPnlVal < 0 ? 'text-rose-400 font-bold' : 'text-slate-300');
                const isV2 = isV2Trade(t);

                const rowClasses = isV2
                    ? 'bg-purple-950/20 hover:bg-purple-900/30 border-l-4 border-l-purple-400 transition'
                    : 'hover:bg-slate-800/40 border-l-4 border-l-slate-700 transition';

                const isFastTrade = (t.cohort === 'FAST_V2' || t.cohort_version === 'MODEL_B_FAST_V2' || (t.trade_id && t.trade_id.startsWith('PTRD_FAST_')) || (typeof t.details_json === 'string' && t.details_json.includes('MODEL_B_FAST')));
                const cohortBadge = isFastTrade
                    ? `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-black tracking-wide bg-cyan-900/90 text-cyan-200 border border-cyan-400 shadow-sm shadow-cyan-950/60 uppercase" title="15M Scalp Fast Cohort">
                         <span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span>
                         15M SCALP (FAST)
                       </span>`
                    : (isV2
                        ? `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-black tracking-wide bg-purple-900/90 text-purple-200 border border-purple-400 shadow-sm shadow-purple-950/60 uppercase" title="Remediated V2 Cohort">
                             <span class="w-2 h-2 rounded-full bg-purple-400 animate-pulse"></span>
                             1H SWING (V2)
                           </span>`
                        : `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-bold tracking-wide bg-slate-800 text-slate-400 border border-slate-700 uppercase" title="${escapeHtml(t.cohort_label || 'Legacy V1 Cohort')}">
                             <span class="w-2 h-2 rounded-full bg-slate-500"></span>
                             V1 LEGACY
                           </span>`);

                const symbolCohortPill = isV2
                    ? `<span class="px-2 py-0.5 rounded text-[10px] font-mono font-black bg-purple-600 text-white shadow-sm ring-1 ring-purple-300/40">V2</span>`
                    : `<span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-800 text-slate-400 border border-slate-700">V1</span>`;

                const isArchived = t.exit_reason === 'COHORT_CUTOVER_V2';

                return `
                    <tr class="${rowClasses}">
                        <td class="px-4 py-3">
                            <div class="flex items-center gap-1.5">
                                <span class="font-bold text-white">${escapeHtml(t.symbol)}</span>
                                ${symbolCohortPill}
                            </div>
                            <div class="text-[10px] text-slate-500 flex items-center gap-1.5 mt-0.5">
                                <span>${escapeHtml(t.trade_id.substring(0, 10))}</span>
                                ${isArchived ? '<span class="inline-flex items-center gap-1 px-1.5 py-0.2 rounded text-[9px] font-semibold bg-amber-950/80 text-amber-300 border border-amber-800" title="Archived at Cohort V2 Cutover"><span class="w-1 h-1 rounded-full bg-amber-400"></span>Archived</span>' : ''}
                            </div>
                        </td>
                        <td class="px-3 py-3">
                            ${cohortBadge}
                        </td>
                        <td class="px-4 py-3 ${dirClass}">${escapeHtml(t.direction || 'LONG')}</td>
                        <td class="px-4 py-3">
                            <div class="text-white font-mono">$${formatPrice(t.entry_price)}</div>
                            <div class="text-[10px] text-slate-400 font-mono">Exit: ${t.exit_price ? '$' + formatPrice(t.exit_price) : 'OPEN'}</div>
                        </td>
                        <td class="px-4 py-3 text-slate-300 font-mono">$${Number(t.pnl_gross || 0).toFixed(3)}</td>
                        <td class="px-4 py-3 text-amber-400 font-mono">$${Number(t.fee_paid || 0).toFixed(3)}</td>
                        <td class="px-4 py-3 font-mono ${netClass}">${netPnlVal >= 0 ? '+$' : '-$'}${Math.abs(netPnlVal).toFixed(3)}</td>
                        <td class="px-4 py-3">
                            <span class="px-2 py-0.5 rounded text-[10px] font-bold ${t.outcome === 'WIN' ? 'bg-emerald-950 text-emerald-300 border border-emerald-600' : (t.outcome === 'LOSS' ? 'bg-rose-950 text-rose-300 border border-rose-600' : 'bg-slate-800 text-slate-300')}">
                                ${escapeHtml(t.outcome || (t.is_open ? 'OPEN' : 'CLOSED'))}
                            </span>
                        </td>
                        <td class="px-4 py-3 text-right">
                            <button onclick="inspectModelBJournalChart('${escapeHtml(t.trade_id)}')" class="px-2.5 py-1 text-xs font-semibold rounded bg-indigo-600 hover:bg-indigo-500 text-white transition flex items-center gap-1 ml-auto shadow-sm" title="Inspect Historical Entry & Exit Chart">
                                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
                                <span>Chart</span>
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');

            if (typeof renderPaginationControls === 'function') {
                renderPaginationControls('model_b_journal', 'modelBJournalPaginationContainer', () => {
                    renderModelBJournalTable();
                });
            }
        }

        async function fetchModelBJournal() {
            try {
                const res = await fetch(`/api/model_b/journal?limit=500&model_id=${encodeURIComponent(currentModelBInstance)}`);
                if (!res.ok) return;
                const data = await res.json();
                modelBAllRawJournalTrades = data.trades || [];
                renderModelBJournalTable();
            } catch (err) {
                console.error("Model B journal error:", err);
            }
        }

        // Attach event delegation on DOMContentLoaded to guarantee interactivity
        if (typeof document !== 'undefined') {
            document.addEventListener('DOMContentLoaded', () => {
                const group = document.getElementById('modelBCohortFilterGroup');
                if (group) {
                    group.addEventListener('click', (e) => {
                        const btn = e.target.closest('button[data-cohort]');
                        if (btn) {
                            const cohort = btn.getAttribute('data-cohort');
                            if (cohort && typeof window.setModelBCohortFilter === 'function') {
                                window.setModelBCohortFilter(cohort);
                            }
                        }
                    });
                }
            });
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
                const res = await fetch(`/api/model_b/scan?model_id=${encodeURIComponent(currentModelBInstance)}`, { method: 'POST' });
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

        // Model B Tab & Document Lifecycle Listeners
        if (typeof document !== 'undefined') {
            document.addEventListener('visibilitychange', () => {
                if (document.hidden) {
                    stopModelBAutoRefresh();
                } else if (isModelBTabActive()) {
                    startModelBAutoRefresh();
                }
            });

            window.addEventListener('beforeunload', () => {
                stopModelBAutoRefresh();
            });

            document.addEventListener('DOMContentLoaded', () => {
                if (isModelBTabActive()) {
                    startModelBAutoRefresh();
                }
            });
        }

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
                    const shortCount = optPort.short_trades || 0;
                    const powerNote = shortCount < 30 ? ` | ⚠️ Low Power (N_short=${shortCount})` : '';
                    deltaEl.innerText = `Win Rate: +${winDelta.toFixed(1)}% | Net PnL: +$${pnlDelta.toFixed(2)} | Drawdown Cut: -$${ddDelta.toFixed(2)}${powerNote}`;
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

                const nClosed = Number(v.simulated_closed || 0);
                const isLowN = nClosed < 30;
                const lowNBadge = isLowN ? `<span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950 text-amber-300 border border-amber-700" title="Sample size < 30 trades lacks statistical power">LOW N (${nClosed})</span>` : '';
                const ciLow = v.win_rate_ci_lower_pct !== undefined ? Number(v.win_rate_ci_lower_pct).toFixed(1) : null;
                const ciHigh = v.win_rate_ci_upper_pct !== undefined ? Number(v.win_rate_ci_upper_pct).toFixed(1) : null;
                const ciText = (ciLow !== null && ciHigh !== null && nClosed > 0) ? `<div class="text-[9px] text-slate-400 font-mono">95% CI: [${ciLow}%-${ciHigh}%]</div>` : '';
                const warningBox = isLowN ? `
                    <div class="mt-2.5 p-2 rounded bg-amber-950/30 border border-amber-800/50 text-[10px] font-mono text-amber-300">
                        ⚠️ <strong>Small Sample Size (N=${nClosed}):</strong> High variance. 95% CI is wide [${ciLow || 0}% &ndash; ${ciHigh || 0}%]. Do not declare strategy superior.
                    </div>
                ` : '';

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
                                <div class="flex items-center gap-1.5">
                                    ${lowNBadge}
                                    <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border ${sideBadgeClass}">${side}</span>
                                </div>
                            </div>

                            <div class="grid grid-cols-3 gap-2 text-center text-xs font-mono pt-3">
                                <div class="p-2 rounded bg-slate-950/60 border border-slate-800/80">
                                    <div class="text-slate-400 text-[10px]">Net PnL</div>
                                    <div class="text-xs font-bold ${pnlColor} mt-0.5">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</div>
                                </div>
                                <div class="p-2 rounded bg-slate-950/60 border border-slate-800/80">
                                    <div class="text-slate-400 text-[10px]">Win Rate</div>
                                    <div class="text-xs font-bold text-white mt-0.5">${winRate}%</div>
                                    ${ciText}
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
                            ${warningBox}
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

                const pElSignals = document.getElementById('researchSignalsPaginationContainer');
                if (researchCachedSignals.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="7" class="px-4 py-6 text-center text-slate-500">No research signals found matching filters.</td></tr>`;
                    if (pElSignals) pElSignals.innerHTML = '';
                    return;
                }

                const pagedSignals = (typeof paginateList === 'function')
                    ? paginateList('model_b_shadow_signals', researchCachedSignals)
                    : researchCachedSignals;

                tbody.innerHTML = pagedSignals.map(sig => {
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

                if (typeof renderPaginationControls === 'function') {
                    renderPaginationControls('model_b_shadow_signals', 'researchSignalsPaginationContainer', () => {
                        fetchModelBResearchSignals();
                    });
                }

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
                fetchModelBDiagnosticReport(),
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
        // =========================================================================
        // MODEL B DIAGNOSTIC BREAKER OVERRIDE & LONG VS SHORT COHORT ANALYSIS
        // =========================================================================

        let modelBDiagCohort = 'experiment';

        async function toggleModelBKillSwitch() {
            const isCurrentlyOn = Boolean(modelBCachedStatus?.risk_limits?.entry_kill_switch);
            const targetState = !isCurrentlyOn;
            try {
                const res = await fetch('/api/model_b/diagnostic/kill_switch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: targetState, model_id: currentModelBInstance })
                });
                if (res.ok) {
                    await fetchModelBStatus();
                    await fetchModelBDiagnosticReport();
                } else {
                    const err = await res.json();
                    alert('Kill switch action failed: ' + (err.detail || 'Unknown error'));
                }
            } catch (e) {
                alert('Kill switch request error: ' + e.message);
            }
        }

        function switchModelBDiagCohort(cohort) {
            modelBDiagCohort = cohort;
            ['experiment', 'baseline', 'all'].forEach(c => {
                const cap = c.charAt(0).toUpperCase() + c.slice(1);
                const btn = document.getElementById(`btnDiagCohort${cap}`);
                if (btn) {
                    if (c === cohort) {
                        btn.className = 'px-2.5 py-1 rounded bg-amber-600 text-white font-medium shadow transition';
                    } else {
                        btn.className = 'px-2.5 py-1 rounded text-slate-400 hover:text-white transition';
                    }
                }
            });
            fetchModelBDiagnosticReport();
        }

        async function fetchModelBDiagnosticReport() {
            try {
                const cohort = modelBDiagCohort || 'experiment';
                const res = await fetch(`/api/model_b/diagnostic/report?cohort=${cohort}&model_id=${encodeURIComponent(currentModelBInstance)}`);
                if (!res.ok) return;
                const data = await res.json();

                const cBadge = document.getElementById('modelBDiagCohortBadge');
                if (cBadge) cBadge.innerText = `${(data.cohort || cohort).toUpperCase()} COHORT`;

                const oBadge = document.getElementById('modelBDiagOverrideBadge');
                if (oBadge) {
                    oBadge.innerText = data.override_active ? 'OVERRIDE ACTIVE' : 'OVERRIDE INACTIVE';
                    oBadge.className = data.override_active
                        ? 'px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-300 border border-amber-700 animate-pulse'
                        : 'px-2 py-0.5 rounded text-[10px] font-bold bg-slate-900 text-slate-300 border border-slate-700';
                }

                const statusText = document.getElementById('modelBDiagStatusText');
                if (statusText) {
                    statusText.innerText = data.override_active ? 'ACTIVE (BYPASSED)' : 'INACTIVE (HALTED)';
                    statusText.className = data.override_active ? 'text-sm font-bold text-amber-300 mt-0.5' : 'text-sm font-bold text-slate-400 mt-0.5';
                }

                const startText = document.getElementById('modelBDiagStartedText');
                if (startText) {
                    startText.innerText = data.override_started_at_utc ? `Started: ${new Date(data.override_started_at_utc).toLocaleTimeString()}` : 'Not started';
                }

                const killText = document.getElementById('modelBDiagKillSwitchStatus');
                if (killText) {
                    const kOn = Boolean(data.diagnostic?.entry_kill_switch || data.risk_status?.entry_kill_switch);
                    killText.innerText = kOn ? 'ON (BLOCKED)' : 'OFF (ALLOWING)';
                    killText.className = kOn ? 'text-sm font-bold text-rose-400 mt-0.5 animate-pulse' : 'text-sm font-bold text-emerald-400 mt-0.5';
                }

                const dupText = document.getElementById('modelBDiagDuplicatesText');
                if (dupText) dupText.innerText = `${data.duplicates_dropped || 0} Duplicates Filtered`;

                // Render directional metrics
                const dirs = data.per_direction || {};
                const l = dirs.LONG || {};
                const s = dirs.SHORT || {};

                const metricsBody = document.getElementById('modelBDiagMetricsTableBody');
                if (metricsBody) {
                    const lWinPct = l.win_rate_pct != null ? `${l.win_rate_pct}%` : 'N/A';
                    const sWinPct = s.win_rate_pct != null ? `${s.win_rate_pct}%` : 'N/A';
                    const winDelta = (l.win_rate_pct != null && s.win_rate_pct != null) ? `${(l.win_rate_pct - s.win_rate_pct).toFixed(1)}%` : '--';

                    const lNet = Number(l.net_realized_pnl || 0);
                    const sNet = Number(s.net_realized_pnl || 0);
                    const totNet = (lNet + sNet).toFixed(4);

                    const lGross = Number(l.gross_realized_pnl || 0);
                    const sGross = Number(s.gross_realized_pnl || 0);
                    const totGross = (lGross + sGross).toFixed(4);

                    const lExp = l.expectancy_per_trade != null ? `$${l.expectancy_per_trade.toFixed(4)}` : 'N/A';
                    const sExp = s.expectancy_per_trade != null ? `$${s.expectancy_per_trade.toFixed(4)}` : 'N/A';

                    const lPayoff = l.payoff_ratio != null ? l.payoff_ratio.toFixed(2) : 'N/A';
                    const sPayoff = s.payoff_ratio != null ? s.payoff_ratio.toFixed(2) : 'N/A';

                    const lExits = Object.entries(l.exit_reason_distribution || {}).map(([k, v]) => `${k}: ${v}`).join(', ') || 'None';
                    const sExits = Object.entries(s.exit_reason_distribution || {}).map(([k, v]) => `${k}: ${v}`).join(', ') || 'None';

                    metricsBody.innerHTML = `
                        <tr>
                            <td class="px-4 py-2.5 text-slate-400 font-medium">Closed Trades (n)</td>
                            <td class="px-4 py-2.5 font-bold text-white">${l.closed_trades || 0} (${l.wins || 0}W / ${l.losses || 0}L)</td>
                            <td class="px-4 py-2.5 font-bold text-white">${s.closed_trades || 0} (${s.wins || 0}W / ${s.losses || 0}L)</td>
                            <td class="px-4 py-2.5 text-slate-300">Total: ${(l.closed_trades || 0) + (s.closed_trades || 0)} trades</td>
                        </tr>
                        <tr>
                            <td class="px-4 py-2.5 text-slate-400 font-medium">Win Rate %</td>
                            <td class="px-4 py-2.5 font-bold ${l.win_rate_pct >= 50 ? 'text-emerald-400' : 'text-amber-400'}">${lWinPct}</td>
                            <td class="px-4 py-2.5 font-bold ${s.win_rate_pct >= 50 ? 'text-emerald-400' : 'text-rose-400'}">${sWinPct}</td>
                            <td class="px-4 py-2.5 font-mono text-indigo-300">Delta: ${winDelta}</td>
                        </tr>
                        <tr>
                            <td class="px-4 py-2.5 text-slate-400 font-medium">Net Realized PnL</td>
                            <td class="px-4 py-2.5 font-bold ${lNet >= 0 ? 'text-emerald-400' : 'text-rose-400'}">$${lNet.toFixed(4)} USDT</td>
                            <td class="px-4 py-2.5 font-bold ${sNet >= 0 ? 'text-emerald-400' : 'text-rose-400'}">$${sNet.toFixed(4)} USDT</td>
                            <td class="px-4 py-2.5 font-bold text-white">Net: $${totNet} USDT</td>
                        </tr>
                        <tr>
                            <td class="px-4 py-2.5 text-slate-400 font-medium">Gross Realized PnL</td>
                            <td class="px-4 py-2.5 font-mono ${lGross >= 0 ? 'text-emerald-300' : 'text-rose-300'}">$${lGross.toFixed(4)}</td>
                            <td class="px-4 py-2.5 font-mono ${sGross >= 0 ? 'text-emerald-300' : 'text-rose-300'}">$${sGross.toFixed(4)}</td>
                            <td class="px-4 py-2.5 font-mono text-slate-400">Gross: $${totGross}</td>
                        </tr>
                        <tr>
                            <td class="px-4 py-2.5 text-slate-400 font-medium">Fees &amp; Slippage</td>
                            <td class="px-4 py-2.5 font-mono text-slate-400">Fee: $${(l.fees || 0).toFixed(4)} | Slip: $${(l.slippage_embedded_in_fills || 0).toFixed(4)}</td>
                            <td class="px-4 py-2.5 font-mono text-slate-400">Fee: $${(s.fees || 0).toFixed(4)} | Slip: $${(s.slippage_embedded_in_fills || 0).toFixed(4)}</td>
                            <td class="px-4 py-2.5 font-mono text-slate-400">Funding: $${((l.funding || 0) + (s.funding || 0)).toFixed(4)}</td>
                        </tr>
                        <tr>
                            <td class="px-4 py-2.5 text-slate-400 font-medium">Expectancy / Trade</td>
                            <td class="px-4 py-2.5 font-bold ${l.expectancy_per_trade > 0 ? 'text-emerald-400' : 'text-slate-300'}">${lExp}</td>
                            <td class="px-4 py-2.5 font-bold ${s.expectancy_per_trade > 0 ? 'text-emerald-400' : 'text-rose-400'}">${sExp}</td>
                            <td class="px-4 py-2.5 text-slate-400">Target Risk: $1.00</td>
                        </tr>
                        <tr>
                            <td class="px-4 py-2.5 text-slate-400 font-medium">Avg Win / Avg Loss (Payoff)</td>
                            <td class="px-4 py-2.5 font-mono text-slate-300">+$${(l.avg_win || 0).toFixed(2)} / -$${Math.abs(l.avg_loss || 0).toFixed(2)} (${lPayoff}x)</td>
                            <td class="px-4 py-2.5 font-mono text-slate-300">+$${(s.avg_win || 0).toFixed(2)} / -$${Math.abs(s.avg_loss || 0).toFixed(2)} (${sPayoff}x)</td>
                            <td class="px-4 py-2.5 font-mono text-slate-400">Min R:R 1.5x</td>
                        </tr>
                        <tr>
                            <td class="px-4 py-2.5 text-slate-400 font-medium">Max Drawdown</td>
                            <td class="px-4 py-2.5 font-mono text-amber-300">$${(l.max_drawdown || 0).toFixed(4)}</td>
                            <td class="px-4 py-2.5 font-mono text-rose-300">$${(s.max_drawdown || 0).toFixed(4)}</td>
                            <td class="px-4 py-2.5 font-mono text-slate-400">Peak-to-trough</td>
                        </tr>
                        <tr>
                            <td class="px-4 py-2.5 text-slate-400 font-medium">Exit Distribution</td>
                            <td class="px-4 py-2.5 font-mono text-xs text-slate-300">${lExits}</td>
                            <td class="px-4 py-2.5 font-mono text-xs text-slate-300">${sExits}</td>
                            <td class="px-4 py-2.5 font-mono text-slate-400">Trailing &amp; SL tags</td>
                        </tr>
                    `;
                }

                // Render Candidate Signals Funnel
                const sigs = data.signals || {};
                const sigL = sigs.LONG || {};
                const sigS = sigs.SHORT || {};
                const totalSigs = (sigL.signals_total || 0) + (sigS.signals_total || 0);

                const sigTotEl = document.getElementById('modelBDiagSignalsTotal');
                if (sigTotEl) sigTotEl.innerText = `${totalSigs} Signals Evaluated`;

                const sigLEl = document.getElementById('modelBDiagSignalsLong');
                if (sigLEl) sigLEl.innerText = `Total: ${sigL.signals_total || 0} | Eligible: ${sigL.eligible || 0} | Rejected: ${sigL.rejected || 0}`;

                const sigSEl = document.getElementById('modelBDiagSignalsShort');
                if (sigSEl) sigSEl.innerText = `Total: ${sigS.signals_total || 0} | Eligible: ${sigS.eligible || 0} | Rejected: ${sigS.rejected || 0}`;

                const rejLEl = document.getElementById('modelBDiagRejectionsLong');
                if (rejLEl) {
                    const topL = Object.entries(sigL.rejection_reasons || {})[0];
                    rejLEl.innerText = topL ? `Top Rejection: ${topL[0]} (${topL[1]})` : 'No rejections';
                }

                const rejSEl = document.getElementById('modelBDiagRejectionsShort');
                if (rejSEl) {
                    const topS = Object.entries(sigS.rejection_reasons || {})[0];
                    rejSEl.innerText = topS ? `Top Rejection: ${topS[0]} (${topS[1]})` : 'No rejections';
                }

                // Render Breaker Reconciliation
                const recon = data.breaker_reconciliation || {};
                const wRecon = recon.weekly || {};
                const recPer = document.getElementById('modelBDiagReconPeriod');
                if (recPer) recPer.innerText = `Week: ${recon.week || 'Current'}`;

                const recLoss = document.getElementById('modelBDiagReconLoss');
                if (recLoss) recLoss.innerText = `$${Number(wRecon.sum_of_losing_trades_used_by_breaker || 0).toFixed(2)} / $${Number(data.risk_status?.weekly_loss_limit_usdt || 7.50).toFixed(2)} Cap`;

                const recWin = document.getElementById('modelBDiagReconWin');
                if (recWin) recWin.innerText = `+$${Number(wRecon.sum_of_winning_trades_ignored_by_breaker || 0).toFixed(2)} USDT`;

                const recNet = document.getElementById('modelBDiagReconNet');
                if (recNet) recNet.innerText = `$${Number(wRecon.net_realized_pnl || 0).toFixed(2)} USDT`;

                const recEnf = document.getElementById('modelBDiagReconEnforcement');
                if (recEnf) {
                    recEnf.innerText = data.override_active ? 'BYPASSED (Diagnostic Override Active)' : 'ENFORCED (Halted)';
                    recEnf.className = data.override_active ? 'font-bold text-amber-400' : 'font-bold text-rose-400';
                }
            } catch (err) {
                console.error('Failed to fetch Model B diagnostic report:', err);
            }
        }

        function inspectPositionChart(symbol) {
            const pos = modelBCachedPositions.find(p => p.symbol === symbol);
            if (!pos) {
                showNotice(`Position data for ${symbol} not found.`, 'info');
                return;
            }
            if (typeof openTradeInspectorModal === 'function') {
                openTradeInspectorModal(pos);
            }
        }

        let modelBCachedJournalTrades = [];
        function inspectModelBJournalChart(tradeId) {
            const trade = modelBCachedJournalTrades.find(t => t.trade_id === tradeId);
            if (!trade) {
                showNotice(`Journal trade ${tradeId} not found.`, 'info');
                return;
            }
            if (typeof openTradeInspectorModal === 'function') {
                openTradeInspectorModal(trade);
            }
        }
