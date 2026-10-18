"""Domain models for Model B News & Catalyst Engine.

Enforces strict provenance, fact vs rumor differentiation, deduplication,
and anti-hallucination entry constraints.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional


class CatalystCategory(str, Enum):
    """Event taxonomy for market news and catalysts."""
    EXCHANGE_LISTING = "EXCHANGE_LISTING"
    EXCHANGE_DELISTING = "EXCHANGE_DELISTING"
    SECURITY_INCIDENT = "SECURITY_INCIDENT"
    TOKEN_UNLOCK = "TOKEN_UNLOCK"
    PROTOCOL_UPGRADE = "PROTOCOL_UPGRADE"
    EXCHANGE_ANNOUNCEMENT = "EXCHANGE_ANNOUNCEMENT"
    REGULATORY_ACTION = "REGULATORY_ACTION"
    GENERAL_MARKET_NEWS = "GENERAL_MARKET_NEWS"


class VerificationStatus(str, Enum):
    """Information integrity tier distinguishing confirmed facts from rumors."""
    CONFIRMED_FACT = "CONFIRMED_FACT"          # Verified exchange announcement, signed on-chain event
    UNVERIFIED_REPORT = "UNVERIFIED_REPORT"    # Social media leak, unsourced claim, unconfirmed rumor
    MODEL_SENTIMENT = "MODEL_SENTIMENT"        # Derived AI/LLM synthetic interpretation


@dataclass
class CatalystEvent:
    """A timestamped, provenance-tracked news or catalyst intelligence item."""
    event_id: str
    title: str
    source_name: str
    source_url: str
    published_at_utc: str
    ingested_at_utc: str
    category: CatalystCategory
    verification_status: VerificationStatus
    affected_symbols: List[str]
    confidence_score: float = 0.50
    summary: str = ""
    is_stale: bool = False
    is_corrected: bool = False
    supersedes_event_id: Optional[str] = None
    conflicting_event_ids: List[str] = field(default_factory=list)
    content_hash: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.content_hash:
            raw = f"{self.source_name}:{self.title}:{','.join(sorted(self.affected_symbols))}"
            self.content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def is_eligible_for_trade_bias(self) -> bool:
        """Strict safety gate: Never permit trade triggers solely from unverified reports."""
        if self.verification_status != VerificationStatus.CONFIRMED_FACT:
            return False
        if self.is_stale or self.is_corrected:
            return False
        if self.confidence_score < 0.70:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "published_at_utc": self.published_at_utc,
            "ingested_at_utc": self.ingested_at_utc,
            "category": self.category.value,
            "verification_status": self.verification_status.value,
            "affected_symbols": list(self.affected_symbols),
            "confidence_score": self.confidence_score,
            "summary": self.summary,
            "is_stale": self.is_stale,
            "is_corrected": self.is_corrected,
            "supersedes_event_id": self.supersedes_event_id,
            "conflicting_event_ids": list(self.conflicting_event_ids),
            "is_eligible_for_trade_bias": self.is_eligible_for_trade_bias,
        }
