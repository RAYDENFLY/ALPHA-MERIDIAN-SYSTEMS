"""Bybit Linear Perpetual public market data adapter.

Provides public REST Level-2 order book depth and recent trades for USDT linear contracts.
Operates strictly in READ-ONLY mode for liquidity telemetry and cross-exchange research.
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


class BybitLinearAdapter:
    """Public read-only adapter for Bybit Linear (USDT) Perpetuals."""

    DEFAULT_BASE_URL = "https://api.bybit.com"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = 6.0,
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
        """Fetch all eligible active USDT linear perpetual instruments from Bybit."""
        now = time.time()
        if not force_refresh and self._catalog_cache and (now - self._last_catalog_refresh < 1800.0):
            return list(self._catalog_cache.values())

        url = f"{self.base_url}/v5/market/instruments-info"
        params = {"category": "linear", "limit": 500}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                logger.warning(f"BybitLinear: instruments-info returned status {resp.status_code}")
                return list(self._catalog_cache.values())

            data = resp.json()
            if data.get("retCode") != 0:
                logger.warning(f"BybitLinear: instruments-info error: {data.get('retMsg')}")
                return list(self._catalog_cache.values())

            list_data = data.get("result", {}).get("list", [])
            discovered: Dict[str, NormalizedInstrument] = {}

            for s in list_data:
                # Filter perpetual USDT contracts
                if s.get("contractType") != "LinearPerpetual":
                    continue
                if s.get("quoteCoin") != "USDT":
                    continue
                if s.get("status") != "Trading":
                    continue

                sym = s["symbol"]
                base_coin = s.get("baseCoin", "")
                quote_coin = s.get("quoteCoin", "")

                lot_filter = s.get("lotSizeFilter", {})
                price_filter = s.get("priceFilter", {})

                min_qty = float(lot_filter.get("minOrderQty", 0.001))
                step_size = float(lot_filter.get("qtyStep", 0.001))
                tick_size = float(price_filter.get("tickSize", 0.01))
                min_notional = float(lot_filter.get("minNotionalValue", 5.0))

                inst = NormalizedInstrument(
                    symbol=sym,
                    exchange_name="bybit",
                    base_asset=base_coin,
                    quote_asset=quote_coin,
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
            logger.info(f"BybitLinear: Discovered {len(discovered)} active USDT perpetuals.")
            return list(discovered.values())

        except Exception as err:
            logger.error(f"BybitLinear: Failed to discover instruments: {err}")
            return list(self._catalog_cache.values())

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        """Fetch Level-2 order book depth for a USDT linear contract."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        url = f"{self.base_url}/v5/market/orderbook"
        params = {"category": "linear", "symbol": clean_symbol, "limit": min(limit, 50)}
        now_ms = int(time.time() * 1000)

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                logger.warning(f"BybitLinear: orderbook for {clean_symbol} returned status {resp.status_code}")
                return None

            data = resp.json()
            if data.get("retCode") != 0:
                logger.warning(f"BybitLinear: orderbook for {clean_symbol} error: {data.get('retMsg')}")
                return None

            result = data.get("result", {})
            raw_bids = result.get("b", [])
            raw_asks = result.get("a", [])

            bids = [OrderBookLevel(price=float(p), size=float(q)) for p, q in raw_bids]
            asks = [OrderBookLevel(price=float(p), size=float(q)) for p, q in raw_asks]

            # Ensure proper sort order: bids descending, asks ascending
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price, reverse=False)

            ts_ms = int(result.get("ts") or data.get("time") or now_ms)

            return NormalizedOrderBook(
                symbol=clean_symbol,
                exchange_name="bybit",
                timestamp_ms=ts_ms,
                bids=bids,
                asks=asks,
            )
        except Exception as err:
            logger.warning(f"BybitLinear: Failed to fetch order book for {clean_symbol}: {err}")
            return None

    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[NormalizedTrade]:
        """Fetch recent public trades for flow analysis."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        url = f"{self.base_url}/v5/market/recent-trade"
        params = {"category": "linear", "symbol": clean_symbol, "limit": min(limit, 100)}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                return []

            data = resp.json()
            if data.get("retCode") != 0:
                return []

            trades_list = data.get("result", {}).get("list", [])
            trades: List[NormalizedTrade] = []
            for t in trades_list:
                price = float(t["price"])
                size = float(t["size"])
                # Bybit provides side directly as "Buy" or "Sell"
                raw_side = str(t.get("side", "")).upper()
                side = "BUY" if raw_side == "BUY" else "SELL"

                trades.append(
                    NormalizedTrade(
                        trade_id=str(t.get("execId", "")),
                        symbol=clean_symbol,
                        exchange_name="bybit",
                        timestamp_ms=int(t.get("time", 0)),
                        price=price,
                        size=size,
                        notional_usdt=price * size,
                        side=side,
                    )
                )
            return trades
        except Exception as err:
            logger.warning(f"BybitLinear: Failed to fetch trades for {clean_symbol}: {err}")
            return []
