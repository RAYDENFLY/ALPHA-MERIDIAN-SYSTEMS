"""Deterministic Evidence and Traceable Setup Explanation Engine.

Generates explicit, auditable rationales based entirely on verified events
and quantitative features. No hallucination, no LLM dependency.
"""

from typing import List, Optional, Tuple
from src.core.types import Direction, FairValueGap, MarketBias, OrderBlock, SMCEvent, SnRZone, StructureState


class SetupExplainer:
    """Builds deterministic, auditable textual explanations and evidence for setups."""

    @staticmethod
    def build_explanation(
        symbol: str,
        direction: Direction,
        bias_4h: MarketBias,
        structure_1h: StructureState,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        invalidation_price: float,
        gross_rr: float,
        net_rr: float,
        recent_smc_events: List[SMCEvent],
        active_ob: Optional[OrderBlock],
        active_fvg: Optional[FairValueGap],
        nearest_support: Optional[SnRZone],
        nearest_resistance: Optional[SnRZone],
    ) -> Tuple[List[str], List[str], List[str], List[str], str]:
        """Construct structured evidence and rationale for candidate setup.

        Returns:
        --------
        Tuple[smc_evidence, snr_evidence, supporting_reasons, opposing_reasons, invalidation_reason]
        """
        smc_evidence: List[str] = []
        snr_evidence: List[str] = []
        supporting: List[str] = []
        opposing: List[str] = []

        # 1. SMC Evidence
        for ev in recent_smc_events[-4:]:
            smc_evidence.append(ev.description)

        if active_ob is not None:
            smc_evidence.append(
                f"Unmitigated {active_ob.direction.value} Order Block zone at [{active_ob.low:.4f} - {active_ob.high:.4f}]"
            )
        if active_fvg is not None:
            smc_evidence.append(
                f"Unmitigated {active_fvg.direction.value} Fair Value Gap at [{active_fvg.bottom:.4f} - {active_fvg.top:.4f}]"
            )

        # 2. SnR Evidence
        if nearest_support:
            snr_evidence.append(
                f"Nearest Support at {nearest_support.price_center:.4f} (zone: [{nearest_support.price_low:.4f} - "
                f"{nearest_support.price_high:.4f}], strength: {nearest_support.strength:.1f}, touches: {nearest_support.touch_count})"
            )
        if nearest_resistance:
            snr_evidence.append(
                f"Nearest Resistance at {nearest_resistance.price_center:.4f} (zone: [{nearest_resistance.price_low:.4f} - "
                f"{nearest_resistance.price_high:.4f}], strength: {nearest_resistance.strength:.1f}, touches: {nearest_resistance.touch_count})"
            )

        # 3. Supporting Reasons
        if (direction == Direction.LONG and bias_4h == MarketBias.BULLISH) or (
            direction == Direction.SHORT and bias_4h == MarketBias.BEARISH
        ):
            supporting.append(f"Full alignment with 4H {bias_4h.value} market bias.")
        else:
            opposing.append(f"4H Market Bias ({bias_4h.value}) is not strongly aligned with trade direction ({direction.value}).")

        if (direction == Direction.LONG and structure_1h == StructureState.BULLISH) or (
            direction == Direction.SHORT and structure_1h == StructureState.BEARISH
        ):
            supporting.append(f"1H market structure is confirmed {structure_1h.value}.")

        if net_rr >= 2.0:
            supporting.append(f"Favorable net reward-to-risk ratio of {net_rr:.2f}:1 exceeds baseline threshold.")
        elif net_rr < 1.5:
            opposing.append(f"Marginal net reward-to-risk ratio of {net_rr:.2f}:1 after exchange commissions and slippage.")

        # Invalidation Reason
        if direction == Direction.LONG:
            invalidation_reason = (
                f"Setup structurally invalidated if 1H candle closes below {invalidation_price:.4f} "
                f"(violating key swing low / order block foundation)."
            )
        else:
            invalidation_reason = (
                f"Setup structurally invalidated if 1H candle closes above {invalidation_price:.4f} "
                f"(violating key swing high / order block ceiling)."
            )

        return smc_evidence, snr_evidence, supporting, opposing, invalidation_reason
