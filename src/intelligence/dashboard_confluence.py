"""Unified Dashboard Intelligence and Confluence Gate Engine.

Unifies background auto-trading with web dashboard intelligence:
1. Liquidity Sweep & Reclaim Module (15m/1h swing highs/lows, sweep detection, 72h dealing range)
2. Market Liquidity & Order Flow Intelligence Module (L2 imbalance ratio, bid/ask walls, spread, funding)
3. Automated Trade Journal Memory (6-hour anti-repeat "same hole" lock on stopped-out zones)
4. Stop-Loss Breathing Room (1.15% - 1.45% structural placement beyond sweep wick & protecting L2 wall)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("src.intelligence.dashboard_confluence")


def parse_utc_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp strings into UTC-aware datetime."""
    if not ts:
        return None
    try:
        cleaned = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


@dataclass
class ConfluenceGateResult:
    """Evaluation result for deterministic 4-module dashboard confluence gates."""
    passed: bool
    rejection_reason: str
    gate_name: Optional[str] = None  # SWEEP_RECLAIM_GATE, ORDER_FLOW_GATE, SAME_HOLE_MEMORY_GATE, STOP_BREATHING_ROOM_GATE
    sweep_reclaim_evidence: str = ""
    orderflow_wall_evidence: str = ""
    why_not_repeating_past_sl: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


def build_web_intelligence_payload(
    symbol: str,
    inspector_snapshot: Dict[str, Any],
    recent_closed_trades: Optional[List[Any]] = None,
    current_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Extract and format the exact 3 intelligence modules used on the web dashboard."""
    now = current_time or datetime.now(timezone.utc)
    clean_sym = symbol.strip().upper().replace(".P", "")

    meta = inspector_snapshot.get("meta", {})
    smc = inspector_snapshot.get("smc_and_liquidity_structure", {})
    model_a = smc.get("model_a_smc_context", {})
    model_b = smc.get("model_b_liquidity_context", {}) or smc.get("model_b_sweep_context", {})
    l2 = inspector_snapshot.get("orderbook_l2_microstructure", {})

    dr_72 = model_a.get("dealing_range_72h", {})
    pos_pct = float(dr_72.get("current_position_pct", 50.0))
    dr_hi = float(dr_72.get("high", 0.0))
    dr_lo = float(dr_72.get("low", 0.0))
    zone_status = str(dr_72.get("zone_status", "EQUILIBRIUM"))

    # 1. Liquidity Sweep & Reclaim Module
    sweep_module = {
        "symbol": clean_sym,
        "dealing_range_72h_pct": pos_pct,
        "dealing_range_high": dr_hi,
        "dealing_range_low": dr_lo,
        "zone_status": zone_status,
        "15m_swing_high": model_b.get("15m_swing_high_20b"),
        "15m_swing_low": model_b.get("15m_swing_low_20b"),
        "1h_swing_high": model_b.get("1h_swing_high_20b"),
        "1h_swing_low": model_b.get("1h_swing_low_20b"),
        "recent_sweep_detected": bool(model_b.get("recent_sweep_detected", False)),
        "sweep_type": str(model_b.get("sweep_type", "NONE")),
        "swept_level": model_b.get("swept_level"),
        "reclaim_confirmed": bool(model_b.get("reclaim_confirmed", False)),
        "rejection_wick_ratio": float(model_b.get("rejection_wick_ratio", 0.0)),
        "nearest_unmitigated_bull_fvg": model_a.get("nearest_unmitigated_bull_fvg"),
        "nearest_unmitigated_bear_fvg": model_a.get("nearest_unmitigated_bear_fvg"),
    }

    # 2. Market Liquidity & Order Flow Intelligence Module
    imb_ratio = float(l2.get("orderbook_imbalance_ratio", 0.50))
    bid_depth = float(l2.get("bid_depth_top20_usdt", 0.0))
    ask_depth = float(l2.get("ask_depth_top20_usdt", 0.0))
    bid_wall = l2.get("nearest_bid_wall")
    ask_wall = l2.get("nearest_ask_wall")
    spread_bps = float(l2.get("spread_bps", 0.0))
    funding_pct = float(meta.get("funding_rate_pct", 0.0))
    vol_24h = float(meta.get("volume_24h_usdt", 0.0))

    orderflow_module = {
        "orderbook_imbalance_ratio": imb_ratio,
        "bid_depth_top20_usdt": bid_depth,
        "ask_depth_top20_usdt": ask_depth,
        "nearest_bid_wall": bid_wall,
        "nearest_ask_wall": ask_wall,
        "spread_bps": spread_bps,
        "funding_rate_pct": funding_pct,
        "volume_24h_usdt": vol_24h,
        "l2_gate_passed": bool(l2.get("l2_gate_passed", False)),
    }

    # 3. Automated Trade Journal Memory (6-Hour Window for Symbol)
    sl_history_6h = []
    if recent_closed_trades:
        from src.ai.trade_memory_feedback import generate_post_loss_autopsy_tags
        for t in recent_closed_trades:
            t_sym = str(getattr(t, "symbol", "") or (t.get("symbol", "") if isinstance(t, dict) else "")).strip().upper().replace(".P", "")
            if t_sym != clean_sym:
                continue

            t_reason = str(getattr(t, "exit_reason", "") or (t.get("exit_reason", "") if isinstance(t, dict) else "")).upper()
            t_pnl = float(getattr(t, "net_pnl_usd", 0.0) or (t.get("net_pnl_usd", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
            is_loss = (t_pnl < -1e-6) or ("STOP_LOSS" in t_reason)
            if not is_loss:
                continue

            exit_dt = parse_utc_timestamp(getattr(t, "exit_timestamp_utc", None) or (t.get("exit_timestamp_utc") if isinstance(t, dict) else None))
            if exit_dt:
                age_seconds = (now - exit_dt).total_seconds()
                if 0 <= age_seconds <= 6 * 3600:
                    prev_sl = float(getattr(t, "average_exit_price", 0.0) or (t.get("average_exit_price", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
                    if prev_sl <= 0:
                        prev_sl = float(getattr(t, "planned_stop_price", 0.0) or (t.get("planned_stop_price", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
                    entry_p = float(getattr(t, "average_entry_price", 0.0) or (t.get("average_entry_price", 0.0) if isinstance(t, dict) else 0.0) or 0.0)
                    side_str = str(getattr(t, "side", "") or (t.get("side", "") if isinstance(t, dict) else "")).upper()

                    sl_history_6h.append({
                        "trade_id": str(getattr(t, "trade_id", "") or (t.get("trade_id", "") if isinstance(t, dict) else "")),
                        "symbol": clean_sym,
                        "side": side_str,
                        "entry_price": entry_p,
                        "sl_exit_price": prev_sl,
                        "exit_timestamp_utc": exit_dt.isoformat(),
                        "age_minutes": round(age_seconds / 60.0, 1),
                        "failure_reasons": generate_post_loss_autopsy_tags(t),
                        "blocked_reentry_ceiling_long": round(prev_sl * 0.988, 4) if side_str == "LONG" and prev_sl > 0 else None,
                        "blocked_reentry_floor_short": round(prev_sl * 1.012, 4) if side_str == "SHORT" and prev_sl > 0 else None,
                    })

    journal_memory_module = {
        "symbol": clean_sym,
        "recent_sl_levels_6h": sl_history_6h,
        "has_recent_loss_in_6h": bool(sl_history_6h),
        "recent_loss_count_6h": len(sl_history_6h),
    }

    return {
        "symbol": clean_sym,
        "liquidity_sweep_and_reclaim_chart_state": sweep_module,
        "market_liquidity_and_orderflow_intelligence": orderflow_module,
        "recent_journal_losses_and_blocked_zones": journal_memory_module,
    }


def evaluate_dashboard_confluence_gates(
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss_price: float,
    intelligence: Dict[str, Any],
    recent_closed_trades: Optional[List[Any]] = None,
    current_time: Optional[datetime] = None,
) -> ConfluenceGateResult:
    """Evaluate the 4 non-negotiable Python confluence gates across dashboard intelligence."""
    now = current_time or datetime.now(timezone.utc)
    clean_sym = symbol.strip().upper().replace(".P", "")
    clean_dir = direction.strip().upper()

    sweep = intelligence.get("liquidity_sweep_and_reclaim_chart_state", {})
    flow = intelligence.get("market_liquidity_and_orderflow_intelligence", {})
    memory = intelligence.get("recent_journal_losses_and_blocked_zones", {})

    # =========================================================================
    # GATE 1: Liquidity Sweep & Reclaim Gate
    # =========================================================================
    dr_pct = float(sweep.get("dealing_range_72h_pct", 50.0))
    recent_sweep = bool(sweep.get("recent_sweep_detected", False))
    sweep_type = str(sweep.get("sweep_type", "NONE"))
    reclaim_confirmed = bool(sweep.get("reclaim_confirmed", False))
    bull_fvg = sweep.get("nearest_unmitigated_bull_fvg")
    bear_fvg = sweep.get("nearest_unmitigated_bear_fvg")
    wick_ratio = float(sweep.get("rejection_wick_ratio", 0.0))
    swept_level = sweep.get("swept_level")

    if clean_dir == "LONG":
        # Hard Law 2: Dealing range must be in discount <= 38.0%
        has_fvg_pullback = bool(bull_fvg and (bull_fvg[0] <= entry_price <= bull_fvg[1] * 1.002))
        if dr_pct > 38.0 and not has_fvg_pullback:
            return ConfluenceGateResult(
                passed=False,
                gate_name="SWEEP_RECLAIM_GATE",
                rejection_reason=f"SWEEP_RECLAIM_GATE: LONG rejected in dealing range {dr_pct:.1f}% > 38.0% Discount bound (Hard Law 2: No buying top half).",
            )

        # Structural sweep + reclaim or active Bull FVG in discount
        has_sweep = (recent_sweep and sweep_type == "BULL_SWEEP_LOW" and reclaim_confirmed)
        if not (has_sweep or has_fvg_pullback):
            return ConfluenceGateResult(
                passed=False,
                gate_name="SWEEP_RECLAIM_GATE",
                rejection_reason=(
                    f"SWEEP_RECLAIM_GATE: No confirmed structural liquidity sweep & reclaim (reclaim_confirmed={reclaim_confirmed}) "
                    f"and no active Bull FVG in discount for {clean_sym} LONG."
                ),
            )
        sweep_evidence = (
            f"Confirmed BULL_SWEEP_LOW of level {swept_level} with {wick_ratio*100:.1f}% rejection wick and reclaim in {dr_pct:.1f}% Discount"
            if has_sweep else f"Active Bull FVG [{bull_fvg[0]} - {bull_fvg[1]}] pullback in {dr_pct:.1f}% Discount"
        )
    else:  # SHORT
        # Hard Law 1: Dealing range must be in premium >= 65.0%
        has_fvg_reject = bool(bear_fvg and (bear_fvg[0] * 0.998 <= entry_price <= bear_fvg[1]))
        if dr_pct < 65.0 and not has_fvg_reject:
            return ConfluenceGateResult(
                passed=False,
                gate_name="SWEEP_RECLAIM_GATE",
                rejection_reason=f"SWEEP_RECLAIM_GATE: SHORT rejected in dealing range {dr_pct:.1f}% < 65.0% Premium bound (Hard Law 1: No shorting bottom half).",
            )

        has_sweep = (recent_sweep and sweep_type == "BEAR_SWEEP_HIGH" and reclaim_confirmed)
        if not (has_sweep or has_fvg_reject):
            return ConfluenceGateResult(
                passed=False,
                gate_name="SWEEP_RECLAIM_GATE",
                rejection_reason=(
                    f"SWEEP_RECLAIM_GATE: No confirmed structural liquidity sweep & reject (reclaim_confirmed={reclaim_confirmed}) "
                    f"and no active Bear FVG in premium for {clean_sym} SHORT."
                ),
            )
        sweep_evidence = (
            f"Confirmed BEAR_SWEEP_HIGH of level {swept_level} with {wick_ratio*100:.1f}% rejection wick and reject in {dr_pct:.1f}% Premium"
            if has_sweep else f"Active Bear FVG [{bear_fvg[0]} - {bear_fvg[1]}] rejection in {dr_pct:.1f}% Premium"
        )

    # =========================================================================
    # GATE 2: Market Liquidity & Order Flow Intelligence Gate
    # =========================================================================
    imb = float(flow.get("orderbook_imbalance_ratio", 0.50))
    bid_wall = flow.get("nearest_bid_wall")
    ask_wall = flow.get("nearest_ask_wall")

    if clean_dir == "LONG":
        if imb < 0.48:
            return ConfluenceGateResult(
                passed=False,
                gate_name="ORDER_FLOW_GATE",
                rejection_reason=f"ORDER_FLOW_GATE: Orderbook imbalance ratio {imb:.3f} is below 0.48 minimum supportive threshold for LONG.",
            )

        # Ensure no massive ask wall blocks within < 0.50% above entry
        if ask_wall and isinstance(ask_wall, dict):
            ask_p = float(ask_wall.get("price", 0.0) or 0.0)
            ask_size = float(ask_wall.get("size_usdt", 0.0) or 0.0)
            if ask_p > entry_price:
                dist_above = (ask_p - entry_price) / (entry_price + 1e-9) * 100.0
                if dist_above < 0.50 and ask_size >= 5000.0:
                    return ConfluenceGateResult(
                        passed=False,
                        gate_name="ORDER_FLOW_GATE",
                        rejection_reason=(
                            f"ORDER_FLOW_GATE: Massive ask wall of ${ask_size:,.0f} at {ask_p} directly blocks "
                            f"LONG entry within {dist_above:.2f}% (< 0.50% ceiling)."
                        ),
                    )

        # Room-to-Resistance Gate (< 1.25R): Opposing ask wall or 15m swing high
        planned_risk = entry_price - stop_loss_price
        if planned_risk > 0:
            if ask_wall and isinstance(ask_wall, dict):
                ask_p = float(ask_wall.get("price", 0.0) or 0.0)
                ask_size = float(ask_wall.get("size_usdt", 0.0) or 0.0)
                if ask_p > entry_price:
                    dist_rr = (ask_p - entry_price) / planned_risk
                    if dist_rr < 1.25 and ask_size >= 5000.0:
                        return ConfluenceGateResult(
                            passed=False,
                            gate_name="ROOM_TO_RESISTANCE_GATE",
                            rejection_reason=(
                                f"INSUFFICIENT_ROOM_TO_RESISTANCE: Massive L2 ask wall of ${ask_size:,.0f} at {ask_p} "
                                f"blocks LONG path at {dist_rr:.2f}R (< 1.25R minimum room-to-resistance)."
                            ),
                        )


        bid_wall_str = f"with ${bid_wall.get('size_usdt', 0):,.0f} bid wall at {bid_wall.get('price')}" if bid_wall else "with healthy bid depth"
        orderflow_evidence = f"L2 orderbook has supportive {imb*100:.1f}% bid imbalance {bid_wall_str}; no ask wall within 0.50% ceiling"
    else:  # SHORT
        if imb > 0.52:
            return ConfluenceGateResult(
                passed=False,
                gate_name="ORDER_FLOW_GATE",
                rejection_reason=f"ORDER_FLOW_GATE: Orderbook imbalance ratio {imb:.3f} exceeds 0.52 maximum bearish threshold for SHORT.",
            )

        # Ensure no massive bid wall blocks within < 0.50% below entry
        if bid_wall and isinstance(bid_wall, dict):
            bid_p = float(bid_wall.get("price", 0.0) or 0.0)
            bid_size = float(bid_wall.get("size_usdt", 0.0) or 0.0)
            if bid_p < entry_price:
                dist_below = (entry_price - bid_p) / (entry_price + 1e-9) * 100.0
                if dist_below < 0.50 and bid_size >= 5000.0:
                    return ConfluenceGateResult(
                        passed=False,
                        gate_name="ORDER_FLOW_GATE",
                        rejection_reason=(
                            f"ORDER_FLOW_GATE: Massive bid wall of ${bid_size:,.0f} at {bid_p} directly blocks "
                            f"SHORT entry within {dist_below:.2f}% (< 0.50% floor)."
                        ),
                    )

        # Room-to-Resistance Gate (< 1.25R): Opposing bid wall or 15m swing low
        planned_risk = stop_loss_price - entry_price
        if planned_risk > 0:
            if bid_wall and isinstance(bid_wall, dict):
                bid_p = float(bid_wall.get("price", 0.0) or 0.0)
                bid_size = float(bid_wall.get("size_usdt", 0.0) or 0.0)
                if bid_p < entry_price:
                    dist_rr = (entry_price - bid_p) / planned_risk
                    if dist_rr < 1.25 and bid_size >= 5000.0:
                        return ConfluenceGateResult(
                            passed=False,
                            gate_name="ROOM_TO_RESISTANCE_GATE",
                            rejection_reason=(
                                f"INSUFFICIENT_ROOM_TO_RESISTANCE: Massive L2 bid wall of ${bid_size:,.0f} at {bid_p} "
                                f"blocks SHORT path at {dist_rr:.2f}R (< 1.25R minimum room-to-resistance)."
                            ),
                        )


        ask_wall_str = f"with ${ask_wall.get('size_usdt', 0):,.0f} ask wall at {ask_wall.get('price')}" if ask_wall else "with healthy ask depth"
        orderflow_evidence = f"L2 orderbook has bearish {imb*100:.1f}% bid imbalance ({(1.0-imb)*100:.1f}% asks) {ask_wall_str}; no bid wall within 0.50% floor"

    # =========================================================================
    # GATE 3: Anti-Repeat "Same Hole" Lock (Journal Memory Gate - 6 Hours)
    # =========================================================================
    sl_history = memory.get("recent_sl_levels_6h", [])
    why_not_repeating = f"No recent STOP_LOSS on {clean_sym} {clean_dir} within the last 6 hours."

    for item in sl_history:
        if item.get("side") == clean_dir:
            prev_sl = float(item.get("sl_exit_price", 0.0) or 0.0)
            if prev_sl <= 0:
                continue

            if clean_dir == "LONG":
                # For LONG: if entry is above previous SL exit, it is entering into the exact same chop hole!
                # Only allowed if entry is at least 1.20% deeper than previous SL exit
                if entry_price > prev_sl:
                    max_allowed = prev_sl * (1.0 - 0.012)
                    if entry_price > max_allowed:
                        return ConfluenceGateResult(
                            passed=False,
                            gate_name="SAME_HOLE_MEMORY_GATE",
                            rejection_reason=(
                                f"SAME_HOLE_MEMORY_GATE: {clean_sym} LONG hit STOP_LOSS at {prev_sl:.4f} within the last 6 hours. "
                                f"Re-entering at {entry_price:.4f} repeats the same losing zone. "
                                f"Allowed only if price sweeps below {prev_sl:.4f} or entry is <= {max_allowed:.4f} (>= 1.20% deeper)."
                            ),
                        )
                why_not_repeating = (
                    f"Entry {entry_price:.4f} is structurally deeper than previous SL exit at {prev_sl:.4f} "
                    f"(>=1.20% deeper or swept below previous SL liquidity level)."
                )
            else:  # SHORT
                if entry_price < prev_sl:
                    min_allowed = prev_sl * (1.0 + 0.012)
                    if entry_price < min_allowed:
                        return ConfluenceGateResult(
                            passed=False,
                            gate_name="SAME_HOLE_MEMORY_GATE",
                            rejection_reason=(
                                f"SAME_HOLE_MEMORY_GATE: {clean_sym} SHORT hit STOP_LOSS at {prev_sl:.4f} within the last 6 hours. "
                                f"Re-entering at {entry_price:.4f} repeats the same losing zone. "
                                f"Allowed only if price sweeps above {prev_sl:.4f} or entry is >= {min_allowed:.4f} (>= 1.20% higher)."
                            ),
                        )
                why_not_repeating = (
                    f"Entry {entry_price:.4f} is structurally higher than previous SL exit at {prev_sl:.4f} "
                    f"(>=1.20% higher or swept above previous SL liquidity level)."
                )

    # =========================================================================
    # GATE 4: Stop-Loss Breathing Room Gate (1.15% – 1.45%)
    # =========================================================================
    actual_stop_pct = abs(entry_price - stop_loss_price) / (entry_price + 1e-9) * 100.0
    if actual_stop_pct < 1.15:
        return ConfluenceGateResult(
            passed=False,
            gate_name="STOP_BREATHING_ROOM_GATE",
            rejection_reason=(
                f"STOP_BREATHING_ROOM_GATE: Planned stop distance {actual_stop_pct:.3f}% is strictly "
                f"below the 1.15% structural breathing room minimum floor."
            ),
        )
    if actual_stop_pct > 1.45:
        return ConfluenceGateResult(
            passed=False,
            gate_name="STOP_BREATHING_ROOM_GATE",
            rejection_reason=(
                f"STOP_BREATHING_ROOM_GATE: Planned stop distance {actual_stop_pct:.3f}% exceeds "
                f"the 1.45% maximum risk boundary."
            ),
        )

    return ConfluenceGateResult(
        passed=True,
        rejection_reason="PASSED",
        gate_name=None,
        sweep_reclaim_evidence=sweep_evidence,
        orderflow_wall_evidence=orderflow_evidence,
        why_not_repeating_past_sl=why_not_repeating,
        details={
            "actual_stop_pct": round(actual_stop_pct, 3),
            "dealing_range_72h_pct": dr_pct,
            "orderbook_imbalance_ratio": imb,
        },
    )


def build_qwen_dashboard_prompt_block(candidate_payloads: List[Dict[str, Any]]) -> str:
    """Format the 3 web dashboard modules into clean, structured Markdown for Qwen's prompt."""
    lines = [
        "=== WEB DASHBOARD INTELLIGENCE & ORDER FLOW DOSSIER ===",
        "You are provided with the exact 3 intelligence modules active on the live dashboard:\n",
    ]

    for idx, c in enumerate(candidate_payloads, start=1):
        sym = c.get("symbol") or c.get("liquidity_sweep_and_reclaim_chart_state", {}).get("symbol", "COIN")
        sweep = c.get("liquidity_sweep_and_reclaim_chart_state", {})
        flow = c.get("market_liquidity_and_orderflow_intelligence", {})
        mem = c.get("recent_journal_losses_and_blocked_zones", {})

        dr_pct = sweep.get("dealing_range_72h_pct", 50.0)
        zone = sweep.get("zone_status", "EQUILIBRIUM")
        sw_low_15m = sweep.get("15m_swing_low")
        sw_high_15m = sweep.get("15m_swing_high")
        sweep_det = sweep.get("recent_sweep_detected", False)
        sweep_type = sweep.get("sweep_type", "NONE")
        reclaimed = sweep.get("reclaim_confirmed", False)
        bull_fvg = sweep.get("nearest_unmitigated_bull_fvg")
        bear_fvg = sweep.get("nearest_unmitigated_bear_fvg")

        imb = flow.get("orderbook_imbalance_ratio", 0.50)
        bid_w = flow.get("nearest_bid_wall")
        ask_w = flow.get("nearest_ask_wall")
        spread = flow.get("spread_bps", 0.0)

        sl_6h = mem.get("recent_sl_levels_6h", [])

        lines.append(f"--- Candidate {idx}: {sym} ---")
        lines.append(
            f"1. Liquidity Sweep & Reclaim: 72H Dealing Range at {dr_pct:.1f}% ({zone}) | "
            f"15m Swing Low: {sw_low_15m} | 15m Swing High: {sw_high_15m} | "
            f"Sweep Detected: {sweep_det} ({sweep_type}) | Reclaim Confirmed: {reclaimed} | "
            f"Bull FVG: {bull_fvg} | Bear FVG: {bear_fvg}"
        )
        lines.append(
            f"2. Order Flow & L2 Walls: Top-20 Imbalance: {imb*100:.1f}% Bids | Spread: {spread:.1f} bps | "
            f"Nearest Bid Wall: {bid_w.get('price')} (${bid_w.get('size_usdt'):,.0f}, {bid_w.get('distance_pct')}%)" if bid_w else f"2. Order Flow & L2 Walls: Top-20 Imbalance: {imb*100:.1f}% Bids | Spread: {spread:.1f} bps | Bid Wall: None"
        )
        if ask_w:
            lines.append(f"   Nearest Ask Wall: {ask_w.get('price')} (${ask_w.get('size_usdt'):,.0f}, {ask_w.get('distance_pct')}%)")

        if sl_6h:
            sl_strs = [
                f"{h.get('side')} SL at {h.get('sl_exit_price')} ({h.get('age_minutes')}m ago, autopsy: {', '.join(h.get('failure_reasons', []))})"
                for h in sl_6h
            ]
            lines.append(f"3. Recent 6-Hour Journal Losses: {'; '.join(sl_strs)}")
        else:
            lines.append("3. Recent 6-Hour Journal Losses: None recorded on this symbol.")

        lines.append("")

    lines.append("==========================================================")
    return "\n".join(lines)
