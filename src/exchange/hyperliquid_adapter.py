"""Hyperliquid public market data adapter.

Provides public POST /info queries for perpetual universe discovery and L2 order book depth.
Operates strictly in READ-ONLY mode for liquidity telemetry and research.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from src.core.logger import logger
from src.core.types import Candle
from src.exchange.normalized import (
    NormalizedInstrument,
    NormalizedOrderBook,
    NormalizedTrade,
    OrderBookLevel,
)


class HyperliquidAdapter:
    """Public read-only adapter for Hyperliquid L1/L2 perpetual markets."""

    DEFAULT_INFO_URL = "https://api.hyperliquid.xyz/info"

    def __init__(
        self,
        info_url: str = DEFAULT_INFO_URL,
        timeout_sec: float = 8.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.info_url = info_url
        self.timeout_sec = timeout_sec

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            self.session.headers.update({'User-Agent': 'SignalGenerator/1.0', 'Content-Type': 'application/json', 'Accept': 'application/json'})
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
        """Fetch all eligible active perpetual contracts from Hyperliquid."""
        now = time.time()
        if not force_refresh and self._catalog_cache and (now - self._last_catalog_refresh < 1800.0):
            return list(self._catalog_cache.values())

        payload = {"type": "metaAndAssetCtxs"}
        try:
            resp = self.session.post(self.info_url, json=payload, timeout=self.timeout_sec)
            if resp.status_code != 200:
                logger.warning(f"Hyperliquid: metaAndAssetCtxs returned status {resp.status_code}")
                return list(self._catalog_cache.values())

            data = resp.json()
            if not isinstance(data, list) or len(data) < 2:
                return list(self._catalog_cache.values())

            meta = data[0]
            asset_ctxs = data[1]
            universe = meta.get("universe", [])

            discovered: Dict[str, NormalizedInstrument] = {}
            for i, coin_meta in enumerate(universe):
                coin_name = coin_meta.get("name")
                if not coin_name:
                    continue

                sz_decimals = int(coin_meta.get("szDecimals", 2))
                step_size = 10.0 ** (-sz_decimals)

                # Pair with asset context if available
                ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
                mark_px = float(ctx.get("markPx", 0.0)) if ctx.get("markPx") else None
                funding = float(ctx.get("funding", 0.0)) if ctx.get("funding") else None
                vol_24h = float(ctx.get("dayNtlVlm", 0.0)) if ctx.get("dayNtlVlm") else None

                # Hyperliquid quotes against USD; map to {COIN}USDT for cross-exchange parity
                standard_symbol = f"{coin_name}USDT"

                inst = NormalizedInstrument(
                    symbol=standard_symbol,
                    exchange_name="hyperliquid",
                    base_asset=coin_name,
                    quote_asset="USDT",
                    min_qty=step_size,
                    step_size=step_size,
                    tick_size=0.01,
                    min_notional=10.0,
                    contract_type="PERPETUAL",
                    funding_rate=funding,
                    funding_interval_hours=1,  # Hyperliquid funding settles hourly
                    mark_price=mark_px,
                    last_price=mark_px,
                    volume_24h_usdt=vol_24h,
                    is_active=True,
                )
                discovered[standard_symbol] = inst

            self._catalog_cache = discovered
            self._last_catalog_refresh = now
            logger.info(f"Hyperliquid: Discovered {len(discovered)} active perpetual markets.")
            return list(discovered.values())

        except Exception as err:
            logger.error(f"Hyperliquid: Failed to discover instruments: {err}")
            return list(self._catalog_cache.values())

    def get_premium_index(self, symbol: str):
        coin = symbol.removesuffix('USDT')
        if self._catalog_cache and (symbol not in self._catalog_cache and coin not in self._catalog_cache):
            return None
        now_ms = int(time.time() * 1000)
        try:
            resp = self.session.post(self.info_url, json={'type': 'allMids'}, timeout=self.timeout_sec)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    mid = data.get(coin)
                    if mid is not None:
                        px = float(mid)
                        return {'symbol': symbol, 'mark_price': px, 'index_price': px, 'last_funding_rate': None, 'timestamp_ms': now_ms}
        except Exception as err:
            logger.debug(f'Hyperliquid: get_premium_index failed for {symbol}: {err}')
        return None

    def get_order_book(self, symbol: str, limit: Optional[int] = 50) -> Optional[NormalizedOrderBook]:
        """Fetch Level-2 order book depth from Hyperliquid.
        
        Args:
            symbol: Target perpetual symbol (e.g. 'BTCUSDT' or 'BTC').
            limit: Maximum depth levels to return on each side. If None, returns all levels.
                   If limit <= 0, returns empty bids and asks.
        """
        # Symbol could be 'BTCUSDT' -> strip USDT to get Hyperliquid coin 'BTC'
        coin = symbol.removesuffix("USDT")
        # Optimization: If catalog is populated and coin is not listed on Hyperliquid, skip immediately
        if self._catalog_cache and (symbol not in self._catalog_cache and coin not in self._catalog_cache):
            return None

        payload = {"type": "l2Book", "coin": coin}
        now_ms = int(time.time() * 1000)

        try:
            resp = self.session.post(self.info_url, json=payload, timeout=self.timeout_sec)
            if resp.status_code != 200:
                safe_sym = symbol.encode("ascii", "replace").decode("ascii")
                logger.debug(f"Hyperliquid: l2Book for {safe_sym} returned status {resp.status_code}")
                return None

            data = resp.json()
            if not isinstance(data, dict):
                return None
            levels = data.get("levels", [])
            if not isinstance(levels, list) or len(levels) < 2:
                return None

            raw_bids = levels[0]  # list of {px, sz, n}
            raw_asks = levels[1]  # list of {px, sz, n}

            bids = [
                OrderBookLevel(price=float(item["px"]), size=float(item["sz"]))
                for item in raw_bids
            ]
            asks = [
                OrderBookLevel(price=float(item["px"]), size=float(item["sz"]))
                for item in raw_asks
            ]

            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price, reverse=False)

            if limit is not None:
                if limit <= 0:
                    bids = []
                    asks = []
                else:
                    bids = bids[:limit]
                    asks = asks[:limit]

            return NormalizedOrderBook(
                symbol=symbol,
                exchange_name="hyperliquid",
                timestamp_ms=int(data.get("time") or now_ms),
                bids=bids,
                asks=asks,
            )
        except Exception as err:
            safe_sym = symbol.encode("ascii", "replace").decode("ascii")
            logger.debug(f"Hyperliquid: Failed to fetch order book for {safe_sym}: {err}")
            return None

    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[NormalizedTrade]:
        """Fetch recent public trades for perpetual contract from Hyperliquid.
        
        Args:
            symbol: Target symbol e.g. 'BTCUSDT' or 'BTC'.
            limit: Maximum trades to return.
        """
        coin = symbol.removesuffix("USDT")
        # Optimization: If catalog is populated and coin is not listed on Hyperliquid, skip immediately
        if self._catalog_cache and (symbol not in self._catalog_cache and coin not in self._catalog_cache):
            return []

        payload = {"type": "recentTrades", "coin": coin}

        try:
            resp = self.session.post(self.info_url, json=payload, timeout=self.timeout_sec)
            if resp.status_code != 200:
                safe_sym = symbol.encode("ascii", "replace").decode("ascii")
                logger.debug(f"Hyperliquid: recentTrades for {safe_sym} returned status {resp.status_code}")
                return []

            data = resp.json()
            if not isinstance(data, list):
                return []

            trades: List[NormalizedTrade] = []
            for item in data[:limit]:
                try:
                    px = float(item["px"])
                    sz = float(item["sz"])
                    # Hyperliquid: 'B' indicates aggressor buyer (crossed ask), 'A' aggressor seller (hit bid)
                    raw_side = str(item.get("side", "")).upper()
                    if raw_side == "B":
                        side = "BUY"
                    elif raw_side == "A":
                        side = "SELL"
                    else:
                        side = "UNKNOWN"

                    trade_id = str(item.get("tid") or item.get("hash") or "")
                    ts_ms = int(item.get("time", 0))

                    trades.append(
                        NormalizedTrade(
                            trade_id=trade_id,
                            symbol=symbol,
                            exchange_name="hyperliquid",
                            timestamp_ms=ts_ms,
                            price=px,
                            size=sz,
                            notional_usdt=px * sz,
                            side=side,
                        )
                    )
                except (ValueError, KeyError, TypeError) as parse_err:
                    logger.debug(f"Hyperliquid: Skipped malformed trade item for {symbol}: {parse_err}")
                    continue

            return trades
        except Exception as err:
            logger.warning(f"Hyperliquid: Failed to fetch trades for {symbol}: {err}")
            return []

    def get_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Candle]:
        """Fetch historical candle snapshots from Hyperliquid.
        
        Args:
            symbol: Target perpetual symbol (e.g. 'BTCUSDT' or 'BTC').
            timeframe: Interval string, e.g. '15m', '1h', '4h', '1d'.
            limit: Maximum candles to return (capped at 500).
            start_time_ms: Start timestamp in milliseconds.
            end_time_ms: End timestamp in milliseconds.
        """
        coin = symbol.removesuffix("USDT")
        if self._catalog_cache and (symbol not in self._catalog_cache and coin not in self._catalog_cache):
            return []

        # Hyperliquid interval mapping
        tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
        interval = tf_map.get(timeframe.lower(), "1h")

        now_ms = int(time.time() * 1000)
        # Default start_time_ms if not provided based on limit and timeframe
        if not start_time_ms:
            tf_sec = 3600
            if interval == "1m": tf_sec = 60
            elif interval == "5m": tf_sec = 300
            elif interval == "15m": tf_sec = 900
            elif interval == "4h": tf_sec = 14400
            elif interval == "1d": tf_sec = 86400
            start_time_ms = max(0, (end_time_ms or now_ms) - (limit * tf_sec * 1000))

        req_body: Dict[str, Any] = {
            "coin": coin,
            "interval": interval,
            "startTime": start_time_ms,
        }
        if end_time_ms:
            req_body["endTime"] = end_time_ms

        payload = {"type": "candleSnapshot", "req": req_body}

        try:
            resp = self.session.post(self.info_url, json=payload, timeout=self.timeout_sec)
            if resp.status_code != 200:
                logger.debug(f"Hyperliquid: candleSnapshot for {symbol} returned status {resp.status_code}")
                return []

            data = resp.json()
            if not isinstance(data, list):
                return []

            candles: List[Candle] = []
            for item in data[-limit:]:
                try:
                    open_time_ms = int(item["t"])
                    close_time_ms = int(item.get("T", open_time_ms + (tf_sec * 1000) - 1))
                    open_px = float(item["o"])
                    high_px = float(item["h"])
                    low_px = float(item["l"])
                    close_px = float(item["c"])
                    vol = float(item.get("v", 0.0))
                    candles.append(
                        Candle(
                            symbol=symbol,
                            timeframe=interval,
                            timestamp_ms=open_time_ms,
                            close_time_ms=close_time_ms,
                            open=open_px,
                            high=high_px,
                            low=low_px,
                            close=close_px,
                            volume=vol,
                            quote_volume=round(vol * close_px, 2),
                            trades_count=int(item.get("n", 0)),
                            is_closed=True,
                        )
                    )
                except (ValueError, KeyError, TypeError) as parse_err:
                    logger.debug(f"Hyperliquid: skipped malformed candle for {symbol}: {parse_err}")
                    continue

            return candles
        except Exception as err:
            logger.warning(f"Hyperliquid: Failed to fetch candles for {symbol}: {err}")
            return []


