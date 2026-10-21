"""Binance USD(S)-M Futures public market data adapter.

Provides public REST market discovery, Level-2 order book depth, and recent trades.
Operates strictly in READ-ONLY mode for liquidity telemetry and research.
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


class BinanceFuturesAdapter:
    """Public read-only adapter for Binance USD(S)-M Futures."""

    DEFAULT_BASE_URL = "https://fapi.binance.com"

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
        """Fetch all eligible active USDT perpetual instruments from Binance Futures."""
        now = time.time()
        if not force_refresh and self._catalog_cache and (now - self._last_catalog_refresh < 1800.0):
            return list(self._catalog_cache.values())

        url = f"{self.base_url}/fapi/v1/exchangeInfo"
        try:
            resp = self.session.get(url, timeout=self.timeout_sec)
            if resp.status_code != 200:
                logger.warning(f"BinanceFutures: exchangeInfo returned status {resp.status_code}")
                return list(self._catalog_cache.values())

            data = resp.json()
            symbols_data = data.get("symbols", [])
            discovered: Dict[str, NormalizedInstrument] = {}

            for s in symbols_data:
                # Filter perpetual USDT contracts
                if s.get("contractType") != "PERPETUAL":
                    continue
                if s.get("quoteAsset") != "USDT":
                    continue
                if s.get("status") != "TRADING":
                    continue

                sym = s["symbol"]
                base_asset = s.get("baseAsset", "")
                quote_asset = s.get("quoteAsset", "")

                # Filters
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
                    exchange_name="binance",
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
            logger.info(f"BinanceFutures: Discovered {len(discovered)} active USDT perpetuals.")
            return list(discovered.values())

        except Exception as err:
            logger.error(f"BinanceFutures: Failed to discover instruments: {err}")
            return list(self._catalog_cache.values())

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        """Fetch Level-2 order book depth for a symbol."""
        url = f"{self.base_url}/fapi/v1/depth"
        params = {"symbol": symbol, "limit": limit}
        now_ms = int(time.time() * 1000)

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                logger.warning(f"BinanceFutures: depth for {symbol} returned status {resp.status_code}")
                return None

            data = resp.json()
            raw_bids = data.get("bids", [])
            raw_asks = data.get("asks", [])

            bids = [OrderBookLevel(price=float(p), size=float(q)) for p, q in raw_bids]
            asks = [OrderBookLevel(price=float(p), size=float(q)) for p, q in raw_asks]

            # Ensure proper sort order: bids descending, asks ascending
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price, reverse=False)

            return NormalizedOrderBook(
                symbol=symbol,
                exchange_name="binance",
                timestamp_ms=int(data.get("E") or now_ms),
                bids=bids,
                asks=asks,
            )
        except Exception as err:
            logger.warning(f"BinanceFutures: Failed to fetch order book for {symbol}: {err}")
            return None

    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[NormalizedTrade]:
        """Fetch recent public trades for flow analysis."""
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
                # isBuyerMaker == True means maker was buyer -> taker was seller (SELL)
                is_buyer_maker = bool(t.get("isBuyerMaker", False))
                side = "SELL" if is_buyer_maker else "BUY"

                trades.append(
                    NormalizedTrade(
                        trade_id=str(t.get("id", "")),
                        symbol=symbol,
                        exchange_name="binance",
                        timestamp_ms=int(t.get("time", 0)),
                        price=price,
                        size=size,
                        notional_usdt=price * size,
                        side=side,
                    )
                )
            return trades
        except Exception as err:
            logger.warning(f"BinanceFutures: Failed to fetch trades for {symbol}: {err}")
            return []

    def get_funding_rates(self) -> Dict[str, Dict[str, Any]]:
        """Fetch latest funding rate and mark prices across all instruments."""
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        try:
            resp = self.session.get(url, timeout=self.timeout_sec)
            if resp.status_code != 200:
                return {}

            data = resp.json()
            if not isinstance(data, list):
                return {}

            rates: Dict[str, Dict[str, Any]] = {}
            for row in data:
                sym = row.get("symbol")
                if not sym:
                    continue
                try:
                    mark_px = float(row.get("markPrice", 0.0))
                    idx_px = float(row.get("indexPrice", 0.0))
                    funding_rate = float(row.get("lastFundingRate", 0.0))
                    next_funding_time = int(row.get("nextFundingTime", 0))

                    rates[sym] = {
                        "mark_price": mark_px,
                        "index_price": idx_px,
                        "funding_rate": funding_rate,
                        "next_funding_time_ms": next_funding_time,
                    }
                except (ValueError, TypeError):
                    continue

            return rates
        except Exception as err:
            logger.warning(f"BinanceFutures: Failed to fetch premiumIndex: {err}")
            return {}

    def get_open_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch current USDⓈ-M open interest from the public endpoint."""
        try:
            resp = self.session.get(f"{self.base_url}/fapi/v1/openInterest", params={"symbol": symbol.upper()}, timeout=self.timeout_sec)
            if resp.status_code != 200: return None
            data = resp.json()
            if not isinstance(data, dict) or "openInterest" not in data: return None
            return {"symbol": symbol.upper(), "open_interest": float(data["openInterest"]), "timestamp_ms": int(data.get("time", time.time() * 1000))}
        except Exception as err:
            logger.warning(f"BinanceFutures: Failed to fetch open interest for {symbol}: {err}")
            return None

    def get_premium_index(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self.session.get(f"{self.base_url}/fapi/v1/premiumIndex", params={"symbol": symbol.upper()}, timeout=self.timeout_sec)
            if resp.status_code != 200: return None
            row = resp.json()
            if not isinstance(row, dict): return None
            return {"symbol": symbol.upper(), "mark_price": float(row["markPrice"]), "index_price": float(row["indexPrice"]),
                    "last_funding_rate": float(row["lastFundingRate"]), "next_funding_time_ms": int(row["nextFundingTime"]),
                    "timestamp_ms": int(row.get("time", time.time() * 1000))}
        except Exception as err:
            logger.warning(f"BinanceFutures: Failed to fetch premium index for {symbol}: {err}")
            return None
