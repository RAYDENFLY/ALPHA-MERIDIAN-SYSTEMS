from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class ModelDUniverse:
    def __init__(self, adapter: Any):
        self.adapter = adapter; self.instruments: Dict[str, Any] = {}; self.last_refresh_utc: Optional[str] = None; self.last_error: Optional[str] = None; self.requested_count = 0; self.excluded: List[Dict[str, Any]] = []

    def refresh(self, force: bool = False) -> List[Any]:
        try:
            items = self.adapter.discover_instruments(force_refresh=force)
            self.requested_count = len(items); self.excluded = [{"symbol": i.symbol, "reason": "inactive" if not i.is_active else "not_perpetual"} for i in items if not (i.contract_type == "PERPETUAL" and i.is_active)]
            self.instruments = {i.symbol: i for i in items}
            self.last_refresh_utc = datetime.now(timezone.utc).isoformat(); self.last_error = None
        except Exception as exc:
            self.last_error = "public metadata refresh failed"
        return list(self.instruments.values())

    def status(self) -> Dict[str, Any]:
        eligible = [i for i in self.instruments.values() if i.contract_type == "PERPETUAL" and i.is_active]
        state = "ERROR" if self.last_error else "LIVE" if self.last_refresh_utc and eligible else "CHECKED_EMPTY" if self.last_refresh_utc else "UNAVAILABLE"
        return {"venue": "asterdex", "eligible_count": len(eligible), "requested_count": self.requested_count, "excluded": self.excluded, "last_refresh_utc": self.last_refresh_utc, "status": state, "error": self.last_error}
