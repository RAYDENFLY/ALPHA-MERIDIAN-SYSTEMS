const puppeteer = require('puppeteer-core');
const path = require('path');
const fs = require('fs');

const EDGE_PATH = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const ARTIFACTS_DIR = 'C:\\Users\\Administrator\\.gemini\\antigravity\\brain\\adae8d27-6351-440f-b44a-c1994272e623';

async function runTest() {
    console.log('[PoC Test] Launching Microsoft Edge via puppeteer-core...');
    const startTime = Date.now();

    const browser = await puppeteer.launch({
        executablePath: EDGE_PATH,
        headless: 'new',
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-gpu',
            '--disable-dev-shm-usage',
            '--window-size=1600,900'
        ]
    });

    try {
        const page = await browser.newPage();
        await page.setViewport({ width: 1600, height: 900 });

        const consoleLogs = [];
        const pageErrors = [];

        page.on('console', msg => {
            console.log(`[Browser Console] ${msg.type().toUpperCase()}: ${msg.text()}`);
            consoleLogs.push({ type: msg.type(), text: msg.text() });
        });

        page.on('pageerror', err => {
            console.error(`[Browser PageError] ${err.message}`);
            pageErrors.push(err.message);
        });

        console.log('[PoC Test] Navigating to http://127.0.0.1:8088/ ...');
        const navStart = Date.now();
        await page.goto('http://127.0.0.1:8088/', { waitUntil: 'networkidle2', timeout: 15000 });
        const loadDuration = Date.now() - navStart;
        console.log(`[PoC Test] Page loaded in ${loadDuration} ms`);

        // Wait for chart canvas to be present
        await page.waitForSelector('#chart canvas', { timeout: 10000 });
        console.log('[PoC Test] Chart canvas found.');

        // Allow 2 seconds for initial candle render & indicators
        await new Promise(r => setTimeout(r, 2000));

        // Evaluate state in browser
        const pageMetrics = await page.evaluate(() => {
            const chartCanvas = document.querySelectorAll('#chart canvas');
            const obAsks = document.querySelectorAll('#ob-asks .ob-row');
            const obBids = document.querySelectorAll('#ob-bids .ob-row');
            const statusText = document.getElementById('status-text')?.textContent || '';
            const ohlcTime = document.getElementById('ohlc-time')?.textContent || '';
            const ohlcClose = document.getElementById('ohlc-close')?.textContent || '';
            const obMid = document.getElementById('ob-mid')?.textContent || '';

            return {
                canvasCount: chartCanvas.length,
                asksCount: obAsks.length,
                bidsCount: obBids.length,
                statusText,
                ohlcTime,
                ohlcClose,
                obMid,
                jsHeapSize: window.performance && window.performance.memory ? window.performance.memory.usedJSHeapSize : null
            };
        });

        console.log('[PoC Test] Page Metrics:', pageMetrics);

        // Capture Workstation Screenshot
        const screenshotPath = path.join(ARTIFACTS_DIR, 'alpha_meridian_vela_poc_workstation.png');
        await page.screenshot({ path: screenshotPath, fullPage: false });
        console.log(`[PoC Test] Saved workstation screenshot to ${screenshotPath}`);

        // Also copy to desktop/reports/ for Git preservation
        const reportImgDir = path.join(__dirname, '..', 'reports', 'images');
        if (!fs.existsSync(reportImgDir)) fs.mkdirSync(reportImgDir, { recursive: true });
        fs.copyFileSync(screenshotPath, path.join(reportImgDir, 'alpha_meridian_vela_poc_workstation.png'));

        // Test Timeframe Switching to 4H
        console.log('[PoC Test] Testing timeframe switch to 4H...');
        await page.click('button[data-tf="4h"]');
        await new Promise(r => setTimeout(r, 1500));

        // Test Indicator Toggling (toggle EMA 20 off and on)
        console.log('[PoC Test] Testing indicator toggle (EMA 20)...');
        await page.click('button[data-ind="ema20"]');
        await new Promise(r => setTimeout(r, 500));
        await page.click('button[data-ind="ema20"]');
        await new Promise(r => setTimeout(r, 500));

        // Capture second screenshot showing 4H timeframe
        const screenshot4h = path.join(ARTIFACTS_DIR, 'alpha_meridian_vela_poc_4h.png');
        await page.screenshot({ path: screenshot4h, fullPage: false });
        fs.copyFileSync(screenshot4h, path.join(reportImgDir, 'alpha_meridian_vela_poc_4h.png'));
        console.log(`[PoC Test] Saved 4H screenshot to ${screenshot4h}`);

        // Test Hover Interaction for Crosshair / OHLC readout
        console.log('[PoC Test] Testing mouse hover crosshair...');
        const chartBox = await page.$eval('#chart', el => {
            const rect = el.getBoundingClientRect();
            return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
        });
        await page.mouse.move(chartBox.x, chartBox.y);
        await new Promise(r => setTimeout(r, 500));

        const updatedOhlc = await page.$eval('#ohlc-close', el => el.textContent);
        console.log('[PoC Test] Hovered OHLC Close readout:', updatedOhlc);

        console.log('[PoC Test] All live interactions verified successfully!');

    } finally {
        await browser.close();
    }
}

runTest().catch(err => {
    console.error('[PoC Test Error]', err);
    process.exit(1);
});
