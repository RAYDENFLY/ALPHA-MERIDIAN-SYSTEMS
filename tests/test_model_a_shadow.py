from src.core.types import Candle
from src.strategy.generator import SetupGenerator
from src.strategy.model_a_shadow import aggregate_shadow_traces, trace_generator_timestamp


def candles(n=170):
    return [Candle("BTCUSDT", "1h", i * 3600000, 100.0, 110.0, 90.0, 100.0, 100.0, i * 3600000 + 3599999) for i in range(n)]


def test_equilibrium_short_circuits_downstream_predicates():
    row = trace_generator_timestamp(SetupGenerator(), "BTCUSDT", candles(), candles(50), 169)
    assert row.first_rejection_reason == "EQUILIBRIUM_ZONE_72H"
    assert row.predicates["equilibrium_zone"] == "FAIL"
    assert row.predicates["smc_confluence"] == "NOT_EVALUATED"
    assert row.predicates["reward_risk"] == "NOT_EVALUATED"


def test_shadow_aggregation_keeps_not_evaluated_separate():
    row = trace_generator_timestamp(SetupGenerator(), "BTCUSDT", candles(), candles(50), 169)
    report = aggregate_shadow_traces([row, row])
    assert report["rows"] == 2
    assert report["predicate_counts"]["smc_confluence"]["NOT_EVALUATED"] == 2
