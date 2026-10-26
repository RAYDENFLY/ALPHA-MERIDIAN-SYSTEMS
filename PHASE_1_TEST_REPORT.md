# Phase 1 Test Report

## Offline commands

```text
python -m compileall -q src tests/test_phase1_strategy_integration.py
pytest -q -p no:cacheprovider tests/test_phase1_strategy_integration.py tests/test_dealing_range_and_sweep_smc.py
```

## Results

```text
17 passed in 1.06s
```

The compile check completed successfully.

The broader `tests/test_live_auto_session.py` collection was attempted, but pytest
could not create its Windows temporary directory and failed during fixture setup with
`PermissionError: [WinError 5] Access is denied`. Those are environment setup errors,
not failed strategy assertions; no live session was started by the test run.

## Coverage added

- Configured `15M_SWEEP_RECLAIM_SMC` resolves to its implementation.
- Missing implementation fails closed.
- Unsupported configured strategy fails closed.
- A live request for `EMA20_RECLAIM_5M_V1` is rejected when 15M is configured,
  before Stage 1 or order dispatch.
- A configured implementation marked non-live fails closed.
- Existing dealing-range and sweep strategy tests remain green.

## Live-order safety

All tests are offline. No live order dispatch, cancellation, modification, position
change, LIVE_AUTO authorization, or application startup occurred.

