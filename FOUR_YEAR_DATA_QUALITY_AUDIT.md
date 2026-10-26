# Four-Year Data Quality Audit

Requested period: 2022-10-26 through 2026-10-26 UTC.

## Available local data

The repository contains SQLite OHLCV data in `data/scanner.db` and `data/backtest_15m_top10.db`. The data is exchange-style perpetual market data, but the local history is not four years.

- `data/backtest_15m_top10.db`: top-10 15m candles begin 2025-10-20 and end 2026-10-24; 1h data generally begins 2025-10-14.
- `data/scanner.db`: most 1h/4h pairs begin 2025-10-14; ZEC 15m begins 2026-08-25; XMR begins 2026-01-13; several symbols have only short 15m samples.
- BTC 1d has older history, but that is not sufficient for a 15m/1h perpetual strategy backtest.

Thus the requested four-year period cannot be evaluated from the supplied local dataset without fabricating or downloading missing history. No missing candles were fabricated.

## Integrity checks

The existing annual Model B report records zero gaps, zero duplicates, and zero invalid OHLCV values for its one-year dataset. That claim is not extended to the requested four-year period or to symbols/timeframes without coverage.

Funding data exists in the backtest database, but its coverage is not a four-year per-symbol archive. Listing dates, migrations, delistings, and Aster-vs-proxy venue equivalence were not inferable for the missing period.

## Classification

`DATA_QUALITY_FAILURE`: insufficient local coverage for the requested four-year test. Any one-year results must be labeled proxy/partial-period results.
