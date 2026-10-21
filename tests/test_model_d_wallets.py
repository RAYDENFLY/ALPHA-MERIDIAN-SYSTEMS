import pytest
from src.model_d.wallets import WalletCandidate, WalletDiscoveryWorker, WalletRegistry, JsonRpcProvider, candidates_from_transfer_logs, merge_candidates, normalize_address, rank_candidate, wallet_id
from src.data.database import DatabaseManager


def test_address_normalization_and_chain_scoped_identity():
    address = '0x' + 'AB' * 20
    assert normalize_address(address) == address.lower()
    assert wallet_id(1, address) != wallet_id(56, address)


def test_deduplication_preserves_provenance():
    address = '0x' + '11' * 20
    records = [WalletCandidate('a', 1, address, ['rpc'], 'transfers', ['tx:1']), WalletCandidate('a', 1, address, ['explorer'], 'activity', ['tx:2'])]
    merged = merge_candidates(records)
    assert len(merged) == 1
    assert merged[0].discovery_sources == ['explorer', 'rpc']
    assert merged[0].source_event_references == ['tx:1', 'tx:2']


def test_missing_enrichment_does_not_receive_score():
    candidate = WalletCandidate('a', 1, '0x' + '22' * 20, [], 'seed', [])
    assert rank_candidate(candidate).ranking_score is None
    candidate.enrichment_status = 'ENRICHED'; candidate.transaction_count_observed = 50
    assert rank_candidate(candidate).ranking_score == 0.75


def test_invalid_address_rejected():
    with pytest.raises(ValueError): normalize_address('not-an-address')


def test_transfer_logs_are_bounded_and_provenance_backed():
    address = '11' * 20
    log = {'topics': ['0x' + 'ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef', '0x' + '0' * 24 + address, '0x' + '0' * 24 + ('22' * 20)], 'transactionHash': '0xtx', 'logIndex': '0x0'}
    records = candidates_from_transfer_logs(1, [log], 'rpc:test')
    assert len(records) == 2
    assert all(r.source_event_references == ['0xtx:0x0'] for r in records)


def test_checkpoint_persists_and_worker_resumes():
    class FakeProvider:
        def latest_block(self): return 100
        def logs(self, start, end, contract=None): return []
    from pathlib import Path
    import uuid
    registry = WalletRegistry(DatabaseManager(Path('data') / f'model_d_wallets_{uuid.uuid4().hex}.db'))
    worker = WalletDiscoveryWorker(registry, {1: FakeProvider()}, block_window=10)
    first = worker.scan_once(1); second = worker.scan_once(1)
    assert (first['from_block'], first['to_block']) == (91, 100)
    assert second['from_block'] == 101 and second['to_block'] == 100
    assert registry.checkpoint(1, 'public_rpc')['last_block'] == 100
