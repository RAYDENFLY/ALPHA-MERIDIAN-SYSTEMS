"""Strategy variant filter rules for Model B Long vs Short research.

Defines independent, deterministic rules for:
- Variant A: Current LONG baseline (evaluates LONG baseline signals)
- Variant B: Current SHORT baseline (evaluates SHORT baseline signals)
- Variant C: SHORT with verified 4H trend alignment
- Variant D: SHORT requiring completed-candle confirmation (red body + close < swing high)
- Variant E: SHORT with order-flow contradiction filtering (depth imbalance <= -5%)
- Variant F: Combined SHORT candidate using all validated conditions (C + D + E + ATR stop)
"""

from typing import Any, Dict, List, Optional, Tuple
import math
import numpy as np
import pandas as pd

from src.core.types import Candle, Direction
from src.research.types import EvaluationDecision, ResearchVariant


def compute_ema(series: List[float], period: int) -> float:
    """Compute Exponential Moving Average."""
    if len(series) < period:
        return series[-1] if series else 0.0
    return float(pd.Series(series).ewm(span=period, adjust=False).mean().iloc[-1])


def compute_atr(candles: List[Candle], period: int = 14) -> float:
    """Compute Average True Range from 1H candle series."""
    if len(candles) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(candles)):
        h = candles[i].high
        l = candles[i].low
        prev_c = candles[i - 1].close
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
    return float(np.mean(tr_list[-period:]))


class VariantRuleEngine:
    """Evaluates candidate signals against individual strategy variants."""

    @staticmethod
    def evaluate_variant(
        variant: ResearchVariant,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
        candles_1h: Optional[List[Candle]] = None,
        candles_4h: Optional[List[Candle]] = None,
    ) -> Tuple[EvaluationDecision, Optional[str], Dict[str, Any]]:
        """Evaluate a signal under a given variant.

        Returns:
            (decision, rejection_reason, adjusted_params)
            where adjusted_params contains any modified SL, TP, or sizing parameters.
        """
        ctx = context or {}
        adjusted = {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
        }

        # -------------------------------------------------------------
        # VARIANT DISPATCH:
        # -------------------------------------------------------------
        if variant == ResearchVariant.VARIANT_A_LONG_BASELINE:
            if direction.upper() == "LONG":
                return EvaluationDecision.ACCEPTED, None, adjusted
            return EvaluationDecision.REJECTED, "SIDE_MISMATCH: Variant A evaluates LONG baseline signals only", adjusted

        elif variant == ResearchVariant.VARIANT_B_SHORT_BASELINE:
            if direction.upper() == "SHORT":
                return EvaluationDecision.ACCEPTED, None, adjusted
            return EvaluationDecision.REJECTED, "SIDE_MISMATCH: Variant B evaluates SHORT baseline signals only", adjusted

        elif variant == ResearchVariant.VARIANT_C_SHORT_4H_TREND:
            if direction.upper() != "SHORT":
                return EvaluationDecision.REJECTED, "SIDE_MISMATCH: Variant C evaluates SHORT candidates only", adjusted
            return VariantRuleEngine._eval_4h_trend(entry_price, candles_4h, ctx, adjusted)

        elif variant == ResearchVariant.VARIANT_D_SHORT_CONFIRMED_CLOSE:
            if direction.upper() != "SHORT":
                return EvaluationDecision.REJECTED, "SIDE_MISMATCH: Variant D evaluates SHORT candidates only", adjusted
            return VariantRuleEngine._eval_confirmed_close(candles_1h, ctx, adjusted)

        elif variant == ResearchVariant.VARIANT_E_SHORT_ORDER_FLOW:
            if direction.upper() != "SHORT":
                return EvaluationDecision.REJECTED, "SIDE_MISMATCH: Variant E evaluates SHORT candidates only", adjusted
            return VariantRuleEngine._eval_order_flow(ctx, adjusted)

        elif variant == ResearchVariant.VARIANT_F_SHORT_COMBINED:
            if direction.upper() != "SHORT":
                return EvaluationDecision.REJECTED, "SIDE_MISMATCH: Variant F evaluates SHORT candidates only", adjusted

            # Evaluates 4H Trend, Completed Candle, Order Flow, and Volatility Stop in strict succession
            dec_trend, reason_trend, adj_trend = VariantRuleEngine._eval_4h_trend(entry_price, candles_4h, ctx, adjusted)
            if dec_trend != EvaluationDecision.ACCEPTED:
                return dec_trend, f"Combined Filter [4H Trend]: {reason_trend}", adjusted

            dec_close, reason_close, adj_close = VariantRuleEngine._eval_confirmed_close(candles_1h, ctx, adjusted)
            if dec_close != EvaluationDecision.ACCEPTED:
                return dec_close, f"Combined Filter [Candle Close]: {reason_close}", adjusted

            dec_flow, reason_flow, adj_flow = VariantRuleEngine._eval_order_flow(ctx, adjusted)
            if dec_flow != EvaluationDecision.ACCEPTED:
                return dec_flow, f"Combined Filter [Order Flow]: {reason_flow}", adjusted

            dec_vol, reason_vol, adj_vol = VariantRuleEngine._eval_volatility_stop(entry_price, stop_loss, take_profit_1, candles_1h, ctx, adjusted)
            if dec_vol != EvaluationDecision.ACCEPTED:
                return dec_vol, f"Combined Filter [Volatility Stop]: {reason_vol}", adjusted

            return EvaluationDecision.ACCEPTED, None, adj_vol

        return EvaluationDecision.UNAVAILABLE, "Unknown variant", adjusted

    @staticmethod
    def _eval_4h_trend(
        entry_price: float,
        candles_4h: Optional[List[Candle]],
        ctx: Dict[str, Any],
        adjusted: Dict[str, Any],
    ) -> Tuple[EvaluationDecision, Optional[str], Dict[str, Any]]:
        """Variant C: Verified 4H Market-Structure Alignment."""
        # 1. Check if 4H trend is provided in context
        bias_4h = ctx.get("bias_4h_true") or ctx.get("bias_4h")
        if bias_4h and bias_4h.upper() == "BULLISH":
            return (
                EvaluationDecision.REJECTED,
                f"HTF_BULLISH_TREND: 4H structure is Bullish (bias_4h={bias_4h})",
                adjusted,
            )

        # 2. Compute from 4H candles if provided
        if candles_4h and len(candles_4h) >= 5:
            closes = [c.close for c in candles_4h]
            ema20 = compute_ema(closes, min(20, len(closes)))
            last_close = closes[-1]
            if last_close > ema20:
                return (
                    EvaluationDecision.REJECTED,
                    f"HTF_BULLISH_TREND: 4H close ({last_close:.4f}) is above 4H EMA20 ({ema20:.4f})",
                    adjusted,
                )

        return EvaluationDecision.ACCEPTED, None, adjusted

    @staticmethod
    def _eval_confirmed_close(
        candles_1h: Optional[List[Candle]],
        ctx: Dict[str, Any],
        adjusted: Dict[str, Any],
    ) -> Tuple[EvaluationDecision, Optional[str], Dict[str, Any]]:
        """Variant D: Confirmed Closed-Candle Reclaim / Breakdown."""
        # Check pre-computed flags in context if candles not directly attached
        if "is_valid_reclaim_at_close" in ctx and not ctx["is_valid_reclaim_at_close"]:
            return (
                EvaluationDecision.REJECTED,
                "UNCONFIRMED_RECLAIM: 1H candle failed to close strictly below swing high",
                adjusted,
            )

        if "candle_body" in ctx and ctx["candle_body"] == "BULLISH_GREEN":
            return (
                EvaluationDecision.REJECTED,
                "BULLISH_CANDLE_BODY: 1H candle closed as a green bullish impulse",
                adjusted,
            )

        # Inspect candle series directly if present
        if candles_1h and len(candles_1h) >= 2:
            # Check confirmed closed candle (last completed bar)
            eval_c = candles_1h[-2] if ctx.get("is_live_stream", False) else candles_1h[-1]
            if eval_c.close >= eval_c.open:
                return (
                    EvaluationDecision.REJECTED,
                    f"BULLISH_CANDLE_BODY: 1H closed green ({eval_c.close:.4f} >= {eval_c.open:.4f})",
                    adjusted,
                )

            key_high = ctx.get("swing_high_price")
            if key_high and eval_c.close >= key_high:
                return (
                    EvaluationDecision.REJECTED,
                    f"UNCONFIRMED_RECLAIM: 1H close ({eval_c.close:.4f}) >= key swing high ({key_high:.4f})",
                    adjusted,
                )

        return EvaluationDecision.ACCEPTED, None, adjusted

    @staticmethod
    def _eval_order_flow(
        ctx: Dict[str, Any],
        adjusted: Dict[str, Any],
    ) -> Tuple[EvaluationDecision, Optional[str], Dict[str, Any]]:
        """Variant E: Order-Flow Confirmation without Contradictory Bid Support."""
        imb = ctx.get("imbalance_25bps")
        if imb is not None:
            # Positive depth imbalance means resting bids exceed asks
            if imb > 0.0:
                return (
                    EvaluationDecision.REJECTED,
                    f"CONTRADICTORY_BID_DEPTH: Order book depth imbalance is +{imb*100:.1f}% (net bid-heavy)",
                    adjusted,
                )
            if imb > -0.05:  # Less than 5% ask dominance (must be <= -5%)
                return (
                    EvaluationDecision.REJECTED,
                    f"WEAK_ASK_IMBALANCE: Order book depth imbalance is {imb*100:.1f}% (insufficient ask depth)",
                    adjusted,
                )

        return EvaluationDecision.ACCEPTED, None, adjusted

    @staticmethod
    def _eval_volatility_stop(
        entry_price: float,
        stop_loss: float,
        take_profit_1: float,
        candles_1h: Optional[List[Candle]],
        ctx: Dict[str, Any],
        adjusted: Dict[str, Any],
    ) -> Tuple[EvaluationDecision, Optional[str], Dict[str, Any]]:
        """Volatility-Aware Stop Placement (Minimum 1.2x ATR)."""
        atr_1h = ctx.get("atr_1h")
        if atr_1h is None and candles_1h:
            atr_1h = compute_atr(candles_1h, period=14)

        if not atr_1h or atr_1h <= 0:
            return EvaluationDecision.ACCEPTED, None, adjusted

        current_sl_dist = abs(stop_loss - entry_price)
        min_sl_dist = 1.2 * atr_1h

        target_sl_dist = max(current_sl_dist, min_sl_dist)
        new_sl = entry_price + target_sl_dist  # SHORT stop is above entry

        # Check feasibility with 10x leverage and $15.00 margin cap
        target_risk_usdt = 1.0  # Model B standard risk target
        quantity = target_risk_usdt / target_sl_dist if target_sl_dist > 0 else 0.0
        notional = quantity * entry_price
        required_margin = notional / 10.0

        if required_margin > 15.00:
            return (
                EvaluationDecision.REJECTED,
                f"INFEASIBLE_MARGIN_AT_1.2_ATR: Required margin ${required_margin:.2f} exceeds cap $15.00",
                adjusted,
            )

        if notional < 5.00:
            return (
                EvaluationDecision.REJECTED,
                f"INFEASIBLE_NOTIONAL_AT_1.2_ATR: Notional ${notional:.2f} below exchange minimum $5.00",
                adjusted,
            )

        # Sizing and stop adjustment accepted
        adjusted["stop_loss"] = round(new_sl, 6)
        adjusted["take_profit_1"] = round(entry_price - (target_sl_dist * 1.5), 6)
        adjusted["simulated_quantity"] = quantity
        adjusted["simulated_notional_usdt"] = round(notional, 4)
        adjusted["simulated_margin_usdt"] = round(required_margin, 4)

        return EvaluationDecision.ACCEPTED, None, adjusted
