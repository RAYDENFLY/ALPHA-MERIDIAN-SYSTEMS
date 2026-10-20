"""Multi-Exchange Perpetual Futures Market Screener Service.

Aggregates public market data across AsterDEX, Binance USD(S)-M Futures,
Bybit Linear Perpetuals, and Hyperliquid.
Provides comprehensive cross-exchange comparison, normalized quote turnover,
open interest, funding rates (with normalized 8h & APR comparisons), bid-ask spreads,
and cross-venue price divergence.
Strictly READ-ONLY.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import logging
import math
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from src.exchange.aster_adapter import AsterAdapter
from src.exchange.binance_adapter import BinanceFuturesAdapter
from src.exchange.bybit_adapter import BybitLinearAdapter
from src.exchange.hyperliquid_adapter import HyperliquidAdapter
from src.screener.types import (
    CrossExchangeMarketAsset,
    ScreenerFilterParams,
    VenueMarketMetrics,
    VenueStatusTelemetry,
)

logger = logging.getLogger(__name__)


def normalize_symbol_key(raw_symbol: str) -> Tuple[str, str, str]:
    """Normalize exchange-specific symbol into standard (normalized_symbol, base_asset, quote_asset).
    
    Examples:
    - 'BTCUSDT' -> ('BTCUSDT', 'BTC', 'USDT')
    - 'BTC' -> ('BTCUSDT', 'BTC', 'USDT')
    - '1000PEPEUSDT' -> ('1000PEPEUSDT', '1000PEPE', 'USDT')
    """
    sym = raw_symbol.strip().upper().replace(".P", "").replace("-PERP", "")
    if sym.endswith("USDT"):
        base = sym[:-4]
        quote = "USDT"
    elif sym.endswith("USD"):
        base = sym[:-3]
        quote = "USDT"  # Standardize USD perps to USDT notional parity
    else:
        base = sym
        quote = "USDT"
    
    norm_symbol = f"{base}{quote}"
    return norm_symbol, base, quote


class MultiExchangeMarketScreener:
    """Service orchestrating cross-exchange market comparison."""

    def __init__(
        self,
        aster_adapter: Optional[AsterAdapter] = None,
        binance_adapter: Optional[BinanceFuturesAdapter] = None,
        bybit_adapter: Optional[BybitLinearAdapter] = None,
        hyperliquid_adapter: Optional[HyperliquidAdapter] = None,
        cache_ttl_sec: float = 20.0,
        request_timeout: float = 6.0,
    ) -> None:
        self.aster = aster_adapter or AsterAdapter()
        self.binance = binance_adapter or BinanceFuturesAdapter()
        self.bybit = bybit_adapter or BybitLinearAdapter()
        self.hyperliquid = hyperliquid_adapter or HyperliquidAdapter()

        self.cache_ttl_sec = cache_ttl_sec
        self.timeout_sec = request_timeout

        self._session = requests.Session()
        retries = Retry(
            total=2,
            backoff_factor=0.2,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=15, pool_maxsize=30)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

        self._lock = threading.Lock()
        self._cached_assets: Dict[str, CrossExchangeMarketAsset] = {}
        self._last_fetch_time: float = 0.0
        self._venue_telemetry: Dict[str, VenueStatusTelemetry] = {
            "asterdex": VenueStatusTelemetry(exchange_name="asterdex"),
            "binance": VenueStatusTelemetry(exchange_name="binance"),
            "bybit": VenueStatusTelemetry(exchange_name="bybit"),
            "hyperliquid": VenueStatusTelemetry(exchange_name="hyperliquid"),
        }
        
        # Historical snapshot ring buffer for 1H OI delta estimation
        self._historical_oi_snapshots: List[Tuple[float, Dict[str, float]]] = []

    def _fetch_asterdex_data(self) -> List[VenueMarketMetrics]:
        """Fetch bulk market data from AsterDEX V3 REST."""
        t0 = time.time()
        base_url = self.aster.base_url.rstrip("/")
        items: List[VenueMarketMetrics] = []
        try:
            # Concurrent fetch for Aster 24hr, bookTicker, premiumIndex
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_24 = pool.submit(self._session.get, f"{base_url}/fapi/v1/ticker/24hr", timeout=self.timeout_sec)
                f_bt = pool.submit(self._session.get, f"{base_url}/fapi/v1/ticker/bookTicker", timeout=self.timeout_sec)
                f_pi = pool.submit(self._session.get, f"{base_url}/fapi/v1/premiumIndex", timeout=self.timeout_sec)

                r_24 = f_24.result()
                r_bt = f_bt.result()
                r_pi = f_pi.result()

            latency = (time.time() - t0) * 1000.0
            now_ms = int(time.time() * 1000)

            t24_map: Dict[str, Dict[str, Any]] = {}
            if r_24.status_code == 200:
                d = r_24.json()
                if isinstance(d, list):
                    for row in d:
                        if isinstance(row, dict) and "symbol" in row:
                            t24_map[str(row["symbol"]).upper()] = row

            bt_map: Dict[str, Dict[str, Any]] = {}
            if r_bt.status_code == 200:
                d = r_bt.json()
                if isinstance(d, list):
                    for row in d:
                        if isinstance(row, dict) and "symbol" in row:
                            bt_map[str(row["symbol"]).upper()] = row

            pi_map: Dict[str, Dict[str, Any]] = {}
            if r_pi.status_code == 200:
                d = r_pi.json()
                if isinstance(d, list):
                    for row in d:
                        if isinstance(row, dict) and "symbol" in row:
                            pi_map[str(row["symbol"]).upper()] = row

            all_symbols = set(t24_map.keys()) | set(bt_map.keys()) | set(pi_map.keys())
            for sym in all_symbols:
                t24 = t24_map.get(sym, {})
                bt = bt_map.get(sym, {})
                pi = pi_map.get(sym, {})

                last_px = float(t24.get("lastPrice", 0)) if t24.get("lastPrice") else None
                quote_vol = float(t24.get("quoteVolume", 0)) if t24.get("quoteVolume") else None
                chg_pct = float(t24.get("priceChangePercent", 0)) if t24.get("priceChangePercent") else None
                
                mark_px = float(pi.get("markPrice", 0)) if pi.get("markPrice") else last_px
                index_px = float(pi.get("indexPrice", 0)) if pi.get("indexPrice") else None
                fr = float(pi.get("lastFundingRate", 0)) if pi.get("lastFundingRate") is not None else None
                next_ft = int(pi.get("nextFundingTime", 0)) if pi.get("nextFundingTime") else None

                best_b = float(bt.get("bidPrice", 0)) if bt.get("bidPrice") else None
                best_a = float(bt.get("askPrice", 0)) if bt.get("askPrice") else None
                b_qty = float(bt.get("bidQty", 0)) if bt.get("bidQty") else None
                a_qty = float(bt.get("askQty", 0)) if bt.get("askQty") else None

                spread_pct = None
                spread_bps = None
                if best_b and best_a and best_b > 0 and best_a >= best_b:
                    mid = (best_b + best_a) / 2.0
                    spread_pct = ((best_a - best_b) / mid) * 100.0
                    spread_bps = spread_pct * 100.0

                # 8h standard interval on AsterDEX
                norm_fr = fr
                ann_fr = (fr * 3 * 365 * 100.0) if fr is not None else None

                items.append(
                    VenueMarketMetrics(
                        symbol=sym,
                        exchange="asterdex",
                        contract_type="PERPETUAL",
                        last_price=last_px,
                        mark_price=mark_px,
                        index_price=index_px,
                        price_change_24h_pct=chg_pct,
                        volume_24h_usdt=quote_vol,
                        funding_rate=fr,
                        funding_interval_hours=8,
                        normalized_8h_funding_rate=norm_fr,
                        annualized_funding_pct=ann_fr,
                        next_funding_time_ms=next_ft,
                        best_bid=best_b,
                        best_ask=best_a,
                        bid_qty=b_qty,
                        ask_qty=a_qty,
                        spread_pct=spread_pct,
                        spread_bps=spread_bps,
                        timestamp_ms=now_ms,
                        latency_ms=latency,
                        status="AVAILABLE",
                    )
                )

            self._venue_telemetry["asterdex"] = VenueStatusTelemetry(
                exchange_name="asterdex",
                status="AVAILABLE",
                latency_ms=latency,
                last_successful_fetch_utc=datetime.now(timezone.utc).isoformat(),
                total_contracts=len(items),
            )
            return items

        except Exception as e:
            logger.warning(f"Screener: AsterDEX fetch failed: {e}")
            self._venue_telemetry["asterdex"] = VenueStatusTelemetry(
                exchange_name="asterdex",
                status="ERROR",
                latency_ms=(time.time() - t0) * 1000.0,
                error_message=str(e),
            )
            return []

    def _fetch_binance_data(self) -> List[VenueMarketMetrics]:
        """Fetch bulk market data from Binance USD(S)-M Futures REST."""
        t0 = time.time()
        base_url = "https://fapi.binance.com"
        items: List[VenueMarketMetrics] = []
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_24 = pool.submit(self._session.get, f"{base_url}/fapi/v1/ticker/24hr", timeout=self.timeout_sec)
                f_bt = pool.submit(self._session.get, f"{base_url}/fapi/v1/ticker/bookTicker", timeout=self.timeout_sec)
                f_pi = pool.submit(self._session.get, f"{base_url}/fapi/v1/premiumIndex", timeout=self.timeout_sec)

                r_24 = f_24.result()
                r_bt = f_bt.result()
                r_pi = f_pi.result()

            latency = (time.time() - t0) * 1000.0
            now_ms = int(time.time() * 1000)

            t24_map: Dict[str, Dict[str, Any]] = {}
            if r_24.status_code == 200:
                d = r_24.json()
                if isinstance(d, list):
                    for row in d:
                        if isinstance(row, dict) and "symbol" in row:
                            t24_map[str(row["symbol"]).upper()] = row

            bt_map: Dict[str, Dict[str, Any]] = {}
            if r_bt.status_code == 200:
                d = r_bt.json()
                if isinstance(d, list):
                    for row in d:
                        if isinstance(row, dict) and "symbol" in row:
                            bt_map[str(row["symbol"]).upper()] = row

            pi_map: Dict[str, Dict[str, Any]] = {}
            if r_pi.status_code == 200:
                d = r_pi.json()
                if isinstance(d, list):
                    for row in d:
                        if isinstance(row, dict) and "symbol" in row:
                            pi_map[str(row["symbol"]).upper()] = row

            all_symbols = set(t24_map.keys()) | set(bt_map.keys()) | set(pi_map.keys())
            for sym in all_symbols:
                if not sym.endswith("USDT"):
                    continue

                t24 = t24_map.get(sym, {})
                bt = bt_map.get(sym, {})
                pi = pi_map.get(sym, {})

                last_px = float(t24.get("lastPrice", 0)) if t24.get("lastPrice") else None
                quote_vol = float(t24.get("quoteVolume", 0)) if t24.get("quoteVolume") else None
                chg_pct = float(t24.get("priceChangePercent", 0)) if t24.get("priceChangePercent") else None

                mark_px = float(pi.get("markPrice", 0)) if pi.get("markPrice") else last_px
                index_px = float(pi.get("indexPrice", 0)) if pi.get("indexPrice") else None
                fr = float(pi.get("lastFundingRate", 0)) if pi.get("lastFundingRate") is not None else None
                next_ft = int(pi.get("nextFundingTime", 0)) if pi.get("nextFundingTime") else None

                best_b = float(bt.get("bidPrice", 0)) if bt.get("bidPrice") else None
                best_a = float(bt.get("askPrice", 0)) if bt.get("askPrice") else None
                b_qty = float(bt.get("bidQty", 0)) if bt.get("bidQty") else None
                a_qty = float(bt.get("askQty", 0)) if bt.get("askQty") else None

                spread_pct = None
                spread_bps = None
                if best_b and best_a and best_b > 0 and best_a >= best_b:
                    mid = (best_b + best_a) / 2.0
                    spread_pct = ((best_a - best_b) / mid) * 100.0
                    spread_bps = spread_pct * 100.0

                norm_fr = fr
                ann_fr = (fr * 3 * 365 * 100.0) if fr is not None else None

                items.append(
                    VenueMarketMetrics(
                        symbol=sym,
                        exchange="binance",
                        contract_type="PERPETUAL",
                        last_price=last_px,
                        mark_price=mark_px,
                        index_price=index_px,
                        price_change_24h_pct=chg_pct,
                        volume_24h_usdt=quote_vol,
                        funding_rate=fr,
                        funding_interval_hours=8,
                        normalized_8h_funding_rate=norm_fr,
                        annualized_funding_pct=ann_fr,
                        next_funding_time_ms=next_ft,
                        best_bid=best_b,
                        best_ask=best_a,
                        bid_qty=b_qty,
                        ask_qty=a_qty,
                        spread_pct=spread_pct,
                        spread_bps=spread_bps,
                        timestamp_ms=now_ms,
                        latency_ms=latency,
                        status="AVAILABLE",
                    )
                )

            self._venue_telemetry["binance"] = VenueStatusTelemetry(
                exchange_name="binance",
                status="AVAILABLE",
                latency_ms=latency,
                last_successful_fetch_utc=datetime.now(timezone.utc).isoformat(),
                total_contracts=len(items),
            )
            return items

        except Exception as e:
            logger.warning(f"Screener: Binance fetch failed: {e}")
            self._venue_telemetry["binance"] = VenueStatusTelemetry(
                exchange_name="binance",
                status="ERROR",
                latency_ms=(time.time() - t0) * 1000.0,
                error_message=str(e),
            )
            return []

    def _fetch_bybit_data(self) -> List[VenueMarketMetrics]:
        """Fetch bulk market data from Bybit Linear Perpetuals V5 REST."""
        t0 = time.time()
        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        items: List[VenueMarketMetrics] = []
        try:
            resp = self._session.get(url, timeout=self.timeout_sec)
            latency = (time.time() - t0) * 1000.0
            now_ms = int(time.time() * 1000)

            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")

            data = resp.json()
            if data.get("retCode") != 0:
                raise RuntimeError(data.get("retMsg") or "Bybit API error")

            tickers = data.get("result", {}).get("list", [])
            for t in tickers:
                sym = str(t.get("symbol", "")).upper()
                if not sym.endswith("USDT"):
                    continue

                last_px = float(t.get("lastPrice", 0)) if t.get("lastPrice") else None
                mark_px = float(t.get("markPrice", 0)) if t.get("markPrice") else last_px
                index_px = float(t.get("indexPrice", 0)) if t.get("indexPrice") else None
                
                # Turnover in USDT (actual quote turnover, not contract count)
                quote_vol = float(t.get("turnover24h", 0)) if t.get("turnover24h") else None
                chg_pct = (float(t.get("price24hPcnt", 0)) * 100.0) if t.get("price24hPcnt") else None

                # Open interest notional directly provided in USDT
                oi_notional = float(t.get("openInterestValue", 0)) if t.get("openInterestValue") else None
                oi_contracts = float(t.get("openInterest", 0)) if t.get("openInterest") else None

                fr = float(t.get("fundingRate", 0)) if t.get("fundingRate") else None
                next_ft = int(t.get("nextFundingTime", 0)) if t.get("nextFundingTime") else None
                interval_h = int(t.get("fundingIntervalHour", 8)) if t.get("fundingIntervalHour") else 8

                best_b = float(t.get("bid1Price", 0)) if t.get("bid1Price") else None
                best_a = float(t.get("ask1Price", 0)) if t.get("ask1Price") else None
                b_qty = float(t.get("bid1Size", 0)) if t.get("bid1Size") else None
                a_qty = float(t.get("ask1Size", 0)) if t.get("ask1Size") else None

                spread_pct = None
                spread_bps = None
                if best_b and best_a and best_b > 0 and best_a >= best_b:
                    mid = (best_b + best_a) / 2.0
                    spread_pct = ((best_a - best_b) / mid) * 100.0
                    spread_bps = spread_pct * 100.0

                # Normalization to standard 8h period
                norm_fr = (fr * (8.0 / interval_h)) if fr is not None else None
                ann_fr = (fr * (24.0 / interval_h) * 365.0 * 100.0) if fr is not None else None

                items.append(
                    VenueMarketMetrics(
                        symbol=sym,
                        exchange="bybit",
                        contract_type="PERPETUAL",
                        last_price=last_px,
                        mark_price=mark_px,
                        index_price=index_px,
                        price_change_24h_pct=chg_pct,
                        volume_24h_usdt=quote_vol,
                        open_interest_notional=oi_notional,
                        open_interest_contracts=oi_contracts,
                        funding_rate=fr,
                        funding_interval_hours=interval_h,
                        normalized_8h_funding_rate=norm_fr,
                        annualized_funding_pct=ann_fr,
                        next_funding_time_ms=next_ft,
                        best_bid=best_b,
                        best_ask=best_a,
                        bid_qty=b_qty,
                        ask_qty=a_qty,
                        spread_pct=spread_pct,
                        spread_bps=spread_bps,
                        timestamp_ms=now_ms,
                        latency_ms=latency,
                        status="AVAILABLE",
                    )
                )

            self._venue_telemetry["bybit"] = VenueStatusTelemetry(
                exchange_name="bybit",
                status="AVAILABLE",
                latency_ms=latency,
                last_successful_fetch_utc=datetime.now(timezone.utc).isoformat(),
                total_contracts=len(items),
            )
            return items

        except Exception as e:
            logger.warning(f"Screener: Bybit fetch failed: {e}")
            self._venue_telemetry["bybit"] = VenueStatusTelemetry(
                exchange_name="bybit",
                status="ERROR",
                latency_ms=(time.time() - t0) * 1000.0,
                error_message=str(e),
            )
            return []

    def _fetch_hyperliquid_data(self) -> List[VenueMarketMetrics]:
        """Fetch bulk market data from Hyperliquid POST /info."""
        t0 = time.time()
        url = "https://api.hyperliquid.xyz/info"
        items: List[VenueMarketMetrics] = []
        try:
            resp = self._session.post(url, json={"type": "metaAndAssetCtxs"}, timeout=self.timeout_sec)
            latency = (time.time() - t0) * 1000.0
            now_ms = int(time.time() * 1000)

            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")

            data = resp.json()
            if not isinstance(data, list) or len(data) < 2:
                raise RuntimeError("Invalid Hyperliquid response schema")

            universe = data[0].get("universe", [])
            asset_ctxs = data[1]

            for i, coin_meta in enumerate(universe):
                coin_name = coin_meta.get("name")
                if not coin_name:
                    continue

                ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
                sym = f"{coin_name}USDT"

                mark_px = float(ctx.get("markPx", 0)) if ctx.get("markPx") else None
                mid_px = float(ctx.get("midPx", 0)) if ctx.get("midPx") else mark_px
                oracle_px = float(ctx.get("oraclePx", 0)) if ctx.get("oraclePx") else None
                
                # Hyperliquid dayNtlVlm is 24h notional turnover in USD
                quote_vol = float(ctx.get("dayNtlVlm", 0)) if ctx.get("dayNtlVlm") else None
                
                # OI in contracts -> converted to USD notional via markPx
                oi_contracts = float(ctx.get("openInterest", 0)) if ctx.get("openInterest") else None
                oi_notional = (oi_contracts * mark_px) if (oi_contracts and mark_px) else None

                # Hyperliquid funding is hourly
                fr_hourly = float(ctx.get("funding", 0)) if ctx.get("funding") is not None else None
                norm_fr = (fr_hourly * 8.0) if fr_hourly is not None else None
                ann_fr = (fr_hourly * 24.0 * 365.0 * 100.0) if fr_hourly is not None else None

                # Compute impact bid/ask from impactPxs if available
                impact_pxs = ctx.get("impactPxs") or []
                best_b = float(impact_pxs[0]) if len(impact_pxs) >= 2 else mid_px
                best_a = float(impact_pxs[1]) if len(impact_pxs) >= 2 else mid_px

                spread_pct = None
                spread_bps = None
                if best_b and best_a and best_b > 0 and best_a >= best_b:
                    mid = (best_b + best_a) / 2.0
                    spread_pct = ((best_a - best_b) / mid) * 100.0
                    spread_bps = spread_pct * 100.0

                items.append(
                    VenueMarketMetrics(
                        symbol=sym,
                        exchange="hyperliquid",
                        contract_type="PERPETUAL",
                        last_price=mid_px,
                        mark_price=mark_px,
                        index_price=oracle_px,
                        volume_24h_usdt=quote_vol,
                        open_interest_notional=oi_notional,
                        open_interest_contracts=oi_contracts,
                        funding_rate=fr_hourly,
                        funding_interval_hours=1,
                        normalized_8h_funding_rate=norm_fr,
                        annualized_funding_pct=ann_fr,
                        best_bid=best_b,
                        best_ask=best_a,
                        spread_pct=spread_pct,
                        spread_bps=spread_bps,
                        timestamp_ms=now_ms,
                        latency_ms=latency,
                        status="AVAILABLE",
                    )
                )

            self._venue_telemetry["hyperliquid"] = VenueStatusTelemetry(
                exchange_name="hyperliquid",
                status="AVAILABLE",
                latency_ms=latency,
                last_successful_fetch_utc=datetime.now(timezone.utc).isoformat(),
                total_contracts=len(items),
            )
            return items

        except Exception as e:
            logger.warning(f"Screener: Hyperliquid fetch failed: {e}")
            self._venue_telemetry["hyperliquid"] = VenueStatusTelemetry(
                exchange_name="hyperliquid",
                status="ERROR",
                latency_ms=(time.time() - t0) * 1000.0,
                error_message=str(e),
            )
            return []

    def refresh_all_venues(self, force: bool = False) -> Dict[str, CrossExchangeMarketAsset]:
        """Fetch market data concurrently from all supported venues and build aggregated assets."""
        now = time.time()
        with self._lock:
            if not force and self._cached_assets and (now - self._last_fetch_time < self.cache_ttl_sec):
                return self._cached_assets

        # Execute venue queries concurrently
        venue_results: Dict[str, List[VenueMarketMetrics]] = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self._fetch_asterdex_data): "asterdex",
                executor.submit(self._fetch_binance_data): "binance",
                executor.submit(self._fetch_bybit_data): "bybit",
                executor.submit(self._fetch_hyperliquid_data): "hyperliquid",
            }
            for fut in as_completed(futures):
                ex = futures[fut]
                try:
                    venue_results[ex] = fut.result()
                except Exception as err:
                    logger.error(f"Screener: Unhandled exception fetching {ex}: {err}")
                    self._venue_telemetry[ex] = VenueStatusTelemetry(
                        exchange_name=ex,
                        status="ERROR",
                        latency_ms=0.0,
                        error_message=str(err),
                    )
                    venue_results[ex] = []

        # Aggregate by normalized underlying asset
        assets_map: Dict[str, CrossExchangeMarketAsset] = {}
        for ex, metrics_list in venue_results.items():
            for m in metrics_list:
                norm_sym, base, quote = normalize_symbol_key(m.symbol)
                if norm_sym not in assets_map:
                    assets_map[norm_sym] = CrossExchangeMarketAsset(
                        normalized_symbol=norm_sym,
                        base_asset=base,
                        quote_asset=quote,
                    )
                assets_map[norm_sym].venues[ex] = m

        now_ms = int(time.time() * 1000)
        current_oi_snapshot: Dict[str, float] = {}

        for sym, asset in assets_map.items():
            asset.venue_count = len(asset.venues)
            asset.cross_listed = asset.venue_count >= 2

            # Sum quote turnover and open interest
            vols = [v.volume_24h_usdt for v in asset.venues.values() if v.volume_24h_usdt is not None]
            asset.total_volume_24h_usdt = sum(vols) if vols else 0.0

            ois = [v.open_interest_notional for v in asset.venues.values() if v.open_interest_notional is not None]
            asset.total_open_interest_usdt = sum(ois) if ois else 0.0
            if asset.total_open_interest_usdt > 0:
                current_oi_snapshot[sym] = asset.total_open_interest_usdt

            # Dominant / Benchmark price: Median mark price across venues
            prices = [v.mark_price or v.last_price for v in asset.venues.values() if (v.mark_price or v.last_price)]
            if prices:
                prices.sort()
                mid_idx = len(prices) // 2
                asset.benchmark_price = prices[mid_idx]

            # Price Change 24h: Take from Binance, Bybit, or Aster
            for preferred in ["binance", "bybit", "asterdex", "hyperliquid"]:
                if preferred in asset.venues and asset.venues[preferred].price_change_24h_pct is not None:
                    asset.price_change_24h_pct = asset.venues[preferred].price_change_24h_pct
                    break

            # Cross-venue price divergence
            valid_venues = {k: v for k, v in asset.venues.items() if (v.mark_price or v.last_price)}
            if len(valid_venues) >= 2:
                sorted_venues = sorted(valid_venues.items(), key=lambda x: (x[1].mark_price or x[1].last_price or 0.0))
                min_ex, min_m = sorted_venues[0]
                max_ex, max_m = sorted_venues[-1]
                min_p = min_m.mark_price or min_m.last_price or 0.0
                max_p = max_m.mark_price or max_m.last_price or 0.0

                div_usdt = max(0.0, max_p - min_p)
                asset.price_divergence_usdt = div_usdt
                asset.min_price_venue = min_ex
                asset.max_price_venue = max_ex

                if asset.benchmark_price and asset.benchmark_price > 0:
                    asset.price_divergence_pct = (div_usdt / asset.benchmark_price) * 100.0
                    asset.price_divergence_bps = asset.price_divergence_pct * 100.0

            # Cross-venue funding divergence
            valid_fr = {k: v for k, v in asset.venues.items() if v.normalized_8h_funding_rate is not None}
            if len(valid_fr) >= 2:
                sorted_fr = sorted(valid_fr.items(), key=lambda x: x[1].normalized_8h_funding_rate or 0.0)
                asset.min_funding_venue = sorted_fr[0][0]
                asset.max_funding_venue = sorted_fr[-1][0]
                diff = (sorted_fr[-1][1].normalized_8h_funding_rate or 0) - (sorted_fr[0][1].normalized_8h_funding_rate or 0)
                asset.funding_divergence_bps = diff * 10000.0

            # Liquidity comparison
            vol_sorted = sorted(
                [(k, v.volume_24h_usdt or 0.0) for k, v in asset.venues.items()],
                key=lambda x: x[1],
                reverse=True,
            )
            if vol_sorted and vol_sorted[0][1] > 0:
                asset.highest_volume_venue = vol_sorted[0][0]

            spread_sorted = sorted(
                [(k, v.spread_bps) for k, v in asset.venues.items() if v.spread_bps is not None and v.spread_bps > 0],
                key=lambda x: x[1],
            )
            if spread_sorted:
                asset.lowest_spread_venue = spread_sorted[0][0]
                asset.best_spread_bps = spread_sorted[0][1]

            # Max age
            ages = [(now_ms - v.timestamp_ms) / 1000.0 for v in asset.venues.values()]
            asset.data_freshness_seconds = min(ages) if ages else 0.0

        # Update historical OI snapshot buffer for 1h change estimation
        if current_oi_snapshot:
            self._historical_oi_snapshots.append((now, current_oi_snapshot))
            # Keep snapshots within 2.5 hours
            self._historical_oi_snapshots = [
                s for s in self._historical_oi_snapshots if (now - s[0]) <= 9000.0
            ]
            # Estimate 1h OI change
            target_1h = now - 3600.0
            closest_snap = None
            closest_diff = 1e9
            for s_ts, s_map in self._historical_oi_snapshots:
                diff = abs(s_ts - target_1h)
                if diff < closest_diff and diff <= 1200.0:  # within 20min of 1h
                    closest_diff = diff
                    closest_snap = s_map
            
            if closest_snap:
                for sym, asset in assets_map.items():
                    if sym in closest_snap and closest_snap[sym] > 0 and asset.total_open_interest_usdt > 0:
                        prev_oi = closest_snap[sym]
                        asset.oi_change_1h_pct = ((asset.total_open_interest_usdt - prev_oi) / prev_oi) * 100.0

        with self._lock:
            self._cached_assets = assets_map
            self._last_fetch_time = now

        logger.info(
            f"Screener: Refreshed {len(assets_map)} assets across "
            f"{sum(1 for t in self._venue_telemetry.values() if t.status == 'AVAILABLE')} active venues."
        )
        return assets_map

    def filter_and_paginate(
        self,
        params: ScreenerFilterParams,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """Apply user filters, multi-column sorting, and pagination."""
        all_assets = self.refresh_all_venues(force=force_refresh)
        asset_list = list(all_assets.values())
        total_universe = len(asset_list)

        # 1. Search Filter (symbol or base asset)
        if params.search:
            q = params.search.strip().upper()
            asset_list = [
                a for a in asset_list
                if (q in a.normalized_symbol.upper()) or (q in a.base_asset.upper())
            ]

        # 2. Minimum & Maximum Volume Filters
        if params.min_volume_usdt and params.min_volume_usdt > 0:
            asset_list = [a for a in asset_list if a.total_volume_24h_usdt >= params.min_volume_usdt]
        if params.max_volume_usdt and params.max_volume_usdt > 0:
            asset_list = [a for a in asset_list if a.total_volume_24h_usdt <= params.max_volume_usdt]

        # 3. Open Interest Filters
        if params.min_oi_usdt and params.min_oi_usdt > 0:
            asset_list = [a for a in asset_list if a.total_open_interest_usdt >= params.min_oi_usdt]
        if params.max_oi_usdt and params.max_oi_usdt > 0:
            asset_list = [a for a in asset_list if a.total_open_interest_usdt <= params.max_oi_usdt]

        # 4. Exchange Filter
        if params.exchanges:
            selected_ex = set(e.lower() for e in params.exchanges)
            asset_list = [
                a for a in asset_list
                if any(k in selected_ex for k in a.venues.keys())
            ]

        # 5. Cross-Listed Only (>= 2 venues)
        if params.cross_listed_only:
            asset_list = [a for a in asset_list if a.cross_listed]

        # 6. Maximum Spread Filter
        if params.max_spread_bps and params.max_spread_bps > 0:
            asset_list = [
                a for a in asset_list
                if a.best_spread_bps is not None and a.best_spread_bps <= params.max_spread_bps
            ]

        # 7. Funding Direction & Magnitude Filter
        if params.funding_direction and params.funding_direction.upper() in ("POSITIVE", "NEGATIVE"):
            req_dir = params.funding_direction.upper()
            filtered_by_fr = []
            for a in asset_list:
                fr_rates = [v.normalized_8h_funding_rate for v in a.venues.values() if v.normalized_8h_funding_rate is not None]
                if not fr_rates:
                    continue
                avg_fr = sum(fr_rates) / len(fr_rates)
                if req_dir == "POSITIVE" and avg_fr > 0:
                    filtered_by_fr.append(a)
                elif req_dir == "NEGATIVE" and avg_fr < 0:
                    filtered_by_fr.append(a)
            asset_list = filtered_by_fr

        if params.min_funding_abs_pct and params.min_funding_abs_pct > 0:
            cutoff = params.min_funding_abs_pct / 100.0
            asset_list = [
                a for a in asset_list
                if any(abs(v.normalized_8h_funding_rate or 0.0) >= cutoff for v in a.venues.values())
            ]

        total_matched = len(asset_list)

        # 8. Sorting
        sort_key = params.sort_by.lower()
        if sort_key == "volume_desc":
            asset_list.sort(key=lambda x: x.total_volume_24h_usdt, reverse=True)
        elif sort_key == "volume_asc":
            asset_list.sort(key=lambda x: x.total_volume_24h_usdt, reverse=False)
        elif sort_key == "oi_desc":
            asset_list.sort(key=lambda x: x.total_open_interest_usdt, reverse=True)
        elif sort_key == "oi_asc":
            asset_list.sort(key=lambda x: x.total_open_interest_usdt, reverse=False)
        elif sort_key == "divergence_desc":
            asset_list.sort(key=lambda x: x.price_divergence_bps or 0.0, reverse=True)
        elif sort_key == "divergence_asc":
            asset_list.sort(key=lambda x: x.price_divergence_bps or 0.0, reverse=False)
        elif sort_key == "spread_desc":
            asset_list.sort(key=lambda x: x.best_spread_bps or 9999.0, reverse=True)
        elif sort_key == "spread_asc":
            asset_list.sort(key=lambda x: x.best_spread_bps or 9999.0, reverse=False)
        elif sort_key == "price_desc":
            asset_list.sort(key=lambda x: x.benchmark_price or 0.0, reverse=True)
        elif sort_key == "price_asc":
            asset_list.sort(key=lambda x: x.benchmark_price or 0.0, reverse=False)
        elif sort_key == "change_desc":
            asset_list.sort(key=lambda x: x.price_change_24h_pct or -999.0, reverse=True)
        elif sort_key == "change_asc":
            asset_list.sort(key=lambda x: x.price_change_24h_pct or 999.0, reverse=False)
        elif sort_key == "funding_desc":
            asset_list.sort(
                key=lambda x: max((v.normalized_8h_funding_rate or -1.0) for v in x.venues.values()) if x.venues else -1.0,
                reverse=True,
            )
        elif sort_key == "funding_asc":
            asset_list.sort(
                key=lambda x: min((v.normalized_8h_funding_rate or 1.0) for v in x.venues.values()) if x.venues else 1.0,
                reverse=False,
            )
        elif sort_key == "symbol_asc":
            asset_list.sort(key=lambda x: x.normalized_symbol, reverse=False)
        elif sort_key == "symbol_desc":
            asset_list.sort(key=lambda x: x.normalized_symbol, reverse=True)
        else:
            # Default to volume_desc
            asset_list.sort(key=lambda x: x.total_volume_24h_usdt, reverse=True)

        # 9. Pagination
        page = max(1, params.page)
        page_size = max(5, min(100, params.page_size))
        total_pages = max(1, math.ceil(total_matched / page_size)) if total_matched > 0 else 1
        
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_items = asset_list[start_idx:end_idx]

        # Aggregate universe summaries
        combined_vol_all = sum(a.total_volume_24h_usdt for a in all_assets.values())
        combined_oi_all = sum(a.total_open_interest_usdt for a in all_assets.values())

        return {
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_matched": total_matched,
            "total_universe_count": total_universe,
            "combined_volume_all_usdt": round(combined_vol_all, 2),
            "combined_oi_all_usdt": round(combined_oi_all, 2),
            "last_fetch_utc": datetime.fromtimestamp(self._last_fetch_time, tz=timezone.utc).isoformat() if self._last_fetch_time else None,
            "venues_telemetry": {k: v.to_dict() for k, v in self._venue_telemetry.items()},
            "data": [item.to_dict() for item in paginated_items],
        }

    def get_markets(self, params: ScreenerFilterParams) -> Dict[str, Any]:
        """Convenience alias for filter_and_paginate."""
        return self.filter_and_paginate(params)

    def get_venue_telemetry(self) -> Dict[str, Any]:
        """Return venue connection status, contract counts, and latencies."""
        return {k: v.to_dict() for k, v in self._venue_telemetry.items()}
