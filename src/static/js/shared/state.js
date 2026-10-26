// =========================================================================
// SHARED GLOBAL STATE & REACTIVE VARIABLES
// =========================================================================
let equityChartInstance = null;

// UI State preservation across polling intervals
let currentFilter = 'ALL';
let currentCategoryFilter = 'ALL';
let currentSort = 'confidence_desc';
let searchQuery = '';
let lastRawSetupsData = null;
let showHistoricalArchive = false;

// Trading Journal State (Unified Automated Trade Journal)
let journalModeFilter = 'LIVE'; // LIVE, PAPER, SHADOW, ALL
let journalStrategyFilter = 'ALL';
let journalSideFilter = 'ALL';
let journalStatusFilter = 'ALL';
let journalReconFilter = 'ALL';
let journalStartDate = '';
let journalEndDate = '';
let journalSearchQuery = '';
let journalSortBy = 'NEWEST';
let lastFetchedTrades = [];
let cachedFillsByTrade = {};
let expandedTrades = new Set();
let isJournalLoading = false;
let isReconciling = false;

// Universal Price Alerts State
let alertsFilter = 'ALL';
let cachedAlertSymbols = [];
let selectedAlertSymbolItem = null;
let soundAlertsEnabled = localStorage.getItem('soundAlertsEnabled') !== 'false';
let seenAlertEventIds = new Set();
let alertsInitialized = false;

// Model B Research State
let modelBCachedStatus = null;

// AsterDEX Position & Market Intelligence State
let currentAnalyzedSymbol = null;
let cachedWhaleEvents = [];
let currentWhaleFilter = 'ALL';

// Interactive Liquidity Sweep & Reclaim Chart State
let tvChart = null;
let candleSeries = null;
let volumeSeries = null;
let activePriceLines = [];
let chartLayersData = null;
let chartEventsData = [];
let currentChartSymbol = 'BTCUSDT';
let currentChartTf = '1h';
let activeOverlays = {
    structural: true,
    orderbook: true,
    liquidation: true
};
let isReplayMode = false;
let replayCurrentIndex = null;
let replayPlaying = false;
let replayTimer = null;
let currentSelectedEventId = null;
let currentChartEventFilter = 'ALL';
let chartPriceScaleAuto = true;
let chartPriceScaleLog = false;
let isChartFullscreen = false;
