# Phase 3.3 — Free Retail Positioning Data Audit

## Classification

`PASS_WITH_LIMITATIONS`: one public source is usable as an exchange-account-holder proxy. It is not a market-wide retail positioning feed.

## Provider audit

| Provider | Official endpoint reviewed | Metric | Access | Historical depth | Decision |
| --- | --- | --- | --- | --- | --- |
| Bybit | `GET /v5/market/account-ratio` | Long and short account-holder ratios | Public market endpoint; no API key is shown in the official request | Documentation states queries can reach 2020-07-20 | Selected, with explicit holder-count methodology |
| Gate | Futures contract statistics | `lsr_taker` (taker long/short ratio) | Public endpoint | Not established as retail account history | Not selected: taker flow is not retail account positioning |
| MEXC | Futures API documentation reviewed | No verified public account-ratio endpoint found | Authentication is required for the documented private position path | Not established | Not selected |
| Aster | Repository and source audit | No verified public retail positioning endpoint | Not established | Not established | Not selected |
| Binance | Source audit for this phase | No verified public retail account-ratio endpoint selected | Not established | Not established | Not selected |

The Bybit metric is documented in the [official long-short ratio API reference](https://bybit-exchange.github.io/docs/v5/market/long-short-ratio). Gate’s documented futures statistics are described in the [official Gate API v4 futures documentation](https://www.gate.com/docs/developers/apiv4/en/futures/). The MEXC API material reviewed is the [official futures integration guide](https://www.mexc.io/api-docs/futures/integration-guide).

## Implementation

The backend exposes `/api/research/retail_positioning` and fetches Bybit’s public account-ratio data for a symbol, period, and bounded limit. Responses retain the source URL, metric definition, timestamps, and an explicit status. Empty responses, rate limits, transport errors, malformed payloads, and HTTP 418/429 are not converted into sentiment values.

A 60-second per-symbol/period/limit cache reduces duplicate public requests. The cache is a freshness optimization, not a claim that cached values are live. No provider averaging or synthetic market-wide retail series is introduced.

The dashboard labels the result as `Bybit · Account ratio` and states that it is holder-count data, not position size and not all crypto retail traders. If the source is unavailable, the panel remains explicitly unavailable.

## Boundaries

This phase does not alter CFTC/institutional data, Astel, OOS evaluation, Model A, Model B, scanner behavior, trading, execution, journals, risk settings, or paper safeguards. No live HTTP calls are used by tests.
