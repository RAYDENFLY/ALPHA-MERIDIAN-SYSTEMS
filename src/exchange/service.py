"""Multi-Exchange Discovery and Unified Liquidity Service for Model B.

Orchestrates multi-exchange instrument discovery, order book polling, and trade stream ingestion
across verified venues (AsterDEX, Binance USD(S)-M, Hyperliquid).
Strictly READ-ONLY. No live order placement.
"""

from typing import Any, Dict, List, Optional
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.core.logger import logger
from src.exchange.aster_adapter import AsterAdapter
from src.exchange.binance_adapter import BinanceFuturesAdapter
from src.exchange.hyperliquid_adapter import HyperliquidAdapter
from src.exchange.normalized import (
    NormalizedInstrument,
    NormalizedOrderBook,
    NormalizedTrade,
)


class MultiExchangeService:
    """Coordinates public market data and order book depth across perpetual exchanges."""

    def __init__(
        self,
        aster_adapter: Optional[AsterAdapter] = None,
        binance_adapter: Optional[BinanceFuturesAdapter] = None,
        hyperliquid_adapter: Optional[HyperliquidAdapter] = None,
        max_workers: int = 8,
    ) -> None:
        self.aster = aster_adapter or AsterAdapter()
        self.binance = binance_adapter or BinanceFuturesAdapter()
        self.hyperliquid = hyperliquid_adapter or HyperliquidAdapter()
        self.max_workers = max_workers

        self._instruments_cache: Dict[str, Dict[str, NormalizedInstrument]] = {}
        self._last_discovery_time: float = 0.0

        # Cycle-level short TTL snapshot caches for eliminating redundant requests
        self._books_cache: Dict[str, Tuple[float, Dict[str, Optional[NormalizedOrderBook]]]] = {}
        self._trades_cache: Dict[str, Tuple[float, Dict[str, List[NormalizedTrade]]]] = {}
        self.cache_ttl_sec: float = 5.0

    def discover_all_instruments(self, force_refresh: bool = False) -> Dict[str, List[NormalizedInstrument]]:
        """Discover active perpetual contracts across all enabled venues."""
        now = time.time()
        if not force_refresh and self._instruments_cache and (now - self._last_discovery_time < 1800.0):
            return {ex: list(insts.values()) for ex, insts in self._instruments_cache.items()}

        results: Dict[str, List[NormalizedInstrument]] = {
            "asterdex": [],
            "binance": [],
            "hyperliquid": [],
        }

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self.aster.discover_instruments, force_refresh): "asterdex",
                executor.submit(self.binance.discover_instruments, force_refresh): "binance",
                executor.submit(self.hyperliquid.discover_instruments, force_refresh): "hyperliquid",
            }
            for fut in as_completed(futures):
                ex = futures[fut]
                try:
                    insts = fut.result()
                    results[ex] = insts
                except Exception as err:
                    logger.error(f"MultiExchangeService: Discovery failed for {ex}: {err}")
                    results[ex] = []

        # Update cache (preserve prior valid cache for any venue that returned empty due to transient error)
        updated_cache = dict(self._instruments_cache)
        for ex, insts in results.items():
            if insts or ex not in updated_cache:
                updated_cache[ex] = {inst.symbol: inst for inst in insts}
        self._instruments_cache = updated_cache
        # Only advance full discovery TTL if all primary venues returned non-empty results
        if all(len(v) > 0 for v in results.values()):
            self._last_discovery_time = now
        total_discovered = sum(len(v) for v in results.values())
        logger.info(
            f"MultiExchangeService: Discovered {total_discovered} instruments across "
            f"AsterDEX ({len(results['asterdex'])}), Binance ({len(results['binance'])}), "
            f"Hyperliquid ({len(results['hyperliquid'])})."
        )
        return results

    def get_aggregated_order_books(self, symbol: str, force_refresh: bool = False) -> Dict[str, Optional[NormalizedOrderBook]]:
        """Fetch concurrent L2 order books for a symbol across verified venues that list it.
        
        Reuses recent snapshot if within cache_ttl_sec to eliminate duplicate in-flight requests.
        """
        now = time.time()
        sym_clean = symbol.upper()

        if not force_refresh and sym_clean in self._books_cache:
            ts, cached_books = self._books_cache[sym_clean]
            if (now - ts) < self.cache_ttl_sec:
                return cached_books

        books: Dict[str, Optional[NormalizedOrderBook]] = {
            "asterdex": None,
            "binance": None,
            "hyperliquid": None,
        }

        # Smart venue routing: Only query venues that actually list this contract
        tasks: Dict[Any, str] = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            # AsterDEX query
            if not self._instruments_cache or sym_clean in self._instruments_cache.get("asterdex", {}):
                tasks[executor.submit(self.aster.get_order_book, symbol)] = "asterdex"

            # Binance query: check local or shared Binance catalog to avoid querying unlisted AsterDEX-only symbols
            binance_insts = self._instruments_cache.get("binance") or getattr(self.binance, "_shared_catalog_cache", {})
            if not binance_insts or sym_clean in binance_insts:
                tasks[executor.submit(self.binance.get_order_book, symbol)] = "binance"

            # Hyperliquid query
            hl_cache = self._instruments_cache.get("hyperliquid", {})
            coin = sym_clean.removesuffix("USDT")
            if not self._instruments_cache or sym_clean in hl_cache or coin in hl_cache:
                tasks[executor.submit(self.hyperliquid.get_order_book, symbol)] = "hyperliquid"

            for fut in as_completed(tasks):
                ex = tasks[fut]
                try:
                    books[ex] = fut.result()
                except Exception as err:
                    logger.debug(f"MultiExchangeService: Order book fetch failed for {symbol} on {ex}: {err}")
                    books[ex] = None

        self._books_cache[sym_clean] = (now, books)
        return books

    def get_recent_trades(self, symbol: str, limit: int = 100, force_refresh: bool = False) -> Dict[str, List[NormalizedTrade]]:
        """Fetch recent public trades across verified venues that list the symbol."""
        now = time.time()
        sym_clean = symbol.upper()

        if not force_refresh and sym_clean in self._trades_cache:
            ts, cached_trades = self._trades_cache[sym_clean]
            if (now - ts) < self.cache_ttl_sec:
                return cached_trades

        trades: Dict[str, List[NormalizedTrade]] = {
            "asterdex": [],
            "binance": [],
            "hyperliquid": [],
        }

        tasks: Dict[Any, str] = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            if not self._instruments_cache or sym_clean in self._instruments_cache.get("asterdex", {}):
                tasks[executor.submit(self.aster.get_recent_trades, symbol, limit)] = "asterdex"

            binance_insts = self._instruments_cache.get("binance") or getattr(self.binance, "_shared_catalog_cache", {})
            if not binance_insts or sym_clean in binance_insts:
                tasks[executor.submit(self.binance.get_recent_trades, symbol, limit)] = "binance"

            hl_cache = self._instruments_cache.get("hyperliquid", {})
            coin = sym_clean.removesuffix("USDT")
            if not self._instruments_cache or sym_clean in hl_cache or coin in hl_cache:
                tasks[executor.submit(self.hyperliquid.get_recent_trades, symbol, limit)] = "hyperliquid"

            for fut in as_completed(tasks):
                ex = tasks[fut]
                try:
                    trades[ex] = fut.result()
                except Exception as err:
                    logger.debug(f"MultiExchangeService: Trades fetch failed for {symbol} on {ex}: {err}")
                    trades[ex] = []

        self._trades_cache[sym_clean] = (now, trades)
        return trades
