"""Binance USD(S)-M Futures public market data adapter.

Provides public REST market discovery, Level-2 order book depth, and recent trades.
Operates strictly in READ-ONLY mode for liquidity telemetry and research.
"""

from collections import deque
from datetime import datetime, timezone
import re
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple
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
from src.core.types import Candle


_BANNED_UNTIL_RE = re.compile(r"banned\s+until\s+(\d{10,16})", re.IGNORECASE)


class BinanceFuturesAdapter:
    """Public read-only adapter for Binance USD(S)-M Futures with shared circuit-breaker,
    single-flight deduplication, weight-budget rate limiting, and stale-cache fallback.
    """

    DEFAULT_BASE_URL = "https://fapi.binance.com"

    # Process-wide shared state so multiple adapter instances never bypass bans or duplicate fetches
    _global_lock = threading.Lock()
    _global_blocked_until: float = 0.0
    _global_block_status: Optional[Dict[str, Any]] = None
    _global_failures: int = 0
    _shared_depth_cache: Dict[str, Tuple[float, NormalizedOrderBook]] = {}
    _shared_trades_cache: Dict[str, Tuple[float, List[NormalizedTrade]]] = {}
    _shared_catalog_cache: Dict[str, NormalizedInstrument] = {}
    _shared_last_catalog_refresh: float = 0.0
    _shared_symbol_status: Dict[str, Dict[str, Any]] = {}
    _inflight_depth_events: Dict[str, threading.Event] = {}
    _weight_window: Deque[Tuple[float, int]] = deque()
    _max_weight_per_minute: int = 600  # Conservative cap (25% of Binance 2400/min IP limit)

    @classmethod
    def reset_shared_state(cls) -> None:
        """Reset shared class-level caches and circuit breaker state (for isolated unit testing)."""
        with cls._global_lock:
            cls._global_blocked_until = 0.0
            cls._global_block_status = None
            cls._global_failures = 0
            cls._shared_depth_cache.clear()
            cls._shared_trades_cache.clear()
            cls._shared_catalog_cache.clear()
            cls._shared_last_catalog_refresh = 0.0
            cls._shared_symbol_status.clear()
            cls._inflight_depth_events.clear()
            cls._weight_window.clear()

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = 8.0,
        session: Optional[requests.Session] = None,
        depth_ttl_sec: float = 5.0,
        stale_fallback_max_age_sec: float = 900.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.depth_ttl_sec = depth_ttl_sec
        self.stale_fallback_max_age_sec = stale_fallback_max_age_sec

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            # Never auto-retry 418 or 429 inside urllib3; handle rate limits and IP bans explicitly
            retries = Retry(
                total=1,
                backoff_factor=0.3,
                status_forcelist=[500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

        # Instance-level aliases pointing to process-wide shared state for backward compatibility
        self._catalog_cache = self.__class__._shared_catalog_cache
        self._depth_cache = self.__class__._shared_depth_cache
        self._depth_lock = self.__class__._global_lock
        self._depth_failures: Dict[str, int] = {}
        self._depth_blocked_until: Dict[str, float] = {}
        self.last_depth_status: Dict[str, Any] = {}

    def _consume_weight(self, weight: int, now: float) -> bool:
        """Return True if weight is within the per-minute budget, else False (must be called under _global_lock)."""
        cutoff = now - 60.0
        while self._weight_window and self._weight_window[0][0] < cutoff:
            self._weight_window.popleft()
        used = sum(w for _, w in self._weight_window)
        if used + weight > self._max_weight_per_minute:
            return False
        self._weight_window.append((now, weight))
        return True

    @staticmethod
    def _extract_error_payload(resp: Any) -> Tuple[Optional[int], str]:
        """Extract Binance JSON error code and message from non-200 HTTP response."""
        err_code: Optional[int] = None
        err_msg: str = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                raw_code = body.get("code")
                if raw_code is not None:
                    try:
                        err_code = int(raw_code)
                    except (ValueError, TypeError):
                        err_code = None
                err_msg = str(body.get("msg") or "")
        except Exception:
            try:
                err_msg = (resp.text or "")[:300].strip()
            except Exception:
                err_msg = ""
        return err_code, err_msg

    @classmethod
    def _parse_ban_cooldown(cls, resp: Any, err_msg: str, now_sec: float, default_cooldown: float) -> Tuple[float, Optional[int], Optional[str]]:
        """Parse Retry-After header and Binance 'banned until <ms>' timestamp."""
        retry_after = 0.0
        try:
            headers = getattr(resp, "headers", {}) or {}
            ra_val = headers.get("Retry-After") or headers.get("retry-after")
            if ra_val:
                retry_after = max(0.0, float(ra_val))
        except (ValueError, TypeError):
            retry_after = 0.0

        banned_until_ms: Optional[int] = None
        banned_until_utc: Optional[str] = None
        match = _BANNED_UNTIL_RE.search(err_msg or "")
        if match:
            try:
                banned_until_ms = int(match.group(1))
                banned_until_sec = banned_until_ms / 1000.0
                banned_until_utc = datetime.fromtimestamp(banned_until_sec, tz=timezone.utc).isoformat()
                remaining_from_msg = max(0.0, banned_until_sec - now_sec)
                retry_after = max(retry_after, remaining_from_msg)
            except (ValueError, TypeError, OSError):
                pass

        cooldown = max(retry_after, default_cooldown)
        return cooldown, banned_until_ms, banned_until_utc

    def get_symbol_depth_status(self, symbol: str) -> Dict[str, Any]:
        """Return structured diagnostic status for the most recent depth query on symbol."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        with self._global_lock:
            sym_st = self._shared_symbol_status.get(clean_symbol)
            if sym_st:
                return dict(sym_st)
            if self.last_depth_status:
                return dict(self.last_depth_status)
            if self._global_block_status and self._global_blocked_until > time.time():
                return dict(self._global_block_status)
        return {"status": "UNAVAILABLE", "error_code": "NO_DATA", "message": f"No Binance depth status recorded for {clean_symbol}"}

    def discover_instruments(self, force_refresh: bool = False) -> List[NormalizedInstrument]:
        """Fetch all eligible active USDT perpetual instruments from Binance Futures."""
        now = time.time()
        with self._global_lock:
            if not force_refresh and self._shared_catalog_cache and (now - self.__class__._shared_last_catalog_refresh < 1800.0):
                return list(self._shared_catalog_cache.values())
            if self.__class__._global_blocked_until > now:
                return list(self._shared_catalog_cache.values())

        url = f"{self.base_url}/fapi/v1/exchangeInfo"
        try:
            resp = self.session.get(url, timeout=self.timeout_sec)
            if resp.status_code != 200:
                err_code, err_msg = self._extract_error_payload(resp)
                if resp.status_code in (418, 429):
                    with self._global_lock:
                        now_err = time.time()
                        cooldown, banned_ms, banned_utc = self._parse_ban_cooldown(
                            resp, err_msg, now_err, 180.0 if resp.status_code == 418 else 30.0
                        )
                        self.__class__._global_blocked_until = max(self.__class__._global_blocked_until, now_err + cooldown)
                        is_ban = resp.status_code == 418
                        ban_detail = f"banned until {banned_utc}" if banned_utc else f"retry after {cooldown:.0f}s"
                        self.__class__._global_block_status = {
                            "status": "IP_BANNED" if is_ban else "RATE_LIMITED",
                            "error_code": "IP_BANNED" if is_ban else "RATE_LIMITED",
                            "binance_error_code": err_code,
                            "binance_error_msg": err_msg,
                            "http_status": resp.status_code,
                            "retry_after_seconds": round(cooldown, 1),
                            "banned_until_ms": banned_ms,
                            "banned_until_utc": banned_utc,
                            "message": (
                                f"Binance Futures HTTP {resp.status_code} {'IP Auto-Ban' if is_ban else 'Rate Limit'} "
                                f"(code {err_code or -1003}: {err_msg or 'Too many requests'}; {ban_detail})"
                            ),
                        }
                logger.warning(f"BinanceFutures: exchangeInfo returned status {resp.status_code} ({err_code}: {err_msg})")
                return list(self._shared_catalog_cache.values())

            data = resp.json()
            symbols_data = data.get("symbols", [])
            discovered: Dict[str, NormalizedInstrument] = {}

            for s in symbols_data:
                if s.get("contractType") != "PERPETUAL":
                    continue
                if s.get("quoteAsset") != "USDT":
                    continue
                if s.get("status") != "TRADING":
                    continue

                sym = s["symbol"]
                base_asset = s.get("baseAsset", "")
                quote_asset = s.get("quoteAsset", "")

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

            with self._global_lock:
                self._shared_catalog_cache.clear()
                self._shared_catalog_cache.update(discovered)
                self.__class__._shared_last_catalog_refresh = now
            logger.info(f"BinanceFutures: Discovered {len(discovered)} active USDT perpetuals.")
            return list(discovered.values())

        except Exception as err:
            logger.error(f"BinanceFutures: Failed to discover instruments: {err}")
            return list(self._shared_catalog_cache.values())

    def _record_status(self, symbol: str, status_dict: Dict[str, Any]) -> None:
        """Store structured status on both instance and shared symbol map (call under _global_lock or safely)."""
        self.last_depth_status = status_dict
        self._shared_symbol_status[symbol] = status_dict

    def get_order_book(self, symbol: str, limit: int = 50) -> Optional[NormalizedOrderBook]:
        """Fetch Level-2 order book depth for a symbol via GET /fapi/v1/depth.

        Distinguishes:
        - Valid depth response (HTTP 200 with non-empty bids & asks)
        - Invalid symbol (HTTP 400 / Binance code -1121 -> INVALID_SYMBOL)
        - HTTP 418 IP auto-ban (code -1003 -> IP_BANNED, circuit breaker honours Retry-After & banned-until timestamp without aggressive retries)
        - HTTP 429 rate limit (RATE_LIMITED, circuit breaker backoff)
        - Network timeout (requests.Timeout -> TIMEOUT)
        - Empty or malformed response (MALFORMED_RESPONSE / EMPTY_ORDER_BOOK)
        - Stale cached fallback when circuit breaker is open or transient failure occurs
        """
        clean_symbol = symbol.strip().upper().replace(".P", "")
        now = time.time()

        wait_event: Optional[threading.Event] = None
        is_leader = False

        with self._global_lock:
            # 1. Fresh cache hit
            cached = self._shared_depth_cache.get(clean_symbol)
            if cached and (now - cached[0]) < self.depth_ttl_sec:
                age = max(0.0, now - cached[0])
                st = {
                    "status": "LIVE",
                    "error_code": None,
                    "source": "cache",
                    "symbol": clean_symbol,
                    "endpoint": "/fapi/v1/depth",
                    "http_status": 200,
                    "age_seconds": round(age, 3),
                    "is_stale": False,
                    "message": None,
                }
                self._record_status(clean_symbol, st)
                return cached[1]

            # 2. Check process-wide or symbol-specific circuit breaker (e.g. HTTP 418 IP ban or 429 backoff)
            sym_blocked_until = self._depth_blocked_until.get(clean_symbol, 0.0)
            effective_blocked_until = max(self.__class__._global_blocked_until, sym_blocked_until)
            if effective_blocked_until > now:
                retry_rem = round(effective_blocked_until - now, 1)
                base_block = dict(self.__class__._global_block_status or {})
                block_reason = base_block.get("error_code") or "CIRCUIT_OPEN"
                http_st = base_block.get("http_status")
                banned_utc = base_block.get("banned_until_utc")

                if cached and (now - cached[0]) <= self.stale_fallback_max_age_sec:
                    age = max(0.0, now - cached[0])
                    msg = (
                        f"Binance Futures HTTP {http_st} ({block_reason}): serving cached order book for {clean_symbol} "
                        f"(age {age:.1f}s, retry in {retry_rem:.0f}s"
                        + (f", banned until {banned_utc}" if banned_utc else "")
                        + ")"
                    )
                    st = {
                        "status": "STALE",
                        "error_code": block_reason,
                        "source": "stale_cache",
                        "symbol": clean_symbol,
                        "endpoint": "/fapi/v1/depth",
                        "http_status": http_st,
                        "retry_after_seconds": retry_rem,
                        "banned_until_ms": base_block.get("banned_until_ms"),
                        "banned_until_utc": banned_utc,
                        "age_seconds": round(age, 3),
                        "is_stale": True,
                        "message": msg,
                    }
                    self._record_status(clean_symbol, st)
                    return cached[1]

                msg = base_block.get("message") or (
                    f"Binance Futures circuit breaker open for {clean_symbol} ({block_reason}; retry in {retry_rem:.0f}s)"
                )
                st = {
                    "status": base_block.get("status", "CIRCUIT_OPEN"),
                    "error_code": block_reason,
                    "source": "circuit_breaker",
                    "symbol": clean_symbol,
                    "endpoint": "/fapi/v1/depth",
                    "http_status": http_st,
                    "retry_after_seconds": retry_rem,
                    "banned_until_ms": base_block.get("banned_until_ms"),
                    "banned_until_utc": banned_utc,
                    "age_seconds": round(now - cached[0], 3) if cached else None,
                    "is_stale": True if cached else False,
                    "message": msg,
                }
                self._record_status(clean_symbol, st)
                return None

            # 3. Single-flight deduplication: if another thread is already fetching this symbol, wait for it
            if clean_symbol in self._inflight_depth_events:
                wait_event = self._inflight_depth_events[clean_symbol]
            else:
                # 3.5. Catalog membership pre-check: if the Binance catalog is populated and this symbol is not listed,
                # return INVALID_SYMBOL immediately without wasting network weight on GET /fapi/v1/depth.
                if self._shared_catalog_cache and clean_symbol not in self._shared_catalog_cache:
                    self._depth_blocked_until[clean_symbol] = now + 300.0
                    msg = f"Binance Futures contract '{clean_symbol}' is not listed in USDⓈ-M PERPETUAL catalog (/fapi/v1/exchangeInfo)"
                    st = {
                        "status": "INVALID_SYMBOL",
                        "error_code": "INVALID_SYMBOL",
                        "binance_error_code": -1121,
                        "binance_error_msg": "Invalid symbol (not in Binance USDⓈ-M catalog)",
                        "source": "catalog_filter",
                        "symbol": clean_symbol,
                        "endpoint": "/fapi/v1/depth",
                        "http_status": None,
                        "age_seconds": None,
                        "is_stale": False,
                        "message": msg,
                    }
                    self._record_status(clean_symbol, st)
                    return None

                # 4. Local weight-budget governor (depth limit<=50 costs weight 2; limit=100 costs 5)
                req_weight = 2 if limit <= 50 else (5 if limit <= 100 else 10)
                if not self._consume_weight(req_weight, now):
                    if cached:
                        age = max(0.0, now - cached[0])
                        st = {
                            "status": "STALE",
                            "error_code": "LOCAL_RATE_LIMIT_BUDGET",
                            "source": "stale_cache",
                            "symbol": clean_symbol,
                            "endpoint": "/fapi/v1/depth",
                            "http_status": None,
                            "age_seconds": round(age, 3),
                            "is_stale": True,
                            "message": f"Binance Futures local request budget reached ({self._max_weight_per_minute} weight/min); serving cached depth for {clean_symbol} ({age:.1f}s old).",
                        }
                        self._record_status(clean_symbol, st)
                        return cached[1]
                    st = {
                        "status": "RATE_LIMITED",
                        "error_code": "LOCAL_RATE_LIMIT_BUDGET",
                        "source": "local_limiter",
                        "symbol": clean_symbol,
                        "endpoint": "/fapi/v1/depth",
                        "http_status": None,
                        "age_seconds": None,
                        "is_stale": False,
                        "message": f"Binance Futures local request budget reached ({self._max_weight_per_minute} weight/min); request deferred for {clean_symbol}.",
                    }
                    self._record_status(clean_symbol, st)
                    return None

                wait_event = threading.Event()
                self._inflight_depth_events[clean_symbol] = wait_event
                is_leader = True

        # Follower thread waits for leader's in-flight request to finish
        if not is_leader and wait_event is not None:
            wait_event.wait(timeout=self.timeout_sec + 1.0)
            with self._global_lock:
                cached_after = self._shared_depth_cache.get(clean_symbol)
                sym_st = self._shared_symbol_status.get(clean_symbol)
                if sym_st:
                    self.last_depth_status = dict(sym_st)
                if cached_after and (time.time() - cached_after[0]) <= self.stale_fallback_max_age_sec:
                    return cached_after[1]
                return None

        url = f"{self.base_url}/fapi/v1/depth"
        params = {"symbol": clean_symbol, "limit": limit}
        now_ms = int(time.time() * 1000)

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            now_after = time.time()

            if resp.status_code != 200:
                err_code, err_msg = self._extract_error_payload(resp)

                # Case A: HTTP 418 IP Auto-Ban (never retry aggressively!)
                if resp.status_code == 418:
                    with self._global_lock:
                        self.__class__._global_failures += 1
                        failures = self._depth_failures.get(clean_symbol, 0) + 1
                        self._depth_failures[clean_symbol] = failures
                        cooldown, banned_ms, banned_utc = self._parse_ban_cooldown(
                            resp, err_msg, now_after, default_cooldown=180.0
                        )
                        blocked_until = now_after + cooldown
                        self.__class__._global_blocked_until = max(self.__class__._global_blocked_until, blocked_until)
                        self._depth_blocked_until[clean_symbol] = blocked_until
                        cached = self._shared_depth_cache.get(clean_symbol)
                        age = round(now_after - cached[0], 3) if cached else None

                        ban_detail = f"banned until {banned_utc}" if banned_utc else f"retry after {cooldown:.0f}s"
                        msg = (
                            f"Binance Futures HTTP 418 IP Auto-Ban on GET /fapi/v1/depth for {clean_symbol} "
                            f"(code {err_code or -1003}: {err_msg or 'Too many requests'}; {ban_detail})"
                        )
                        block_info = {
                            "status": "STALE" if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else "IP_BANNED",
                            "error_code": "IP_BANNED",
                            "binance_error_code": err_code,
                            "binance_error_msg": err_msg,
                            "source": "stale_cache" if cached else "network",
                            "symbol": clean_symbol,
                            "endpoint": "/fapi/v1/depth",
                            "http_status": 418,
                            "retry_after_seconds": round(cooldown, 1),
                            "banned_until_ms": banned_ms,
                            "banned_until_utc": banned_utc,
                            "age_seconds": age,
                            "is_stale": bool(cached),
                            "message": msg,
                        }
                        self.__class__._global_block_status = dict(block_info)
                        self._record_status(clean_symbol, block_info)
                        logger.warning(msg)
                        if cached and age is not None and age <= self.stale_fallback_max_age_sec:
                            return cached[1]
                        return None

                # Case B: HTTP 429 Rate Limit
                if resp.status_code == 429:
                    with self._global_lock:
                        self.__class__._global_failures += 1
                        failures = self._depth_failures.get(clean_symbol, 0) + 1
                        self._depth_failures[clean_symbol] = failures
                        default_cd = min(60.0, 2.0 ** min(failures, 5))
                        cooldown, banned_ms, banned_utc = self._parse_ban_cooldown(
                            resp, err_msg, now_after, default_cooldown=default_cd
                        )
                        blocked_until = now_after + cooldown
                        self.__class__._global_blocked_until = max(self.__class__._global_blocked_until, blocked_until)
                        self._depth_blocked_until[clean_symbol] = blocked_until
                        cached = self._shared_depth_cache.get(clean_symbol)
                        age = round(now_after - cached[0], 3) if cached else None

                        msg = (
                            f"Binance Futures HTTP 429 Rate Limit on GET /fapi/v1/depth for {clean_symbol} "
                            f"(code {err_code or -1003}: {err_msg or 'Rate limit exceeded'}; backoff {cooldown:.0f}s)"
                        )
                        rate_info = {
                            "status": "STALE" if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else "RATE_LIMITED",
                            "error_code": "RATE_LIMITED",
                            "binance_error_code": err_code,
                            "binance_error_msg": err_msg,
                            "source": "stale_cache" if cached else "network",
                            "symbol": clean_symbol,
                            "endpoint": "/fapi/v1/depth",
                            "http_status": 429,
                            "retry_after_seconds": round(cooldown, 1),
                            "age_seconds": age,
                            "is_stale": bool(cached),
                            "message": msg,
                        }
                        self.__class__._global_block_status = dict(rate_info)
                        self._record_status(clean_symbol, rate_info)
                        logger.warning(msg)
                        if cached and age is not None and age <= self.stale_fallback_max_age_sec:
                            return cached[1]
                        return None

                # Case C: Invalid Symbol (HTTP 400 with code -1121 or message containing Invalid symbol)
                if resp.status_code == 400 and (err_code in (-1121, -1100, -1102) or "invalid symbol" in err_msg.lower()):
                    with self._global_lock:
                        # Cache negative symbol lookup for 300s on this symbol only (do not trip global circuit breaker)
                        self._depth_blocked_until[clean_symbol] = now_after + 300.0
                        msg = f"Binance Futures invalid symbol '{clean_symbol}' on GET /fapi/v1/depth (HTTP 400, code {err_code}: {err_msg})"
                        st = {
                            "status": "INVALID_SYMBOL",
                            "error_code": "INVALID_SYMBOL",
                            "binance_error_code": err_code,
                            "binance_error_msg": err_msg,
                            "source": "network",
                            "symbol": clean_symbol,
                            "endpoint": "/fapi/v1/depth",
                            "http_status": resp.status_code,
                            "age_seconds": None,
                            "is_stale": False,
                            "message": msg,
                        }
                        self._record_status(clean_symbol, st)
                    logger.info(msg)
                    return None

                # Case D: Other HTTP Error (5xx, 403 WAF, etc.)
                with self._global_lock:
                    cached = self._shared_depth_cache.get(clean_symbol)
                    age = round(now_after - cached[0], 3) if cached else None
                    msg = (
                        f"Binance Futures GET /fapi/v1/depth returned HTTP {resp.status_code} for {clean_symbol}"
                        + (f" (code {err_code}: {err_msg})" if (err_code or err_msg) else "")
                    )
                    st = {
                        "status": "STALE" if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else "HTTP_ERROR",
                        "error_code": f"HTTP_{resp.status_code}",
                        "binance_error_code": err_code,
                        "binance_error_msg": err_msg,
                        "source": "stale_cache" if cached else "network",
                        "symbol": clean_symbol,
                        "endpoint": "/fapi/v1/depth",
                        "http_status": resp.status_code,
                        "age_seconds": age,
                        "is_stale": bool(cached),
                        "message": msg,
                    }
                    self._record_status(clean_symbol, st)
                    logger.warning(msg)
                    if cached and age is not None and age <= self.stale_fallback_max_age_sec:
                        return cached[1]
                return None

            # HTTP 200: Parse and validate JSON payload
            try:
                data = resp.json()
            except ValueError as json_err:
                with self._global_lock:
                    cached = self._shared_depth_cache.get(clean_symbol)
                    age = round(now_after - cached[0], 3) if cached else None
                    msg = f"Binance Futures malformed JSON depth response for {clean_symbol}: {json_err}"
                    st = {
                        "status": "STALE" if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else "MALFORMED_RESPONSE",
                        "error_code": "MALFORMED_RESPONSE",
                        "source": "stale_cache" if cached else "network",
                        "symbol": clean_symbol,
                        "endpoint": "/fapi/v1/depth",
                        "http_status": 200,
                        "age_seconds": age,
                        "is_stale": bool(cached),
                        "message": msg,
                    }
                    self._record_status(clean_symbol, st)
                    logger.warning(msg)
                    return cached[1] if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else None

            if not isinstance(data, dict) or "bids" not in data or "asks" not in data:
                with self._global_lock:
                    cached = self._shared_depth_cache.get(clean_symbol)
                    age = round(now_after - cached[0], 3) if cached else None
                    msg = f"Binance Futures malformed depth payload for {clean_symbol}: missing 'bids' or 'asks' keys"
                    st = {
                        "status": "STALE" if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else "MALFORMED_RESPONSE",
                        "error_code": "MALFORMED_RESPONSE",
                        "source": "stale_cache" if cached else "network",
                        "symbol": clean_symbol,
                        "endpoint": "/fapi/v1/depth",
                        "http_status": 200,
                        "age_seconds": age,
                        "is_stale": bool(cached),
                        "message": msg,
                    }
                    self._record_status(clean_symbol, st)
                    logger.warning(msg)
                    return cached[1] if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else None

            raw_bids = data.get("bids")
            raw_asks = data.get("asks")
            if not isinstance(raw_bids, list) or not isinstance(raw_asks, list):
                with self._global_lock:
                    msg = f"Binance Futures malformed depth levels for {clean_symbol}: bids/asks are not lists"
                    st = {
                        "status": "MALFORMED_RESPONSE",
                        "error_code": "MALFORMED_RESPONSE",
                        "source": "network",
                        "symbol": clean_symbol,
                        "endpoint": "/fapi/v1/depth",
                        "http_status": 200,
                        "age_seconds": None,
                        "is_stale": False,
                        "message": msg,
                    }
                    self._record_status(clean_symbol, st)
                    return None

            try:
                bids = [OrderBookLevel(price=float(row[0]), size=float(row[1])) for row in raw_bids if len(row) >= 2]
                asks = [OrderBookLevel(price=float(row[0]), size=float(row[1])) for row in raw_asks if len(row) >= 2]
            except (ValueError, TypeError, IndexError) as parse_err:
                with self._global_lock:
                    msg = f"Binance Futures malformed price/qty level in depth response for {clean_symbol}: {parse_err}"
                    st = {
                        "status": "MALFORMED_RESPONSE",
                        "error_code": "MALFORMED_RESPONSE",
                        "source": "network",
                        "symbol": clean_symbol,
                        "endpoint": "/fapi/v1/depth",
                        "http_status": 200,
                        "age_seconds": None,
                        "is_stale": False,
                        "message": msg,
                    }
                    self._record_status(clean_symbol, st)
                    return None

            if not bids or not asks:
                with self._global_lock:
                    cached = self._shared_depth_cache.get(clean_symbol)
                    age = round(now_after - cached[0], 3) if cached else None
                    msg = f"Binance Futures returned empty order book for {clean_symbol} (bids={len(bids)}, asks={len(asks)})"
                    st = {
                        "status": "STALE" if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else "EMPTY_ORDER_BOOK",
                        "error_code": "EMPTY_ORDER_BOOK",
                        "source": "stale_cache" if cached else "network",
                        "symbol": clean_symbol,
                        "endpoint": "/fapi/v1/depth",
                        "http_status": 200,
                        "age_seconds": age,
                        "is_stale": bool(cached),
                        "message": msg,
                    }
                    self._record_status(clean_symbol, st)
                    return cached[1] if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else None

            # Ensure proper sort order: bids descending, asks ascending
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price, reverse=False)

            book = NormalizedOrderBook(
                symbol=clean_symbol,
                exchange_name="binance",
                timestamp_ms=int(data.get("E") or data.get("T") or now_ms),
                bids=bids,
                asks=asks,
            )
            with self._global_lock:
                self._shared_depth_cache[clean_symbol] = (now_after, book)
                self._depth_failures.pop(clean_symbol, None)
                self._depth_blocked_until.pop(clean_symbol, None)
                self.__class__._global_failures = 0
                if self.__class__._global_blocked_until <= now_after:
                    self.__class__._global_block_status = None
                st = {
                    "status": "LIVE",
                    "error_code": None,
                    "source": "network",
                    "symbol": clean_symbol,
                    "endpoint": "/fapi/v1/depth",
                    "http_status": 200,
                    "age_seconds": 0.0,
                    "is_stale": False,
                    "message": None,
                }
                self._record_status(clean_symbol, st)
            return book

        except requests.Timeout as timeout_err:
            now_err = time.time()
            with self._global_lock:
                cached = self._shared_depth_cache.get(clean_symbol)
                age = round(now_err - cached[0], 3) if cached else None
                msg = f"Binance Futures network timeout after {self.timeout_sec:.1f}s on GET /fapi/v1/depth for {clean_symbol}: {timeout_err}"
                st = {
                    "status": "STALE" if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else "TIMEOUT",
                    "error_code": "TIMEOUT",
                    "source": "stale_cache" if cached else "network",
                    "symbol": clean_symbol,
                    "endpoint": "/fapi/v1/depth",
                    "http_status": None,
                    "age_seconds": age,
                    "is_stale": bool(cached),
                    "message": msg,
                }
                self._record_status(clean_symbol, st)
                logger.warning(msg)
                if cached and age is not None and age <= self.stale_fallback_max_age_sec:
                    return cached[1]
            return None

        except Exception as err:
            now_err = time.time()
            with self._global_lock:
                cached = self._shared_depth_cache.get(clean_symbol)
                age = round(now_err - cached[0], 3) if cached else None
                msg = f"Binance Futures connection error on GET /fapi/v1/depth for {clean_symbol}: {err}"
                st = {
                    "status": "STALE" if (cached and age is not None and age <= self.stale_fallback_max_age_sec) else "ERROR",
                    "error_code": "NETWORK_ERROR",
                    "source": "stale_cache" if cached else "network",
                    "symbol": clean_symbol,
                    "endpoint": "/fapi/v1/depth",
                    "http_status": None,
                    "age_seconds": age,
                    "is_stale": bool(cached),
                    "message": msg,
                    "error": str(err),
                }
                self._record_status(clean_symbol, st)
                logger.warning(msg)
                if cached and age is not None and age <= self.stale_fallback_max_age_sec:
                    return cached[1]
            return None

        finally:
            if is_leader:
                with self._global_lock:
                    ev = self._inflight_depth_events.pop(clean_symbol, None)
                    if ev is not None:
                        ev.set()

    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[NormalizedTrade]:
        """Fetch recent public trades for flow analysis with circuit-breaker & short-TTL cache."""
        clean_symbol = symbol.strip().upper().replace(".P", "")
        now = time.time()
        with self._global_lock:
            cached = self._shared_trades_cache.get(clean_symbol)
            if cached and (now - cached[0]) < self.depth_ttl_sec:
                return cached[1]
            if self.__class__._global_blocked_until > now or self._depth_blocked_until.get(clean_symbol, 0.0) > now:
                return cached[1] if cached else []
            if self._shared_catalog_cache and clean_symbol not in self._shared_catalog_cache:
                return []
            if not self._consume_weight(5, now):
                return cached[1] if cached else []

        url = f"{self.base_url}/fapi/v1/trades"
        params = {"symbol": clean_symbol, "limit": limit}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                if resp.status_code in (418, 429):
                    err_code, err_msg = self._extract_error_payload(resp)
                    with self._global_lock:
                        cooldown, _, _ = self._parse_ban_cooldown(
                            resp, err_msg, time.time(), 180.0 if resp.status_code == 418 else 30.0
                        )
                        self.__class__._global_blocked_until = max(self.__class__._global_blocked_until, time.time() + cooldown)
                return []

            data = resp.json()
            if not isinstance(data, list):
                return []
            trades: List[NormalizedTrade] = []
            for t in data:
                price = float(t["price"])
                size = float(t["qty"])
                is_buyer_maker = bool(t.get("isBuyerMaker", False))
                side = "SELL" if is_buyer_maker else "BUY"

                trades.append(
                    NormalizedTrade(
                        trade_id=str(t.get("id", "")),
                        symbol=clean_symbol,
                        exchange_name="binance",
                        timestamp_ms=int(t.get("time", 0)),
                        price=price,
                        size=size,
                        notional_usdt=price * size,
                        side=side,
                    )
                )
            with self._global_lock:
                self._shared_trades_cache[clean_symbol] = (time.time(), trades)
            return trades
        except Exception as err:
            logger.warning(f"BinanceFutures: Failed to fetch trades for {clean_symbol}: {err}")
            return []

    def get_klines(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> List[Candle]:
        clean_symbol = symbol.strip().upper().replace(".P", "")
        with self._global_lock:
            if self.__class__._global_blocked_until > time.time():
                return []
        try:
            params: Dict[str, Any] = {
                "symbol": clean_symbol,
                "interval": timeframe,
                "limit": min(limit, 1000),
            }
            if start_time_ms is not None:
                params["startTime"] = start_time_ms
            if end_time_ms is not None:
                params["endTime"] = end_time_ms
            resp = self.session.get(f"{self.base_url}/fapi/v1/klines", params=params, timeout=self.timeout_sec)
            if resp.status_code != 200:
                if resp.status_code in (418, 429):
                    _, err_msg = self._extract_error_payload(resp)
                    with self._global_lock:
                        cooldown, _, _ = self._parse_ban_cooldown(
                            resp, err_msg, time.time(), 180.0 if resp.status_code == 418 else 30.0
                        )
                        self.__class__._global_blocked_until = max(self.__class__._global_blocked_until, time.time() + cooldown)
                return []
            body = resp.json()
            if not isinstance(body, list):
                return []
            return [Candle.from_raw_kline(row, clean_symbol, timeframe) for row in body]
        except Exception as err:
            logger.warning(f"BinanceFutures: Failed to fetch klines for {clean_symbol}: {err}")
            return []

    def get_funding_rates(self) -> Dict[str, Dict[str, Any]]:
        """Fetch latest funding rate and mark prices across all instruments."""
        with self._global_lock:
            if self.__class__._global_blocked_until > time.time():
                return {}
        url = f"{self.base_url}/fapi/v1/premiumIndex"
        try:
            resp = self.session.get(url, timeout=self.timeout_sec)
            if resp.status_code != 200:
                if resp.status_code in (418, 429):
                    _, err_msg = self._extract_error_payload(resp)
                    with self._global_lock:
                        cooldown, _, _ = self._parse_ban_cooldown(
                            resp, err_msg, time.time(), 180.0 if resp.status_code == 418 else 30.0
                        )
                        self.__class__._global_blocked_until = max(self.__class__._global_blocked_until, time.time() + cooldown)
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
        clean_symbol = symbol.strip().upper().replace(".P", "")
        with self._global_lock:
            if self.__class__._global_blocked_until > time.time():
                return None
        try:
            resp = self.session.get(f"{self.base_url}/fapi/v1/openInterest", params={"symbol": clean_symbol}, timeout=self.timeout_sec)
            if resp.status_code != 200:
                if resp.status_code in (418, 429):
                    _, err_msg = self._extract_error_payload(resp)
                    with self._global_lock:
                        cooldown, _, _ = self._parse_ban_cooldown(
                            resp, err_msg, time.time(), 180.0 if resp.status_code == 418 else 30.0
                        )
                        self.__class__._global_blocked_until = max(self.__class__._global_blocked_until, time.time() + cooldown)
                return None
            data = resp.json()
            if not isinstance(data, dict) or "openInterest" not in data:
                return None
            return {"symbol": clean_symbol, "open_interest": float(data["openInterest"]), "timestamp_ms": int(data.get("time", time.time() * 1000))}
        except Exception as err:
            logger.warning(f"BinanceFutures: Failed to fetch open interest for {clean_symbol}: {err}")
            return None

    def get_premium_index(self, symbol: str) -> Optional[Dict[str, Any]]:
        clean_symbol = symbol.strip().upper().replace(".P", "")
        with self._global_lock:
            if self.__class__._global_blocked_until > time.time():
                return None
        try:
            resp = self.session.get(f"{self.base_url}/fapi/v1/premiumIndex", params={"symbol": clean_symbol}, timeout=self.timeout_sec)
            if resp.status_code != 200:
                if resp.status_code in (418, 429):
                    _, err_msg = self._extract_error_payload(resp)
                    with self._global_lock:
                        cooldown, _, _ = self._parse_ban_cooldown(
                            resp, err_msg, time.time(), 180.0 if resp.status_code == 418 else 30.0
                        )
                        self.__class__._global_blocked_until = max(self.__class__._global_blocked_until, time.time() + cooldown)
                return None
            row = resp.json()
            if not isinstance(row, dict):
                return None
            return {
                "symbol": clean_symbol,
                "mark_price": float(row["markPrice"]),
                "index_price": float(row["indexPrice"]),
                "last_funding_rate": float(row["lastFundingRate"]),
                "next_funding_time_ms": int(row["nextFundingTime"]),
                "timestamp_ms": int(row.get("time", time.time() * 1000)),
            }
        except Exception as err:
            logger.warning(f"BinanceFutures: Failed to fetch premium index for {clean_symbol}: {err}")
            return None
