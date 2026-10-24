# Historical Data, Chart, and Model A/B Architecture Audit

## Scope and safeguards

This audit precedes implementation work. The project remains paper-only; no strategy rules, paper records, execution journals, or forward cohorts were changed.

Verified safeguards:

- `config/default_config.yaml` keeps `live_trading_locked: true`.
- `config/settings.py` enforces Model D `paper_only=True`; Model A/B execution paths use paper execution services.
- No live order submission is authorized by this task.

## Existing historical-data architecture

The project already has a local SQLite historical candle system:

- `src/data/database.py` creates `raw_candles` keyed by symbol/timeframe/timestamp and maintains `candle_sync_metadata`.
- `src/data/downloader.py` performs initial and resumable historical downloads in exchange-sized batches.
- `src/data/sync.py` synchronizes recent closed candles and uses `INSERT OR IGNORE` deduplication.
- `src/data/validator.py` checks chronological order, OHLC consistency, timeframe spacing, and incomplete candles.
- AsterDEX adapters expose paginated klines; Binance and Hyperliquid adapters also expose candle retrieval.

Conclusion: SQLite is the current authoritative store and is already integrated with scanner/backtest code. Introducing a second Parquet store before measuring current workload would duplicate persistence and create consistency risk. The first implementation should extend metadata/integrity observability around this existing store; a Parquet export/cache can be evaluated separately after a benchmark.

Known risk requiring verification: the repository audit documentation records a historical `get_candles()` newest-vs-oldest limit defect. The current query implementation must be tested before relying on it for chart range loading.

## Existing chart architecture

`src/static/js/sweep_chart.js` uses TradingView Lightweight Charts. The current implementation includes:

- Candlestick and histogram volume series.
- Crosshair configuration.
- Resize handling.
- Native fullscreen with CSS fallback and fullscreen-change handlers.
- Timeframe and symbol controls.
- Historical event/level overlays and live liquidity data paths.
- EMA/Stochastic data paths.

The chart data is currently delivered through existing API endpoints and loaded into the browser. It is not yet a complete TradingView-style workstation: a persistent drawing model, configurable drawing toolbar, range-preserving incremental history loading, and viewport-aware overlay rendering are not fully verified in source.

## Model A/B audit surface

Model A is documented as a frozen defensive SMC/SnR forward cohort. Its strategy parameters must remain unchanged during the baseline audit.

Model B is an active paper research engine with independent risk, pending-order, lifecycle, journal, liquidity, and research-shadow components. The audit must keep completed trades, open positions, pending orders, and manual exchange positions in separate populations.

Existing evidence and limitations:

- `docs/forward_cohort_integrity.md` requires at least 30 completed forward trades before profitability claims.
- Existing reports identify the forward cohort as unproven/insufficient rather than profitable.
- `src/journal/repository.py` and `src/backtest/performance.py` provide the primary metric surfaces; Model B lifecycle and research data are additionally exposed through `src/model_b` and `src/research`.
- A defensible A/B report must calculate costs and direction splits from persisted records, not infer profitability from dashboard labels.

Read-only baseline snapshot from `data/scanner.db`:

- `raw_candles`: 120,343 rows — 96,283 1H and 24,060 4H candles.
- 1H range: 2025-10-14 15:00 UTC through 2026-10-22 17:00 UTC.
- `trade_journal`: 42 Model B rows — 25 LONG and 17 SHORT; net PnL approximately `-6.4093` USDT before any additional attribution work.
- Model B journal rows include 11 open rows and 31 closed rows; all 42 are paper journal records.
- Model B fees total approximately `1.5211` USDT; recorded funding is `0.0` in this snapshot.
- No Model A rows were present in `trade_journal`, so Model A profitability metrics cannot be estimated from this table.

This is a baseline observation, not a profitability claim. Directional, closed-only, pending-order, MAE/MFE, confidence interval, and leakage analyses still require a dedicated read-only audit query/report.

## Initial implementation boundary

1. Preserve the SQLite candle store and add only missing integrity/sync metadata or APIs after tests demonstrate the gap.
2. Improve chart controls and drawing persistence within Lightweight Charts; do not replace the engine.
3. Produce the Model A/B baseline from existing persisted data before changing any strategy or execution rule.
4. Report data insufficiency explicitly; do not manufacture confidence intervals from empty or tiny samples.

## Browser validation

No browser automation runtime is currently available in the execution environment. Visual chart and fullscreen validation is therefore pending until a browser-capable runtime is provided.
