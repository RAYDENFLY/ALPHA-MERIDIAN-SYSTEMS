"""Target Labeling for ML Setup Evaluation.

Defines deterministic binary target: whether proposed TP is reached before SL
within a fixed horizon of H bars, with conservative SL-first collision resolution.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
from src.core.types import Candle, Direction, SetupCandidate


@dataclass
class SetupOutcome:
    """Outcome of forward-looking setup simulation for training labels."""
    candidate_id: str
    symbol: str
    target_hit: int             # 1 = TP hit first (Win), 0 = SL hit first / Collision / Timeout (Loss)
    outcome_reason: str         # "TP_HIT", "SL_HIT", "SL_COLLISION", "TIMEOUT"
    bars_to_outcome: int
    exit_price: float


class SetupLabeler:
    """Labels candidate setups based on forward price action."""

    @staticmethod
    def label_candidate(
        candidate: SetupCandidate,
        forward_candles: List[Candle],
        horizon_bars: int = 12,
    ) -> SetupOutcome:
        """Evaluate forward candles to determine if TP is reached before SL.

        Parameters:
        -----------
        candidate : SetupCandidate
            The proposed trade candidate.
        forward_candles : List[Candle]
            Chronological candles that occur STRICTLY AFTER candidate confirmation.
        horizon_bars : int
            Maximum number of bars to wait for resolution.
        """
        horizon_slice = forward_candles[:horizon_bars]

        entry = candidate.entry_price
        sl = candidate.stop_loss
        tp = candidate.take_profit_1

        if not horizon_slice:
            return SetupOutcome(
                candidate_id=candidate.candidate_id,
                symbol=candidate.symbol,
                target_hit=0,
                outcome_reason="TIMEOUT",
                bars_to_outcome=0,
                exit_price=entry,
            )

        for bar_idx, bar in enumerate(horizon_slice):
            bars_elapsed = bar_idx + 1

            if candidate.direction == Direction.LONG:
                hit_tp = bar.high >= tp
                hit_sl = bar.low <= sl

                # Conservative rule: If both hit on same candle, assume SL first!
                if hit_tp and hit_sl:
                    return SetupOutcome(
                        candidate_id=candidate.candidate_id,
                        symbol=candidate.symbol,
                        target_hit=0,
                        outcome_reason="SL_COLLISION",
                        bars_to_outcome=bars_elapsed,
                        exit_price=sl,
                    )
                elif hit_sl:
                    return SetupOutcome(
                        candidate_id=candidate.candidate_id,
                        symbol=candidate.symbol,
                        target_hit=0,
                        outcome_reason="SL_HIT",
                        bars_to_outcome=bars_elapsed,
                        exit_price=sl,
                    )
                elif hit_tp:
                    return SetupOutcome(
                        candidate_id=candidate.candidate_id,
                        symbol=candidate.symbol,
                        target_hit=1,
                        outcome_reason="TP_HIT",
                        bars_to_outcome=bars_elapsed,
                        exit_price=tp,
                    )

            elif candidate.direction == Direction.SHORT:
                hit_tp = bar.low <= tp
                hit_sl = bar.high >= sl

                if hit_tp and hit_sl:
                    return SetupOutcome(
                        candidate_id=candidate.candidate_id,
                        symbol=candidate.symbol,
                        target_hit=0,
                        outcome_reason="SL_COLLISION",
                        bars_to_outcome=bars_elapsed,
                        exit_price=sl,
                    )
                elif hit_sl:
                    return SetupOutcome(
                        candidate_id=candidate.candidate_id,
                        symbol=candidate.symbol,
                        target_hit=0,
                        outcome_reason="SL_HIT",
                        bars_to_outcome=bars_elapsed,
                        exit_price=sl,
                    )
                elif hit_tp:
                    return SetupOutcome(
                        candidate_id=candidate.candidate_id,
                        symbol=candidate.symbol,
                        target_hit=1,
                        outcome_reason="TP_HIT",
                        bars_to_outcome=bars_elapsed,
                        exit_price=tp,
                    )

        # Neither TP nor SL reached within horizon: Timeout (Conservative = 0)
        final_bar = horizon_slice[-1]
        return SetupOutcome(
            candidate_id=candidate.candidate_id,
            symbol=candidate.symbol,
            target_hit=0,
            outcome_reason="TIMEOUT",
            bars_to_outcome=len(horizon_slice),
            exit_price=final_bar.close,
        )
