# Phase 2.6 Historical Coverage and Data-Quality Audit

## Classification

`PASS_WITH_LIMITATIONS`

## Scope

This phase audited only the Research Data Foundation. The persisted-data audit is read-only and evaluates a requested dataset/date range without mutating observations or checkpoints.

Model B and `src/research/` were not inspected, modified, tested, or repaired.

## Validation contract

`audit_dataset()` reports coverage and quality separately. It returns source-specific expected dates, missing dates, unexpected dates, coverage ratio, and violations for invalid dates or non-finite numeric values.

Treasury uses weekdays minus explicitly supplied holidays. CFTC uses weekly report dates. Unknown datasets do not receive an invented schedule or coverage ratio.

## Invariants

- Existing observation uniqueness remains enforced by the database constraint.
- Repeated ingestion remains idempotent.
- Observation dates remain distinct from `retrieved_at` and `created_at`.
- Invalid numeric values are reported as `INVALID` and do not count as valid quality evidence.
- Coverage does not imply freshness or synchronization success.
- Audits are read-only.

## Evidence

The audit tests use isolated local SQLite databases and deterministic fixtures. No production database or live source response was audited. Live Treasury and CFTC compatibility remains unverified.

## Limitations

- The current SQLite uniqueness constraint prevents duplicate rows from being stored, so duplicate identity violations can only be inferred from rejected inserts rather than audited after persistence.
- No authoritative historical Treasury holiday calendar is configured; holidays must be supplied explicitly.
- CFTC schedule validation remains a weekly report-date model and does not verify publication-time completeness.
- Historical coverage claims are limited to the requested local database range.

## Test evidence

```text
pytest -q tests/test_research_foundation.py tests/test_research_ingestion.py tests/test_research_live_sync.py tests/test_research_semantics.py tests/test_research_coverage.py tests/test_research_audit.py --disable-warnings
```

Model B was not inspected, modified, tested, or repaired as part of Phase 2.6.
