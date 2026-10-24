"""HTTP source fetchers; no credentials and no trading dependencies."""
from typing import Callable, Optional
from datetime import datetime, timezone
import requests
from src.research_data.adapters import parse_treasury_csv, parse_cot_csv
from src.research_data.ingestion import TransientIngestionError

TREASURY_CSV_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}".format(year=datetime.now(timezone.utc).year)
CFTC_CSV_URL = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"

class PublicResearchHTTP:
    def __init__(self, timeout_seconds: float = 15.0, session: Optional[requests.Session] = None):
        self.timeout_seconds = timeout_seconds; self.session = session or requests.Session()
    def _get(self, url: str) -> str:
        try: response = self.session.get(url, timeout=self.timeout_seconds)
        except (requests.Timeout, requests.ConnectionError) as exc: raise TransientIngestionError(str(exc)) from exc
        if response.status_code == 429 or 500 <= response.status_code < 600:
            retry_after = response.headers.get("Retry-After")
            raise TransientIngestionError(f"HTTP {response.status_code}", float(retry_after) if retry_after else None)
        if response.status_code != 200: raise ValueError(f"source returned HTTP {response.status_code}")
        if not response.text.strip(): raise ValueError("source returned an empty response")
        return response.text
    def treasury_nominal(self, url: str = TREASURY_CSV_URL):
        return parse_treasury_csv(self._get(url), "treasury_nominal", url)
    def cftc(self, url: str = CFTC_CSV_URL):
        return parse_cot_csv(self._get(url), url)
