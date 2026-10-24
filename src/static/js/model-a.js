// =========================================================================
// MODEL A — DEFENSIVE SMC & SNR PERPETUAL ENGINE (READ-ONLY CONTROLLER)
// Strictly isolated to model_id = "MODEL_A"
// Never displays Model B data or fabricated mock values
// =========================================================================

let modelAAutoRefreshTimer = null;
let modelALatestPositions = [];
let modelALatestJournal = [];

function startModelAAutoRefresh() {
    stopModelAAutoRefresh();
    modelAAutoRefreshTimer = setInterval(() => {
        const panel = document.getElementById('tab-model_a');
        if (panel && !panel.classList.contains('hidden')) {
            loadModelA(true);
        }
    }, 30000);
}

function stopModelAAutoRefresh() {
    if (modelAAutoRefreshTimer) {
        clearInterval(modelAAutoRefreshTimer);
        modelAAutoRefreshTimer = null;
    }
}

function fmtModelATimeWIB(isoOrMs) {
    if (!isoOrMs) return 'UNAVAILABLE';
    if (typeof formatJakartaTime === 'function') {
        const res = formatJakartaTime(isoOrMs);
        return res === '--' ? 'UNAVAILABLE' : res;
    }
    try {
        const d = typeof isoOrMs === 'number' ? new Date(isoOrMs) : new Date(isoOrMs);
        if (isNaN(d.getTime())) return 'UNAVAILABLE';
        return d.toISOString();
    } catch (e) {
        return 'UNAVAILABLE';
    }
}

function fmtModelANum(val, decimals = 2, prefix = '', suffix = '') {
    if (val === null || val === undefined || val === '' || Number.isNaN(Number(val))) {
        return 'UNAVAILABLE';
    }
    return `${prefix}${Number(val).toFixed(decimals)}${suffix}`;
}

async function loadModelA(silent = false) {
    const icon = document.getElementById('modelARefreshIcon');
    if (icon && !silent) icon.classList.add('fa-spin');

    try {
        const res = await fetch('/api/model_a/status');
        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }
        const data = await res.json();
        if (!data || data.model_id !== 'MODEL_A') {
            throw new Error('Invalid Model A payload');
        }

        renderModelAStatusSection(data.strategy_status || {});
        renderModelAConfigurationSection(data.strategy_configuration || {});
        renderModelAForwardValidationSection(data.forward_validation || {});
        renderModelAValidationMetricsSection(data.validation_metrics || {});
        renderModelAOpenPositionsSection(data.open_positions || [], data.strategy_configuration?.risk_settings || {});
        renderModelATradeJournalSection(data.trade_journal || []);
        renderModelADiagnosticsSection(data.diagnostics || {});

        startModelAAutoRefresh();
    } catch (err) {
        console.error('Error loading Model A telemetry:', err);
        const badge = document.getElementById('modelAStateBadge');
        if (badge) {
            badge.textContent = 'UNAVAILABLE';
            badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-rose-950 text-rose-300 border border-rose-700';
        }
    } finally {
        if (icon) icon.classList.remove('fa-spin');
    }
}

function renderModelAStatusSection(st) {
    const badge = document.getElementById('modelAStateBadge');
    const stateVal = st.state || 'UNAVAILABLE';
    if (badge) {
        badge.textContent = stateVal;
        if (stateVal === 'IDLE' || stateVal === 'RUNNING') {
            badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-emerald-950 text-emerald-300 border border-emerald-700';
        } else if (stateVal === 'FROZEN_DEFERRED' || stateVal === 'STOPPED') {
            badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-blue-950 text-blue-300 border border-blue-700';
        } else {
            badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-amber-950 text-amber-300 border border-amber-700';
        }
    }

    const paperBadge = document.getElementById('modelAPaperBadge');
    if (paperBadge) {
        const lockedText = st.live_trading_locked ? 'LIVE LOCKED' : 'UNLOCKED';
        const paperText = st.paper_only ? 'PAPER ONLY' : (st.mode || 'RESEARCH');
        paperBadge.innerHTML = `${escapeHtml(paperText)} &bull; ${escapeHtml(lockedText)}`;
    }

    const cfgBadge = document.getElementById('modelAConfigIdBadge');
    if (cfgBadge) cfgBadge.textContent = st.active_config_id || 'MODEL_A_FROZEN';

    const setEl = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    };

    setEl('modelAStatusState', stateVal);
    setEl('modelAStatusProcess', st.process_status || 'UNAVAILABLE');
    setEl('modelAStatusLock', st.live_trading_locked ? 'PAPER / LIVE LOCKED' : 'LIVE UNLOCKED');
    setEl('modelAStatusMode', `Mode: ${st.mode || 'UNAVAILABLE'} (paper_only=${Boolean(st.paper_only)})`);
    setEl('modelAStatusHealth', st.scanner_health || 'UNAVAILABLE');
    setEl('modelAStatusWorker', `Worker: ${st.background_worker_active ? 'ACTIVE' : 'INACTIVE'} | Specs: ${st.metadata_available ? 'LOADED' : 'DEFERRED'}`);
    setEl('modelAStatusLastScan', st.last_scan_utc ? fmtModelATimeWIB(st.last_scan_utc) : 'NEVER_RUN');
    setEl('modelAStatusLastSuccess', `Last OK: ${st.last_successful_scan_utc ? fmtModelATimeWIB(st.last_successful_scan_utc) : 'NEVER_RUN'}`);
    setEl('modelAStatusScanCycles', `${st.historical_scan_cycles_completed ?? 0} Completed`);
    setEl('modelAStatusSessionScans', `Session Scans: ${st.session_scan_count ?? 0}`);
    setEl('modelAStatusConfigId', st.active_config_id || 'UNAVAILABLE');
}

function renderModelAConfigurationSection(cfg) {
    const whitelist = Array.isArray(cfg.whitelisted_symbols) ? cfg.whitelisted_symbols : [];
    const enabled = new Set(Array.isArray(cfg.enabled_symbols) ? cfg.enabled_symbols : []);

    const symCountEl = document.getElementById('modelAConfigSymbolCount');
    if (symCountEl) symCountEl.textContent = String(whitelist.length);

    const tf = cfg.timeframes || {};
    const tfLabel = document.getElementById('modelAConfigTimeframesLabel');
    if (tfLabel) {
        tfLabel.innerHTML = `Venue: <strong>${escapeHtml(cfg.venue || 'Aster DEX')}</strong> &bull; Bias: <strong>${escapeHtml(tf.bias_timeframe || '4h')}</strong> &bull; Execution: <strong>${escapeHtml(tf.execution_timeframe || '1h')}</strong> &bull; Macro Window: <strong>${tf.macro_lookback_bars_1h ?? 170} bars (7D)</strong>`;
    }

    const symListEl = document.getElementById('modelAConfigSymbolsList');
    if (symListEl) {
        if (whitelist.length === 0) {
            symListEl.innerHTML = '<span class="text-xs font-mono text-slate-500">UNAVAILABLE</span>';
        } else {
            symListEl.innerHTML = whitelist.map(sym => {
                const isEn = enabled.has(sym);
                const cls = isEn
                    ? 'bg-blue-950/70 text-blue-300 border-blue-800/80'
                    : 'bg-slate-900 text-slate-500 border-slate-800';
                return `<span class="px-2 py-0.5 rounded text-[11px] font-mono font-semibold border ${cls}">${escapeHtml(sym)}</span>`;
            }).join('');
        }
    }

    const sp = cfg.strategy_parameters || {};
    const stratContainer = document.getElementById('modelAConfigStrategyParams');
    if (stratContainer) {
        const rows = [
            ['Min Net Reward:Risk', sp.min_reward_to_risk !== undefined ? `${sp.min_reward_to_risk}R` : 'UNAVAILABLE'],
            ['72H Discount / Premium', (sp.discount_threshold_72h !== undefined && sp.premium_threshold_72h !== undefined) ? `<= ${(sp.discount_threshold_72h * 100).toFixed(0)}% / >= ${(sp.premium_threshold_72h * 100).toFixed(0)}%` : 'UNAVAILABLE'],
            ['48H / 12H Impulse Req', (sp.min_extension_48h !== undefined && sp.min_impulse_12h !== undefined) ? `${(sp.min_extension_48h * 100).toFixed(1)}% / ${(sp.min_impulse_12h * 100).toFixed(1)}%` : 'UNAVAILABLE'],
            ['SHORT 7D Macro Cap', sp.short_macro_cap_168h !== undefined ? `<= +${(sp.short_macro_cap_168h * 100).toFixed(1)}%` : 'UNAVAILABLE'],
            ['Bull Breakout VR(24/12)', sp.bull_variance_ratio_min !== undefined ? `>= ${sp.bull_variance_ratio_min}` : 'UNAVAILABLE'],
            ['Swing Pivots (L / R)', (sp.swing_left_bars !== undefined && sp.swing_right_bars !== undefined) ? `${sp.swing_left_bars} / ${sp.swing_right_bars} bars` : 'UNAVAILABLE'],
            ['ATR Period & SL Buffer', (sp.atr_period !== undefined && sp.sl_buffer_atr_multiple !== undefined) ? `ATR(${sp.atr_period}) x ${sp.sl_buffer_atr_multiple}` : 'UNAVAILABLE'],
            ['FVG Min Size / SnR Tol', (sp.fvg_min_size_atr_ratio !== undefined && sp.sr_cluster_tolerance_pct !== undefined) ? `${sp.fvg_min_size_atr_ratio} ATR / ${(sp.sr_cluster_tolerance_pct * 100).toFixed(2)}%` : 'UNAVAILABLE'],
            ['Signal Staleness / TTL', `${sp.stale_threshold_hours ?? 3.0}h stale / ${sp.pending_expiry_bars_1h ?? 12}h TTL`],
        ];
        stratContainer.innerHTML = rows.map(([k, v]) => `
            <div class="flex items-center justify-between border-b border-slate-800/40 pb-1">
                <span class="text-slate-400">${escapeHtml(k)}</span>
                <span class="text-white font-semibold">${escapeHtml(String(v))}</span>
            </div>
        `).join('');
    }

    const rk = cfg.risk_settings || {};
    const riskContainer = document.getElementById('modelAConfigRiskParams');
    if (riskContainer) {
        const rows = [
            ['Initial / Current Equity', `$${Number(rk.initial_equity_usdt ?? 16).toFixed(2)} / $${Number(rk.current_paper_equity_usdt ?? 16).toFixed(2)} USDT`],
            ['Margin Mode & Leverage', `${rk.margin_mode || 'ISOLATED'} (${rk.max_leverage ?? 10}x Max)`],
            ['Target Isolated Margin', `$${Number(rk.target_isolated_margin_usdt ?? 1).toFixed(2)} - $${Number(rk.target_isolated_margin_max_usdt ?? 1.5).toFixed(2)} USDT`],
            ['Nominal / Effective Risk', `$${Number(rk.nominal_risk_per_trade_usdt ?? 0.25).toFixed(2)} / $${Number(rk.effective_trade_risk_usdt ?? 0.09).toFixed(2)} USDT`],
            ['Hard Risk Ceiling', `$${Number(rk.hard_risk_limit_usdt ?? 1.5).toFixed(2)} USDT`],
            ['Daily / Weekly Loss Cap', `$${Number(rk.daily_loss_limit_usdt ?? 0.18).toFixed(2)} / $${Number(rk.weekly_loss_limit_usdt ?? 0.54).toFixed(2)} USDT`],
            ['Max Open Positions', `${rk.max_open_positions ?? 1} Concurrent Position`],
            ['Exchange Min Notional', `$${Number(rk.min_notional_usdt ?? 5.0).toFixed(2)} USDT`],
            ['Maker / Taker / Slippage', `${((rk.maker_fee_rate ?? 0.0002) * 100).toFixed(2)}% / ${((rk.taker_fee_rate ?? 0.0005) * 100).toFixed(2)}% / ${((rk.slippage_rate ?? 0.0005) * 100).toFixed(2)}%`],
        ];
        riskContainer.innerHTML = rows.map(([k, v]) => `
            <div class="flex items-center justify-between border-b border-slate-800/40 pb-1">
                <span class="text-slate-400">${escapeHtml(k)}</span>
                <span class="text-white font-semibold">${escapeHtml(String(v))}</span>
            </div>
        `).join('');
    }

    const ml = cfg.ml_parameters || {};
    const anchor = cfg.forward_validation_anchor || {};
    const cohortContainer = document.getElementById('modelAConfigCohortParams');
    if (cohortContainer) {
        const rows = [
            ['Strategy Version', cfg.strategy_version || 'MODEL_A_FROZEN'],
            ['Config Identifier', cfg.config_id || 'MODEL_A_FROZEN_v1.0.0'],
            ['ML Model & Calibration', `${ml.model_type || 'lightgbm'} (${ml.calibration_method || 'isotonic'})`],
            ['ML Governance Role', ml.role || 'INFORMATIVE_RANKING_NON_BLOCKING'],
            ['ML Confidence Reference', ml.min_confidence_threshold !== undefined ? `${(ml.min_confidence_threshold * 100).toFixed(1)}%` : 'UNAVAILABLE'],
            ['Label Horizon', ml.label_horizon_bars !== undefined ? `${ml.label_horizon_bars} x 1H bars` : 'UNAVAILABLE'],
            ['Cohort Start (WIB)', anchor.cohort_start_utc ? fmtModelATimeWIB(anchor.cohort_start_utc) : 'UNAVAILABLE'],
            ['Required Validation Sample', `${anchor.required_sample_size ?? 30} Completed Trades`],
        ];
        cohortContainer.innerHTML = rows.map(([k, v]) => `
            <div class="flex items-center justify-between border-b border-slate-800/40 pb-1">
                <span class="text-slate-400">${escapeHtml(k)}</span>
                <span class="text-white font-semibold">${escapeHtml(String(v))}</span>
            </div>
        `).join('');
    }
}

function renderModelAForwardValidationSection(fv) {
    const statusBadge = document.getElementById('modelACohortStatusBadge');
    if (statusBadge) {
        statusBadge.textContent = fv.validation_status || 'UNPROVEN';
    }

    const startEl = document.getElementById('modelACohortStartDisplay');
    if (startEl) {
        startEl.textContent = `Cohort Anchor: ${fv.cohort_start_utc ? fmtModelATimeWIB(fv.cohort_start_utc) : 'UNAVAILABLE'}`;
    }

    const expBanner = document.getElementById('modelACohortExplanationBanner');
    if (expBanner) {
        expBanner.textContent = fv.status_explanation || 'No forward validation explanation available.';
    }

    const setEl = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    };

    setEl('modelAFunnelCandles1h', fv.cached_candles_1h_total !== undefined ? Number(fv.cached_candles_1h_total).toLocaleString() : 'UNAVAILABLE');
    setEl('modelAFunnelCandles4h', `4H Cached: ${fv.cached_candles_4h_total !== undefined ? Number(fv.cached_candles_4h_total).toLocaleString() : 'UNAVAILABLE'}`);
    setEl('modelAFunnelBarEvals', fv.bar_evaluations_logged !== undefined ? Number(fv.bar_evaluations_logged).toLocaleString() : 'UNAVAILABLE');
    setEl('modelAFunnelScanCycles', `Cycles: ${fv.completed_scan_cycles ?? 0} OK / ${fv.started_scan_cycles ?? 0} Start`);
    setEl('modelAFunnelCandidates', String(fv.candidate_setups_evaluated ?? 0));
    setEl('modelAFunnelValidSetups', String(fv.valid_ready_setups ?? 0));
    setEl('modelAFunnelWatchlistSetups', `Watchlist: ${fv.watchlist_setups ?? 0}`);
    setEl('modelAFunnelRejectedSetups', String(fv.rejected_setups ?? 0));

    const bySt = fv.setups_by_status || {};
    setEl('modelAFunnelInfeasibleSub', `Inf: ${bySt.INFEASIBLE ?? 0} | Stale: ${bySt.STALE ?? 0} | Inv: ${bySt.INVALIDATED ?? 0}`);
    setEl('modelAFunnelPaperOrders', String(fv.paper_orders_created ?? 0));
    setEl('modelAFunnelPendingOrders', `Pending: ${fv.paper_orders_pending ?? 0}`);
    setEl('modelAFunnelOrdersFilled', String(fv.paper_orders_filled ?? 0));
    setEl('modelAFunnelOpenPos', `Open Pos: ${fv.open_paper_positions ?? 0}`);
    setEl('modelAFunnelCompletedTrades', `${fv.completed_paper_trades ?? 0} / ${fv.required_sample_size ?? 30}`);
    setEl('modelAFunnelSampleReq', fv.is_sample_sufficient ? 'SUFFICIENT_SAMPLE' : 'INSUFFICIENT_SAMPLE');

    // Pre-screen rejection reasons
    const preContainer = document.getElementById('modelAPreScreenRejections');
    if (preContainer) {
        const preRej = fv.pre_screen_rejection_reasons || {};
        const entries = Object.entries(preRej).sort((a, b) => b[1] - a[1]);
        if (entries.length === 0) {
            preContainer.innerHTML = '<div class="text-slate-500">No pre-screen rejection events recorded for MODEL_A.</div>';
        } else {
            const totalPre = entries.reduce((acc, [, c]) => acc + c, 0);
            preContainer.innerHTML = entries.map(([reason, cnt]) => {
                const pct = totalPre > 0 ? ((cnt / totalPre) * 100).toFixed(1) : '0.0';
                return `
                    <div class="flex items-center justify-between border-b border-slate-800/40 pb-1">
                        <span class="text-slate-300">${escapeHtml(reason)}</span>
                        <span class="text-amber-300 font-semibold">${Number(cnt).toLocaleString()} (${pct}%)</span>
                    </div>
                `;
            }).join('');
        }
    }

    // Candidate setup status & rejection breakdown
    const candContainer = document.getElementById('modelACandidateRejections');
    if (candContainer) {
        const candRej = fv.candidate_rejection_reasons || {};
        const entries = Object.entries(candRej).sort((a, b) => b[1] - a[1]);
        const statusSummary = `
            <div class="flex flex-wrap gap-2 pb-1.5 border-b border-slate-800/60 text-[11px]">
                <span class="px-2 py-0.5 rounded bg-emerald-950/60 text-emerald-300 border border-emerald-800">READY: ${bySt.READY ?? 0}</span>
                <span class="px-2 py-0.5 rounded bg-indigo-950/60 text-indigo-300 border border-indigo-800">WATCHLIST: ${bySt.WATCHLIST ?? 0}</span>
                <span class="px-2 py-0.5 rounded bg-amber-950/60 text-amber-300 border border-amber-800">INFEASIBLE: ${bySt.INFEASIBLE ?? 0}</span>
                <span class="px-2 py-0.5 rounded bg-slate-900 text-slate-300 border border-slate-700">STALE: ${bySt.STALE ?? 0}</span>
                <span class="px-2 py-0.5 rounded bg-rose-950/60 text-rose-300 border border-rose-800">INVALIDATED: ${bySt.INVALIDATED ?? 0}</span>
            </div>
        `;
        if (entries.length === 0) {
            candContainer.innerHTML = statusSummary + '<div class="text-slate-500 pt-1">No candidate rejection reasons recorded.</div>';
        } else {
            candContainer.innerHTML = statusSummary + entries.map(([reason, cnt]) => `
                <div class="flex items-center justify-between border-b border-slate-800/40 pb-1 pt-1">
                    <span class="text-slate-300 truncate max-w-[75%]" title="${escapeHtml(reason)}">${escapeHtml(reason)}</span>
                    <span class="text-rose-300 font-semibold">${Number(cnt).toLocaleString()} setups</span>
                </div>
            `).join('');
        }
    }
}

function renderModelAValidationMetricsSection(vm) {
    const sampleBadge = document.getElementById('modelAValidationSampleBadge');
    const isSuff = Boolean(vm.is_sample_sufficient);
    if (sampleBadge) {
        sampleBadge.textContent = vm.sample_status || (isSuff ? 'SUFFICIENT_SAMPLE' : 'INSUFFICIENT_SAMPLE');
        sampleBadge.className = isSuff
            ? 'px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-emerald-950 text-emerald-300 border border-emerald-700'
            : 'px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-amber-950 text-amber-300 border border-amber-700';
    }

    const setEl = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    };

    const completed = vm.completed_trades_count ?? 0;
    const req = vm.required_sample_size ?? 30;
    setEl('modelAMetricCompleted', `${completed} / ${req}`);
    setEl('modelAMetricSampleStatus', vm.sample_status || 'INSUFFICIENT_SAMPLE');

    setEl('modelAMetricWinRate', vm.win_rate_pct !== null && vm.win_rate_pct !== undefined ? `${Number(vm.win_rate_pct).toFixed(2)}%` : 'INSUFFICIENT_SAMPLE');
    setEl('modelAMetricWinLossSub', `${vm.wins_count ?? 0}W / ${vm.losses_count ?? 0}L / ${vm.breakeven_count ?? 0}BE`);

    const netPnl = Number(vm.net_realized_pnl_usdt ?? 0);
    setEl('modelAMetricNetPnl', `${netPnl >= 0 ? '+' : ''}$${netPnl.toFixed(4)}`);
    setEl('modelAMetricFeesSub', `Fees: $${Number(vm.total_fees_usdt ?? 0).toFixed(4)}`);

    setEl(
        'modelAMetricExpectancy',
        vm.expectancy_per_trade_usdt !== null && vm.expectancy_per_trade_usdt !== undefined
            ? `${Number(vm.expectancy_per_trade_usdt) >= 0 ? '+' : ''}$${Number(vm.expectancy_per_trade_usdt).toFixed(4)}`
            : 'INSUFFICIENT_SAMPLE'
    );

    setEl(
        'modelAMetricProfitFactor',
        vm.profit_factor !== null && vm.profit_factor !== undefined
            ? String(vm.profit_factor)
            : 'INSUFFICIENT_SAMPLE'
    );

    setEl(
        'modelAMetricMaxDD',
        vm.max_drawdown_usdt !== null && vm.max_drawdown_usdt !== undefined
            ? `$${Number(vm.max_drawdown_usdt).toFixed(4)}`
            : 'INSUFFICIENT_SAMPLE'
    );

    const avgW = vm.avg_win_usdt !== null && vm.avg_win_usdt !== undefined ? `+$${Number(vm.avg_win_usdt).toFixed(3)}` : '--';
    const avgL = vm.avg_loss_usdt !== null && vm.avg_loss_usdt !== undefined ? `$${Number(vm.avg_loss_usdt).toFixed(3)}` : '--';
    setEl('modelAMetricAvgWinLoss', completed > 0 ? `${avgW} / ${avgL}` : 'INSUFFICIENT_SAMPLE');

    const lb = vm.long_breakdown || {};
    const sb = vm.short_breakdown || {};
    setEl(
        'modelAMetricLongShort',
        `L: ${lb.count ?? 0} ($${Number(lb.net_pnl_usdt ?? 0).toFixed(2)}) | S: ${sb.count ?? 0} ($${Number(sb.net_pnl_usdt ?? 0).toFixed(2)})`
    );
    const lwr = lb.win_rate_pct !== null && lb.win_rate_pct !== undefined ? `${Number(lb.win_rate_pct).toFixed(1)}%` : '--';
    const swr = sb.win_rate_pct !== null && sb.win_rate_pct !== undefined ? `${Number(sb.win_rate_pct).toFixed(1)}%` : '--';
    setEl('modelAMetricLongShortWR', `WR: L ${lwr} / S ${swr}`);
}

function renderModelAOpenPositionsSection(positions, riskSettings) {
    // Strictly filter to MODEL_A only
    const isolated = (Array.isArray(positions) ? positions : []).filter(
        p => String(p.model_id || '').toUpperCase() === 'MODEL_A'
    );
    modelALatestPositions = isolated;

    const maxPos = riskSettings?.max_open_positions ?? 1;
    const badge = document.getElementById('modelAOpenPosCountBadge');
    if (badge) {
        badge.textContent = `${isolated.length} / ${maxPos} Open`;
    }

    const tbody = document.getElementById('modelAOpenPositionsBody');
    if (!tbody) return;

    if (isolated.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="py-6 text-center text-slate-400 font-mono">
                    0 Open Model A Paper Positions (Account in Cash &bull; Max Open Positions = ${maxPos})
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = isolated.map((pos, idx) => {
        const isLong = String(pos.direction || '').toUpperCase() === 'LONG';
        const sideCls = isLong
            ? 'bg-emerald-950/80 text-emerald-400 border-emerald-700'
            : 'bg-rose-950/80 text-rose-400 border-rose-700';

        const upnl = pos.unrealized_pnl_usdt;
        const upnlStr = (upnl !== null && upnl !== undefined)
            ? `${Number(upnl) >= 0 ? '+' : ''}$${Number(upnl).toFixed(4)} (${Number(pos.roe_pct ?? 0).toFixed(2)}%)`
            : 'UNAVAILABLE';
        const upnlCls = (upnl !== null && upnl !== undefined)
            ? (Number(upnl) > 0 ? 'text-emerald-400 font-bold' : (Number(upnl) < 0 ? 'text-rose-400 font-bold' : 'text-slate-300'))
            : 'text-slate-500';

        const markStr = pos.mark_price !== null && pos.mark_price !== undefined
            ? `$${formatPrice(pos.mark_price)}`
            : 'UNAVAILABLE';

        return `
            <tr class="hover:bg-slate-900/60 transition">
                <td class="py-2.5 px-3">
                    <div class="font-bold text-white">${escapeHtml(pos.symbol)}</div>
                    <span class="px-1.5 py-0.2 rounded text-[10px] font-bold border ${sideCls}">${escapeHtml(pos.direction)} ${Number(pos.leverage ?? 10).toFixed(0)}x</span>
                </td>
                <td class="py-2.5 px-3">
                    <div class="text-white">Entry: $${formatPrice(pos.entry_price)}</div>
                    <div class="text-indigo-300 text-[11px]">Mark: ${markStr}</div>
                </td>
                <td class="py-2.5 px-3">
                    <div class="text-white">${pos.quantity} (${fmtModelANum(pos.notional_usdt, 2, '$')})</div>
                    <div class="text-slate-400 text-[11px]">Margin: ${fmtModelANum(pos.margin_usdt, 2, '$')}</div>
                </td>
                <td class="py-2.5 px-3">
                    <div class="text-rose-400">SL: $${formatPrice(pos.stop_loss)}</div>
                    <div class="text-emerald-400 text-[11px]">TP1: $${formatPrice(pos.take_profit_1)} ${pos.take_profit_2 ? `| TP2: $${formatPrice(pos.take_profit_2)}` : ''}</div>
                </td>
                <td class="py-2.5 px-3 ${upnlCls}">${upnlStr}</td>
                <td class="py-2.5 px-3">
                    <div class="text-white">${escapeHtml(pos.age_display || '--')}</div>
                    <div class="text-slate-500 text-[10px]">${fmtModelATimeWIB(pos.entry_timestamp_ms)}</div>
                </td>
                <td class="py-2.5 px-3">
                    <span class="px-2 py-0.5 rounded text-[10px] bg-blue-950/70 text-blue-300 border border-blue-800">${escapeHtml(pos.source_strategy || 'MODEL_A')}</span>
                </td>
                <td class="py-2.5 px-3 text-right">
                    <button type="button" onclick="inspectModelAPosition(${idx})" class="px-2.5 py-1 rounded text-[11px] font-semibold bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 border border-indigo-500/40 transition">
                        <i class="fa-solid fa-chart-candlestick mr-1"></i>Inspect Setup
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

function renderModelATradeJournalSection(trades) {
    // Strictly filter to MODEL_A only
    const isolated = (Array.isArray(trades) ? trades : []).filter(
        t => String(t.model_id || '').toUpperCase() === 'MODEL_A'
    );
    modelALatestJournal = isolated;

    const badge = document.getElementById('modelAJournalCountBadge');
    if (badge) {
        badge.textContent = `${isolated.length} Records`;
    }

    const tbody = document.getElementById('modelATradeJournalBody');
    if (!tbody) return;

    if (isolated.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="py-6 text-center text-slate-400 font-mono">
                    0 Model A paper trades recorded in <code>trade_journal</code> (<code>model_id = "MODEL_A"</code>). Forward validation cohort has 0/30 completed fills.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = isolated.map((t, idx) => {
        const isLong = String(t.direction || '').toUpperCase() === 'LONG';
        const sideCls = isLong
            ? 'bg-emerald-950/80 text-emerald-400 border-emerald-700'
            : 'bg-rose-950/80 text-rose-400 border-rose-700';

        const pnl = Number(t.pnl_net ?? 0);
        const pnlCls = pnl > 0.0001 ? 'text-emerald-400 font-bold' : (pnl < -0.0001 ? 'text-rose-400 font-bold' : 'text-slate-300');
        const outcome = String(t.outcome || 'OPEN').toUpperCase();
        let outCls = 'bg-slate-800 text-slate-300 border-slate-700';
        if (outcome === 'WIN') outCls = 'bg-emerald-950 text-emerald-300 border-emerald-700';
        else if (outcome === 'LOSS') outCls = 'bg-rose-950 text-rose-300 border-rose-700';
        else if (outcome === 'OPEN') outCls = 'bg-blue-950 text-blue-300 border-blue-700';

        const mae = t.mae_pct ? `-${Math.abs(Number(t.mae_pct)).toFixed(2)}%` : '0.00%';
        const mfe = t.mfe_pct ? `+${Math.abs(Number(t.mfe_pct)).toFixed(2)}%` : '0.00%';

        return `
            <tr class="hover:bg-slate-900/60 transition">
                <td class="py-2.5 px-3">
                    <div class="text-white">In: ${fmtModelATimeWIB(t.entry_timestamp_ms)}</div>
                    <div class="text-slate-400 text-[11px]">Out: ${t.exit_timestamp_ms ? fmtModelATimeWIB(t.exit_timestamp_ms) : 'OPEN'}</div>
                </td>
                <td class="py-2.5 px-3">
                    <div class="font-bold text-white">${escapeHtml(t.symbol)}</div>
                    <span class="px-1.5 py-0.2 rounded text-[10px] font-bold border ${sideCls}">${escapeHtml(t.direction)}</span>
                </td>
                <td class="py-2.5 px-3">
                    <div class="text-white">In: $${formatPrice(t.entry_price)}</div>
                    <div class="text-slate-300 text-[11px]">Out: ${t.exit_price ? '$' + formatPrice(t.exit_price) : '--'}</div>
                </td>
                <td class="py-2.5 px-3">
                    <div class="text-indigo-300 truncate max-w-[180px]" title="${escapeHtml(t.entry_reason || t.entry_category || '')}">${escapeHtml(t.entry_reason || t.entry_category || 'SMC/SnR Setup')}</div>
                    <div class="text-slate-400 text-[11px] truncate max-w-[180px]" title="${escapeHtml(t.exit_reason || '')}">${escapeHtml(t.exit_reason || '--')}</div>
                </td>
                <td class="py-2.5 px-3 ${pnlCls}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(4)}</td>
                <td class="py-2.5 px-3 text-slate-300">$${Number(t.total_fees ?? 0).toFixed(4)}</td>
                <td class="py-2.5 px-3">
                    <span class="text-rose-400">${mae}</span> / <span class="text-emerald-400">${mfe}</span>
                </td>
                <td class="py-2.5 px-3">
                    <span class="px-2 py-0.5 rounded text-[10px] font-bold border ${outCls}">${escapeHtml(outcome)}</span>
                </td>
                <td class="py-2.5 px-3 text-right">
                    <button type="button" onclick="inspectModelAJourneyTrade(${idx})" class="px-2.5 py-1 rounded text-[11px] font-semibold bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 border border-indigo-500/40 transition">
                        <i class="fa-solid fa-chart-candlestick mr-1"></i>Chart
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

function renderModelADiagnosticsSection(diag) {
    const freshBadge = document.getElementById('modelADiagFreshnessBadge');
    const isStale = Boolean(diag.is_stale_market_data);
    if (freshBadge) {
        freshBadge.textContent = isStale ? 'STALE MARKET DATA' : 'FRESH MARKET DATA';
        freshBadge.className = isStale
            ? 'px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-amber-950 text-amber-300 border border-amber-700'
            : 'px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-emerald-950 text-emerald-300 border border-emerald-700';
    }

    const setEl = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    };

    const errText = diag.scanner_errors || 'None';
    const errEl = document.getElementById('modelADiagErrors');
    if (errEl) {
        errEl.textContent = errText;
        errEl.className = diag.scanner_errors
            ? 'text-sm font-bold text-rose-400 font-mono mt-1 truncate'
            : 'text-sm font-bold text-emerald-400 font-mono mt-1 truncate';
    }
    setEl('modelADiagFailedCycles', `Failed Cycles: ${diag.scan_failed_events_count ?? 0}`);
    setEl('modelADiagStaleState', isStale ? `STALE (${diag.stale_symbols_count ?? 0} syms)` : 'LIVE / FRESH');
    setEl('modelADiagCandleAge', diag.candle_age_hours !== null && diag.candle_age_hours !== undefined ? `Age: ${diag.candle_age_hours}h (Max ${diag.stale_threshold_hours ?? 3.0}h)` : 'Age: UNAVAILABLE');
    setEl('modelADiagMissingCandles', `${diag.missing_candles_symbols_count ?? 0} Symbols`);
    setEl('modelADiagInvalidated', String(diag.invalidated_setups_count ?? 0));
    setEl('modelADiagStaleSetups', `Stale Setups: ${diag.stale_setups_count ?? 0}`);
    setEl('modelADiagInfeasible', String(diag.infeasible_setups_count ?? 0));
    setEl('modelADiagPendingOrders', String(diag.pending_orders_count ?? 0));
    setEl('modelADiagLastSuccess', diag.last_successful_scan_utc ? fmtModelATimeWIB(diag.last_successful_scan_utc) : 'NEVER_RUN');
    setEl('modelADiagMetadataState', `Specs: ${diag.metadata_available ? 'AVAILABLE' : 'DEFERRED'} | Runner: ${diag.runner_initialized ? 'INIT' : 'DEFERRED'}`);

    const tbody = document.getElementById('modelADiagSymbolTableBody');
    if (!tbody) return;
    const rows = Array.isArray(diag.symbol_data_audit) ? diag.symbol_data_audit : [];
    if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="py-4 text-center text-slate-500">UNAVAILABLE</td></tr>';
        return;
    }

    tbody.innerHTML = rows.map(r => {
        const stBadge = r.is_stale
            ? '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-300 border border-amber-800">STALE</span>'
            : '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-300 border border-emerald-800">FRESH</span>';
        const barBadge = r.has_sufficient_bars
            ? '<span class="text-emerald-400 font-semibold">PASS</span>'
            : '<span class="text-rose-400 font-semibold">MISSING</span>';

        return `
            <tr class="hover:bg-slate-900/60 transition">
                <td class="py-2 px-3 font-bold text-white">${escapeHtml(r.symbol)}</td>
                <td class="py-2 px-3">${r.enabled ? '<span class="text-blue-400">ENABLED</span>' : '<span class="text-slate-500">DISABLED</span>'}</td>
                <td class="py-2 px-3 text-slate-200">${Number(r.candles_1h ?? 0).toLocaleString()} (${barBadge})</td>
                <td class="py-2 px-3 text-slate-200">${Number(r.candles_4h ?? 0).toLocaleString()}</td>
                <td class="py-2 px-3 text-slate-300">${r.latest_1h_utc ? fmtModelATimeWIB(r.latest_1h_utc) : 'UNAVAILABLE'}</td>
                <td class="py-2 px-3 text-slate-400">${r.last_sync_utc ? fmtModelATimeWIB(r.last_sync_utc) : 'UNAVAILABLE'}</td>
                <td class="py-2 px-3 text-slate-300">${r.candle_age_hours !== null && r.candle_age_hours !== undefined ? `${r.candle_age_hours}h` : 'UNAVAILABLE'}</td>
                <td class="py-2 px-3">${stBadge}</td>
            </tr>
        `;
    }).join('');
}

function inspectModelAPosition(idx) {
    const pos = modelALatestPositions[idx];
    if (!pos || typeof openTradeInspectorModal !== 'function') return;
    openTradeInspectorModal({
        ...pos,
        is_open: true,
        outcome: 'OPEN',
        timeframe: '1h',
        signal_source: 'AsterDEX',
        model_id: 'MODEL_A',
    });
}

function inspectModelAJourneyTrade(idx) {
    const trade = modelALatestJournal[idx];
    if (!trade || typeof openTradeInspectorModal !== 'function') return;
    openTradeInspectorModal({
        ...trade,
        timeframe: trade.timeframe || '1h',
        signal_source: 'AsterDEX',
        model_id: 'MODEL_A',
    });
}
