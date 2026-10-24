# Phase 3.5 — CFTC Freshness & Market Participant UI Validation

## Freshness model

CFTC freshness is derived from the latest persisted `observation_timestamp`, which is the weekly report date. Retrieval time is exposed separately as `sync.last_success_at` and is never used to make an old report appear fresh.

| State | Observation age | Meaning |
| --- | --- | --- |
| `FRESH` | 0–10 days | Within the expected weekly cadence plus publication tolerance |
| `AGING` | >10–21 days | Older than the normal weekly window but still within a bounded delay window |
| `STALE` | >21 days | Beyond two expected weekly cycles |
| `UNKNOWN` | Missing, malformed, or future date | Freshness cannot be determined safely |

The thresholds reflect weekly CFTC reporting and deliberately avoid treating retrieval time as observation time. CFTC remains labeled weekly and non-real-time.

## Changes

- Added `cftc_freshness()` with explicit missing, malformed, and future-date handling.
- Added CFTC `freshness`, `latest_observation_timestamp`, and `retrieved_at` fields to the existing research data response.
- Updated the existing CFTC and Institutional Positioning panels to show source, weekly/non-real-time methodology, freshness, observation date, and sync status.
- Preserved the Retail Positioning panel’s Bybit timestamp and holder-count methodology label.
- Preserved null-safe CFTC state handling and provider-failure isolation.

## Intelligence API test diagnosis

The existing intelligence API test exercises runtime market-data adapter calls. It is not fixture-isolated and can wait on external/runtime exchange paths, which caused the earlier hang. This phase did not alter production market-data behavior or disable the test. Deterministic research tests were run separately.

## Validation

- Deterministic research and Market Participant tests: `12 passed`.
- JavaScript syntax validation: passed.
- Python compilation: passed.
- No live API stress testing was performed.
- Browser visual validation was unavailable, so screenshot and visual interaction validation remain pending.

No Astel, Model A/B, OOS, strategy, execution, risk, journal, or paper-trading code was changed.
