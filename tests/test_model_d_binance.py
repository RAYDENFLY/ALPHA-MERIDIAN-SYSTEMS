from src.model_d.binance import BinanceOrderBookSync, BinanceModelDCollector
from src.model_d.types import DataAvailability


def test_order_book_sequence_gap_requires_snapshot():
    book = BinanceOrderBookSync()
    book.apply_snapshot({'lastUpdateId': 10, 'bids': [['100', '1']], 'asks': [['101', '1']]})
    assert book.apply_update({'U': 12, 'u': 12, 'b': [], 'a': []}) is False
    assert book.needs_snapshot is True


def test_order_book_duplicate_and_contiguous_updates():
    book = BinanceOrderBookSync()
    book.apply_snapshot({'lastUpdateId': 10, 'bids': [['100', '1']], 'asks': [['101', '1']]})
    assert book.apply_update({'U': 10, 'u': 10, 'b': [['100', '2']], 'a': []}) is True
    assert book.apply_update({'U': 11, 'u': 11, 'b': [['100', '2']], 'a': []}) is True
    assert book.bids['100'] == '2'


def test_binance_collector_has_explicit_venue_and_no_aster_fallback():
    class FakeAdapter:
        def get_order_book(self, symbol, limit): return None
        def get_recent_trades(self, symbol, limit): return []
        def get_premium_index(self, symbol): return None
        def get_open_interest(self, symbol): return None
    collector = BinanceModelDCollector(adapter=FakeAdapter())
    result = collector.collect_symbol('BTCUSDT')
    assert result['exchange'] == 'binance'
    assert result['reference_venue'] is None
    assert result['sources']['order_book']['status'] == 'UNAVAILABLE'
