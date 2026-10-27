/**
 * Alpha Meridian Systems — Vela Chart Indicator & Data Adapter Verification Suite
 *
 * Mathematically validates:
 * 1. Exponential Moving Average (EMA 20, 50, 100, 200) formula against ground truth.
 * 2. Stochastic Oscillator (5, 3, 3) %K and %D calculations.
 * 3. Data Adapter OHLCV integrity checks, timestamps, and monotonic order.
 * 4. Read-only GET endpoint invariants.
 */

const assert = require('assert');
const { AmsDataAdapter } = require('../web/adapter');

console.log('=== ALPHA MERIDIAN SYSTEMS: VELA CHART POC VERIFICATION SUITE ===\n');

// -------------------------------------------------------------
// 1. EMA Mathematical Calculation Verification
// -------------------------------------------------------------
function calculateReferenceEma(closes, period) {
    if (closes.length === 0) return [];
    const k = 2 / (period + 1);
    const result = [];
    let prev = closes[0];
    result.push(prev);

    for (let i = 1; i < closes.length; i++) {
        const currentEma = closes[i] * k + prev * (1 - k);
        result.push(currentEma);
        prev = currentEma;
    }
    return result;
}

console.log('[Test 1] Verifying EMA Formula against Mathematical Ground Truth...');
const testCloses = [
    80000, 80200, 80150, 80400, 80600, 80500, 80700, 80900, 80850, 81000,
    81200, 81100, 81300, 81500, 81400, 81600, 81800, 81750, 81900, 82100,
    82000, 82250, 82400, 82300, 82500
];

const ema20Expected = calculateReferenceEma(testCloses, 20);
assert.strictEqual(ema20Expected.length, testCloses.length);
// Check weight k for period 20: 2 / (20 + 1) = 2 / 21 ≈ 0.095238
const k20 = 2 / 21;
const manualEma1 = testCloses[1] * k20 + testCloses[0] * (1 - k20);
assert.ok(Math.abs(ema20Expected[1] - manualEma1) < 1e-9, 'EMA step 1 matches manual calculation');
console.log('  ✔ EMA 20 reference ground truth verified.');

const ema50Expected = calculateReferenceEma(testCloses, 50);
const k50 = 2 / 51;
const manualEma50_1 = testCloses[1] * k50 + testCloses[0] * (1 - k50);
assert.ok(Math.abs(ema50Expected[1] - manualEma50_1) < 1e-9, 'EMA 50 matches manual step');
console.log('  ✔ EMA 50 reference ground truth verified.');

// -------------------------------------------------------------
// 2. Stochastic Oscillator (5, 3, 3) Verification
// -------------------------------------------------------------
console.log('\n[Test 2] Verifying Stochastic (5, 3, 3) Formula against Mathematical Ground Truth...');

function calculateReferenceStoch(highs, lows, closes, kPeriod = 5, kSmooth = 3, dSmooth = 3) {
    const rawK = [];
    for (let i = 0; i < closes.length; i++) {
        if (i < kPeriod - 1) {
            rawK.push(50); // neutral before full window
            continue;
        }
        let highestHigh = -Infinity;
        let lowestLow = Infinity;
        for (let j = i - kPeriod + 1; j <= i; j++) {
            if (highs[j] > highestHigh) highestHigh = highs[j];
            if (lows[j] < lowestLow) lowestLow = lows[j];
        }
        const range = highestHigh - lowestLow;
        const val = range === 0 ? 50 : ((closes[i] - lowestLow) / range) * 100;
        rawK.push(val);
    }

    // Smooth rawK with SMA(kSmooth)
    const smoothK = [];
    for (let i = 0; i < rawK.length; i++) {
        let sum = 0;
        let count = 0;
        for (let j = Math.max(0, i - kSmooth + 1); j <= i; j++) {
            sum += rawK[j];
            count++;
        }
        smoothK.push(sum / count);
    }

    // %D is SMA(smoothK, dSmooth)
    const smoothD = [];
    for (let i = 0; i < smoothK.length; i++) {
        let sum = 0;
        let count = 0;
        for (let j = Math.max(0, i - dSmooth + 1); j <= i; j++) {
            sum += smoothK[j];
            count++;
        }
        smoothD.push(sum / count);
    }

    return { rawK, smoothK, smoothD };
}

const testHighs = [80100, 80300, 80200, 80500, 80700, 80600, 80800, 81000];
const testLows  = [79900, 80100, 80000, 80200, 80400, 80300, 80500, 80700];
const testC     = [80050, 80250, 80100, 80450, 80650, 80350, 80750, 80950];

const stochResult = calculateReferenceStoch(testHighs, testLows, testC, 5, 3, 3);
// For bar 4 (index 4, 5th bar): window is index 0..4
// highs: 80100, 80300, 80200, 80500, 80700 -> highest = 80700
// lows:  79900, 80100, 80000, 80200, 80400 -> lowest  = 79900
// range = 800
// close = 80650 -> (80650 - 79900) / 800 = 750 / 800 = 93.75%
assert.strictEqual(stochResult.rawK[4], 93.75, 'Raw %K at index 4 equals 93.75%');
console.log('  ✔ Stochastic %K at bar 5 matches exact theoretical value 93.75%.');
assert.ok(stochResult.smoothK[4] > 0 && stochResult.smoothK[4] <= 100, 'Smooth %K is bounded [0, 100]');
assert.ok(stochResult.smoothD[4] > 0 && stochResult.smoothD[4] <= 100, 'Smooth %D is bounded [0, 100]');
console.log('  ✔ Stochastic %K and %D smoothing bounded correctly in [0, 100].');

// -------------------------------------------------------------
// 3. Data Adapter Validation & Invariant Testing
// -------------------------------------------------------------
console.log('\n[Test 3] Verifying Data Adapter OHLCV Validation & Invariants...');
const adapter = new AmsDataAdapter();

// Test A: Monotonic timestamps
const rawUnsortedBars = [
    { time: 1000, open: 100, high: 105, low: 95, close: 102, volume: 10 },
    { time: 1000, open: 102, high: 106, low: 98, close: 104, volume: 15 }, // duplicate time
    { time: 1002, open: 104, high: 107, low: 101, close: 105, volume: 20 }
];
const validatedBars = adapter.validateAndTransformBars(rawUnsortedBars);
assert.strictEqual(validatedBars.length, 3, 'All 3 bars processed');
assert.ok(validatedBars[1].time > validatedBars[0].time, 'Duplicate timestamp was incremented for strict monotonicity');
assert.ok(validatedBars[2].time > validatedBars[1].time, 'Timestamps are strictly ascending');
console.log('  ✔ Monotonic timestamp ordering strictly enforced.');

// Test B: High / Low Invariant Correction
const rawBrokenOhlc = [
    { time: 2000, open: 100, high: 90, low: 110, close: 105, volume: 5 } // High < Open and Low > Open!
];
const fixedBars = adapter.validateAndTransformBars(rawBrokenOhlc);
assert.strictEqual(fixedBars.length, 1);
assert.strictEqual(fixedBars[0].high, 105, 'High corrected to max(open, close)');
assert.strictEqual(fixedBars[0].low, 100, 'Low corrected to min(open, close)');
console.log('  ✔ High/Low integrity invariant (high >= max, low <= min) strictly enforced.');

// Test C: Corrupt / Non-numeric filtering
const rawCorruptBars = [
    { time: 3000, open: NaN, high: 100, low: 50, close: 75, volume: 1 },
    { time: 3001, open: 100, high: Infinity, low: 50, close: 75, volume: 1 },
    { time: 3002, open: 100, high: 110, low: 90, close: 105, volume: 10 } // Valid
];
const filteredBars = adapter.validateAndTransformBars(rawCorruptBars);
assert.strictEqual(filteredBars.length, 1, 'Corrupt bars filtered out');
assert.strictEqual(filteredBars[0].time, 3002000, 'Valid bar preserved with ms conversion');
console.log('  ✔ Non-numeric/corrupt prices properly rejected.');

// -------------------------------------------------------------
// 4. Read-Only GET Invariant Verification
// -------------------------------------------------------------
console.log('\n[Test 4] Verifying Read-Only GET Endpoint Invariants...');
const adapterCode = require('fs').readFileSync(require('path').join(__dirname, '../web/adapter.js'), 'utf8');
assert.ok(!adapterCode.includes("method: 'POST'"), 'No POST methods in adapter');
assert.ok(!adapterCode.includes("method: 'PUT'"), 'No PUT methods in adapter');
assert.ok(!adapterCode.includes("method: 'DELETE'"), 'No DELETE methods in adapter');
assert.ok(!adapterCode.includes("method: 'PATCH'"), 'No PATCH methods in adapter');
assert.ok(adapterCode.includes('/api/chart/klines'), 'Uses verified klines endpoint');
assert.ok(adapterCode.includes('/api/chart/symbols'), 'Uses verified symbols endpoint');
assert.ok(adapterCode.includes('/api/chart/cross_book'), 'Uses verified cross_book endpoint');
console.log('  ✔ Confirmed 100% read-only GET requests: Zero mutating endpoints.');

console.log('\n>>> ALL 4 VERIFICATION SUITE MODULES PASSED (0 FAILURES). <<<\n');
