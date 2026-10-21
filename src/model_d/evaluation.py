"""Leakage-safe chronological comparison reports for Model D research."""
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class EvaluationObservation:
    signal_id: str
    timestamp_ms: int
    direction: str
    net_pnl: float
    strategy: str
    venue: str
    source_timestamp_ms: int
    wallet_context_available: bool = False


def _metrics(rows: List[EvaluationObservation], min_sample: int) -> Dict[str, Any]:
    if len(rows) < min_sample:
        return {"status": "INSUFFICIENT_DATA", "sample_size": len(rows), "net_expectancy": None, "drawdown": None}
    pnl = [r.net_pnl for r in rows]; equity = peak = drawdown = 0.0
    for value in pnl:
        equity += value; peak = max(peak, equity); drawdown = max(drawdown, peak - equity)
    return {"status": "OK", "sample_size": len(rows), "net_expectancy": sum(pnl) / len(pnl), "drawdown": drawdown}


def chronological_oos_report(observations: Iterable[EvaluationObservation], min_test_sample: int = 30) -> Dict[str, Any]:
    rows = sorted(observations, key=lambda r: (r.timestamp_ms, r.signal_id))
    unique = {}; duplicate_count = 0
    for row in rows:
        if row.signal_id in unique: duplicate_count += 1
        else: unique[row.signal_id] = row
    rows = list(unique.values())
    if not rows: return {"status": "INSUFFICIENT_DATA", "duplicate_count": duplicate_count, "strategies": {}}
    test_start = rows[max(0, int(len(rows) * 0.8))].timestamp_ms
    test = [r for r in rows if r.timestamp_ms >= test_start]
    strategies = {}
    for strategy in ("binance_orderflow", "binance_liquidity_amd", "binance_liquidity_amd_wallet"):
        selected = [r for r in test if r.strategy == strategy]
        strategies[strategy] = {"LONG": _metrics([r for r in selected if r.direction == "LONG"], min_test_sample),
                                "SHORT": _metrics([r for r in selected if r.direction == "SHORT"], min_test_sample)}
    return {"status": "OK" if any(v["LONG"]["status"] == "OK" or v["SHORT"]["status"] == "OK" for v in strategies.values()) else "INSUFFICIENT_DATA",
            "train_validation_boundary_ms": test_start, "test_sample_size": len(test), "duplicate_count": duplicate_count, "strategies": strategies}
