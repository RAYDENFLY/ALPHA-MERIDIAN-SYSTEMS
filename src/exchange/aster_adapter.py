"""AsterDEX public market data adapter.

Provides perpetual universe discovery, Level-2 order book depth, and recent trades.
Reuses existing AsterDEX infrastructure and operates strictly in READ-ONLY mode.
"""

from typing import Any, Dict, List, Optional
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from src.core.logger import logger
from src.exchange.normalized import (
    NormalizedInstrument,
    NormalizedOrderBook,
    NormalizedTrade,
    OrderBookLevel,
)


class AsterAdapter:
    """Public read-only adapter for AsterDEX Perpetual markets."""

    DEFAULT_BASE_URL = "https://fapi.asterdex.com"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = 8.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            retries = Retry(
                total=3,
                backoff_factor=0.3,
                status_forcelist=[429, 500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

        self._catalog_cache: Dict[str, NormalizedInstrument] = {}
        self._last_catalog_refresh: float = 0.0

    def discover_instruments(self, force_refresh: bool = False) -> List[NormalizedInstrument]:
        """Fetch all eligible active USDT perpetual instruments from AsterDEX."""
        now = time.time()
        if not force_refresh and self._catalog_cache and (now - self._last_catalog_refresh < 1800.0):
            return list(self._catalog_cache.values())

        url = f"{self.base_url}/fapi/v1/exchangeInfo"
        try:
            resp = self.session.get(url, timeout=self.timeout_sec)
            if resp.status_code != 200:
                logger.warning(f"AsterAdapter: exchangeInfo returned status {resp.status_code}")
                return list(self._catalog_cache.values())

            data = resp.json()
            symbols_data = data.get("symbols", [])
            discovered: Dict[str, NormalizedInstrument] = {}

            for s in symbols_data:
                if s.get("contractType") and s.get("contractType") != "PERPETUAL":
                    continue
                if s.get("quoteAsset") != "USDT":
                    continue
                if s.get("status") not in ("TRADING", "NORMAL", "ACTIVE", None):
                    continue

                sym = s["symbol"]
                base_asset = s.get("baseAsset", "")
                quote_asset = s.get("quoteAsset", "USDT")

                min_qty = 0.001
                step_size = 0.001
                tick_size = 0.01
                min_notional = 5.0

                for f in s.get("filters", []):
                    f_type = f.get("filterType")
                    if f_type == "LOT_SIZE":
                        step_size = float(f.get("stepSize", step_size))
                        min_qty = float(f.get("minQty", min_qty))
                    elif f_type == "PRICE_FILTER":
                        tick_size = float(f.get("tickSize", tick_size))
                    elif f_type == "MIN_NOTIONAL":
                        min_notional = float(f.get("notional", min_notional))

                inst = NormalizedInstrument(
                    symbol=sym,
                    exchange_name="asterdex",
                    base_asset=base_asset,
                    quote_asset=quote_asset,
                    min_qty=min_qty,
                    step_size=step_size,
                    tick_size=tick_size,
                    min_notional=min_notional,
                    contract_type="PERPETUAL",
                    is_active=True,
                )
                discovered[sym] = inst

            self._catalog_cache = discovered
            self._last_catalog_refresh = now
            logger.info(f"AsterAdapter: Discovered {len(discovered)} active perpetuals.")
            return list(discovered.values())

        except Exception as err:
            logger.error(f"AsterAdapter: Failed to discover instruments: {err}")
            return list(self._catalog_cache.values())

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        """Fetch Level-2 order book depth for a symbol from AsterDEX."""
        url = f"{self.base_url}/fapi/v1/depth"
        params = {"symbol": symbol, "limit": limit}
        now_ms = int(time.time() * 1000)

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                logger.warning(f"AsterAdapter: depth for {symbol} returned status {resp.status_code}")
                return None

            data = resp.json()
            raw_bids = data.get("bids", [])
            raw_asks = data.get("asks", [])

            bids = [OrderBookLevel(price=float(p), size=float(q)) for p, q in raw_bids]
            asks = [OrderBookLevel(price=float(p), size=float(q)) for p, q in raw_asks]

            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price, reverse=False)

            return NormalizedOrderBook(
                symbol=symbol,
                exchange_name="asterdex",
                timestamp_ms=int(data.get("E") or now_ms),
                bids=bids,
                asks=asks,
            )
        except Exception as err:
            logger.warning(f"AsterAdapter: Failed to fetch order book for {symbol}: {err}")
            return None

    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[NormalizedTrade]:
        """Fetch recent trades from AsterDEX."""
        url = f"{self.base_url}/fapi/v1/trades"
        params = {"symbol": symbol, "limit": limit}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                return []

            data = resp.json()
            trades: List[NormalizedTrade] = []
            for t in data:
                price = float(t["price"])
                size = float(t["qty"])
                is_buyer_maker = bool(t.get("isBuyerMaker", False))
                side = "SELL" if is_buyer_maker else "BUY"

                trades.append(
                    NormalizedTrade(
                        trade_id=str(t.get("id", "")),
                        symbol=symbol,
                        exchange_name="asterdex",
                        timestamp_ms=int(t.get("time", 0)),
                        price=price,
                        size=size,
                        notional_usdt=price * size,
                        side=side,
                    )
                )
            return trades
        except Exception as err:
            logger.warning(f"AsterAdapter: Failed to fetch trades for {symbol}: {err}")
            return []
