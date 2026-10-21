from types import SimpleNamespace
from src.model_d.features import AMDState, LiquidityFeatures
from src.model_d.whale_following import classify_wallet, fuse_orderflow_context
from src.model_d.wallets import WalletCandidate


def test_wallet_activity_is_context_not_trader_identity():
    candidate = WalletCandidate('id', 1, '0x' + '11' * 20, ['rpc'], 'transfer_counterparty', ['tx:0'], enrichment_status='ENRICHED', transaction_count_observed=20)
    evidence = classify_wallet(candidate)
    assert evidence.label == 'ACTIVE_WALLET_CANDIDATE'
    assert evidence.confidence is None


def test_stale_or_missing_orderflow_returns_wait():
    features = LiquidityFeatures(100.0, 1.0, 0.2, 100.0, 10.0, 50.0, 'OK')
    amd = AMDState('MANIPULATION', .7, 110, 90, 'BELOW', True)
    decision = fuse_orderflow_context(1000, features, amd, source_fresh=False)
    assert decision.direction == 'WAIT'
    assert decision.data_quality == 'UNAVAILABLE'


def test_wallet_context_does_not_override_contradictory_market_structure():
    features = LiquidityFeatures(100.0, 1.0, 0.2, 100.0, 10.0, 50.0, 'OK')
    amd = AMDState('ACCUMULATION', .6, 110, 90, None, False)
    wallet = classify_wallet(WalletCandidate('id', 1, '0x' + '22' * 20, ['rpc'], 'seed', [], enrichment_status='ENRICHED', transaction_count_observed=20))
    decision = fuse_orderflow_context(1000, features, amd, [wallet])
    assert decision.direction == 'WAIT'
    assert decision.wallet_context_score == .2
