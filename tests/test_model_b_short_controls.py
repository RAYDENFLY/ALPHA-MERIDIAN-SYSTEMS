from types import SimpleNamespace
from src.core.types import Candle, Direction, MarketBias, StructureState
from src.strategy.model_b.risk_controls import short_gate, enforce_stop_floor, cooldown_clear, closed_context


def test_bullish_htf_short_is_rejected():
    context = {"bias_4h": MarketBias.BULLISH, "structure_1h": StructureState.BEARISH, "ema20_1h": 99.0, "ema50_1h": 100.0, "atr_1h": 1.0}
    ok, reason = short_gate(context, -100.0, -0.2, 0.05, False)
    assert not ok and reason == "short_blocked_4h_bullish"


def test_missing_context_is_not_bearish_confirmation():
    ok, reason = short_gate(None, -100.0, -0.2, 0.05, False)
    assert not ok and reason == "short_htf_context_unavailable"


def test_incomplete_and_stale_closed_context_is_unavailable():
    base = 1_700_000_000_000
    one_h = [Candle("BTCUSDT", "1h", base + i * 3600000, 100, 101, 99, 100, 10, base + i * 3600000 + 3599999, is_closed=True) for i in range(50)]
    four_h = [Candle("BTCUSDT", "4h", base + i * 14400000, 100, 101, 99, 100, 10, base + i * 14400000 + 14399999, is_closed=True) for i in range(50)]
    assert closed_context(one_h, four_h, now_ms=base + 50 * 14400000, max_1h_age_ms=3600000) is None
    one_h[-1].is_closed = False
    assert closed_context(one_h, four_h, now_ms=base + 50 * 3600000) is None


def test_stop_floor_uses_percentage_and_atr():
    stop = enforce_stop_floor(100.0, 99.8, Direction.SHORT, 2.0, 0.015, 1.25)
    assert stop == 102.5


def test_cooldown_and_displacement_are_persistent_journal_gates():
    class Journal:
        def get_trades(self, **kwargs):
            return [{"symbol": "ASTERUSDT", "direction": "SHORT", "is_open": False, "exit_timestamp_ms": 990_000, "exit_reason": "STOP_LOSS", "exit_price": 100.0}]
    journal = Journal()
    ok, reason = cooldown_clear(journal, "ASTERUSDT", Direction.SHORT, 1_000_000, 60, 100.1, 1.0, 0.5)
    assert not ok and reason == "reentry_displacement_not_met"
    ok, reason = cooldown_clear(journal, "ASTERUSDT", Direction.SHORT, 1_000_000, 60, 101.0, 1.0, 0.5)
    assert not ok and reason == "post_stop_cooldown"
