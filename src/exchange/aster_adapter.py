"""AsterDEX public market data adapter.

Provides perpetual universe discovery, Level-2 order book depth, and recent trades.
Reuses existing AsterDEX infrastructure and operates strictly in READ-ONLY mode.
"""

from typing import Any, Dict, List, Optional
import math
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

            # Fetch 24-hour ticker turnover data to populate quote-volume
            ticker_map: Dict[str, Dict[str, Any]] = {}
            ticker_url = f"{self.base_url}/fapi/v1/ticker/24hr"
            try:
                t_resp = self.session.get(ticker_url, timeout=self.timeout_sec)
                if t_resp.status_code == 200:
                    t_data = t_resp.json()
                    if isinstance(t_data, list):
                        for item in t_data:
                            if isinstance(item, dict) and "symbol" in item:
                                sym_key = str(item["symbol"]).strip().upper()
                                # Handle duplicate ticker records by prioritizing later closeTime
                                existing = ticker_map.get(sym_key)
                                if existing is None or int(item.get("closeTime", 0)) >= int(existing.get("closeTime", 0)):
                                    ticker_map[sym_key] = item
                    elif isinstance(t_data, dict) and "symbol" in t_data:
                        ticker_map[str(t_data["symbol"]).strip().upper()] = t_data
                else:
                    logger.warning(f"AsterAdapter: ticker/24hr returned status {t_resp.status_code}")
            except Exception as t_err:
                logger.warning(f"AsterAdapter: Failed to fetch ticker/24hr: {t_err}")

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

                # Parse 24h ticker data strictly using quoteVolume (USDT notional, not base asset)
                vol_24h = None
                vol_status = "UNAVAILABLE"
                eligibility_reason = None
                last_price = None

                ticker_item = ticker_map.get(sym.upper())
                if ticker_item:
                    try:
                        raw_quote_vol = ticker_item.get("quoteVolume")
                        if raw_quote_vol is not None and str(raw_quote_vol).strip() != "":
                            vol_val = float(raw_quote_vol)
                            if vol_val >= 0.0 and math.isfinite(vol_val):
                                vol_24h = vol_val
                                vol_status = "AVAILABLE"
                            else:
                                vol_status = "MALFORMED"
                                eligibility_reason = f"Negative or non-finite quoteVolume: {raw_quote_vol}"
                        else:
                            vol_status = "MISSING_FIELD"
                            eligibility_reason = "quoteVolume missing in ticker record"

                        raw_last_px = ticker_item.get("lastPrice")
                        if raw_last_px is not None:
                            last_price = float(raw_last_px)
                    except (ValueError, TypeError) as parse_err:
                        vol_status = "PARSE_ERROR"
                        eligibility_reason = f"Failed to parse quoteVolume: {parse_err}"
                else:
                    vol_status = "UNMATCHED_TICKER"
                    eligibility_reason = "No matching 24hr ticker record returned by exchange"

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
                    last_price=last_price,
                    volume_24h_usdt=vol_24h,
                    is_active=True,
                    volume_status=vol_status,
                    eligibility_reason=eligibility_reason,
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

    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Candle]:
        """Fetch candlestick bars for a symbol and timeframe from AsterDEX."""
        clean_symbol = symbol.upper().replace(".P", "")
        url = f"{self.base_url}/fapi/v1/klines"
        params: Dict[str, Any] = {
            "symbol": clean_symbol,
            "interval": timeframe,
            "limit": min(limit, 1000),
        }
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                logger.warning(f"AsterAdapter: klines for {symbol} returned status {resp.status_code}")
                return []
            raw_klines = resp.json()
            if not isinstance(raw_klines, list):
                return []
            candles: List[Candle] = []
            for row in raw_klines:
                try:
                    candles.append(Candle.from_raw_kline(row, clean_symbol, timeframe))
                except (ValueError, IndexError):
                    continue
            return candles
        except Exception as err:
            logger.warning(f"AsterAdapter: Failed to fetch klines for {symbol}: {err}")
            return []

    def get_open_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch current open interest for a symbol from AsterDEX."""
        clean_symbol = symbol.upper().replace(".P", "")
        url = f"{self.base_url}/fapi/v1/openInterest"
        params = {"symbol": clean_symbol}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if isinstance(data, dict) and "openInterest" in data:
                return {
                    "symbol": clean_symbol,
                    "open_interest": float(data["openInterest"]),
                    "timestamp_ms": int(data.get("time", int(time.time() * 1000))),
                }
            return None
        except Exception as err:
            logger.warning(f"AsterAdapter: Failed to fetch open interest for {symbol}: {err}")
            return None

    def get_premium_index(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch mark price, index price, and funding rate from AsterDEX."""
        clean_symbol = symbol.upper().replace(".P", "")
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        params = {"symbol": clean_symbol}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if isinstance(data, dict):
                return {
                    "symbol": clean_symbol,
                    "mark_price": float(data.get("markPrice", 0.0)),
                    "index_price": float(data.get("indexPrice", 0.0)),
                    "last_funding_rate": float(data.get("lastFundingRate", 0.0)),
                    "interest_rate": float(data.get("interestRate", 0.0)),
                    "next_funding_time_ms": int(data.get("nextFundingTime", 0)),
                    "timestamp_ms": int(data.get("time", int(time.time() * 1000))),
                }
            return None
        except Exception as err:
            logger.warning(f"AsterAdapter: Failed to fetch premium index for {symbol}: {err}")
            return None

    def get_funding_rates(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch historical funding rate records from AsterDEX."""
        clean_symbol = symbol.upper().replace(".P", "")
        url = f"{self.base_url}/fapi/v1/fundingRate"
        params = {"symbol": clean_symbol, "limit": min(limit, 100)}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if isinstance(data, list):
                return [
                    {
                        "symbol": clean_symbol,
                        "funding_rate": float(item.get("fundingRate", 0.0)),
                        "funding_time_ms": int(item.get("fundingTime", 0)),
                    }
                    for item in data
                    if isinstance(item, dict) and "fundingRate" in item
                ]
            return []
        except Exception as err:
            logger.warning(f"AsterAdapter: Failed to fetch funding rates for {symbol}: {err}")
            return []

    def get_bulk_market_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Fetch bulk ticker and bookTicker in a single pass to build Stage 1 market snapshots for all AsterDEX symbols."""
        snapshots: Dict[str, Dict[str, Any]] = {}
        now_ms = int(time.time() * 1000)

        # 1. Fetch bookTicker for instant live spread and bid/ask
        bt_map: Dict[str, Dict[str, Any]] = {}
        try:
            resp = self.session.get(f"{self.base_url}/fapi/v1/ticker/bookTicker", timeout=self.timeout_sec)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "symbol" in item:
                            bt_map[str(item["symbol"]).strip().upper()] = item
        except Exception as e:
            logger.debug(f"AsterAdapter: Failed to fetch bulk bookTicker: {e}")

        # 2. Fetch 24hr ticker for volume and price change
        t24_map: Dict[str, Dict[str, Any]] = {}
        try:
            resp = self.session.get(f"{self.base_url}/fapi/v1/ticker/24hr", timeout=self.timeout_sec)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "symbol" in item:
                            t24_map[str(item["symbol"]).strip().upper()] = item
        except Exception as e:
            logger.debug(f"AsterAdapter: Failed to fetch bulk 24hr ticker: {e}")

        # 3. Combine into normalized Stage 1 snapshots
        all_symbols = set(bt_map.keys()) | set(t24_map.keys())
        for sym in all_symbols:
            bt = bt_map.get(sym, {})
            t24 = t24_map.get(sym, {})

            try:
                bid_px = float(bt.get("bidPrice", 0.0) or 0.0)
            except (ValueError, TypeError):
                bid_px = 0.0

            try:
                ask_px = float(bt.get("askPrice", 0.0) or 0.0)
            except (ValueError, TypeError):
                ask_px = 0.0

            mid_px = (bid_px + ask_px) / 2.0 if (bid_px > 0 and ask_px > 0) else (bid_px or ask_px)
            spread_bps = ((ask_px - bid_px) / mid_px * 10000.0) if (mid_px > 0 and ask_px >= bid_px) else 0.0

            raw_vol = t24.get("quoteVolume")
            try:
                vol_24h = float(raw_vol) if raw_vol is not None and math.isfinite(float(raw_vol)) else 0.0
            except (ValueError, TypeError):
                vol_24h = 0.0

            raw_change = t24.get("priceChangePercent")
            try:
                change_pct = float(raw_change) if raw_change is not None and math.isfinite(float(raw_change)) else 0.0
            except (ValueError, TypeError):
                change_pct = 0.0

            try:
                last_px = float(t24.get("lastPrice", mid_px) or mid_px)
            except (ValueError, TypeError):
                last_px = mid_px

            snapshots[sym] = {
                "symbol": sym,
                "bid_price": bid_px,
                "ask_price": ask_px,
                "mid_price": mid_px,
                "last_price": last_px,
                "spread_bps": round(spread_bps, 2),
                "volume_24h_usdt": round(vol_24h, 2),
                "price_change_pct": round(change_pct, 2),
                "timestamp_ms": int(bt.get("time", now_ms) or now_ms),
            }

        return snapshots

