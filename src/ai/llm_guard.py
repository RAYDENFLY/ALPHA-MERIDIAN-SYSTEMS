"""LLM Integration Guardrails Engine conforming to Section 3.

Strictly confines LLM roles to:
1. Candidate ranking (ordering setups)
2. Market regime classification
3. Veto power (with mandatory counterfactual logging)

Prohibits LLM from:
- Generating entry, stop-loss, or take-profit prices.
- Modifying position sizing or leverage.
- Overriding risk limits or global position locks.
- Blocking the critical trigger path (deterministic trigger runs autonomously if LLM fails/times out).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("src.ai.llm_guard")


class LLMVetoException(Exception):
    """Raised when an LLM vetoes a trade candidate."""
    pass


class LLMGuardrailViolationError(Exception):
    """Raised when an LLM attempts an unauthorized mutation (e.g. price/size alteration)."""
    pass


@dataclass
class LLMRegimeContext:
    symbol: str
    regime: str  # TRENDING_BULL, TRENDING_BEAR, RANGING, HIGH_VOLATILITY, UNKNOWN
    rationale: str = ""
    timestamp_utc: str = ""


@dataclass
class LLMVetoDecision:
    symbol: str
    strategy_id: str
    veto: bool
    veto_reason: str
    timestamp_utc: str = ""


class LLMExecutionGuard:
    """Enforces strict boundaries around LLM outputs and execution authority."""

    ALLOWED_ACTIONS = {"CANDIDATE_RANKING", "MARKET_REGIME", "VETO"}
    PROHIBITED_MUTATIONS = {"entry_price", "stop_loss", "take_profit", "quantity", "leverage", "margin_usdt"}

    def __init__(self, shadow_tracker: Optional[Any] = None) -> None:
        self.shadow_tracker = shadow_tracker

    def validate_action(self, action_type: str) -> bool:
        """Validate whether the requested LLM action is permitted."""
        if action_type not in self.ALLOWED_ACTIONS:
            raise LLMGuardrailViolationError(
                f"Unauthorized LLM action '{action_type}'. Allowed actions: {self.ALLOWED_ACTIONS}"
            )
        return True

    def sanitize_llm_output(
        self,
        llm_response: Dict[str, Any],
        deterministic_setup: Any,
    ) -> Dict[str, Any]:
        """Strip any unauthorized price, sizing, or risk overrides from LLM output.
        
        Guarantees that entry, stop, target, and size remain 100% deterministic.
        """
        sanitized = dict(llm_response)

        # Check if LLM attempted to tamper with core trade parameters
        tamper_attempts = []
        for prohibited in self.PROHIBITED_MUTATIONS:
            if prohibited in sanitized:
                det_val = getattr(deterministic_setup, prohibited, None)
                llm_val = sanitized[prohibited]
                if det_val is not None and llm_val != det_val:
                    tamper_attempts.append(f"{prohibited}: {llm_val} != {det_val}")
                # Strip and override with deterministic value
                sanitized[prohibited] = det_val

        if tamper_attempts:
            logger.warning(
                "LLM attempted prohibited parameter modifications: %s. Overriding with deterministic levels.",
                ", ".join(tamper_attempts)
            )

        # Ensure core fields match deterministic setup
        sanitized["entry_price"] = getattr(deterministic_setup, "entry_price", None)
        sanitized["stop_loss"] = getattr(deterministic_setup, "stop_loss", None)
        sanitized["take_profit"] = getattr(deterministic_setup, "take_profit", None)
        sanitized["quantity"] = getattr(deterministic_setup, "quantity", None)
        sanitized["leverage"] = getattr(deterministic_setup, "leverage", 10.0)

        return sanitized

    def evaluate_veto(
        self,
        setup: Any,
        llm_veto: bool,
        veto_reason: str = "",
        market_regime: str = "UNKNOWN",
    ) -> bool:
        """Evaluate LLM veto and log counterfactual outcome if vetoed.
        
        Returns:
            True if candidate is ALLOWED to proceed to execution.
            False if candidate was VETOED.
        """
        if not llm_veto:
            return True

        logger.info(
            "Candidate %s (%s) VETOED by LLM. Reason: %s",
            setup.symbol, setup.strategy_id, veto_reason
        )

        # Record counterfactual if shadow tracker is configured
        if self.shadow_tracker:
            try:
                self.shadow_tracker.record_shadow_setup(
                    setup=setup,
                    market_regime=market_regime,
                    llm_selected=False,
                    llm_veto=True,
                    llm_veto_reason=veto_reason,
                )
            except Exception as e:
                logger.error("Failed to log LLM veto counterfactual: %s", e)

        return False

    def rank_candidates(
        self,
        candidates: List[Any],
        llm_rankings: Optional[List[str]] = None,
    ) -> List[Any]:
        """Order valid deterministic candidates according to LLM ranking without altering setups."""
        if not llm_rankings or not candidates:
            return candidates

        symbol_to_candidate = {c.symbol: c for c in candidates}
        ordered = []

        # Add ranked candidates first
        for sym in llm_rankings:
            if sym in symbol_to_candidate:
                ordered.append(symbol_to_candidate.pop(sym))

        # Add remaining unranked candidates
        for remaining in symbol_to_candidate.values():
            ordered.append(remaining)

        return ordered
