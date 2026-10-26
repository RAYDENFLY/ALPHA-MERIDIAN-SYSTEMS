# Backtest Engine Validation

`src/backtest/engine.py:BacktestEngine` is event-driven and processes timestamps chronologically. It evaluates open trades before new entries, uses historical slices through the current index, applies fees/slippage/funding, and uses conservative SL-first intrabar collision handling.

It is not a faithful engine for `15M_SWEEP_RECLAIM_SMC`:

- It loads 1h and 4h candles, not 15m execution candles.
- It calls `src.strategy.generator.SetupGenerator`, not `SweepReclaim15MStrategy`.
- It does not model the sweep strategy's 15m swing confirmation, wick ratio, RSI recovery, post-only limit semantics, or its 1.15%-1.40% stop rule.
- Its limit fill, partial-fill, and exact intrabar assumptions cannot validate the current 15m live implementation.

Existing Model B scripts are separate research implementations and are not evidence that the current `15M_SWEEP_RECLAIM_SMC` code was backtested.

Result: a research-only adapter/simulator is required before claiming a valid four-year current-strategy backtest. This audit did not alter production execution code.
