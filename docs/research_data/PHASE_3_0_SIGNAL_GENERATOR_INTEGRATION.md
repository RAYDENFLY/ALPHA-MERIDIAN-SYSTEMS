# Phase 3.0 Signal Generator Research Data Integration

## Classification

`PASS_WITH_LIMITATIONS`

## Scope

This phase adds a read-only integration contract for Signal Generator research consumers. It does not add strategy features, execute trades, consume OOS observations, or alter the OOS protocol scheduled through 2026-11-14.

## Integration contract

`src/research_data/integration.py` provides `ResearchDataAccess` over the existing SQLite store:

- `get_range(dataset_id, start, end)` returns deterministic, source-preserving observations.
- `readiness(dataset_id, start, end)` returns availability, quality, coverage, sync metadata, and an audit result as separate dimensions.
- Date boundaries are inclusive and use source observation timestamps.
- The contract is read-only and does not invoke source fetchers or schedulers.

No duplicate ingestion, storage, coverage, or audit subsystem was introduced.

## Temporal integrity

Observation dates remain separate from publication timestamps, `retrieved_at`, and `created_at`. Queries filter on `observation_timestamp`, so future observations cannot enter a historical range query. Missing observations are returned as empty results, not forward-filled. Unknown publication timing remains visible in the source record.

The contract does not claim point-in-time completeness because publication timestamps and authoritative calendars are incomplete for current sources.

## Dataset readiness

Readiness preserves independent dimensions:

- Availability: `AVAILABLE` or `MISSING`
- Quality: audit-derived `VALID` or `INVALID`
- Coverage: `COMPLETE`, `PARTIAL`, `MISSING`, or `UNKNOWN`
- Freshness: reserved for source-specific freshness assessment; unknown when no cadence is supplied

## Reproducibility workflow

1. Initialize an isolated SQLite database with `DatabaseManager`.
2. Insert deterministic source observations through `ResearchDataStore`.
3. Query with `ResearchDataAccess.get_range()`.
4. Run `readiness()` for the requested range.
5. Preserve the dataset ID, source URL, observation timestamps, quality, coverage, and freshness metadata in downstream research artifacts.

Ordinary tests use local SQLite fixtures and do not require live HTTP or OOS data.

## Limitations

- Live Treasury/CFTC compatibility remains unverified.
- Publication timestamps are often unavailable.
- Treasury holiday calendars remain explicitly supplied.
- The integration currently reports freshness as `UNKNOWN` unless a future source-specific cadence registry is supplied.
- No production or multi-worker validation was performed.

## OOS boundary

No OOS observations or metrics were consumed. No OOS boundary, collection rule, evaluation protocol, or reporting behavior was changed.

This work was limited to the Signal Generator project. Astel was not inspected or modified. Model B was not inspected, modified, tested, or repaired. No trading or execution code was changed.
