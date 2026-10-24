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

// 4. Model B refresh lifecycle is managed reactively by startModelBAutoRefresh / stopModelBAutoRefresh
if (typeof startModelBAutoRefresh === 'function' && typeof isModelBTabActive === 'function' && isModelBTabActive()) {
    startModelBAutoRefresh();
}

// 5. Initialize tab from URL parameter/hash if provided, strictly defaulting to 'setups'
const urlParams = new URLSearchParams(window.location.search);
const initialTab = urlParams.get('tab') || window.location.hash.replace('#', '') || 'setups';
if (typeof switchTab === 'function') {
    switchTab(initialTab);
}
