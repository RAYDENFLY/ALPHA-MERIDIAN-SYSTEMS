# Phase 2.7 Operational Readiness and Reproducibility

## Classification

`PASS_WITH_LIMITATIONS`

## Scope

This review covers only `src/research_data/`, its research schema/API integration, deterministic tests, and research documentation. Model B and `src/research/` were not inspected, modified, tested, or repaired.

## Scheduler lifecycle

The scheduler is opt-in through `RESEARCH_DATA_SCHEDULER_ENABLED=true`; the default is disabled. FastAPI creates one scheduler instance during lifespan startup and awaits its cancellation during shutdown. Treasury and CFTC runs use independent dataset locks and failure results are isolated. Repeated `start()` calls do not create duplicate tasks.

Locks are process-local. Multiple application workers would each have independent locks and require deployment-level single-worker coordination or a future database-backed lock.

## Outcomes and checkpoints

`SUCCESS_WITH_NEW_DATA`, `SUCCESS_NO_NEW_DATA`, `ERROR`, and backfill `PARTIAL` remain distinct from freshness and coverage. Persistence occurs before a checkpoint is advanced. Retry exhaustion and validation failures retain explicit sync errors.

## Read-only audit

`GET /api/research/data/{dataset_id}/audit?start=YYYY-MM-DD&end=YYYY-MM-DD` performs only database reads. Reversed date ranges return a predictable 400 error. Unknown datasets report `UNKNOWN` coverage rather than fabricated completeness.

## Reproducible local audit

Run the Research Data Foundation tests against isolated SQLite fixtures:

```text
pytest -q tests/test_research_foundation.py tests/test_research_ingestion.py tests/test_research_live_sync.py tests/test_research_semantics.py tests/test_research_coverage.py tests/test_research_audit.py tests/test_research_operational_readiness.py --disable-warnings
```

This is local deterministic evidence only. No production database, live source, or multi-worker deployment was validated.

## Limitations

- Live Treasury/CFTC compatibility remains unverified.
- Process-local locking is not cross-process safe.
- No production shutdown or restart test was performed.
- Source-specific holiday calendars remain explicitly configured rather than authoritative.
