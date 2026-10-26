# Four-Year Backtest Report

## Result

No valid four-year backtest of either requested strategy was claimed. The local dataset lacks the required four-year 15m/1h history, and the existing `BacktestEngine` does not execute `15M_SWEEP_RECLAIM_SMC`.

The repository does contain a separate one-year Model B proxy result, but it is not the current strategy and must not be substituted for it.

For context only, the existing `backtest_results/model_b_v1_v2_v3_comparison/master_portfolio_comparison.csv` reports V3 Tuned (not the current sweep implementation): 1,962 trades, 40.42% win rate, net PnL -201.31, profit factor 0.96. This is not a result for `15M_SWEEP_RECLAIM_SMC`.

Classification: `INCONCLUSIVE`, `DATA_QUALITY_FAILURE`, `IMPLEMENTATION_FAILURE`, `INSUFFICIENT_SAMPLE_SIZE` for the requested claim.
