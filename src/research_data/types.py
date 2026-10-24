from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class ResearchObservation:
    source_id: str; dataset_id: str; market_id: str; symbol: str; observation_timestamp: str
    publication_timestamp: Optional[str]; reporting_period: Optional[str]; value: Optional[float]
    unit: str; source_url: str; freshness_state: str = "MISSING"; quality_state: str = "VALID"; payload_hash: str = ""
    def validate(self):
        if self.freshness_state not in {"FRESH","DELAYED","STALE","MISSING"}: raise ValueError("invalid freshness state")
        if self.quality_state not in {"VALID","PROVISIONAL","INVALID","UNVERIFIED"}: raise ValueError("invalid quality state")
        if not self.source_id or not self.dataset_id or not self.observation_timestamp or not self.unit: raise ValueError("missing identity")
