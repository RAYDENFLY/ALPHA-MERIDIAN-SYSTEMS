"""Model B directional regime and risk controls. All inputs are closed-bar data."""
from typing import Any, List, Optional, Tuple
from src.core.types import Candle, Direction, MarketBias, StructureState


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period: return None
    k = 2.0 / (period + 1); result = sum(values[:period]) / period
    for value in values[period:]: result = value * k + result * (1 - k)
    return result


def closed_context(candles_1h: List[Candle], candles_4h: Optional[List[Candle]], atr_period: int = 14, now_ms: Optional[int] = None, max_1h_age_ms: int = 2 * 60 * 60 * 1000, max_4h_age_ms: int = 8 * 60 * 60 * 1000):
    if not candles_1h or not candles_4h or len(candles_1h) < 50 or len(candles_4h) < 50:
        return None
    if any(not getattr(c, "is_closed", False) for c in candles_1h[-50:] + candles_4h[-50:]): return None
    if any(c.timestamp_ms >= candles_1h[i + 1].timestamp_ms for i, c in enumerate(candles_1h[:-1])): return None
    if any(c.timestamp_ms >= candles_4h[i + 1].timestamp_ms for i, c in enumerate(candles_4h[:-1])): return None
    if now_ms is not None:
        if now_ms - candles_1h[-1].close_time_ms > max_1h_age_ms or now_ms - candles_4h[-1].close_time_ms > max_4h_age_ms: return None
    c1 = candles_1h[-1]; c4 = candles_4h[-1]
    closes1 = [c.close for c in candles_1h]; closes4 = [c.close for c in candles_4h]
    e20 = ema(closes1, 20); e50 = ema(closes1, 50)
    trs = [max(c.high-c.low, abs(c.high-candles_1h[i-1].close), abs(c.low-candles_1h[i-1].close)) for i, c in enumerate(candles_1h) if i]
    atr = sum(trs[-atr_period:]) / atr_period if len(trs) >= atr_period else None
    bias = MarketBias.BULLISH if c4.close > ema(closes4, 20) and ema(closes4, 20) >= ema(closes4, 50) else MarketBias.BEARISH if c4.close < ema(closes4, 20) and ema(closes4, 20) <= ema(closes4, 50) else MarketBias.NEUTRAL
    structure = StructureState.BULLISH if c1.close > e20 and e20 >= e50 else StructureState.BEARISH if c1.close < e20 and e20 <= e50 else StructureState.SIDEWAYS
    return {"bias_4h": bias, "structure_1h": structure, "ema20_1h": e20, "ema50_1h": e50, "atr_1h": atr, "reference_1h_close_ms": c1.close_time_ms, "reference_4h_close_ms": c4.close_time_ms}


def short_gate(context: Optional[dict], cvd: Optional[float], ask_imbalance: Optional[float], min_imbalance: float, bullish_catalyst: bool) -> Tuple[bool, str]:
    if context is None: return False, "short_htf_context_unavailable"
    if context["bias_4h"] == MarketBias.BULLISH: return False, "short_blocked_4h_bullish"
    if context["structure_1h"] == StructureState.BULLISH: return False, "short_blocked_1h_bullish"
    if context["ema20_1h"] is None or context["ema50_1h"] is None: return False, "short_ema_unavailable"
    if context["ema20_1h"] > context["ema50_1h"]: return False, "short_ema_alignment_failed"
    if cvd is None or cvd >= 0: return False, "short_negative_cvd_required"
    if ask_imbalance is None or ask_imbalance > -abs(min_imbalance): return False, "short_ask_imbalance_required"
    if bullish_catalyst: return False, "short_bullish_catalyst_veto"
    return True, "short_regime_confirmed"


def enforce_stop_floor(entry: float, stop: float, direction: Direction, atr: Optional[float], min_pct: float, atr_multiple: float) -> float:
    distance = max(abs(entry-stop), entry * min_pct, (atr or 0.0) * atr_multiple)
    return entry - distance if direction == Direction.LONG else entry + distance


def cooldown_clear(journal: Any, symbol: str, direction: Direction, now_ms: int, cooldown_minutes: int, current_price: Optional[float] = None, atr: Optional[float] = None, min_displacement_atr: float = 0.5, model_id: str = "MODEL_B") -> Tuple[bool, str]:
    if journal is None: return False, "cooldown_journal_unavailable"
    cutoff = now_ms - cooldown_minutes * 60_000
    for trade in journal.get_trades(open_only=False, limit=500, model_id=model_id):
        if trade.get("symbol") != symbol or str(trade.get("direction", "")).upper() != direction.value: continue
        exit_ts = trade.get("exit_timestamp_ms")
        if trade.get("is_open") or (exit_ts and exit_ts >= cutoff):
            reason = str(trade.get("exit_reason", "")).upper()
            if trade.get("is_open"): return False, "same_direction_open"
            if reason in {"STOP_LOSS", "INVALIDATION", "LIQUIDATION", "SL_COLLISION"}:
                if current_price is not None and atr and atr > 0 and trade.get("exit_price") is not None and abs(current_price-float(trade["exit_price"])) < atr * min_displacement_atr:
                    return False, "reentry_displacement_not_met"
                return False, "post_stop_cooldown"
    return True, "cooldown_clear"
