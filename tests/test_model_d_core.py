from src.model_d.ledger import ModelDShadowLedger
from src.model_d.types import DataAvailability


def test_model_d_ledger_deduplicates_and_separates_direction():
    ledger = ModelDShadowLedger(fee_rate=0.0, slippage_rate=0.0)
    ledger.record_signal("a", "BTCUSDT", "LONG", 100.0)
    ledger.record_signal("a", "BTCUSDT", "LONG", 101.0)
    ledger.record_signal("b", "BTCUSDT", "SHORT", 100.0)
    ledger.close("a", 102.0, "TP")
    ledger.close("b", 98.0, "TP")
    assert ledger.summary() == {"signals": 2.0, "closed": 2.0, "long_closed": 1.0, "short_closed": 1.0, "net_pnl": 4.0}


def test_model_d_data_states_are_explicit():
    assert {s.value for s in DataAvailability} == {"LIVE", "STALE", "UNAVAILABLE", "ERROR"}
