import pytest
from src.model_d.wallets import WalletCandidate, WalletDiscoveryWorker, WalletPipelineWorker, WalletRegistry, JsonRpcProvider, TRANSFER_TOPIC, candidates_from_transfer_logs, merge_candidates, normalize_address, rank_candidate, wallet_id
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


def test_pipeline_validates_persists_and_ranks(tmp_path):
    class PipelineProvider:
        def verify_chain(self): return True
        def latest_block(self): return 100
        def logs(self, start, end, contract=None):
            return [{'topics': [TRANSFER_TOPIC, '0x' + '0' * 24 + '1' * 40, '0x' + '0' * 24 + '2' * 40], 'transactionHash': '0xabc', 'logIndex': '0'}]
        def native_balance(self, address): return '0x10'
    registry = WalletRegistry(DatabaseManager(tmp_path / 'pipeline.db'))
    worker = WalletPipelineWorker(registry, {1: PipelineProvider()}, interval_seconds=60)
    result = worker.run_once()
    assert result['status'] == 'OK'
    assert result['discovered'] == 2
    assert result['enriched'] == 2
    assert result['ranked'] == 2
    assert registry.count_by_chain()[1] == 2
    assert registry.checkpoint(1, 'public_rpc')['last_block'] == 100


def test_pipeline_rejects_chain_mismatch_without_checkpoint(tmp_path):
    class BadProvider:
        def verify_chain(self): return False
        def latest_block(self): return 100
    registry = WalletRegistry(DatabaseManager(tmp_path / 'pipeline.db'))
    worker = WalletPipelineWorker(registry, {1: BadProvider()}, interval_seconds=60)
    result = worker.run_once()
    assert result['status'] == 'ERROR'
    assert result['discovered'] == 0
    assert result['chains']['1']['status'] == 'CHAIN_ID_MISMATCH'
    assert registry.checkpoint(1, 'public_rpc') is None


def test_partial_chain_failure_isolated_and_restart_is_idempotent(tmp_path):
    class GoodProvider:
        def verify_chain(self): return True
        def latest_block(self): return 100
        def logs(self, start, end, contract=None):
            return [{'topics': [TRANSFER_TOPIC, '0x' + '0' * 24 + '3' * 40, '0x' + '0' * 24 + '4' * 40], 'transactionHash': '0xgood', 'logIndex': '0'}]
        def native_balance(self, address): return '0x10'

    class FailingProvider:
        def verify_chain(self): return True
        def latest_block(self): return 200
        def logs(self, start, end, contract=None): raise RuntimeError('chain A unavailable')

    registry = WalletRegistry(DatabaseManager(tmp_path / 'partial.db'))
    registry.save_checkpoint(1, 'public_rpc', 50, block_hash='0xold')
    worker = WalletPipelineWorker(registry, {1: FailingProvider(), 56: GoodProvider()}, interval_seconds=60)
    first = worker.run_once()
    assert first['status'] == 'OK'
    assert first['chains']['1']['last_scan_status'] == 'ERROR'
    assert first['chains']['56']['last_scan_status'] == 'OK'
    assert registry.checkpoint(1, 'public_rpc')['last_block'] == 50
    assert registry.checkpoint(56, 'public_rpc')['last_block'] == 100
    assert registry.count_by_chain()[56] == 2

    second = worker.run_once()
    assert second['status'] == 'OK'
    assert registry.checkpoint(1, 'public_rpc')['last_block'] == 50
    assert registry.checkpoint(56, 'public_rpc')['last_block'] == 100
    assert registry.count_by_chain()[56] == 2


def test_confirmation_depth_records_hash_and_rewinds_on_reorg(tmp_path):
    class ReorgProvider:
        def __init__(self): self.hashes = {n: f'0x{n:064x}' for n in range(90, 101)}; self.reorg = False
        def latest_block(self): return 102
        def block_hash(self, number):
            return '0x' + ('f' * 64) if self.reorg and number == 100 else self.hashes.get(number)
        def logs(self, start, end, contract=None): return []

    provider = ReorgProvider()
    registry = WalletRegistry(DatabaseManager(tmp_path / 'reorg.db'))
    worker = WalletDiscoveryWorker(registry, {1: provider}, block_window=10, confirmation_depth=2)
    first = worker.scan_once(1)
    assert first['to_block'] == 100
    checkpoint = registry.checkpoint(1, 'public_rpc')
    assert checkpoint['last_block'] == 100
    assert checkpoint['last_block_hash'] == provider.hashes[100]
    provider.reorg = True
    second = worker.scan_once(1)
    assert second['from_block'] == 99
    assert registry.checkpoint(1, 'public_rpc')['last_block'] == 100
