/**
 * SignalGenerator Dashboard Centralized Localization Engine (i18n)
 * Provides seamless English (en) and Bahasa Indonesia (id) translation.
 * Strictly avoids translating raw symbols, strategy IDs, or exchange API fields.
 */

const SIGNALGEN_I18N = {
    en: {
        // App & Navigation
        app_title: "SMC-SNR ML Perpetual Setup Scanner",
        app_subtitle: "Zero-LLM Institutional Setup Engine \u2022 Aster DEX Perpetuals",
        paper_baseline_label: "Paper Baseline",
        live_equity_label: "Live Exchange Equity",
        candle_age_label: "Candle Age",
        badge_research: "RESEARCH",
        badge_live_locked: "LIVE LOCKED",
        badge_no_live_orders: "PAPER / RESEARCH — NO LIVE ORDERS",
        scanner_idle: "IDLE",
        scanner_active: "ACTIVE",
        scanner_busy: "SCANNING",
        last_scan_label: "Last Scan",
        last_scan_never: "Never",
        btn_scan_watchlist: "Scan Watchlist Now",
        btn_scanning: "Scanning...",
        
        // Navigation Tabs
        tab_setups: "Candidate Setups",
        tab_backtest: "Historical Backtest",
        tab_watchlist: "Watchlist & Controls",
        tab_journal: "Trading Journal",
        tab_alerts: "Price Alerts",
        tab_model_b: "Model B (Liquidity Engine)",
        tab_liquidity_intel: "Market Liquidity Intelligence",
        tab_sweep_chart: "Liquidity Sweep Chart",
        tab_demand_zone: "Market Screener",
        tab_market_screener: "Market Screener",
        screener_title: "Multi-Exchange Perpetual Market Screener",
        screener_subtitle: "Coinglass-style cross-venue perpetual futures discovery, liquidity comparison, and funding rate analytics across AsterDEX, Binance, Bybit, and Hyperliquid.",
        screener_kpi_total_pairs: "Screened Universe",
        screener_kpi_volume: "Combined 24h Turnover",
        screener_kpi_oi: "Open Interest Notional",
        tab_ai_analyst: "AI Analyst",
        tab_nav_category_all: "All Tabs",
        tab_nav_category_exec: "Execution",
        tab_nav_category_models: "Models",
        tab_nav_category_intel: "Intelligence & AI",

        // Header Metrics Cards
        card_exchange_equity: "Account Equity & Margin",
        card_target_margin: "Target Isolated Margin",
        card_daily_loss: "Daily Loss Limit",
        card_weekly_loss: "Weekly Loss Limit",
        card_position_exposure: "Position Exposure",
        hard_risk_limit: "Hard Risk Limit",
        active_no_losses: "Active (0 Losses)",
        entries_this_week: "Entries This Week",
        paper_exposure_badge: "PAPER / LIVE",
        isolated_margin_mode: "ISOLATED \u2022 10x Max",
        sync_label: "Sync",

        // Forward Paper Validation Banner
        forward_val_label: "Forward Paper Validation",
        forward_val_note: "0/30 completed forward paper trades recorded. Strategy edge is unproven in forward execution.",
        paper_orders_label: "Paper Orders",
        completed_trades_label: "Completed Trades",
        forward_expectancy_label: "Forward Net Expectancy",
        cost_assumptions: "Assumptions: 0.02% maker / 0.05% taker / 0.05% slip",

        // Live Positions Section
        live_positions_title: "Live AsterDEX Positions",
        btn_sync_live: "Sync Live",
        btn_analyze_all_positions: "Analyze All Positions",
        live_positions_source: "Source: AsterDEX V3 REST",
        live_positions_checking: "Checking live exchange positions...",
        live_positions_empty: "No open AsterDEX positions detected. Account is currently in cash.",

        // Status & Filters
        status_all: "All",
        status_ready: "Ready",
        status_watchlist: "Watchlist",
        status_infeasible: "Infeasible",
        status_invalidated: "Invalidated",
        status_stale: "Stale",
        status_error: "Error",
        filter_all_categories: "All Categories",
        cat_breakout: "Breakout",
        cat_pullback: "Pullback",
        cat_reversal: "Reversal",
        cat_range: "Range / Mean Reversion",
        cat_unclassified: "Unclassified",
        sort_confidence_desc: "Highest Confidence",
        sort_rr_desc: "Highest Net R:R",
        sort_symbol_asc: "Symbol (A–Z)",
        search_symbol_placeholder: "Search symbol...",
        btn_clear_search: "Clear",

        // Candidate Setups Empty & Archive States
        no_actionable_setup_title: "NO ACTIONABLE SETUP AT CURRENT CANDLE",
        no_actionable_setup_desc: "No setup candidates qualified at the latest 1H/4H candle close. All watchlist pairs are currently trading mid-range without hitting extreme liquidity zones or expansion triggers.",
        scanned_symbols_label: "Scanned Symbols",
        btn_view_watchlist: "View Watchlist & Specs",
        btn_inspect_historical_archive: "Inspect Historical Setup Archive",
        btn_return_live_scan: "Return to Live Scan View",
        historical_archive_banner: "HISTORICAL ARCHIVE VIEW: Showing past setup records from SQLite database. Current live scan has 0 active candidates.",

        // Table Headers & Columns
        col_trade_setup_id: "Trade / Setup ID",
        col_model: "Model",
        col_symbol_side: "Symbol & Side",
        col_category: "Entry Category",
        col_entry_time_price: "Entry (UTC / Price)",
        col_exit_time_price: "Exit (UTC / Price)",
        col_targets: "SL / TP Targets",
        col_duration: "Duration",
        col_gross_pnl: "Gross PnL",
        col_fees: "Total Fees",
        col_net_pnl: "Net PnL & Outcome",
        col_exit_reason: "Exit Reason",
        col_action: "Action",
        col_status: "Status",
        col_min_notional: "Min Notional",
        col_step_size: "Step Size",
        col_tick_size: "Tick Size",
        col_alert_name: "Alert / Symbol",
        col_target_price: "Target Price",
        col_condition: "Condition",
        col_created_at: "Created At",
        col_last_checked: "Last Checked",

        // Pagination Labels
        pg_showing: "Showing",
        pg_to: "to",
        pg_of: "of",
        pg_records: "records",
        pg_data: "items",
        pg_per_page: "per page",
        pg_page: "Page",
        pg_first: "First",
        pg_prev: "Previous",
        pg_next: "Next",
        pg_last: "Last",
        pg_no_records: "No records found matching filters.",

        // Journal & Analytics
        journal_title: "Trading Journal & Performance Analytics",
        journal_subtitle: "Complete lifecycle audit of executed paper trades, fee & funding settlements, audited realized PnL, and setup provenance.",
        btn_refresh_journal: "Refresh Journal",
        model_scope: "Model Scope",
        all_models: "All Models",
        model_a_title: "Model A — Defensive SMC/SnR",
        model_b_title: "Model B — Aggressive Liquidity",
        model_c_title: "Model C — Solana Momentum (Deferred)",
        closed_trades_metric: "Closed Trades",
        win_rate_metric: "Win Rate",
        gross_pnl_metric: "Gross Realized PnL",
        fees_friction_metric: "Total Fees & Friction",
        net_pnl_metric: "Net Realized PnL",
        avg_net_pnl_metric: "Average Net PnL",
        active_open_positions: "Active Open Positions",
        no_trades_found_title: "No Trade Records Found",
        no_trades_found_desc: "Paper trades are recorded automatically when qualifying setups decisively fill simulated limit orders and subsequently trigger take-profit or stop-loss brackets.",
        btn_reset_filters: "Reset",

        // Alerts Tab
        alerts_title: "Universal Perpetual Price Alerts",
        alerts_subtitle: "Sub-second cross-exchange price alerting engine with sound, browser toast, and webhook triggers.",
        btn_new_alert: "Create Price Alert",
        active_alerts_tab: "Active Alerts",
        triggered_history_tab: "Triggered History",

        // Demand-Zone LONG Scanner
        dz_title: "AsterDEX Demand-Zone LONG Setup Scanner",
        dz_subtitle: "Deterministic 1H support/demand zone identification \u2022 $0.30 fixed risk budget \u2022 1.5R minimum net reward-to-risk \u2022 L2 resting bid walls \u2022 BTC alignment.",
        btn_scan_demand_zones: "Scan Demand Zones",
        btn_inspect_setup: "Inspect Setup",

        // AI Analyst Tab
        ai_title: "Groq Trading Analyst \u2022 SMC Intelligence",
        ai_subtitle: "Algorithmic market-structure synthesis \u2022 Multi-scenario confluence scoring \u2022 Dynamic 60/40 entry laddering \u2022 Net R:R modeling \u2022 Institutional narrative.",
        btn_run_ai_analysis: "Run AI Analysis",
        btn_model_config: "Model Config",
        ai_long_scenario: "LONG Scenario",
        ai_short_scenario: "SHORT Scenario",
        ai_key_confluences: "Key Confluences",
        ai_structural_rationale: "Structural Rationale",
        ai_invalidation: "Invalidation Level",
        ai_risk_factors: "Risk Factors",
        ai_execution_plan: "Algorithmic Execution Plan",
        ai_market_snapshot: "Quantitative Market Snapshot",
        ai_id_narrative: "Institutional Narrative Review",
        ai_history_title: "Analysis History & Cache Log"
    },

    id: {
        // App & Navigation
        app_title: "Pemindai Setup Perpetual SMC-SNR ML",
        app_subtitle: "Mesin Setup Institusional Zero-LLM \u2022 Aster DEX Perpetual",
        paper_baseline_label: "Basis Paper",
        live_equity_label: "Ekuitas Live Exchange",
        candle_age_label: "Usia Candle",
        badge_research: "RISET",
        badge_live_locked: "ORDER LIVE TERKUNCI",
        badge_no_live_orders: "SIMULASI PAPER — TANPA ORDER LIVE",
        scanner_idle: "IDLE / STANDBY",
        scanner_active: "AKTIF",
        scanner_busy: "MEMINDAI",
        last_scan_label: "Pemindaian Terakhir",
        last_scan_never: "Belum Pernah",
        btn_scan_watchlist: "Pindai Watchlist Sekarang",
        btn_scanning: "Memindai...",

        // Navigation Tabs
        tab_setups: "Kandidat Setup",
        tab_backtest: "Backtest Historis",
        tab_watchlist: "Watchlist & Kontrol",
        tab_journal: "Jurnal Trading",
        tab_alerts: "Peringatan Harga",
        tab_model_b: "Model B (Mesin Likuiditas)",
        tab_liquidity_intel: "Intelijen Likuiditas Pasar",
        tab_sweep_chart: "Grafik Sweep Likuiditas",
        tab_demand_zone: "Pemindai Pasar",
        tab_market_screener: "Pemindai Pasar",
        screener_title: "Pemindai Pasar Perpetual Multi-Exchange",
        screener_subtitle: "Eksplorasi futures perpetual lintas exchange bergaya Coinglass, perbandingan likuiditas, dan analitik funding rate di AsterDEX, Binance, Bybit, dan Hyperliquid.",
        screener_kpi_total_pairs: "Universe Terpindai",
        screener_kpi_volume: "Turnover Gabungan 24j",
        screener_kpi_oi: "Nosional Open Interest",
        tab_ai_analyst: "Analis AI",
        tab_nav_category_all: "Semua Tab",
        tab_nav_category_exec: "Eksekusi",
        tab_nav_category_models: "Model Riset",
        tab_nav_category_intel: "Intelijen & AI",

        // Header Metrics Cards
        card_exchange_equity: "Ekuitas Akun & Margin",
        card_target_margin: "Target Margin Terisolasi",
        card_daily_loss: "Batas Rugi Harian",
        card_weekly_loss: "Batas Rugi Mingguan",
        card_position_exposure: "Eksposur Posisi",
        hard_risk_limit: "Batas Risiko Keras",
        active_no_losses: "Aktif (0 Kerugian)",
        entries_this_week: "Entri Minggu Ini",
        paper_exposure_badge: "PAPER / LIVE",
        isolated_margin_mode: "TERISOLASI \u2022 Maks 10x",
        sync_label: "Sinkron",

        // Forward Paper Validation Banner
        forward_val_label: "Validasi Paper Forward",
        forward_val_note: "0/30 trade forward paper terselesaikan. Keunggulan strategi belum terbukti dalam eksekusi forward.",
        paper_orders_label: "Order Paper",
        completed_trades_label: "Trade Selesai",
        forward_expectancy_label: "Ekspektansi Bersih Forward",
        cost_assumptions: "Asumsi: maker 0.02% / taker 0.05% / slippage 0.05%",

        // Live Positions Section
        live_positions_title: "Posisi Live AsterDEX",
        btn_sync_live: "Sinkronisasi Live",
        btn_analyze_all_positions: "Audit Semua Posisi",
        live_positions_source: "Sumber: AsterDEX V3 REST",
        live_positions_checking: "Memeriksa posisi live exchange...",
        live_positions_empty: "Tidak ada posisi terbuka di AsterDEX. Akun saat ini dalam posisi kas (cash).",

        // Status & Filters
        status_all: "Semua",
        status_ready: "Siap (Ready)",
        status_watchlist: "Pantauan (Watchlist)",
        status_infeasible: "Tidak Layak",
        status_invalidated: "Tidak Valid / Batal",
        status_stale: "Kedaluwarsa",
        status_error: "Kesalahan",
        filter_all_categories: "Semua Kategori",
        cat_breakout: "Breakout",
        cat_pullback: "Pullback",
        cat_reversal: "Reversal",
        cat_range: "Rentang / Mean Reversion",
        cat_unclassified: "Tanpa Klasifikasi",
        sort_confidence_desc: "Keyakinan Tertinggi",
        sort_rr_desc: "Net R:R Tertinggi",
        sort_symbol_asc: "Simbol (A–Z)",
        search_symbol_placeholder: "Cari simbol...",
        btn_clear_search: "Hapus",

        // Candidate Setups Empty & Archive States
        no_actionable_setup_title: "TIDAK ADA SETUP AKTIF PADA CANDLE SAAT INI",
        no_actionable_setup_desc: "Tidak ada kandidat setup yang memenuhi syarat pada penutupan candle 1H/4H terkini. Seluruh pasangan watchlist berada pada area mid-range tanpa menyentuh zona likuiditas ekstrem atau pemicu ekspansi.",
        scanned_symbols_label: "Simbol Terpindai",
        btn_view_watchlist: "Lihat Watchlist & Spesifikasi",
        btn_inspect_historical_archive: "Buka Arsip Setup Historis",
        btn_return_live_scan: "Kembali ke Tampilan Live Scan",
        historical_archive_banner: "TAMPILAN ARSIP HISTORIS: Menampilkan data setup masa lalu dari database SQLite. Pemindaian live saat ini memiliki 0 kandidat aktif.",

        // Table Headers & Columns
        col_trade_setup_id: "ID Trade / Setup",
        col_model: "Model",
        col_symbol_side: "Simbol & Arah",
        col_category: "Kategori Entri",
        col_entry_time_price: "Entri (UTC / Harga)",
        col_exit_time_price: "Keluar (UTC / Harga)",
        col_targets: "Target SL / TP",
        col_duration: "Durasi",
        col_gross_pnl: "PnL Kotor",
        col_fees: "Total Biaya",
        col_net_pnl: "PnL Bersih & Hasil",
        col_exit_reason: "Alasan Keluar",
        col_action: "Tindakan",
        col_status: "Status",
        col_min_notional: "Notional Min",
        col_step_size: "Ukuran Step",
        col_tick_size: "Ukuran Tick",
        col_alert_name: "Peringatan / Simbol",
        col_target_price: "Harga Target",
        col_condition: "Kondisi",
        col_created_at: "Dibuat Pada",
        col_last_checked: "Terakhir Dicek",

        // Pagination Labels
        pg_showing: "Menampilkan",
        pg_to: "hingga",
        pg_of: "dari",
        pg_records: "data",
        pg_data: "data",
        pg_per_page: "per halaman",
        pg_page: "Halaman",
        pg_first: "Awal",
        pg_prev: "Sebelumnya",
        pg_next: "Berikutnya",
        pg_last: "Akhir",
        pg_no_records: "Tidak ada data yang cocok dengan filter aktif.",

        // Journal & Analytics
        journal_title: "Jurnal Trading & Analisis Kinerja",
        journal_subtitle: "Audit siklus lengkap trade simulasi (paper), penyelesaian fee & funding, PnL bersih terealisasi, dan rekam jejak setup.",
        btn_refresh_journal: "Segarkan Jurnal",
        model_scope: "Cakupan Model",
        all_models: "Semua Model",
        model_a_title: "Model A — Defensif SMC/SnR",
        model_b_title: "Model B — Agresif Likuiditas",
        model_c_title: "Model C — Solana Momentum (Ditunda)",
        closed_trades_metric: "Trade Selesai",
        win_rate_metric: "Rasio Menang",
        gross_pnl_metric: "PnL Kotor Terealisasi",
        fees_friction_metric: "Total Biaya & Gesekan",
        net_pnl_metric: "PnL Bersih Terealisasi",
        avg_net_pnl_metric: "Rata-rata PnL Bersih",
        active_open_positions: "Posisi Aktif Berjalan",
        no_trades_found_title: "Tidak Ada Catatan Trade Ditemukan",
        no_trades_found_desc: "Trade simulasi (paper) dicatat secara otomatis ketika setup yang memenuhi kualifikasi mengeksekusi limit order dan memicu target take-profit atau stop-loss.",
        btn_reset_filters: "Atur Ulang",

        // Alerts Tab
        alerts_title: "Peringatan Harga Perpetual Universal",
        alerts_subtitle: "Mesin peringatan harga lintas exchange secepat sub-detik dengan suara, notifikasi browser, dan webhook.",
        btn_new_alert: "Buat Peringatan Baru",
        active_alerts_tab: "Peringatan Aktif",
        triggered_history_tab: "Riwayat Terpicu",

        // Demand-Zone LONG Scanner
        dz_title: "Pemindai Setup Demand-Zone LONG AsterDEX",
        dz_subtitle: "Identifikasi zona demand/support 1H deterministik \u2022 Anggaran risiko tetap $0.30 \u2022 Minimal net R:R 1.5R \u2022 Bid wall resting L2 \u2022 Konteks BTC.",
        btn_scan_demand_zones: "Pindai Zona Demand",
        btn_inspect_setup: "Inspeksi Setup",

        // AI Analyst Tab
        ai_title: "Analis Trading Groq \u2022 Intelijen SMC",
        ai_subtitle: "Sintesis struktur pasar algoritmik \u2022 Skor konfluensi multi-skenario \u2022 Entry ladder 60/40 \u2022 Pemodelan Net R:R \u2022 Narasi institusional.",
        btn_run_ai_analysis: "Jalankan Analisis AI",
        btn_model_config: "Konfigurasi Model",
        ai_long_scenario: "Skenario LONG",
        ai_short_scenario: "Skenario SHORT",
        ai_key_confluences: "Konfluensi Utama",
        ai_structural_rationale: "Alasan Struktural",
        ai_invalidation: "Level Batas Batal (Invalidasi)",
        ai_risk_factors: "Faktor Risiko",
        ai_execution_plan: "Rencana Eksekusi Algoritmik",
        ai_market_snapshot: "Snapshot Pasar Kuantitatif",
        ai_id_narrative: "Tinjauan Narasi Institusional",
        ai_history_title: "Riwayat Analisis & Log Cache"
    }
};

let currentLanguage = localStorage.getItem('signalgen_lang') || 'en';

function getLanguage() {
    return currentLanguage;
}

function getCurrentLanguage() {
    return currentLanguage;
}

function t(key, fallback = '') {
    const dict = SIGNALGEN_I18N[currentLanguage] || SIGNALGEN_I18N.en;
    if (dict && dict[key] !== undefined) {
        return dict[key];
    }
    // Fallback to English dictionary
    if (SIGNALGEN_I18N.en && SIGNALGEN_I18N.en[key] !== undefined) {
        return SIGNALGEN_I18N.en[key];
    }
    return fallback || key;
}

function setLanguage(lang) {
    if (lang !== 'en' && lang !== 'id') lang = 'en';
    currentLanguage = lang;
    localStorage.setItem('signalgen_lang', lang);

    // Update Header Language Buttons
    const enBtn = document.getElementById('langBtn-en');
    const idBtn = document.getElementById('langBtn-id');
    if (enBtn && idBtn) {
        if (lang === 'en') {
            enBtn.className = 'px-2.5 py-1 text-xs font-bold rounded bg-indigo-600 text-white shadow-sm transition';
            idBtn.className = 'px-2.5 py-1 text-xs font-medium rounded text-slate-400 hover:text-white transition';
        } else {
            idBtn.className = 'px-2.5 py-1 text-xs font-bold rounded bg-indigo-600 text-white shadow-sm transition';
            enBtn.className = 'px-2.5 py-1 text-xs font-medium rounded text-slate-400 hover:text-white transition';
        }
    }

    // Apply translations to all DOM elements with data-i18n
    applyTranslationsToDOM();

    // Dispatch event so active components re-render tables/cards with new locale
    window.dispatchEvent(new CustomEvent('languageChanged', { detail: { language: lang } }));

    // Re-render currently active tab view
    refreshActiveTabView();
}

function applyTranslationsToDOM() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (key) {
            const translation = t(key);
            if (translation) {
                // If element has icon children, replace text node only, else innerText
                const hasChildren = el.children && el.children.length > 0;
                if (!hasChildren) {
                    el.innerText = translation;
                } else {
                    const textNode = Array.from(el.childNodes).find(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim().length > 0);
                    if (textNode) {
                        textNode.textContent = ' ' + translation;
                    }
                }
            }
        }
    });

    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        if (key) el.placeholder = t(key);
    });

    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        if (key) el.title = t(key);
    });
}

function refreshActiveTabView() {
    try {
        if (typeof lastRawSetupsData !== 'undefined' && lastRawSetupsData && typeof renderSetups === 'function') {
            renderSetups(lastRawSetupsData);
        }
        if (typeof loadJournal === 'function' && document.getElementById('tab-journal') && !document.getElementById('tab-journal').classList.contains('hidden')) {
            loadJournal();
        }
        if (typeof renderWatchlistTable === 'function' && typeof cachedSymbols !== 'undefined' && cachedSymbols) {
            renderWatchlistTable(cachedSymbols);
        }
        if (typeof renderAlertsTable === 'function') {
            renderAlertsTable();
        }
        if (typeof loadMarketScreenerData === 'function' && document.getElementById('tab-demand_zone') && !document.getElementById('tab-demand_zone').classList.contains('hidden')) {
            if (typeof screenerDataCache !== 'undefined' && screenerDataCache) {
                renderScreenerTable(screenerDataCache);
            }
        } else if (typeof renderDemandZoneCandidatesTable === 'function' && document.getElementById('tab-demand_zone') && !document.getElementById('tab-demand_zone').classList.contains('hidden')) {
            renderDemandZoneCandidatesTable();
        }
        if (typeof renderAIAnalystHistoryTable === 'function' && document.getElementById('tab-ai_analyst') && !document.getElementById('tab-ai_analyst').classList.contains('hidden')) {
            renderAIAnalystHistoryTable();
        }
    } catch (e) {
        console.warn('Tab refresh error on language switch:', e);
    }
}

// Auto-initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    setLanguage(currentLanguage);
});

window.updateDOMTranslations = applyTranslationsToDOM;
