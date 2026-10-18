// =========================================================================
// APPLICATION ENTRYPOINT & STARTUP LIFECYCLE
// =========================================================================

// Initial price alerts telemetry fetch
loadAlerts();

// 1. Account status and risk limits refresh every 15 seconds
fetchStatus();
setInterval(fetchStatus, 15000);

// 2. Setup cards and status classifications refresh every 30 seconds
fetchSetups();
setInterval(fetchSetups, 30000);

// 3. Periodic polling for price alerts and trigger events (every 5 seconds)
setInterval(() => {
    loadAlertHistory();
    const alertsTab = document.getElementById('tab-alerts');
    if (alertsTab && !alertsTab.classList.contains('hidden')) {
        loadAlerts();
    }
}, 5000);

// 4. Periodic Model B refresh when Model B tab is active (every 10 seconds)
setInterval(() => {
    const tab = document.getElementById('tab-model_b');
    if (tab && !tab.classList.contains('hidden')) {
        loadModelB();
    }
}, 10000);

// 5. Initialize tab from URL parameter/hash if provided
const urlParams = new URLSearchParams(window.location.search);
const initialTab = urlParams.get('tab') || window.location.hash.replace('#', '');
if (initialTab && ['setups', 'backtest', 'watchlist', 'journal', 'alerts', 'model_b', 'liquidity_intel', 'sweep_chart'].includes(initialTab)) {
    switchTab(initialTab);
}
