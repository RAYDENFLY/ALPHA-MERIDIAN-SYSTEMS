# Phase 2.5 Source Verification and Coverage Audit

## Classification

`PASS_WITH_LIMITATIONS` for deterministic offline coverage analysis. Live source compatibility remains `LIVE_SOURCE_UNVERIFIED`.

## Source verification

### Treasury

The configured source is the official Treasury daily nominal-yield CSV endpoint. The parser expects `Date`, `2 Yr`, `5 Yr`, `10 Yr`, and `30 Yr`. Deterministic fixtures validate date parsing, tenor mapping, missing values, and numeric conversion. No live response was retrieved for this audit.

### CFTC

The configured source is the official CFTC public report endpoint. The current parser supports the existing fixture schema and Dealer, Asset Manager, and Leveraged Funds fields. Report dates are validated and publication dates remain unavailable when absent. Live compatibility is explicitly `LIVE_SOURCE_UNVERIFIED`.

## Coverage policy

- Treasury: expected dates are weekdays in the requested range minus an explicitly supplied holiday set. Weekends and holidays are not marked missing. No forward fill is performed.
- CFTC: expected dates use a documented weekly report-date anchor and are never interpreted as daily coverage. Publication delay is not converted into a fabricated timestamp.
- If the expected schedule cannot be justified, coverage is `UNKNOWN` and no ratio is returned.

Coverage states are `COMPLETE`, `PARTIAL`, `MISSING`, or `UNKNOWN`. Unexpected dates are reported separately and are not silently counted as expected observations.

## Temporal integrity

Observation dates remain source dates. `retrieved_at` remains collection time. Publication timestamps remain nullable. Freshness remains independent of sync outcome and is not changed by coverage assessment.

## Findings

- `RDA-025-001` — MEDIUM: live Treasury/CFTC response compatibility remains unverified. No live request was made.
- `RDA-025-002` — LOW: coverage policies require an explicit holiday set and CFTC anchor; this is intentional to avoid invented expectations.
- `RDA-025-003` — INFO: coverage assessment is pure and does not alter persisted observations or checkpoints.

## Test evidence

Coverage tests use deterministic dates and no external network. They verify weekend/holiday handling, partial coverage, unknown schedules, and empty ranges.

Model B was not inspected, modified, tested, or repaired as part of Phase 2.5.
