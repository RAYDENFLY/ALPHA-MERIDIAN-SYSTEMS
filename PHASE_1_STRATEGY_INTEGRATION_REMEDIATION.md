# Phase 1 Strategy Integration Remediation

## Changes

### `src/execution/live_session.py`

- Added `LiveAutoSessionManager.get_configured_live_strategy_id()`.
- The scanner resolves the configured strategy and blocks LIVE_AUTO entry generation
  if the configuration is missing, unsupported, or its implementation is unavailable
  or not live-authorized.
- The scanner passes the resolved ID into `GroqAutoTrader.run_full_pipeline()`.
- Session status exposes `configured_live_strategy_id` and its validation status.
- Updated the module invariant text to describe configuration authority.

### `src/ai/groq_autotrader.py`

- Added `_configured_live_strategy_id()` as the live execution authority check.
- `run_full_pipeline()` and `run_stage3_finalist_and_order()` reject a live request
  whose strategy ID differs from the configured implementation.
- Stage 3 candidate evaluation and output/journal metadata retain the same strategy
  ID passed through the pipeline.
- Runtime cycle telemetry records the strategy ID.
- Removed stale EMA-specific descriptions from the live Stage 3 path; the prompt is
  parameterized by the selected strategy ID.

### `src/scheduler/live_micro_runner.py`

- `run_5m_scan_cycle()` selects the configured strategy implementation.
- Missing or unsupported strategy configuration now raises a safe failure instead of
  falling through to EMA.
- Strategy IDs continue to flow into global-lock and executable-signal metadata.

### `src/api/app.py`

- `/api/status` scanner data now exposes the configured live strategy ID and authority
  status alongside existing cycle telemetry.

### `tests/test_phase1_strategy_integration.py`

- Added offline tests for configured selection, unavailable/unsupported strategy
  rejection, live legacy mismatch rejection before pipeline dispatch, and rejection
  of a non-live implementation.

## Safety behavior

An invalid or unavailable configured strategy blocks new live entries. Existing
position management and exchange reconciliation are not disabled by this check.
No strategy parameters, leverage, sizing, risk budget, SL/TP prices, or historical
journal rows were changed.

## Remaining runtime requirement

The application was intentionally not started. A normal controlled deployment must
reload the code and inspect `/api/status` and the live-session status before any
operator authorization. Runtime confirmation that the loaded process uses
`15M_SWEEP_RECLAIM_SMC` remains pending until that safe verification is performed.

