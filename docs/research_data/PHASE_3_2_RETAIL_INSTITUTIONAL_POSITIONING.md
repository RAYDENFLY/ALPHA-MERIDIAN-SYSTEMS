# Phase 3.2 Retail and Institutional Positioning Intelligence

## Classification

`PASS_WITH_LIMITATIONS`

## Implemented routes

- `market_participant`: overview
- `cot`: CFTC Commitment of Traders view
- `institutional_positioning`: CFTC-reported institutional/professional category view
- `retail_sentiment`: explicit unavailable state

## Source feasibility

| Source | Access | Status | Supported use |
|---|---|---|---|
| Persisted CFTC research dataset | Existing SQLite/API | Available in fixtures/local data | Report date, market, category, persisted net position |
| Binance retail long/short ratios | Not present in the verified current integration | Unavailable | No retail metric rendered |
| Bybit/Gate/MEXC/Aster retail ratios | No verified current adapter/schema | Unavailable | No retail metric rendered |
| CME detailed positioning | No verified free source integrated | Unavailable | No fabricated CME metrics |
| Crypto ETF flows/holdings | No verified free source integrated | Unavailable | No fabricated ETF metrics |

No paid API dependency or new live provider was introduced.

## Retail positioning

The Retail Sentiment page explicitly reports that no verified retail-positioning dataset is configured. Account ratios, position ratios, and trader identity are not inferred from other metrics.

## Institutional positioning

The Institutional Positioning view reuses the persisted CFTC dataset and displays report date, market identity, category, net position, unit, source dataset, and synchronization status. CFTC is weekly futures positioning and is not presented as all institutional crypto positioning or real-time data.

## Temporal safeguards

- Historical queries use observation/report dates.
- Future observations are excluded from historical ranges.
- No forward filling occurs.
- Publication timestamps remain nullable when not supplied.
- COT is not converted into fabricated daily observations.

## Testing

The feature uses local/fixture-backed data only. No live HTTP calls or OOS observations were used. Existing integration, persistence, audit, and foundation tests remain applicable.

## Limitations

Retail positioning, CME detail, and ETF flow integrations remain future dependencies pending verified free-source confirmation. Browser visual validation was not performed.

No OOS observations or metrics were consumed. Astel was not inspected or modified. Model B was not inspected, modified, tested, or repaired. No trading or execution code was changed.
