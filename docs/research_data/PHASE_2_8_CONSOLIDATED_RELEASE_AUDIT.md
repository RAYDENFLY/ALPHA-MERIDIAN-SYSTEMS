# Phase 2.8 Consolidated Research Data Release Audit

## Classification

`PASS_WITH_LIMITATIONS`

## Release decision

`READY_WITH_LIMITATIONS`

The Research Data Foundation is suitable as a dependency for the next isolated research phase using deterministic/local data and the documented single-process deployment scope. It is not approved as verified live-source or multi-worker production infrastructure.

## Scope and exclusions

Audited only the Research Data Foundation, directly related SQLite schema/API integration, research tests, and research documentation. Model B and `src/research/` were not inspected, modified, tested, or repaired.

## Consolidated findings

### Persistence and schema

- Research schema initialization is deterministic and migration-safe for the research tables.
- Observation uniqueness is enforced by the dataset/market/symbol/date/payload identity constraint.
- Repeated insertion is idempotent.
- `retrieved_at`, `created_at`, and source observation timestamps remain distinct.
- Total counts are SQL-derived; inserted counts are sync-specific.

Evidence: repository inspection and isolated SQLite tests.

### Temporal and quality semantics

- Invalid source dates are rejected.
- Treasury and CFTC source-date semantics remain distinct.
- Retrieval and persistence timestamps are not used as observation dates or publication timestamps.
- Freshness is independent of sync outcome.
- Coverage is independent of freshness and quality.
- Unknown schedules return `UNKNOWN`, not fabricated completeness.
- Invalid numeric evidence is reported as `INVALID`.

Evidence: unit tests and local SQLite audit tests.

### Ingestion and checkpoints

- Retries are bounded and distinguish transport from validation failures.
- Failed fetches and failed validation do not advance checkpoints.
- Replayed chunks remain idempotent.
- Duplicate-only ingestion returns `SUCCESS_NO_NEW_DATA`.
- New observations return `SUCCESS_WITH_NEW_DATA`.
- Partial backfill remains distinct from complete coverage.

Evidence: deterministic ingestion and persistence tests.

### Scheduler

- Scheduler is disabled by default and opt-in via `RESEARCH_DATA_SCHEDULER_ENABLED=true`.
- Repeated startup does not create duplicate tasks.
- Treasury/CFTC failures are isolated.
- Shutdown cancels and clears the scheduler task.
- Locks are process-local only.

Evidence: repository inspection and scheduler tests. No production or multi-worker test was performed.

### Read-only API

- Research observation and audit endpoints perform database reads only.
- Audit requests do not invoke fetchers or scheduler jobs.
- Reversed date ranges return a controlled error.
- Unknown datasets do not report complete coverage.

Evidence: code inspection and deterministic audit tests.

## Documentation consistency

Historical reports correctly identify their evidence limits. Earlier reports that state live compatibility was unverified remain historically accurate. Current operational documentation also states the process-local locking and opt-in scheduler assumptions.

## Relevant test suite

Command executed:

```text
pytest -q tests/test_research_foundation.py tests/test_research_ingestion.py tests/test_research_live_sync.py tests/test_research_semantics.py tests/test_research_coverage.py tests/test_research_audit.py tests/test_research_operational_readiness.py tests/test_research_coverage.py --disable-warnings
```

Result: `18 passed` collected because `test_research_coverage.py` was listed twice; this represents `15` unique tests, all passing.

No Model B tests were run.

## Evidence limitations

- Live Treasury/CFTC compatibility remains `LIVE_SOURCE_UNVERIFIED`.
- No production database was audited.
- No multi-worker deployment was validated.
- Treasury holiday handling requires an explicit holiday set; no authoritative calendar is configured.
- CFTC publication-time completeness is not verified.

## Downstream usage restrictions

The foundation may support the next isolated research phase for offline and fixture-backed work. Downstream consumers must preserve source, timestamp, freshness, coverage, and quality metadata. No research observation may drive trading configuration or execution without a separate future approval and audit.

No trading or execution code was changed. Model B was not inspected, modified, tested, or repaired as part of Phase 2.8.
