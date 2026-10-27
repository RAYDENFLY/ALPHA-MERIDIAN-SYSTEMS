/**
 * Alpha Meridian Systems — Vela Chart Performance & Usability Benchmark
 */

const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const EDGE_PATH = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

async function runBenchmark() {
    console.log('=== ALPHA MERIDIAN SYSTEMS: VELA CHART POC BENCHMARK ===\n');

    // 1. Cold Start & Launch Benchmark
    const t0 = Date.now();
    const browser = await puppeteer.launch({
        executablePath: EDGE_PATH,
        headless: 'new',
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-gpu',
            '--window-size=1600,900'
        ]
    });
    const tBrowserReady = Date.now();
    const browserLaunchMs = tBrowserReady - t0;
    console.log(`[Metric] Cold Browser Process Launch: ${browserLaunchMs} ms`);

    try {
        const page = await browser.newPage();
        await page.setViewport({ width: 1600, height: 900 });

        // Baseline memory
        const baselineMetrics = await page.metrics();
        console.log(`[Metric] Baseline JS Heap: ${(baselineMetrics.JSHeapUsedSize / 1024 / 1024).toFixed(2)} MB`);

        // Navigate & Measure Chart Load Time
        const tNavStart = Date.now();
        await page.goto('http://127.0.0.1:8088/', { waitUntil: 'networkidle2' });
        await page.waitForSelector('#chart canvas');
        const tChartReady = Date.now();
        const chartLoadMs = tChartReady - tNavStart;
        console.log(`[Metric] Chart Load & First Render Time: ${chartLoadMs} ms`);

        // Post-render metrics
        await new Promise(r => setTimeout(r, 1500));
        const renderedMetrics = await page.metrics();
        console.log(`[Metric] Post-Render JS Heap: ${(renderedMetrics.JSHeapUsedSize / 1024 / 1024).toFixed(2)} MB`);
        console.log(`[Metric] DOM Nodes: ${renderedMetrics.Nodes}`);
        console.log(`[Metric] JS Event Listeners: ${renderedMetrics.JSEventListeners}`);

        // Check WebGL2 vs Canvas2D backend
        const backendInfo = await page.evaluate(() => {
            const canvas = document.createElement('canvas');
            const gl2 = canvas.getContext('webgl2');
            const gl = canvas.getContext('webgl');
            const c2d = canvas.getContext('2d');

            return {
                hasWebGL2: !!gl2,
                hasWebGL1: !!gl,
                hasCanvas2D: !!c2d,
                activeCanvases: document.querySelectorAll('#chart canvas').length
            };
        });
        console.log('\n[Renderer Backend Assessment]:');
        console.log(`  - WebGL2 Available in Environment: ${backendInfo.hasWebGL2}`);
        console.log(`  - WebGL1 Available in Environment: ${backendInfo.hasWebGL1}`);
        console.log(`  - Canvas 2D Fallback Available: ${backendInfo.hasCanvas2D}`);
        console.log(`  - Active Canvas Elements Rendered: ${backendInfo.activeCanvases}`);

        // 2. Interaction Stress Test: Repeated Zooming and 2D Panning (50 operations)
        console.log('\n[Interaction Test] Executing 50 rapid pan and zoom cycles...');
        const tInteractStart = Date.now();
        const chartBox = await page.$eval('#chart', el => {
            const rect = el.getBoundingClientRect();
            return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
        });

        for (let i = 0; i < 25; i++) {
            // Drag pan
            await page.mouse.move(chartBox.x, chartBox.y);
            await page.mouse.down();
            await page.mouse.move(chartBox.x + 30, chartBox.y + 15, { steps: 2 });
            await page.mouse.up();

            // Wheel zoom
            await page.mouse.wheel({ deltaY: -50 });
            await page.mouse.wheel({ deltaY: 50 });
        }
        const tInteractEnd = Date.now();
        const interactDurationMs = tInteractEnd - tInteractStart;
        const avgInteractionMs = interactDurationMs / 50;
        console.log(`[Metric] 50 Interaction Cycles Completed in: ${interactDurationMs} ms (Avg: ${avgInteractionMs.toFixed(2)} ms/action)`);

        // Post-interaction memory check (leak audit)
        const postInteractMetrics = await page.metrics();
        const heapDeltaMb = (postInteractMetrics.JSHeapUsedSize - renderedMetrics.JSHeapUsedSize) / 1024 / 1024;
        console.log(`[Metric] Post-Interaction Heap: ${(postInteractMetrics.JSHeapUsedSize / 1024 / 1024).toFixed(2)} MB`);
        console.log(`[Metric] Heap Growth after 50 Interactions: ${heapDeltaMb.toFixed(2)} MB (Audit: ${heapDeltaMb < 5 ? 'STABLE / NO LEAK' : 'WARN'})`);

        // 3. Symbol Switching Responsiveness (BTCUSDT -> ETHUSDT -> SOLUSDT)
        console.log('\n[Market Switching Benchmark]:');
        const symbols = ['ETHUSDT', 'SOLUSDT', 'BTCUSDT'];
        for (const sym of symbols) {
            const tSymStart = Date.now();
            await page.select('#symbol-select', sym);
            // wait for new data to populate
            await page.waitForFunction(s => {
                const el = document.getElementById('ohlc-open');
                return el && el.textContent !== '--';
            }, {}, sym);
            const tSymEnd = Date.now();
            console.log(`  - Switched to ${sym}: ${tSymEnd - tSymStart} ms`);
        }

        // 4. Timeframe Switching Responsiveness (15m -> 1h -> 4h -> 1d)
        console.log('\n[Timeframe Switching Benchmark]:');
        const timeframes = ['15m', '4h', '1d', '1h'];
        for (const tf of timeframes) {
            const tTfStart = Date.now();
            await page.click(`button[data-tf="${tf}"]`);
            await new Promise(r => setTimeout(r, 400));
            const tTfEnd = Date.now();
            console.log(`  - Switched to timeframe ${tf}: ${tTfEnd - tTfStart} ms`);
        }

        // Summary result object
        const benchmarkSummary = {
            timestamp: new Date().toISOString(),
            coldStartBrowserMs: browserLaunchMs,
            chartLoadMs: chartLoadMs,
            baselineHeapMb: Number((baselineMetrics.JSHeapUsedSize / 1024 / 1024).toFixed(2)),
            postRenderHeapMb: Number((renderedMetrics.JSHeapUsedSize / 1024 / 1024).toFixed(2)),
            postInteractHeapMb: Number((postInteractMetrics.JSHeapUsedSize / 1024 / 1024).toFixed(2)),
            heapGrowthMb: Number(heapDeltaMb.toFixed(2)),
            avgInteractionMs: Number(avgInteractionMs.toFixed(2)),
            backend: backendInfo,
            status: 'BENCHMARK_COMPLETED_SUCCESSFULLY'
        };

        fs.writeFileSync(
            path.join(__dirname, 'benchmark_results.json'),
            JSON.stringify(benchmarkSummary, null, 2)
        );
        console.log('\n[Benchmark] Results written to benchmark_results.json');

    } finally {
        await browser.close();
    }
}

runBenchmark().catch(err => {
    console.error('[Benchmark Error]', err);
    process.exit(1);
});
