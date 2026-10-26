# Root Cause and Recommendation

## Root causes

1. The configured live strategy is `15M_SWEEP_RECLAIM_SMC`, but the active Groq LIVE_AUTO path hard-codes and authorizes `EMA20_RECLAIM_5M_V1`.
2. The local dataset does not cover the requested four years at the required 15m/1h resolutions.
3. The existing event-driven BacktestEngine is not a simulator for the current 15m sweep strategy.

## Recommendation

Do not make profitability or deployment claims from the current stored results. First resolve the live strategy wiring through a separately reviewed change, then build a research-only 15m/1h simulator with explicit closed-candle alignment, limit-fill, partial-fill, fee, funding, and intrabar collision rules. Acquire and validate complete venue-labeled history before running the chronological split and ablations.

Final classification: `IMPLEMENTATION_FAILURE`, `DATA_QUALITY_FAILURE`, `INCONCLUSIVE`, `INSUFFICIENT_SAMPLE_SIZE`.

LIVE deployment recommendation: do not treat the current strategy audit as evidence of a genuine edge; keep LIVE_AUTO disabled pending strategy-path reconciliation and valid OOS research.
