# Phase 2.3 Research Data Quality Audit

## Classification

`PASS_WITH_LIMITATIONS` for deterministic offline research. Live source compatibility remains `UNVERIFIED`.

## Scope and boundary

Audited only `src/research_data/`, its research-specific SQLite schema, directly related API routes, and research-data tests. The existing `src/research/` subsystem was not inspected, modified, tested, or repaired.

## Source verification

### Treasury

- Endpoint configured in `src/research_data/http_sources.py` is the U.S. Treasury daily nominal-yield CSV endpoint.
- Parser expects `Date`, `2 Yr`, `5 Yr`, `10 Yr`, and `30 Yr` columns.
- Date-only observations are stored as ISO dates; publication timestamps remain `NULL`.
- Missing values (`N/A`, empty cells) are skipped rather than converted to zero.
- Deterministic CSV fixtures verify the supported column mapping.
- Live response retrieval was not performed in this audit; current endpoint/format compatibility remains unverified.

### CFTC

- Configured endpoint is the official CFTC public `FinFutWk.txt` location.
- Current parser expects CSV headers from the supported fixture format, including `Market_and_Exchange_Names`, `Report_Date_as_YYYY-MM-DD`, and `Open_Interest_All`.
- Supported categories are Dealer, Asset Manager, and Leveraged Funds.
- Net position is `long - short`.
- Publication timestamps are not fabricated.
- Invalid report dates are now rejected explicitly.
- Live response compatibility remains unverified because no external request was used in ordinary tests.

## Temporal integrity

- Observation dates are source dates, not retrieval times.
- `created_at` records persistence time separately.
- `last_success_at` records synchronization completion separately.
- Date-only observations do not claim intraday precision.
- Publication timestamps remain unavailable when the source does not provide them.
- Backfill checkpoints are advanced only after the observation insert transaction returns successfully.
- Potential limitation: the current schema does not store a dedicated `retrieved_at` field per observation; retrieval is represented by `created_at`.

## Data quality

- Missing numeric values remain absent.
- Invalid Treasury and CFTC dates are rejected.
- Invalid numeric fields are skipped by the parsers and do not become zero.
- Sync failures are recorded as `ERROR`; validation failures as `INVALID`.
- Backfill progress uses `completed_through` and does not advance after a failed chunk.
- Empty successful fetches currently can be recorded as `AVAILABLE`; this is a medium-severity limitation because no-new-data and empty-source responses are not yet distinct in sync state.
- Freshness is represented on observations, but dataset-level freshness is not independently recalculated from expected cadence. This remains a medium-severity limitation.

## Persistence integrity

- `research_observations` has a deterministic uniqueness constraint across dataset, market, symbol, observation timestamp, and payload hash.
- Replayed observations are idempotent.
- Source and dataset metadata are isolated from trading tables.
- Sync metadata is persisted separately.
- Observation count currently reflects inserted rows for a sync, not necessarily total rows in the dataset; consumers should not interpret it as a total historical count.

## API consistency

`GET /api/research/data/{dataset_id}` returns observations plus persisted sync metadata. Errors are returned as sanitized status fields rather than stack traces. The API does not expose credentials or authorization headers.

Remaining API limitation: dataset-level freshness and a dedicated partial-sync boolean are not yet derived independently; clients must use `sync_status` and checkpoint fields.

## Findings

| ID | Severity | Finding | Correction |
|---|---|---|---|
| RDA-001 | LOW | CFTC report dates were not validated | Implemented explicit ISO-date validation |
| RDA-002 | MEDIUM | Empty fetch and no-new-data states are not distinct | Documented; not changed in this narrow audit |
| RDA-003 | MEDIUM | Dataset freshness is not recalculated from source cadence | Documented; not changed |
| RDA-004 | MEDIUM | Live Treasury/CFTC response compatibility is unverified | Documented; no live request performed |
| RDA-005 | LOW | Observation retrieval time is represented by `created_at`, not a dedicated field | Documented; no schema expansion made |

## Test evidence

Command:

```text
pytest -q tests/test_research_foundation.py tests/test_research_ingestion.py tests/test_research_live_sync.py --disable-warnings
```

Result before this audit: 6 passed.

The added invalid-CFTC-date test is deterministic and uses no network. Live endpoint verification was not performed.

## Final classification

Suitable for further offline research, historical parser experiments, and isolated temporal experiments with the documented limitations. Not authorized for trading-strategy integration.

Model B was not inspected, modified, tested, or repaired as part of this phase.
