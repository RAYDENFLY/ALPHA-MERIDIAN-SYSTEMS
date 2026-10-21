"""Read-only LONG vs SHORT diagnostic metrics for the Model B paper-trading experiment.

Pure functions over journal rows (dicts). Nothing here places orders or mutates strategy state.

Ledger conventions verified against the journal:
  pnl_net = pnl_gross - fee_paid - funding_paid   (slippage_paid is already embedded in fill prices)
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

DIRECTIONS = ("LONG", "SHORT")
RECON_TOLERANCE = 1e-4


def dedupe_trades(trades: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Drop duplicate observations by trade_id and by (symbol, direction, entry_timestamp_ms)."""
    seen_ids, seen_keys, out, dups = set(), set(), [], 0
    for t in trades:
        tid = t.get("trade_id")
        key = (t.get("symbol"), t.get("direction"), t.get("entry_timestamp_ms"))
        if tid in seen_ids or key in seen_keys:
            dups += 1
            continue
        seen_ids.add(tid)
        seen_keys.add(key)
        out.append(t)
    return out, dups


def _f(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def max_drawdown(pnls_in_order: List[float]) -> float:
    peak = cum = dd = 0.0
    for p in pnls_in_order:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return round(dd, 4)


def direction_metrics(trades: List[Dict[str, Any]], direction: str) -> Dict[str, Any]:
    rows = [t for t in trades if (t.get("direction") or "").upper() == direction]
    closed = sorted(
        (t for t in rows if not t.get("is_open") and t.get("exit_timestamp_ms") is not None),
        key=lambda t: t["exit_timestamp_ms"],
    )
    open_rows = [t for t in rows if t.get("is_open")]
    nets = [_f(t.get("pnl_net")) for t in closed]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    n = len(closed)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    exits: Dict[str, int] = defaultdict(int)
    for t in closed:
        exits[t.get("exit_reason") or "UNKNOWN"] += 1
    missing_funding = sum(1 for t in closed if t.get("funding_data_missing"))
    recon_errors = sum(
        1
        for t in closed
        if abs(_f(t.get("pnl_gross")) - _f(t.get("fee_paid")) - _f(t.get("funding_paid")) - _f(t.get("pnl_net"))) > RECON_TOLERANCE
    )
    warnings = []
    if n < 30:
        warnings.append(f"Small sample (n={n} < 30): metrics are not statistically meaningful.")
    if missing_funding:
        warnings.append(f"{missing_funding} closed trade(s) have funding_data_missing.")
    if recon_errors:
        warnings.append(f"{recon_errors} trade(s) fail pnl_net = gross - fees - funding reconciliation.")
    return {
        "direction": direction,
        "open_trades": len(open_rows),
        "closed_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / n * 100.0, 2) if n else None,
        "gross_realized_pnl": round(sum(_f(t.get("pnl_gross")) for t in closed), 4),
        "fees": round(sum(_f(t.get("fee_paid")) for t in closed), 4),
        "slippage_embedded_in_fills": round(sum(_f(t.get("slippage_paid")) for t in closed), 4),
        "funding": round(sum(_f(t.get("funding_paid")) for t in closed), 4),
        "net_realized_pnl": round(sum(nets), 4),
        "expectancy_per_trade": round(sum(nets) / n, 4) if n else None,
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "payoff_ratio": round(avg_win / abs(avg_loss), 3) if avg_loss else None,
        "max_drawdown": max_drawdown(nets),
        "exit_reason_distribution": dict(exits),
        "funding_data_missing_count": missing_funding,
        "ledger_reconciliation_errors": recon_errors,
        "warnings": warnings,
    }


_NUM = re.compile(r"[\d.,]+")


def normalize_reason(reason: str) -> str:
    """Collapse numbers so equivalent rejections group together."""
    return _NUM.sub("#", reason or "").strip()


def candidate_reasons(row: Dict[str, Any]) -> List[str]:
    raw = row.get("details_json")
    try:
        d = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        d = {}
    return list(d.get("rejection_reasons") or [])


def signal_stats(candidates: List[Dict[str, Any]], since_ms: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for direction in DIRECTIONS:
        rows = [
            c for c in candidates
            if (c.get("direction") or "").upper() == direction
            and (since_ms is None or int(c.get("timestamp_ms") or 0) >= since_ms)
        ]
        eligible, rejected = 0, 0
        reasons: Dict[str, int] = defaultdict(int)
        for c in rows:
            rs = candidate_reasons(c)
            if c.get("status") == "READY" and not rs:
                eligible += 1
            else:
                rejected += 1
                if rs:
                    for r in rs:
                        reasons[normalize_reason(r)] += 1
                else:
                    reasons[f"status={c.get('status')}"] += 1
        out[direction] = {
            "signals_total": len(rows),
            "eligible": eligible,
            "rejected": rejected,
            "rejection_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        }
    return out


def breaker_reconciliation(trades: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Re-derive the exact numbers the breaker uses, and the alternatives, for the current UTC day/week."""
    now = now or datetime.now(timezone.utc)
    day, week = now.strftime("%Y-%m-%d"), now.strftime("%Y-W%W")
    res = {"day": day, "week": week}
    for label, key, fmt in (("daily", day, "%Y-%m-%d"), ("weekly", week, "%Y-W%W")):
        gross_losses = wins = net = 0.0
        count = 0
        for t in trades:
            if t.get("is_open") or t.get("exit_timestamp_ms") is None:
                continue
            if datetime.fromtimestamp(t["exit_timestamp_ms"] / 1000.0, tz=timezone.utc).strftime(fmt) != key:
                continue
            p = _f(t.get("pnl_net"))
            count += 1
            net += p
            if p < 0:
                gross_losses += -p
            else:
                wins += p
        res[label] = {
            "closed_trades": count,
            "sum_of_losing_trades_used_by_breaker": round(gross_losses, 4),
            "sum_of_winning_trades_ignored_by_breaker": round(wins, 4),
            "net_realized_pnl": round(net, 4),
        }
    res["note"] = (
        "The breaker accumulates only the absolute value of losing trades (net of fees); winning trades never "
        "offset it. It is therefore a gross-loss meter, not a net-PnL meter. Unrealized PnL is not included."
    )
    return res


def build_report(
    trades: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    diagnostic_state: Dict[str, Any],
    cohort: str = "auto",
) -> Dict[str, Any]:
    """cohort: 'experiment' (entries since override start), 'baseline' (before), 'all', or 'auto'."""
    unique, dups = dedupe_trades(trades)
    start_ms = diagnostic_state.get("started_at_ms")
    if cohort == "auto":
        cohort = "experiment" if start_ms else "all"
    if cohort == "experiment" and start_ms:
        sel = [t for t in unique if int(t.get("entry_timestamp_ms") or 0) >= start_ms]
        since = start_ms
    elif cohort == "baseline" and start_ms:
        sel = [t for t in unique if int(t.get("entry_timestamp_ms") or 0) < start_ms]
        since = None
    else:
        sel, since = unique, None
    return {
        "cohort": cohort,
        "override_active": bool(diagnostic_state.get("active")),
        "override_started_at_utc": diagnostic_state.get("started_at_utc"),
        "duplicates_dropped": dups,
        "per_direction": {d: direction_metrics(sel, d) for d in DIRECTIONS},
        "signals": signal_stats(candidates, since_ms=since if cohort == "experiment" else None),
        "breaker_reconciliation": breaker_reconciliation(unique),
        "baseline_config": diagnostic_state.get("baseline_config"),
        "caveats": [
            "Cohort is separated by entry timestamp relative to the override start; no schema change was made.",
            "ATR and HTF source-candle timestamps are not stored per signal by the existing pipeline.",
            "Do not infer profitability or a defect from small samples.",
        ],
    }
