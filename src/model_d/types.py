from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class DataAvailability(str, Enum):
    LIVE = "LIVE"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


@dataclass
class SourceHealth:
    source: str
    status: DataAvailability = DataAvailability.UNAVAILABLE
    exchange_timestamp_ms: Optional[int] = None
    observed_timestamp_ms: Optional[int] = None
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status.value,
            "exchange_timestamp_ms": self.exchange_timestamp_ms,
            "observed_timestamp_ms": self.observed_timestamp_ms,
            "error": self.error,
            "details": self.details,
        }
