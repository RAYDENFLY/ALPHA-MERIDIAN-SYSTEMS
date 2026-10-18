"""Market Data Service for Universal AsterDEX Perpetual Price Alerts.

Discovers all eligible tradable USDT perpetual contracts (~578 symbols)
and polls exchange bulk endpoints for Mark Price and Last Price.
Maintains high throughput and low overhead without per-symbol polling.
"""

import asyncio
from datetime import datetime, timezone
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from src.alerts.models import AlertSource, SymbolCatalogItem
from src.core.logger import logger


class AlertMarketDataService:
    """Universal market data provider for perpetual contract prices and symbols."""

    def __init__(
        self,
        base_url: str = "https://fapi.asterdex.com",
        poll_interval_sec: float = 4.0,
        stale_threshold_sec: float = 60.0,
        catalog_refresh_interval_sec: float = 1800.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.poll_interval_sec = poll_interval_sec
        self.stale_threshold_sec = stale_threshold_sec
        self.catalog_refresh_interval_sec = catalog_refresh_interval_sec

        # State storage
        self._catalog: Dict[str, SymbolCatalogItem] = {}
        self._prices: Dict[str, Dict[str, Any]] = {}
        self._last_catalog_refresh: float = 0.0
        self._last_poll_time: float = 0.0
        self._poll_count: int = 0
        self._error_count: int = 0
        self._last_error: Optional[str] = None
        self._is_running: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._tick_callbacks: List[Callable[[Dict[str, Dict[str, Any]]], Any]] = []

        # Configure resilient HTTP session
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def subscribe_tick(self, callback: Callable[[Dict[str, Dict[str, Any]]], Any]) -> None:
        """Register a callback invoked after each successful price update tick."""
        if callback not in self._tick_callbacks:
            self._tick_callbacks.append(callback)

    def refresh_catalog(self) -> Dict[str, SymbolCatalogItem]:
        """Fetch exchangeInfo and discover all active USDT perpetual symbols."""
        url = f"{self.base_url}/fapi/v3/exchangeInfo"
        try:
            resp = self.session.get(url, timeout=10, headers={"User-Agent": "SMC-SNR-Alerts/1.0"})
            if resp.status_code != 200:
                msg = f"Failed to fetch exchangeInfo: HTTP {resp.status_code}"
                logger.warning(msg)
                self._last_error = msg
                return self._catalog

            data = resp.json()
            symbols_raw = data.get("symbols", [])
            new_catalog: Dict[str, SymbolCatalogItem] = {}

            for item in symbols_raw:
                quote = item.get("quoteAsset")
                status = item.get("status")
                contract_type = item.get("contractType")
                # Filter for active USDT perpetual contracts
                if quote == "USDT" and status == "TRADING" and contract_type in (None, "PERPETUAL"):
                    sym = item.get("symbol", "").upper()
                    base = item.get("baseAsset", "")
                    price_prec = int(item.get("pricePrecision", 2))
                    qty_prec = int(item.get("quantityPrecision", 2))
                    tick_size = 0.01
                    min_qty = 0.01
                    min_notional = 5.0

                    for f in item.get("filters", []):
                        ftype = f.get("filterType")
                        if ftype == "PRICE_FILTER":
                            try:
                                tick_size = float(f.get("tickSize", 0.01))
                            except (ValueError, TypeError):
                                pass
                        elif ftype in ("LOT_SIZE", "MARKET_LOT_SIZE"):
                            try:
                                min_qty = float(f.get("minQty", 0.01))
                            except (ValueError, TypeError):
                                pass
                        elif ftype == "MIN_NOTIONAL":
                            try:
                                min_notional = float(f.get("notional", 5.0))
                            except (ValueError, TypeError):
                                pass

                    new_catalog[sym] = SymbolCatalogItem(
                        symbol=sym,
                        base_asset=base,
                        quote_asset=quote,
                        price_precision=price_prec,
                        quantity_precision=qty_prec,
                        tick_size=tick_size,
                        min_qty=min_qty,
                        min_notional=min_notional,
                        status=status,
                    )

            if new_catalog:
                self._catalog = new_catalog
                self._last_catalog_refresh = time.time()
                logger.info(f"AlertMarketDataService: loaded {len(self._catalog)} active USDT perpetual contracts.")

            return self._catalog
        except Exception as e:
            msg = f"Error refreshing symbol catalog: {e}"
            logger.error(msg)
            self._last_error = msg
            self._error_count += 1
            return self._catalog

    def poll_bulk_prices(self) -> Dict[str, Dict[str, Any]]:
        """Fetch bulk mark prices and last prices in single-call operations."""
        now_ts = time.time()
        now_iso = datetime.now(timezone.utc).isoformat()
        mark_url = f"{self.base_url}/fapi/v3/premiumIndex"
        last_url = f"{self.base_url}/fapi/v3/ticker/price"

        try:
            # 1. Fetch Mark Prices
            mark_resp = self.session.get(mark_url, timeout=8, headers={"User-Agent": "SMC-SNR-Alerts/1.0"})
            if mark_resp.status_code == 200:
                mark_list = mark_resp.json()
                if isinstance(mark_list, list):
                    for item in mark_list:
                        sym = item.get("symbol", "").upper()
                        mp_str = item.get("markPrice")
                        if sym and mp_str is not None:
                            try:
                                mp_val = float(mp_str)
                                if sym not in self._prices:
                                    self._prices[sym] = {}
                                self._prices[sym]["mark_price"] = mp_val
                                self._prices[sym]["mark_timestamp"] = now_ts
                                self._prices[sym]["updated_at_utc"] = now_iso
                            except (ValueError, TypeError):
                                pass

            # 2. Fetch Last Prices
            last_resp = self.session.get(last_url, timeout=8, headers={"User-Agent": "SMC-SNR-Alerts/1.0"})
            if last_resp.status_code == 200:
                last_list = last_resp.json()
                if isinstance(last_list, list):
                    for item in last_list:
                        sym = item.get("symbol", "").upper()
                        lp_str = item.get("price")
                        if sym and lp_str is not None:
                            try:
                                lp_val = float(lp_str)
                                if sym not in self._prices:
                                    self._prices[sym] = {}
                                self._prices[sym]["last_price"] = lp_val
                                self._prices[sym]["last_timestamp"] = now_ts
                                self._prices[sym]["updated_at_utc"] = now_iso
                            except (ValueError, TypeError):
                                pass

            self._last_poll_time = now_ts
            self._poll_count += 1

            # Update catalog items with latest prices
            for sym, cat in self._catalog.items():
                p_info = self._prices.get(sym)
                if p_info:
                    cat.mark_price = p_info.get("mark_price")
                    cat.last_price = p_info.get("last_price")
                    cat.last_updated_at = p_info.get("updated_at_utc")
                    # Check staleness
                    last_seen = max(p_info.get("mark_timestamp", 0.0), p_info.get("last_timestamp", 0.0))
                    cat.is_stale = (now_ts - last_seen) > self.stale_threshold_sec

            return self._prices
        except Exception as e:
            msg = f"Error polling bulk prices: {e}"
            logger.warning(msg)
            self._last_error = msg
            self._error_count += 1
            return self._prices

    def get_symbol_price(
        self,
        symbol: str,
        source: AlertSource = AlertSource.MARK_PRICE,
    ) -> Tuple[Optional[float], bool]:
        """
        Get current price and staleness status for a symbol.
        Returns: (price: Optional[float], is_stale: bool)
        """
        sym_clean = symbol.upper().strip()
        now_ts = time.time()
        p_info = self._prices.get(sym_clean)

        if not p_info:
            return None, True

        if source == AlertSource.MARK_PRICE:
            price = p_info.get("mark_price")
            ts = p_info.get("mark_timestamp", 0.0)
        else:
            price = p_info.get("last_price")
            ts = p_info.get("last_timestamp", 0.0)

        if price is None:
            # Fallback to alternate price if available
            alt_price = p_info.get("mark_price" if source == AlertSource.LAST_PRICE else "last_price")
            alt_ts = p_info.get("mark_timestamp" if source == AlertSource.LAST_PRICE else "last_timestamp", 0.0)
            if alt_price is not None:
                is_stale = (now_ts - alt_ts) > self.stale_threshold_sec
                return alt_price, is_stale
            return None, True

        is_stale = (now_ts - ts) > self.stale_threshold_sec
        return price, is_stale

    def get_all_symbols(self) -> List[SymbolCatalogItem]:
        """Return list of all discovered eligible perpetual symbols with prices."""
        now_ts = time.time()
        result: List[SymbolCatalogItem] = []
        for sym, cat in self._catalog.items():
            p_info = self._prices.get(sym)
            if p_info:
                cat.mark_price = p_info.get("mark_price")
                cat.last_price = p_info.get("last_price")
                cat.last_updated_at = p_info.get("updated_at_utc")
                last_seen = max(p_info.get("mark_timestamp", 0.0), p_info.get("last_timestamp", 0.0))
                cat.is_stale = (now_ts - last_seen) > self.stale_threshold_sec
            else:
                cat.is_stale = True
            result.append(cat)
        # Sort alphabetically by symbol
        result.sort(key=lambda x: x.symbol)
        return result

    def get_symbol_catalog_item(self, symbol: str) -> Optional[SymbolCatalogItem]:
        """Get specifications for a specific symbol."""
        sym_clean = symbol.upper().strip()
        cat = self._catalog.get(sym_clean)
        if not cat:
            return None
        now_ts = time.time()
        p_info = self._prices.get(sym_clean)
        if p_info:
            cat.mark_price = p_info.get("mark_price")
            cat.last_price = p_info.get("last_price")
            cat.last_updated_at = p_info.get("updated_at_utc")
            last_seen = max(p_info.get("mark_timestamp", 0.0), p_info.get("last_timestamp", 0.0))
            cat.is_stale = (now_ts - last_seen) > self.stale_threshold_sec
        else:
            cat.is_stale = True
        return cat

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic health and telemetry."""
        now_ts = time.time()
        age_sec = (now_ts - self._last_poll_time) if self._last_poll_time > 0 else None
        is_healthy = (age_sec is not None and age_sec < self.stale_threshold_sec and len(self._catalog) > 0)
        return {
            "is_running": self._is_running,
            "is_healthy": is_healthy,
            "symbols_count": len(self._catalog),
            "prices_count": len(self._prices),
            "poll_count": self._poll_count,
            "error_count": self._error_count,
            "last_poll_time": datetime.fromtimestamp(self._last_poll_time, tz=timezone.utc).isoformat() if self._last_poll_time > 0 else None,
            "last_poll_age_sec": round(age_sec, 1) if age_sec is not None else None,
            "last_catalog_refresh": datetime.fromtimestamp(self._last_catalog_refresh, tz=timezone.utc).isoformat() if self._last_catalog_refresh > 0 else None,
            "last_error": self._last_error,
        }

    async def start(self) -> None:
        """Start periodic background market data polling loop."""
        if self._is_running:
            return
        self._is_running = True
        logger.info("Starting AlertMarketDataService polling loop...")
        # Initial synchronous or threaded catalog refresh
        await asyncio.to_thread(self.refresh_catalog)
        await asyncio.to_thread(self.poll_bulk_prices)
        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop market data polling loop."""
        self._is_running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info("AlertMarketDataService stopped cleanly.")

    async def _run_loop(self) -> None:
        """Async polling loop."""
        while self._is_running:
            try:
                await asyncio.sleep(self.poll_interval_sec)
                if not self._is_running:
                    break

                now_ts = time.time()
                # Periodic catalog refresh (e.g. every 30 minutes)
                if (now_ts - self._last_catalog_refresh) > self.catalog_refresh_interval_sec:
                    await asyncio.to_thread(self.refresh_catalog)

                # Poll prices in thread pool
                prices = await asyncio.to_thread(self.poll_bulk_prices)

                # Dispatch tick callbacks
                for cb in self._tick_callbacks:
                    try:
                        res = cb(prices)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as cb_err:
                        logger.error(f"Error in market data tick callback: {cb_err}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in market data polling loop: {e}")
                self._error_count += 1
                self._last_error = str(e)
                await asyncio.sleep(2.0)
