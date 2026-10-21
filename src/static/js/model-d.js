let modelDPollTimer = null;
let modelDRequestInFlight = false;

function stopModelDAutoRefresh() {
    if (modelDPollTimer) { clearInterval(modelDPollTimer); modelDPollTimer = null; }
}

async function loadModelD() {
    stopModelDAutoRefresh();
    await refreshModelD();
    modelDPollTimer = setInterval(() => { if (!document.hidden) refreshModelD(); }, 15000);
}

async function refreshModelD() {
    if (modelDRequestInFlight) return;
    modelDRequestInFlight = true;
    const state = document.getElementById('modelDState');
    const symbol = (document.getElementById('modelDSymbol')?.value || 'BTCUSDT').trim().toUpperCase();
    try {
        const [statusRes, liquidityRes, reportRes, walletsRes] = await Promise.all([
            fetch(`/api/model_d/status?symbol=${encodeURIComponent(symbol)}`),
            fetch(`/api/model_d/liquidity?symbol=${encodeURIComponent(symbol)}`),
            fetch('/api/model_d/diagnostic/report'),
            fetch('/api/model_d/wallets/status')
        ]);
        if (!statusRes.ok || !liquidityRes.ok || !reportRes.ok || !walletsRes.ok) throw new Error('Model D endpoint unavailable');
        const [status, liquidity, report, wallets] = await Promise.all([statusRes.json(), liquidityRes.json(), reportRes.json(), walletsRes.json()]);
        if (state) state.textContent = `Model D: ${status.enabled ? 'ENABLED' : 'DISABLED'} · paper-only: ${status.paper_only} · live lock: ${status.live_trading_locked} · wallet tracking: ${status.wallet_tracking}`;
        const sources = document.getElementById('modelDSources');
        if (sources) sources.textContent = JSON.stringify(status.sources || {}, null, 2);
        const liq = document.getElementById('modelDLiquidity');
        if (liq) liq.textContent = JSON.stringify(liquidity, null, 2);
        const research = document.getElementById('modelDResearch');
        if (research) research.textContent = JSON.stringify(report, null, 2);
        const walletEl = document.getElementById('modelDWallets');
        if (walletEl) walletEl.textContent = JSON.stringify(wallets, null, 2);
    } catch (err) {
        if (state) state.textContent = `Model D ERROR: ${err.message}`;
    } finally { modelDRequestInFlight = false; }
}

document.addEventListener('visibilitychange', () => { if (document.hidden) stopModelDAutoRefresh(); else if (document.getElementById('tab-model_d') && !document.getElementById('tab-model_d').classList.contains('hidden')) loadModelD(); });
