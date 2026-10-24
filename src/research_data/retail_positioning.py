"""Bybit public account-ratio provider. This is holder-count data, not position size."""
from dataclasses import dataclass
import time
from typing import Any, Dict, Optional
import requests

@dataclass
class RetailPositioningProvider:
    base_url: str = "https://api.bybit.com"
    timeout_seconds: float = 10.0
    cache_ttl_seconds: float = 60.0
    session: Optional[requests.Session] = None
    def __post_init__(self):
        self.session = self.session or requests.Session()
        self._cache = {}

    @staticmethod
    def _unavailable(error: str) -> Dict[str, Any]:
        return {
            "status": "UNAVAILABLE",
            "error": error,
            "provider": "Bybit",
            "metric": "account_ratio",
            "metric_definition": "long and short holder counts; not position size and not all crypto retail traders",
        }
    def get(self, symbol: str = "BTCUSDT", period: str = "1h", limit: int = 50) -> Dict[str, Any]:
        symbol = symbol.strip().upper()
        period = period.strip()
        limit = max(1, min(int(limit), 500))
        key = (symbol, period, limit)
        now = time.time()
        cached=self._cache.get(key)
        if cached and now-cached[0] < self.cache_ttl_seconds:
            result = dict(cached[1])
            result["cache_status"] = "HIT"
            return result
        url=f"{self.base_url.rstrip('/')}/v5/market/account-ratio"
        try:
            response=self.session.get(url,params={"category":"linear","symbol":symbol,"period":period,"limit":limit},timeout=self.timeout_seconds)
            if response.status_code in (418, 429, 500, 502, 503, 504):
                return self._unavailable("provider_rate_or_transport_limit")
            if response.status_code != 200: return {"status":"ERROR","error":f"provider_http_{response.status_code}","provider":"Bybit","metric":"account_ratio"}
            body = response.json()
            result = body.get("result", {})
            raw = result.get("list", [])
            if not isinstance(raw, list):
                return {"status": "ERROR", "error": "malformed_account_ratio", "provider": "Bybit", "metric": "account_ratio"}
            observations=[]
            for item in raw:
                try:
                    buy=float(item["buyRatio"]); sell=float(item["sellRatio"]); ts=int(item["timestamp"])
                    if not (0 <= buy <= 1 and 0 <= sell <= 1): raise ValueError("ratio out of range")
                except (KeyError,TypeError,ValueError): return {"status":"ERROR","error":"malformed_account_ratio","provider":"Bybit","metric":"account_ratio"}
                observations.append({"symbol":symbol,"observation_timestamp_ms":ts,"long_account_ratio":buy,"short_account_ratio":sell,"long_short_account_ratio":buy/sell if sell else None})
            result={"status":"AVAILABLE" if observations else "UNAVAILABLE","provider":"Bybit","metric":"account_ratio","metric_definition":"long and short holder counts as percentages of all position holders; not position size and not all crypto retail traders","symbol":symbol,"period":period,"observations":observations,"source_url":url,"cache_status":"MISS"}
            self._cache[key]=(now,result); return result
        except (requests.Timeout,requests.ConnectionError):
            return self._unavailable("provider_timeout_or_connection_error")
        except (ValueError,TypeError):
            return {"status":"ERROR","error":"malformed_provider_response","provider":"Bybit","metric":"account_ratio"}
