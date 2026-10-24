# Phase 2.4 Sync Semantics and Freshness Integrity

## Classification

`PASS_WITH_LIMITATIONS` for deterministic offline research.

## Sync outcomes

- `SUCCESS_WITH_NEW_DATA`: valid source response and at least one inserted observation.
- `SUCCESS_NO_NEW_DATA`: valid response but all observations were duplicates or the response was empty.
- `PARTIAL`: reserved for multi-chunk operations where successful chunks are persisted but the requested range is incomplete.
- `ERROR`: transport, validation, or persistence failure.

Sync outcome is separate from dataset freshness.

## Freshness

`src/research_data/freshness.py` provides deterministic calculation with an injected reference time:

- no observation: `MISSING`
- unsupported cadence: `UNKNOWN`
- malformed observation timestamp: `INVALID`
- observation age within cadence plus documented publication lag: `FRESH`
- otherwise: `STALE`

The current adapters provide date-only observations and no fabricated publication timestamps. A source-specific cadence must be supplied by the caller; the system does not infer one from retrieval time.

## Retrieval metadata

`research_observations.retrieved_at` was added through a migration-safe column addition. New observations store UTC retrieval time. Existing rows retain `NULL`; their `created_at` meaning remains database insertion time and is not rewritten.

Observation timestamps continue to represent source observation dates, not retrieval or publication time.

## Counts

- `inserted_count` is the number of newly inserted rows for the most recent sync.
- `total_observation_count` is calculated from persisted rows using SQL aggregation.
- Duplicate replay does not increase total count.

## Limitations

- The API exposes sync outcome and counts, but source-specific cadence metadata is not yet registered in dataset metadata.
- Partial status is available for multi-chunk workflows but is not automatically inferred from every provider response.
- Live source correctness remains unverified.

Model B was not inspected, modified, tested, or repaired as part of Phase 2.4.
