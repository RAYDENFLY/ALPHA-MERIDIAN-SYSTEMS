/**
 * Alpha Meridian Systems — Vela Chart Read-Only Data Adapter
 *
 * Connects LuxAlgo Vela to the local FastAPI market data service.
 * Enforces strict read-only invariants:
 * - Only verified GET endpoints (/api/chart/klines, /api/chart/symbols, /api/chart/cross_book)
 * - Zero mutating actions, zero authentication/secret exposure
 * - Rigorous OHLCV validation: numeric types, chronological monotonicity, high/low invariants
 * - Request cancellation to avoid overlap or UI blocking
 */

class AmsDataAdapter {
    constructor(baseUrl = '') {
        this.baseUrl = baseUrl;
        this.activeAbortController = null;
        this.isPolling = false;
        this.pollTimer = null;
        this.lastCandleTime = 0;
    }

    /**
     * Fetch supported symbols and timeframes
     */
    async fetchSymbols() {
        const url = `${this.baseUrl}/api/chart/symbols`;
        const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (!res.ok) {
            throw new Error(`Failed to fetch symbols: HTTP ${res.status}`);
        }
        const data = await res.json();
        return {
            symbols: Array.isArray(data.symbols) ? data.symbols : ['BTCUSDT'],
            defaultSymbol: data.default_symbol || 'BTCUSDT',
            defaultTimeframe: data.default_timeframe || '1h',
            supportedTimeframes: Array.isArray(data.supported_timeframes) ? data.supported_timeframes : ['15m', '1h', '4h', '1d']
        };
    }

    /**
     * Fetch historical klines with input validation
     */
    async fetchKlines(symbol, timeframe, limit = 150) {
        if (this.activeAbortController) {
            this.activeAbortController.abort();
        }
        this.activeAbortController = new AbortController();

        const safeLimit = Math.max(10, Math.min(limit, 500));
        const url = `${this.baseUrl}/api/chart/klines?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=${safeLimit}`;

        const res = await fetch(url, {
            headers: { 'Accept': 'application/json' },
            signal: this.activeAbortController.signal
        });

        if (!res.ok) {
            throw new Error(`Failed to fetch klines: HTTP ${res.status}`);
        }

        const rawBars = await res.json();
        return this.validateAndTransformBars(rawBars);
    }

    /**
     * Validate and transform raw API candle array into Vela OHLCV[] format
     */
    validateAndTransformBars(rawBars) {
        if (!Array.isArray(rawBars) || rawBars.length === 0) {
            return [];
        }

        const bars = [];
        let previousTime = -1;

        for (let i = 0; i < rawBars.length; i++) {
            const raw = rawBars[i];

            // Resolve timestamp to epoch milliseconds
            let timeMs = 0;
            if (typeof raw.timestamp_ms === 'number' && Number.isFinite(raw.timestamp_ms)) {
                timeMs = raw.timestamp_ms;
            } else if (typeof raw.time === 'number' && Number.isFinite(raw.time)) {
                timeMs = raw.time < 10000000000 ? raw.time * 1000 : raw.time;
            } else {
                continue; // Skip malformed bar
            }

            // Ensure chronological monotonicity
            if (timeMs <= previousTime) {
                // Duplicate or backward timestamp; enforce ascending order
                timeMs = previousTime + 1;
            }
            previousTime = timeMs;

            const open = Number(raw.open);
            const high = Number(raw.high);
            const low = Number(raw.low);
            const close = Number(raw.close);
            const volume = typeof raw.volume !== 'undefined' ? Number(raw.volume) : 0;

            if (!Number.isFinite(open) || !Number.isFinite(high) || !Number.isFinite(low) || !Number.isFinite(close)) {
                continue; // Skip corrupt prices
            }

            // Invariant check: high >= max(open, close), low <= min(open, close)
            const safeHigh = Math.max(high, open, close);
            const safeLow = Math.min(low, open, close);

            bars.push({
                time: timeMs,
                open: open,
                high: safeHigh,
                low: safeLow,
                close: close,
                volume: Math.max(0, volume)
            });
        }

        // Sort ascending by time (Vela requirement)
        bars.sort((a, b) => a.time - b.time);
        return bars;
    }

    /**
     * Fetch order book cross-venue depth
     */
    async fetchOrderBook(symbol) {
        const url = `${this.baseUrl}/api/chart/cross_book?symbol=${encodeURIComponent(symbol)}`;
        const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (!res.ok) {
            throw new Error(`Failed to fetch order book: HTTP ${res.status}`);
        }
        const data = await res.json();
        return {
            symbol: data.symbol || symbol,
            midPrice: data.composite_mid || 0,
            spreadBps: data.venue_summaries?.asterdex?.spread_bps ?? 0.01,
            bids: Array.isArray(data.bids_buckets) ? data.bids_buckets.slice(0, 10).map(b => ({
                price: b.bucket_price,
                size: b.total_quantity,
                notional: b.total_notional_usdt,
                venues: b.venue_count || 1
            })) : [],
            asks: Array.isArray(data.asks_buckets) ? data.asks_buckets.slice(0, 10).map(a => ({
                price: a.bucket_price,
                size: a.total_quantity,
                notional: a.total_notional_usdt,
                venues: a.venue_count || 1
            })) : [],
            disclosure: data.distinction_disclosure || "ORDER BOOK DEPTH: Visible resting maker orders. DISTINCT from historical structural liquidity."
        };
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { AmsDataAdapter };
}
