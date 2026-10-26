# Phase 1 Strategy Integration Audit

## Scope

This audit covers the LIVE_MICRO_V1 path only. No application process was started,
LIVE_AUTO was not authorized, and no exchange endpoint or order mutation was invoked.

## Configuration authority

`config/live_micro_v1.yaml` selects:

```text
live_strategy_id: 15M_SWEEP_RECLAIM_SMC
shadow_strategy_id: SWEEP_1M_V1
```

`src/scheduler/live_micro_runner.py:LiveMicroCoordinator.__init__` constructs the
15-minute sweep/reclaim implementation, the legacy EMA implementation, and the
1-minute shadow implementation. The configured live ID is now the selector used by
`run_5m_scan_cycle()`.

## Traced execution flow

```text
live_micro_v1.yaml
  -> LiveMicroCoordinator.config.strategy.live_strategy_id
  -> run_5m_scan_cycle() strategy trigger
  -> LiveAutoSessionManager._scanner_loop()
  -> GroqAutoTrader.run_full_pipeline(strategy_id=configured_live_strategy_id)
  -> run_stage3_finalist_and_order(strategy_id=same ID)
  -> evaluate_candidate_with_strategy(strategy_id=same ID)
  -> setup.strategy_id / lock / order metadata
  -> AutomatedTradeJournal strategy_id
  -> reconciliation and position management
```

The live session now verifies that the configured implementation exists, has the
same `strategy_id`, and is marked `is_live=True` before allowing entry generation.
The Groq full-pipeline and Stage 3 entry points repeat this authority check for live
venues, so a caller cannot pass the legacy strategy around the scanner guard.

## Legacy references

`EMA20_RECLAIM_5M_V1` remains in the repository for explicit compatibility,
historical records, and the case where configuration deliberately selects it. It is
not a silent fallback. `SWEEP_1M_V1` remains shadow-only and is rejected for live
execution. Existing journal rows are not relabeled.

## Findings

Before remediation, strategy authority was distributed across scanner and Groq
layers and the scheduler had an implicit fallback branch. The current configured
15M path was present in parts of the implementation, but there was no single
fail-closed check proving that every live entry used the configured implementation.

Position management, protective-order reconciliation, risk controls, and sizing were
not redesigned by this phase. Their existing paths remain in place.

