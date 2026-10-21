# Model D operations

Model D is an independent, read-only AsterDEX perpetual-futures research surface. It collects public market data and maintains a separate in-memory shadow ledger; it never submits, modifies, or cancels exchange orders.

## Data flow

`AsterDEX public adapter -> ModelDCollector -> freshness states -> read-only API -> Model D dashboard`

The collector records exchange timestamps separately from local observation time. Executed trades, visible resting depth, and estimated liquidation context are separate concepts. Wallet attribution is currently `UNAVAILABLE` because no verified chain source is configured.

## Endpoints

- `GET /api/model_d/status?symbol=BTCUSDT` — configuration, safety invariants, and source freshness.
- `GET /api/model_d/liquidity?symbol=BTCUSDT` — current order-book/trade features and source states.
- `GET /api/model_d/signals?limit=50&offset=0` — bounded shadow-signal history.
- `GET /api/model_d/whale-events?limit=50&offset=0` — explicit unavailable state until event persistence is enabled.
- `GET /api/model_d/diagnostic/report` — isolated ledger summary and sample-size warning.

All endpoints are read-only. No Model D endpoint reaches the execution engine.

## Wallet discovery scanner

Wallet discovery is disabled by default and is independent of AsterDEX order-flow collection. Supported production chain IDs are Ethereum `1`, BNB Smart Chain `56`, Arbitrum One `42161`, and Base `8453`. The registry identity is `(chain_id, normalized_address)`, so the same address on two chains remains two records.

The current free-first slice supports configured public JSON-RPC calls for `eth_chainId`, `eth_blockNumber`, `eth_getBalance`, and bounded `eth_getLogs` transfer queries. Explorer/indexer adapters are not enabled by default. A configured provider must prove its chain ID before use. Missing historical indexing, quotas, and unavailable RPC URLs remain explicit unavailable states. The implementation does not claim 1,000 candidates until real, persisted, provenance-backed records reach that count.

Wallet endpoints:

- `GET /api/model_d/wallets/status` — enablement, target, per-chain counts, and provider status.
- `GET /api/model_d/wallets?limit=50&offset=0&chain_id=1` — bounded registry listing.

Wallet ranking is `wallet-heuristic-v1`; it is not profitability, whale, or futures-trader classification. Enrichment marked incomplete cannot receive a ranking score.

The resumable discovery slice stores per-chain/per-source checkpoints in `model_d_wallet_checkpoints`. It scans bounded ERC-20 Transfer log ranges, preserves transaction/log references, upserts by `(chain_id, normalized_address)`, and returns `NOOP` when the checkpoint is already at the provider head. It does not launch automatically and does not perform unbounded history scans.

Current limitations: documented explorer adapters, scheduled background scanning, full staged enrichment, infrastructure-label registries, and a verified 1,000-candidate scale run are not yet enabled. Public RPC availability and historical `eth_getLogs` coverage vary by provider. The current registry count must be treated as the actual result; no target completion is implied.

## Whale-following context

`actor-context-v1` classifies wallet evidence separately from market activity. It can identify an active-wallet candidate or infrastructure/protocol context, but it does not identify a profitable trader or an AsterDEX account. `orderflow-wallet-context-v1` uses observed AsterDEX executions, liquidity, and AMD confirmation for direction; wallet evidence is contextual only. Missing or stale market data returns `WAIT`. Wallet balance or transfer direction cannot create a LONG/SHORT signal.

## Configuration

Defaults are conservative:

```text
MODEL_D_ENABLED=false
MODEL_D_COLLECTOR_ENABLED=false
MODEL_D_WHALE_TRACKING_ENABLED=true
MODEL_D_WALLET_TRACKING_ENABLED=false
```

Enable paper research only after setting `SCANNER_MODE=PAPER` and reviewing source health. `paper_only` is hard-enforced in code and cannot be disabled by environment override. Wallet tracking must remain disabled until a documented, verifiable chain attribution source is added.

Source states mean:

- `LIVE`: timestamp is within the configured freshness threshold.
- `STALE`: a timestamp exists but exceeds that threshold.
- `UNAVAILABLE`: no valid source response exists or collection is disabled.
- `ERROR`: the source raised an error; the error is returned without internal credentials.

## Ledger semantics

The Model D ledger is separate from Model A/B journal records. It deduplicates signal IDs and accounts for configured fee and slippage assumptions. Net PnL is hypothetical shadow research, not actual execution PnL. Performance claims require adequate chronological observations; the API reports a warning while samples are insufficient.

## Verification

```powershell
pytest -q tests/test_model_d_core.py tests/test_navigation_tab_isolation.py tests/test_dashboard_js_syntax.py
pytest -q tests/test_model_b_strategy_and_risk.py
```

The API contract tests use `TestClient` without entering the application lifespan so they remain deterministic and offline. Entering the legacy lifespan performs live AsterDEX `exchangeInfo` discovery with retries; `tests/test_api.py` therefore remains dependent on external network availability and can time out before its first request. This is an existing test-infrastructure/runtime coupling, not a Model D endpoint failure.

## Rollback

Disable Model D with `MODEL_D_ENABLED=false` and `MODEL_D_COLLECTOR_ENABLED=false`, restart the service, and remove only the Model D tab/API files if a code rollback is required. Do not delete the shared SQLite database or alter Model A/B state.
