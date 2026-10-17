"""Deterministic Setup Entry Category Classifier.

Classifies setups into concrete categories based entirely on objective SMC/SnR evidence:
- Breakout
- Pullback
- Reversal
- Range/Mean Reversion
- Unclassified (fallback)

HARD SAFETY INVARIANTS:
1. Classification never fabricates categories from symbol names, trade direction (LONG/SHORT),
   or ML scores.
2. Every category follows explicit, verifiable market structure rules.
3. If structural evidence is absent, insufficient, or inconclusive, classification strictly
   falls back to `EntryCategory.UNCLASSIFIED`.
"""

from typing import List, Optional
from src.core.types import (
    Direction,
    EntryCategory,
    MarketBias,
    SMCEvent,
    SMCEventType,
    StructureState,
)


def classify_entry_category(
    direction: Direction,
    bias_4h: Optional[MarketBias] = None,
    structure_1h: Optional[StructureState] = None,
    smc_evidence: Optional[List[str]] = None,
    recent_events: Optional[List[SMCEvent]] = None,
    is_bull_expansion: bool = False,
    is_premium_exhaustion: bool = False,
    is_discount_rejection: bool = False,
    has_active_ob: bool = False,
    has_active_fvg: bool = False,
    snr_evidence: Optional[List[str]] = None,
    supporting_reasons: Optional[List[str]] = None,
) -> EntryCategory:
    """Classify the candidate setup entry into an explicit EntryCategory.

    Deterministic Classification Rules:
    -----------------------------------
    1. BREAKOUT:
       - Active regime structural expansion (`is_bull_expansion == True`), OR
       - Any recent SMC event is a BOS (Break of Structure), OR
       - Evidence text explicitly confirms BOS expansion / structural breakout.

    2. REVERSAL:
       - Any recent SMC event is a CHoCH (Change of Character) or LIQUIDITY_SWEEP, OR
       - Evidence text explicitly mentions CHoCH or Liquidity Sweep rejecting a level.

    3. PULLBACK:
       - Multi-timeframe trend alignment (4H bias or 1H structure aligned with direction, or explicit pullback/retest),
       - AND trade enters at an unmitigated Order Block (`has_active_ob`) or Fair Value Gap (`has_active_fvg`),
       - AND neither Breakout nor Reversal conditions are triggered.

    4. RANGE / MEAN REVERSION:
       - SMC Dealing Range boundary exhaustion/rejection (`is_premium_exhaustion` or `is_discount_rejection`), OR
       - Evidence text indicates Dealing Range rejection/exhaustion/consolidation without a structural BOS or CHoCH.

    5. UNCLASSIFIED:
       - Fallback when structural evidence is insufficient, contradictory, or empty.
    """
    all_texts = list(smc_evidence or []) + list(snr_evidence or []) + list(supporting_reasons or [])
    evidence = [str(e).upper() for e in all_texts]
    events = recent_events or []

    # Helper: Check if event matches
    has_bos_event = any(ev.event_type == SMCEventType.BOS for ev in events)
    has_choch_event = any(ev.event_type == SMCEventType.CHOCH for ev in events)
    has_sweep_event = any(ev.event_type == SMCEventType.LIQUIDITY_SWEEP for ev in events)

    # Helper: Text matching in structural evidence
    has_bos_text = any("BOS" in ev or "BREAK OF STRUCTURE" in ev or "EXPANSION" in ev or "BREAKOUT" in ev for ev in evidence)
    has_choch_text = any("CHOCH" in ev or "CHANGE OF CHARACTER" in ev for ev in evidence)
    has_sweep_text = any("SWEEP" in ev or "LIQUIDITY SWEEP" in ev for ev in evidence)
    has_ob_text = any("ORDER BLOCK" in ev or "OB" in ev for ev in evidence)
    has_fvg_text = any("FAIR VALUE GAP" in ev or "FVG" in ev for ev in evidence)
    has_pullback_text = any("PULLBACK" in ev or "RETEST" in ev for ev in evidence)
    has_range_text = any(
        "DEALING RANGE" in ev
        or "RANGE EXHAUSTION" in ev
        or "RANGE REJECTION" in ev
        or "MEAN REVERSION" in ev
        or "RANGE BOUNDARY" in ev
        or "RANGE HIGH" in ev
        or "RANGE LOW" in ev
        or "PREMIUM EXHAUSTION" in ev
        or "DISCOUNT REJECTION" in ev
        or "SIDEWAYS" in ev
        or "CONSOLIDATION" in ev
        for ev in evidence
    )

    # 1. Breakout Check
    if is_bull_expansion or has_bos_event or has_bos_text:
        return EntryCategory.BREAKOUT

    # 2. Reversal Check
    if has_choch_event or has_sweep_event or has_choch_text or has_sweep_text:
        return EntryCategory.REVERSAL

    # 3. Pullback Check: Retest / mitigation into active OB or FVG in trend direction
    trend_aligned = (
        (direction == Direction.LONG and (bias_4h == MarketBias.BULLISH or structure_1h == StructureState.BULLISH))
        or (direction == Direction.SHORT and (bias_4h == MarketBias.BEARISH or structure_1h == StructureState.BEARISH))
        or (has_pullback_text)
    )
    ob_or_fvg_present = has_active_ob or has_active_fvg or has_ob_text or has_fvg_text or has_pullback_text
    if trend_aligned and ob_or_fvg_present:
        return EntryCategory.PULLBACK

    # 4. Range / Mean Reversion Check: Explicit dealing range boundary or consolidation
    if is_premium_exhaustion or is_discount_rejection or has_range_text:
        return EntryCategory.RANGE_MEAN_REVERSION

    # 5. Default Fallback
    return EntryCategory.UNCLASSIFIED
