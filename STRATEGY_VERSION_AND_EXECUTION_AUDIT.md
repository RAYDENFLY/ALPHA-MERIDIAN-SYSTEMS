# Strategy Version and Execution Audit

Date: 2026-10-26 UTC

## Finding

The configured LIVE_MICRO_V1 strategy is `15M_SWEEP_RECLAIM_SMC` (`config/live_micro_v1.yaml`). `LiveMicroCoordinator` constructs `SweepReclaim15MStrategy` and marks it live.

The active Groq LIVE_AUTO execution path is different. `src/execution/live_session.py:_scanner_loop()` calls `GroqAutoTrader.run_full_pipeline()` and hard-codes `strategy_id="EMA20_RECLAIM_5M_V1"`. `src/ai/groq_autotrader.py:run_stage3_finalist_and_order()` then evaluates `EMA20_RECLAIM_5M_V1`; its live authorization also explicitly allows only that strategy. Therefore the configured sweep strategy is not connected to this LIVE_AUTO order path.

`_refresh_lock_from_exchange()` and restart recovery use `15M_SWEEP_RECLAIM_SMC` only as a lock attribution label. That does not prove that the strategy generated the entry.

## Historical labels

The `EMA20_RECLAIM_5M_V1` labels on live journal records are consistent with the actual Groq execution path, not merely a dashboard formatting issue. The ZEC record in `data/scanner.db` has `strategy_id=EMA20_RECLAIM_5M_V1`. This is evidence that the old path generated the trade.

## Dealing-range locks

Both `src/strategy/ema20_reclaim_5m.py` and `src/strategy/sweep_reclaim_15m.py` call `check_dealing_range_zone_lock()`. The Groq live path does not call the sweep strategy and instead uses the EMA strategy path, so the sweep strategy's locks are not applied to those entries.

## Classification

`IMPLEMENTATION_FAILURE`: configured and documented live strategy differ from the active LIVE_AUTO execution path. No production strategy code was changed during this audit.
