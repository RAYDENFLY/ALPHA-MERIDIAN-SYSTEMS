"""Offline Model A predicate trace.

This module is deliberately separate from :mod:`src.strategy.generator`.
It does not feed production decisions, persist setups, or submit orders.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

from src.core.types import Candle
from src.strategy.generator import SetupGenerator


@dataclass
class ShadowTrace:
    symbol: str
    timestamp_ms: int
    predicates: Dict[str, str]
    values: Dict[str, Any]
    first_rejection_reason: Optional[str]
    production_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def trace_generator_timestamp(generator: SetupGenerator, symbol: str, candles_1h: List[Candle],
                              candles_4h: List[Candle], as_of_index_1h: int) -> ShadowTrace:
    """Trace the real generator's pre-screen at one closed 1H candle."""
    bar = candles_1h[as_of_index_1h]
    p = {"history_sufficiency": "NOT_EVALUATED", "data_quality": "NOT_EVALUATED",
         "equilibrium_zone": "NOT_EVALUATED", "short_eligibility": "NOT_EVALUATED",
         "long_discount_eligibility": "NOT_EVALUATED", "long_breakout_eligibility": "NOT_EVALUATED",
         "smc_confluence": "NOT_EVALUATED", "macro_regime": "NOT_EVALUATED",
         "reward_risk": "NOT_EVALUATED"}
    v: Dict[str, Any] = {"config": {"discount_threshold": generator.discount_threshold,
        "premium_threshold": generator.premium_threshold, "min_extension_48h": generator.min_extension_48h,
        "min_impulse_12h": generator.min_impulse_12h, "short_macro_cap_168h": generator.short_macro_cap_168h,
        "bull_breakout_pos_72h": generator.bull_breakout_pos_72h, "bull_breakout_ret_24h": generator.bull_breakout_ret_24h,
        "bull_breakout_ret_48h": generator.bull_breakout_ret_48h, "bull_breakout_ret_168h": generator.bull_breakout_ret_168h,
        "bull_variance_ratio_min": generator.bull_variance_ratio_min}}
    if as_of_index_1h + 1 < 170:
        p["history_sufficiency"] = "FAIL"
        return ShadowTrace(symbol, bar.timestamp_ms, p, v, "INSUFFICIENT_DATA", "INSUFFICIENT_DATA")
    p["history_sufficiency"] = "PASS"
    sub = candles_1h[max(0, as_of_index_1h + 1 - 170):as_of_index_1h + 1]
    if bar.close <= 0:
        p["data_quality"] = "FAIL"
        return ShadowTrace(symbol, bar.timestamp_ms, p, v, "INVALID_DATA", "NO_SETUP")
    p["data_quality"] = "PASS"
    window = sub[-72:]; hi, lo = max(x.high for x in window), min(x.low for x in window); span = hi - lo
    if span <= 1e-8:
        p["equilibrium_zone"] = "FAIL"
        return ShadowTrace(symbol, bar.timestamp_ms, p, v, "INSUFFICIENT_DATA", "NO_SETUP")
    pos = (bar.close - lo) / span
    ret48 = (bar.close - sub[-49].close) / sub[-49].close
    ret24 = (bar.close - sub[-25].close) / sub[-25].close
    ret12 = (bar.close - sub[-13].close) / sub[-13].close
    ret168 = (bar.close - sub[0].close) / sub[0].close
    v.update({"range_high_72h": hi, "range_low_72h": lo, "range_span_72h": span, "position_72h": pos,
              "return_48h": ret48, "return_24h": ret24, "return_12h": ret12, "return_168h": ret168,
              "candle_open": bar.open, "candle_close": bar.close})
    in_eq = generator.discount_threshold < pos < generator.premium_threshold
    p["equilibrium_zone"] = "FAIL" if in_eq else "PASS"
    short = pos >= generator.premium_threshold and ret48 >= generator.min_extension_48h and ret12 >= generator.min_impulse_12h and ret168 <= generator.short_macro_cap_168h and bar.close < bar.open
    discount = pos <= generator.discount_threshold and ret48 <= -generator.min_extension_48h and ret12 <= -generator.min_impulse_12h and ret168 >= 0.020 and bar.close > bar.open
    breakout = pos >= generator.bull_breakout_pos_72h and ret24 >= generator.bull_breakout_ret_24h and ret48 >= generator.bull_breakout_ret_48h and ret168 >= generator.bull_breakout_ret_168h and bar.close > bar.open
    p["short_eligibility"] = "PASS" if short else "FAIL"; p["long_discount_eligibility"] = "PASS" if discount else "FAIL"; p["long_breakout_eligibility"] = "PASS" if breakout else "FAIL"
    if in_eq: reason = "EQUILIBRIUM_ZONE_72H"
    elif not (short or discount or breakout): reason = "NO_SMC_CONFLUENCE"
    else: reason = None
    if reason:
        return ShadowTrace(symbol, bar.timestamp_ms, p, v, reason, "NO_SETUP")
    candidate = generator.generate_candidate(symbol, candles_1h, candles_4h, as_of_index_1h=as_of_index_1h)
    if candidate is None:
        diag = generator.last_diagnostic
        reason = diag.reason_code if diag else "NO_SMC_CONFLUENCE"
        if reason == "INSUFFICIENT_RISK_REWARD":
            p["smc_confluence"] = "PASS"; p["macro_regime"] = "PASS"; p["reward_risk"] = "FAIL"
        else:
            p["smc_confluence"] = "FAIL"
        return ShadowTrace(symbol, bar.timestamp_ms, p, v, reason, "NO_SETUP")
    for key in ("smc_confluence", "macro_regime", "reward_risk"): p[key] = "PASS"
    return ShadowTrace(symbol, bar.timestamp_ms, p, v, None, candidate.status.value)


def aggregate_shadow_traces(traces: Iterable[ShadowTrace]) -> Dict[str, Any]:
    rows = list(traces); reasons = Counter(); predicates: Dict[str, Counter] = {}; symbols: Dict[str, int] = {}
    for row in rows:
        if row.first_rejection_reason: reasons[row.first_rejection_reason] += 1
        symbols[row.symbol] = symbols.get(row.symbol, 0) + 1
        for name, state in row.predicates.items(): predicates.setdefault(name, Counter())[state] += 1
    return {"rows": len(rows), "symbols": symbols, "first_rejection_counts": dict(reasons), "predicate_counts": {k: dict(v) for k, v in predicates.items()}, "ready_count": sum(r.production_status == "READY" for r in rows)}
