"""News & Catalyst Engine for Model B.

Maintains an in-memory and queryable registry of verified market intelligence,
performing deduplication, contradiction tracking, and anti-hallucination gating.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Set

from src.catalyst.types import CatalystCategory, CatalystEvent, VerificationStatus

logger = logging.getLogger(__name__)


class NewsCatalystEngine:
    """Manages catalyst event ingestion, deduplication, and signal qualification."""

    def __init__(self, max_event_age_hours: float = 48.0):
        self.max_event_age_hours = max_event_age_hours
        self._events: Dict[str, CatalystEvent] = {}
        self._hash_index: Set[str] = set()

    def ingest_event(self, event: CatalystEvent) -> bool:
        """Ingest a catalyst event with deduplication and conflict linking.
        
        Returns True if newly added, False if duplicate.
        """
        # 1. Deduplication check
        if event.content_hash in self._hash_index:
            logger.debug("Duplicate catalyst event ignored: %s", event.title)
            return False

        # 2. Handle superseding / corrections
        if event.supersedes_event_id and event.supersedes_event_id in self._events:
            prior = self._events[event.supersedes_event_id]
            prior.is_corrected = True
            logger.info("Catalyst %s marked corrected by %s", prior.event_id, event.event_id)

        # 3. Conflict resolution / flagging
        for existing in self._events.values():
            if any(s in existing.affected_symbols for s in event.affected_symbols):
                # Opposing category check (e.g. listing vs delisting)
                if (
                    event.category == CatalystCategory.EXCHANGE_DELISTING
                    and existing.category == CatalystCategory.EXCHANGE_LISTING
                ):
                    event.conflicting_event_ids.append(existing.event_id)
                    existing.conflicting_event_ids.append(event.event_id)

        self._events[event.event_id] = event
        self._hash_index.add(event.content_hash)
        logger.info(
            "Ingested catalyst [%s] '%s' (%s, status=%s)",
            event.event_id,
            event.title,
            event.category.value,
            event.verification_status.value,
        )
        return True

    def get_catalysts_for_symbol(
        self,
        symbol: str,
        only_actionable: bool = False,
    ) -> List[CatalystEvent]:
        """Retrieve recent catalysts matching a symbol."""
        now = datetime.now(timezone.utc)
        results = []
        sym_clean = symbol.upper()

        for ev in self._events.values():
            if sym_clean in [s.upper() for s in ev.affected_symbols]:
                if only_actionable and not ev.is_eligible_for_trade_bias:
                    continue
                results.append(ev)

        return sorted(results, key=lambda x: x.published_at_utc, reverse=True)

    def evaluate_catalyst_bias(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Evaluate directional bias from confirmed, un-conflicted catalysts.
        
        Hard Invariant: NEVER return actionable bias for unverified reports.
        """
        catalysts = self.get_catalysts_for_symbol(symbol, only_actionable=True)
        if not catalysts:
            return None

        # Sort by confidence
        top_catalyst = catalysts[0]
        if top_catalyst.conflicting_event_ids:
            logger.warning("Catalyst for %s has conflicting reports; bias neutralized", symbol)
            return None

        bias = None
        if top_catalyst.category in (
            CatalystCategory.EXCHANGE_LISTING,
            CatalystCategory.PROTOCOL_UPGRADE,
        ):
            bias = "BULLISH"
        elif top_catalyst.category in (
            CatalystCategory.EXCHANGE_DELISTING,
            CatalystCategory.SECURITY_INCIDENT,
            CatalystCategory.TOKEN_UNLOCK,
        ):
            bias = "BEARISH"

        if not bias:
            return None

        return {
            "bias": bias,
            "catalyst_id": top_catalyst.event_id,
            "category": top_catalyst.category.value,
            "confidence": top_catalyst.confidence_score,
            "source": top_catalyst.source_name,
            "source_url": top_catalyst.source_url,
        }
