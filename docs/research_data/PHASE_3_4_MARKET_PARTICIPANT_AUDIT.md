# Phase 3.4 — Market Participant Data Integration Audit

## Findings

### Medium — Retail loader replaced the entire card

The retail loader selected the first `.border` inside the retail tab. Because the card itself has that class, a refresh replaced the card container and heading. It now targets the dedicated `retailPositioningState` element only.

### Medium — Institutional-only navigation could throw a null-element error

The shared CFTC loader safely collected optional state elements, then unconditionally wrote to `cotState`. The write is now guarded so Institutional Positioning can refresh independently.

### Low — Retail observation freshness was not visible

The API preserved the provider timestamp, but the UI did not expose it. The retail panel now shows the latest observation timestamp alongside the provider and period. This is timestamp disclosure, not a claim that the provider is real-time.

### Low — CFTC cadence is correctly labeled but freshness is limited

The CFTC and Institutional Positioning panels label the data as weekly and report synchronization status. They do not present CFTC as real-time. A separate source-cadence freshness classifier is not attached to this dashboard view and remains a documented limitation.

## Source and metric audit

- Whale Activity is an order-book, trade-flow, and open-interest-derived market-intelligence summary. It is not labeled as confirmed wallet identity or confirmed whale capital.
- Derivatives intelligence uses venue market-data fields such as funding, open interest, basis, volume, and spreads. These are venue observations, not retail positioning.
- CFTC views use persisted weekly report-date observations and explicitly state that they are not real-time and do not represent all institutional crypto positioning.
- Bybit Account Long/Short Ratio uses holder account counts. It is explicitly labeled as a holder-count proxy, not position size and not all crypto retail traders.
- Missing, empty, provider-error, and unavailable responses remain explicit; no fallback values are fabricated.

## Tests and boundaries

All tests use fixtures or mocks. No live exchange, OOS, Astel, Model B, trading, execution, risk, or journal data was accessed for validation. No new provider or UI redesign was added.
