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

        # Update cache
        self._instruments_cache = {
            ex: {inst.symbol: inst for inst in insts}
            for ex, insts in results.items()
        }
        self._last_discovery_time = now
        total_discovered = sum(len(v) for v in results.values())
        logger.info(
            f"MultiExchangeService: Discovered {total_discovered} instruments across "
            f"AsterDEX ({len(results['asterdex'])}), Binance ({len(results['binance'])}), "
            f"Hyperliquid ({len(results['hyperliquid'])})."
        )
        return results

    def get_aggregated_order_books(self, symbol: str) -> Dict[str, Optional[NormalizedOrderBook]]:
        """Fetch concurrent L2 order books for a symbol across all venues that list it."""
        books: Dict[str, Optional[NormalizedOrderBook]] = {
            "asterdex": None,
            "binance": None,
            "hyperliquid": None,
        }

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self.aster.get_order_book, symbol): "asterdex",
                executor.submit(self.binance.get_order_book, symbol): "binance",
                executor.submit(self.hyperliquid.get_order_book, symbol): "hyperliquid",
            }
            for fut in as_completed(futures):
                ex = futures[fut]
                try:
                    books[ex] = fut.result()
                except Exception as err:
                    logger.debug(f"MultiExchangeService: Order book fetch failed for {symbol} on {ex}: {err}")
                    books[ex] = None

        return books

    def get_recent_trades(self, symbol: str, limit: int = 100) -> Dict[str, List[NormalizedTrade]]:
        """Fetch recent public trades across supported venues."""
        trades: Dict[str, List[NormalizedTrade]] = {
            "asterdex": [],
            "binance": [],
        }

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(self.aster.get_recent_trades, symbol, limit): "asterdex",
                executor.submit(self.binance.get_recent_trades, symbol, limit): "binance",
            }
            for fut in as_completed(futures):
                ex = futures[fut]
                try:
                    trades[ex] = fut.result()
                except Exception as err:
                    logger.debug(f"MultiExchangeService: Trades fetch failed for {symbol} on {ex}: {err}")
                    trades[ex] = []

        return trades
